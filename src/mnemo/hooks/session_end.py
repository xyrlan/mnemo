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
    """True while an extraction holds the extract lock.

    The same judgment ``run_extraction`` makes before taking it (#674): one
    small JSON read and a pid probe.
    """
    from mnemo.core import locks

    try:
        found = locks.holder(lock_path)
    except OSError:
        return False
    return found is not None and found.alive


def _spawn_detached_extraction() -> None:
    """Fire-and-forget background extraction.

    Started through :func:`mnemo._detach.spawn`, so the worker survives the
    hook's exit and is not a descendant of the hook when Claude Code kills a
    hook that ran past its ``SessionEnd`` bound (#475).
    """
    from mnemo._detach import spawn
    from mnemo._selfexec import self_argv

    spawn(self_argv("extract", "--background"))


def _given_transcript(transcript_path):
    """The hook payload's ``transcript_path`` when it names a file, else None."""
    from pathlib import Path

    if not isinstance(transcript_path, str) or not transcript_path:
        return None
    path = Path(transcript_path)
    return path if path.is_file() else None


def _resolve_session_jsonl_path(session_id: str, cwd: str):
    """Return the Claude Code session transcript path, or None if missing.

    The fallback for a payload with no ``transcript_path``. Claude Code
    stores sessions at
    ``<config dir>/projects/<encoded cwd>/<session-id>.jsonl``, the cwd
    encoded by :func:`mnemo.core.backfill.discover.encode_cwd`, the config dir
    ``$CLAUDE_CONFIG_DIR`` when set and ``~/.claude`` otherwise; both are
    tried. Until 2026-10-14 this replaced the path separators alone, which
    names no directory on Windows (the drive's colon stays) nor any cwd with
    a dot or an underscore in it: no briefing was ever scheduled for those
    sessions, and nothing said so.
    """
    from pathlib import Path

    from mnemo.core.backfill.discover import encode_cwd
    from mnemo.core.sessions import accounts

    if not session_id or not cwd:
        return None
    encoded = encode_cwd(cwd)
    homes = [Path(os.path.expanduser("~")) / ".claude"]
    here = accounts.current()
    if here is not None:
        homes.insert(0, here)
    for home in homes:
        path = home / "projects" / encoded / f"{session_id}.jsonl"
        if path.exists():
            return path
    return None


def _spawn_detached_briefing(jsonl_path, agent: str) -> None:
    """Fire-and-forget background briefing.

    Invokes `mnemo briefing <jsonl_path> <agent>`. Detach semantics match
    _spawn_detached_extraction so the child survives the hook's exit.
    """
    from mnemo._detach import spawn
    from mnemo._selfexec import self_argv

    spawn(self_argv("briefing", str(jsonl_path), agent))


def _maybe_schedule_briefing(
    cfg: dict,
    vault_root,
    agent_name: str,
    *,
    session_id: str,
    cwd: str,
    transcript_path=None,
    grok: bool = False,
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
    while the log line beside it went under ``bots/<repo>/``. The transcript,
    which outlives the tree, is the payload's ``transcript_path``; ``cwd``
    finds it only when the payload names none.

    Unlike extraction, briefings skip the count+time debounce — they are
    cheap and run on every session end so no handoff state is dropped.

    The spawned ``mnemo briefing`` decides what the session gets (#517): one
    with a file edit is briefed, one without gets the corrections-only pass,
    and either way its verified corrections land in the friction ledger. The
    decision reads the transcript, so it is made in the detached process and
    not here, under the 1.5 s bound.

    One exception (#449): a twin's briefing is held until the maintainer
    delivers it, and the twin never delivered is never briefed — see
    :mod:`mnemo.core.twins` for why the losing run must not teach the vault.

    ``grok``: this already runs in the detached worker a Grok SessionEnd
    starts (:func:`_grok_hand_off`), so the briefing runs here, to the end,
    and then the project's rules file is rewritten with it for the next Grok
    session (:mod:`mnemo.core.grok.briefing_file`).
    """
    try:
        from mnemo.core import errors as err_mod

        briefings_cfg = cfg.get("briefings") or {}
        if not bool(briefings_cfg.get("enabled", False)):
            return

        jsonl_path = (_given_transcript(transcript_path)
                      or _resolve_session_jsonl_path(session_id, cwd))
        if jsonl_path is None:
            return

        from mnemo.core import twins

        if twins.hold_briefing(vault_root, cwd=cwd, jsonl=jsonl_path, agent=agent_name):
            return

        if grok:
            _grok_brief_and_refresh(cfg, vault_root, jsonl_path, agent_name, cwd)
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


#: How long the Grok worker waits on its session's briefing before it gives up
#: on refreshing the rules file with it. A briefing is one model call.
GROK_BRIEFING_TIMEOUT_S = 900


def _run_briefing(jsonl_path, agent: str) -> None:
    """``mnemo briefing <jsonl_path> <agent>``, waited on."""
    import subprocess

    from mnemo._selfexec import self_argv

    subprocess.run(
        self_argv("briefing", str(jsonl_path), agent),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=GROK_BRIEFING_TIMEOUT_S, check=False,
    )


def _grok_brief_and_refresh(cfg: dict, vault_root, jsonl_path, agent: str, cwd: str) -> None:
    """Brief a finished Grok session, then hand that briefing to the next one.

    The rules file is rewritten even when the briefing fails: SessionStart
    wrote it from the vault as it was then, and this pass is never worse.
    """
    from pathlib import Path

    from mnemo.core import errors as err_mod
    from mnemo.core.grok import briefing_file

    try:
        _run_briefing(jsonl_path, agent)
    except Exception as exc:  # noqa: BLE001
        err_mod.log_error(vault_root, "session_end.grok_briefing", exc)
    try:
        briefing_file.refresh(vault_root, cfg, Path(cwd), agent)
    except Exception as exc:  # noqa: BLE001
        err_mod.log_error(vault_root, "session_end.grok_briefing_file", exc)


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
    from mnemo._detach import spawn
    from mnemo._selfexec import self_argv

    spawn(self_argv("sessions", "--consume-unblocks"))


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
    from mnemo._detach import spawn
    from mnemo._selfexec import self_argv

    argv = self_argv("child-report", short_id, "--parent", parent)
    if cwd:
        argv += ["--cwd", cwd]
    if transcript:
        argv += ["--transcript", str(transcript)]
    return spawn(argv)


def _maybe_notify_parent(
    cfg, vault, *, session_id: str, cwd: str = "", transcript=None, via: str = "",
    short: str = "",
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

    Since #502 this is also what :mod:`mnemo.core.sessions.child_notices`
    runs for a child that stopped with no notice — the hook never ran, or was
    killed before it got here. That caller passes ``via``, which every row it
    writes carries. The hook, in turn, stands down when such a row already
    answers this stop, so a hook that runs late never sends a second notice.
    """
    if not bool((cfg.get("dispatch") or {}).get("notifyParent", False)):
        return
    from mnemo.core.sessions import child_notices, inbox, parents, report_card

    # ``short``: a Grok session's key is the end of its id, not the start
    # (:func:`mnemo.core.grok.paths.short_id`).
    short_id = short or (session_id or "")[:8]
    if not short_id or short_id == "unknown"[:8]:
        return
    parent = parents.read(vault).get(short_id)
    if not parent:
        return
    # A Grok stop is told once by its claim (:func:`_claim_grok_finish`), and
    # its history has no turn times for the backstop's anchor to read.
    if not via and not short and child_notices.told_by_backstop(vault, short_id, transcript):
        return
    row = {"short_id": short_id, "parent": parent, "event": "finished"}
    if via:
        row["via"] = via
    # A parent that has exited cannot be told anything; spawning a reporter
    # (and a half-hour watch) for it would be work with no reader. It is
    # still said, in the log (#454): this return used to be silent, and three
    # children of one live parent ended here on 2026-09-22.
    address, why = inbox.resolve(vault, parent)
    if address is None:
        # Held, not dropped (#586): the parent sees it at its next prompt.
        report_card.undelivered(vault, row, why, text=_notice_text(short_id))
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
    text = _notice_text(short_id)
    why = inbox.deliver(vault, parent, text)
    if why:
        report_card.undelivered(vault, row, f"{reason}; that failed too: {why}", text=text)
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
    from mnemo.core.hook_guard import hooks_off

    if hooks_off():
        return 0

    # A Grok Build session ends here too (spec decision 7), in two steps: the
    # hook hands the payload to a worker, and the worker runs what follows.
    handed = _grok_handed_payload()
    if handed is not None:
        # An unreadable hand-off names no session: nothing to end.
        return _run(handed, grok=True) if handed else 0
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    grok = _grok_session(payload)
    if grok is None:
        return 0
    if grok:
        return _grok_hand_off(payload)
    return _run(payload)


#: Names the payload file of a Grok SessionEnd for the worker it hands off to.
GROK_PAYLOAD_ENV = "MNEMO_GROK_SESSION_END"

#: Keys a finish handed off by ``child_notices`` adds to the payload: the stop
#: it saw (its last turn's ``ts``) and the ``via`` its rows carry.
GROK_FINISH_KEY = "mnemo_finish_stamp"
GROK_VIA_KEY = "mnemo_via"
#: ``reason`` of a session that finished without a SessionEnd of its own.
GROK_FINISHED_REASON = "finished"


def _grok_session(payload: object):
    """``True`` for a Grok session's first copy of this event, ``False`` for
    a Claude payload, ``None`` for a Grok copy another hook process already
    has (:func:`mnemo.core.grok.hooks.claim_once`). Fail-open to ``False``."""
    try:
        from mnemo.core.grok import hooks as grok

        if not isinstance(payload, dict) or not grok.is_grok_payload(payload):
            return False
        return True if grok.claim_once(payload) else None
    except Exception:  # noqa: BLE001 — hook must never propagate
        return False


def _grok_hand_off(payload: dict) -> int:
    """Run a Grok session's end in a detached worker and return at once.

    Grok gives SessionEnd hooks 1.5 s (``GROK_SESSION_END_HOOKS_TIMEOUT_MS``)
    and the work below reads every session's state, mirrors memory and runs
    the proposer, so none of it runs here: the payload goes to a file, a
    worker (:func:`_spawn_grok_worker`) picks it up, and this returns.
    """
    try:
        _write_grok_payload(payload)
    except Exception:  # noqa: BLE001 — hook must never propagate
        pass
    return 0


def _write_grok_payload(payload: dict) -> None:
    """Write *payload* where a worker finds it, and start that worker."""
    import tempfile
    from pathlib import Path

    from mnemo.core.grok import hooks as grok

    sid = grok.session_id(payload) or "unknown"
    folder = Path(tempfile.gettempdir()) / "mnemo" / "grok-session-end"
    folder.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=str(folder), prefix=sid[-12:] + ".", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    _spawn_grok_worker(name)


def hand_off_grok_finish(*, session_id: str, cwd: str, history=None, stamp: str,
                         via: str = "") -> None:
    """Run the end of a Grok child that finished with no SessionEnd (#502's
    second channel, for Grok: a dormant session fires none).

    The same worker a Grok ``SessionEnd`` hands off to runs it, over the
    payload that hook would have carried, plus the stop it is for: the worker
    claims that stop first (:func:`_claim_grok_finish`), so it runs once
    however many hands it reaches. Raises when the worker cannot start.
    """
    from pathlib import Path

    from mnemo.core.grok import paths as grok_paths

    payload = {
        "hook_event_name": "SessionEnd",
        "session_id": session_id,
        "cwd": cwd,
        "reason": GROK_FINISHED_REASON,
        GROK_FINISH_KEY: stamp,
    }
    if history:
        payload["transcript_path"] = str(Path(str(history)).with_name(grok_paths.UPDATES))
    if via:
        payload[GROK_VIA_KEY] = via
    _write_grok_payload(payload)


def _claim_grok_finish(vault, payload: dict, sid: str, short: str, history) -> bool:
    """Whether this run is the one that ends this Grok stop. Once per stop.

    The stop is the session's last turn event (``child_notices.grok_turn``),
    named by its ``ts``; a finish ``child_notices`` handed off names the one
    it saw. Two watchers, a restart, or a SessionEnd that fires when the
    session is later closed find the stop claimed and stand down. A session
    whose turns cannot be read has no stop to name, and runs, as before.
    """
    from pathlib import Path

    from mnemo.core.sessions import child_notices

    stamp = payload.get(GROK_FINISH_KEY)
    if not isinstance(stamp, str) or not stamp:
        turn = child_notices.grok_turn(Path(str(history)).parent if history else None)
        stamp = turn.stamp if turn is not None else ""
    if not stamp or not short:
        return True
    via = payload.get(GROK_VIA_KEY)
    return child_notices.claim_finish(
        vault, short, stamp, via=via if isinstance(via, str) and via else "session_end",
    )


def _spawn_grok_worker(payload_file: str) -> None:
    """Fire-and-forget ``mnemo hook session_end`` over ``payload_file``."""
    from mnemo._detach import spawn
    from mnemo._selfexec import self_argv

    os.environ[GROK_PAYLOAD_ENV] = payload_file
    try:
        spawn(self_argv("hook", "session_end"))
    finally:
        os.environ.pop(GROK_PAYLOAD_ENV, None)


def _grok_handed_payload():
    """The payload a Grok SessionEnd handed this worker, read once; else None."""
    name = os.environ.pop(GROK_PAYLOAD_ENV, "")
    if not name:
        return None
    from pathlib import Path

    path = Path(name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return payload if isinstance(payload, dict) else {}


def _grok_history(payload: dict):
    """A Grok session's ``chat_history.jsonl``: beside the ``updates.jsonl``
    its ``transcript_path`` names, else found by session id. None if neither."""
    from pathlib import Path

    from mnemo.core.grok import paths as grok_paths

    named = payload.get("transcript_path") or payload.get("transcriptPath")
    if isinstance(named, str) and named:
        beside = Path(named).with_name(grok_paths.CHAT_HISTORY)
        if beside.is_file():
            return beside
    from mnemo.core.grok import briefing_file

    sid = str(payload.get("session_id") or payload.get("sessionId") or "")
    where = briefing_file.session_dir(sid, str(payload.get("cwd") or "")) if sid else None
    if where is not None and grok_paths.chat_history_path(where).is_file():
        return grok_paths.chat_history_path(where)
    return None


def _run(payload: dict, grok: bool = False) -> int:
    """The end of one session. ``grok``: a Grok session's, in its worker."""
    from mnemo.core.hook_guard import throwaway_session

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
        # #643: a satellite writes only its own briefings. The mirror,
        # extraction and the autopilot's proposer stand down there; the
        # briefing below still runs.
        from mnemo.core.sync import roles as sync_roles
        satellite = sync_roles.is_satellite(vault, cfg)
        # A Grok session's transcript is its chat_history.jsonl, never the
        # updates.jsonl its payload names, and its key is its id's end.
        grok_history = None
        grok_short = ""
        if grok:
            try:
                from mnemo.core.grok import hooks as grok_hooks
                from mnemo.core.grok import paths as grok_paths
                grok_hooks.drop(vault, sid)
                grok_short = grok_paths.short_id(sid) if sid != "unknown" else ""
                grok_history = _grok_history(payload)
            except Exception as e:
                errors.log_error(vault, "session_end.grok_transcript", e)
        # A Grok child's end runs once per stop, from whichever of its
        # SessionEnd and child_notices' watcher gets here first: everything
        # below is that end — the day log's line, the parent's card, the PR
        # follow, extraction, the briefing, the proposer — and the other one
        # skips it.
        ends = True
        if grok:
            try:
                ends = _claim_grok_finish(vault, payload, sid, grok_short, grok_history)
            except Exception as e:
                errors.log_error(vault, "session_end.grok_finish", e)
        if not ends:
            return 0
        try:
            if not satellite:
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
        # The parent's notice and the PR follow go first of the scheduled work
        # (#502): the hook runs under a 1.5 s bound on a lean child, and the
        # sweep and schedulers below read every session's state. What a hook
        # killed past this point loses is a briefing, not the parent's news —
        # and a stop whose hook never got here is child_notices' to report.
        try:
            cwd = str(payload.get("cwd") or os.getcwd())
            if grok:
                transcript = str(grok_history) if grok_history is not None else None
                via = payload.get(GROK_VIA_KEY)
                _maybe_notify_parent(
                    cfg, vault, session_id=sid, cwd=cwd, transcript=transcript,
                    short=grok_short, via=via if isinstance(via, str) else "",
                )
            else:
                transcript = (payload.get("transcript_path")
                              or _resolve_session_jsonl_path(sid, cwd))
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
            if not satellite:
                _maybe_schedule_extraction(cfg, vault, agent_name)
        except Exception as e:
            errors.log_error(vault, "session_end.schedule_wrap", e)
        try:
            cwd = str(payload.get("cwd") or os.getcwd())
            if grok:
                if grok_history is not None:
                    _maybe_schedule_briefing(
                        cfg, vault, agent_name, session_id=sid, cwd=cwd,
                        transcript_path=str(grok_history), grok=True,
                    )
            else:
                _maybe_schedule_briefing(
                    cfg, vault, agent_name, session_id=sid, cwd=cwd,
                    transcript_path=payload.get("transcript_path"),
                )
        except Exception as e:
            errors.log_error(vault, "session_end.briefing_wrap", e)
        try:
            _maybe_sweep_sessions(vault)
        except Exception as e:
            errors.log_error(vault, "session_end.sweep_sessions_wrap", e)
        try:
            _maybe_consume_unblocks(cfg, vault)
        except Exception as e:
            errors.log_error(vault, "session_end.unblocks_wrap", e)
        try:
            cwd = str(payload.get("cwd") or os.getcwd())
            if satellite:
                session.mark_analyzed(sid)
            else:
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
