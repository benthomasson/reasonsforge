"""User-guide forge commands."""

from __future__ import annotations

import sys
from pathlib import Path

from . import PROJECT_DIR
from .. import REASONS_DB


def cmd_extract(args):
    """Extract interfaces and display them."""
    from .extractors.base import detect_language, get_extractor
    from .pipeline import _format_interfaces, _format_config, _format_tests, stage_extract

    repo_path = args.repo
    language = getattr(args, "language", None) or detect_language(repo_path)
    if not language:
        print("Could not detect language.", file=sys.stderr)
        sys.exit(1)

    extractor = get_extractor(language)
    extracted = stage_extract(args, extractor)

    print(f"Language: {language}")
    print(f"\n## Interfaces ({len(extracted['interfaces'])})\n")
    print(_format_interfaces(extracted["interfaces"]))
    print(f"\n## Config ({len(extracted['config'])})\n")
    print(_format_config(extracted["config"]))
    print(f"\n## Tests ({len(extracted['tests'])})\n")
    print(_format_tests(extracted["tests"]))


def cmd_status(args):
    """Show user-guide forge status."""
    db_path = getattr(args, "output", REASONS_DB)

    if not Path(db_path).exists():
        print(f"No database found at {db_path}")
        print("Run: reasonsforge user-guide --repo <path>")
        return

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        nodes = network.get("nodes", {})
    except Exception:
        nodes = {}

    total = len(nodes)
    in_nodes = sum(1 for n in nodes.values() if n.get("truth_value") == "IN")
    ug_forge = sum(
        1 for n in nodes.values()
        if "user-guide-forge" in (n.get("source") or "")
    )

    print(f"Database: {db_path}")
    print(f"  Total beliefs: {total}")
    print(f"  IN beliefs: {in_nodes}")
    print(f"  User-guide forge beliefs: {ug_forge}")
