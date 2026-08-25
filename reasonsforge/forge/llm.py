"""Model invocation for expert agent builder.

Cost tracking: CLI models use --output-format json to capture token
counts and costs. Use get_cost_summary() to retrieve accumulated stats.
Ollama models use the HTTP API for clean JSON responses.
Cursor agent models use the cursor-agent CLI.
"""

import asyncio
import json
import os
import shutil
import urllib.request
import urllib.error

MODEL_COMMANDS: dict[str, list[str]] = {
    "claude": ["claude", "-p", "--output-format", "json"],
    "gemini": ["gemini", "--skip-trust", "-o", "json", "-p", ""],
}


def _ollama_base_url() -> str:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return host.rstrip("/")


def resolve_model_cmd(model: str) -> list[str]:
    """Resolve a model name to a CLI command list.

    Supports 'claude', 'gemini', 'claude:<variant>' (e.g. 'claude:opus'),
    'gemini:<model>' (e.g. 'gemini:gemini-2.5-flash').
    Ollama models use the HTTP API instead of CLI.
    """
    if model in MODEL_COMMANDS:
        return MODEL_COMMANDS[model]
    if model.startswith("claude:"):
        submodel = model.split(":", 1)[1]
        return ["claude", "-p", "--model", submodel, "--output-format", "json"]
    if model.startswith("gemini:"):
        submodel = model.split(":", 1)[1]
        return ["gemini", "--skip-trust", "-m", submodel, "-o", "json", "-p", ""]
    if model.startswith("ollama:"):
        raise ValueError(f"Ollama models use the HTTP API, not CLI commands: {model}")
    if model.startswith("cursor:"):
        raise ValueError(f"Cursor models use the cursor-agent CLI directly: {model}")
    available = (
        list(MODEL_COMMANDS)
        + ["claude:<model>", "gemini:<model>", "ollama:<model>",
           "cursor:<model>"]
    )
    raise ValueError(f"Unknown model: {model}. Available: {available}")

DEFAULT_TIMEOUT = 600

_cost_tracker = {
    "calls": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "total_cost_usd": 0.0,
    "by_model": {},
}


def reset_cost_tracker():
    """Reset accumulated cost/token stats."""
    _cost_tracker["calls"] = 0
    _cost_tracker["input_tokens"] = 0
    _cost_tracker["output_tokens"] = 0
    _cost_tracker["total_cost_usd"] = 0.0
    _cost_tracker["by_model"] = {}


def get_cost_summary() -> dict:
    """Return accumulated cost/token stats across all LLM calls."""
    return dict(_cost_tracker)


def format_cost_summary() -> str:
    """Format cost summary as a human-readable string."""
    s = _cost_tracker
    if s["calls"] == 0:
        return ""
    parts = []
    if s["total_cost_usd"] > 0:
        parts.append(f"${s['total_cost_usd']:.4f}")
    parts.append(f"{s['input_tokens']:,} input + {s['output_tokens']:,} output tokens")
    parts.append(f"{s['calls']} call(s)")
    return "Cost: " + " | ".join(parts)


def _record_cost(model: str, input_tokens: int, output_tokens: int, cost_usd: float):
    """Record token/cost stats from one LLM call."""
    _cost_tracker["calls"] += 1
    _cost_tracker["input_tokens"] += input_tokens
    _cost_tracker["output_tokens"] += output_tokens
    _cost_tracker["total_cost_usd"] += cost_usd

    if model not in _cost_tracker["by_model"]:
        _cost_tracker["by_model"][model] = {
            "calls": 0, "input_tokens": 0, "output_tokens": 0, "total_cost_usd": 0.0,
        }
    m = _cost_tracker["by_model"][model]
    m["calls"] += 1
    m["input_tokens"] += input_tokens
    m["output_tokens"] += output_tokens
    m["total_cost_usd"] += cost_usd


async def _invoke_ollama(prompt: str, model: str, timeout: int) -> str:
    """Invoke an Ollama model via the HTTP API."""
    ollama_model = model.split(":", 1)[1]
    url = f"{_ollama_base_url()}/api/generate"
    payload = json.dumps({
        "model": ollama_model,
        "prompt": prompt,
        "stream": False,
    }).encode()
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    def _do_request():
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"Ollama API error {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Cannot connect to Ollama at {_ollama_base_url()} — is it running?"
            ) from e

    data = await asyncio.get_event_loop().run_in_executor(None, _do_request)
    text = data.get("response", "")
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)
    if input_tokens or output_tokens:
        _record_cost(model, input_tokens, output_tokens, 0.0)
    return text


async def _invoke_cursor(prompt: str, model: str, timeout: int) -> str:
    """Invoke a Cursor agent model via the cursor-agent CLI."""
    cursor_model = model.split(":", 1)[1]
    binary = "cursor-agent"
    cmd = [
        binary, "--print", "--output-format", "json",
        "--trust", "--mode", "ask", "--model", cursor_model,
    ]
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        raise TimeoutError(f"cursor-agent timed out after {timeout}s") from None
    if proc.returncode != 0:
        raise RuntimeError(f"cursor-agent failed: {stderr.decode()}")
    try:
        data = json.loads(stdout.decode())
    except (json.JSONDecodeError, ValueError):
        return stdout.decode()
    text = data.get("result", stdout.decode())
    usage = data.get("usage", {})
    input_tokens = usage.get("inputTokens", 0) + usage.get("cacheReadTokens", 0)
    output_tokens = usage.get("outputTokens", 0)
    _record_cost(model, input_tokens, output_tokens, 0.0)
    return text


def _parse_cli_json(output: str, model: str) -> str:
    """Parse JSON output from CLI, extract response text and record costs.

    Falls back to returning raw output if JSON parsing fails.
    """
    try:
        data = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return output

    if not isinstance(data, dict):
        return output

    if model.startswith("gemini"):
        text = data.get("response") or output
        stats = data.get("stats", {})
        input_tokens = 0
        output_tokens = 0
        for model_stats in stats.get("models", {}).values():
            tokens = model_stats.get("tokens", {})
            input_tokens += tokens.get("input", 0)
            output_tokens += tokens.get("candidates", 0)
        _record_cost(model, input_tokens, output_tokens, 0.0)
        return text

    text = data.get("result") or output
    usage = data.get("usage", {})
    input_tokens = (usage.get("input_tokens", 0)
                    + usage.get("cache_creation_input_tokens", 0)
                    + usage.get("cache_read_input_tokens", 0))
    output_tokens = usage.get("output_tokens", 0)
    cost_usd = data.get("total_cost_usd", 0.0)
    _record_cost(model, input_tokens, output_tokens, cost_usd)
    return text


def check_model_available(model: str) -> bool:
    """Check if a model's CLI or API is available."""
    if model.startswith("ollama:"):
        return True
    if model.startswith("cursor:"):
        return shutil.which("cursor-agent") is not None
    try:
        cmd = resolve_model_cmd(model)
    except ValueError:
        return False
    return shutil.which(cmd[0]) is not None


async def invoke(prompt: str, model: str = "claude", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Invoke model via CLI or HTTP API.

    Ollama models use the HTTP API for clean JSON responses.
    CLI models use --output-format json to capture token/cost data.
    Accumulated stats available via get_cost_summary().
    """
    if model.startswith("ollama:"):
        return await _invoke_ollama(prompt, model, timeout)

    if model.startswith("cursor:"):
        return await _invoke_cursor(prompt, model, timeout)

    cmd = resolve_model_cmd(model)

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(prompt.encode()),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        raise TimeoutError(f"Model {model} timed out after {timeout}s") from None

    if proc.returncode != 0:
        raise RuntimeError(f"Model {model} failed: {stderr.decode()}")

    return _parse_cli_json(stdout.decode(), model)


def invoke_sync(prompt: str, model: str = "claude", timeout: int = DEFAULT_TIMEOUT) -> str:
    """Synchronous wrapper for invoke."""
    return asyncio.run(invoke(prompt, model, timeout))


async def invoke_concurrent(
    prompts: list[str],
    model: str = "claude",
    timeout: int = DEFAULT_TIMEOUT,
    max_concurrent: int = 3,
) -> list[str | Exception]:
    """Invoke model on multiple prompts concurrently with bounded parallelism."""
    sem = asyncio.Semaphore(max_concurrent)

    async def _guarded(prompt: str) -> str:
        async with sem:
            return await invoke(prompt, model, timeout)

    return await asyncio.gather(
        *[_guarded(p) for p in prompts],
        return_exceptions=True,
    )


def invoke_concurrent_sync(
    prompts: list[str],
    model: str = "claude",
    timeout: int = DEFAULT_TIMEOUT,
    max_concurrent: int = 3,
) -> list[str | Exception]:
    """Synchronous wrapper around invoke_concurrent."""
    return asyncio.run(invoke_concurrent(prompts, model, timeout, max_concurrent))


RETRY_JSON = "Your response was not valid JSON. Respond with ONLY the JSON object, no other text."


def extract_json(response: str) -> dict | list | None:
    """Extract a JSON object or array from an LLM response."""
    text = response.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    start = text.find("{")
    start_arr = text.find("[")
    if start_arr != -1 and (start == -1 or start_arr < start):
        end = text.rfind("]")
        if end > start_arr:
            try:
                return json.loads(text[start_arr:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
    if start != -1:
        end = text.rfind("}")
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                pass
    return None
