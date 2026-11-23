"""Review commands for reasonsforge code review."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from ..language import detect_language
from ..git_utils import extract_changed_files, get_diff

from . import Verdict
from .aggregator import aggregate_reviews
from .beliefs import filter_beliefs
from .prompts import build_observe_prompt, build_review_prompt
from .report import format_aggregate_review, format_summary
from .reviewer import (
    format_preflight_error,
    parse_observe_response,
    parse_observations,
    preflight_check,
    review_with_models,
    run_model,
)

DEFAULT_MODELS = ["claude"]


def read_file_content(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _parse_ref(ref: str) -> tuple[str | None, int]:
    """Parse owner/repo#N, URL, or plain number."""
    import re

    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/(?:pull|issues)/(\d+)", ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"([^/]+/[^#]+)#(\d+)", ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"(\d+)$", ref)
    if m:
        return None, int(m.group(1))
    raise ValueError(f"Cannot parse reference: {ref}")


def pr_output_dir_name(pr_ref: str) -> str:
    repo, pr_number = _parse_ref(pr_ref)
    if repo:
        return f"{repo.replace('/', '-')}-{pr_number}"
    return f"pr-{pr_number}"


def get_pr_diff(pr_ref: str) -> tuple[str, str, str]:
    import subprocess

    repo, pr_number = _parse_ref(pr_ref)
    cmd = ["gh", "pr", "diff", str(pr_number)]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr diff failed: {result.stderr.strip()}")
    diff_ref = f"{repo}#{pr_number}" if repo else f"PR #{pr_number}"
    return result.stdout, diff_ref, repo or ""


def fetch_pr_locally(pr_ref: str, cwd: str) -> tuple[str, str, str]:
    import subprocess

    repo, pr_number = _parse_ref(pr_ref)
    cmd = ["gh", "pr", "view", str(pr_number), "--json", "headRefName,baseRefName,url"]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    head_branch = data["headRefName"]
    base_branch = data["baseRefName"]
    diff_ref = f"{repo}#{pr_number}" if repo else f"PR #{pr_number}"
    subprocess.run(
        ["git", "fetch", "origin", head_branch], cwd=cwd, capture_output=True, text=True
    )
    check = subprocess.run(
        ["git", "rev-parse", "--verify", head_branch], cwd=cwd, capture_output=True
    )
    if check.returncode == 0:
        subprocess.run(["git", "checkout", head_branch], cwd=cwd, capture_output=True)
        subprocess.run(
            ["git", "pull", "--ff-only", "origin", head_branch],
            cwd=cwd,
            capture_output=True,
        )
    else:
        subprocess.run(
            ["git", "checkout", "-b", head_branch, f"origin/{head_branch}"],
            cwd=cwd,
            capture_output=True,
        )
    return head_branch, base_branch, diff_ref


def get_github_issue(issue_ref: str) -> str:
    import subprocess

    repo, issue_number = _parse_ref(issue_ref)
    cmd = ["gh", "issue", "view", str(issue_number), "--json", "title,body"]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    return f"## {data.get('title', '')}\n\n{data.get('body', '') or ''}"


def post_pr_comment(pr_ref: str, body: str) -> None:
    import subprocess

    repo, pr_number = _parse_ref(pr_ref)
    cmd = ["gh", "pr", "comment", str(pr_number), "--body", body]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr comment failed: {result.stderr.strip()}")


async def _gather_coverage_lookups(source_files, repo_path):
    from .observations import coverage_map_tests

    results = {}
    tasks = []
    for sf in source_files:
        tasks.append((sf, asyncio.ensure_future(coverage_map_tests(sf, repo_path))))
    for sf, task in tasks:
        try:
            result = await task
            if not result.get("error"):
                results[f"coverage:{sf}"] = result
        except Exception:
            pass
    return results


def cmd_review(args) -> None:
    branch = getattr(args, "branch", None)
    base = getattr(args, "base", "main")
    pr = getattr(args, "pr", None)
    repo = getattr(args, "repo", ".")
    spec = getattr(args, "spec", None)
    models_arg = getattr(args, "model", None)
    output = getattr(args, "output", "full")
    output_dir = getattr(args, "output_dir", None)
    max_iterations = getattr(args, "max_iterations", 2)
    beliefs = getattr(args, "beliefs", None)
    issue = getattr(args, "issue", None)
    github_issue = getattr(args, "github_issue", None)
    comment = getattr(args, "comment", False)
    run_tests_flag = getattr(args, "run_tests", False)
    use_coverage_map = getattr(args, "coverage_map", False)
    reasons_db = getattr(args, "reasons_db", None)

    if comment and not pr:
        print("Error: --comment requires --pr", file=sys.stderr)
        sys.exit(1)

    models = list(models_arg) if models_arg else DEFAULT_MODELS

    if output_dir is None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if pr:
            branch_id = pr_output_dir_name(pr)
        else:
            branch_id = (branch or "staged").replace("/", "-")
        output_dir = os.path.join("reviews", branch_id, timestamp)
        print(f"Saving review to {output_dir}/", file=sys.stderr)

    missing = preflight_check(models)
    if missing:
        print(format_preflight_error(missing), file=sys.stderr)
        sys.exit(1)

    try:
        if pr and repo != ".":
            print(f"Fetching PR branch into {repo}...", file=sys.stderr)
            head_branch, base_branch, diff_ref = fetch_pr_locally(pr, repo)
            diff_content = get_diff(head_branch, base_branch, cwd=repo)
        elif pr:
            diff_content, diff_ref, _ = get_pr_diff(pr)
        elif branch:
            diff_ref = branch
            diff_content = get_diff(branch, base, cwd=repo)
        else:
            diff_ref = "staged"
            diff_content = get_diff(cwd=repo)
    except (RuntimeError, ValueError) as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if not diff_content.strip():
        print("No changes to review.", file=sys.stderr)
        sys.exit(0)

    spec_content = None
    if spec:
        spec_content = read_file_content(spec)
        if spec_content is None:
            print(f"Warning: Spec file not found: {spec}", file=sys.stderr)

    beliefs_content = None
    if beliefs:
        raw_beliefs = read_file_content(beliefs)
        if raw_beliefs:
            beliefs_content, kept, total = filter_beliefs(raw_beliefs, diff_content)
            print(
                f"Loaded beliefs from {beliefs} ({kept}/{total} relevant)",
                file=sys.stderr,
            )
        else:
            print(f"Warning: Could not read beliefs file: {beliefs}", file=sys.stderr)

    issue_content = None
    if github_issue:
        try:
            issue_content = get_github_issue(github_issue)
            print(f"Fetched GitHub issue {github_issue}", file=sys.stderr)
        except (RuntimeError, ValueError) as e:
            print(f"Warning: Could not fetch GitHub issue: {e}", file=sys.stderr)
    elif issue:
        issue_content = read_file_content(issue)
        if issue_content:
            print(f"Loaded issue from {issue}", file=sys.stderr)
        else:
            print(f"Warning: Could not read issue file: {issue}", file=sys.stderr)

    observe_model = models[0]
    all_observations: dict = {}
    os.makedirs(output_dir, exist_ok=True)

    changed_files = extract_changed_files(diff_content)
    lang = detect_language(repo)
    source_files = [
        f
        for f in changed_files
        if lang.matches_extension(f) and not lang.is_test_file(f)
    ]

    if use_coverage_map and source_files:
        import subprocess as _sp

        cov_json = os.path.join(repo, "coverage-map.json")
        needs_collect = not os.path.exists(cov_json)
        if not needs_collect:
            cov_mtime = os.path.getmtime(cov_json)
            for src in source_files:
                src_path = os.path.join(repo, src)
                if os.path.exists(src_path) and os.path.getmtime(src_path) > cov_mtime:
                    needs_collect = True
                    break
        if needs_collect:
            print("Running coverage-map collect...", file=sys.stderr)
            try:
                proc = _sp.run(
                    ["coverage-map", "collect"],
                    capture_output=True,
                    text=True,
                    cwd=repo,
                    timeout=300,
                )
                if proc.returncode == 0:
                    print("coverage-map collect completed", file=sys.stderr)
                else:
                    print(
                        f"WARNING: coverage-map collect failed: {proc.stderr[:200]}",
                        file=sys.stderr,
                    )
            except FileNotFoundError:
                print("WARNING: coverage-map not installed, skipping", file=sys.stderr)
            except _sp.TimeoutExpired:
                print("WARNING: coverage-map collect timed out", file=sys.stderr)

    if source_files:
        print(
            f"Auto-lookup: {len(source_files)} {lang.name} file(s) changed",
            file=sys.stderr,
        )
        coverage_obs = asyncio.run(_gather_coverage_lookups(source_files, repo))
        for obs_name, result in coverage_obs.items():
            all_observations[obs_name] = result
            print(
                f"  {result.get('source_file', obs_name)}: {result.get('test_count', 0)} tests",
                file=sys.stderr,
            )
        if all_observations:
            with open(os.path.join(output_dir, "00-auto-coverage.json"), "w") as f:
                json.dump(all_observations, f, indent=2, default=str)

    if repo != ".":
        from .observations import gather_function_context

        print("Auto-gathering modified function bodies...", file=sys.stderr)
        fn_context = asyncio.run(gather_function_context(diff_content, repo))
        if fn_context:
            all_observations.update(fn_context)
            print(
                f"Auto-gathered {len(fn_context)} function body(ies)", file=sys.stderr
            )
            with open(os.path.join(output_dir, "00-auto-functions.json"), "w") as f:
                json.dump(fn_context, f, indent=2, default=str)

    if repo != ".":
        from .observations import gather_related_test_files

        print("Auto-discovering related test files...", file=sys.stderr)
        test_context = asyncio.run(gather_related_test_files(diff_content, repo))
        test_files = test_context.get("test_files", [])
        if test_files:
            for tf in test_files:
                obs_key = f"related_test:{tf['path']}"
                all_observations[obs_key] = {
                    "path": tf["path"],
                    "covers": tf["covers"],
                    "symbols_referenced": tf["symbols_referenced"],
                    "content": tf["content"],
                    "truncated": tf.get("truncated", False),
                    "line_count": tf["line_count"],
                }
            print(
                f"Auto-discovered {len(test_files)} related test file(s)",
                file=sys.stderr,
            )
            with open(os.path.join(output_dir, "00-auto-tests.json"), "w") as f:
                json.dump(test_context, f, indent=2, default=str)
        else:
            print("No related test files found.", file=sys.stderr)

    if beliefs_content and repo != ".":
        import re as _re

        ext_pattern = "|".join(_re.escape(ext) for ext in lang.source_extensions)
        belief_file_refs = _re.findall(
            r"[\w/]+(?:" + ext_pattern + r")", beliefs_content
        )
        belief_file_refs = sorted(set(belief_file_refs))
        if belief_file_refs:
            from .observations import file_imports

            print(
                f"Auto-extracting imports from {len(belief_file_refs)} belief-referenced file(s)...",
                file=sys.stderr,
            )
            for ref_path in belief_file_refs:
                full_ref = Path(repo) / ref_path
                if full_ref.exists():
                    obs_key = f"belief_ref:{ref_path}"
                    ref_result = asyncio.run(file_imports(ref_path, repo_path=repo))
                    all_observations[obs_key] = ref_result

    abs_repo = os.path.abspath(repo)
    from .reasons import (
        find_reasons_db,
        query_beliefs_for_files,
        query_gated_beliefs,
        format_beliefs_for_prompt,
    )

    resolved_db = find_reasons_db(abs_repo, reasons_db)
    if resolved_db:
        print(f"Querying beliefs from {resolved_db}...", file=sys.stderr)
        belief_result = query_beliefs_for_files(changed_files, resolved_db)
        gated_result = query_gated_beliefs(changed_files, resolved_db)
        matched_beliefs = belief_result["beliefs"]
        gated_beliefs = gated_result["gated"]
        if matched_beliefs or gated_beliefs:
            reasons_beliefs_content = format_beliefs_for_prompt(
                matched_beliefs, gated_beliefs
            )
            if beliefs_content:
                beliefs_content = beliefs_content + "\n\n" + reasons_beliefs_content
            else:
                beliefs_content = reasons_beliefs_content
            in_count = sum(1 for b in matched_beliefs if b["truth_value"] == "IN")
            out_count = sum(1 for b in matched_beliefs if b["truth_value"] == "OUT")
            print(
                f"Found {len(matched_beliefs)} beliefs ({in_count} IN, {out_count} OUT) from {belief_result['total_in_db']} total",
                file=sys.stderr,
            )
            if gated_beliefs:
                print(
                    f"Found {len(gated_beliefs)} gated belief(s) relevant to changed files",
                    file=sys.stderr,
                )
            with open(os.path.join(output_dir, "00-reasons-beliefs.json"), "w") as f:
                json.dump(
                    {"beliefs": matched_beliefs, "gated": gated_beliefs},
                    f,
                    indent=2,
                    default=str,
                )
        else:
            print("No relevant beliefs found in reasons.db.", file=sys.stderr)

    if run_tests_flag and source_files:
        from .observations import format_test_results, run_tests_for_files

        print("Running tests on related test files...", file=sys.stderr)
        test_results = asyncio.run(run_tests_for_files(source_files, repo))
        status = test_results["status"]
        if status != "SKIPPED":
            passed = test_results["passed"]
            failed = test_results["failed"]
            errors = test_results["errors"]
            duration = test_results["duration_seconds"]
            print(
                f"Tests: {passed} passed, {failed} failed, {errors} errors ({duration}s) — {status}",
                file=sys.stderr,
            )
            all_observations["test_execution"] = test_results
            all_observations["test_execution_summary"] = format_test_results(
                test_results
            )
            with open(os.path.join(output_dir, "00-auto-test-results.json"), "w") as f:
                json.dump(test_results, f, indent=2, default=str)
        else:
            print(f"Tests skipped: {test_results['output']}", file=sys.stderr)

    reviews = None
    for iteration in range(1, max_iterations + 1):
        print(f"\n=== Iteration {iteration}/{max_iterations} ===", file=sys.stderr)
        iter_prefix = f"{iteration:02d}"

        print(f"Gathering observations with {observe_model}...", file=sys.stderr)
        observe_prompt = build_observe_prompt(diff_content)
        with open(
            os.path.join(output_dir, f"{iter_prefix}-observe-prompt.txt"), "w"
        ) as f:
            f.write(observe_prompt)

        observe_response = asyncio.run(run_model(observe_model, observe_prompt))
        with open(
            os.path.join(output_dir, f"{iter_prefix}-observe-response.txt"), "w"
        ) as f:
            f.write(observe_response)

        requested_obs = parse_observe_response(observe_response)
        if requested_obs:
            print(f"Requested {len(requested_obs)} observation(s)", file=sys.stderr)
            from .observations import run_observations

            new_obs = asyncio.run(run_observations(requested_obs, repo))
            all_observations.update(new_obs)
            failed_obs = [
                (name, result)
                for name, result in new_obs.items()
                if isinstance(result, dict) and "error" in result
            ]
            if failed_obs:
                print(
                    f"WARNING: {len(failed_obs)} observation(s) failed", file=sys.stderr
                )
            with open(
                os.path.join(output_dir, f"{iter_prefix}-observations.json"), "w"
            ) as f:
                json.dump(new_obs, f, indent=2, default=str)
            succeeded = len(new_obs) - len(failed_obs)
            print(
                f"Total observations: {len(all_observations)} ({succeeded} succeeded, {len(failed_obs)} failed)",
                file=sys.stderr,
            )
        else:
            print("No new observations requested.", file=sys.stderr)

        print(f"Running review with {', '.join(models)}...", file=sys.stderr)
        review_prompt = build_review_prompt(
            diff_content,
            spec_content,
            observations=all_observations if all_observations else None,
            beliefs_content=beliefs_content,
            issue_content=issue_content,
        )
        with open(
            os.path.join(output_dir, f"{iter_prefix}-review-prompt.txt"), "w"
        ) as f:
            f.write(review_prompt)

        reviews = asyncio.run(
            review_with_models(
                models,
                review_prompt,
                observations=all_observations if all_observations else None,
            )
        )

        for model_review in reviews:
            with open(
                os.path.join(
                    output_dir, f"{iter_prefix}-{model_review.model}-response.txt"
                ),
                "w",
            ) as f:
                f.write(model_review.raw_response)

        more_obs_requested = []
        for review_result in reviews:
            obs_requests = parse_observations(review_result.raw_response)
            if obs_requests:
                more_obs_requested.extend(obs_requests)

        if not more_obs_requested:
            print(
                "No additional observations requested. Review complete.",
                file=sys.stderr,
            )
            break

        if iteration < max_iterations:
            print(
                f"Models requested {len(more_obs_requested)} more observation(s), continuing...",
                file=sys.stderr,
            )
        else:
            print("Max iterations reached. Finalizing review.", file=sys.stderr)

    if reviews is None:
        print("Error: No review iterations completed.", file=sys.stderr)
        sys.exit(1)

    result = aggregate_reviews(diff_ref, reviews, spec)

    if output == "full":
        report = format_aggregate_review(result)
    else:
        report = format_summary(result)

    report_path = os.path.join(output_dir, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"Saved report to {report_path}", file=sys.stderr)

    if all_observations:
        obs_path = os.path.join(output_dir, "observations.json")
        with open(obs_path, "w", encoding="utf-8") as f:
            json.dump(all_observations, f, indent=2, default=str)
        print(f"Saved observations to {obs_path}", file=sys.stderr)

    print(report)

    if comment:
        try:
            post_pr_comment(pr, report)
            print(f"Posted review as comment on {diff_ref}", file=sys.stderr)
        except RuntimeError as e:
            print(f"Error posting comment: {e}", file=sys.stderr)

    gate_code = {Verdict.PASS: 0, Verdict.CONCERN: 1, Verdict.BLOCK: 2}
    sys.exit(gate_code.get(result.gate, 1))
