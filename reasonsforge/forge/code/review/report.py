"""Report generation for code review results."""

from . import AggregateReview, ModelReview, Verdict


def format_verdict_badge(verdict: Verdict) -> str:
    if verdict == Verdict.PASS:
        return "[PASS]"
    elif verdict == Verdict.CONCERN:
        return "[CONCERN]"
    return "[BLOCK]"


def format_model_review(review: ModelReview) -> str:
    lines = [f"## {review.model} {format_verdict_badge(review.gate)}", ""]

    if not review.changes and review.raw_response:
        lines.append(review.raw_response.strip())
        lines.append("")
    else:
        for change in review.changes:
            lines.append(f"### {change.change_id}")
            lines.append(f"**Verdict:** {change.verdict.value}")
            if change.correctness:
                lines.append(f"**Correctness:** {change.correctness.value}")
            if change.spec_compliance:
                lines.append(f"**Spec Compliance:** {change.spec_compliance.value}")
            if change.test_coverage:
                lines.append(f"**Test Coverage:** {change.test_coverage.value}")
            if change.integration:
                lines.append(f"**Integration:** {change.integration.value}")
            if change.reasoning:
                lines.append(f"\n{change.reasoning}")
            lines.append("")

    if review.self_review:
        lines.append("### Self-Review")
        if review.self_review.limitations:
            lines.append(f"**Limitations:** {review.self_review.limitations}")
        lines.append("")

    if review.feature_requests:
        lines.append("### Feature Requests")
        for req in review.feature_requests:
            lines.append(f"- {req}")
        lines.append("")

    return "\n".join(lines)


def format_disagreements(disagreements: list[dict]) -> str:
    if not disagreements:
        return ""
    lines = ["## Disagreements", "", "The following changes have different verdicts across models:", ""]
    for d in disagreements:
        verdict_str = ", ".join(f"{m}: {v}" for m, v in d["verdicts"].items())
        lines.append(f"- **{d['change_id']}** [{d['severity']}]: {verdict_str}")
    lines.append("")
    return "\n".join(lines)


def format_aggregate_review(review: AggregateReview) -> str:
    lines = [
        "# Code Review Report",
        "",
        f"**Branch:** {review.diff_ref}",
        f"**Models:** {', '.join(review.models)}",
        f"**Gate:** {format_verdict_badge(review.gate)} {review.gate.value}",
    ]
    if review.spec_file:
        lines.append(f"**Spec:** {review.spec_file}")
    lines.append("")
    if review.disagreements:
        lines.append(format_disagreements(review.disagreements))
    for model_review in review.reviews:
        lines.append(format_model_review(model_review))
    return "\n".join(lines)


def format_summary(review: AggregateReview) -> str:
    lines = [
        f"Gate: {review.gate.value}",
        f"Models: {', '.join(review.models)}",
    ]
    if review.disagreements:
        high = sum(1 for d in review.disagreements if d["severity"] == "HIGH")
        med = sum(1 for d in review.disagreements if d["severity"] == "MEDIUM")
        low = sum(1 for d in review.disagreements if d["severity"] == "LOW")
        lines.append(f"Disagreements: {high} high, {med} medium, {low} low")

    blocks = concerns = passes = 0
    for mr in review.reviews:
        for change in mr.changes:
            if change.verdict == Verdict.BLOCK:
                blocks += 1
            elif change.verdict == Verdict.CONCERN:
                concerns += 1
            else:
                passes += 1
    lines.append(f"Verdicts: {passes} pass, {concerns} concern, {blocks} block")
    return "\n".join(lines)
