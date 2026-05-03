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
