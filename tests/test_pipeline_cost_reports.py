"""Tests for per-phase cost reports in forge pipelines."""

import json
from types import SimpleNamespace
from unittest.mock import patch

from reasonsforge.forge.code.pipeline import (
    _write_step_cost_report,
)


class TestWriteStepCostReport:

    def test_writes_report_and_resets(self, tmp_path):
        costs_dir = tmp_path / "costs"
        args = SimpleNamespace(costs_dir=str(costs_dir), domain="test", model="claude")
        fake_summary = {
            "calls": 5,
            "input_tokens": 2000,
            "output_tokens": 800,
            "total_cost_usd": 0.012,
            "by_model": {},
        }
        with patch("reasonsforge.forge.llm.get_cost_summary",
                    return_value=fake_summary), \
             patch("reasonsforge.forge.llm.reset_cost_tracker") as mock_reset:
            _write_step_cost_report(args, "analyze", "explore", round_number=1)
            mock_reset.assert_called_once()

        reports = list(costs_dir.glob("*.json"))
        assert len(reports) == 1
        data = json.loads(reports[0].read_text())
        assert data["operation"] == "analyze-explore"
        assert data["calls"] == 5
        assert data["round_number"] == 1
        assert data["domain"] == "test"
        assert data["model"] == "claude"

    def test_skips_when_no_calls(self, tmp_path):
        costs_dir = tmp_path / "costs"
        args = SimpleNamespace(costs_dir=str(costs_dir), domain="test", model="claude")
        fake_summary = {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_cost_usd": 0.0,
            "by_model": {},
        }
        with patch("reasonsforge.forge.llm.get_cost_summary",
                    return_value=fake_summary), \
             patch("reasonsforge.forge.llm.reset_cost_tracker") as mock_reset:
            _write_step_cost_report(args, "analyze", "init")
            mock_reset.assert_not_called()

        assert not costs_dir.exists()

    def test_no_round_number(self, tmp_path):
        costs_dir = tmp_path / "costs"
        args = SimpleNamespace(costs_dir=str(costs_dir), domain=None, model="gemini")
        fake_summary = {
            "calls": 1,
            "input_tokens": 100,
            "output_tokens": 50,
            "total_cost_usd": 0.001,
            "by_model": {},
        }
        with patch("reasonsforge.forge.llm.get_cost_summary",
                    return_value=fake_summary), \
             patch("reasonsforge.forge.llm.reset_cost_tracker"):
            _write_step_cost_report(args, "update", "walk-commits")

        reports = list(costs_dir.glob("*.json"))
        assert len(reports) == 1
        data = json.loads(reports[0].read_text())
        assert data["operation"] == "update-walk-commits"
        assert "round_number" not in data

    def test_exception_does_not_propagate(self, tmp_path):
        args = SimpleNamespace(costs_dir="/nonexistent/path", domain=None, model=None)
        with patch("reasonsforge.forge.llm.get_cost_summary",
                    side_effect=ImportError("no module")):
            _write_step_cost_report(args, "analyze", "scan")
