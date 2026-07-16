"""Executive synthesis prompt for meta forge."""


def build_summary_prompt(
    beliefs_text: str,
    domain: str,
    agent_stats: dict,
    agent_names: list[str] | None = None,
) -> str:
    """Build an executive summary prompt from combined beliefs.

    Args:
        beliefs_text: Compact summary of all beliefs.
        domain: Domain description (e.g., project name).
        agent_stats: Dict with agent names as keys and belief counts as values,
                     plus 'derived' and 'nogoods' keys.
        agent_names: List of agent names. If None, uses keys from agent_stats.
    """
    names = agent_names or [k for k in sorted(agent_stats) if k not in ("derived", "nogoods")]

    stats_lines = []
    for name in names:
        count = agent_stats.get(name, 0)
        stats_lines.append(f"- {name.capitalize()} expert: {count} beliefs")
    stats_lines.append(f"- Cross-domain derived: {agent_stats.get('derived', 0)} beliefs")
    stats_lines.append(f"- Contradictions (nogoods): {agent_stats.get('nogoods', 0)}")
    stats_section = "\n".join(stats_lines)

    domains_list = ", ".join(n.capitalize() for n in names)

    return f"""\
You are synthesizing an executive summary across expert knowledge bases ({domains_list}) for: {domain}

## Agent Statistics
{stats_section}

## Combined Beliefs

{beliefs_text}

## Instructions

Synthesize a single executive summary that a CTO or VP Engineering reads in 5 minutes:

1. **System Overview** — What is this system? What does it do?
2. **Domain Health** — For each expert domain, summarize strengths and weaknesses
3. **Cross-Domain Tensions** — Where do the perspectives conflict or reinforce each other?
4. **Nogoods & Contradictions** — What contradictions exist across domains? What do they mean?
5. **Top Risks** — The 5 most important risks, considering ALL domains together
6. **Recommendations** — Top 5 actions that address cross-domain concerns

Be concrete. Reference specific belief IDs. Highlight where one domain's strength masks another domain's weakness.
"""
