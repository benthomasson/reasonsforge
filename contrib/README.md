# Community Contributions

## eem-verify-domain-belief

A Claude Code skill that audits ReasonsForge knowledge bases for accuracy. It samples
random beliefs from a domain, verifies each one against the codebase, other beliefs,
and web documentation, then proposes retractions for stale, contradicted, or duplicate
beliefs.

### What it does

1. Samples N random beliefs from a domain (default 25)
2. Runs four verification checks per belief:
   - **Internal consistency** — searches for contradicting IN beliefs
   - **Codebase verification** — greps the source repo to confirm claims
   - **Web/doc verification** — checks external dependencies and APIs
   - **Duplicate detection** — finds near-duplicate beliefs and proposes consolidation
3. Classifies each belief: CONFIRMED, STALE, CONTRADICTED, DUPLICATE, or UNVERIFIABLE
4. Proposes retractions (with rationale) for stale/contradicted/duplicate beliefs
5. Generates a verification report

### Usage

```
/eem-verify-domain-belief <domain> [--count N] [--strategy full|internal|code|web]
```

### Sample reports

See `reports/` for verification reports run against the `eda-server-expert` domain:

- `2026-08-07-eda-server-expert-verify-5.md` — initial 5-belief test run (1 stale found)
- `2026-08-07-eda-server-expert-verify-25.md` — full 25-belief run (1 stale, 3 duplicates)

### Findings from initial runs

- **Stale rate:** 4-20% depending on sample (beliefs from the Redis/RQ era survive despite the dispatcherd migration)
- **Duplicate rate:** ~12% — beliefs generated per-entry with no cross-entry dedup pass
- **Internal contradictions:** Found pairs of IN beliefs making opposing claims (e.g., "uses Redis" vs "migrated away from Redis")
- The TMS `find_issues` blocker/gated system works well — confirmed a real bug (`bare-bearer-header-causes-index-error`) is correctly gating 4 auth robustness beliefs

### Feature requests surfaced

1. **`find_duplicates` tool** — text similarity search to cluster near-duplicate beliefs
2. **Ingestion-time dedup pass** — between `propose-beliefs` and `accept-beliefs` in the pipeline
3. **Bulk review UI** — for domain readers to spot-check unreviewed beliefs
4. **Staleness detection for code-derived beliefs** — beliefs citing source files should auto-flag when those files change
