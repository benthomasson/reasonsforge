"""Product forge pipeline — full automated analysis from scratch."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from types import SimpleNamespace

from . import PRODUCT_DIR
from .commands import (
    REASONS_DB,
    _get_product_dir,
    _load_config,
    cmd_accept_beliefs,
    cmd_derive,
    cmd_explore,
    cmd_init,
    cmd_propose_beliefs,
    cmd_review_proposals,
    cmd_scan,
    cmd_status,
)
from .topics import pending_count


def _write_step_cost_report(args, pipeline: str, step_key: str):
    """Capture per-step cost, write a report, and reset the tracker."""
    try:
        from ..llm import get_cost_summary, reset_cost_tracker
        from ...cost_report import write_cost_report

        cost_summary = get_cost_summary()
        if cost_summary["calls"] == 0:
            return
        write_cost_report(
            costs_dir=getattr(args, "costs_dir", "costs/"),
            operation=f"{pipeline}-{step_key}",
            cost_summary=cost_summary,
            domain=getattr(args, "domain", None),
            model=getattr(args, "model", None),
        )
        reset_cost_tracker()
    except Exception:
        pass


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
    """Full automated analysis of a product from scratch.

    Pipeline: init -> scan -> explore -> propose -> review -> accept -> derive -> summary
    """
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    errors = []
    started = datetime.now().isoformat(timespec="seconds")
    db_path = getattr(args, "output", REASONS_DB)
    explore_limit = getattr(args, "limit", 500)

    _run_step("Step 1: Init", cmd_init, args, errors)
    _write_step_cost_report(args, "product-analyze", "init")

    _run_step("Step 2: Scan", cmd_scan, args, errors)
    _write_step_cost_report(args, "product-analyze", "scan")

    product_dir = _get_product_dir()
    if explore_limit <= 0:
        explore_limit = pending_count(product_dir) or 500
    explore_args = SimpleNamespace(**vars(args))
    explore_args.loop = explore_limit
    explore_args.skip = None
    explore_args.pick = None
    explore_args.parallel = getattr(args, "parallel", 1)
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
    _write_step_cost_report(args, "product-analyze", "explore")

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    propose_args = SimpleNamespace(**vars(args))
    propose_args.auto = False
    propose_args.batch_size = getattr(args, "batch_size", 5)
    propose_args.proposals_output = "proposed-beliefs.md"
    propose_args.all = True
    _run_step("Step 4: Propose beliefs", cmd_propose_beliefs, propose_args, errors)
    _write_step_cost_report(args, "product-analyze", "propose")

    _run_step("Step 5: Review proposals", cmd_review_proposals, args, errors)
    _write_step_cost_report(args, "product-analyze", "review-proposals")

    _run_step("Step 6: Accept beliefs", cmd_accept_beliefs, args, errors)
    _write_step_cost_report(args, "product-analyze", "accept")

    derive_args = SimpleNamespace(**vars(args))
    derive_args.exhaust = True
    derive_args.auto = True
    derive_args.max_derive_rounds = getattr(args, "max_derive_rounds", 10)
    derive_args.budget = getattr(args, "budget", 300)
    derive_args.domain = getattr(args, "domain", None)
    _run_step("Step 7: Derive (exhaust)", cmd_derive, derive_args, errors)
    _write_step_cost_report(args, "product-analyze", "derive")

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
    os.makedirs(product_dir, exist_ok=True)
    checkpoint_path = os.path.join(product_dir, "last-analyze.json")
    with open(checkpoint_path, "w") as f:
        json.dump(checkpoint, f, indent=2)
    print(f"Analyze checkpoint saved to {checkpoint_path}", file=sys.stderr)

    print("\n=== Analysis complete ===\n", file=sys.stderr)
    print(f"Beliefs: {len(pre_run_ids)} → {len(post_run_ids)} "
          f"(+{len(post_run_ids - pre_run_ids)})", file=sys.stderr)
    remaining = pending_count(product_dir)
    if remaining:
        print(f"Topics remaining: {remaining} (use explore --loop to continue)", file=sys.stderr)
    if errors:
        print(f"Completed with {len(errors)} warning(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print("All steps completed successfully.", file=sys.stderr)
