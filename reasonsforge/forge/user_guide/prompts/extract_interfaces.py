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

## Usage Examples

{examples_text}

## Instructions

Generate beliefs about the API:
1. For each interface prop: what it does, whether it is required, valid values
2. For prop combinations: which props work together, which are mutually exclusive
3. For configuration values: what each value means and when to use it
4. For test-revealed patterns: edge cases, error states, required handlers
5. For usage examples: common patterns, idiomatic usage, integration approaches

## Output Format

Output a JSON array. Each element has "id", "text", and optionally "example" (a short code snippet demonstrating the belief):
```json
[
  {{"id": "component-prop-description", "text": "description of the belief", "example": "code snippet"}},
  {{"id": "another-belief-id", "text": "another belief"}}
]
```
Output ONLY the JSON array, no other text.
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
