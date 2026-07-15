"""Code forge commands — analyze git repos and extract architectural beliefs.

Each function takes an argparse Namespace and uses reasonsforge.api directly
(no subprocess calls to the reasons CLI).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from . import PROJECT_DIR
from .git_utils import (
    commits_since_checkpoint,
    extract_symbol,
    find_related_tests,
    get_commit_log,
    get_diff,
    get_diff_since,
    get_diff_since_commit,
    get_file_content,
    get_imports,
    get_repo_structure,
    list_commits_with_files,
    list_source_files,
    load_diff_checkpoint,
    save_diff_checkpoint,
)
from .language import PYTHON, detect_language
from .observations import parse_observation_requests, run_observations
from .prompts import (
    PROPOSE_BELIEFS_CODE,
    REVIEW_PROMPT,
    VERIFY_INFER_FILE_PROMPT,
    VERIFY_OBSERVE_PROMPT,
    VERIFY_PROMPT,
    build_diff_prompt,
    build_diff_summary_prompt,
    build_file_prompt,
    build_function_prompt,
    build_observe_prompt,
    build_repo_prompt,
    build_scan_prompt,
)
from .topics import (
    Topic,
    add_topics,
    load_queue,
    parse_topics_from_response,
    pending_count,
    pop_batch,
    pop_next,
)

REASONS_DB = "reasons.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config() -> dict | None:
    config_path = Path.cwd() / PROJECT_DIR / "config.json"
    if config_path.is_file():
        return json.loads(config_path.read_text())
    return None


def _save_config(config: dict) -> None:
    config_dir = Path.cwd() / PROJECT_DIR
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.json").write_text(json.dumps(config, indent=2))


def _get_repo(args) -> str:
    return os.path.abspath(getattr(args, "repo", None) or os.getcwd())


def _get_project_dir(args) -> str:
    return os.path.join(_get_repo(args), PROJECT_DIR)


def _get_lang(args, repo_path=None):
    if not hasattr(args, "_cached_lang"):
        args._cached_lang = detect_language(repo_path or _get_repo(args))
    return args._cached_lang


def _sanitize_path_for_filename(path: str) -> str:
    name = path.replace("/", "-").replace("\\", "-")
    if "." in name:
        name = name.rsplit(".", 1)[0]
    name = name.lstrip("-")
    return name[:80] if name else "unknown"


def _create_entry(topic: str, title: str, content: str) -> Path | None:
    """Write an entry file directly (replaces subprocess call to entry CLI)."""
    today = date.today()
    summary_dir = Path("summaries") / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}"
    summary_dir.mkdir(parents=True, exist_ok=True)
    entry_path = summary_dir / f"{topic}.md"
    entry_path.write_text(f"# {title}\n\n{content}\n")
    print(f"Entry: {entry_path}", file=sys.stderr)
    return entry_path


def _enqueue_topics(response: str, source: str, project_dir: str | None = None) -> None:
    new_topics = parse_topics_from_response(response, source=source)
    if new_topics:
        added = add_topics(new_topics, project_dir)
        if added:
            total = pending_count(project_dir)
            print(f"Queued {added} new topic(s) ({total} pending)", file=sys.stderr)


def _parse_beliefs_from_response(response: str) -> list[dict]:
    section_match = re.search(
        r"#+\s*Beliefs?\s*\n(.*?)(?=\n#|\Z)",
        response, re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return []
    beliefs = []
    pattern = re.compile(r"^[-*]\s+`([^`]+)`\s*(?:—|-|:)\s*(.+)$", re.MULTILINE)
    for match in pattern.finditer(section_match.group(1)):
        beliefs.append({"id": match.group(1), "text": match.group(2).strip()})
    return beliefs


def _report_beliefs(response: str) -> None:
    beliefs = _parse_beliefs_from_response(response)
    if beliefs:
        print(f"Surfaced {len(beliefs)} belief(s):", file=sys.stderr)
        for b in beliefs[:5]:
            print(f"  {b['id']}: {b['text'][:80]}", file=sys.stderr)


def _find_project_config(repo_path: str) -> tuple[str | None, str | None]:
    config_files = [
        "pyproject.toml", "package.json", "Cargo.toml",
        "go.mod", "pom.xml", "build.gradle", "Makefile",
    ]
    for config in config_files:
        path = os.path.join(repo_path, config)
        content = get_file_content(path)
        if content is not None:
            return config, content
    return None, None


def _find_entry_points(repo_path: str, config_content: str | None, lang=None) -> list[str]:
    lang = lang or PYTHON
    entry_points = []
    for candidate in lang.entry_point_candidates:
        if os.path.isfile(os.path.join(repo_path, candidate)):
            entry_points.append(candidate)

    marker = lang.config_entry_point_marker
    if config_content and marker and marker in config_content:
        in_section = False
        for line in config_content.split("\n"):
            if marker in line:
                in_section = True
                continue
            if in_section:
                if line.startswith("["):
                    in_section = False
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    if key == "path" and value:
                        entry_points.append(value)
                    elif key != "path" and key != "name":
                        entry_points.append(line.strip())
    return entry_points


def _repo_path_to_entry_pattern(repo_path: str, lang=None) -> str:
    lang = lang or PYTHON
    ext = lang.primary_extension
    if repo_path.endswith(ext):
        repo_path = repo_path[:-len(ext)]
    return repo_path.replace("/", "-").replace("\\", "-")


def _extract_source_file(entry_path: str, project_dir: str | None = None) -> str | None:
    base = project_dir or "."
    full_path = Path(base) / entry_path
    if not full_path.is_file():
        return None
    try:
        for line in full_path.read_text().splitlines()[:5]:
            if line.startswith("# File: "):
                return line[8:].strip()
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def cmd_init(args):
    """Bootstrap a code forge knowledge base for a codebase."""
    from reasonsforge.api import add_node, init_db

    repo_path = _get_repo(args)
    repo_name = os.path.basename(repo_path)
    domain = getattr(args, "domain", None) or repo_name
    db_path = getattr(args, "output", REASONS_DB)

    project_dir = Path.cwd() / PROJECT_DIR
    project_dir.mkdir(parents=True, exist_ok=True)

    _save_config({
        "repo_path": repo_path,
        "domain": domain,
        "created": date.today().isoformat(),
    })

    Path("summaries").mkdir(exist_ok=True)

    if not Path(db_path).exists():
        init_db(db_path=db_path)
        print("Initialized reasons database")
    else:
        print(f"{db_path} already exists, skipping init")

    # Register repo as a premise node
    try:
        add_node(
            f"repo-{repo_name}",
            f"Repository: {repo_name} at {repo_path}",
            source=f"init:{repo_name}",
            db_path=db_path,
        )
    except Exception:
        pass

    gitignore = Path.cwd() / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("reasons.db\nrag_fts.db\n")
        print("Created .gitignore")

    print(f"\nCode forge initialized: {repo_name}")
    print(f"  Repo: {repo_path}")
    print(f"  Domain: {domain}")
    print(f"\nNext: reasonsforge code scan")


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def cmd_scan(args):
    """Scan a repo to identify key files and populate the exploration queue."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    repo_path = _get_repo(args)
    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    print(f"Scanning {repo_path}...", file=sys.stderr)

    lang = _get_lang(args, repo_path)
    tree = get_repo_structure(repo_path, max_depth=3)
    _, config_content = _find_project_config(repo_path)
    readme_content = get_file_content(os.path.join(repo_path, "README.md"))
    entry_points = _find_entry_points(repo_path, config_content, lang=lang)

    prompt = build_scan_prompt(
        tree=tree,
        config_content=config_content,
        readme_content=readme_content,
        entry_points=entry_points or None,
    )

    print(f"Running {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    repo_name = os.path.basename(repo_path)
    _create_entry(f"scan-{repo_name}", f"Scan: {repo_name}", result)

    project_dir = _get_project_dir(args)

    source_files = list_source_files(repo_path)
    if source_files:
        file_topics = [
            Topic(title=f, kind="file", target=f, source=f"scan:{repo_name}")
            for f in source_files
        ]
        added = add_topics(file_topics, project_dir)
        print(f"Queued {added} file(s) for exploration", file=sys.stderr)

    _enqueue_topics(result, source=f"scan:{repo_name}", project_dir=project_dir)
    print(result)


# ---------------------------------------------------------------------------
# explore
# ---------------------------------------------------------------------------


def _prepare_file_topic(topic, repo_path, lang=None):
    lang = lang or PYTHON
    file_path = topic.target
    abs_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path

    if os.path.isdir(abs_path):
        topic.kind = "repo"
        return _prepare_repo_topic(topic, repo_path, lang=lang)

    if not os.path.isfile(abs_path):
        print(f"File not found: {file_path} (skipping)", file=sys.stderr)
        return None

    content = get_file_content(abs_path)
    if content is None:
        print(f"Cannot read file: {file_path}", file=sys.stderr)
        return None

    rel_path = os.path.relpath(abs_path, repo_path)
    import_info = get_imports(abs_path, repo_path, lang=lang)
    repo_tree = get_repo_structure(repo_path, max_depth=2)

    prompt = build_file_prompt(
        file_path=rel_path,
        file_content=content,
        imports=import_info["imports"] or None,
        imported_by=import_info["imported_by"] or None,
        repo_context=repo_tree,
    )

    entry_name = _sanitize_path_for_filename(rel_path)
    return prompt, entry_name, f"File: {rel_path}", f"file:{rel_path}"


def _prepare_function_topic(topic, repo_path, lang=None):
    lang = lang or PYTHON
    if ":" not in topic.target:
        print(f"Function topic must be file:symbol, got: {topic.target}", file=sys.stderr)
        return None

    file_path, symbol_name = topic.target.rsplit(":", 1)
    abs_path = os.path.join(repo_path, file_path) if not os.path.isabs(file_path) else file_path

    if not os.path.isfile(abs_path):
        print(f"File not found: {file_path} (skipping)", file=sys.stderr)
        return None

    symbol_source = extract_symbol(abs_path, symbol_name, lang=lang)
    if symbol_source is None:
        print(f"Symbol '{symbol_name}' not found in {file_path} (skipping)", file=sys.stderr)
        return None

    full_content = get_file_content(abs_path)
    related_tests = find_related_tests(abs_path, repo_path, symbol_name, lang=lang)
    rel_path = os.path.relpath(abs_path, repo_path)

    prompt = build_function_prompt(
        file_path=rel_path,
        symbol_name=symbol_name,
        symbol_source=symbol_source,
        full_file_content=full_content,
        related_tests=related_tests or None,
        language=lang.fence_language,
    )

    entry_name = _sanitize_path_for_filename(rel_path) + f"-{symbol_name}"
    return prompt, entry_name, f"Function: {symbol_name} in {rel_path}", f"function:{rel_path}:{symbol_name}"


def _prepare_repo_topic(topic, repo_path, lang=None):
    lang = lang or PYTHON
    target_path = os.path.join(repo_path, topic.target) if topic.target != "." else repo_path
    if not os.path.isdir(target_path):
        target_path = repo_path

    tree = get_repo_structure(target_path)
    _, config_content = _find_project_config(target_path)
    readme_content = get_file_content(os.path.join(target_path, "README.md"))
    entry_points = _find_entry_points(target_path, config_content, lang=lang)

    prompt = build_repo_prompt(
        tree=tree,
        config_content=config_content,
        readme_content=readme_content,
        entry_points=entry_points or None,
    )

    return prompt, "repo-overview", "Repo Overview", "repo-overview"


def _prepare_diff_topic(topic, repo_path, lang=None):
    try:
        diff_content = get_diff(topic.target, cwd=repo_path)
    except RuntimeError as e:
        print(f"Error getting diff: {e}", file=sys.stderr)
        return None

    if not diff_content.strip():
        print("No changes to explain.", file=sys.stderr)
        return None

    commit_log = get_commit_log(topic.target, cwd=repo_path)

    changed_files = []
    for line in diff_content.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            if path != "/dev/null":
                changed_files.append(path)

    prompt = build_diff_prompt(
        diff_content=diff_content,
        commit_log=commit_log,
        changed_files_summary=changed_files or None,
    )

    safe_label = topic.target.replace("/", "-")
    return prompt, f"diff-{safe_label}", f"Diff: {topic.target}", f"diff:{topic.target}"


_PREPARE_DISPATCH = {
    "file": _prepare_file_topic,
    "function": _prepare_function_topic,
    "repo": _prepare_repo_topic,
    "diff": _prepare_diff_topic,
}


def _finalize_topic(entry_name, entry_title, source, result, project_dir):
    _create_entry(entry_name, entry_title, result)
    _enqueue_topics(result, source=source, project_dir=project_dir)
    _report_beliefs(result)


async def _run_general_topic_async(topic, model, repo_path, timeout):
    from ..llm import invoke
    from .prompts.common import BELIEFS_INSTRUCTIONS, TOPICS_INSTRUCTIONS

    lang = detect_language(repo_path)
    tree = get_repo_structure(repo_path, max_depth=2)
    observe_prompt = build_observe_prompt(
        question=topic.title, tree=tree, default_glob=lang.source_globs[0],
    )

    observe_response = await invoke(observe_prompt, model)
    requested_obs = parse_observation_requests(observe_response)

    obs_results = {}
    if requested_obs:
        obs_results = await run_observations(requested_obs, repo_path)

    explain_sections = [
        "You are a senior software engineer explaining a codebase to a new team member.",
        f"The reader wants to understand: **{topic.title}**",
        "",
    ]
    if obs_results:
        explain_sections.extend([
            "## Observations", "",
            "The following information was gathered from the codebase:", "",
            "```json", json.dumps(obs_results, indent=2, default=str), "```", "",
        ])
    explain_sections.extend([
        "## Instructions", "",
        f"Explain **{topic.title}** based on the observations above.",
        "Reference specific files, functions, and line numbers from the observations.",
        "If the observations are insufficient, say what's missing.", "",
        "Format your response as markdown.",
        TOPICS_INSTRUCTIONS, BELIEFS_INSTRUCTIONS,
    ])

    result = await invoke("\n".join(explain_sections), model, timeout=timeout)

    safe_label = _sanitize_path_for_filename(topic.target)
    return result, f"topic-{safe_label}", f"Topic: {topic.title}", f"general:{topic.target}"


async def _explore_topics_concurrent(topics, model, repo_path, timeout, max_concurrent, lang=None):
    from ..llm import invoke
    lang = lang or detect_language(repo_path)
    sem = asyncio.Semaphore(max_concurrent)

    async def _do_topic(topic):
        async with sem:
            print(f"Explaining [{topic.kind}] {topic.target} with {model}...", file=sys.stderr)
            if topic.kind == "general":
                result, entry_name, entry_title, source = await _run_general_topic_async(
                    topic, model, repo_path, timeout,
                )
                return topic, result, entry_name, entry_title, source

            prepare_fn = _PREPARE_DISPATCH.get(topic.kind)
            if not prepare_fn:
                raise ValueError(f"Unknown topic kind: {topic.kind}")

            prepared = prepare_fn(topic, repo_path, lang=lang)
            if prepared is None:
                return None

            prompt, entry_name, entry_title, source = prepared
            result = await invoke(prompt, model, timeout=timeout)
            return topic, result, entry_name, entry_title, source

    return await asyncio.gather(
        *[_do_topic(t) for t in topics],
        return_exceptions=True,
    )


def cmd_explore(args):
    """Explore topics in the queue."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    project_dir = _get_project_dir(args)
    repo_path = _get_repo(args)
    abs_repo = os.path.abspath(repo_path)
    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    parallel = getattr(args, "parallel", 1)
    loop_max = getattr(args, "loop", None)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    if loop_max is not None:
        _explore_loop(args, project_dir, loop_max)
        return

    topic = pop_next(project_dir)
    if topic is None:
        print("No pending topics. Run `reasonsforge code scan` to discover topics.")
        return

    lang = _get_lang(args, abs_repo)

    if topic.kind == "general":
        print(f"Exploring '{topic.title}' with {model}...", file=sys.stderr)
        try:
            result, entry_name, entry_title, source = asyncio.run(
                _run_general_topic_async(topic, model, abs_repo, timeout)
            )
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        _finalize_topic(entry_name, entry_title, source, result, project_dir)
    else:
        prepare_fn = _PREPARE_DISPATCH.get(topic.kind)
        if not prepare_fn:
            print(f"Unknown topic kind: {topic.kind}", file=sys.stderr)
            return

        prepared = prepare_fn(topic, abs_repo, lang=lang)
        if prepared is None:
            return

        prompt, entry_name, entry_title, source = prepared
        print(f"Explaining {topic.target} with {model}...", file=sys.stderr)
        try:
            result = asyncio.run(invoke(prompt, model, timeout=timeout))
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        _finalize_topic(entry_name, entry_title, source, result, project_dir)

    remaining = pending_count(project_dir)
    if remaining:
        print(f"\n{remaining} topic(s) remaining. Run `reasonsforge code explore` to continue.", file=sys.stderr)
    else:
        print("\nNo more topics. Exploration complete.", file=sys.stderr)


def _explore_loop(args, project_dir, max_topics):
    from ..llm import check_model_available, invoke

    repo_path = _get_repo(args)
    abs_repo = os.path.abspath(repo_path)
    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    parallel = getattr(args, "parallel", 1)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    lang = _get_lang(args, abs_repo)
    explored = 0
    while explored < max_topics:
        batch_size = min(parallel, max_topics - explored)
        batch = pop_batch(batch_size, project_dir)
        if not batch:
            if explored == 0:
                print("No pending topics. Run `reasonsforge code scan` to discover topics.")
            else:
                print(f"\nNo more topics after {explored} exploration(s).", file=sys.stderr)
            return

        remaining = pending_count(project_dir)
        print(f"\n{'=' * 40}", file=sys.stderr)
        print(f"[{explored + 1}-{explored + len(batch)}/{max_topics}] "
              f"({remaining} remaining in queue)", file=sys.stderr)
        print(f"{'=' * 40}", file=sys.stderr)
        for topic in batch:
            print(f"  [{topic.kind}] {topic.target}", file=sys.stderr)

        if parallel > 1 and len(batch) > 1:
            results = asyncio.run(
                _explore_topics_concurrent(batch, model, abs_repo, timeout, parallel, lang=lang)
            )
            for r in results:
                if isinstance(r, Exception):
                    print(f"  Error: {r}", file=sys.stderr)
                elif r is not None:
                    _, result, entry_name, entry_title, source = r
                    _finalize_topic(entry_name, entry_title, source, result, project_dir)
        else:
            topic = batch[0]
            if topic.kind == "general":
                print(f"Exploring '{topic.title}' with {model}...", file=sys.stderr)
                try:
                    result, entry_name, entry_title, source = asyncio.run(
                        _run_general_topic_async(topic, model, abs_repo, timeout)
                    )
                except Exception as e:
                    print(f"Error: {e}", file=sys.stderr)
                    explored += len(batch)
                    continue
                _finalize_topic(entry_name, entry_title, source, result, project_dir)
            else:
                prepare_fn = _PREPARE_DISPATCH.get(topic.kind)
                if not prepare_fn:
                    print(f"Unknown topic kind: {topic.kind}", file=sys.stderr)
                    explored += len(batch)
                    continue
                prepared = prepare_fn(topic, abs_repo, lang=lang)
                if prepared is None:
                    explored += len(batch)
                    continue
                prompt, entry_name, entry_title, source = prepared
                print(f"Explaining {topic.target} with {model}...", file=sys.stderr)
                try:
                    result = asyncio.run(invoke(prompt, model, timeout=timeout))
                except Exception as e:
                    print(f"Error: {e}", file=sys.stderr)
                    explored += len(batch)
                    continue
                _finalize_topic(entry_name, entry_title, source, result, project_dir)

        explored += len(batch)

    remaining = pending_count(project_dir)
    print(f"\nExplored {explored} topic(s). {remaining} remaining in queue.", file=sys.stderr)


# ---------------------------------------------------------------------------
# explain file / function / repo / diff
# ---------------------------------------------------------------------------


def cmd_explain_file(args):
    """Explain a file's purpose, structure, and key patterns."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    repo_path = _get_repo(args)
    file_path = args.target

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        repo_resolved = os.path.join(os.path.abspath(repo_path), file_path)
        if os.path.isfile(repo_resolved):
            abs_path = repo_resolved
        else:
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            sys.exit(1)

    content = get_file_content(abs_path)
    if content is None:
        print(f"Error: Cannot read file: {file_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Explaining {file_path}...", file=sys.stderr)
    lang = _get_lang(args, repo_path)
    rel_path = os.path.relpath(abs_path, os.path.abspath(repo_path))
    import_info = get_imports(abs_path, os.path.abspath(repo_path), lang=lang)
    repo_tree = get_repo_structure(os.path.abspath(repo_path), max_depth=2)

    prompt = build_file_prompt(
        file_path=rel_path,
        file_content=content,
        imports=import_info["imports"] or None,
        imported_by=import_info["imported_by"] or None,
        repo_context=repo_tree,
    )

    print(f"Running {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    project_dir = _get_project_dir(args)
    topic_name = _sanitize_path_for_filename(rel_path)
    _create_entry(topic_name, f"File: {rel_path}", result)
    _enqueue_topics(result, source=f"file:{rel_path}", project_dir=project_dir)
    _report_beliefs(result)
    print(result)


def cmd_explain_function(args):
    """Explain a specific function or class. target: file_path:symbol_name"""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    repo_path = _get_repo(args)
    target = args.target

    if ":" not in target:
        print("Error: target must be FILE_PATH:SYMBOL_NAME", file=sys.stderr)
        sys.exit(1)

    file_path, symbol_name = target.rsplit(":", 1)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    abs_path = os.path.abspath(file_path)
    abs_repo = os.path.abspath(repo_path)
    lang = _get_lang(args, repo_path)

    symbol_source = extract_symbol(abs_path, symbol_name, lang=lang)
    if symbol_source is None:
        print(f"Error: Symbol '{symbol_name}' not found in {file_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Explaining {symbol_name} from {file_path}...", file=sys.stderr)
    full_content = get_file_content(abs_path)
    related_tests = find_related_tests(abs_path, abs_repo, symbol_name, lang=lang)
    rel_path = os.path.relpath(abs_path, abs_repo)

    prompt = build_function_prompt(
        file_path=rel_path,
        symbol_name=symbol_name,
        symbol_source=symbol_source,
        full_file_content=full_content,
        related_tests=related_tests or None,
        language=lang.fence_language,
    )

    print(f"Running {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    project_dir = _get_project_dir(args)
    topic_name = _sanitize_path_for_filename(rel_path) + f"-{symbol_name}"
    _create_entry(topic_name, f"Function: {symbol_name} in {rel_path}", result)
    _enqueue_topics(result, source=f"function:{rel_path}:{symbol_name}", project_dir=project_dir)
    _report_beliefs(result)
    print(result)


def cmd_explain_diff(args):
    """Explain what changed in a diff and why."""
    from ..llm import check_model_available, invoke

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    repo_path = _get_repo(args)
    project_dir = _get_project_dir(args)

    branch = getattr(args, "branch", None)
    base = getattr(args, "base", "main")
    since = getattr(args, "since", None)
    since_last = getattr(args, "since_last", False)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    abs_repo = os.path.abspath(repo_path)

    try:
        if since_last:
            checkpoint = load_diff_checkpoint(project_dir)
            if not checkpoint:
                print("No previous diff checkpoint found. Use --since DATE first.", file=sys.stderr)
                sys.exit(1)
            print(f"Picking up from {checkpoint['timestamp']} ({checkpoint['head'][:8]})", file=sys.stderr)
            diff_content, commit_log = get_diff_since_commit(checkpoint["head"], cwd=abs_repo)
        elif since:
            diff_content, commit_log = get_diff_since(since, cwd=abs_repo)
        elif branch:
            diff_content = get_diff(branch, base, cwd=abs_repo)
            commit_log = get_commit_log(branch, base, cwd=abs_repo)
        else:
            diff_content = get_diff(cwd=abs_repo)
            commit_log = None
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not diff_content.strip():
        print("No changes since last run.", file=sys.stderr)
        return

    changed_files = []
    for line in diff_content.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            if path != "/dev/null":
                changed_files.append(path)

    if since_last:
        diff_label = f"since-last ({checkpoint['head'][:8]})"
    elif since:
        diff_label = f"since {since}"
    else:
        diff_label = branch or "staged"
    print(f"Explaining {diff_label} changes ({len(changed_files)} files)...", file=sys.stderr)

    max_diff_chars = 100_000
    if len(diff_content) > max_diff_chars:
        print(f"Diff too large ({len(diff_content):,} chars). Using summary mode.", file=sys.stderr)
        prompt = build_diff_summary_prompt(
            commit_log=commit_log,
            changed_files=changed_files or None,
        )
    else:
        prompt = build_diff_prompt(
            diff_content=diff_content,
            commit_log=commit_log,
            changed_files_summary=changed_files or None,
        )

    print(f"Running {model}...", file=sys.stderr)
    try:
        result = asyncio.run(invoke(prompt, model, timeout=timeout))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    safe_label = diff_label.replace("/", "-").replace(" ", "-")
    _create_entry(f"diff-{safe_label}", f"Diff: {diff_label}", result)
    _enqueue_topics(result, source=f"diff:{diff_label}", project_dir=project_dir)
    _report_beliefs(result)

    if since or since_last:
        save_diff_checkpoint(project_dir, cwd=abs_repo)
        print("Diff checkpoint saved.", file=sys.stderr)

    print(result)


# ---------------------------------------------------------------------------
# walk-commits
# ---------------------------------------------------------------------------


def _retract_beliefs_for_deleted_files(deleted_files: set[str], db_path: str, lang=None) -> None:
    from reasonsforge.api import export_network, retract_node

    try:
        network = export_network(db_path=db_path)
    except Exception:
        return

    nodes = network.get("nodes", {})
    patterns = {_repo_path_to_entry_pattern(f, lang=lang) for f in deleted_files}

    to_retract = []
    for nid, node in nodes.items():
        if node.get("truth_value") != "IN":
            continue
        source = node.get("source", "")
        source_file = (node.get("metadata") or {}).get("source_file", "")
        for pattern in patterns:
            if pattern in source or pattern in source_file:
                to_retract.append(nid)
                break

    if not to_retract:
        print(f"  No beliefs found sourced from deleted files", file=sys.stderr)
        return

    deleted_names = ", ".join(sorted(deleted_files))
    print(f"  Retracting {len(to_retract)} belief(s) sourced from deleted file(s): {deleted_names}",
          file=sys.stderr)

    retracted = 0
    for nid in to_retract:
        try:
            retract_node(nid, reason=f"Source file deleted: {deleted_names}", db_path=db_path)
            retracted += 1
            print(f"    Retracted: {nid}", file=sys.stderr)
        except Exception as e:
            print(f"    Failed to retract {nid}: {e}", file=sys.stderr)
    print(f"  Retracted {retracted}/{len(to_retract)} belief(s)", file=sys.stderr)


def cmd_walk_commits(args):
    """Walk commits since a date/commit and explore each changed file."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke
    _caffeinate()

    repo_path = _get_repo(args)
    abs_repo = os.path.abspath(repo_path)
    project_dir = _get_project_dir(args)
    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    parallel = getattr(args, "parallel", 1)
    db_path = getattr(args, "output", REASONS_DB)

    since = getattr(args, "since", None)
    since_commit = getattr(args, "since_commit", None)
    since_last = getattr(args, "since_last", False)
    dry_run = getattr(args, "dry_run", False)

    if since_last:
        checkpoint = load_diff_checkpoint(project_dir)
        if not checkpoint:
            print("No previous diff checkpoint found. Use --since DATE first.", file=sys.stderr)
            sys.exit(1)
        since_commit = checkpoint["head"]
        print(f"Walking from checkpoint {since_commit[:8]}", file=sys.stderr)
    elif not since and not since_commit:
        print("Error: provide --since DATE, --since-commit SHA, or --since-last", file=sys.stderr)
        sys.exit(1)

    try:
        commits = list_commits_with_files(since=since, since_commit=since_commit, cwd=abs_repo)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not commits:
        print("No commits found.", file=sys.stderr)
        return

    file_to_commit: dict[str, dict] = {}
    deleted_files: set[str] = set()
    for commit in commits:
        for f in commit["files"]:
            file_to_commit[f] = commit
        for f in commit.get("deleted_files", []):
            deleted_files.add(f)
    for f in list(deleted_files):
        abs_path = os.path.join(abs_repo, f)
        if os.path.isfile(abs_path):
            deleted_files.discard(f)

    total_files = len(file_to_commit)
    print(f"Found {len(commits)} commit(s), {total_files} unique file(s) to explore", file=sys.stderr)
    if deleted_files:
        print(f"  {len(deleted_files)} file(s) deleted", file=sys.stderr)

    if dry_run:
        for commit in commits:
            print(f"\n  {commit['sha'][:8]} {commit['subject']}")
            for f in commit["files"]:
                marker = " " if file_to_commit[f] is commit else " (earlier version, skip)"
                if f in deleted_files:
                    marker = " [DELETED]"
                print(f"    {marker} {f}")
        print(f"\nWould explore {total_files} file(s)")
        return

    if deleted_files:
        lang = _get_lang(args, abs_repo)
        _retract_beliefs_for_deleted_files(deleted_files, db_path, lang=lang)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    lang = _get_lang(args, abs_repo)
    all_topics = []
    skipped = 0
    for file_path, commit in file_to_commit.items():
        abs_path = os.path.join(abs_repo, file_path)
        if not os.path.isfile(abs_path):
            print(f"  File not found (deleted?): {file_path}, skipping", file=sys.stderr)
            skipped += 1
            continue
        all_topics.append(Topic(
            title=f"{commit['subject']} — {file_path}",
            kind="file",
            target=file_path,
            source=f"walk-commits:{commit['sha'][:8]}",
        ))

    explored = 0
    while explored < len(all_topics):
        batch = all_topics[explored:explored + parallel]
        print(f"\n{'=' * 40}", file=sys.stderr)
        print(f"[{explored + 1}-{explored + len(batch)}/{len(all_topics)}]", file=sys.stderr)
        print(f"{'=' * 40}", file=sys.stderr)

        if parallel > 1 and len(batch) > 1:
            results = asyncio.run(
                _explore_topics_concurrent(batch, model, abs_repo, timeout, parallel, lang=lang)
            )
            for r in results:
                if isinstance(r, Exception):
                    print(f"  Error: {r}", file=sys.stderr)
                elif r is not None:
                    _, result, entry_name, entry_title, source = r
                    _finalize_topic(entry_name, entry_title, source, result, project_dir)
        else:
            for topic in batch:
                prepared = _prepare_file_topic(topic, abs_repo, lang=lang)
                if prepared is None:
                    continue
                prompt, entry_name, entry_title, source = prepared
                print(f"Explaining {topic.target} with {model}...", file=sys.stderr)
                try:
                    result = asyncio.run(invoke(prompt, model, timeout=timeout))
                except Exception as e:
                    print(f"Error: {e}", file=sys.stderr)
                    continue
                _finalize_topic(entry_name, entry_title, source, result, project_dir)

        explored += len(batch)

    save_diff_checkpoint(project_dir, cwd=abs_repo)
    print(f"\nWalked {len(commits)} commit(s), explored {len(all_topics)} file(s) ({skipped} skipped)",
          file=sys.stderr)
    print("Diff checkpoint saved.", file=sys.stderr)


# ---------------------------------------------------------------------------
# propose-beliefs
# ---------------------------------------------------------------------------


def cmd_propose_beliefs(args):
    """Extract candidate beliefs from entries."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke, invoke_sync
    _caffeinate()

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    batch_size = getattr(args, "batch_size", 5)
    output = getattr(args, "proposals_output", "proposed-beliefs.md")
    process_all = getattr(args, "all", False)
    auto_accept = getattr(args, "auto", False)
    since = getattr(args, "since", None)
    parallel = getattr(args, "parallel", 1)
    db_path = getattr(args, "output", REASONS_DB)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    input_dir = Path("summaries")
    if not input_dir.exists():
        print("No summaries/ directory found. Run explorations first.")
        sys.exit(1)
    entries = sorted(input_dir.rglob("*.md"))

    if not entries:
        print("No .md files found.")
        return

    if since:
        before = len(entries)
        entries = [e for e in entries if (_entry_date(e) or "") >= since]
        print(f"Filtered to {len(entries)} entries since {since} (from {before} total)", file=sys.stderr)
        if not entries:
            print("No entries found since that date.")
            return

    # Filter already-processed
    processed_path = Path(PROJECT_DIR) / "proposed-entries.json"
    processed = _load_processed(processed_path)
    if not process_all:
        total = len(entries)
        entries = _filter_unprocessed(entries, processed)
        skipped = total - len(entries)
        if skipped:
            print(f"Skipping {skipped} already-processed entries (use --all to reprocess)")
        if not entries:
            print("No new entries to process.")
            return

    # Load existing beliefs from reasons.db for dedup
    existing_beliefs = _load_existing_beliefs(db_path)
    existing_ids = {b["id"] for b in existing_beliefs}
    if existing_ids:
        print(f"Found {len(existing_ids)} existing beliefs (will skip duplicates)")

    print(f"Reading {len(entries)} entries...")

    batches = []
    batch_paths = []
    current_batch = []
    current_paths = []
    for entry_path in entries:
        content = entry_path.read_text()
        if len(content) > 10000:
            content = content[:10000] + "\n[Truncated]"
        current_batch.append(f"--- FILE: {entry_path} ---\n{content}")
        current_paths.append(str(entry_path))
        if len(current_batch) >= batch_size:
            batches.append("\n\n".join(current_batch))
            batch_paths.append(current_paths)
            current_batch = []
            current_paths = []
    if current_batch:
        batches.append("\n\n".join(current_batch))
        batch_paths.append(current_paths)

    print(f"Processing {len(batches)} batches (batch size: {batch_size})...")

    all_proposals = []
    for i, batch_text in enumerate(batches):
        existing_context = _build_dedup_context(existing_beliefs, batch_paths[i], batch_text)
        prompt = PROPOSE_BELIEFS_CODE.format(entries=batch_text) + existing_context

        print(f"  Batch {i + 1}/{len(batches)}...", file=sys.stderr)
        try:
            result = asyncio.run(invoke(prompt, model, timeout=timeout))
            print(f"  Batch {i + 1}/{len(batches)} done")
            all_proposals.append(result)
        except Exception as e:
            print(f"  ERROR in batch {i + 1}: {e}")

    # Filter already-existing proposals
    filtered_proposals = []
    dup_skipped = 0
    for proposal in all_proposals:
        lines = proposal.split("\n")
        filtered_lines = []
        skip_until_next = False
        for line in lines:
            m = re.match(r"^### \[?(?:ACCEPT|REJECT)\]? (\S+)", line)
            if m:
                belief_id = m.group(1)
                if belief_id in existing_ids:
                    skip_until_next = True
                    dup_skipped += 1
                    continue
                else:
                    skip_until_next = False
            if skip_until_next:
                if line.startswith("### "):
                    skip_until_next = False
                    filtered_lines.append(line)
                continue
            filtered_lines.append(line)
        filtered_proposals.append("\n".join(filtered_lines))

    if dup_skipped:
        print(f"  Filtered {dup_skipped} already-accepted beliefs")

    _save_processed(processed_path, entries, processed)

    if auto_accept:
        accept_pattern = re.compile(
            r"^### \[?(?:ACCEPT(?:/REJECT)?|REJECT)\]? (\S+)\n(.+?)\n- Source: (.+?)(?:\n|$)",
            re.MULTILINE,
        )
        matches = []
        for proposal in filtered_proposals:
            matches.extend(accept_pattern.findall(proposal))
        if not matches:
            print("No beliefs extracted from proposals.")
            return
        print(f"\nAuto-accepting {len(matches)} beliefs...")
        _accept_proposals(matches, db_path)
        return

    # Write proposals file
    source_desc = f"{len(entries)} summaries from summaries/"
    output_path = Path(output)
    if output_path.exists() and output_path.stat().st_size > 0:
        with output_path.open("a") as f:
            f.write(f"\n---\n\n")
            f.write(f"**Generated:** {date.today().isoformat()}\n")
            f.write(f"**Source:** {source_desc}\n")
            f.write(f"**Model:** {model}\n\n")
            for proposal in filtered_proposals:
                f.write(proposal)
                f.write("\n\n")
        print(f"\nAppended to {output_path}")
    else:
        with output_path.open("w") as f:
            f.write("# Proposed Beliefs\n\n")
            f.write("Edit each entry: change `[ACCEPT/REJECT]` to `[ACCEPT]` or `[REJECT]`.\n")
            f.write("Then run: `reasonsforge code accept-beliefs`\n\n")
            f.write("---\n\n")
            f.write(f"**Generated:** {date.today().isoformat()}\n")
            f.write(f"**Source:** {source_desc}\n")
            f.write(f"**Model:** {model}\n\n")
            for proposal in filtered_proposals:
                f.write(proposal)
                f.write("\n\n")
        print(f"\nWrote {output_path}")

    print("Review the file, mark entries as [ACCEPT] or [REJECT], then run:")
    print("  reasonsforge code accept-beliefs")


def _entry_date(path: Path) -> str | None:
    parts = path.parts
    for i, part in enumerate(parts):
        if part == "summaries" and i + 3 < len(parts):
            try:
                y, m, d = parts[i + 1], parts[i + 2], parts[i + 3]
                if len(y) == 4 and len(m) == 2 and len(d) == 2:
                    return f"{y}-{m}-{d}"
            except (IndexError, ValueError):
                pass
    return None


def _load_processed(path: Path) -> dict[str, str]:
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _save_processed(path: Path, entries: list[Path], existing: dict[str, str]):
    import hashlib
    updated = dict(existing)
    for entry_path in entries:
        content = entry_path.read_text()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        updated[str(entry_path)] = content_hash
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(updated, indent=2) + "\n")


def _filter_unprocessed(entries: list[Path], processed: dict[str, str]) -> list[Path]:
    import hashlib
    unprocessed = []
    for entry_path in entries:
        key = str(entry_path)
        if key not in processed:
            unprocessed.append(entry_path)
            continue
        content = entry_path.read_text()
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
        if content_hash != processed[key]:
            unprocessed.append(entry_path)
    return unprocessed


def _load_existing_beliefs(db_path: str) -> list[dict]:
    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        nodes = network.get("nodes", {})
        return [
            {"id": nid, "text": node.get("text", ""), "source": node.get("source", "")}
            for nid, node in nodes.items()
            if node.get("truth_value") == "IN"
        ]
    except Exception:
        return []


def _build_dedup_context(
    existing_beliefs: list[dict],
    batch_entry_paths: list[str],
    batch_text: str,
    max_detailed: int = 50,
    max_compact: int = 200,
) -> str:
    if not existing_beliefs:
        return ""

    scored = _score_by_keywords(existing_beliefs, batch_text, batch_entry_paths)
    detailed = scored[:max_detailed]
    compact = scored[max_detailed:max_detailed + max_compact]

    parts = [
        "\n\n## Already Accepted Beliefs\n\n"
        "The following beliefs already exist. Do NOT propose beliefs with these IDs "
        "or that duplicate their meaning under different names.\n"
    ]
    if detailed:
        parts.append("\nRelevant existing beliefs:")
        for _, belief in detailed:
            parts.append(f"- `{belief['id']}`: {belief['text']}")
    if compact:
        compact_ids = ", ".join(b["id"] for _, b in compact)
        parts.append(f"\nOther existing IDs: {compact_ids}")

    return "\n".join(parts) + "\n"


def _score_by_keywords(
    beliefs: list[dict],
    batch_text: str,
    batch_entry_paths: list[str],
) -> list[tuple[float, dict]]:
    batch_words = set(re.findall(r'[a-z]{3,}', batch_text.lower()))
    scored = []
    for belief in beliefs:
        score = 0.0
        if belief["source"] and any(belief["source"] in p or p in belief["source"]
                                     for p in batch_entry_paths):
            score += 1000
        belief_words = set(re.findall(r'[a-z]{3,}', belief["text"].lower()))
        belief_words |= set(belief["id"].replace("-", " ").lower().split())
        overlap = len(batch_words & belief_words)
        score += overlap
        scored.append((score, belief))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# accept-beliefs
# ---------------------------------------------------------------------------


def _accept_proposals(matches: list[tuple[str, str, str]], db_path: str) -> tuple[int, int, int]:
    from reasonsforge.api import add_node, set_metadata

    print("Using reasonsforge API as primary store...")
    added = 0
    failed = 0
    skipped = 0
    for belief_id, claim_text, source in matches:
        try:
            result = add_node(belief_id, claim_text.strip(), source=source.strip(), db_path=db_path)
            print(f"  Added: {belief_id}")
            added += 1
            now = datetime.now().isoformat(timespec="seconds")
            try:
                set_metadata(belief_id, "accepted_at", now, db_path=db_path)
            except Exception:
                pass
        except Exception as e:
            err_str = str(e)
            if "already exists" in err_str:
                print(f"  EXISTS: {belief_id}")
                skipped += 1
            else:
                print(f"  FAIL: {belief_id}: {e}")
                failed += 1

    print(f"\nAccepted {added} beliefs ({skipped} existing, {failed} failed)")
    return added, skipped, failed


def cmd_accept_beliefs(args):
    """Import accepted beliefs from proposals file."""
    proposals_file = getattr(args, "proposals_file", "proposed-beliefs.md")
    db_path = getattr(args, "output", REASONS_DB)

    proposals_path = Path(proposals_file)
    if not proposals_path.exists():
        print(f"Proposals file not found: {proposals_file}")
        print("Run: reasonsforge code propose-beliefs")
        sys.exit(1)

    text = proposals_path.read_text()
    pattern = re.compile(
        r"### \[?ACCEPT\]? (\S+)\n"
        r"(.+?)\n"
        r"- Source: (.+?)(?:\n|$)"
    )
    matches = pattern.findall(text)

    if not matches:
        print("No [ACCEPT] entries found in proposals file.")
        print("Edit the file and change [ACCEPT/REJECT] to [ACCEPT] for beliefs to keep.")
        return

    print(f"Found {len(matches)} accepted beliefs")
    _accept_proposals(matches, db_path)


# ---------------------------------------------------------------------------
# review-proposals
# ---------------------------------------------------------------------------


def _build_existing_beliefs_section(nodes: dict) -> str:
    if not nodes:
        return "(No existing beliefs)"
    lines = []
    for node_id, node in sorted(nodes.items()):
        text = node.get("text", "")[:100]
        tv = node.get("truth_value", "?")
        lines.append(f"- `{node_id}` [{tv}]: {text}")
    if len(lines) > 300:
        lines = lines[:300]
        lines.append(f"... and {len(nodes) - 300} more beliefs")
    return "\n".join(lines)


def _build_proposals_section(proposals: list[dict]) -> str:
    lines = []
    for p in proposals:
        lines.append(f"### {p['id']}")
        lines.append(p["text"])
        lines.append(f"- Source: {p['source']}")
        lines.append("")
    return "\n".join(lines)


def _parse_review_response(response: str) -> dict[str, tuple[bool, str | None]]:
    decisions = {}
    for line in response.splitlines():
        line = line.strip()
        if line.startswith("ACCEPT "):
            belief_id = line[7:].strip()
            decisions[belief_id] = (False, None)
        elif line.startswith("REJECT "):
            rest = line[7:].strip()
            parts = rest.split(" ", 1)
            belief_id = parts[0]
            reason = parts[1] if len(parts) > 1 else "rejected by review"
            decisions[belief_id] = (True, reason)
    return decisions


def cmd_review_proposals(args):
    """Filter low-quality belief proposals using LLM review."""
    from ..llm import check_model_available, invoke_sync

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    db_path = getattr(args, "output", REASONS_DB)
    proposals_file = getattr(args, "proposals_file", "proposed-beliefs.md")
    batch_size = getattr(args, "batch_size", 20)

    proposals_path = Path(proposals_file)
    if not proposals_path.exists():
        print(f"Proposals file not found: {proposals_file}")
        sys.exit(1)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    text = proposals_path.read_text()

    try:
        from reasonsforge.api import export_network
        network = export_network(db_path=db_path)
        existing_nodes = network.get("nodes", {})
    except Exception:
        existing_nodes = {}

    proposal_pattern = re.compile(
        r"(### \[?(?:ACCEPT(?:/REJECT)?|REJECT)\]?)\s*(\S+)\n"
        r"(.+?)\n"
        r"(- Source: .+?)(?=\n###|\n---|\Z)",
        re.DOTALL,
    )

    matches = list(proposal_pattern.finditer(text))
    if not matches:
        print("No proposals found in file.")
        return

    to_review = []
    already_rejected = 0
    for match in matches:
        header = match.group(1)
        if "[REJECT]" in header and "[ACCEPT" not in header:
            already_rejected += 1
            continue
        to_review.append({
            "match": match,
            "header": header,
            "id": match.group(2),
            "text": match.group(3).strip(),
            "source": match.group(4).strip().removeprefix("- Source: "),
        })

    if not to_review:
        print("No proposals to review (all already rejected).", file=sys.stderr)
        return

    print(f"Reviewing {len(to_review)} proposals ({already_rejected} already rejected)...",
          file=sys.stderr)

    existing_beliefs = _build_existing_beliefs_section(existing_nodes)
    all_decisions: dict[str, tuple[bool, str | None]] = {}
    review_batches = [to_review[i:i + batch_size] for i in range(0, len(to_review), batch_size)]

    for i, batch in enumerate(review_batches):
        proposals_section = _build_proposals_section(batch)
        prompt = REVIEW_PROMPT.format(
            existing_beliefs=existing_beliefs,
            proposals=proposals_section,
        )
        print(f"  Batch {i + 1}/{len(review_batches)}...", file=sys.stderr)
        try:
            result = invoke_sync(prompt, model=model, timeout=timeout)
            decisions = _parse_review_response(result)
            all_decisions.update(decisions)
        except Exception as e:
            print(f"  ERROR in batch {i + 1}: {e}", file=sys.stderr)

    kept = 0
    rejected = 0
    categories: dict[str, int] = {}
    replacements: list[tuple[str, str]] = []

    for proposal in to_review:
        belief_id = proposal["id"]
        match = proposal["match"]
        header = proposal["header"]

        decision = all_decisions.get(belief_id)
        if decision is None:
            kept += 1
            continue

        reject, reason = decision
        if reject:
            rejected += 1
            category = reason.split(":")[0].strip() if reason else "unknown"
            categories[category] = categories.get(category, 0) + 1

            old_block = match.group(0)
            new_header = f"### [REJECT] {belief_id}"
            new_block = old_block.replace(f"{header} {belief_id}", new_header, 1)
            source_line = match.group(4).strip()
            new_block = new_block.replace(source_line, f"{source_line}\n- Rejected: {reason}", 1)
            replacements.append((old_block, new_block))
            print(f"  REJECT {belief_id}: {reason}", file=sys.stderr)
        else:
            kept += 1

    print(f"\nReviewed {len(to_review)} proposals: {kept} kept, {rejected} rejected", file=sys.stderr)
    if categories:
        print("Rejections by category:", file=sys.stderr)
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}", file=sys.stderr)

    if replacements:
        for old_block, new_block in replacements:
            text = text.replace(old_block, new_block, 1)
        proposals_path.write_text(text)
        print(f"Updated {proposals_file}", file=sys.stderr)


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------


def _parse_verify_response(response: str) -> dict[str, dict]:
    m = re.search(r"\{.*\}", response, re.DOTALL)
    if not m:
        print("  WARN: no JSON found in LLM response", file=sys.stderr)
        return {}
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, TypeError):
        print("  WARN: failed to parse JSON from LLM response", file=sys.stderr)
        return {}
    results = {}
    for k, v in data.items():
        if not isinstance(v, dict) or "verdict" not in v:
            continue
        verdict = v["verdict"]
        if not isinstance(verdict, str):
            continue
        results[k] = {"verdict": verdict.upper(), "reason": v.get("reason", "")}
    return results


async def _gather_belief_context(belief, nodes, repo_path, project_dir=None):
    from .observations import grep, read_file

    bid = belief["id"]
    node = nodes.get(bid, {})
    source = node.get("source", "")
    parts: list[str] = []

    src_file = (node.get("metadata") or {}).get("source_file")
    if not src_file:
        src_file = _extract_source_file(source, project_dir)
    if src_file:
        result = await read_file(src_file, repo_path, max_lines=300)
        if "content" in result:
            content = result["content"]
            if len(content) > 4000:
                content = content[:4000] + "\n... (truncated)"
            parts.append(f"### {src_file}\n```\n{content}\n```")

    terms = [t for t in bid.replace("-", " ").split() if len(t) > 3][:3]
    grep_tasks = [grep(term, repo_path, glob="*", max_results=5) for term in terms]
    grep_results = await asyncio.gather(*grep_tasks, return_exceptions=True)
    for term, result in zip(terms, grep_results):
        if isinstance(result, Exception):
            continue
        if result.get("matches"):
            matches_text = "\n".join(
                f"  {m['file']}:{m['line']}: {m['text']}" for m in result["matches"][:5]
            )
            parts.append(f"### grep '{term}'\n{matches_text}")

    return bid, "\n\n".join(parts) if parts else "(no code context found)"


async def _gather_confirmation_context(beliefs, nodes, repo_path, project_dir=None):
    tasks = [_gather_belief_context(b, nodes, repo_path, project_dir) for b in beliefs]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    contexts: dict[str, str] = {}
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            contexts[beliefs[i]["id"]] = "(error gathering context)"
        else:
            contexts[result[0]] = result[1]
    return contexts


def cmd_verify(args):
    """Check whether beliefs still hold against current source code."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke_sync
    _caffeinate()

    model = getattr(args, "model", "claude")
    timeout = getattr(args, "timeout", 300)
    repo_path = _get_repo(args)
    abs_repo = os.path.abspath(repo_path)
    project_dir = _get_project_dir(args)
    db_path = getattr(args, "output", REASONS_DB)
    retract = getattr(args, "retract", False)
    verify_all = getattr(args, "all", False)
    category = getattr(args, "category", None)
    batch_size = getattr(args, "batch_size", 10)
    dry_run = getattr(args, "dry_run", False)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    try:
        from reasonsforge.api import export_network, retract_node, set_metadata
        network = export_network(db_path=db_path)
        nodes = network.get("nodes", {})
    except Exception as e:
        print(f"Error loading belief network: {e}", file=sys.stderr)
        sys.exit(1)

    if not nodes:
        print("No beliefs found. Run explorations and propose-beliefs first.")
        return

    beliefs: list[dict] = []
    belief_ids = getattr(args, "belief_ids", [])

    if belief_ids:
        for bid in belief_ids:
            node = nodes.get(bid)
            if node:
                beliefs.append({"id": bid, "text": node.get("text", "")})
            else:
                print(f"  Belief not found: {bid}", file=sys.stderr)
    elif category:
        keyword = category.lower()
        for nid, node in nodes.items():
            if node.get("truth_value") != "IN":
                continue
            if keyword in nid.lower() or keyword in node.get("text", "").lower():
                beliefs.append({"id": nid, "text": node.get("text", "")})
        print(f"Found {len(beliefs)} IN belief(s) matching '{category}'", file=sys.stderr)
    elif verify_all:
        for nid, node in nodes.items():
            if node.get("truth_value") == "IN":
                beliefs.append({"id": nid, "text": node.get("text", "")})
        print(f"Found {len(beliefs)} IN belief(s) to verify", file=sys.stderr)
    else:
        print("Specify belief IDs, or use --category or --all.", file=sys.stderr)
        sys.exit(1)

    if not beliefs:
        print("No beliefs to verify.")
        return

    if dry_run:
        print(f"\n{len(beliefs)} belief(s) would be verified:", file=sys.stderr)
        for b in beliefs:
            print(f"  {b['id']}: {b['text'][:100]}", file=sys.stderr)
        return

    all_results: dict[str, dict] = {}
    batches = [beliefs[i:i + batch_size] for i in range(0, len(beliefs), batch_size)]

    for i, batch in enumerate(batches):
        print(f"\nVerifying batch {i + 1}/{len(batches)} ({len(batch)} beliefs)...", file=sys.stderr)

        contexts = asyncio.run(
            _gather_confirmation_context(batch, nodes, abs_repo, project_dir)
        )

        beliefs_section = []
        for belief in batch:
            ctx_text = contexts.get(belief["id"], "(no code context found)")
            beliefs_section.append(
                f"### `{belief['id']}`\n{belief['text']}\n\n"
                f"**Code context:**\n{ctx_text}"
            )

        prompt = VERIFY_PROMPT.format(beliefs="\n\n---\n\n".join(beliefs_section))

        try:
            response = invoke_sync(prompt, model=model, timeout=timeout)
            results = _parse_verify_response(response)
            all_results.update(results)
        except Exception as e:
            print(f"  Error: {e}", file=sys.stderr)

    confirmed = []
    stale = []
    inconclusive = []

    for belief in beliefs:
        bid = belief["id"]
        result = all_results.get(bid)
        if not result:
            inconclusive.append(bid)
            print(f"  {bid}: INCONCLUSIVE (no verdict returned)", file=sys.stderr)
            continue

        verdict = result["verdict"]
        reason = result.get("reason", "")

        if verdict == "CONFIRMED":
            confirmed.append(bid)
            print(f"  {bid}: CONFIRMED — {reason}", file=sys.stderr)
        elif verdict == "STALE":
            stale.append(bid)
            print(f"  {bid}: STALE — {reason}", file=sys.stderr)
        else:
            inconclusive.append(bid)
            print(f"  {bid}: INCONCLUSIVE — {reason}", file=sys.stderr)

    print(f"\nResults: {len(confirmed)} confirmed, {len(stale)} stale, "
          f"{len(inconclusive)} inconclusive", file=sys.stderr)

    # Stamp verified_at on confirmed beliefs
    if confirmed:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stamped = 0
        for bid in confirmed:
            try:
                set_metadata(bid, "verified_at", now, db_path=db_path)
                stamped += 1
            except Exception:
                pass
        if stamped:
            print(f"Stamped verified_at on {stamped} belief(s)", file=sys.stderr)

    # Retract stale beliefs
    if retract and stale:
        print(f"\nRetracting {len(stale)} stale belief(s)...", file=sys.stderr)
        for bid in stale:
            reason = all_results.get(bid, {}).get("reason", "stale per verify")
            try:
                retract_node(bid, reason=reason, db_path=db_path)
                print(f"  Retracted: {bid}", file=sys.stderr)
            except Exception as e:
                print(f"  Failed to retract {bid}: {e}", file=sys.stderr)
        print("Network updated.", file=sys.stderr)


# ---------------------------------------------------------------------------
# derive
# ---------------------------------------------------------------------------


def cmd_derive(args):
    """Derive deeper reasoning chains from existing beliefs."""
    from ..caffeinate import hold as _caffeinate
    from ..llm import check_model_available, invoke_sync
    from reasonsforge.derive import apply_proposals, build_prompt, parse_proposals, validate_proposals
    from reasonsforge.api import export_network
    _caffeinate()

    model = getattr(args, "model", "claude")
    timeout = max(getattr(args, "timeout", 300), 600)
    db_path = getattr(args, "output", REASONS_DB)
    auto_add = getattr(args, "auto", False)
    exhaust = getattr(args, "exhaust", False)
    max_rounds = getattr(args, "max_derive_rounds", 10)
    budget = getattr(args, "budget", 300)
    domain = getattr(args, "domain", None)

    if not check_model_available(model):
        print(f"Error: Model '{model}' CLI not available", file=sys.stderr)
        sys.exit(1)

    if exhaust:
        auto_add = True

    total_added = 0
    for round_num in range(1, max_rounds + 1):
        if exhaust:
            print(f"\n--- Derive round {round_num}/{max_rounds} ---", file=sys.stderr)

        try:
            network = export_network(db_path=db_path)
            nodes = network.get("nodes", {})
        except Exception as e:
            print(f"Error loading network: {e}", file=sys.stderr)
            sys.exit(1)

        prompt = build_prompt(nodes, domain=domain, budget=budget, sample=True)

        try:
            response = invoke_sync(prompt, model=model, timeout=timeout)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)

        proposals = parse_proposals(response)
        if not proposals:
            print("No proposals generated.", file=sys.stderr)
            if exhaust:
                print(f"Exhausted after {round_num} round(s), {total_added} total derivation(s).",
                      file=sys.stderr)
            break

        valid = validate_proposals(proposals, nodes)
        if not valid:
            print("No valid proposals after validation.", file=sys.stderr)
            if exhaust:
                print(f"Exhausted after {round_num} round(s), {total_added} total derivation(s).",
                      file=sys.stderr)
            break

        print(f"Generated {len(valid)} valid derivation(s)", file=sys.stderr)

        if auto_add:
            added = apply_proposals(valid, db_path=db_path)
            print(f"Added {added} derivation(s) to {db_path}", file=sys.stderr)
            total_added += added
            if added == 0 and exhaust:
                print(f"Exhausted after {round_num} round(s), {total_added} total derivation(s).",
                      file=sys.stderr)
                break
        else:
            for v in valid:
                print(f"  {v['id']}: {v['text'][:80]}", file=sys.stderr)
            break

        if not exhaust:
            break

    if exhaust and total_added > 0:
        print(f"\nDerived {total_added} belief(s) total.", file=sys.stderr)


# ---------------------------------------------------------------------------
# topics
# ---------------------------------------------------------------------------


def cmd_topics(args):
    """Show the exploration queue."""
    project_dir = _get_project_dir(args)
    queue = load_queue(project_dir)

    if not queue:
        print("No topics queued. Run `reasonsforge code scan` to discover topics.")
        return

    pending = [t for t in queue if t.status == "pending"]
    done = [t for t in queue if t.status == "done"]
    skipped = [t for t in queue if t.status == "skipped"]

    show_all = getattr(args, "all", False)

    if pending:
        print(f"Pending ({len(pending)}):\n")
        for i, topic in enumerate(pending):
            print(f"  {i}. [{topic.kind}] {topic.target}")
            print(f"     {topic.title}")
            if topic.source:
                print(f"     (from {topic.source})")
            print()
    else:
        print("No pending topics.")

    if show_all:
        if done:
            print(f"Done ({len(done)}):\n")
            for topic in done:
                print(f"  [{topic.kind}] {topic.target} - {topic.title}")
        if skipped:
            print(f"\nSkipped ({len(skipped)}):\n")
            for topic in skipped:
                print(f"  [{topic.kind}] {topic.target} - {topic.title}")

    print(f"\n{len(pending)} pending, {len(done)} done, {len(skipped)} skipped")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(args):
    """Show code forge dashboard."""
    config = _load_config()

    print("=== Code Forge Status ===\n")

    if config:
        print(f"Repo:     {config.get('repo_path', 'unknown')}")
        print(f"Domain:   {config.get('domain', 'unknown')}")
        print(f"Created:  {config.get('created', 'unknown')}")
    else:
        print("Not initialized. Run: reasonsforge code --repo <path>")
        return

    print()

    summaries_dir = Path("summaries")
    summary_count = len(list(summaries_dir.rglob("*.md"))) if summaries_dir.exists() else 0
    print(f"Summaries: {summary_count}")

    db_path = getattr(args, "output", REASONS_DB)
    if Path(db_path).exists():
        try:
            from reasonsforge.api import export_network, get_status
            status = get_status(db_path=db_path)
            print(f"Beliefs:  {status['in_count']} IN, {status['out_count']} OUT")
            network = export_network(db_path=db_path)
            nogood_count = len(network.get("nogoods", []))
            if nogood_count:
                print(f"Nogoods:  {nogood_count}")
        except Exception:
            print("Beliefs:  (error reading database)")
    else:
        print("Beliefs:  (no database)")

    project_dir = _get_project_dir(args)
    queue = load_queue(project_dir)
    pending = sum(1 for t in queue if t.status == "pending")
    done_count = sum(1 for t in queue if t.status == "done")
    skipped = sum(1 for t in queue if t.status == "skipped")
    print(f"Topics:   {pending} pending, {done_count} done, {skipped} skipped")

    repo_path = _get_repo(args)
    unexplored = commits_since_checkpoint(project_dir, cwd=repo_path)
    if unexplored is not None:
        checkpoint = load_diff_checkpoint(project_dir)
        ts = checkpoint["timestamp"] if checkpoint else "?"
        if unexplored == 0:
            print(f"Diff:     up to date (last: {ts})")
        else:
            print(f"Diff:     {unexplored} unexplored commit(s) (last: {ts})")
    else:
        print("Diff:     no checkpoint")

    proposals_path = Path("proposed-beliefs.md")
    if proposals_path.exists():
        text = proposals_path.read_text()
        total = len(re.findall(r"^### \[(?:ACCEPT|REJECT|ACCEPT/REJECT)\]", text, re.MULTILINE))
        accepted = len(re.findall(r"^### \[ACCEPT\]", text, re.MULTILINE))
        if total:
            print(f"Proposed: {total} candidates ({accepted} accepted)")
