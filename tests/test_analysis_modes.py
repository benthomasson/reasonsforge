"""Tests for analysis mode overlays on explore, propose, and derive prompts."""

import pytest

from reasonsforge.forge.code.prompts.modes import get_mode, VALID_MODES
from reasonsforge.forge.code.prompts.file import build_file_prompt
from reasonsforge.forge.code.prompts.function import build_function_prompt
from reasonsforge.forge.code.prompts.repo import build_repo_prompt
from reasonsforge.forge.code.prompts.diff import build_diff_prompt, build_diff_summary_prompt
from reasonsforge.forge.code.prompts.propose import build_propose_prompt


class TestModeRegistry:
    def test_valid_modes(self):
        assert "discover" in VALID_MODES
        assert "security" in VALID_MODES
        assert "performance" in VALID_MODES

    def test_get_mode_returns_dict(self):
        m = get_mode("security")
        assert isinstance(m, dict)
        assert "explore_role" in m
        assert "explore_extra" in m
        assert "derive_task_extra" in m

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Unknown mode"):
            get_mode("nonexistent")

    def test_discover_has_empty_extras(self):
        m = get_mode("discover")
        assert m["explore_extra"] == ""
        assert m["propose_extra"] == ""
        assert m["derive_task_extra"] == ""


class TestFilePromptMode:
    def test_discover_mode_default_role(self):
        prompt = build_file_prompt("test.py", "x = 1")
        assert "explaining code to a colleague" in prompt

    def test_security_mode_changes_role(self):
        prompt = build_file_prompt("test.py", "x = 1", mode="security")
        assert "security engineer" in prompt
        assert "auditing code" in prompt

    def test_security_mode_adds_focus(self):
        prompt = build_file_prompt("test.py", "x = 1", mode="security")
        assert "Input validation" in prompt
        assert "Injection vectors" in prompt

    def test_performance_mode_changes_role(self):
        prompt = build_file_prompt("test.py", "x = 1", mode="performance")
        assert "performance engineer" in prompt

    def test_performance_mode_adds_focus(self):
        prompt = build_file_prompt("test.py", "x = 1", mode="performance")
        assert "Algorithmic complexity" in prompt
        assert "N+1 queries" in prompt

    def test_discover_mode_no_extra(self):
        prompt = build_file_prompt("test.py", "x = 1", mode="discover")
        assert "Security Focus" not in prompt
        assert "Performance Focus" not in prompt


class TestFunctionPromptMode:
    def test_security_mode(self):
        prompt = build_function_prompt("test.py", "login", "def login(): pass",
                                       mode="security")
        assert "security engineer" in prompt
        assert "Trust boundaries" in prompt

    def test_performance_mode(self):
        prompt = build_function_prompt("test.py", "query", "def query(): pass",
                                       mode="performance")
        assert "performance engineer" in prompt
        assert "Resource leaks" in prompt


class TestRepoPromptMode:
    def test_security_mode(self):
        prompt = build_repo_prompt("src/\n  main.py", mode="security")
        assert "security engineer" in prompt
        assert "Credential handling" in prompt

    def test_performance_mode(self):
        prompt = build_repo_prompt("src/\n  main.py", mode="performance")
        assert "performance engineer" in prompt
        assert "Caching" in prompt


class TestDiffPromptMode:
    def test_security_mode(self):
        prompt = build_diff_prompt("+ password = input()", mode="security")
        assert "security engineer" in prompt

    def test_performance_mode(self):
        prompt = build_diff_prompt("+ for x in all_users():", mode="performance")
        assert "performance engineer" in prompt

    def test_summary_security_mode(self):
        prompt = build_diff_summary_prompt(changed_files=["auth.py"], mode="security")
        assert "security engineer" in prompt


class TestProposePromptMode:
    def test_discover_mode_no_extra(self):
        prompt = build_propose_prompt("entry content here")
        assert "Trust boundary violations" not in prompt

    def test_security_mode_adds_guidance(self):
        prompt = build_propose_prompt("entry content here", mode="security")
        assert "Trust boundary violations" in prompt

    def test_performance_mode_adds_guidance(self):
        prompt = build_propose_prompt("entry content here", mode="performance")
        assert "Algorithmic issues" in prompt

    def test_base_prompt_always_present(self):
        prompt = build_propose_prompt("entry content here", mode="security")
        assert "extracting architectural and behavioral claims" in prompt
