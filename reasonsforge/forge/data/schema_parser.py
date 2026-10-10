"""Parse database schemas from various sources into a normalized representation.

Supported sources:
- SQL DDL files (CREATE TABLE, ALTER TABLE)
- CSV headers (auto-detected types)
- Parquet metadata (via pyarrow)
- Live database introspection (SQLAlchemy)
"""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Column:
    name: str
    type: str
    nullable: bool = True
    default: str | None = None
    primary_key: bool = False
    comment: str | None = None


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    primary_key: list[str] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    unique_constraints: list[list[str]] = field(default_factory=list)
    comment: str | None = None


@dataclass
class Schema:
    tables: list[Table] = field(default_factory=list)
    source: str = ""

    def table_names(self) -> list[str]:
        return [t.name for t in self.tables]

    def get_table(self, name: str) -> Table | None:
        for t in self.tables:
            if t.name.lower() == name.lower():
                return t
        return None


# ---------------------------------------------------------------------------
# DDL parsing
# ---------------------------------------------------------------------------

_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"']?(\w+)[`\"']?\s*\(",
    re.IGNORECASE,
)

_COLUMN_RE = re.compile(
    r"^\s*[`\"']?(\w+)[`\"']?\s+"
    r"(\w+(?:\([^)]*\))?)"
    r"(.*?)$",
    re.IGNORECASE,
)


_ALTER_TABLE_RE = re.compile(
    r"ALTER\s+TABLE\s+[`\"']?(\w+)[`\"']?\s+ADD\s+COLUMN\s+[`\"']?(\w+)[`\"']?\s+"
    r"(\w+(?:\([^)]*\))?)(.*?);",
    re.IGNORECASE,
)


def parse_ddl(sql: str, source: str = "ddl") -> Schema:
    """Parse SQL DDL statements into a Schema."""
    schema = Schema(source=source)
    tables = _split_create_tables(sql)

    for table_name, body in tables:
        table = Table(name=table_name)
        for line in _split_columns(body):
            line = line.strip().rstrip(",")
            if not line:
                continue

            upper = line.upper().lstrip()
            if upper.startswith("PRIMARY KEY"):
                cols = _extract_paren_list(line)
                table.primary_key = cols
                continue
            if upper.startswith("FOREIGN KEY"):
                fk = _parse_fk_constraint(line)
                if fk:
                    table.foreign_keys.append(fk)
                continue
            if upper.startswith("UNIQUE"):
                cols = _extract_paren_list(line)
                if cols:
                    table.unique_constraints.append(cols)
                continue
            if upper.startswith("CONSTRAINT") or upper.startswith("CHECK"):
                continue

            m = _COLUMN_RE.match(line)
            if m:
                col_name = m.group(1)
                col_type = m.group(2)
                rest = m.group(3).upper()
                col = Column(
                    name=col_name,
                    type=col_type,
                    nullable="NOT NULL" not in rest,
                    primary_key="PRIMARY KEY" in rest,
                )
                if "DEFAULT" in rest:
                    default_match = re.search(r"DEFAULT\s+(\S+)", rest, re.IGNORECASE)
                    if default_match:
                        col.default = default_match.group(1).strip("'\"")
                table.columns.append(col)
                if col.primary_key:
                    table.primary_key.append(col_name)

        schema.tables.append(table)

    _apply_alter_tables(sql, schema)

    return schema


def _apply_alter_tables(sql: str, schema: Schema) -> None:
    """Apply ALTER TABLE ADD COLUMN statements to existing tables."""
    for m in _ALTER_TABLE_RE.finditer(sql):
        table_name = m.group(1)
        col_name = m.group(2)
        col_type = m.group(3)
        rest = m.group(4).upper()

        table = schema.get_table(table_name)
        if table is None:
            continue

        if any(c.name.lower() == col_name.lower() for c in table.columns):
            continue

        col = Column(
            name=col_name,
            type=col_type,
            nullable="NOT NULL" not in rest,
        )
        if "DEFAULT" in rest:
            default_match = re.search(r"DEFAULT\s+(\S+)", rest, re.IGNORECASE)
            if default_match:
                col.default = default_match.group(1).strip("'\"")
        table.columns.append(col)


def _split_columns(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas, respecting parenthesized type args."""
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _split_create_tables(sql: str) -> list[tuple[str, str]]:
    results = []
    for m in _CREATE_TABLE_RE.finditer(sql):
        table_name = m.group(1)
        start = m.end()
        depth = 1
        i = start
        while i < len(sql) and depth > 0:
            if sql[i] == "(":
                depth += 1
            elif sql[i] == ")":
                depth -= 1
            i += 1
        body = sql[start:i - 1]
        results.append((table_name, body))
    return results


def _extract_paren_list(text: str) -> list[str]:
    m = re.search(r"\(([^)]+)\)", text)
    if not m:
        return []
    return [c.strip().strip("`\"'") for c in m.group(1).split(",")]


def _parse_fk_constraint(line: str) -> ForeignKey | None:
    m = re.search(
        r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+[`\"']?(\w+)[`\"']?\s*\(([^)]+)\)",
        line, re.IGNORECASE,
    )
    if not m:
        return None
    cols = [c.strip().strip("`\"'") for c in m.group(1).split(",")]
    ref_table = m.group(2)
    ref_cols = [c.strip().strip("`\"'") for c in m.group(3).split(",")]
    return ForeignKey(columns=cols, ref_table=ref_table, ref_columns=ref_cols)


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------

_TYPE_CHECKS = [
    (int, "INTEGER"),
    (float, "FLOAT"),
]


def parse_csv(path: str | Path, source: str | None = None) -> Schema:
    """Parse CSV headers and infer column types from sample rows."""
    path = Path(path)
    source = source or str(path)

    with path.open(newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return Schema(source=source)

        sample_rows = []
        for i, row in enumerate(reader):
            sample_rows.append(row)
            if i >= 99:
                break

    columns = []
    for col_idx, header in enumerate(headers):
        col_type = _infer_csv_type(sample_rows, col_idx)
        has_nulls = any(
            col_idx >= len(row) or row[col_idx].strip() == ""
            for row in sample_rows
        )
        columns.append(Column(
            name=header.strip(),
            type=col_type,
            nullable=has_nulls,
        ))

    table_name = path.stem.replace("-", "_").replace(" ", "_")
    table = Table(name=table_name, columns=columns)
    return Schema(tables=[table], source=source)


def _infer_csv_type(rows: list[list[str]], col_idx: int) -> str:
    values = []
    for row in rows:
        if col_idx < len(row) and row[col_idx].strip():
            values.append(row[col_idx].strip())

    if not values:
        return "TEXT"

    for cast_fn, type_name in _TYPE_CHECKS:
        try:
            for v in values[:20]:
                cast_fn(v)
            return type_name
        except (ValueError, TypeError):
            continue

    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}")
    if all(date_re.match(v) for v in values[:20]):
        return "DATE"

    return "TEXT"


# ---------------------------------------------------------------------------
# Parquet parsing
# ---------------------------------------------------------------------------


def parse_parquet(path: str | Path, source: str | None = None) -> Schema:
    """Parse Parquet file metadata into a Schema."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        print("pyarrow is required for Parquet support: pip install pyarrow",
              file=sys.stderr)
        raise

    path = Path(path)
    source = source or str(path)
    pf = pq.ParquetFile(path)
    arrow_schema = pf.schema_arrow

    columns = []
    for i in range(len(arrow_schema)):
        field = arrow_schema.field(i)
        columns.append(Column(
            name=field.name,
            type=str(field.type),
            nullable=field.nullable,
        ))

    table_name = path.stem.replace("-", "_").replace(" ", "_")
    table = Table(name=table_name, columns=columns)
    return Schema(tables=[table], source=source)


# ---------------------------------------------------------------------------
# Live database introspection
# ---------------------------------------------------------------------------


def introspect_database(connection_url: str, source: str | None = None) -> Schema:
    """Introspect a live database via SQLAlchemy."""
    try:
        from sqlalchemy import create_engine, inspect
    except ImportError:
        print("sqlalchemy is required for database introspection: pip install sqlalchemy",
              file=sys.stderr)
        raise

    if source is None:
        source = connection_url.split("@")[-1] if "@" in connection_url else connection_url
    engine = create_engine(connection_url)
    inspector = inspect(engine)

    schema = Schema(source=source)

    for table_name in inspector.get_table_names():
        table = Table(name=table_name)

        for col in inspector.get_columns(table_name):
            table.columns.append(Column(
                name=col["name"],
                type=str(col["type"]),
                nullable=col.get("nullable", True),
                default=str(col["default"]) if col.get("default") else None,
                comment=col.get("comment"),
            ))

        pk = inspector.get_pk_constraint(table_name)
        if pk and pk.get("constrained_columns"):
            table.primary_key = pk["constrained_columns"]
            for col in table.columns:
                if col.name in table.primary_key:
                    col.primary_key = True

        for fk in inspector.get_foreign_keys(table_name):
            table.foreign_keys.append(ForeignKey(
                columns=fk["constrained_columns"],
                ref_table=fk["referred_table"],
                ref_columns=fk["referred_columns"],
            ))

        for uc in inspector.get_unique_constraints(table_name):
            table.unique_constraints.append(uc["column_names"])

        comment = inspector.get_table_comment(table_name)
        if comment and comment.get("text"):
            table.comment = comment["text"]

        schema.tables.append(table)

    engine.dispose()
    return schema


# ---------------------------------------------------------------------------
# Schema → text rendering (for LLM prompts)
# ---------------------------------------------------------------------------


def schema_to_text(schema: Schema) -> str:
    """Render a Schema as human-readable text for LLM consumption."""
    parts = []
    for table in schema.tables:
        lines = [f"Table: {table.name}"]
        if table.comment:
            lines.append(f"  Description: {table.comment}")
        lines.append(f"  Columns:")
        for col in table.columns:
            attrs = []
            if col.primary_key:
                attrs.append("PK")
            if not col.nullable:
                attrs.append("NOT NULL")
            if col.default:
                attrs.append(f"DEFAULT {col.default}")
            attr_str = f" ({', '.join(attrs)})" if attrs else ""
            comment_str = f" — {col.comment}" if col.comment else ""
            lines.append(f"    - {col.name}: {col.type}{attr_str}{comment_str}")

        if table.foreign_keys:
            lines.append(f"  Foreign Keys:")
            for fk in table.foreign_keys:
                lines.append(
                    f"    - {', '.join(fk.columns)} → "
                    f"{fk.ref_table}({', '.join(fk.ref_columns)})"
                )

        if table.unique_constraints:
            lines.append(f"  Unique Constraints:")
            for uc in table.unique_constraints:
                lines.append(f"    - ({', '.join(uc)})")

        parts.append("\n".join(lines))

    return "\n\n".join(parts)
