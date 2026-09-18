"""Belief proposal prompt for project forge."""

PROPOSE_BELIEFS_PROJECT = """You are extracting durable domain knowledge from project analysis entries.

Read the entries below and extract claims that capture **technical decisions,
architectural constraints, dependencies, risks, and invariants** — knowledge
that remains valuable even as issue status changes.

Each belief should be:
- A single factual claim (not an opinion or recommendation)
- Durable — still meaningful after the originating issue is closed or reassigned
- About the domain, not the tracker — describes how systems, services, or
  processes work, not what state an issue is in
- Named with a kebab-case ID that describes the claim
- When a claim expresses a relationship between two concepts, prefer these naming patterns:
  - X-is-a-Y (taxonomy), X-has-Y (composition), X-requires-Y (dependency)
  - X-enables-Y (capability), X-implements-Y (realization), X-produces-Y (output)
  - X-replaces-Y (substitution), X-causes-Y (causation), X-prevents-Y (inhibition)
  - X-extends-Y (extension), X-maps-to-Y (correspondence), X-contains-Y (containment)
  Not every belief needs a relational name — use descriptive names for facts that \
  don't express a binary relationship.

**Skip these — they are ephemeral metadata, not domain knowledge:**
- Issue status, resolution, or workflow state (open/closed/in-progress)
- Issue counts (child issues, subtasks, blockers, linked issues)
- Assignees, reporters, watchers, or authors
- Dates, sprints, fix versions, or milestone membership
- Labels, priorities, or issue type classifications

**DO extract relationship beliefs** — parent/child, dependency, and link
relationships between issues are valuable for reasoning (X-is-child-of-Y,
X-requires-Y, X-linked-to-Y, X-blocks-Y). These express structural
knowledge about how work items relate.

**Extract these — they are durable project knowledge:**
- Technical constraints ("RBAC runs as root", "credential rotation requires downtime")
- System dependencies ("billing export depends on metrics-utility tarball")
- Architectural decisions ("dashboards deploy via app-interface, not direct Grafana config")
- Risk and failure modes ("retry storms can amplify API overload")
- Process invariants ("weekly release requires manual QE sign-off")
- Security and compliance constraints ("session tokens must not persist beyond 24h")
- Strategic context ("indirect node counting targets Summit 2027 GA")
- **Delivery risks** — patterns that could hold up releases or indicate project
  health problems. These are NOT issue status — they describe structural problems:
  features blocked by missing prerequisites, teams that are a delivery bottleneck,
  work areas with no clear ownership, backlogs growing faster than closure,
  test coverage gaps blocking GA, unresolved design decisions blocking implementation,
  cross-team dependencies with no coordination mechanism

For each belief, output in this exact format:

### [ACCEPT/REJECT] belief-id
Factual claim text here
- Source: entry-file-name

Examples:
### [ACCEPT] indirect-counting-requires-certified-collections
Indirect node detection relies on query files shipped in certified Ansible collections; uncertified collections cannot contribute indirect counts
- Source: entries/2026/04/09/explore-ANSTRAT-1628.md

### [ACCEPT] billing-dashboard-deploys-via-app-interface
Production billing dashboards are promoted by updating a commit ref in the app-interface repository, not by direct Grafana configuration
- Source: entries/2026/04/09/explore-AAP-59477.md

### [ACCEPT] api-overload-amplified-by-client-retries
Client retry behavior without backoff or jitter can amplify API overload beyond the initial traffic spike
- Source: entries/2026/04/09/explore-api-overload-root-cause.md

### [ACCEPT] indirect-counting-ga-blocked-by-test-coverage
Indirect node counting cannot exit tech preview until ATF test coverage and perf/scale validation are complete, blocking the Summit 2027 GA target
- Source: entries/2026/04/09/explore-ANSTRAT-1628.md

### [ACCEPT] portal-team-is-plugin-delivery-bottleneck
Increasing plugin contributions from PDT funnel through the Portal team for review and integration, creating a scaling bottleneck without a self-service plugin factory
- Source: entries/2026/04/09/explore-ANSTRAT-2400.md

---

## Entries to analyze:

{entries}
"""
