"""Tests for the review_relations module."""

from reasonsforge.review_relations import (
    build_existing_relations_section,
    build_fixed_id,
    find_relational_beliefs,
    format_relation_for_review,
    parse_review_response,
)


SAMPLE_NODES = {
    "group-definition": {
        "truth_value": "IN",
        "text": "A group is a set with a binary operation",
        "justifications": [],
    },
    "ring-definition": {
        "truth_value": "IN",
        "text": "A ring is a group under addition with a second operation",
        "justifications": [],
    },
    "ring-requires-group": {
        "truth_value": "IN",
        "text": "Ring definition requires group structure under addition",
        "justifications": [
            {"antecedents": ["ring-definition", "group-definition"], "outlist": []}
        ],
    },
    "field-is-a-ring": {
        "truth_value": "IN",
        "text": "Every field is a ring with multiplicative inverses",
        "justifications": [
            {"antecedents": ["ring-definition"], "outlist": []}
        ],
    },
    "ring-enables-field": {
        "truth_value": "IN",
        "text": "Ring structure enables field construction",
        "justifications": [
            {"antecedents": ["ring-definition"], "outlist": []}
        ],
    },
    "retracted-relation": {
        "truth_value": "OUT",
        "text": "This was retracted",
        "justifications": [],
    },
    "plain-fact": {
        "truth_value": "IN",
        "text": "Just a regular belief",
        "justifications": [],
    },
}


def test_find_relational_beliefs_all():
    ids = find_relational_beliefs(SAMPLE_NODES)
    assert "ring-requires-group" in ids
    assert "field-is-a-ring" in ids
    assert "ring-enables-field" in ids
    assert "plain-fact" not in ids
    assert "group-definition" not in ids
    assert "retracted-relation" not in ids


def test_find_relational_beliefs_by_type():
    ids = find_relational_beliefs(SAMPLE_NODES, relation_type="requires")
    assert ids == ["ring-requires-group"]


def test_find_relational_beliefs_by_topic():
    ids = find_relational_beliefs(SAMPLE_NODES, topic="field")
    assert "field-is-a-ring" in ids
    assert "ring-enables-field" in ids
    assert "ring-requires-group" not in ids


def test_format_relation_for_review():
    text = format_relation_for_review("ring-requires-group", SAMPLE_NODES)
    assert "ring-requires-group" in text
    assert "Relation type: requires" in text
    assert "Subject: ring" in text
    assert "Object: group" in text
    assert "ring-definition" in text
    assert "group-definition" in text


def test_format_relation_for_review_missing():
    text = format_relation_for_review("nonexistent", SAMPLE_NODES)
    assert text == ""


def test_format_relation_for_review_non_relational():
    text = format_relation_for_review("plain-fact", SAMPLE_NODES)
    assert text == ""


def test_build_existing_relations_section():
    section = build_existing_relations_section(SAMPLE_NODES)
    assert "requires" in section
    assert "is-a" in section
    assert "enables" in section
    assert "ring" in section


def test_parse_review_response_valid():
    response = """Here are my findings:

```json
[
  {
    "id": "ring-requires-group",
    "type_correct": true,
    "direction_correct": true,
    "grounded": true,
    "redundant": false,
    "suggested_type": "",
    "suggested_direction": "",
    "comment": "Correct: rings require group structure under addition"
  },
  {
    "id": "field-is-a-ring",
    "type_correct": false,
    "direction_correct": true,
    "grounded": true,
    "redundant": false,
    "suggested_type": "extends",
    "suggested_direction": "",
    "comment": "Fields add multiplicative inverses, suggesting extends rather than is-a"
  }
]
```"""
    results = parse_review_response(response)
    assert len(results) == 2

    assert results[0]["id"] == "ring-requires-group"
    assert results[0]["type_correct"] is True
    assert results[0]["direction_correct"] is True
    assert results[0]["grounded"] is True
    assert results[0]["redundant"] is False

    assert results[1]["id"] == "field-is-a-ring"
    assert results[1]["type_correct"] is False
    assert results[1]["suggested_type"] == "extends"


def test_parse_review_response_empty():
    results = parse_review_response("No valid JSON here")
    assert results == []


def test_parse_review_response_missing_fields():
    response = '[{"id": "ring-requires-group"}]'
    results = parse_review_response(response)
    assert len(results) == 1
    assert results[0]["type_correct"] is True
    assert results[0]["direction_correct"] is True
    assert results[0]["grounded"] is True
    assert results[0]["redundant"] is False
    assert results[0]["suggested_type"] == ""


def test_parse_review_response_redundant():
    response = '''[{
        "id": "a-requires-c",
        "type_correct": true,
        "direction_correct": true,
        "grounded": true,
        "redundant": true,
        "suggested_type": "",
        "suggested_direction": "",
        "comment": "Implied by a-requires-b and b-requires-c transitivity"
    }]'''
    results = parse_review_response(response)
    assert len(results) == 1
    assert results[0]["redundant"] is True


def test_parse_review_response_wrong_direction():
    response = '''[{
        "id": "a-enables-b",
        "type_correct": true,
        "direction_correct": false,
        "grounded": true,
        "redundant": false,
        "suggested_type": "",
        "suggested_direction": "reverse",
        "comment": "B actually enables A, not the other way"
    }]'''
    results = parse_review_response(response)
    assert len(results) == 1
    assert results[0]["direction_correct"] is False
    assert results[0]["suggested_direction"] == "reverse"


def test_build_fixed_id_wrong_type():
    review = {
        "id": "field-is-a-ring",
        "type_correct": False,
        "suggested_type": "extends",
        "direction_correct": True,
        "suggested_direction": "",
    }
    result = build_fixed_id("field-is-a-ring", review)
    assert result is not None
    new_id, new_text = result
    assert new_id == "field-extends-ring"
    assert "Field" in new_text
    assert "extends" in new_text
    assert "Ring" in new_text


def test_build_fixed_id_wrong_direction():
    review = {
        "id": "a-enables-b",
        "type_correct": True,
        "suggested_type": "",
        "direction_correct": False,
        "suggested_direction": "reverse",
    }
    result = build_fixed_id("a-enables-b", review)
    assert result is not None
    new_id, new_text = result
    assert new_id == "b-enables-a"


def test_build_fixed_id_both_wrong():
    review = {
        "id": "x-requires-y",
        "type_correct": False,
        "suggested_type": "enables",
        "direction_correct": False,
        "suggested_direction": "reverse",
    }
    result = build_fixed_id("x-requires-y", review)
    assert result is not None
    new_id, new_text = result
    assert new_id == "y-enables-x"


def test_build_fixed_id_no_change():
    review = {
        "id": "ring-requires-group",
        "type_correct": True,
        "suggested_type": "",
        "direction_correct": True,
        "suggested_direction": "",
    }
    result = build_fixed_id("ring-requires-group", review)
    assert result is None


def test_build_fixed_id_non_relational():
    review = {
        "id": "plain-fact",
        "type_correct": False,
        "suggested_type": "enables",
    }
    result = build_fixed_id("plain-fact", review)
    assert result is None


def test_build_fixed_id_invalid_suggested_type():
    review = {
        "id": "a-requires-b",
        "type_correct": False,
        "suggested_type": "not-a-real-type",
        "direction_correct": True,
        "suggested_direction": "",
    }
    result = build_fixed_id("a-requires-b", review)
    assert result is None
