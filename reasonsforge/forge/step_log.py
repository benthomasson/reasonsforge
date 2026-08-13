"""Per-phase log capture for forge pipelines.

Tees stderr to both the terminal and a per-step log file in a logs/
directory, matching the per-step cost reports in costs/.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path


class _TeeWriter:
    """Write to two streams simultaneously."""

    def __init__(self, original, log_file):
        self._original = original
        self._log_file = log_file

    def write(self, data):
        self._original.write(data)
        try:
            self._log_file.write(data)
        except Exception:
            pass

    def flush(self):
        self._original.flush()
        try:
            self._log_file.flush()
        except Exception:
            pass

    def fileno(self):
        return self._original.fileno()

    def isatty(self):
        return self._original.isatty()


@contextmanager
def step_log(logs_dir: str | Path, pipeline: str, step_key: str,
             round_number: int | None = None):
    """Context manager that tees stderr to a per-step log file.

    Usage::

        with step_log("logs/", "analyze", "explore", round_number=1):
            step_fn()

    Creates files like ``logs/2026-08-13T142300-analyze-explore.log``.
    """
    logs_path = Path(logs_dir)
    try:
        logs_path.mkdir(parents=True, exist_ok=True)
    except OSError:
        yield
        return

    ts = datetime.now()
    ts_file = ts.isoformat(timespec="seconds").replace(":", "")
    filename = f"{ts_file}-{pipeline}-{step_key}.log"
    log_path = logs_path / filename

    try:
        log_file = open(log_path, "w")
    except OSError:
        yield
        return

    header = f"=== {pipeline} / {step_key}"
    if round_number is not None:
        header += f" (round {round_number})"
    header += f" — {ts.isoformat(timespec='seconds')} ===\n"
    log_file.write(header)

    original_stderr = sys.stderr
    tee = _TeeWriter(original_stderr, log_file)
    sys.stderr = tee
    try:
        yield log_path
    finally:
        sys.stderr = original_stderr
        try:
            log_file.close()
        except OSError:
            pass
