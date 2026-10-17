"""#725: on Windows a finishing child reaches its open parent through the pipe
Claude Code's registry names, authenticated with the parent's peer key."""
from __future__ import annotations

import hashlib
import json
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

import pytest

from mnemo.core.sessions import inbox, registry

SID = "8d11db63-0000-4000-8000-000000000001"
HOST = "win32:desktop-test"
PID = 69968
START = 134367099831262660
PIPE = "\\\\.\\pipe\\LOCAL\\cc-msg-6823e286fd79b9ff9477ed9b2115ae92"
#: Synthetic, never from a real machine: tests assert it never leaks.
TOKEN = "feedface" * 8

#: pid -> creation time of the processes running "now".
RUNNING: Dict[int, int] = {PID: START}


def _created(pid: int) -> Optional[int]:
    return RUNNING.get(pid)


def _entry(folder: Path, pid: int = PID, **fields) -> None:
    data = {"pid": pid, "sessionId": SID, "procStart": str(RUNNING.get(pid, 1)),
            "pidDomain": HOST, "kind": "interactive", "messagingSocketPath": PIPE,
            **fields}
    (folder / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


def _key(folder: Path, name: Optional[str] = None, *, pid: int = PID,
         start: int = START, domain: str = HOST, token: str = TOKEN) -> Path:
    path = folder / (name or inbox.key_name(pid, PIPE))
    path.write_text(json.dumps({"peerToken": token, "procStartFt": str(start),
                                "pidDomain": domain}), encoding="utf-8")
    return path


def _address(folder: Path, sid: str = SID) -> Optional[dict]:
    return inbox.registry_address(sid, directory=folder, domain=HOST, creation_time=_created)


def _lines(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


# --- the address, from the registry ---------------------------------------

def test_the_live_entry_gives_the_pipe_and_nothing_secret(tmp_path):
    _entry(tmp_path)
    _key(tmp_path)
    address = _address(tmp_path)
    assert address == {
        "session_id": SID, "agent": inbox.PIPE, "socket": PIPE, "pid": PID,
        "proc_start": str(START), "pid_domain": HOST, "sessions_dir": str(tmp_path),
    }
    assert TOKEN not in json.dumps(address)


def test_an_exited_parent_has_no_address(tmp_path):
    _entry(tmp_path, pid=41000)   # nothing runs under 41000 now
    assert _address(tmp_path) is None


def test_a_reused_pid_is_not_the_parent(tmp_path):
    _entry(tmp_path, procStart="134300000000000000")
    assert _address(tmp_path) is None


def test_another_hosts_entry_is_not_this_parent(tmp_path):
    _entry(tmp_path, pidDomain="win32:another-machine")
    assert _address(tmp_path) is None


def test_the_running_one_of_several_entries_for_a_resumed_parent(tmp_path):
    _entry(tmp_path, pid=41000, procStart="134300000000000000",
           messagingSocketPath="\\\\.\\pipe\\LOCAL\\cc-msg-old")
    _entry(tmp_path)
    assert _address(tmp_path)["socket"] == PIPE


def test_an_entry_without_a_pipe_is_no_address(tmp_path):
    _entry(tmp_path, messagingSocketPath="")
    assert _address(tmp_path) is None


# --- the peer key ------------------------------------------------------

def test_the_key_is_named_after_the_lowercased_pipe():
    digest = hashlib.sha256(PIPE.lower().encode("utf-8")).hexdigest()
    assert inbox.key_name(PID, PIPE) == f"{PID}.{digest}.key"


def test_the_key_named_after_the_pipe_is_read(tmp_path):
    _entry(tmp_path)
    _key(tmp_path)
    assert inbox.peer_token(_address(tmp_path)) == TOKEN


def test_a_stale_key_under_the_name_gives_way_to_the_matching_one(tmp_path):
    """Key files outlive their session like entries do: a key whose start
    time is not the entry's belongs to an earlier process under that pid."""
    _entry(tmp_path)
    _key(tmp_path, start=134300000000000000, token="0" * 64)
    _key(tmp_path, f"{PID}.{'a' * 64}.key")
    assert inbox.peer_token(_address(tmp_path)) == TOKEN


def test_no_key_with_the_entry_s_start_and_host_is_no_token(tmp_path):
    _entry(tmp_path)
    _key(tmp_path, start=134300000000000000)
    _key(tmp_path, f"{PID}.{'b' * 64}.key", domain="win32:another-machine")
    _key(tmp_path, f"{PID + 1}.{'c' * 64}.key")
    assert inbox.peer_token(_address(tmp_path)) == ""


def test_an_unreadable_key_is_skipped(tmp_path):
    _entry(tmp_path)
    (tmp_path / inbox.key_name(PID, PIPE)).write_text("{not json", encoding="utf-8")
    _key(tmp_path, f"{PID}.{'d' * 64}.key")
    assert inbox.peer_token(_address(tmp_path)) == TOKEN


# --- the wire (a regular file stands in for the pipe) ------------------

def _pipe_address(tmp_path: Path, pipe: Path) -> dict:
    _entry(tmp_path, messagingSocketPath=str(pipe))
    _key(tmp_path, inbox.key_name(PID, str(pipe)))
    return _address(tmp_path)


def test_post_pipe_authenticates_then_writes_the_turn(tmp_path):
    pipe = tmp_path / "pipe"
    pipe.write_bytes(b"")
    assert inbox.post_pipe(_pipe_address(tmp_path, pipe), "a child of yours finished") == ""
    assert _lines(pipe) == [
        {"type": "auth", "token": TOKEN},
        {"type": "user", "message": {"role": "user", "content": "a child of yours finished"}},
    ]


def test_post_routes_a_pipe_address_through_the_key(tmp_path):
    pipe = tmp_path / "pipe"
    pipe.write_bytes(b"")
    assert inbox.post(_pipe_address(tmp_path, pipe), "hi") is True
    assert [line["type"] for line in _lines(pipe)] == ["auth", "user"]


def test_without_a_key_nothing_is_written_and_the_reason_says_so(tmp_path):
    pipe = tmp_path / "pipe"
    pipe.write_bytes(b"")
    _entry(tmp_path, messagingSocketPath=str(pipe))
    why = inbox.post_pipe(_address(tmp_path), "hi")
    assert why.startswith(f"no peer key for pid {PID}")
    assert pipe.read_bytes() == b""


def test_a_write_that_fails_is_not_delivered_and_never_names_the_token(tmp_path):
    address = _pipe_address(tmp_path, tmp_path / "pipe")   # nothing listens there
    why = inbox.post_pipe(address, "hi")
    assert why.startswith("pipe write failed: ")
    assert TOKEN not in why


def test_a_pipe_that_never_takes_the_write_times_out(tmp_path, monkeypatch):
    release = threading.Event()

    def hung(*args, **kwargs):
        release.wait(5)
        raise OSError("released")

    monkeypatch.setattr(inbox, "open", hung, raising=False)
    try:
        assert inbox.write_pipe("\\\\.\\pipe\\x", [b"x\n"], timeout=0.05) \
            == "pipe write timed out after 0.05s: \\\\.\\pipe\\x"
    finally:
        release.set()


# --- resolve and deliver -----------------------------------------------

@pytest.fixture
def windows(tmp_path, monkeypatch) -> Path:
    """This host is :data:`HOST`, its running processes :data:`RUNNING`, and
    the default account's registry is where the parent's entry goes."""
    monkeypatch.setattr(inbox, "registry_reachable", lambda: True)
    monkeypatch.setattr(registry, "host_domain", lambda: HOST)
    monkeypatch.setattr(registry, "windows_creation_time", _created)
    folder = Path.home() / ".claude" / "sessions"
    folder.mkdir(parents=True)
    return folder


def test_deliver_reaches_an_open_parent_with_no_row(tmp_path, windows):
    """The 2026-10-17 shape: no ``session-inbox.jsonl`` at all, the parent
    open; the notice goes out at once instead of waiting for a prompt."""
    vault = tmp_path / "vault"
    pipe = tmp_path / "pipe"
    pipe.write_bytes(b"")
    _pipe_address(windows, pipe)
    address, why = inbox.resolve(vault, SID)
    assert why == "" and address["agent"] == inbox.PIPE
    assert inbox.deliver(vault, SID, "<mnemo-child-finished c89703c5>") == ""
    assert _lines(pipe)[1]["message"]["content"] == "<mnemo-child-finished c89703c5>"
    assert not inbox.log_path(vault).exists()


def test_an_exited_parent_is_held_with_the_registry_named(tmp_path, windows):
    vault = tmp_path / "vault"
    _entry(windows, pid=41000)
    address, why = inbox.resolve(vault, SID)
    assert address is None
    assert why.startswith("no address recorded for the parent in session-inbox.jsonl; "
                          "no live entry for the parent in Claude Code's registry (")
    assert str(windows) in why
    assert inbox.notify(vault, SID, "hi") is False


def test_the_parent_s_own_account_registry_is_read_too(tmp_path, windows, monkeypatch):
    """A child's hook runs on the child's account; its parent may be on
    another one that mnemo has seen (:mod:`.accounts`)."""
    from mnemo.core.sessions import accounts

    other = tmp_path / "claude-other"
    (other / "sessions").mkdir(parents=True)
    vault = tmp_path / "vault"
    vault.mkdir()
    assert accounts.remember(vault, other)
    pipe = tmp_path / "pipe"
    pipe.write_bytes(b"")
    _pipe_address(other / "sessions", pipe)
    assert inbox.deliver(vault, SID, "hi") == ""


def test_the_registry_is_read_on_windows_alone():
    assert inbox.registry_reachable() is (sys.platform == "win32")


def test_off_windows_the_registry_is_never_read(tmp_path, windows, monkeypatch):
    monkeypatch.setattr(inbox, "registry_reachable", lambda: False)
    _pipe_address(windows, tmp_path / "pipe")
    assert inbox.resolve(tmp_path / "vault", SID) == (
        None, "no address recorded for the parent in session-inbox.jsonl")


def test_a_failed_send_reason_never_carries_the_token(tmp_path, windows):
    vault = tmp_path / "vault"
    _pipe_address(windows, tmp_path / "missing-pipe")
    why = inbox.deliver(vault, SID, "hi")
    assert why and TOKEN not in why
