"""Tests for cost_report module."""

import json
from pathlib import Path

from reasonsforge.cost_report import (
    load_cost_reports,
    summarize_costs,
    write_cost_report,
)


class TestWriteCostReport:

    def test_writes_json_file(self, tmp_path):
        costs_dir = tmp_path / "costs"
        cost_summary = {
            "calls": 3,
            "input_tokens": 1000,
            "output_tokens": 500,
            "total_cost_usd": 0.0042,
            "by_model": {"claude": {"calls": 3, "input_tokens": 1000,
                                     "output_tokens": 500, "total_cost_usd": 0.0042}},
        }
        path = write_cost_report(
            costs_dir, "derive", cost_summary,
            domain="test-domain", model="claude",
            beliefs_added=5, beliefs_retracted=1,
        )
        assert path.exists()
        assert path.suffix == ".json"
        data = json.loads(path.read_text())
        assert data["operation"] == "derive"
        assert data["domain"] == "test-domain"
        assert data["model"] == "claude"
        assert data["calls"] == 3
        assert data["input_tokens"] == 1000
        assert data["output_tokens"] == 500
        assert data["cost_dollars"] == 0.0042
        assert data["beliefs_added"] == 5
        assert data["beliefs_retracted"] == 1
        assert "timestamp" in data

    def test_creates_directory(self, tmp_path):
        costs_dir = tmp_path / "nested" / "costs"
        cost_summary = {"calls": 1, "input_tokens": 100, "output_tokens": 50,
                        "total_cost_usd": 0.001, "by_model": {}}
        path = write_cost_report(costs_dir, "review", cost_summary)
        assert costs_dir.is_dir()
        assert path.exists()

    def test_includes_optional_fields(self, tmp_path):
        cost_summary = {"calls": 1, "input_tokens": 100, "output_tokens": 50,
                        "total_cost_usd": 0.001, "by_model": {}}
        path = write_cost_report(
            tmp_path, "derive", cost_summary,
            round_number=3, wall_clock_seconds=12.345,
            extra={"source_files": 10},
        )
        data = json.loads(path.read_text())
        assert data["round_number"] == 3
        assert data["wall_clock_seconds"] == 12.35
        assert data["source_files"] == 10

    def test_omits_optional_fields_when_not_provided(self, tmp_path):
        cost_summary = {"calls": 1, "input_tokens": 100, "output_tokens": 50,
                        "total_cost_usd": 0.0, "by_model": {}}
        path = write_cost_report(tmp_path, "verify", cost_summary)
        data = json.loads(path.read_text())
        assert "round_number" not in data
        assert "wall_clock_seconds" not in data


class TestLoadCostReports:

    def test_loads_sorted_reports(self, tmp_path):
        for i, op in enumerate(["derive", "review", "repair"]):
            data = {"timestamp": f"2026-08-1{i}T10:00:00", "operation": op,
                    "calls": i + 1, "input_tokens": 100 * (i + 1),
                    "output_tokens": 50, "cost_dollars": 0.001 * (i + 1)}
            (tmp_path / f"2026-08-1{i}T100000-{op}.json").write_text(
                json.dumps(data))
        reports = load_cost_reports(tmp_path)
        assert len(reports) == 3
        assert reports[0]["operation"] == "derive"
        assert reports[2]["operation"] == "repair"

    def test_empty_directory(self, tmp_path):
        reports = load_cost_reports(tmp_path)
        assert reports == []

    def test_missing_directory(self, tmp_path):
        reports = load_cost_reports(tmp_path / "nonexistent")
        assert reports == []

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "good.json").write_text('{"operation": "derive", "calls": 1}')
        (tmp_path / "bad.json").write_text("not json")
        reports = load_cost_reports(tmp_path)
        assert len(reports) == 1


class TestSummarizeCosts:

    def test_empty_reports(self):
        summary = summarize_costs([])
        assert summary["total"]["reports"] == 0
        assert summary["total"]["cost_dollars"] == 0.0

    def test_aggregates_totals(self):
        reports = [
            {"operation": "derive", "domain": "awx", "calls": 3,
             "input_tokens": 1000, "output_tokens": 500,
             "cost_dollars": 0.01, "beliefs_added": 5, "beliefs_retracted": 0},
            {"operation": "review-beliefs", "domain": "awx", "calls": 2,
             "input_tokens": 800, "output_tokens": 300,
             "cost_dollars": 0.005, "beliefs_added": 0, "beliefs_retracted": 2},
        ]
        summary = summarize_costs(reports)
        assert summary["total"]["calls"] == 5
        assert summary["total"]["input_tokens"] == 1800
        assert summary["total"]["output_tokens"] == 800
        assert summary["total"]["cost_dollars"] == 0.015
        assert summary["total"]["beliefs_added"] == 5
        assert summary["total"]["beliefs_retracted"] == 2
        assert summary["total"]["reports"] == 2

    def test_by_operation(self):
        reports = [
            {"operation": "derive", "domain": "a", "calls": 1,
             "input_tokens": 100, "output_tokens": 50,
             "cost_dollars": 0.01, "beliefs_added": 3, "beliefs_retracted": 0},
            {"operation": "derive", "domain": "b", "calls": 1,
             "input_tokens": 200, "output_tokens": 100,
             "cost_dollars": 0.02, "beliefs_added": 2, "beliefs_retracted": 0},
            {"operation": "review-beliefs", "domain": "a", "calls": 1,
             "input_tokens": 50, "output_tokens": 25,
             "cost_dollars": 0.005, "beliefs_added": 0, "beliefs_retracted": 2},
        ]
        summary = summarize_costs(reports)
        assert summary["by_operation"]["derive"]["reports"] == 2
        assert summary["by_operation"]["derive"]["cost_dollars"] == 0.03
        assert summary["by_operation"]["derive"]["beliefs_retracted"] == 0
        assert summary["by_operation"]["review-beliefs"]["reports"] == 1
        assert summary["by_operation"]["review-beliefs"]["beliefs_retracted"] == 2

    def test_by_domain(self):
        reports = [
            {"operation": "derive", "domain": "awx", "calls": 1,
             "input_tokens": 100, "output_tokens": 50,
             "cost_dollars": 0.01, "beliefs_added": 3, "beliefs_retracted": 0},
            {"operation": "derive", "domain": "eda", "calls": 1,
             "input_tokens": 200, "output_tokens": 100,
             "cost_dollars": 0.02, "beliefs_added": 2, "beliefs_retracted": 1},
        ]
        summary = summarize_costs(reports)
        assert "awx" in summary["by_domain"]
        assert "eda" in summary["by_domain"]
        assert summary["by_domain"]["awx"]["cost_dollars"] == 0.01
        assert summary["by_domain"]["eda"]["beliefs_retracted"] == 1

    def test_missing_domain_defaults_to_unknown(self):
        reports = [
            {"operation": "derive", "calls": 1,
             "input_tokens": 100, "output_tokens": 50,
             "cost_dollars": 0.01, "beliefs_added": 0, "beliefs_retracted": 0},
        ]
        summary = summarize_costs(reports)
        assert "unknown" in summary["by_domain"]
