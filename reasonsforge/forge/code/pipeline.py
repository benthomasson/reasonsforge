"""Code forge pipeline — full automated analysis from scratch or incremental update."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import PROJECT_DIR
from .commands import (
    REASONS_DB,
    _get_project_dir,
    _get_repo,
    _load_config,
    cmd_accept_beliefs,
    cmd_derive,
    cmd_explore,
    cmd_init,
    cmd_propose_beliefs,
    cmd_review_proposals,
    cmd_scan,
    cmd_status,
    cmd_walk_commits,
)
from .topics import pending_count


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


def cmd_update(args):
    """Incremental update: walk-commits, propose, review, accept, derive, summarize."""
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    project_dir = _get_project_dir(args)
    db_path = getattr(args, "output", REASONS_DB)
    errors = []
    started = datetime.now().isoformat(timespec="seconds")

    since = getattr(args, "since", None)
    effective_since = since

    if not effective_since and getattr(args, "since_last", False):
        checkpoint_path = os.path.join(project_dir, "last-update.json")
        if os.path.isfile(checkpoint_path):
            try:
                with open(checkpoint_path) as f:
                    prev = json.load(f)
                effective_since = prev.get("since") or prev.get("started", "")[:10]
            except (json.JSONDecodeError, ValueError):
                pass

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    # Step 1: walk-commits
    _run_step("Step 1: Walk commits", cmd_walk_commits, args, errors)

    # Step 2: propose-beliefs
    propose_args = SimpleNamespace(**vars(args))
    propose_args.since = effective_since
    propose_args.auto = False
    _run_step("Step 2: Propose beliefs", cmd_propose_beliefs, propose_args, errors)

    # Step 3: review-proposals
    _run_step("Step 3: Review proposals", cmd_review_proposals, args, errors)

    # Step 4: accept-beliefs
    _run_step("Step 4: Accept beliefs", cmd_accept_beliefs, args, errors)

    # Step 5: derive (exhaust)
    derive_args = SimpleNamespace(**vars(args))
    derive_args.exhaust = True
    derive_args.auto = True
    _run_step("Step 5: Derive (exhaust)", cmd_derive, derive_args, errors)

    # Save update checkpoint
    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        post_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        post_run_ids = pre_run_ids

    checkpoint = {
        "started": started,
        "finished": datetime.now().isoformat(timespec="seconds"),
        "since": effective_since,
        "beliefs_before": len(pre_run_ids),
        "beliefs_after": len(post_run_ids),
        "beliefs_added": len(post_run_ids - pre_run_ids),
        "errors": errors,
    }
    os.makedirs(project_dir, exist_ok=True)
    checkpoint_path = os.path.join(project_dir, "last-update.json")
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Update checkpoint saved to {checkpoint_path}", file=sys.stderr)

    print("\n=== Update complete ===\n", file=sys.stderr)
    if errors:
        print(f"Completed with {len(errors)} warning(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print("All steps completed successfully.", file=sys.stderr)


def cmd_analyze(args):
    """Full automated analysis of a codebase from scratch.

    Pipeline: init -> scan -> explore -> propose -> review -> accept -> derive
    """
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    errors = []
    started = datetime.now().isoformat(timespec="seconds")
    db_path = getattr(args, "output", REASONS_DB)
    explore_limit = getattr(args, "limit", 500)

    # Step 1: init
    _run_step("Step 1: Init", cmd_init, args, errors)

    # Step 2: scan
    _run_step("Step 2: Scan", cmd_scan, args, errors)

    # Step 3: explore
    project_dir = _get_project_dir(args)
    if explore_limit <= 0:
        explore_limit = pending_count(project_dir) or 500
    explore_args = SimpleNamespace(**vars(args))
    explore_args.loop = explore_limit
    print(f"\n=== Step 3: Explore (up to {explore_limit} topics) ===\n", file=sys.stderr)
    try:
        cmd_explore(explore_args)
    except SystemExit as e:
        if e.code and e.code != 0:
            errors.append(f"explore exited with code {e.code}")
            print(f"WARN: explore failed (exit {e.code}), continuing...", file=sys.stderr)
    except Exception as e:
        errors.append(f"explore: {e}")
        print(f"WARN: explore failed: {e}, continuing...", file=sys.stderr)

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    # Step 4: propose-beliefs
    propose_args = SimpleNamespace(**vars(args))
    propose_args.auto = False
    _run_step("Step 4: Propose beliefs", cmd_propose_beliefs, propose_args, errors)

    # Step 5: review-proposals
    _run_step("Step 5: Review proposals", cmd_review_proposals, args, errors)

    # Step 6: accept-beliefs
    _run_step("Step 6: Accept beliefs", cmd_accept_beliefs, args, errors)

    # Step 7: derive (exhaust)
    derive_args = SimpleNamespace(**vars(args))
    derive_args.exhaust = True
    derive_args.auto = True
    _run_step("Step 7: Derive (exhaust)", cmd_derive, derive_args, errors)

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        post_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        post_run_ids = pre_run_ids

    checkpoint = {
        "started": started,
        "finished": datetime.now().isoformat(timespec="seconds"),
        "explore_limit": explore_limit,
        "beliefs_before": len(pre_run_ids),
        "beliefs_after": len(post_run_ids),
        "beliefs_added": len(post_run_ids - pre_run_ids),
        "errors": errors,
    }
    os.makedirs(project_dir, exist_ok=True)
    checkpoint_path = os.path.join(project_dir, "last-analyze.json")
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Analyze checkpoint saved to {checkpoint_path}", file=sys.stderr)

    print("\n=== Analysis complete ===\n", file=sys.stderr)
    print(f"Beliefs: {len(pre_run_ids)} → {len(post_run_ids)} "
          f"(+{len(post_run_ids - pre_run_ids)})", file=sys.stderr)
    remaining = pending_count(project_dir)
    if remaining:
        print(f"Topics remaining: {remaining} (use explore --loop to continue)", file=sys.stderr)
    if errors:
        print(f"Completed with {len(errors)} warning(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print("All steps completed successfully.", file=sys.stderr)
