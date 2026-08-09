"""Prompt template for diff explanation."""

from .common import BELIEFS_INSTRUCTIONS, TOPICS_INSTRUCTIONS
from .modes import get_mode


def build_diff_prompt(
    diff_content: str,
    commit_log: str | None = None,
    changed_files_summary: list[str] | None = None,
    mode: str = "discover",
) -> str:
    """
    Build prompt for explaining a diff (what changed and why).

    Args:
        diff_content: Unified diff output
        commit_log: Commit messages for the changes
        changed_files_summary: List of changed file paths
        mode: Analysis mode (discover, security, performance)
    """
    m = get_mode(mode)
    sections = [
        m["explore_role"],
        "Explain what changed in this diff and why.",
        "",
    ]

    if commit_log:
        sections.extend([
            "## Commit History",
            "",
            "```",
            commit_log,
            "```",
            "",
        ])

    if changed_files_summary:
        sections.extend([
            "## Changed Files",
            "",
        ])
        for f in changed_files_summary:
            sections.append(f"- `{f}`")
        sections.append("")

    sections.extend([
        "## Diff",
        "",
        "```diff",
        diff_content,
        "```",
        "",
    ])

    if m["explore_extra"]:
        sections.append(m["explore_extra"])

    sections.extend([
        "## Instructions",
        "",
        "Explain these changes covering:",
        "",
        "1. **Summary**: One-paragraph overview of what changed",
        "2. **Motivation**: Why were these changes made? (infer from commit messages and code)",
        "3. **File-by-File Breakdown**: For each changed file, explain what changed and why",
        "4. **Impact**: What behavior changes as a result?",
        "5. **Risks**: Any potential issues or things to watch out for",
        "",
        "Format your response as markdown.",
        "Focus on the 'why' — don't just describe what lines were added/removed.",
        TOPICS_INSTRUCTIONS + (m["topics_extra"] or ""),
        BELIEFS_INSTRUCTIONS + (m["beliefs_extra"] or ""),
    ])

    return "\n".join(sections)


def build_diff_summary_prompt(
    commit_log: str | None = None,
    changed_files: list[str] | None = None,
    mode: str = "discover",
) -> str:
    """
    Build a summary-only prompt for large diffs (no diff content).

    Used when the full diff exceeds model context limits. Sends only
    commit log and file list so the model can produce a high-level
    overview and generate file-level topics for individual exploration.
    """
    m = get_mode(mode)
    sections = [
        m["explore_role"],
        "The diff is too large to include directly. Use the commit history and",
        "changed file list below to explain what happened at a high level.",
        "",
    ]

    if commit_log:
        sections.extend([
            "## Commit History",
            "",
            "```",
            commit_log,
            "```",
            "",
        ])

    if changed_files:
        sections.extend([
            f"## Changed Files ({len(changed_files)} files)",
            "",
        ])
        for f in changed_files:
            sections.append(f"- `{f}`")
        sections.append("")

    sections.extend([
        "## Instructions",
        "",
        "Based on the commit messages and file paths, explain:",
        "",
        "1. **Summary**: What was the overall thrust of these changes?",
        "2. **Key Themes**: Group the changes by theme or feature area",
        "3. **Notable Changes**: Which files or commits look most significant?",
        "4. **Impact**: What behavior likely changed as a result?",
        "",
        "Format your response as markdown.",
        "Since you don't have the actual diff content, focus on what you can infer",
        "from commit messages and file paths. Be explicit about what you're inferring.",
        "",
        "IMPORTANT: Generate topics for the most interesting changed files so they",
        "can be explored individually with full source context.",
        TOPICS_INSTRUCTIONS + (m["topics_extra"] or ""),
        BELIEFS_INSTRUCTIONS + (m["beliefs_extra"] or ""),
    ])

    return "\n".join(sections)
