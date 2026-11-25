"""Tests for JSON round-trip: export → import-json preserves full state."""

import json
from pathlib import Path

import pytest

from reasonsforge import api


class TestImportJson:
    def test_round_trip_premises(self, tmp_path):
        db1 = str(tmp_path / "src.db")
        db2 = str(tmp_path / "dst.db")
        json_file = str(tmp_path / "export.json")

        api.init_db(db_path=db1)
        api.add_node("a", "Premise A", source="repo/a.md", db_path=db1)
        api.add_node("b", "Premise B", db_path=db1)

        # Export
        data = api.export_network(db_path=db1)
        Path(json_file).write_text(json.dumps(data))

        # Import into fresh DB
        api.init_db(db_path=db2)
        result = api.import_json(json_file, db_path=db2)
        assert result["nodes_imported"] == 2

        # Verify
        status = api.get_status(db_path=db2)
        assert status["total"] == 2
        assert status["in_count"] == 2
        node_a = api.show_node("a", db_path=db2)
        assert node_a["source"] == "repo/a.md"

    def test_round_trip_justifications(self, tmp_path):
        db1 = str(tmp_path / "src.db")
        db2 = str(tmp_path / "dst.db")
        json_file = str(tmp_path / "export.json")

        api.init_db(db_path=db1)
        api.add_node("a", "Premise A", db_path=db1)
        api.add_node("b", "Derived B", sl="a", label="test", db_path=db1)

        data = api.export_network(db_path=db1)
        Path(json_file).write_text(json.dumps(data))

        api.init_db(db_path=db2)
        api.import_json(json_file, db_path=db2)

        node_b = api.show_node("b", db_path=db2)
        assert node_b["truth_value"] == "IN"
        assert len(node_b["justifications"]) == 1
        assert node_b["justifications"][0]["antecedents"] == ["a"]
        assert node_b["justifications"][0]["label"] == "test"

    def test_round_trip_outlist(self, tmp_path):
        db1 = str(tmp_path / "src.db")
        db2 = str(tmp_path / "dst.db")
        json_file = str(tmp_path / "export.json")

        api.init_db(db_path=db1)
        api.add_node("y", "Counter Y", db_path=db1)
        api.retract_node("y", db_path=db1)
        api.add_node("x", "Default X", unless="y", db_path=db1)

        data = api.export_network(db_path=db1)
        Path(json_file).write_text(json.dumps(data))

        api.init_db(db_path=db2)
        api.import_json(json_file, db_path=db2)

        node_x = api.show_node("x", db_path=db2)
        assert node_x["truth_value"] == "IN"
        assert node_x["justifications"][0]["outlist"] == ["y"]

        # Outlist propagation works after import
        api.assert_node("y", db_path=db2)
        node_x = api.show_node("x", db_path=db2)
        assert node_x["truth_value"] == "OUT"

    def test_round_trip_retracted_state(self, tmp_path):
        db1 = str(tmp_path / "src.db")
        db2 = str(tmp_path / "dst.db")
        json_file = str(tmp_path / "export.json")

        api.init_db(db_path=db1)
        api.add_node("a", "Premise A", db_path=db1)
        api.add_node("b", "Derived B", sl="a", db_path=db1)
        api.retract_node("a", db_path=db1)

        data = api.export_network(db_path=db1)
        Path(json_file).write_text(json.dumps(data))

        api.init_db(db_path=db2)
        api.import_json(json_file, db_path=db2)

        assert api.show_node("a", db_path=db2)["truth_value"] == "OUT"
        assert api.show_node("b", db_path=db2)["truth_value"] == "OUT"

        # Restoration works
        api.assert_node("a", db_path=db2)
        assert api.show_node("b", db_path=db2)["truth_value"] == "IN"

    def test_round_trip_nogoods(self, tmp_path):
        db1 = str(tmp_path / "src.db")
        db2 = str(tmp_path / "dst.db")
        json_file = str(tmp_path / "export.json")

        api.init_db(db_path=db1)
        api.add_node("a", "Premise A", db_path=db1)
        api.add_node("b", "Premise B", db_path=db1)
        api.add_nogood(["a", "b"], db_path=db1)

        data = api.export_network(db_path=db1)
        Path(json_file).write_text(json.dumps(data))

        api.init_db(db_path=db2)
        result = api.import_json(json_file, db_path=db2)
        assert result["nogoods_imported"] == 1

    def test_round_trip_metadata(self, tmp_path):
        db1 = str(tmp_path / "src.db")
        db2 = str(tmp_path / "dst.db")
        json_file = str(tmp_path / "export.json")

        api.init_db(db_path=db1)
        api.add_node("a", "Premise A", source="repo/a.md", db_path=db1)
        api.challenge("a", "reason", db_path=db1)
        api.defend("a", "challenge-a", "defense", db_path=db1)

        data = api.export_network(db_path=db1)
        Path(json_file).write_text(json.dumps(data))

        api.init_db(db_path=db2)
        api.import_json(json_file, db_path=db2)

        node_a = api.show_node("a", db_path=db2)
        assert "challenges" in node_a["metadata"]
        assert node_a["truth_value"] == "IN"

    def test_round_trip_full_network(self, tmp_path):
        """Export the real physics-pi-meta registry and reimport."""
        beliefs_path = Path.home() / "git" / "physics-pi-meta" / "beliefs.md"
        if not beliefs_path.exists():
            pytest.skip("physics-pi-meta not found")

        db1 = str(tmp_path / "src.db")
        db2 = str(tmp_path / "dst.db")
        json_file = str(tmp_path / "export.json")

        # Build from beliefs.md
        api.init_db(db_path=db1)
        api.import_beliefs(str(beliefs_path), db_path=db1)
        status1 = api.get_status(db_path=db1)

        # Export
        data = api.export_network(db_path=db1)
        Path(json_file).write_text(json.dumps(data))

        # Import into fresh DB
        api.init_db(db_path=db2)
        api.import_json(json_file, db_path=db2)
        status2 = api.get_status(db_path=db2)

        # Same node count and IN count
        assert status2["total"] == status1["total"]
        assert status2["in_count"] == status1["in_count"]

    def test_import_rejects_invalid_truth_without_mutating_destination(self, tmp_path):
        db = str(tmp_path / "test.db")
        json_file = tmp_path / "invalid.json"
        api.init_db(db_path=db)
        api.add_node("existing", "Existing", db_path=db)
        json_file.write_text(json.dumps({
            "nodes": {
                "new": {"text": "New", "truth_value": "MAYBE", "justifications": []}
            }
        }))

        with pytest.raises(ValueError, match="truth value"):
            api.import_json(str(json_file), db_path=db)

        assert api.show_node("existing", db_path=db)["truth_value"] == "IN"
        with pytest.raises(KeyError):
            api.show_node("new", db_path=db)

    def test_import_rejects_invalid_justification_before_mutation(self, tmp_path):
        db = str(tmp_path / "test.db")
        json_file = tmp_path / "invalid.json"
        api.init_db(db_path=db)
        api.add_node("existing", "Existing", db_path=db)
        json_file.write_text(json.dumps({
            "nodes": {
                "new": {
                    "text": "New",
                    "justifications": [{"type": "SL", "antecedents": "existing"}],
                }
            }
        }))

        with pytest.raises(ValueError, match="antecedents"):
            api.import_json(str(json_file), db_path=db)

        assert api.show_node("existing", db_path=db)["truth_value"] == "IN"
        with pytest.raises(KeyError):
            api.show_node("new", db_path=db)

    @pytest.mark.parametrize(
        "justification",
        [
            {"type": "SL", "outlist": "not-a-list"},
            {"type": "SL", "antecedents": ["ok", 3]},
            {"type": "SL", "label": 3},
            {"type": "SL", "content_hash": []},
        ],
    )
    def test_import_rejects_malformed_justification_fields(self, tmp_path, justification):
        db = str(tmp_path / "test.db")
        json_file = tmp_path / "invalid.json"
        api.init_db(db_path=db)
        api.add_node("existing", "Existing", db_path=db)
        api.add_node("derived", "Derived", sl="existing", label="kept", db_path=db)
        before = api.export_network(db_path=db)
        json_file.write_text(json.dumps({"nodes": {"new": {
            "text": "New", "justifications": [justification]
        }}}))

        with pytest.raises(ValueError):
            api.import_json(str(json_file), db_path=db)
        assert api.export_network(db_path=db) == before
        with pytest.raises(KeyError):
            api.show_node("new", db_path=db)

    def test_import_rejects_malformed_metadata_and_nogoods(self, tmp_path):
        db = str(tmp_path / "test.db")
        json_file = tmp_path / "invalid.json"
        api.init_db(db_path=db)
        api.add_node("existing", "Existing", db_path=db)
        api.add_nogood(["existing"], db_path=db)
        before = api.export_network(db_path=db)
        json_file.write_text(json.dumps({
            "nodes": {"new": {"text": "New", "metadata": []}},
            "nogoods": [],
        }))
        with pytest.raises(ValueError, match="Metadata"):
            api.import_json(str(json_file), db_path=db)
        assert api.export_network(db_path=db) == before

        json_file.write_text(json.dumps({"nodes": {}, "nogoods": [{"id": "n", "nodes": [3]}]}))
        with pytest.raises(ValueError, match="nogood nodes"):
            api.import_json(str(json_file), db_path=db)
        assert api.export_network(db_path=db) == before

    @pytest.mark.parametrize(
        "payload",
        [
            [],
            {"nodes": []},
            {"nodes": {"new": []}},
            {"nodes": {"new": {"justifications": [{"type": "INVALID"}]} }},
        ],
    )
    def test_invalid_payload_preserves_existing_state(self, tmp_path, payload):
        db = str(tmp_path / "test.db")
        json_file = tmp_path / "invalid.json"
        api.init_db(db_path=db)
        api.add_node("a", "Premise A", db_path=db)
        api.add_node("b", "Derived B", sl="a", label="kept", db_path=db)
        api.add_nogood(["a", "b"], db_path=db)
        api.retract_node("a", db_path=db)
        before = api.export_network(db_path=db)
        json_file.write_text(json.dumps(payload))

        with pytest.raises(ValueError):
            api.import_json(str(json_file), db_path=db)

        assert api.export_network(db_path=db) == before

    def test_missing_file_raises(self, tmp_path):
        db = str(tmp_path / "test.db")
        api.init_db(db_path=db)
        with pytest.raises(FileNotFoundError):
            api.import_json("/nonexistent.json", db_path=db)

    def test_skip_duplicates(self, tmp_path):
        db = str(tmp_path / "test.db")
        json_file = str(tmp_path / "export.json")

        api.init_db(db_path=db)
        api.add_node("a", "Premise A", db_path=db)

        data = api.export_network(db_path=db)
        Path(json_file).write_text(json.dumps(data))

        # Import again — should skip existing
        result = api.import_json(json_file, db_path=db)
        assert result["nodes_imported"] == 0
