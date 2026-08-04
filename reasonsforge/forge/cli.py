"""Forge CLI subcommands for reasonsforge."""

import importlib
import sys


def _lazy(module_name, func_name):
    mod = importlib.import_module(f".{module_name}", package="reasonsforge.forge")
    return getattr(mod, func_name)


def _add_common_pipeline_args(p):
    """Add args shared by all forge type commands."""
    p.add_argument("--model", default="claude", help="LLM model to use")
    p.add_argument("--rounds", type=int, default=3,
                   help="Convergence loop cycles")
    p.add_argument("--max-derive-rounds", type=int, default=10,
                   help="Max derive rounds per cycle")
    p.add_argument("--timeout", type=int, default=600, help="LLM timeout (s)")
    p.add_argument("--output", default="reasons.db",
                   help="Output database path")
    p.add_argument("--no-auto-accept", action="store_true",
                   help="Pause after proposing beliefs for manual review")
    p.add_argument("--resume", action="store_true",
                   help="Resume a previously interrupted pipeline")
    p.add_argument("--parallel", type=int, default=1,
                   help="Parallel LLM calls")


def register_forge_type_commands(parent_subparsers):
    """Register top-level forge type commands (code, product, project, paper, document).

    Returns a dict mapping command name → handler function.
    """

    # document — general document/PDF ingestion (the base forge)
    p = parent_subparsers.add_parser(
        "document",
        help="Build beliefs from documents (PDFs, markdown, code files)")
    p.add_argument("--sources-dir", default="sources",
                   help="Directory containing source documents")
    p.add_argument("--pdf", action="append",
                   help="PDF files to ingest (repeatable)")
    p.add_argument("--domain", help="Domain description for derive context")
    p.add_argument("--recursive", "-r", action="store_true",
                   help="Recurse into subdirectories")
    p.add_argument("--namespace", default=None)
    _add_common_pipeline_args(p)

    # code — codebase analysis
    p = parent_subparsers.add_parser(
        "code",
        help="Analyze a codebase and extract architectural beliefs")
    p.add_argument("--repo", default=".", help="Path to git repository")
    p.add_argument("--domain", help="Domain description")
    p.add_argument("--since", help="Analyze commits since this date or SHA")
    p.add_argument("--limit", type=int, default=500,
                   help="Max files to explore (0 = unlimited)")
    _add_common_pipeline_args(p)

    code_sub = p.add_subparsers(dest="code_command")

    # code scan
    cs = code_sub.add_parser("scan", help="Scan repo structure and populate topic queue")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)

    # code explore
    cs = code_sub.add_parser("explore", help="Explore next topic(s) in queue")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--loop", type=int, default=None, help="Explore up to N topics")
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--parallel", type=int, default=1)

    # code explain
    cs = code_sub.add_parser("explain", help="Explain a file, function, or diff")
    cs.add_argument("kind", choices=["file", "function", "diff"],
                    help="What to explain")
    cs.add_argument("target", nargs="?", help="File path or file:symbol")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--branch", default=None)
    cs.add_argument("--base", default="main")
    cs.add_argument("--since", default=None)
    cs.add_argument("--since-last", action="store_true", dest="since_last")
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--output", default="reasons.db")

    # code walk-commits
    cs = code_sub.add_parser("walk-commits",
                             help="Walk recent commits and explore changed files")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--since", default=None)
    cs.add_argument("--since-commit", default=None)
    cs.add_argument("--since-last", action="store_true", dest="since_last")
    cs.add_argument("--dry-run", action="store_true", dest="dry_run")
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--parallel", type=int, default=1)
    cs.add_argument("--output", default="reasons.db")

    # code propose-beliefs
    cs = code_sub.add_parser("propose-beliefs",
                             help="Extract candidate beliefs from entries")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--batch-size", type=int, default=5)
    cs.add_argument("--proposals-output", default="proposed-beliefs.md")
    cs.add_argument("--all", action="store_true")
    cs.add_argument("--auto", action="store_true")
    cs.add_argument("--since", default=None)
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--parallel", type=int, default=1)
    cs.add_argument("--output", default="reasons.db")

    # code accept-beliefs
    cs = code_sub.add_parser("accept-beliefs",
                             help="Import accepted beliefs from proposals file")
    cs.add_argument("--proposals-file", default="proposed-beliefs.md")
    cs.add_argument("--output", default="reasons.db")

    # code review-proposals
    cs = code_sub.add_parser("review-proposals",
                             help="Filter low-quality proposals using LLM review")
    cs.add_argument("--proposals-file", default="proposed-beliefs.md")
    cs.add_argument("--batch-size", type=int, default=20)
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--output", default="reasons.db")

    # code verify
    cs = code_sub.add_parser("verify",
                             help="Check beliefs against current source code")
    cs.add_argument("belief_ids", nargs="*", default=[])
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--category", default=None)
    cs.add_argument("--all", action="store_true")
    cs.add_argument("--retract", action="store_true")
    cs.add_argument("--dry-run", action="store_true", dest="dry_run")
    cs.add_argument("--batch-size", type=int, default=10)
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--output", default="reasons.db")

    # code research
    cs = code_sub.add_parser("research",
                             help="Evidence-driven exploration from belief review gaps")
    cs.add_argument("--review-file", required=True, dest="review_file",
                    help="Path to review JSON file (from review-beliefs)")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--limit", type=int, default=None,
                    help="Max candidates to research")
    cs.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Show candidates and inferred files without exploring")
    cs.add_argument("--parallel", type=int, default=1)
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--output", default="reasons.db")

    # code derive
    cs = code_sub.add_parser("derive",
                             help="Derive reasoning chains from existing beliefs")
    cs.add_argument("--auto", action="store_true")
    cs.add_argument("--exhaust", action="store_true")
    cs.add_argument("--max-derive-rounds", type=int, default=10)
    cs.add_argument("--budget", type=int, default=300)
    cs.add_argument("--domain", default=None)
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--output", default="reasons.db")

    # code topics
    cs = code_sub.add_parser("topics", help="Show exploration queue")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--all", action="store_true")

    # code status
    cs = code_sub.add_parser("status", help="Show code forge dashboard")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--output", default="reasons.db")

    # code update
    cs = code_sub.add_parser("update",
                             help="Incremental update: walk-commits + propose + derive")
    cs.add_argument("--repo", default=".", help="Path to git repository")
    cs.add_argument("--since", default=None)
    cs.add_argument("--since-commit", default=None)
    cs.add_argument("--since-last", action="store_true", dest="since_last")
    cs.add_argument("--model", default="claude")
    cs.add_argument("--timeout", type=int, default=600)
    cs.add_argument("--parallel", type=int, default=1)
    cs.add_argument("--output", default="reasons.db")
    cs.add_argument("--rounds", type=int, default=3)
    cs.add_argument("--max-derive-rounds", type=int, default=10)

    # product — product data from issue trackers
    p = parent_subparsers.add_parser(
        "product",
        help="Analyze product data from issue trackers")
    p.add_argument("--github", metavar="OWNER/REPO",
                   help="GitHub repository")
    p.add_argument("--gitlab", metavar="OWNER/REPO",
                   help="GitLab repository")
    p.add_argument("--jira", metavar="PROJECT_KEY",
                   help="Jira project key")
    p.add_argument("--jira-url", help="Jira base URL")
    p.add_argument("--domain", help="Domain description")
    p.add_argument("--since", help="Analyze issues since this date")
    _add_common_pipeline_args(p)

    product_sub = p.add_subparsers(dest="product_command")

    # product init
    ps = product_sub.add_parser("init", help="Initialize product forge")
    ps.add_argument("--github", metavar="OWNER/REPO")
    ps.add_argument("--gitlab", metavar="OWNER/REPO")
    ps.add_argument("--jira", metavar="PROJECT_KEY")
    ps.add_argument("--jira-url", help="Jira base URL")
    ps.add_argument("--domain", help="Domain description")
    ps.add_argument("--output", default="reasons.db")

    # product scan
    ps = product_sub.add_parser("scan", help="Scan issues")
    ps.add_argument("--state", default="open",
                    choices=["open", "closed", "all"])
    ps.add_argument("--labels", default=None)
    ps.add_argument("--limit", type=int, default=100)
    ps.add_argument("--page", type=int, default=1)
    ps.add_argument("--all-pages", action="store_true", dest="all_pages")
    ps.add_argument("--jql", default=None)
    ps.add_argument("--since", default=None)
    ps.add_argument("--since-last", action="store_true", dest="since_last")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # product ingest
    ps = product_sub.add_parser("ingest",
                                help="Ingest markdown documents for product analysis")
    ps.add_argument("docs_dir", help="Directory containing documents")
    ps.add_argument("--glob-pattern", default="**/*.md", dest="glob_pattern")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)

    # product explore
    ps = product_sub.add_parser("explore", help="Explore topics in queue")
    ps.add_argument("--skip", type=int, default=None)
    ps.add_argument("--pick", type=int, nargs="*", default=None)
    ps.add_argument("--loop", type=int, default=None)
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)

    # product propose-beliefs
    ps = product_sub.add_parser("propose-beliefs",
                                help="Extract candidate beliefs from entries")
    ps.add_argument("--batch-size", type=int, default=5)
    ps.add_argument("--proposals-output", default="proposed-beliefs.md")
    ps.add_argument("--all", action="store_true")
    ps.add_argument("--auto", action="store_true")
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # product accept-beliefs
    ps = product_sub.add_parser("accept-beliefs",
                                help="Import accepted beliefs from proposals")
    ps.add_argument("--proposals-file", default="proposed-beliefs.md")
    ps.add_argument("--output", default="reasons.db")

    # product review-proposals
    ps = product_sub.add_parser("review-proposals",
                                help="Filter low-quality proposals using LLM review")
    ps.add_argument("--proposals-file", default="proposed-beliefs.md")
    ps.add_argument("--batch-size", type=int, default=20)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # product derive
    ps = product_sub.add_parser("derive",
                                help="Derive reasoning chains from existing beliefs")
    ps.add_argument("--auto", action="store_true")
    ps.add_argument("--exhaust", action="store_true")
    ps.add_argument("--max-derive-rounds", type=int, default=10)
    ps.add_argument("--budget", type=int, default=300)
    ps.add_argument("--domain", default=None)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # product generate-summary
    ps = product_sub.add_parser("generate-summary",
                                help="Programmatic summary of belief state (no LLM)")
    ps.add_argument("--output", default="reasons.db")

    # product summary
    ps = product_sub.add_parser("summary",
                                help="LLM-synthesized product summary from beliefs")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # product topics
    ps = product_sub.add_parser("topics", help="Show exploration queue")
    ps.add_argument("--all", action="store_true")

    # product status
    ps = product_sub.add_parser("status", help="Show product forge dashboard")
    ps.add_argument("--output", default="reasons.db")

    # product update
    ps = product_sub.add_parser("update",
                                help="Incremental update pipeline")
    ps.add_argument("--since", default=None)
    ps.add_argument("--since-last", action="store_true", dest="since_last")
    ps.add_argument("--limit", type=int, default=100)
    ps.add_argument("--all-pages", action="store_true", dest="all_pages")
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project — project management from issue trackers
    p = parent_subparsers.add_parser(
        "project",
        help="Analyze project state from issue trackers")
    p.add_argument("--github", metavar="OWNER/REPO",
                   help="GitHub repository")
    p.add_argument("--gitlab", metavar="OWNER/REPO",
                   help="GitLab repository")
    p.add_argument("--jira", metavar="PROJECT_KEY",
                   help="Jira project key")
    p.add_argument("--jira-url", help="Jira base URL")
    p.add_argument("--domain", help="Domain description")
    p.add_argument("--since", help="Analyze issues since this date")
    _add_common_pipeline_args(p)

    project_sub = p.add_subparsers(dest="project_command")

    # project init
    ps = project_sub.add_parser("init", help="Initialize project forge")
    ps.add_argument("--github", metavar="OWNER/REPO")
    ps.add_argument("--gitlab", metavar="OWNER/REPO")
    ps.add_argument("--jira", metavar="PROJECT_KEY")
    ps.add_argument("--jira-url", help="Jira base URL")
    ps.add_argument("--domain", help="Domain description")
    ps.add_argument("--output", default="reasons.db")

    # project scan
    ps = project_sub.add_parser("scan", help="Scan issues/PRs")
    ps.add_argument("--state", default="open",
                    choices=["open", "closed", "all"])
    ps.add_argument("--labels", default=None)
    ps.add_argument("--limit", type=int, default=100)
    ps.add_argument("--page", type=int, default=1)
    ps.add_argument("--all-pages", action="store_true", dest="all_pages")
    ps.add_argument("--jql", default=None)
    ps.add_argument("--per-issue", action="store_true", dest="per_issue")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project explore
    ps = project_sub.add_parser("explore", help="Explore topics in queue")
    ps.add_argument("--skip", type=int, default=None)
    ps.add_argument("--pick", type=int, nargs="*", default=None)
    ps.add_argument("--loop", type=int, default=None)
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)

    # project propose-beliefs
    ps = project_sub.add_parser("propose-beliefs",
                                help="Extract candidate beliefs from entries")
    ps.add_argument("--batch-size", type=int, default=5)
    ps.add_argument("--proposals-output", default="proposed-beliefs.md")
    ps.add_argument("--all", action="store_true")
    ps.add_argument("--auto", action="store_true")
    ps.add_argument("--since", default=None)
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project accept-beliefs
    ps = project_sub.add_parser("accept-beliefs",
                                help="Import accepted beliefs from proposals")
    ps.add_argument("--proposals-file", default="proposed-beliefs.md")
    ps.add_argument("--output", default="reasons.db")

    # project review-proposals
    ps = project_sub.add_parser("review-proposals",
                                help="Filter low-quality proposals using LLM review")
    ps.add_argument("--proposals-file", default="proposed-beliefs.md")
    ps.add_argument("--batch-size", type=int, default=20)
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project research
    ps = project_sub.add_parser("research",
                                help="Verify beliefs against live tracker data")
    ps.add_argument("belief_id", nargs="?", default=None)
    ps.add_argument("--negative", action="store_true")
    ps.add_argument("--high-impact", action="store_true", dest="high_impact")
    ps.add_argument("--limit", type=int, default=5)
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project derive
    ps = project_sub.add_parser("derive",
                                help="Derive reasoning chains from existing beliefs")
    ps.add_argument("--auto", action="store_true")
    ps.add_argument("--exhaust", action="store_true")
    ps.add_argument("--max-derive-rounds", type=int, default=10)
    ps.add_argument("--budget", type=int, default=300)
    ps.add_argument("--domain", default=None)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project review-beliefs
    ps = project_sub.add_parser("review-beliefs",
                                help="Review derived beliefs for quality")
    ps.add_argument("--auto-retract", action="store_true", dest="auto_retract")
    ps.add_argument("--sample", type=int, default=None)
    ps.add_argument("--min-depth", type=int, default=None, dest="min_depth")
    ps.add_argument("--dry-run", action="store_true", dest="dry_run")
    ps.add_argument("--review-output", default=None, dest="review_output")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project repair
    ps = project_sub.add_parser("repair",
                                help="Repair flagged beliefs")
    ps.add_argument("--review-file", default=None, dest="review_file")
    ps.add_argument("--dry-run", action="store_true", dest="dry_run")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project summary
    ps = project_sub.add_parser("summary",
                                help="Synthesize project summary from beliefs")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project sprint-plan
    ps = project_sub.add_parser("sprint-plan",
                                help="Generate prioritized sprint plan")
    ps.add_argument("--sprint-length", default="2w", dest="sprint_length")
    ps.add_argument("--team-size", type=int, default=None, dest="team_size")
    ps.add_argument("--dry-run", action="store_true", dest="dry_run")
    ps.add_argument("--sprint-output", default=None, dest="sprint_output")
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # project topics
    ps = project_sub.add_parser("topics", help="Show exploration queue")
    ps.add_argument("--all", action="store_true")

    # project status
    ps = project_sub.add_parser("status", help="Show project forge dashboard")
    ps.add_argument("--output", default="reasons.db")

    # project update
    ps = project_sub.add_parser("update",
                                help="Incremental update pipeline")
    ps.add_argument("--since", default=None)
    ps.add_argument("--since-last", action="store_true", dest="since_last")
    ps.add_argument("--state", default=None,
                    choices=["open", "closed", "all"])
    ps.add_argument("--limit", type=int, default=100)
    ps.add_argument("--all-pages", action="store_true", dest="all_pages")
    ps.add_argument("--max-explore", type=int, default=None, dest="max_explore")
    ps.add_argument("--parallel", type=int, default=1)
    ps.add_argument("--model", default="claude")
    ps.add_argument("--timeout", type=int, default=300)
    ps.add_argument("--output", default="reasons.db")

    # meta — cross-domain reasoning
    p = parent_subparsers.add_parser(
        "meta",
        help="Cross-domain reasoning across expert belief networks")
    p.add_argument("--domain", help="Domain description")
    _add_common_pipeline_args(p)

    meta_sub = p.add_subparsers(dest="meta_command")

    # meta init
    ms = meta_sub.add_parser("init", help="Initialize meta forge with expert repos")
    ms.add_argument("experts", nargs="+", metavar="NAME=PATH",
                    help="Expert repos as NAME=PATH pairs")
    ms.add_argument("--domain", help="Domain description")
    ms.add_argument("--output", default="reasons.db")

    # meta import
    ms = meta_sub.add_parser("import", help="Import beliefs from expert repos")
    ms.add_argument("--expert", default=None,
                    help="Import from a single expert only")
    ms.add_argument("--only-in", action="store_true", dest="only_in",
                    help="Only import IN beliefs")
    ms.add_argument("--output", default="reasons.db")

    # meta derive
    ms = meta_sub.add_parser("derive",
                              help="Cross-domain derivation from combined beliefs")
    ms.add_argument("--auto", action="store_true")
    ms.add_argument("--exhaust", action="store_true")
    ms.add_argument("--dry-run", action="store_true", dest="dry_run")
    ms.add_argument("--budget", type=int, default=300)
    ms.add_argument("--seed", type=int, default=None)
    ms.add_argument("--model", default="claude")
    ms.add_argument("--timeout", type=int, default=600)
    ms.add_argument("--output", default="reasons.db")

    # meta ask
    ms = meta_sub.add_parser("ask", help="Ask a question across all expert domains")
    ms.add_argument("question", help="Question to answer")
    ms.add_argument("--model", default="claude")
    ms.add_argument("--timeout", type=int, default=300)
    ms.add_argument("--output", default="reasons.db")

    # meta contradictions
    ms = meta_sub.add_parser("contradictions",
                              help="Detect cross-domain contradictions")
    ms.add_argument("--auto", action="store_true")
    ms.add_argument("--model", default="claude")
    ms.add_argument("--timeout", type=int, default=600)
    ms.add_argument("--output", default="reasons.db")

    # meta summary
    ms = meta_sub.add_parser("summary",
                              help="Executive synthesis across all domains")
    ms.add_argument("--model", default="claude")
    ms.add_argument("--timeout", type=int, default=600)
    ms.add_argument("--output", default="reasons.db")

    # meta topics
    ms = meta_sub.add_parser("topics", help="Show investigation queue")
    ms.add_argument("--all", action="store_true")

    # meta status
    ms = meta_sub.add_parser("status", help="Show meta forge dashboard")
    ms.add_argument("--output", default="reasons.db")

    # meta update
    ms = meta_sub.add_parser("update",
                              help="Pipeline: import → derive → contradictions → summary")
    ms.add_argument("--skip", nargs="*", default=[],
                    help="Steps to skip (import, derive, contradictions, summary)")
    ms.add_argument("--budget", type=int, default=300)
    ms.add_argument("--seed", type=int, default=None)
    ms.add_argument("--model", default="claude")
    ms.add_argument("--timeout", type=int, default=600)
    ms.add_argument("--output", default="reasons.db")

    # run — sandbox wrapper (Phase 3)
    p = parent_subparsers.add_parser(
        "run",
        help="Run a forge in a sandbox environment")
    p.add_argument("--sandbox", default="none",
                   choices=["none", "container", "vm", "lightweight"],
                   help="Sandbox tier")
    p.add_argument("forge_type",
                   choices=["document", "code", "product", "project", "meta"],
                   help="Forge type to run")
    p.add_argument("forge_args", nargs="*",
                   help="Arguments passed to the forge")

    return {
        "document": _cmd_document,
        "code": _cmd_code,
        "product": _cmd_product,
        "project": _cmd_project,
        "meta": _cmd_meta,
        "run": _cmd_run,
    }


def _cmd_document(args):
    """Run the document forge pipeline."""
    from . import REASONS_DB
    import reasonsforge.forge as forge

    if args.output != REASONS_DB:
        forge.REASONS_DB = args.output

    from .pipeline import cmd_pipeline
    args.sources_dir = getattr(args, "sources_dir", "sources")
    args.index_db = "rag_fts.db"
    cmd_pipeline(args)


def _print_cost():
    """Print accumulated LLM cost summary if any calls were made."""
    try:
        from .llm import format_cost_summary
        cost = format_cost_summary()
        if cost:
            print(f"  {cost}", file=sys.stderr)
    except Exception:
        pass


def _cmd_code(args):
    """Run the code forge pipeline or dispatch to a subcommand."""
    from .code.commands import (
        cmd_accept_beliefs as _code_accept,
        cmd_derive as _code_derive,
        cmd_explore as _code_explore,
        cmd_explain_diff as _code_explain_diff,
        cmd_explain_file as _code_explain_file,
        cmd_explain_function as _code_explain_func,
        cmd_init as _code_init,
        cmd_propose_beliefs as _code_propose,
        cmd_research as _code_research,
        cmd_review_proposals as _code_review,
        cmd_scan as _code_scan,
        cmd_status as _code_status,
        cmd_topics as _code_topics,
        cmd_verify as _code_verify,
        cmd_walk_commits as _code_walk,
    )
    from .code.pipeline import cmd_analyze, cmd_update

    code_command = getattr(args, "code_command", None)

    _code_dispatch = {
        "scan": _code_scan,
        "explore": _code_explore,
        "walk-commits": _code_walk,
        "propose-beliefs": _code_propose,
        "accept-beliefs": _code_accept,
        "review-proposals": _code_review,
        "verify": _code_verify,
        "research": _code_research,
        "derive": _code_derive,
        "topics": _code_topics,
        "status": _code_status,
        "update": cmd_update,
    }

    try:
        if code_command == "explain":
            kind = getattr(args, "kind", "file")
            if kind == "file":
                _code_explain_file(args)
            elif kind == "function":
                _code_explain_func(args)
            elif kind == "diff":
                _code_explain_diff(args)
            return

        if code_command and code_command in _code_dispatch:
            _code_dispatch[code_command](args)
            return

        # No subcommand — run full analyze pipeline
        cmd_analyze(args)
    finally:
        _print_cost()


def _cmd_product(args):
    """Run the product forge pipeline or dispatch to a subcommand."""
    from .product.commands import (
        cmd_accept_beliefs as _prod_accept,
        cmd_derive as _prod_derive,
        cmd_explore as _prod_explore,
        cmd_generate_summary as _prod_gen_summary,
        cmd_ingest as _prod_ingest,
        cmd_init as _prod_init,
        cmd_propose_beliefs as _prod_propose,
        cmd_review_proposals as _prod_review,
        cmd_scan as _prod_scan,
        cmd_status as _prod_status,
        cmd_summary as _prod_summary,
        cmd_topics as _prod_topics,
        cmd_update as _prod_update,
    )
    from .product.pipeline import cmd_analyze

    product_command = getattr(args, "product_command", None)

    _prod_dispatch = {
        "init": _prod_init,
        "scan": _prod_scan,
        "ingest": _prod_ingest,
        "explore": _prod_explore,
        "propose-beliefs": _prod_propose,
        "accept-beliefs": _prod_accept,
        "review-proposals": _prod_review,
        "derive": _prod_derive,
        "generate-summary": _prod_gen_summary,
        "summary": _prod_summary,
        "topics": _prod_topics,
        "status": _prod_status,
        "update": _prod_update,
    }

    if product_command and product_command in _prod_dispatch:
        _prod_dispatch[product_command](args)
        return

    # No subcommand — run full analyze pipeline
    source = args.github or args.gitlab or args.jira
    if not source:
        print("Error: specify --github, --gitlab, or --jira", file=sys.stderr)
        sys.exit(1)
    cmd_analyze(args)


def _cmd_project(args):
    """Run the project forge pipeline or dispatch to a subcommand."""
    from .project.commands import (
        cmd_accept_beliefs as _proj_accept,
        cmd_derive as _proj_derive,
        cmd_explore as _proj_explore,
        cmd_init as _proj_init,
        cmd_propose_beliefs as _proj_propose,
        cmd_repair as _proj_repair,
        cmd_research as _proj_research,
        cmd_review_beliefs as _proj_review_beliefs,
        cmd_review_proposals as _proj_review,
        cmd_scan as _proj_scan,
        cmd_sprint_plan as _proj_sprint,
        cmd_status as _proj_status,
        cmd_summary as _proj_summary,
        cmd_topics as _proj_topics,
        cmd_update as _proj_update,
    )
    from .project.pipeline import cmd_analyze

    project_command = getattr(args, "project_command", None)

    _proj_dispatch = {
        "init": _proj_init,
        "scan": _proj_scan,
        "explore": _proj_explore,
        "propose-beliefs": _proj_propose,
        "accept-beliefs": _proj_accept,
        "review-proposals": _proj_review,
        "research": _proj_research,
        "derive": _proj_derive,
        "review-beliefs": _proj_review_beliefs,
        "repair": _proj_repair,
        "summary": _proj_summary,
        "sprint-plan": _proj_sprint,
        "topics": _proj_topics,
        "status": _proj_status,
        "update": _proj_update,
    }

    if project_command and project_command in _proj_dispatch:
        _proj_dispatch[project_command](args)
        return

    # No subcommand — run full analyze pipeline
    source = args.github or args.gitlab or args.jira
    if not source:
        print("Error: specify --github, --gitlab, or --jira", file=sys.stderr)
        sys.exit(1)
    cmd_analyze(args)


def _cmd_meta(args):
    """Run the meta forge pipeline or dispatch to a subcommand."""
    from .meta.commands import (
        cmd_ask as _meta_ask,
        cmd_contradictions as _meta_contradictions,
        cmd_derive as _meta_derive,
        cmd_import_beliefs as _meta_import,
        cmd_init as _meta_init,
        cmd_status as _meta_status,
        cmd_summary as _meta_summary,
        cmd_topics as _meta_topics,
        cmd_update as _meta_update,
    )
    from .meta.pipeline import cmd_analyze

    meta_command = getattr(args, "meta_command", None)

    _meta_dispatch = {
        "init": _meta_init,
        "import": _meta_import,
        "derive": _meta_derive,
        "ask": _meta_ask,
        "contradictions": _meta_contradictions,
        "summary": _meta_summary,
        "topics": _meta_topics,
        "status": _meta_status,
        "update": _meta_update,
    }

    if meta_command and meta_command in _meta_dispatch:
        _meta_dispatch[meta_command](args)
        return

    # No subcommand — run full analyze pipeline
    cmd_analyze(args)



def _cmd_run(args):
    """Run a forge inside a sandbox."""
    if args.sandbox == "none":
        print("Error: --sandbox=none is the default; use the forge "
              "command directly instead of 'run'", file=sys.stderr)
        sys.exit(1)
    print(f"Sandbox ({args.sandbox}): {args.forge_type} "
          f"{' '.join(args.forge_args)}", file=sys.stderr)
    print("Not yet implemented — coming in Phase 3.", file=sys.stderr)
    sys.exit(1)


def register_forge_commands(parent_subparsers):
    """Register 'forge' subcommand group for individual pipeline steps."""
    forge_parser = parent_subparsers.add_parser(
        "forge", help="Individual forge pipeline steps"
    )
    sub = forge_parser.add_subparsers(dest="forge_command")

    # init
    p = sub.add_parser("init", help="Initialize a forge project")
    p.add_argument("name", help="Project name")
    p.add_argument("--domain", help="One-line domain description")

    # chunk-pdf
    p = sub.add_parser("chunk-pdf", help="Chunk a PDF into section entries")
    p.add_argument("pdf", help="Path to PDF file")
    p.add_argument("--prefix", help="Entry filename prefix")
    p.add_argument("--source-label", help="Citation label")
    p.add_argument("--dry-run", action="store_true")

    # chunk-docs
    p = sub.add_parser("chunk-docs", help="Chunk large documents")
    p.add_argument("--input-dir", default="sources")
    p.add_argument("--threshold", type=int, default=30000)
    p.add_argument("--recursive", "-r", action="store_true")
    p.add_argument("--dry-run", action="store_true")

    # summarize
    p = sub.add_parser("summarize", help="Generate summaries from source documents")
    p.add_argument("--input-dir", default="sources")
    p.add_argument("--recursive", "-r", action="store_true")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--limit", type=int)
    p.add_argument("--model", default="claude")

    # propose-beliefs
    p = sub.add_parser("propose-beliefs",
                       help="Extract candidate beliefs from summaries")
    p.add_argument("--input-dir", default="summaries")
    p.add_argument("--output", default="proposed-beliefs.md")
    p.add_argument("--model", default="claude")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--batch-size", type=int, default=5)
    p.add_argument("--entry", action="append")
    p.add_argument("--all", action="store_true")

    # accept-beliefs
    p = sub.add_parser("accept-beliefs",
                       help="Import accepted beliefs from proposals")
    p.add_argument("--file", default="proposed-beliefs.md")

    # pipeline
    p = sub.add_parser("pipeline",
                       help="Run end-to-end belief construction pipeline")
    p.add_argument("--pdf", action="append")
    p.add_argument("--sources-dir", default="sources")
    p.add_argument("--model", default="claude")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--max-derive-rounds", type=int, default=10)
    p.add_argument("--no-auto-accept", action="store_true")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--domain", help="Domain description for derive context")
    p.add_argument("--parallel", type=int, default=1)
    p.add_argument("--recursive", "-r", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--namespace", default=None)

    # derive-review-repair
    p = sub.add_parser("derive-review-repair",
                       help="Run derive/review/repair convergence loop")
    p.add_argument("--model", default="claude")
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--max-derive-rounds", type=int, default=10)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--domain", help="Domain description for derive context")
    p.add_argument("--namespace", default=None)

    # index-sources
    p = sub.add_parser("index-sources", help="Build FTS5 search index")
    p.add_argument("--input-dir", default="sources")
    p.add_argument("--recursive", "-r", action="store_true")
    p.add_argument("--db", default="rag_fts.db")
    p.add_argument("--type", default="source",
                   choices=["source", "summary", "chunked-summary"])
    p.add_argument("--chunk-size", type=int, default=2000)
    p.add_argument("--rebuild", action="store_true")

    # status
    sub.add_parser("status", help="Show forge pipeline progress")

    return {
        "init": lambda a: _lazy("init_cmd", "cmd_init")(a),
        "chunk-pdf": lambda a: _lazy("chunk_pdf", "cmd_chunk_pdf")(a),
        "chunk-docs": lambda a: _lazy("chunk_docs", "cmd_chunk_docs")(a),
        "summarize": lambda a: _lazy("summarize", "cmd_summarize")(a),
        "propose-beliefs": lambda a: _lazy("propose", "cmd_propose_beliefs")(a),
        "accept-beliefs": lambda a: _lazy("propose", "cmd_accept_beliefs")(a),
        "pipeline": lambda a: _lazy("pipeline", "cmd_pipeline")(a),
        "derive-review-repair": lambda a: _lazy("pipeline",
                                                 "cmd_derive_review_repair")(a),
        "index-sources": lambda a: _lazy("index_sources",
                                         "cmd_index_sources")(a),
        "status": lambda a: _lazy("init_cmd", "cmd_status")(a),
    }
