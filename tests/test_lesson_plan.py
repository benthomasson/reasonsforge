"""Tests for lesson_plan module."""

import json
import pytest

from reasonsforge.lesson_plan import (
    build_lesson_plan,
    build_lesson_prompt,
    select_beliefs_for_lesson,
    format_lesson_plan_markdown,
    _topological_sort,
    _classify_modules,
    _parse_lesson_response,
    _identify_standalone_lessons,
    _build_consolidation_prompt,
    _parse_consolidation_response,
    consolidate_lessons,
    format_consolidated_markdown,
)


NODES = {
    "group": {
        "text": "A group is a set with a binary operation satisfying closure, associativity, identity, and inverses.",
        "truth_value": "IN",
        "metadata": {},
    },
    "ring": {
        "text": "A ring is an abelian group under addition with a second associative operation.",
        "truth_value": "IN",
        "metadata": {},
    },
    "field": {
        "text": "A field is a ring where every nonzero element has a multiplicative inverse.",
        "truth_value": "IN",
        "metadata": {},
    },
    "ring-requires-group": {
        "text": "Ring requires Group",
        "truth_value": "IN",
        "metadata": {},
    },
    "field-requires-ring": {
        "text": "Field requires Ring",
        "truth_value": "IN",
        "metadata": {},
    },
    "field-extends-ring": {
        "text": "Field extends Ring with multiplicative inverses",
        "truth_value": "IN",
        "metadata": {},
    },
    "group-enables-ring": {
        "text": "Group enables Ring",
        "truth_value": "IN",
        "metadata": {},
    },
    "vector-space": {
        "text": "A vector space is a set of vectors with field scalars.",
        "truth_value": "IN",
        "metadata": {},
    },
    "vector-space-requires-field": {
        "text": "Vector space requires Field",
        "truth_value": "IN",
        "metadata": {},
    },
    "ring-contains-abelian-group": {
        "text": "Ring contains abelian group under addition",
        "truth_value": "IN",
        "metadata": {},
    },
    "kernel-is-a-subspace": {
        "text": "Kernel is a subspace",
        "truth_value": "IN",
        "metadata": {},
    },
    "gaussian-implements-row-reduction": {
        "text": "Gaussian elimination implements row reduction",
        "truth_value": "IN",
        "metadata": {},
    },
    "symmetry": {
        "text": "Symmetry is invariance under transformation.",
        "truth_value": "IN",
        "metadata": {},
    },
    "group-maps-to-symmetry": {
        "text": "Group maps to symmetry in physics",
        "truth_value": "IN",
        "metadata": {},
    },
    # OUT beliefs should be ignored
    "retracted-requires-something": {
        "text": "Should be ignored",
        "truth_value": "OUT",
        "metadata": {},
    },
}


def test_build_lesson_plan_basic():
    plan = build_lesson_plan(NODES)
    assert plan["stats"]["total_lessons"] > 0
    assert plan["stats"]["total_modules"] > 0
    assert plan["stats"]["total_relations"] > 0
    assert len(plan["lessons"]) == plan["stats"]["total_lessons"]


def test_topological_order():
    plan = build_lesson_plan(NODES)
    lessons = plan["lessons"]
    concept_order = {l["concept"]: l["number"] for l in lessons}

    assert concept_order["group"] < concept_order["ring"]
    assert concept_order["ring"] < concept_order["field"]
    assert concept_order["field"] < concept_order["vector-space"]


def test_prerequisites_populated():
    plan = build_lesson_plan(NODES)
    lessons_by_concept = {l["concept"]: l for l in plan["lessons"]}
    assert "group" in lessons_by_concept["ring"]["prerequisites"]
    assert "ring" in lessons_by_concept["field"]["prerequisites"]
    assert "field" in lessons_by_concept["vector-space"]["prerequisites"]


def test_motivation_from_enables():
    plan = build_lesson_plan(NODES)
    lessons_by_concept = {l["concept"]: l for l in plan["lessons"]}
    group_lesson = lessons_by_concept["group"]
    assert group_lesson["motivation"] is not None
    assert "ring" in group_lesson["motivation"].lower()


def test_cross_references():
    plan = build_lesson_plan(NODES)
    lessons_by_concept = {l["concept"]: l for l in plan["lessons"]}
    group_lesson = lessons_by_concept["group"]
    assert "symmetry" in group_lesson["cross_references"]


def test_out_beliefs_ignored():
    plan = build_lesson_plan(NODES)
    concepts = {l["concept"] for l in plan["lessons"]}
    assert "retracted" not in concepts
    assert "something" not in concepts


def test_module_classification_from_graph():
    from reasonsforge.relations import extract_all_relations
    triples = extract_all_relations(NODES)
    concepts = set()
    for _, rel in triples:
        concepts.add(rel.subject)
        concepts.add(rel.object)
    modules = _classify_modules(concepts, triples)
    assert modules["ring"] == modules["group"]
    assert modules["field"] == modules["ring"]
    assert "symmetry" in modules


def test_module_classification_contains():
    from reasonsforge.relations import Relation
    concepts = {"parent", "child1", "child2", "isolated"}
    triples = [
        ("parent-contains-child1", Relation("parent", "contains", "child1")),
        ("parent-contains-child2", Relation("parent", "contains", "child2")),
    ]
    modules = _classify_modules(concepts, triples)
    assert modules["child1"] == "parent"
    assert modules["child2"] == "parent"
    assert modules["isolated"] == "other"


def test_topological_sort_no_cycles():
    concepts = {"a", "b", "c", "d"}
    prereqs = {"b": {"a"}, "c": {"b"}, "d": {"c"}}
    order = _topological_sort(concepts, prereqs)
    assert order == ["a", "b", "c", "d"]


def test_topological_sort_handles_cycle():
    concepts = {"a", "b", "c"}
    prereqs = {"a": {"c"}, "b": {"a"}, "c": {"b"}}
    order = _topological_sort(concepts, prereqs)
    assert set(order) == concepts


def test_topological_sort_independent():
    concepts = {"x", "y", "z"}
    prereqs = {}
    order = _topological_sort(concepts, prereqs)
    assert order == ["x", "y", "z"]


def test_format_markdown():
    plan = build_lesson_plan(NODES)
    md = format_lesson_plan_markdown(plan, title="Test Plan")
    assert "# Test Plan" in md
    assert "Module:" in md
    assert "Lesson" in md
    assert "Prerequisites" in md


def test_format_markdown_default_title():
    plan = build_lesson_plan(NODES)
    md = format_lesson_plan_markdown(plan)
    assert "# Lesson Plan" in md


def test_empty_domain():
    plan = build_lesson_plan({})
    assert plan["stats"]["total_lessons"] == 0
    assert plan["stats"]["total_modules"] == 0
    assert plan["lessons"] == []


def test_no_relational_beliefs():
    nodes = {
        "plain-fact": {
            "text": "Just a fact",
            "truth_value": "IN",
            "metadata": {},
        },
    }
    plan = build_lesson_plan(nodes)
    assert plan["stats"]["total_lessons"] == 0


def test_json_output_serializable():
    plan = build_lesson_plan(NODES)
    serialized = json.dumps(plan)
    deserialized = json.loads(serialized)
    assert deserialized["stats"]["total_lessons"] == plan["stats"]["total_lessons"]


# --- Lesson generation tests ---


def test_build_lesson_prompt_basic():
    lesson = {
        "concept": "field",
        "prerequisites": ["ring", "group"],
        "motivation": "Unlocks: vector-space",
        "examples": ["rational-numbers"],
        "cross_references": ["linear-algebra-scalars"],
        "number": 3,
        "module": "ring-and-field-theory",
    }
    beliefs = [
        {"id": "field", "text": "A field is a ring with multiplicative inverses."},
    ]
    prompt = build_lesson_prompt(lesson, beliefs, domain="algebra")
    assert "Field" in prompt
    assert "algebra" in prompt
    assert "Ring" in prompt
    assert "Group" in prompt
    assert "vector-space" in prompt.lower() or "Unlocks" in prompt
    assert "Rational Numbers" in prompt
    assert "Linear Algebra Scalars" in prompt
    assert "[field]" in prompt


def test_build_lesson_prompt_with_completed():
    lesson = {
        "concept": "field",
        "prerequisites": ["ring", "group"],
        "motivation": None,
        "examples": [],
        "cross_references": [],
        "number": 3,
        "module": "ring-and-field-theory",
    }
    beliefs = [{"id": "field", "text": "A field."}]
    prompt = build_lesson_prompt(lesson, beliefs, completed=["group"])
    assert "already learned" in prompt.lower()
    assert "Group" in prompt


def test_build_lesson_prompt_no_prereqs():
    lesson = {
        "concept": "set-theory",
        "prerequisites": [],
        "motivation": None,
        "examples": [],
        "cross_references": [],
        "number": 1,
        "module": "foundations",
    }
    beliefs = [{"id": "set-theory", "text": "Set theory."}]
    prompt = build_lesson_prompt(lesson, beliefs)
    assert "Prerequisites" not in prompt
    assert "already learned" not in prompt.lower()


def test_select_beliefs_for_lesson():
    plan = build_lesson_plan(NODES)
    lessons_by_concept = {l["concept"]: l for l in plan["lessons"]}
    ring_lesson = lessons_by_concept["ring"]
    beliefs = select_beliefs_for_lesson(ring_lesson, NODES)
    ids = [b["id"] for b in beliefs]
    assert "ring" in ids


def test_select_beliefs_respects_max():
    plan = build_lesson_plan(NODES)
    lessons_by_concept = {l["concept"]: l for l in plan["lessons"]}
    group_lesson = lessons_by_concept["group"]
    beliefs = select_beliefs_for_lesson(group_lesson, NODES, max_beliefs=2)
    assert len(beliefs) <= 2


def test_select_beliefs_ignores_out():
    plan = build_lesson_plan(NODES)
    lesson = {"concept": "retracted", "number": 1, "module": "other",
              "prerequisites": [], "motivation": None, "examples": [],
              "cross_references": []}
    beliefs = select_beliefs_for_lesson(lesson, NODES)
    ids = [b["id"] for b in beliefs]
    assert "retracted-requires-something" not in ids


def test_parse_lesson_response():
    content = """TITLE: Introduction to Fields
A field is a ring where every nonzero element has an inverse.

This is important because it lets us do division.

Fields are everywhere in mathematics.

BELIEFS: field, ring-requires-group, field-requires-ring"""
    parsed = _parse_lesson_response(content)
    assert parsed["title"] == "Introduction to Fields"
    assert "ring where every" in parsed["body"]
    assert "field" in parsed["beliefs"]
    assert "ring-requires-group" in parsed["beliefs"]


def test_parse_lesson_response_no_markers():
    content = "Just some text without markers."
    parsed = _parse_lesson_response(content)
    assert parsed["body"] == content
    assert parsed["title"] == ""
    assert parsed["beliefs"] == []


def test_generate_lesson_with_mock():
    from reasonsforge.lesson_plan import generate_lesson
    plan = build_lesson_plan(NODES)
    lessons_by_concept = {l["concept"]: l for l in plan["lessons"]}
    ring_lesson = lessons_by_concept["ring"]

    mock_response = """TITLE: Understanding Rings
A ring builds on the group concept.

Rings add a second operation.

BELIEFS: ring, ring-requires-group"""

    def mock_invoke(prompt, model):
        return mock_response

    result = generate_lesson(
        ring_lesson, NODES, invoke_fn=mock_invoke,
    )
    assert result is not None
    assert result["title"] == "Understanding Rings"
    assert result["concept"] == "ring"
    assert result["module"] == ring_lesson["module"]
    assert "ring" in result["beliefs"]


# --- Consolidation tests ---

STANDALONE_NODES = {
    # Connected concepts (via requires/enables)
    "alpha": {"text": "Alpha concept", "truth_value": "IN", "metadata": {}},
    "beta": {"text": "Beta concept", "truth_value": "IN", "metadata": {}},
    "gamma": {"text": "Gamma concept", "truth_value": "IN", "metadata": {}},
    "delta": {"text": "Delta concept", "truth_value": "IN", "metadata": {}},
    "alpha-requires-beta": {"text": "Alpha requires Beta", "truth_value": "IN", "metadata": {}},
    "gamma-enables-delta": {"text": "Gamma enables Delta", "truth_value": "IN", "metadata": {}},
    # Standalone concepts (only connected via contains — no lesson fields)
    "topics": {"text": "Topics", "truth_value": "IN", "metadata": {}},
    "topic-a": {"text": "Topic A", "truth_value": "IN", "metadata": {}},
    "topic-b": {"text": "Topic B", "truth_value": "IN", "metadata": {}},
    "topic-c": {"text": "Topic C", "truth_value": "IN", "metadata": {}},
    "topics-contains-topic-a": {"text": "Topics contains Topic A", "truth_value": "IN", "metadata": {}},
    "topics-contains-topic-b": {"text": "Topics contains Topic B", "truth_value": "IN", "metadata": {}},
    "topics-contains-topic-c": {"text": "Topics contains Topic C", "truth_value": "IN", "metadata": {}},
}


def test_identify_standalone_lessons():
    plan = build_lesson_plan(STANDALONE_NODES)
    connected, standalone = _identify_standalone_lessons(plan)
    connected_concepts = {l["concept"] for l in connected}
    standalone_concepts = {l["concept"] for l in standalone}
    assert "alpha" in connected_concepts
    assert "beta" in connected_concepts
    assert "gamma" in connected_concepts
    assert "delta" in connected_concepts
    assert len(standalone) > 0
    for l in standalone:
        assert l["concept"] in standalone_concepts


def test_identify_standalone_all_connected():
    plan = build_lesson_plan(NODES)
    connected, standalone = _identify_standalone_lessons(plan)
    assert len(connected) > 0
    assert len(connected) + len(standalone) == len(plan["lessons"])


def test_build_consolidation_prompt():
    concepts = ["alpha", "beta", "gamma"]
    prompt = _build_consolidation_prompt(concepts)
    assert "3 standalone concepts" in prompt
    assert "alpha" in prompt
    assert "beta" in prompt
    assert "JSON" in prompt


def test_parse_consolidation_response_valid():
    response = '''[
        {"title": "Greek Letters", "concepts": ["alpha", "beta"], "rationale": "Both Greek"},
        {"title": "More Greek", "concepts": ["gamma"], "rationale": "Also Greek"}
    ]'''
    groups = _parse_consolidation_response(response)
    assert len(groups) == 2
    assert groups[0]["title"] == "Greek Letters"
    assert groups[0]["concepts"] == ["alpha", "beta"]
    assert groups[1]["concepts"] == ["gamma"]


def test_parse_consolidation_response_with_preamble():
    response = '''Here are the groups:
    ```json
    [{"title": "Group A", "concepts": ["x", "y"], "rationale": "Related"}]
    ```'''
    groups = _parse_consolidation_response(response)
    assert len(groups) == 1
    assert groups[0]["concepts"] == ["x", "y"]


def test_parse_consolidation_response_invalid():
    groups = _parse_consolidation_response("Not valid JSON at all")
    assert groups == []


def test_consolidate_lessons_with_mock():
    plan = build_lesson_plan(STANDALONE_NODES)
    _, standalone = _identify_standalone_lessons(plan)
    standalone_concepts = [l["concept"] for l in standalone]

    mock_response = '[{"title": "Misc Topics", "concepts": '
    import json as _json
    mock_response += _json.dumps(standalone_concepts)
    mock_response += ', "rationale": "Grouped standalone concepts"}]'

    def mock_invoke(prompt, model):
        return mock_response

    consolidated = consolidate_lessons(plan, invoke_fn=mock_invoke)
    assert consolidated["stats"]["total_lessons"] <= plan["stats"]["total_lessons"]
    assert "consolidated_from" in consolidated["stats"]

    concept_or_subtopic = set()
    for l in consolidated["lessons"]:
        concept_or_subtopic.add(l["concept"])
        concept_or_subtopic.update(l.get("subtopics", []))
    for c in standalone_concepts:
        assert c in concept_or_subtopic


def test_consolidate_no_standalone():
    plan = build_lesson_plan(NODES)
    connected, standalone = _identify_standalone_lessons(plan)
    if not standalone:
        result = consolidate_lessons(plan, invoke_fn=lambda p, m: "[]")
        assert result is plan


def test_format_consolidated_markdown():
    plan = build_lesson_plan(STANDALONE_NODES)
    _, standalone = _identify_standalone_lessons(plan)
    standalone_concepts = [l["concept"] for l in standalone]

    import json as _json
    mock_response = '[{"title": "Misc Topics", "concepts": '
    mock_response += _json.dumps(standalone_concepts)
    mock_response += ', "rationale": "Standalone concepts"}]'

    consolidated = consolidate_lessons(plan, invoke_fn=lambda p, m: mock_response)
    md = format_consolidated_markdown(consolidated, title="Test Consolidated")
    assert "# Test Consolidated" in md
    assert "Covers" in md or "Misc Topics" in md
