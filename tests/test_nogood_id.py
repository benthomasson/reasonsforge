"""Tests for content-based nogood ID generation.

Nogood IDs are derived from the sorted contradicting node IDs,
e.g. add_nogood(["b", "a"]) -> "nogood-a-b". Duplicate nogoods
get a numeric suffix: "nogood-a-b-2".
"""

import json

import pytest

from reasonsforge import api, Nogood
from reasonsforge.network import Network
from reasonsforge.storage import Storage


@pytest.fixture
def db(tmp_path):
    db_path = str(tmp_path / "reasons.db")
    api.init_db(db_path=db_path)
    return db_path


class TestContentBasedIds:

    def test_id_from_sorted_nodes(self):
        net = Network()
        net.add_node("b", "B")
        net.add_node("a", "A")
        net.add_nogood(["b", "a"])
        assert net.nogoods[0].id == "nogood-a-b"

    def test_different_nodes_different_ids(self):
        net = Network()
        net.add_node("a", "A")
        net.add_node("b", "B")
        net.add_node("c", "C")
        net.add_node("d", "D")
        net.add_nogood(["a", "b"])
        net.add_nogood(["c", "d"])
        assert net.nogoods[0].id == "nogood-a-b"
        assert net.nogoods[1].id == "nogood-c-d"

    def test_duplicate_gets_suffix(self):
        net = Network()
        net.add_node("a", "A")
        net.add_node("b", "B")
        net.add_nogood(["a", "b"])
        net.add_nogood(["a", "b"])
        assert net.nogoods[0].id == "nogood-a-b"
        assert net.nogoods[1].id == "nogood-a-b-2"

    def test_three_node_nogood(self):
        net = Network()
        net.add_node("x", "X")
        net.add_node("y", "Y")
        net.add_node("z", "Z")
        net.add_nogood(["z", "x", "y"])
        assert net.nogoods[0].id == "nogood-x-y-z"


class TestPersistence:

    def test_id_survives_save_load(self, tmp_path):
        db_path = str(tmp_path / "reasons.db")
        storage = Storage(db_path)
        net = Network()
        net.add_node("a", "A")
        net.add_node("b", "B")
        net.add_nogood(["a", "b"])
        storage.save(net)
        storage.close()

        storage2 = Storage(db_path)
        loaded = storage2.load()
        assert loaded.nogoods[0].id == "nogood-a-b"
        storage2.close()


class TestImportJson:

    def test_import_preserves_old_format_ids(self, db, tmp_path):
        api.add_node("a", "A", db_path=db)
        api.add_node("b", "B", db_path=db)

        json_data = {
            "nodes": {},
            "nogoods": [
                {"id": "nogood-005", "nodes": ["a", "b"], "discovered": "", "resolution": ""},
            ],
            "repos": {},
        }
        json_file = str(tmp_path / "import.json")
        with open(json_file, "w") as f:
            json.dump(json_data, f)

        api.import_json(json_file, db_path=db)
        result = api.add_nogood(["a", "b"], db_path=db)
        assert result["nogood_id"] == "nogood-a-b"


class TestImportBeliefs:

    def test_import_nogoods_preserves_old_format_ids(self, db, tmp_path):
        api.add_node("a", "A", db_path=db)
        api.add_node("b", "B", db_path=db)

        beliefs_text = ""
        nogoods_text = """# Nogoods

### nogood-010: a, b
- Affects: a, b
- Discovered: 2026-01-01
"""
        from reasonsforge.import_beliefs import import_into_network
        from reasonsforge.storage import Storage

        storage = Storage(db)
        net = storage.load()
        import_into_network(net, beliefs_text, nogoods_text)
        storage.save(net)
        storage.close()

        result = api.add_nogood(["a", "b"], db_path=db)
        assert result["nogood_id"] == "nogood-a-b"
