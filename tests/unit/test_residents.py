"""``mnemo doctor`` separates the daemon's idle spare from resident children (#280).

The process table below is the machine's real ``ps`` output from 2026-09-14,
trimmed to the daemon's processes and with the argv shortened only after the
spare marker — the shape the census keys on (a claimed spare keeps its
``--bg-spare`` argv; a resumed worker carries ``--resume`` instead) is kept
verbatim. The roster is the real ``daemon/roster.json`` reduced to the fields
read. Fixtures that mirror an imagined shape have hidden four parser bugs in
this repo; this one mirrors the measured one.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands import doctor as doctor_mod
from mnemo.cli.commands.doctor_checks import background_processes
from mnemo.core import claude_cli
from mnemo.core.sessions import residents

SPARE = "/tmp/cc-daemon-501/3c0b3d77/spare"
VERSIONS = "/Users/xyrlan/.local/share/claude/versions/2.1.270"

REAL_PS = f"""\
36487     1 106912 01-21:27:13 /Users/xyrlan/.local/bin/claude daemon run --json-path /Users/xyrlan/.claude/daemon.json --log-file /Users/xyrlan/.claude/daemon.log --origin transient
 5072 36487  23056 04:30:32 /Users/xyrlan/.local/share/claude/ClaudeCode.app/Contents/MacOS/claude --bg-pty-host {SPARE}/25b53d8e.pty.sock 200 50 -- {VERSIONS} --resume /x/14fa8a4d.jsonl --model opus[1m]
 5090  5072 300000 04:30:30 claude --resume /x/14fa8a4d.jsonl
22998 36487  88832    01:20 claude bg-pty-host --bg-pty-host {SPARE}/ab88f713.pty.sock 200 50 -- {VERSIONS} --bg-spare {SPARE}/ab88f713.claim.sock
23009 22998 278432    01:20 claude bg-spare --bg-spare {SPARE}/ab88f713.claim.sock
24321 23009   2688    00:00 /bin/zsh -c source snapshot.sh
23120 36487  86384    01:19 claude bg-pty-host --bg-pty-host {SPARE}/77d6079b.pty.sock 200 50 -- {VERSIONS} --bg-spare {SPARE}/77d6079b.claim.sock
23135 23120 120208    01:19 claude bg-spare --bg-spare {SPARE}/77d6079b.claim.sock
58007 36487  43552 03:08:19 claude bg-pty-host --bg-pty-host {SPARE}/468c23fa.pty.sock 200 50 -- {VERSIONS} --bg-spare {SPARE}/468c23fa.claim.sock
64112 58007 282000 02:58:17 claude bg-spare --bg-spare {SPARE}/468c23fa.claim.sock
"""

REAL_ROSTER = {
    "proto": 1,
    "supervisorPid": 36487,
    "workers": {
        "14fa8a4d": {"pid": 5072, "ptySock": f"{SPARE}/25b53d8e.pty.sock"},
        "63d152a5": {"pid": 58007, "ptySock": f"{SPARE}/468c23fa.pty.sock"},
        "f4f35061": {"pid": 22998, "ptySock": f"{SPARE}/ab88f713.pty.sock"},
    },
}

STATES = {"14fa8a4d": "done", "63d152a5": "working", "f4f35061": "working"}


@pytest.mark.parametrize(("text", "seconds"), [
    ("00:00", 0), ("01:20", 80), ("03:08:19", 11299),
    ("01-21:27:13", 163633), ("  05:07 ", 307), ("garbage", None), ("1:2:3:4", None),
])
def test_parse_etime(text: str, seconds: int | None) -> None:
    assert residents.parse_etime(text) == seconds


def test_parse_ps_skips_what_it_cannot_read() -> None:
    procs = residents.parse_ps(REAL_PS + "not a line\nabc 1 2 00:01 cmd\n")
    assert len(procs) == 10
    assert procs[23009].ppid == 22998 and procs[23009].rss_kb == 278432


def _real_census() -> residents.Census:
    return residents.census(residents.parse_ps(REAL_PS), REAL_ROSTER, STATES)


def test_a_claimed_spare_is_a_worker_not_a_spare() -> None:
    """The #280 misreading: 58007/64112 carry ``--bg-spare`` and are a child."""
    found = _real_census()
    assert [s.pid for s in found.spares] == [23120]
    assert {w.short_id: w.pid for w in found.workers} == {
        "14fa8a4d": 5072, "63d152a5": 58007, "f4f35061": 22998,
    }


def test_rss_is_summed_over_each_host_tree() -> None:
    found = _real_census()
    assert found.spares[0].rss_kb == 86384 + 120208
    by_id = {w.short_id: w for w in found.workers}
    assert by_id["f4f35061"].rss_kb == 88832 + 278432 + 2688
    assert by_id["14fa8a4d"].rss_kb == 23056 + 300000  # resumed: no spare marker, still counted


def test_finished_is_done_workers_only() -> None:
    found = _real_census()
    assert [w.short_id for w in found.finished] == ["14fa8a4d"]
    assert found.spares[0].orphaned is False


def test_only_a_done_child_is_finished() -> None:
    """Only ``done`` is offered for stopping: ``blocked`` is waiting for a human."""
    for state in ("blocked", "working", None):
        states = {**STATES, "14fa8a4d": state}
        found = residents.census(residents.parse_ps(REAL_PS), REAL_ROSTER, states)
        assert found.finished == (), state


def test_a_worker_whose_process_is_gone_is_not_resident() -> None:
    roster = json.loads(json.dumps(REAL_ROSTER))
    roster["workers"]["deadbeef"] = {"pid": 99999}
    found = residents.census(residents.parse_ps(REAL_PS), roster, STATES)
    assert "deadbeef" not in {w.short_id for w in found.workers}


def test_a_spare_whose_host_died_is_orphaned() -> None:
    """A ``bg-spare`` reparented to launchd is a root and not the daemon's."""
    table = REAL_PS + f"70000     1 120000 1-19:00:00 claude bg-spare --bg-spare {SPARE}/dead.claim.sock\n"
    found = residents.census(residents.parse_ps(table), REAL_ROSTER, STATES)
    orphan = [s for s in found.spares if s.pid == 70000]
    assert orphan and orphan[0].orphaned is True


def test_census_survives_a_malformed_roster() -> None:
    for roster in (None, {}, {"workers": []}, {"workers": {"x": {"pid": True}}, "supervisorPid": "1"}):
        found = residents.census(residents.parse_ps(REAL_PS), roster, {})
        assert found.workers == ()
        # Without a roster every host looks idle — which is why doctor stays silent then.
        assert len(found.spares) == 3


def test_a_pid_cycle_does_not_hang() -> None:
    procs = {1: residents.Proc(1, 2, 10, 0, "a --bg-spare x"), 2: residents.Proc(2, 1, 20, 0, "b")}
    found = residents.census(procs, {"workers": {"abcd1234": {"pid": 1}}}, {})
    assert found.workers[0].rss_kb == 30


@pytest.mark.parametrize(("kb", "text"), [(206592, "202 MB"), (2296480, "2.19 GB")])
def test_format_mb(kb: int, text: str) -> None:
    assert residents.format_mb(kb) == text


@pytest.mark.parametrize(("seconds", "text"), [(80, "1m"), (11299, "3h08m"), (163633, "1d21h")])
def test_format_age(seconds: int, text: str) -> None:
    assert residents.format_age(seconds) == text


def test_read_ps_is_silent_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(residents.sys, "platform", "win32")
    assert residents.read_ps() is None


# --- the doctor row --------------------------------------------------------


@pytest.fixture
def home(tmp_path: Path) -> Path:
    root = tmp_path / "claude"
    (root / "daemon").mkdir(parents=True)
    (root / "daemon" / "roster.json").write_text(json.dumps(REAL_ROSTER), encoding="utf-8")
    jobs = root / "jobs"
    for short_id, state in STATES.items():
        (jobs / short_id).mkdir(parents=True)
        (jobs / short_id / "state.json").write_text(
            json.dumps({"state": state, "tempo": "idle" if state == "done" else "active"}),
            encoding="utf-8",
        )
    return root


def _run(home: Path, table: str, capsys) -> str:
    ok = background_processes._doctor_check_background_processes(
        ps_stdout=table, claude_home=home, jobs_root=home / "jobs",
    )
    assert ok is True  # advisory, never fails doctor
    return capsys.readouterr().out


def test_doctor_names_the_split_and_the_finished_child(home: Path, capsys) -> None:
    out = _run(home, REAL_PS, capsys)
    assert "✓ claude daemon: 1 idle spare (202 MB, oldest 1m); 3 children resident" in out
    assert "1 done, 2 working" in out
    assert "`claude stop <id>`" in out and "14fa8a4d" in out
    assert "63d152a5" not in out  # a working child is never offered for stopping


def test_doctor_warns_on_more_than_one_idle_spare(home: Path, capsys) -> None:
    table = REAL_PS + (
        f"80000 36487  86000    00:30 claude bg-pty-host --bg-pty-host {SPARE}/e.pty.sock -- --bg-spare {SPARE}/e.claim.sock\n"
    )
    out = _run(home, table, capsys)
    assert "⚠ claude daemon: 2 idle spares" in out


def test_doctor_names_an_orphaned_spare(home: Path, capsys) -> None:
    table = REAL_PS + f"70000     1 120000 1-19:00:00 claude bg-spare --bg-spare {SPARE}/dead.claim.sock\n"
    out = _run(home, table, capsys)
    assert "⚠" in out and "not owned by the running daemon (pid 70000)" in out


def test_doctor_is_silent_without_a_roster(tmp_path: Path, capsys) -> None:
    """No roster → a claimed spare cannot be told from an idle one; say nothing."""
    assert _run(tmp_path / "nothing", REAL_PS, capsys) == ""


def test_doctor_is_silent_without_ps(home: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr(residents, "read_ps", lambda: None)
    out_ok = background_processes._doctor_check_background_processes(claude_home=home)
    assert out_ok is True and capsys.readouterr().out == ""


def test_doctor_with_a_roster_and_nothing_running(home: Path, capsys) -> None:
    out = _run(home, "    1     0  1000 10-00:00:00 /sbin/launchd\n", capsys)
    assert "no background claude processes" in out


def test_doctor_lists_at_most_five_ids(home: Path, capsys) -> None:
    roster = {"supervisorPid": 36487, "workers": {}}
    lines = [REAL_PS.splitlines()[0]]
    for n in range(7):
        sid = f"0000000{n}"
        roster["workers"][sid] = {"pid": 1000 + n}
        lines.append(f"{1000 + n} 36487 1024 01:00:00 claude --bg-pty-host x -- --resume y")
        (home / "jobs" / sid).mkdir(parents=True)
        (home / "jobs" / sid / "state.json").write_text('{"state": "done", "tempo": "idle"}', encoding="utf-8")
    (home / "daemon" / "roster.json").write_text(json.dumps(roster), encoding="utf-8")
    out = _run(home, "\n".join(lines) + "\n", capsys)
    assert "7 finished" in out and "and 2 more" in out


def test_registered_in_doctor() -> None:
    """Among the process rows at the end of the registry. It stopped being the
    last one when #329 added a second census beside it; what matters is that
    doctor calls it at all — an unregistered check is a measurement nobody
    ever sees."""
    assert (
        "background_processes", background_processes._doctor_check_background_processes,
    ) in doctor_mod.DOCTOR_CHECKS


def test_the_pool_assumption_is_stated() -> None:
    a = claude_cli.assumption("daemon-spare-pool")
    assert "sessions.residents.census" in a.used_by
