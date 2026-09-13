"""Belief proposal prompt for project forge."""

PROPOSE_BELIEFS_PROJECT = """You are extracting factual beliefs from project analysis entries.

Read the entries below and extract specific, verifiable claims about the project.
Each belief should be:
- A single factual claim (not an opinion or recommendation)
- Verifiable by checking the issue tracker
- Scoped to a specific issue, epic, team, or milestone
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
### [ACCEPT/REJECT] auth-epic-has-3-blockers
The authentication epic PROJ-100 has 3 open blocker issues preventing completion
- Source: entries/2026/04/09/scan-myproject.md

### [ACCEPT/REJECT] api-team-overloaded
The API team has 18 open issues assigned to 2 engineers with no recent closures
- Source: entries/2026/04/09/epic-api-redesign.md

---

## Entries to analyze:

{entries}
"""
