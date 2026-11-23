"""Tests for reasonsforge merge command."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from reasonsforge import api
from reasonsforge.cli import main


def run_cli(*args, db_path=None):
    argv = ["reasons"]
    if db_path:
        argv += ["--db", db_path]
    argv += list(args)
    stdout, stderr = StringIO(), StringIO()
    with (
        patch.object(sys, "argv", argv),
        patch.object(sys, "stdout", stdout),
        patch.object(sys, "stderr", stderr),
    ):
        try:
            main()
        except SystemExit as e:
            return stdout.getvalue(), stderr.getvalue(), e.code
    return stdout.getvalue(), stderr.getvalue(), 0


class TestMerge:
    def _setup_dbs(self, tmp_path):
        db = str(tmp_path / "local.db")
        api.init_db(db_path=db)
        return db

    def _export_to(self, db_path, out_path):
        data = api.export_network(db_path=db_path)
        Path(out_path).write_text(json.dumps(data))

    def test_add_new_beliefs_from_theirs(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Local A", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("b", "Remote B", db_path=remote_db)
        api.add_node("c", "Remote C", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="ours", db_path=db)

        assert result["added"] == 2
        assert result["conflicts"] == 0
        status = api.get_status(db_path=db)
        assert status["total"] == 3

    def test_keep_local_only_beliefs(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Local A", db_path=db)
        api.add_node("b", "Local B", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Local A", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="ours", db_path=db)

        assert result["only_ours"] == 1
        assert result["added"] == 0
        assert result["conflicts"] == 0

    def test_conflict_strategy_ours(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Local version", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Remote version", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="ours", db_path=db)

        assert result["conflicts"] == 1
        assert result["kept_ours"] == 1
        assert result["kept_theirs"] == 0
        node = api.show_node("a", db_path=db)
        assert node["text"] == "Local version"

    def test_conflict_strategy_theirs(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Local version", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Remote version", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="theirs", db_path=db)

        assert result["conflicts"] == 1
        assert result["kept_theirs"] == 1
        node = api.show_node("a", db_path=db)
        assert node["text"] == "Remote version"

    def test_conflict_strategy_newest(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Old version", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "New version", db_path=remote_db)
        theirs_path = str(tmp_path / "theirs.json")

        # Manipulate timestamps: make theirs newer
        theirs_data = api.export_network(db_path=remote_db)
        theirs_data["nodes"]["a"]["updated_at"] = "2026-12-01T00:00:00Z"
        Path(theirs_path).write_text(json.dumps(theirs_data))

        # Make ours older via export/reimport
        ours_data = api.export_network(db_path=db)
        assert ours_data["nodes"]["a"]["updated_at"] <= "2026-12-01T00:00:00Z"

        result = api.merge_networks(theirs_path, strategy="newest", db_path=db)

        assert result["conflicts"] == 1
        assert result["kept_theirs"] == 1
        node = api.show_node("a", db_path=db)
        assert node["text"] == "New version"

    def test_conflict_strategy_newest_keeps_ours(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Current version", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Old version", db_path=remote_db)
        theirs_path = str(tmp_path / "theirs.json")

        # Make theirs older
        theirs_data = api.export_network(db_path=remote_db)
        theirs_data["nodes"]["a"]["updated_at"] = "2020-01-01T00:00:00Z"
        Path(theirs_path).write_text(json.dumps(theirs_data))

        result = api.merge_networks(theirs_path, strategy="newest", db_path=db)

        assert result["kept_ours"] == 1
        node = api.show_node("a", db_path=db)
        assert node["text"] == "Current version"

    def test_interactive_leaves_conflicts_unresolved(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Local", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Remote", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="interactive", db_path=db)

        assert result["conflicts"] == 1
        assert result["resolved"] == 0
        assert len(result["conflict_details"]) == 1
        assert result["conflict_details"][0]["id"] == "a"
        # Original should be preserved
        node = api.show_node("a", db_path=db)
        assert node["text"] == "Local"

    def test_dry_run(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Local", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Remote", db_path=remote_db)
        api.add_node("b", "Remote B", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="theirs", dry_run=True, db_path=db)

        assert result["added"] == 1
        assert result["conflicts"] == 1
        assert result["resolved"] == 1
        assert result["kept_theirs"] == 1
        # But nothing actually changed
        status = api.get_status(db_path=db)
        assert status["total"] == 1
        node = api.show_node("a", db_path=db)
        assert node["text"] == "Local"

    def test_truth_value_conflict(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Belief A", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Belief A", db_path=remote_db)
        api.retract_node("a", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="theirs", db_path=db)

        assert result["conflicts"] == 1
        assert "truth_value" in result["conflict_details"][0]["fields"]
        node = api.show_node("a", db_path=db)
        assert node["truth_value"] == "OUT"

    def test_file_not_found(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        with pytest.raises(FileNotFoundError):
            api.merge_networks(str(tmp_path / "nonexistent.json"), db_path=db)

    def test_mixed_add_and_conflict(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Local A", db_path=db)
        api.add_node("b", "Local B", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Changed A", db_path=remote_db)
        api.add_node("c", "New C", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="theirs", db_path=db)

        assert result["added"] == 1
        assert result["conflicts"] == 1
        assert result["kept_theirs"] == 1
        assert result["only_ours"] == 1
        status = api.get_status(db_path=db)
        assert status["total"] == 3
        node_a = api.show_node("a", db_path=db)
        assert node_a["text"] == "Changed A"

    def test_topological_order_theirs_only(self, tmp_path):
        """Remote nodes that depend on each other are added in correct order."""
        db = self._setup_dbs(tmp_path)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("premise", "A premise", db_path=remote_db)
        api.add_node("derived", "Derived from premise", sl="premise", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="ours", db_path=db)

        assert result["added"] == 2
        node = api.show_node("derived", db_path=db)
        assert node["truth_value"] == "IN"
        assert node["justifications"][0]["antecedents"] == ["premise"]

    def test_justification_conflict_updates_dependents(self, tmp_path):
        """When resolving a justification conflict with theirs, dependent graph is updated."""
        db = self._setup_dbs(tmp_path)
        api.add_node("old-dep", "Old dependency", db_path=db)
        api.add_node("a", "Belief A", sl="old-dep", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("new-dep", "New dependency", db_path=remote_db)
        api.add_node("a", "Belief A", sl="new-dep", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        result = api.merge_networks(theirs, strategy="theirs", db_path=db)

        assert result["conflicts"] == 1
        node = api.show_node("a", db_path=db)
        assert node["justifications"][0]["antecedents"] == ["new-dep"]

    def test_derived_to_premise_conversion(self, tmp_path):
        """When theirs converts a derived node to a premise (empty justifications), merge applies it."""
        db = self._setup_dbs(tmp_path)
        api.add_node("dep", "Dependency", db_path=db)
        api.add_node("a", "Derived A", sl="dep", db_path=db)

        # Theirs has the same node but as a premise (no justifications)
        theirs_path = str(tmp_path / "theirs.json")
        theirs_data = api.export_network(db_path=db)
        theirs_data["nodes"]["a"]["justifications"] = []
        theirs_data["nodes"]["a"]["text"] = "Now a premise"
        Path(theirs_path).write_text(json.dumps(theirs_data))

        result = api.merge_networks(theirs_path, strategy="theirs", db_path=db)

        assert result["conflicts"] == 1
        node = api.show_node("a", db_path=db)
        assert node["text"] == "Now a premise"
        assert node["justifications"] == []

    def test_invalid_strategy_raises(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(db, theirs)
        with pytest.raises(ValueError, match="Unknown strategy"):
            api.merge_networks(theirs, strategy="foobar", db_path=db)

    def test_metadata_replaced_not_merged_on_theirs(self, tmp_path):
        """Strategy theirs fully replaces metadata, not additive merge."""
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Belief A", db_path=db)
        api.set_metadata("a", "local_key", "local_val", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Belief A changed", db_path=remote_db)
        api.set_metadata("a", "remote_key", "remote_val", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        self._export_to(remote_db, theirs)

        api.merge_networks(theirs, strategy="theirs", db_path=db)

        node = api.show_node("a", db_path=db)
        meta = node.get("metadata", {})
        assert "remote_key" in meta
        assert "local_key" not in meta

    def test_no_conflicts_identical(self, tmp_path):
        db = self._setup_dbs(tmp_path)
        api.add_node("a", "Same text", db_path=db)

        theirs = str(tmp_path / "theirs.json")
        self._export_to(db, theirs)

        result = api.merge_networks(theirs, strategy="ours", db_path=db)

        assert result["added"] == 0
        assert result["conflicts"] == 0
        assert result["only_ours"] == 0


class TestMergeCLI:
    def test_merge_cli_adds_beliefs(self, tmp_path):
        db = str(tmp_path / "local.db")
        api.init_db(db_path=db)
        api.add_node("a", "Local A", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("b", "Remote B", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        data = api.export_network(db_path=remote_db)
        Path(theirs).write_text(json.dumps(data))

        out, err, code = run_cli("merge", theirs, "--strategy", "ours", db_path=db)

        assert code == 0
        assert "Added 1" in out

    def test_merge_cli_dry_run(self, tmp_path):
        db = str(tmp_path / "local.db")
        api.init_db(db_path=db)
        api.add_node("a", "Local", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Remote", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        data = api.export_network(db_path=remote_db)
        Path(theirs).write_text(json.dumps(data))

        out, err, code = run_cli(
            "merge", theirs, "--strategy", "theirs", "--dry-run", db_path=db
        )

        assert code == 0
        assert "Dry run" in out
        node = api.show_node("a", db_path=db)
        assert node["text"] == "Local"

    def test_merge_cli_interactive_shows_conflicts(self, tmp_path):
        db = str(tmp_path / "local.db")
        api.init_db(db_path=db)
        api.add_node("a", "Local version", db_path=db)

        remote_db = str(tmp_path / "remote.db")
        api.init_db(db_path=remote_db)
        api.add_node("a", "Remote version", db_path=remote_db)
        theirs = str(tmp_path / "theirs.json")
        data = api.export_network(db_path=remote_db)
        Path(theirs).write_text(json.dumps(data))

        out, err, code = run_cli("merge", theirs, db_path=db)

        assert code == 0
        assert "unresolved" in out
        assert "Re-run with --strategy" in out

    def test_merge_cli_file_not_found(self, tmp_path):
        db = str(tmp_path / "local.db")
        api.init_db(db_path=db)

        out, err, code = run_cli("merge", str(tmp_path / "nope.json"), db_path=db)

        assert code == 1
        assert "Error" in err
