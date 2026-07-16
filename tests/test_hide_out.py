"""Tests for hiding OUT beliefs from model-facing retrieval (#243)."""

from reasonsforge import api


def _setup_mixed_db(tmp_path):
    """Create a DB with IN and OUT beliefs for search/lookup testing."""
    db = str(tmp_path / "test.db")
    api.init_db(db)
    api.add_node("premise-in", "Retraction cascades propagate correctly", db_path=db)
    api.add_node("premise-out", "Retraction cascades are broken", db_path=db)
    api.retract_node("premise-out", db_path=db)
    api.add_node("derived-in", "System handles retraction well",
                 sl="premise-in", db_path=db)
    return db


class TestSearchHidesOut:

    def test_default_excludes_out(self, tmp_path):
        db = _setup_mixed_db(tmp_path)
        result = api.search("retraction", db_path=db)
        assert "premise-in" in result
        assert "premise-out" not in result

    def test_include_out_shows_all(self, tmp_path):
        db = _setup_mixed_db(tmp_path)
        result = api.search("retraction", db_path=db, include_out=True)
        assert "premise-in" in result
        assert "premise-out" in result

    def test_no_results_when_only_out_matches(self, tmp_path):
        db = str(tmp_path / "test.db")
        api.init_db(db)
        api.add_node("only-out", "unique xyzterm", db_path=db)
        api.retract_node("only-out", db_path=db)
        result = api.search("xyzterm", db_path=db)
        assert "No results found" in result

    def test_include_out_finds_only_out(self, tmp_path):
        db = str(tmp_path / "test.db")
        api.init_db(db)
        api.add_node("only-out", "unique xyzterm", db_path=db)
        api.retract_node("only-out", db_path=db)
        result = api.search("xyzterm", db_path=db, include_out=True)
        assert "only-out" in result

    def test_out_neighbors_not_expanded(self, tmp_path):
        db = str(tmp_path / "test.db")
        api.init_db(db)
        api.add_node("root", "root premise", db_path=db)
        api.add_node("derived-out", "derived from root",
                     sl="root", db_path=db)
        api.retract_node("derived-out", db_path=db)
        result = api.search("root premise", db_path=db)
        assert "### root" in result
        assert "### derived-out" not in result


class TestLookupHidesOut:

    def test_default_excludes_out(self, tmp_path):
        db = _setup_mixed_db(tmp_path)
        result = api.lookup("retraction", db_path=db)
        assert "premise-in" in result
        assert "premise-out" not in result

    def test_include_out_shows_all(self, tmp_path):
        db = _setup_mixed_db(tmp_path)
        result = api.lookup("retraction", db_path=db, include_out=True)
        assert "premise-in" in result
        assert "premise-out" in result

    def test_no_results_when_only_out_matches(self, tmp_path):
        db = str(tmp_path / "test.db")
        api.init_db(db)
        api.add_node("only-out", "unique xyzterm", db_path=db)
        api.retract_node("only-out", db_path=db)
        result = api.lookup("xyzterm", db_path=db)
        assert "No beliefs found" in result


class TestCompactHidesOut:

    def test_default_excludes_out_section(self, tmp_path):
        db = str(tmp_path / "test.db")
        api.init_db(db)
        api.add_node("active", "Active belief", db_path=db)
        api.add_node("retracted", "Retracted belief", db_path=db)
        api.retract_node("retracted", db_path=db)
        result = api.compact(db_path=db)
        assert "active" in result
        assert "retracted" not in result
        assert "OUT (retracted)" not in result

    def test_include_out_shows_out_section(self, tmp_path):
        db = str(tmp_path / "test.db")
        api.init_db(db)
        api.add_node("active", "Active belief", db_path=db)
        api.add_node("retracted", "Retracted belief", db_path=db)
        api.retract_node("retracted", db_path=db)
        result = api.compact(db_path=db, include_out=True)
        assert "active" in result
        assert "retracted" in result
        assert "OUT (retracted)" in result
