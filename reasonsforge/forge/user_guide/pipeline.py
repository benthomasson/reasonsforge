"""User-guide forge pipeline — extract → propose → accept → derive."""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from . import PROJECT_DIR

REASONS_DB = "reasons.db"


def _fmt_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds) // 60
    secs = seconds - minutes * 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"


def _create_entry(topic: str, title: str, content: str, model: str = "") -> Path | None:
    """Write an entry file directly to summaries/YYYY/MM/DD/."""
    from datetime import date, datetime
    today = date.today()
    summary_dir = Path("summaries") / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
    summary_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H%M")
    entry_name = f"{topic}-{timestamp}"
    entry_path = summary_dir / f"{entry_name}.md"
    parts = []
    if model:
        parts.append(f"---\nmodel: \"{model}\"\ndate: {today.isoformat()}\n---\n\n")
    parts.append(f"# {title}\n\n{content}\n")
    entry_path.write_text("".join(parts))
    print(f"Entry: {entry_path}", file=sys.stderr)
    return entry_path


def _format_interfaces(interfaces) -> str:
    """Format extracted interfaces for LLM prompt."""
    parts = []
    for iface in interfaces:
        lines = [f"### {iface.name} ({iface.file_path}:{iface.line_number})"]
        if iface.description:
            lines.append(iface.description)
        for prop in iface.props:
            req = "required" if prop.required else "optional"
            default = f" = {prop.default_value}" if prop.default_value else ""
            desc = f" — {prop.description}" if prop.description else ""
            lines.append(
                f"- `{prop.name}{'?' if not prop.required else ''}: "
                f"{prop.type}{default}` ({req}){desc}"
            )
        parts.append("\n".join(lines))
    return "\n\n".join(parts) if parts else "(none extracted)"


def _format_config(config) -> str:
    """Format extracted config/tokens for LLM prompt."""
    if not config:
        return "(none extracted)"
    parts = []
    for cv in config:
        parts.append(f"- `{cv.name}`: {cv.value} [{cv.category}]")
    return "\n".join(parts)


def _format_tests(tests) -> str:
    """Format extracted test patterns for LLM prompt."""
    if not tests:
        return "(none extracted)"
    parts = []
    for tp in tests:
        parts.append(
            f"- [{tp.pattern_type}] {tp.description} "
            f"({tp.file_path}:{tp.line_number})"
        )
    return "\n".join(parts)


def _format_examples(examples: list[str]) -> str:
    """Format extracted usage examples for LLM prompt."""
    if not examples:
        return "(none extracted)"
    parts = []
    for i, snippet in enumerate(examples[:10], 1):
        parts.append(f"### Example {i}\n```\n{snippet}\n```")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def stage_extract(args, extractor) -> dict:
    """Stage 1: Extract interfaces, config, tests, examples from repo."""
    repo_path = args.repo
    components = getattr(args, "components", None)
    if components and isinstance(components, str):
        components = [c.strip() for c in components.split(",")]

    interfaces = extractor.extract_interfaces(repo_path, components=components)
    config = extractor.extract_config(repo_path, components=components)
    tests = extractor.extract_tests(repo_path, components=components)
    examples = extractor.extract_examples(repo_path, components=components)

    return {
        "interfaces": interfaces,
        "config": config,
        "tests": tests,
        "examples": examples,
    }


def _parse_beliefs(response: str) -> list[dict]:
    """Parse beliefs from an LLM response (JSON array or markdown fallback)."""
    from ..llm import extract_json

    data = extract_json(response)
    if isinstance(data, list):
        beliefs = []
        for item in data:
            if isinstance(item, dict) and "id" in item and "text" in item:
                belief = {"id": item["id"], "text": item["text"]}
                if item.get("example"):
                    belief["example"] = item["example"]
                beliefs.append(belief)
        if beliefs:
            return beliefs

    beliefs = []
    pattern = re.compile(r"^[-*]\s+`([^`]+)`\s*(?:—|-|:)\s*(.+)$", re.MULTILINE)
    for m in pattern.finditer(response):
        beliefs.append({"id": m.group(1), "text": m.group(2).strip()})
    return beliefs


BATCH_SIZE = 20


def _build_batches(extracted: dict) -> list[dict]:
    """Group interfaces into batches of ~BATCH_SIZE with matched tests."""
    components: dict[str, dict] = {}
    for iface in extracted["interfaces"]:
        comp = iface.component_name or iface.name
        if comp not in components:
            components[comp] = {"interfaces": [], "tests": []}
        components[comp]["interfaces"].append(iface)

    for tp in extracted.get("tests", []):
        stem = Path(tp.file_path).stem.lower().replace(".test", "").replace(".spec", "")
        for comp in components:
            if comp.lower() in stem:
                components[comp]["tests"].append(tp)
                break

    batches: list[dict] = []
    current: dict = {"interfaces": [], "tests": [], "names": []}
    for comp_name, comp_data in components.items():
        current["interfaces"].extend(comp_data["interfaces"])
        current["tests"].extend(comp_data["tests"])
        current["names"].append(comp_name)
        if len(current["interfaces"]) >= BATCH_SIZE:
            batches.append(current)
            current = {"interfaces": [], "tests": [], "names": []}
    if current["interfaces"]:
        batches.append(current)

    return batches


def _propose_batch(
    interfaces, config, tests, examples, model, timeout,
) -> list[dict]:
    """Propose beliefs for a single batch."""
    from ..llm import invoke_sync
    from .prompts.extract_interfaces import PROPOSE_BELIEFS_PROMPT

    prompt = PROPOSE_BELIEFS_PROMPT.format(
        interfaces_text=_format_interfaces(interfaces),
        config_text=_format_config(config),
        test_text=_format_tests(tests),
        examples_text=_format_examples(examples),
    )
    response = invoke_sync(prompt, model=model, timeout=timeout)
    return _parse_beliefs(response)


def stage_propose(args, extracted: dict) -> list[dict]:
    """Stage 2: LLM proposes beliefs from extracted facts."""
    import threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 600)
    parallel = getattr(args, "parallel", 1)

    batches = _build_batches(extracted)
    config = extracted.get("config", [])
    examples = extracted.get("examples", [])

    if len(batches) <= 1:
        return _propose_batch(
            extracted["interfaces"], config,
            extracted.get("tests", []), examples,
            model, timeout,
        )

    n_batches = len(batches)
    print(f"  {n_batches} batches (~{BATCH_SIZE} interfaces each, "
          f"parallel={parallel})", file=sys.stderr)

    all_beliefs: list[dict] = []
    lock = threading.Lock()
    done = [0]

    def _run(batch):
        beliefs = _propose_batch(
            batch["interfaces"], config,
            batch["tests"], examples,
            model, timeout,
        )
        with lock:
            done[0] += 1
            print(f"  Batch {done[0]}/{n_batches}: "
                  f"{len(batch['interfaces'])} interfaces → "
                  f"{len(beliefs)} beliefs", file=sys.stderr)
        return beliefs

    with ThreadPoolExecutor(max_workers=parallel) as executor:
        futures = [executor.submit(_run, b) for b in batches]
        for future in as_completed(futures):
            all_beliefs.extend(future.result())

    return all_beliefs


def stage_accept(beliefs: list[dict], db_path: str, model: str = "") -> int:
    """Stage 3: Accept beliefs into reasons.db (bulk insert, single DB open)."""
    from reasonsforge.api import _with_network, init_db

    if not Path(db_path).exists():
        init_db(db_path=db_path)

    metadata = {}
    if model:
        metadata["model"] = model

    added = 0
    with _with_network(db_path, write=True) as net:
        for belief in beliefs:
            try:
                node_meta = dict(metadata) if metadata else {}
                if belief.get("example"):
                    node_meta["example"] = belief["example"]
                net.add_node(
                    id=belief["id"],
                    text=belief["text"],
                    source="user-guide-forge",
                    metadata=node_meta or None,
                )
                added += 1
            except Exception as e:
                if "already exists" not in str(e):
                    print(f"  Failed to add {belief['id']}: {e}", file=sys.stderr)
    return added


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def cmd_pipeline(args):
    """Run the full user-guide forge pipeline."""
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    from .extractors.base import detect_language, get_extractor

    repo_path = args.repo
    language = getattr(args, "language", None)
    if not language:
        language = detect_language(repo_path)
        if not language:
            print("Could not detect language. Use --language to specify.",
                  file=sys.stderr)
            sys.exit(1)
        print(f"Detected language: {language}", file=sys.stderr)

    extractor = get_extractor(language)
    model = getattr(args, "model", "claude")
    db_path = getattr(args, "output", REASONS_DB)
    pipeline_t0 = time.monotonic()

    # Stage 1: Extract
    print("Stage 1: Extracting interfaces, config, tests...", file=sys.stderr)
    t0 = time.monotonic()
    extracted = stage_extract(args, extractor)
    elapsed = time.monotonic() - t0
    n_ifaces = len(extracted["interfaces"])
    n_props = sum(len(i.props) for i in extracted["interfaces"])
    n_config = len(extracted["config"])
    n_tests = len(extracted["tests"])
    n_examples = len(extracted["examples"])
    print(f"  {n_ifaces} interfaces, {n_props} props, {n_config} config values, "
          f"{n_tests} test patterns, {n_examples} examples ({_fmt_duration(elapsed)})",
          file=sys.stderr)

    if not extracted["interfaces"] and not extracted["config"]:
        print("No interfaces or config extracted. Check your --repo path.",
              file=sys.stderr)
        return

    # Stage 2: Propose beliefs
    print("Stage 2: Proposing beliefs...", file=sys.stderr)
    t0 = time.monotonic()
    beliefs = stage_propose(args, extracted)
    elapsed = time.monotonic() - t0
    print(f"  {len(beliefs)} beliefs proposed ({_fmt_duration(elapsed)})",
          file=sys.stderr)

    if not beliefs:
        print("No beliefs proposed.", file=sys.stderr)
        return

    # Stage 3: Accept beliefs
    print("Stage 3: Accepting beliefs...", file=sys.stderr)
    t0 = time.monotonic()
    added = stage_accept(beliefs, db_path, model=model)
    elapsed = time.monotonic() - t0
    print(f"  Added {added} belief(s) to {db_path} ({_fmt_duration(elapsed)})",
          file=sys.stderr)

    # Stage 4: Derive (optional)
    no_auto_accept = getattr(args, "no_auto_accept", False)
    rounds = getattr(args, "rounds", 1)
    ran_convergence = False
    if rounds > 0 and not no_auto_accept:
        print("Stage 4: Derive...", file=sys.stderr)
        t0 = time.monotonic()
        from ..pipeline import _run_convergence_loop
        try:
            _run_convergence_loop(args, rounds)
            elapsed = time.monotonic() - t0
            print(f"  DERIVE took {_fmt_duration(elapsed)}", file=sys.stderr)
            ran_convergence = True
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  Derive failed: {e} ({_fmt_duration(elapsed)})",
                  file=sys.stderr)

    # Stage 5: Dedup (only if convergence loop didn't run)
    if not ran_convergence:
        print("Stage 5: Deduplication...", file=sys.stderr)
        t0 = time.monotonic()
        try:
            from reasonsforge.api import deduplicate
            dedup_result = deduplicate(auto=True, db_path=db_path)
            retracted = dedup_result.get("retracted", [])
            elapsed = time.monotonic() - t0
            if retracted:
                print(f"  Retracted {len(retracted)} duplicate(s) "
                      f"({_fmt_duration(elapsed)})", file=sys.stderr)
            else:
                print(f"  No duplicates found ({_fmt_duration(elapsed)})",
                      file=sys.stderr)
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  Dedup skipped: {e} ({_fmt_duration(elapsed)})",
                  file=sys.stderr)

    total_elapsed = time.monotonic() - pipeline_t0
    print(f"\nUser-guide forge complete: {n_ifaces} interfaces, {n_props} props, "
          f"{added} beliefs added to {db_path}", file=sys.stderr)
    print(f"Duration: {_fmt_duration(total_elapsed)}", file=sys.stderr)

    _create_entry(
        "user-guide",
        f"User-guide forge: {n_ifaces} interfaces, {added} beliefs",
        f"Extracted {n_ifaces} interface(s) with {n_props} prop(s) "
        f"from {language} repo.\n"
        f"Proposed {len(beliefs)} belief(s), added {added} to {db_path}.\n"
        f"Duration: {_fmt_duration(total_elapsed)}.",
        model=model,
    )
