"""Tests for the relations module."""

from reasonsforge.relations import (
    Relation,
    apply_relations_to_network,
    build_subject_index,
    ensure_relations,
    extract_all_relations,
    extract_triple,
    find_prevention_conflicts,
    find_unmet_requirements,
    get_relations,
    has_stored_relations,
    relation_stats,
    store_relation_metadata,
    transitive_closure,
)


class FakeNode:
    def __init__(self, id, truth_value="IN", metadata=None):
        self.id = id
        self.truth_value = truth_value
        self.text = f"Text for {id}"
        self.metadata = metadata or {}


class FakeNetwork:
    def __init__(self, nodes_dict):
        self.nodes = nodes_dict


def test_extract_triple_requires():
    r = extract_triple("ring-requires-group")
    assert r == Relation("ring", "requires", "group")


def test_extract_triple_enables():
    r = extract_triple("outlist-enables-non-monotonic-reasoning")
    assert r == Relation("outlist", "enables", "non-monotonic-reasoning")


def test_extract_triple_is_a():
    r = extract_triple("field-is-a-ring")
    assert r == Relation("field", "is-a", "ring")


def test_extract_triple_maps_to():
    r = extract_triple("group-homomorphism-maps-to-ring-homomorphism")
    assert r == Relation("group-homomorphism", "maps-to", "ring-homomorphism")


def test_extract_triple_no_match():
    assert extract_triple("plain-fact-about-something") is None


def test_extract_triple_no_empty_subject():
    assert extract_triple("requires-something") is None


def test_extract_triple_no_empty_object():
    assert extract_triple("something-requires") is None


def test_extract_triple_is_a_before_is():
    """is-a (longer) should match before is (shorter)."""
    r = extract_triple("field-is-a-ring")
    assert r.relation == "is-a"
    assert r.subject == "field"
    assert r.object == "ring"


def test_extract_triple_is_not_before_is():
    r = extract_triple("mutable-state-is-not-thread-safe")
    assert r.relation == "is-not"


def test_extract_all_relations():
    nodes = {
        "a-requires-b": {"truth_value": "IN"},
        "c-enables-d": {"truth_value": "IN"},
        "out-node-requires-x": {"truth_value": "OUT"},
        "plain-belief": {"truth_value": "IN"},
    }
    results = extract_all_relations(nodes)
    assert len(results) == 2
    ids = [bid for bid, _ in results]
    assert "a-requires-b" in ids
    assert "c-enables-d" in ids


def test_extract_all_relations_filter():
    nodes = {
        "a-requires-b": {"truth_value": "IN"},
        "c-enables-d": {"truth_value": "IN"},
    }
    results = extract_all_relations(nodes, relation_filter="requires")
    assert len(results) == 1
    assert results[0][1].relation == "requires"


def test_store_relation_metadata_deduplicates():
    meta = {}
    rel = Relation("a", "requires", "b")
    store_relation_metadata(meta, rel, "outgoing")
    store_relation_metadata(meta, rel, "outgoing")
    assert len(meta["relations"]) == 1


def test_get_relations_filters():
    meta = {
        "relations": [
            {"relation": "requires", "target": "b", "direction": "outgoing"},
            {"relation": "enables", "target": "c", "direction": "outgoing"},
            {"relation": "requires", "target": "a", "direction": "incoming"},
        ]
    }
    assert len(get_relations(meta)) == 3
    assert len(get_relations(meta, relation_type="requires")) == 2
    assert len(get_relations(meta, direction="outgoing")) == 2
    assert len(get_relations(meta, relation_type="requires", direction="outgoing")) == 1


def test_apply_relations_no_double_store():
    """Outgoing relation should only be stored on the relational belief node,
    not duplicated on the subject node."""
    network = FakeNetwork({
        "a": FakeNode("a"),
        "a-requires-b": FakeNode("a-requires-b"),
        "b": FakeNode("b"),
    })
    apply_relations_to_network(network)
    a_rels = get_relations(network.nodes["a"].metadata, direction="outgoing")
    assert len(a_rels) == 0
    rel_rels = get_relations(network.nodes["a-requires-b"].metadata, direction="outgoing")
    assert len(rel_rels) == 1
    b_rels = get_relations(network.nodes["b"].metadata, direction="incoming")
    assert len(b_rels) == 1


def test_has_stored_relations():
    network = FakeNetwork({"a": FakeNode("a")})
    assert not has_stored_relations(network)
    network.nodes["a"].metadata["relations"] = [{"relation": "requires", "target": "b", "direction": "outgoing"}]
    assert has_stored_relations(network)


def test_ensure_relations_skips_if_stored():
    network = FakeNetwork({
        "a-requires-b": FakeNode("a-requires-b", metadata={
            "relations": [{"relation": "requires", "target": "b", "direction": "outgoing"}]
        }),
    })
    ensure_relations(network)
    assert len(network.nodes["a-requires-b"].metadata["relations"]) == 1


def test_transitive_closure():
    network = FakeNetwork({
        "a-requires-b": FakeNode("a-requires-b"),
        "b-requires-c": FakeNode("b-requires-c"),
        "c": FakeNode("c"),
    })
    apply_relations_to_network(network)
    chain = transitive_closure(network, "a", "requires")
    assert "b" in chain
    assert "c" in chain


def test_find_unmet_requirements():
    network = FakeNetwork({
        "a-requires-b": FakeNode("a-requires-b"),
        "b": FakeNode("b", truth_value="OUT"),
    })
    apply_relations_to_network(network)
    unmet = find_unmet_requirements(network)
    assert len(unmet) == 1
    assert unmet[0][2] == "b"


def test_find_prevention_conflicts():
    network = FakeNetwork({
        "a-prevents-b": FakeNode("a-prevents-b"),
        "a": FakeNode("a"),
        "b": FakeNode("b"),
    })
    apply_relations_to_network(network)
    conflicts = find_prevention_conflicts(network)
    assert len(conflicts) == 1


def test_relation_stats():
    network = FakeNetwork({
        "a-requires-b": FakeNode("a-requires-b"),
        "c-enables-d": FakeNode("c-enables-d"),
        "plain": FakeNode("plain"),
    })
    apply_relations_to_network(network)
    stats = relation_stats(network)
    assert stats["total_relations"] == 2
    assert stats["nodes_with_relations"] == 2
    assert stats["total_nodes"] == 3
    assert stats["by_type"]["requires"] == 1
    assert stats["by_type"]["enables"] == 1
