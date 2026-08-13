"""Tests for project scan resume functionality."""

import json
from pathlib import Path
from unittest.mock import patch

from reasonsforge.forge.project.commands import (
    _clear_scan_progress,
    _load_scan_progress,
    _save_scan_progress,
)


class TestScanProgress:

    def test_save_and_load(self, tmp_path):
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=tmp_path / "scan-progress.json"):
            params = {"state": "open", "labels": None, "jql": None,
                      "per_issue": False, "limit": 100}
            _save_scan_progress(3, 300, params)

            progress = _load_scan_progress()
            assert progress["last_completed_page"] == 3
            assert progress["total_scanned"] == 300
            assert progress["params"] == params

    def test_load_missing_file(self, tmp_path):
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=tmp_path / "nonexistent.json"):
            assert _load_scan_progress() is None

    def test_load_corrupt_file(self, tmp_path):
        path = tmp_path / "scan-progress.json"
        path.write_text("not json")
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=path):
            assert _load_scan_progress() is None

    def test_clear(self, tmp_path):
        path = tmp_path / "scan-progress.json"
        path.write_text("{}")
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=path):
            _clear_scan_progress()
            assert not path.exists()

    def test_clear_missing_file(self, tmp_path):
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=tmp_path / "nonexistent.json"):
            _clear_scan_progress()  # should not raise

    def test_save_creates_directory(self, tmp_path):
        path = tmp_path / "nested" / "dir" / "scan-progress.json"
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=path):
            _save_scan_progress(1, 50, {"state": "open"})
            assert path.exists()
            data = json.loads(path.read_text())
            assert data["last_completed_page"] == 1

    def test_save_overwrites(self, tmp_path):
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=tmp_path / "scan-progress.json"):
            _save_scan_progress(1, 100, {"state": "open"})
            _save_scan_progress(2, 200, {"state": "open"})

            progress = _load_scan_progress()
            assert progress["last_completed_page"] == 2
            assert progress["total_scanned"] == 200

    def test_params_mismatch_detection(self, tmp_path):
        with patch("reasonsforge.forge.project.commands._scan_progress_path",
                    return_value=tmp_path / "scan-progress.json"):
            params_v1 = {"state": "open", "labels": None}
            _save_scan_progress(5, 500, params_v1)

            progress = _load_scan_progress()
            params_v2 = {"state": "closed", "labels": None}
            assert progress["params"] != params_v2
