"""Multi-model code review integrated into reasonsforge.

Vendored from ftl-code-review, adapted to use reasonsforge's LLM layer
and shared utilities.
"""

from dataclasses import dataclass, field
from enum import Enum


class Verdict(Enum):
    PASS = "PASS"
    CONCERN = "CONCERN"
    BLOCK = "BLOCK"


class Correctness(Enum):
    VALID = "VALID"
    QUESTIONABLE = "QUESTIONABLE"
    BROKEN = "BROKEN"


class SpecCompliance(Enum):
    MEETS = "MEETS"
    PARTIAL = "PARTIAL"
    VIOLATES = "VIOLATES"
    NA = "N/A"


class TestCoverage(Enum):
    COVERED = "COVERED"
    PARTIAL = "PARTIAL"
    UNTESTED = "UNTESTED"


class Integration(Enum):
    WIRED = "WIRED"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"


@dataclass
class SelfReview:
    limitations: str = ""


@dataclass
class ChangeVerdict:
    change_id: str
    verdict: Verdict
    correctness: Correctness | None = None
    spec_compliance: SpecCompliance | None = None
    test_coverage: TestCoverage | None = None
    integration: Integration | None = None
    reasoning: str = ""


@dataclass
class ModelReview:
    model: str
    gate: Verdict
    changes: list[ChangeVerdict] = field(default_factory=list)
    raw_response: str = ""
    self_review: SelfReview | None = None
    feature_requests: list[str] = field(default_factory=list)
    observations: dict = field(default_factory=dict)


@dataclass
class AggregateReview:
    diff_ref: str
    spec_file: str | None = None
    models: list[str] = field(default_factory=list)
    reviews: list[ModelReview] = field(default_factory=list)
    gate: Verdict = Verdict.PASS
    disagreements: list[dict] = field(default_factory=list)


__all__ = [
    "Verdict",
    "Correctness",
    "SpecCompliance",
    "TestCoverage",
    "Integration",
    "SelfReview",
    "ChangeVerdict",
    "ModelReview",
    "AggregateReview",
]
