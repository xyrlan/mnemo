"""#357: a finishing child reaches the session that dispatched it."""
from __future__ import annotations

import json
import os
import pathlib
import socket
import stat
import threading

import pytest

from mnemo.core.sessions import detector, inbox


def _addr(tmp_path, sock_path, *, pid=4242, pid_start="Wed Sep 17 03:00:00 2026"):
    return {
        "session_id": "parent-uuid",
        "socket": str(sock_path),
        "pid": pid,
        "pid_start": pid_start,
    }


@pytest.fixture
def short_dir():
    """A socket path under the OS temp root.

    ``AF_UNIX`` caps the whole path at ~104 bytes on macOS, and pytest's
    ``tmp_path`` (which embeds the test's name) already overruns it.
    """
    import shutil
    import tempfile

    d = tempfile.mkdtemp(prefix="mn")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _serve(sock_path, received, ready):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(str(sock_path))
    srv.listen(1)
    ready.set()
    conn, _ = srv.accept()
    with conn:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
    received.append(data.decode("utf-8"))
    srv.close()


# --- the address map ---------------------------------------------------

def test_address_from_env_reads_the_socket_and_its_pid():
    env = {
        inbox.SESSION_ENV: "abc-123",
        inbox.SOCKET_ENV: "/tmp/cc-socks/2156.sock",
        "CLAUDE_CODE_MESSAGING_TOKEN": "s3cr3t-tok",
    }
    addr = inbox.address_from_env(env)
    assert addr["session_id"] == "abc-123"
    assert addr["socket"] == "/tmp/cc-socks/2156.sock"
    # #553: the token beside the socket is never part of the address.
    assert "token" not in addr and "s3cr3t" not in json.dumps(addr)
    # The pid comes from the socket's name, not from getpid(): a hook runs in
    # a child of the session's own process.
    assert addr["pid"] == 2156


def test_address_from_env_is_none_outside_claude_code():
    assert inbox.address_from_env({}) is None
    assert inbox.address_from_env({inbox.SESSION_ENV: "abc"}) is None


def test_lookup_returns_the_newest_row_for_a_session(tmp_path):
    inbox.record(tmp_path, {"session_id": "s1", "socket": "/old.sock", "pid": 1})
    inbox.record(tmp_path, {"session_id": "other", "socket": "/x.sock", "pid": 2})
    inbox.record(tmp_path, {"session_id": "s1", "socket": "/new.sock", "pid": 3})
    assert inbox.lookup(tmp_path, "s1")["socket"] == "/new.sock"
    assert inbox.lookup(tmp_path, "missing") is None


def test_record_and_lookup_never_raise_on_a_bad_vault(tmp_path):
    bad = tmp_path / "file"
    bad.write_text("not a dir", encoding="utf-8")
    inbox.record(bad, {"session_id": "s", "socket": "/a.sock", "pid": 1})
    assert inbox.lookup(bad, "s") is None


# --- liveness: the recycled-pid guard ----------------------------------

def test_is_live_false_when_the_socket_is_gone(tmp_path):
    assert inbox.is_live(_addr(tmp_path, tmp_path / "nope.sock")) is False


def test_is_live_false_when_the_pid_was_recycled(tmp_path, monkeypatch):
    sock = tmp_path / "live.sock"
    sock.touch()
    # Same pid, different process: a later `claude` inherited the number.
    monkeypatch.setattr(inbox, "pid_start", lambda pid: "Thu Sep 18 09:00:00 2026")
    assert inbox.is_live(_addr(tmp_path, sock)) is False


def test_is_live_true_when_pid_and_start_still_match(tmp_path, monkeypatch):
    sock = tmp_path / "live.sock"
    sock.touch()
    monkeypatch.setattr(inbox, "pid_start", lambda pid: "Wed Sep 17 03:00:00 2026")
    assert inbox.is_live(_addr(tmp_path, sock)) is True


def test_is_live_false_when_the_process_is_gone(tmp_path, monkeypatch):
    sock = tmp_path / "live.sock"
    sock.touch()
    monkeypatch.setattr(inbox, "pid_start", lambda pid: "")
    assert inbox.is_live(_addr(tmp_path, sock)) is False


# --- the wire ----------------------------------------------------------

@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="POSIX sockets only")
def test_post_writes_a_user_turn_a_session_would_accept(short_dir):
    sock_path = pathlib.Path(short_dir) / "s.sock"
    received, ready = [], threading.Event()
    t = threading.Thread(target=_serve, args=(sock_path, received, ready), daemon=True)
    t.start()
    ready.wait(timeout=5)

    assert inbox.post({"socket": str(sock_path), "token": None}, "hello") is True
    t.join(timeout=5)

    payload = json.loads(received[0].strip())
    assert payload["type"] == "user"
    assert payload["message"] == {"role": "user", "content": "hello"}


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="POSIX sockets only")
def test_post_never_sends_an_auth_line_even_for_a_legacy_row(short_dir):
    """#553: a row written before the fix still carries a token until the next
    session start rewrites the file. It must not go out: the env token is the
    session's own child token, and a sibling's hook is not its child."""
    sock_path = pathlib.Path(short_dir) / "s.sock"
    received, ready = [], threading.Event()
    t = threading.Thread(target=_serve, args=(sock_path, received, ready), daemon=True)
    t.start()
    ready.wait(timeout=5)

    inbox.post({"socket": str(sock_path), "token": "secret"}, "hi")
    t.join(timeout=5)

    lines = [json.loads(x) for x in received[0].strip().splitlines()]
    assert [x["type"] for x in lines] == ["user"]
    assert "secret" not in received[0]


# --- the log on disk (#553) --------------------------------------------

def _rows(root):
    text = inbox.log_path(root).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_record_scrubs_legacy_tokens_and_prunes_exited_pids(tmp_path, monkeypatch):
    path = inbox.log_path(tmp_path)
    path.parent.mkdir(parents=True)
    legacy = [
        {"session_id": "live", "socket": "/s/10.sock", "token": "t-live", "pid": 10, "pid_start": "a"},
        {"session_id": "gone", "socket": "/s/11.sock", "token": "t-gone", "pid": 11, "pid_start": "b"},
    ]
    path.write_text("".join(json.dumps(r) + "\n" for r in legacy) + "{torn\n", encoding="utf-8")
    monkeypatch.setattr(inbox, "live_pids", lambda: {10, 12})

    inbox.record(tmp_path, {"session_id": "new", "socket": "/s/12.sock", "pid": 12, "pid_start": "c"})

    text = path.read_text(encoding="utf-8")
    assert "token" not in text and "t-live" not in text and "t-gone" not in text
    assert [r["session_id"] for r in _rows(tmp_path)] == ["live", "new"]
    assert _rows(tmp_path)[0] == {"session_id": "live", "socket": "/s/10.sock", "pid": 10, "pid_start": "a"}


def test_record_keeps_every_row_when_the_process_table_is_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "live_pids", lambda: None)
    inbox.record(tmp_path, {"session_id": "a", "socket": "/s/1.sock", "pid": 1, "token": "x"})
    inbox.record(tmp_path, {"session_id": "b", "socket": "/s/2.sock", "pid": 2})
    assert [r["session_id"] for r in _rows(tmp_path)] == ["a", "b"]
    assert "token" not in inbox.log_path(tmp_path).read_text(encoding="utf-8")


def test_record_never_prunes_the_row_it_is_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "live_pids", lambda: {999})
    inbox.record(tmp_path, {"session_id": "me", "socket": "/s/5.sock", "pid": 5})
    assert [r["session_id"] for r in _rows(tmp_path)] == ["me"]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes only")
def test_the_log_is_readable_by_its_owner_only(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "live_pids", lambda: None)
    path = inbox.log_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    path.chmod(0o644)
    inbox.record(tmp_path, {"session_id": "a", "socket": "/s/1.sock", "pid": 1})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_stuck_lock_costs_the_rewrite_not_the_row(tmp_path, monkeypatch):
    monkeypatch.setattr(inbox, "LOCK_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(inbox, "live_pids", lambda: set())
    (tmp_path / ".mnemo" / inbox.LOCK_NAME).mkdir(parents=True)
    inbox.record(tmp_path, {"session_id": "a", "socket": "/s/1.sock", "pid": 1, "token": "x"})
    assert _rows(tmp_path) == [{"session_id": "a", "socket": "/s/1.sock", "pid": 1}]


def test_live_pids_includes_this_process():
    if os.name != "posix":
        pytest.skip("ps is POSIX")
    assert os.getpid() in (inbox.live_pids() or set())


def test_post_is_false_when_nothing_listens(tmp_path):
    assert inbox.post({"socket": str(tmp_path / "dead.sock")}, "hi") is False


def test_notify_drops_the_notice_when_the_parent_is_gone(tmp_path, monkeypatch):
    inbox.record(tmp_path, {"session_id": "p", "socket": str(tmp_path / "x.sock"), "pid": 1})
    monkeypatch.setattr(inbox, "post", lambda *a, **k: pytest.fail("must not send"))
    assert inbox.notify(tmp_path, "p", "text") is False


LIVE_PID = 75451


def _dead_pid() -> int:
    return 28611


@pytest.fixture
def processes(monkeypatch):
    """A process table with one live process, :data:`LIVE_PID`. ``pid_start``
    shells to ``ps``, which Windows lacks; ``is_live`` and ``resolve`` run as
    shipped on top of it."""
    monkeypatch.setattr(
        inbox, "pid_start",
        lambda pid: "Tue Sep 22 16:32:36 2026" if int(pid) == LIVE_PID else "",
    )
    monkeypatch.setattr(inbox, "live_pids", lambda: {LIVE_PID})


def _live_row(sid, sock_path):
    return {"session_id": sid, "socket": str(sock_path),
            "pid": LIVE_PID, "pid_start": "Tue Sep 22 16:32:36 2026"}


def test_resolve_skips_a_newer_row_whose_process_exited(tmp_path, processes):
    """#454, the 2026-09-22 shape: the parent stays open in one process while
    a second process resumes its id, writes a newer row, and exits. The live
    one is the address; newest-wins sent every later child's report nowhere."""
    live_sock = tmp_path / "live.sock"
    live_sock.write_text("", encoding="utf-8")
    inbox.record(tmp_path, _live_row("parent-uuid", live_sock))
    inbox.record(tmp_path, {"session_id": "parent-uuid", "socket": str(tmp_path / "gone.sock"),
                            "pid": _dead_pid(), "pid_start": "Tue Sep 22 19:32:46 2026"})

    assert not inbox.is_live(inbox.lookup(tmp_path, "parent-uuid"))
    address, why = inbox.resolve(tmp_path, "parent-uuid")
    assert why == "" and address["socket"] == str(live_sock)


def test_resolve_says_why_when_nothing_is_live(tmp_path, processes):
    assert inbox.resolve(tmp_path, "p") == (None, "no address recorded for the parent in session-inbox.jsonl")
    pid = _dead_pid()
    inbox.record(tmp_path, {"session_id": "p", "socket": str(tmp_path / "x.sock"),
                            "pid": pid, "pid_start": "x"})
    address, why = inbox.resolve(tmp_path, "p")
    assert address is None
    assert why == f"parent not live: none of its 1 recorded address(es) is (pid {pid})"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="POSIX sockets only")
def test_deliver_reaches_the_live_parent_behind_a_dead_newer_row(short_dir, processes):
    sock = pathlib.Path(short_dir) / "p.sock"
    received, ready = [], threading.Event()
    t = threading.Thread(target=_serve, args=(sock, received, ready))
    t.start()
    ready.wait(5)
    inbox.record(short_dir, _live_row("p", sock))
    inbox.record(short_dir, {"session_id": "p", "socket": str(pathlib.Path(short_dir) / "d.sock"),
                             "pid": _dead_pid(), "pid_start": "x"})

    assert inbox.deliver(short_dir, "p", "hello parent") == ""
    t.join(5)
    assert "hello parent" in received[0]


def test_deliver_names_a_socket_that_refused_the_write(tmp_path, processes):
    sock = tmp_path / "not-a-socket.sock"
    sock.write_text("", encoding="utf-8")
    inbox.record(tmp_path, _live_row("p", sock))
    assert inbox.deliver(tmp_path, "p", "hi") == f"socket write failed: {sock}"


# --- the sibling-channel interaction -----------------------------------

def test_a_notice_is_not_counted_as_a_human_unblocking_the_parent():
    """The sweep runs in the same hook that sends the notice (#176/#195).

    Without the marker, mnemo's own message would look like the maintainer
    answering a blocked parent, and the detector would record a false edge.
    """
    text = (
        f'{inbox.NOTICE_PREFIX} id="c0da0f55">\n'
        "c0da0f55 finished. mnemo is reporting a dispatched child's exit."
    )
    record = {"type": "user", "message": {"role": "user", "content": text}}
    assert detector.is_human_turn(record) is False
    # A real answer in the same shape still counts, so the guard is not a
    # blanket mute on peer turns.
    human = {"type": "user", "message": {"role": "user", "content": "pode mergear"}}
    assert detector.is_human_turn(human) is True


def test_notice_prefix_stays_in_the_detectors_synthetic_list():
    """`detector` spells the prefix out to avoid importing `inbox`; this is
    the pin that keeps the two copies equal."""
    assert inbox.NOTICE_PREFIX in detector.SYNTHETIC_PREFIXES
