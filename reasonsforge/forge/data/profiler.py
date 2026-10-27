"""Statistical profiling for data sources.

Generates distribution statistics (row counts, cardinality, null rates,
min/max/mean) that the LLM uses during semantic enrichment.
"""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .schema_parser import Schema


@dataclass
class ColumnProfile:
    name: str
    row_count: int = 0
    null_count: int = 0
    unique_count: int = 0
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    top_values: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class TableProfile:
    name: str
    row_count: int = 0
    columns: list[ColumnProfile] = field(default_factory=list)


@dataclass
class DataProfile:
    tables: list[TableProfile] = field(default_factory=list)


def profile_csv(path: str | Path, table_name: str | None = None) -> DataProfile:
    """Profile a CSV file for column statistics."""
    path = Path(path)
    table_name = table_name or path.stem.replace("-", "_").replace(" ", "_")

    with path.open(newline="") as f:
        reader = csv.reader(f)
        try:
            headers = next(reader)
        except StopIteration:
            return DataProfile()

        col_data: list[list[str]] = [[] for _ in headers]
        row_count = 0
        for row in reader:
            row_count += 1
            for i, header in enumerate(headers):
                val = row[i].strip() if i < len(row) else ""
                col_data[i].append(val)

    columns = []
    for i, header in enumerate(headers):
        values = col_data[i]
        non_null = [v for v in values if v]
        null_count = len(values) - len(non_null)

        unique_vals = set(non_null)

        cp = ColumnProfile(
            name=header.strip(),
            row_count=row_count,
            null_count=null_count,
            unique_count=len(unique_vals),
        )

        numeric_vals = []
        for v in non_null:
            try:
                numeric_vals.append(float(v))
            except (ValueError, TypeError):
                break

        if numeric_vals and len(numeric_vals) == len(non_null):
            cp.min_value = str(min(numeric_vals))
            cp.max_value = str(max(numeric_vals))
            cp.mean_value = sum(numeric_vals) / len(numeric_vals)
        elif non_null:
            sorted_vals = sorted(non_null)
            cp.min_value = sorted_vals[0]
            cp.max_value = sorted_vals[-1]

        if len(unique_vals) <= 50:
            from collections import Counter
            counts = Counter(non_null)
            cp.top_values = counts.most_common(10)

        columns.append(cp)

    table = TableProfile(name=table_name, row_count=row_count, columns=columns)
    return DataProfile(tables=[table])


def profile_parquet(path: str | Path, table_name: str | None = None) -> DataProfile:
    """Profile a Parquet file for column statistics."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
        import pyarrow.compute as pc
        import pyarrow.types as pat
    except ImportError:
        print("pyarrow is required for Parquet profiling: pip install pyarrow",
              file=sys.stderr)
        raise

    path = Path(path)
    table_name = table_name or path.stem.replace("-", "_").replace(" ", "_")

    table = pq.read_table(path)
    row_count = table.num_rows

    columns = []
    for col_name in table.column_names:
        column = table.column(col_name)
        null_count = column.null_count
        unique_count = pc.count_distinct(column).as_py()

        cp = ColumnProfile(
            name=col_name,
            row_count=row_count,
            null_count=null_count,
            unique_count=unique_count,
        )

        col_type = column.type
        is_numeric = (pat.is_integer(col_type) or pat.is_floating(col_type)
                      or pat.is_decimal(col_type))

        if is_numeric and row_count > null_count:
            cp.min_value = str(pc.min(column).as_py())
            cp.max_value = str(pc.max(column).as_py())
            mean = pc.mean(column).as_py()
            if mean is not None:
                cp.mean_value = float(mean)
        elif row_count > null_count:
            try:
                dropped = pc.drop_null(column)
                if len(dropped) > 0:
                    cast = pc.cast(dropped, "string")
                    sorted_vals = pc.sort_indices(cast)
                    cp.min_value = cast[sorted_vals[0].as_py()].as_py()
                    cp.max_value = cast[sorted_vals[-1].as_py()].as_py()
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                pass

        if unique_count <= 50 and row_count > null_count:
            vc = pc.value_counts(column)
            top = sorted(vc.to_pylist(), key=lambda x: -x["counts"])[:10]
            cp.top_values = [(str(v["values"]), v["counts"]) for v in top]

        columns.append(cp)

    tp = TableProfile(name=table_name, row_count=row_count, columns=columns)
    return DataProfile(tables=[tp])


def profile_to_text(profile: DataProfile) -> str:
    """Render a DataProfile as text for LLM consumption."""
    parts = []
    for table in profile.tables:
        lines = [f"Table: {table.name} ({table.row_count:,} rows)"]
        for col in table.columns:
            null_pct = (col.null_count / col.row_count * 100) if col.row_count else 0
            lines.append(f"  {col.name}:")
            lines.append(f"    Unique: {col.unique_count:,}, "
                         f"Nulls: {col.null_count:,} ({null_pct:.1f}%)")
            if col.min_value is not None:
                lines.append(f"    Range: {col.min_value} .. {col.max_value}")
            if col.mean_value is not None:
                lines.append(f"    Mean: {col.mean_value:.2f}")
            if col.top_values:
                top_str = ", ".join(f"{v}({c})" for v, c in col.top_values[:5])
                lines.append(f"    Top values: {top_str}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)
