"""Model invocation and response parsing for code review.

Uses reasonsforge's LLM layer instead of direct CLI/API calls.
"""

import asyncio
import json
import re

from . import (
    ChangeVerdict,
    Correctness,
    Integration,
    ModelReview,
    SelfReview,
    SpecCompliance,
    TestCoverage,
    Verdict,
)

DEFAULT_TIMEOUT = 300


async def run_model(model: str, prompt: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    from reasonsforge.llm import invoke_model

    return await asyncio.to_thread(invoke_model, prompt, model, timeout)


def check_model_available(model: str) -> bool:
    import os
    import shutil
    from reasonsforge.llm import resolve_model_cmd

    if model.startswith("api:") or model.startswith("vertex:"):
        return True
    if model.startswith("openai:"):
        return bool(os.environ.get("OPENAI_API_KEY"))
    if model.startswith("ollama:"):
        return shutil.which("ollama") is not None
    try:
        cmd = resolve_model_cmd(model)
        return shutil.which(cmd[0]) is not None
    except ValueError:
        return False


def preflight_check(models: list[str]) -> list[str]:
    return [m for m in models if not check_model_available(m)]


def format_preflight_error(missing: list[str]) -> str:
    return f"Error: Missing model(s): {', '.join(missing)}"


def parse_verdict(text: str) -> Verdict:
    text = text.strip().upper()
    if text == "PASS":
        return Verdict.PASS
    elif text == "BLOCK":
        return Verdict.BLOCK
    return Verdict.CONCERN


def parse_correctness(text: str) -> Correctness | None:
    text = text.strip().upper()
    mapping = {
        "VALID": Correctness.VALID,
        "QUESTIONABLE": Correctness.QUESTIONABLE,
        "BROKEN": Correctness.BROKEN,
    }
    return mapping.get(text)


def parse_spec_compliance(text: str) -> SpecCompliance | None:
    text = text.strip().upper()
    mapping = {
        "MEETS": SpecCompliance.MEETS,
        "PARTIAL": SpecCompliance.PARTIAL,
        "VIOLATES": SpecCompliance.VIOLATES,
    }
    if text in ("N/A", "NA"):
        return SpecCompliance.NA
    return mapping.get(text)


def parse_test_coverage(text: str) -> TestCoverage | None:
    text = text.strip().upper()
    mapping = {
        "COVERED": TestCoverage.COVERED,
        "PARTIAL": TestCoverage.PARTIAL,
        "UNTESTED": TestCoverage.UNTESTED,
    }
    return mapping.get(text)


def parse_integration(text: str) -> Integration | None:
    text = text.strip().upper()
    mapping = {
        "WIRED": Integration.WIRED,
        "PARTIAL": Integration.PARTIAL,
        "MISSING": Integration.MISSING,
    }
    return mapping.get(text)


CHANGE_PATTERN = re.compile(
    r"###\s+(.+?)\s*\n"
    r"VERDICT:\s*(PASS|CONCERN|BLOCK)\s*\n"
    r"(?:CORRECTNESS:\s*(\w+)\s*\n)?"
    r"(?:SPEC_COMPLIANCE:\s*([^\n]+)\s*\n)?"
    r"(?:ISSUE_COMPLIANCE:\s*([^\n]+)\s*\n)?"
    r"(?:BELIEF_COMPLIANCE:\s*([^\n]+)\s*\n)?"
    r"(?:TEST_COVERAGE:\s*(\w+)\s*\n)?"
    r"(?:INTEGRATION:\s*(\w+)\s*\n)?"
    r"REASONING:\s*(.*?)(?=\n---|\n###|\n##|$)",
    re.DOTALL | re.IGNORECASE,
)

SELF_REVIEW_PATTERN = re.compile(
    r"###\s*SELF_REVIEW\s*\n"
    r"(?:CONFIDENCE:\s*(?:HIGH|MEDIUM|LOW)\s*\n)?"
    r"LIMITATIONS:\s*(.*?)(?=\n---|\n###|\n##|$)",
    re.DOTALL | re.IGNORECASE,
)

FEATURE_REQUESTS_PATTERN = re.compile(
    r"###\s*FEATURE_REQUESTS\s*\n" r"(.*?)(?=\n---|\n###|\n##|$)",
    re.DOTALL | re.IGNORECASE,
)

OBSERVATIONS_PATTERN = re.compile(
    r"###\s*OBSERVATIONS\s*\n```json\n(.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


def parse_self_review(response: str) -> SelfReview | None:
    match = SELF_REVIEW_PATTERN.search(response)
    if not match:
        return None
    return SelfReview(limitations=match.group(1).strip() if match.group(1) else "")


def parse_feature_requests(response: str) -> list[str]:
    match = FEATURE_REQUESTS_PATTERN.search(response)
    if not match:
        return []
    requests = []
    for line in match.group(1).strip().split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            requests.append(line[2:].strip())
    return requests


def parse_observations(response: str) -> list[dict]:
    match = OBSERVATIONS_PATTERN.search(response)
    if not match:
        return []
    try:
        observations = json.loads(match.group(1).strip())
        return observations if isinstance(observations, list) else []
    except json.JSONDecodeError:
        return []


def parse_review_response(model: str, response: str) -> ModelReview:
    changes: list[ChangeVerdict] = []

    for match in CHANGE_PATTERN.finditer(response):
        change_id = match.group(1).strip()
        verdict = parse_verdict(match.group(2))
        correctness = parse_correctness(match.group(3)) if match.group(3) else None
        spec_compliance = (
            parse_spec_compliance(match.group(4)) if match.group(4) else None
        )
        belief_compliance = match.group(6).strip() if match.group(6) else None
        test_coverage = parse_test_coverage(match.group(7)) if match.group(7) else None
        integration = parse_integration(match.group(8)) if match.group(8) else None
        reasoning = match.group(9).strip() if match.group(9) else ""

        if belief_compliance and belief_compliance.upper() not in ("N/A", "CONSISTENT"):
            reasoning = f"[BELIEF: {belief_compliance}] {reasoning}"

        changes.append(
            ChangeVerdict(
                change_id=change_id,
                verdict=verdict,
                correctness=correctness,
                spec_compliance=spec_compliance,
                test_coverage=test_coverage,
                integration=integration,
                reasoning=reasoning,
            )
        )

    gate = Verdict.PASS
    for change in changes:
        if change.verdict == Verdict.BLOCK:
            gate = Verdict.BLOCK
            break
        elif change.verdict == Verdict.CONCERN:
            gate = Verdict.CONCERN

    if not changes:
        gate = Verdict.CONCERN

    self_review = parse_self_review(response)
    feature_requests = parse_feature_requests(response)

    return ModelReview(
        model=model,
        gate=gate,
        changes=changes,
        raw_response=response,
        self_review=self_review,
        feature_requests=feature_requests,
    )


def parse_observe_response(response: str) -> list[dict]:
    json_match = re.search(r"```json\n(.*?)\n```", response, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass
    try:
        result = json.loads(response.strip())
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    return []


async def observe_with_model(model: str, diff_content: str) -> list[dict]:
    from .prompts.observe import build_observe_prompt

    prompt = build_observe_prompt(diff_content)
    response = await run_model(model, prompt)
    return parse_observe_response(response)


async def review_with_model(
    model: str,
    prompt: str,
    observations: dict | None = None,
    timeout: int | None = None,
) -> ModelReview:
    try:
        response = await run_model(model, prompt, timeout=timeout or DEFAULT_TIMEOUT)
        review = parse_review_response(model, response)
        if observations:
            review.observations = observations
        return review
    except Exception as e:
        return ModelReview(
            model=model,
            gate=Verdict.BLOCK,
            changes=[
                ChangeVerdict(
                    change_id="ERROR",
                    verdict=Verdict.BLOCK,
                    reasoning=f"Model invocation failed: {e}",
                )
            ],
            raw_response=str(e),
        )


async def review_with_models(
    models: list[str],
    prompt: str,
    observations: dict | None = None,
    timeout: int | None = None,
) -> list[ModelReview]:
    tasks = [
        review_with_model(model, prompt, observations, timeout=timeout)
        for model in models
    ]
    return await asyncio.gather(*tasks)
