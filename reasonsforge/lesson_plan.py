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

def _classify_modules(concepts: set[str], triples: list[tuple[str, Relation]]) -> dict[str, str]:
    """Classify concepts into modules using the relation graph.

    Strategy:
    1. Explicit grouping: contains/is-a relations define parent modules
    2. Connected components: concepts linked by any relation share a module
    3. The module is named after the most-connected concept in the component
    """
    explicit_groups: dict[str, str] = {}
    for _, rel in triples:
        if rel.relation == "contains" and rel.subject in concepts:
            explicit_groups[rel.object] = rel.subject
        elif rel.relation == "is-a" and rel.object in concepts:
            explicit_groups[rel.subject] = rel.object

    adjacency: dict[str, set[str]] = defaultdict(set)
    for _, rel in triples:
        if rel.subject in concepts and rel.object in concepts:
            adjacency[rel.subject].add(rel.object)
            adjacency[rel.object].add(rel.subject)

    visited: set[str] = set()
    components: list[set[str]] = []

    for concept in sorted(concepts):
        if concept in visited:
            continue
        component: set[str] = set()
        queue = [concept]
        while queue:
            current = queue.pop(0)
            if current in visited:
                continue
            visited.add(current)
            component.add(current)
            for neighbor in adjacency.get(current, set()):
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(component)

    concept_to_module: dict[str, str] = {}

    for component in components:
        for c in component:
            if c in explicit_groups:
                module_name = explicit_groups[c]
                break
        else:
            degree = {c: len(adjacency.get(c, set())) for c in component}
            module_name = max(component, key=lambda c: (degree[c], c))

        for c in component:
            concept_to_module[c] = module_name

    for c in concepts:
        if c not in concept_to_module:
            concept_to_module[c] = c

    min_size = 3
    module_members: dict[str, list[str]] = defaultdict(list)
    for c, m in concept_to_module.items():
        module_members[m].append(c)

    for module_name, members in list(module_members.items()):
        if len(members) < min_size:
            for c in members:
                concept_to_module[c] = "other"

    return concept_to_module


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

    in_node_ids = {nid for nid, n in nodes.items()
                   if (n.get("truth_value") if isinstance(n, dict)
                       else getattr(n, "truth_value", "IN")) == "IN"}

    def _is_concept(name: str) -> bool:
        """Filter out data points that aren't teachable concepts."""
        if name in in_node_ids:
            return True
        first_word = name.split("-")[0] if name else ""
        if first_word.isdigit():
            return False
        return True

    concepts: set[str] = set()

    for belief_id, rel in triples:
        if _is_concept(rel.subject):
            concepts.add(rel.subject)
        if _is_concept(rel.object):
            concepts.add(rel.object)

        both_real = rel.subject in concepts and rel.object in concepts
        if rel.relation in ORDERING_RELATIONS and both_real:
            prereqs[rel.subject].add(rel.object)
        elif rel.relation == "enables" and both_real:
            enables_map[rel.subject].append(rel.object)
        elif rel.relation == "contains" and both_real:
            contains_map[rel.subject].append(rel.object)
        elif rel.relation == "is-a" and both_real:
            isa_map[rel.subject].append(rel.object)
        elif rel.relation == "implements" and both_real:
            implements_map[rel.subject].append(rel.object)
        elif rel.relation == "maps-to" and both_real:
            maps_to_map[rel.subject].append(rel.object)

    order = _topological_sort(concepts, prereqs)
    concept_to_module = _classify_modules(concepts, triples)

    modules: dict[str, list[dict]] = defaultdict(list)
    lessons = []
    for i, concept in enumerate(order, 1):
        module = concept_to_module.get(concept, concept)
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

    module_sizes = sorted(plan["modules"].items(),
                          key=lambda x: (-len(x[1]), x[0]))
    for module_name, module_lessons in module_sizes:
        _format_module(lines, module_name, module_lessons)

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


def build_lesson_prompt(
    lesson: dict,
    beliefs: list[dict],
    domain: str = "",
    completed: list[str] | None = None,
) -> str:
    """Build an LLM prompt for generating a single lesson.

    Augments the standard lesson prompt with plan context:
    prerequisites, motivation, cross-references.

    Args:
        lesson: a lesson dict from build_lesson_plan
        beliefs: list of {"id": str, "text": str} for this concept
        domain: domain name for context
        completed: list of concept names the student has already learned
    """
    concept = lesson["concept"]
    display = concept.replace("-", " ").title()
    domain_clause = f' from the "{domain}" knowledge domain' if domain else ""

    parts = [
        f'You are an expert teacher creating a lesson about "{display}"{domain_clause}.',
        "",
    ]

    if lesson.get("prerequisites"):
        prereq_names = [p.replace("-", " ").title() for p in lesson["prerequisites"]]
        parts.append(f"Prerequisites: {', '.join(prereq_names)}.")
        if completed:
            completed_set = set(completed)
            met = [p for p in lesson["prerequisites"] if p in completed_set]
            if met:
                met_names = [p.replace("-", " ").title() for p in met]
                parts.append(
                    f"The student has already learned: {', '.join(met_names)}. "
                    "Build on that knowledge — don't re-explain these concepts."
                )
        parts.append("")

    if lesson.get("motivation"):
        parts.append(f"Context: {lesson['motivation']}.")
        parts.append("Open the lesson by explaining why this concept matters.")
        parts.append("")

    parts.append(
        "Using the beliefs below, write a clear, structured lesson that:\n"
        "1. Introduces the key concepts and why they matter\n"
        "2. Explains how the concepts relate to each other\n"
        "3. Highlights the most important things to understand\n"
        "4. Notes any nuances, exceptions, or common misconceptions"
    )
    parts.append("")

    if lesson.get("cross_references"):
        xref_names = [x.replace("-", " ").title() for x in lesson["cross_references"]]
        parts.append(
            f"Where relevant, connect to: {', '.join(xref_names)}."
        )
        parts.append("")

    if lesson.get("examples"):
        ex_names = [e.replace("-", " ").title() for e in lesson["examples"]]
        parts.append(
            f"Include concrete examples related to: {', '.join(ex_names)}."
        )
        parts.append("")

    parts.append(
        "Write 3-5 paragraphs. Be clear and educational. Use plain language. "
        "Refer to beliefs by their IDs to ground your claims."
    )
    parts.append("")

    beliefs_text = "\n".join(f"[{b['id']}] {b['text']}" for b in beliefs)
    parts.append(f"Available beliefs:\n{beliefs_text}")
    parts.append("")
    parts.append(
        "Start with TITLE: followed by a lesson title on one line.\n"
        "Then write the lesson.\n"
        "End with BELIEFS: followed by a comma-separated list "
        "of the belief IDs you referenced."
    )

    return "\n".join(parts)


def select_beliefs_for_lesson(
    lesson: dict,
    nodes: dict,
    max_beliefs: int = 30,
) -> list[dict]:
    """Select relevant beliefs for a lesson concept.

    Matches beliefs whose ID contains the concept name or
    whose text mentions it. Prioritizes exact ID matches.
    """
    concept = lesson["concept"]
    concept_words = set(concept.split("-"))

    scored: list[tuple[float, str]] = []
    for node_id, node in nodes.items():
        if node.get("truth_value") != "IN":
            continue
        node_words = set(node_id.split("-"))
        overlap = len(concept_words & node_words)
        if overlap == 0:
            text_lower = node.get("text", "").lower()
            concept_readable = concept.replace("-", " ")
            if concept_readable in text_lower:
                overlap = 0.5
        if overlap > 0:
            is_relational = any(f"-{r}-" in node_id for r in ORDERING_RELATIONS | MOTIVATION_RELATIONS | EXAMPLE_RELATIONS | CROSSREF_RELATIONS | GROUPING_RELATIONS)
            priority = overlap + (0 if is_relational else 0.1)
            scored.append((priority, node_id))

    scored.sort(key=lambda x: (-x[0], x[1]))
    selected = scored[:max_beliefs]

    return [
        {"id": node_id, "text": nodes[node_id].get("text", "")}
        for _, node_id in selected
    ]


def generate_lesson(
    lesson: dict,
    nodes: dict,
    model: str = "claude",
    domain: str = "",
    completed: list[str] | None = None,
    timeout: int = 300,
    invoke_fn=None,
) -> dict | None:
    """Generate a single lesson from a plan entry.

    Args:
        lesson: lesson dict from build_lesson_plan
        nodes: full node dict from api.export_network
        model: LLM model name
        domain: domain name
        completed: concepts already taught
        timeout: LLM timeout
        invoke_fn: callable(prompt, model) -> str; if None, uses llm.invoke_model

    Returns:
        dict with title, body, beliefs, concept, module keys
    """
    beliefs = select_beliefs_for_lesson(lesson, nodes)
    if not beliefs:
        return None

    prompt = build_lesson_prompt(lesson, beliefs, domain=domain, completed=completed)

    if invoke_fn is None:
        from .llm import invoke_model
        result = invoke_model(prompt, model=model, timeout=timeout)
    else:
        result = invoke_fn(prompt, model)

    parsed = _parse_lesson_response(result)
    parsed["concept"] = lesson["concept"]
    parsed["module"] = lesson["module"]
    parsed["lesson_number"] = lesson["number"]
    return parsed


def _parse_lesson_response(content: str) -> dict:
    """Parse TITLE: ... body ... BELIEFS: ... format."""
    title = ""
    body = content
    beliefs = []

    lines = content.strip().split("\n")
    body_start = 0
    for i, line in enumerate(lines):
        if line.startswith("TITLE:"):
            title = line[len("TITLE:"):].strip()
            body_start = i + 1
            break

    body_end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].startswith("BELIEFS:"):
            beliefs_str = lines[i][len("BELIEFS:"):].strip()
            beliefs = [b.strip().strip("[]") for b in beliefs_str.split(",") if b.strip()]
            body_end = i
            break

    body = "\n".join(lines[body_start:body_end]).strip()

    return {"title": title, "body": body, "beliefs": beliefs}


def _identify_standalone_lessons(plan: dict) -> tuple[list[dict], list[dict]]:
    """Split lessons into connected (have prereqs/dependents/motivation) and standalone."""
    has_prereqs = {l["concept"] for l in plan["lessons"] if l["prerequisites"]}
    is_prereq_of = set()
    for l in plan["lessons"]:
        is_prereq_of.update(l["prerequisites"])
    has_motivation = {l["concept"] for l in plan["lessons"] if l["motivation"]}
    has_examples = {l["concept"] for l in plan["lessons"] if l["examples"]}
    has_crossrefs = {l["concept"] for l in plan["lessons"] if l["cross_references"]}
    mentioned_in_motivation = set()
    for l in plan["lessons"]:
        if l["motivation"]:
            for c in l["motivation"].replace("Unlocks: ", "").split(", "):
                mentioned_in_motivation.add(c.strip())
    mentioned_in_examples = set()
    for l in plan["lessons"]:
        for e in l["examples"]:
            mentioned_in_examples.add(e)
    mentioned_in_crossrefs = set()
    for l in plan["lessons"]:
        for x in l["cross_references"]:
            mentioned_in_crossrefs.add(x)

    connected_concepts = (has_prereqs | is_prereq_of | has_motivation |
                          has_examples | has_crossrefs |
                          mentioned_in_motivation | mentioned_in_examples |
                          mentioned_in_crossrefs)

    standalone = []
    connected = []
    for l in plan["lessons"]:
        if l["concept"] in connected_concepts:
            connected.append(l)
        else:
            standalone.append(l)

    return connected, standalone


def _build_consolidation_prompt(concepts: list[str], batch_num: int = 1) -> str:
    """Build prompt asking LLM to group standalone concepts into teaching units."""
    concept_list = "\n".join(f"- {c}" for c in concepts)
    return f"""You are a curriculum designer. Below are {len(concepts)} standalone concepts from a knowledge domain. Group them into coherent teaching units of 3-8 concepts each. Each unit should have a clear theme.

Concepts:
{concept_list}

Return your grouping as JSON — a list of objects, each with:
- "title": a short descriptive title for the teaching unit (2-5 words)
- "concepts": list of concept names from the input that belong together
- "rationale": one sentence explaining the grouping

Every concept must appear in exactly one group. If a concept doesn't fit any group, put it in a group called "Miscellaneous Topics".

Return ONLY the JSON array, no other text."""


def _parse_consolidation_response(content: str) -> list[dict]:
    """Parse LLM response into list of groups."""
    import json as _json
    import re
    match = re.search(r'\[.*\]', content, re.DOTALL)
    if not match:
        return []
    try:
        groups = _json.loads(match.group())
        for g in groups:
            if not isinstance(g.get("concepts"), list):
                g["concepts"] = []
            if not g.get("title"):
                g["title"] = "Untitled Group"
        return groups
    except (_json.JSONDecodeError, TypeError):
        return []


def consolidate_lessons(
    plan: dict,
    model: str = "claude",
    timeout: int = 300,
    batch_size: int = 80,
    invoke_fn=None,
) -> dict:
    """Consolidate granular lessons into broader teaching units using an LLM.

    Connected lessons (with prereqs/dependents/motivation) stay as-is.
    Standalone lessons are grouped by the LLM into thematic units.

    Returns a new plan with consolidated lessons.
    """
    connected, standalone = _identify_standalone_lessons(plan)

    if not standalone:
        return plan

    if invoke_fn is None:
        from .llm import invoke_model
        invoke_fn = lambda prompt, model: invoke_model(prompt, model=model, timeout=timeout)

    all_groups: list[dict] = []
    standalone_concepts = [l["concept"] for l in standalone]

    for i in range(0, len(standalone_concepts), batch_size):
        batch = standalone_concepts[i:i + batch_size]
        prompt = _build_consolidation_prompt(batch, batch_num=i // batch_size + 1)
        response = invoke_fn(prompt, model)
        groups = _parse_consolidation_response(response)
        if groups:
            all_groups.extend(groups)
        else:
            all_groups.append({
                "title": "Ungrouped",
                "concepts": batch,
                "rationale": "LLM grouping failed for this batch",
            })

    standalone_by_concept = {l["concept"]: l for l in standalone}

    consolidated_lessons = []
    lesson_num = 1

    for lesson in connected:
        consolidated_lessons.append({
            **lesson,
            "number": lesson_num,
            "subtopics": [],
        })
        lesson_num += 1

    for group in all_groups:
        subtopics = []
        for c in group["concepts"]:
            if c in standalone_by_concept:
                subtopics.append(standalone_by_concept[c])

        if not subtopics:
            continue

        consolidated_lessons.append({
            "number": lesson_num,
            "concept": group["title"].lower().replace(" ", "-"),
            "module": "consolidated",
            "prerequisites": [],
            "motivation": group.get("rationale"),
            "examples": [],
            "cross_references": [],
            "subtopics": [s["concept"] for s in subtopics],
            "title": group["title"],
        })
        lesson_num += 1

    modules: dict[str, list[dict]] = defaultdict(list)
    for l in consolidated_lessons:
        modules[l["module"]].append(l)

    stats = {
        "total_lessons": len(consolidated_lessons),
        "total_modules": len(modules),
        "total_relations": plan["stats"]["total_relations"],
        "concepts": plan["stats"]["concepts"],
        "relations_by_type": plan["stats"]["relations_by_type"],
        "consolidated_from": plan["stats"]["total_lessons"],
        "standalone_grouped": len(standalone),
        "groups_created": len(all_groups),
    }

    return {
        "lessons": consolidated_lessons,
        "modules": dict(modules),
        "stats": stats,
    }


def format_consolidated_markdown(plan: dict, title: Optional[str] = None) -> str:
    """Format a consolidated lesson plan as markdown."""
    lines = []
    title = title or "Consolidated Lesson Plan"
    lines.append(f"# {title}")
    lines.append("")

    stats = plan["stats"]
    summary = f"**{stats['total_lessons']} lessons**"
    if "consolidated_from" in stats:
        summary += f" (consolidated from {stats['consolidated_from']})"
    lines.append(summary)
    lines.append("")

    module_sizes = sorted(plan["modules"].items(),
                          key=lambda x: (-len(x[1]), x[0]))
    for module_name, module_lessons in module_sizes:
        display = module_name.replace("-", " ").title()
        lines.append(f"## {display}")
        lines.append(f"*{len(module_lessons)} lessons*")
        lines.append("")

        for lesson in module_lessons:
            concept_display = lesson.get("title", lesson["concept"].replace("-", " ").title())
            lines.append(f"### Lesson {lesson['number']}: {concept_display}")
            if lesson.get("prerequisites"):
                lines.append(f"- **Prerequisites**: {', '.join(lesson['prerequisites'])}")
            if lesson.get("motivation"):
                lines.append(f"- **Motivation**: {lesson['motivation']}")
            if lesson.get("subtopics"):
                lines.append(f"- **Covers**: {', '.join(lesson['subtopics'])}")
            if lesson.get("examples"):
                lines.append(f"- **Examples**: {', '.join(lesson['examples'])}")
            if lesson.get("cross_references"):
                lines.append(f"- **See also**: {', '.join(lesson['cross_references'])}")
            lines.append("")

    return "\n".join(lines)
