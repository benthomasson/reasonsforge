"""Rust extractor — structs, enums, const/static, tests, doc comments."""

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
    r"^(?:pub(?:\([^)]*\))?\s+)?(?:const|static)\s+(\w+)\s*:\s*([^=]+?)\s*=\s*(.+?)\s*;",
    re.MULTILINE,
)
_TEST_FN_RE = re.compile(
    r"#\[test\]\s*(?:#\[[\w(,\s)]+\]\s*)*fn\s+(\w+)",
    re.MULTILINE,
)
_MD_CODE_BLOCK_RE = re.compile(
    r"```rust\n(.*?)```",
    re.DOTALL,
)
_DECLARE_CLASS_RE = re.compile(
    r"declare_class!\s*\(",
    re.MULTILINE,
)
_CLASS_NAME_RE = re.compile(
    r"""const\s+NAME\s*:\s*&'static\s+str\s*=\s*"(\w+)"\s*;""",
)
_CLASS_SUPER_RE = re.compile(
    r"type\s+Super\s*=\s*(\w+)\s*;",
)
_CLASS_INHERITS_RE = re.compile(
    r"#\[inherits\(([^)]+)\)\]",
)
_UNSAFE_IMPL_RE = re.compile(
    r"unsafe\s+impl\s+(\w+)\s+for\s+(\w+)",
)
_METHOD_RE = re.compile(
    r"""#\[method\((\w+(?::[\w]*)*)\)\]\s*fn\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*(\S+))?""",
)
_METHOD_ID_RE = re.compile(
    r"""#\[method_id\(([^)]+)\)\]\s*(?:pub\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*(.+?))\s*[{;]""",
    re.DOTALL,
)
_EXTERN_CLASS_RE = re.compile(
    r"extern_class!\s*\(",
    re.MULTILINE,
)
_EXTERN_METHODS_RE = re.compile(
    r"extern_methods!\s*\(",
    re.MULTILINE,
)
_EXTERN_PROTOCOL_RE = re.compile(
    r"extern_protocol!\s*\(",
    re.MULTILINE,
)
_PROTOCOL_TRAIT_RE = re.compile(
    r"pub\s+unsafe\s+trait\s+(\w+)\s*:\s*([^{]+)\{",
)
_EXTERN_IMPL_RE = re.compile(
    r"unsafe\s+impl\s+(\w+)\s*\{",
)
_OPTIONAL_RE = re.compile(r"#\[optional\]")


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


def _extract_paren_block(text: str, start: int) -> str:
    """Extract text inside matched parens starting at `start` (the opening paren)."""
    depth = 0
    i = start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
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
    """Extract #[derive(...)] traits from attribute block directly above `pos`."""
    before = text[:pos].rstrip()
    lines = before.split("\n")
    derives = []
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("#["):
            m = _DERIVE_RE.search(stripped)
            if m:
                for trait in m.group(1).split(","):
                    t = trait.strip()
                    if t:
                        derives.append(t)
        elif stripped.startswith("///") or stripped.startswith("//"):
            continue
        else:
            break
    return derives


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


def _matches_component(name: str, components: list[str], file_path: str = "") -> bool:
    """Match by struct/class name OR by file path containing the component."""
    lower = name.lower()
    lower_path = file_path.lower()
    for comp in components:
        c = comp.lower()
        if lower == c:
            return True
        if c in lower_path:
            return True
    return False


def _parse_declare_class(text: str, start: int, rel_path: str) -> InterfaceInfo | None:
    """Parse a declare_class!(...) macro invocation into an InterfaceInfo."""
    body = _extract_brace_block(text, start)
    if not body:
        paren_start = text.find("(", start)
        if paren_start < 0:
            return None
        # declare_class! uses () not {} — find matching paren
        depth = 0
        i = paren_start
        while i < len(text):
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    body = text[paren_start + 1:i]
                    break
            i += 1
        if not body:
            return None

    # Extract Rust struct name
    struct_m = re.search(r"(?:pub(?:\([^)]*\))?\s+)?struct\s+(\w+)", body)
    rust_name = struct_m.group(1) if struct_m else "Unknown"

    # Extract ObjC runtime class name (const NAME)
    name_m = _CLASS_NAME_RE.search(body)
    objc_name = name_m.group(1) if name_m else rust_name

    # Extract superclass
    super_m = _CLASS_SUPER_RE.search(body)
    superclass = super_m.group(1) if super_m else ""

    # Extract #[inherits(...)] chain
    inherits_m = _CLASS_INHERITS_RE.search(body)
    inherits = []
    if inherits_m:
        inherits = [s.strip() for s in inherits_m.group(1).split(",")]

    # Extract protocol conformances (unsafe impl Protocol for Type)
    protocols = []
    for proto_m in _UNSAFE_IMPL_RE.finditer(body):
        proto_name = proto_m.group(1)
        if proto_name != rust_name and proto_name not in ("ClassType", "DeclaredClass", "NSObjectProtocol"):
            protocols.append(proto_name)

    # Extract methods (#[method(selector)] fn name)
    methods = []
    for method_m in _METHOD_RE.finditer(body):
        selector = method_m.group(1)
        fn_name = method_m.group(2)
        params = method_m.group(3).strip()
        ret = method_m.group(4) or "void"
        methods.append((selector, fn_name, params, ret))

    # Build props from the extracted information
    props = []
    props.append(PropInfo(
        name="objc_class_name",
        type="&'static str",
        required=True,
        description=f"ObjC runtime class name (use with objc_getClass). Rust struct name is '{rust_name}'.",
        default_value=f'"{objc_name}"',
    ))
    if superclass:
        chain = " > ".join([superclass] + inherits) if inherits else superclass
        props.append(PropInfo(
            name="superclass",
            type="ObjC class",
            required=True,
            description=f"Inheritance chain: {chain}",
        ))
    for proto in protocols:
        props.append(PropInfo(
            name=f"protocol_{proto}",
            type="protocol conformance",
            required=True,
            description=f"Conforms to {proto} protocol",
        ))
    for selector, fn_name, params, ret in methods:
        props.append(PropInfo(
            name=fn_name,
            type=f"fn({params}) -> {ret}",
            required=True,
            description=f"ObjC selector: {selector}",
        ))

    line_number = text[:start].count("\n") + 1
    description = _extract_doc_comment(text, start)
    if not description:
        desc_parts = [f"ObjC class '{objc_name}'"]
        if superclass:
            desc_parts.append(f"extends {superclass}")
        if protocols:
            desc_parts.append(f"conforms to {', '.join(protocols)}")
        description = " ".join(desc_parts)

    return InterfaceInfo(
        name=f"{rust_name} ({objc_name})",
        file_path=rel_path,
        line_number=line_number,
        props=props,
        description=description,
        component_name=rust_name,
    )


def _extract_methods_from_body(body: str) -> list[PropInfo]:
    """Extract #[method(...)] and #[method_id(...)] from an impl or trait body."""
    props: list[PropInfo] = []

    lines = body.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        is_optional = False
        if line == "#[optional]":
            is_optional = True
            i += 1
            if i >= len(lines):
                break
            line = lines[i].strip()

        # Skip #[cfg(...)] lines
        while line.startswith("#[cfg("):
            i += 1
            if i >= len(lines):
                break
            line = lines[i].strip()
        if i >= len(lines):
            break

        # Match #[method(selector)] or #[method_id(@... selector)]
        method_match = re.match(r"#\[method(?:_id)?\(([^)]+)\)\]", line)
        if method_match:
            selector_raw = method_match.group(1)
            # Strip @__retain_semantics prefix
            selector = re.sub(r"@__retain_semantics\s+\w+\s+", "", selector_raw).strip()

            # Find the fn on the next non-attribute lines
            j = i + 1
            while j < len(lines):
                fn_line = lines[j].strip()
                if fn_line.startswith("#[") or not fn_line:
                    j += 1
                    continue
                fn_match = re.match(
                    r"(?:pub\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*(.+?))?(?:\s*[{;]|$)",
                    fn_line,
                )
                if fn_match:
                    fn_name = fn_match.group(1)
                    params = fn_match.group(2).strip()
                    ret = fn_match.group(3) or "void"
                    ret = ret.rstrip("{;").strip()
                    desc = f"ObjC selector: {selector}"
                    if is_optional:
                        desc += " (optional)"
                    props.append(PropInfo(
                        name=fn_name,
                        type=f"fn({params}) -> {ret}",
                        required=not is_optional,
                        description=desc,
                    ))
                    i = j
                break
        i += 1

    return props


def _parse_extern_class(text: str, paren_start: int, rel_path: str) -> InterfaceInfo | None:
    """Parse an extern_class!(...) macro invocation."""
    body = _extract_paren_block(text, paren_start)
    if not body:
        return None

    struct_m = re.search(r"(?:pub\s+)?struct\s+(\w+)", body)
    if not struct_m:
        return None
    class_name = struct_m.group(1)

    super_m = _CLASS_SUPER_RE.search(body)
    superclass = super_m.group(1) if super_m else ""

    inherits_m = _CLASS_INHERITS_RE.search(body)
    inherits = []
    if inherits_m:
        inherits = [s.strip() for s in inherits_m.group(1).split(",")]

    props = []
    if superclass:
        chain = " > ".join([superclass] + inherits) if inherits else superclass
        props.append(PropInfo(
            name="superclass",
            type="ObjC class",
            required=True,
            description=f"Inheritance chain: {chain}",
        ))

    line_number = text[:paren_start].count("\n") + 1
    desc_parts = [f"ObjC class '{class_name}'"]
    if superclass:
        desc_parts.append(f"extends {superclass}")
    description = " ".join(desc_parts)

    return InterfaceInfo(
        name=class_name,
        file_path=rel_path,
        line_number=line_number,
        props=props,
        description=description,
        component_name=class_name,
    )


def _parse_extern_methods(text: str, paren_start: int, rel_path: str) -> tuple[str, list[PropInfo]]:
    """Parse an extern_methods!(...) block. Returns (class_name, methods)."""
    body = _extract_paren_block(text, paren_start)
    if not body:
        return ("", [])

    impl_m = _EXTERN_IMPL_RE.search(body)
    if not impl_m:
        return ("", [])

    class_name = impl_m.group(1)
    impl_body = _extract_brace_block(body, impl_m.end() - 1)
    methods = _extract_methods_from_body(impl_body)
    return (class_name, methods)


def _parse_extern_protocol(text: str, paren_start: int, rel_path: str) -> InterfaceInfo | None:
    """Parse an extern_protocol!(...) macro invocation."""
    body = _extract_paren_block(text, paren_start)
    if not body:
        return None

    trait_m = _PROTOCOL_TRAIT_RE.search(body)
    if not trait_m:
        return None

    protocol_name = trait_m.group(1)
    super_traits = trait_m.group(2).strip().rstrip("{").strip()

    trait_body = _extract_brace_block(body, trait_m.end() - 1)
    methods = _extract_methods_from_body(trait_body)

    props = []
    if super_traits:
        props.append(PropInfo(
            name="super_traits",
            type="protocol",
            required=True,
            description=f"Requires: {super_traits}",
        ))
    props.extend(methods)

    if not props:
        return None

    line_number = text[:paren_start].count("\n") + 1

    return InterfaceInfo(
        name=protocol_name,
        file_path=rel_path,
        line_number=line_number,
        props=props,
        description=f"ObjC protocol '{protocol_name}'",
        component_name=protocol_name,
    )


def _collect_protocol_conformances(text: str, class_name: str) -> list[str]:
    """Find `unsafe impl ProtocolName for ClassName {}` outside macro bodies."""
    protocols = []
    for m in re.finditer(
        rf"unsafe\s+impl\s+(\w+)\s+for\s+{re.escape(class_name)}\s*\{{\}}",
        text,
    ):
        proto = m.group(1)
        skip = {"ClassType", "DeclaredClass", "NSObjectProtocol", "Encode", "RefEncode"}
        if proto not in skip:
            protocols.append(proto)
    return protocols


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

            # Standard struct/enum extraction
            for pattern, parser in [
                (_STRUCT_RE, _parse_struct_fields),
                (_ENUM_RE, _parse_enum_variants),
            ]:
                for m in pattern.finditer(text):
                    struct_name = m.group(1)

                    if components and not _matches_component(struct_name, components, rel_path):
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

            # declare_class! macro extraction (ObjC interop — user-defined classes)
            for m in _DECLARE_CLASS_RE.finditer(text):
                if components and not _matches_component("", components, rel_path):
                    continue

                info = _parse_declare_class(text, m.start() + len("declare_class!") - 1, rel_path)
                if info:
                    if components and not _matches_component(info.component_name or "", components, rel_path):
                        continue
                    interfaces.append(info)

            # extern_class! macro extraction (ObjC interop — external class bindings)
            extern_class_map: dict[str, InterfaceInfo] = {}
            for m in _EXTERN_CLASS_RE.finditer(text):
                paren_start = text.find("(", m.start())
                if paren_start < 0:
                    continue
                info = _parse_extern_class(text, paren_start, rel_path)
                if info:
                    if components and not _matches_component(info.component_name or "", components, rel_path):
                        continue
                    # Collect protocol conformances from the file
                    protos = _collect_protocol_conformances(text, info.name)
                    for proto in protos:
                        info.props.append(PropInfo(
                            name=f"protocol_{proto}",
                            type="protocol conformance",
                            required=True,
                            description=f"Conforms to {proto} protocol",
                        ))
                    extern_class_map[info.name] = info

            # extern_methods! — attach methods to extern_class! entries
            for m in _EXTERN_METHODS_RE.finditer(text):
                paren_start = text.find("(", m.start())
                if paren_start < 0:
                    continue
                class_name, methods = _parse_extern_methods(text, paren_start, rel_path)
                if class_name and methods:
                    if class_name in extern_class_map:
                        extern_class_map[class_name].props.extend(methods)
                    elif not components or _matches_component(class_name, components, rel_path):
                        # Standalone extern_methods for a class defined elsewhere
                        extern_class_map[class_name] = InterfaceInfo(
                            name=class_name,
                            file_path=rel_path,
                            line_number=text[:m.start()].count("\n") + 1,
                            props=methods,
                            description=f"ObjC class '{class_name}' methods",
                            component_name=class_name,
                        )

            interfaces.extend(extern_class_map.values())

            # extern_protocol! macro extraction (ObjC protocol bindings)
            for m in _EXTERN_PROTOCOL_RE.finditer(text):
                paren_start = text.find("(", m.start())
                if paren_start < 0:
                    continue
                info = _parse_extern_protocol(text, paren_start, rel_path)
                if info:
                    if components and not _matches_component(info.component_name or "", components, rel_path):
                        continue
                    interfaces.append(info)

        return interfaces

    def extract_config(
        self, repo_path: str, components: list[str] | None = None,
    ) -> list[ConfigValue]:
        repo = Path(repo_path)
        files = _iter_files(repo, {".rs"})
        values: list[ConfigValue] = []

        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            rel_path = str(fpath.relative_to(repo))

            if components and not _matches_component("", components, rel_path):
                continue

            for m in _CONST_RE.finditer(text):
                name = m.group(1)
                ctype = m.group(2).strip()
                value = m.group(3).strip().rstrip(";")

                matched_text = m.group(0)
                category = "static" if "static" in matched_text else "const"

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

        patterns: list[TestPattern] = []
        for fpath in files:
            try:
                text = fpath.read_text(errors="replace")
            except OSError:
                continue

            if "#[test]" not in text and "#[cfg(test)]" not in text:
                continue

            rel_path = str(fpath.relative_to(repo))

            if components and not _matches_component(fpath.stem, components, rel_path):
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
