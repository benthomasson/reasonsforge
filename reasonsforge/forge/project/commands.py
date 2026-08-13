"""Project forge commands — analyze issue trackers and extract project beliefs.

Each function takes an argparse Namespace and uses reasonsforge.api directly
(no subprocess calls to the reasons CLI).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from collections import defaultdict, deque
from datetime import date, datetime
from pathlib import Path

from . import PROJECT_DIR
from .prompts import (
    DERIVE_BELIEFS_PROMPT,
    PROPOSE_BELIEFS_PROJECT,
    RESEARCH_PROMPT,
    REVIEW_PROMPT,
    build_explore_prompt,
    build_scan_prompt,
    build_sprint_plan_prompt,
    build_summary_prompt,
)
from .sources import GitHubSource, GitLabSource, JiraSource, Issue
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict | None:
    config_path = Path.cwd() / PROJECT_DIR / "config.json"
    if config_path.is_file():
        return json.loads(config_path.read_text())
    return None


def _save_config(config: dict) -> None:
    config_dir = Path.cwd() / PROJECT_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(config, indent=2))


def _get_project_dir() -> str:
    return str(Path.cwd() / PROJECT_DIR)


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


def _enqueue_topics(response: str, source: str, project_dir: str | None = None) -> None:
    new_topics = parse_topics_from_response(response, source=source)
    if new_topics:
        added = add_topics(new_topics, project_dir)
        if added:
            total = pending_count(project_dir)
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


def _cache_issues(issues: list[Issue], project_dir: str) -> None:
    """Cache fetched issues so explore can reference them without re-fetching."""
    cache_path = os.path.join(project_dir, "issues-cache.json")
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
    os.makedirs(project_dir, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(data, f, indent=2)


def _load_cached_issues(project_dir: str) -> dict:
    """Load cached issues."""
    cache_path = os.path.join(project_dir, "issues-cache.json")
    if not os.path.isfile(cache_path):
        return {}
    with open(cache_path) as f:
        return json.load(f)


def _build_topic_prompt(topic: Topic, config: dict | None, project_dir: str) -> str:
    """Build the explore prompt for a topic without invoking the LLM."""
    issue_text = ""
    context_text = ""

    if topic.kind in ("issue", "epic") and config:
        try:
            source = _get_source(config)
            issue_id = topic.target
            if config["platform"] == "github":
                num = re.search(r"\d+", issue_id)
                if num:
                    issue = source.get_issue(int(num.group()))
                    issue_text = issue.to_prompt_text()

                    cached = _load_cached_issues(project_dir)
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
        cached = _load_cached_issues(project_dir)
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
    project_dir = _get_project_dir()
    config = _load_config()

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    print(f"Topic: [{topic.kind}] {topic.target}", file=sys.stderr)
    print(f"  {topic.title}", file=sys.stderr)
    print(file=sys.stderr)

    prompt = _build_topic_prompt(topic, config, project_dir)

    print(f"Exploring with {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return

    safe_target = re.sub(r"[^a-zA-Z0-9_-]", "-", topic.target)[:80]
    _create_entry(f"explore-{safe_target}", f"Explore: {topic.target}", result)
    _enqueue_topics(result, source=f"explore:{topic.target}", project_dir=project_dir)
    _report_beliefs(result)

    print(result)


def _explore_loop(args, project_dir: str, max_topics: int, max_parallel: int = 1) -> None:
    """Continuously explore topics up to max_topics."""
    if max_parallel > 1:
        _explore_loop_parallel(args, project_dir, max_topics, max_parallel)
        return

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)

    explored = 0
    while explored < max_topics:
        topic = pop_next(project_dir)
        if topic is None:
            if explored == 0:
                print("No pending topics. Run `reasonsforge project scan` to discover topics.")
            else:
                print(f"\nNo more topics after {explored} exploration(s).", file=sys.stderr)
            return

        explored += 1
        remaining = pending_count(project_dir)
        print(f"\n{'=' * 40}", file=sys.stderr)
        print(f"[{explored}/{max_topics}] ({remaining} remaining in queue)", file=sys.stderr)
        print(f"{'=' * 40}", file=sys.stderr)

        _run_topic(args, topic)

    remaining = pending_count(project_dir)
    print(f"\nExplored {explored} topic(s). {remaining} remaining.", file=sys.stderr)


def _explore_loop_parallel(args, project_dir: str, max_topics: int, max_parallel: int) -> None:
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
            topic = pop_next(project_dir)
            if topic is None:
                break
            batch_topics.append(topic)

        if not batch_topics:
            if explored == 0:
                print("No pending topics. Run `reasonsforge project scan` to discover topics.")
            else:
                print(f"\nNo more topics after {explored} exploration(s).", file=sys.stderr)
            return

        print(f"\nExploring {len(batch_topics)} topic(s) in parallel...", file=sys.stderr)
        for t in batch_topics:
            print(f"  [{t.kind}] {t.target}: {t.title}", file=sys.stderr)

        prompts = [_build_topic_prompt(t, config, project_dir) for t in batch_topics]

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
            _enqueue_topics(result, source=f"explore:{topic.target}", project_dir=project_dir)
            _report_beliefs(result)
            print(result)

    remaining = pending_count(project_dir)
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


def _build_issue_state_section(cached_issues: dict) -> str:
    """Build a compact issue state reference for the review prompt."""
    if not cached_issues:
        return "(No cached issue data available)"
    lines = []
    for issue_id, data in sorted(cached_issues.items()):
        state = data.get("state", "unknown")
        title = data.get("title", "")[:80]
        lines.append(f"- {issue_id}: [{state}] {title}")
    return "\n".join(lines)


def _build_existing_beliefs_section(nodes: dict) -> str:
    """Build a compact existing beliefs reference for the review prompt."""
    if not nodes:
        return "(No existing beliefs)"
    lines = []
    for node_id, node in sorted(nodes.items()):
        text = node.get("text", "")[:100]
        tv = node.get("truth_value", "?")
        lines.append(f"- `{node_id}` [{tv}]: {text}")
    if len(lines) > 300:
        lines = lines[:300]
        lines.append(f"... and {len(nodes) - 300} more beliefs")
    return "\n".join(lines)


def _build_proposals_section(proposals: list[dict]) -> str:
    """Format proposals for the review prompt."""
    lines = []
    for p in proposals:
        lines.append(f"### {p['id']}")
        lines.append(p["text"])
        lines.append(f"- Source: {p['source']}")
        lines.append("")
    return "\n".join(lines)


def _parse_review_response(response: str) -> dict[str, tuple[bool, str | None]]:
    """Parse LLM review response into accept/reject decisions."""
    decisions = {}
    for line in response.splitlines():
        line = line.strip()
        if line.startswith("ACCEPT "):
            belief_id = line[7:].strip()
            decisions[belief_id] = (False, None)
        elif line.startswith("REJECT "):
            rest = line[7:].strip()
            parts = rest.split(" ", 1)
            belief_id = parts[0]
            reason = parts[1] if len(parts) > 1 else "rejected by review"
            decisions[belief_id] = (True, reason)
    return decisions


def _extract_issue_refs(text: str) -> list[dict]:
    """Extract issue and PR references from text.

    Returns list of dicts with 'type' (issue/pr) and 'number' or 'key'.
    """
    refs = []
    seen = set()

    # GH-123, PR-123, GL-123, MR-123
    for match in re.finditer(r"\b(GH|PR|GL|MR)-(\d+)\b", text):
        prefix = match.group(1)
        num = int(match.group(2))
        kind = "pr" if prefix in ("PR", "MR") else "issue"
        key = (kind, num)
        if key not in seen:
            seen.add(key)
            refs.append({"type": kind, "number": num})

    # #123 (common GitHub/GitLab shorthand)
    for match in re.finditer(r"(?<!\w)#(\d+)\b", text):
        num = int(match.group(1))
        key = ("issue", num)
        if key not in seen:
            seen.add(key)
            refs.append({"type": "issue", "number": num})

    # PROJ-123 (Jira-style keys)
    for match in re.finditer(r"\b([A-Z][A-Z0-9]+-\d+)\b", text):
        jira_key = match.group(1)
        if jira_key.startswith(("GH-", "PR-", "GL-", "MR-")):
            continue
        if jira_key not in seen:
            seen.add(jira_key)
            refs.append({"type": "issue", "key": jira_key})

    return refs


def _fetch_artifacts(refs: list[dict], source, config: dict,
                     github_source=None) -> str:
    """Fetch current state for each issue/PR reference."""
    parts = []
    platform = config["platform"]

    for ref in refs:
        ref_label = ref.get("key") or f"#{ref.get('number')}"
        try:
            if "key" in ref:
                if platform != "jira":
                    parts.append(f"(Skipping Jira key {ref['key']} on {platform})")
                    continue
                issue = source.get_issue(ref["key"])
                parts.append(issue.to_prompt_text())
            elif ref["type"] == "pr":
                if hasattr(source, "get_pr"):
                    pr = source.get_pr(ref["number"])
                    parts.append(pr.to_prompt_text())
                elif github_source:
                    pr = github_source.get_pr(ref["number"])
                    parts.append(pr.to_prompt_text())
                else:
                    parts.append(f"(Could not fetch pr {ref_label} -- no GitHub repo configured)")
            elif ref["type"] == "issue":
                if platform == "jira" and isinstance(ref.get("number"), int):
                    if github_source:
                        issue = github_source.get_issue(ref["number"])
                        parts.append(issue.to_prompt_text())
                    else:
                        parts.append(f"(Skipping numeric ref #{ref['number']} on Jira -- no GitHub repo configured)")
                else:
                    issue = source.get_issue(ref["number"])
                    parts.append(issue.to_prompt_text())
            else:
                parts.append(f"(Could not fetch {ref['type']} {ref_label})")
        except Exception as e:
            parts.append(f"(Error fetching {ref['type']} {ref_label}: {e})")

    return "\n\n---\n\n".join(parts) if parts else "(No artifacts fetched)"


def _get_dependent_beliefs(belief_id: str, network: dict) -> str:
    """Format dependent beliefs for the research prompt."""
    nodes = network.get("nodes", {})
    node = nodes.get(belief_id, {})
    dep_ids = node.get("dependents", [])

    if not dep_ids:
        return "(No dependent beliefs)"

    lines = []
    for dep_id in dep_ids:
        dep_node = nodes.get(dep_id, {})
        text = dep_node.get("text", "")[:120]
        status = dep_node.get("truth_value", "?")
        lines.append(f"- `{dep_id}` [{status}]: {text}")
    return "\n".join(lines)


def _select_beliefs_for_research(network: dict, negative: bool = False,
                                  high_impact: bool = False,
                                  limit: int = 10) -> list[str]:
    """Select belief IDs for research based on criteria."""
    nodes = network.get("nodes", {})
    candidates = []

    for node_id, node in nodes.items():
        tv = node.get("truth_value", "")

        if negative and tv != "OUT":
            continue
        if not negative and tv != "IN":
            continue

        dep_count = len(node.get("dependents", []))
        candidates.append((node_id, dep_count, tv))

    if high_impact:
        candidates.sort(key=lambda x: -x[1])
    else:
        candidates.sort(key=lambda x: x[0])

    return [c[0] for c in candidates[:limit]]


def _get_belief_info(belief_id: str, db_path: str = REASONS_DB) -> dict | None:
    """Get belief info using reasonsforge.api.export_network() instead of subprocess."""
    from reasonsforge.api import export_network

    try:
        network = export_network(db_path=db_path)
    except Exception:
        return None

    nodes = network.get("nodes", {})
    node = nodes.get(belief_id)
    if node is None:
        return None

    return {
        "id": belief_id,
        "text": node.get("text", ""),
        "source": node.get("source", ""),
        "status": node.get("truth_value", ""),
        "dependents": node.get("dependents", []),
    }


def _load_network(db_path: str = REASONS_DB) -> dict:
    """Load network using reasonsforge.api.export_network() directly."""
    from reasonsforge.api import export_network

    try:
        return export_network(db_path=db_path)
    except Exception:
        return {"nodes": {}}


def _get_depth(node_id: str, nodes: dict, derived: dict, memo: dict | None = None) -> int:
    """Compute the depth of a node in the reasoning chain."""
    if memo is None:
        memo = {}
    if node_id in memo:
        return memo[node_id]
    if node_id not in derived:
        memo[node_id] = 0
        return 0
    max_d = 0
    for j in derived[node_id].get("justifications", []):
        for a in j.get("antecedents", []):
            max_d = max(max_d, _get_depth(a, nodes, derived, memo))
    memo[node_id] = max_d + 1
    return max_d + 1


def _build_beliefs_section(nodes: dict, derived: dict, max_beliefs: int = 300) -> str:
    """Build a compact beliefs section for the derive prompt."""
    lines = []
    in_nodes = {k: v for k, v in nodes.items()
                if v.get("truth_value") == "IN" and k not in derived}
    groups = defaultdict(list)
    for k, v in in_nodes.items():
        prefix = k.split("-")[0] if "-" in k else k
        groups[prefix].append((k, v["text"][:120]))

    count = 0
    for prefix in sorted(groups, key=lambda p: -len(groups[p])):
        if count >= max_beliefs:
            break
        lines.append(f"\n### {prefix} ({len(groups[prefix])} beliefs)")
        for belief_id, text in sorted(groups[prefix]):
            if count >= max_beliefs:
                break
            lines.append(f"- `{belief_id}`: {text}")
            count += 1

    return "\n".join(lines)


def _build_derived_section(nodes: dict, derived: dict) -> str:
    """Build the derived conclusions section for the derive prompt."""
    memo = {}
    lines = []
    for k in sorted(derived, key=lambda x: -_get_depth(x, nodes, derived, memo)):
        depth = _get_depth(k, nodes, derived, memo)
        text = nodes[k]["text"][:150]
        justs = derived[k]["justifications"]
        antes = justs[0].get("antecedents", []) if justs else []
        outlist = justs[0].get("outlist", []) if justs else []
        status = nodes[k].get("truth_value", "?")

        lines.append(f"\n#### [{status}] depth-{depth}: `{k}`")
        lines.append(text)
        lines.append(f"- Antecedents: {', '.join(antes)}")
        if outlist:
            lines.append(f"- Unless: {', '.join(outlist)}")

    return "\n".join(lines) if lines else "(No derived conclusions yet)"


def _parse_derive_proposals(response: str) -> list[dict]:
    """Parse DERIVE and GATE proposals from LLM response."""
    proposals = []
    pattern = re.compile(
        r"### (DERIVE|GATE) (\S+)\n"
        r"(.+?)\n"
        r"- Antecedents: (.+?)\n"
        r"(?:- Unless: (.+?)\n)?"
        r"- Label: (.+?)(?:\n|$)",
    )
    for match in pattern.finditer(response):
        kind = match.group(1)
        proposal = {
            "kind": kind.lower(),
            "id": match.group(2).strip("`"),
            "text": match.group(3).strip(),
            "antecedents": [a.strip().strip("`") for a in match.group(4).split(",")],
            "unless": [u.strip().strip("`") for u in match.group(5).split(",")] if match.group(5) else [],
            "label": match.group(6).strip(),
        }
        proposals.append(proposal)
    return proposals


def _derive_once(model: str, timeout: int, db_path: str = REASONS_DB) -> int:
    """Run a single derivation round using the legacy derive prompt.

    Returns number of beliefs added.
    """
    from reasonsforge.api import add_node
    from ..llm import invoke

    network = _load_network(db_path)
    nodes = network.get("nodes", {})
    if not nodes:
        return 0

    derived = {k: v for k, v in nodes.items()
               if v.get("justifications") and len(v["justifications"]) > 0}
    in_nodes = {k: v for k, v in nodes.items() if v.get("truth_value") == "IN"}
    memo = {}
    max_depth = max((_get_depth(k, nodes, derived, memo) for k in derived), default=0)

    print(f"Network: {len(in_nodes)} IN, {len(derived)} derived, depth {max_depth}", file=sys.stderr)

    beliefs_section = _build_beliefs_section(nodes, derived)
    derived_section = _build_derived_section(nodes, derived)

    prompt = DERIVE_BELIEFS_PROMPT.format(
        beliefs_section=beliefs_section,
        derived_section=derived_section,
        total_in=len(in_nodes),
        total_derived=len(derived),
        max_depth=max_depth,
    )

    print(f"Deriving with {model}...", file=sys.stderr)
    result = asyncio.run(invoke(prompt, model, timeout=timeout))

    proposals = _parse_derive_proposals(result)
    if not proposals:
        return 0

    valid = []
    for p in proposals:
        missing = [a for a in p["antecedents"] if a not in nodes]
        missing_unless = [u for u in p["unless"] if u not in nodes]
        if missing or missing_unless:
            print(f"  SKIP {p['id']}: missing nodes {missing + missing_unless}", file=sys.stderr)
            continue
        if p["id"] in nodes:
            print(f"  SKIP {p['id']}: already exists", file=sys.stderr)
            continue
        valid.append(p)

    if not valid:
        return 0

    added = 0
    for p in valid:
        try:
            sl = ",".join(p["antecedents"])
            unless = ",".join(p["unless"]) if p["unless"] else ""
            add_node(
                p["id"], p["text"],
                sl=sl,
                unless=unless,
                label=p["label"],
                db_path=db_path,
            )
            print(f"  Added {p['id']}", file=sys.stderr)
            added += 1
        except Exception as e:
            print(f"  FAIL {p['id']}: {e}", file=sys.stderr)

    return added


def _compute_gating_analysis(network: dict) -> list[dict]:
    """Compute which nodes gate the most downstream work."""
    nodes = network.get("nodes", {})
    if not nodes:
        return []

    dependents_map = defaultdict(set)
    outlist_gates = defaultdict(set)
    for k, v in nodes.items():
        for j in v.get("justifications", []):
            for a in j.get("antecedents", []):
                dependents_map[a].add(k)
            for o in j.get("outlist", []):
                dependents_map[o].add(k)
                outlist_gates[o].add(k)

    def _transitive_count(node_id: str) -> int:
        visited = set()
        queue = deque([node_id])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            for dep in dependents_map.get(current, []):
                if dep not in visited:
                    queue.append(dep)
        return len(visited) - 1

    results = []
    for node_id in dependents_map:
        if node_id not in nodes:
            continue
        node = nodes[node_id]
        downstream = _transitive_count(node_id)
        if downstream == 0:
            continue
        results.append({
            "id": node_id,
            "text": node.get("text", ""),
            "truth_value": node.get("truth_value", "?"),
            "downstream_count": downstream,
            "gated_conclusions": list(outlist_gates.get(node_id, [])),
        })

    results.sort(key=lambda x: x["downstream_count"], reverse=True)
    return results


def _compute_team_signals(cached_issues: dict) -> dict:
    """Infer team composition and capacity from cached issues."""
    team = defaultdict(lambda: {
        "open": 0, "closed_recent": 0, "total": 0,
        "priorities": defaultdict(int),
    })
    total_open = 0
    unassigned_open = 0
    now = datetime.now()

    issues = cached_issues.get("issues", cached_issues)
    if isinstance(issues, dict):
        issue_list = list(issues.values()) if issues else []
    elif isinstance(issues, list):
        issue_list = issues
    else:
        issue_list = []

    for issue in issue_list:
        state = (issue.get("state") or "").lower()
        assignees = issue.get("assignees") or []
        priority = issue.get("priority") or "none"
        is_open = state in ("open", "opened", "to do", "in progress", "new")

        if is_open:
            total_open += 1

        if not assignees and is_open:
            unassigned_open += 1

        is_recent_close = False
        if not is_open:
            closed_str = issue.get("closed") or issue.get("updated") or ""
            if closed_str:
                try:
                    closed_dt = datetime.fromisoformat(closed_str.replace("Z", "+00:00"))
                    is_recent_close = (now - closed_dt.replace(tzinfo=None)).days <= 30
                except (ValueError, TypeError):
                    pass

        for assignee in assignees:
            name = assignee if isinstance(assignee, str) else assignee.get("login", assignee.get("name", "unknown"))
            team[name]["total"] += 1
            if is_open:
                team[name]["open"] += 1
            if is_recent_close:
                team[name]["closed_recent"] += 1
            team[name]["priorities"][priority] += 1

    members = []
    for name, data in sorted(team.items(), key=lambda x: x[1]["open"], reverse=True):
        members.append({
            "name": name,
            "open_issues": data["open"],
            "closed_recent": data["closed_recent"],
            "total": data["total"],
            "priorities": dict(data["priorities"]),
        })

    return {
        "team_members": members,
        "inferred_team_size": len(members),
        "total_open": total_open,
        "unassigned_open": unassigned_open,
    }


def _format_gating_section(gating_analysis: list[dict], max_items: int = 30) -> str:
    if not gating_analysis:
        return "No gating analysis available."
    lines = []
    for item in gating_analysis[:max_items]:
        gated = ", ".join(item["gated_conclusions"][:5]) if item["gated_conclusions"] else "indirect"
        lines.append(
            f"- `{item['id']}` [{item['truth_value']}] "
            f"(downstream: {item['downstream_count']}) -- {item['text'][:120]}"
        )
        if item["gated_conclusions"]:
            lines.append(f"  Gates: {gated}")
    total = len(gating_analysis)
    if total > max_items:
        lines.append(f"\n({total - max_items} more gating nodes omitted)")
    return "\n".join(lines)


def _format_team_section(team_signals: dict) -> str:
    if not team_signals["team_members"]:
        return "No team data available (no assignees found in issues)."
    lines = [
        f"Total open issues: {team_signals['total_open']} "
        f"({team_signals['unassigned_open']} unassigned)",
        "",
    ]
    for m in team_signals["team_members"]:
        prio_str = ", ".join(f"{k}: {v}" for k, v in m["priorities"].items() if k != "none")
        lines.append(
            f"- **{m['name']}**: {m['open_issues']} open, "
            f"{m['closed_recent']} closed (last 30d)"
            + (f" | priorities: {prio_str}" if prio_str else "")
        )
    return "\n".join(lines)


def _format_backlog_section(
    cached_issues: dict,
    gating_analysis: list[dict],
    max_items: int = 40,
) -> str:
    issues = cached_issues.get("issues", cached_issues)
    if isinstance(issues, dict):
        issue_list = list(issues.values()) if issues else []
    elif isinstance(issues, list):
        issue_list = issues
    else:
        issue_list = []

    # Build word-boundary patterns for cached issue IDs
    cached_ids = {str(issue.get("id", "")) for issue in issue_list}
    id_patterns = {}
    for cid in cached_ids:
        if cid:
            id_patterns[cid] = re.compile(r"\b" + re.escape(cid) + r"\b")

    # Build a map from issue tracker IDs to max belief downstream count
    issue_belief_impact = {}
    for g in gating_analysis:
        text = g["text"]
        for cached_id, pattern in id_patterns.items():
            if pattern.search(text):
                existing = issue_belief_impact.get(cached_id, 0)
                issue_belief_impact[cached_id] = max(existing, g["downstream_count"])

    priority_order = {"critical": 0, "highest": 1, "high": 2, "medium": 3, "low": 4}

    open_issues = []
    for issue in issue_list:
        state = (issue.get("state") or "").lower()
        if state not in ("open", "opened", "to do", "in progress", "new"):
            continue

        issue_id = str(issue.get("id", ""))
        belief_impact = issue_belief_impact.get(issue_id, 0)

        prio = (issue.get("priority") or "medium").lower()
        prio_rank = priority_order.get(prio, 3)

        open_issues.append({
            "issue": issue,
            "priority_rank": prio_rank,
            "belief_impact": belief_impact,
        })

    open_issues.sort(key=lambda x: (-x["belief_impact"], x["priority_rank"]))

    if not open_issues:
        return "No open issues found in cache."

    lines = []
    for item in open_issues[:max_items]:
        issue = item["issue"]
        assignees = issue.get("assignees") or []
        assignee_str = ", ".join(assignees) if assignees else "unassigned"
        prio = issue.get("priority") or "--"
        title = (issue.get("title") or "")[:100]
        issue_id = issue.get("id", "?")
        milestone = issue.get("milestone") or ""
        updated = (issue.get("updated") or "")[:10]
        impact = item["belief_impact"]

        line = f"- **{issue_id}**: {title}"
        line += f"\n  Priority: {prio} | Assignee: {assignee_str} | Updated: {updated}"
        if impact > 0:
            line += f" | Belief impact: {impact} downstream"
        if milestone:
            line += f" | Milestone: {milestone}"
        lines.append(line)

    total = len(open_issues)
    if total > max_items:
        lines.append(f"\n({total - max_items} more open issues omitted)")
    return "\n".join(lines)


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


def _save_update_checkpoint(project_dir: str) -> None:
    """Save current timestamp as the update checkpoint."""
    checkpoint = {
        "timestamp": datetime.now().isoformat(),
        "since": date.today().isoformat(),
    }
    checkpoint_path = os.path.join(project_dir, "last-update.json")
    os.makedirs(project_dir, exist_ok=True)
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)


def _load_update_checkpoint(project_dir: str) -> str | None:
    """Load the since date from the last update checkpoint."""
    checkpoint_path = os.path.join(project_dir, "last-update.json")
    if not os.path.isfile(checkpoint_path):
        return None
    with open(checkpoint_path) as f:
        data = json.load(f)
    return data.get("since")


def _fetch_issues(source, config, state, labels, limit, page, since):
    """Fetch issues from source with since filtering."""
    platform = config["platform"]
    if platform == "jira":
        return source.list_issues(state=state, labels=labels, limit=limit,
                                  page=page, since=since)
    elif platform == "gitlab":
        return source.list_issues(state=state, labels=labels, limit=limit,
                                  page=page, since=since)
    else:
        return source.list_issues(state=state, labels=labels, limit=limit,
                                  since=since)


def _run_scan_step(config, source, issues, project_name,
                   state, limit, page, project_dir, model, timeout):
    """Run the scan LLM step on a batch of fetched issues."""
    from ..llm import invoke

    print(f"  Fetched {len(issues)} issues", file=sys.stderr)

    # Fetch PRs for platforms that support them
    prs = []
    if config["platform"] in ("github", "gitlab") and hasattr(source, "list_prs"):
        try:
            prs = source.list_prs(state=state or "open", limit=limit)
            if prs:
                print(f"  Fetched {len(prs)} pull requests", file=sys.stderr)
        except Exception as e:
            print(f"  Warning: Could not fetch PRs: {e}", file=sys.stderr)

    issues_text = "\n\n".join(issue.to_prompt_text() for issue in issues)
    prs_text = ""
    if prs:
        prs_text = "\n\n".join(pr.to_prompt_text() for pr in prs)

    prompt = build_scan_prompt(
        issues_text=issues_text,
        prs_text=prs_text,
        project_name=project_name,
        platform=config["platform"],
        issue_count=len(issues),
        pr_count=len(prs),
        state=state,
    )

    print(f"  Running {model}...", file=sys.stderr)
    result = asyncio.run(invoke(prompt, model, timeout=timeout))

    short_name = project_name.split("//")[-1] if "//" in project_name else project_name
    safe_name = short_name.replace("/", "-")
    state_suffix = f"-{state}" if state and state not in ("open", "opened") else ""
    page_suffix = f"-p{page}" if page > 1 else ""
    _create_entry(f"scan-{safe_name}{state_suffix}{page_suffix}",
                  f"Scan: {project_name} ({state or 'open'}, page {page})", result)
    _enqueue_topics(result, source=f"scan:{project_name}", project_dir=project_dir)
    _report_beliefs(result)
    _cache_issues(issues, project_dir)

    print(result)


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args):
    """Bootstrap a project forge knowledge base for an issue tracker.

    Creates .forge/project/config.json, summaries/ dir, and initializes
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
    github_repo = getattr(args, "github_repo", None)
    db_path = getattr(args, "output", REASONS_DB)

    # Validate platform prerequisites
    if platform == "jira":
        if not jira_url and not os.environ.get("JIRA_URL"):
            print("Error: --jira-url or JIRA_URL env var required for Jira", file=sys.stderr)
            sys.exit(1)

    # Create project dir
    project_dir = Path.cwd() / PROJECT_DIR
    project_dir.mkdir(parents=True, exist_ok=True)

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

    if github_repo:
        config["github_repo"] = github_repo

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

    print(f"\nProject forge initialized")
    print(f"  Platform: {platform}")
    print(f"  Target:   {target}")
    print(f"  Domain:   {domain}")
    print(f"\nNext: reasonsforge project scan")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def _scan_progress_path() -> Path:
    return Path(_get_project_dir()) / "scan-progress.json"


def _save_scan_progress(page: int, total_scanned: int, params: dict) -> None:
    progress = {
        "last_completed_page": page,
        "total_scanned": total_scanned,
        "params": params,
    }
    path = _scan_progress_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(progress, indent=2) + "\n")


def _load_scan_progress() -> dict | None:
    path = _scan_progress_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _clear_scan_progress() -> None:
    path = _scan_progress_path()
    if path.exists():
        path.unlink()


def cmd_scan(args):
    """Scan project issues and create an overview."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge project init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    project_dir = _get_project_dir()

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
    per_issue = getattr(args, "per_issue", False)
    resume = getattr(args, "resume", False)

    # Set default state per platform
    if state is None:
        if config["platform"] == "gitlab":
            state = "opened"
        elif config["platform"] == "jira":
            state = "open"
        else:
            state = "open"

    if per_issue:
        all_pages = True

    project_name = config.get("repo", config.get("project", "unknown"))

    scan_params = {"state": state, "labels": label_list, "jql": jql,
                   "per_issue": per_issue, "limit": limit}

    if all_pages:
        current_page = 1
        total_scanned = 0

        if resume:
            progress = _load_scan_progress()
            if progress:
                saved_params = progress.get("params", {})
                if saved_params != scan_params:
                    print("Warning: scan parameters changed since last run. "
                          "Starting from page 1.", file=sys.stderr)
                else:
                    current_page = progress["last_completed_page"] + 1
                    total_scanned = progress["total_scanned"]
                    print(f"Resuming from page {current_page} "
                          f"({total_scanned} issues already scanned)",
                          file=sys.stderr)
            else:
                print("No previous scan progress found. Starting from page 1.",
                      file=sys.stderr)

        while True:
            print(f"\n{'=' * 40}", file=sys.stderr)
            print(f"Page {current_page}", file=sys.stderr)
            print(f"{'=' * 40}", file=sys.stderr)

            try:
                if config["platform"] == "jira":
                    issues = source.list_issues(jql=jql, state=state, labels=label_list,
                                                limit=limit, page=current_page)
                elif config["platform"] == "gitlab":
                    issues = source.list_issues(state=state, labels=label_list,
                                                limit=limit, page=current_page)
                else:
                    issues = source.list_issues(state=state, labels=label_list, limit=limit)
            except Exception as e:
                print(f"Error fetching issues: {e}", file=sys.stderr)
                sys.exit(1)

            if not issues:
                if total_scanned == 0:
                    print("No issues found.")
                else:
                    print(f"\nDone. Scanned {total_scanned} issues across {current_page - 1} pages.",
                          file=sys.stderr)
                _clear_scan_progress()
                break

            print(f"Fetched {len(issues)} issues", file=sys.stderr)

            if per_issue:
                topics = [
                    Topic(
                        title=issue.title,
                        kind="issue",
                        target=issue.id,
                        source=f"scan:{project_name}",
                    )
                    for issue in issues
                ]
                added = add_topics(topics, project_dir)
                _cache_issues(issues, project_dir)
                print(f"Queued {added} topic(s) from {len(issues)} issues", file=sys.stderr)
                total_scanned += len(issues)
                _save_scan_progress(current_page, total_scanned, scan_params)
            else:
                # Fetch PRs for platforms that support them
                prs = []
                if config["platform"] in ("github", "gitlab") and hasattr(source, "list_prs"):
                    try:
                        prs = source.list_prs(state=state or "open", limit=limit)
                        if prs:
                            print(f"Fetched {len(prs)} pull requests", file=sys.stderr)
                    except Exception as e:
                        print(f"Warning: Could not fetch PRs: {e}", file=sys.stderr)

                issues_text = "\n\n".join(issue.to_prompt_text() for issue in issues)
                prs_text = ""
                if prs:
                    prs_text = "\n\n".join(pr.to_prompt_text() for pr in prs)

                prompt = build_scan_prompt(
                    issues_text=issues_text,
                    prs_text=prs_text,
                    project_name=project_name,
                    platform=config["platform"],
                    issue_count=len(issues),
                    pr_count=len(prs),
                    state=state,
                )

                print(f"Running {model}...", file=sys.stderr)
                try:
                    result = asyncio.run(invoke(prompt, model, timeout=timeout))
                except Exception as e:
                    print(f"Error: {e}", file=sys.stderr)
                    sys.exit(1)

                short_name = project_name.split("//")[-1] if "//" in project_name else project_name
                safe_name = short_name.replace("/", "-")
                state_suffix = f"-{state}" if state and state not in ("open", "opened") else ""
                page_suffix = f"-p{current_page}" if current_page > 1 else ""
                _create_entry(f"scan-{safe_name}{state_suffix}{page_suffix}",
                              f"Scan: {project_name} ({state or 'open'}, page {current_page})",
                              result)
                _enqueue_topics(result, source=f"scan:{project_name}", project_dir=project_dir)
                _report_beliefs(result)
                _cache_issues(issues, project_dir)
                total_scanned += len(issues)
                _save_scan_progress(current_page, total_scanned, scan_params)

                print(result)

            if len(issues) < limit:
                print(f"\nDone. Scanned {total_scanned} issues across {current_page} pages.",
                      file=sys.stderr)
                _clear_scan_progress()
                break
            current_page += 1
    else:
        # Single page
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
            print("No issues found.")
            return

        print(f"Fetched {len(issues)} issues", file=sys.stderr)

        if per_issue:
            topics = [
                Topic(
                    title=issue.title,
                    kind="issue",
                    target=issue.id,
                    source=f"scan:{project_name}",
                )
                for issue in issues
            ]
            added = add_topics(topics, project_dir)
            _cache_issues(issues, project_dir)
            print(f"Queued {added} topic(s) from {len(issues)} issues", file=sys.stderr)
            return

        # Fetch PRs for platforms that support them
        prs = []
        if config["platform"] in ("github", "gitlab") and hasattr(source, "list_prs"):
            try:
                prs = source.list_prs(state=state or "open", limit=limit)
                if prs:
                    print(f"Fetched {len(prs)} pull requests", file=sys.stderr)
            except Exception as e:
                print(f"Warning: Could not fetch PRs: {e}", file=sys.stderr)

        issues_text = "\n\n".join(issue.to_prompt_text() for issue in issues)
        prs_text = ""
        if prs:
            prs_text = "\n\n".join(pr.to_prompt_text() for pr in prs)

        prompt = build_scan_prompt(
            issues_text=issues_text,
            prs_text=prs_text,
            project_name=project_name,
            platform=config["platform"],
            issue_count=len(issues),
            pr_count=len(prs),
            state=state,
        )

        print(f"Running {model}...", file=sys.stderr)
        try:
            result = asyncio.run(invoke(prompt, model, timeout=timeout))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        short_name = project_name.split("//")[-1] if "//" in project_name else project_name
        safe_name = short_name.replace("/", "-")
        state_suffix = f"-{state}" if state and state not in ("open", "opened") else ""
        page_suffix = f"-p{page}" if page > 1 else ""
        _create_entry(f"scan-{safe_name}{state_suffix}{page_suffix}",
                      f"Scan: {project_name} ({state or 'open'}, page {page})", result)
        _enqueue_topics(result, source=f"scan:{project_name}", project_dir=project_dir)
        _report_beliefs(result)
        _cache_issues(issues, project_dir)

        print(result)


# ---------------------------------------------------------------------------
# explore
# ---------------------------------------------------------------------------


def cmd_explore(args):
    """Explore the next topic in the queue."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available
    _caffeinate()

    project_dir = _get_project_dir()
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
        _explore_loop(args, project_dir, loop_max, max_parallel)
        return

    if do_skip:
        if skip_topic(0, project_dir):
            queue = load_queue(project_dir)
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
            topic_list = pop_multiple(indices, project_dir)
        else:
            topic_list = [pop_at(indices[0], project_dir)]
    else:
        topic_list = [pop_next(project_dir)]

    valid_topics = [(i, t) for i, t in zip(
        indices if pick_index is not None else [0],
        topic_list,
    ) if t is not None]

    if not valid_topics:
        print("No pending topics. Run `reasonsforge project scan` to discover topics.")
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

        prompts = [_build_topic_prompt(t, config, project_dir) for t in topics_only]

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
            _enqueue_topics(result, source=f"explore:{topic.target}", project_dir=project_dir)
            _report_beliefs(result)
            print(result)
    else:
        for seq, (idx, topic) in enumerate(valid_topics):
            if len(valid_topics) > 1:
                print(f"\n{'=' * 40}", file=sys.stderr)
                print(f"[{seq + 1}/{len(valid_topics)}] Topic #{idx}", file=sys.stderr)
                print(f"{'=' * 40}", file=sys.stderr)

            _run_topic(args, topic)

    remaining = pending_count(project_dir)
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
    processed_path = Path(PROJECT_DIR) / "proposed-entries.json"
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
            prompts.append(PROPOSE_BELIEFS_PROJECT.format(entries=batch_text) + existing_context)

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
            prompt = PROPOSE_BELIEFS_PROJECT.format(entries=batch_text) + existing_context

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
            f.write("Then run: `reasonsforge project accept-beliefs`\n\n")
            f.write("---\n\n")
            f.write(f"**Generated:** {date.today().isoformat()}\n")
            f.write(f"**Source:** {source_desc}\n")
            f.write(f"**Model:** {model}\n\n")
            for proposal in filtered_proposals:
                f.write(proposal)
                f.write("\n\n")
        print(f"\nWrote {output_path}")

    print("Review the file, mark entries as [ACCEPT] or [REJECT], then run:")
    print("  reasonsforge project accept-beliefs")


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
        print("Run: reasonsforge project propose-beliefs")
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
    """Filter low-quality belief proposals using LLM review."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)
    proposals_file = getattr(args, "proposals_file", "proposed-beliefs.md")
    batch_size = getattr(args, "batch_size", 20)
    parallel = getattr(args, "parallel", 1)

    proposals_path = Path(proposals_file)
    if not proposals_path.exists():
        print(f"Proposals file not found: {proposals_file}")
        sys.exit(1)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    project_dir = _get_project_dir()
    text = proposals_path.read_text()

    # Load context
    cached_issues = _load_cached_issues(project_dir)
    try:
        network = _load_network(db_path)
        existing_nodes = network.get("nodes", {})
    except Exception:
        existing_nodes = {}

    # Parse all proposals
    proposal_pattern = re.compile(
        r"(### \[?(?:ACCEPT(?:/REJECT)?|REJECT)\]?)\s*(\S+)\n"
        r"(.+?)\n"
        r"(- Source: .+?)(?=\n###|\n---|\Z)",
        re.DOTALL,
    )

    matches = list(proposal_pattern.finditer(text))
    if not matches:
        print("No proposals found in file.")
        return

    # Separate already-rejected from reviewable
    to_review = []
    already_rejected = 0
    for match in matches:
        header = match.group(1)
        if "[REJECT]" in header and "[ACCEPT" not in header:
            already_rejected += 1
            continue
        to_review.append({
            "match": match,
            "header": header,
            "id": match.group(2),
            "text": match.group(3).strip(),
            "source": match.group(4).strip().removeprefix("- Source: "),
        })

    if not to_review:
        print("No proposals to review (all already rejected).", file=sys.stderr)
        return

    print(f"Reviewing {len(to_review)} proposals ({already_rejected} already rejected)...",
          file=sys.stderr)

    # Build context sections (shared across batches)
    issue_state = _build_issue_state_section(cached_issues)
    existing_beliefs = _build_existing_beliefs_section(existing_nodes)

    # Batch and review
    all_decisions: dict[str, tuple[bool, str | None]] = {}
    review_batches = [to_review[i:i + batch_size] for i in range(0, len(to_review), batch_size)]

    prompts = [
        REVIEW_PROMPT.format(
            issue_state=issue_state,
            existing_beliefs=existing_beliefs,
            proposals=_build_proposals_section(batch),
        )
        for batch in review_batches
    ]

    print(f"  {len(review_batches)} batch(es) (parallel={parallel})...", file=sys.stderr)

    if parallel > 1 and len(prompts) > 1:
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
                print(f"  ERROR in batch {i + 1}: {r}", file=sys.stderr)
            else:
                decisions = _parse_review_response(r)
                all_decisions.update(decisions)
    else:
        for i, prompt in enumerate(prompts):
            print(f"  Batch {i + 1}/{len(review_batches)} ({len(review_batches[i])} proposals)...",
                  file=sys.stderr)
            try:
                result = asyncio.run(invoke(prompt, model, timeout=timeout))
                decisions = _parse_review_response(result)
                all_decisions.update(decisions)
            except Exception as e:
                print(f"  ERROR in batch {i + 1}: {e}", file=sys.stderr)
                continue

    # Apply decisions
    kept = 0
    rejected = 0
    categories: dict[str, int] = {}
    replacements: list[tuple[str, str]] = []

    for proposal in to_review:
        belief_id = proposal["id"]
        match = proposal["match"]
        header = proposal["header"]

        decision = all_decisions.get(belief_id)
        if decision is None:
            kept += 1
            continue

        reject, reason = decision
        if reject:
            rejected += 1
            category = reason.split(":")[0].strip() if reason else "unknown"
            categories[category] = categories.get(category, 0) + 1

            old_block = match.group(0)
            new_header = f"### [REJECT] {belief_id}"
            new_block = old_block.replace(
                f"{header} {belief_id}",
                new_header,
                1,
            )
            source_line = match.group(4).strip()
            new_block = new_block.replace(
                source_line,
                f"{source_line}\n- Rejected: {reason}",
                1,
            )
            replacements.append((old_block, new_block))
            print(f"  REJECT {belief_id}: {reason}", file=sys.stderr)
        else:
            kept += 1

    # Summary
    print(f"\nReviewed {len(to_review)} proposals: {kept} kept, {rejected} rejected",
          file=sys.stderr)
    if categories:
        print("Rejections by category:", file=sys.stderr)
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}", file=sys.stderr)

    if replacements:
        for old_block, new_block in replacements:
            text = text.replace(old_block, new_block, 1)
        proposals_path.write_text(text)
        print(f"Updated {proposals_file}", file=sys.stderr)
    else:
        print("No changes needed.", file=sys.stderr)


# ---------------------------------------------------------------------------
# research
# ---------------------------------------------------------------------------


def cmd_research(args):
    """Verify beliefs against their source system (live issue tracker)."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge project init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)
    belief_id = getattr(args, "belief_id", None)
    negative = getattr(args, "negative", False)
    high_impact = getattr(args, "high_impact", False)
    select_limit = getattr(args, "limit", 1)
    parallel = getattr(args, "parallel", 1)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    # Determine which beliefs to research
    network = _load_network(db_path)
    if belief_id:
        belief_ids = [belief_id]
    else:
        belief_ids = _select_beliefs_for_research(
            network, negative=negative, high_impact=high_impact,
            limit=select_limit,
        )
        if not belief_ids:
            label = "negative " if negative else ""
            print(f"No {label}beliefs found to research.")
            return
        print(f"Selected {len(belief_ids)} belief(s) for research:", file=sys.stderr)
        for bid in belief_ids:
            print(f"  {bid}", file=sys.stderr)

    source = _get_source(config)

    github_source = None
    gh_repo = config.get("github_repo")
    if gh_repo and config["platform"] != "github":
        github_source = GitHubSource(gh_repo)
        print(f"Using GitHub repo {gh_repo} for PR lookups", file=sys.stderr)

    def _research_one(bid: str) -> str | None:
        """Build a research prompt for a single belief."""
        info = _get_belief_info(bid, db_path)
        if not info:
            print(f"Belief not found: {bid}", file=sys.stderr)
            return None

        print(f"\nResearching: {bid}", file=sys.stderr)
        print(f"  Claim: {info['text'][:100]}", file=sys.stderr)
        print(f"  Status: {info['status']}", file=sys.stderr)

        # Read source entry
        source_entry = "(No source entry found)"
        source_path = info.get("source", "")
        if source_path:
            entry_path = Path(source_path)
            if entry_path.is_file():
                content = entry_path.read_text()
                if len(content) > 15000:
                    content = content[:15000] + "\n[Truncated]"
                source_entry = content
                print(f"  Source: {source_path}", file=sys.stderr)

        # Extract and fetch artifacts
        all_text = f"{info['text']}\n{source_entry}"
        refs = _extract_issue_refs(all_text)
        print(f"  Found {len(refs)} reference(s)", file=sys.stderr)

        artifacts = _fetch_artifacts(refs, source, config, github_source=github_source)

        # Get dependent beliefs
        dependents = _get_dependent_beliefs(bid, network)
        dep_count = len(info.get("dependents", []))
        if dep_count:
            print(f"  {dep_count} dependent belief(s)", file=sys.stderr)

        return RESEARCH_PROMPT.format(
            belief_id=bid,
            belief_text=info["text"],
            belief_status=info["status"],
            source_entry=source_entry,
            artifacts=artifacts,
            dependents=dependents,
        )

    # Build prompts
    prompts_with_ids = []
    for bid in belief_ids:
        prompt = _research_one(bid)
        if prompt:
            prompts_with_ids.append((bid, prompt))

    if not prompts_with_ids:
        print("No beliefs could be researched.")
        return

    # Invoke LLM
    ids = [p[0] for p in prompts_with_ids]
    prompts = [p[1] for p in prompts_with_ids]

    print(f"\nRunning {model} on {len(prompts)} belief(s)...", file=sys.stderr)

    if parallel > 1 and len(prompts) > 1:
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
    else:
        results = []
        for i, prompt in enumerate(prompts):
            print(f"  [{i + 1}/{len(prompts)}] {ids[i]}...", file=sys.stderr)
            try:
                result = asyncio.run(invoke(prompt, model, timeout=timeout))
                results.append(result)
            except Exception as e:
                print(f"  ERROR: {e}", file=sys.stderr)
                results.append(e)

    # Process results
    for bid, result in zip(ids, results):
        if isinstance(result, Exception):
            print(f"\nERROR researching {bid}: {result}", file=sys.stderr)
            continue

        # Extract verdict
        verdict = "UNKNOWN"
        for line in result.splitlines():
            line_stripped = line.strip()
            if line_stripped.startswith("VERDICT:"):
                verdict = line_stripped[8:].strip()
                break

        print(f"\n{'=' * 40}", file=sys.stderr)
        print(f"  {bid}: {verdict}", file=sys.stderr)
        print(f"{'=' * 40}", file=sys.stderr)

        safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", bid)[:80]
        _create_entry(f"research-{safe_id}", f"Research: {bid} [{verdict}]", result)

        print(result)


# ---------------------------------------------------------------------------
# derive
# ---------------------------------------------------------------------------


def cmd_derive(args):
    """Derive deeper reasoning chains from existing beliefs.

    Uses reasonsforge.derive.build_prompt(), parse_proposals(),
    validate_proposals(), and apply_proposals() -- matching the code forge
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

        prompt, _stats = build_prompt(nodes, domain=domain, budget=budget, sample=True)

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
# review-beliefs
# ---------------------------------------------------------------------------


def cmd_review_beliefs(args):
    """Review derived beliefs for validity using LLM evaluation.

    Delegates to reasonsforge.api.review_beliefs() directly.
    """
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)
    auto_retract = getattr(args, "auto_retract", False)
    sample = getattr(args, "sample", None)
    min_depth = getattr(args, "min_depth", None)
    dry_run = getattr(args, "dry_run", False)
    output = getattr(args, "review_output", None)

    try:
        from reasonsforge.api import review_beliefs as api_review_beliefs, retract_node

        print(f"Reviewing beliefs with {model}...", file=sys.stderr)

        result = api_review_beliefs(
            model=model,
            timeout=timeout,
            sample=sample,
            min_depth=min_depth,
            dry_run=dry_run,
            db_path=db_path,
        )

        reviewed = result.get("reviewed", 0)
        invalid = result.get("invalid", 0)
        insufficient = result.get("insufficient", 0)
        unnecessary = result.get("unnecessary", 0)
        total_derived = result.get("total_derived", 0)

        print(f"\nReviewed {reviewed} of {total_derived} derived beliefs:", file=sys.stderr)
        print(f"  Invalid: {invalid}", file=sys.stderr)
        print(f"  Insufficient: {insufficient}", file=sys.stderr)
        print(f"  Unnecessary: {unnecessary}", file=sys.stderr)

        results_list = result.get("results", [])

        if auto_retract and not dry_run:
            retracted = 0
            for r in results_list:
                if not r.get("valid", True):
                    try:
                        retract_node(
                            r["id"],
                            reason=r.get("comment", "invalid per review"),
                            db_path=db_path,
                        )
                        print(f"  Retracted: {r['id']}", file=sys.stderr)
                        retracted += 1
                    except Exception as e:
                        print(f"  Failed to retract {r['id']}: {e}", file=sys.stderr)
            if retracted:
                print(f"Retracted {retracted} belief(s).", file=sys.stderr)

        if output:
            output_path = Path(output)
            output_path.write_text(json.dumps(result, indent=2) + "\n")
            print(f"Wrote review results to {output_path}", file=sys.stderr)

    except ImportError:
        print("Error: reasonsforge.api.review_beliefs not available", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error reviewing beliefs: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# repair
# ---------------------------------------------------------------------------


def cmd_repair(args):
    """Repair beliefs flagged by review-beliefs.

    Delegates to reasonsforge.api.repair() directly.
    """
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)
    review_file = getattr(args, "review_file", None)
    dry_run = getattr(args, "dry_run", False)

    try:
        from reasonsforge.api import repair as api_repair

        print(f"Repairing beliefs with {model}...", file=sys.stderr)

        result = api_repair(
            review_file=review_file,
            model=model,
            timeout=timeout,
            dry_run=dry_run,
            db_path=db_path,
        )

        total = result.get("total_inaccurate", 0) or len(result.get("results", []))
        rewritten = result.get("rewritten", 0)
        retracted = result.get("retracted", 0)
        failed = result.get("failed", 0)

        print(f"\nRepair complete:", file=sys.stderr)
        print(f"  Total flagged: {total}", file=sys.stderr)
        print(f"  Rewritten: {rewritten}", file=sys.stderr)
        print(f"  Retracted: {retracted}", file=sys.stderr)
        print(f"  Failed: {failed}", file=sys.stderr)

    except ImportError:
        print("Error: reasonsforge.api.repair not available", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error repairing beliefs: {e}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


def cmd_summary(args):
    """Synthesize a project summary from beliefs."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge project init --github <owner/repo>")
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
    total_count = 0
    sorted_by_impact = False

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

            total_count = len(in_nodes)
            in_nodes.sort(key=lambda x: -x[2])

            if total_count > max_beliefs:
                in_nodes = in_nodes[:max_beliefs]

            lines = []
            for nid, node, dep_count in in_nodes:
                text = node.get("text", "")
                dep_suffix = f" (dependents: {dep_count})" if dep_count > 0 else ""
                lines.append(f"- `{nid}`: {text}{dep_suffix}")

            beliefs_text = "\n".join(lines)
            belief_count = len(in_nodes)
            sorted_by_impact = True
        except Exception as e:
            print(f"Warning: Could not load beliefs: {e}", file=sys.stderr)

    if not beliefs_text or belief_count == 0:
        print("No beliefs found. Run the pipeline first:")
        print("  reasonsforge project scan")
        print("  reasonsforge project propose-beliefs --auto")
        sys.exit(1)

    if total_count > max_beliefs:
        print(
            f"Summarizing top {belief_count} of {total_count} beliefs (by impact) with {model}...",
            file=sys.stderr,
        )
    else:
        print(f"Summarizing {belief_count} beliefs with {model}...", file=sys.stderr)

    project_name = config.get("repo", config.get("project", "unknown"))

    prompt = build_summary_prompt(
        beliefs_text=beliefs_text,
        project_name=project_name,
        belief_count=belief_count,
        total_count=total_count,
        sorted_by_impact=sorted_by_impact,
    )

    prompt_size_kb = len(prompt.encode()) / 1024
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(
            f"Error: Model {model} failed (prompt size: {prompt_size_kb:.0f} KB): {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    short_name = project_name.split("//")[-1] if "//" in project_name else project_name
    safe_name = short_name.replace("/", "-")
    _create_entry(f"summary-{safe_name}", f"Summary: {project_name}", result)

    print(result)


# ---------------------------------------------------------------------------
# sprint-plan
# ---------------------------------------------------------------------------


def cmd_sprint_plan(args):
    """Generate a prioritized sprint plan from beliefs and issues."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge project init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)
    sprint_length = getattr(args, "sprint_length", "2w")
    team_size = getattr(args, "team_size", None)
    dry_run = getattr(args, "dry_run", False)
    output = getattr(args, "sprint_output", None)

    if not dry_run and not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    # Load network and compute gating analysis
    network = _load_network(db_path)
    nodes = network.get("nodes", {})
    gating_analysis = _compute_gating_analysis(network) if nodes else []

    if gating_analysis:
        print(
            f"Gating analysis: {len(gating_analysis)} nodes with downstream impact",
            file=sys.stderr,
        )
    else:
        print("WARN: No belief network available for gating analysis", file=sys.stderr)

    # Load cached issues and compute team signals
    project_dir = _get_project_dir()
    cached_issues = _load_cached_issues(project_dir)
    team_signals = _compute_team_signals(cached_issues) if cached_issues else {
        "team_members": [], "inferred_team_size": 0,
        "total_open": 0, "unassigned_open": 0,
    }

    if cached_issues:
        print(
            f"Issues: {team_signals['total_open']} open "
            f"({team_signals['unassigned_open']} unassigned), "
            f"{team_signals['inferred_team_size']} team members detected",
            file=sys.stderr,
        )
    else:
        print("WARN: No cached issues. Run: reasonsforge project scan", file=sys.stderr)

    effective_team_size = team_size or team_signals["inferred_team_size"] or 3

    # Load top beliefs by impact
    beliefs_section = ""
    max_beliefs = 100
    if Path(db_path).exists():
        try:
            from reasonsforge.api import export_network
            net = export_network(db_path=db_path)
            net_nodes = net.get("nodes", {})

            in_nodes = []
            for nid, node in net_nodes.items():
                if node.get("truth_value") == "IN":
                    dep_count = len(node.get("dependents", []))
                    in_nodes.append((nid, node, dep_count))

            in_nodes.sort(key=lambda x: -x[2])
            lines = []
            for nid, node, dep_count in in_nodes[:max_beliefs]:
                text = node.get("text", "")
                dep_suffix = f" (dependents: {dep_count})" if dep_count > 0 else ""
                lines.append(f"- `{nid}`: {text}{dep_suffix}")
            beliefs_section = "\n".join(lines)
            if in_nodes:
                print(f"Beliefs: using top {min(len(in_nodes), max_beliefs)} of {len(in_nodes)} IN beliefs",
                      file=sys.stderr)
        except Exception:
            pass

    if not beliefs_section:
        beliefs_section = "No beliefs available."

    # Build prompt sections
    gating_section = _format_gating_section(gating_analysis)
    team_section = _format_team_section(team_signals)
    backlog_section = _format_backlog_section(cached_issues, gating_analysis)

    project_name = config.get("repo", config.get("project", "unknown"))

    prompt = build_sprint_plan_prompt(
        project_name=project_name,
        sprint_length=sprint_length,
        team_size=effective_team_size,
        gating_section=gating_section,
        team_section=team_section,
        backlog_section=backlog_section,
        beliefs_section=beliefs_section,
        start_date=date.today().isoformat(),
    )

    if dry_run:
        prompt_size_kb = len(prompt.encode()) / 1024
        print(f"\n=== Sprint Plan Prompt ({prompt_size_kb:.0f} KB) ===\n")
        print(prompt[:5000])
        if len(prompt) > 5000:
            print(f"\n... ({len(prompt) - 5000} more chars)")
        return

    print(f"Generating sprint plan with {model}...", file=sys.stderr)

    prompt_size_kb = len(prompt.encode()) / 1024
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(
            f"Error: Model {model} failed (prompt size: {prompt_size_kb:.0f} KB): {e}",
            file=sys.stderr,
        )
        sys.exit(1)

    short_name = project_name.split("//")[-1] if "//" in project_name else project_name
    safe_name = short_name.replace("/", "-")
    _create_entry(f"sprint-plan-{safe_name}",
                  f"Sprint Plan: {project_name} ({sprint_length})", result)

    if output:
        Path(output).write_text(result)
        print(f"Wrote sprint plan to {output}", file=sys.stderr)

    print(result)


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------


def cmd_topics(args):
    """Show the exploration queue."""
    project_dir = _get_project_dir()
    queue = load_queue(project_dir)

    if not queue:
        print("No topics queued. Run `reasonsforge project scan` to discover topics.")
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
    """Show project forge dashboard."""
    config = _load_config()

    print("=== Project Forge Status ===\n")

    if config:
        print(f"Platform: {config.get('platform', 'unknown')}")
        print(f"Target:   {config.get('repo', config.get('project', 'unknown'))}")
        print(f"Domain:   {config.get('domain', 'unknown')}")
        print(f"Created:  {config.get('created', 'unknown')}")
        if config.get("github_repo"):
            print(f"GitHub:   {config['github_repo']}")
    else:
        print("Not initialized. Run: reasonsforge project init --github <owner/repo>")
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
            from reasonsforge.api import export_network, get_status
            status = get_status(db_path=db_path)
            print(f"Beliefs:  {status['in_count']} IN, {status.get('out_count', status.get('total', 0) - status['in_count'])} OUT")
            network = export_network(db_path=db_path)
            nogood_count = len(network.get("nogoods", []))
            if nogood_count:
                print(f"Nogoods:  {nogood_count}")
        except Exception:
            print("Beliefs:  (error reading database)")
    else:
        print("Beliefs:  (no database)")

    # Topics
    project_dir = _get_project_dir()
    queue = load_queue(project_dir)
    pending = sum(1 for t in queue if t.status == "pending")
    done_count = sum(1 for t in queue if t.status == "done")
    skipped = sum(1 for t in queue if t.status == "skipped")
    print(f"Topics:   {pending} pending, {done_count} done, {skipped} skipped")

    # Cached issues
    cached = _load_cached_issues(project_dir)
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
    """Automated update pipeline: scan, explore, extract beliefs, derive, review, repair, summarize.

    Pulls all issues/PRs updated since a date, explores them, proposes and
    accepts beliefs, derives logical consequences, reviews and repairs
    derived beliefs, and generates a summary.
    """
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available
    _caffeinate()

    config = _load_config()
    if not config:
        print("Not initialized. Run: reasonsforge project init --github <owner/repo>")
        sys.exit(1)

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    project_dir = _get_project_dir()

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    # Resolve since date
    since = getattr(args, "since", None)
    since_last = getattr(args, "since_last", False)
    state = getattr(args, "state", None)
    limit = getattr(args, "limit", 100)
    all_pages = getattr(args, "all_pages", False)
    max_explore = getattr(args, "max_explore", None)
    parallel = getattr(args, "parallel", 1)
    db_path = getattr(args, "output", REASONS_DB)

    if since_last:
        since = _load_update_checkpoint(project_dir)
        if not since:
            print("No checkpoint found. Use --since <date> for the first run.", file=sys.stderr)
            sys.exit(1)
        print(f"Resuming from checkpoint: since {since}", file=sys.stderr)
    elif not since:
        print("Error: --since <date> or --since-last required.", file=sys.stderr)
        sys.exit(1)

    # Default to "all" states for update
    if state is None:
        state = "all"

    # Snapshot pre-run belief IDs
    try:
        network = _load_network(db_path)
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    errors = []
    project_name = config.get("repo", config.get("project", "unknown"))
    source = _get_source(config)

    # --- Step 1: Fetch and scan issues ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 1: Scanning issues/PRs", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        label_list = None
        total_issues = 0

        if all_pages:
            current_page = 1
            while True:
                print(f"  Fetching page {current_page}...", file=sys.stderr)
                issues = _fetch_issues(source, config, state=state, labels=label_list,
                                       limit=limit, page=current_page, since=since)
                if not issues:
                    break
                total_issues += len(issues)
                _run_scan_step(config, source, issues, project_name,
                               state, limit, current_page, project_dir, model, timeout)
                if len(issues) < limit:
                    break
                current_page += 1
        else:
            issues = _fetch_issues(source, config, state=state, labels=label_list,
                                   limit=limit, page=1, since=since)
            if issues:
                total_issues = len(issues)
                _run_scan_step(config, source, issues, project_name,
                               state, limit, 1, project_dir, model, timeout)

        if total_issues == 0:
            print(f"No issues found since {since}.", file=sys.stderr)
        else:
            print(f"Scanned {total_issues} issue(s).", file=sys.stderr)

    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"scan exited with code {e.code}")
            print(f"WARN: scan failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"scan: {e}")
        print(f"WARN: scan failed: {e}, continuing...", file=sys.stderr)

    # --- Step 2: Explore all pending topics ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 2: Exploring topics", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        topic_limit = max_explore or 99
        _explore_loop(args, project_dir, topic_limit, parallel)

    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"explore exited with code {e.code}")
            print(f"WARN: explore failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"explore: {e}")
        print(f"WARN: explore failed: {e}, continuing...", file=sys.stderr)

    # --- Step 3: Propose beliefs (auto-accept) ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 3: Proposing and accepting beliefs", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        # Create a synthetic args namespace for propose-beliefs
        import argparse
        propose_args = argparse.Namespace(
            model=model, timeout=timeout, batch_size=5,
            proposals_output="proposed-beliefs.md", output_file="proposed-beliefs.md",
            all=False, auto=True, since=since, parallel=parallel, output=db_path,
        )
        cmd_propose_beliefs(propose_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"propose-beliefs exited with code {e.code}")
            print(f"WARN: propose-beliefs failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"propose-beliefs: {e}")
        print(f"WARN: propose-beliefs failed: {e}, continuing...", file=sys.stderr)

    # --- Step 4: Derive (exhaust) ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 4: Deriving logical consequences", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        import argparse
        derive_args = argparse.Namespace(
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

    # --- Step 5: Review beliefs ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 5: Reviewing derived beliefs", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        import argparse
        review_args = argparse.Namespace(
            model=model, timeout=timeout, auto_retract=True,
            sample=None, min_depth=None, dry_run=False,
            review_output=None, output=db_path,
        )
        cmd_review_beliefs(review_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"review-beliefs exited with code {e.code}")
            print(f"WARN: review-beliefs failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"review-beliefs: {e}")
        print(f"WARN: review-beliefs failed: {e}, continuing...", file=sys.stderr)

    # --- Step 6: Repair ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 6: Repairing flagged beliefs", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        import argparse
        repair_args = argparse.Namespace(
            model=model, timeout=timeout, review_file=None,
            dry_run=False, output=db_path,
        )
        cmd_repair(repair_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"repair exited with code {e.code}")
            print(f"WARN: repair failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"repair: {e}")
        print(f"WARN: repair failed: {e}, continuing...", file=sys.stderr)

    # --- Step 7: Summary ---
    print(f"\n{'=' * 40}", file=sys.stderr)
    print("Step 7: Generating summary", file=sys.stderr)
    print(f"{'=' * 40}", file=sys.stderr)

    try:
        cmd_summary(args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"summary exited with code {e.code}")
            print(f"WARN: summary failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"summary: {e}")
        print(f"WARN: summary failed: {e}, continuing...", file=sys.stderr)

    # --- Save checkpoint ---
    _save_update_checkpoint(project_dir)
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
