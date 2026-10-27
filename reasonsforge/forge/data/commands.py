"""Data forge commands — build EEMs from database schemas."""

from __future__ import annotations

import sys
from pathlib import Path

from . import PROJECT_DIR
from .. import REASONS_DB


def cmd_schema(args):
    """Parse schema sources and display the normalized schema."""
    from .pipeline import stage_parse_schema
    from .schema_parser import schema_to_text

    schema = stage_parse_schema(args)
    print(schema_to_text(schema))


def cmd_introspect(args):
    """Introspect a live database and display the schema."""
    connection = getattr(args, "connection", None)
    if not connection:
        print("Error: --connection is required for introspect", file=sys.stderr)
        sys.exit(1)

    from .schema_parser import introspect_database, schema_to_text
    schema = introspect_database(connection)
    print(f"Found {len(schema.tables)} table(s)", file=sys.stderr)
    print(schema_to_text(schema))


def cmd_profile(args):
    """Profile data files and display statistics."""
    from .profiler import profile_csv, profile_to_text, DataProfile

    profiles = DataProfile()
    csv_files = getattr(args, "csv", None) or []

    for csv_path in csv_files:
        path = Path(csv_path)
        if not path.is_file():
            print(f"File not found: {csv_path}", file=sys.stderr)
            continue
        p = profile_csv(path)
        profiles.tables.extend(p.tables)

    if not profiles.tables:
        print("No data files to profile.", file=sys.stderr)
        return

    print(profile_to_text(profiles))


def cmd_enrich(args):
    """Run semantic enrichment on parsed schema."""
    from .pipeline import stage_parse_schema, stage_profile, stage_enrich, stage_accept_beliefs

    schema = stage_parse_schema(args)
    profile_text = stage_profile(args, schema)
    beliefs = stage_enrich(args, schema, profile_text)

    if not beliefs:
        print("No beliefs extracted.", file=sys.stderr)
        return

    db_path = getattr(args, "output", REASONS_DB)
    model = getattr(args, "model", "")
    added = stage_accept_beliefs(beliefs, db_path, model=model)
    print(f"Added {added} belief(s) to {db_path}", file=sys.stderr)


def cmd_status(args):
    """Show data forge status."""
    db_path = getattr(args, "output", REASONS_DB)

    if not Path(db_path).exists():
        print(f"No database found at {db_path}")
        print("Run: reasonsforge data --ddl schema.sql")
        return

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        nodes = network.get("nodes", {})
    except Exception:
        nodes = {}

    total = len(nodes)
    in_nodes = sum(1 for n in nodes.values() if n.get("truth_value") == "IN")
    data_forge = sum(
        1 for n in nodes.values()
        if "data-forge" in (n.get("source") or "")
    )

    print(f"Database: {db_path}")
    print(f"  Total beliefs: {total}")
    print(f"  IN beliefs: {in_nodes}")
    print(f"  Data forge beliefs: {data_forge}")
