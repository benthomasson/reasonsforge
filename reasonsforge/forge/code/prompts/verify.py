"""Prompt templates for verifying belief staleness against current source code."""

VERIFY_OBSERVE_PROMPT = """\
You are preparing to verify whether a belief about a codebase still holds.

## Belief to Verify

**ID:** {belief_id}

**Claim:** {belief_text}

## Initial Code Context

{seed_context}

## Repository Structure

```
{tree}
```

## Your Task

Determine what additional information you need to confirm or refute this belief.
Do NOT verify the belief yet. Only request observations.

## Available Observation Tools

| Tool | Purpose | Params |
|------|---------|--------|
| `grep` | Search for a pattern in the codebase | `pattern`, `glob` (default: {default_glob}) |
| `read_file` | Read a file's contents | `file_path`, `start_line`, `max_lines` |
| `list_directory` | List contents of a directory | `dir_path`, `max_depth` |
| `find_symbol` | Find where a class/function is defined | `symbol` |
| `find_usages` | Find where a symbol is used | `symbol` |
| `file_imports` | Extract imports from a file | `file_path` |

## Output Format

Output a JSON array of observation requests:

```json
[
  {{"name": "descriptive_name", "tool": "tool_name", "params": {{"param": "value"}}}},
  ...
]
```

## Guidelines

- Request 3-8 observations. Be targeted, not exhaustive.
- Focus on what you need to verify the specific claim above.
- If the initial code context already covers the claim, request observations that would \
confirm related behavior (callers, tests, configuration).
- Use `find_usages` to trace how functions/classes are actually used.
- Use `find_symbol` to locate definitions referenced in the belief.
- If the initial context is empty, start with `grep` or `find_symbol` to locate the relevant code.

Now output your observation requests as JSON:
"""


VERIFY_PROMPT = """\
You are verifying whether beliefs about a codebase still hold by examining the current source code.

For each belief below, I provide the belief text and relevant code context gathered from \
the current state of the repository.

Determine whether each belief is:
- **CONFIRMED** — the current code still supports this claim
- **STALE** — the code has changed and the belief no longer holds (explain what changed)
- **INCONCLUSIVE** — the provided code context is insufficient to determine either way

Return ONLY a JSON object mapping each belief ID to an object with "verdict" and "reason":

Example:
```json
{{"belief-1": {{"verdict": "CONFIRMED", "reason": "The handler still enforces zero-arg construction"}}, \
"belief-2": {{"verdict": "STALE", "reason": "LoginAttemptAuditHandler now covers failed logins with WARNING severity"}}, \
"belief-3": {{"verdict": "INCONCLUSIVE", "reason": "The relevant middleware file was not in the provided context"}}}}
```

## Beliefs to Verify

{beliefs}"""


VERIFY_INFER_FILE_PROMPT = """\
You are identifying which source file a belief about a codebase refers to.

## Belief

**ID:** {belief_id}

**Claim:** {belief_text}

## Repository structure

```
{repo_tree}
```

## Instructions

Based on the belief ID and claim text, identify the single source file in the repository \
that this belief is primarily about. The file must exist in the repository structure above.

Output a JSON array with exactly one file path relative to the repository root.

Example output:
```json
["src/auth/handler.py"]
```

Output ONLY the JSON array, nothing else.
"""
