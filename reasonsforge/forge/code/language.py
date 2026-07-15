"""Language profiles for multi-language code analysis."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LanguageProfile:
    name: str
    source_globs: list[str]
    source_extensions: list[str]
    fence_language: str
    definition_patterns: list[str]
    import_line_prefixes: list[str]
    scope_style: str  # "indent" or "brace"
    test_globs: list[str]
    entry_point_candidates: list[str]
    config_files: list[str]
    primary_extension: str
    config_entry_point_marker: str | None = None
    decorator_prefix: str | None = None

    def matches_import(self, line: str) -> bool:
        stripped = line.strip()
        return any(stripped.startswith(p) for p in self.import_line_prefixes)

    def module_name_from_path(self, rel_path: str) -> str:
        if rel_path.endswith(self.primary_extension):
            name = rel_path[:-len(self.primary_extension)]
        else:
            name = os.path.splitext(rel_path)[0]
        if self.name == "python":
            return name.replace("/", ".").replace(".__init__", "")
        return os.path.basename(name)


PYTHON = LanguageProfile(
    name="python",
    source_globs=["*.py"],
    source_extensions=[".py"],
    fence_language="python",
    definition_patterns=[
        r"^(class|def|async def) {symbol}[(:  ]",
        r"^{symbol}\s*=",
    ],
    import_line_prefixes=["import ", "from "],
    scope_style="indent",
    test_globs=["test_*.py", "*_test.py"],
    entry_point_candidates=[
        "src/main.py", "main.py", "app.py", "src/app.py",
        "manage.py", "setup.py", "cli.py",
    ],
    config_files=["pyproject.toml", "setup.py", "setup.cfg"],
    primary_extension=".py",
    config_entry_point_marker="[project.scripts]",
    decorator_prefix="@",
)

CPP = LanguageProfile(
    name="cpp",
    source_globs=["*.cpp", "*.cc", "*.cxx", "*.c", "*.h", "*.hpp", "*.hh", "*.hxx"],
    source_extensions=[".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh", ".hxx"],
    fence_language="cpp",
    definition_patterns=[
        r"^(class|struct|enum)\s+{symbol}\b",
        r"^namespace\s+{symbol}\b",
        r"^#define\s+{symbol}\b",
        r"^(\w[\w:*& ]*\s+)?{symbol}\s*\(",
    ],
    import_line_prefixes=["#include"],
    scope_style="brace",
    test_globs=["test_*.cpp", "*_test.cpp", "test_*.cc", "*_test.cc"],
    entry_point_candidates=[
        "src/main.cpp", "main.cpp", "src/main.cc", "main.cc",
        "src/main.c", "main.c",
    ],
    config_files=["CMakeLists.txt", "meson.build"],
    primary_extension=".cpp",
    config_entry_point_marker=None,
    decorator_prefix=None,
)

RUST = LanguageProfile(
    name="rust",
    source_globs=["*.rs"],
    source_extensions=[".rs"],
    fence_language="rust",
    definition_patterns=[
        r"^pub(\s*\(crate\))?\s*(async\s+)?fn\s+{symbol}\b",
        r"^(async\s+)?fn\s+{symbol}\b",
        r"^pub(\s*\(crate\))?\s*(struct|enum|trait|type|const|static|mod)\s+{symbol}\b",
        r"^(struct|enum|trait|type|const|static|mod)\s+{symbol}\b",
        r"^impl(<.*>)?\s+{symbol}\b",
        r"^macro_rules!\s+{symbol}\b",
    ],
    import_line_prefixes=["use ", "mod "],
    scope_style="brace",
    test_globs=["*_test.rs", "test_*.rs"],
    entry_point_candidates=[
        "src/main.rs", "src/lib.rs",
    ],
    config_files=["Cargo.toml"],
    primary_extension=".rs",
    config_entry_point_marker="[[bin]]",
    decorator_prefix="#[",
)

LANGUAGE_REGISTRY: dict[str, LanguageProfile] = {
    "python": PYTHON,
    "cpp": CPP,
    "rust": RUST,
}

_CONFIG_TO_LANGUAGE: dict[str, str] = {
    "pyproject.toml": "python",
    "setup.py": "python",
    "setup.cfg": "python",
    "CMakeLists.txt": "cpp",
    "meson.build": "cpp",
    "package.json": "javascript",
    "Cargo.toml": "rust",
    "go.mod": "go",
    "pom.xml": "java",
    "build.gradle": "java",
}


def detect_language(repo_path: str) -> LanguageProfile:
    """Auto-detect the repo's primary language. Falls back to Python."""
    for config_file, lang_name in _CONFIG_TO_LANGUAGE.items():
        if os.path.isfile(os.path.join(repo_path, config_file)):
            if lang_name in LANGUAGE_REGISTRY:
                return LANGUAGE_REGISTRY[lang_name]

    from .git_utils import list_source_files

    try:
        files = list_source_files(repo_path)
    except Exception:
        return PYTHON

    ext_counts: dict[str, int] = {}
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        if ext:
            ext_counts[ext] = ext_counts.get(ext, 0) + 1

    best_lang = None
    best_count = 0
    for lang in LANGUAGE_REGISTRY.values():
        count = sum(ext_counts.get(ext, 0) for ext in lang.source_extensions)
        if count > best_count:
            best_count = count
            best_lang = lang

    return best_lang or PYTHON


def get_grep_include_args(lang: LanguageProfile) -> list[str]:
    return [f"--include={g}" for g in lang.source_globs]


def _extract_symbol_indent(file_path: str, symbol: str, lang: LanguageProfile) -> str | None:
    """Indent-based scope extraction (Python)."""
    from .git_utils import get_file_content

    content = get_file_content(file_path)
    if content is None:
        return None

    lines = content.split("\n")
    result_lines: list[str] = []
    capturing = False
    base_indent = None

    compiled = [re.compile(p.format(symbol=re.escape(symbol))) for p in lang.definition_patterns]

    for line in lines:
        stripped = line.lstrip()

        if not capturing:
            if any(pat.match(stripped) for pat in compiled):
                capturing = True
                base_indent = len(line) - len(stripped)
                result_lines.append(line)
                continue

        if capturing:
            if not stripped:
                result_lines.append(line)
                continue

            current_indent = len(line) - len(stripped)
            if current_indent <= base_indent and stripped and not stripped.startswith("#"):
                break

            result_lines.append(line)

    if not result_lines:
        return None
    return "\n".join(result_lines)


def _extract_symbol_brace(file_path: str, symbol: str, lang: LanguageProfile) -> str | None:
    """Brace-counting scope extraction (C/C++/Java/JS/etc.)."""
    from .git_utils import get_file_content

    content = get_file_content(file_path)
    if content is None:
        return None

    lines = content.split("\n")
    compiled = [re.compile(p.format(symbol=re.escape(symbol))) for p in lang.definition_patterns]

    start_idx = None
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if any(pat.match(stripped) for pat in compiled):
            start_idx = i
            break

    if start_idx is None:
        return None

    result_lines: list[str] = []
    brace_depth = 0
    found_open = False
    in_block_comment = False

    for i in range(start_idx, len(lines)):
        line = lines[i]
        result_lines.append(line)

        j = 0
        while j < len(line):
            if in_block_comment:
                if line[j:j + 2] == "*/":
                    in_block_comment = False
                    j += 2
                    continue
                j += 1
                continue

            if line[j:j + 2] == "//":
                break
            if line[j:j + 2] == "/*":
                in_block_comment = True
                j += 2
                continue

            if line[j] == '"' or line[j] == "'":
                quote = line[j]
                j += 1
                while j < len(line) and line[j] != quote:
                    if line[j] == "\\":
                        j += 1
                    j += 1
                j += 1
                continue

            if line[j] == ";" and not found_open:
                return "\n".join(result_lines)

            if line[j] == "{":
                brace_depth += 1
                found_open = True
            elif line[j] == "}":
                brace_depth -= 1

            j += 1

        if found_open and brace_depth <= 0:
            break

    if not result_lines:
        return None
    return "\n".join(result_lines)


def extract_symbol_with_profile(
    file_path: str, symbol: str, lang: LanguageProfile | None = None,
) -> str | None:
    lang = lang or PYTHON
    if lang.scope_style == "indent":
        return _extract_symbol_indent(file_path, symbol, lang)
    return _extract_symbol_brace(file_path, symbol, lang)
