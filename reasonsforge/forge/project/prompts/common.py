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
## Beliefs

At the end of your response, list 3-10 factual beliefs you can extract from
the issues above. These should be specific, verifiable claims about the project.
Format each as:

- `belief-id` — Factual claim about the project state

When a claim expresses a relationship, prefer these naming patterns:
- `X-requires-Y` (dependency), `X-enables-Y` (capability), `X-causes-Y` (causation)
- `X-prevents-Y` (inhibition), `X-replaces-Y` (substitution), `X-contains-Y` (composition)
- `X-is-a-Y` (taxonomy), `X-has-Y` (property), `X-maps-to-Y` (correspondence)
Not every belief needs a relational name — use descriptive names when no binary relationship applies.

Examples:
- `auth-epic-requires-sso-migration` — The authentication epic is blocked until the SSO migration completes
- `release-3.2-on-track` — Release 3.2 has no critical open issues
- `api-team-understaffed` — The API team has 15 open issues with only 2 assignees
"""
