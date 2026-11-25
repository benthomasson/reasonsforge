"""Smoke coverage for runtime data-model invariants."""

import pytest

from reasonsforge import Justification, Node


def test_valid_construction_and_rejection():
    assert Node(id="smoke", text="smoke").truth_value == "IN"
    assert Justification(type="SL", antecedents=["smoke"]).type == "SL"

    with pytest.raises(ValueError):
        Node(id="invalid", text="invalid", truth_value="MAYBE")
    with pytest.raises(ValueError):
        Justification(type="SL", antecedents="smoke")
