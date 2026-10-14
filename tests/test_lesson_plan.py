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
