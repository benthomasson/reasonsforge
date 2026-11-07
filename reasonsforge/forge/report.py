"""Pipeline progress report.

Writes a structured JSON report to .forge/report.json as a pipeline runs.
The report is the contract between pipeline code (reasonsforge) and the
management layer (reasons-manager). Pipelines own their logic; manager
owns the display.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Report:
    """Track pipeline progress and write to a JSON report file."""

    def __init__(self, pipeline: str, steps: list[str],
                 path: str = ".forge/report.json"):
        self._path = path
        self._current: str | None = None
        self._data = {
            "pipeline": pipeline,
            "started_at": _now(),
            "updated_at": _now(),
            "status": "running",
            "steps": [{"name": s, "status": "pending"} for s in steps],
        }
        self._save()

    def _step(self, name: str) -> dict | None:
        for s in self._data["steps"]:
            if s["name"] == name:
                return s
        return None

    def _save(self) -> None:
        self._data["updated_at"] = _now()
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def start_step(self, name: str) -> None:
        """Mark a step as running."""
        step = self._step(name)
        if step is None:
            step = {"name": name}
            self._data["steps"].append(step)
        step["status"] = "running"
        step["started_at"] = _now()
        self._current = name
        self._save()

    def complete_step(self, detail: str | None = None) -> None:
        """Mark the current step as completed."""
        if self._current is None:
            return
        step = self._step(self._current)
        if step:
            step["status"] = "completed"
            step["completed_at"] = _now()
            if detail:
                step["detail"] = detail
        self._current = None
        self._save()

    def fail_step(self, error: str) -> None:
        """Mark the current step as failed."""
        if self._current is None:
            return
        step = self._step(self._current)
        if step:
            step["status"] = "failed"
            step["completed_at"] = _now()
            step["error"] = error
        self._current = None
        self._save()

    def complete(self) -> None:
        """Mark the entire pipeline as completed."""
        self._data["status"] = "completed"
        self._data["completed_at"] = _now()
        self._save()

    def fail(self, error: str) -> None:
        """Mark the entire pipeline as failed."""
        if self._current:
            self.fail_step(error)
        self._data["status"] = "failed"
        self._data["error"] = error
        self._data["completed_at"] = _now()
        self._save()

    def item_done(self, name: str, detail: str | None = None) -> None:
        """Record a completed item in the current step. (Future use.)"""
        pass

    def item_failed(self, name: str, error: str) -> None:
        """Record a failed item in the current step. (Future use.)"""
        pass
