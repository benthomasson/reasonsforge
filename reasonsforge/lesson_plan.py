"""Generate a sequenced lesson plan from FOL relations in a TMS domain.

Topological sort on requires/extends edges produces teaching order.
Module grouping from contains/is-a. Motivation from enables.
Cross-references from maps-to. No LLM needed — pure graph traversal.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Optional

from .relations import extract_all_relations, Relation


ORDERING_RELATIONS = {"requires", "extends"}
GROUPING_RELATIONS = {"contains", "is-a"}
MOTIVATION_RELATIONS = {"enables"}
EXAMPLE_RELATIONS = {"implements"}
CROSSREF_RELATIONS = {"maps-to"}

MODULE_KEYWORDS = [
    ("foundations", ["set", "zfc", "nbg", "axiom", "cardinality", "paradox",
                     "logic", "proof", "predicate", "propositional"]),
    ("group-theory", ["group", "subgroup", "abelian", "cyclic", "permutation",
                      "symmetry", "homomorphism", "isomorphism", "coset",
                      "lagrange", "cayley"]),
    ("ring-and-field-theory", ["ring", "field", "ideal", "polynomial",
                               "frobenius", "galois", "commutative",
                               "integral-domain", "euclidean", "principal"]),
    ("linear-algebra", ["vector", "matrix", "linear", "dimension", "basis",
                        "kernel", "eigenvalue", "determinant", "rank",
                        "span", "transformation", "gaussian"]),
    ("representation-theory", ["representation", "maschke", "reducible",
                               "character", "module", "composition",
                               "semisimple"]),
    ("algebra-structures", ["algebra", "star", "jordan", "lie", "tensor",
                            "antimultiplicativity", "involution"]),
]


def _classify_module(concept: str) -> str:
    concept_lower = concept.lower()
    for module_name, keywords in MODULE_KEYWORDS:
        if any(kw in concept_lower for kw in keywords):
            return module_name
    return "other"


def build_lesson_plan(nodes: dict) -> dict:
    """Build a lesson plan from a TMS node dict.

    Args:
        nodes: dict of node_id -> node data (from api.export_network)

    Returns:
        dict with keys: lessons (list), modules (dict), stats (dict)
    """
    triples = extract_all_relations(nodes)

    prereqs: dict[str, set[str]] = defaultdict(set)
    enables_map: dict[str, list[str]] = defaultdict(list)
    contains_map: dict[str, list[str]] = defaultdict(list)
    isa_map: dict[str, list[str]] = defaultdict(list)
    implements_map: dict[str, list[str]] = defaultdict(list)
    maps_to_map: dict[str, list[str]] = defaultdict(list)

    concepts: set[str] = set()

    for belief_id, rel in triples:
        concepts.add(rel.subject)
        concepts.add(rel.object)

        if rel.relation in ORDERING_RELATIONS:
            prereqs[rel.subject].add(rel.object)
        elif rel.relation == "enables":
            enables_map[rel.subject].append(rel.object)
        elif rel.relation == "contains":
            contains_map[rel.subject].append(rel.object)
        elif rel.relation == "is-a":
            isa_map[rel.subject].append(rel.object)
        elif rel.relation == "implements":
            implements_map[rel.subject].append(rel.object)
        elif rel.relation == "maps-to":
            maps_to_map[rel.subject].append(rel.object)

    order = _topological_sort(concepts, prereqs)

    modules: dict[str, list[dict]] = defaultdict(list)
    lessons = []
    for i, concept in enumerate(order, 1):
        module = _classify_module(concept)
        motivation = None
        enabled = enables_map.get(concept, [])
        if enabled:
            motivation = f"Unlocks: {', '.join(enabled)}"

        crossrefs = maps_to_map.get(concept, [])
        examples = implements_map.get(concept, [])
        lesson_prereqs = sorted(prereqs.get(concept, set()))

        lesson = {
            "number": i,
            "concept": concept,
            "module": module,
            "prerequisites": lesson_prereqs,
            "motivation": motivation,
            "examples": examples,
            "cross_references": crossrefs,
        }
        lessons.append(lesson)
        modules[module].append(lesson)

    stats = {
        "total_lessons": len(lessons),
        "total_modules": len(modules),
        "total_relations": len(triples),
        "concepts": len(concepts),
        "relations_by_type": _count_relation_types(triples),
    }

    return {
        "lessons": lessons,
        "modules": dict(modules),
        "stats": stats,
    }


def _count_relation_types(triples: list[tuple[str, Relation]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for _, rel in triples:
        counts[rel.relation] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def _topological_sort(concepts: set[str], prereqs: dict[str, set[str]]) -> list[str]:
    """Kahn's algorithm. Concepts with no prerequisites come first."""
    in_degree: dict[str, int] = {c: 0 for c in concepts}
    dependents: dict[str, list[str]] = defaultdict(list)

    for concept, reqs in prereqs.items():
        for req in reqs:
            if req in concepts:
                in_degree[concept] = in_degree.get(concept, 0) + 1
                dependents[req].append(concept)

    queue = sorted([c for c, d in in_degree.items() if d == 0])
    result = []

    while queue:
        current = queue.pop(0)
        result.append(current)
        for dep in sorted(dependents.get(current, [])):
            in_degree[dep] -= 1
            if in_degree[dep] == 0:
                queue.append(dep)
        queue.sort()

    remaining = sorted(concepts - set(result))
    result.extend(remaining)

    return result


def format_lesson_plan_markdown(plan: dict, title: Optional[str] = None) -> str:
    """Format a lesson plan as markdown."""
    lines = []
    title = title or "Lesson Plan"
    lines.append(f"# {title}")
    lines.append("")

    stats = plan["stats"]
    lines.append(f"**{stats['total_lessons']} lessons** across "
                 f"**{stats['total_modules']} modules** from "
                 f"**{stats['total_relations']} relations**")
    lines.append("")

    module_order = [name for name, _ in MODULE_KEYWORDS] + ["other"]
    seen_modules = set()

    for module_name in module_order:
        if module_name not in plan["modules"]:
            continue
        seen_modules.add(module_name)
        _format_module(lines, module_name, plan["modules"][module_name])

    for module_name in sorted(plan["modules"].keys()):
        if module_name not in seen_modules:
            _format_module(lines, module_name, plan["modules"][module_name])

    return "\n".join(lines)


def _format_module(lines: list[str], module_name: str, lessons: list[dict]) -> None:
    display = module_name.replace("-", " ").title()
    lines.append(f"## Module: {display}")
    lines.append(f"*{len(lessons)} lessons*")
    lines.append("")

    for lesson in lessons:
        lines.append(f"### Lesson {lesson['number']}: {lesson['concept']}")
        if lesson["prerequisites"]:
            lines.append(f"- **Prerequisites**: {', '.join(lesson['prerequisites'])}")
        if lesson["motivation"]:
            lines.append(f"- **Motivation**: {lesson['motivation']}")
        if lesson["examples"]:
            lines.append(f"- **Examples**: {', '.join(lesson['examples'])}")
        if lesson["cross_references"]:
            lines.append(f"- **See also**: {', '.join(lesson['cross_references'])}")
        lines.append("")


def format_lesson_plan_json(plan: dict) -> dict:
    """Return a JSON-serializable lesson plan."""
    return plan
