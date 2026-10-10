"""LLM prompts for the data forge pipeline."""

ENRICH_SCHEMA_PROMPT = """\
You are a data analyst examining a database schema. Your job is to generate \
semantic beliefs about each table and column — what the data means in business \
terms, not just its technical definition.

## Schema

{schema_text}

## Additional Context

{context_text}

## Instructions

For each table, generate:
1. A belief about the table's purpose and what entity it represents
2. A belief for each column explaining its business meaning, edge cases, and \
how it is typically used

For relationships (foreign keys), generate beliefs about:
- What the relationship means in business terms
- Whether it is one-to-many or many-to-many
- Common join patterns

For the schema as a whole, derive:
- Key metrics or KPIs that can be calculated
- Common query patterns
- Business rules implied by constraints

## Output Format

Output each belief as a markdown list item:
- `table-name` — description of the table's purpose
- `table-name.column-name` — description of what this column represents
- `table-name.fk-ref-table` — description of the relationship
- `metric-name` — description of a derivable metric or KPI

Be specific and grounded. Reference the schema and context. Do not speculate \
beyond what the schema and context support.
"""

PROFILE_INTERPRET_PROMPT = """\
You are reviewing statistical profiles of database columns alongside the schema \
and any context provided. Use the statistics to refine your understanding of \
the data and generate additional beliefs.

## Schema

{schema_text}

## Statistical Profile

{profile_text}

## Additional Context

{context_text}

## Instructions

Based on the statistics, generate beliefs about:
- Data quality issues (high null rates, unexpected distributions)
- Categorical column semantics (what the top values mean)
- Temporal patterns (date ranges suggesting active periods)
- Cardinality insights (unique counts suggesting entity types vs attributes)
- Anomalies worth investigating

Output each belief as a markdown list item using the same ID format:
- `table-name.column-name.stat` — what this statistic reveals about the data
"""
