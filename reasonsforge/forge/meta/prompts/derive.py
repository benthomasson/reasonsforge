"""Cross-domain derivation prompt for meta forge."""

import random

from .common import OUTPUT_FORMAT


def build_derive_prompt(
    beliefs_by_agent: dict[str, list[dict]],
    derived_beliefs: list[dict],
    agent_descriptions: dict[str, str] | None = None,
    budget: int = 300,
    seed: int | None = None,
) -> str:
    """Build a cross-domain derivation prompt.

    Args:
        beliefs_by_agent: Dict of agent_name -> list of belief dicts with id, text, truth_value.
        derived_beliefs: Existing cross-domain derived beliefs.
        agent_descriptions: Optional dict of agent_name -> one-line description.
        budget: Max beliefs to include per agent.
        seed: Random seed for sampling. None uses a random seed each run.
    """
    rng = random.Random(seed)

    default_descriptions = {
        "code": "Architecture, patterns, invariants, and technical debt from codebase analysis",
        "project": "Team capacity, milestone health, process quality, and delivery risks from issue tracking",
        "product": "Feature readiness, user experience, competitive position, and product-market fit",
    }
    descs = agent_descriptions or default_descriptions

    agent_names = sorted(beliefs_by_agent)
    agent_intro_lines = []
    for name in agent_names:
        desc = descs.get(name, f"Expert analysis from the {name} domain")
        agent_intro_lines.append(f"- **{name.capitalize()}**: {desc}")
    agent_intro = "\n".join(agent_intro_lines)

    sections = []
    total_in = 0
    for agent_name in agent_names:
        beliefs = beliefs_by_agent[agent_name]
        in_beliefs = [b for b in beliefs if b["truth_value"] == "IN"]
        total_in += len(in_beliefs)

        if len(in_beliefs) > budget:
            selected = rng.sample(in_beliefs, budget)
            selected.sort(key=lambda b: b["id"])
        else:
            selected = in_beliefs
        omitted = len(in_beliefs) - len(selected)

        lines = [f"### {agent_name} expert ({len(in_beliefs)} IN beliefs)"]
        for b in selected:
            lines.append(f"- `{b['id']}`: {b['text'][:200]}")
        if omitted:
            lines.append(f"*({omitted} more beliefs omitted)*")
        sections.append("\n".join(lines))

    beliefs_section = "\n\n".join(sections)

    if derived_beliefs:
        derived_lines = ["### Existing cross-domain derived beliefs"]
        for b in derived_beliefs:
            status = "IN" if b["truth_value"] == "IN" else "OUT"
            derived_lines.append(f"- [{status}] `{b['id']}`: {b['text'][:200]}")
        derived_section = "\n".join(derived_lines)
    else:
        derived_section = "*No cross-domain derived beliefs yet.*"

    statistics = f"Total IN beliefs across agents: {total_in}"

    return f"""\
You are a reasoning architect analyzing a belief network that spans expert domains:
{agent_intro}

Your task is to find emergent insights that ONLY become visible when combining knowledge across domains.

## Cross-Domain Derivation Patterns

1. **Multi-domain synthesis**: Insights that require evidence from 2+ expert domains
2. **Outlist-gated cross-domain**: Recovery paths GATE'd by domain-specific blockers
3. **Cross-domain causation**: Where one domain's state explains another domain's observations

Examples:
- "Ship readiness" GATE'd by code:open-bugs AND project:unresolved-blockers
- "Tech debt impact on velocity" DERIVE'd from code:debt-in-X + project:velocity-declining-on-Y
- "Architecture supports product direction" GATE'd by code:missing-capability

## Rules

- Each proposed conclusion MUST reference antecedents from AT LEAST 2 different agents
- Antecedents must be existing belief IDs from the lists below
- Prefer insights that would be INVISIBLE to any single expert
- Don't force connections between unrelated beliefs
- Each conclusion should represent genuine cross-domain emergence

{OUTPUT_FORMAT}

---

## Current Beliefs

{beliefs_section}

## Existing Cross-Domain Derivations

{derived_section}

{statistics}
"""
