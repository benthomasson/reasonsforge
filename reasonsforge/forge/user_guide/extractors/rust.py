"""Rust extractor — structs, enums, traits, const/static, tests, doc comments."""

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

SKIP_DIRS = {"target", "node_modules", ".git", "vendor"}

_STRUCT_RE = re.compile(
    r"(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)(?:<[^>]*>)?\s*\{",
    re.MULTILINE,
)
_ENUM_RE = re.compile(
    r"(?:pub(?:\([^)]*\))?\s+)?enum\s+(\w+)(?:<[^>]*>)?\s*\{",
    re.MULTILINE,
)
_DERIVE_RE = re.compile(
    r"#\[derive\(([^)]+)\)\]",
)
_SERDE_ATTR_RE = re.compile(
    r"#\[serde\(([^)]+)\)\]",
)
_FIELD_RE = re.compile(
    r"^\s+(?:pub(?:\([^)]*\))?\s+)?(\w+)\s*:\s*(.+?)\s*,?\s*$",
)
_CONST_RE = re.compile(
    r"^(?:pub\s+)?(?:const|static)\s+(\w+)\s*:\s*([^=]+?)\s*=\s*(.+?)\s*;",
    re.MULTILINE,
)
_TEST_FN_RE = re.compile(
    r"#\[test\]\s*(?:#\[[\w(,\s)]+\]\s*)*fn\s+(\w+)",
    re.MULTILINE,
)
_DOC_COMMENT_RE = re.compile(
    r"(?:^\s*///\s?(.*)$\n)+",
    re.MULTILINE,
)
_MD_CODE_BLOCK_RE = re.compile(
    r"```rust\n(.*?)```",
    re.DOTALL,
)


def _iter_files(repo: Path, extensions: set[str]) -> list[Path]:
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


def _extract_doc_comment(text: str, pos: int) -> str:
    """Extract /// doc comments immediately above `pos`."""
    before = text[:pos].rstrip()
    lines = before.split("\n")
    doc_lines = []
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("///"):
            doc_lines.append(stripped.lstrip("/").strip())
        elif stripped.startswith("#["):
            continue
        else:
            break
    if not doc_lines:
        return ""
    doc_lines.reverse()
    return " ".join(line for line in doc_lines if line)


def _extract_derives(text: str, pos: int) -> list[str]:
    """Extract #[derive(...)] traits from attributes above `pos`."""
    before = text[:pos]
    derives = []
    for m in _DERIVE_RE.finditer(before):
        if m.end() <= pos and pos - m.end() < 200:
            for trait in m.group(1).split(","):
                t = trait.strip()
                if t:
                    derives.append(t)
    return derives


def _extract_serde_attrs(text: str, pos: int) -> list[str]:
    """Extract #[serde(...)] attributes from above `pos`."""
    before = text[:pos]
    attrs = []
    for m in _SERDE_ATTR_RE.finditer(before):
        if m.end() <= pos and pos - m.end() < 200:
            attrs.append(m.group(1).strip())
    return attrs


def _parse_struct_fields(body: str) -> list[PropInfo]:
    """Parse field declarations from a struct body."""
    props = []
    doc_buf = ""

    for line in body.splitlines():
        stripped = line.strip()

        if stripped.startswith("///"):
            comment = stripped.lstrip("/").strip()
            doc_buf = (doc_buf + " " + comment).strip() if doc_buf else comment
            continue

        if stripped.startswith("#["):
            serde_m = _SERDE_ATTR_RE.search(stripped)
            if serde_m:
                doc_buf = (doc_buf + f" [serde: {serde_m.group(1)}]").strip()
            continue

        m = _FIELD_RE.match(line)
        if m:
            name = m.group(1)
            ftype = m.group(2).strip().rstrip(",")

            is_option = ftype.startswith("Option<")
            required = not is_option

            default_val = None
            if is_option:
                default_val = "None"

            is_pub = "pub" in line.split(name)[0] if name in line else False
            if not is_pub:
                doc_buf = ""
                continue

            props.append(PropInfo(
                name=name,
                type=ftype,
                required=required,
                default_value=default_val,
                description=doc_buf,
            ))
            doc_buf = ""
        elif stripped and not stripped.startswith("//"):
            doc_buf = ""

    return props


def _parse_enum_variants(body: str) -> list[PropInfo]:
    """Parse enum variants as props (name=variant, type=payload)."""
    props = []
    doc_buf = ""

    for line in body.splitlines():
        stripped = line.strip()

        if stripped.startswith("///"):
            comment = stripped.lstrip("/").strip()
            doc_buf = (doc_buf + " " + comment).strip() if doc_buf else comment
            continue

        if stripped.startswith("#["):
            continue

        variant_m = re.match(r"(\w+)(?:\s*\{[^}]*\}|\s*\([^)]*\))?\s*,?\s*$", stripped)
        if variant_m and stripped[0].isupper():
            name = variant_m.group(0).rstrip(",").strip()
            variant_name = variant_m.group(1)

            payload = ""
            if "{" in name:
                payload = "struct variant"
            elif "(" in name:
                payload = "tuple variant"
            else:
                payload = "unit variant"

            props.append(PropInfo(
                name=variant_name,
                type=payload,
                required=True,
                description=doc_buf,
            ))
            doc_buf = ""
        elif stripped:
            doc_buf = ""

    return props


def _matches_component(name: str, components: list[str]) -> bool:
    lower = name.lower()
    for comp in components:
        if lower == comp.lower():
            return True
    return False


def _humanize_test_name(name: str) -> str:
    s = name.removeprefix("test_")
    return s.replace("_", " ")


class RustExtractor(Extractor):

    @property
    def name(self) -> str:
        return "rust"

    def detect(self, repo_path: str) -> bool:
        return (Path(repo_path) / "Cargo.toml").exists()

    def extract_interfaces(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[InterfaceInfo]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".rs"})
        interfaces: list[InterfaceInfo] = []

        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))

            for pattern, parser in [
                (_STRUCT_RE, _parse_struct_fields),
                (_ENUM_RE, _parse_enum_variants),
            ]:
                for m in pattern.finditer(text):
                    struct_name = m.group(1)

                    if components and not _matches_component(struct_name, components):
                        continue

                    body = _extract_brace_block(text, m.end() - 1)
                    props = parser(body)
                    if not props:
                        continue

                    line_number = text[:m.start()].count("\n") + 1
                    description = _extract_doc_comment(text, m.start())

                    derives = _extract_derives(text, m.start())
                    if derives:
                        description = (description + f" [derives: {', '.join(derives)}]").strip()

                    interfaces.append(InterfaceInfo(
                        name=struct_name,
                        file_path=rel_path,
                        line_number=line_number,
                        props=props,
                        description=description,
                        component_name=struct_name,
                    ))

        return interfaces

    def extract_config(self, repo_path: str) -> list[ConfigValue]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".rs"})
        values: list[ConfigValue] = []

        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))

            for m in _CONST_RE.finditer(text):
                name = m.group(1)
                ctype = m.group(2).strip()
                value = m.group(3).strip().rstrip(";")

                category = "static" if "static" in text[m.start():m.start() + 10] else "const"

                values.append(ConfigValue(
                    name=name,
                    value=f"{value} ({ctype})",
                    category=category,
                    file_path=rel_path,
                ))

        # Also extract from Cargo.toml features
        cargo_path = repo / "Cargo.toml"
        if cargo_path.exists():
            try:
                cargo_text = cargo_path.read_text(errors="replace")
                in_features = False
                for line in cargo_text.splitlines():
                    stripped = line.strip()
                    if stripped == "[features]":
                        in_features = True
                        continue
                    if stripped.startswith("[") and in_features:
                        break
                    if in_features and "=" in stripped:
                        fname, fval = stripped.split("=", 1)
                        values.append(ConfigValue(
                            name=fname.strip(),
                            value=fval.strip(),
                            category="feature",
                            file_path="Cargo.toml",
                        ))
            except OSError:
                pass

        return values

    def extract_tests(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[TestPattern]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".rs"})

        if components:
            lower_comps = [c.lower() for c in components]

        patterns: list[TestPattern] = []
        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            if "#[test]" not in text and "#[cfg(test)]" not in text:
                continue

            rel_path = str(fpath.relative_to(repo))

            if components:
                stem = fpath.stem.lower()
                if not any(c in stem for c in lower_comps):
                    continue

            for m in _TEST_FN_RE.finditer(text):
                func_name = m.group(1)
                desc = _humanize_test_name(func_name)
                line_number = text[:m.start()].count("\n") + 1

                lower_desc = desc.lower()
                if any(kw in lower_desc for kw in ("error", "invalid", "fail", "panic", "err")):
                    ptype = "error_case"
                elif any(kw in lower_desc for kw in ("not", "cannot", "without", "missing")):
                    ptype = "invalid_usage"
                elif any(kw in lower_desc for kw in ("edge", "boundary", "empty", "none", "zero", "default")):
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

        # examples/ directory (Cargo convention)
        example_dirs = []
        for name in ("examples", "docs"):
            d = repo / name
            if d.is_dir():
                example_dirs.append(d)

        for d in example_dirs:
            for fpath in sorted(d.rglob("*.rs")):
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

        return examples
