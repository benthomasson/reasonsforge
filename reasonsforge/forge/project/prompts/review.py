"""Review prompt — filter low-quality proposals for project forge."""

REVIEW_PROMPT = """\
You are reviewing proposed beliefs extracted from a project analysis. Your job is to filter \
out low-quality proposals that should NOT be added to the belief network.

## Rejection Criteria

Reject a proposal if it matches ANY of these categories:

1. **Stale** — References issues as open/blocking/unresolved when the issue state data shows \
they are actually closed, done, or merged.
2. **Factually false** — Claims something wasn't implemented when linked PRs exist, or makes \
claims contradicted by the issue data provided.
3. **Meta** — About the belief network itself, not the project. Examples: node counts, cascade \
analyses, compaction strategies, retraction history, knowledge base statistics.
4. **Duplicate** — Same claim already exists in the current beliefs (listed below), or is a \
trivial rewording of an existing belief.
5. **Ephemeral** — Point-in-time snapshots that expire immediately. Examples: "issue open N days", \
"network has N nodes as of date", "currently N items in backlog" (specific counts that change daily).
6. **Speculative** — Cascade risk analyses, estimates, editorial judgments about what "should" \
happen, predictions. Not grounded in observable project facts.

## What to KEEP

Keep proposals that are:
- Specific, verifiable factual claims about the project
- Grounded in issue tracker data (references specific issues, teams, milestones)
- Structural observations (dependency chains, ownership gaps, blocking relationships)
- Durable enough to remain true for at least a week

## Issue State Data

{issue_state}

## Existing Beliefs

{existing_beliefs}

## Proposals to Review

{proposals}

## Output Format

For EACH proposal, output exactly one line:

ACCEPT belief-id
or
REJECT belief-id reason-category: brief explanation

Categories: stale, false, meta, duplicate, ephemeral, speculative

Review every proposal — do not skip any. Output nothing else.
"""
