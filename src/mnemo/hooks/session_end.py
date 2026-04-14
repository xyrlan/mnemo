# src/mnemo/hooks/session_end.py
"""SessionEnd hook entry point."""
from __future__ import annotations

import json
import os
import sys
import time


def _debounce_passes(
    state_path,
    vault_root,
    cfg: dict,
    *,
    now=None,
) -> bool:
    """Pure function: check count+time debounce for background scheduling.

    Returns True when both minNewMemories and minIntervalMinutes conditions
    are satisfied.  Any exception results in False (fail-closed).
    """
    from datetime import datetime, timedelta
    try:
        auto_cfg = (cfg.get("extraction", {}) or {}).get("auto", {}) or {}
        min_new = int(auto_cfg.get("minNewMemories", 5) or 5)
        min_interval_min = int(auto_cfg.get("minIntervalMinutes", 60) or 60)

        last_run = None
        if state_path.exists():
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                last_run = payload.get("last_run")
            except (OSError, json.JSONDecodeError):
                last_run = None

        now_dt = now or datetime.now()

        # Time gate
        if last_run:
            try:
                last_run_dt = datetime.fromisoformat(last_run)
            except ValueError:
                last_run_dt = None
            if last_run_dt is not None:
                if (now_dt - last_run_dt) < timedelta(minutes=min_interval_min):
                    return False

        # Count gate
        last_run_ts = 0.0
        if last_run:
            try:
                last_run_ts = datetime.fromisoformat(last_run).timestamp()
            except ValueError:
                last_run_ts = 0.0

        count = 0
        bots_root = vault_root / "bots"
        if bots_root.is_dir():
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

        return count >= min_new
    except Exception:
        return False


def _lock_held(lock_path) -> bool:
    """True if the extract lock exists and is younger than the stale threshold.

    Matches the 5-minute self-heal in locks.try_lock.
    """
    try:
        if not lock_path.exists():
            return False
        age = time.time() - lock_path.stat().st_mtime
        return age < 300  # 5 minutes
    except OSError:
        return False


def _spawn_detached_extraction() -> None:
    """Fire-and-forget background extraction via subprocess.Popen.

    Uses platform-specific detach flags so the child survives the hook's
    exit. Stdio is redirected to DEVNULL because nothing reads a detached
    subprocess's output.
    """
    import subprocess

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    argv = [sys.executable, "-m", "mnemo", "extract", "--background"]
    subprocess.Popen(argv, **kwargs)


def _maybe_schedule_extraction(cfg: dict, vault_root, agent_name: str) -> None:
    """Main entry point called from session_end.main().

    When auto.enabled=True and debounce passes, spawn a detached background
    extraction. When auto.enabled=False, do nothing (the user opted out).
    Any exception is logged with where='session_end.schedule' and swallowed.
    """
    try:
        from mnemo.core import errors as err_mod

        auto_cfg = (cfg.get("extraction", {}) or {}).get("auto", {}) or {}
        auto_enabled = bool(auto_cfg.get("enabled", False))

        if not auto_enabled:
            return

        state_path = vault_root / ".mnemo" / "extraction-state.json"
        if not _debounce_passes(state_path, vault_root, cfg):
            return

        lock_path = vault_root / ".mnemo" / "extract.lock"
        if _lock_held(lock_path):
            return

        try:
            _spawn_detached_extraction()
        except OSError as exc:
            err_mod.log_error(vault_root, "session_end.schedule.popen", exc)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_end.schedule", exc)
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
            _maybe_schedule_extraction(cfg, vault, agent_name)
        except Exception as e:
            errors.log_error(vault, "session_end.schedule_wrap", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_end.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
