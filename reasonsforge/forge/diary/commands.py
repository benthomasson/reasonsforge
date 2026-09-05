"""Diary forge commands — extract beliefs from diary entries."""

import sys
from pathlib import Path
from types import SimpleNamespace

from .. import REASONS_DB


def cmd_update(args):
    """Run propose-beliefs → accept-beliefs on diary entries."""
    from ..propose import cmd_propose_beliefs, cmd_accept_beliefs, auto_accept_proposals

    input_dir = getattr(args, "input_dir", "entries")
    model = getattr(args, "model", "claude")
    parallel = getattr(args, "parallel", 1)
    batch_size = getattr(args, "batch_size", 5)
    process_all = getattr(args, "all", False)
    no_auto_accept = getattr(args, "no_auto_accept", False)
    output_file = getattr(args, "proposals_output", "proposed-beliefs.md")

    if not Path(input_dir).exists():
        print(f"No {input_dir}/ directory found. Write some entries first.")
        sys.exit(1)

    entries = sorted(Path(input_dir).rglob("*.md"))
    if not entries:
        print(f"No .md files found in {input_dir}/.")
        return

    prop_args = SimpleNamespace(
        input_dir=input_dir,
        output=output_file,
        model=model,
        parallel=parallel,
        batch_size=batch_size,
        entry=None,
    )
    setattr(prop_args, "all", process_all)

    cmd_propose_beliefs(prop_args)

    proposals_path = Path(output_file)
    if not proposals_path.exists() or proposals_path.stat().st_size == 0:
        return

    if no_auto_accept:
        print("\nReview proposed-beliefs.md, then run:", file=sys.stderr)
        print("  reasonsforge forge diary accept", file=sys.stderr)
        return

    auto_accept_proposals(str(proposals_path))
    accept_args = SimpleNamespace(file=str(proposals_path))
    cmd_accept_beliefs(accept_args)


def cmd_accept(args):
    """Import accepted beliefs from proposals file."""
    from ..propose import cmd_accept_beliefs

    accept_args = SimpleNamespace(
        file=getattr(args, "file", "proposed-beliefs.md"),
    )
    cmd_accept_beliefs(accept_args)


def cmd_status(args):
    """Show diary forge status."""
    from ..propose import _load_processed
    from .. import PROJECT_DIR

    input_dir = getattr(args, "input_dir", "entries")
    input_path = Path(input_dir)

    if not input_path.exists():
        print(f"No {input_dir}/ directory.")
        return

    entries = sorted(input_path.rglob("*.md"))
    processed_path = Path(PROJECT_DIR) / "proposed-entries.json"
    processed = _load_processed(processed_path)

    processed_count = sum(1 for e in entries if str(e) in processed)
    unprocessed = len(entries) - processed_count

    db_path = getattr(args, "output", REASONS_DB)
    belief_count = 0
    if Path(db_path).exists():
        try:
            from reasonsforge.api import export_network
            network = export_network(db_path=db_path)
            belief_count = sum(
                1 for n in network.get("nodes", {}).values()
                if n.get("truth_value") == "IN"
            )
        except Exception:
            pass

    print(f"Diary forge status:")
    print(f"  Entries directory: {input_dir}/")
    print(f"  Total entries:     {len(entries)}")
    print(f"  Processed:         {processed_count}")
    print(f"  Unprocessed:       {unprocessed}")
    print(f"  Active beliefs:    {belief_count}")
    if unprocessed:
        print(f"\nRun 'reasonsforge forge diary update' to process {unprocessed} new entries.")
