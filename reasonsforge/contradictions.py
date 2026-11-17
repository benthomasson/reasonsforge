"""Detect contradictions between IN beliefs via LLM analysis.

Sends batches of currently-held beliefs to an LLM to identify sets
of beliefs that cannot all be true simultaneously (nogoods).
"""

import random
import re
import sys

from .llm import invoke_model

CONTRADICTION_BATCH_SIZE = 50

CONTRADICTION_PROMPT = """\
You are auditing a belief network for contradictions.
Each belief below is currently held as true (IN). Your task is to find
sets of beliefs that cannot all be true simultaneously.

Types of contradictions:

1. **Direct negation**: belief A asserts X, belief B asserts not-X
2. **Incompatible properties**: A says X has property P, B says X has
   property Q, where P and Q are mutually exclusive
3. **Scope conflict**: A says "all X are Y", B provides a counterexample
4. **Quantitative mismatch**: A implies a value or bound incompatible with B

For each contradiction found, output EXACTLY this format:

### NOGOOD short-kebab-id
- Claims: belief-id-1, belief-id-2
- Analysis: Why these cannot all be true
- Severity: High|Medium|Low

Rules:
- Only report genuine contradictions where the claims are logically
  incompatible. Do NOT report tensions, tradeoffs, or differences in
  emphasis that can coexist.
- Claims must use exact belief IDs from the list below.
- Minimum 2 claims per NOGOOD.
- Be rigorous: "X is fast" and "X could be faster" is NOT a contradiction.
- If no contradictions are found, respond with: No contradictions detected.

## Beliefs to check

{beliefs}"""


def format_beliefs_for_contradiction_check(belief_ids, nodes):
    """Format beliefs as a flat list for contradiction checking."""
    lines = []
    for nid in belief_ids:
        node = nodes.get(nid)
        if not node:
            continue
        text = node.get("text", "")
        if len(text) > 200:
            text = text[:197] + "..."
        lines.append(f"- `{nid}`: {text}")
    return "\n".join(lines)


_NOGOOD_PATTERN = re.compile(
    r"###\s+NOGOOD\s+(\S+)\s*\n"
    r"(.+?)(?=\n###\s+NOGOOD|\Z)",
    re.DOTALL,
)


def parse_contradiction_response(response, valid_ids=None):
    """Extract NOGOOD proposals from LLM response.

    Args:
        response: Raw LLM response text.
        valid_ids: Optional set of valid belief IDs to filter claims against.

    Returns: list of dicts with id, claims, analysis, severity.
    """
    results = []

    for match in _NOGOOD_PATTERN.finditer(response):
        nogood_id = match.group(1)
        body = match.group(2).strip()

        claims = []
        analysis = ""
        severity = ""
        for line in body.split("\n"):
            line = line.strip().lstrip("- ")
            if line.lower().startswith("claims:"):
                claims_str = line.split(":", 1)[1].strip()
                claims = [c.strip().strip("`") for c in claims_str.split(",")
                          if c.strip()]
            elif line.lower().startswith("analysis:"):
                analysis = line.split(":", 1)[1].strip()
            elif line.lower().startswith("severity:"):
                severity = line.split(":", 1)[1].strip()

        if valid_ids is not None:
            claims = [c for c in claims if c in valid_ids]

        if len(claims) >= 2:
            results.append({
                "id": nogood_id,
                "claims": claims,
                "analysis": analysis,
                "severity": severity,
            })

    return results


def _flush_results(results, output_path, header_written):
    """Write results to plan file incrementally."""
    if not output_path or not results:
        return header_written
    from .api import write_contradiction_plan
    write_contradiction_plan(results, output_path, append=header_written)
    return True


def detect_contradictions(nodes, belief_ids=None, model="claude", timeout=300,
                          batch_size=CONTRADICTION_BATCH_SIZE,
                          output_path=None, parallel=1):
    """Detect contradictions between IN beliefs via LLM.

    Args:
        nodes: Dict of node_id -> node data from export_network().
        belief_ids: Optional list of specific IDs to check.
            If None, checks all IN beliefs.
        model: LLM model to use.
        timeout: LLM timeout in seconds.
        batch_size: Number of beliefs per LLM call.
        output_path: If set, write results incrementally to this file.
        parallel: Max concurrent LLM calls (default 1 = sequential).

    Returns: list of contradiction dicts.
    """
    if belief_ids is None:
        belief_ids = [
            nid for nid, node in sorted(nodes.items())
            if node.get("truth_value") == "IN"
        ]
    else:
        belief_ids = [
            nid for nid in belief_ids
            if nid in nodes
            and nodes[nid].get("truth_value") == "IN"
        ]

    if not belief_ids:
        return []

    belief_ids = list(belief_ids)
    random.shuffle(belief_ids)

    valid_ids = set(belief_ids)
    all_results = []
    total_batches = (len(belief_ids) + batch_size - 1) // batch_size
    header_written = False

    if parallel > 1 and total_batches > 1:
        import asyncio
        from .llm import invoke

        batches_data = []
        for i in range(0, len(belief_ids), batch_size):
            batch = belief_ids[i:i + batch_size]
            beliefs_text = format_beliefs_for_contradiction_check(batch, nodes)
            prompt = CONTRADICTION_PROMPT.format(beliefs=beliefs_text)
            batches_data.append(prompt)

        done_count = 0

        async def _run_all():
            nonlocal done_count, header_written
            sem = asyncio.Semaphore(parallel)
            lock = asyncio.Lock()

            async def _run_one(idx, prompt):
                nonlocal done_count, header_written
                async with sem:
                    response = await invoke(prompt, model, timeout=timeout)
                results = parse_contradiction_response(response,
                                                       valid_ids=valid_ids)
                async with lock:
                    all_results.extend(results)
                    header_written = _flush_results(results, output_path,
                                                    header_written)
                    done_count += 1
                    print(f"  Batch {done_count}/{total_batches} done",
                          file=sys.stderr)

            tasks = [_run_one(i, p) for i, p in enumerate(batches_data)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    print(f"  WARN: batch {i + 1} failed: {r}",
                          file=sys.stderr)

        print(f"  Checking {total_batches} batches with parallel={parallel}...",
              file=sys.stderr)
        asyncio.run(_run_all())
    else:
        for i in range(0, len(belief_ids), batch_size):
            batch = belief_ids[i:i + batch_size]
            batch_num = i // batch_size + 1
            print(f"  Checking batch {batch_num}/{total_batches} "
                  f"({len(batch)} beliefs)...", file=sys.stderr)

            beliefs_text = format_beliefs_for_contradiction_check(batch, nodes)
            prompt = CONTRADICTION_PROMPT.format(beliefs=beliefs_text)

            try:
                response = invoke_model(prompt, model=model, timeout=timeout)
                results = parse_contradiction_response(response,
                                                       valid_ids=valid_ids)
                all_results.extend(results)
                header_written = _flush_results(results, output_path,
                                                header_written)
            except Exception as e:
                print(f"  WARN: batch {batch_num} failed: {e}",
                      file=sys.stderr)

    return all_results


def detect_contradictions_semantic(nodes, belief_ids=None, model="claude",
                                   timeout=300, embedding_model=None,
                                   output_path=None, parallel=1):
    """Detect contradictions by clustering beliefs semantically before LLM analysis.

    Groups beliefs by semantic similarity so topically related beliefs
    are analyzed together, increasing the chance of catching contradictions.

    Args:
        nodes: Dict of node_id -> node data from export_network().
        belief_ids: Optional list of specific IDs to check.
        model: LLM model to use.
        timeout: LLM timeout in seconds.
        embedding_model: Sentence-transformers model name.
        output_path: If set, write results incrementally to this file.
        parallel: Max concurrent LLM calls (default 1 = sequential).

    Returns: list of contradiction dicts.
    """
    from .cluster import list_clusters, DEFAULT_MODEL

    if belief_ids is None:
        belief_ids = [
            nid for nid, node in sorted(nodes.items())
            if node.get("truth_value") == "IN"
        ]
    else:
        belief_ids = [
            nid for nid in belief_ids
            if nid in nodes
            and nodes[nid].get("truth_value") == "IN"
        ]

    if not belief_ids:
        return []

    beliefs = {}
    for nid in belief_ids:
        text = nodes[nid].get("text", "")
        beliefs[nid] = text

    result = list_clusters(
        beliefs,
        model_name=embedding_model or DEFAULT_MODEL,
    )

    valid_ids = set(belief_ids)
    all_results = []
    clusters = result["clusters"]
    header_written = False

    # Flatten clusters into a list of (cluster_index, batch) pairs for LLM calls
    work_items = []
    for ci, cluster in enumerate(clusters, 1):
        cluster_ids = [b["id"] for b in cluster["beliefs"]]
        if len(cluster_ids) < 2:
            continue
        for i in range(0, len(cluster_ids), CONTRADICTION_BATCH_SIZE):
            batch = cluster_ids[i:i + CONTRADICTION_BATCH_SIZE]
            beliefs_text = format_beliefs_for_contradiction_check(batch, nodes)
            prompt = CONTRADICTION_PROMPT.format(beliefs=beliefs_text)
            work_items.append((ci, prompt))

    if not work_items:
        return []

    total_work = len(work_items)

    if parallel > 1 and total_work > 1:
        import asyncio
        from .llm import invoke

        done_count = 0

        async def _run_all():
            nonlocal done_count, header_written
            sem = asyncio.Semaphore(parallel)
            lock = asyncio.Lock()

            async def _run_one(idx, ci, prompt):
                nonlocal done_count, header_written
                async with sem:
                    response = await invoke(prompt, model, timeout=timeout)
                results = parse_contradiction_response(response,
                                                       valid_ids=valid_ids)
                async with lock:
                    all_results.extend(results)
                    header_written = _flush_results(results, output_path,
                                                    header_written)
                    done_count += 1
                    print(f"  Cluster batch {done_count}/{total_work} done",
                          file=sys.stderr)

            tasks = [_run_one(i, ci, p) for i, (ci, p) in enumerate(work_items)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, r in enumerate(results):
                if isinstance(r, Exception):
                    ci = work_items[i][0]
                    print(f"  WARN: cluster {ci} batch failed: {r}",
                          file=sys.stderr)

        print(f"  Checking {total_work} cluster batches with parallel={parallel}...",
              file=sys.stderr)
        asyncio.run(_run_all())
    else:
        for idx, (ci, prompt) in enumerate(work_items):
            print(f"  Checking cluster {ci}/{len(clusters)} "
                  f"(batch {idx + 1}/{total_work})...", file=sys.stderr)
            try:
                response = invoke_model(prompt, model=model, timeout=timeout)
                results = parse_contradiction_response(response,
                                                       valid_ids=valid_ids)
                all_results.extend(results)
                header_written = _flush_results(results, output_path,
                                                header_written)
            except Exception as e:
                print(f"  WARN: cluster {ci} batch failed: {e}",
                      file=sys.stderr)

    return all_results
