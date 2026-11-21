"""CLAUDE.md template for expert knowledge base repositories."""

TEMPLATE = """\
# {domain} Expert Knowledge Base

This repository is an expert knowledge base for **{domain}**, built and maintained with `reasonsforge`. It contains justified beliefs about the domain — architecture, patterns, behaviors, and known issues — extracted by LLM analysis and stored in a truth maintenance system.

## Use This Knowledge Base First

**Do not rely solely on your training data when working on {domain}.** This knowledge base contains specific, verified beliefs that are more accurate and current than what you learned during training.

Before writing code, answering questions, or making decisions about {domain}:

1. **Search** the knowledge base for relevant beliefs
2. **Use what you find** to inform your work
3. **Flag gaps** — if the knowledge base doesn't cover something you need, tell the user so they can extend it

## How to Query

All commands run from this directory (`{expert_dir}`).

### Search for beliefs by keyword
```bash
reasonsforge search "topic or keyword"
```
FTS5 full-text search across all belief IDs and text. Use multiple terms to narrow results.

### Ask a question (LLM-synthesized answer from beliefs)
```bash
reasonsforge ask "How does X work?"
reasonsforge ask "What are the known issues?" --simple
```
Retrieves relevant beliefs, then synthesizes a coherent answer. Use `--no-synth` to see raw belief matches without LLM synthesis.

### Show a specific belief
```bash
reasonsforge show belief-id
```
Full details: text, truth value, source, justifications, dependents.

### Explain why a belief is held (or not)
```bash
reasonsforge explain belief-id
```
Traces the justification chain back to premises. Use when you need to understand the reasoning behind a belief.

### Browse what's been explored
```bash
reasonsforge code topics list --status done    # files/functions already analyzed
reasonsforge code topics list --status pending # what hasn't been explored yet
```

### Get an overview
```bash
reasonsforge stats
reasonsforge compact --budget 500
reasonsforge list --premises
reasonsforge list --has-dependents
```

### Find known issues and blockers
```bash
reasonsforge list-negative
reasonsforge list-gated
reasonsforge search "bug"
```

### Trace dependencies
```bash
reasonsforge trace belief-id
reasonsforge explain belief-id
```

## When to Flag Missing Knowledge

Tell the user the knowledge base is missing information when:

- You search for something and get no results
- The beliefs you find are too general to answer the specific question
- You need to know about something that hasn't been explored yet
- The user asks about recent changes that may not be reflected yet

Example: "I searched the knowledge base for 'websocket handling' but found no relevant beliefs. This area may not have been explored yet — want me to extend the knowledge base?"

## Extending the Knowledge Base

If the knowledge base is missing information:

```bash
# Run an update to capture recent changes
reasonsforge code update --since-last --repo {repo_path}

# Or add a specific topic and explore it
reasonsforge code topics add path/to/file.py
reasonsforge code explore --repo {repo_path}
```

## Important Notes

- Beliefs have truth values: IN (active/believed) and OUT (retracted)
- By default, only IN beliefs are shown. Use `--show-out` with search to see retracted beliefs
- The knowledge base is a point-in-time snapshot — run `reasonsforge check-stale` to detect beliefs whose source files have changed
"""


def generate_claude_md(domain: str, repo_path: str = "", expert_dir: str = "") -> str:
    """Generate a CLAUDE.md for an expert knowledge base."""
    return TEMPLATE.format(
        domain=domain,
        repo_path=repo_path or ".",
        expert_dir=expert_dir or ".",
    )


def write_claude_md_if_missing(domain: str, repo_path: str = "", expert_dir: str = "") -> bool:
    """Write CLAUDE.md to cwd if it doesn't exist. Returns True if written."""
    import os
    path = os.path.join(expert_dir or ".", "CLAUDE.md")
    if os.path.exists(path):
        return False
    content = generate_claude_md(domain, repo_path, expert_dir)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return True
