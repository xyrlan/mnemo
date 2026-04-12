# src/mnemo/hooks/session_end.py
"""SessionEnd hook entry point."""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, mirror, paths, session

        cfg = config.load_config()
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cached = session.load(sid)
        if cached and cached.get("name"):
            agent_name = cached["name"]
        elif cached and cached.get("agent"):
            agent_name = cached["agent"]
        else:
            cwd = payload.get("cwd") or os.getcwd()
            agent_name = agent.resolve_agent(cwd).name
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            errors.log_error(vault, "session_end.mirror", e)
        if cfg.get("capture", {}).get("sessionStartEnd", True):
            reason = payload.get("reason", "exit")
            try:
                log_writer.append_line(agent_name, f"🔴 session ended ({reason})", cfg)
            except Exception as e:
                errors.log_error(vault, "session_end.log", e)
        try:
            session.clear(sid)
        except Exception as e:
            errors.log_error(vault, "session_end.clear", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_end.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
