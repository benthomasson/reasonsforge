"""Git utilities for code explanation."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .language import LanguageProfile


def get_diff(
    ref: str | None = None,
    base: str | None = None,
    cwd: str | None = None,
    context_lines: int = 10,
) -> str:
    """
    Get git diff.

    Args:
        ref: Branch or commit to diff. If None, uses staged changes.
        base: Base branch to diff against (default: main)
        cwd: Working directory
        context_lines: Number of context lines

    Returns:
        Git diff output

    Raises:
        RuntimeError: If git command fails
    """
    context_arg = f"-U{context_lines}"

    if ref is None:
        cmd = ["git", "diff", "--staged", context_arg]
    else:
        if base is None:
            check = subprocess.run(
                ["git", "rev-parse", "--verify", "origin/main"],
                capture_output=True,
                cwd=cwd,
            )
            base = "origin/main" if check.returncode == 0 else "main"
        cmd = ["git", "diff", context_arg, f"{base}...{ref}"]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)

    if result.returncode != 0:
        raise RuntimeError(f"Git diff failed: {result.stderr}")

    return result.stdout


def get_diff_since(since: str, cwd: str | None = None, context_lines: int = 10) -> tuple[str, str]:
    """Get diff of all changes since a date.

    Args:
        since: Date string (e.g., "2026-03-01", "1 week ago")
        cwd: Working directory
        context_lines: Number of context lines

    Returns:
        Tuple of (diff_content, commit_log)

    Raises:
        RuntimeError: If no commits found since the date
    """
    # Find the last commit BEFORE the date to use as diff base
    result = subprocess.run(
        ["git", "log", f"--until={since}", "--format=%H", "-1"],
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Git log failed: {result.stderr}")

    base = result.stdout.strip()
    if not base:
        # No commits before this date — all commits are since the date
        # Use the first commit and diff it against empty tree won't work
        # in shallow clones, so just get the full log
        check = subprocess.run(
            ["git", "log", f"--since={since}", "--format=%H"],
            capture_output=True, text=True, cwd=cwd,
        )
        commits = [c for c in check.stdout.strip().split("\n") if c]
        if not commits:
            raise RuntimeError(f"No commits found since {since}")
        # Use the oldest commit directly — we'll miss its own changes
        # but this is the shallow clone safe path
        base = commits[-1]

    # Diff from base to HEAD
    context_arg = f"-U{context_lines}"
    diff_result = subprocess.run(
        ["git", "diff", context_arg, f"{base}..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if diff_result.returncode != 0:
        raise RuntimeError(f"Git diff failed: {diff_result.stderr}")
    diff = diff_result.stdout

    # Get commit log
    log_result = subprocess.run(
        ["git", "log", "--oneline", f"{base}..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    log = log_result.stdout if log_result.returncode == 0 else ""

    return diff, log


def save_diff_checkpoint(project_dir: str, cwd: str | None = None) -> None:
    """Record the current HEAD as the last-explored commit."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        return
    head_sha = result.stdout.strip()
    checkpoint = {
        "head": head_sha,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    os.makedirs(project_dir, exist_ok=True)
    path = os.path.join(project_dir, "last-diff.json")
    with open(path, "w") as f:
        json.dump(checkpoint, f, indent=2)


def load_diff_checkpoint(project_dir: str) -> dict | None:
    """Load the last diff checkpoint. Returns {head, timestamp} or None."""
    path = os.path.join(project_dir, "last-diff.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return None


def get_diff_since_commit(base_sha: str, cwd: str | None = None, context_lines: int = 10) -> tuple[str, str]:
    """Get diff from a specific commit to HEAD.

    Returns:
        Tuple of (diff_content, commit_log)
    """
    context_arg = f"-U{context_lines}"
    diff_result = subprocess.run(
        ["git", "diff", context_arg, f"{base_sha}..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if diff_result.returncode != 0:
        raise RuntimeError(f"Git diff failed: {diff_result.stderr}")

    log_result = subprocess.run(
        ["git", "log", "--oneline", f"{base_sha}..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    log = log_result.stdout if log_result.returncode == 0 else ""

    return diff_result.stdout, log


def commits_since_checkpoint(project_dir: str, cwd: str | None = None) -> int | None:
    """Count commits since last diff checkpoint. Returns None if no checkpoint."""
    checkpoint = load_diff_checkpoint(project_dir)
    if not checkpoint:
        return None
    result = subprocess.run(
        ["git", "rev-list", "--count", f"{checkpoint['head']}..HEAD"],
        capture_output=True, text=True, cwd=cwd,
    )
    if result.returncode != 0:
        return None
    return int(result.stdout.strip())


def get_file_content(path: str) -> str | None:
    """Read file content, returning None if not found."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return None


BINARY_EXTENSIONS = frozenset({
    ".pyc", ".pyo", ".so", ".o", ".a", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".webp",
    ".woff", ".woff2", ".ttf", ".eot",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z",
    ".jar", ".war", ".class", ".exe", ".dll",
    ".bin", ".dat", ".db", ".sqlite", ".sqlite3", ".pdf",
})


def list_source_files(repo_path: str) -> list[str]:
    """List all tracked source files using git ls-files.

    Filters out binary files by extension. Returns paths relative to repo root.
    """
    result = subprocess.run(
        ["git", "ls-files"],
        capture_output=True, text=True, cwd=repo_path,
    )
    if result.returncode != 0:
        return []
    files = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        ext = os.path.splitext(line)[1].lower()
        if ext not in BINARY_EXTENSIONS:
            files.append(line)
    files.sort(key=lambda p: (p.count("/"), p))
    return files


def get_repo_structure(repo_path: str, max_depth: int = 4) -> str:
    """
    Get filtered directory tree of a repository.

    Args:
        repo_path: Path to repository root
        max_depth: Maximum directory depth to traverse

    Returns:
        Formatted directory tree string
    """
    skip_dirs = {
        ".git", ".hg", ".svn", "node_modules", "__pycache__",
        ".tox", ".venv", "venv", ".env", "env", ".eggs",
        "dist", "build", ".mypy_cache", ".pytest_cache",
        ".ruff_cache", "htmlcov", ".coverage", "*.egg-info",
    }
    skip_suffixes = {".pyc", ".pyo", ".so", ".o", ".a", ".dylib"}

    lines = []
    root = Path(repo_path)

    def _walk(dir_path: Path, prefix: str, depth: int):
        if depth > max_depth:
            return

        try:
            entries = sorted(dir_path.iterdir(), key=lambda e: (not e.is_dir(), e.name))
        except PermissionError:
            return

        # Filter entries
        filtered = []
        for entry in entries:
            if entry.name.startswith(".") and entry.name in skip_dirs:
                continue
            if entry.is_dir() and entry.name in skip_dirs:
                continue
            if entry.is_dir() and entry.name.endswith(".egg-info"):
                continue
            if entry.is_file() and entry.suffix in skip_suffixes:
                continue
            filtered.append(entry)

        for i, entry in enumerate(filtered):
            is_last = i == len(filtered) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension, depth + 1)

    lines.append(root.name + "/")
    _walk(root, "", 1)

    return "\n".join(lines)


def get_commit_log(
    ref: str | None = None,
    base: str | None = None,
    cwd: str | None = None,
    max_count: int = 20,
) -> str:
    """
    Get commit log between base and ref.

    Args:
        ref: Branch or commit
        base: Base branch
        cwd: Working directory
        max_count: Maximum number of commits

    Returns:
        Formatted commit log
    """
    if ref and base:
        cmd = ["git", "log", "--oneline", f"--max-count={max_count}", f"{base}...{ref}"]
    elif ref:
        cmd = ["git", "log", "--oneline", f"--max-count={max_count}", ref]
    else:
        cmd = ["git", "log", "--oneline", f"--max-count={max_count}"]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        return ""
    return result.stdout


def get_imports(file_path: str, repo_path: str, lang: LanguageProfile | None = None) -> dict:
    """
    Analyze imports for a source file.

    Returns dict with:
        - imports: list of modules this file imports
        - imported_by: list of files that import this file
    """
    from .language import PYTHON

    lang = lang or PYTHON

    content = get_file_content(file_path)
    if content is None:
        return {"imports": [], "imported_by": []}

    imports = []
    for line in content.split("\n"):
        if lang.matches_import(line):
            imports.append(line.strip())

    rel_path = os.path.relpath(file_path, repo_path)
    module_name = lang.module_name_from_path(rel_path)
    simple_name = Path(file_path).stem

    imported_by = []
    root = Path(repo_path)
    for glob_pattern in lang.source_globs:
        for src_file in root.rglob(glob_pattern):
            if str(src_file) == file_path:
                continue
            try:
                src_content = src_file.read_text(encoding="utf-8")
            except (UnicodeDecodeError, PermissionError):
                continue
            for src_line in src_content.split("\n"):
                if lang.matches_import(src_line) and (
                    module_name in src_line or simple_name in src_line
                ):
                    imported_by.append(str(src_file.relative_to(root)))
                    break

    return {"imports": imports, "imported_by": imported_by}


def extract_symbol(file_path: str, symbol: str, lang: LanguageProfile | None = None) -> str | None:
    """
    Extract a function or class definition from a file.

    Args:
        file_path: Path to the file
        symbol: Name of the function or class
        lang: Language profile (defaults to Python)

    Returns:
        Source code of the symbol, or None if not found
    """
    from .language import extract_symbol_with_profile

    return extract_symbol_with_profile(file_path, symbol, lang)


def list_commits_with_files(
    since: str | None = None,
    since_commit: str | None = None,
    cwd: str | None = None,
) -> list[dict]:
    """List commits with their changed files since a date or commit.

    Args:
        since: Date string (e.g., "2026-03-01", "1 week ago")
        since_commit: Base commit SHA to start from (exclusive)
        cwd: Working directory

    Returns:
        List of dicts: [{sha, subject, files: [{path, status}, ...],
        deleted_files: [path, ...]}, ...] oldest first.
        Status is one of: A (added), M (modified), D (deleted), R (renamed),
        C (copied), T (type changed).
    """
    if since_commit:
        range_spec = f"{since_commit}..HEAD"
        cmd = ["git", "log", "--reverse", "--format=%H %s", "--name-status", range_spec]
    elif since:
        cmd = ["git", "log", "--reverse", "--format=%H %s", "--name-status", f"--since={since}"]
    else:
        raise ValueError("Either since or since_commit must be provided")

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Git log failed: {result.stderr}")

    commits = []
    current = None
    for line in result.stdout.splitlines():
        if not line:
            continue
        # Commit lines are 40-char hex SHA followed by space and subject
        if len(line) > 40 and line[40] == " " and all(c in "0123456789abcdef" for c in line[:40]):
            if current:
                commits.append(current)
            current = {"sha": line[:40], "subject": line[41:], "files": [], "deleted_files": []}
        elif current is not None:
            # --name-status lines: "M\tpath" or "R100\told\tnew"
            parts = line.split("\t")
            if len(parts) >= 2:
                status = parts[0][0]  # First char: A, M, D, R, C, T
                path = parts[-1]     # Last field (handles renames: old\tnew)
                current["files"].append(path)
                if status == "D":
                    current["deleted_files"].append(path)
            else:
                # Fallback: treat as plain filename (shouldn't happen)
                current["files"].append(line)

    if current:
        commits.append(current)

    return commits


def find_related_tests(
    file_path: str, repo_path: str, symbol: str | None = None, lang: LanguageProfile | None = None,
) -> list[str]:
    """
    Find test files related to a source file or symbol.

    Args:
        file_path: Source file path
        repo_path: Repository root
        symbol: Optional symbol name to search for
        lang: Language profile (defaults to Python)

    Returns:
        List of related test file paths (relative to repo)
    """
    from .language import PYTHON

    lang = lang or PYTHON

    root = Path(repo_path)
    source_name = Path(file_path).stem
    related: list[str] = []
    seen: set[str] = set()

    for glob_pattern in lang.test_globs:
        for test_file in root.rglob(glob_pattern):
            rel = str(test_file.relative_to(root))
            if rel in seen:
                continue

            if source_name in test_file.name:
                related.append(rel)
                seen.add(rel)
                continue

            if symbol:
                try:
                    content = test_file.read_text(encoding="utf-8")
                    if symbol in content:
                        related.append(rel)
                        seen.add(rel)
                except (UnicodeDecodeError, PermissionError):
                    continue

    return related
