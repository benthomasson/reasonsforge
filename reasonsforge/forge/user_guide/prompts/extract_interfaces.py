"""LLM prompts for the user-guide forge pipeline."""

PROPOSE_BELIEFS_PROMPT = """\
You are analyzing extracted API interface definitions to generate beliefs about \
how this API should be used.

## Extracted Interfaces

{interfaces_text}

## Extracted Configuration/Tokens

{config_text}

## Test Patterns

{test_text}

## Instructions

Generate beliefs about the API:
1. For each interface prop: what it does, whether it is required, valid values
2. For prop combinations: which props work together, which are mutually exclusive
3. For configuration values: what each value means and when to use it
4. For test-revealed patterns: edge cases, error states, required handlers

## Output Format

Output each belief as a markdown list item:
- `component-name-prop-description` — description of the belief
"""

VERIFY_BELIEFS_PROMPT = """\
You are verifying API usage beliefs against source code evidence.

## Beliefs to Verify

{beliefs_text}

## Source Evidence

{evidence_text}

## Instructions

For each belief, verify:
1. Is it supported by the interface definition?
2. Is it contradicted by any test?
3. Is the wording accurate and precise?

Output a JSON array:
[{{"id": "belief-id", "verdict": "confirmed|revised|rejected", \
"revised_text": "...", "reason": "..."}}]
"""
