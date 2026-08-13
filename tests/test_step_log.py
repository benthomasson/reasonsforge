"""Tests for per-phase log capture."""

import sys

from reasonsforge.forge.step_log import step_log


class TestStepLog:

    def test_creates_log_file(self, tmp_path):
        logs_dir = tmp_path / "logs"
        with step_log(logs_dir, "analyze", "explore") as log_path:
            print("hello from explore", file=sys.stderr)

        assert log_path.exists()
        content = log_path.read_text()
        assert "hello from explore" in content
        assert "analyze / explore" in content

    def test_tees_to_original_stderr(self, tmp_path, capsys):
        logs_dir = tmp_path / "logs"
        with step_log(logs_dir, "analyze", "scan"):
            print("visible on stderr", file=sys.stderr)

        captured = capsys.readouterr()
        assert "visible on stderr" in captured.err

    def test_restores_stderr(self, tmp_path):
        original = sys.stderr
        with step_log(tmp_path, "analyze", "init"):
            pass
        assert sys.stderr is original

    def test_restores_stderr_on_exception(self, tmp_path):
        original = sys.stderr
        try:
            with step_log(tmp_path, "analyze", "derive"):
                raise ValueError("boom")
        except ValueError:
            pass
        assert sys.stderr is original

    def test_round_number_in_header(self, tmp_path):
        with step_log(tmp_path, "refine", "derive", round_number=3) as log_path:
            pass

        content = log_path.read_text()
        assert "round 3" in content

    def test_no_round_number_in_header(self, tmp_path):
        with step_log(tmp_path, "update", "scan") as log_path:
            pass

        content = log_path.read_text()
        assert "round" not in content

    def test_log_filename_format(self, tmp_path):
        with step_log(tmp_path, "analyze", "explore") as log_path:
            pass

        assert log_path.name.endswith("-analyze-explore.log")

    def test_unwritable_dir_does_not_raise(self):
        with step_log("/nonexistent/path/logs", "analyze", "scan"):
            print("still works", file=sys.stderr)

    def test_creates_nested_dirs(self, tmp_path):
        logs_dir = tmp_path / "nested" / "deep" / "logs"
        with step_log(logs_dir, "analyze", "init") as log_path:
            print("nested test", file=sys.stderr)

        assert log_path.exists()
        content = log_path.read_text()
        assert "nested test" in content
