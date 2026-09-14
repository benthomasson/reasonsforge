# EEM Belief Verification Report

**Domain:** eda-server-expert
**Date:** 2026-08-07
**Sample size:** 5 beliefs
**Strategy:** full (internal consistency + codebase + web)
**Repo:** /Users/bwhittin/code/claude-bw-atf/eda-server

## Results

| Verdict | Count |
|---------|-------|
| CONFIRMED | 4 |
| STALE | 1 |
| CONTRADICTED | 0 |
| DUPLICATE | 0 |
| UNVERIFIABLE | 0 |

## Actions Taken

- Retractions proposed: 1
- Replacements proposed: 0

## TMS Issues (from find_issues)

11 blockers gating 39 beliefs in this domain. Notable blockers:
- `eda-default-worker-has-no-health-probes` — gates 9 beliefs about production readiness
- `eda-init-container-has-no-timeout` — gates 7 beliefs about initialization resilience
- `bare-bearer-header-causes-index-error` — gates 4 beliefs about auth robustness

## Findings Detail

### STALE: `old-job-states-affect-uniqueness`

- **Claim:** unique_enqueue behavior depends on whether an existing job is running, finished, or failed (tests validate all three paths)
- **Evidence:**
  - `src/aap_eda/core/tasking/__init__.py:56-73` — current implementation is a thin wrapper around `submit_task` with no job state checking
  - Docstring explicitly states: "Uniqueness is not guaranteed in dispatcherd, job is simply enqueued."
  - **Internal contradiction:** IN belief `unique-enqueue-does-not-dedupe` says the exact opposite
- **Root cause:** Was true under old Redis/RQ system; invalidated by dispatcherd migration
- **Action:** Retraction proposed (proposal `4b49f165`)

### CONFIRMED: `rulebook-logs-bound-to-activation-instance`

- **Claim:** RulebookProcessLog entries always associated with a specific activation_instance ID
- **Evidence:** `src/aap_eda/core/models/rulebook_process.py:187` — `activation_instance = models.ForeignKey("RulebookProcess", on_delete=models.CASCADE)` — required FK, no null=True
- **Note:** Field name is misleading (points to RulebookProcess, not ActivationInstance), but the binding claim is correct

### CONFIRMED: `webhook-api-stateless-load-balancing`

- **Claim:** eda-webhook-api Service uses round-robin with no session affinity
- **Evidence:** `tools/deploy/eda-webhook-api/service.yaml` — no `sessionAffinity` field; K8s defaults to `None` (round-robin)

### CONFIRMED: `container-engine-dual-mode-abstraction`

- **Claim:** Container ops abstracted behind unified dual-mode interface
- **Evidence:** `DEPLOYMENT_TYPE` at `settings/defaults.py:122`, validators short-circuit on non-k8s at `core/validators.py:358`, `ContainerRequest` DTO and `new_container_engine` factory in activation_manager.py
- **Note:** Well-supported derived belief with 4 justifications and 1 dependent

### CONFIRMED: `eventstream-restart-policy-enum`

- **Claim:** RestartPolicy enum with exactly 3 values (always, on-failure, never)
- **Evidence:** `src/aap_eda/core/enums.py:37-40` — exactly 3 enum members confirmed
- **Note:** Belief says "EventStream restart" but RestartPolicy is shared by both EventStream and Activation — claim is correct but narrower than reality

## Internal Contradictions Found

| Belief A (IN) | Belief B (IN) | Contradiction |
|---------------|---------------|---------------|
| `old-job-states-affect-uniqueness` | `unique-enqueue-does-not-dedupe` | A says unique_enqueue checks job states; B says it does NOT deduplicate |

## Coverage

- Beliefs in domain: 1,965 total
- Verified this run: 5
- Stale rate: 20% (1/5) — small sample, but consistent with the Redis cleanup findings
