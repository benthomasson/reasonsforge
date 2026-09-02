"""Meta forge commands — cross-domain reasoning across expert belief networks.

Each function takes an argparse Namespace and uses reasonsforge.api directly
(no subprocess calls to the reasons CLI).
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from . import META_DIR
from .prompts import (
    ASK_FALLBACK_PROMPT,
    build_contradictions_prompt,
    build_derive_prompt,
    build_summary_prompt,
)
from .topics import (
    Topic,
    add_topics,
    load_queue,
    parse_topics_from_response,
    pending_count,
)

REASONS_DB = "reasons.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict | None:
    """Load .forge/meta/config.json if it exists."""
    config_path = Path.cwd() / META_DIR / "config.json"
    if config_path.is_file():
        return json.loads(config_path.read_text())
    return None


def _save_config(config: dict) -> None:
    """Save config to .forge/meta/config.json."""
    config_dir = Path.cwd() / META_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(config, indent=2))


def _require_config() -> dict:
    """Load config or exit with error."""
    config = _load_config()
    if config is None:
        print("Error: not a meta forge knowledge base", file=sys.stderr)
        print("Run: reasonsforge meta init", file=sys.stderr)
        sys.exit(1)
    return config


def _get_meta_dir() -> str:
    """Return the absolute path to the meta forge directory."""
    return str(Path.cwd() / META_DIR)


def _create_entry(topic: str, title: str, content: str) -> Path | None:
    """Write an entry file directly to summaries/YYYY/MM/DD/."""
    today = date.today()
    summary_dir = Path("summaries") / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
    summary_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H%M")
    entry_name = f"{topic}-{timestamp}"
    entry_path = summary_dir / f"{entry_name}.md"
    entry_path.write_text(f"# {title}\n\n{content}\n")
    print(f"Entry: {entry_path}", file=sys.stderr)
    return entry_path


def _load_network(db_path: str = REASONS_DB) -> dict:
    """Load the full network via reasonsforge.api.export_network()."""
    from reasonsforge.api import export_network
    return export_network(db_path=db_path)


def _group_beliefs_by_agent(nodes: dict) -> dict[str, list[dict]]:
    """Group node entries by agent namespace (split on ':' prefix).

    Returns a dict of agent_name -> list of belief dicts.
    Skips ':active' sentinel nodes.
    """
    beliefs_by_agent: dict[str, list[dict]] = {}
    for nid, node in nodes.items():
        if nid.endswith(":active"):
            continue
        if ":" in nid:
            agent = nid.split(":")[0]
            beliefs_by_agent.setdefault(agent, []).append({
                "id": nid,
                "text": node["text"],
                "truth_value": node["truth_value"],
            })
    return beliefs_by_agent


def _get_derived_beliefs(nodes: dict) -> list[dict]:
    """Return beliefs without a ':' prefix (cross-domain derived)."""
    derived = []
    for nid, node in nodes.items():
        if nid.endswith(":active"):
            continue
        if ":" not in nid:
            derived.append({
                "id": nid,
                "text": node["text"],
                "truth_value": node["truth_value"],
            })
    return derived


def _parse_derive_proposals(response: str) -> list[dict]:
    """Parse DERIVE and GATE proposals from LLM response."""
    proposals = []
    pattern = re.compile(
        r"###\s+(DERIVE|GATE)\s+(\S+)\s*\n"
        r"(.+?)(?=\n###\s+(?:DERIVE|GATE)|\Z)",
        re.DOTALL,
    )

    for match in pattern.finditer(response):
        kind = match.group(1).lower()
        belief_id = match.group(2)
        body = match.group(3).strip()

        # Extract text (first line)
        lines = body.split("\n")
        text = lines[0].strip()

        # Extract antecedents, outlist, label
        antecedents = []
        outlist = []
        label = ""
        for line in lines[1:]:
            line = line.strip().lstrip("- ")
            if line.lower().startswith("antecedents:"):
                ant_str = line.split(":", 1)[1].strip()
                antecedents = [a.strip() for a in ant_str.split(",") if a.strip()]
            elif line.lower().startswith("unless:"):
                unless_str = line.split(":", 1)[1].strip()
                outlist = [u.strip() for u in unless_str.split(",") if u.strip()]
            elif line.lower().startswith("label:"):
                label = line.split(":", 1)[1].strip()

        if antecedents:
            proposals.append({
                "kind": kind,
                "id": belief_id,
                "text": text,
                "antecedents": antecedents,
                "outlist": outlist,
                "label": label,
            })

    return proposals


def _parse_nogood_proposals(response: str) -> list[dict]:
    """Parse NOGOOD proposals from LLM response."""
    proposals = []
    pattern = re.compile(
        r"###\s+NOGOOD\s+(\S+)\s*\n"
        r"(.+?)(?=\n###\s+NOGOOD|\Z)",
        re.DOTALL,
    )

    for match in pattern.finditer(response):
        nogood_id = match.group(1)
        body = match.group(2).strip()

        claims = []
        analysis = ""
        severity = ""
        resolution = ""
        for line in body.split("\n"):
            line = line.strip().lstrip("- ")
            if line.lower().startswith("claims:"):
                claims_str = line.split(":", 1)[1].strip()
                claims = [c.strip() for c in claims_str.split(",") if c.strip()]
            elif line.lower().startswith("analysis:"):
                analysis = line.split(":", 1)[1].strip()
            elif line.lower().startswith("severity:"):
                severity = line.split(":", 1)[1].strip()
            elif line.lower().startswith("resolution:"):
                resolution = line.split(":", 1)[1].strip()

        if len(claims) >= 2:
            proposals.append({
                "id": nogood_id,
                "claims": claims,
                "analysis": analysis,
                "severity": severity,
                "resolution": resolution,
            })

    return proposals


def _derive_once(
    model: str,
    timeout: int,
    db_path: str = REASONS_DB,
    budget: int = 300,
    seed: int | None = None,
) -> int:
    """Run a single derivation round.  Returns the count of new beliefs applied."""
    from ..llm import invoke_sync

    network = _load_network(db_path)
    nodes = network.get("nodes", {})
    beliefs_by_agent = _group_beliefs_by_agent(nodes)
    derived_beliefs = _get_derived_beliefs(nodes)

    if not beliefs_by_agent:
        return 0

    prompt = build_derive_prompt(beliefs_by_agent, derived_beliefs, budget=budget, seed=seed)
    response = invoke_sync(prompt, model, timeout)
    proposals = _parse_derive_proposals(response)

    if not proposals:
        return 0

    from reasonsforge.api import add_node
    applied = 0
    for p in proposals:
        try:
            add_node(
                node_id=p["id"],
                text=p["text"],
                sl=",".join(p["antecedents"]),
                unless=",".join(p["outlist"]) if p["outlist"] else "",
                label=p["label"],
                db_path=db_path,
            )
            kind = p["kind"].upper()
            print(f"  [{kind}] {p['id']}: {p['text'][:80]}")
            applied += 1
        except Exception as exc:
            print(f"  Failed: {p['id']}: {exc}", file=sys.stderr)

    return applied


def _build_agent_stats(nodes: dict, nogoods: list) -> dict[str, int]:
    """Compute per-agent belief counts plus derived and nogoods totals."""
    stats: dict[str, int] = {"nogoods": len(nogoods), "derived": 0}
    for nid, node in nodes.items():
        if nid.endswith(":active"):
            continue
        if ":" in nid:
            agent = nid.split(":")[0]
            stats[agent] = stats.get(agent, 0) + 1
        else:
            stats["derived"] += 1
    return stats


def _resolve_expert_paths(config: dict) -> dict[str, str]:
    """Return validated expert name -> path mapping.

    Prints warnings for missing paths.
    """
    experts = config.get("experts", {})
    resolved = {}
    for name, path in experts.items():
        if os.path.isdir(path):
            resolved[name] = path
        else:
            print(f"Warning: expert path missing: {name} -> {path}", file=sys.stderr)
    return resolved


def _find_beliefs_file(expert_path: str) -> str | None:
    """Find network.json or beliefs.md in an expert directory.

    Checks the root and .forge/ subdirectory.
    Prefers network.json (lossless) over beliefs.md (lossy).
    """
    candidates = [
        os.path.join(expert_path, "network.json"),
        os.path.join(expert_path, ".forge", "network.json"),
        os.path.join(expert_path, "beliefs.md"),
        os.path.join(expert_path, ".forge", "beliefs.md"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _format_beliefs_by_agent(beliefs_by_agent: dict[str, list[dict]], budget: int = 200) -> str:
    """Format beliefs grouped by agent for prompt inclusion."""
    sections = []
    for agent_name in sorted(beliefs_by_agent):
        beliefs = beliefs_by_agent[agent_name]
        in_beliefs = [b for b in beliefs if b["truth_value"] == "IN"]
        if not in_beliefs:
            continue

        lines = [f"### {agent_name} expert ({len(in_beliefs)} IN beliefs)"]
        for b in in_beliefs[:budget]:
            lines.append(f"- `{b['id']}`: {b['text'][:200]}")
        if len(in_beliefs) > budget:
            lines.append(f"*({len(in_beliefs) - budget} more omitted)*")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _export_files(db_path: str = REASONS_DB) -> None:
    """Re-export beliefs.md and network.json from the database."""
    from reasonsforge.api import export_markdown, export_network

    md = export_markdown(db_path=db_path)
    Path("beliefs.md").write_text(md)

    network = export_network(db_path=db_path)
    Path("network.json").write_text(json.dumps(network, indent=2, sort_keys=True))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_init(args) -> None:
    """Bootstrap .forge/meta/, save config, init DB, register repos.

    Args attributes:
        experts: list of "NAME=PATH" strings
        domain: domain description string
    """
    # Parse expert strings into name -> path mapping
    experts: dict[str, str] = {}
    for arg in getattr(args, "experts", []) or []:
        if "=" not in arg:
            print(f"Error: expected NAME=PATH, got: {arg}", file=sys.stderr)
            sys.exit(1)
        name, path = arg.split("=", 1)
        experts[name] = os.path.abspath(path)

    domain = getattr(args, "domain", "Cross-domain expert synthesis") or "Cross-domain expert synthesis"

    # Save config
    config = {
        "experts": experts,
        "domain": domain,
        "created": date.today().isoformat(),
    }
    _save_config(config)

    # Create summaries dir
    Path("summaries").mkdir(exist_ok=True)

    # Init reasons.db
    from reasonsforge.api import init_db, add_repo

    if not Path(REASONS_DB).exists():
        init_db(db_path=REASONS_DB)
        for name, path in experts.items():
            add_repo(name, path, db_path=REASONS_DB)

    print(f"Initialized meta forge knowledge base")
    print(f"  Domain: {domain}")
    if experts:
        print(f"  Experts: {', '.join(experts.keys())}")
    else:
        print("  Experts: none (add via .forge/meta/config.json)")
    print(f"  Config: {META_DIR}/config.json")
    print(f"  Database: {REASONS_DB}")
    print()
    print("Next: reasonsforge meta import")


def cmd_import_beliefs(args) -> None:
    """Import beliefs from expert repos via sync_agent.

    Args attributes:
        expert: optional, import from a specific expert only
        only_in: bool, import only IN beliefs
    """
    config = _require_config()
    experts = config.get("experts", {})
    only_in = getattr(args, "only_in", False)
    target_expert = getattr(args, "expert", None)

    if target_expert:
        if target_expert not in experts:
            print(f"Error: unknown expert '{target_expert}'", file=sys.stderr)
            print(f"Available: {', '.join(experts.keys())}", file=sys.stderr)
            sys.exit(1)
        experts = {target_expert: experts[target_expert]}

    from reasonsforge.api import sync_agent

    total_imported = 0
    import_details = []
    for name, path in experts.items():
        beliefs_path = _find_beliefs_file(path)
        if beliefs_path is None:
            print(f"Warning: no network.json or beliefs.md in {path}, skipping {name}", file=sys.stderr)
            continue

        try:
            result = sync_agent(
                agent_name=name,
                beliefs_file=beliefs_path,
                only_in=only_in,
                db_path=REASONS_DB,
            )
            added = result.get("beliefs_added", 0)
            updated = result.get("beliefs_updated", 0)
            retracted = result.get("beliefs_retracted", 0)
            print(f"Imported {name} expert from {beliefs_path}")
            print(f"  Added: {added}, Updated: {updated}, Retracted: {retracted}")
            import_details.append(f"{name}: +{added} ~{updated} -{retracted}")
            total_imported += 1
        except Exception as exc:
            print(f"Error importing {name}: {exc}", file=sys.stderr)
            continue

    if total_imported > 0:
        print()
        print("Exporting combined network...", file=sys.stderr)
        _export_files(REASONS_DB)
        print("  -> beliefs.md", file=sys.stderr)
        print("  -> network.json", file=sys.stderr)

        # Create entry
        expert_names = ", ".join(experts.keys())
        _create_entry(
            "import",
            f"Imported beliefs from {expert_names}",
            f"Imported beliefs from {total_imported} expert(s): {expert_names}\n\n"
            f"Only IN: {only_in}\n\n"
            + "\n".join(import_details),
        )

    print()
    print(f"Done. Imported from {total_imported}/{len(experts)} expert(s).")
    print("Next: reasonsforge meta derive --auto")


def cmd_derive(args) -> None:
    """Cross-domain derivation.

    Args attributes:
        auto: bool, automatically apply proposals
        exhaust: bool, loop until no new proposals
        dry_run: bool, print prompt without invoking LLM
        budget: int, max beliefs per agent in prompt (default 300)
        seed: optional int, random seed for sampling
        model: str, LLM model name
        timeout: int, LLM timeout in seconds
    """
    _require_config()
    auto_apply = getattr(args, "auto", False)
    exhaust = getattr(args, "exhaust", False)
    dry_run = getattr(args, "dry_run", False)
    budget = getattr(args, "budget", 300) or 300
    seed = getattr(args, "seed", None)
    model = getattr(args, "model", "claude") or "claude"
    timeout = getattr(args, "timeout", 300) or 300

    db_path = REASONS_DB

    # Load network
    network = _load_network(db_path)
    nodes = network.get("nodes", {})
    beliefs_by_agent = _group_beliefs_by_agent(nodes)
    derived_beliefs = _get_derived_beliefs(nodes)

    if not beliefs_by_agent:
        print("No agent beliefs found. Run: reasonsforge meta import", file=sys.stderr)
        sys.exit(1)

    # Build prompt
    prompt = build_derive_prompt(beliefs_by_agent, derived_beliefs, budget=budget, seed=seed)

    if dry_run:
        print(prompt)
        return

    from ..llm import invoke_sync

    print("Deriving cross-domain insights...", file=sys.stderr)
    for agent, beliefs in beliefs_by_agent.items():
        in_count = sum(1 for b in beliefs if b["truth_value"] == "IN")
        print(f"  {agent}: {in_count} IN beliefs", file=sys.stderr)
    print(f"  Existing derived: {len(derived_beliefs)}", file=sys.stderr)
    print(file=sys.stderr)

    if exhaust and auto_apply:
        # Loop until no new proposals
        total_applied = 0
        round_num = 0
        while True:
            round_num += 1
            print(f"--- Round {round_num} ---", file=sys.stderr)
            applied = _derive_once(model, timeout, db_path, budget, seed)
            total_applied += applied
            print(f"  Applied {applied} in round {round_num}", file=sys.stderr)
            if applied == 0:
                break

        if total_applied > 0:
            _export_files(db_path)
            _create_entry(
                "derive",
                f"Derived {total_applied} cross-domain beliefs ({round_num} rounds)",
                f"Exhaustive derivation: {total_applied} beliefs across {round_num} round(s).",
            )
        print(f"\nTotal: {total_applied} beliefs derived in {round_num} round(s)")
        return

    response = invoke_sync(prompt, model, timeout)

    # Parse proposals
    proposals = _parse_derive_proposals(response)
    print(f"Found {len(proposals)} proposal(s)")

    if not proposals:
        print("No cross-domain derivations proposed.")
        return

    if auto_apply:
        from reasonsforge.api import add_node

        applied = 0
        for p in proposals:
            try:
                add_node(
                    node_id=p["id"],
                    text=p["text"],
                    sl=",".join(p["antecedents"]),
                    unless=",".join(p["outlist"]) if p["outlist"] else "",
                    label=p["label"],
                    db_path=db_path,
                )
                kind = p["kind"].upper()
                print(f"  [{kind}] {p['id']}: {p['text'][:80]}")
                applied += 1
            except Exception as exc:
                print(f"  Failed: {p['id']}: {exc}", file=sys.stderr)

        print(f"\nApplied {applied}/{len(proposals)} proposals")
        _export_files(db_path)

        _create_entry(
            "derive",
            f"Derived {applied} cross-domain beliefs",
            f"Applied {applied} cross-domain derivations.\n\n{response}",
        )
    else:
        # Write to file for review
        output_path = "proposed-derivations.md"
        Path(output_path).write_text(
            f"# Cross-Domain Derivation Proposals\n\n"
            f"Generated: {date.today().isoformat()}\n\n"
            f"{response}"
        )
        print(f"Proposals written to {output_path}")
        print("Review and run: reasonsforge meta derive --auto")


def cmd_ask(args) -> None:
    """Question answering against the combined belief network.

    Args attributes:
        question: str, the question to ask
        model: str, LLM model name
        timeout: int, LLM timeout in seconds
    """
    config = _require_config()
    domain = config.get("domain", "")
    model = getattr(args, "model", "claude") or "claude"
    timeout = getattr(args, "timeout", 300) or 300

    db_path = REASONS_DB
    if not Path(db_path).exists():
        print("Error: reasons.db not found. Run: reasonsforge meta import", file=sys.stderr)
        sys.exit(1)

    question = args.question

    from ..llm import invoke_sync

    # Try FTS5 search for closest beliefs
    closest_beliefs = ""
    try:
        from reasonsforge.api import search
        search_result = search(question, db_path=db_path, format="markdown")
        if search_result and "No results" not in search_result:
            closest_beliefs = search_result
    except Exception:
        pass

    # Fall back to loading network and simple keyword matching
    if not closest_beliefs:
        network = _load_network(db_path)
        nodes = network.get("nodes", {})
        query_terms = question.lower().split()
        matches = []
        for nid, node in nodes.items():
            if nid.endswith(":active"):
                continue
            text_lower = f"{nid} {node['text']}".lower()
            score = sum(1 for t in query_terms if t in text_lower)
            if score > 0:
                matches.append((score, nid, node))
        matches.sort(key=lambda x: -x[0])
        if matches:
            lines = []
            for score, nid, node in matches[:10]:
                lines.append(f"- [{node['truth_value']}] `{nid}`: {node['text'][:200]}")
            closest_beliefs = "\n".join(lines)
        else:
            closest_beliefs = "(no matching beliefs found)"

    # Get compact summary
    from reasonsforge.api import compact
    try:
        compact_summary = compact(budget=2000, db_path=db_path)
    except Exception:
        compact_summary = "(compact unavailable)"

    # Build agent names from config
    agent_names = ", ".join(sorted(config.get("experts", {}).keys()))

    prompt = ASK_FALLBACK_PROMPT.format(
        domain=domain,
        question=question,
        closest_beliefs=closest_beliefs,
        compact_summary=compact_summary,
        agent_names=agent_names,
    )

    print("(Querying belief network...)\n", file=sys.stderr)
    response = invoke_sync(prompt, model, timeout)
    print(response)

    # Create entry for the question
    _create_entry(
        "ask",
        f"Question: {question[:60]}",
        f"## Question\n{question}\n\n## Answer\n{response}",
    )


def cmd_contradictions(args) -> None:
    """Detect cross-domain contradictions.

    Args attributes:
        auto: bool, automatically apply nogoods
        model: str, LLM model name
        timeout: int, LLM timeout in seconds
    """
    config = _require_config()
    auto_apply = getattr(args, "auto", False)
    model = getattr(args, "model", "claude") or "claude"
    timeout = getattr(args, "timeout", 300) or 300

    db_path = REASONS_DB
    network = _load_network(db_path)
    nodes = network.get("nodes", {})
    beliefs_by_agent = _group_beliefs_by_agent(nodes)

    if not beliefs_by_agent:
        print("No agent beliefs found. Run: reasonsforge meta import", file=sys.stderr)
        sys.exit(1)

    # Build beliefs section with only IN beliefs
    beliefs_section = _format_beliefs_by_agent(beliefs_by_agent)

    if not beliefs_section:
        print("No IN beliefs found across agents.", file=sys.stderr)
        sys.exit(1)

    agent_names = list(config.get("experts", {}).keys())
    prompt = build_contradictions_prompt(beliefs_section, agent_names=agent_names)

    from ..llm import invoke_sync

    print("Detecting cross-domain contradictions...", file=sys.stderr)
    response = invoke_sync(prompt, model, timeout)

    proposals = _parse_nogood_proposals(response)
    print(f"Found {len(proposals)} contradiction(s)")

    if not proposals:
        print("No cross-domain contradictions detected.")
        return

    if auto_apply:
        from reasonsforge.api import add_nogood

        applied = 0
        for p in proposals:
            try:
                add_nogood(node_ids=p["claims"], db_path=db_path)
                print(f"  [NOGOOD] {p['id']}: {', '.join(p['claims'])}")
                print(f"    {p['analysis'][:100]}")
                applied += 1
            except Exception as exc:
                print(f"  Failed: {p['id']}: {exc}", file=sys.stderr)

        print(f"\nApplied {applied}/{len(proposals)} nogoods")
        _export_files(db_path)

        _create_entry(
            "contradictions",
            f"Found {applied} cross-domain contradictions",
            f"Detected {applied} cross-domain contradictions.\n\n{response}",
        )
    else:
        output_path = "proposed-nogoods.md"
        Path(output_path).write_text(
            f"# Cross-Domain Contradiction Proposals\n\n"
            f"Generated: {date.today().isoformat()}\n\n"
            f"{response}"
        )
        print(f"Proposals written to {output_path}")


def cmd_summary(args) -> None:
    """Executive synthesis across all expert domains.

    Args attributes:
        model: str, LLM model name
        timeout: int, LLM timeout in seconds
    """
    config = _require_config()
    domain = config.get("domain", "")
    model = getattr(args, "model", "claude") or "claude"
    timeout = getattr(args, "timeout", 300) or 300

    db_path = REASONS_DB

    # Get compact summary
    from reasonsforge.api import compact
    try:
        beliefs_text = compact(budget=2000, db_path=db_path)
    except Exception:
        beliefs_text = "(compact unavailable)"

    # Get per-agent stats
    network = _load_network(db_path)
    nodes = network.get("nodes", {})
    nogoods = network.get("nogoods", [])
    agent_stats = _build_agent_stats(nodes, nogoods)

    agent_names = list(config.get("experts", {}).keys())
    prompt = build_summary_prompt(beliefs_text, domain, agent_stats, agent_names=agent_names)

    from ..llm import invoke_sync

    print("Generating executive summary...", file=sys.stderr)
    response = invoke_sync(prompt, model, timeout)

    print(response)

    _create_entry(
        "summary",
        "Executive synthesis across all expert domains",
        response,
    )


def cmd_topics(args) -> None:
    """Show investigation queue.

    Args attributes:
        all: bool, show all topics including done/skipped
    """
    _require_config()
    queue = load_queue()

    if not queue:
        print("No topics in queue.")
        return

    show_all = getattr(args, "all", False)

    pending = [t for t in queue if t.status == "pending"]
    done = [t for t in queue if t.status == "done"]
    skipped = [t for t in queue if t.status == "skipped"]

    print(f"Topics: {len(pending)} pending, {len(done)} done, {len(skipped)} skipped\n")

    display = queue if show_all else pending
    for i, topic in enumerate(display):
        status_icon = {"pending": " ", "done": "x", "skipped": "-"}.get(topic.status, "?")
        print(f"  [{status_icon}] {i:3d}. [{topic.kind}] `{topic.target}` -- {topic.title}")


def cmd_status(args) -> None:
    """Dashboard showing meta forge state.

    Args attributes: (none required)
    """
    config = _require_config()
    domain = config.get("domain", "")
    experts = config.get("experts", {})

    print(f"Meta Forge: {domain}")
    print(f"Created: {config.get('created', 'unknown')}")
    print()

    # Expert repos
    print(f"Expert repos ({len(experts)}):")
    for name, path in experts.items():
        beliefs_file = _find_beliefs_file(path)
        exists = "ok" if beliefs_file else "MISSING"
        print(f"  {name:12s} {path} [{exists}]")
    print()

    # Network stats
    if Path(REASONS_DB).exists():
        try:
            network = _load_network(REASONS_DB)
            nodes = network.get("nodes", {})
            nogoods = network.get("nogoods", [])

            # Filter out :active sentinel nodes for display
            real_nodes = {
                nid: n for nid, n in nodes.items()
                if not nid.endswith(":active")
            }
            total_in = sum(1 for n in real_nodes.values() if n["truth_value"] == "IN")
            total_out = sum(1 for n in real_nodes.values() if n["truth_value"] != "IN")
            print(f"Belief network: {total_in} IN, {total_out} OUT, {len(real_nodes)} total")
            print()

            # Per-agent breakdown
            agent_counts: dict[str, dict[str, int]] = {}
            derived_count = 0
            for nid, node in real_nodes.items():
                if ":" in nid:
                    agent = nid.split(":")[0]
                    if agent not in agent_counts:
                        agent_counts[agent] = {"IN": 0, "OUT": 0}
                    if node["truth_value"] == "IN":
                        agent_counts[agent]["IN"] += 1
                    else:
                        agent_counts[agent]["OUT"] += 1
                else:
                    derived_count += 1

            if agent_counts:
                print("Per-agent breakdown:")
                for agent in sorted(agent_counts):
                    c = agent_counts[agent]
                    print(f"  {agent:12s} {c['IN']} IN, {c['OUT']} OUT")
                print(f"  {'derived':12s} {derived_count}")
                print(f"  {'nogoods':12s} {len(nogoods)}")
                print()
        except Exception:
            print("Belief network: error reading network")
            print()
    else:
        print("Belief network: not initialized")
        print()

    # Topics
    pc = pending_count()
    total = len(load_queue())
    print(f"Topics: {pc} pending / {total} total")

    # Entries
    entries_dir = Path("summaries")
    if entries_dir.exists():
        entry_count = sum(1 for _ in entries_dir.rglob("*.md"))
        print(f"Entries: {entry_count}")
    else:
        print("Entries: 0")


STEPS = ["import", "derive", "contradictions", "summary"]


def cmd_update(args) -> None:
    """Pipeline: import -> derive -> contradictions -> summary.

    Args attributes:
        skip: list of step names to skip
        budget: int, max beliefs per agent for derive
        seed: optional int, random seed for derive
        model: str, LLM model name
        timeout: int, LLM timeout in seconds
    """
    config = _require_config()
    skip = set(getattr(args, "skip", []) or [])
    budget = getattr(args, "budget", 300) or 300
    seed = getattr(args, "seed", None)
    model = getattr(args, "model", "claude") or "claude"
    timeout = getattr(args, "timeout", 300) or 300

    db_path = REASONS_DB
    results: dict[str, int | str] = {}

    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    # --- import ---
    if "import" not in skip:
        print("=== import ===")
        experts = config.get("experts", {})
        from reasonsforge.api import sync_agent

        total_imported = 0
        for name, path in experts.items():
            beliefs_path = _find_beliefs_file(path)
            if beliefs_path is None:
                print(f"Warning: no network.json or beliefs.md in {path}, skipping {name}", file=sys.stderr)
                continue

            try:
                sync_agent(
                    agent_name=name,
                    beliefs_file=beliefs_path,
                    db_path=db_path,
                )
                print(f"  Imported {name} from {beliefs_path}")
                total_imported += 1
            except Exception as exc:
                print(f"Error importing {name}: {exc}", file=sys.stderr)
                continue

        if total_imported > 0:
            _export_files(db_path)
        results["import"] = total_imported
        print()

    # --- derive ---
    if "derive" not in skip:
        print("=== derive ===")
        network = _load_network(db_path)
        nodes = network.get("nodes", {})
        beliefs_by_agent = _group_beliefs_by_agent(nodes)

        if beliefs_by_agent:
            applied = _derive_once(model, timeout, db_path, budget, seed)
            _export_files(db_path)
            results["derive"] = applied
            print(f"  Applied {applied} derivations")
        else:
            print("  No agent beliefs found, skipping")
            results["derive"] = 0
        print()

    # --- contradictions ---
    if "contradictions" not in skip:
        print("=== contradictions ===")
        network = _load_network(db_path)
        nodes = network.get("nodes", {})
        beliefs_by_agent = _group_beliefs_by_agent(nodes)

        beliefs_section = _format_beliefs_by_agent(beliefs_by_agent, budget=budget)
        if beliefs_section:
            agent_names = list(config.get("experts", {}).keys())
            prompt = build_contradictions_prompt(beliefs_section, agent_names=agent_names)

            from ..llm import invoke_sync
            response = invoke_sync(prompt, model, timeout)
            proposals = _parse_nogood_proposals(response)

            from reasonsforge.api import add_nogood
            applied = 0
            for p in proposals:
                try:
                    add_nogood(node_ids=p["claims"], db_path=db_path)
                    print(f"  [NOGOOD] {p['id']}: {', '.join(p['claims'])}")
                    applied += 1
                except Exception as exc:
                    print(f"  Failed: {p['id']}: {exc}", file=sys.stderr)

            _export_files(db_path)
            results["contradictions"] = applied
            print(f"  Applied {applied}/{len(proposals)} nogoods")
        else:
            print("  No agent beliefs found, skipping")
            results["contradictions"] = 0
        print()

    # --- summary ---
    if "summary" not in skip:
        print("=== summary ===")
        domain = config.get("domain", "")

        from reasonsforge.api import compact
        try:
            beliefs_text = compact(budget=2000, db_path=db_path)
        except Exception:
            beliefs_text = "(compact unavailable)"

        network = _load_network(db_path)
        nodes = network.get("nodes", {})
        nogoods = network.get("nogoods", [])
        agent_stats = _build_agent_stats(nodes, nogoods)
        agent_names = list(config.get("experts", {}).keys())

        prompt = build_summary_prompt(beliefs_text, domain, agent_stats, agent_names=agent_names)

        from ..llm import invoke_sync
        response = invoke_sync(prompt, model, timeout)
        print(response)

        _create_entry(
            "update",
            "Full pipeline update",
            f"## Pipeline Results\n"
            f"- Import: {results.get('import', 'skipped')}\n"
            f"- Derive: {results.get('derive', 'skipped')}\n"
            f"- Contradictions: {results.get('contradictions', 'skipped')}\n\n"
            f"## Summary\n{response}",
        )

    # --- final status ---
    print()
    print("=== done ===")
    for step in STEPS:
        if step in skip:
            print(f"  {step:16s} skipped")
        elif step in results:
            print(f"  {step:16s} {results[step]}")
        else:
            print(f"  {step:16s} done")
