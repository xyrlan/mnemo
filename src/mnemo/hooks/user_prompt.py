# src/mnemo/hooks/user_prompt.py
"""UserPromptSubmit hook entry point."""
from __future__ import annotations

import json
import os
import sys

PROMPT_LINE_CAP = 200


def _first_line(prompt: str) -> str:
    for raw in prompt.splitlines():
        stripped = raw.strip()
        if stripped:
            return stripped
    return ""


def _sanitize(line: str) -> str:
    cleaned = line.replace("`", "'")
    if len(cleaned) > PROMPT_LINE_CAP:
        cleaned = cleaned[: PROMPT_LINE_CAP - 3] + "..."
    return cleaned


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, paths, session

        cfg = config.load_config()
        if not cfg.get("capture", {}).get("userPrompt", True):
            return 0
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        prompt = payload.get("prompt", "") or ""
        first = _first_line(prompt)
        if not first or "system-reminder" in first.lower():
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cached = session.load(sid)
        agent_name = (cached or {}).get("name") or (cached or {}).get("agent")
        if not agent_name:
            cwd = payload.get("cwd") or os.getcwd()
            agent_name = agent.resolve_agent(cwd).name
        try:
            log_writer.append_line(agent_name, f"💬 {_sanitize(first)}", cfg)
        except Exception as e:
            errors.log_error(vault, "user_prompt.log", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "user_prompt.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
