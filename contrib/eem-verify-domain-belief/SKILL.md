---
name: eem-verify-domain-belief
description: Verify EEM beliefs against the codebase, other beliefs, and web docs. Samples random beliefs from a ReasonsForge domain, checks for staleness and contradictions, and proposes retractions for invalid beliefs.
user-invocable: true
argument-hint: <domain> [--count N] [--status IN|OUT|all] [--strategy full|internal|code|web]
effort: max
---

# EEM Verify Domain Belief

You are a belief auditor for the ReasonsForge EEM (External Epistemic Memory) system.
Your job is to sample beliefs from a domain, verify each one against multiple sources,
and propose retractions for beliefs that are stale, contradicted, or incorrect.

## Prerequisites

- ReasonsForge MCP server (`reasons-service`) must be connected and authenticated
- For codebase verification: the relevant repo must be locally available
- Web search access for documentation verification

## Inputs

- `$ARGUMENTS` — domain name and optional flags

### Argument Parsing

```
/eem-verify-domain-belief <domain> [--count N] [--status IN|OUT|all] [--strategy full|internal|code|web]
```

| Argument | Required | Default | Description |
|----------|----------|---------|-------------|
| `<domain>` | Yes | — | ReasonsForge domain name (e.g., `eda-server-expert`) |
| `--count` | No | `25` | Number of beliefs to sample |
| `--status` | No | `IN` | Filter beliefs by truth value: `IN`, `OUT`, or `all` |
| `--strategy` | No | `full` | Verification strategy (see below) |

### Verification Strategies

| Strategy | What it does |
|----------|-------------|
| `full` | All three checks: internal consistency, codebase, and web docs |
| `internal` | Only check for contradictions within the domain's own beliefs |
| `code` | Only verify against the local codebase |
| `web` | Only verify against web search / documentation |

## Domain-to-Repo Mapping

When running codebase verification, look up the domain's corresponding local repo
using `~/.claude/projects-config.yaml`. Known mappings:

| Domain | Repo (projects-config alias) | Local Path |
|--------|------------------------------|------------|
| `eda-server-expert` | eda-server | `/Users/bwhittin/code/claude-bw-atf/eda-server` |
| `ansible-rulebook-expert` | ansible-rulebook | `/Users/bwhittin/code/github.com/b-whitt/ansible-rulebook` |
| `eda-project-expert` | — (Jira data, no repo) | N/A — use Jira MCP for verification |

For unmapped domains, ask the user which repo to verify against, or skip code verification.

## Workflow

### Phase 1: Setup

1. Validate the domain exists by calling `list_domains` via the reasons-service MCP
2. Call `list_beliefs` with the specified status filter to get the full belief list
3. Randomly sample `--count` beliefs from the list
4. Call `find_issues` on the domain to get any TMS-level dependency conflicts (free check)
5. Display the sample to the user: belief ID, text, and truth value

### Phase 2: Verification

For each sampled belief, run the applicable verification checks:

#### Check 1: Internal Consistency (all strategies)

- `search` the domain for terms from the belief text
- Look for other beliefs that directly contradict the sampled belief
- Flag pairs where both are IN but make opposing claims (like the Redis case:
  "uses Redis" vs "migrated away from Redis")
- Check if the belief has justifications — unjustified premises are higher risk for staleness

#### Check 2: Codebase Verification (strategies: `full`, `code`)

- Only for domains with a mapped repo
- Extract the key claim from the belief (file name, function, pattern, dependency, etc.)
- Search the codebase with grep/find to verify:
  - Does the referenced file/function still exist?
  - Does the code still behave as the belief claims?
  - Has a migration, refactor, or removal invalidated the belief?
- Check git log for relevant recent changes to the referenced files

#### Check 3: Web/Doc Verification (strategies: `full`, `web`)

- Only for beliefs about external dependencies, libraries, APIs, or documented behaviors
- Web search for the specific claim (version numbers, API behavior, deprecations)
- Compare the belief's claim against current documentation
- Flag beliefs referencing outdated versions or deprecated features

#### Check 4: Duplicate Detection (all strategies)

For each sampled belief, search the domain for near-duplicates:

1. **Extract key terms** — pull the 3-5 most distinctive terms from the belief text
   (skip common words like "the", "is", "uses", etc.)
2. **Search the domain** — call `search` with those key terms
3. **Score similarity** — for each returned belief (excluding the sampled one), assess:
   - Do both beliefs make the **same core claim** about the **same subject**?
   - Is one strictly more specific or better sourced than the other?
   - Would retracting one lose any information not captured by the other?
4. **Flag duplicates** — mark as DUPLICATE when:
   - Two or more IN beliefs say substantially the same thing
   - The claims overlap by ~80%+ in meaning (not just word overlap)
   - Examples: "Brandon has 10+ issues and is overloaded" vs "Brandon has 10+ open
     issues spanning log growth, backports, and CVE triage — heaviest load on team"

**Choosing which duplicate to retract:**
- Keep the belief with **more justifications or dependents** (better connected in TMS)
- If tied, keep the one with a **source file reference** over one without
- If tied, keep the **more precise/specific** version over the vague one
- If tied, keep the **older one** (established longer in the system)

**Report all duplicate clusters** — not just the sampled belief's duplicates. If belief A
is sampled and you find B and C are also duplicates of A, report the full {A, B, C} cluster
and propose retraction of all but the best one.

### Phase 3: Classification

Classify each belief into one of:

| Verdict | Meaning | Action |
|---------|---------|--------|
| **CONFIRMED** | Verified correct against available evidence | No action needed |
| **STALE** | Was true but the world has changed | Propose retraction + replacement |
| **CONTRADICTED** | Conflicts with another IN belief or codebase evidence | Propose retraction, note the contradiction |
| **UNVERIFIABLE** | Cannot verify with available tools (no repo, no docs, too abstract) | Log for manual review |
| **DUPLICATE** | Substantially identical to another IN belief | Propose retraction of the less precise version |

### Phase 4: Action

1. For each STALE or CONTRADICTED belief:
   - Call `propose_retraction` with a rationale citing the evidence
   - If a replacement is warranted, call `propose_belief` with the corrected claim
2. For DUPLICATE beliefs:
   - Call `propose_retraction` on the less precise/less sourced version

### Phase 5: Report

Generate a summary report with:

```
## EEM Belief Verification Report
**Domain:** <domain>
**Date:** <date>
**Sample size:** N beliefs
**Strategy:** <strategy>

### Results
| Verdict | Count |
|---------|-------|
| CONFIRMED | X |
| STALE | X |
| CONTRADICTED | X |
| DUPLICATE | X |
| UNVERIFIABLE | X |

### Actions Taken
- Retractions proposed: X
- Replacements proposed: X

### Findings Detail
<for each non-CONFIRMED belief, show: belief ID, text, verdict, evidence, action taken>

### Internal Contradictions Found
<pairs of beliefs that contradict each other>

### Duplicate Clusters Found
<for each cluster: list all belief IDs and texts, which one was kept, which were retracted>

### Coverage
- Beliefs in domain: N total
- Verified this run: N
- Cumulative verified: (if tracking exists)
```

Save the report to `/Users/bwhittin/Google Drive/My Drive/claude-notes/eem-verify/`
with filename `YYYY-MM-DD-<domain>-verify.md`.

## Important Guidelines

- **Do not retract beliefs you cannot verify.** UNVERIFIABLE is a valid verdict — absence
  of evidence is not evidence of absence.
- **Check git blame dates.** A belief about code that hasn't changed in 2 years is more
  likely still valid than one about recently-refactored code.
- **Batch related beliefs.** If you find one stale belief about Redis, search for ALL
  Redis-related beliefs before proposing retractions (as we did in the Redis cleanup).
- **Propose precise replacements.** A retraction without a replacement leaves a knowledge
  gap. If the belief was partially right, propose a corrected version.
- **Rate limit awareness.** The reasons-service MCP may have rate limits. If you hit errors,
  reduce batch size and add delays.
- **Report the EEM's own contradictions.** When two IN beliefs say opposite things, that's
  the highest-value finding — it means the knowledge base is giving wrong answers to queries
  that happen to hit the wrong belief.
