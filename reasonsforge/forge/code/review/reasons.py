"""Direct reasons.db integration for code review (Phase 2).

Queries beliefs via the reasonsforge API instead of shelling out to the CLI.
Provides source-file-based lookup, truth value context, justification info,
and gated belief surfacing.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def find_reasons_db(
    repo_path: str,
    explicit_db: str | None = None,
) -> str | None:
    """Auto-detect reasons.db, checking multiple locations.

    Search order:
    1. Explicit path (--reasons-db flag)
    2. repo_path/reasons.db (standalone expert repo)
    3. Expert repo patterns: look for .forge/code/config.json pointing elsewhere
    """
    if explicit_db:
        p = os.path.abspath(explicit_db)
        return p if os.path.exists(p) else None

    direct = os.path.join(os.path.abspath(repo_path), "reasons.db")
    if os.path.exists(direct):
        return direct

    return None


def query_beliefs_for_files(
    changed_files: list[str],
    db_path: str,
) -> dict[str, Any]:
    """Query beliefs from reasons.db that relate to changed files.

    Uses direct API access instead of CLI text search. Matches beliefs by:
    - source field containing the file path
    - belief ID containing file stem keywords
    - belief text mentioning file paths

    Returns structured data with truth values, justification info, and metadata.
    """
    from reasonsforge.api import _with_network

    with _with_network(db_path) as net:
        file_stems = set()
        for fp in changed_files:
            stem = Path(fp).stem
            if stem not in _GENERIC_STEMS:
                file_stems.add(stem.lower())
            file_stems.add(fp.lower())
            parts = fp.lower().replace("\\", "/").split("/")
            if len(parts) >= 2:
                file_stems.add("/".join(parts[-2:]))

        matched: dict[str, dict] = {}

        for nid, node in net.nodes.items():
            if node.metadata.get("superseded_by"):
                continue

            score = _match_score(node, nid, changed_files, file_stems)
            if score == 0:
                continue

            is_premise = not node.justifications
            justification_types = []
            has_outlist = False
            for j in node.justifications:
                justification_types.append(j.type)
                if j.outlist:
                    has_outlist = True

            matched[nid] = {
                "id": nid,
                "text": node.text,
                "truth_value": node.truth_value,
                "source": node.source,
                "is_premise": is_premise,
                "justification_types": list(set(justification_types)),
                "has_outlist": has_outlist,
                "dependent_count": len(node.dependents),
                "score": score,
                "metadata": {
                    k: v for k, v in node.metadata.items()
                    if k in ("source_type", "review_result", "challenges")
                },
            }

        results = sorted(matched.values(), key=lambda b: (-b["score"], b["id"]))
        return {
            "beliefs": results,
            "count": len(results),
            "total_in_db": len(net.nodes),
        }


def query_gated_beliefs(
    changed_files: list[str],
    db_path: str,
) -> dict[str, Any]:
    """Find gated beliefs (OUT nodes blocked by IN outlist) related to changed files.

    These are beliefs that COULD become true if the diff resolves their blockers.
    """
    from reasonsforge.api import _with_network

    with _with_network(db_path) as net:
        file_stems = set()
        for fp in changed_files:
            stem = Path(fp).stem
            if stem not in _GENERIC_STEMS:
                file_stems.add(stem.lower())
            file_stems.add(fp.lower())

        relevant_gates: list[dict] = []

        for nid, node in net.nodes.items():
            if node.truth_value != "OUT":
                continue
            if node.metadata.get("superseded_by"):
                continue

            score = _match_score(node, nid, changed_files, file_stems)
            if score == 0:
                continue

            for j in node.justifications:
                active_blockers = []
                for outlist_id in j.outlist:
                    if outlist_id in net.nodes and net.nodes[outlist_id].truth_value == "IN":
                        blocker = net.nodes[outlist_id]
                        active_blockers.append({
                            "id": outlist_id,
                            "text": blocker.text,
                        })
                if active_blockers:
                    relevant_gates.append({
                        "gated_id": nid,
                        "gated_text": node.text,
                        "blockers": active_blockers,
                        "score": score,
                    })

        relevant_gates.sort(key=lambda g: -g["score"])
        return {
            "gated": relevant_gates,
            "count": len(relevant_gates),
        }


def format_beliefs_for_prompt(
    beliefs: list[dict],
    gated: list[dict] | None = None,
    max_size: int = 200_000,
) -> str:
    """Format queried beliefs into markdown for the review prompt.

    Includes truth values, justification info, and gated relationships.
    """
    lines = ["# Beliefs from reasons.db", ""]

    in_beliefs = [b for b in beliefs if b["truth_value"] == "IN"]
    out_beliefs = [b for b in beliefs if b["truth_value"] == "OUT"]

    if in_beliefs:
        lines.append("## Active Beliefs (IN)")
        lines.append("")
        for b in in_beliefs:
            jtype = ", ".join(b["justification_types"]) if b["justification_types"] else "premise"
            deps = f", {b['dependent_count']} dependents" if b["dependent_count"] else ""
            lines.append(f"### `{b['id']}` [IN]")
            lines.append(f"{b['text']}")
            lines.append(f"- Justification: {jtype}{deps}")
            if b["source"]:
                lines.append(f"- Source: {b['source']}")
            lines.append("")

    if out_beliefs:
        lines.append("## Retracted Beliefs (OUT)")
        lines.append("")
        for b in out_beliefs:
            lines.append(f"### `{b['id']}` [OUT]")
            lines.append(f"{b['text']}")
            if b["source"]:
                lines.append(f"- Source: {b['source']}")
            lines.append("")

    if gated:
        lines.append("## Gated Beliefs (blocked by active beliefs)")
        lines.append("")
        lines.append("These beliefs are OUT because their blockers are IN.")
        lines.append("If the diff resolves a blocker, the gated belief could become true.")
        lines.append("")
        for g in gated:
            lines.append(f"### `{g['gated_id']}` [GATED]")
            lines.append(f"{g['gated_text']}")
            for blocker in g["blockers"]:
                lines.append(f"- Blocked by `{blocker['id']}`: {blocker['text']}")
            lines.append("")

    result = "\n".join(lines)
    if len(result) > max_size:
        cut = result[:max_size]
        last_header = cut.rfind("\n### ")
        if last_header > 0:
            cut = cut[:last_header]
        result = cut + f"\n\n<!-- truncated at {max_size // 1024}KB -->\n"
    return result


def _match_score(node, nid: str, changed_files: list[str], file_stems: set[str]) -> int:
    """Score how relevant a belief is to the changed files."""
    score = 0
    nid_lower = nid.lower()
    text_lower = node.text.lower()
    source_lower = (node.source or "").lower()

    for fp in changed_files:
        fp_lower = fp.lower()
        if fp_lower in source_lower:
            score += 5
        if fp_lower in text_lower:
            score += 3

    for stem in file_stems:
        if "/" in stem:
            if stem in source_lower:
                score += 4
            continue
        stem_parts = nid_lower.replace(":", "-").split("-")
        if stem in stem_parts:
            score += 2
        if stem in text_lower:
            score += 1

    return score


_GENERIC_STEMS = {
    "__init__", "utils", "helpers", "common", "types", "main",
    "index", "app", "core", "base", "models", "api", "config",
    "setup", "conftest", "fixtures", "mod", "lib",
}
