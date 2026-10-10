"""Routing forge pipeline — extract routing beliefs from domain EEMs."""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from .prompts import DERIVE_ROUTING_PROMPT, EXTRACT_ROUTING_PROMPT


def _load_domain_beliefs(db_path: str, max_beliefs: int = 200) -> list[dict]:
    """Load IN beliefs from a domain EEM."""
    from reasonsforge.api import export_network

    network = export_network(db_path=db_path)
    nodes = network.get("nodes", {})

    beliefs = []
    for nid, node in sorted(nodes.items()):
        if node.get("truth_value") != "IN":
            continue
        beliefs.append({
            "id": nid,
            "text": node.get("text", ""),
            "source": node.get("source", ""),
        })

    if len(beliefs) > max_beliefs:
        high_level = [b for b in beliefs if "." not in b["id"]]
        detailed = [b for b in beliefs if "." in b["id"]]
        beliefs = high_level + detailed[:max_beliefs - len(high_level)]

    return beliefs


def _beliefs_to_text(beliefs: list[dict]) -> str:
    """Format beliefs for LLM prompt."""
    lines = []
    for b in beliefs:
        text = b["text"][:200]
        lines.append(f"- `{b['id']}`: {text}")
    return "\n".join(lines)


def _parse_routing_beliefs(response: str) -> list[dict]:
    """Parse routing belief items from LLM response."""
    beliefs = []
    pattern = re.compile(r"^[-*]\s+`([^`]+)`\s*(?:—|-|:)\s*(.+)$", re.MULTILINE)
    for m in pattern.finditer(response):
        beliefs.append({"id": m.group(1), "text": m.group(2).strip()})
    return beliefs


def _domain_slug(db_path: str) -> str:
    """Extract a slug from a database path."""
    name = Path(db_path).stem
    return name.replace(" ", "-").replace("_", "-").lower()


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------


def stage_extract(args, domain_db: str, routing_db: str) -> int:
    """Extract routing beliefs from a single domain EEM."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 600)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    domain_path = Path(domain_db)
    if not domain_path.exists():
        print(f"Domain EEM not found: {domain_db}", file=sys.stderr)
        sys.exit(1)

    slug = _domain_slug(domain_db)
    print(f"  Loading beliefs from {domain_path.name}...", file=sys.stderr)
    beliefs = _load_domain_beliefs(domain_db)

    if not beliefs:
        print(f"  No IN beliefs found in {domain_db}", file=sys.stderr)
        return 0

    beliefs_text = _beliefs_to_text(beliefs)

    prompt = EXTRACT_ROUTING_PROMPT.format(
        domain_name=slug,
        domain_slug=slug,
        belief_count=len(beliefs),
        shown_count=len(beliefs),
        beliefs_text=beliefs_text,
    )

    print(f"  Extracting routing beliefs with {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error during extraction: {e}", file=sys.stderr)
        sys.exit(1)

    routing_beliefs = _parse_routing_beliefs(result)
    print(f"  Extracted {len(routing_beliefs)} routing belief(s)", file=sys.stderr)

    if not routing_beliefs:
        return 0

    from reasonsforge.api import add_node, init_db

    if not Path(routing_db).exists():
        init_db(db_path=routing_db)

    added = 0
    for belief in routing_beliefs:
        try:
            add_node(
                belief["id"],
                belief["text"],
                source=f"routing-forge:extract:{slug}",
                db_path=routing_db,
            )
            added += 1
        except Exception as e:
            if "already exists" not in str(e):
                print(f"  Failed to add {belief['id']}: {e}", file=sys.stderr)

    return added


def stage_derive(args, routing_db: str) -> int:
    """Derive cross-domain routing relationships."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 600)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    routing_path = Path(routing_db)
    if not routing_path.exists():
        print(f"Routing EEM not found: {routing_db}", file=sys.stderr)
        print("Run 'reasonsforge routing extract' first.", file=sys.stderr)
        sys.exit(1)

    beliefs = _load_domain_beliefs(routing_db, max_beliefs=500)
    routing_only = [b for b in beliefs if b["id"].startswith("routing:")]

    if len(routing_only) < 2:
        print("  Need routing beliefs from at least 2 domains to derive relationships.",
              file=sys.stderr)
        return 0

    domains_seen = set()
    for b in routing_only:
        parts = b["id"].split(".")
        if len(parts) >= 2:
            domain_part = parts[0].replace("routing:", "")
            if domain_part != "cross":
                domains_seen.add(domain_part)

    print(f"  Deriving relationships across {len(domains_seen)} domain(s) "
          f"({len(routing_only)} routing beliefs)...", file=sys.stderr)

    routing_beliefs_text = _beliefs_to_text(routing_only)
    prompt = DERIVE_ROUTING_PROMPT.format(routing_beliefs_text=routing_beliefs_text)

    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error during derive: {e}", file=sys.stderr)
        sys.exit(1)

    cross_beliefs = _parse_routing_beliefs(result)
    print(f"  Derived {len(cross_beliefs)} cross-domain belief(s)", file=sys.stderr)

    if not cross_beliefs:
        return 0

    from reasonsforge.api import add_node

    added = 0
    for belief in cross_beliefs:
        try:
            add_node(
                belief["id"],
                belief["text"],
                source="routing-forge:derive",
                db_path=routing_db,
            )
            added += 1
        except Exception as e:
            if "already exists" not in str(e):
                print(f"  Failed to add {belief['id']}: {e}", file=sys.stderr)

    return added


def stage_query(args, routing_db: str, query: str) -> list[dict]:
    """Search the routing EEM for domains matching a query."""
    routing_path = Path(routing_db)
    if not routing_path.exists():
        print(f"Routing EEM not found: {routing_db}", file=sys.stderr)
        sys.exit(1)

    beliefs = _load_domain_beliefs(routing_db, max_beliefs=1000)
    routing_only = [b for b in beliefs if b["id"].startswith("routing:")]

    query_terms = set(re.findall(r'[a-z]{3,}', query.lower()))
    scored = []
    for belief in routing_only:
        belief_terms = set(re.findall(r'[a-z]{3,}', belief["text"].lower()))
        belief_terms |= set(re.findall(r'[a-z]{3,}', belief["id"].lower()))
        overlap = len(query_terms & belief_terms)
        if overlap > 0:
            scored.append((overlap, belief))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [b for _, b in scored[:10]]
