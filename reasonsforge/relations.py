"""First-class FOL relation support for reasonsforge.

Extracts, stores, and queries cross-belief relations as structured metadata.
Relations are encoded in belief names using a 12-type vocabulary
(e.g., ``X-requires-Y``) and stored as triples in node metadata.

See: reasons-service-expert/entries/2026/10/13/first-class-fol-relations-in-reasonsforge.md
GitHub: reasonsforge#40
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


RELATION_TYPES = [
    "maps-to",
    "is-a",
    "has-a",
    "implements",
    "requires",
    "produces",
    "replaces",
    "prevents",
    "contains",
    "extends",
    "enables",
    "causes",
]

RELATION_TYPES_SORTED = sorted(RELATION_TYPES, key=len, reverse=True)

BROAD_PATTERNS = [
    "is-not",
    "is",
]

ALL_PATTERNS = RELATION_TYPES_SORTED + sorted(BROAD_PATTERNS, key=len, reverse=True)

TRANSITIVE_RELATIONS = {"requires", "extends", "causes", "contains", "maps-to"}

OBLIGATION_RELATIONS = {"requires", "implements"}


@dataclass
class Relation:
    """A directed relation between two beliefs."""
    subject: str
    relation: str
    object: str

    def inverse_direction(self) -> Relation:
        return Relation(subject=self.object, relation=self.relation, object=self.subject)

    def to_dict(self, direction: str = "outgoing") -> dict:
        if direction == "outgoing":
            return {"relation": self.relation, "target": self.object, "direction": "outgoing"}
        return {"relation": self.relation, "target": self.subject, "direction": "incoming"}


def extract_triple(belief_id: str, patterns: list[str] | None = None) -> Relation | None:
    """Extract a relation triple from a belief ID.

    Matches the longest relation keyword found between hyphens.
    Returns None if no relation pattern is found.

    >>> extract_triple("ring-requires-group")
    Relation(subject='ring', relation='requires', object='group')
    >>> extract_triple("plain-fact-about-something") is None
    True
    """
    if patterns is None:
        patterns = ALL_PATTERNS

    for rel in patterns:
        marker = f"-{rel}-"
        idx = belief_id.find(marker)
        if idx != -1:
            subject = belief_id[:idx]
            obj = belief_id[idx + len(marker):]
            if subject and obj:
                return Relation(subject=subject, relation=rel, object=obj)
    return None


def extract_all_relations(
    nodes: dict,
    relation_filter: str | None = None,
    include_broad_is: bool = False,
) -> list[tuple[str, Relation]]:
    """Extract all relational triples from a node dict.

    Args:
        nodes: dict of node_id -> node data (as from network.json or Network)
        relation_filter: if set, only extract this relation type
        include_broad_is: if True, include broad "is" and "is-not" patterns

    Returns:
        list of (belief_id, Relation) tuples for IN beliefs
    """
    if relation_filter:
        patterns = [relation_filter]
    elif include_broad_is:
        patterns = ALL_PATTERNS
    else:
        patterns = RELATION_TYPES_SORTED

    results = []
    for node_id, node in sorted(nodes.items()):
        tv = node.get("truth_value", "IN") if isinstance(node, dict) else getattr(node, "truth_value", "IN")
        if tv != "IN":
            continue
        rel = extract_triple(node_id, patterns)
        if rel:
            results.append((node_id, rel))
    return results


def store_relation_metadata(node_metadata: dict, relation: Relation, direction: str = "outgoing") -> None:
    """Add a relation triple to a node's metadata dict.

    Appends to the ``relations`` list, creating it if absent.
    Skips duplicates.
    """
    relations = node_metadata.setdefault("relations", [])
    entry = relation.to_dict(direction)
    if entry not in relations:
        relations.append(entry)


def get_relations(
    node_metadata: dict,
    relation_type: str | None = None,
    direction: str | None = None,
) -> list[dict]:
    """Query relations from a node's metadata.

    Args:
        node_metadata: the node's metadata dict
        relation_type: filter by relation type (e.g., "requires")
        direction: filter by "outgoing" or "incoming"

    Returns:
        list of relation dicts matching the filters
    """
    relations = node_metadata.get("relations", [])
    if relation_type:
        relations = [r for r in relations if r["relation"] == relation_type]
    if direction:
        relations = [r for r in relations if r["direction"] == direction]
    return relations


def has_stored_relations(network) -> bool:
    """Check if any node in the network already has stored relation metadata."""
    for node in network.nodes.values():
        if node.metadata.get("relations"):
            return True
    return False


def ensure_relations(network, include_broad_is: bool = False) -> None:
    """Apply relations only if none are already stored."""
    if not has_stored_relations(network):
        apply_relations_to_network(network, include_broad_is=include_broad_is)


def apply_relations_to_network(network, include_broad_is: bool = False) -> int:
    """Extract relations from all belief names and store in node metadata.

    Args:
        network: a Network instance with a ``nodes`` dict of Node objects

    Returns:
        count of relations stored
    """
    nodes_dict = {}
    for node_id, node in network.nodes.items():
        nodes_dict[node_id] = {"truth_value": node.truth_value}

    triples = extract_all_relations(nodes_dict, include_broad_is=include_broad_is)
    count = 0

    for belief_id, rel in triples:
        if belief_id in network.nodes:
            store_relation_metadata(network.nodes[belief_id].metadata, rel, "outgoing")
            count += 1

        if rel.object in network.nodes:
            store_relation_metadata(network.nodes[rel.object].metadata, rel, "incoming")
            count += 1

        if rel.subject in network.nodes and rel.subject != belief_id:
            store_relation_metadata(network.nodes[rel.subject].metadata, rel, "outgoing")
            count += 1

    return count


def build_subject_index(network) -> dict[str, list[dict]]:
    """Build an index from subject names to their outgoing relations.

    Since relational beliefs encode the subject in their name
    (e.g., ``eem-build-requires-full-python-stack`` has subject ``eem-build``),
    this index lets us look up relations by subject even when the subject
    is not a standalone node.
    """
    index: dict[str, list[dict]] = {}
    for node in network.nodes.values():
        for rel_dict in node.metadata.get("relations", []):
            if rel_dict["direction"] == "outgoing":
                triple = extract_triple(node.id)
                if triple:
                    index.setdefault(triple.subject, []).append(rel_dict)
            if rel_dict["direction"] == "outgoing" and node.id in network.nodes:
                index.setdefault(node.id, []).append(rel_dict)
    return index


def transitive_closure(
    network,
    start_node_id: str,
    relation_type: str,
    max_depth: int = 20,
) -> list[str]:
    """Follow a transitive relation to its full chain.

    Looks up relations both by node ID and by subject name extracted
    from relational belief names.

    Args:
        network: a Network instance
        start_node_id: node ID or subject name to start from
        relation_type: must be in TRANSITIVE_RELATIONS
        max_depth: prevent infinite loops

    Returns:
        ordered list of node IDs in the chain (excluding start)
    """
    if relation_type not in TRANSITIVE_RELATIONS:
        return []

    subject_idx = build_subject_index(network)

    visited = set()
    chain = []
    current = [start_node_id]

    for _ in range(max_depth):
        next_level = []
        for node_id in current:
            rels = []
            if node_id in network.nodes:
                rels.extend(get_relations(
                    network.nodes[node_id].metadata,
                    relation_type=relation_type,
                    direction="outgoing",
                ))
            rels.extend(subject_idx.get(node_id, []))
            seen_targets = set()
            for r in rels:
                if r["relation"] != relation_type:
                    continue
                target = r["target"]
                if target not in visited and target != start_node_id and target not in seen_targets:
                    seen_targets.add(target)
                    visited.add(target)
                    chain.append(target)
                    next_level.append(target)
        if not next_level:
            break
        current = next_level

    return chain


def find_unmet_requirements(network) -> list[tuple[str, str, str]]:
    """Find beliefs where a required target is OUT.

    Returns:
        list of (belief_id, relation_belief_id, required_target) tuples
    """
    results = []
    for node_id, node in network.nodes.items():
        if node.truth_value != "IN":
            continue
        for rel_dict in get_relations(node.metadata, relation_type="requires", direction="outgoing"):
            target = rel_dict["target"]
            if target in network.nodes and network.nodes[target].truth_value == "OUT":
                results.append((node_id, f"{node_id}-requires-{target}", target))
    return results


def find_prevention_conflicts(network) -> list[tuple[str, str]]:
    """Find cases where A-prevents-B but both A and B are IN.

    Returns:
        list of (preventer_id, prevented_id) tuples
    """
    results = []
    for node_id, node in network.nodes.items():
        if node.truth_value != "IN":
            continue
        for rel_dict in get_relations(node.metadata, relation_type="prevents", direction="outgoing"):
            target = rel_dict["target"]
            if target in network.nodes and network.nodes[target].truth_value == "IN":
                results.append((node_id, target))
    return results


def relation_stats(network) -> dict:
    """Compute summary statistics about relations in a network."""
    from collections import Counter
    type_counts = Counter()
    nodes_with_relations = 0
    total_relations = 0

    for node in network.nodes.values():
        rels = node.metadata.get("relations", [])
        outgoing = [r for r in rels if r["direction"] == "outgoing"]
        if outgoing:
            nodes_with_relations += 1
            total_relations += len(outgoing)
            for r in outgoing:
                type_counts[r["relation"]] += 1

    return {
        "total_relations": total_relations,
        "nodes_with_relations": nodes_with_relations,
        "total_nodes": len(network.nodes),
        "coverage_pct": round(100 * nodes_with_relations / max(len(network.nodes), 1), 1),
        "by_type": dict(type_counts.most_common()),
    }
