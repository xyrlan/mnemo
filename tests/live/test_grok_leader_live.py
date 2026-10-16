"""``mnemo.core.grok.acp`` against the **installed** ``grok`` and a real login.

Opt-in: ``MNEMO_LIVE_GROK=1 PYTHONPATH=src python3 -m pytest tests/live/test_grok_leader_live.py``.
It needs ``grok`` on PATH and a logged-in home (``$GROK_HOME`` or ``~/.grok``).
The tests taking ``quota`` run real turns (~15k input tokens each: Grok's
system prompt); a free account allows 600k tokens per rolling 24 hours, and a
test that meets the limit is skipped, not passed.

Isolation (wave-1 contract): the leader these tests talk to is their own, on a
socket in a fresh temp directory, started with ``HOME`` set to a throwaway
directory and ``GROK_CLAUDE_HOOKS_ENABLED=0``, so no session here fires
mnemo's hooks or reaches the maintainer's vault. Only ``GROK_HOME`` is real,
for the login; ``auth.json`` is never read. The leader is stopped at the end
(its pid is the content of the ``.lock`` file beside its socket).
"""
from __future__ import annotations

import os
import shutil
import signal
import tempfile
import time
import uuid
from pathlib import Path

import pytest

from mnemo.core.grok import acp

# Read before conftest's tmp_home moves HOME.
REAL_GROK_HOME = Path(os.environ.get("GROK_HOME") or Path.home() / ".grok").expanduser()

pytestmark = pytest.mark.skipif(
    os.environ.get("MNEMO_LIVE_GROK") != "1" or shutil.which("grok") is None
    or not (REAL_GROK_HOME / "auth.json").is_file(),
    reason="live Grok leader probe: set MNEMO_LIVE_GROK=1 with grok on PATH and a logged-in home",
)


def _until(predicate, seconds: float, every: float = 1.0):
    deadline = time.monotonic() + seconds
    while True:
        value = predicate()
        if value or time.monotonic() > deadline:
            return value
        time.sleep(every)


def _stop_leader(socket: Path) -> None:
    try:
        pid = int(socket.with_suffix(".lock").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    _until(lambda: not _alive(pid), 15, 0.5)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@pytest.fixture(scope="module")
def sandbox():
    """``(throwaway HOME, own socket, work dir)``; the leader on that socket is stopped after."""
    base = Path(tempfile.mkdtemp(prefix="mg-", dir="/tmp" if os.path.isdir("/tmp") else None))
    socket = base / "l.sock"
    (base / "home").mkdir()
    work = base / "work"
    work.mkdir()
    yield base / "home", socket, work
    _stop_leader(socket)
    shutil.rmtree(base, ignore_errors=True)


@pytest.fixture
def leader(sandbox, monkeypatch, tmp_path):
    fake_home, socket, _work = sandbox
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GROK_HOME", str(REAL_GROK_HOME))
    monkeypatch.setenv("GROK_LEADER_SOCKET", str(socket))
    monkeypatch.setenv("GROK_CLAUDE_HOOKS_ENABLED", "0")
    monkeypatch.setattr(acp, "state_dir", lambda: tmp_path / "st")
    return acp.Leader()


@pytest.fixture
def work(sandbox):
    d = sandbox[2] / uuid.uuid4().hex[:8]
    d.mkdir()
    return d


def _row(leader, sid):
    return next((e for e in leader.roster() if e.session_id == sid), None)


def _model_turn(call):
    try:
        return call()
    except acp.RateLimited as exc:
        pytest.skip(f"the account is out of usage: {exc}")


_QUOTA = {}


@pytest.fixture
def quota(leader, sandbox):
    """Skip a model test when the account is out of usage; one real turn per run finds out.

    A turn sent with ``wait=False`` starts even when the account is out, then
    fails inside the leader, so without this a test waiting for a tool's file
    would fail for a reason that is not the code's.
    """
    if "out" not in _QUOTA:
        sid = leader.new_session(str(sandbox[2]), effort="low", yolo=False)
        try:
            leader.prompt(sid, str(sandbox[2]), "Reply with exactly OK", wait=True, timeout=180)
            _QUOTA["out"] = None
        except acp.RateLimited as exc:
            _QUOTA["out"] = str(exc)
    if _QUOTA["out"]:
        pytest.skip(f"the account is out of usage: {_QUOTA['out']}")


# --- no tokens spent ----------------------------------------------------------

def test_roster_close_and_billing(leader, work):
    sid = leader.new_session(str(work), effort="low")
    assert acp.was_created_yolo(leader.home, sid)
    assert leader.close(sid) in ("closed", "notResident")
    assert leader.close(sid) == "notResident"
    billing = leader.billing()
    assert "config" in billing
    for entry in leader.roster():
        assert entry.activity in ("working", "idle", "needs_input", "dormant", "completed", "dead")
        assert entry.home == leader.home


def test_an_unknown_model_is_refused(leader, work):
    with pytest.raises(ValueError, match="no model"):
        leader.new_session(str(work), model="grok-does-not-exist")


def test_a_session_nobody_has_is_not_found(leader, work):
    with pytest.raises(acp.SessionNotFound):
        leader.prompt("01a144cc-0000-7000-8000-000000000000", str(work), "anyone?")


def test_a_home_too_deep_for_its_socket_still_gets_a_leader(sandbox, monkeypatch, tmp_path):
    # A long symlink to the real home: Grok's default socket under it would
    # not fit SUN_LEN, so mnemo must pick its own and the leader must bind it.
    fake_home, _socket, _work = sandbox
    deep = sandbox[0].parent / ("d" * 110)
    deep.symlink_to(REAL_GROK_HOME, target_is_directory=True)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("GROK_LEADER_SOCKET", raising=False)
    monkeypatch.setenv("GROK_CLAUDE_HOOKS_ENABLED", "0")
    monkeypatch.setattr(acp, "state_dir", lambda: tmp_path / "st")
    leader = acp.Leader(deep)
    assert len(os.fsencode(str(deep / "leader.sock"))) > acp.socket_limit()
    assert leader.socket != str(deep / "leader.sock")
    try:
        assert isinstance(leader.roster(), list)
        assert isinstance(acp.Leader(deep).roster(), list)  # the same socket, found again
    finally:
        _stop_leader(Path(leader.socket))


# --- real turns ---------------------------------------------------------------

def test_yolo_session_runs_its_tools_with_no_client_attached(leader, work, quota):
    sid = leader.new_session(str(work), effort="low")
    marker = work / "after-the-client-left.txt"
    # The second command starts ~12s after prompt() returned and its client left.
    _model_turn(lambda: leader.prompt(
        sid, str(work),
        "Run the shell command `sleep 12` and wait for it. Then, as a separate "
        f"command, run: echo yolo > {marker.name} . Then reply with exactly DONE."))
    assert _until(marker.exists, 120), "the tool call after the client left never ran"
    assert marker.read_text(encoding="utf-8").strip() == "yolo"


def test_prompt_without_wait_returns_while_the_turn_still_runs(leader, work, quota):
    sid = leader.new_session(str(work), effort="low")
    started = time.monotonic()
    _model_turn(lambda: leader.prompt(sid, str(work), "Run the shell command `sleep 20`, then reply DONE."))
    assert time.monotonic() - started < 15
    row = _until(lambda: (lambda r: r if r and r.activity == "working" else None)(_row(leader, sid)), 10)
    assert row is not None and row.resident


def test_prompt_with_wait_returns_the_model_s_text(leader, work, quota):
    sid = leader.new_session(str(work), effort="low")
    text = _model_turn(lambda: leader.prompt(sid, str(work), "Reply with exactly the word PONG.",
                                             wait=True, timeout=180))
    assert "PONG" in (text or "")


def test_a_prompt_to_a_working_session_is_queued_and_runs_unattended(leader, work, quota):
    sid = leader.new_session(str(work), effort="low")
    marker = work / "queued.txt"
    _model_turn(lambda: leader.prompt(sid, str(work), "Run the shell command `sleep 15`, then reply ONE."))
    _model_turn(lambda: leader.prompt(sid, str(work), f"Run the shell command: echo two > {marker.name} . "
                                                      "Then reply TWO."))
    assert _until(marker.exists, 150), "the queued turn never ran"


def test_a_dormant_session_is_loaded_and_keeps_yolo(leader, work, quota):
    sid = leader.new_session(str(work), effort="low")
    assert leader.close(sid) in ("closed", "notResident")
    marker = work / "reloaded.txt"
    _model_turn(lambda: leader.prompt(sid, str(work), f"Run the shell command: echo back > {marker.name} . "
                                                      "Then reply BACK."))
    assert _until(marker.exists, 120), "the reloaded session did not run its tool unattended"
