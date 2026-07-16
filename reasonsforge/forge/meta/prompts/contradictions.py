"""Cross-domain contradiction detection prompt for meta forge."""


def build_contradictions_prompt(
    beliefs_section: str,
    agent_names: list[str] | None = None,
) -> str:
    """Build a contradiction detection prompt.

    Args:
        beliefs_section: Formatted beliefs grouped by agent.
        agent_names: List of agent names for context. Defaults to code/project/product.
    """
    names = agent_names or ["code", "project", "product"]
    domains = ", ".join(names)

    return f"""\
You are analyzing beliefs from expert domains ({domains}) looking for cross-domain contradictions.

A contradiction (nogood) occurs when beliefs from different domains cannot ALL be true simultaneously.

## Types of Cross-Domain Contradictions

1. **Optimism mismatch**: One domain says something is ready/healthy but another shows problems
2. **Capacity conflict**: One domain assumes resources that another shows are unavailable
3. **Quality disagreement**: Domains disagree on quality, coverage, or readiness metrics
4. **Timeline contradiction**: Domains have incompatible expectations about timelines
5. **Strategy mismatch**: Domains have incompatible assumptions about direction or architecture

## Beliefs from Expert Domains

{beliefs_section}

## Instructions

For each contradiction found, output EXACTLY this format:

### NOGOOD cross-domain-contradiction-id
- Claims: agent-a:belief-id, agent-b:belief-id
- Analysis: Why these cannot both be true
- Severity: High|Medium|Low
- Resolution: What needs to change to resolve the contradiction

Only report genuine contradictions where the claims are logically incompatible.
Do not report tensions or tradeoffs that can coexist — only true contradictions.
"""
