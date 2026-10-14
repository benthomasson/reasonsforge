"""Tests for lesson_plan module."""

import json
import pytest

from reasonsforge.lesson_plan import (
    build_lesson_plan,
    format_lesson_plan_markdown,
    _topological_sort,
    _classify_module,
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


def test_module_classification():
    assert _classify_module("group") == "group-theory"
    assert _classify_module("ring") == "ring-and-field-theory"
    assert _classify_module("vector-space") == "linear-algebra"
    assert _classify_module("set-theory-axiom") == "foundations"
    assert _classify_module("unknown-concept-xyz") == "other"


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
