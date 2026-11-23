"""Prompt templates for verifying that a fix addresses a blocker belief."""

VERIFY_FIX_PROMPT = """\
You are verifying whether a code change fixes the problem described by a belief.

## Blocker Belief

**ID:** `{belief_id}`

**Claim:** {belief_text}

## Fix Context

{fix_context}

## Current Code State

{code_context}

## Your Task

Determine whether the fix fully addresses the problem described in the belief:

- **FIXED** — The fix directly addresses the problem. The belief is no longer true \
because the code change resolves it.
- **PARTIAL** — The fix addresses part of the problem but not all of it. The belief \
is still partially true.
- **NOT_FIXED** — The fix does not address the problem described in the belief, or \
the problem persists despite the change.
- **INCONCLUSIVE** — There is insufficient context to determine whether the fix \
addresses the belief.

Return ONLY a JSON object with the following fields:

- **verdict**: one of FIXED, PARTIAL, NOT_FIXED, INCONCLUSIVE
- **reason**: a short explanation of the verdict
- **gap_description**: (PARTIAL only) what specifically remains unfixed — be concrete \
about which code paths, functions, or behaviors still exhibit the problem
- **affected_files**: (PARTIAL only) list of file paths that need changes to fully fix \
the belief

```json
{{"verdict": "FIXED", "reason": "The rate limiting logic was added to the endpoint, \
addressing the missing rate limit described in the belief"}}
```

For PARTIAL verdicts, include the gap:

```json
{{"verdict": "PARTIAL", "reason": "Write path is fixed but read path still vulnerable", \
"gap_description": "_read_file and _edit_file still use open(path, 'r') after \
validation, allowing a symlink swap between check and use", \
"affected_files": ["worker_bee/tools.py"]}}
```

Be rigorous: a fix must actually resolve the specific claim in the belief, not just \
touch related code. If the belief says "X is missing" and the fix adds X, that's FIXED. \
If the fix adds Y which is related but X is still missing, that's NOT_FIXED.
"""


VERIFY_FIX_OBSERVE_PROMPT = """\
You are preparing to verify whether a code change fixes a known problem.

## Blocker Belief

**ID:** `{belief_id}`

**Claim:** {belief_text}

## Fix Diff

```diff
{fix_diff}
```

## Repository Structure

```
{tree}
```

## Your Task

Determine what additional code context you need to verify the fix is correct and \
complete. Do NOT verify yet. Only request observations.

## Available Observation Tools

| Tool | Purpose | Params |
|------|---------|--------|
| `grep` | Search for a pattern in the codebase | `pattern`, `glob` (default: {default_glob}) |
| `read_file` | Read a file's contents | `file_path`, `start_line`, `max_lines` |
| `list_directory` | List contents of a directory | `dir_path`, `max_depth` |
| `find_symbol` | Find where a class/function is defined | `symbol` |
| `find_usages` | Find where a symbol is used | `symbol` |
| `file_imports` | Extract imports from a file | `file_path` |

## Guidelines

- Request 3-8 observations. Focus on what you need to confirm the fix is correct.
- Read the changed files to see their current state.
- Check callers/usages of changed functions to verify integration.
- Look for tests that cover the fixed behavior.

Output a JSON array of observation requests:

```json
[
  {{"name": "descriptive_name", "tool": "tool_name", "params": {{"param": "value"}}}},
  ...
]
```

Now output your observation requests as JSON:
"""


def build_verify_fix_prompt(
    belief_id: str,
    belief_text: str,
    fix_context: str,
    code_context: str,
) -> str:
    return VERIFY_FIX_PROMPT.format(
        belief_id=belief_id,
        belief_text=belief_text,
        fix_context=fix_context,
        code_context=code_context,
    )


def build_verify_fix_observe_prompt(
    belief_id: str,
    belief_text: str,
    fix_diff: str,
    tree: str,
    default_glob: str = "*.py",
) -> str:
    return VERIFY_FIX_OBSERVE_PROMPT.format(
        belief_id=belief_id,
        belief_text=belief_text,
        fix_diff=fix_diff,
        tree=tree,
        default_glob=default_glob,
    )
