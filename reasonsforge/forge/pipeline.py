"""End-to-end EEM construction pipeline."""

import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .llm import check_model_available, invoke_sync
from . import PROJECT_DIR, REASONS_DB

STATE_FILE = Path(PROJECT_DIR) / "pipeline-state.json"

STAGE_NAMES = {
    1: "ingest",
    2: "summarize",
    3: "extract",
    4: "derive",
    5: "review",
    6: "repair",
    7: "deduplicate",
    8: "export",
    9: "index",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt_duration(seconds):
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds) // 60
    secs = seconds - minutes * 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h {minutes}m {secs:.0f}s"


def _init_state(args):
    state = {
        "started_at": _now(),
        "updated_at": _now(),
        "status": "running",
        "current_stage": None,
        "current_cycle": None,
        "args": {
            "model": args.model,
            "rounds": args.rounds,
            "domain": getattr(args, "domain", None),
        },
        "stages": {
            f"{n}_{name}": {"status": "pending"}
            for n, name in STAGE_NAMES.items()
        },
    }
    _save_state(state)
    return state


def _load_state():
    if not STATE_FILE.exists():
        return None
    try:
        return json.loads(STATE_FILE.read_text())
    except (json.JSONDecodeError, ValueError):
        print(f"WARNING: corrupt state file {STATE_FILE}, ignoring",
              file=sys.stderr)
        return None


def _save_state(state):
    state["updated_at"] = _now()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n")


def _mark_stage(state, stage_num, status, **meta):
    key = f"{stage_num}_{STAGE_NAMES[stage_num]}"
    state["stages"][key]["status"] = status
    if status == "running":
        state["current_stage"] = stage_num
    elif status == "completed":
        state["stages"][key]["completed_at"] = _now()
    state["stages"][key].update(meta)
    _save_state(state)


def _stage_completed(state, stage_num):
    key = f"{stage_num}_{STAGE_NAMES[stage_num]}"
    return state["stages"][key]["status"] == "completed"


def _banner(stage_num, total, name):
    print(f"\n{'=' * 50}", file=sys.stderr)
    print(f"  Stage {stage_num}/{total}: {name}", file=sys.stderr)
    print(f"{'=' * 50}\n", file=sys.stderr)


def _stage_ingest(args):
    """Stage 1: Chunk PDFs into sources/."""
    if args.pdf:
        from .chunk_pdf import cmd_chunk_pdf
        for pdf_path in args.pdf:
            try:
                chunk_args = SimpleNamespace(
                    pdf=pdf_path,
                    prefix=None,
                    source_label=None,
                    dry_run=False,
                )
                cmd_chunk_pdf(chunk_args)
            except KeyboardInterrupt:
                print(f"\n  Ending ingest early — partial results saved",
                      file=sys.stderr)
                break


def _stage_summarize(args):
    """Stage 2: Generate entries from source documents."""
    from .summarize import cmd_summarize
    sum_args = SimpleNamespace(
        input_dir=args.sources_dir,
        limit=None,
        model=args.model,
        recursive=getattr(args, "recursive", False),
        parallel=getattr(args, "parallel", 1),
    )
    cmd_summarize(sum_args)


def _stage_extract(args):
    """Stage 3: Extract beliefs from summaries and optionally auto-accept."""
    from .propose import cmd_propose_beliefs, cmd_accept_beliefs

    prop_args = SimpleNamespace(
        input_dir="summaries",
        output="proposed-beliefs.md",
        model=args.model,
        parallel=getattr(args, "parallel", 1),
        batch_size=5,
        entry=None,
        mode=getattr(args, "mode", None),
        num_ctx=getattr(args, "num_ctx", None),
    )
    setattr(prop_args, "all", False)

    cmd_propose_beliefs(prop_args)

    if args.no_auto_accept:
        print("\nStopping after propose-beliefs (--no-auto-accept)", file=sys.stderr)
        print("Review proposed-beliefs.md, mark entries as [ACCEPT], then run:", file=sys.stderr)
        print("  reasonsforge forge accept-beliefs", file=sys.stderr)
        return False

    proposals_path = Path("proposed-beliefs.md")
    if proposals_path.exists():
        from .propose import auto_accept_proposals
        auto_accept_proposals(str(proposals_path))
        print("Auto-accepted all proposed beliefs", file=sys.stderr)

        accept_args = SimpleNamespace(file="proposed-beliefs.md")
        cmd_accept_beliefs(accept_args)

    return True


def _stage_derive(args, round_label=""):
    """Stage 4: Derive new beliefs until saturated or max rounds hit.

    Returns (total_added, derived_ids) — count and list of IDs added this stage.
    """
    from reasonsforge.api import export_network
    from reasonsforge.derive import build_prompt, parse_proposals, validate_proposals, apply_proposals

    total_added = 0
    all_derived_ids = []
    prefix = f"[{round_label}] " if round_label else ""
    search_rounds = getattr(args, "search_rounds", 0)

    try:
        for derive_round in range(1, args.max_derive_rounds + 1):
            print(f"{prefix}Derive round {derive_round}/{args.max_derive_rounds}...",
                  file=sys.stderr)

            data = export_network(db_path=REASONS_DB)
            nodes = data.get("nodes", {})
            if not nodes:
                print(f"{prefix}No nodes in network", file=sys.stderr)
                break

            ns = getattr(args, "namespace", None)
            if ns is not None:
                if ns == "":
                    nodes = {k: v for k, v in nodes.items() if ":" not in k}
                else:
                    nodes = {k: v for k, v in nodes.items()
                             if k.startswith(f"{ns}:")}

            prompt, stats = build_prompt(nodes, domain=args.domain)
            print(f"{prefix}  Network: {stats['total_in']} IN, "
                  f"{stats['total_derived']} derived, depth {stats['max_depth']}",
                  file=sys.stderr)

            if search_rounds > 0:
                from reasonsforge.derive_tools import derive_with_tools
                try:
                    proposals = derive_with_tools(
                        prompt, model=args.model, db_path=REASONS_DB,
                        max_rounds=search_rounds, timeout=args.timeout,
                    )
                except Exception as e:
                    print(f"{prefix}  Derive error: {e}", file=sys.stderr)
                    break
            else:
                try:
                    response = invoke_sync(prompt, model=args.model, timeout=args.timeout)
                except Exception as e:
                    print(f"{prefix}  Derive error: {e}", file=sys.stderr)
                    break
                proposals = parse_proposals(response)

            if not proposals:
                print(f"{prefix}  Saturated (no proposals)", file=sys.stderr)
                break

            valid, skipped = validate_proposals(proposals, nodes)
            for p, reason in skipped:
                print(f"{prefix}  SKIP {p['id']}: {reason}", file=sys.stderr)

            if not valid:
                print(f"{prefix}  Saturated (no valid proposals)", file=sys.stderr)
                break

            results = apply_proposals(valid, db_path=REASONS_DB, model=args.model)
            added_ids = [p["id"] for p, r in results if isinstance(r, dict)]
            total_added += len(added_ids)
            all_derived_ids.extend(added_ids)
            print(f"{prefix}  Added {len(added_ids)} beliefs", file=sys.stderr)
    except KeyboardInterrupt:
        print(f"\n{prefix}  Ending derive early — {total_added} beliefs saved",
              file=sys.stderr)

    return total_added, all_derived_ids


def _stage_review(args, round_label="", belief_ids=None):
    """Stage 5: Review derived beliefs for validity.

    Args:
        belief_ids: If provided, only review these specific beliefs (from this round's derive).

    Returns the review results dict.
    """
    from reasonsforge.api import review_beliefs

    prefix = f"[{round_label}] " if round_label else ""
    if belief_ids:
        print(f"{prefix}Reviewing {len(belief_ids)} beliefs from this round...",
              file=sys.stderr)
    else:
        print(f"{prefix}Reviewing beliefs...", file=sys.stderr)

    ns = getattr(args, "namespace", None)
    try:
        result = review_beliefs(
            model=args.model,
            timeout=args.timeout,
            namespace=ns,
            belief_ids=belief_ids,
            db_path=REASONS_DB,
        )
    except KeyboardInterrupt:
        print(f"\n{prefix}  Ending review early", file=sys.stderr)
        return {"reviewed": 0, "invalid": 0, "results": []}

    reviewed = result.get("reviewed", 0)
    invalid = result.get("invalid", 0)
    print(f"{prefix}  Reviewed {reviewed}, invalid {invalid}", file=sys.stderr)

    return result


def _stage_repair(args, review_result, round_label=""):
    """Stage 6: Research and repair invalid beliefs.

    Returns the research results dict.
    """
    from reasonsforge.api import research

    prefix = f"[{round_label}] " if round_label else ""

    invalid_ids = [
        r.get("belief_id") or r.get("id")
        for r in review_result.get("results", [])
        if not r.get("valid", True)
    ]
    invalid_ids = [i for i in invalid_ids if i]

    if not invalid_ids:
        print(f"{prefix}No invalid beliefs to repair", file=sys.stderr)
        return {"total_invalid": 0}

    print(f"{prefix}Researching {len(invalid_ids)} invalid beliefs...", file=sys.stderr)

    try:
        result = research(
            belief_ids=invalid_ids,
            model=args.model,
            timeout=args.timeout,
            db_path=REASONS_DB,
        )
    except KeyboardInterrupt:
        print(f"\n{prefix}  Ending repair early", file=sys.stderr)
        return {"total_invalid": len(invalid_ids), "linked": 0,
                "softened": 0, "abandoned": 0}

    print(f"{prefix}  Linked: {result.get('linked', 0)}, "
          f"Softened: {result.get('softened', 0)}, "
          f"Abandoned: {result.get('abandoned', 0)}", file=sys.stderr)

    return result


def _stage_deduplicate(args, round_label=""):
    """Stage 7: Remove duplicate beliefs with optional LLM verification."""
    from reasonsforge.api import deduplicate, verify_dedup_clusters, apply_dedup_plan, add_nogood

    verify = getattr(args, "verify_dedup", True)
    model = getattr(args, "model", None) or "claude"
    prefix = f"[{round_label}] " if round_label else ""
    print(f"{prefix}Deduplicating{' (with LLM verify)' if verify else ''}...",
          file=sys.stderr)

    try:
        result = deduplicate(auto=not verify, db_path=REASONS_DB)
    except KeyboardInterrupt:
        print(f"\n{prefix}  Ending dedup early", file=sys.stderr)
        return {}
    clusters = result.get("clusters", [])

    if not clusters:
        print(f"{prefix}  No duplicates found", file=sys.stderr)
        return result

    if not verify:
        retracted = result.get("retracted", [])
        if retracted:
            print(f"{prefix}  {len(clusters)} clusters, retracted {len(retracted)}",
                  file=sys.stderr)
        return result

    print(f"{prefix}  {len(clusters)} candidate cluster(s), verifying...",
          file=sys.stderr)
    try:
        vresult = verify_dedup_clusters(clusters, model=model)
    except KeyboardInterrupt:
        print(f"\n{prefix}  Ending dedup verification early", file=sys.stderr)
        return result

    total_retracted = []
    if vresult["verified"]:
        plan = [{"keep": c["kept"],
                 "retract": [b["id"] for b in c["beliefs"] if b["id"] != c["kept"]]}
                for c in vresult["verified"]]
        apply_result = apply_dedup_plan(plan, db_path=REASONS_DB)
        total_retracted = apply_result.get("retracted", [])
        print(f"{prefix}  Verified {len(vresult['verified'])} cluster(s), "
              f"retracted {len(total_retracted)}", file=sys.stderr)

    if vresult["rejected"]:
        print(f"{prefix}  Rejected {len(vresult['rejected'])} false duplicate(s)",
              file=sys.stderr)

    if vresult["contradictions"]:
        print(f"{prefix}  Found {len(vresult['contradictions'])} contradiction(s)",
              file=sys.stderr)
        for cluster in vresult["contradictions"]:
            node_ids = [b["id"] for b in cluster["beliefs"]]
            try:
                add_nogood(node_ids, db_path=REASONS_DB)
            except Exception:
                pass

    result["retracted"] = total_retracted
    result["verified"] = len(vresult["verified"])
    result["rejected"] = len(vresult["rejected"])
    result["contradictions"] = len(vresult["contradictions"])
    return result


def _stage_rederive_superseded(args, round_label=""):
    """Re-derive beliefs that went OUT because an antecedent was superseded."""
    from reasonsforge.api import add_node, add_justification, propagate
    from reasonsforge.storage import Storage
    from .llm import extract_json

    prefix = f"[{round_label}] " if round_label else ""

    store = Storage(REASONS_DB)
    net = store.load()
    store.close()

    nodes = net.nodes

    def _get_meta(node):
        meta = node.metadata if hasattr(node, "metadata") else {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except (json.JSONDecodeError, ValueError):
                meta = {}
        return meta

    superseded_map = {}
    for nid, node in nodes.items():
        sup = _get_meta(node).get("superseded_by")
        if sup:
            superseded_map[nid] = sup

    if not superseded_map:
        print(f"{prefix}No superseded beliefs found", file=sys.stderr)
        return {"rederived": 0}

    node_map = nodes

    casualties = []
    seen = set()
    for nid, node in nodes.items():
        if node.truth_value != "OUT":
            continue
        node_meta = _get_meta(node)
        if node_meta.get("superseded_by"):
            continue
        if node_meta.get("_retracted"):
            continue

        for j in node.justifications:
            for ant_id in j.antecedents:
                if ant_id in superseded_map:
                    new_id = superseded_map[ant_id]
                    new_node = node_map.get(new_id)
                    if not new_node or new_node.truth_value != "IN":
                        continue
                    old_node = node_map.get(ant_id)
                    if not old_node:
                        continue

                    key = (nid, ant_id)
                    if key in seen:
                        continue
                    seen.add(key)

                    other_ants = [
                        (a, node_map[a].text)
                        for a in j.antecedents
                        if a != ant_id and a in node_map
                    ]
                    casualties.append({
                        "node_id": nid,
                        "node_text": node.text,
                        "old_ant_id": ant_id,
                        "old_ant_text": old_node.text,
                        "new_ant_id": new_id,
                        "new_ant_text": new_node.text,
                        "other_antecedents": other_ants,
                    })

    if not casualties:
        print(f"{prefix}No supersession casualties found", file=sys.stderr)
        return {"rederived": 0}

    print(f"{prefix}Found {len(casualties)} supersession casualties to evaluate",
          file=sys.stderr)

    total_rederived = 0
    batch_size = 10
    round_num = round_label.split("/")[0].replace("cycle ", "") if round_label else "1"

    for i in range(0, len(casualties), batch_size):
        batch = casualties[i:i + batch_size]

        batch_text = "\n\n".join(
            "{num}. Dependent: **{nid}**: {ntext}\n"
            "  Old antecedent: **{oid}**: {otext}\n"
            "  New antecedent (replacement): **{newid}**: {newtext}\n"
            "  Other antecedents:\n{others}".format(
                num=k + 1,
                nid=c["node_id"], ntext=c["node_text"],
                oid=c["old_ant_id"], otext=c["old_ant_text"],
                newid=c["new_ant_id"], newtext=c["new_ant_text"],
                others=(
                    "\n".join(f"    - {a}: {t}" for a, t in c["other_antecedents"])
                    if c["other_antecedents"] else "    (none)"
                ),
            )
            for k, c in enumerate(batch)
        )

        prompt = (
            "A belief was superseded (replaced by a refined version). Its dependents went OUT "
            "because their antecedent is gone. For each dependent below, decide whether the "
            "conclusion still follows from the NEW antecedent.\n\n"
            f"{batch_text}\n\n---\n\n"
            "For each dependent, respond with ONE of:\n"
            "- **rejustify**: The conclusion still follows from the new antecedent as-is. "
            "We add a new justification referencing the new antecedent.\n"
            "- **revise**: The conclusion needs rewording to follow from the new antecedent. "
            "Provide updated text and a new ID.\n"
            "- **drop**: The conclusion no longer follows from the new antecedent.\n\n"
            'Respond with ONLY this JSON array:\n'
            '[{"id": "<dependent-id>", "action": "rejustify|revise|drop", '
            '"new_id": "<kebab-case-id if revise>", '
            '"new_text": "<revised claim if revise>", '
            '"reason": "<brief explanation>"}]'
        )

        try:
            response = invoke_sync(prompt, model=args.model, timeout=args.timeout)
        except KeyboardInterrupt:
            print(f"\n{prefix}  Ending re-derive early", file=sys.stderr)
            break
        except Exception as e:
            print(f"{prefix}  LLM error: {e}", file=sys.stderr)
            continue

        decisions = extract_json(response)
        if not isinstance(decisions, list):
            print(f"{prefix}  Failed to parse re-derive response", file=sys.stderr)
            continue

        for decision in decisions:
            dep_id = decision.get("id", "")
            action = decision.get("action", "drop")
            reason = decision.get("reason", "")

            casualty = next((c for c in batch if c["node_id"] == dep_id), None)
            if not casualty:
                continue

            if action == "rejustify":
                new_ants = [casualty["new_ant_id"]]
                for ant_id, _ in casualty["other_antecedents"]:
                    n = node_map.get(ant_id)
                    if n and n.truth_value == "IN":
                        new_ants.append(ant_id)

                if new_ants:
                    add_justification(
                        node_id=dep_id,
                        sl=",".join(new_ants),
                        label=f"re-derive-round-{round_num}",
                        db_path=REASONS_DB,
                    )
                    print(f"{prefix}  rejustified {dep_id} ({reason})",
                          file=sys.stderr)
                    total_rederived += 1

            elif action == "revise":
                new_id = decision.get("new_id", "")
                new_text = decision.get("new_text", "")
                if not new_id or not new_text or new_id == dep_id:
                    continue

                new_ants = [casualty["new_ant_id"]]
                for ant_id, _ in casualty["other_antecedents"]:
                    n = node_map.get(ant_id)
                    if n and n.truth_value == "IN":
                        new_ants.append(ant_id)

                add_node(
                    node_id=new_id,
                    text=new_text,
                    sl=",".join(new_ants),
                    unless=dep_id,
                    label=f"re-derive-round-{round_num}",
                    source=f"supersedes:{dep_id}",
                    model=args.model,
                    db_path=REASONS_DB,
                )

                store = Storage(REASONS_DB)
                updated_net = store.load()
                old_dep = updated_net.nodes.get(dep_id)
                if old_dep:
                    meta = _get_meta(old_dep)
                    meta["superseded_by"] = new_id
                    old_dep.metadata = meta
                    store.save(updated_net)
                store.close()

                print(f"{prefix}  revised {dep_id} -> {new_id} ({reason})",
                      file=sys.stderr)
                total_rederived += 1

            else:
                print(f"{prefix}  dropped {dep_id} ({reason})", file=sys.stderr)

    if total_rederived > 0:
        propagate(db_path=REASONS_DB)

    print(f"{prefix}Re-derived {total_rederived} casualties", file=sys.stderr)
    return {"rederived": total_rederived}


def _stage_export(args):
    """Export network and README card."""
    from reasonsforge.api import export_network, export_card

    data = export_network(db_path=REASONS_DB)

    network_path = Path("network.json")
    network_path.write_text(json.dumps(data, indent=2, sort_keys=True))
    print(f"Exported {network_path}", file=sys.stderr)

    card = export_card(db_path=REASONS_DB, domain=args.domain)
    readme_path = Path("README.md")
    readme_path.write_text(card)
    print(f"Exported {readme_path}", file=sys.stderr)

    in_count = sum(1 for n in data.get("nodes", {}).values()
                   if n.get("truth_value") == "IN")
    total = len(data.get("nodes", {}))
    print(f"\nFinal: {in_count} IN / {total} total beliefs", file=sys.stderr)


def _stage_index(args):
    """Stage 9: Build FTS5 index from sources and summaries."""
    from .index_sources import cmd_index_sources

    sources_dir = Path(args.sources_dir)
    summaries_dir = Path("summaries")
    db_path = getattr(args, "index_db", "rag_fts.db")

    if sources_dir.exists():
        print(f"Indexing sources from {sources_dir}...", file=sys.stderr)
        idx_args = SimpleNamespace(
            input_dir=str(sources_dir),
            recursive=True,
            db=db_path,
            type="source",
            chunk_size=2000,
            rebuild=True,
        )
        cmd_index_sources(idx_args)

    if summaries_dir.exists():
        print(f"Indexing summaries from {summaries_dir}...", file=sys.stderr)
        idx_args = SimpleNamespace(
            input_dir=str(summaries_dir),
            recursive=True,
            db=db_path,
            type="summary",
            chunk_size=2000,
            rebuild=False,
        )
        cmd_index_sources(idx_args)


def _run_convergence_loop(args, rounds, start_cycle=1, total_rounds=None,
                          on_stage=None):
    """Run derive -> review -> repair -> dedup until convergence.

    Args:
        args: namespace with .model, .timeout, .domain, .max_derive_rounds
        rounds: number of cycles to run
        start_cycle: cycle number to start from (for labeling)
        total_rounds: total cycles for labels (defaults to start_cycle + rounds - 1)
        on_stage: optional callback(cycle, stage_num, event, **kwargs)
            event is "start" or "end", kwargs has stage-specific data

    Returns summary dict with totals across all cycles.
    """
    if total_rounds is None:
        total_rounds = start_cycle + rounds - 1

    summary = {
        "cycles": 0,
        "total_derived": 0,
        "total_reviewed": 0,
        "total_invalid": 0,
        "total_linked": 0,
        "total_softened": 0,
        "total_abandoned": 0,
        "converged": False,
    }

    for i in range(rounds):
        cycle = start_cycle + i
        label = f"cycle {cycle}/{total_rounds}"
        summary["cycles"] = i + 1

        if on_stage:
            on_stage(cycle, 4, "start")
        t0 = time.monotonic()
        added, derived_ids = _stage_derive(args, round_label=label)
        elapsed = time.monotonic() - t0
        summary["total_derived"] += added
        print(f"  DERIVE took {_fmt_duration(elapsed)}", file=sys.stderr)
        if on_stage:
            on_stage(cycle, 4, "end", added=added,
                     duration_seconds=round(elapsed, 2))

        if on_stage:
            on_stage(cycle, 5, "start")
        t0 = time.monotonic()
        review_result = _stage_review(args, round_label=label,
                                      belief_ids=derived_ids or None)
        elapsed = time.monotonic() - t0
        invalid_count = review_result.get("invalid", 0)
        summary["total_reviewed"] += review_result.get("reviewed", 0)
        summary["total_invalid"] += invalid_count
        print(f"  REVIEW took {_fmt_duration(elapsed)}", file=sys.stderr)
        if on_stage:
            on_stage(cycle, 5, "end", reviewed=review_result.get("reviewed", 0),
                     invalid=invalid_count,
                     duration_seconds=round(elapsed, 2))

        repair_result = None
        if invalid_count > 0:
            if on_stage:
                on_stage(cycle, 6, "start")
            t0 = time.monotonic()
            repair_result = _stage_repair(args, review_result, round_label=label)
            elapsed = time.monotonic() - t0
            summary["total_linked"] += repair_result.get("linked", 0)
            summary["total_softened"] += repair_result.get("softened", 0)
            summary["total_abandoned"] += repair_result.get("abandoned", 0)
            print(f"  REPAIR took {_fmt_duration(elapsed)}", file=sys.stderr)
            if on_stage:
                on_stage(cycle, 6, "end",
                         duration_seconds=round(elapsed, 2))
        elif on_stage:
            on_stage(cycle, 6, "end", skipped=True)

        if on_stage:
            on_stage(cycle, 7, "start")
        t0 = time.monotonic()
        _stage_deduplicate(args, round_label=label)
        elapsed = time.monotonic() - t0
        print(f"  DEDUPLICATE took {_fmt_duration(elapsed)}", file=sys.stderr)
        if on_stage:
            on_stage(cycle, 7, "end",
                     duration_seconds=round(elapsed, 2))

        t0 = time.monotonic()
        rederive_result = _stage_rederive_superseded(args, round_label=label)
        elapsed = time.monotonic() - t0
        rederived = rederive_result.get("rederived", 0)
        summary["total_rederived"] = summary.get("total_rederived", 0) + rederived
        if rederived > 0:
            print(f"  RE-DERIVE took {_fmt_duration(elapsed)}", file=sys.stderr)

        if invalid_count == 0 and added == 0 and rederived == 0:
            print(f"\nConverged after {cycle} cycles "
                  f"(0 invalids, 0 new derivations, 0 re-derived)", file=sys.stderr)
            summary["converged"] = True
            break

    return summary


def cmd_derive_review_repair(args):
    """Run derive/review/repair loop on existing belief network."""
    from .caffeinate import hold as _caffeinate
    _caffeinate()

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}", file=sys.stderr)
        sys.exit(1)

    if not Path(REASONS_DB).exists():
        print(f"Reasons database not found: {REASONS_DB}", file=sys.stderr)
        print("Run: reasonsforge forge pipeline or reasonsforge forge accept-beliefs first",
              file=sys.stderr)
        sys.exit(1)

    rounds = getattr(args, "rounds", 1)
    ns = getattr(args, "namespace", None)
    print(f"=== Derive-Review-Repair ===", file=sys.stderr)
    print(f"Model: {args.model}", file=sys.stderr)
    print(f"Max rounds: {rounds}", file=sys.stderr)
    print(f"Max derive rounds per cycle: {args.max_derive_rounds}",
          file=sys.stderr)
    if ns is not None:
        label = "non-namespaced" if ns == "" else f"{ns}:*"
        print(f"Namespace filter: {label}", file=sys.stderr)
    print(file=sys.stderr)

    t0 = time.monotonic()
    summary = _run_convergence_loop(args, rounds)
    total_elapsed = time.monotonic() - t0

    print(f"\n=== Summary ===", file=sys.stderr)
    print(f"Duration: {_fmt_duration(total_elapsed)}", file=sys.stderr)
    print(f"Cycles: {summary['cycles']}", file=sys.stderr)
    print(f"Derived: {summary['total_derived']}", file=sys.stderr)
    print(f"Reviewed: {summary['total_reviewed']}", file=sys.stderr)
    print(f"Invalid: {summary['total_invalid']}", file=sys.stderr)
    print(f"  Linked: {summary['total_linked']}", file=sys.stderr)
    print(f"  Softened: {summary['total_softened']}", file=sys.stderr)
    print(f"  Abandoned: {summary['total_abandoned']}", file=sys.stderr)
    print(f"Re-derived: {summary.get('total_rederived', 0)}", file=sys.stderr)
    print(f"Converged: {'yes' if summary['converged'] else 'no'}", file=sys.stderr)


def cmd_pipeline(args):
    """Run end-to-end EEM construction pipeline."""
    from .caffeinate import hold as _caffeinate
    _caffeinate()

    if not check_model_available(args.model):
        print(f"Model not available: {args.model}", file=sys.stderr)
        sys.exit(1)

    resume = getattr(args, "resume", False)
    if resume:
        state = _load_state()
        if not state:
            print("No pipeline state to resume. Run without --resume first.",
                  file=sys.stderr)
            sys.exit(1)
        if state["status"] == "completed":
            print("Pipeline already completed. Run without --resume to start fresh.",
                  file=sys.stderr)
            return
        print("Resuming pipeline from state file", file=sys.stderr)
        state["status"] = "running"
        _save_state(state)
    else:
        state = _init_state(args)

    total_stages = 9
    has_sources = args.pdf
    pipeline_t0 = time.monotonic()

    try:
        # Stage 1: Ingest
        if not _stage_completed(state, 1):
            if has_sources:
                _banner(1, total_stages, "INGEST")
                _mark_stage(state, 1, "running")
                t0 = time.monotonic()
                try:
                    _stage_ingest(args)
                    elapsed = time.monotonic() - t0
                    _mark_stage(state, 1, "completed", duration_seconds=round(elapsed, 2))
                    print(f"  INGEST took {_fmt_duration(elapsed)}", file=sys.stderr)
                except KeyboardInterrupt:
                    elapsed = time.monotonic() - t0
                    print(f"\n  Ending INGEST early — partial results saved ({_fmt_duration(elapsed)})",
                          file=sys.stderr)
                    _mark_stage(state, 1, "completed", duration_seconds=round(elapsed, 2))
            else:
                print("No --pdf provided, skipping ingest", file=sys.stderr)
                _mark_stage(state, 1, "completed", skipped=True)
        else:
            print("Stage 1 (INGEST) already completed, skipping", file=sys.stderr)

        # Stage 2: Summarize
        if not _stage_completed(state, 2):
            _banner(2, total_stages, "SUMMARIZE")
            _mark_stage(state, 2, "running")
            t0 = time.monotonic()
            try:
                _stage_summarize(args)
                elapsed = time.monotonic() - t0
                _mark_stage(state, 2, "completed", duration_seconds=round(elapsed, 2))
                print(f"  SUMMARIZE took {_fmt_duration(elapsed)}", file=sys.stderr)
            except KeyboardInterrupt:
                elapsed = time.monotonic() - t0
                print(f"\n  Ending SUMMARIZE early — partial results saved ({_fmt_duration(elapsed)})",
                      file=sys.stderr)
                _mark_stage(state, 2, "completed", duration_seconds=round(elapsed, 2))
        else:
            print("Stage 2 (SUMMARIZE) already completed, skipping", file=sys.stderr)

        # Stage 3: Extract
        if not _stage_completed(state, 3):
            _banner(3, total_stages, "EXTRACT")
            _mark_stage(state, 3, "running")
            t0 = time.monotonic()
            try:
                should_continue = _stage_extract(args)
                elapsed = time.monotonic() - t0
                _mark_stage(state, 3, "completed", duration_seconds=round(elapsed, 2))
                print(f"  EXTRACT took {_fmt_duration(elapsed)}", file=sys.stderr)
                if not should_continue:
                    state["status"] = "paused"
                    _save_state(state)
                    return
            except KeyboardInterrupt:
                elapsed = time.monotonic() - t0
                print(f"\n  Ending EXTRACT early — partial results saved ({_fmt_duration(elapsed)})",
                      file=sys.stderr)
                _mark_stage(state, 3, "completed", duration_seconds=round(elapsed, 2))
        else:
            print("Stage 3 (EXTRACT) already completed, skipping", file=sys.stderr)

        # Stages 4-7: Derive → Review → Repair → Deduplicate (convergence loop)
        if not state.get("loop_completed"):
            start_cycle = state.get("current_cycle") or 1
            remaining_rounds = args.rounds - start_cycle + 1

            def _pipeline_on_stage(cycle, stage_num, event, **kwargs):
                if event == "start":
                    label = f"cycle {cycle}/{args.rounds}"
                    if stage_num == 4:
                        state["current_cycle"] = cycle
                        _save_state(state)
                    _banner(stage_num, total_stages,
                            f"{STAGE_NAMES[stage_num].upper()} ({label})")
                    _mark_stage(state, stage_num, "running", cycle=cycle)
                else:
                    _mark_stage(state, stage_num, "completed",
                                cycle=cycle, **kwargs)

            t0 = time.monotonic()
            try:
                _run_convergence_loop(
                    args, remaining_rounds,
                    start_cycle=start_cycle,
                    total_rounds=args.rounds,
                    on_stage=_pipeline_on_stage,
                )
                elapsed = time.monotonic() - t0
                print(f"  Convergence loop took {_fmt_duration(elapsed)}", file=sys.stderr)
            except KeyboardInterrupt:
                elapsed = time.monotonic() - t0
                print(f"\n  Ending convergence loop early — partial results saved ({_fmt_duration(elapsed)})",
                      file=sys.stderr)

            state["loop_completed"] = True
            _save_state(state)
        else:
            print("Convergence loop already completed, skipping", file=sys.stderr)

        # Stage 8: Export
        if not _stage_completed(state, 8):
            _banner(8, total_stages, "EXPORT")
            _mark_stage(state, 8, "running")
            t0 = time.monotonic()
            try:
                _stage_export(args)
                elapsed = time.monotonic() - t0
                _mark_stage(state, 8, "completed", duration_seconds=round(elapsed, 2))
                print(f"  EXPORT took {_fmt_duration(elapsed)}", file=sys.stderr)
            except KeyboardInterrupt:
                elapsed = time.monotonic() - t0
                print(f"\n  Ending EXPORT early — partial results saved ({_fmt_duration(elapsed)})",
                      file=sys.stderr)
                _mark_stage(state, 8, "completed", duration_seconds=round(elapsed, 2))
        else:
            print("Stage 8 (EXPORT) already completed, skipping", file=sys.stderr)

        # Stage 9: Index
        if not _stage_completed(state, 9):
            _banner(9, total_stages, "INDEX")
            _mark_stage(state, 9, "running")
            t0 = time.monotonic()
            try:
                _stage_index(args)
                elapsed = time.monotonic() - t0
                _mark_stage(state, 9, "completed", duration_seconds=round(elapsed, 2))
                print(f"  INDEX took {_fmt_duration(elapsed)}", file=sys.stderr)
            except KeyboardInterrupt:
                elapsed = time.monotonic() - t0
                print(f"\n  Ending INDEX early — partial results saved ({_fmt_duration(elapsed)})",
                      file=sys.stderr)
                _mark_stage(state, 9, "completed", duration_seconds=round(elapsed, 2))
        else:
            print("Stage 9 (INDEX) already completed, skipping", file=sys.stderr)

        total_elapsed = time.monotonic() - pipeline_t0
        state["status"] = "completed"
        state["total_duration_seconds"] = round(total_elapsed, 2)
        _save_state(state)
        print(f"\nPipeline complete in {_fmt_duration(total_elapsed)}.", file=sys.stderr)

    except Exception as e:
        state["status"] = "failed"
        state["error"] = str(e)
        state["error_traceback"] = traceback.format_exc()
        _save_state(state)
        raise
