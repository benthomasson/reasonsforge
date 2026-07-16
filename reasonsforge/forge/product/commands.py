"""Product forge commands — analyze issue trackers from a product management perspective.

Each function takes an argparse Namespace and uses reasonsforge.api directly
(no subprocess calls to the reasons CLI).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from . import PRODUCT_DIR
from .prompts import (
    DERIVE_BELIEFS_PROMPT,
    PROPOSE_BELIEFS_PRODUCT,
    REVIEW_PROPOSALS_PROMPT,
    build_explore_prompt,
    build_ingest_prompt,
    build_scan_prompt,
    build_summary_prompt,
)
from ..project.sources import GitHubSource, GitLabSource, JiraSource, Issue
from .topics import (
    Topic,
    add_topics,
    load_queue,
    parse_topics_from_response,
    pending_count,
    pop_at,
    pop_multiple,
    pop_next,
    skip_topic,
)

REASONS_DB = "reasons.db"

_RELATIVE_DATE_RE = re.compile(r"(\d+)\s*(day|week|month)s?\s*ago", re.IGNORECASE)

_NEGATIVE_KEYWORDS = re.compile(
    r"\b(gap|missing|churn|attrition|competitor|delay|regression|blocker|"
    r"complaint|friction|confusion|workaround|debt|deprioritized|blocked|"
    r"stalled|declining|risk|broken|fragile|untested)\b",
    re.IGNORECASE,
)

_CRITICAL_KEYWORDS = re.compile(
    r"\b(revenue|retention|churn|security|compliance|legal|data loss|privacy|"
    r"outage|downtime|SLA|enterprise|competitor)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_since_date(since_str: str) -> datetime:
    """Parse a --since value into a datetime.

    Accepts ISO dates (2026-04-01) or relative strings (1 week ago, 7 days ago).
    """
    m = _RELATIVE_DATE_RE.match(since_str.strip())
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit == "day":
            return datetime.now() - timedelta(days=n)
        elif unit == "week":
            return datetime.now() - timedelta(weeks=n)
        elif unit == "month":
            return datetime.now() - timedelta(days=n * 30)
    try:
        return datetime.fromisoformat(since_str.strip())
    except ValueError:
        print(
            f"Error: Cannot parse date: {since_str!r}. "
            "Use ISO format (2026-04-01) or relative (7 days ago, 1 week ago).",
            file=sys.stderr,
        )
        sys.exit(1)


def _parse_issue_updated(updated: str) -> datetime:
    """Parse an issue's updated timestamp to a naive datetime for comparison."""
    if not updated:
        return datetime.min
    cleaned = updated.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(cleaned)
        return dt.replace(tzinfo=None)
    except ValueError:
        return datetime.min


def _load_config() -> dict | None:
    config_path = Path.cwd() / PRODUCT_DIR / "config.json"
    if config_path.is_file():
        return json.loads(config_path.read_text())
    return None


def _save_config(config: dict) -> None:
    config_dir = Path.cwd() / PRODUCT_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(config, indent=2))


def _get_product_dir() -> str:
    return str(Path.cwd() / PRODUCT_DIR)


def _get_source(config: dict) -> GitHubSource | GitLabSource | JiraSource:
    """Create the appropriate source from config."""
    platform = config["platform"]
    if platform == "github":
        return GitHubSource(config["repo"])
    elif platform == "gitlab":
        return GitLabSource(config["repo"])
    elif platform == "jira":
        return JiraSource(
            config["project"],
            url=config.get("jira_url"),
        )
    else:
        raise ValueError(f"Unknown platform: {platform}")


def _create_entry(topic: str, title: str, content: str) -> Path | None:
    """Write an entry file directly (replaces subprocess call to entry CLI)."""
    today = date.today()
    summary_dir = Path("summaries") / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
    summary_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H%M")
    entry_name = f"{topic}-{timestamp}"
    entry_path = summary_dir / f"{entry_name}.md"
    entry_path.write_text(f"# {title}\n\n{content}\n")
    print(f"Entry: {entry_path}", file=sys.stderr)
    return entry_path


def _enqueue_topics(response: str, source: str, product_dir: str | None = None) -> None:
    new_topics = parse_topics_from_response(response, source=source)
    if new_topics:
        added = add_topics(new_topics, product_dir)
        if added:
            total = pending_count(product_dir)
            print(f"Queued {added} new topic(s) ({total} pending)", file=sys.stderr)


def _parse_beliefs_from_response(response: str) -> list[dict]:
    section_match = re.search(
        r"#+\s*Beliefs?\s*\n(.*?)(?=\n#|\Z)",
        response, re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []
    beliefs = []
    pattern = re.compile(r"^[-*]\s+`([^`]+)`\s*(?:—|-|:)\s*(.+)$", re.MULTILINE)
    for match in pattern.finditer(section_match.group(1)):
        beliefs.append({"id": match.group(1), "text": match.group(2).strip()})
    return beliefs


def _report_beliefs(response: str) -> None:
    beliefs = _parse_beliefs_from_response(response)
    if beliefs:
        print(f"Surfaced {len(beliefs)} belief(s):", file=sys.stderr)
        for b in beliefs[:5]:
            print(f"  {b['id']}: {b['text'][:80]}", file=sys.stderr)


def _cache_issues(issues: list[Issue], product_dir: str) -> None:
    """Cache fetched issues so explore can reference them without re-fetching."""
    cache_path = os.path.join(product_dir, "issues-cache.json")
    data = {}
    if os.path.isfile(cache_path):
        with open(cache_path) as f:
            data = json.load(f)
    for issue in issues:
        data[issue.id] = {
            "id": issue.id,
            "title": issue.title,
            "url": issue.url,
            "platform": issue.platform,
            "body": issue.body,
            "state": issue.state,
            "labels": issue.labels,
            "assignees": issue.assignees,
            "milestone": issue.milestone,
            "priority": issue.priority,
            "issue_type": issue.issue_type,
            "parent": issue.parent,
            "children": issue.children,
            "linked": issue.linked,
            "author": issue.author,
            "created": issue.created,
            "updated": issue.updated,
            "closed": issue.closed,
            "comment_count": issue.comment_count,
        }
    os.makedirs(product_dir, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)


def _load_cached_issues(product_dir: str) -> dict:
    """Load cached issues."""
    cache_path = os.path.join(product_dir, "issues-cache.json")
    if not os.path.isfile(cache_path):
        return {}
    with open(cache_path) as f:
        return json.load(f)


def _build_topic_prompt(topic: Topic, config: dict | None, product_dir: str) -> str:
    """Build the explore prompt for a topic without invoking the LLM."""
    issue_text = ""
    context_text = ""

    # Product forge fetches for feature, epic, user-story kinds (not issue)
    if topic.kind in ("feature", "epic", "user-story") and config:
        try:
            source = _get_source(config)
            issue_id = topic.target
            if config["platform"] == "github":
                num = re.search(r"\d+", issue_id)
                if num:
                    issue = source.get_issue(int(num.group()))
                    issue_text = issue.to_prompt_text()

                    cached = _load_cached_issues(product_dir)
                    related_ids = issue.children + issue.linked
                    if issue.parent:
                        related_ids.append(issue.parent)
                    context_parts = []
                    for rid in related_ids:
                        if rid in cached:
                            ci = cached[rid]
                            context_parts.append(
                                f"### {ci['id']}: {ci['title']}\n"
                                f"- State: {ci['state']}\n"
                                f"- Labels: {', '.join(ci.get('labels', []))}"
                            )
                    if context_parts:
                        context_text = "\n\n".join(context_parts)

            elif config["platform"] == "gitlab":
                num = re.search(r"\d+", issue_id)
                if num:
                    issue = source.get_issue(int(num.group()))
                    issue_text = issue.to_prompt_text()

            elif config["platform"] == "jira":
                issue = source.get_issue(issue_id)
                issue_text = issue.to_prompt_text()

        except Exception as e:
            print(f"Warning: Could not fetch issue {topic.target}: {e}", file=sys.stderr)

    if not issue_text:
        cached = _load_cached_issues(product_dir)
        if topic.target in cached:
            ci = cached[topic.target]
            issue_text = (
                f"## {ci['id']}: {ci['title']}\n"
                f"- State: {ci['state']}\n"
                f"- Labels: {', '.join(ci.get('labels', []))}\n"
                f"- Assignees: {', '.join(ci.get('assignees', []))}\n"
            )
            if ci.get("body"):
                issue_text += f"\n### Description\n\n{ci['body']}"
        else:
            issue_text = f"## {topic.target}\n\n{topic.title}"

    return build_explore_prompt(
        issue_text=issue_text,
        context_text=context_text or None,
        question=topic.title,
    )


def _run_topic(args, topic: Topic) -> None:
    """Explore a single topic."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    product_dir = _get_product_dir()
    config = _load_config()

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    print(f"Topic: [{topic.kind}] {topic.target}", file=sys.stderr)
    print(f"  {topic.title}", file=sys.stderr)
    print(file=sys.stderr)

    prompt = _build_topic_prompt(topic, config, product_dir)

    print(f"Exploring with {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return

    safe_target = re.sub(r"[^a-zA-Z0-9_-]", "-", topic.target)[:80]
    _create_entry(f"explore-{safe_target}", f"Explore: {topic.target}", result)
    _enqueue_topics(result, source=f"explore:{topic.target}", product_dir=product_dir)
    _report_beliefs(result)

    print(result)


def _explore_loop(args, product_dir: str, max_topics: int, max_parallel: int = 1) -> None:
    """Continuously explore topics up to max_topics."""
    if max_parallel > 1:
        _explore_loop_parallel(args, product_dir, max_topics, max_parallel)
        return

    explored = 0
    while explored < max_topics:
        topic = pop_next(product_dir)
        if topic is None:
            if explored == 0:
                print("No pending topics. Run `reasonsforge product scan` to discover topics.")
            else:
                print(f"\nNo more topics after {explored} exploration(s).", file=sys.stderr)
            return

        explored += 1
        remaining = pending_count(product_dir)
        print(f"\n{'=' * 40}", file=sys.stderr)
        print(f"[{explored}/{max_topics}] ({remaining} remaining in queue)", file=sys.stderr)
        print(f"{'=' * 40}", file=sys.stderr)

        _run_topic(args, topic)

    remaining = pending_count(product_dir)
    print(f"\nExplored {explored} topic(s). {remaining} remaining.", file=sys.stderr)


def _explore_loop_parallel(args, product_dir: str, max_topics: int, max_parallel: int) -> None:
    """Explore topics in parallel batches."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    config = _load_config()

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    explored = 0

    while explored < max_topics:
        batch_size = min(max_parallel, max_topics - explored)
        batch_topics = []
        for _ in range(batch_size):
            topic = pop_next(product_dir)
            if topic is None:
                break
            batch_topics.append(topic)

        if not batch_topics:
            if explored == 0:
                print("No pending topics. Run `reasonsforge product scan` to discover topics.")
            else:
                print(f"\nNo more topics after {explored} exploration(s).", file=sys.stderr)
            return

        print(f"\nExploring {len(batch_topics)} topic(s) in parallel...", file=sys.stderr)
        for t in batch_topics:
            print(f"  [{t.kind}] {t.target}: {t.title}", file=sys.stderr)

        prompts = [_build_topic_prompt(t, config, product_dir) for t in batch_topics]

        async def _invoke_all():
            sem = asyncio.Semaphore(max_parallel)

            async def _invoke_one(prompt):
                async with sem:
                    return await invoke(prompt, model, timeout=timeout)

            return await asyncio.gather(
                *[_invoke_one(p) for p in prompts],
                return_exceptions=True,
            )

        results = asyncio.run(_invoke_all())

        for topic, result in zip(batch_topics, results):
            explored += 1
            if isinstance(result, Exception):
                print(f"  ERROR [{topic.target}]: {result}", file=sys.stderr)
                continue
            safe_target = re.sub(r"[^a-zA-Z0-9_-]", "-", topic.target)[:80]
            _create_entry(f"explore-{safe_target}", f"Explore: {topic.target}", result)
            _enqueue_topics(result, source=f"explore:{topic.target}", product_dir=product_dir)
            _report_beliefs(result)
            print(result)

    remaining = pending_count(product_dir)
    print(f"\nExplored {explored} topic(s). {remaining} remaining.", file=sys.stderr)


def _auto_accept_proposals(proposals: list[str], db_path: str = REASONS_DB) -> None:
    """Parse LLM proposals and accept all beliefs directly via reasonsforge.api."""
    from reasonsforge.api import add_node, set_metadata

    accept_pattern = re.compile(
        r"### \[?(?:ACCEPT(?:/REJECT)?|REJECT)\]?\s*(\S+)\n"
        r"(.+?)\n"
        r"- Source: (.+?)(?:\n|$)",
        re.MULTILINE,
    )
    matches = []
    for proposal in proposals:
        matches.extend(accept_pattern.findall(proposal))

    if not matches:
        print("No beliefs extracted from proposals.")
        return

    print(f"Auto-accepting {len(matches)} beliefs...", file=sys.stderr)

    added = 0
    failed = 0
    skipped = 0
    for belief_id, claim_text, source in matches:
        try:
            add_node(belief_id, claim_text.strip(), source=source.strip(), db_path=db_path)
            print(f"  Added: {belief_id}", file=sys.stderr)
            added += 1
            now = datetime.now().isoformat(timespec="seconds")
            try:
                set_metadata(belief_id, "accepted_at", now, db_path=db_path)
            except Exception:
                pass
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str:
                print(f"  EXISTS: {belief_id}", file=sys.stderr)
                skipped += 1
            else:
                print(f"  FAIL: {belief_id}: {e}", file=sys.stderr)
                failed += 1

    print(f"Accepted {added} belief(s) ({skipped} existing, {failed} failed)", file=sys.stderr)


def _entry_date(path: Path) -> str | None:
    """Extract YYYY-MM-DD date from a summaries path like summaries/2026/04/30/foo.md."""
    parts = path.parts
    for i, part in enumerate(parts):
        if part == "summaries" and i + 3 < len(parts):
            try:
                y, m, d = parts[i + 1], parts[i + 2], parts[i + 3]
                if len(y) == 4 and len(m) == 2 and len(d) == 2:
                    return f"{y}-{m}-{d}"
            except (IndexError, ValueError):
                pass
    return None


def _load_existing_beliefs_text(db_path: str = REASONS_DB) -> str:
    """Load existing IN beliefs as compact text for dedup context."""
    from reasonsforge.api import export_network

    try:
        network = export_network(db_path=db_path)
        nodes = network.get("nodes", {})
        lines = []
        for nid, node in sorted(nodes.items()):
            if node.get("truth_value") == "IN":
                text = node.get("text", "")[:120]
                lines.append(f"[+] {nid}: {text}")
        return "\n".join(lines) if lines else "(No existing beliefs)"
    except Exception:
        return "(No existing beliefs)"


def _load_network(db_path: str = REASONS_DB) -> dict:
    """Load network using reasonsforge.api.export_network() directly."""
    from reasonsforge.api import export_network

    try:
        return export_network(db_path=db_path)
    except Exception:
        return {"nodes": {}}


def _parse_all_proposals(text: str) -> list[dict]:
    """Parse all proposals from a proposed-beliefs.md file."""
    pattern = re.compile(
        r"^### \[?(?:ACCEPT(?:/REJECT)?|REJECT)\]? (\S+)\n(.+?)\n- Source: (.+?)(?:\n|$)",
        re.MULTILINE,
    )
    proposals = []
    for m in pattern.finditer(text):
        proposals.append({
            "id": m.group(1),
            "text": m.group(2).strip(),
            "source": m.group(3).strip(),
        })
    return proposals


def _parse_review_response(response: str) -> dict[str, str]:
    """Parse LLM review response into category classifications.

    Expects lines like:
        ### [ok] belief-id
        ### [duplicate] belief-id
    """
    classifications = {}
    class_pattern = re.compile(
        r"^### \[?(ok|meta|duplicate|ephemeral|stale|speculative)\]? (\S+)",
        re.MULTILINE | re.IGNORECASE,
    )
    for m in class_pattern.finditer(response):
        category = m.group(1).lower()
        belief_id = m.group(2)
        classifications[belief_id] = category
    return classifications


def _accept_proposals(matches: list[tuple[str, str, str]], db_path: str) -> tuple[int, int, int]:
    """Accept a list of (belief_id, claim_text, source) tuples via reasonsforge.api."""
    from reasonsforge.api import add_node, set_metadata

    print("Using reasonsforge API as primary store...")
    added = 0
    failed = 0
    skipped = 0
    for belief_id, claim_text, source in matches:
        try:
            add_node(belief_id, claim_text.strip(), source=source.strip(), db_path=db_path)
            print(f"  Added: {belief_id}")
            added += 1
            now = datetime.now().isoformat(timespec="seconds")
            try:
                set_metadata(belief_id, "accepted_at", now, db_path=db_path)
            except Exception:
                pass
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str:
                print(f"  EXISTS: {belief_id}")
                skipped += 1
            else:
                print(f"  FAIL: {belief_id}: {e}")
                failed += 1

    print(f"\nAccepted {added} beliefs ({skipped} existing, {failed} failed)")
    return added, skipped, failed


def _save_scan_checkpoint(product_dir: str | None = None) -> None:
    """Save the current timestamp as the scan checkpoint."""
    pdir = product_dir or str(Path.cwd() / PRODUCT_DIR)
    cp = Path(pdir) / "scan-checkpoint.json"
    os.makedirs(pdir, exist_ok=True)
    cp.write_text(json.dumps({
        "timestamp": datetime.now().isoformat(),
        "project": str(Path.cwd()),
    }, indent=2))


def _load_scan_checkpoint(product_dir: str | None = None) -> str | None:
    """Load the last scan timestamp from checkpoint file."""
    pdir = product_dir or str(Path.cwd() / PRODUCT_DIR)
    cp = Path(pdir) / "scan-checkpoint.json"
    if cp.is_file():
        data = json.loads(cp.read_text())
        return data.get("timestamp")
    return None


def _load_processed(path: Path) -> dict[str, str]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _save_processed(path: Path, entries: list[Path], existing: dict[str, str]):
    import hashlib
    updated = dict(existing)
    for entry_path in entries:
        content = entry_path.read_text()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        updated[str(entry_path)] = content_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2) + "\n")


def _filter_unprocessed(entries: list[Path], processed: dict[str, str]) -> list[Path]:
    import hashlib
    unprocessed = []
    for entry_path in entries:
        key = str(entry_path)
        if key not in processed:
            unprocessed.append(entry_path)
            continue
        content = entry_path.read_text()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        if content_hash != processed[key]:
            unprocessed.append(entry_path)
    return unprocessed


def _load_existing_beliefs(db_path: str) -> list[dict]:
    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        nodes = network.get("nodes", {})
        return [
            {"id": nid, "text": node.get("text", ""), "source": node.get("source", "")}
            for nid, node in nodes.items()
            if node.get("truth_value") == "IN"
        ]
    except Exception:
        return []


def _score_by_keywords(
    beliefs: list[dict],
    batch_text: str,
    batch_entry_paths: list[str],
) -> list[tuple[float, dict]]:
    batch_words = set(re.findall(r'[a-z]{3,}', batch_text.lower()))
    scored = []
    for belief in beliefs:
        score = 0.0
        if belief["source"] and any(belief["source"] in p or p in belief["source"]
                                     for p in batch_entry_paths):
            score += 1000
        belief_words = set(re.findall(r'[a-z]{3,}', belief["text"].lower()))
        belief_words |= set(belief["id"].replace("-", " ").lower().split())
        overlap = len(batch_words & belief_words)
        score += overlap
        scored.append((score, belief))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


def _build_dedup_context(
    existing_beliefs: list[dict],
    batch_entry_paths: list[str],
    batch_text: str,
    max_detailed: int = 50,
    max_compact: int = 200,
) -> str:
    if not existing_beliefs:
        return ""

    scored = _score_by_keywords(existing_beliefs, batch_text, batch_entry_paths)
    detailed = scored[:max_detailed]
    compact = scored[max_detailed:max_detailed + max_compact]

    parts = [
        "\n\n## Already Accepted Beliefs\n\n"
        "The following beliefs already exist. Do NOT propose beliefs with these IDs "
        "or that duplicate their meaning under different names.\n"
    ]
    if detailed:
        parts.append("\nRelevant existing beliefs:")
        for _, belief in detailed:
            parts.append(f"- `{belief['id']}`: {belief['text']}")
    if compact:
        compact_ids = ", ".join(b["id"] for _, b in compact)
        parts.append(f"\nOther existing IDs: {compact_ids}")

    return "\n".join(parts) + "\n"


def _find_gated_out_beliefs(nodes: dict) -> list[dict]:
    """Find gated OUT beliefs and their active blockers."""
    results = []
    for nid, node in nodes.items():
        if node.get("truth_value") != "OUT":
            continue
        if node.get("metadata", {}).get("superseded_by"):
            continue
        for j in node.get("justifications", []):
            if not j.get("outlist"):
                continue
            active_blockers = [
                oid for oid in j["outlist"]
                if oid in nodes and nodes[oid].get("truth_value") == "IN"
            ]
            if active_blockers:
                results.append({
                    "id": nid,
                    "text": node.get("text", ""),
                    "blockers": [
                        {"id": bid, "text": nodes[bid].get("text", "")}
                        for bid in active_blockers
                    ],
                })
                break
    return results


def _find_negative_in_beliefs(nodes: dict) -> list[dict]:
    """Find IN beliefs with negative-signal keywords."""
    results = []
    for nid, node in nodes.items():
        if node.get("truth_value") != "IN":
            continue
        text = node.get("text", "")
        if _NEGATIVE_KEYWORDS.search(text):
            results.append({"id": nid, "text": text})
    return results


def _format_gated_section(beliefs: list[dict]) -> str:
    if not beliefs:
        return "_None_\n"
    lines = []
    for b in beliefs:
        lines.append(f"- **{b['id']}**: {b['text']}")
        for blocker in b["blockers"]:
            lines.append(f"  - Blocked by: `{blocker['id']}` — {blocker['text']}")
    return "\n".join(lines) + "\n"


def _format_belief_list(beliefs: list[dict]) -> str:
    if not beliefs:
        return "_None_\n"
    lines = []
    for b in beliefs:
        lines.append(f"- **{b['id']}**: {b['text']}")
    return "\n".join(lines) + "\n"


def _save_update_checkpoint(product_dir: str) -> None:
    """Save current timestamp as the update checkpoint."""
    checkpoint = {
        "timestamp": datetime.now().isoformat(),
        "since": date.today().isoformat(),
    }
    checkpoint_path = os.path.join(product_dir, "last-update.json")
    os.makedirs(product_dir, exist_ok=True)
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)


def _load_update_checkpoint(product_dir: str) -> str | None:
    """Load the since date from the last update checkpoint."""
    checkpoint_path = os.path.join(product_dir, "last-update.json")
    if not os.path.isfile(checkpoint_path):
        return None
    with open(checkpoint_path) as f:
        data = json.load(f)
    return data.get("since")


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args):
    """Bootstrap a product forge knowledge base for an issue tracker.

    Creates .forge/product/config.json, summaries/ dir, and initializes
    reasons.db.
    """
    from reasonsforge.api import init_db

    # Determine platform and target from args
    platform = None
    target = None
    if getattr(args, "github", None):
        platform = "github"
        target = args.github
    elif getattr(args, "gitlab", None):
        platform = "gitlab"
        target = args.gitlab
    elif getattr(args, "jira", None):
        platform = "jira"
        target = args.jira
    else:
        print("Error: specify --github, --gitlab, or --jira target", file=sys.stderr)
        sys.exit(1)

    domain = getattr(args, "domain", None) or target
    jira_url = getattr(args, "jira_url", None)
    db_path = getattr(args, "output", REASONS_DB)

    # Validate platform prerequisites
    if platform == "jira":
        if not jira_url and not os.environ.get("JIRA_URL"):
            print("Error: --jira-url or JIRA_URL env var required for Jira", file=sys.stderr)
            sys.exit(1)

    # Create product dir
    product_dir = Path.cwd() / PRODUCT_DIR
    product_dir.mkdir(parents=True, exist_ok=True)

    # Save config
    config = {
        "platform": platform,
        "domain": domain,
        "created": date.today().isoformat(),
    }
    if platform in ("github", "gitlab"):
        config["repo"] = target
    else:
        config["project"] = target
        config["jira_url"] = jira_url or os.environ.get("JIRA_URL", "")

    _save_config(config)

    # Create summaries dir
    Path("summaries").mkdir(exist_ok=True)

    # Init belief store
    if not Path(db_path).exists():
        init_db(db_path=db_path)
        print("Initialized reasons database")
    else:
        print(f"{db_path} already exists, skipping init")

    # Create .gitignore
    gitignore = Path.cwd() / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("reasons.db\nrag_fts.db\n")
        print("Created .gitignore")

    print(f"\nProduct forge initialized")
    print(f"  Platform: {platform}")
    print(f"  Target:   {target}")
    print(f"  Domain:   {domain}")
    print(f"\nNext: reasonsforge product scan")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def cmd_scan(args):
    """Scan product issues and create a product-focused overview."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge product init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    product_dir = _get_product_dir()

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    source = _get_source(config)
    labels_raw = getattr(args, "labels", None)
    label_list = [l.strip() for l in labels_raw.split(",") if l.strip()] if labels_raw else None

    state = getattr(args, "state", None)
    limit = getattr(args, "limit", 100)
    page = getattr(args, "page", 1)
    all_pages = getattr(args, "all_pages", False)
    jql = getattr(args, "jql", None)

    # Resolve --since / --since-last
    since_date = None
    since_last = getattr(args, "since_last", False)
    since_str = getattr(args, "since", None)

    if since_last:
        ts = _load_scan_checkpoint(product_dir)
        if not ts:
            print("No scan checkpoint found. Run a scan first, or use --since.", file=sys.stderr)
            sys.exit(1)
        since_date = _parse_since_date(ts)
        print(f"Scanning issues updated since {since_date.isoformat()}", file=sys.stderr)
    elif since_str:
        since_date = _parse_since_date(since_str)
        print(f"Scanning issues updated since {since_date.isoformat()}", file=sys.stderr)

    # Set default state per platform
    if state is None:
        if config["platform"] == "gitlab":
            state = "opened"
        elif config["platform"] == "jira":
            state = None  # Jira uses JQL
        else:
            state = "open"

    project_name = config.get("repo", config.get("project", "unknown"))

    if all_pages:
        current_page = 1
        total_scanned = 0
        while True:
            print(f"\n{'=' * 40}", file=sys.stderr)
            print(f"Page {current_page}", file=sys.stderr)
            print(f"{'=' * 40}", file=sys.stderr)

            count = _scan_page(
                config, source, model, timeout, product_dir, project_name,
                state, label_list, limit, current_page, jql, since_date,
            )
            if count == 0:
                if total_scanned == 0:
                    print("No issues found.")
                else:
                    print(
                        f"\nDone. Scanned {total_scanned} issues across {current_page - 1} pages.",
                        file=sys.stderr,
                    )
                break
            total_scanned += count
            if count < limit:
                print(
                    f"\nDone. Scanned {total_scanned} issues across {current_page} pages.",
                    file=sys.stderr,
                )
                break
            current_page += 1
    else:
        _scan_page(
            config, source, model, timeout, product_dir, project_name,
            state, label_list, limit, page, jql, since_date,
        )

    _save_scan_checkpoint(product_dir)


def _scan_page(config, source, model, timeout, product_dir, project_name,
               state, label_list, limit, page, jql, since_date=None):
    """Scan a single page of issues. Returns the number of issues fetched."""
    from ..llm import invoke

    print(f"Scanning {project_name} (page {page})...", file=sys.stderr)

    try:
        if config["platform"] == "jira":
            issues = source.list_issues(jql=jql, state=state, labels=label_list,
                                         limit=limit, page=page)
        elif config["platform"] == "gitlab":
            issues = source.list_issues(state=state, labels=label_list,
                                         limit=limit, page=page)
        else:
            issues = source.list_issues(state=state, labels=label_list, limit=limit)
    except Exception as e:
        print(f"Error fetching issues: {e}", file=sys.stderr)
        sys.exit(1)

    if not issues:
        return 0

    fetched_count = len(issues)
    if since_date:
        naive_since = since_date.replace(tzinfo=None)
        issues = [i for i in issues if _parse_issue_updated(i.updated) >= naive_since]
        print(f"Fetched {fetched_count} issues, {len(issues)} after --since filter", file=sys.stderr)
        if not issues:
            return 0
    else:
        print(f"Fetched {len(issues)} issues", file=sys.stderr)

    # No PR support in product forge
    issues_text = "\n\n".join(issue.to_prompt_text() for issue in issues)

    prompt = build_scan_prompt(
        issues_text=issues_text,
        project_name=project_name,
        platform=config["platform"],
        issue_count=len(issues),
    )

    print(f"Running {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    short_name = project_name.split("//")[-1] if "//" in project_name else project_name
    safe_name = short_name.replace("/", "-")
    page_suffix = f"-p{page}" if page > 1 else ""
    _create_entry(f"scan-{safe_name}{page_suffix}",
                  f"Scan: {project_name} (page {page})", result)
    _enqueue_topics(result, source=f"scan:{project_name}", product_dir=product_dir)
    _report_beliefs(result)
    _cache_issues(issues, product_dir)

    print(result)
    return len(issues)


# ---------------------------------------------------------------------------
# ingest
# ---------------------------------------------------------------------------


def cmd_ingest(args):
    """Ingest markdown documents for product analysis.

    Reads markdown files from a directory, analyzes each through a product lens,
    creates entries, and queues follow-up topics.
    """
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge product init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    product_dir = _get_product_dir()

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    docs_dir = getattr(args, "docs_dir", None)
    glob_pattern = getattr(args, "glob_pattern", "**/*.md")

    if not docs_dir:
        print("Error: docs_dir is required", file=sys.stderr)
        sys.exit(1)

    docs_path = Path(docs_dir)
    if not docs_path.exists():
        print(f"Error: directory not found: {docs_dir}", file=sys.stderr)
        sys.exit(1)

    files = sorted(docs_path.glob(glob_pattern))

    if not files:
        print(f"No files matching '{glob_pattern}' in {docs_dir}")
        return

    print(f"Found {len(files)} document(s) to ingest", file=sys.stderr)

    for i, file_path in enumerate(files):
        print(f"\n{'=' * 40}", file=sys.stderr)
        print(f"[{i + 1}/{len(files)}] {file_path.name}", file=sys.stderr)
        print(f"{'=' * 40}", file=sys.stderr)

        content = file_path.read_text()
        if not content.strip():
            print(f"  Skipping empty file", file=sys.stderr)
            continue

        if len(content) > 50000:
            content = content[:50000] + f"\n\n[Truncated -- original was {len(content)} chars]"

        prompt = build_ingest_prompt(
            doc_text=content,
            doc_name=file_path.name,
        )

        print(f"Analyzing with {model}...", file=sys.stderr)
        try:
            result = asyncio.run(invoke(prompt, model, timeout=timeout))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            continue

        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "-", file_path.stem)[:80]
        _create_entry(f"ingest-{safe_name}", f"Ingest: {file_path.name}", result)
        _enqueue_topics(result, source=f"ingest:{file_path.name}", product_dir=product_dir)
        _report_beliefs(result)

        print(result)

    print(f"\nIngested {len(files)} document(s).", file=sys.stderr)


# ---------------------------------------------------------------------------
# explore
# ---------------------------------------------------------------------------


def cmd_explore(args):
    """Explore the next topic in the queue."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available
    _caffeinate()

    product_dir = _get_product_dir()
    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    do_skip = getattr(args, "skip", False)
    pick_index = getattr(args, "pick", None)
    loop_max = getattr(args, "loop", None)
    max_parallel = getattr(args, "parallel", 1)

    if loop_max is not None:
        if do_skip or pick_index:
            print("Error: --loop cannot be combined with --skip or --pick", file=sys.stderr)
            sys.exit(1)
        _explore_loop(args, product_dir, loop_max, max_parallel)
        return

    if do_skip:
        if skip_topic(0, product_dir):
            queue = load_queue(product_dir)
            pending = [t for t in queue if t.status == "pending"]
            if pending:
                print(f"Skipped. Next: [{pending[0].kind}] {pending[0].target}")
            else:
                print("Skipped. No more pending topics.")
        else:
            print("Nothing to skip.")
        return

    if pick_index is not None:
        try:
            indices = [int(x.strip()) for x in pick_index.split(",")]
        except ValueError:
            print(f"Error: --pick must be integers, got: {pick_index}", file=sys.stderr)
            sys.exit(1)
        if len(indices) > 1:
            topic_list = pop_multiple(indices, product_dir)
        else:
            topic_list = [pop_at(indices[0], product_dir)]
    else:
        topic_list = [pop_next(product_dir)]

    valid_topics = [(i, t) for i, t in zip(
        indices if pick_index is not None else [0],
        topic_list,
    ) if t is not None]

    if not valid_topics:
        print("No pending topics. Run `reasonsforge product scan` to discover topics.")
        return

    invalid_count = len(topic_list) - len(valid_topics)
    if invalid_count:
        print(f"Warning: {invalid_count} index(es) out of bounds, skipped.", file=sys.stderr)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    if max_parallel > 1 and len(valid_topics) > 1:
        from ..llm import invoke

        config = _load_config()
        topics_only = [t for _, t in valid_topics]
        print(f"Exploring {len(topics_only)} topic(s) in parallel...", file=sys.stderr)
        for t in topics_only:
            print(f"  [{t.kind}] {t.target}: {t.title}", file=sys.stderr)

        prompts = [_build_topic_prompt(t, config, product_dir) for t in topics_only]

        async def _invoke_all():
            sem = asyncio.Semaphore(max_parallel)

            async def _invoke_one(prompt):
                async with sem:
                    return await invoke(prompt, model, timeout=timeout)

            return await asyncio.gather(
                *[_invoke_one(p) for p in prompts],
                return_exceptions=True,
            )

        results = asyncio.run(_invoke_all())

        for topic, result in zip(topics_only, results):
            if isinstance(result, Exception):
                print(f"  ERROR [{topic.target}]: {result}", file=sys.stderr)
                continue
            safe_target = re.sub(r"[^a-zA-Z0-9_-]", "-", topic.target)[:80]
            _create_entry(f"explore-{safe_target}", f"Explore: {topic.target}", result)
            _enqueue_topics(result, source=f"explore:{topic.target}", product_dir=product_dir)
            _report_beliefs(result)
            print(result)
    else:
        for seq, (idx, topic) in enumerate(valid_topics):
            if len(valid_topics) > 1:
                print(f"\n{'=' * 40}", file=sys.stderr)
                print(f"[{seq + 1}/{len(valid_topics)}] Topic #{idx}", file=sys.stderr)
                print(f"{'=' * 40}", file=sys.stderr)

            _run_topic(args, topic)

    remaining = pending_count(product_dir)
    if remaining:
        print(f"\n{remaining} topic(s) remaining.", file=sys.stderr)
    else:
        print("\nNo more topics. Exploration complete.", file=sys.stderr)


# ---------------------------------------------------------------------------
# propose-beliefs
# ---------------------------------------------------------------------------


def cmd_propose_beliefs(args):
    """Extract candidate beliefs from entries."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    batch_size = getattr(args, "batch_size", 5)
    output = getattr(args, "proposals_output", None) or getattr(args, "output_file", "proposed-beliefs.md")
    process_all = getattr(args, "all", False)
    auto_accept = getattr(args, "auto", False)
    since = getattr(args, "since", None)
    parallel = getattr(args, "parallel", 1)
    db_path = getattr(args, "output", REASONS_DB)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    input_dir = Path("summaries")
    if not input_dir.exists():
        print("No summaries/ directory found. Run explorations first.")
        sys.exit(1)
    entries = sorted(input_dir.rglob("*.md"))

    if not entries:
        print("No .md files found.")
        return

    if since:
        before = len(entries)
        entries = [e for e in entries if (_entry_date(e) or "") >= since]
        print(f"Filtered to {len(entries)} entries since {since} (from {before} total)", file=sys.stderr)
        if not entries:
            print("No entries found since that date.")
            return

    # Filter already-processed
    processed_path = Path(PRODUCT_DIR) / "proposed-entries.json"
    processed = _load_processed(processed_path)
    if not process_all:
        total = len(entries)
        entries = _filter_unprocessed(entries, processed)
        skipped_count = total - len(entries)
        if skipped_count:
            print(f"Skipping {skipped_count} already-processed entries (use --all to reprocess)")
        if not entries:
            print("No new entries to process.")
            return

    # Load existing beliefs from reasons.db for dedup
    existing_beliefs = _load_existing_beliefs(db_path)
    existing_ids = {b["id"] for b in existing_beliefs}
    if existing_ids:
        print(f"Found {len(existing_ids)} existing beliefs (will skip duplicates)")

    print(f"Reading {len(entries)} entries...")

    batches = []
    batch_paths = []
    current_batch = []
    current_paths = []
    for entry_path in entries:
        content = entry_path.read_text()
        if len(content) > 10000:
            content = content[:10000] + "\n[Truncated]"
        current_batch.append(f"--- FILE: {entry_path} ---\n{content}")
        current_paths.append(str(entry_path))
        if len(current_batch) >= batch_size:
            batches.append("\n\n".join(current_batch))
            batch_paths.append(current_paths)
            current_batch = []
            current_paths = []
    if current_batch:
        batches.append("\n\n".join(current_batch))
        batch_paths.append(current_paths)

    print(f"Processing {len(batches)} batches (batch size: {batch_size})...")

    all_proposals = []

    if parallel > 1 and len(batches) > 1:
        prompts = []
        for i, batch_text in enumerate(batches):
            existing_context = _build_dedup_context(existing_beliefs, batch_paths[i], batch_text)
            prompts.append(PROPOSE_BELIEFS_PRODUCT.format(entries=batch_text) + existing_context)

        async def _invoke_all():
            sem = asyncio.Semaphore(parallel)

            async def _invoke_one(prompt):
                async with sem:
                    return await invoke(prompt, model, timeout=timeout)

            return await asyncio.gather(
                *[_invoke_one(p) for p in prompts],
                return_exceptions=True,
            )

        results = asyncio.run(_invoke_all())
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                print(f"  Batch {i + 1} ERROR: {r}")
            else:
                all_proposals.append(r)
    else:
        for i, batch_text in enumerate(batches):
            existing_context = _build_dedup_context(existing_beliefs, batch_paths[i], batch_text)
            prompt = PROPOSE_BELIEFS_PRODUCT.format(entries=batch_text) + existing_context

            print(f"  Batch {i + 1}/{len(batches)}...", file=sys.stderr)
            try:
                result = asyncio.run(invoke(prompt, model, timeout=timeout))
                print(f"  Batch {i + 1}/{len(batches)} done")
                all_proposals.append(result)
            except Exception as e:
                print(f"  ERROR in batch {i + 1}: {e}")

    # Filter already-existing proposals
    filtered_proposals = []
    dup_skipped = 0
    for proposal in all_proposals:
        lines = proposal.split("\n")
        filtered_lines = []
        skip_until_next = False
        for line in lines:
            m = re.match(r"^### \[?(?:ACCEPT|REJECT)\]? (\S+)", line)
            if m:
                belief_id = m.group(1)
                if belief_id in existing_ids:
                    skip_until_next = True
                    dup_skipped += 1
                    continue
                else:
                    skip_until_next = False
            if skip_until_next:
                if line.startswith("### "):
                    skip_until_next = False
                    filtered_lines.append(line)
                continue
            filtered_lines.append(line)
        filtered_proposals.append("\n".join(filtered_lines))

    if dup_skipped:
        print(f"  Filtered {dup_skipped} already-accepted beliefs")

    _save_processed(processed_path, entries, processed)

    if auto_accept:
        _auto_accept_proposals(filtered_proposals, db_path)
        return

    # Write proposals file
    source_desc = f"{len(entries)} summaries from summaries/"
    output_path = Path(output)
    if output_path.exists() and output_path.stat().st_size > 0:
        with output_path.open("a") as f:
            f.write(f"\n---\n\n")
            f.write(f"**Generated:** {date.today().isoformat()}\n")
            f.write(f"**Source:** {source_desc}\n")
            f.write(f"**Model:** {model}\n\n")
            for proposal in filtered_proposals:
                f.write(proposal)
                f.write("\n\n")
        print(f"\nAppended to {output_path}")
    else:
        with output_path.open("w") as f:
            f.write("# Proposed Beliefs\n\n")
            f.write("Edit each entry: change `[ACCEPT/REJECT]` to `[ACCEPT]` or `[REJECT]`.\n")
            f.write("Then run: `reasonsforge product accept-beliefs`\n\n")
            f.write("---\n\n")
            f.write(f"**Generated:** {date.today().isoformat()}\n")
            f.write(f"**Source:** {source_desc}\n")
            f.write(f"**Model:** {model}\n\n")
            for proposal in filtered_proposals:
                f.write(proposal)
                f.write("\n\n")
        print(f"\nWrote {output_path}")

    print("Review the file, mark entries as [ACCEPT] or [REJECT], then run:")
    print("  reasonsforge product accept-beliefs")


# ---------------------------------------------------------------------------
# accept-beliefs
# ---------------------------------------------------------------------------


def cmd_accept_beliefs(args):
    """Import accepted beliefs from proposals file."""
    proposals_file = getattr(args, "proposals_file", "proposed-beliefs.md")
    db_path = getattr(args, "output", REASONS_DB)

    proposals_path = Path(proposals_file)
    if not proposals_path.exists():
        print(f"Proposals file not found: {proposals_file}")
        print("Run: reasonsforge product propose-beliefs")
        sys.exit(1)

    text = proposals_path.read_text()
    pattern = re.compile(
        r"### \[?ACCEPT\]? (\S+)\n"
        r"(.+?)\n"
        r"- Source: (.+?)(?:\n|$)"
    )
    matches = pattern.findall(text)

    if not matches:
        print("No [ACCEPT] entries found in proposals file.")
        print("Edit the file and change [ACCEPT/REJECT] to [ACCEPT] for beliefs to keep.")
        return

    print(f"Found {len(matches)} accepted beliefs")
    _accept_proposals(matches, db_path)


# ---------------------------------------------------------------------------
# review-proposals
# ---------------------------------------------------------------------------


def cmd_review_proposals(args):
    """Filter low-quality belief proposals using LLM review.

    Classifies each proposal as ok, meta, duplicate, ephemeral, stale,
    or speculative. Rewrites the proposals file with ACCEPT for ok beliefs
    and REJECT for flagged ones.
    """
    from ..llm import check_model_available, invoke_sync

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)
    proposals_file = getattr(args, "proposals_file", "proposed-beliefs.md")
    batch_size = getattr(args, "batch_size", 20)

    proposals_path = Path(proposals_file)
    if not proposals_path.exists():
        print(f"Proposals file not found: {proposals_file}")
        print("Run: reasonsforge product propose-beliefs")
        sys.exit(1)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    proposals = _parse_all_proposals(proposals_path.read_text())
    if not proposals:
        print("No proposals found in file.")
        return

    print(f"Reviewing {len(proposals)} proposals...", file=sys.stderr)

    existing_beliefs = _load_existing_beliefs_text(db_path)

    # Batch proposals for LLM review
    classifications = {}
    batches = [proposals[i:i + batch_size] for i in range(0, len(proposals), batch_size)]

    for i, batch in enumerate(batches):
        print(f"  Batch {i + 1}/{len(batches)} ({len(batch)} proposals)...", file=sys.stderr)

        proposals_text = "\n\n".join(
            f"### [ACCEPT/REJECT] {p['id']}\n{p['text']}\n- Source: {p['source']}"
            for p in batch
        )

        prompt = REVIEW_PROPOSALS_PROMPT.format(
            existing_beliefs=existing_beliefs,
            proposals=proposals_text,
        )

        try:
            result = invoke_sync(prompt, model=model, timeout=timeout)
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            for p in batch:
                classifications[p["id"]] = "ok"
            continue

        # Parse classifications from response
        batch_classifications = _parse_review_response(result)
        classifications.update(batch_classifications)

        # Any proposals not classified default to ok
        for p in batch:
            if p["id"] not in classifications:
                classifications[p["id"]] = "ok"

    # Count by category
    from collections import Counter
    counts = Counter(classifications.values())
    ok_count = counts.get("ok", 0)
    rejected_count = len(proposals) - ok_count

    print(f"\nResults: {ok_count} ok, {rejected_count} rejected", file=sys.stderr)
    for cat in ["meta", "duplicate", "ephemeral", "stale", "speculative"]:
        if counts.get(cat, 0) > 0:
            print(f"  {cat}: {counts[cat]}", file=sys.stderr)

    # Rewrite proposals file
    with proposals_path.open("w") as f:
        f.write("# Proposed Beliefs (Reviewed)\n\n")
        f.write(f"**Reviewed:** {date.today().isoformat()}\n")
        f.write(f"**Model:** {model}\n")
        f.write(f"**Results:** {ok_count} accepted, {rejected_count} rejected\n\n---\n\n")

        for p in proposals:
            category = classifications.get(p["id"], "ok")
            if category == "ok":
                f.write(f"### [ACCEPT] {p['id']}\n")
            else:
                f.write(f"### [REJECT] {p['id']}\n")
            f.write(f"{p['text']}\n")
            f.write(f"- Source: {p['source']}\n")
            if category != "ok":
                f.write(f"- Quality: {category}\n")
            f.write("\n")

    print(f"Rewrote {proposals_path}")
    print("Run: reasonsforge product accept-beliefs")


# ---------------------------------------------------------------------------
# derive
# ---------------------------------------------------------------------------


def cmd_derive(args):
    """Derive deeper reasoning chains from existing beliefs.

    Uses reasonsforge.derive.build_prompt(), parse_proposals(),
    validate_proposals(), and apply_proposals() -- matching the project forge
    pattern.
    """
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke_sync
    from reasonsforge.derive import apply_proposals, build_prompt, parse_proposals, validate_proposals
    from reasonsforge.api import export_network
    _caffeinate()

    model = getattr(args, "model", "claude")
    timeout = max(getattr(args, "timeout", 300), 600)
    db_path = getattr(args, "output", REASONS_DB)
    auto_add = getattr(args, "auto", False)
    exhaust = getattr(args, "exhaust", False)
    max_rounds = getattr(args, "max_derive_rounds", 10)
    budget = getattr(args, "budget", 300)
    domain = getattr(args, "domain", None)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    if exhaust:
        auto_add = True

    total_added = 0
    for round_num in range(1, max_rounds + 1):
        if exhaust:
            print(f"\n--- Derive round {round_num}/{max_rounds} ---", file=sys.stderr)

        try:
            network = export_network(db_path=db_path)
            nodes = network.get("nodes", {})
        except Exception as e:
            print(f"Error loading network: {e}", file=sys.stderr)
            sys.exit(1)

        prompt = build_prompt(nodes, domain=domain, budget=budget, sample=True)

        try:
            response = invoke_sync(prompt, model=model, timeout=timeout)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        proposals = parse_proposals(response)
        if not proposals:
            print("No proposals generated.", file=sys.stderr)
            if exhaust:
                print(f"Exhausted after {round_num} round(s), {total_added} total derivation(s).",
                      file=sys.stderr)
            break

        valid, _skipped = validate_proposals(proposals, nodes)
        if not valid:
            print("No valid proposals after validation.", file=sys.stderr)
            if exhaust:
                print(f"Exhausted after {round_num} round(s), {total_added} total derivation(s).",
                      file=sys.stderr)
            break

        print(f"Generated {len(valid)} valid derivation(s)", file=sys.stderr)

        if auto_add:
            results = apply_proposals(valid, db_path=db_path)
            added = sum(1 for _, r in results if isinstance(r, dict))
            print(f"Added {added} derivation(s) to {db_path}", file=sys.stderr)
            total_added += added
            if added == 0 and exhaust:
                print(f"Exhausted after {round_num} round(s), {total_added} total derivation(s).",
                      file=sys.stderr)
                break
        else:
            for v in valid:
                print(f"  {v['id']}: {v['text'][:80]}", file=sys.stderr)
            break

        if not exhaust:
            break

    if exhaust and total_added > 0:
        print(f"\nDerived {total_added} belief(s) total.", file=sys.stderr)


# ---------------------------------------------------------------------------
# generate-summary
# ---------------------------------------------------------------------------


def cmd_generate_summary(args):
    """Generate a summary entry of belief state (no LLM).

    Highlights gated OUT beliefs, negative IN beliefs,
    critical issues, and statistics.
    """
    db_path = getattr(args, "output", REASONS_DB)
    snapshot_ids = getattr(args, "snapshot_ids", None)

    network = _load_network(db_path)
    nodes = network.get("nodes", {})
    if not nodes:
        print("No beliefs found. Run explorations first.", file=sys.stderr)
        sys.exit(1)

    pre_run_ids = set(snapshot_ids) if snapshot_ids else set()

    all_gated = _find_gated_out_beliefs(nodes)
    all_negative = _find_negative_in_beliefs(nodes)

    if pre_run_ids:
        new_gated = [b for b in all_gated if b["id"] not in pre_run_ids]
        new_negative = [b for b in all_negative if b["id"] not in pre_run_ids]
    else:
        new_gated = all_gated
        new_negative = all_negative

    critical_gated = [b for b in all_gated if _CRITICAL_KEYWORDS.search(b["text"])
                      or any(_CRITICAL_KEYWORDS.search(bl["text"]) for bl in b["blockers"])]
    critical_negative = [b for b in all_negative if _CRITICAL_KEYWORDS.search(b["text"])]

    total_in = sum(1 for n in nodes.values() if n.get("truth_value") == "IN")
    total_out = sum(1 for n in nodes.values() if n.get("truth_value") == "OUT")
    total_derived = sum(1 for n in nodes.values()
                        if n.get("justifications") and len(n["justifications"]) > 0)

    content = f"## New Gated OUT Beliefs\n\n{_format_gated_section(new_gated)}"
    content += f"\n## New Negative IN Beliefs\n\n{_format_belief_list(new_negative)}"
    content += "\n## Critical Watch List\n\n"

    if critical_gated or critical_negative:
        if critical_gated:
            content += f"### Gated (blocked)\n\n{_format_gated_section(critical_gated)}\n"
        if critical_negative:
            content += f"### Active Issues\n\n{_format_belief_list(critical_negative)}\n"
    else:
        content += "_No critical issues detected._\n"

    content += "\n## Statistics\n\n"
    content += f"- **Total beliefs:** {len(nodes)}\n"
    content += f"- **IN:** {total_in}\n"
    content += f"- **OUT:** {total_out}\n"
    content += f"- **Derived:** {total_derived}\n"
    content += f"- **Gated OUT (all):** {len(all_gated)}\n"
    content += f"- **Negative IN (all):** {len(all_negative)}\n"
    if pre_run_ids:
        content += f"- **New beliefs this run:** {len(nodes) - len(pre_run_ids)}\n"
        content += f"- **New gated OUT:** {len(new_gated)}\n"
        content += f"- **New negative IN:** {len(new_negative)}\n"

    _create_entry("update", "Update Summary", content)
    print(
        f"\nSummary: {len(new_gated)} new gated OUT, {len(new_negative)} new negative IN, "
        f"{len(critical_gated) + len(critical_negative)} critical",
        file=sys.stderr,
    )


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def cmd_summary(args):
    """Synthesize a product summary from beliefs."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge product init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    # Read beliefs from reasons.db, sort by impact (dependents count)
    max_beliefs = 500
    beliefs_text = ""
    belief_count = 0

    if Path(db_path).exists():
        try:
            from reasonsforge.api import export_network
            network = export_network(db_path=db_path)
            nodes = network.get("nodes", {})

            # Filter IN nodes and sort by dependents count
            in_nodes = []
            for nid, node in nodes.items():
                if node.get("truth_value") == "IN":
                    dep_count = len(node.get("dependents", []))
                    in_nodes.append((nid, node, dep_count))

            in_nodes.sort(key=lambda x: -x[2])

            if len(in_nodes) > max_beliefs:
                in_nodes = in_nodes[:max_beliefs]

            lines = []
            for nid, node, dep_count in in_nodes:
                text = node.get("text", "")
                dep_suffix = f" (dependents: {dep_count})" if dep_count > 0 else ""
                lines.append(f"- `{nid}`: {text}{dep_suffix}")

            beliefs_text = "\n".join(lines)
            belief_count = len(in_nodes)
        except Exception as e:
            print(f"Warning: Could not load beliefs: {e}", file=sys.stderr)

    if not beliefs_text or belief_count == 0:
        print("No beliefs found. Run the pipeline first:")
        print("  reasonsforge product scan")
        print("  reasonsforge product propose-beliefs --auto")
        sys.exit(1)

    print(f"Summarizing {belief_count} beliefs with {model}...", file=sys.stderr)

    project_name = config.get("repo", config.get("project", "unknown"))

    prompt = build_summary_prompt(
        beliefs_text=beliefs_text,
        project_name=project_name,
        belief_count=belief_count,
    )

    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    short_name = project_name.split("//")[-1] if "//" in project_name else project_name
    safe_name = short_name.replace("/", "-")
    _create_entry(f"summary-{safe_name}", f"Summary: {project_name}", result)

    print(result)


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------


def cmd_topics(args):
    """Show the exploration queue."""
    product_dir = _get_product_dir()
    queue = load_queue(product_dir)

    if not queue:
        print("No topics queued. Run `reasonsforge product scan` to discover topics.")
        return

    pending = [t for t in queue if t.status == "pending"]
    done = [t for t in queue if t.status == "done"]
    skipped = [t for t in queue if t.status == "skipped"]

    show_all = getattr(args, "all", False)

    if pending:
        print(f"Pending ({len(pending)}):\n")
        for i, topic in enumerate(pending):
            print(f"  {i}. [{topic.kind}] {topic.target}")
            print(f"     {topic.title}")
            if topic.source:
                print(f"     (from {topic.source})")
            print()
    else:
        print("No pending topics.")

    if show_all:
        if done:
            print(f"Done ({len(done)}):\n")
            for topic in done:
                print(f"  [{topic.kind}] {topic.target} - {topic.title}")
        if skipped:
            print(f"\nSkipped ({len(skipped)}):\n")
            for topic in skipped:
                print(f"  [{topic.kind}] {topic.target} - {topic.title}")

    print(f"\n{len(pending)} pending, {len(done)} done, {len(skipped)} skipped")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args):
    """Show product forge dashboard."""
    config = _load_config()

    print("=== Product Forge Status ===\n")

    if config:
        print(f"Platform: {config.get('platform', 'unknown')}")
        print(f"Target:   {config.get('repo', config.get('project', 'unknown'))}")
        print(f"Domain:   {config.get('domain', 'unknown')}")
        print(f"Created:  {config.get('created', 'unknown')}")
    else:
        print("Not initialized. Run: reasonsforge product init --github <owner/repo>")
        return

    print()

    # Summaries
    summaries_dir = Path("summaries")
    summary_count = len(list(summaries_dir.rglob("*.md"))) if summaries_dir.exists() else 0
    print(f"Summaries: {summary_count}")

    # Beliefs
    db_path = getattr(args, "output", REASONS_DB)
    if Path(db_path).exists():
        try:
            from reasonsforge.api import export_network
            network = export_network(db_path=db_path)
            nodes = network.get("nodes", {})
            r_in = sum(1 for n in nodes.values() if n.get("truth_value") == "IN")
            r_out = sum(1 for n in nodes.values() if n.get("truth_value") == "OUT")
            print(f"Beliefs:  {r_in} IN, {r_out} OUT")
        except Exception:
            print("Beliefs:  (error reading database)")
    else:
        print("Beliefs:  (no database)")

    # Topics
    product_dir = _get_product_dir()
    queue = load_queue(product_dir)
    pending = sum(1 for t in queue if t.status == "pending")
    done_count = sum(1 for t in queue if t.status == "done")
    skipped = sum(1 for t in queue if t.status == "skipped")
    print(f"Topics:   {pending} pending, {done_count} done, {skipped} skipped")

    # Cached issues
    cached = _load_cached_issues(product_dir)
    if cached:
        print(f"Cached:   {len(cached)} issues")

    # Proposals
    proposals_path = Path("proposed-beliefs.md")
    if proposals_path.exists():
        text = proposals_path.read_text()
        total = len(re.findall(r"^### \[(?:ACCEPT|REJECT|ACCEPT/REJECT)\]", text, re.MULTILINE))
        accepted = len(re.findall(r"^### \[ACCEPT\]", text, re.MULTILINE))
        if total:
            print(f"Proposed: {total} candidates ({accepted} accepted)")


# ---------------------------------------------------------------------------
# update (pipeline orchestrator)
# ---------------------------------------------------------------------------


def cmd_update(args):
    """Automated update pipeline: scan, explore, propose, review, accept, derive, generate-summary.

    Runs the full 7-step pipeline in one command:
      1. scan --all-pages (with optional --since filtering)
      2. explore --loop (drain pending topics)
      3. propose-beliefs (extract candidate beliefs)
      4. review-proposals (quality filter)
      5. accept-beliefs (import reviewed beliefs)
      6. derive --exhaust (compute all logical consequences)
      7. generate-summary (update report entry)
    """
    import argparse as _argparse
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge product init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    product_dir = _get_product_dir()
    db_path = getattr(args, "output", REASONS_DB)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    # Resolve since date
    since_str = getattr(args, "since", None)
    since_last = getattr(args, "since_last", False)
    limit = getattr(args, "limit", 100)

    errors = []

    # Snapshot current node IDs before any changes
    try:
        network = _load_network(db_path)
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    # --- Step 1: Scan issues ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 1: Scanning issues", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        scan_args = _argparse.Namespace(
            model=model, timeout=timeout, state=None, labels=None,
            limit=limit, page=1, all_pages=True, jql=None,
            since=since_str, since_last=since_last, output=db_path,
        )
        cmd_scan(scan_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"scan exited with code {e.code}")
            print(f"WARN: scan failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"scan: {e}")
        print(f"WARN: scan failed: {e}, continuing...", file=sys.stderr)

    # --- Step 2: Explore all pending topics ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 2: Exploring pending topics", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        _explore_loop(args, product_dir, 99)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"explore exited with code {e.code}")
            print(f"WARN: explore failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"explore: {e}")
        print(f"WARN: explore failed: {e}, continuing...", file=sys.stderr)

    # --- Step 3: Propose beliefs ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 3: Proposing beliefs", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        propose_args = _argparse.Namespace(
            model=model, timeout=timeout, batch_size=5,
            proposals_output="proposed-beliefs.md", output_file="proposed-beliefs.md",
            all=False, auto=False, since=None, parallel=1, output=db_path,
        )
        cmd_propose_beliefs(propose_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"propose-beliefs exited with code {e.code}")
            print(f"WARN: propose-beliefs failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"propose-beliefs: {e}")
        print(f"WARN: propose-beliefs failed: {e}, continuing...", file=sys.stderr)

    # --- Step 4: Review proposals ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 4: Reviewing proposals", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        review_args = _argparse.Namespace(
            model=model, timeout=timeout, proposals_file="proposed-beliefs.md",
            batch_size=20, output=db_path,
        )
        cmd_review_proposals(review_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"review-proposals exited with code {e.code}")
            print(f"WARN: review-proposals failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"review-proposals: {e}")
        print(f"WARN: review-proposals failed: {e}, continuing...", file=sys.stderr)

    # --- Step 5: Accept beliefs ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 5: Accepting beliefs", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        accept_args = _argparse.Namespace(
            proposals_file="proposed-beliefs.md", output=db_path,
        )
        cmd_accept_beliefs(accept_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"accept-beliefs exited with code {e.code}")
            print(f"WARN: accept-beliefs failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"accept-beliefs: {e}")
        print(f"WARN: accept-beliefs failed: {e}, continuing...", file=sys.stderr)

    # --- Step 6: Derive (exhaust) ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 6: Deriving logical consequences", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        derive_args = _argparse.Namespace(
            model=model, timeout=timeout, auto=True, exhaust=True,
            max_derive_rounds=5, budget=300, domain=None, output=db_path,
        )
        cmd_derive(derive_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"derive exited with code {e.code}")
            print(f"WARN: derive failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"derive: {e}")
        print(f"WARN: derive failed: {e}, continuing...", file=sys.stderr)

    # --- Step 7: Generate summary ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 7: Generating summary", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        summary_args = _argparse.Namespace(
            output=db_path, snapshot_ids=tuple(pre_run_ids),
        )
        cmd_generate_summary(summary_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"generate-summary exited with code {e.code}")
    except Exception as e:
        errors.append(f"generate-summary: {e}")
        print(f"WARN: generate-summary failed: {e}", file=sys.stderr)

    # --- Save checkpoint ---
    _save_update_checkpoint(product_dir)
    print(f"\nCheckpoint saved (since: {date.today().isoformat()}).", file=sys.stderr)

    # --- Report ---
    try:
        network = _load_network(db_path)
        post_run_ids = set(network.get("nodes", {}).keys())
        new_beliefs = post_run_ids - pre_run_ids
        print(f"New beliefs: {len(new_beliefs)}", file=sys.stderr)
    except Exception:
        pass

    print(f"\n{'=' * 40}", file=sys.stderr)
    if errors:
        print(f"Update completed with {len(errors)} warning(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print("Update completed successfully.", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)
