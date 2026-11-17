"""Prompt templates for extracting beliefs from per-file commit diffs."""

from .common import BELIEFS_INSTRUCTIONS


DIFF_OBSERVE_PROMPT = """\
You are analyzing a code change to extract knowledge about the codebase.

## Commit Message

```
{commit_message}
```

## File: `{file_path}`

```diff
{diff_content}
```

## Your Task

Analyze this diff and identify what additional context you need to extract \
meaningful beliefs about what changed and why. Do NOT extract beliefs yet. \
Only request observations.

## Available Observation Tools

| Tool | Purpose | When to use |
|------|---------|-------------|
| `function_body` | Full source of a function/method | Need complete function context beyond diff hunks |
| `find_callers` | Caller analysis with prod/test split | Understanding impact of changed functions |
| `find_usages` | Where a symbol is used | Tracing how a changed symbol is consumed |
| `call_graph` | What a function calls | Impact analysis on changed functions |
| `file_imports` | Extract imports from a file | Understanding dependency context |
| `reasons_search` | Search beliefs in reasons.db | Find existing beliefs that may need updating |
| `related_test_files` | Find test files for a source file | Check test coverage of changed code |
| `class_hierarchy` | Base classes and `__init__` signatures | Class inheritance changes |

## What to Look For

1. **Function context**: Diff hunks that modify part of a function — request `function_body` to see the full picture
2. **Impact scope**: Changed function signatures or return types — request `find_callers` to understand blast radius
3. **Existing beliefs**: Changes that might invalidate existing knowledge — request `reasons_search` to find related beliefs
4. **Dependency changes**: New imports or removed dependencies — request `file_imports`
5. **Test coverage**: Whether the changed code has tests — request `related_test_files`

## Output Format

Output a JSON array of observation requests:

```json
[
  {{"name": "descriptive_name", "tool": "tool_name", "params": {{"param": "value"}}}},
  ...
]
```

If the diff is self-explanatory and you don't need additional context, output:

```json
[]
```

Request 2-6 observations. Be targeted — focus on what would change your understanding of this diff.

Now output your observation requests as JSON:
"""


DIFF_EXTRACT_PROMPT = """\
You are analyzing a code change to extract knowledge about the codebase.

## Commit Message

```
{commit_message}
```

## File: `{file_path}`

```diff
{diff_content}
```
{observations_section}
## Your Task

Extract two types of output from this change:

### 1. New Beliefs

Facts about the codebase that this change reveals or establishes. Focus on \
what the change *tells us* that a file summary alone wouldn't capture:

- What problem was being solved (inferred from the diff and commit message)
- What design decision was made and why
- What invariant was introduced or preserved
- What dependency or constraint was added or removed

{beliefs_instructions}

### 2. Belief Maintenance Signals

Existing beliefs that this change may invalidate or modify. For each, explain \
what changed and why the belief may need updating.

Format each signal as:

- `existing-belief-id` — What changed and why the belief may be outdated

If you found relevant existing beliefs via `reasons_search`, reference them \
directly. If not, describe the kind of belief that might be affected.

If no existing beliefs are affected, write: "No maintenance signals detected."

## Guidelines

- Focus on the *why*, not the *what* — the diff already shows what changed
- Prefer beliefs that would survive code churn (architectural facts, design decisions, invariants)
- Don't extract beliefs that are obvious from reading the current file
- If the commit message explains the motivation, use it
- Each belief should be grounded in evidence from the diff
"""


def build_diff_observe_prompt(
    diff_content: str,
    commit_message: str,
    file_path: str,
) -> str:
    return DIFF_OBSERVE_PROMPT.format(
        diff_content=diff_content,
        commit_message=commit_message,
        file_path=file_path,
    )


def build_diff_extract_prompt(
    diff_content: str,
    commit_message: str,
    file_path: str,
    observations: dict | None = None,
) -> str:
    if observations:
        import json
        obs_section = (
            "\n## Observations\n\n"
            "The following information was gathered from the codebase:\n\n"
            "```json\n"
            f"{json.dumps(observations, indent=2, default=str)}\n"
            "```\n\n"
        )
    else:
        obs_section = "\n"

    return DIFF_EXTRACT_PROMPT.format(
        diff_content=diff_content,
        commit_message=commit_message,
        file_path=file_path,
        observations_section=obs_section,
        beliefs_instructions=BELIEFS_INSTRUCTIONS,
    )
