# CLAUDE.md

## What This Is

Reasonsforge — a Python package implementing Doyle's (1979) Truth Maintenance System with LLM-powered analysis. Extends the core `reasons` TMS (from `ftl-reasons`) with domain-specific "forges" that scan, explore, and extract justified beliefs from codebases, issue trackers, product data, documents, and diary entries.

## Key Concepts

- **Node**: A belief with a truth value (IN or OUT), stored in SQLite (`reasons.db`)
- **Premise**: A node with no justifications — IN by default
- **Justification**: Why a node is believed (SL = all antecedents must be IN)
- **Retraction cascade**: When a node goes OUT, dependents whose justifications fail also go OUT
- **Forge**: A domain-specific pipeline that scans a source, extracts beliefs, and maintains them
- **Topics queue**: Each forge maintains a queue of things to explore (files, issues, etc.)
- **`.forge/` directory**: Per-forge state files (config, topics, reports, progress). Resolved relative to the database path, not cwd.

## Project Structure

```
reasonsforge/
  cli.py                    # Main CLI entry point (top-level TMS commands)
  network.py                # Network class — dependency graph + propagation
  storage.py                # SQLite persistence (load/save full network)
  api.py                    # Functional Python API
  derive.py                 # Derive deeper reasoning chains
  compact.py                # Token-budgeted summary
  merkle.py                 # Per-justification Merkle hashes
  forge/
    cli.py                  # Forge CLI registration (code/project/product subcommands)
    pipeline.py             # Document forge pipeline (chunk → summarize → propose → accept)
    report.py               # Pipeline progress tracker (writes to .forge/reports/)
    propose.py              # Propose + accept beliefs from entries
    summarize.py            # LLM summarization of source documents
    init_cmd.py             # `forge init` command
    llm.py                  # LLM invocation (claude/ollama/gemini)
    code/                   # Code forge — codebase analysis
      commands.py           # scan, explore, propose-beliefs, verify, verify-fix, etc.
      pipeline.py           # Code forge pipeline orchestration
      prompts/              # LLM prompt templates
    project/                # Project forge — issue tracker analysis
      commands.py           # scan, explore, track, analyze-issue, sprint-plan
    product/                # Product forge — product data analysis
      commands.py           # scan, ingest, explore, propose-beliefs
    diary/                  # Diary forge — belief extraction from diary entries
      commands.py           # update, accept, status
    meta/                   # Meta forge — cross-domain reasoning
    data/                   # Data utilities
tests/                      # 200+ tests
```

## Forges

### Code Forge (`reasonsforge code`)
Analyzes a codebase: scans files into a topic queue, explores each file/function with an LLM, extracts architectural beliefs.

```bash
reasonsforge code init --repo ~/git/myproject --description "What this domain covers"
reasonsforge code scan --repo ~/git/myproject
reasonsforge code explore --loop 10 --parallel 4
reasonsforge code propose-beliefs --auto
reasonsforge code analyze          # full pipeline: scan + explore + propose + accept + derive
reasonsforge code update --since-last  # incremental: walk-commits + propose + derive
reasonsforge code verify --all --retract  # verify beliefs against current source
reasonsforge code verify-fix BELIEF-ID --pr 123  # verify a fix addresses a belief
reasonsforge code file-issues --dry-run  # file GitHub issues from blocker beliefs
reasonsforge code status           # dashboard
```

### Project Forge (`reasonsforge project`)
Analyzes issue trackers (GitHub, GitLab, Jira): scans issues, extracts project management beliefs.

```bash
reasonsforge project init --github owner/repo
reasonsforge project scan --all-pages
reasonsforge project explore --loop 10
reasonsforge project analyze-issue ISSUE-123 --walk
reasonsforge project track add ISSUE-456 --walk
reasonsforge project track                 # run tracked issues
reasonsforge project sprint-plan
reasonsforge project update --since-last
reasonsforge project status
```

### Product Forge (`reasonsforge product`)
Analyzes product data from issue trackers through a product lens (feature readiness, competitive analysis).

```bash
reasonsforge product init --github owner/repo
reasonsforge product scan
reasonsforge product ingest docs/ --glob-pattern "**/*.md"
reasonsforge product explore --loop 10
reasonsforge product propose-beliefs --auto
reasonsforge product status
```

### Document Forge (`reasonsforge forge pipeline`)
General document/PDF ingestion: chunk, summarize, propose beliefs.

```bash
reasonsforge forge init "Project Name" --domain "What it covers"
reasonsforge forge pipeline --pdf paper.pdf --model claude
reasonsforge forge summarize --input-dir sources
reasonsforge forge propose-beliefs
reasonsforge forge accept-beliefs
```

### Diary Forge (`reasonsforge forge diary`)
Extract beliefs from diary entries and session notes.

```bash
reasonsforge forge diary update          # propose + accept from entries/
reasonsforge forge diary accept          # accept from proposals file
reasonsforge forge diary status          # show diary forge status
```

## Core TMS Commands

```bash
reasonsforge add NODE "text" --sl a,b     # add with justification
reasonsforge retract NODE                  # mark OUT + cascade
reasonsforge assert NODE                   # mark IN + restore
reasonsforge explain NODE                  # trace why IN or OUT
reasonsforge search "query"                # full-text search
reasonsforge derive --auto --exhaust       # derive deeper reasoning chains
reasonsforge export                        # JSON export
reasonsforge compact --budget 500          # token-budgeted summary
reasonsforge check-stale                   # detect source file changes
reasonsforge contradictions                # detect contradictions
reasonsforge deduplicate                   # find duplicate beliefs
reasonsforge review-beliefs                # LLM review of beliefs
reasonsforge verify --all                  # verify against sources
```

## Setup After Clone

```bash
git config core.hooksPath .githooks
```

This enables the pre-commit hook that runs `ruff format` on staged Python files.

## Running Tests

```bash
uv run pytest tests/ -v
uv run pytest tests/test_report.py -v     # specific test file
uv run pytest tests/ -k "accept"          # by keyword
```

## Common Flags

- `--db reasons.db` — database path (top-level commands)
- `--output reasons.db` — database path (forge subcommands)
- `--model claude` — LLM model (claude, ollama, gemini)
- `--parallel N` — concurrent LLM calls
- `--timeout N` — per-call timeout in seconds
- `--repo .` — target git repository path (code forge)

## `.forge/` Directory

Each forge stores state under `.forge/`:
- `.forge/code/` — code forge (config.json, topics.json, summaries, proposed-entries.json)
- `.forge/project/` — project forge (config.json, topics.json, cached-issues.json)
- `.forge/product/` — product forge (config.json, topics.json)
- `.forge/diary/` — diary forge (proposed-entries.json)
- `.forge/reports/` — pipeline progress reports (`{pipeline}-{timestamp}.json`)

These paths are resolved relative to the database location (`--db` / `--output`), not the current working directory.

## Design Decisions

- SQLite for ACID transactions during propagation cascades
- Full network load/save (not incremental) — simple but scales to ~50K nodes
- LLM calls are the bottleneck, not TMS operations
- Each forge has its own `_get_project_dir(args)` that derives paths from the database location
- Reports use unique timestamped filenames to prevent git conflicts
- Generated `.gitignore` includes `reasons.db`, `rag_fts.db`, `logs/`, `.DS_Store`
