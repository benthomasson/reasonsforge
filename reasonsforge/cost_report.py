"""JSON cost reports for LLM operations.

Writes per-operation cost records to a costs/ directory for tracking
construction and maintenance costs of belief networks.
"""

import json
from datetime import datetime
from pathlib import Path


def write_cost_report(
    costs_dir: str | Path,
    operation: str,
    cost_summary: dict,
    *,
    domain: str | None = None,
    model: str | None = None,
    beliefs_added: int = 0,
    beliefs_retracted: int = 0,
    beliefs_unchanged: int = 0,
    round_number: int | None = None,
    wall_clock_seconds: float | None = None,
    extra: dict | None = None,
) -> Path:
    """Write a JSON cost report for a single operation.

    Returns the path to the written report file.
    """
    ts = datetime.now()
    ts_iso = ts.isoformat(timespec="seconds")
    ts_file = ts_iso.replace(":", "")

    report = {
        "timestamp": ts_iso,
        "operation": operation,
        "domain": domain,
        "model": model,
        "calls": cost_summary.get("calls", 0),
        "input_tokens": cost_summary.get("input_tokens", 0),
        "output_tokens": cost_summary.get("output_tokens", 0),
        "cost_dollars": cost_summary.get("total_cost_usd", 0.0),
        "by_model": cost_summary.get("by_model", {}),
        "beliefs_added": beliefs_added,
        "beliefs_retracted": beliefs_retracted,
        "beliefs_unchanged": beliefs_unchanged,
    }

    if round_number is not None:
        report["round_number"] = round_number
    if wall_clock_seconds is not None:
        report["wall_clock_seconds"] = round(wall_clock_seconds, 2)
    if extra:
        report.update(extra)

    costs_path = Path(costs_dir)
    costs_path.mkdir(parents=True, exist_ok=True)
    report_path = costs_path / f"{ts_file}-{operation}.json"

    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report_path


def load_cost_reports(costs_dir: str | Path) -> list[dict]:
    """Load all cost reports from a directory, sorted by timestamp."""
    costs_path = Path(costs_dir)
    if not costs_path.is_dir():
        return []
    reports = []
    for p in sorted(costs_path.glob("*.json")):
        try:
            reports.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return reports


def summarize_costs(reports: list[dict]) -> dict:
    """Aggregate cost reports into a summary.

    Returns totals and per-operation and per-domain breakdowns.
    """
    total = {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_dollars": 0.0,
        "wall_clock_seconds": 0.0,
        "beliefs_added": 0,
        "beliefs_retracted": 0,
        "reports": len(reports),
    }
    by_operation = {}
    by_domain = {}

    for r in reports:
        total["calls"] += r.get("calls", 0)
        total["input_tokens"] += r.get("input_tokens", 0)
        total["output_tokens"] += r.get("output_tokens", 0)
        total["cost_dollars"] += r.get("cost_dollars", 0.0)
        total["wall_clock_seconds"] += r.get("wall_clock_seconds", 0.0)
        total["beliefs_added"] += r.get("beliefs_added", 0)
        total["beliefs_retracted"] += r.get("beliefs_retracted", 0)

        op = r.get("operation", "unknown")
        if op not in by_operation:
            by_operation[op] = {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_dollars": 0.0, "wall_clock_seconds": 0.0,
                "beliefs_added": 0, "beliefs_retracted": 0, "reports": 0,
            }
        by_operation[op]["calls"] += r.get("calls", 0)
        by_operation[op]["input_tokens"] += r.get("input_tokens", 0)
        by_operation[op]["output_tokens"] += r.get("output_tokens", 0)
        by_operation[op]["cost_dollars"] += r.get("cost_dollars", 0.0)
        by_operation[op]["wall_clock_seconds"] += r.get("wall_clock_seconds", 0.0)
        by_operation[op]["beliefs_added"] += r.get("beliefs_added", 0)
        by_operation[op]["beliefs_retracted"] += r.get("beliefs_retracted", 0)
        by_operation[op]["reports"] += 1

        domain = r.get("domain") or "unknown"
        if domain not in by_domain:
            by_domain[domain] = {
                "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_dollars": 0.0, "wall_clock_seconds": 0.0,
                "beliefs_added": 0, "beliefs_retracted": 0, "reports": 0,
            }
        by_domain[domain]["calls"] += r.get("calls", 0)
        by_domain[domain]["input_tokens"] += r.get("input_tokens", 0)
        by_domain[domain]["output_tokens"] += r.get("output_tokens", 0)
        by_domain[domain]["cost_dollars"] += r.get("cost_dollars", 0.0)
        by_domain[domain]["wall_clock_seconds"] += r.get("wall_clock_seconds", 0.0)
        by_domain[domain]["beliefs_added"] += r.get("beliefs_added", 0)
        by_domain[domain]["beliefs_retracted"] += r.get("beliefs_retracted", 0)
        by_domain[domain]["reports"] += 1

    return {
        "total": total,
        "by_operation": by_operation,
        "by_domain": by_domain,
    }
