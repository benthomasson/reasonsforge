"""Tests for LLM-verified deduplication."""

from unittest.mock import patch

import pytest
from reasonsforge import api


def _make_db_with_dupes(tmp_path):
    db = str(tmp_path / "test.db")
    api.init_db(db)
    api.add_node("widget-uses-react", "The widget component uses React", db_path=db)
    api.add_node("widget-uses-react-framework", "The widget component uses the React framework", db_path=db)
    api.add_node("widget-is-stateful", "The widget component maintains internal state", db_path=db)
    return db


def test_verify_same_claim(tmp_path):
    db = _make_db_with_dupes(tmp_path)
    result = api.deduplicate(threshold=0.4, db_path=db)
    assert len(result["clusters"]) > 0

    with patch("reasonsforge.llm.invoke_model") as mock_llm:
        mock_llm.return_value = (
            "VERDICT: SAME_CLAIM\n"
            "REASON: Both state that the widget uses React."
        )
        vresult = api.verify_dedup_clusters(result["clusters"], model="claude")

    assert len(vresult["verified"]) > 0
    assert vresult["verified"][0]["verdict"] == "SAME_CLAIM"
    assert "React" in vresult["verified"][0]["reason"]
    assert len(vresult["rejected"]) == 0
    assert len(vresult["contradictions"]) == 0


def test_verify_different_claims(tmp_path):
    db = _make_db_with_dupes(tmp_path)
    result = api.deduplicate(threshold=0.4, db_path=db)

    with patch("reasonsforge.llm.invoke_model") as mock_llm:
        mock_llm.return_value = (
            "VERDICT: DIFFERENT_CLAIMS\n"
            "REASON: One describes the framework, the other describes state management."
        )
        vresult = api.verify_dedup_clusters(result["clusters"], model="claude")

    assert len(vresult["rejected"]) > 0
    assert vresult["rejected"][0]["verdict"] == "DIFFERENT_CLAIMS"
    assert len(vresult["verified"]) == 0


def test_verify_contradiction(tmp_path):
    db = str(tmp_path / "test.db")
    api.init_db(db)
    api.add_node("hook-is-invoked", "console_error_panic_hook is invoked at startup", db_path=db)
    api.add_node("hook-is-never-invoked", "console_error_panic_hook is never invoked", db_path=db)

    result = api.deduplicate(threshold=0.4, db_path=db)
    assert len(result["clusters"]) > 0

    with patch("reasonsforge.llm.invoke_model") as mock_llm:
        mock_llm.return_value = (
            "VERDICT: CONTRADICTION\n"
            "REASON: One says the hook is invoked, the other says it is never invoked."
        )
        vresult = api.verify_dedup_clusters(result["clusters"], model="claude")

    assert len(vresult["contradictions"]) > 0
    assert vresult["contradictions"][0]["verdict"] == "CONTRADICTION"
    assert len(vresult["verified"]) == 0


def test_verify_llm_failure_rejects_cluster(tmp_path):
    db = _make_db_with_dupes(tmp_path)
    result = api.deduplicate(threshold=0.4, db_path=db)

    with patch("reasonsforge.llm.invoke_model") as mock_llm:
        mock_llm.side_effect = RuntimeError("model unavailable")
        vresult = api.verify_dedup_clusters(result["clusters"], model="claude")

    assert len(vresult["rejected"]) > 0
    assert "verify_error" in vresult["rejected"][0]
    assert len(vresult["verified"]) == 0


def test_verify_defaults_to_different_on_bad_output(tmp_path):
    db = _make_db_with_dupes(tmp_path)
    result = api.deduplicate(threshold=0.4, db_path=db)

    with patch("reasonsforge.llm.invoke_model") as mock_llm:
        mock_llm.return_value = "I'm not sure what to say here."
        vresult = api.verify_dedup_clusters(result["clusters"], model="claude")

    assert len(vresult["rejected"]) > 0
    assert len(vresult["verified"]) == 0


def test_verify_auto_applies_verified_only(tmp_path):
    db = _make_db_with_dupes(tmp_path)
    result = api.deduplicate(threshold=0.4, db_path=db)
    verified_clusters = result["clusters"]

    with patch("reasonsforge.llm.invoke_model") as mock_llm:
        mock_llm.return_value = (
            "VERDICT: SAME_CLAIM\n"
            "REASON: Same claim."
        )
        vresult = api.verify_dedup_clusters(verified_clusters, model="claude")

    plan = []
    for cluster in vresult["verified"]:
        keep = cluster["kept"]
        retract = [b["id"] for b in cluster["beliefs"] if b["id"] != keep]
        plan.append({"keep": keep, "retract": retract})

    apply_result = api.apply_dedup_plan(plan, db_path=db)
    assert len(apply_result["retracted"]) > 0
    assert len(apply_result["errors"]) == 0

    for nid in apply_result["retracted"]:
        node = api.show_node(nid, db_path=db)
        assert node["truth_value"] == "OUT"
