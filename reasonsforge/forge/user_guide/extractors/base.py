"""Base classes for language-specific extractors."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PropInfo:
    """A single property/parameter extracted from an interface or type."""
    name: str
    type: str = ""
    required: bool = True
    default_value: str | None = None
    description: str = ""


@dataclass
class InterfaceInfo:
    """An interface, struct, or type definition extracted from source code."""
    name: str
    file_path: str = ""
    line_number: int = 0
    props: list[PropInfo] = field(default_factory=list)
    description: str = ""
    component_name: str | None = None


@dataclass
class ConfigValue:
    """A configuration value, design token, or constant."""
    name: str
    value: str = ""
    category: str = ""
    file_path: str = ""


@dataclass
class TestPattern:
    """A usage pattern extracted from test files."""
    description: str
    file_path: str = ""
    line_number: int = 0
    pattern_type: str = "valid_usage"


class Extractor(ABC):
    """Abstract base for language-specific extractors."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Language name (e.g. 'typescript', 'python')."""

    @abstractmethod
    def detect(self, repo_path: str) -> bool:
        """Return True if this language is present in the repo."""

    @abstractmethod
    def extract_interfaces(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[InterfaceInfo]:
        """Extract interface/type definitions from the codebase."""

    @abstractmethod
    def extract_config(self, repo_path: str) -> list[ConfigValue]:
        """Extract configuration values, design tokens, or constants."""

    @abstractmethod
    def extract_tests(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[TestPattern]:
        """Extract usage patterns from test files."""

    @abstractmethod
    def extract_examples(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[str]:
        """Extract raw example code snippets from docs/demos."""


def detect_language(repo_path: str) -> str | None:
    """Detect primary language from repo structure."""
    markers = {
        "typescript": ["tsconfig.json"],
        "python": ["pyproject.toml", "setup.py", "setup.cfg"],
        "go": ["go.mod"],
        "rust": ["Cargo.toml"],
        "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
    }
    root = Path(repo_path)
    for lang, files in markers.items():
        for f in files:
            if (root / f).exists():
                return lang
    return None


def get_extractor(language: str) -> Extractor:
    """Get extractor for the given language."""
    if language == "typescript":
        from .typescript import TypeScriptExtractor
        return TypeScriptExtractor()
    raise ValueError(f"No extractor for language: {language}")
