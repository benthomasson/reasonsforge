"""Aggregation logic for combining reviews from multiple models."""

from . import AggregateReview, ModelReview, Verdict


def find_disagreements(reviews: list[ModelReview]) -> list[dict]:
    if len(reviews) < 2:
        return []

    change_verdicts: dict[str, dict[str, Verdict]] = {}
    for review in reviews:
        for change in review.changes:
            if change.change_id not in change_verdicts:
                change_verdicts[change.change_id] = {}
            change_verdicts[change.change_id][review.model] = change.verdict

    disagreements = []
    for change_id, verdicts in change_verdicts.items():
        unique_verdicts = set(verdicts.values())
        if len(unique_verdicts) > 1:
            if Verdict.PASS in unique_verdicts and Verdict.BLOCK in unique_verdicts:
                severity = "HIGH"
            elif Verdict.BLOCK in unique_verdicts:
                severity = "MEDIUM"
            else:
                severity = "LOW"
            disagreements.append(
                {
                    "change_id": change_id,
                    "verdicts": {model: v.value for model, v in verdicts.items()},
                    "severity": severity,
                }
            )

    severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    disagreements.sort(key=lambda d: severity_order[d["severity"]])
    return disagreements


def compute_gate(reviews: list[ModelReview]) -> Verdict:
    if not reviews:
        return Verdict.CONCERN
    gates = [r.gate for r in reviews]
    if Verdict.BLOCK in gates:
        return Verdict.BLOCK
    elif Verdict.CONCERN in gates:
        return Verdict.CONCERN
    return Verdict.PASS


def aggregate_reviews(
    diff_ref: str,
    reviews: list[ModelReview],
    spec_file: str | None = None,
) -> AggregateReview:
    return AggregateReview(
        diff_ref=diff_ref,
        spec_file=spec_file,
        models=[r.model for r in reviews],
        reviews=reviews,
        gate=compute_gate(reviews),
        disagreements=find_disagreements(reviews),
    )
