"""Tests for the pipeline report helper."""

import json

from reasonsforge.forge.report import Report


def test_report_creates_file(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test-pipeline", ["step-a", "step-b"], path=path)

    with open(path) as f:
        data = json.load(f)
    assert data["pipeline"] == "test-pipeline"
    assert data["status"] == "running"
    assert len(data["steps"]) == 2
    assert data["steps"][0]["name"] == "step-a"
    assert data["steps"][0]["status"] == "pending"


def test_start_and_complete_step(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a", "b"], path=path)

    report.start_step("a")
    with open(path) as f:
        data = json.load(f)
    assert data["steps"][0]["status"] == "running"
    assert "started_at" in data["steps"][0]

    report.complete_step("did 5 things")
    with open(path) as f:
        data = json.load(f)
    assert data["steps"][0]["status"] == "completed"
    assert data["steps"][0]["detail"] == "did 5 things"
    assert "completed_at" in data["steps"][0]


def test_fail_step(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a", "b"], path=path)

    report.start_step("a")
    report.fail_step("something broke")

    with open(path) as f:
        data = json.load(f)
    assert data["steps"][0]["status"] == "failed"
    assert data["steps"][0]["error"] == "something broke"
    assert data["status"] == "running"


def test_complete_pipeline(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a"], path=path)

    report.start_step("a")
    report.complete_step()
    report.complete()

    with open(path) as f:
        data = json.load(f)
    assert data["status"] == "completed"
    assert "completed_at" in data


def test_unknown_step_appended(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a"], path=path)

    report.start_step("r2-explore")
    with open(path) as f:
        data = json.load(f)
    assert len(data["steps"]) == 2
    assert data["steps"][1]["name"] == "r2-explore"
    assert data["steps"][1]["status"] == "running"


def test_complete_step_without_start_is_noop(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a"], path=path)
    report.complete_step("should not crash")

    with open(path) as f:
        data = json.load(f)
    assert data["steps"][0]["status"] == "pending"


def test_updated_at_changes(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a", "b"], path=path)

    with open(path) as f:
        t1 = json.load(f)["updated_at"]

    report.start_step("a")
    with open(path) as f:
        t2 = json.load(f)["updated_at"]

    assert t2 >= t1


def test_complete_step_without_detail(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a"], path=path)
    report.start_step("a")
    report.complete_step()

    with open(path) as f:
        data = json.load(f)
    assert data["steps"][0]["status"] == "completed"
    assert "detail" not in data["steps"][0]


def test_complete_step_with_counts(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a"], path=path)
    report.start_step("a")
    report.complete_step("added 5", counts={"added": 5}, duration_seconds=12.34)

    with open(path) as f:
        data = json.load(f)
    assert data["steps"][0]["counts"] == {"added": 5}
    assert data["steps"][0]["duration_seconds"] == 12.34
    assert data["steps"][0]["detail"] == "added 5"


def test_complete_step_without_counts(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a"], path=path)
    report.start_step("a")
    report.complete_step("done")

    with open(path) as f:
        data = json.load(f)
    assert "counts" not in data["steps"][0]
    assert "duration_seconds" not in data["steps"][0]


def test_complete_with_summary(tmp_path):
    path = str(tmp_path / "report.json")
    report = Report("test", ["a"], path=path)
    report.start_step("a")
    report.complete_step()
    report.complete(summary={"total_derived": 10, "converged": True})

    with open(path) as f:
        data = json.load(f)
    assert data["status"] == "completed"
    assert data["summary"]["total_derived"] == 10
    assert data["summary"]["converged"] is True
