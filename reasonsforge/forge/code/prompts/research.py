"""Prompt template for research command — inferring source files from review gaps."""

RESEARCH_INFER_FILES_PROMPT = """\
You are analyzing a belief that was flagged during code review as lacking sufficient evidence. \
Your job is to identify which source files in the repository would provide the missing evidence.

## Belief

**ID:** {belief_id}

**Claim:** {belief_text}

**Review comment:** {comment}

## Repository structure

```
{repo_tree}
```

## Already explored files

{explored_files}

## Instructions

Based on the belief claim and review comment, identify which source files in the repository \
would provide evidence to either confirm or refute this belief. Focus on files that:

1. Are mentioned or implied by the belief ID or review comment
2. Would contain the implementation being claimed
3. Have NOT already been explored (listed above)

Output a JSON array of file paths relative to the repository root. Only include files that \
actually exist in the repository structure shown above. Output 1-5 files, prioritizing the \
most relevant ones.

Example output:
```json
["src/auth/handler.py", "src/auth/middleware.py", "tests/test_auth.py"]
```

Output ONLY the JSON array, nothing else.
"""
