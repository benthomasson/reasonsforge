"""Belief proposal prompt for product forge."""

PROPOSE_BELIEFS_PRODUCT = """You are extracting factual beliefs from product analysis entries.

Read the entries below and extract specific, verifiable claims about the product.
Each belief should be:
- A single factual claim (not an opinion or recommendation)
- Verifiable by checking the issue tracker, analytics, or product artifacts
- Scoped to a specific feature, user segment, or product area
- Named with a kebab-case ID that describes the claim
- When a claim expresses a relationship between two concepts, prefer these naming patterns:
  - X-is-a-Y (taxonomy), X-has-Y (composition), X-requires-Y (dependency)
  - X-enables-Y (capability), X-implements-Y (realization), X-produces-Y (output)
  - X-replaces-Y (substitution), X-causes-Y (causation), X-prevents-Y (inhibition)
  - X-extends-Y (extension), X-maps-to-Y (correspondence), X-contains-Y (containment)
  Not every belief needs a relational name — use descriptive names for facts that \
don't express a binary relationship.

For each belief, output in this exact format:

### [ACCEPT/REJECT] belief-id
Factual claim text here
- Source: entry-file-name

Examples:
### [ACCEPT/REJECT] search-p95-above-sla
Search P95 latency is 3.2s, exceeding the 2s SLA defined in the platform agreement
- Source: entries/2026/04/09/scan-myproduct.md

### [ACCEPT/REJECT] onboarding-42pct-drop-step3
42% of new users abandon the onboarding flow at the permissions request step
- Source: entries/2026/04/09/explore-onboarding-flow.md

---

## Entries to analyze:

{entries}
"""
