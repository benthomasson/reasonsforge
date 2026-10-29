"""TypeScript extractor — interfaces, CSS tokens, tests, and examples."""

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

SKIP_DIRS = {"node_modules", "dist", "build", ".next", "coverage", "__pycache__"}

_INTERFACE_RE = re.compile(
    r"(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+[^{]+)?\s*\{",
    re.MULTILINE,
)
_TYPE_ALIAS_RE = re.compile(
    r"(?:export\s+)?type\s+(\w+)\s*=\s*\{",
    re.MULTILINE,
)
_JSDOC_RE = re.compile(r"/\*\*\s*(.*?)\*/", re.DOTALL)
_PROP_RE = re.compile(
    r"^\s*(?:readonly\s+)?(\w+)(\??):\s*(.+?);\s*$",
)
_CSS_VAR_RE = re.compile(r"(--[\w-]+)\s*:\s*([^;]+);")
_TEST_DESC_RE = re.compile(
    r"""(?:it|test)\s*\(\s*(?:['"`])(.*?)(?:['"`])""",
)
_MD_CODE_BLOCK_RE = re.compile(
    r"```(?:tsx?|jsx?)\n(.*?)```",
    re.DOTALL,
)


def _iter_files(repo: Path, extensions: set[str]) -> list[Path]:
    """Recursively find files, skipping common non-source directories."""
    results = []
    for p in repo.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in extensions:
            results.append(p)
    return sorted(results)


def _extract_brace_block(text: str, start: int) -> str:
    """Extract text inside matched braces starting at `start` (the opening brace)."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return text[start + 1:]


def _parse_jsdoc_above(text: str, pos: int) -> str:
    """Find the JSDoc comment immediately above `pos`."""
    before = text[:pos].rstrip()
    m = re.search(r"/\*\*\s*(.*?)\*/\s*$", before, re.DOTALL)
    if m:
        lines = m.group(1).strip().splitlines()
        cleaned = []
        for line in lines:
            line = re.sub(r"^\s*\*\s?", "", line).strip()
            if line:
                cleaned.append(line)
        return " ".join(cleaned)
    return ""


def _parse_props(body: str) -> list[PropInfo]:
    """Parse property declarations from an interface/type body."""
    props = []
    lines = body.splitlines()
    jsdoc_buf = ""

    for line in lines:
        stripped = line.strip()

        jsdoc_inline = re.search(r"/\*\*\s*(.*?)\*/", stripped)
        if jsdoc_inline and not _PROP_RE.search(stripped):
            jsdoc_buf = re.sub(r"\s*\*\s*", " ", jsdoc_inline.group(1)).strip()
            continue

        if stripped.startswith("/**"):
            jsdoc_buf = ""
            continue
        if stripped.startswith("*"):
            text = re.sub(r"^\*\s?", "", stripped).strip()
            if text and not text.startswith("/"):
                jsdoc_buf = (jsdoc_buf + " " + text).strip() if jsdoc_buf else text
            continue
        if stripped == "*/":
            continue

        m = _PROP_RE.match(stripped)
        if m:
            name = m.group(1)
            optional = m.group(2) == "?"
            prop_type = m.group(3).strip().rstrip(";").strip()
            props.append(PropInfo(
                name=name,
                type=prop_type,
                required=not optional,
                description=jsdoc_buf,
            ))
            jsdoc_buf = ""
        elif stripped and not stripped.startswith("//"):
            jsdoc_buf = ""

    return props


def _matches_component(iface_name: str, components: list[str]) -> bool:
    """Check if an interface name matches the component filter."""
    lower = iface_name.lower()
    for comp in components:
        cl = comp.lower()
        if lower == cl or lower == cl + "props" or lower == cl + "Props":
            return True
    return False


class TypeScriptExtractor(Extractor):

    @property
    def name(self) -> str:
        return "typescript"

    def detect(self, repo_path: str) -> bool:
        return (Path(repo_path) / "tsconfig.json").exists()

    def extract_interfaces(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[InterfaceInfo]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".ts", ".tsx"})
        interfaces: list[InterfaceInfo] = []

        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            for pattern in (_INTERFACE_RE, _TYPE_ALIAS_RE):
                for m in pattern.finditer(text):
                    iface_name = m.group(1)

                    if components and not _matches_component(iface_name, components):
                        continue

                    body = _extract_brace_block(text, m.end() - 1)
                    props = _parse_props(body)
                    if not props:
                        continue

                    line_number = text[:m.start()].count("\n") + 1
                    description = _parse_jsdoc_above(text, m.start())

                    comp_name = None
                    if iface_name.endswith("Props"):
                        comp_name = iface_name[:-5]

                    rel_path = str(fpath.relative_to(repo))
                    interfaces.append(InterfaceInfo(
                        name=iface_name,
                        file_path=rel_path,
                        line_number=line_number,
                        props=props,
                        description=description,
                        component_name=comp_name,
                    ))

        return interfaces

    def extract_config(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[ConfigValue]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".css", ".scss"})
        values: list[ConfigValue] = []

        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))
            for m in _CSS_VAR_RE.finditer(text):
                var_name = m.group(1)
                var_value = m.group(2).strip()

                parts = var_name.split("--", 2)
                if len(parts) >= 3:
                    segments = parts[2].split("--")
                    category = segments[0] if segments else "other"
                else:
                    category = "other"

                values.append(ConfigValue(
                    name=var_name,
                    value=var_value,
                    category=category,
                    file_path=rel_path,
                ))

        return values

    def extract_tests(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[TestPattern]:
        repo = Path(repo_path)
        all_files = _iter_files(repo, {".ts", ".tsx"})
        test_files = [
            f for f in all_files
            if ".test." in f.name or ".spec." in f.name
            or f.name.startswith("test_") or "/test/" in str(f)
            or "/__tests__/" in str(f)
        ]

        if components:
            lower_comps = [c.lower() for c in components]
            filtered = []
            for f in test_files:
                stem = f.stem.lower().replace(".test", "").replace(".spec", "")
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

            for m in _TEST_DESC_RE.finditer(text):
                desc = m.group(1)
                line_number = text[:m.start()].count("\n") + 1

                lower_desc = desc.lower()
                if any(kw in lower_desc for kw in ("error", "disabled", "invalid", "fail", "throw")):
                    ptype = "error_case"
                elif any(kw in lower_desc for kw in ("should not", "must not", "cannot", "does not")):
                    ptype = "invalid_usage"
                elif any(kw in lower_desc for kw in ("edge", "boundary", "empty", "null", "undefined")):
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
            for fpath in sorted(d.rglob("*")):
                if any(part in SKIP_DIRS for part in fpath.parts):
                    continue
                if fpath.suffix in (".tsx", ".jsx") and fpath.is_file():
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
