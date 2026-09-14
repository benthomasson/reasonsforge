"""Pseudo-tool-calling for derive — LLM queries beliefs via JSON blocks.

The LLM outputs JSON tool-call blocks alongside derivation proposals.
We parse them, execute against the belief network, and return results
in a follow-up prompt. This works across all backends without native
tool-calling support.

Flow:
  1. Seed prompt (sampled beliefs) + tool definitions
  2. Parse response for ### DERIVE proposals and JSON tool calls
  3. Execute tool calls, build follow-up with results
  4. Repeat until no tool calls or max rounds reached
  5. Return all collected proposals
"""

import json
import re
import sys

from . import api
from .derive import parse_proposals


TOOL_DEFINITIONS = """
## Available Tools

Before proposing derivations, you may request additional information by outputting \
tool calls as JSON blocks:

```json
{"tool": "get_belief", "args": {"id": "belief-id-here"}}
```

Available tools:

- **get_belief(id)**: Get full text, justifications, source, and truth value for a \
specific belief. Use this to read the full context of beliefs you want to combine.

- **search_beliefs(query)**: Search for beliefs matching keywords. Returns up to 10 \
matches with IDs and text. Use this to find supporting or contradicting beliefs.

- **get_dependents(id)**: Get beliefs that depend on this belief as an antecedent. \
Use this to understand how a belief is used in existing derivations.

You may output multiple tool calls and derivation proposals in the same response. \
If you output tool calls, results will be returned and you can continue exploring.
"""


FOLLOWUP_PROMPT = """\
## Tool Results

{tool_results}

{prior_proposals_section}
## Instructions

Continue exploring with more tool calls, or propose derivations.

Output format for derivations:

### DERIVE <belief-id-in-kebab-case>
<one-line claim text>
- Antecedents: <comma-separated list of existing belief IDs>
- Mode: ALL or ANY
- Label: <brief justification rationale>

### GATE <belief-id-in-kebab-case>
<one-line claim text>
- Antecedents: <comma-separated list of existing belief IDs>
- Unless: <comma-separated list of belief IDs that must be OUT>
- Mode: ALL or ANY
- Label: <brief justification rationale>

Tool call format:

```json
{{"tool": "tool_name", "args": {{"key": "value"}}}}
```

Available tools: get_belief(id), search_beliefs(query), get_dependents(id)
"""


def parse_tool_calls(response):
    """Extract tool-call JSON blocks from LLM response.

    Looks for JSON objects with a "tool" key, either inside fenced
    code blocks or as bare JSON on their own line.

    Returns list of {"tool": str, "args": dict}.
    """
    calls = []

    fenced = re.findall(r'```(?:json)?\s*\n(\{[^`]+?\})\s*\n```', response)
    bare = re.findall(r'^(\{[^\n]+\})$', response, re.MULTILINE)

    seen = set()
    for candidate in fenced + bare:
        candidate = candidate.strip()
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and "tool" in obj:
                args = obj.get("args", {})
                if not isinstance(args, dict):
                    args = {}
                calls.append({"tool": obj["tool"], "args": args})
        except (json.JSONDecodeError, ValueError):
            continue

    return calls


def execute_tool(tool_name, args, db_path):
    """Execute a tool call and return the result string."""
    dispatch = {
        "get_belief": _tool_get_belief,
        "search_beliefs": _tool_search_beliefs,
        "get_dependents": _tool_get_dependents,
    }
    fn = dispatch.get(tool_name)
    if fn is None:
        return f"Unknown tool: {tool_name}. Available: {', '.join(dispatch)}"
    return fn(args, db_path)


def _tool_get_belief(args, db_path):
    belief_id = args.get("id", "")
    if not belief_id:
        return "Error: 'id' argument is required"
    try:
        node = api.show_node(belief_id, db_path=db_path)
    except KeyError:
        return f"Belief '{belief_id}' not found"
    except Exception as e:
        return f"Error: {e}"

    lines = [
        f"**{node['id']}** [{node['truth_value']}]",
        node["text"],
        f"- Source: {node.get('source', 'unknown')}",
    ]
    for j in node.get("justifications", []):
        antes = ", ".join(j.get("antecedents", []))
        outlist = j.get("outlist", [])
        if antes:
            line = f"- Justified by: {antes}"
            if outlist:
                line += f" (unless: {', '.join(outlist)})"
            lines.append(line)
    deps = node.get("dependents", [])
    if deps:
        lines.append(f"- Dependents: {', '.join(deps[:10])}")
        if len(deps) > 10:
            lines.append(f"  ({len(deps) - 10} more)")
    return "\n".join(lines)


def _tool_search_beliefs(args, db_path):
    query = args.get("query", "")
    if not query:
        return "Error: 'query' argument is required"
    try:
        return api.search(query, db_path=db_path, format="compact")
    except Exception as e:
        return f"Error: {e}"


def _tool_get_dependents(args, db_path):
    belief_id = args.get("id", "")
    if not belief_id:
        return "Error: 'id' argument is required"
    try:
        node = api.show_node(belief_id, db_path=db_path)
    except KeyError:
        return f"Belief '{belief_id}' not found"
    except Exception as e:
        return f"Error: {e}"

    deps = node.get("dependents", [])
    if not deps:
        return f"No beliefs depend on `{belief_id}`"

    try:
        network = api.export_network(db_path=db_path)
        nodes = network.get("nodes", {})
        lines = []
        for dep_id in deps:
            dep_node = nodes.get(dep_id, {})
            text = dep_node.get("text", "")[:120]
            tv = dep_node.get("truth_value", "?")
            lines.append(f"[{tv}] {dep_id} — {text}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


def _format_tool_results(calls, results):
    """Format tool results for the follow-up prompt."""
    parts = []
    for call, result in zip(calls, results):
        args_str = ", ".join(f'{k}="{v}"' for k, v in call["args"].items())
        parts.append(f"### {call['tool']}({args_str})\n\n{result}")
    return "\n\n".join(parts)


def derive_with_tools(seed_prompt, model, db_path, max_rounds=3, timeout=600):
    """Run derive with pseudo-tool-calling rounds.

    Args:
        seed_prompt: Initial derive prompt (from build_prompt).
        model: Model name for invoke_sync.
        db_path: Path to reasons database.
        max_rounds: Max tool-call rounds after seed (default: 3).
        timeout: LLM timeout in seconds.

    Returns:
        list of proposal dicts (same format as parse_proposals).
    """
    from .forge.llm import invoke_sync

    all_proposals = {}
    prompt = seed_prompt + TOOL_DEFINITIONS

    for round_num in range(1, max_rounds + 2):
        label = "Seed" if round_num == 1 else f"Search {round_num - 1}/{max_rounds}"
        print(f"  {label} round...", file=sys.stderr)

        response = invoke_sync(prompt, model=model, timeout=timeout)

        proposals = parse_proposals(response)
        if proposals:
            for p in proposals:
                all_proposals[p["id"]] = p
            print(f"    {len(proposals)} proposal(s)", file=sys.stderr)

        if round_num > max_rounds:
            break

        calls = parse_tool_calls(response)
        if not calls:
            print(f"    No tool calls — done", file=sys.stderr)
            break

        print(f"    {len(calls)} tool call(s): "
              f"{', '.join(c['tool'] for c in calls)}", file=sys.stderr)

        results = []
        for call in calls:
            result = execute_tool(call["tool"], call["args"], db_path)
            results.append(result)

        tool_results = _format_tool_results(calls, results)

        prior_section = ""
        if all_proposals:
            lines = [f"- `{p['id']}`: {p['text'][:100]}"
                     for p in all_proposals.values()]
            prior_section = (
                "## Prior Proposals\n\n"
                "You have already proposed:\n"
                + "\n".join(lines)
                + "\n\nYou may revise these (reuse the same ID) or add new ones.\n\n"
            )

        prompt = FOLLOWUP_PROMPT.format(
            tool_results=tool_results,
            prior_proposals_section=prior_section,
        )

    return list(all_proposals.values())
