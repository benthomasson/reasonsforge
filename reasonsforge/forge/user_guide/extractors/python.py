"""Python extractor — dataclasses, TypedDict, pydantic, function signatures, pytest."""

from __future__ import annotations

import re
from pathlib import Path

from .base import (
    ConfigValue,
    Extractor,
    InterfaceInfo,
    PropInfo,
    TestPattern,
)

SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".tox", ".nox",
             "dist", "build", ".eggs", ".mypy_cache", ".ruff_cache"}

_DATACLASS_RE = re.compile(
    r"@dataclass(?:es\.dataclass)?(?:\([^)]*\))?\s*\n\s*class\s+(\w+)",
    re.MULTILINE,
)
_PYDANTIC_RE = re.compile(
    r"class\s+(\w+)\s*\([^)]*(?:pydantic\.)?BaseModel[^)]*\)",
    re.MULTILINE,
)
_TYPEDDICT_RE = re.compile(
    r"class\s+(\w+)\s*\([^)]*(?:typing\.)?TypedDict[^)]*\)",
    re.MULTILINE,
)
_FIELD_RE = re.compile(
    r"^\s{4}(\w+)\s*:\s*(.+?)(?:\s*=\s*(.+))?$",
)
_FIELD_WITH_FIELD_CALL = re.compile(
    r"^\s{4}(\w+)\s*:\s*(.+?)\s*=\s*(?:field|Field)\s*\((.+)\)\s*$",
)
_PYTEST_DESC_RE = re.compile(
    r"^def\s+(test_\w+)",
    re.MULTILINE,
)
_ENV_VAR_RE = re.compile(
    r"""os\.(?:environ|getenv)\s*(?:\[|\.get\s*\()\s*['"](\w+)['"]""",
)
_CONSTANT_RE = re.compile(
    r"^([A-Z][A-Z0-9_]{2,})\s*(?::\s*\w+\s*)?=\s*(.+)$",
    re.MULTILINE,
)
_MD_CODE_BLOCK_RE = re.compile(
    r"```python\n(.*?)```",
    re.DOTALL,
)


def _iter_files(repo: Path, extensions: set[str]) -> list[Path]:
    results = []
    for p in repo.rglob("*"):
        if any(part in SKIP_DIRS or part.endswith(".egg-info") for part in p.parts):
            continue
        if p.is_file() and p.suffix in extensions:
            results.append(p)
    return sorted(results)


def _extract_docstring(text: str, class_end: int) -> str:
    """Extract the docstring immediately after a class/def line."""
    rest = text[class_end:]
    m = re.match(r'\s*:\s*\n\s+(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', rest, re.DOTALL)
    if m:
        doc = (m.group(1) or m.group(2) or "").strip()
        lines = doc.splitlines()
        return lines[0] if lines else ""
    return ""


def _extract_class_body(text: str, class_match_end: int) -> str:
    """Extract the indented body of a class definition."""
    rest = text[class_match_end:]
    colon_match = re.search(r":\s*(?:#.*)?\n", rest)
    if not colon_match:
        return ""
    rest = rest[colon_match.end():]
    lines = rest.split("\n")
    body_lines = []
    for line in lines:
        if line and not line[0].isspace() and line.strip():
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _parse_fields(body: str) -> list[PropInfo]:
    """Parse field declarations from a class body."""
    props = []
    lines = body.splitlines()
    docstring_buf = ""

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith('"""') or stripped.startswith("'''"):
            quote = stripped[:3]
            if stripped.count(quote) >= 2:
                docstring_buf = stripped.strip(quote[0]).strip()
            else:
                buf = [stripped.lstrip(quote[0])]
                i += 1
                while i < len(lines):
                    if quote in lines[i]:
                        break
                    buf.append(lines[i].strip())
                    i += 1
                docstring_buf = " ".join(buf).strip()
            i += 1
            continue

        if stripped.startswith("#"):
            comment = stripped.lstrip("#").strip()
            if comment:
                docstring_buf = comment
            i += 1
            continue

        m = _FIELD_WITH_FIELD_CALL.match(line)
        if m:
            name = m.group(1)
            ftype = m.group(2).strip()
            field_args = m.group(3)
            required = "default" not in field_args and "default_factory" not in field_args
            default_val = None
            dm = re.search(r"default\s*=\s*([^,)]+)", field_args)
            if dm:
                default_val = dm.group(1).strip()
            props.append(PropInfo(
                name=name, type=ftype, required=required,
                default_value=default_val, description=docstring_buf,
            ))
            docstring_buf = ""
            i += 1
            continue

        m = _FIELD_RE.match(line)
        if m:
            name = m.group(1)
            if name.startswith("_"):
                i += 1
                continue
            ftype = m.group(2).strip()
            default = m.group(3)
            optional = ftype.startswith("Optional") or "| None" in ftype
            required = default is None and not optional
            default_val = default.strip() if default else None
            props.append(PropInfo(
                name=name, type=ftype, required=required,
                default_value=default_val, description=docstring_buf,
            ))
            docstring_buf = ""

        elif stripped and not stripped.startswith("def ") and not stripped.startswith("@"):
            docstring_buf = ""

        i += 1

    return props


def _matches_component(name: str, components: list[str]) -> bool:
    lower = name.lower()
    for comp in components:
        if lower == comp.lower():
            return True
    return False


def _humanize_test_name(name: str) -> str:
    """Convert test_foo_bar_baz to 'foo bar baz'."""
    s = name.removeprefix("test_")
    return s.replace("_", " ")


class PythonExtractor(Extractor):

    @property
    def name(self) -> str:
        return "python"

    def detect(self, repo_path: str) -> bool:
        root = Path(repo_path)
        return any(
            (root / f).exists()
            for f in ("pyproject.toml", "setup.py", "setup.cfg")
        )

    def extract_interfaces(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[InterfaceInfo]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".py"})
        interfaces: list[InterfaceInfo] = []

        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))

            for pattern in (_DATACLASS_RE, _PYDANTIC_RE, _TYPEDDICT_RE):
                for m in pattern.finditer(text):
                    class_name = m.group(1)

                    if components and not _matches_component(class_name, components):
                        continue

                    line_number = text[:m.start()].count("\n") + 1
                    body = _extract_class_body(text, m.end())
                    props = _parse_fields(body)

                    if not props:
                        continue

                    description = _extract_docstring(text, m.end())

                    interfaces.append(InterfaceInfo(
                        name=class_name,
                        file_path=rel_path,
                        line_number=line_number,
                        props=props,
                        description=description,
                        component_name=class_name,
                    ))

        return interfaces

    def extract_config(self, repo_path: str) -> list[ConfigValue]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".py"})
        values: list[ConfigValue] = []

        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))

            for m in _CONSTANT_RE.finditer(text):
                name = m.group(1)
                value = m.group(2).strip()
                if value.startswith(("import", "from", "class", "def")):
                    continue
                values.append(ConfigValue(
                    name=name, value=value, category="constant",
                    file_path=rel_path,
                ))

            for m in _ENV_VAR_RE.finditer(text):
                values.append(ConfigValue(
                    name=m.group(1), value="(env)", category="environment",
                    file_path=rel_path,
                ))

        return values

    def extract_tests(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[TestPattern]:
        repo = Path(repo_path)
        all_files = _iter_files(repo, {".py"})
        test_files = [
            f for f in all_files
            if f.name.startswith("test_") or f.name.endswith("_test.py")
            or "/tests/" in str(f) or "/test/" in str(f)
        ]

        if components:
            lower_comps = [c.lower() for c in components]
            filtered = []
            for f in test_files:
                stem = f.stem.lower().replace("test_", "").replace("_test", "")
                if any(c in stem for c in lower_comps):
                    filtered.append(f)
            test_files = filtered

        patterns: list[TestPattern] = []
        for fpath in test_files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))

            for m in _PYTEST_DESC_RE.finditer(text):
                func_name = m.group(1)
                desc = _humanize_test_name(func_name)
                line_number = text[:m.start()].count("\n") + 1

                lower_desc = desc.lower()
                if any(kw in lower_desc for kw in ("error", "invalid", "fail", "raise", "exception")):
                    ptype = "error_case"
                elif any(kw in lower_desc for kw in ("not", "cannot", "without", "missing")):
                    ptype = "invalid_usage"
                elif any(kw in lower_desc for kw in ("edge", "boundary", "empty", "none", "zero")):
                    ptype = "edge_case"
                else:
                    ptype = "valid_usage"

                patterns.append(TestPattern(
                    description=desc,
                    file_path=rel_path,
                    line_number=line_number,
                    pattern_type=ptype,
                ))

        return patterns

    def extract_examples(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[str]:
        repo = Path(repo_path)
        examples: list[str] = []

        example_dirs = []
        for name in ("examples", "demo", "demos", "docs"):
            d = repo / name
            if d.is_dir():
                example_dirs.append(d)

        for d in example_dirs:
            for fpath in sorted(d.rglob("*.md")):
                if any(part in SKIP_DIRS for part in fpath.parts):
                    continue
                try:
                    text = fpath.read_text(errors="replace")
                except OSError:
                    continue
                for m in _MD_CODE_BLOCK_RE.finditer(text):
                    snippet = m.group(1).strip()
                    if snippet and len(snippet) > 20:
                        if components:
                            lower = snippet.lower()
                            if any(c.lower() in lower for c in components):
                                examples.append(snippet)
                        else:
                            examples.append(snippet)

        for d in example_dirs:
            for fpath in sorted(d.rglob("*.py")):
                if any(part in SKIP_DIRS for part in fpath.parts):
                    continue
                try:
                    text = fpath.read_text(errors="replace")
                except OSError:
                    continue
                if len(text) > 50:
                    if components:
                        lower = text.lower()
                        if any(c.lower() in lower for c in components):
                            examples.append(text)
                    else:
                        examples.append(text)

        return examples
