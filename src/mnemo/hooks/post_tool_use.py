# src/mnemo/hooks/post_tool_use.py
"""PostToolUse hook entry point (Write|Edit only)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _extract_file_path(payload: dict) -> str | None:
    response = payload.get("tool_response") or {}
    if isinstance(response, dict):
        fp = response.get("filePath") or response.get("file_path")
        if fp:
            return str(fp)
    inputs = payload.get("tool_input") or {}
    if isinstance(inputs, dict):
        fp = inputs.get("file_path")
        if fp:
            return str(fp)
    return None


def _display_path(file_path: str, repo_root: str | None) -> str:
    p = Path(file_path)
    if repo_root:
        try:
            return str(p.resolve().relative_to(Path(repo_root).resolve()))
        except ValueError:
            pass
    return p.name


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, paths, session

        cfg = config.load_config()
        if not cfg.get("capture", {}).get("fileEdits", True):
            return 0
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        file_path = _extract_file_path(payload)
        if not file_path:
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cached = session.load(sid)
        agent_name = (cached or {}).get("name") or (cached or {}).get("agent")
        repo_root = (cached or {}).get("repo_root")
        if not agent_name:
            cwd = payload.get("cwd") or os.getcwd()
            ainfo = agent.resolve_agent(cwd)
            agent_name = ainfo.name
            repo_root = ainfo.repo_root if ainfo.has_git else None
        verb = "created" if payload.get("tool_name") == "Write" else "edited"
        display = _display_path(file_path, repo_root)
        try:
            log_writer.append_line(agent_name, f"✏️ {verb} `{display}`", cfg)
        except Exception as e:
            errors.log_error(vault, "post_tool_use.log", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "post_tool_use.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
