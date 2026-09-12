"""Whether a background session's process is still alive.

``state.json`` records no pid — the issue was right about that. But the
daemon keeps ``~/.claude/daemon/roster.json``, whose ``workers`` map is keyed
by the *same short id* as the job directories and carries a real pid. That is
a liveness probe, not a proxy for one, so it is what the queue uses.

Measured against the real roster on 2026-09-12: 3 workers, 2 with job dirs,
every pid live under ``os.kill(pid, 0)``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from mnemo.core.sessions import liveness


def _roster(root: Path, workers: dict[str, dict]) -> Path:
    """Write a roster.json under a fake ~/.claude and return that root."""
    d = root / "daemon"
    d.mkdir(parents=True, exist_ok=True)
    (d / "roster.json").write_text(
        json.dumps({"proto": 1, "supervisorPid": 1, "workers": workers}),
        encoding="utf-8",
    )
    return root


# --- read_roster -----------------------------------------------------------

def test_roster_maps_short_id_to_pid(tmp_path: Path) -> None:
    _roster(tmp_path, {"a3f1": {"pid": 4242, "cwd": "/repo"}})

    assert liveness.read_roster(tmp_path) == {"a3f1": 4242}


def test_roster_missing_reads_as_unknown(tmp_path: Path) -> None:
    """No roster is not evidence of death — it is absence of evidence."""
    assert liveness.read_roster(tmp_path) is None


def test_roster_malformed_reads_as_unknown(tmp_path: Path) -> None:
    d = tmp_path / "daemon"
    d.mkdir(parents=True)
    (d / "roster.json").write_text("{not json", encoding="utf-8")

    assert liveness.read_roster(tmp_path) is None


def test_roster_skips_workers_without_an_integer_pid(tmp_path: Path) -> None:
    _roster(tmp_path, {
        "good": {"pid": 4242},
        "nopid": {"cwd": "/repo"},
        "strpid": {"pid": "4242"},
        "notdict": [],
    })

    assert liveness.read_roster(tmp_path) == {"good": 4242}


def test_roster_with_no_workers_is_an_empty_map_not_unknown(tmp_path: Path) -> None:
    """A readable roster with zero workers is real evidence: nothing is alive."""
    _roster(tmp_path, {})

    assert liveness.read_roster(tmp_path) == {}


# --- pid_alive -------------------------------------------------------------

def test_pid_alive_is_true_for_this_process() -> None:
    assert liveness.pid_alive(os.getpid()) is True


def test_pid_alive_is_false_for_a_pid_that_cannot_exist() -> None:
    assert liveness.pid_alive(-1) is False
    assert liveness.pid_alive(0) is False


def test_pid_alive_is_false_for_a_reaped_pid() -> None:
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child never returns
        os._exit(0)
    os.waitpid(pid, 0)

    assert liveness.pid_alive(pid) is False


# --- classify --------------------------------------------------------------

def test_session_in_roster_with_a_live_pid_is_live(tmp_path: Path) -> None:
    _roster(tmp_path, {"a3f1": {"pid": os.getpid()}})

    assert liveness.is_live("a3f1", claude_home=tmp_path) is True


def test_session_absent_from_a_readable_roster_is_dead(tmp_path: Path) -> None:
    _roster(tmp_path, {"other": {"pid": os.getpid()}})

    assert liveness.is_live("a3f1", claude_home=tmp_path) is False


def test_session_in_roster_with_a_dead_pid_is_dead(tmp_path: Path) -> None:
    pid = os.fork()
    if pid == 0:  # pragma: no cover - child never returns
        os._exit(0)
    os.waitpid(pid, 0)
    _roster(tmp_path, {"a3f1": {"pid": pid}})

    assert liveness.is_live("a3f1", claude_home=tmp_path) is False


def test_unknown_when_the_roster_cannot_be_read(tmp_path: Path) -> None:
    """No roster means we must not accuse: unknown, never dead.

    Downstream treats unknown as live, because a false 'dead' hides a session
    that really is waiting for a human — the exact failure the queue exists to
    prevent.
    """
    assert liveness.is_live("a3f1", claude_home=tmp_path) is None
