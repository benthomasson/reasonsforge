"""Meta forge pipeline — full automated cross-domain analysis."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

from . import META_DIR
from .commands import (
    REASONS_DB,
    _get_meta_dir,
    _load_config,
    cmd_contradictions,
    cmd_derive,
    cmd_import_beliefs,
    cmd_init,
    cmd_summary,
)


def _run_step(name, func, args, errors):
    """Run a pipeline step, catching failures without stopping."""
    print(f"\n=== {name} ===\n", file=sys.stderr)
    try:
        func(args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"{name} exited with code {e.code}")
            print(f"WARN: {name} failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"{name}: {e}")
        print(f"WARN: {name} failed: {e}, continuing...", file=sys.stderr)


def cmd_analyze(args):
    """Full automated cross-domain analysis from scratch.

    Pipeline: init -> import -> derive -> contradictions -> summary
    """
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    errors = []
    started = datetime.now().isoformat(timespec="seconds")
    db_path = getattr(args, "output", REASONS_DB)

    _run_step("Step 1: Init", cmd_init, args, errors)

    import_args = SimpleNamespace(**vars(args))
    import_args.expert = None
    import_args.only_in = False
    _run_step("Step 2: Import beliefs", cmd_import_beliefs, import_args, errors)

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    derive_args = SimpleNamespace(**vars(args))
    derive_args.exhaust = True
    derive_args.auto = True
    derive_args.dry_run = False
    derive_args.budget = getattr(args, "budget", 300)
    derive_args.seed = getattr(args, "seed", None)
    _run_step("Step 3: Derive", cmd_derive, derive_args, errors)

    contra_args = SimpleNamespace(**vars(args))
    contra_args.auto = True
    _run_step("Step 4: Contradictions", cmd_contradictions, contra_args, errors)

    _run_step("Step 5: Summary", cmd_summary, args, errors)

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        post_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        post_run_ids = pre_run_ids

    meta_dir = _get_meta_dir()
    checkpoint = {
        "started": started,
        "finished": datetime.now().isoformat(timespec="seconds"),
        "beliefs_before": len(pre_run_ids),
        "beliefs_after": len(post_run_ids),
        "beliefs_added": len(post_run_ids - pre_run_ids),
        "errors": errors,
    }
    os.makedirs(meta_dir, exist_ok=True)
    checkpoint_path = os.path.join(meta_dir, "last-analyze.json")
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Analyze checkpoint saved to {checkpoint_path}", file=sys.stderr)

    print("\n=== Analysis complete ===\n", file=sys.stderr)
    print(f"Beliefs: {len(pre_run_ids)} → {len(post_run_ids)} "
          f"(+{len(post_run_ids - pre_run_ids)})", file=sys.stderr)
    if errors:
        print(f"Completed with {len(errors)} warning(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print("All steps completed successfully.", file=sys.stderr)
