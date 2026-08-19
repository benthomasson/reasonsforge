"""Tests for reasonsforge.forge.llm — cost tracking and JSON output parsing."""

import json

import pytest

from reasonsforge.forge.llm import (
    _parse_cli_json,
    _parse_ollama_stats,
    _record_cost,
    _strip_ansi,
    reset_cost_tracker,
    get_cost_summary,
    format_cost_summary,
)


@pytest.fixture(autouse=True)
def clean_tracker():
    """Reset cost tracker before each test."""
    reset_cost_tracker()
    yield
    reset_cost_tracker()


# --- _parse_cli_json ---

def test_parse_claude_json():
    data = {
        "result": "The answer is 4.",
        "total_cost_usd": 0.21,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 0,
        },
    }
    text = _parse_cli_json(json.dumps(data), "claude")
    assert text == "The answer is 4."
    s = get_cost_summary()
    assert s["calls"] == 1
    assert s["input_tokens"] == 600
    assert s["output_tokens"] == 10
    assert s["total_cost_usd"] == 0.21


def test_parse_gemini_json():
    data = {
        "response": "4",
        "stats": {
            "models": {
                "gemini-2.5-flash": {
                    "tokens": {"input": 200, "candidates": 5, "total": 205},
                },
            },
        },
    }
    text = _parse_cli_json(json.dumps(data), "gemini")
    assert text == "4"
    s = get_cost_summary()
    assert s["calls"] == 1
    assert s["input_tokens"] == 200
    assert s["output_tokens"] == 5
    assert s["total_cost_usd"] == 0.0


def test_parse_non_json_falls_back():
    text = _parse_cli_json("Just plain text", "claude")
    assert text == "Just plain text"
    s = get_cost_summary()
    assert s["calls"] == 0


def test_parse_claude_null_result_falls_back():
    data = {"result": None, "total_cost_usd": 0.01, "usage": {"input_tokens": 10, "output_tokens": 0}}
    raw = json.dumps(data)
    text = _parse_cli_json(raw, "claude")
    assert text == raw
    s = get_cost_summary()
    assert s["calls"] == 1


def test_parse_non_dict_json_falls_back():
    text = _parse_cli_json("[1, 2, 3]", "claude")
    assert text == "[1, 2, 3]"
    s = get_cost_summary()
    assert s["calls"] == 0


def test_parse_gemini_multi_model():
    data = {
        "response": "answer",
        "stats": {
            "models": {
                "gemini-2.5-flash-lite": {
                    "tokens": {"input": 100, "candidates": 10},
                },
                "gemini-3-flash-preview": {
                    "tokens": {"input": 500, "candidates": 20},
                },
            },
        },
    }
    text = _parse_cli_json(json.dumps(data), "gemini")
    assert text == "answer"
    s = get_cost_summary()
    assert s["input_tokens"] == 600
    assert s["output_tokens"] == 30


# --- cost accumulation ---

def test_accumulates_across_calls():
    _record_cost("claude", 100, 10, 0.10)
    _record_cost("claude", 200, 20, 0.20)
    s = get_cost_summary()
    assert s["calls"] == 2
    assert s["input_tokens"] == 300
    assert s["output_tokens"] == 30
    assert abs(s["total_cost_usd"] - 0.30) < 0.001


def test_tracks_by_model():
    _record_cost("claude", 100, 10, 0.10)
    _record_cost("gemini", 200, 20, 0.0)
    s = get_cost_summary()
    assert s["by_model"]["claude"]["calls"] == 1
    assert s["by_model"]["gemini"]["calls"] == 1
    assert s["by_model"]["claude"]["total_cost_usd"] == 0.10


def test_reset_clears_all():
    _record_cost("claude", 100, 10, 0.10)
    reset_cost_tracker()
    s = get_cost_summary()
    assert s["calls"] == 0
    assert s["input_tokens"] == 0
    assert s["by_model"] == {}


# --- format_cost_summary ---

def test_format_no_calls():
    assert format_cost_summary() == ""


def test_format_with_cost():
    _record_cost("claude", 1000, 50, 0.1234)
    result = format_cost_summary()
    assert "Cost:" in result
    assert "$0.1234" in result
    assert "1,000 input" in result
    assert "50 output" in result
    assert "1 call(s)" in result


def test_format_without_cost():
    _record_cost("gemini", 500, 25, 0.0)
    result = format_cost_summary()
    assert "Cost:" in result
    assert "$" not in result
    assert "500 input" in result


# --- _strip_ansi ---

def test_strip_ansi_removes_csi():
    assert _strip_ansi("hello\x1b[4Dworld") == "helloworld"


def test_strip_ansi_removes_cursor_show_hide():
    assert _strip_ansi("text\x1b[?25l\x1b[?25h") == "text"


def test_strip_ansi_preserves_clean_text():
    assert _strip_ansi("no escape codes here") == "no escape codes here"


def test_strip_ansi_preserves_brackets():
    assert _strip_ansi("[file] `main.py`") == "[file] `main.py`"


# --- _parse_ollama_stats ---

def test_parse_ollama_stats():
    stderr = (
        "\x1b[?25ltotal duration:       1.4s\n"
        "prompt eval count:    12 token(s)\n"
        "eval count:           43 token(s)\n"
        "\x1b[?25h"
    )
    _parse_ollama_stats(stderr, "ollama:qwen3.8:27b")
    s = get_cost_summary()
    assert s["calls"] == 1
    assert s["input_tokens"] == 12
    assert s["output_tokens"] == 43
    assert s["total_cost_usd"] == 0.0


def test_parse_ollama_stats_no_stats():
    _parse_ollama_stats("no stats here", "ollama:test")
    s = get_cost_summary()
    assert s["calls"] == 0
