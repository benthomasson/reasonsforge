"""Review relational beliefs for type accuracy, directionality, and grounding.

Unlike general belief review (review.py), this focuses on the structural
correctness of FOL relations: is the relation type right, is the direction
correct, and do both source beliefs actually support the claimed relationship.

Pipeline position: summarize → propose → derive → derive-relations → review-relations → verify
"""

import json
import sys

from .llm import invoke_model
from .relations import RELATION_TYPES, extract_triple, Relation

REVIEW_BATCH_SIZE = 15

REVIEW_RELATIONS_PROMPT = """\
You are auditing relational beliefs in a Truth Maintenance System (TMS).
Each belief below encodes a structural relationship between two concepts
using one of 12 FOL relation types. The relation was proposed by an earlier
LLM pass. Your job is to verify each relation is correct.

## Relation Types

| Relation | Meaning |
|----------|---------|
| **requires** | A cannot function without B (strict dependency) |
| **enables** | A makes B possible or easier (non-strict) |
| **extends** | A builds on B, adding structure |
| **is-a** | A is a specialization of B |
| **has-a** | A contains B as a component |
| **contains** | A includes B as a member/topic |
| **implements** | A is a concrete instance of B |
| **produces** | A generates or yields B |
| **causes** | A leads to or triggers B |
| **replaces** | A supersedes or substitutes for B |
| **prevents** | A blocks or conflicts with B |
| **maps-to** | A corresponds to B across domains |

## Evaluation Axes

For each relational belief, evaluate:

1. **type_correct**: Is this the right relation type? For example, if a relation
   is labeled "requires" but the dependency is not strict, it should be "enables".
   If labeled "extends" but there is no additive structure, it might be "is-a".

2. **direction_correct**: Is the direction right? "A-requires-B" means A depends
   on B. If the dependency runs the other way, the direction is wrong.

3. **grounded**: Do both source beliefs (subject and object) actually support this
   specific relationship? A relation between two real concepts is only grounded if
   the claimed structural relationship actually holds between them.

4. **redundant**: Is this relation already implied by other relations in the
   network? For example, if A-requires-B and B-requires-C, then A-requires-C
   is implied by transitivity and therefore redundant.

## Output Format

Return ONLY a JSON array. For each belief, one object:

```json
[
  {{
    "id": "belief-id",
    "type_correct": true,
    "direction_correct": true,
    "grounded": true,
    "redundant": false,
    "suggested_type": "",
    "suggested_direction": "",
    "comment": "brief explanation of most important finding"
  }}
]
```

Rules:
- Return one object per relation reviewed, in the same order as presented.
- "suggested_type" should be empty "" if type_correct is true, otherwise the
  correct relation type from the 12 types above.
- "suggested_direction" should be empty "" if direction_correct is true,
  otherwise "reverse" to indicate A and B should be swapped.
- Be rigorous: "enables" and "requires" are frequently confused. "requires"
  means A literally cannot function without B. "enables" means A makes B easier
  or possible but B could exist without A.
- Similarly, "extends" means A adds structure ON TOP of B. "is-a" means A is
  a special case of B. These differ: a ring extends a group (adds multiplication),
  but a field is-a ring (specialization, not new structure).
- Mark as redundant only if the relation is strictly implied by existing relations
  through transitivity or composition, not merely because related relations exist.
- "comment" should be a single sentence.

## Relations to Review

{relations}

## Existing Relations (for redundancy checking)

{existing_relations}"""


def format_relation_for_review(node_id, nodes):
    """Format one relational belief with its source beliefs for LLM review."""
    node = nodes.get(node_id)
    if not node:
        return ""

    triple = extract_triple(node_id)
    if not triple:
        return ""

    lines = [f"### {node_id}"]
    text = node.get("text", "") if isinstance(node, dict) else getattr(node, "text", "")
    lines.append(f"Claim: {text}")
    lines.append(f"Relation type: {triple.relation}")
    lines.append(f"Subject: {triple.subject}")
    lines.append(f"Object: {triple.object}")

    justs = node.get("justifications", []) if isinstance(node, dict) else getattr(node, "justifications", [])
    if justs:
        lines.append("Source beliefs:")
        for j in justs:
            antes = j.get("antecedents", []) if isinstance(j, dict) else getattr(j, "antecedents", [])
            for ant_id in antes:
                ant_node = nodes.get(ant_id)
                if ant_node:
                    ant_text = ant_node.get("text", "") if isinstance(ant_node, dict) else getattr(ant_node, "text", "")
                    lines.append(f"  - {ant_id}: {ant_text}")
                else:
                    lines.append(f"  - {ant_id}: (not found)")

    return "\n".join(lines)


def build_existing_relations_section(nodes):
    """Build a section listing existing relations for redundancy checking."""
    lines = []
    for nid in sorted(nodes.keys()):
        tv = nodes[nid].get("truth_value", "IN") if isinstance(nodes[nid], dict) else getattr(nodes[nid], "truth_value", "IN")
        if tv != "IN":
            continue
        triple = extract_triple(nid)
        if triple and triple.relation in RELATION_TYPES:
            lines.append(f"- {triple.subject} —[{triple.relation}]→ {triple.object}")
    return "\n".join(lines) if lines else "(None)"


def parse_review_response(response):
    """Extract review results JSON array from LLM response."""
    decoder = json.JSONDecoder()
    for i, ch in enumerate(response):
        if ch != "[":
            continue
        try:
            items, _ = decoder.raw_decode(response, i)
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        results = []
        for item in items:
            if not isinstance(item, dict) or "id" not in item:
                continue
            results.append({
                "id": item["id"],
                "type_correct": item.get("type_correct", True),
                "direction_correct": item.get("direction_correct", True),
                "grounded": item.get("grounded", True),
                "redundant": item.get("redundant", False),
                "suggested_type": item.get("suggested_type", ""),
                "suggested_direction": item.get("suggested_direction", ""),
                "comment": item.get("comment", ""),
            })
        if results:
            return results
    return []


def find_relational_beliefs(nodes, topic=None, relation_type=None):
    """Find all IN relational beliefs, optionally filtered."""
    results = []
    for nid, node in sorted(nodes.items()):
        tv = node.get("truth_value", "IN") if isinstance(node, dict) else getattr(node, "truth_value", "IN")
        if tv != "IN":
            continue
        triple = extract_triple(nid)
        if not triple or triple.relation not in RELATION_TYPES:
            continue
        if relation_type and triple.relation != relation_type:
            continue
        if topic:
            keywords = topic.lower().split()
            text = node.get("text", "") if isinstance(node, dict) else getattr(node, "text", "")
            searchable = f"{nid} {text}".lower()
            if not any(kw in searchable for kw in keywords):
                continue
        results.append(nid)
    return results


def review_relations(nodes, belief_ids=None, model="claude", timeout=300,
                     batch_size=REVIEW_BATCH_SIZE, topic=None,
                     relation_type=None, on_batch=None):
    """Review relational beliefs for correctness.

    Args:
        nodes: Dict of node_id -> node data from export_network().
        belief_ids: Optional list of specific IDs to review.
        model: LLM model to use.
        timeout: LLM timeout in seconds.
        batch_size: Number of relations per LLM call.
        topic: Optional keyword filter.
        relation_type: Optional relation type filter.
        on_batch: Optional callback after each batch.

    Returns: list of review result dicts.
    """
    if belief_ids is None:
        belief_ids = find_relational_beliefs(nodes, topic=topic,
                                             relation_type=relation_type)
    else:
        belief_ids = [nid for nid in belief_ids if nid in nodes]

    if not belief_ids:
        return []

    existing_section = build_existing_relations_section(nodes)

    all_results = []
    total_batches = (len(belief_ids) + batch_size - 1) // batch_size

    for i in range(0, len(belief_ids), batch_size):
        batch = belief_ids[i:i + batch_size]
        batch_num = i // batch_size + 1
        print(f"  Reviewing batch {batch_num}/{total_batches} "
              f"({len(batch)} relations)...", file=sys.stderr)

        relations_text = "\n\n".join(
            format_relation_for_review(nid, nodes) for nid in batch
        )
        prompt = REVIEW_RELATIONS_PROMPT.format(
            relations=relations_text,
            existing_relations=existing_section,
        )

        try:
            response = invoke_model(prompt, model=model, timeout=timeout)
            results = parse_review_response(response)
            all_results.extend(results)
            if on_batch is not None:
                on_batch(all_results)
        except Exception as e:
            print(f"  WARN: batch {batch_num} failed: {e}", file=sys.stderr)

    return all_results


def build_fixed_id(original_id, review):
    """Build a corrected belief ID from the review result.

    Handles wrong type, wrong direction, or both.
    Returns (new_id, new_text) or None if no fix is possible.
    """
    triple = extract_triple(original_id)
    if not triple:
        return None

    subject = triple.subject
    obj = triple.object
    relation = triple.relation

    if review.get("suggested_direction") == "reverse":
        subject, obj = obj, subject

    if review.get("suggested_type") and review["suggested_type"] in RELATION_TYPES:
        relation = review["suggested_type"]

    new_id = f"{subject}-{relation}-{obj}"
    if new_id == original_id:
        return None

    new_triple = Relation(subject=subject, relation=relation, object=obj)
    display_subject = subject.replace("-", " ").title()
    display_object = obj.replace("-", " ").title()
    new_text = f"{display_subject} {relation} {display_object}"

    return new_id, new_text


def fix_relations(reviews, nodes, db_path="reasons.db"):
    """Fix relations flagged with wrong type or direction.

    For each fixable review result:
    1. Retract the original belief
    2. Add a corrected belief with the suggested type/direction
    3. Preserve justification links from the original

    Returns list of (original_id, new_id, status) tuples.
    """
    from . import api

    results = []
    for r in reviews:
        has_wrong_type = not r.get("type_correct", True) and r.get("suggested_type")
        has_wrong_dir = not r.get("direction_correct", True)

        if not has_wrong_type and not has_wrong_dir:
            continue

        original_id = r["id"]
        fix = build_fixed_id(original_id, r)
        if fix is None:
            results.append((original_id, None, "no-fix-possible"))
            continue

        new_id, new_text = fix

        if new_id in nodes:
            node_data = nodes[new_id]
            tv = node_data.get("truth_value", "IN") if isinstance(node_data, dict) else getattr(node_data, "truth_value", "IN")
            if tv == "IN":
                results.append((original_id, new_id, "already-exists"))
                continue

        original_node = nodes.get(original_id, {})
        justs = original_node.get("justifications", []) if isinstance(original_node, dict) else getattr(original_node, "justifications", [])
        sl_parts = []
        for j in justs:
            antes = j.get("antecedents", []) if isinstance(j, dict) else getattr(j, "antecedents", [])
            sl_parts.extend(antes)
        sl = ",".join(sl_parts) if sl_parts else ""

        reason = f"review-relations fix: {r.get('comment', 'corrected relation')}"
        try:
            api.retract_node(original_id, reason=reason, db_path=db_path)
        except Exception as e:
            results.append((original_id, new_id, f"retract-failed: {e}"))
            continue

        try:
            api.add_node(
                node_id=new_id,
                text=new_text,
                sl=sl,
                label=f"fix:{original_id}",
                source_type="derived",
                db_path=db_path,
            )
            results.append((original_id, new_id, "fixed"))
        except Exception as e:
            results.append((original_id, new_id, f"add-failed: {e}"))

    return results
