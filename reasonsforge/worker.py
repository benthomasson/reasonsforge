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
    _request(f"{base_url}/api/tasks/{task_id}/progress", token,
             data=data, method="POST")


def _report_result(base_url: str, token: str, task_id: str, result: dict):
    data = json.dumps(result).encode()
    _request(f"{base_url}/api/tasks/{task_id}/result", token,
             data=data, method="POST")


def _report_failure(base_url: str, token: str, task_id: str, error: str):
    data = json.dumps({"error": error}).encode()
    _request(f"{base_url}/api/tasks/{task_id}/fail", token,
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
    except Exception:
        pass  # OK if no existing DB — forge will create one

    domain = config.get("domain", "")
    forge_model = config.get("model", model)
    rounds = config.get("rounds", 1)
    max_derive = config.get("max_derive_rounds", 10)

    os.chdir(str(work_dir))

    import reasonsforge.forge as forge
    forge.REASONS_DB = str(db_path)

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
    try:
        _download_file(db_url, token, db_path)
    except Exception as e:
        return {"error": f"Failed to download reasons.db: {e}"}

    if not db_path.exists():
        return {"error": "No reasons.db available"}

    os.chdir(str(work_dir))

    import reasonsforge.forge as forge
    forge.REASONS_DB = str(db_path)

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

    cmd_derive_review_repair(args)

    _report_progress(base_url, token, task_id, "uploading", 90)

    if db_path.exists():
        upload_url = f"{base_url}/domains/{domain_id}/import-reasons"
        _upload_file(upload_url, token, db_path)

    return {"status": "completed"}


TASK_HANDLERS = {
    "document-forge": _run_document_forge,
    "derive-review-repair": _run_derive_review_repair,
}


def cmd_worker(args):
    base_url = (args.url or os.environ.get("REASONS_SERVICE_URL", "")).rstrip("/")
    token = args.token or os.environ.get("REASONS_SERVICE_TOKEN", "")
    model = args.model
    poll_interval = args.poll_interval
    once = args.once

    if not base_url:
        print("Error: --url or REASONS_SERVICE_URL required", file=sys.stderr)
        sys.exit(1)
    if not token:
        print("Error: --token or REASONS_SERVICE_TOKEN required", file=sys.stderr)
        sys.exit(1)

    print(f"[worker] connecting to {base_url}", file=sys.stderr)
    print(f"[worker] model: {model}", file=sys.stderr)
    print(f"[worker] poll interval: {poll_interval}s", file=sys.stderr)

    while True:
        result = _request(f"{base_url}/api/tasks/pending", token)
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

        claim = _request(f"{base_url}/api/tasks/{task_id}/claim", token,
                         data=b"{}", method="POST")
        if not claim:
            print(f"[worker] failed to claim task {task_id}", file=sys.stderr)
            time.sleep(poll_interval)
            continue

        print(f"[worker] claimed task {task_id} ({task_type})", file=sys.stderr)

        orig_dir = os.getcwd()
        work_dir = Path(tempfile.mkdtemp(prefix=f"forge-{task_id[:8]}-"))

        try:
            task_result = handler(work_dir, task, base_url, token, model)
            _report_result(base_url, token, task_id, task_result)
            print(f"[worker] completed task {task_id}", file=sys.stderr)
        except KeyboardInterrupt:
            _report_failure(base_url, token, task_id, "Worker interrupted")
            print(f"\n[worker] interrupted, reported failure for {task_id}",
                  file=sys.stderr)
            raise
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            _report_failure(base_url, token, task_id, error_msg)
            print(f"[worker] task {task_id} failed: {error_msg}",
                  file=sys.stderr)
        finally:
            os.chdir(orig_dir)
            shutil.rmtree(work_dir, ignore_errors=True)

        if once:
            return
