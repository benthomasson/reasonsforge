"""Common prompt fragments for product forge."""

TOPICS_INSTRUCTIONS = """## Topics to Explore

If your analysis reveals areas worth investigating further, list them at the end under a "## Topics to Explore" heading using this exact format:

- [kind] `target` — One-line description of what to investigate

Where `kind` is one of: feature, epic, roadmap, user-story, feedback, general
And `target` is an issue ID (e.g., GH-42, GL-15, PROJ-100), feature name, or kebab-case slug.

Example:
- [feature] `GH-42` — Whether the search rewrite addresses the top 3 user complaints
- [epic] `GL-15` — Timeline risk for the onboarding redesign epic
- [roadmap] `q2-platform` — How the Q2 platform work aligns with customer retention goals
- [feedback] `search-complaints` — Patterns in user feedback about search relevance
- [general] `pricing-tier-gaps` — Whether the current pricing tiers match actual usage patterns
"""

BELIEFS_INSTRUCTIONS = """## Beliefs

If your analysis surfaces specific, verifiable factual claims about the product, list them under a "## Beliefs" heading:

- `belief-id` — Factual claim text

When a claim expresses a relationship, prefer these naming patterns:
- `X-requires-Y` (dependency), `X-enables-Y` (capability), `X-causes-Y` (causation)
- `X-prevents-Y` (inhibition), `X-replaces-Y` (substitution), `X-contains-Y` (composition)
- `X-is-a-Y` (taxonomy), `X-has-Y` (property), `X-maps-to-Y` (correspondence)
Not every belief needs a relational name — use descriptive names when no binary relationship applies.

Example:
- `onboarding-drop-off-step-3` — 42% of new users abandon onboarding at the permissions step
- `search-latency-causes-user-churn` — Search P95 latency of 3.2s exceeds the 2s SLA and correlates with user drop-off
"""
