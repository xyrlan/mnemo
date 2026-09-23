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

    Two exceptions to the arithmetic:

    - No ``last_run`` means the vault has never been extracted. That first
      pass is the one that turns a freshly-installed mnemo into something the
      user can see working, so it is never debounced — a brand-new vault has
      no memory files yet and would otherwise fail the count gate forever.
    - Session briefings count as new material. They are the primary input to
      consolidation (``scanner`` routes them through the feedback cluster),
      and a user whose sessions produce briefings but no memory files would
      never trip the count gate.
    """
    from datetime import datetime, timedelta
    try:
        auto_cfg = (cfg.get("extraction", {}) or {}).get("auto", {}) or {}
        min_new = int(auto_cfg.get("minNewMemories", 1) or 1)
        min_interval_min = int(auto_cfg.get("minIntervalMinutes", 60) or 60)

        last_run = None
        if state_path.exists():
            try:
                payload = json.loads(state_path.read_text(encoding="utf-8"))
                last_run = payload.get("last_run")
            except (OSError, json.JSONDecodeError):
                last_run = None

        # The vault has never been extracted: run, unconditionally.
        if not last_run:
            return True

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
                if memory_dir.is_dir():
                    for p in memory_dir.glob("*.md"):
                        if p.name == "MEMORY.md":
                            continue
                        try:
                            if p.stat().st_mtime > last_run_ts:
                                count += 1
                        except OSError:
                            continue

                # Briefings are new material too — scanner feeds them through
                # the feedback cluster, so a session that produced only a
                # briefing still gives the extractor something to consolidate.
                briefings_dir = agent_dir / "briefings" / "sessions"
                if briefings_dir.is_dir():
                    for p in briefings_dir.glob("*.md"):
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

    from mnemo._detach import detach_kwargs
    from mnemo._selfexec import self_argv

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    kwargs.update(detach_kwargs())

    argv = self_argv("extract", "--background")
    subprocess.Popen(argv, **kwargs)


def _resolve_session_jsonl_path(session_id: str, cwd: str):
    """Return the Claude Code session transcript path, or None if missing.

    Claude Code stores sessions at
    `~/.claude/projects/<dash-encoded-cwd>/<session-id>.jsonl`, where the
    cwd is encoded by replacing every path separator with a dash.
    """
    from pathlib import Path

    if not session_id or not cwd:
        return None
    encoded = cwd.replace(os.sep, "-")
    if os.altsep:
        encoded = encoded.replace(os.altsep, "-")
    home = Path(os.path.expanduser("~"))
    path = home / ".claude" / "projects" / encoded / f"{session_id}.jsonl"
    return path if path.exists() else None


def _spawn_detached_briefing(jsonl_path, agent: str) -> None:
    """Fire-and-forget background briefing via subprocess.Popen.

    Invokes `mnemo briefing <jsonl_path> <agent>`. Detach semantics match
    _spawn_detached_extraction so the child survives the hook's exit.
    """
    import subprocess

    from mnemo._detach import detach_kwargs
    from mnemo._selfexec import self_argv

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    kwargs.update(detach_kwargs())

    argv = self_argv("briefing", str(jsonl_path), agent)
    subprocess.Popen(argv, **kwargs)


def _maybe_schedule_briefing(
    cfg: dict,
    vault_root,
    agent_name: str,
    *,
    session_id: str,
    cwd: str,
) -> None:
    """Spawn a detached per-session briefing when briefings.enabled=True.

    The briefing's storage agent is ``agent_name`` — the name ``main()``
    resolved for this session: the session cache first (written by
    session_start, canonically since #225, while the tree still existed),
    canonical resolution of the cwd only on a cache miss. That is the same
    name the day's log line is written under, so the two never disagree.

    Not re-resolved from ``cwd`` here (#247): a dispatched worktree is
    routinely removed before its child is stopped, so by the time SessionEnd
    fires the cwd can be a path with nothing on disk. A fresh resolution then
    finds no ``.git`` above it and falls back to the directory basename —
    ``bots/<repo>-wt-N/briefings/``, one orphan namespace per stopped child,
    while the log line beside it went under ``bots/<repo>/``. ``cwd`` is
    still needed to locate the transcript, which outlives the tree.

    Unlike extraction, briefings skip the count+time debounce — they are
    cheap and run on every session end so no handoff state is dropped.

    One exception (#449): a twin's briefing is held until the maintainer
    delivers it, and the twin never delivered is never briefed — see
    :mod:`mnemo.core.twins` for why the losing run must not teach the vault.
    """
    try:
        from mnemo.core import errors as err_mod

        briefings_cfg = cfg.get("briefings") or {}
        if not bool(briefings_cfg.get("enabled", False)):
            return

        jsonl_path = _resolve_session_jsonl_path(session_id, cwd)
        if jsonl_path is None:
            return

        from mnemo.core import twins

        if twins.hold_briefing(vault_root, cwd=cwd, jsonl=jsonl_path, agent=agent_name):
            return

        try:
            _spawn_detached_briefing(jsonl_path, agent_name)
        except OSError as exc:
            err_mod.log_error(vault_root, "session_end.briefing.popen", exc)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_end.briefing", exc)
        except Exception:
            pass


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


def _maybe_sweep_sessions(vault_root) -> None:
    """Record every answer that reached a background session since the last
    sweep (#176).

    ``core.sessions.detector`` documented itself as riding this hook from the
    start; until PR #191 only ``mnemo sessions`` ever called it, so on a
    machine where nobody runs that command by hand the detector never ran at
    all.

    The hook fires when *this* session ends, which is never inside another
    session's ten-second ``blocked -> active`` flip (measured, PR #203). That
    no longer matters: the detector reads each session's transcript forward
    from its own bookmark, so an answer that landed an hour ago is recorded
    here just as well as one that landed a second ago. This trigger only has
    to happen eventually, not in time.

    Unscoped on purpose. The hook fires in one repo, but the blocked sessions
    worth recording may be running anywhere; ``mnemo sessions`` filters by cwd
    because a human is reading the list, while the detector is populating
    state and wants every session it can see.

    Never raises: the queue's state file is disposable (losing it costs
    un-extracted markers, never a rule), and the hook's other work is not.
    """
    try:
        from mnemo.core.sessions import detector
        from mnemo.core.sessions import jobs

        detector.sweep(jobs.read_sessions(), vault_root=vault_root)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_end.sweep_sessions", exc)
        except Exception:
            pass


def _spawn_detached_unblock_consumption() -> None:
    """Fire-and-forget ``mnemo sessions --consume-unblocks``.

    Detached for the same reason briefing and extraction are: consuming a
    marker runs a briefing and an extraction, both LLM-bound, and the hook
    must not hold up the session's exit for them.
    """
    import subprocess

    from mnemo._detach import detach_kwargs
    from mnemo._selfexec import self_argv

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    kwargs.update(detach_kwargs())

    subprocess.Popen(self_argv("sessions", "--consume-unblocks"), **kwargs)


def _maybe_consume_unblocks(cfg: dict, vault_root) -> None:
    """Learn from any session that was answered while it was blocked (#195).

    The sweep above records those edges; without this they were recorded and
    discarded. It runs after the sweep on purpose — an edge this very hook
    just recorded is redeemable in the same pass.

    Gated on ``briefings.enabled`` because consuming a marker *is* a briefing
    plus an extraction: a user who turned briefings off has opted out of the
    LLM calls this would make.

    Never raises: a missing transcript must not fail the hook.
    """
    try:
        from mnemo.core import errors as err_mod

        if not bool((cfg.get("briefings") or {}).get("enabled", False)):
            return
        from mnemo.core.sessions import detector, unblocks

        # Checked here rather than in the child so the common case — no
        # unblock to redeem — costs a file read instead of a process spawn.
        if not detector.pending_unblocks(vault_root=vault_root):
            return
        # A sweep already running will redeem these markers, and the child we
        # would spawn could only take its lock, find it held and exit. Skipping
        # the spawn is the same stat-then-don't-bother the extraction branch
        # above does; the lock inside the sweep is what actually enforces it
        # (#329), so a race here costs one wasted process, not a second pass.
        if unblocks.sweep_in_flight(vault_root):
            return
        try:
            _spawn_detached_unblock_consumption()
        except OSError as exc:
            err_mod.log_error(vault_root, "session_end.unblocks.popen", exc)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_end.unblocks", exc)
        except Exception:
            pass


def _maybe_schedule_propose(
    cfg: dict,
    vault_root,
    agent_name: str,
    *,
    session_id: str,
    cwd: str,
) -> None:
    """Run the end-of-session rule proposer when autopilot is active.

    The project is ``agent_name``, the name ``main()`` resolved for this
    session (session cache first) — not a fresh resolution of ``cwd``, which
    can already be a removed worktree by the time SessionEnd fires (#247; see
    :func:`_maybe_schedule_briefing`). ``cwd`` still goes to the analyzer for
    its git calls.

    Always stamps ``analyzed_at`` on the session cache after this returns —
    reaching SessionEnd is the user's intent to close the session, and the
    Tier 3 catchup (autopilot.core.scheduler) must respect that even when
    autopilot is currently disabled.

    A twin (#449) is marked but never analyzed, for the reason its briefing
    is held: its run may be the one never delivered.
    """
    from mnemo.core import errors as err_mod
    from mnemo.core import session as session_mod
    from mnemo.core import twins

    try:
        from mnemo.autopilot.core.kill_switch import is_active

        if twins.holds(vault_root, cwd):
            pass
        elif is_active(vault_root=vault_root):
            from mnemo.autopilot.proposer.eos_extractor import analyze_session

            cwd_path = __import__("pathlib").Path(cwd)
            try:
                analyze_session(
                    session_id=session_id,
                    project=agent_name,
                    vault_root=vault_root,
                    cwd=cwd_path,
                )
            except Exception as exc:
                err_mod.log_error(vault_root, "session_end.propose.analyze", exc)
    except Exception as exc:
        try:
            err_mod.log_error(vault_root, "session_end.propose", exc)
        except Exception:
            pass

    # Always mark — kill-switch off should still close the session for catchup.
    try:
        session_mod.mark_analyzed(session_id)
    except Exception as exc:
        try:
            err_mod.log_error(vault_root, "session_end.mark_analyzed", exc)
        except Exception:
            pass


def _notice_text(short_id: str, detail: str | None = None) -> str:
    """The one line a parent is told. Opens with the marker that keeps it out
    of the unblock population (:data:`inbox.NOTICE_PREFIX`).

    Deliberately thin. The child's PR number is not on ``Session`` — the queue
    resolves it elsewhere — and a notice that guessed at one would be the kind
    of unbacked report #306 exists to prevent. So this says the child exited
    and points at the command that knows the rest.
    """
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    tail = f" — {detail}" if detail else ""
    return (
        f'{NOTICE_PREFIX} id="{short_id}">\n'
        f"{short_id} finished{tail}. mnemo is reporting a dispatched child's exit; "
        f"this is not your user speaking. Run `mnemo sessions` for its state and PR."
    )


def _spawn_detached_child_report(short_id: str, *, parent: str, cwd: str, transcript):
    """Fire-and-forget ``mnemo child-report`` (#426). Detach semantics match
    :func:`_spawn_detached_briefing`; raises when the process cannot start, so
    the caller can fall back to the one-line notice. Returns the reporter's pid."""
    import subprocess

    from mnemo._detach import detach_kwargs
    from mnemo._selfexec import self_argv

    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    kwargs.update(detach_kwargs())

    argv = self_argv("child-report", short_id, "--parent", parent)
    if cwd:
        argv += ["--cwd", cwd]
    if transcript:
        argv += ["--transcript", str(transcript)]
    return subprocess.Popen(argv, **kwargs).pid


def _maybe_notify_parent(
    cfg, vault, *, session_id: str, cwd: str = "", transcript=None,
) -> None:
    """Tell the dispatching session that this child finished (#357).

    Only runs for a child that ``mnemo dispatch`` spawned: the parent link is
    the routing table, and a session nobody dispatched has nobody to tell.
    Delivery is best-effort by design — if the parent has exited, the notice
    is dropped rather than queued. This is a convenience, not a mailbox.

    Since #426 the notice is a report card — the child's PR, its checks, its
    closing report — built by a detached ``mnemo child-report``, because the
    ``gh`` calls behind it (and the wait for checks that follows) must not hold
    this hook open. If that process cannot start, the one-line notice goes out
    here instead, as before.
    """
    if not bool((cfg.get("dispatch") or {}).get("notifyParent", False)):
        return
    from mnemo.core.sessions import inbox, parents, report_card

    short_id = (session_id or "")[:8]
    if not short_id or short_id == "unknown"[:8]:
        return
    parent = parents.read(vault).get(short_id)
    if not parent:
        return
    row = {"short_id": short_id, "parent": parent, "event": "finished"}
    # A parent that has exited cannot be told anything; spawning a reporter
    # (and a half-hour watch) for it would be work with no reader. It is
    # still said, in the log (#454): this return used to be silent, and three
    # children of one live parent ended here on 2026-09-22.
    address, why = inbox.resolve(vault, parent)
    if address is None:
        report_card.undelivered(vault, row, why)
        return

    try:
        pid = _spawn_detached_child_report(
            short_id, parent=parent, cwd=cwd, transcript=transcript,
        )
        # The reporter writes its own row; this one says it was started, so a
        # reporter that dies before writing is a "spawned" with nothing after.
        report_card.record(vault, {**row, "event": "spawned", "pid": pid})
        return
    except Exception as exc:
        from mnemo.core import errors

        errors.log_error(vault, "session_end.child_report_spawn", exc)
        reason = f"reporter did not start ({type(exc).__name__}: {exc}); sent the one-line notice"
    why = inbox.deliver(vault, parent, _notice_text(short_id))
    if why:
        report_card.undelivered(vault, row, f"{reason}; that failed too: {why}")
    else:
        report_card.record(vault, {**row, "state": "thin", "delivered": True, "reason": reason})


def _maybe_follow_pr(cfg, vault, *, session_id: str, cwd: str) -> None:
    """Follow a dispatched child's PR after it stops (#436).

    Registers the stop and makes sure a ``mnemo pr-follow`` watcher is alive;
    the watcher, not this hook, reads the PR and decides whether to wake the
    child. A session nobody dispatched, or a child not granted ``push``, costs
    two small file reads here and nothing else.
    """
    from mnemo.core.sessions import pr_follow

    pr_follow.on_session_end(cfg, vault_root=vault, session_id=session_id, cwd=cwd)


def main() -> int:
    # Nothing at all inside a session mnemo launched for itself (#329): the
    # `claude --print` helpers that brief and extract run under the user's own
    # settings, mnemo's hooks included, so an unguarded hook here schedules the
    # work whose helper is running it. See :mod:`mnemo.core.hook_guard`.
    from mnemo.core.hook_guard import hooks_off, throwaway_session

    if hooks_off():
        return 0

    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, mirror, paths, session

        cfg = config.load_config()
        vault = paths.vault_root(cfg)
        # A session in a temp, pytest or job-scratch dir writes nothing into
        # a vault that outlives it (#420). See hook_guard.throwaway_session.
        if throwaway_session(payload.get("cwd") or os.getcwd(), vault):
            return 0
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
            # Canonical (#225): must agree with the name session_start caches,
            # since this is only the cache-miss path for the same session. The
            # day's log is written under this name.
            agent_name = agent.resolve_canonical_agent(cwd).name
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
            from mnemo.core.mcp import session_state as _ss
            _ss.evict_session(vault, sid)
        except Exception as e:
            errors.log_error(vault, "session_end.evict_reflex_state", e)
        try:
            _maybe_schedule_extraction(cfg, vault, agent_name)
        except Exception as e:
            errors.log_error(vault, "session_end.schedule_wrap", e)
        try:
            cwd = str(payload.get("cwd") or os.getcwd())
            _maybe_schedule_briefing(
                cfg, vault, agent_name, session_id=sid, cwd=cwd,
            )
        except Exception as e:
            errors.log_error(vault, "session_end.briefing_wrap", e)
        try:
            _maybe_sweep_sessions(vault)
        except Exception as e:
            errors.log_error(vault, "session_end.sweep_sessions_wrap", e)
        try:
            cwd = str(payload.get("cwd") or os.getcwd())
            transcript = payload.get("transcript_path") or _resolve_session_jsonl_path(sid, cwd)
            _maybe_notify_parent(
                cfg, vault, session_id=sid, cwd=cwd, transcript=transcript,
            )
        except Exception as e:
            errors.log_error(vault, "session_end.notify_parent", e)
        try:
            cwd = str(payload.get("cwd") or os.getcwd())
            _maybe_follow_pr(cfg, vault, session_id=sid, cwd=cwd)
        except Exception as e:
            errors.log_error(vault, "session_end.follow_pr", e)
        try:
            _maybe_consume_unblocks(cfg, vault)
        except Exception as e:
            errors.log_error(vault, "session_end.unblocks_wrap", e)
        try:
            cwd = str(payload.get("cwd") or os.getcwd())
            _maybe_schedule_propose(
                cfg, vault, agent_name, session_id=sid, cwd=cwd,
            )
        except Exception as e:
            errors.log_error(vault, "session_end.propose_wrap", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_end.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
