"""extract.lock belongs to its holder for as long as the holder runs (#674).

Every holder here is a real second process running the real
``run_extraction``, with only its body replaced: one that waits on stdin (a
run still going) or one that ``os._exit``s while holding the lock (a run that
crashed). "Past 60 s" is the lock's mtimes pushed back, not a minute waited.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import mnemo
from mnemo.core.extract import ExtractionIOError, run_extraction

_SRC = Path(mnemo.__file__).resolve().parents[1]

_HOLDER = r"""
import json, os, sys
import mnemo.core.extract as ex

mode = sys.argv[2]

def body(*_a, **_kw):
    print("held", flush=True)
    if mode == "die":
        os._exit(0)
    sys.stdin.readline()

ex._run_extraction_body = body
ex.run_extraction(json.loads(sys.argv[1]), dry_run=True)
print("released", flush=True)
"""


def _cfg(vault: Path) -> dict:
    return {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": True}}}


def _lock(vault: Path) -> Path:
    return vault / ".mnemo" / "extract.lock"


def _start_holder(vault: Path, mode: str) -> subprocess.Popen:
    env = dict(os.environ, PYTHONPATH=str(_SRC))
    proc = subprocess.Popen(
        [sys.executable, "-c", _HOLDER, json.dumps(_cfg(vault)), mode],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, text=True, encoding="utf-8",
    )
    line = proc.stdout.readline().strip()
    assert line == "held", (line, proc.stderr.read() if proc.poll() is not None else "")
    if mode == "die":
        proc.wait(timeout=30)
    return proc


def _finish(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        try:
            proc.stdin.write("\n")
            proc.stdin.flush()
        except OSError:
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        stream.close()


def _backdate(lock: Path, seconds: float) -> None:
    """Make the lock look ``seconds`` old to anything that reads an mtime."""
    old = time.time() - seconds
    for p in [lock, *lock.rglob("*")]:
        os.utime(p, (old, old))


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".mnemo").mkdir(parents=True)
    return v


@pytest.fixture
def live_holder(vault: Path):
    proc = _start_holder(vault, "wait")
    yield proc
    _finish(proc)


@pytest.fixture
def dead_holder(vault: Path):
    proc = _start_holder(vault, "die")
    yield proc
    _finish(proc)


@pytest.fixture
def no_body(monkeypatch: pytest.MonkeyPatch) -> list:
    ran: list = []
    monkeypatch.setattr(
        "mnemo.core.extract._run_extraction_body", lambda *a, **kw: ran.append(True),
    )
    return ran


# --- the lock itself ------------------------------------------------------


def test_live_holder_past_60s_keeps_the_lock(vault, live_holder, no_body):
    _backdate(_lock(vault), 120)
    with pytest.raises(ExtractionIOError, match="in progress"):
        run_extraction(_cfg(vault), dry_run=True)
    assert no_body == []
    assert live_holder.poll() is None
    assert _lock(vault).is_dir()


def test_dead_holder_lock_is_taken_back(vault, dead_holder, no_body):
    assert _lock(vault).is_dir(), "the crashed holder left its lock behind"
    run_extraction(_cfg(vault), dry_run=True)
    assert no_body == [True]
    assert not _lock(vault).exists()


def test_releasing_a_lock_no_longer_held_leaves_the_new_holders(vault, live_holder):
    """The holder lost its lock; when it finishes, the new holder's stays."""
    import shutil

    from mnemo.core.extract import machine_edits

    shutil.rmtree(_lock(vault))
    with machine_edits.edit_session(vault, create=True):
        _finish(live_holder)
        assert live_holder.returncode == 0
        assert _lock(vault).is_dir(), "the old holder removed the new holder's lock"
    assert not _lock(vault).exists()


def test_legacy_empty_lock_dir_old_enough_is_taken_back(vault, no_body):
    """A lock directory left by an older mnemo carries no owner."""
    _lock(vault).mkdir()
    _backdate(_lock(vault), 120)
    run_extraction(_cfg(vault), dry_run=True)
    assert no_body == [True]


def test_legacy_empty_lock_dir_fresh_is_respected(vault, no_body):
    _lock(vault).mkdir()
    with pytest.raises(ExtractionIOError, match="in progress"):
        run_extraction(_cfg(vault), dry_run=True)
    assert _lock(vault).is_dir()


# --- the readers agree with the lock --------------------------------------


def test_session_end_sees_a_live_holder_past_the_old_ages(vault, live_holder):
    from mnemo.hooks import session_end

    _backdate(_lock(vault), 900)
    assert session_end._lock_held(_lock(vault)) is True


def test_session_end_does_not_wait_on_a_dead_holder(vault, dead_holder):
    from mnemo.hooks import session_end

    assert session_end._lock_held(_lock(vault)) is False


def _status(vault, monkeypatch, capsys) -> str:
    from mnemo import cli

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: _cfg(vault))
    cli.main(["status"])
    return capsys.readouterr().out


def _doctor(vault, monkeypatch, capsys) -> str:
    from mnemo import cli

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: _cfg(vault))
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())
    cli.main(["doctor"])
    return capsys.readouterr().out


def test_status_reports_a_live_holder_as_running(vault, live_holder, monkeypatch, capsys):
    _backdate(_lock(vault), 900)
    out = _status(vault, monkeypatch, capsys)
    assert "running now" in out
    assert f"pid {live_holder.pid}" in out


def test_status_does_not_report_a_dead_holder_as_running(vault, dead_holder, monkeypatch, capsys):
    out = _status(vault, monkeypatch, capsys)
    assert "running now" not in out
    assert "stale extract.lock" in out


def test_doctor_does_not_call_a_live_holder_stale(vault, live_holder, monkeypatch, capsys):
    _backdate(_lock(vault), 900)
    out = _doctor(vault, monkeypatch, capsys)
    assert "stale extract.lock" not in out


def test_doctor_calls_a_dead_holder_stale(vault, dead_holder, monkeypatch, capsys):
    out = _doctor(vault, monkeypatch, capsys)
    assert "stale extract.lock" in out


# --- locks.owned_lock directly ---------------------------------------------


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _plant_owner(lock: Path, *, pid: int, host: str, token: str = "t0") -> None:
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        json.dumps({"pid": pid, "host": host, "started": time.time(), "token": token}),
        encoding="utf-8",
    )


def test_contenders_on_a_dead_lock_take_it_exactly_once(tmp_path, monkeypatch):
    """Every contender judges the lock dead; only one may end up holding it."""
    import socket
    import threading

    from mnemo.core import locks

    lock = tmp_path / "x.lock"
    _plant_owner(lock, pid=_dead_pid(), host=socket.gethostname())

    n = 8
    judged = threading.Barrier(n)
    real_holder = locks.holder
    first_call = threading.local()

    def slow_holder(path):
        # Every contender's first judgment returns before any of them acts,
        # which is the window the takeover has to survive.
        found = real_holder(path)
        if not getattr(first_call, "done", False):
            first_call.done = True
            try:
                judged.wait(timeout=10)
            except threading.BrokenBarrierError:
                pass
        return found

    monkeypatch.setattr(locks, "holder", slow_holder)
    results: list = []
    done = threading.Barrier(n)

    def contend():
        with locks.owned_lock(lock) as held:
            results.append(held)
            done.wait(timeout=10)

    threads = [threading.Thread(target=contend) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(results) == [False] * (n - 1) + [True]
    assert not lock.exists()


def test_heartbeat_keeps_a_holder_on_another_host_alive(tmp_path, monkeypatch):
    """Where the pid cannot be asked, the heartbeat is the sign of life."""
    from mnemo.core import locks

    monkeypatch.setattr(locks, "HEARTBEAT_EVERY", 0.05)
    lock = tmp_path / "x.lock"
    with locks.owned_lock(lock) as held:
        assert held
        _backdate(lock, 600)
        monkeypatch.setattr(locks.socket, "gethostname", lambda: "some-other-host")
        assert locks.holder(lock).alive is False  # nothing has refreshed it yet
        deadline = time.time() + 5
        while not locks.holder(lock).alive and time.time() < deadline:
            time.sleep(0.02)
        assert locks.holder(lock).alive is True


def test_silent_holder_on_another_host_is_dead(tmp_path):
    from mnemo.core import locks

    lock = tmp_path / "x.lock"
    _plant_owner(lock, pid=os.getpid(), host="some-other-host")
    assert locks.holder(lock).alive is True
    _backdate(lock, 120)
    assert locks.holder(lock).alive is False


def test_live_pid_silent_for_an_hour_is_taken_for_a_reused_pid(tmp_path):
    import socket

    from mnemo.core import locks

    lock = tmp_path / "x.lock"
    _plant_owner(lock, pid=os.getpid(), host=socket.gethostname())
    _backdate(lock, 1800)
    assert locks.holder(lock).alive is True
    _backdate(lock, 4000)
    assert locks.holder(lock).alive is False
