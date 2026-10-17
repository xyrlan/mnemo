"""Finding a live session's own process in Claude Code's registry (#723)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest

from mnemo.core.sessions import registry

SID = "f244f830-c48a-469b-9ece-7585b5aa43c7"
HOST = "win32:desktop-test"

#: pid -> creation time of the processes running "now".
RUNNING: Dict[int, int] = {53360: 134367181883724083, 59668: 134367190000000000}


def _created(pid: int) -> Optional[int]:
    return RUNNING.get(pid)


def _write(folder: Path, file_pid: int, **fields) -> None:
    entry = {"pid": file_pid, "sessionId": SID,
             "procStart": str(RUNNING.get(file_pid, 1)),
             "pidDomain": HOST, "kind": "bg", **fields}
    (folder / f"{file_pid}.json").write_text(json.dumps(entry), encoding="utf-8")


def _live(folder: Path, sid: str = SID):
    return registry.live_entry(sid, directory=folder, domain=HOST, creation_time=_created)


def test_the_running_one_of_several_entries_for_a_resumed_session(tmp_path: Path) -> None:
    _write(tmp_path, 41000, procStart="134300000000000000")   # first run, exited
    _write(tmp_path, 42000, procStart="134310000000000000")   # a wake, exited
    _write(tmp_path, 59668)                                   # the wake running now
    assert _live(tmp_path)["pid"] == 59668


def test_a_reused_pid_is_not_the_session(tmp_path: Path) -> None:
    """The pid runs, but it is a process created after the entry was written."""
    _write(tmp_path, 53360, procStart="134300000000000000")
    assert _live(tmp_path) is None


def test_another_hosts_entry_is_never_this_host_s(tmp_path: Path) -> None:
    _write(tmp_path, 53360, pidDomain="win32:another-machine")
    assert _live(tmp_path) is None


def test_another_session_id_is_not_matched(tmp_path: Path) -> None:
    _write(tmp_path, 53360, sessionId="00000000-0000-0000-0000-000000000000")
    assert _live(tmp_path) is None
    assert _live(tmp_path, sid="") is None


@pytest.mark.parametrize("bad", [
    {"pid": "53360"}, {"pid": True}, {"pid": -1}, {"procStart": None},
    {"procStart": "soon"}, {"pidDomain": None},
])
def test_an_entry_missing_what_proves_it_is_skipped(tmp_path: Path, bad) -> None:
    _write(tmp_path, 53360, **bad)
    assert _live(tmp_path) is None


def test_unreadable_files_and_a_missing_directory_are_none(tmp_path: Path) -> None:
    (tmp_path / "1.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "2.json").write_text("[]", encoding="utf-8")
    assert _live(tmp_path) is None
    assert _live(tmp_path / "missing") is None


def test_an_integer_proc_start_is_accepted(tmp_path: Path) -> None:
    _write(tmp_path, 53360, procStart=RUNNING[53360])
    assert _live(tmp_path)["pid"] == 53360


def test_the_directory_follows_the_account(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "acct"))
    assert registry.sessions_dir() == tmp_path / "acct" / "sessions"
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert registry.sessions_dir() == Path.home() / ".claude" / "sessions"


def test_the_host_domain_is_the_platform_and_lowercase_hostname(monkeypatch) -> None:
    monkeypatch.setattr(registry.socket, "gethostname", lambda: "DESKTOP-8B0ED29")
    assert registry.host_domain() == f"{sys.platform}:desktop-8b0ed29"


def test_nothing_is_confirmed_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(registry.sys, "platform", "linux")
    assert registry.windows_creation_time(os.getpid()) is None


@pytest.mark.skipif(sys.platform != "win32", reason="FILETIME creation times are Windows")
def test_a_real_process_is_found_by_its_entry_and_lost_when_it_exits(tmp_path: Path) -> None:
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        start = registry.windows_creation_time(proc.pid)
        assert start and start > 0
        entry = {"pid": proc.pid, "sessionId": SID, "procStart": str(start),
                 "pidDomain": registry.host_domain(), "kind": "bg"}
        (tmp_path / f"{proc.pid}.json").write_text(json.dumps(entry), encoding="utf-8")
        assert registry.live_entry(SID, directory=tmp_path)["pid"] == proc.pid
    finally:
        proc.kill()
        proc.wait()
    assert registry.live_entry(SID, directory=tmp_path) is None
