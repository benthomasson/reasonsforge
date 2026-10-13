"""Tests for the derive_relations module."""

from reasonsforge.derive_relations import (
    _build_justification_graph,
    _filter_existing_relations,
    _pairs_by_justification,
    _sample_pairs,
    build_existing_relations_section,
    build_pairs_section,
    build_prompt,
    parse_relation_proposals,
    validate_proposals,
)


SAMPLE_NODES = {
    "group-definition": {"truth_value": "IN", "text": "A group is a set with a binary operation"},
    "ring-definition": {"truth_value": "IN", "text": "A ring is a group under addition with a second operation"},
    "field-is-a-ring": {"truth_value": "IN", "text": "Every field is a ring with multiplicative inverses"},
    "lagrange-theorem": {"truth_value": "IN", "text": "The order of a subgroup divides the order of the group"},
    "integers-implement-ring-axioms": {"truth_value": "IN", "text": "The integers form a ring under addition and multiplication"},
    "normal-subgroup": {"truth_value": "IN", "text": "A normal subgroup is invariant under conjugation"},
}


def test_sample_pairs_random():
    ids = ["a", "b", "c", "d"]
    pairs = _sample_pairs(ids, budget=3, seed=42, strategy="random")
    assert len(pairs) == 3
    for a, b in pairs:
        assert a in ids and b in ids
        assert a != b


def test_sample_pairs_all_small():
    ids = ["a", "b", "c"]
    pairs = _sample_pairs(ids, budget=10, strategy="all")
    assert len(pairs) == 3  # C(3,2) = 3


def test_sample_pairs_prefix():
    ids = ["group-definition", "group-theory", "ring-definition", "ring-theory"]
    pairs = _sample_pairs(ids, budget=20, seed=42, strategy="prefix")
    assert len(pairs) > 0
    has_intra = any(a.startswith("group") and b.startswith("group") for a, b in pairs)
    has_cross = any(
        (a.startswith("group") and b.startswith("ring")) or
        (a.startswith("ring") and b.startswith("group"))
        for a, b in pairs
    )
    assert has_intra
    assert has_cross


def test_filter_existing_relations():
    pairs = [
        ("group-definition", "ring-definition"),
        ("field", "ring"),
        ("lagrange-theorem", "normal-subgroup"),
    ]
    nodes = {
        "field-is-a-ring": {"truth_value": "IN"},
        "group-definition": {"truth_value": "IN"},
    }
    filtered = _filter_existing_relations(pairs, nodes)
    assert ("field", "ring") not in filtered
    assert ("group-definition", "ring-definition") in filtered


def test_build_pairs_section():
    pairs = [("group-definition", "ring-definition")]
    section = build_pairs_section(pairs, SAMPLE_NODES)
    assert "### Pair 1" in section
    assert "group-definition" in section
    assert "ring-definition" in section
    assert "A group is a set" in section


def test_build_existing_relations_section():
    section = build_existing_relations_section(SAMPLE_NODES)
    assert "field-is-a-ring" in section
    assert "is-a" in section
    assert "implements" in section or "field" in section


def test_build_prompt_includes_pairs():
    pairs = [("group-definition", "ring-definition")]
    prompt = build_prompt(SAMPLE_NODES, pairs, domain="Abstract Algebra")
    assert "Abstract Algebra" in prompt
    assert "group-definition" in prompt
    assert "ring-definition" in prompt
    assert "Lesson Planning" in prompt


def test_build_prompt_no_use_case():
    pairs = [("group-definition", "ring-definition")]
    prompt = build_prompt(SAMPLE_NODES, pairs, use_case=None)
    assert "Lesson Planning" not in prompt


def test_parse_relation_proposals():
    response = """Here are the proposed relations:

### RELATION ring-requires-group
A ring requires group structure under addition as its foundation
- Subject: ring-definition
- Object: group-definition
- Relation: requires
- Confidence: HIGH
- Label: Ring definition explicitly builds on group axioms

### RELATION ring-extends-group
Ring theory extends group theory by adding a second operation
- Subject: ring-definition
- Object: group-definition
- Relation: extends
- Confidence: MEDIUM
- Label: Rings add multiplicative structure to groups

### RELATION integers-implements-ring
The integers are a concrete instance of ring axioms
- Subject: integers-implement-ring-axioms
- Object: ring-definition
- Relation: implements
- Confidence: HIGH
- Label: Integers satisfy all ring axioms
"""
    proposals = parse_relation_proposals(response)
    assert len(proposals) == 3

    assert proposals[0]["id"] == "ring-requires-group"
    assert proposals[0]["subject_belief"] == "ring-definition"
    assert proposals[0]["object_belief"] == "group-definition"
    assert proposals[0]["relation"] == "requires"
    assert proposals[0]["confidence"] == "HIGH"

    assert proposals[1]["relation"] == "extends"
    assert proposals[1]["confidence"] == "MEDIUM"

    assert proposals[2]["relation"] == "implements"


def test_parse_relation_proposals_invalid_relation():
    response = """### RELATION a-loves-b
A loves B
- Subject: a
- Object: b
- Relation: loves
- Confidence: HIGH
- Label: not a valid relation type
"""
    proposals = parse_relation_proposals(response)
    assert len(proposals) == 0


def test_parse_relation_proposals_bad_name():
    response = """### RELATION just-a-name
Some text
- Subject: a
- Object: b
- Relation: requires
- Confidence: HIGH
- Label: name doesn't encode relation
"""
    proposals = parse_relation_proposals(response)
    assert len(proposals) == 0


def test_validate_proposals_valid():
    proposals = [{
        "id": "ring-requires-group",
        "text": "Ring requires group",
        "subject_belief": "ring-definition",
        "object_belief": "group-definition",
        "relation": "requires",
        "confidence": "HIGH",
        "label": "test",
    }]
    valid, skipped = validate_proposals(proposals, SAMPLE_NODES)
    assert len(valid) == 1
    assert len(skipped) == 0


def test_validate_proposals_missing_subject():
    proposals = [{
        "id": "x-requires-group",
        "text": "X requires group",
        "subject_belief": "nonexistent",
        "object_belief": "group-definition",
        "relation": "requires",
        "confidence": "HIGH",
        "label": "test",
    }]
    valid, skipped = validate_proposals(proposals, SAMPLE_NODES)
    assert len(valid) == 0
    assert "subject not found" in skipped[0][1]


def test_validate_proposals_missing_object():
    proposals = [{
        "id": "group-requires-x",
        "text": "Group requires X",
        "subject_belief": "group-definition",
        "object_belief": "nonexistent",
        "relation": "requires",
        "confidence": "HIGH",
        "label": "test",
    }]
    valid, skipped = validate_proposals(proposals, SAMPLE_NODES)
    assert len(valid) == 0
    assert "object not found" in skipped[0][1]


def test_validate_proposals_already_exists():
    proposals = [{
        "id": "field-is-a-ring",
        "text": "Field is a ring",
        "subject_belief": "group-definition",
        "object_belief": "ring-definition",
        "relation": "is-a",
        "confidence": "HIGH",
        "label": "test",
    }]
    valid, skipped = validate_proposals(proposals, SAMPLE_NODES)
    assert len(valid) == 0
    assert "already exists" in skipped[0][1]


def test_validate_proposals_deduplicates():
    p = {
        "id": "ring-requires-group",
        "text": "Ring requires group",
        "subject_belief": "ring-definition",
        "object_belief": "group-definition",
        "relation": "requires",
        "confidence": "HIGH",
        "label": "test",
    }
    valid, skipped = validate_proposals([p, p.copy()], SAMPLE_NODES)
    assert len(valid) == 1
    assert len(skipped) == 1
    assert "duplicate" in skipped[0][1]


# --- Justification proximity tests ---

JUSTIFICATION_NODES = {
    "group-definition": {
        "truth_value": "IN", "text": "A group is a set with a binary operation",
        "justifications": [],
    },
    "ring-definition": {
        "truth_value": "IN", "text": "A ring is a group under addition",
        "justifications": [
            {"antecedents": ["group-definition"], "outlist": []}
        ],
    },
    "field-definition": {
        "truth_value": "IN", "text": "A field is a commutative ring with inverses",
        "justifications": [
            {"antecedents": ["ring-definition"], "outlist": []}
        ],
    },
    "integers-are-ring": {
        "truth_value": "IN", "text": "The integers form a ring",
        "justifications": [
            {"antecedents": ["ring-definition"], "outlist": []}
        ],
    },
    "unrelated-premise": {
        "truth_value": "IN", "text": "Something unrelated",
        "justifications": [],
    },
}


def test_build_justification_graph_direct_links():
    adj = _build_justification_graph(JUSTIFICATION_NODES)
    assert "group-definition" in adj["ring-definition"]
    assert "ring-definition" in adj["group-definition"]


def test_build_justification_graph_siblings():
    """Beliefs sharing an antecedent should be adjacent."""
    adj = _build_justification_graph(JUSTIFICATION_NODES)
    assert "integers-are-ring" in adj["field-definition"]
    assert "field-definition" in adj["integers-are-ring"]


def test_build_justification_graph_no_spurious():
    """Unrelated premise should not be connected."""
    adj = _build_justification_graph(JUSTIFICATION_NODES)
    assert "unrelated-premise" not in adj


def test_pairs_by_justification_finds_neighbors():
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=100, max_hops=2)
    pair_set = set(pairs)
    assert ("group-definition", "ring-definition") in pair_set
    assert ("field-definition", "ring-definition") in pair_set


def test_pairs_by_justification_finds_siblings():
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=100, max_hops=1)
    pair_set = set(pairs)
    assert ("field-definition", "integers-are-ring") in pair_set


def test_pairs_by_justification_excludes_unrelated():
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=100, max_hops=2)
    for a, b in pairs:
        assert a != "unrelated-premise" and b != "unrelated-premise"


def test_pairs_by_justification_respects_budget():
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=2, max_hops=2, seed=42)
    assert len(pairs) <= 2


def test_pairs_by_justification_2hop():
    """group-definition and field-definition are 2 hops apart."""
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=100, max_hops=2)
    pair_set = set(pairs)
    assert ("field-definition", "group-definition") in pair_set


def test_pairs_by_justification_1hop_no_2hop():
    """With max_hops=1, should not find 2-hop pairs."""
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=100, max_hops=1)
    pair_set = set(pairs)
    assert ("field-definition", "group-definition") not in pair_set


def test_pairs_by_justification_allowed_ids():
    """Only pairs beliefs in allowed_ids, even though graph walks through others."""
    allowed = {"ring-definition", "field-definition"}
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=100, max_hops=2,
                                     allowed_ids=allowed)
    for a, b in pairs:
        assert a in allowed and b in allowed


def test_pairs_by_justification_allowed_ids_uses_full_graph():
    """Allowed filter restricts output but walks the full graph for proximity."""
    allowed = {"group-definition", "field-definition"}
    pairs = _pairs_by_justification(JUSTIFICATION_NODES, budget=100, max_hops=2,
                                     allowed_ids=allowed)
    pair_set = set(pairs)
    assert ("field-definition", "group-definition") in pair_set


def test_sample_pairs_justification_strategy():
    pairs = _sample_pairs(
        list(JUSTIFICATION_NODES.keys()), budget=10, strategy="justification",
        nodes=JUSTIFICATION_NODES,
    )
    assert len(pairs) > 0
    for a, b in pairs:
        assert a != "unrelated-premise" and b != "unrelated-premise"
