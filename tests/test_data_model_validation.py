"""Runtime validation for core data-model invariants."""

import pytest

from reasonsforge import Justification, Node
from reasonsforge.network import Network


def test_node_rejects_invalid_truth_value():
    with pytest.raises(ValueError, match="truth value"):
        Node(id="n", text="node", truth_value="MAYBE")


def test_node_rejects_unhashable_truth_value():
    with pytest.raises(ValueError, match="truth value"):
        Node(id="n", text="node", truth_value=[])


def test_justification_rejects_invalid_type():
    with pytest.raises(ValueError, match="justification type"):
        Justification(type="BAD")


def test_justification_rejects_unhashable_type():
    with pytest.raises(ValueError, match="justification type"):
        Justification(type=[])


def test_justification_rejects_empty_reference_ids():
    with pytest.raises(ValueError, match="antecedents"):
        Justification(type="SL", antecedents=[""])


def test_justification_rejects_invalid_reference_containers_and_ids():
    with pytest.raises(ValueError, match="antecedents"):
        Justification(type="SL", antecedents="node")
    with pytest.raises(ValueError, match="outlist"):
        Justification(type="SL", outlist=("node",))
    with pytest.raises(ValueError, match="antecedents"):
        Justification(type="SL", antecedents=[1])
    with pytest.raises(ValueError, match="outlist"):
        Justification(type="SL", outlist=[None])


def test_network_preserves_dangling_references_without_reverse_edges():
    network = Network()
    node = network.add_node(
        "conclusion",
        "Conclusion",
        [Justification(type="SL", antecedents=["missing"], outlist=["blocked"])],
    )

    assert node.justifications[0].antecedents == ["missing"]
    assert node.justifications[0].outlist == ["blocked"]
    assert network.nodes["conclusion"].truth_value == "OUT"
    assert network.nodes["conclusion"].dependents == set()


def test_network_add_dangling_justification_does_not_mutate_indexes_before_add():
    network = Network()
    network.add_node("conclusion", "Conclusion")
    result = network.add_justification(
        "conclusion", Justification(type="SL", antecedents=["missing"], outlist=["blocked"])
    )

    assert result["node_id"] == "conclusion"
    assert len(network.nodes["conclusion"].justifications) == 1
    assert network.nodes["conclusion"].dependents == set()
