"""Prompt templates for meta forge."""

from .ask import ASK_FALLBACK_PROMPT
from .common import BELIEFS_INSTRUCTIONS, OUTPUT_FORMAT, TOPICS_INSTRUCTIONS
from .contradictions import build_contradictions_prompt
from .derive import build_derive_prompt
from .summary import build_summary_prompt

__all__ = [
    "ASK_FALLBACK_PROMPT",
    "BELIEFS_INSTRUCTIONS",
    "OUTPUT_FORMAT",
    "TOPICS_INSTRUCTIONS",
    "build_contradictions_prompt",
    "build_derive_prompt",
    "build_summary_prompt",
]
