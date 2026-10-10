"""Remote forge worker — pulls tasks from reasons-service-edge and processes them locally."""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(url: str, token: str, data: bytes | None = None,
             method: str = "GET", content_type: str = "application/json",
             timeout: int = 30) -> dict | None:
    req = Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "User-Agent": "reasonsforge-worker/1.0",
        },
        method=method,
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")
        except Exception:
            pass
        print(f"  HTTP {e.code}: {body[:200]}", file=sys.stderr)
        return None
    except URLError as e:
        print(f"  Connection failed: {e.reason}", file=sys.stderr)
        return None


def _report_progress(base_url: str, token: str, task_id: str,
                     stage: str, percent: int = 0):
    data = json.dumps({"stage": stage, "percent": percent}).encode()
    _request(f"{base_url}/tasks/{task_id}/progress", token,
             data=data, method="POST")


def _report_result(base_url: str, token: str, task_id: str, result: dict):
    data = json.dumps(result).encode()
    _request(f"{base_url}/tasks/{task_id}/result", token,
             data=data, method="POST")


def _report_failure(base_url: str, token: str, task_id: str, error: str):
    data = json.dumps({"error": error}).encode()
    _request(f"{base_url}/tasks/{task_id}/fail", token,
             data=data, method="POST")


def _download_file(url: str, token: str, dest: Path, timeout: int = 120):
    req = Request(url, headers={
        "Authorization": f"Bearer {token}",
        "User-Agent": "reasonsforge-worker/1.0",
    })
    with urlopen(req, timeout=timeout) as resp:
        dest.write_bytes(resp.read())


def _upload_file(url: str, token: str, file_path: Path, timeout: int = 300):
    content = file_path.read_bytes()
    filename = file_path.name
    boundary = f"----ReasonsforgePush{os.urandom(16).hex()}"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: application/octet-stream\r\n"
        f"\r\n"
    ).encode() + content + f"\r\n--{boundary}--\r\n".encode()

    return _request(url, token, data=body, method="POST",
                    content_type=f"multipart/form-data; boundary={boundary}",
                    timeout=timeout)


def _run_document_forge(work_dir: Path, task: dict, base_url: str,
                        token: str, model: str):
    task_id = task["id"]
    tenant_id = task.get("tenant_id", "")
    domain_id = task.get("domain_id", "")
    config = task.get("config", {})

    sources_dir = work_dir / "sources"
    sources_dir.mkdir(exist_ok=True)

    _report_progress(base_url, token, task_id, "downloading", 5)

    source_slugs = config.get("source_slugs", [])
    for slug in source_slugs:
        url = f"{base_url}/domains/{domain_id}/sources/{slug}"
        result = _request(url, token)
        if result and result.get("content"):
            fname = f"{slug}.md"
            (sources_dir / fname).write_text(result["content"], encoding="utf-8")
            print(f"[worker] downloaded source: {slug}", file=sys.stderr)

    source_urls = config.get("source_urls", [])
    for i, src in enumerate(source_urls):
        fname = src.get("filename", f"source-{i}.md")
        url = src.get("url", "")
        if url:
            _download_file(url, token, sources_dir / fname)

    db_url = config.get("reasons_db_url",
                        f"{base_url}/domains/{domain_id}/download-db")
    db_path = work_dir / "reasons.db"
    try:
        _download_file(db_url, token, db_path)
        print(f"[worker] downloaded existing DB ({db_path.stat().st_size} bytes)",
              file=sys.stderr)
    except Exception:
        from reasonsforge.storage import Storage
        Storage(str(db_path))
        print("[worker] initialized empty reasons.db", file=sys.stderr)

    domain = config.get("domain", "")
    forge_model = config.get("model", model)
    rounds = config.get("rounds", 1)
    max_derive = config.get("max_derive_rounds", 10)

    os.chdir(str(work_dir))

    _report_progress(base_url, token, task_id, "summarizing", 15)

    from reasonsforge.forge.pipeline import cmd_pipeline
    args = SimpleNamespace(
        sources_dir=str(sources_dir),
        pdf=None,
        model=forge_model,
        rounds=rounds,
        max_derive_rounds=max_derive,
        search_rounds=0,
        no_auto_accept=False,
        timeout=600,
        domain=domain,
        parallel=1,
        recursive=False,
        resume=False,
        namespace=None,
        mode=None,
        num_ctx=None,
        index_db=str(work_dir / "rag_fts.db"),
    )

    cmd_pipeline(args)

    _report_progress(base_url, token, task_id, "uploading", 90)

    if db_path.exists():
        upload_url = f"{base_url}/domains/{domain_id}/import-reasons"
        _upload_file(upload_url, token, db_path)

    return {"status": "completed", "reasons_db_size": db_path.stat().st_size
            if db_path.exists() else 0}


def _run_derive_review_repair(work_dir: Path, task: dict, base_url: str,
                              token: str, model: str):
    task_id = task["id"]
    tenant_id = task.get("tenant_id", "")
    domain_id = task.get("domain_id", "")
    config = task.get("config", {})

    db_path = work_dir / "reasons.db"
    db_url = config.get("reasons_db_url")
    if not db_url:
        db_url = f"{base_url}/domains/{domain_id}/download-db"

    _report_progress(base_url, token, task_id, "downloading", 5)
    print(f"[worker] downloading {db_url}", file=sys.stderr)
    try:
        _download_file(db_url, token, db_path)
    except Exception as e:
        print(f"[worker] download failed: {e}", file=sys.stderr)
        return {"error": f"Failed to download reasons.db: {e}"}

    if not db_path.exists():
        print("[worker] reasons.db not found after download", file=sys.stderr)
        return {"error": "No reasons.db available"}

    print(f"[worker] downloaded {db_path.stat().st_size} bytes", file=sys.stderr)

    os.chdir(str(work_dir))

    import reasonsforge.forge as forge
    forge.REASONS_DB = "reasons.db"
    # Also patch the already-imported module reference
    import reasonsforge.forge.pipeline as pipeline_mod
    pipeline_mod.REASONS_DB = "reasons.db"

    forge_model = config.get("model", model)
    rounds = config.get("rounds", 3)
    max_derive = config.get("max_derive_rounds", 10)
    domain = config.get("domain", "")

    _report_progress(base_url, token, task_id, "deriving", 20)

    from reasonsforge.forge.pipeline import cmd_derive_review_repair
    args = SimpleNamespace(
        model=forge_model,
        rounds=rounds,
        max_derive_rounds=max_derive,
        search_rounds=0,
        timeout=600,
        domain=domain,
        namespace=None,
        verify_dedup=True,
    )

    print(f"[worker] running derive-review-repair (rounds={rounds}, "
          f"model={forge_model})", file=sys.stderr)
    cmd_derive_review_repair(args)

    _report_progress(base_url, token, task_id, "uploading", 90)

    if db_path.exists():
        upload_url = f"{base_url}/domains/{domain_id}/import-reasons"
        print(f"[worker] uploading {db_path.stat().st_size} bytes to {upload_url}",
              file=sys.stderr)
        result = _upload_file(upload_url, token, db_path)
        print(f"[worker] upload result: {result}", file=sys.stderr)
    else:
        print("[worker] no reasons.db to upload", file=sys.stderr)

    return {"status": "completed"}


def _run_generate_stories(work_dir: Path, task: dict, base_url: str,
                          token: str, model: str):
    """Generate feed stories from domain beliefs using LLM analysis."""
    import random
    import sqlite3

    task_id = task["id"]
    domain_id = task.get("domain_id", "")
    config = task.get("config", {})
    domain_name = config.get("domain", task.get("domain_name", ""))
    max_stories = config.get("max_stories", 8)
    algorithms = config.get("algorithms", ["insight", "gated", "recent", "lesson"])

    _report_progress(base_url, token, task_id, "downloading", 5)

    db_url = f"{base_url}/domains/{domain_id}/download-db"
    db_path = work_dir / "reasons.db"
    try:
        _download_file(db_url, token, db_path)
        print(f"[stories] downloaded DB ({db_path.stat().st_size} bytes)",
              file=sys.stderr)
    except Exception as e:
        return {"error": f"Failed to download reasons.db: {e}"}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    nodes = [dict(r) for r in conn.execute(
        "SELECT id, text, truth_value, source, created_at, updated_at FROM nodes"
    ).fetchall()]
    if not nodes:
        return {"error": "No beliefs in domain"}

    in_nodes = [n for n in nodes if n["truth_value"] == "IN"]
    if not in_nodes:
        return {"error": "No IN beliefs to generate stories from"}

    node_map = {n["id"]: n for n in nodes}

    justs = [dict(r) for r in conn.execute(
        "SELECT node_id, antecedents, outlist FROM justifications"
    ).fetchall()]
    for j in justs:
        j["antecedents"] = json.loads(j["antecedents"]) if j["antecedents"] else []
        j["outlist"] = json.loads(j["outlist"]) if j["outlist"] else []

    conn.close()

    gated = []
    seen_gated = set()
    for j in justs:
        if not j["outlist"]:
            continue
        node = node_map.get(j["node_id"])
        if not node or node["truth_value"] != "OUT":
            continue
        if j["node_id"] in seen_gated:
            continue
        in_blockers = [(bid, node_map[bid]["text"])
                       for bid in j["outlist"]
                       if bid in node_map and node_map[bid]["truth_value"] == "IN"]
        if not in_blockers:
            continue
        antecedents = [(aid, node_map[aid]["text"])
                       for aid in j["antecedents"] if aid in node_map]
        gated.append({
            "node_id": node["id"],
            "node_text": node["text"],
            "blockers": in_blockers,
            "antecedents": antecedents,
        })
        seen_gated.add(j["node_id"])

    recent_nodes = sorted(
        in_nodes,
        key=lambda n: max(n.get("updated_at", ""), n.get("created_at", "")),
        reverse=True,
    )[:40]

    topic_counts: dict[str, list[int]] = {}
    has_sources = False
    for i, n in enumerate(in_nodes):
        src = n.get("source", "")
        if src:
            has_sources = True
            fname = src.rsplit("/", 1)[-1]
            topic = fname.removesuffix(".md")
            topic_counts.setdefault(topic, []).append(i)
        else:
            sep_pos = None
            for ch in "/:.":
                p = n["id"].find(ch)
                if p > 0:
                    sep_pos = p if sep_pos is None else min(sep_pos, p)
                    break
            if sep_pos:
                topic = n["id"][:sep_pos]
                topic_counts.setdefault(topic, []).append(i)

    if not has_sources:
        topic_counts.clear()
        word_indices: dict[str, list[int]] = {}
        stop_words = {
            "a", "an", "the", "is", "are", "was", "were", "in", "on", "at",
            "to", "for", "of", "and", "or", "not", "as", "by", "via", "can",
            "with", "from", "than", "that", "this", "be", "has", "have", "it",
        }
        for i, n in enumerate(in_nodes):
            for word in n["id"].replace("-", " ").replace(".", " ").replace("_", " ").replace(":", " ").split():
                if len(word) > 2 and word.lower() not in stop_words:
                    word_indices.setdefault(word.lower(), []).append(i)
        topic_counts = word_indices

    topics = [(name, indices) for name, indices in topic_counts.items()
              if len(indices) >= 3]
    topics.sort(key=lambda t: len(t[1]), reverse=True)
    topics = topics[:50]

    print(f"[stories] {len(in_nodes)} IN beliefs, {len(gated)} gated, "
          f"{len(topics)} topics, algos={algorithms}", file=sys.stderr)

    from reasonsforge.forge.llm import invoke_sync

    active_algos = []
    if "insight" in algorithms:
        active_algos.append("insight")
    if "gated" in algorithms and gated:
        active_algos.append("gated")
    if "recent" in algorithms:
        active_algos.append("recent")
    if "lesson" in algorithms and topics:
        active_algos.append("lesson")

    if not active_algos:
        active_algos = ["insight"]

    stories_created = 0
    gated_idx = 0
    recent_idx = 0
    lesson_idx = 0
    forge_model = config.get("model", model)

    for si in range(max_stories):
        algo = active_algos[si % len(active_algos)]
        progress = int((si / max_stories) * 85) + 10
        _report_progress(base_url, token, task_id,
                         f"generating ({algo})", progress)

        try:
            if algo == "gated" and gated_idx < len(gated):
                story = _generate_gated_story(
                    gated[gated_idx], domain_name, forge_model, invoke_sync)
                gated_idx += 1
            elif algo == "recent":
                sample_size = min(20, len(recent_nodes))
                start = min(recent_idx, max(0, len(recent_nodes) - sample_size))
                end = min(start + sample_size, len(recent_nodes))
                sample = recent_nodes[start:end]
                recent_idx = end
                story = _generate_recent_story(
                    sample, domain_name, forge_model, invoke_sync)
            elif algo == "lesson" and topics:
                topic_name, topic_indices = topics[lesson_idx % len(topics)]
                lesson_idx += 1
                sample = [in_nodes[i] for i in topic_indices[:30]]
                story = _generate_lesson_story(
                    sample, topic_name, domain_name, forge_model, invoke_sync)
            else:
                sample = random.sample(in_nodes, min(20, len(in_nodes)))
                story = _generate_insight_story(
                    sample, domain_name, forge_model, invoke_sync)

            if story:
                story["domain_id"] = domain_id
                story["tenant_id"] = task.get("tenant_id")
                story["model"] = forge_model
                story["algorithm"] = algo
                result = _request(
                    f"{base_url}/stories", token,
                    data=json.dumps(story).encode(), method="POST")
                if result:
                    stories_created += 1
                    print(f"[stories] created {algo} story: {story['title'][:60]}",
                          file=sys.stderr)
                else:
                    print(f"[stories] failed to upload {algo} story",
                          file=sys.stderr)
        except Exception as e:
            print(f"[stories] {algo} story failed: {e}", file=sys.stderr)

    return {"status": "completed", "stories_created": stories_created}


def _parse_story_response(content: str) -> dict:
    """Parse TITLE: ... body ... BELIEFS: ... format from LLM response."""
    title = ""
    body = content.strip()
    belief_ids = []

    if "TITLE:" in content:
        after = content.split("TITLE:", 1)[1].strip()
        nl = after.find("\n")
        if nl >= 0:
            title = after[:nl].strip()
            body = after[nl:].strip()
        else:
            title = after.strip()
            body = ""

    if "BELIEFS:" in body:
        parts = body.rsplit("BELIEFS:", 1)
        body = parts[0].strip()
        belief_ids = [b.strip() for b in parts[1].split(",") if b.strip()]

    return {"title": title, "body": body, "belief_ids": belief_ids}


def _generate_insight_story(sample, domain, model, invoke_fn):
    beliefs_text = "\n".join(f"[{n['id']}] {n['text']}" for n in sample)
    prompt = (
        f'You are a research analyst examining the "{domain}" knowledge domain. '
        "Given these beliefs, identify one non-obvious insight: a hidden connection, "
        "a surprising implication, a tension worth investigating, a missing assumption, "
        "or a question the beliefs raise but do not answer.\n\n"
        "Write 2-3 concise paragraphs of analysis. Be direct and specific. "
        "Do not invent fictional characters or narratives. "
        "Refer to beliefs by their IDs when making claims.\n\n"
        f"Available beliefs:\n{beliefs_text}\n\n"
        "Start with TITLE: followed by a short descriptive title on one line.\n"
        "Then write the analysis.\n"
        "End with BELIEFS: followed by a comma-separated list "
        "of the belief IDs you referenced."
    )
    result = invoke_fn(prompt, model)
    return _parse_story_response(result)


def _generate_gated_story(gated_belief, domain, model, invoke_fn):
    blockers_str = "\n".join(
        f"  - [{bid}] {btext}" for bid, btext in gated_belief["blockers"])
    antecedents_str = ("  (premise)" if not gated_belief["antecedents"]
                       else "\n".join(
        f"  - [{aid}] {atext}" for aid, atext in gated_belief["antecedents"]))

    prompt = (
        f'You are analyzing the "{domain}" knowledge domain for logical tensions.\n\n'
        'A "gated belief" is a claim that would be true except a blocker belief defeats it.\n\n'
        f'**Gated belief (OUT):** [{gated_belief["node_id"]}] {gated_belief["node_text"]}\n'
        f"**Depends on:**\n{antecedents_str}\n"
        f"**Blocked by (IN):**\n{blockers_str}\n\n"
        "Write a short story (1-2 paragraphs) that:\n"
        "1. Explains the tension in plain language\n"
        "2. Identifies whether this is a real conflict, missing nuance, or problem worth investigating\n"
        "3. Suggests what would need to change for the gated belief to become supported\n\n"
        "Start with TITLE: followed by a short descriptive title on one line.\n"
        "Then write the story.\n"
        "End with BELIEFS: followed by a comma-separated list of the belief IDs mentioned."
    )
    result = invoke_fn(prompt, model)
    return _parse_story_response(result)


def _generate_recent_story(sample, domain, model, invoke_fn):
    beliefs_text = "\n".join(
        f"[{n['id']}] {n['text']} (updated: {n.get('updated_at', '')})"
        for n in sample)

    prompt = (
        f'You are a research analyst examining the "{domain}" knowledge domain. '
        "The following beliefs were recently added or updated. "
        "Analyze what's new: identify emerging patterns, shifts in understanding, "
        "or implications of these recent changes for the broader knowledge base.\n\n"
        "Write 2-3 concise paragraphs of analysis. Be direct and specific. "
        "Do not invent fictional characters or narratives. "
        "Refer to beliefs by their IDs when making claims.\n\n"
        f"Recently changed beliefs:\n{beliefs_text}\n\n"
        "Start with TITLE: followed by a short descriptive title on one line.\n"
        "Then write the analysis.\n"
        "End with BELIEFS: followed by a comma-separated list "
        "of the belief IDs you referenced."
    )
    result = invoke_fn(prompt, model)
    return _parse_story_response(result)


def _generate_lesson_story(sample, topic_name, domain, model, invoke_fn):
    beliefs_text = "\n".join(f"[{n['id']}] {n['text']}" for n in sample)

    prompt = (
        f'You are an expert teacher creating a lesson about "{topic_name}" '
        f'from the "{domain}" knowledge domain.\n\n'
        "Using the beliefs below, write a clear, structured lesson that:\n"
        "1. Introduces the key concepts and why they matter\n"
        "2. Explains how the concepts relate to each other\n"
        "3. Highlights the most important things to understand\n"
        "4. Notes any nuances, exceptions, or common misconceptions\n\n"
        "Write 3-5 paragraphs. Be clear and educational. Use plain language. "
        "Refer to beliefs by their IDs to ground your claims.\n\n"
        f"Available beliefs:\n{beliefs_text}\n\n"
        "Start with TITLE: followed by a lesson title on one line.\n"
        "Then write the lesson.\n"
        "End with BELIEFS: followed by a comma-separated list "
        "of the belief IDs you referenced."
    )
    result = invoke_fn(prompt, model)
    parsed = _parse_story_response(result)
    parsed["topic"] = topic_name
    return parsed


TASK_HANDLERS = {
    "document-forge": _run_document_forge,
    "derive-review-repair": _run_derive_review_repair,
    "generate-stories": _run_generate_stories,
}


def _tenant_url(api_base: str, tenant_id: str) -> str:
    return f"{api_base}/tenants/{tenant_id}"


def cmd_worker(args):
    base_url = (args.url or os.environ.get("REASONS_SERVICE_URL", "")).rstrip("/")
    token = args.token or os.environ.get("REASONS_SERVICE_TOKEN", "")
    model = args.model
    poll_interval = args.poll_interval
    once = args.once
    is_global = getattr(args, "scope", None) == "global"

    if not base_url:
        print("Error: --url or REASONS_SERVICE_URL required", file=sys.stderr)
        sys.exit(1)
    if not token:
        print("Error: --token or REASONS_SERVICE_TOKEN required", file=sys.stderr)
        sys.exit(1)

    scope_label = "global" if is_global else "tenant-scoped"
    print(f"[worker] connecting to {base_url} ({scope_label})", file=sys.stderr)
    print(f"[worker] model: {model}", file=sys.stderr)
    print(f"[worker] poll interval: {poll_interval}s", file=sys.stderr)

    while True:
        result = _request(f"{base_url}/tasks/pending", token)
        if not result:
            if once:
                print("[worker] no tasks, exiting (--once)", file=sys.stderr)
                return
            time.sleep(poll_interval)
            continue

        tasks = result.get("items", result.get("tasks", []))
        if not tasks:
            if once:
                print("[worker] no tasks, exiting (--once)", file=sys.stderr)
                return
            time.sleep(poll_interval)
            continue

        task = tasks[0]
        task_id = task["id"]
        task_type = task.get("type", "unknown")

        handler = TASK_HANDLERS.get(task_type)
        if not handler:
            print(f"[worker] unknown task type: {task_type}, skipping",
                  file=sys.stderr)
            time.sleep(poll_interval)
            continue

        if is_global:
            tenant_id = task.get("tenant_id", "")
            task_base = _tenant_url(base_url, tenant_id)
        else:
            task_base = base_url

        claim = _request(f"{task_base}/tasks/{task_id}/claim", token,
                         data=b"{}", method="POST")
        if not claim:
            print(f"[worker] failed to claim task {task_id}", file=sys.stderr)
            time.sleep(poll_interval)
            continue

        print(f"[worker] claimed task {task_id} ({task_type})", file=sys.stderr)

        orig_dir = os.getcwd()
        work_dir = Path(tempfile.mkdtemp(prefix=f"forge-{task_id[:8]}-"))

        try:
            task_result = handler(work_dir, task, task_base, token, model)
            if "error" in task_result:
                _report_failure(task_base, token, task_id, task_result["error"])
                print(f"[worker] task {task_id} failed: {task_result['error']}",
                      file=sys.stderr)
            else:
                _report_result(task_base, token, task_id, task_result)
                print(f"[worker] completed task {task_id}", file=sys.stderr)
        except KeyboardInterrupt:
            _report_failure(task_base, token, task_id, "Worker interrupted")
            print(f"\n[worker] interrupted, reported failure for {task_id}",
                  file=sys.stderr)
            raise
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            _report_failure(task_base, token, task_id, error_msg)
            print(f"[worker] task {task_id} failed: {error_msg}",
                  file=sys.stderr)
        finally:
            os.chdir(orig_dir)
            shutil.rmtree(work_dir, ignore_errors=True)

        if once:
            return
