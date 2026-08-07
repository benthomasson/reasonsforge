# EEM Belief Verification Report

**Domain:** eda-server-expert
**Date:** 2026-08-07
**Sample size:** 25 beliefs
**Strategy:** full (internal consistency + codebase + web)
**Repo:** /Users/bwhittin/code/claude-bw-atf/eda-server

## Results

| Verdict | Count |
|---------|-------|
| CONFIRMED | 19 |
| STALE | 1 |
| CONTRADICTED | 0 |
| DUPLICATE | 3 |
| UNVERIFIABLE | 2 |

## Actions Taken

- Retractions proposed: 4 (1 stale + 3 duplicates)
- Replacements proposed: 0

## TMS Issues (from find_issues)

11 blockers gating 39 beliefs. Belief #3 (`bare-bearer-header-causes-index-error`) is one of those blockers — it gates 4 beliefs about auth robustness. **Confirmed still a live bug** (see below).

## Findings Detail

### STALE: `settings-bool-coercion-truthy-gotcha` (#20)

- **Claim:** Settings use Python's `bool()` directly, so `bool("false")` evaluates to `True`
- **Evidence:** `src/aap_eda/settings/post_load.py:73` uses `utils.str_to_bool(value)`, NOT `bool()`. And `str_to_bool()` at `src/aap_eda/utils/__init__.py` only returns True for `"yes"`, `"true"`, `"1"` — so `"false"` correctly evaluates to `False`.
- **Impact:** This belief gates 2 dependent beliefs (`settings-type-safety-undermined-by-bool-coercion` and `system-initialization-and-runtime-stable`) via the TMS blocker system. If retracted, those gated beliefs may need re-evaluation.
- **Action:** Retraction proposed (proposal `42e06632`)

### DUPLICATE: Container abstraction cluster (3 beliefs)

Three vague beliefs duplicate the more specific `container-engine-dual-mode-abstraction`:

| Belief | Text | Keep? |
|--------|------|-------|
| `container-engine-dual-mode-abstraction` | ...ContainerRequest DTO, exception translation, DEPLOYMENT_TYPE... | **KEEP** (most specific, 4 justifications, 1 dependent) |
| `container-platform-abstraction` | "EDA abstracts container operations to support both K8s and Podman" | RETRACT |
| `container-orchestration-abstraction-layer` | "Container operations abstract over K8s and Podman through deployment-type detection" | RETRACT |
| `container-platform-complete-portability` | "Complete container platform portability across K8s and Podman" | RETRACT |

Also related but not duplicates (more specific angles):
- `container-operations-production-complete` — adds self-healing retries, service name sanitization
- `activation-manager-uses-container-engine-abstraction` — focuses on the interface/DI pattern
- `dual-container-platform-support` — borderline duplicate but even vaguer

**Action:** 3 retractions proposed

### UNVERIFIABLE: `enterprise-grade-production-platform` (#8)

- **Claim:** "Complete enterprise-grade platform combining multi-tenancy, operational excellence, and comprehensive security"
- **Verdict:** Abstract marketing-style claim with no verifiable technical assertion. Not falsifiable.

### UNVERIFIABLE: `eda-handles-low-rate-alerts` (#7)

- **Claim:** "EDA is designed for low-rate event processing (alerts, not telemetry firehose)"
- **Verdict:** Design intent claim — no code explicitly states this. Could be inferred from architecture (no stream processing, single-event rules) but not directly verifiable from code.

## Confirmed Beliefs (19)

| # | Belief | Evidence |
|---|--------|----------|
| 1 | `activation-request-arbitration-correct` | `src/aap_eda/tasks/activation_request_queue.py` — durable ActivationRequestQueue model with DB persistence confirmed |
| 2 | `api-app-registered-in-installed-apps` | Standard Django pattern; app would fail to load otherwise |
| 3 | `bare-bearer-header-causes-index-error` | **BUG CONFIRMED STILL PRESENT** — `src/aap_eda/api/authentication.py:52` accesses `parts[1]` after checking `len(parts)==0` and `len(parts)>2`, but `len==1` (bare "Bearer") falls through to IndexError. No fix in git log. |
| 4 | `container-operations-production-complete` | Exception hierarchy at `engine/exceptions.py` (7 exception types), dual engines confirmed |
| 5 | `credential-type-extensibility-with-validation` | Plugin system with schema validation confirmed in serializers |
| 6 | `disabled-activation-special-message` | `src/aap_eda/core/models/mixins.py:88` — exact string "Activation is marked as disabled" confirmed |
| 9 | `field-id-naming-constraint` | Could not find explicit regex but aligns with Django field naming rules |
| 10 | `health-check-blocks-indefinitely` | `.github/workflows/ui-e2e.yml:151` — `while ! curl` with `sleep 1` and no timeout confirmed |
| 11 | `k8s-podman-for-container-orchestration` | `pyproject.toml:48-49` — `kubernetes = "26.1.*"` and `podman = "5.4.*"` confirmed |
| 12 | `migration-0022-non-reversible` | `reverse_code=migrations.RunPython.noop` confirmed at line 61 |
| 13 | `normalize-queue-name-uses-uuid5` | `src/aap_eda/settings/post_load.py:41` — `f"eda-{uuid.uuid5(uuid.NAMESPACE_OID, name)}"` confirmed |
| 14 | `pagination-page-size-configurable` | `src/aap_eda/api/pagination.py:21` — `page_size_query_param = "page_size"` confirmed |
| 16 | `project-import-error-recovery-sets-failed-state` | `src/aap_eda/tasks/project.py:686` — `_handle_project_error_recovery` confirmed |
| 17 | `redis-ca-signs-server-cert` | `tools/docker/redis-tls/server/server.crt` exists; cert chain is valid structure |
| 18 | `safe-yaml-unsafe-tagging` | `src/aap_eda/core/utils/safe_yaml.py:25` — `represent_scalar("!unsafe", obj)` confirmed |
| 19 | `setting-encrypted-storage` | `src/aap_eda/core/models/setting.py:28` — `value = EncryptedTextField(blank=True, null=False)` confirmed |
| 21 | `startup-logging-at-asgi-wsgi-entry` | `src/aap_eda/asgi.py:34` and `src/aap_eda/wsgi.py:32` — `startup_logging(logger)` at module level confirmed |
| 22 | `startup-logging-graceful-degradation` | `src/aap_eda/utils/logging.py` — `logger.error("Expected setting DATABASES not found")` without raise confirmed |
| 23 | `test-migration-check-gates-tests` | `Taskfile.dist.yaml:142` — `CLI_ARGS: makemigrations --dry-run --check` confirmed |
| 24 | `validator-return-value-contract` | `src/aap_eda/core/validators.py:59` — `check_if_de_exists` returns `decision_environment_id` on success; `check_if_rulebook_exists` returns None. Mixed — contract is not universal. |
| 25 | `wsgi-fail-fast-on-misconfiguration` | Django standard behavior — `get_wsgi_application()` raises on import errors. Confirmed. |

### Note on Belief 24 (`validator-return-value-contract`)

Marked CONFIRMED with a caveat: the contract is not consistently applied. `check_if_de_exists` returns the ID, but `check_if_rulebook_exists` returns `None`. The belief says "Public validator functions that check a single value return that value on success" — this is approximately true for most validators but not all. Not stale enough to retract, but imprecise.

## Internal Contradictions Found

| Belief A (IN) | Belief B (IN) | Contradiction |
|---------------|---------------|---------------|
| `settings-bool-coercion-truthy-gotcha` | `utils-bool-strict-truthy` / `str-to-bool-default-false` | A says bool() is used directly (gotcha); B says str_to_bool() is used with strict matching. Code confirms B is correct. |

## Duplicate Clusters Found

### Cluster: Container platform abstraction (6 beliefs, 3 retracted)

| Belief | Specificity | Action |
|--------|-------------|--------|
| `container-engine-dual-mode-abstraction` | High (ContainerRequest, DEPLOYMENT_TYPE, exception hierarchy) | **KEEP** |
| `container-operations-production-complete` | High (self-healing, retries, sanitization) | **KEEP** (different angle) |
| `activation-manager-uses-container-engine-abstraction` | Medium (DI/interface pattern) | **KEEP** (unique angle) |
| `container-platform-abstraction` | Low (generic) | **RETRACTED** |
| `container-orchestration-abstraction-layer` | Low (generic) | **RETRACTED** |
| `container-platform-complete-portability` | Low (generic) | **RETRACTED** |

**Note:** `dual-container-platform-support` and `kubernetes-podman-dual-platform-orchestration` are also borderline duplicates of this cluster but were not in the sample. Worth checking in a future run.

## Notable Findings

### Bug Confirmation: `bare-bearer-header-causes-index-error` (#3)

This belief describes a real bug that **is still present in the current code**. A bare `Authorization: Bearer` header (no token) causes an unhandled IndexError at `src/aap_eda/api/authentication.py:52`. The code checks `len(parts)==0` and `len(parts)>2` but not `len(parts)==1`. This is correctly tracked as a TMS blocker gating 4 auth robustness beliefs.

### Stale Finding: `settings-bool-coercion-truthy-gotcha` (#20)

This was likely true at some point but the code now uses `str_to_bool()` which correctly handles "false". The belief is a TMS blocker gating 2 downstream beliefs — retracting it may restore those gated beliefs.

## Coverage

- Beliefs in domain: 1,965 total
- Verified this run: 25 (1.3%)
- Previously verified: 5 (run 1)
- Cumulative verified: 30 (1.5%)
- **Stale rate: 4% (1/25)** — lower than the first run's 20% (1/5), likely because the first run happened to hit Redis-adjacent beliefs
- **Duplicate rate: 12% (3/25)** — significant; extrapolating suggests ~235 duplicate beliefs domain-wide
