"""Shared prompt fragments for project forge."""

TOPICS_INSTRUCTIONS = """
## Topics to Explore

At the end of your response, suggest 3-8 follow-up topics that would deepen
understanding of the project. Format each as:

- [issue] `PROJ-123` — Why this blocker hasn't been resolved
- [epic] `PROJ-100` — Overall epic health and child issue status
- [general] `release-readiness` — Whether the current milestone is on track
- [general] `team-velocity` — How the team's throughput has changed recently

Topic kinds: issue, epic, milestone, general
"""

BELIEFS_INSTRUCTIONS = """
## Factual Beliefs

At the end of your response, list 3-10 factual beliefs extracted from
the issues above. Focus on **durable domain knowledge** — technical decisions,
architectural constraints, dependencies, risks, and invariants that remain
meaningful even as issue status changes.

**Do NOT extract beliefs about:**
- Issue status (open, closed, in progress, backlog)
- Issue counts (child issues, linked issues, blockers)
- Assignees or team member names
- Dates, sprint assignments, or milestone membership
- Labels, priorities, or issue types

These are ephemeral metadata best queried from the issue tracker directly.

**DO extract beliefs about:**
- Technical constraints and architectural decisions
- Dependencies between systems, services, or capabilities
- Relationships between issues (X-is-child-of-Y, X-requires-Y, X-blocks-Y)
- Risks, failure modes, and blast radius
- Process invariants and workflow requirements
- Security, compliance, or operational constraints
- Strategic decisions and their rationale
- **Delivery risks** — patterns that could hold up releases: features blocked by
  missing prerequisites, teams that are a delivery bottleneck, work areas with
  no clear ownership, backlogs growing faster than closure, test coverage gaps
  blocking GA, unresolved design decisions blocking implementation

Format each as:

- `belief-id` — Factual claim about the project

When a claim expresses a relationship, prefer these naming patterns:
- `X-requires-Y` (dependency), `X-enables-Y` (capability), `X-causes-Y` (causation)
- `X-prevents-Y` (inhibition), `X-replaces-Y` (substitution), `X-contains-Y` (composition)
- `X-is-a-Y` (taxonomy), `X-has-Y` (property), `X-maps-to-Y` (correspondence)
Not every belief needs a relational name — use descriptive names when no binary relationship applies.

Examples:
- `auth-epic-requires-sso-migration` — The authentication epic is blocked until the SSO migration completes
- `rbac-runs-as-root` — The RBAC service runs with root privileges in the current deployment
- `billing-dashboard-depends-on-app-interface` — Production billing dashboards are deployed via app-interface MRs, not direct Grafana config
- `indirect-counting-ga-blocked-by-test-coverage` — Indirect node counting cannot exit tech preview until ATF test coverage and perf/scale validation are complete
- `portal-team-is-plugin-delivery-bottleneck` — Increasing plugin contributions funnel through the Portal team, creating a delivery bottleneck without a plugin factory process
"""
