"""Build a static wiki from the belief network.

Exports beliefs as interlinked markdown pages grouped by topic
(word-frequency) or semantic cluster. Optionally uses an LLM to
synthesize each topic page into a coherent narrative.
"""

import hashlib
import json
import os
import re
import sys


_TOPIC_STOP_WORDS = {
    "the", "is", "in", "to", "of", "and", "or", "not", "as", "by",
    "via", "can", "with", "from", "than", "that", "this", "be", "has",
    "have", "it", "its", "no", "do", "if", "so", "up", "out", "all",
    "but", "get", "set", "only", "per", "use", "may", "one", "two",
    "new", "any", "each", "must", "when", "how", "also", "into",
    "over", "more", "both", "same", "own", "used", "using", "based",
    "does", "then", "for",
}


def _assign_topics(node_ids, topics):
    """Assign each node to its best-matching topic based on ID segments.

    Returns {topic_label: [node_id, ...], ...} with "Other" for unmatched.
    """
    topic_set = {t["topic"] for t in topics}
    groups = {t["topic"]: [] for t in topics}
    groups["Other"] = []

    for nid in node_ids:
        words = [w for w in re.split(r'[-._:]', nid) if w and len(w) > 2]
        matched = False
        for word in words:
            if word in topic_set:
                groups[word].append(nid)
                matched = True
                break
        if not matched:
            groups["Other"].append(nid)

    return {k: v for k, v in groups.items() if v}


def _assign_topics_multi_word(node_ids, topics, node_details=None):
    """Assign each node to its best-matching topic using multi-word matching.

    Topics can be multi-word phrases with optional aliases. Matching
    checks both the node ID (segments split on [-._:]) and the node
    text. Any single alias matching is enough. Longer match patterns
    take priority over shorter ones.

    Returns {topic_label: [node_id, ...], ...} with "Other" for unmatched.
    """
    topic_matchers = []
    for t in topics:
        label = t["topic"]
        patterns = [label] + t.get("aliases", [])
        for pattern in patterns:
            words = [w.lower() for w in re.split(r'[\s-]+', pattern) if w]
            topic_matchers.append((label, words))
    topic_matchers.sort(key=lambda t: -len(t[1]))

    groups = {t["topic"]: [] for t in topics}
    groups["Other"] = []

    for nid in node_ids:
        id_words = set(w.lower() for w in re.split(r'[-._:]', nid) if w)
        text_words = set()
        if node_details and nid in node_details:
            text = node_details[nid].get("text", "")
            text_words = set(w.lower() for w in re.split(r'[\s-]+', text) if len(w) > 2)

        searchable = id_words | text_words
        matched = False
        for label, kws in topic_matchers:
            if all(kw in searchable for kw in kws):
                groups[label].append(nid)
                matched = True
                break
        if not matched:
            groups["Other"].append(nid)

    return {k: v for k, v in groups.items() if v}


def load_topics_file(path):
    """Load topics from a file (one topic per line).

    Lines starting with # are comments. Blank lines are skipped.
    If a line contains a tab or pipe, the first field is used and
    the rest is ignored (allows for descriptions).

    Comma-separated entries on a line define aliases: the first entry
    is the display label, additional entries are alternative match
    patterns.  Example::

        sparse autoencoder, sae
        in-context learning, icl
        knowledge editing, rome, memit

    Returns list of {"topic": str, "aliases": [str, ...]} dicts.
    """
    topics = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("\t", "|"):
                if sep in line:
                    line = line.split(sep, 1)[0].strip()
                    break
            if not line:
                continue
            parts = [p.strip() for p in line.split(",") if p.strip()]
            label = parts[0]
            aliases = parts[1:] if len(parts) > 1 else []
            topics.append({"topic": label, "aliases": aliases})
    return topics


def _compute_belief_hash(node_details):
    """Compute a hash over belief IDs and text, matching eem-wiki's convention."""
    items = sorted(
        (nid, (detail.get("text") or ""))
        for nid, detail in node_details.items()
    )
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()


def save_topic_cache(output_dir, node_details, groups):
    """Write .topic_cache.json compatible with eem-wiki."""
    belief_hash = _compute_belief_hash(node_details)
    path = os.path.join(output_dir, ".topic_cache.json")
    with open(path, "w") as f:
        json.dump({"hash": belief_hash, "topics": groups}, f, indent=2)


TOPIC_SUMMARY_PROMPT = """\
You are summarizing a group of beliefs from a Truth Maintenance System (TMS) \
knowledge base. These beliefs are grouped under the topic "{topic}".

Write a 2-4 paragraph summary that:
1. Explains what this topic covers and why it matters
2. Highlights the key claims and how they relate to each other
3. Notes any important distinctions (e.g., which beliefs are premises vs derived)
4. Mentions if any beliefs are OUT (retracted) and what that implies

Write in clear, direct prose. Do not list the beliefs — synthesize them into \
a coherent narrative. Reference specific belief IDs inline in parentheses \
where relevant, e.g. (belief-id-here).

Output plain text paragraphs only. Do not use markdown formatting — no headers, \
no bullet lists, no bold/italic. Separate paragraphs with blank lines.

## Beliefs in this topic

{beliefs}"""

BELIEF_SUMMARY_PROMPT = """\
You are writing a plain-language summary for a belief in a Truth Maintenance System.

The belief is a formal claim that may be dense or technical. Write 1-2 sentences \
that explain what this belief means in plain language. Focus on the "so what" — \
why does this matter? What does it imply for the system?

Do not repeat the belief text verbatim. Do not use the word "belief". Just explain it.

Output plain text only. No markdown formatting — no bold, italic, headers, or lists.

Belief ID: {node_id}
Status: {truth_value}
Type: {node_type}

Claim: {text}

{context}"""

PROJECT_SUMMARY_PROMPT = """\
You are writing an overview summary for a belief network wiki called "{project_name}".

This knowledge base contains {total_beliefs} beliefs ({in_count} IN, {out_count} OUT) \
organized into {topic_count} topics by a Truth Maintenance System (TMS).

Write a 3-5 paragraph summary that:
1. Explains what this knowledge base is about — what domain does it cover?
2. Highlights the major themes and what the network has discovered
3. Notes the scale and structure — how many topics, what kinds of beliefs
4. Mentions what OUT (retracted) beliefs tell us about how understanding has evolved
5. Gives a reader a sense of why this knowledge base is valuable

Write in clear, direct prose for someone encountering this wiki for the first time. \
Do not list every topic — synthesize the big picture.

Output plain text paragraphs only. Do not use markdown formatting — no headers, \
no bullet lists, no bold/italic. Separate paragraphs with blank lines.

## Topics and their sizes

{topic_list}

## Sample beliefs (for flavor)

{sample_beliefs}"""


def generate_topic_summary(topic, nids, node_details, model, timeout):
    """Generate a plain-text topic summary compatible with eem-wiki."""
    from .llm import invoke_model

    lines = []
    for nid in sorted(nids):
        detail = node_details.get(nid)
        if not detail:
            continue
        status = detail.get("truth_value", "?")
        lines.append(f"- [{status}] {nid}: {detail.get('text', '')}")

    prompt = TOPIC_SUMMARY_PROMPT.format(
        topic=topic, beliefs="\n".join(lines))
    return invoke_model(prompt, model=model, timeout=timeout)


def generate_belief_summary(nid, node_detail, node_details, model, timeout):
    """Generate a plain-text belief summary compatible with eem-wiki."""
    from .llm import invoke_model

    is_premise = not node_detail.get("justifications")
    node_type = "premise (direct observation)" if is_premise else "derived belief"

    context_lines = []
    for j in node_detail.get("justifications", []):
        for ant_id in j.get("antecedents", []):
            ant = node_details.get(ant_id, {})
            context_lines.append(f"Antecedent [{ant_id}]: {ant.get('text', '')}")

    context = "\n".join(context_lines) if context_lines else "No antecedents (premise)."

    prompt = BELIEF_SUMMARY_PROMPT.format(
        node_id=nid,
        truth_value=node_detail.get("truth_value", "?"),
        node_type=node_type,
        text=node_detail.get("text", ""),
        context=context,
    )
    return invoke_model(prompt, model=model, timeout=timeout)


def generate_project_summary(project_name, node_details, groups, model, timeout):
    """Generate a plain-text project summary compatible with eem-wiki."""
    from .llm import invoke_model
    import random

    in_count = sum(1 for d in node_details.values() if d.get("truth_value") == "IN")
    out_count = len(node_details) - in_count

    topic_lines = []
    for topic, nids in sorted(groups.items(), key=lambda x: -len(x[1])):
        topic_lines.append(f"- {topic} ({len(nids)} beliefs)")

    rng = random.Random(42)
    all_ids = list(node_details.keys())
    samples = []
    for nid in rng.sample(all_ids, min(20, len(all_ids))):
        detail = node_details[nid]
        tv = detail.get("truth_value", "?")
        samples.append(f"- [{tv}] {nid}: {detail.get('text', '')[:150]}")

    prompt = PROJECT_SUMMARY_PROMPT.format(
        project_name=project_name,
        total_beliefs=len(node_details),
        in_count=in_count,
        out_count=out_count,
        topic_count=len(groups),
        topic_list="\n".join(topic_lines),
        sample_beliefs="\n".join(samples),
    )
    return invoke_model(prompt, model=model, timeout=timeout)


def load_summaries(summaries_dir):
    """Load cached summaries from a directory (eem-wiki compatible format)."""
    topic_sums = {}
    belief_sums = {}
    project_sum = ""
    if not summaries_dir or not os.path.isdir(summaries_dir):
        return topic_sums, belief_sums, project_sum

    topic_path = os.path.join(summaries_dir, "topic-summaries.json")
    belief_path = os.path.join(summaries_dir, "belief-summaries.json")
    project_path = os.path.join(summaries_dir, "project-summary.txt")

    if os.path.exists(topic_path):
        try:
            with open(topic_path) as f:
                topic_sums = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    if os.path.exists(belief_path):
        try:
            with open(belief_path) as f:
                belief_sums = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    if os.path.exists(project_path):
        try:
            with open(project_path) as f:
                project_sum = f.read().strip()
        except OSError:
            pass
    return topic_sums, belief_sums, project_sum


def save_summaries(summaries_dir, topic_sums, belief_sums, project_sum=""):
    """Write summaries in eem-wiki compatible format."""
    os.makedirs(summaries_dir, exist_ok=True)
    if topic_sums:
        with open(os.path.join(summaries_dir, "topic-summaries.json"), "w") as f:
            json.dump(topic_sums, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
    if belief_sums:
        with open(os.path.join(summaries_dir, "belief-summaries.json"), "w") as f:
            json.dump(belief_sums, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
    if project_sum:
        with open(os.path.join(summaries_dir, "project-summary.txt"), "w") as f:
            f.write(project_sum + "\n")


def _page_name(label):
    """Sanitize a topic/cluster label to a valid markdown filename."""
    safe = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')
    return safe or "other"


def _format_node(node_id, node_detail, node_to_page, all_details=None):
    """Render one node as markdown with cross-reference links."""
    lines = []
    lines.append(f"### {node_id}")
    lines.append(f"**Status:** {node_detail['truth_value']}")
    lines.append("")

    # Render duplicate-of or superseded-by relationships
    metadata = node_detail.get("metadata") or {}
    if "duplicate_of" in metadata:
        canonical_id = metadata["duplicate_of"]
        page = node_to_page.get(canonical_id)
        if page:
            link = f"[{canonical_id}]({page}#{canonical_id})"
        else:
            link = canonical_id
        lines.append(f"**Duplicate of:** {link}")
        lines.append("")

    if "superseded_by" in metadata:
        new_id = metadata["superseded_by"]
        page = node_to_page.get(new_id)
        if page:
            link = f"[{new_id}]({page}#{new_id})"
        else:
            link = new_id
        lines.append(f"**Superseded by:** {link}")
        lines.append("")

    # Render defeaters from outlist
    if all_details:
        defeaters = []
        for j in node_detail.get("justifications", []):
            for o in j.get("outlist", []):
                o_detail = all_details.get(o)
                if o_detail:
                    o_meta = o_detail.get("metadata") or {}
                    if o_meta.get("defeats_node") == node_id:
                        defeaters.append((o, o_meta, o_detail.get("text", "")))
        if defeaters:
            for d_id, d_meta, d_text in defeaters:
                d_type = d_meta.get("defeater_type", "defeater")
                r_type = d_meta.get("defeat_reason_type", "")
                label = f"{d_type}, {r_type}" if r_type else d_type
                page = node_to_page.get(d_id)
                if page:
                    link = f"[{d_id}]({page}#{d_id})"
                else:
                    link = d_id
                lines.append(f"**Defeated by:** {link} ({label})")
            lines.append("")

    lines.append(node_detail["text"])
    lines.append("")

    justifications = node_detail.get("justifications", [])
    si = node_detail.get("supporting_justification")
    if si is not None and 0 <= si < len(justifications):
        designated = justifications[si]
        antecedents = set(designated.get("antecedents", []))
        other_antecedents = set()
        for idx, j in enumerate(justifications):
            if idx != si:
                for a in j.get("antecedents", []):
                    if a not in antecedents:
                        other_antecedents.add(a)
    else:
        antecedents = set()
        other_antecedents = set()
        for j in justifications:
            for a in j.get("antecedents", []):
                antecedents.add(a)

    if antecedents:
        links = []
        for a in sorted(antecedents):
            page = node_to_page.get(a)
            if page:
                links.append(f"[{a}]({page}#{a})")
            else:
                links.append(a)
        label = "**Depends on (active):**" if other_antecedents else "**Depends on:**"
        lines.append(f"{label} {', '.join(links)}")
    if other_antecedents:
        links = []
        for a in sorted(other_antecedents):
            page = node_to_page.get(a)
            if page:
                links.append(f"[{a}]({page}#{a})")
            else:
                links.append(a)
        lines.append(f"**Depends on (other):** {', '.join(links)}")

    dependents = node_detail.get("dependents", [])
    if dependents:
        links = []
        for d in sorted(dependents):
            page = node_to_page.get(d)
            if page:
                links.append(f"[{d}]({page}#{d})")
            else:
                links.append(d)
        lines.append(f"**Supports:** {', '.join(links)}")

    lines.append("")
    return "\n".join(lines)


WIKI_PAGE_PROMPT = """\
You are writing a wiki page about "{topic}" for a knowledge base built from \
a belief network (Truth Maintenance System). The page should be a coherent, \
readable narrative that synthesizes the beliefs below into an informative article.

## Guidelines

- Write in clear, encyclopedic prose — not a list of beliefs
- Use markdown headers (##, ###) to organize sections
- Start with a brief overview paragraph
- Group related beliefs into thematic sections
- Mention the status (IN = currently held, OUT = retracted) only when relevant
- Include belief IDs in parentheses after key claims so readers can trace sources, \
  e.g. "The system uses SL justifications (sl-justification-mechanism)."
- Note important dependency relationships between beliefs
- If some beliefs contradict or qualify others, explain the nuance
- Do NOT include a title — the page already has one
- Keep the page concise but comprehensive

## Beliefs

{beliefs}

Write the wiki page content now.
"""


def _format_beliefs_for_prompt(node_ids, node_details):
    """Format beliefs into a structured text block for the LLM prompt."""
    lines = []
    for nid in sorted(node_ids):
        detail = node_details.get(nid)
        if not detail:
            continue
        lines.append(f"### {nid}")
        lines.append(f"Status: {detail['truth_value']}")
        lines.append(f"Text: {detail['text']}")

        justifications = detail.get("justifications", [])
        si = detail.get("supporting_justification")
        if si is not None and 0 <= si < len(justifications):
            antecedents = set(justifications[si].get("antecedents", []))
        else:
            antecedents = set()
            for j in justifications:
                for a in j.get("antecedents", []):
                    antecedents.add(a)
        if antecedents:
            lines.append(f"Depends on: {', '.join(sorted(antecedents))}")

        dependents = detail.get("dependents", [])
        if dependents:
            lines.append(f"Supports: {', '.join(sorted(dependents))}")
        lines.append("")
    return "\n".join(lines)


def generate_wiki_page(topic, node_ids, node_details, model, timeout):
    """Generate a wiki page for a topic group using an LLM."""
    from .llm import invoke_model

    beliefs_text = _format_beliefs_for_prompt(node_ids, node_details)
    prompt = WIKI_PAGE_PROMPT.format(topic=topic, beliefs=beliefs_text)
    return invoke_model(prompt, model=model, timeout=timeout)


def _linkify(content, current_page, node_to_page, all_ids):
    """Replace cross-page belief IDs with markdown links."""
    for nid in sorted(all_ids, key=len, reverse=True):
        target = node_to_page.get(nid)
        if not target or target == current_page:
            continue
        if nid not in content:
            continue
        if "[" + nid + "](" in content:
            continue
        link = "[" + nid + "](" + target + "#" + nid + ")"
        pattern = r'(?<![a-z0-9\-])' + re.escape(nid) + r'(?![a-z0-9\-])'
        content = re.sub(pattern, link, content)
    return content


_RESERVED_SLUGS = {"index"}


def build_wiki(node_details, groups, output_dir, model="", timeout=300,
               parallel=0, summaries_dir="", skip_belief_summaries=False):
    """Write index.md and per-group pages to output_dir.

    Args:
        node_details: {node_id: show_node dict}
        groups: {group_label: [node_id, ...]}
        output_dir: directory to write markdown files into
        model: LLM model for page generation (empty = no LLM)
        timeout: LLM timeout in seconds
        parallel: number of concurrent LLM workers (0 = sequential)
        summaries_dir: if set, write eem-wiki compatible summary files
        skip_belief_summaries: skip per-belief summaries (expensive)
    """
    os.makedirs(output_dir, exist_ok=True)

    used_slugs: dict[str, str] = {}
    label_to_file: dict[str, str] = {}
    for label in groups:
        slug = _page_name(label)
        if slug in _RESERVED_SLUGS:
            slug = f"{slug}-topic"
        while slug in used_slugs:
            slug = f"{slug}-2"
        used_slugs[slug] = label
        label_to_file[label] = slug + ".md"

    node_to_page = {}
    for label, nids in groups.items():
        page_file = label_to_file[label]
        for nid in nids:
            node_to_page[nid] = page_file

    index_lines = ["# Belief Wiki", ""]
    index_lines.append("| Topic | Beliefs |")
    index_lines.append("|-------|---------|")
    for label in sorted(groups, key=lambda l: (-len(groups[l]), l)):
        page_file = label_to_file[label]
        count = len(groups[label])
        index_lines.append(f"| [{label}]({page_file}) | {count} |")
    index_lines.append("")

    total = sum(len(nids) for nids in groups.values())
    index_lines.append(f"*{total} beliefs across {len(groups)} pages*")
    index_lines.append("")

    with open(os.path.join(output_dir, "index.md"), "w") as f:
        f.write("\n".join(index_lines))

    total_groups = len(groups)
    generated_content: dict[str, str] = {}

    if model and parallel > 0:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _gen(label, nids):
            return label, generate_wiki_page(label, nids, node_details,
                                             model, timeout)

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {
                executor.submit(_gen, label, nids): label
                for label, nids in groups.items()
            }
            done = 0
            for future in as_completed(futures):
                label = futures[future]
                done += 1
                try:
                    _, content = future.result()
                    generated_content[label] = content
                    print(f"  Generated {label} ({done}/{total_groups})",
                          file=sys.stderr)
                except Exception as e:
                    print(f"  WARN: {label} failed: {e} ({done}/{total_groups})",
                          file=sys.stderr)
    elif model:
        for i, (label, nids) in enumerate(groups.items(), 1):
            print(f"  Generating {label} ({i}/{total_groups})...",
                  file=sys.stderr)
            try:
                generated_content[label] = generate_wiki_page(
                    label, nids, node_details, model, timeout)
            except Exception as e:
                print(f"  WARN: {label} failed: {e}", file=sys.stderr)

    for label, nids in groups.items():
        page_file = label_to_file[label]
        page_lines = [f"# {label}", ""]
        page_lines.append(f"[Back to index](index.md)")
        page_lines.append("")
        if label in generated_content:
            page_lines.append(generated_content[label])
            page_lines.append("")
        else:
            for nid in sorted(nids):
                detail = node_details.get(nid)
                if detail:
                    page_lines.append(_format_node(nid, detail, node_to_page, all_details=node_details))
        page_text = "\n".join(page_lines)
        page_text = _linkify(page_text, page_file, node_to_page,
                             node_details.keys())
        with open(os.path.join(output_dir, page_file), "w") as f:
            f.write(page_text)

    save_topic_cache(output_dir, node_details, groups)

    if summaries_dir and model:
        saved_topic, saved_belief, saved_project = load_summaries(summaries_dir)

        # Topic summaries — skip already-cached, save after each
        topic_sums = dict(saved_topic)
        needed_topics = [(t, nids) for t, nids in groups.items()
                         if t not in topic_sums]
        if needed_topics:
            print(f"  Generating {len(needed_topics)} topic summaries "
                  f"({len(topic_sums)} cached)...", file=sys.stderr)
            if parallel > 0:
                from concurrent.futures import ThreadPoolExecutor, as_completed

                def _gen_topic(t, nids):
                    return t, generate_topic_summary(t, nids, node_details,
                                                     model, timeout)

                with ThreadPoolExecutor(max_workers=parallel) as executor:
                    futures = {
                        executor.submit(_gen_topic, t, n): t
                        for t, n in needed_topics
                    }
                    for future in as_completed(futures):
                        t = futures[future]
                        try:
                            _, text = future.result()
                            topic_sums[t] = text
                            save_summaries(summaries_dir, topic_sums, {}, "")
                        except Exception as e:
                            print(f"  WARN: topic summary '{t}' failed: {e}",
                                  file=sys.stderr)
            else:
                for i, (t, nids) in enumerate(needed_topics, 1):
                    try:
                        topic_sums[t] = generate_topic_summary(
                            t, nids, node_details, model, timeout)
                        save_summaries(summaries_dir, topic_sums, {}, "")
                        print(f"  Topic summary {i}/{len(needed_topics)}: {t}",
                              file=sys.stderr)
                    except Exception as e:
                        print(f"  WARN: topic summary '{t}' failed: {e}",
                              file=sys.stderr)

        # Belief summaries — skip already-cached, save every 10
        belief_sums = dict(saved_belief)
        if not skip_belief_summaries:
            needed_beliefs = [(nid, d) for nid, d in node_details.items()
                              if nid not in belief_sums]
            if needed_beliefs:
                print(f"  Generating {len(needed_beliefs)} belief summaries "
                      f"({len(belief_sums)} cached)...", file=sys.stderr)
                if parallel > 0:
                    from concurrent.futures import ThreadPoolExecutor, as_completed

                    def _gen_belief(nid, detail):
                        return nid, generate_belief_summary(
                            nid, detail, node_details, model, timeout)

                    with ThreadPoolExecutor(max_workers=parallel) as executor:
                        futures = {
                            executor.submit(_gen_belief, n, d): n
                            for n, d in needed_beliefs
                        }
                        done_count = 0
                        unsaved_count = 0
                        for future in as_completed(futures):
                            nid = futures[future]
                            done_count += 1
                            try:
                                _, text = future.result()
                                belief_sums[nid] = text
                                unsaved_count += 1
                                if unsaved_count >= 10:
                                    save_summaries(summaries_dir, topic_sums,
                                                   belief_sums, "")
                                    unsaved_count = 0
                                if done_count % 50 == 0:
                                    print(f"  {done_count}/{len(needed_beliefs)}"
                                          " belief summaries",
                                          file=sys.stderr)
                            except Exception as e:
                                print(f"  WARN: belief summary '{nid}' "
                                      f"failed: {e}", file=sys.stderr)
                else:
                    unsaved_count = 0
                    for i, (nid, detail) in enumerate(needed_beliefs, 1):
                        try:
                            belief_sums[nid] = generate_belief_summary(
                                nid, detail, node_details, model, timeout)
                            unsaved_count += 1
                            if unsaved_count >= 10:
                                save_summaries(summaries_dir, topic_sums,
                                               belief_sums, "")
                                unsaved_count = 0
                            if i % 50 == 0:
                                print(f"  {i}/{len(needed_beliefs)} "
                                      "belief summaries", file=sys.stderr)
                        except Exception as e:
                            print(f"  WARN: belief summary '{nid}' "
                                  f"failed: {e}", file=sys.stderr)

        # Project summary
        project_sum = saved_project
        if not project_sum:
            print("  Generating project summary...", file=sys.stderr)
            try:
                project_sum = generate_project_summary(
                    "Belief Wiki", node_details, groups, model, timeout)
            except Exception as e:
                print(f"  WARN: project summary failed: {e}", file=sys.stderr)

        save_summaries(summaries_dir, topic_sums, belief_sums, project_sum)

    return {"output_dir": output_dir, "pages": len(groups), "total_nodes": total}
