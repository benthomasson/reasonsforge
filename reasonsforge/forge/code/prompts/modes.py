"""Analysis mode definitions for explore, propose, and derive prompts.

Each mode provides framing overlays that shift the model's attention
toward a specific class of findings (security issues, performance
problems, etc.) without replacing the base prompt structure.
"""

MODES = {
    "discover": {
        "label": "Discovery",
        "explore_role": (
            "You are a senior software engineer explaining code to a colleague."
        ),
        "explore_extra": "",
        "propose_extra": "",
        "derive_task_extra": "",
        "beliefs_extra": "",
        "topics_extra": "",
    },
    "security": {
        "label": "Security Audit",
        "explore_role": (
            "You are a senior security engineer auditing code for vulnerabilities."
        ),
        "explore_extra": """
## Security Focus

In addition to explaining the code, actively look for security issues:

- **Input validation**: Where is user input accepted? Is it validated, sanitized, escaped?
- **Authentication & authorization**: Are auth checks present and correct? Can they be bypassed?
- **Credential handling**: Are secrets hardcoded, logged, or exposed in error messages?
- **Injection vectors**: SQL injection, command injection, path traversal, template injection
- **Trust boundaries**: Where does trusted code interact with untrusted input or external systems?
- **Cryptography**: Weak algorithms, hardcoded keys, missing TLS verification
- **Access control**: Can users access resources they shouldn't? Are permissions checked consistently?
- **Data exposure**: Are sensitive fields leaked in logs, responses, or error messages?
- **Dependency risks**: Known vulnerable dependencies, unsafe deserialization

Flag every potential issue you find, even if you're not certain it's exploitable.
It is better to flag a false positive than to miss a real vulnerability.
""",
        "propose_extra": """
Security-focused beliefs to extract:
- Trust boundary violations: "User-supplied plugins execute with database credentials"
- Missing validation: "The /api/webhook endpoint accepts arbitrary JSON without schema validation"
- Credential exposure: "Database connection strings are passed to worker containers via environment variables"
- Auth bypass paths: "The admin API skips authentication when X-Internal-Request header is present"
- Injection surfaces: "User input flows from form field to SQL query without parameterization in report_builder"
""",
        "derive_task_extra": (
            "\n4. **Identify security implications** — look for chains where multiple "
            "individually-safe observations combine into a security concern. A credential "
            "being passed to a container is safe if the container runs trusted code, but "
            "becomes a vulnerability if the container runs user-supplied code. Derive these "
            "cross-cutting security findings.\n"
            "5. **Surface trust boundary violations** — where does untrusted input cross "
            "into trusted execution contexts? Where do credentials flow to components with "
            "broader access than intended?\n"
            "6. **Flag missing security controls** — if authentication, authorization, "
            "input validation, or rate limiting is absent where it should exist, derive "
            "a GATE belief that the positive claim (e.g., 'API is functional') should be "
            "gated on the negative claim (e.g., 'API lacks authentication')."
        ),
        "beliefs_extra": """
Security-focused beliefs:
- "User-supplied event source plugins execute arbitrary Python in containers that hold database credentials"
- "The webhook endpoint deserializes untrusted YAML using yaml.load() instead of yaml.safe_load()"
- "Session tokens are stored in localStorage, making them accessible to XSS attacks"
""",
        "topics_extra": """
Prioritize topics related to:
- Authentication and authorization handlers
- Input parsing and validation code
- Credential management and secret handling
- Code that processes external/user input
- Network-facing endpoints and API handlers
""",
    },
    "performance": {
        "label": "Performance Audit",
        "explore_role": (
            "You are a senior performance engineer analyzing code for "
            "efficiency and scalability issues."
        ),
        "explore_extra": """
## Performance Focus

In addition to explaining the code, actively look for performance issues:

- **Algorithmic complexity**: O(n^2) or worse operations on potentially large datasets
- **N+1 queries**: Database queries inside loops, missing eager loading
- **Missing indexes**: Queries that filter/sort on unindexed columns
- **Memory**: Unbounded collections, loading entire datasets into memory, missing pagination
- **Concurrency**: Lock contention, thread safety issues, blocking I/O in async contexts
- **Caching**: Missing caches for expensive computations, cache invalidation bugs
- **I/O patterns**: Synchronous I/O blocking event loops, excessive network round-trips
- **Resource leaks**: Unclosed connections, file handles, or cursors
- **Serialization overhead**: Expensive JSON/XML parsing in hot paths
- **Startup cost**: Heavy initialization that delays application readiness

Flag every potential issue you find, even if it only matters at scale.
Performance problems that are invisible at low load become critical at production scale.
""",
        "propose_extra": """
Performance-focused beliefs to extract:
- Algorithmic issues: "Topic queue uses O(n) linear scan on every pop operation"
- Query patterns: "get_all_users() loads every user into memory without pagination"
- Concurrency issues: "The worker pool uses threading but the GIL serializes CPU-bound processing"
- Resource management: "Database connections are created per-request without pooling"
- Scaling limits: "The in-memory cache has no eviction policy and grows unbounded"
""",
        "derive_task_extra": (
            "\n4. **Identify performance bottlenecks** — look for chains where multiple "
            "observations reveal a scaling problem. A linear scan is fine for 100 items but "
            "becomes a bottleneck at 100K. Derive these scaling-aware conclusions.\n"
            "5. **Surface resource contention** — where do multiple components compete for "
            "the same resource (database connections, locks, memory)? Derive conclusions "
            "about contention patterns.\n"
            "6. **Flag missing performance controls** — if pagination, caching, connection "
            "pooling, or rate limiting is absent where it should exist, derive a GATE belief "
            "that the positive claim (e.g., 'query returns results') should be gated on the "
            "negative claim (e.g., 'query lacks pagination')."
        ),
        "beliefs_extra": """
Performance-focused beliefs:
- "The search index rebuilds synchronously on every write, blocking the event loop for large datasets"
- "Worker processes share a single database connection instead of using a connection pool"
- "The report generator loads all historical records into memory before filtering"
""",
        "topics_extra": """
Prioritize topics related to:
- Database query patterns and ORM usage
- Caching layers and invalidation logic
- Event loops, async patterns, and concurrency
- Data processing pipelines and batch operations
- Resource allocation and cleanup
""",
    },
}

VALID_MODES = tuple(MODES.keys())


def get_mode(mode_name):
    if mode_name not in MODES:
        raise ValueError(
            f"Unknown mode: {mode_name!r}. Valid modes: {', '.join(VALID_MODES)}"
        )
    return MODES[mode_name]
