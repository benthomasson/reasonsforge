"""LLM prompts for the routing forge pipeline."""

EXTRACT_ROUTING_PROMPT = """\
You are analyzing a knowledge domain's belief network to produce routing \
beliefs — concise descriptions of what this domain knows about, suitable \
for routing natural language queries to the right domain.

## Domain: {domain_name}

## Beliefs ({belief_count} total, showing top {shown_count})

{beliefs_text}

## Instructions

Generate 5-20 routing beliefs that describe what this domain covers. Each \
routing belief should answer: "If a user asked about X, should this domain \
handle it?"

Categories of routing beliefs to generate:

1. **Coverage beliefs** — what topics, entities, and concepts this domain \
can answer questions about
2. **Authority beliefs** — what this domain is the authoritative source for \
(vs. other domains that might partially overlap)
3. **Boundary beliefs** — what this domain does NOT cover, to prevent \
incorrect routing
4. **Query pattern beliefs** — what kinds of questions (aggregation, lookup, \
comparison, trend) this domain supports

Output each routing belief as a markdown list item:
- `routing:{domain_slug}.coverage-topic` — description of what this domain covers regarding this topic
- `routing:{domain_slug}.authority-topic` — what this domain is authoritative for
- `routing:{domain_slug}.boundary-topic` — what this domain does not cover
- `routing:{domain_slug}.query-pattern` — what kinds of questions this domain answers

Be specific and grounded in the actual beliefs shown. Do not speculate \
beyond what the belief network supports.
"""

DERIVE_ROUTING_PROMPT = """\
You are analyzing routing beliefs from multiple knowledge domains to discover \
relationships between them — overlaps, bridges, and authority boundaries.

## Routing Beliefs

{routing_beliefs_text}

## Instructions

Generate cross-domain routing beliefs that describe:

1. **Overlap beliefs** — where two or more domains cover the same concept \
from different angles
2. **Bridge beliefs** — how to connect queries that span multiple domains \
(which shared keys or concepts link them)
3. **Authority resolution** — when domains overlap, which is authoritative \
for which aspect
4. **Multi-domain patterns** — common query patterns that require consulting \
multiple domains

Output each belief as a markdown list item:
- `routing:cross.overlap-topic` — description of the overlap
- `routing:cross.bridge-domain1-domain2` — how these domains connect
- `routing:cross.authority-topic` — which domain is authoritative for this topic
- `routing:cross.multi-pattern` — a query pattern requiring multiple domains

Only generate beliefs that are grounded in the routing beliefs above. Do not \
invent domains or capabilities not present in the input.
"""
