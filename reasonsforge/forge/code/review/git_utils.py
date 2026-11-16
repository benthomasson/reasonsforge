"""Git utilities for code review (PR handling, diff parsing)."""

from __future__ import annotations

import json
import re
import subprocess


def get_diff(
    ref: str | None = None,
    base: str | None = None,
    cwd: str | None = None,
    context_lines: int = 10,
) -> str:
    context_arg = f"-U{context_lines}"
    if ref is None:
        cmd = ["git", "diff", "--staged", context_arg]
    else:
        if base is None:
            check = subprocess.run(
                ["git", "rev-parse", "--verify", "origin/main"],
                capture_output=True, cwd=cwd,
            )
            base = "origin/main" if check.returncode == 0 else "main"
        cmd = ["git", "diff", context_arg, f"{base}...{ref}"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if result.returncode != 0:
        raise RuntimeError(f"Git diff failed: {result.stderr}")
    return result.stdout


def read_file_content(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return None


def parse_pr_url(pr_ref: str) -> tuple[str | None, int]:
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", pr_ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"([^/]+/[^#]+)#(\d+)", pr_ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"(\d+)$", pr_ref)
    if m:
        return None, int(m.group(1))
    raise ValueError(f"Cannot parse PR reference: {pr_ref}")


def get_pr_diff(pr_ref: str) -> tuple[str, str, str]:
    repo, pr_number = parse_pr_url(pr_ref)
    cmd = ["gh", "pr", "diff", str(pr_number)]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr diff failed: {result.stderr.strip()}")
    if repo:
        diff_ref = f"{repo}#{pr_number}"
    else:
        info_cmd = ["gh", "pr", "view", str(pr_number), "--json", "url"]
        info_result = subprocess.run(info_cmd, capture_output=True, text=True)
        if info_result.returncode == 0:
            url = json.loads(info_result.stdout).get("url", "")
            m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", url)
            diff_ref = f"{m.group(1)}#{pr_number}" if m else f"PR #{pr_number}"
        else:
            diff_ref = f"PR #{pr_number}"
    return result.stdout, diff_ref, repo or ""


def fetch_pr_locally(pr_ref: str, cwd: str) -> tuple[str, str, str]:
    repo, pr_number = parse_pr_url(pr_ref)
    cmd = ["gh", "pr", "view", str(pr_number), "--json", "headRefName,baseRefName,url"]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr view failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    head_branch = data["headRefName"]
    base_branch = data["baseRefName"]
    if repo:
        diff_ref = f"{repo}#{pr_number}"
    else:
        url = data.get("url", "")
        m = re.match(r"https?://github\.com/([^/]+/[^/]+)/pull/\d+", url)
        diff_ref = f"{m.group(1)}#{pr_number}" if m else f"PR #{pr_number}"
    subprocess.run(["git", "fetch", "origin", head_branch], cwd=cwd, capture_output=True, text=True)
    check = subprocess.run(["git", "rev-parse", "--verify", head_branch], cwd=cwd, capture_output=True)
    if check.returncode == 0:
        subprocess.run(["git", "checkout", head_branch], cwd=cwd, capture_output=True)
        subprocess.run(["git", "pull", "--ff-only", "origin", head_branch], cwd=cwd, capture_output=True)
    else:
        subprocess.run(["git", "checkout", "-b", head_branch, f"origin/{head_branch}"], cwd=cwd, capture_output=True)
    return head_branch, base_branch, diff_ref


def pr_output_dir_name(pr_ref: str) -> str:
    repo, pr_number = parse_pr_url(pr_ref)
    if repo:
        return f"{repo.replace('/', '-')}-{pr_number}"
    return f"pr-{pr_number}"


def post_pr_comment(pr_ref: str, body: str) -> None:
    repo, pr_number = parse_pr_url(pr_ref)
    cmd = ["gh", "pr", "comment", str(pr_number), "--body", body]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh pr comment failed: {result.stderr.strip()}")


def parse_issue_ref(issue_ref: str) -> tuple[str | None, int]:
    m = re.match(r"https?://github\.com/([^/]+/[^/]+)/issues/(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"([^/]+/[^#]+)#(\d+)", issue_ref)
    if m:
        return m.group(1), int(m.group(2))
    m = re.match(r"(\d+)$", issue_ref)
    if m:
        return None, int(m.group(1))
    raise ValueError(f"Cannot parse issue reference: {issue_ref}")


def get_github_issue(issue_ref: str) -> str:
    repo, issue_number = parse_issue_ref(issue_ref)
    cmd = ["gh", "issue", "view", str(issue_number), "--json", "title,body"]
    if repo:
        cmd.extend(["--repo", repo])
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"gh issue view failed: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    title = data.get("title", "")
    body = data.get("body", "") or ""
    return f"## {title}\n\n{body}"


def extract_changed_files(diff_content: str) -> list[str]:
    files = []
    for line in diff_content.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            if path != "/dev/null":
                files.append(path)
    return files


def extract_modified_line_ranges(diff_content: str) -> dict[str, list[tuple[int, int]]]:
    result: dict[str, list[tuple[int, int]]] = {}
    current_file: str | None = None
    for line in diff_content.split("\n"):
        if line.startswith("+++ b/"):
            path = line[6:]
            current_file = path if path != "/dev/null" else None
            continue
        if current_file and line.startswith("@@"):
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                start = int(m.group(1))
                count = int(m.group(2)) if m.group(2) else 1
                if count > 0:
                    end = start + count - 1
                    result.setdefault(current_file, []).append((start, end))
    return result
