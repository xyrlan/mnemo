"""Thin subprocess wrapper over `claude --print` for v0.2 extraction.

Reusable beyond v0.2 — kept outside core/extract/ on purpose.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Any


class LLMSubprocessError(Exception):
    """The `claude` subprocess failed, timed out, or was missing."""


class LLMParseError(Exception):
    """The subprocess output could not be parsed as JSON."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    total_cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    api_key_source: str | None
    raw: dict


# Seam for test monkey-patching. Tests replace this symbol directly.
_subprocess_run = subprocess.run


def _parse_llm_json(result_text: str) -> dict:
    """Extract a single JSON object from an LLM response, tolerating fences and prose."""
    s = result_text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```\s*$", "", s)
    start = s.find("{")
    if start < 0:
        raise LLMParseError("no JSON object found in response")
    try:
        obj, _end = json.JSONDecoder().raw_decode(s[start:])
    except json.JSONDecodeError as e:
        raise LLMParseError(f"malformed JSON at offset {e.pos}: {e.msg}") from e
    if not isinstance(obj, dict):
        raise LLMParseError("parsed JSON is not an object")
    return obj


def _is_rate_limit(stderr: str) -> bool:
    return bool(re.search(r"rate.?limit", stderr or "", re.IGNORECASE))


def _build_argv(model: str, system: str | None) -> list[str]:
    # --strict-mcp-config (with no paired --mcp-config) tells the CLI to
    # ignore every MCP configuration source, producing an empty mcp_servers
    # list. Combined with --tools "" (which zeroes the built-in set), the
    # subprocess sees zero tools — no MCP plugin tools leaking in from the
    # user's Claude Code config. See issue #6.
    #
    # NOTE: do NOT use --bare here; it also skips plugin sync but forces
    # ANTHROPIC_API_KEY auth and refuses to read OAuth/keychain, which
    # breaks subscription users.
    argv = [
        "claude",
        "--print",
        "--strict-mcp-config",
        "--no-session-persistence",
        "--output-format", "json",
        "--model", model,
        "--tools", "",
    ]
    if system is not None:
        argv.extend(["--system-prompt", system])
    return argv


def _build_env() -> dict[str, str]:
    """Parent env + CLAUDE_CODE_DISABLE_THINKING=1 to suppress extended thinking.

    Haiku 4.5 does extended thinking by default in `claude --print`, which
    costs ~200-400 output tokens and several seconds of wall-time on trivial
    prompts. This env var is the canonical switch. See issue #7.
    """
    env = os.environ.copy()
    env["CLAUDE_CODE_DISABLE_THINKING"] = "1"
    return env


def _invoke_once(argv: list[str], prompt: str, timeout: int):
    return _subprocess_run(
        argv,
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=_build_env(),
    )


def call(
    prompt: str,
    *,
    system: str | None,
    model: str = "claude-haiku-4-5",
    timeout: int = 60,
) -> LLMResponse:
    argv = _build_argv(model, system)

    attempts = 0
    while True:
        attempts += 1
        try:
            result = _invoke_once(argv, prompt, timeout)
        except FileNotFoundError as exc:
            raise LLMSubprocessError(
                "claude CLI not found; install Claude Code first"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            if attempts == 1:
                time.sleep(2.0)
                continue
            raise LLMSubprocessError(f"subprocess timed out twice after {timeout}s") from exc

        if result.returncode != 0:
            if _is_rate_limit(result.stderr) and attempts == 1:
                time.sleep(5.0)
                continue
            raise LLMSubprocessError(
                f"claude exited with code {result.returncode}: {result.stderr.strip()}"
            )
        break

    try:
        parsed: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise LLMParseError(f"envelope JSON invalid: {exc.msg}") from exc

    # Claude Code CLI ≥2.x returns `--output-format json` as an array of events.
    # Older format (single dict envelope) is also supported for test fixtures
    # and backwards compatibility.
    if isinstance(parsed, list):
        events = parsed
    elif isinstance(parsed, dict):
        events = [parsed]
    else:
        raise LLMParseError("envelope is neither a JSON object nor a JSON array")

    result_event: dict | None = None
    init_event: dict | None = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = ev.get("type")
        if etype == "result" and result_event is None:
            result_event = ev
        elif etype == "system" and ev.get("subtype") == "init":
            init_event = ev

    if result_event is None:
        raise LLMParseError("no result event in response")

    text = result_event.get("result", "")
    usage = result_event.get("usage") or {}
    api_key_source = None
    if init_event is not None:
        api_key_source = init_event.get("apiKeySource")
    if api_key_source is None:
        api_key_source = result_event.get("apiKeySource")

    return LLMResponse(
        text=text,
        total_cost_usd=result_event.get("total_cost_usd"),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        api_key_source=api_key_source,
        raw={"events": events, "result": result_event, "init": init_event},
    )
