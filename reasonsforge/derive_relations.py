"""Derive-relations phase: discover FOL relations between existing beliefs.

Examines pairs of beliefs and asks the LLM to classify which of the 12
relation types apply, then creates new relational beliefs with both source
beliefs in the support list.

Pipeline position: summarize → propose → derive → derive-relations → [review-relations] → verify

See: reasons-service-expert/entries/2026/10/13/first-class-fol-relations-in-reasonsforge.md
GitHub: reasonsforge#40
"""

from __future__ import annotations

import random
import re
from itertools import combinations

from .relations import RELATION_TYPES, extract_triple


DERIVE_RELATIONS_PROMPT = """\
You are a knowledge architect analyzing a belief network for structural \
relationships between concepts.

{domain_context}

## Background

An EEM (Executable Epistemology Model) captures beliefs with justifications. \
Beyond justification, beliefs can relate to each other structurally — one concept \
requires another, extends it, contains it, etc. These relations are valuable for \
curriculum design, dependency analysis, and knowledge navigation.

## Relation Types

The 12 relation types you can propose:

| Relation | Meaning | Example |
|----------|---------|---------|
| **requires** | A cannot function without B | ring-requires-group |
| **enables** | A makes B possible or easier | normal-subgroup-enables-quotient-group |
| **extends** | A builds on B, adding structure | ring-extends-group |
| **is-a** | A is a specialization of B | field-is-a-ring |
| **has-a** | A contains B as a component | group-has-a-identity-element |
| **contains** | A includes B as a member/topic | algebra-contains-ring-theory |
| **implements** | A is a concrete instance of B | integers-implement-ring-axioms |
| **produces** | A generates or yields B | homomorphism-produces-kernel |
| **causes** | A leads to or triggers B | associativity-causes-unique-identity |
| **replaces** | A supersedes or substitutes for B | category-theory-replaces-set-theoretic-foundations |
| **prevents** | A blocks or conflicts with B | commutativity-prevents-nonabelian-structure |
| **maps-to** | A corresponds to B across domains | group-homomorphism-maps-to-ring-homomorphism |

## Your Task

Given the belief pairs below, determine which structural relationships hold \
between them. For each pair, propose a relational belief if a clear relationship \
exists. Skip pairs with no meaningful structural relationship.

## Rules

- Only propose relations where the structural relationship is clear and defensible
- Choose the most specific relation type — prefer `requires` over `enables` when \
  the dependency is strict
- Direction matters: `A-requires-B` means A depends on B, not the reverse
- Both source beliefs become antecedents (the relation is justified by both concepts existing)
- The relational belief name must follow the pattern: `subject-relation-object`
- Subject and object should be short, descriptive slugs derived from the belief names
- Don't propose relations already encoded in existing belief names
- Don't force relations where none exist — skip pairs that are merely topically related
{use_case_guidance}
## Output Format

For each proposed relation, output EXACTLY this format:

### RELATION <subject-relation-object>
<one-line description of why this relationship holds>
- Subject: <belief-id-1>
- Object: <belief-id-2>
- Relation: <one of the 12 relation types>
- Confidence: HIGH or MEDIUM
- Label: <brief rationale>

If no relation exists for a pair, skip it entirely (do not output anything).

---

## Belief Pairs to Analyze

{pairs_section}

## Existing Relations

These relations are already captured — do not re-propose them:

{existing_relations_section}
"""

LESSON_PLANNING_GUIDANCE = """
## Use Case: Lesson Planning

These relations will be used for curriculum design:
- **requires** → prerequisite ordering (teach A before B)
- **extends** → complexity gradient (A builds on B within a module)
- **is-a** → taxonomy (organize concept hierarchy into modules)
- **implements** → examples (pair concrete instances with abstract concepts)
- **enables** → motivation ("we learn A because it unlocks B")
- **contains** → module boundaries (what belongs in chapter N)
- **maps-to** → cross-topic review ("remember how this worked in topic X?")

Prioritize these 7 relation types. The others (causes, produces, replaces, \
prevents, has-a) are less relevant for lesson planning but propose them if \
they clearly apply.
"""


def _build_justification_graph(nodes: dict) -> dict[str, set[str]]:
    """Build an undirected adjacency graph from justification links.

    Two beliefs are adjacent if:
    - One is an antecedent of the other (direct justification link)
    - They share an antecedent (co-justified siblings)
    """
    adj: dict[str, set[str]] = {}
    antecedent_to_dependents: dict[str, set[str]] = {}

    for nid, node in nodes.items():
        if isinstance(node, dict):
            justs = node.get("justifications", [])
        else:
            justs = getattr(node, "justifications", [])
            if hasattr(justs, "__iter__") and not isinstance(justs, (str, dict)):
                justs = [{"antecedents": getattr(j, "antecedents", [])} for j in justs]
            else:
                justs = []

        for j in justs:
            antes = j.get("antecedents", [])
            for a in antes:
                adj.setdefault(nid, set()).add(a)
                adj.setdefault(a, set()).add(nid)
                antecedent_to_dependents.setdefault(a, set()).add(nid)

    for ante, deps in antecedent_to_dependents.items():
        dep_list = list(deps)
        for i in range(len(dep_list)):
            for j in range(i + 1, len(dep_list)):
                adj.setdefault(dep_list[i], set()).add(dep_list[j])
                adj.setdefault(dep_list[j], set()).add(dep_list[i])

    return adj


def _pairs_by_justification(
    nodes: dict,
    budget: int,
    max_hops: int = 2,
    seed: int | None = None,
    allowed_ids: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Generate belief pairs using justification graph proximity.

    Walks N hops on the justification graph and pairs beliefs that are
    within max_hops of each other. The full graph is used for traversal
    but only beliefs in allowed_ids (if provided) appear in output pairs.

    Args:
        nodes: Full nodes dict (used to build the justification graph).
        budget: Maximum number of pairs to return.
        max_hops: Maximum graph distance for pairing.
        seed: Random seed for reproducibility.
        allowed_ids: If provided, only pair beliefs in this set.
    """
    adj = _build_justification_graph(nodes)

    all_in = set()
    for nid, node in nodes.items():
        tv = node.get("truth_value", "IN") if isinstance(node, dict) else getattr(node, "truth_value", "IN")
        if tv == "IN":
            all_in.add(nid)

    start_set = all_in & allowed_ids if allowed_ids is not None else all_in

    pairs_by_distance: dict[int, set[tuple[str, str]]] = {}

    for start in sorted(start_set):
        visited = {start}
        frontier = {start}
        for hop in range(1, max_hops + 1):
            next_frontier = set()
            for node in frontier:
                for neighbor in adj.get(node, set()):
                    if neighbor not in visited and neighbor in all_in:
                        if allowed_ids is None or neighbor in allowed_ids:
                            pair = (min(start, neighbor), max(start, neighbor))
                            pairs_by_distance.setdefault(hop, set()).add(pair)
                        next_frontier.add(neighbor)
                visited |= next_frontier
            frontier = next_frontier

    all_pairs = []
    for hop in range(1, max_hops + 1):
        hop_pairs = sorted(pairs_by_distance.get(hop, set()))
        all_pairs.extend(hop_pairs)

    seen = set()
    deduped = []
    for p in all_pairs:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    if len(deduped) <= budget:
        return deduped

    rng = random.Random(seed)
    hop1 = sorted(pairs_by_distance.get(1, set()))
    hop2 = sorted(pairs_by_distance.get(2, set()) - pairs_by_distance.get(1, set()))

    result = []
    hop1_budget = min(len(hop1), budget * 2 // 3)
    if len(hop1) > hop1_budget:
        result.extend(rng.sample(hop1, hop1_budget))
    else:
        result.extend(hop1)

    remaining = budget - len(result)
    if remaining > 0 and hop2:
        if len(hop2) > remaining:
            result.extend(rng.sample(hop2, remaining))
        else:
            result.extend(hop2)

    return result


def _sample_pairs(
    belief_ids: list[str],
    budget: int,
    seed: int | None = None,
    strategy: str = "random",
    nodes: dict | None = None,
) -> list[tuple[str, str]]:
    """Sample belief pairs for relation analysis.

    Args:
        belief_ids: List of belief IDs to pair.
        budget: Maximum number of pairs to return.
        seed: Random seed for reproducibility.
        strategy: Sampling strategy — "random", "prefix", "justification", or "all".
        nodes: Full nodes dict (required for "justification" strategy).

    Returns:
        List of (belief_id_1, belief_id_2) tuples.
    """
    if strategy == "justification" and nodes:
        return _pairs_by_justification(nodes, budget, seed=seed,
                                       allowed_ids=set(belief_ids) if belief_ids else None)

    if strategy == "all" or len(belief_ids) <= 1:
        pairs = list(combinations(sorted(belief_ids), 2))
        if len(pairs) <= budget:
            return pairs
        rng = random.Random(seed)
        return rng.sample(pairs, budget)

    if strategy == "prefix":
        groups: dict[str, list[str]] = {}
        for bid in belief_ids:
            prefix = bid.split("-")[0] if "-" in bid else bid
            groups.setdefault(prefix, []).append(bid)

        pairs = []
        for prefix, members in groups.items():
            pairs.extend(combinations(sorted(members), 2))

        for prefixes in combinations(sorted(groups.keys()), 2):
            g1, g2 = groups[prefixes[0]], groups[prefixes[1]]
            cross = [(a, b) for a in g1 for b in g2]
            rng = random.Random(seed)
            per_cross = max(2, budget // (len(groups) * 2))
            if len(cross) > per_cross:
                cross = rng.sample(cross, per_cross)
            pairs.extend(cross)

        if len(pairs) <= budget:
            return pairs
        rng = random.Random(seed)
        return rng.sample(pairs, budget)

    rng = random.Random(seed)
    all_pairs = list(combinations(sorted(belief_ids), 2))
    if len(all_pairs) <= budget:
        return all_pairs
    return rng.sample(all_pairs, budget)


def _filter_existing_relations(pairs, nodes):
    """Remove pairs that already have relational beliefs encoding the relation."""
    existing_subjects = set()
    for nid in nodes:
        triple = extract_triple(nid)
        if triple:
            existing_subjects.add((triple.subject, triple.object))
            existing_subjects.add((triple.object, triple.subject))

    filtered = []
    for a, b in pairs:
        a_short = a.split("-")[0] if "-" in a else a
        b_short = b.split("-")[0] if "-" in b else b
        if (a_short, b_short) not in existing_subjects and (b_short, a_short) not in existing_subjects:
            filtered.append((a, b))
    return filtered


def build_pairs_section(pairs, nodes):
    """Build the belief pairs section for the prompt."""
    lines = []
    for i, (a, b) in enumerate(pairs, 1):
        a_text = ""
        b_text = ""
        if a in nodes:
            a_text = nodes[a].get("text", "")[:150] if isinstance(nodes[a], dict) else getattr(nodes[a], "text", "")[:150]
        if b in nodes:
            b_text = nodes[b].get("text", "")[:150] if isinstance(nodes[b], dict) else getattr(nodes[b], "text", "")[:150]
        lines.append(f"### Pair {i}")
        lines.append(f"- **A**: `{a}` — {a_text}")
        lines.append(f"- **B**: `{b}` — {b_text}")
        lines.append("")
    return "\n".join(lines)


def build_existing_relations_section(nodes):
    """Build a section listing relations already encoded in belief names."""
    lines = []
    for nid in sorted(nodes.keys()):
        triple = extract_triple(nid)
        if triple:
            lines.append(f"- `{nid}`: {triple.subject} —[{triple.relation}]→ {triple.object}")
    return "\n".join(lines) if lines else "(None found)"


def build_prompt(
    nodes: dict,
    pairs: list[tuple[str, str]],
    domain: str | None = None,
    use_case: str | None = "lesson-planning",
) -> str:
    """Build the derive-relations prompt.

    Args:
        nodes: Dict of node_id -> node data.
        pairs: List of (belief_id_1, belief_id_2) tuples.
        domain: Optional domain description.
        use_case: Use case for guidance. Currently supports "lesson-planning".

    Returns:
        prompt text
    """
    domain_context = ""
    if domain:
        domain_context = f"## Domain\n\n{domain}\n"

    use_case_guidance = ""
    if use_case == "lesson-planning":
        use_case_guidance = LESSON_PLANNING_GUIDANCE

    pairs_section = build_pairs_section(pairs, nodes)
    existing_section = build_existing_relations_section(nodes)

    return DERIVE_RELATIONS_PROMPT.format(
        domain_context=domain_context,
        use_case_guidance=use_case_guidance,
        pairs_section=pairs_section,
        existing_relations_section=existing_section,
    )


def parse_relation_proposals(response: str) -> list[dict]:
    """Parse RELATION proposals from LLM response.

    Expected format:
        ### RELATION subject-relation-object
        Description text
        - Subject: belief-id-1
        - Object: belief-id-2
        - Relation: requires
        - Confidence: HIGH
        - Label: rationale
    """
    proposals = []
    pattern = re.compile(
        r"### RELATION (\S+)\n"
        r"(.+?)\n"
        r"- Subject: (.+?)\n"
        r"- Object: (.+?)\n"
        r"- Relation: (.+?)\n"
        r"- Confidence: (HIGH|MEDIUM)\n"
        r"- Label: (.+?)(?:\n|$)",
    )
    for match in pattern.finditer(response):
        relation_type = match.group(5).strip().lower()
        if relation_type not in RELATION_TYPES:
            continue
        proposal = {
            "id": match.group(1).strip("`"),
            "text": match.group(2).strip(),
            "subject_belief": match.group(3).strip().strip("`"),
            "object_belief": match.group(4).strip().strip("`"),
            "relation": relation_type,
            "confidence": match.group(6).strip(),
            "label": match.group(7).strip(),
        }
        triple = extract_triple(proposal["id"])
        if triple and triple.relation in RELATION_TYPES:
            proposals.append(proposal)

    return proposals


def validate_proposals(proposals, nodes):
    """Validate relation proposals against the network.

    Returns (valid, skipped) where skipped entries have a reason.
    """
    valid = []
    skipped = []
    seen_ids = set()

    for p in proposals:
        if p["subject_belief"] not in nodes:
            skipped.append((p, f"subject not found: {p['subject_belief']}"))
            continue
        if p["object_belief"] not in nodes:
            skipped.append((p, f"object not found: {p['object_belief']}"))
            continue
        if p["id"] in nodes:
            skipped.append((p, "belief already exists"))
            continue
        if p["id"] in seen_ids:
            skipped.append((p, "duplicate proposal"))
            continue
        triple = extract_triple(p["id"])
        if not triple:
            skipped.append((p, "name does not encode a valid relation"))
            continue
        seen_ids.add(p["id"])
        valid.append(p)

    return valid, skipped


def apply_proposals(valid, db_path="reasons.db", model=""):
    """Add valid relation proposals to the database.

    Each relational belief is justified by both source beliefs (the subject
    and object). This means if either source belief goes OUT, the relation
    goes OUT too — standard TMS behavior.

    Returns list of (proposal, result_dict_or_error_string).
    """
    from . import api

    results = []
    for p in valid:
        try:
            sl = f"{p['subject_belief']},{p['object_belief']}"
            result = api.add_node(
                node_id=p["id"],
                text=p["text"],
                sl=sl,
                label=p["label"],
                source_type="derived",
                model=model,
                db_path=db_path,
            )
            results.append((p, result))
        except Exception as e:
            results.append((p, str(e)))
    return results


def write_proposals_file(valid, output_path):
    """Write proposals to a markdown file for human review."""
    from pathlib import Path
    lines = [
        "# Derived Relations",
        "",
        f"Generated {len(valid)} relation proposals.",
        "",
    ]
    for p in valid:
        lines.append(f"### RELATION {p['id']}")
        lines.append(p["text"])
        lines.append(f"- Subject: {p['subject_belief']}")
        lines.append(f"- Object: {p['object_belief']}")
        lines.append(f"- Relation: {p['relation']}")
        lines.append(f"- Confidence: {p['confidence']}")
        lines.append(f"- Label: {p['label']}")
        lines.append("")

    Path(output_path).write_text("\n".join(lines))
