"""Routing forge commands — build and query routing EEMs."""

from __future__ import annotations

import sys
from pathlib import Path

from .. import REASONS_DB


def cmd_extract(args):
    """Extract routing beliefs from a domain EEM."""
    from .pipeline import stage_extract

    domain_db = getattr(args, "input", None)
    if not domain_db:
        print("Error: --input is required (path to domain EEM)", file=sys.stderr)
        sys.exit(1)

    routing_db = getattr(args, "output", "routing.db")
    added = stage_extract(args, domain_db, routing_db)
    print(f"Added {added} routing belief(s) to {routing_db}", file=sys.stderr)


def cmd_extract_all(args):
    """Extract routing beliefs from multiple domain EEMs."""
    from .pipeline import stage_extract

    inputs = getattr(args, "inputs", None) or []
    if not inputs:
        print("Error: provide one or more domain EEM paths", file=sys.stderr)
        sys.exit(1)

    routing_db = getattr(args, "output", "routing.db")
    total = 0

    for domain_db in inputs:
        if not Path(domain_db).exists():
            print(f"Skipping {domain_db}: not found", file=sys.stderr)
            continue
        print(f"\nExtracting from {domain_db}...", file=sys.stderr)
        added = stage_extract(args, domain_db, routing_db)
        total += added

    print(f"\nTotal: {total} routing belief(s) added to {routing_db}", file=sys.stderr)


def cmd_derive(args):
    """Derive cross-domain routing relationships."""
    from .pipeline import stage_derive

    routing_db = getattr(args, "output", "routing.db")
    added = stage_derive(args, routing_db)
    print(f"Added {added} cross-domain belief(s) to {routing_db}", file=sys.stderr)


def cmd_query(args):
    """Test routing by searching the routing EEM."""
    from .pipeline import stage_query

    routing_db = getattr(args, "output", "routing.db")
    query = getattr(args, "query", None)
    if not query:
        print("Error: provide a query string", file=sys.stderr)
        sys.exit(1)

    results = stage_query(args, routing_db, query)

    if not results:
        print("No matching domains found.")
        return

    domains_seen = {}
    for belief in results:
        parts = belief["id"].split(".")
        domain_part = parts[0].replace("routing:", "")
        if domain_part not in domains_seen:
            domains_seen[domain_part] = []
        domains_seen[domain_part].append(belief)

    print(f"Query: {query}")
    print(f"Matched {len(domains_seen)} domain(s):\n")

    for domain, beliefs in domains_seen.items():
        print(f"  {domain}:")
        for b in beliefs[:3]:
            text = b["text"][:100]
            print(f"    - {b['id']}: {text}")
        if len(beliefs) > 3:
            print(f"    ... and {len(beliefs) - 3} more")
        print()


def cmd_status(args):
    """Show routing EEM status."""
    routing_db = getattr(args, "output", "routing.db")

    if not Path(routing_db).exists():
        print(f"No routing EEM found at {routing_db}")
        print("Run: reasonsforge routing extract --input domain.db")
        return

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=routing_db)
        nodes = network.get("nodes", {})
    except Exception:
        nodes = {}

    routing_nodes = {k: v for k, v in nodes.items() if k.startswith("routing:")}
    cross_nodes = {k: v for k, v in routing_nodes.items() if ":cross." in k}
    domain_nodes = {k: v for k, v in routing_nodes.items() if ":cross." not in k}

    domains = set()
    for nid in domain_nodes:
        parts = nid.replace("routing:", "").split(".")
        if parts:
            domains.add(parts[0])

    print(f"Routing EEM: {routing_db}")
    print(f"  Total beliefs: {len(nodes)}")
    print(f"  Routing beliefs: {len(routing_nodes)}")
    print(f"  Domain-specific: {len(domain_nodes)}")
    print(f"  Cross-domain: {len(cross_nodes)}")
    print(f"  Domains indexed: {len(domains)}")
    if domains:
        for d in sorted(domains):
            count = sum(1 for k in domain_nodes if k.startswith(f"routing:{d}."))
            print(f"    - {d}: {count} beliefs")
