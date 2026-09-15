"""Retiring unblock markers that can never be redeemed, and only those.

Measured on the real vault (2026-09-15): 27 markers pending, every one retried
on every pass. All 27 had a ``cwd`` that ``learn`` cannot resolve to the
project owning the transcript (26 deleted worktrees, one live worktree whose
dashed name does not round-trip the projects-dir encoding) — and all 27
transcripts were still on disk. So "learn returned an error" is exactly the
wrong key: it would retire 27 learnable answers. The key is the transcript
file itself being gone from every project directory.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.sessions import detector, unblocks


class _Report:
    def __init__(self, *, error: str = "", learned=None):
        self.error = error
        self.learned = learned or []


NOT_FOUND = "no transcript with session id {sid} for this directory"


@pytest.fixture
def projects(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "claude" / "projects"
    root.mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.backfill.discover.projects_root", lambda: root)
    return root


def _write_marker(vault: Path, *, session_id: str, link_scan_path: str,
                  cwd: str = "/Users/x/github/mnemo-wt-200", short_id: str = "9293fe7b") -> None:
    """The marker shape the detector writes, copied from the real file."""
    state = {"seen": {short_id: {"last_tempo": "active", "unblocks": [{
        "at": "2026-09-12T22:49:08+00:00",
        "answered_at": "2026-09-12T22:49:01.000Z",
        "answer": "yes, go ahead",
        "needs": "should I push?",
        "session_id": session_id,
        "link_scan_path": link_scan_path,
        "cwd": cwd,
        "extracted": False,
    }]}}}
    path = vault / ".mnemo" / detector.STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _marker(vault: Path, short_id: str = "9293fe7b") -> dict:
    state = json.loads((vault / ".mnemo" / detector.STATE_FILENAME).read_text(encoding="utf-8"))
    return state["seen"][short_id]["unblocks"][0]


def _fail_learn(monkeypatch, sid: str) -> list:
    calls: list = []

    def _learn(cfg, *, cwd, session_id, **k):
        calls.append(session_id)
        return _Report(error=NOT_FOUND.format(sid=session_id))

    monkeypatch.setattr("mnemo.core.learn.learn", _learn)
    return calls


def _error_lines(vault: Path) -> list[dict]:
    log = vault / ".errors.log"
    if not log.exists():
        return []
    return [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- what retires --------------------------------------------------------


def test_a_marker_whose_transcript_is_gone_is_retired(monkeypatch, tmp_path, projects) -> None:
    vault = tmp_path / "vault"
    sid = "9293fe7b-a31b-4819-8a8d-d6c660a959c8"
    (projects / "-Users-x-github-mnemo-wt-200").mkdir()
    _write_marker(vault, session_id=sid,
                  link_scan_path=str(projects / "-Users-x-github-mnemo-wt-200" / f"{sid}.jsonl"))
    _fail_learn(monkeypatch, sid)

    report = unblocks.consume({}, vault_root=vault)

    assert (report.retired, report.failed, report.consumed) == (1, 0, 0)
    assert report.errors == []
    assert _marker(vault)["extracted"] is True
    assert detector.pending_unblocks(vault_root=vault) == []


def test_a_retired_marker_is_not_retried(monkeypatch, tmp_path, projects) -> None:
    vault = tmp_path / "vault"
    sid = "gone-1"
    _write_marker(vault, session_id=sid, link_scan_path=str(projects / "-p" / f"{sid}.jsonl"))
    calls = _fail_learn(monkeypatch, sid)

    unblocks.consume({}, vault_root=vault)
    second = unblocks.consume({}, vault_root=vault)

    assert calls == [sid]
    assert (second.retired, second.failed) == (0, 0)


def test_a_raising_learn_on_a_gone_transcript_also_retires(monkeypatch, tmp_path, projects) -> None:
    vault = tmp_path / "vault"
    _write_marker(vault, session_id="gone-2", link_scan_path=str(projects / "-p" / "gone-2.jsonl"))

    def _boom(*a, **k):
        raise FileNotFoundError("transcript vanished")

    monkeypatch.setattr("mnemo.core.learn.learn", _boom)

    report = unblocks.consume({}, vault_root=vault)

    assert (report.retired, report.failed) == (1, 0)


# --- what must not retire -------------------------------------------------


def test_a_deleted_worktree_whose_transcript_exists_is_deferred_not_retired(
    monkeypatch, tmp_path, projects
) -> None:
    """The real 26-of-27 case. ``learn`` cannot find it — the worktree's
    ``.git`` is gone, so ``cwd`` resolves to a project that owns nothing — but
    the answer is on disk and becomes learnable the day the lookup is fixed.
    Retiring it would discard the correction #195 exists to keep."""
    vault = tmp_path / "vault"
    sid = "9293fe7b-a31b-4819-8a8d-d6c660a959c8"
    project_dir = projects / "-Users-x-github-mnemo-wt-200"
    project_dir.mkdir()
    transcript = project_dir / f"{sid}.jsonl"
    transcript.write_text('{"type":"user"}\n', encoding="utf-8")
    _write_marker(vault, session_id=sid, link_scan_path=str(transcript))
    _fail_learn(monkeypatch, sid)

    report = unblocks.consume({}, vault_root=vault)

    assert (report.retired, report.failed) == (0, 1)
    assert _marker(vault)["extracted"] is False
    assert len(detector.pending_unblocks(vault_root=vault)) == 1


def test_a_transcript_found_under_a_different_project_dir_is_not_gone(
    monkeypatch, tmp_path, projects
) -> None:
    """The stem is searched across every project directory, not just the
    recorded one: the check must be wider than ``learn``'s lookup, never
    narrower, or it retires something a fixed lookup could still find."""
    vault = tmp_path / "vault"
    sid = "moved-1"
    (projects / "-somewhere-else").mkdir()
    (projects / "-somewhere-else" / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
    _write_marker(vault, session_id=sid, link_scan_path=str(projects / "-recorded" / f"{sid}.jsonl"))
    _fail_learn(monkeypatch, sid)

    report = unblocks.consume({}, vault_root=vault)

    assert (report.retired, report.failed) == (0, 1)


def test_a_recorded_path_outside_the_scanned_root_never_retires(
    monkeypatch, tmp_path, projects
) -> None:
    """A relocated config dir, or a marker from another machine, puts the
    transcript somewhere this root does not cover. Scanning the wrong root
    would call every transcript gone and retire the whole list."""
    vault = tmp_path / "vault"
    elsewhere = tmp_path / "other-config" / "projects" / "-p" / "sid-x.jsonl"
    _write_marker(vault, session_id="sid-x", link_scan_path=str(elsewhere))
    _fail_learn(monkeypatch, "sid-x")

    report = unblocks.consume({}, vault_root=vault)

    assert (report.retired, report.failed) == (0, 1)


def test_no_projects_root_on_this_machine_never_retires(monkeypatch, tmp_path) -> None:
    vault = tmp_path / "vault"
    root = tmp_path / "missing" / "projects"
    monkeypatch.setattr("mnemo.core.backfill.discover.projects_root", lambda: root)
    _write_marker(vault, session_id="sid-y", link_scan_path=str(root / "-p" / "sid-y.jsonl"))
    _fail_learn(monkeypatch, "sid-y")

    report = unblocks.consume({}, vault_root=vault)

    assert (report.retired, report.failed) == (0, 1)


def test_a_marker_with_no_recorded_path_never_retires(monkeypatch, tmp_path, projects) -> None:
    """The tempo fallback can record a marker with no ``link_scan_path``.
    Without the path there is no proof the scanned root is the right one."""
    vault = tmp_path / "vault"
    _write_marker(vault, session_id="sid-z", link_scan_path="")
    _fail_learn(monkeypatch, "sid-z")

    report = unblocks.consume({}, vault_root=vault)

    assert (report.retired, report.failed) == (0, 1)


def test_a_redeemed_marker_never_pays_for_the_scan(monkeypatch, tmp_path, projects) -> None:
    vault = tmp_path / "vault"
    _write_marker(vault, session_id="live", link_scan_path=str(projects / "-p" / "live.jsonl"))
    monkeypatch.setattr("mnemo.core.learn.learn", lambda *a, **k: _Report())
    monkeypatch.setattr(unblocks, "transcript_gone",
                        lambda *a, **k: pytest.fail("scanned a redeemed marker"))

    report = unblocks.consume({}, vault_root=vault)

    assert (report.consumed, report.retired) == (1, 0)


# --- a deferred failure is visible ----------------------------------------


def test_a_deferred_failure_is_written_onto_its_marker(monkeypatch, tmp_path, projects) -> None:
    vault = tmp_path / "vault"
    (projects / "-p").mkdir()
    (projects / "-p" / "s1.jsonl").write_text("{}\n", encoding="utf-8")
    _write_marker(vault, session_id="s1", link_scan_path=str(projects / "-p" / "s1.jsonl"))
    _fail_learn(monkeypatch, "s1")

    unblocks.consume({}, vault_root=vault)
    unblocks.consume({}, vault_root=vault)

    marker = _marker(vault)
    assert marker["attempts"] == 2
    assert marker["last_error"] == NOT_FOUND.format(sid="s1")
    assert marker["extracted"] is False


def test_errors_log_gets_one_line_per_distinct_error_not_per_pass(
    monkeypatch, tmp_path, projects
) -> None:
    """The pass rides every session end. Logging the same stuck marker each
    time would bury the rest of ``.errors.log``; logging never is the bug."""
    vault = tmp_path / "vault"
    (projects / "-p").mkdir()
    (projects / "-p" / "s1.jsonl").write_text("{}\n", encoding="utf-8")
    _write_marker(vault, session_id="s1", link_scan_path=str(projects / "-p" / "s1.jsonl"))
    _fail_learn(monkeypatch, "s1")

    for _ in range(3):
        unblocks.consume({}, vault_root=vault)
    assert [e["where"] for e in _error_lines(vault)] == [unblocks.ERROR_WHERE]
    assert "s1" in _error_lines(vault)[0]["message"]

    monkeypatch.setattr("mnemo.core.learn.learn",
                        lambda *a, **k: _Report(error="another extraction is in progress"))
    unblocks.consume({}, vault_root=vault)

    assert len(_error_lines(vault)) == 2
    assert "another extraction" in _error_lines(vault)[1]["message"]


def test_one_pass_over_many_failing_markers_writes_one_errors_log_row(
    monkeypatch, tmp_path, projects
) -> None:
    """#314: 27 stuck markers failing in one pass wrote 27 same-second rows and
    tripped the circuit breaker. A pass is one action: one row, with the count
    and every id, and the breaker stays closed."""
    from mnemo.core import errors

    vault = tmp_path / "vault"
    (projects / "-p").mkdir()
    sids = [f"4dfb38a9-0000-4000-8000-{i:012d}" for i in range(27)]
    seen = {}
    for sid in sids:
        (projects / "-p" / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
        seen[sid[:8] + sid[-4:]] = {"last_tempo": "active", "unblocks": [{
            "at": "2026-09-12T22:49:08+00:00", "session_id": sid,
            "link_scan_path": str(projects / "-p" / f"{sid}.jsonl"),
            "cwd": "/Users/x/github/mnemo-wt-gone", "extracted": False,
        }]}
    path = vault / ".mnemo" / detector.STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": seen}), encoding="utf-8")
    _fail_learn(monkeypatch, "")

    report = unblocks.consume({}, vault_root=vault)

    assert report.failed == 27
    rows = _error_lines(vault)
    assert [r["where"] for r in rows] == [unblocks.ERROR_WHERE]
    message = rows[0]["message"]
    assert message.startswith(
        "27 unblock markers deferred — 27 × no transcript with session id <id> for this directory: "
    )
    assert all(sid in message for sid in sids)
    assert errors.should_run(vault) is True

    unblocks.consume({}, vault_root=vault)
    assert len(_error_lines(vault)) == 1, "an unchanged error is not logged again"


def test_a_pass_groups_different_errors_in_its_one_row(monkeypatch, tmp_path, projects) -> None:
    vault = tmp_path / "vault"
    (projects / "-p").mkdir()
    seen = {}
    for sid in ("aaa", "bbb", "ccc"):
        (projects / "-p" / f"{sid}.jsonl").write_text("{}\n", encoding="utf-8")
        marker = {"session_id": sid, "cwd": "/x", "extracted": False,
                  "link_scan_path": str(projects / "-p" / f"{sid}.jsonl")}
        # "aaa" was answered twice: two markers, one session (real vault: 4074e62b held six).
        seen[sid] = {"unblocks": [dict(marker), dict(marker)] if sid == "aaa" else [marker]}
    path = vault / ".mnemo" / detector.STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": seen}), encoding="utf-8")

    def _learn(cfg, *, cwd, session_id, **k):
        if session_id == "ccc":
            return _Report(error="another extraction is in progress")
        return _Report(error=NOT_FOUND.format(sid=session_id))

    monkeypatch.setattr("mnemo.core.learn.learn", _learn)

    unblocks.consume({}, vault_root=vault)

    rows = _error_lines(vault)
    assert len(rows) == 1
    assert rows[0]["message"] == (
        "4 unblock markers deferred — "
        "3 × no transcript with session id <id> for this directory: aaa, bbb | "
        "1 × another extraction is in progress: ccc"
    )
