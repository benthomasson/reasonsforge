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


def _load_step_checkpoint(project_dir: str, pipeline: str) -> dict:
    """Load step-level checkpoint for resume support."""
    path = os.path.join(project_dir, f"progress-{pipeline}.json")
    if os.path.isfile(path):
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return {}


def _save_step_checkpoint(project_dir: str, pipeline: str, step: str,
                          started: str, errors: list[str]):
    """Save step-level progress so pipeline can resume after crashes."""
    path = os.path.join(project_dir, f"progress-{pipeline}.json")
    os.makedirs(project_dir, exist_ok=True)
    checkpoint = _load_step_checkpoint(project_dir, pipeline)
    completed = checkpoint.get("completed_steps", [])
    if step not in completed:
        completed.append(step)
    checkpoint.update({
        "started": checkpoint.get("started", started),
        "last_step": step,
        "last_step_at": datetime.now().isoformat(timespec="seconds"),
        "completed_steps": completed,
        "errors": errors,
    })
    with open(path, "w") as f:
        json.dump(checkpoint, f, indent=2)


def _clear_step_checkpoint(project_dir: str, pipeline: str):
    """Remove step checkpoint after successful completion."""
    path = os.path.join(project_dir, f"progress-{pipeline}.json")
    if os.path.isfile(path):
        os.remove(path)


def cmd_update(args):
    """Incremental update: walk-commits, propose, review, accept, derive, summarize."""
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    project_dir = _get_project_dir(args)
    db_path = getattr(args, "output", REASONS_DB)
    errors = []
    started = datetime.now().isoformat(timespec="seconds")
    resume = getattr(args, "resume", False)

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

    progress = _load_step_checkpoint(project_dir, "update") if resume else {}
    completed = set(progress.get("completed_steps", []))
    if resume and completed:
        print(f"Resuming update — skipping completed steps: {', '.join(sorted(completed))}", file=sys.stderr)

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        pre_run_ids = set(network.get("nodes", {}).keys())
    except Exception:
        pre_run_ids = set()

    steps = [
        ("walk-commits", "Step 1: Walk commits",
         lambda: _run_step("Step 1: Walk commits", cmd_walk_commits, args, errors)),
        ("propose-beliefs", "Step 2: Propose beliefs",
         lambda: _run_step("Step 2: Propose beliefs", cmd_propose_beliefs,
                           SimpleNamespace(**vars(args), since=effective_since, auto=False), errors)),
        ("review-proposals", "Step 3: Review proposals",
         lambda: _run_step("Step 3: Review proposals", cmd_review_proposals, args, errors)),
        ("accept-beliefs", "Step 4: Accept beliefs",
         lambda: _run_step("Step 4: Accept beliefs", cmd_accept_beliefs, args, errors)),
        ("derive", "Step 5: Derive (exhaust)",
         lambda: _run_step("Step 5: Derive (exhaust)", cmd_derive,
                           SimpleNamespace(**vars(args), exhaust=True, auto=True), errors)),
    ]

    for step_key, step_name, step_fn in steps:
        if step_key in completed:
            print(f"  Skipping {step_name} (already completed)", file=sys.stderr)
            continue
        step_fn()
        _save_step_checkpoint(project_dir, "update", step_key, started, errors)

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

    _clear_step_checkpoint(project_dir, "update")

    print("\n=== Update complete ===\n", file=sys.stderr)
    if errors:
        print(f"Completed with {len(errors)} warning(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
    else:
        print("All steps completed successfully.", file=sys.stderr)


def cmd_analyze(args):
    """Full automated analysis of a codebase from scratch.

    Pipeline: init -> scan -> (explore -> propose -> review -> accept -> derive) x rounds

    Round 1 runs all steps. Rounds 2+ loop explore->propose->review->accept->derive
    to drain more of the topic queue. With --resume, skips steps that completed
    in a prior interrupted run.
    """
    from ..caffeinate import hold as _caffeinate
    _caffeinate()

    errors = []
    started = datetime.now().isoformat(timespec="seconds")
    db_path = getattr(args, "output", REASONS_DB)
    explore_limit = getattr(args, "limit", 500)
    rounds = getattr(args, "rounds", 1)
    resume = getattr(args, "resume", False)
    project_dir = _get_project_dir(args)

    progress = _load_step_checkpoint(project_dir, "analyze") if resume else {}
    completed = set(progress.get("completed_steps", []))
    if resume and completed:
        print(f"Resuming analysis — skipping completed steps: {', '.join(sorted(completed))}", file=sys.stderr)

    def _run_explore(label: str):
        eff_limit = explore_limit
        if eff_limit <= 0:
            eff_limit = pending_count(project_dir) or 500
        explore_args = SimpleNamespace(**vars(args))
        explore_args.loop = eff_limit
        print(f"\n=== {label}: Explore (up to {eff_limit} topics) ===\n", file=sys.stderr)
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

    # Round 1: init + scan + explore + propose + review + accept + derive
    round1_steps = [
        ("init", "Step 1: Init",
         lambda: _run_step("Step 1: Init", cmd_init, args, errors)),
        ("scan", "Step 2: Scan",
         lambda: _run_step("Step 2: Scan", cmd_scan, args, errors)),
        ("r1-explore", "Step 3: Explore",
         lambda: _run_explore("Step 3")),
        ("r1-propose", "Step 4: Propose beliefs",
         lambda: _run_step("Step 4: Propose beliefs", cmd_propose_beliefs,
                           SimpleNamespace(**vars(args), auto=False), errors)),
        ("r1-review", "Step 5: Review proposals",
         lambda: _run_step("Step 5: Review proposals", cmd_review_proposals, args, errors)),
        ("r1-accept", "Step 6: Accept beliefs",
         lambda: _run_step("Step 6: Accept beliefs", cmd_accept_beliefs, args, errors)),
        ("r1-derive", "Step 7: Derive (exhaust)",
         lambda: _run_step("Step 7: Derive (exhaust)", cmd_derive,
                           SimpleNamespace(**vars(args), exhaust=True, auto=True), errors)),
    ]

    for step_key, step_name, step_fn in round1_steps:
        if step_key in completed:
            print(f"  Skipping {step_name} (already completed)", file=sys.stderr)
            continue
        step_fn()
        _save_step_checkpoint(project_dir, "analyze", step_key, started, errors)

    # Rounds 2+: explore -> propose -> review -> accept -> derive
    for round_num in range(2, rounds + 1):
        remaining_topics = pending_count(project_dir)
        if not remaining_topics:
            print(f"\n--- Round {round_num}/{rounds}: no topics remaining, stopping early ---",
                  file=sys.stderr)
            break

        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"  Round {round_num}/{rounds} ({remaining_topics} topics remaining)", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)

        prefix = f"r{round_num}"
        round_steps = [
            (f"{prefix}-explore", f"Round {round_num} Explore",
             lambda rn=round_num: _run_explore(f"Round {rn}")),
            (f"{prefix}-propose", f"Round {round_num} Propose beliefs",
             lambda rn=round_num: _run_step(f"Round {rn}: Propose beliefs", cmd_propose_beliefs,
                                            SimpleNamespace(**vars(args), auto=False), errors)),
            (f"{prefix}-review", f"Round {round_num} Review proposals",
             lambda rn=round_num: _run_step(f"Round {rn}: Review proposals",
                                            cmd_review_proposals, args, errors)),
            (f"{prefix}-accept", f"Round {round_num} Accept beliefs",
             lambda rn=round_num: _run_step(f"Round {rn}: Accept beliefs",
                                            cmd_accept_beliefs, args, errors)),
            (f"{prefix}-derive", f"Round {round_num} Derive (exhaust)",
             lambda rn=round_num: _run_step(f"Round {rn}: Derive (exhaust)", cmd_derive,
                                            SimpleNamespace(**vars(args), exhaust=True, auto=True),
                                            errors)),
        ]

        for step_key, step_name, step_fn in round_steps:
            if step_key in completed:
                print(f"  Skipping {step_name} (already completed)", file=sys.stderr)
                continue
            step_fn()
            _save_step_checkpoint(project_dir, "analyze", step_key, started, errors)

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
        "rounds": rounds,
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

    _clear_step_checkpoint(project_dir, "analyze")

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
