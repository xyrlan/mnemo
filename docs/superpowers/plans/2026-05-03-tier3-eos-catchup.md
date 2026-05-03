# Tier 3 EoS Scheduler Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover Tier 3 rule extraction for crashed Claude Code sessions via a daily inline catchup operation in the hook-driven scheduler.

**Architecture:** SessionEnd stamps `analyzed_at` into the per-session cache file. A new `tier3.eos-catchup` scheduler operation iterates unanalyzed cache entries (≤26h old), groups by cwd, and re-runs `analyze_session` per project. `cleanup_stale` retention bumped 24h→48h to buffer the catchup window. No new modules, no schema bumps.

**Tech Stack:** Python 3.10+, pytest, mnemo internals (`core.session`, `autopilot.core.scheduler`, `autopilot.proposer.eos_extractor`).

**Spec:** `docs/superpowers/specs/2026-05-03-tier3-eos-catchup-design.md`
**Branch:** `feat/tier3-eos-catchup` (already created, spec committed at `6965473`)

---

## File Structure

| File | Responsibility | Status |
|---|---|---|
| `src/mnemo/core/session.py` | Add `mark_analyzed`, `iter_unanalyzed` | modify |
| `src/mnemo/hooks/session_start.py` | Bump `cleanup_stale(48*3600)` | modify |
| `src/mnemo/hooks/session_end.py` | Call `mark_analyzed` after propose | modify |
| `src/mnemo/autopilot/core/scheduler.py` | Register `tier3.eos-catchup` op + status | modify |
| `tests/unit/test_session.py` | Cover `mark_analyzed` + `iter_unanalyzed` | extend |
| `tests/unit/test_session_end_schedule.py` | `mark_analyzed` called after propose | extend |
| `tests/unit/test_session_start_*` (one fitting file) | Verify 48h retention | extend |
| `tests/autopilot/core/test_scheduler.py` | `tier3.eos-catchup` in registry + status | extend |
| `tests/autopilot/proposer/test_eos_catchup.py` | Catchup integration | new |

---

## Task 1: `session.mark_analyzed` + `iter_unanalyzed`

**Files:**
- Modify: `src/mnemo/core/session.py`
- Test: `tests/unit/test_session.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_session.py`:

```python
import time
from datetime import datetime, timezone
from mnemo.core import session as session_mod


def test_mark_analyzed_stamps_iso_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-1", {"name": "proj", "started_at": "2026-05-03T10:00:00"})
    session_mod.mark_analyzed("sid-1")
    loaded = session_mod.load("sid-1")
    assert "analyzed_at" in loaded
    # Parses as ISO-8601
    datetime.fromisoformat(loaded["analyzed_at"].replace("Z", "+00:00"))
    # Other fields preserved
    assert loaded["name"] == "proj"
    assert loaded["started_at"] == "2026-05-03T10:00:00"


def test_mark_analyzed_noop_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    # Should not raise
    session_mod.mark_analyzed("nonexistent-sid")


def test_iter_unanalyzed_returns_recent_unmarked(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-fresh", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    session_mod.save("sid-marked", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    session_mod.mark_analyzed("sid-marked")

    entries = session_mod.iter_unanalyzed(max_age_seconds=26 * 3600)
    sids = {e["session_id"] for e in entries}
    assert sids == {"sid-fresh"}


def test_iter_unanalyzed_filters_by_age(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-old", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    # Backdate the file's mtime beyond the window
    f = tmp_path / "session-sid-old.json"
    old_ts = time.time() - (48 * 3600)
    import os as _os
    _os.utime(f, (old_ts, old_ts))

    session_mod.save("sid-new", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})

    entries = session_mod.iter_unanalyzed(max_age_seconds=26 * 3600)
    sids = {e["session_id"] for e in entries}
    assert sids == {"sid-new"}


def test_iter_unanalyzed_skips_malformed_files(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    (tmp_path / "session-broken.json").write_text("not-json{{{")
    session_mod.save("sid-ok", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})

    entries = session_mod.iter_unanalyzed(max_age_seconds=26 * 3600)
    sids = {e["session_id"] for e in entries}
    assert sids == {"sid-ok"}


def test_iter_unanalyzed_includes_session_id_field(tmp_path, monkeypatch):
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: tmp_path)
    session_mod.save("sid-xyz", {"name": "p", "started_at": "x", "cwd_at_start": "/a"})
    entries = session_mod.iter_unanalyzed()
    assert len(entries) == 1
    assert entries[0]["session_id"] == "sid-xyz"
    assert entries[0]["name"] == "p"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/test_session.py -v -k "mark_analyzed or iter_unanalyzed"`
Expected: FAIL — `mark_analyzed` / `iter_unanalyzed` not defined.

- [ ] **Step 3: Implement**

Append to `src/mnemo/core/session.py`:

```python
from datetime import datetime, timezone


def mark_analyzed(session_id: str) -> None:
    """Stamp ``analyzed_at`` (ISO-8601 UTC, suffix Z) into the cache file.

    No-op if the cache file does not exist or is unreadable. Atomic replace
    matches the ``save`` pattern.
    """
    info = load(session_id)
    if info is None:
        return
    info["analyzed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save(session_id, info)


def iter_unanalyzed(max_age_seconds: float = 26 * 3600) -> list[dict[str, Any]]:
    """Return cache entries with no ``analyzed_at`` and mtime within window.

    Each returned dict has ``session_id`` injected (parsed from filename).
    Malformed/unreadable files are skipped silently.
    """
    cache_dir = _cache_dir()
    if not cache_dir.exists():
        return []
    cutoff = time.time() - max_age_seconds
    out: list[dict[str, Any]] = []
    for f in cache_dir.glob("session-*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                continue
            data = json.loads(f.read_text())
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if "analyzed_at" in data:
            continue
        # Recover session_id from filename: "session-<safe>.json"
        sid = f.stem[len("session-"):]
        data["session_id"] = sid
        out.append(data)
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/test_session.py -v -k "mark_analyzed or iter_unanalyzed"`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/session.py tests/unit/test_session.py
git commit -m "feat(session): add mark_analyzed + iter_unanalyzed for catchup"
```

---

## Task 2: SessionStart bumps `cleanup_stale` to 48h

**Files:**
- Modify: `src/mnemo/hooks/session_start.py:190`
- Test: `tests/unit/test_session_start_telemetry.py` (or create `tests/unit/test_session_start_cleanup.py` if no fitting existing test)

- [ ] **Step 1: Locate the current call**

Run: `grep -n cleanup_stale src/mnemo/hooks/session_start.py`
Expected: one line, `session.cleanup_stale()` (no arg).

- [ ] **Step 2: Write failing test**

Create `tests/unit/test_session_start_cleanup.py`:

```python
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import session_start


def test_session_start_bumps_cleanup_to_48h(tmp_path, monkeypatch):
    """Catchup window is 26h; cleanup must retain at least that long."""
    payload = json.dumps({"session_id": "sid-test", "cwd": str(tmp_path), "source": "startup"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    monkeypatch.setattr("sys.stdout", io.StringIO())

    with patch("mnemo.core.session.cleanup_stale") as cleanup:
        session_start.main()

    cleanup.assert_called_once()
    kwargs = cleanup.call_args.kwargs
    args = cleanup.call_args.args
    arg_value = kwargs.get("max_age_seconds") if kwargs else (args[0] if args else None)
    assert arg_value == 48 * 3600
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/unit/test_session_start_cleanup.py -v`
Expected: FAIL — `cleanup_stale` called with no args (current behavior).

- [ ] **Step 4: Implement**

Edit `src/mnemo/hooks/session_start.py` line ~190:

```python
            session.cleanup_stale(max_age_seconds=48 * 3600)
```

(Replace `session.cleanup_stale()` with the keyword call.)

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/unit/test_session_start_cleanup.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/hooks/session_start.py tests/unit/test_session_start_cleanup.py
git commit -m "feat(session_start): retain session cache 48h to buffer EoS catchup"
```

---

## Task 3: SessionEnd marks session as analyzed

**Files:**
- Modify: `src/mnemo/hooks/session_end.py` (in `_maybe_schedule_propose`)
- Test: `tests/unit/test_session_end_schedule.py` (extend)

- [ ] **Step 1: Read current `_maybe_schedule_propose`**

Run: `sed -n '243,290p' src/mnemo/hooks/session_end.py`
Confirm: function exists, ends after `try/except` swallowing.

- [ ] **Step 2: Write failing tests**

Append to `tests/unit/test_session_end_schedule.py`:

```python
from unittest.mock import patch

from mnemo.hooks import session_end as se_mod


def test_maybe_schedule_propose_marks_analyzed_on_success(tmp_path):
    with patch("mnemo.autopilot.core.kill_switch.is_active", return_value=True), \
         patch("mnemo.autopilot.proposer.eos_extractor.analyze_session") as analyze, \
         patch("mnemo.core.agent.resolve_canonical_agent") as resolve, \
         patch("mnemo.core.session.mark_analyzed") as mark:
        resolve.return_value.name = "proj-x"
        se_mod._maybe_schedule_propose(
            cfg={}, vault_root=tmp_path, agent_name="proj-x",
            session_id="sid-success", cwd=str(tmp_path),
        )
    analyze.assert_called_once()
    mark.assert_called_once_with("sid-success")


def test_maybe_schedule_propose_marks_analyzed_when_kill_switch_off(tmp_path):
    """Reaching SessionEnd is the user's intent — respect it even if autopilot off."""
    with patch("mnemo.autopilot.core.kill_switch.is_active", return_value=False), \
         patch("mnemo.core.session.mark_analyzed") as mark:
        se_mod._maybe_schedule_propose(
            cfg={}, vault_root=tmp_path, agent_name="proj-x",
            session_id="sid-off", cwd=str(tmp_path),
        )
    mark.assert_called_once_with("sid-off")


def test_maybe_schedule_propose_swallows_mark_analyzed_failure(tmp_path):
    with patch("mnemo.autopilot.core.kill_switch.is_active", return_value=True), \
         patch("mnemo.autopilot.proposer.eos_extractor.analyze_session"), \
         patch("mnemo.core.agent.resolve_canonical_agent") as resolve, \
         patch("mnemo.core.session.mark_analyzed", side_effect=OSError("boom")):
        resolve.return_value.name = "proj-x"
        # Must not raise
        se_mod._maybe_schedule_propose(
            cfg={}, vault_root=tmp_path, agent_name="proj-x",
            session_id="sid-mark-fail", cwd=str(tmp_path),
        )
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/unit/test_session_end_schedule.py -v -k "marks_analyzed or swallows_mark"`
Expected: FAIL — `mark_analyzed` not called.

- [ ] **Step 4: Implement**

Edit `src/mnemo/hooks/session_end.py`. Restructure `_maybe_schedule_propose` so `mark_analyzed` is called regardless of kill-switch state, and its failure is swallowed:

```python
def _maybe_schedule_propose(
    cfg: dict,
    vault_root,
    agent_name: str,
    *,
    session_id: str,
    cwd: str,
) -> None:
    """Run the end-of-session rule proposer when autopilot is active.

    Always stamps ``analyzed_at`` on the session cache after this returns —
    reaching SessionEnd is the user's intent to close the session, and the
    Tier 3 catchup (autopilot.core.scheduler) must respect that even when
    autopilot is currently disabled.
    """
    from mnemo.core import errors as err_mod
    from mnemo.core import session as session_mod

    try:
        from mnemo.autopilot.core.kill_switch import is_active

        if is_active(vault_root=vault_root):
            from mnemo.autopilot.proposer.eos_extractor import analyze_session
            from mnemo.core import agent as agent_mod

            cwd_path = __import__("pathlib").Path(cwd)
            try:
                project = agent_mod.resolve_canonical_agent(cwd).name
            except Exception:
                project = agent_name

            try:
                analyze_session(
                    session_id=session_id,
                    project=project,
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/unit/test_session_end_schedule.py -v`
Expected: all tests in this file PASS (including pre-existing).

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/hooks/session_end.py tests/unit/test_session_end_schedule.py
git commit -m "feat(session_end): mark session analyzed for tier3 catchup"
```

---

## Task 4: Catchup runner — `_eos_catchup_inline`

**Files:**
- Modify: `src/mnemo/autopilot/core/scheduler.py`
- Test: `tests/autopilot/proposer/test_eos_catchup.py` (new)

- [ ] **Step 1: Write failing tests**

Create `tests/autopilot/proposer/test_eos_catchup.py`:

```python
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from mnemo.core import session as session_mod


def _mk_entry(tmp_cache, sid, cwd, started_at="2026-05-03T10:00:00Z"):
    """Helper: write a session cache file routed via monkeypatched _cache_dir."""
    session_mod.save(sid, {
        "name": "agent-x",
        "started_at": started_at,
        "cwd_at_start": str(cwd),
    })


def test_catchup_runs_once_per_distinct_cwd(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: cache_dir)

    cwd_a = tmp_path / "proj-a"
    cwd_b = tmp_path / "proj-b"
    cwd_a.mkdir()
    cwd_b.mkdir()

    _mk_entry(cache_dir, "sid-a1", cwd_a, started_at="2026-05-03T09:00:00Z")
    _mk_entry(cache_dir, "sid-a2", cwd_a, started_at="2026-05-03T08:00:00Z")
    _mk_entry(cache_dir, "sid-b1", cwd_b, started_at="2026-05-03T11:00:00Z")

    from mnemo.autopilot.core.scheduler import _eos_catchup_inline

    with patch("mnemo.autopilot.proposer.eos_extractor.analyze_session") as analyze, \
         patch("mnemo.core.agent.resolve_canonical_agent") as resolve:
        resolve.side_effect = lambda c: type("A", (), {"name": Path(c).name})()
        _eos_catchup_inline(tmp_path)

    assert analyze.call_count == 2
    cwds_called = sorted(str(c.kwargs["cwd"]) for c in analyze.call_args_list)
    assert cwds_called == sorted([str(cwd_a), str(cwd_b)])
    # cwd_a window starts at the EARLIEST of its two entries
    a_call = next(c for c in analyze.call_args_list if str(c.kwargs["cwd"]) == str(cwd_a))
    assert a_call.kwargs["session_start_iso"] == "2026-05-03T08:00:00Z"


def test_catchup_marks_all_entries_in_cwd_on_success(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: cache_dir)

    cwd_a = tmp_path / "proj-a"
    cwd_a.mkdir()
    _mk_entry(cache_dir, "sid-a1", cwd_a)
    _mk_entry(cache_dir, "sid-a2", cwd_a)

    from mnemo.autopilot.core.scheduler import _eos_catchup_inline

    with patch("mnemo.autopilot.proposer.eos_extractor.analyze_session"), \
         patch("mnemo.core.agent.resolve_canonical_agent") as resolve:
        resolve.side_effect = lambda c: type("A", (), {"name": "p"})()
        _eos_catchup_inline(tmp_path)

    assert "analyzed_at" in session_mod.load("sid-a1")
    assert "analyzed_at" in session_mod.load("sid-a2")


def test_catchup_skips_missing_cwd(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: cache_dir)

    cwd_real = tmp_path / "real"
    cwd_real.mkdir()
    _mk_entry(cache_dir, "sid-real", cwd_real)
    _mk_entry(cache_dir, "sid-gone", tmp_path / "deleted-nowhere")

    from mnemo.autopilot.core.scheduler import _eos_catchup_inline

    with patch("mnemo.autopilot.proposer.eos_extractor.analyze_session") as analyze, \
         patch("mnemo.core.agent.resolve_canonical_agent") as resolve:
        resolve.side_effect = lambda c: type("A", (), {"name": "p"})()
        _eos_catchup_inline(tmp_path)

    assert analyze.call_count == 1
    assert str(analyze.call_args.kwargs["cwd"]) == str(cwd_real)


def test_catchup_per_cwd_failure_isolation(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: cache_dir)

    cwd_a = tmp_path / "fails"
    cwd_b = tmp_path / "ok"
    cwd_a.mkdir()
    cwd_b.mkdir()
    _mk_entry(cache_dir, "sid-a", cwd_a)
    _mk_entry(cache_dir, "sid-b", cwd_b)

    from mnemo.autopilot.core.scheduler import _eos_catchup_inline

    def _analyze(**kw):
        if str(kw["cwd"]) == str(cwd_a):
            raise RuntimeError("boom")

    with patch("mnemo.autopilot.proposer.eos_extractor.analyze_session", side_effect=_analyze), \
         patch("mnemo.core.agent.resolve_canonical_agent") as resolve:
        resolve.side_effect = lambda c: type("A", (), {"name": "p"})()
        _eos_catchup_inline(tmp_path)

    # cwd_b succeeded → marked; cwd_a failed → not marked
    assert "analyzed_at" not in session_mod.load("sid-a")
    assert "analyzed_at" in session_mod.load("sid-b")


def test_catchup_synthetic_session_id_prefix(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(session_mod, "_cache_dir", lambda: cache_dir)

    cwd_a = tmp_path / "proj"
    cwd_a.mkdir()
    _mk_entry(cache_dir, "sid-original", cwd_a)

    from mnemo.autopilot.core.scheduler import _eos_catchup_inline

    with patch("mnemo.autopilot.proposer.eos_extractor.analyze_session") as analyze, \
         patch("mnemo.core.agent.resolve_canonical_agent") as resolve:
        resolve.side_effect = lambda c: type("A", (), {"name": "p"})()
        _eos_catchup_inline(tmp_path)

    assert analyze.call_args.kwargs["session_id"] == "catchup-sid-original"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/autopilot/proposer/test_eos_catchup.py -v`
Expected: FAIL — `_eos_catchup_inline` not defined.

- [ ] **Step 3: Implement**

Add to `src/mnemo/autopilot/core/scheduler.py` (after `_collect_misses_inline`):

```python
def _eos_catchup_inline(vault_root: Path) -> None:
    """Tier 3 fallback: re-run analyze_session for sessions that crashed.

    Iterates session-cache entries lacking ``analyzed_at`` (≤26h old),
    groups by cwd, calls ``analyze_session`` once per cwd using the earliest
    ``started_at`` in that group as the git window start. On success, stamps
    ``analyzed_at`` on every entry in the group. Per-cwd error-isolated.
    """
    from mnemo.core import agent as agent_mod
    from mnemo.core import errors as err_mod
    from mnemo.core import session as session_mod
    from mnemo.autopilot.proposer.eos_extractor import analyze_session

    by_cwd: dict[str, list[dict]] = {}
    for entry in session_mod.iter_unanalyzed(max_age_seconds=26 * 3600):
        cwd = entry.get("cwd_at_start")
        if not cwd or not Path(cwd).exists():
            continue
        by_cwd.setdefault(cwd, []).append(entry)

    for cwd, entries in by_cwd.items():
        earliest_iso = min(e["started_at"] for e in entries)
        try:
            project = agent_mod.resolve_canonical_agent(cwd).name
            analyze_session(
                session_id=f"catchup-{entries[0]['session_id']}",
                project=project,
                vault_root=vault_root,
                cwd=Path(cwd),
                session_start_iso=earliest_iso,
            )
            for e in entries:
                session_mod.mark_analyzed(e["session_id"])
        except Exception as exc:
            err_mod.log_error(vault_root, "autopilot.tier3.catchup", exc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/autopilot/proposer/test_eos_catchup.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/scheduler.py tests/autopilot/proposer/test_eos_catchup.py
git commit -m "feat(autopilot): add tier3 EoS catchup runner"
```

---

## Task 5: Register `tier3.eos-catchup` in scheduler + status

**Files:**
- Modify: `src/mnemo/autopilot/core/scheduler.py` (`run_due_jobs` and `status_summary`)
- Test: `tests/autopilot/core/test_scheduler.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/autopilot/core/test_scheduler.py`:

```python
def test_tier3_eos_catchup_fires_when_due(tmp_path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")

    catchup_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._eos_catchup_inline",
        lambda vr: catchup_calls.append(vr),
    )
    # Stub the others to keep test focused
    monkeypatch.setattr("mnemo.autopilot.core.scheduler._digest_inline", lambda vr: None)
    monkeypatch.setattr("mnemo.autopilot.core.scheduler._collect_misses_inline", lambda vr: None)
    monkeypatch.setattr("mnemo.autopilot.core.scheduler.run_detached", lambda **kw: None)

    out = run_due_jobs(vault_root=tmp_path)
    fired_names = [n for (n, _mode, _ok) in out["fired"]]
    assert "tier3.eos-catchup" in fired_names
    assert catchup_calls == [tmp_path]


def test_tier3_eos_catchup_in_status_summary(tmp_path):
    out = status_summary(vault_root=tmp_path)
    names = [r["name"] for r in out]
    assert "tier3.eos-catchup" in names
    rec = next(r for r in out if r["name"] == "tier3.eos-catchup")
    assert rec["interval_days"] == 1
```

Also extend the existing `test_run_due_skips_recently_run` to mark the new op as just-run. Edit:

```python
    for name in ("tier0.digest", "tier0.collect-misses", "tier1.doctor",
                 "tier1.sweep", "tier1.telemetry", "tier2.bm25", "tier2.reflex",
                 "tier3.eos-catchup"):
        mark_run(vault_root=tmp_path, name=name)
```

And add the catchup stub in the same test:

```python
    catchup_calls = []
    monkeypatch.setattr(
        "mnemo.autopilot.core.scheduler._eos_catchup_inline",
        lambda vr: catchup_calls.append(vr),
    )
```

And finally extend `test_run_due_inline_jobs_fire_on_first_run` with the same stub (so it doesn't actually run catchup and produce side-effects).

- [ ] **Step 2: Run tests to verify failures**

Run: `pytest tests/autopilot/core/test_scheduler.py -v`
Expected: 2 new tests FAIL (op not registered); existing tests still pass.

- [ ] **Step 3: Implement**

Edit `src/mnemo/autopilot/core/scheduler.py`:

In `run_due_jobs`, after the `tier0.collect-misses` block and before the `tier1.doctor` block, add:

```python
    if should_run(vault_root=vault_root, name="tier3.eos-catchup", interval_days=1):
        ok = run_inline(
            vault_root=vault_root,
            name="tier3.eos-catchup",
            fn=lambda: _eos_catchup_inline(vault_root),
        )
        fired.append(("tier3.eos-catchup", "inline", ok))
```

In `status_summary`, append to `operations`:

```python
        ("tier3.eos-catchup", 1),
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `pytest tests/autopilot/core/test_scheduler.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/scheduler.py tests/autopilot/core/test_scheduler.py
git commit -m "feat(autopilot): register tier3.eos-catchup in scheduler + status"
```

---

## Task 6: Full suite + smoke

- [ ] **Step 1: Run full test suite**

Run: `pytest -q`
Expected: all green; suite count up by ~14 (6 + 1 + 3 + 5 + 2 = 17, minus any collisions). Zero failures.

- [ ] **Step 2: CLI smoke — status shows new op**

Run: `python -m mnemo autopilot on && python -m mnemo autopilot status`
Expected: output lists `tier3.eos-catchup` with interval 1 and `due: True`.

- [ ] **Step 3: Smoke — catchup runs without error on a vault with no unanalyzed sessions**

Run: `python -c "from pathlib import Path; from mnemo.autopilot.core.scheduler import _eos_catchup_inline; _eos_catchup_inline(Path('/tmp/mnemo-smoke-vault'))"`
Expected: no output, no error (no-op when nothing to do).

- [ ] **Step 4: Restore kill-switch state**

Run: `python -m mnemo autopilot off`

- [ ] **Step 5: Push branch + open PR**

```bash
git push -u origin feat/tier3-eos-catchup
gh pr create --title "feat(autopilot): tier3 EoS scheduler fallback" --body "$(cat <<'EOF'
## Summary
- Daily inline `tier3.eos-catchup` operation in the hook-driven scheduler
- Recovers Tier 3 rule extraction for crashed sessions (no SessionEnd fired)
- Per-cwd grouping → catches cross-project crashes
- Idempotent via existing proposer dedup + new `analyzed_at` marker
- `cleanup_stale` retention bumped 24h → 48h to buffer the 26h catchup window

Spec: `docs/superpowers/specs/2026-05-03-tier3-eos-catchup-design.md`

## Test plan
- [x] Unit: `mark_analyzed` + `iter_unanalyzed` (6 tests)
- [x] Unit: SessionEnd marks analyzed even with kill-switch off
- [x] Unit: SessionStart cleanup_stale bumped to 48h
- [x] Unit: `tier3.eos-catchup` registered in run_due_jobs + status_summary
- [x] Integration: catchup groups by cwd, marks all entries, isolates per-cwd failures
- [x] Smoke: `mnemo autopilot status` shows new op
- [x] Full suite green

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

**Spec coverage:**
- `mark_analyzed` + `iter_unanalyzed` → Task 1 ✓
- 48h cleanup_stale → Task 2 ✓
- session_end marks analyzed (incl. kill-switch off) → Task 3 ✓
- `_eos_catchup_inline` per-cwd grouping, earliest start, mark on success, isolation → Task 4 ✓
- Synthetic `catchup-<sid>` session id → Task 4 ✓
- Skip nonexistent cwd → Task 4 ✓
- Register in `run_due_jobs` + `status_summary` with interval=1 → Task 5 ✓
- All tests from spec's "Testing" section → covered across Tasks 1, 2, 3, 4, 5 ✓

**Placeholder scan:** none — all tests show full code, all impls show full code, all commands explicit.

**Type consistency:** `mark_analyzed(session_id: str) -> None`, `iter_unanalyzed(max_age_seconds: float) -> list[dict]`, `_eos_catchup_inline(vault_root: Path) -> None` — used identically across all tasks.
