# src/mnemo/hooks/session_end.py
"""SessionEnd hook entry point."""
from __future__ import annotations

import json
import os
import sys


def _maybe_emit_hint(cfg: dict, vault_root, agent_name: str) -> None:
    """Append a hint line to today's log if enough new memories have accumulated.

    This is cosmetic. Any exception is swallowed — hooks never fail the session.
    """
    try:
        from datetime import datetime

        from mnemo.core import log_writer

        state_path = vault_root / ".mnemo" / "extraction-state.json"
        if not state_path.exists():
            return
        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        last_run = payload.get("last_run")
        if not last_run:
            return
        try:
            last_run_ts = datetime.fromisoformat(last_run).timestamp()
        except ValueError:
            return

        count = 0
        bots_root = vault_root / "bots"
        if not bots_root.is_dir():
            return
        for agent_dir in bots_root.iterdir():
            memory_dir = agent_dir / "memory"
            if not memory_dir.is_dir():
                continue
            for p in memory_dir.glob("*.md"):
                if p.name == "MEMORY.md":
                    continue
                try:
                    if p.stat().st_mtime > last_run_ts:
                        count += 1
                except OSError:
                    continue

        threshold = int(cfg.get("extraction", {}).get("hintThreshold", 5))
        if count < threshold:
            return

        today = datetime.now().strftime("%Y-%m-%d")
        log_path = vault_root / "bots" / agent_name / "logs" / f"{today}.md"
        if log_path.exists() and "🟡" in log_path.read_text(errors="ignore"):
            return  # already hinted today

        if count >= threshold * 3:
            line = f"🟡 {count} new memories (a lot!) — run /mnemo extract"
        else:
            line = f"🟡 {count} new memories — run /mnemo extract"

        # Minimal config object for log_writer.append_line
        mini_cfg = {"vaultRoot": str(vault_root)}
        log_writer.append_line(agent_name, line, mini_cfg)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_end.hint", exc)
        except Exception:
            pass


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
        try:
            _maybe_emit_hint(cfg, vault, agent_name)
        except Exception as e:
            errors.log_error(vault, "session_end.hint_wrap", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_end.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
