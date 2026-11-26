"""Regression tests for code-forge initialization paths."""

from types import SimpleNamespace
from unittest.mock import patch

from reasonsforge.forge.code.commands import cmd_init
from reasonsforge.forge.init_cmd import GITIGNORE_CONTENT


def _init_args(**overrides):
    values = {
        "repo": None,
        "domain": None,
        "description": None,
        "output": "reasons.db",
        "db": None,
        "test_command": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _run_init():
    with (
        patch("reasonsforge.api.init_db"),
        patch("reasonsforge.api.add_node"),
        patch("reasonsforge.forge.claude_md.write_claude_md_if_missing", return_value=False),
    ):
        cmd_init(_init_args())


def test_code_init_writes_gitignore_in_current_repository(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    _run_init()

    assert (tmp_path / ".gitignore").read_text() == GITIGNORE_CONTENT


def test_code_init_with_repo_writes_gitignore_in_target_repository(tmp_path, monkeypatch):
    caller = tmp_path / "caller"
    repository = tmp_path / "repository"
    caller.mkdir()
    repository.mkdir()
    monkeypatch.chdir(caller)

    with (
        patch("reasonsforge.api.init_db"),
        patch("reasonsforge.api.add_node"),
        patch("reasonsforge.forge.claude_md.write_claude_md_if_missing", return_value=False),
    ):
        cmd_init(_init_args(repo=str(repository)))

    assert (repository / ".gitignore").read_text() == GITIGNORE_CONTENT
    assert not (caller / ".gitignore").exists()
