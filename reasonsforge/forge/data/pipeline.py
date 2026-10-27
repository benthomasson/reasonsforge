"""Data forge pipeline — schema → enrich → derive → dedup → export."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from pathlib import Path

from . import PROJECT_DIR
from .prompts import ENRICH_SCHEMA_PROMPT, PROFILE_INTERPRET_PROMPT
from .schema_parser import Schema, schema_to_text


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


def _load_context_files(context_paths: list[str]) -> str:
    """Load and concatenate context files (data dictionaries, docs, etc.)."""
    parts = []
    for path_str in context_paths:
        path = Path(path_str)
        if path.is_dir():
            for f in sorted(path.rglob("*.md")) + sorted(path.rglob("*.txt")):
                content = f.read_text()
                parts.append(f"### {f.name}\n\n{content}")
        elif path.is_file():
            content = path.read_text()
            parts.append(f"### {path.name}\n\n{content}")
        else:
            print(f"Warning: context path not found: {path_str}", file=sys.stderr)
    return "\n\n---\n\n".join(parts) if parts else "(no additional context provided)"


def _parse_beliefs_from_enrichment(response: str) -> list[dict]:
    """Parse belief items from LLM enrichment response."""
    beliefs = []
    pattern = re.compile(r"^[-*]\s+`([^`]+)`\s*(?:—|-|:)\s*(.+)$", re.MULTILINE)
    for m in pattern.finditer(response):
        beliefs.append({"id": m.group(1), "text": m.group(2).strip()})
    return beliefs


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def stage_parse_schema(args) -> Schema:
    """Stage 1: Parse schema from input sources."""
    from .schema_parser import parse_csv, parse_ddl, parse_parquet, introspect_database

    schemas: list[Schema] = []

    ddl_files = getattr(args, "ddl", None) or []
    for ddl_path in ddl_files:
        path = Path(ddl_path)
        if not path.is_file():
            print(f"DDL file not found: {ddl_path}", file=sys.stderr)
            continue
        sql = path.read_text()
        schema = parse_ddl(sql, source=str(path))
        schemas.append(schema)
        print(f"  Parsed {len(schema.tables)} table(s) from {path.name}", file=sys.stderr)

    csv_files = getattr(args, "csv", None) or []
    for csv_path in csv_files:
        path = Path(csv_path)
        if not path.is_file():
            print(f"CSV file not found: {csv_path}", file=sys.stderr)
            continue
        schema = parse_csv(path)
        schemas.append(schema)
        print(f"  Parsed {len(schema.tables)} table(s) from {path.name}", file=sys.stderr)

    parquet_files = getattr(args, "parquet", None) or []
    for pq_path in parquet_files:
        path = Path(pq_path)
        if not path.is_file():
            print(f"Parquet file not found: {pq_path}", file=sys.stderr)
            continue
        schema = parse_parquet(path)
        schemas.append(schema)
        print(f"  Parsed {len(schema.tables)} table(s) from {path.name}", file=sys.stderr)

    connection = getattr(args, "connection", None)
    if connection:
        schema = introspect_database(connection)
        schemas.append(schema)
        print(f"  Introspected {len(schema.tables)} table(s) from database", file=sys.stderr)

    if not schemas:
        print("No schema sources provided.", file=sys.stderr)
        sys.exit(1)

    combined = Schema(source="combined")
    for s in schemas:
        combined.tables.extend(s.tables)

    print(f"Schema: {len(combined.tables)} table(s), "
          f"{sum(len(t.columns) for t in combined.tables)} column(s)", file=sys.stderr)
    return combined


def stage_profile(args, schema: Schema) -> str | None:
    """Stage 2: Statistical profiling (optional)."""
    do_profile = getattr(args, "profile", False)
    if not do_profile:
        return None

    from .profiler import profile_csv, profile_parquet, profile_to_text, DataProfile

    profiles = DataProfile()

    csv_files = getattr(args, "csv", None) or []
    for csv_path in csv_files:
        path = Path(csv_path)
        if path.is_file():
            p = profile_csv(path)
            profiles.tables.extend(p.tables)

    parquet_files = getattr(args, "parquet", None) or []
    for pq_path in parquet_files:
        path = Path(pq_path)
        if path.is_file():
            p = profile_parquet(path)
            profiles.tables.extend(p.tables)

    if not profiles.tables:
        print("  No data files to profile.", file=sys.stderr)
        return None

    text = profile_to_text(profiles)
    print(f"  Profiled {len(profiles.tables)} table(s)", file=sys.stderr)
    return text


def stage_enrich(args, schema: Schema, profile_text: str | None = None) -> list[dict]:
    """Stage 3: LLM semantic enrichment of schema."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 600)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    context_paths = getattr(args, "context", None) or []
    context_text = _load_context_files(context_paths)
    schema_text = schema_to_text(schema)

    print(f"  Enriching schema with {model}...", file=sys.stderr)
    prompt = ENRICH_SCHEMA_PROMPT.format(
        schema_text=schema_text,
        context_text=context_text,
    )

    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error during enrichment: {e}", file=sys.stderr)
        sys.exit(1)

    beliefs = _parse_beliefs_from_enrichment(result)
    print(f"  Extracted {len(beliefs)} belief(s) from schema enrichment", file=sys.stderr)

    if profile_text:
        print(f"  Interpreting statistical profile...", file=sys.stderr)
        profile_prompt = PROFILE_INTERPRET_PROMPT.format(
            schema_text=schema_text,
            profile_text=profile_text,
            context_text=context_text,
        )
        try:
            profile_result = asyncio.run(invoke(profile_prompt, model, timeout=timeout))
            profile_beliefs = _parse_beliefs_from_enrichment(profile_result)
            beliefs.extend(profile_beliefs)
            print(f"  Extracted {len(profile_beliefs)} belief(s) from profile", file=sys.stderr)
        except Exception as e:
            print(f"Warning: profile interpretation failed: {e}", file=sys.stderr)

    return beliefs


def stage_accept_beliefs(beliefs: list[dict], db_path: str,
                         model: str = "") -> int:
    """Accept enriched beliefs into the reasons database."""
    from reasonsforge.api import add_node, init_db

    if not Path(db_path).exists():
        init_db(db_path=db_path)

    added = 0
    for belief in beliefs:
        try:
            add_node(
                belief["id"],
                belief["text"],
                source="data-forge:enrich",
                model=model,
                db_path=db_path,
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
    """Run the full data forge pipeline."""
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    db_path = getattr(args, "output", REASONS_DB)
    model = getattr(args, "model", "claude")
    pipeline_t0 = time.monotonic()

    print("Stage 1: Parsing schema...", file=sys.stderr)
    t0 = time.monotonic()
    schema = stage_parse_schema(args)
    elapsed = time.monotonic() - t0
    print(f"  PARSE took {_fmt_duration(elapsed)}", file=sys.stderr)

    print("Stage 2: Statistical profiling...", file=sys.stderr)
    t0 = time.monotonic()
    profile_text = stage_profile(args, schema)
    elapsed = time.monotonic() - t0
    print(f"  PROFILE took {_fmt_duration(elapsed)}", file=sys.stderr)

    print("Stage 3: Semantic enrichment...", file=sys.stderr)
    t0 = time.monotonic()
    beliefs = stage_enrich(args, schema, profile_text)
    elapsed = time.monotonic() - t0
    print(f"  ENRICH took {_fmt_duration(elapsed)}", file=sys.stderr)

    if not beliefs:
        print("No beliefs extracted. Check your inputs.", file=sys.stderr)
        return

    print("Stage 4: Accepting beliefs...", file=sys.stderr)
    t0 = time.monotonic()
    added = stage_accept_beliefs(beliefs, db_path, model=model)
    elapsed = time.monotonic() - t0
    print(f"  Added {added} belief(s) to {db_path} ({_fmt_duration(elapsed)})", file=sys.stderr)

    no_auto_accept = getattr(args, "no_auto_accept", False)
    rounds = getattr(args, "rounds", 1)

    if rounds > 0 and not no_auto_accept:
        print("Stage 5: Derive...", file=sys.stderr)
        t0 = time.monotonic()
        from ..pipeline import _run_convergence_loop
        try:
            _run_convergence_loop(args, rounds)
            elapsed = time.monotonic() - t0
            print(f"  DERIVE took {_fmt_duration(elapsed)}", file=sys.stderr)
        except Exception as e:
            elapsed = time.monotonic() - t0
            print(f"  Derive failed: {e} ({_fmt_duration(elapsed)})", file=sys.stderr)

    print("Stage 6: Deduplication...", file=sys.stderr)
    t0 = time.monotonic()
    try:
        from reasonsforge.api import deduplicate
        dedup_result = deduplicate(auto=True, db_path=db_path)
        retracted = dedup_result.get("retracted", [])
        elapsed = time.monotonic() - t0
        if retracted:
            print(f"  Retracted {len(retracted)} duplicate(s) ({_fmt_duration(elapsed)})", file=sys.stderr)
        else:
            print(f"  No duplicates found ({_fmt_duration(elapsed)})", file=sys.stderr)
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  Dedup skipped: {e} ({_fmt_duration(elapsed)})", file=sys.stderr)

    total_tables = len(schema.tables)
    total_cols = sum(len(t.columns) for t in schema.tables)
    total_elapsed = time.monotonic() - pipeline_t0

    print(f"\nData forge complete: {total_tables} tables, {total_cols} columns, "
          f"{added} beliefs added to {db_path}", file=sys.stderr)
    print(f"Duration: {_fmt_duration(total_elapsed)}", file=sys.stderr)

    _create_entry(
        "data-forge",
        f"Data forge: {total_tables} tables, {added} beliefs",
        f"Processed {total_tables} table(s) with {total_cols} column(s).\n"
        f"Extracted {len(beliefs)} belief(s), added {added} to {db_path}.\n"
        f"Duration: {_fmt_duration(total_elapsed)}.",
        model=model,
    )
