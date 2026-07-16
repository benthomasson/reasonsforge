# Session Summary — 2026-07-15

## What we did

Ported three standalone expert prototypes into the `reasonsforge` package as forge subpackages, completing the core forge lineup. Also removed the paper forge stub (the document forge already handles PDFs with chunking).

### Forges added

| Forge | Source prototype | Commands | Lines | Commit |
|-------|-----------------|----------|-------|--------|
| **project** | `~/git/project-expert/` | 15 commands + 36 helpers | 3,065 | `1b04289` |
| **product** | `~/git/product-expert/` | 13 commands + ~30 helpers | 2,102 | `a6cd8b8` |
| **meta** | `~/git/ftl-meta-expert/` | 9 commands + 15 helpers | 1,039 | `1501384` |

**Total: 9,203 lines of Python across 37 commands ported in one session.**

### What each forge does

- **Project forge** (`reasonsforge.forge.project`) — Analyzes project state from issue trackers (GitHub, GitLab, Jira). Scans issues/PRs, explores topics, proposes and reviews beliefs about team velocity, milestone health, process quality, and delivery risks. Includes sprint planning and research verification against live tracker data.

- **Product forge** (`reasonsforge.forge.product`) — Analyzes product data from issue trackers and markdown documents. Focused on feature readiness, user experience, competitive position, and product-market fit. Reuses project forge's source adapters (`from ..project.sources import ...`). Adds an `ingest` command for document analysis and a programmatic `generate-summary` (no LLM needed).

- **Meta forge** (`reasonsforge.forge.meta`) — Cross-domain reasoning across expert belief networks. Does not scan source material directly. Instead, imports pre-built belief networks from other forges via `sync_agent()` and finds emergent insights invisible to any single expert: cross-domain derivations (requiring antecedents from 2+ agents), contradiction detection (nogoods), and executive synthesis. Beliefs are namespaced by agent (e.g., `code:belief-id`, `project:belief-id`).

### Key architectural decisions

1. **No subprocess calls** — All `subprocess.run(["reasons", ...])` replaced with direct `reasonsforge.api` calls (`init_db`, `add_node`, `export_network`, `sync_agent`, `add_nogood`, `compact`, etc.)

2. **No Click** — Click decorators replaced with plain `cmd_*` functions taking argparse `Namespace`. `print()` instead of `click.echo()`.

3. **Deferred imports** — `reasonsforge.api` is never imported at module level; always inside the function that uses it.

4. **Source adapter reuse** — Product forge imports from `..project.sources` rather than duplicating GitHub/GitLab/Jira adapters.

5. **Generalized prompts** — Meta forge prompts accept dynamic agent names instead of hardcoding "code, project, product", so they work with any combination of expert forges.

6. **Paper forge removed** — The document forge already has `chunk_pdf.py` (section-based splitting) and `chunk_docs.py` (markdown/Python splitting), which is exactly the chunk-then-summarize pattern the paper expert was prototyping. No separate paper forge needed.

### Current forge lineup

```
reasonsforge forge document   # PDFs, markdown, code files (with chunking)
reasonsforge forge code       # Codebase architecture analysis
reasonsforge forge project    # Issue tracker / project management
reasonsforge forge product    # Product data and feature analysis
reasonsforge forge meta       # Cross-domain synthesis
```

### Tests

All 1,732 tests pass after every change. No test regressions introduced.
