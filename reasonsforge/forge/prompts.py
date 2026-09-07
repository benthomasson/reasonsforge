"""Prompt templates for forge belief extraction pipeline."""

SUMMARIZE = """\
You are an expert technical writer creating structured study notes.

Given the following documentation page, create a concise summary suitable for \
building domain expertise. Structure your output as:

## <Descriptive Title>
Start with a short, specific title that names the topic (e.g., \
"IAM Role Configuration", "Network Policy Rules", "Cluster Autoscaling"). \
Then one paragraph summarizing what this page covers.

## Key Concepts
Bulleted list of the most important facts, definitions, and concepts.

## Commands and Syntax
Any commands, configuration syntax, or procedures described (with examples).

## Relationships
How this topic connects to other topics in the domain.

## Exam-Relevant Points
Facts that are likely to be tested on a certification exam.

---

SOURCE DOCUMENT:

{content}
"""

SUMMARIZE_CODE = """\
You are an expert technical writer creating structured notes from source code.

Given the following source code file, create a concise summary focused on how \
this code is used in practice. Structure your output as:

## <Descriptive Title>
Start with a short, specific title that names the module or component (e.g., \
"CLI Entry Point", "PDF Chunker", "LLM Invocation Layer"). Then one paragraph \
summarizing what this code does and its role in the project.

## Usage Patterns
How this code is meant to be called or used — entry points, key functions, \
typical invocations. Include code snippets where helpful.

## API and Configuration
Key parameters, options, environment variables, config files, or arguments \
this code accepts.

## Key Behaviors
Important behaviors, error handling, edge cases, or gotchas a user should know about.

## Relationships
How this code connects to other components — what it imports, what calls it, \
what services or systems it interacts with.

---

SOURCE CODE:

{content}
"""

PROPOSE_BELIEFS = """\
You are extracting factual claims from study notes to build a belief registry.

Rules:
- Each belief should be a single, testable factual claim
- Use kebab-case IDs that are descriptive (e.g., rhel9-default-filesystem-xfs)
- Prefer specific facts over vague generalizations
- Include commands, paths, config values when relevant
- Do NOT include opinions or subjective assessments
- Aim for 3-8 beliefs per entry (not every sentence is a belief)
- Set "accept" to true if the claim is well-supported by the source material, \
false if it is vague, speculative, or poorly supported

---

ENTRIES:

{entries}

---

Respond with ONLY this JSON array (no other text):
[{{"id": "<kebab-case-id>", "claim": "<one-line factual claim>", "accept": true, "source": "<path to entry file>", "source_url": "<url from SOURCE_URL in header, or empty string>"}}]
"""


GAP_ANALYSIS = """\
You are analyzing a belief network for coverage gaps.

The following beliefs have been extracted from source material about a specific domain. \
Identify topics, definitions, theorems, or concepts that are **missing** but would be \
expected in a thorough treatment of this domain.

Rules:
- Only list genuinely missing items — not beliefs that are present under a different name
- Organize by topic area
- For each missing item, give a one-line description of what should be there
- Focus on foundational and structural gaps, not trivia
- If a concept is partially present (properties exist but definition is missing), note that
- Respond with ONLY a JSON array (no other text)

{domain_instruction}

---

CURRENT BELIEFS:

{beliefs}

---

Respond with ONLY this JSON array (no other text):
[{{"topic_area": "<area>", "subject": "<what is missing>", "description": "<one-line description>", "priority": "<high|medium|low>", "partially_present": false}}]
"""

GAP_EXTRACT = """\
You are re-reading source material to find specific topics that were missed in a \
previous extraction pass.

IMPORTANT: Only extract beliefs that are **actually present in the entries below**. \
Do not invent or hallucinate claims. If a topic from the gap list is not discussed \
in these entries, skip it.

In addition to the standard extraction rules, look specifically for these missing topics:

{gap_list}

Rules:
- Each belief should be a single, testable factual claim
- Use kebab-case IDs that are descriptive
- Set "accept" to true if the claim is well-supported by the source material
- Only extract claims that appear in the entries — do not fabricate content
- Aim for the missing topics listed above, but also extract any other beliefs you find

---

ENTRIES:

{entries}

---

Respond with ONLY this JSON array (no other text):
[{{"id": "<kebab-case-id>", "claim": "<one-line factual claim>", "accept": true, "source": "<path to entry file>", "source_url": "<url from SOURCE_URL in header, or empty string>"}}]
"""


PROPOSE_MODES = {
    "general": {
        "label": "General",
        "propose_extra": "",
    },
    "academic": {
        "label": "Academic",
        "propose_extra": """
Additional priorities for academic content:

- **Formal definitions**: Extract "X is a Y satisfying..." statements, axiom sets, \
and defining properties as individual beliefs. Definitions are first-class knowledge, \
not boilerplate — the entire subject builds on them.
- **Theorem statements**: Named theorems with precise hypotheses and conclusions. \
State the theorem claim, not just that the theorem exists.
- **Key examples**: Canonical examples that instantiate definitions \
(e.g., "GL(n,F) is a group under matrix multiplication", "Z/pZ is a field when p is prime").
- **Equivalences and implications**: "X if and only if Y", "X implies Y" — these are \
testable structural claims.
- **Axiom lists**: When a definition has numbered axioms (closure, associativity, \
identity, inverses), extract each axiom as a separate belief tied to the parent definition.
- **Structure-preserving maps**: Extract definitions of morphisms, homomorphisms, \
isomorphisms — "a map/function φ: X → Y such that φ preserves..." These define \
the relationships between structures and are as fundamental as the structures themselves.
- **Substructure conditions**: Extract definitions of subgroups, subrings, ideals, \
subspaces — "a subset S of X that is closed under..." or "a subgroup N satisfying..." \
These characterize important sub-objects by the conditions they satisfy.
""",
    },
    "security": {
        "label": "Security Audit",
        "propose_extra": """
Additional priorities for security-relevant content:

- **Trust boundary violations**: Where user-supplied input reaches privileged operations \
or where untrusted code executes with elevated credentials.
- **Missing validation**: Endpoints, inputs, or data paths that accept data without \
schema validation, sanitization, or authentication checks.
- **Credential exposure**: Secrets passed through environment variables, logs, error \
messages, or configuration files accessible to untrusted components.
- **Authorization gaps**: Operations that skip permission checks, or where role \
boundaries are not enforced consistently.
- **Injection surfaces**: User input that flows to SQL queries, shell commands, template \
engines, or deserialization without proper escaping or parameterization.
""",
    },
}

VALID_PROPOSE_MODES = tuple(PROPOSE_MODES.keys())


def get_propose_extra(mode="general"):
    """Return the propose_extra overlay for the given mode."""
    if mode not in PROPOSE_MODES:
        raise ValueError(
            f"Unknown mode: {mode!r}. Valid modes: {', '.join(VALID_PROPOSE_MODES)}"
        )
    return PROPOSE_MODES[mode]["propose_extra"]
