"""Tests for _phase_reconcile — retract stale beliefs by issue_id."""

import pytest
from reasonsforge.api import add_node, init_db, set_metadata, export_network
from reasonsforge.forge.project.commands import _phase_reconcile


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "test.db")
    init_db(db_path=path)
    return path


def _add_belief(db_path, belief_id, text, issue_id):
    add_node(belief_id, text, source="project-forge", db_path=db_path)
    set_metadata(belief_id, "issue_id", issue_id, db_path=db_path)


def test_reconcile_retracts_stale(db):
    _add_belief(db, "old-belief-1", "This is stale", "PROJ-1")
    _add_belief(db, "old-belief-2", "This is still valid", "PROJ-1")

    proposals = {
        "PROJ-1": [
            {"belief_id": "old-belief-2", "text": "Updated text", "source": "PROJ-1"},
            {"belief_id": "new-belief-3", "text": "Brand new", "source": "PROJ-1"},
        ],
    }

    retracted, kept = _phase_reconcile(proposals, db)

    assert retracted == 1
    assert kept == 1

    network = export_network(db_path=db)
    assert network["nodes"]["old-belief-1"]["truth_value"] == "OUT"
    assert network["nodes"]["old-belief-2"]["truth_value"] == "IN"


def test_reconcile_no_proposals_no_retraction(db):
    _add_belief(db, "existing-1", "Some belief", "PROJ-2")

    retracted, kept = _phase_reconcile({}, db)

    assert retracted == 0
    assert kept == 0

    network = export_network(db_path=db)
    assert network["nodes"]["existing-1"]["truth_value"] == "IN"


def test_reconcile_different_issue_ids_independent(db):
    _add_belief(db, "proj1-belief", "Belongs to PROJ-1", "PROJ-1")
    _add_belief(db, "proj2-belief", "Belongs to PROJ-2", "PROJ-2")

    proposals = {
        "PROJ-1": [
            {"belief_id": "proj1-replacement", "text": "New", "source": "PROJ-1"},
        ],
    }

    retracted, kept = _phase_reconcile(proposals, db)

    assert retracted == 1
    assert kept == 0

    network = export_network(db_path=db)
    assert network["nodes"]["proj1-belief"]["truth_value"] == "OUT"
    assert network["nodes"]["proj2-belief"]["truth_value"] == "IN"


def test_reconcile_empty_db(db):
    proposals = {
        "PROJ-1": [
            {"belief_id": "new-1", "text": "New belief", "source": "PROJ-1"},
        ],
    }

    retracted, kept = _phase_reconcile(proposals, db)

    assert retracted == 0
    assert kept == 0
