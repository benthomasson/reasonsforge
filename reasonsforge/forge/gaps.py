"""Gap analysis — identify missing topics in a belief network."""

import asyncio
import json
import sys
from pathlib import Path

from reasonsforge.api import compact, export_network

from .llm import check_model_available, extract_json, invoke, RETRY_JSON
from .prompts import GAP_ANALYSIS, GAP_EXTRACT, PROPOSE_BELIEFS

from . import REASONS_DB


def cmd_gaps(args):
    """Analyze belief network for coverage gaps."""
    from .caffeinate import hold as _caffeinate
    _caffeinate()

    model = getattr(args, "model", "claude")
    domain = getattr(args, "domain", None)
    output = getattr(args, "output", None)
    extract = getattr(args, "extract", False)
    input_dir = getattr(args, "input_dir", "summaries")
    db_path = getattr(args, "db", REASONS_DB)
    timeout = getattr(args, "timeout", 600)
    num_ctx = getattr(args, "num_ctx", None)

    if not Path(db_path).exists():
        print(f"No belief database found: {db_path}")
        sys.exit(1)

    if not check_model_available(model):
        print(f"Model not available: {model}")
        sys.exit(1)

    beliefs_text = compact(budget=2000, db_path=db_path)
    if not beliefs_text.strip():
        print("No beliefs in the network. Run extraction first.")
        return

    domain_instruction = ""
    if domain:
        domain_instruction = f"Domain: {domain}\n"

    prompt = GAP_ANALYSIS.format(
        beliefs=beliefs_text,
        domain_instruction=domain_instruction,
    )

    est_tokens = len(prompt) // 4
    if num_ctx:
        pct = est_tokens * 100 / num_ctx
        print(f"Analyzing gaps (~{est_tokens:,} tokens, {pct:.0f}% of {num_ctx:,} ctx)...",
              file=sys.stderr)
    else:
        print(f"Analyzing gaps (~{est_tokens:,} tokens)...", file=sys.stderr)

    result = asyncio.run(invoke(prompt, model=model, timeout=timeout))

    gaps = extract_json(result)
    if not isinstance(gaps, list):
        try:
            retry = asyncio.run(invoke(
                prompt + "\n\n" + result + "\n\n" + RETRY_JSON,
                model=model, timeout=timeout,
            ))
            gaps = extract_json(retry)
        except Exception:
            pass
    if not isinstance(gaps, list):
        print("Could not parse gap analysis response.", file=sys.stderr)
        print(result)
        return

    if output:
        Path(output).write_text(json.dumps(gaps, indent=2) + "\n")
        print(f"Wrote {len(gaps)} gaps to {output}", file=sys.stderr)

    _print_gaps(gaps)

    from .llm import format_cost_summary
    cost = format_cost_summary()
    if cost:
        print(f"\n  {cost}", file=sys.stderr)

    if extract:
        _run_extract(args, gaps, input_dir, model, db_path, timeout, num_ctx)


def _print_gaps(gaps):
    """Print gap analysis in readable format."""
    by_area = {}
    for g in gaps:
        area = g.get("topic_area", "Other")
        by_area.setdefault(area, []).append(g)

    priority_order = {"high": 0, "medium": 1, "low": 2}

    for area, items in sorted(by_area.items()):
        print(f"\n  {area}")
        items.sort(key=lambda x: priority_order.get(x.get("priority", "medium"), 1))
        for g in items:
            pri = g.get("priority", "medium")
            partial = " (partially present)" if g.get("partially_present") else ""
            marker = {"high": "!", "medium": "-", "low": "."}
            print(f"    {marker.get(pri, '-')} {g['subject']}{partial}")
            if g.get("description"):
                print(f"      {g['description']}")


def _run_extract(args, gaps, input_dir, model, db_path, timeout, num_ctx):
    """Run targeted extraction using gap list."""
    from .propose import (
        _load_existing_beliefs, _build_dedup_context,
        _has_embeddings, _get_belief_embeddings,
    )
    from .llm import extract_json as _extract_json
    from . import PROJECT_DIR

    input_path = Path(input_dir)
    if not input_path.exists():
        print(f"\nNo {input_dir}/ directory for extraction.", file=sys.stderr)
        return

    entries = sorted(input_path.rglob("*.md"))
    if not entries:
        print(f"\nNo .md files in {input_dir}/.", file=sys.stderr)
        return

    gap_list = "\n".join(
        f"- {g['subject']}: {g.get('description', '')}"
        for g in gaps
        if g.get("priority") in ("high", "medium")
    )
    if not gap_list:
        print("\nNo high/medium priority gaps to extract.", file=sys.stderr)
        return

    existing_beliefs = _load_existing_beliefs(db_path)
    existing_ids = {b["id"] for b in existing_beliefs}

    belief_vectors = None
    if existing_beliefs and _has_embeddings():
        cache_path = Path(PROJECT_DIR) / "belief-vectors.json"
        belief_vectors = _get_belief_embeddings(existing_beliefs, cache_path)

    batch_size = getattr(args, "batch_size", 5)
    parallel = getattr(args, "parallel", 1)

    print(f"\nTargeted extraction: {len(entries)} entries, looking for {len(gaps)} gaps...",
          file=sys.stderr)

    batches = []
    batch_paths = []
    current_batch = []
    current_paths = []
    for entry in entries:
        current_batch.append(entry.read_text())
        current_paths.append(str(entry))
        if len(current_batch) >= batch_size:
            batches.append("\n\n---\n\n".join(current_batch))
            batch_paths.append(current_paths)
            current_batch = []
            current_paths = []
    if current_batch:
        batches.append("\n\n---\n\n".join(current_batch))
        batch_paths.append(current_paths)

    output_path = Path(getattr(args, "proposals_output", "proposed-beliefs-gaps.md"))

    all_found = []

    async def _extract_batch(i, batch_text):
        existing_context = _build_dedup_context(
            existing_beliefs, batch_paths[i], batch_text,
            belief_vectors=belief_vectors,
        )
        prompt = GAP_EXTRACT.format(
            gap_list=gap_list,
            entries=batch_text,
        ) + existing_context

        est = len(prompt) // 4
        if num_ctx:
            pct = est * 100 / num_ctx
            print(f"  Batch {i + 1}/{len(batches)} (~{est:,} tokens, {pct:.0f}% of {num_ctx:,} ctx)...")
        else:
            print(f"  Batch {i + 1}/{len(batches)} (~{est:,} tokens)...")

        try:
            result = await invoke(prompt, model=model, timeout=timeout)
        except Exception as e:
            print(f"  ERROR: {e}")
            return []

        beliefs = _extract_json(result)
        if not isinstance(beliefs, list):
            try:
                retry = await invoke(
                    prompt + "\n\n" + result + "\n\n" + RETRY_JSON,
                    model=model, timeout=timeout,
                )
                beliefs = _extract_json(retry)
            except Exception:
                pass
        if not isinstance(beliefs, list):
            return []

        new = [b for b in beliefs if b.get("id") and b["id"] not in existing_ids]
        return new

    async def _run_all():
        sem = asyncio.Semaphore(parallel)

        async def _limited(i, text):
            async with sem:
                return await _extract_batch(i, text)

        tasks = [_limited(i, text) for i, text in enumerate(batches)]
        return await asyncio.gather(*tasks)

    results = asyncio.run(_run_all())
    for batch_beliefs in results:
        all_found.extend(batch_beliefs)

    if not all_found:
        print("\nNo new beliefs found from gap extraction.", file=sys.stderr)
        return

    with output_path.open("w") as f:
        f.write("# Gap Extraction — Proposed Beliefs\n\n")
        for b in all_found:
            bid = b.get("id", "unknown")
            claim = b.get("claim", "")
            source = b.get("source", "")
            accept = b.get("accept", True)
            tag = "ACCEPT" if accept else "REJECT"
            f.write(f"### [{tag}] {bid}\n")
            f.write(f"{claim}\n")
            if source:
                f.write(f"- Source: {source}\n")
            f.write("\n")

    print(f"\nFound {len(all_found)} new beliefs, wrote to {output_path}", file=sys.stderr)
    print("Review the proposals, then run:", file=sys.stderr)
    print(f"  reasonsforge forge accept-beliefs --file {output_path}", file=sys.stderr)
