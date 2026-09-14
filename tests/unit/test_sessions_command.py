"""``mnemo sessions`` wiring: what scope the command hands the reader.

The reader's own matching is covered in :mod:`tests.unit.test_sessions_jobs`.
What is checked here is only that the command normalizes the cwd it passes —
a worktree or a macOS ``/tmp`` path otherwise reaches the reader in a form
that matches nothing and the queue reports an empty repo — and that the
detector's sweep rides the read.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mnemo.cli.commands import sessions as sessions_cmd


def _run(monkeypatch, cwd: str, **flags) -> list[str | None]:
    seen: list[str | None] = []

    def fake_read_sessions(root=None, *, cwd=None):
        seen.append(cwd)
        return []

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", fake_read_sessions)
    monkeypatch.setattr("os.getcwd", lambda: cwd)

    args = argparse.Namespace(json=False, watch=False, **{"all": False, **flags})
    assert sessions_cmd.cmd_sessions(args) == 0
    return seen


def test_passes_the_normalized_cwd_to_the_reader(monkeypatch, tmp_path: Path) -> None:
    real = tmp_path / "real-repo"
    real.mkdir()
    link = tmp_path / "linked-repo"
    link.symlink_to(real)

    seen = _run(monkeypatch, str(link) + "/")

    # The scope the queue is read with. An empty result adds a second,
    # unscoped read to count what is elsewhere (#281), so pin the first —
    # asserting on the whole list would be pinning that follow-up by accident.
    assert seen[0] == str(real.resolve())


def test_all_passes_no_scope(monkeypatch, tmp_path: Path) -> None:
    seen = _run(monkeypatch, str(tmp_path), all=True)

    assert seen == [None]


def test_reading_the_queue_sweeps_for_unblocks(monkeypatch, tmp_path: Path) -> None:
    """The detector rides the command: nothing else runs it."""
    vault = tmp_path / "vault"
    found = [object()]
    swept: list[tuple[object, Path]] = []

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: found,
    )
    monkeypatch.setattr("mnemo.core.sessions.render.render_queue", lambda s, a=None: "queue")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr(
        "mnemo.core.sessions.detector.sweep",
        lambda sessions, *, vault_root: swept.append((sessions, vault_root)) or 0,
    )

    args = argparse.Namespace(json=False, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert swept == [(found, vault)]


def test_the_json_path_sweeps_too(monkeypatch, tmp_path: Path) -> None:
    """``--json`` shares ``_read`` with the other two branches; nothing else
    pins that, and a machine-read queue is as good a sweep trigger as a human
    one."""
    vault = tmp_path / "vault"
    swept: list[tuple[object, Path]] = []

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: [],
    )
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr(
        "mnemo.core.sessions.detector.sweep",
        lambda sessions, *, vault_root: swept.append((sessions, vault_root)) or 0,
    )

    args = argparse.Namespace(json=True, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert swept == [([], vault)]


def test_an_unavailable_vault_still_prints_the_queue(monkeypatch, capsys) -> None:
    """A queue that refused to print because the vault moved would be useless."""
    def boom() -> Path:
        raise RuntimeError("vault is gone")

    monkeypatch.setattr(
        "mnemo.core.sessions.jobs.read_sessions",
        lambda root=None, *, cwd=None: [],
    )
    monkeypatch.setattr("mnemo.core.sessions.render.render_queue", lambda s, a=None: "the queue")
    monkeypatch.setattr("mnemo.cli._resolve_vault", boom)

    args = argparse.Namespace(json=False, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert "the queue" in capsys.readouterr().out


def test_consume_unblocks_redeems_the_markers(monkeypatch, tmp_path: Path) -> None:
    """``--consume-unblocks`` is the production reader ``pending_unblocks``
    never had (#195). It must not print the queue: the hook spawns it
    detached, and a human running it wants the ledger delta, not a listing.
    """
    vault = tmp_path / "vault"
    calls: list[Path] = []

    class _Report:
        consumed, failed, skipped = 1, 0, 0
        learned = [{"slug": "argon2-not-bcrypt", "name": "Use argon2"}]
        errors: list[str] = []

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "mnemo.core.sessions.unblocks.consume",
        lambda cfg, *, vault_root: calls.append(vault_root) or _Report(),
    )
    monkeypatch.setattr(
        "mnemo.core.sessions.render.render_queue",
        lambda s: pytest.fail("must not render the queue"),
    )

    args = argparse.Namespace(
        json=False, watch=False, consume_unblocks=True, **{"all": False}
    )
    assert sessions_cmd.cmd_sessions(args) == 0
    assert calls == [vault]


def test_consume_unblocks_reports_what_it_learned(monkeypatch, capsys, tmp_path: Path) -> None:
    class _Report:
        consumed, failed, skipped = 1, 0, 0
        learned = [{"slug": "argon2-not-bcrypt", "name": "Use argon2"}]
        errors: list[str] = []

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "mnemo.core.sessions.unblocks.consume", lambda cfg, *, vault_root: _Report()
    )

    args = argparse.Namespace(
        json=False, watch=False, consume_unblocks=True, **{"all": False}
    )
    sessions_cmd.cmd_sessions(args)

    out = capsys.readouterr().out
    assert "argon2-not-bcrypt" in out


def test_consume_unblocks_says_so_when_there_is_nothing(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    """Silence would read as a broken command; this is the verb a maintainer
    runs by hand to check the wire is alive."""
    class _Report:
        consumed, failed, skipped = 0, 0, 0
        learned: list = []
        errors: list[str] = []

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_path)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {})
    monkeypatch.setattr(
        "mnemo.core.sessions.unblocks.consume", lambda cfg, *, vault_root: _Report()
    )

    args = argparse.Namespace(
        json=False, watch=False, consume_unblocks=True, **{"all": False}
    )
    assert sessions_cmd.cmd_sessions(args) == 0
    assert "no unblocked session" in capsys.readouterr().out


# --- append mode (#218) ------------------------------------------------
#
# The default redraw clears the screen every tick, so scrollback holds
# nothing and every row reads as equally new. Measured against six real
# dispatch children over 60s: 114 row-renders carried 7 actual changes
# (6.1%). Append mode emits the 6% and stays silent for the rest.


def _watch_args(**kw):
    kw.setdefault("json", False)
    kw.setdefault("watch", True)
    kw.setdefault("append", False)
    kw.setdefault("interval", 2.0)
    kw.setdefault("consume_unblocks", False)
    kw.setdefault("all", True)
    return argparse.Namespace(**kw)


@pytest.fixture
def _ticker(monkeypatch):
    """Drive the watch loop a bounded number of ticks, then Ctrl-C out."""

    def drive(ticks: int):
        seen = [0]

        def fake_sleep(_):
            seen[0] += 1
            if seen[0] >= ticks:
                raise KeyboardInterrupt

        monkeypatch.setattr("time.sleep", fake_sleep)
        return seen

    return drive


def _moving_session(monkeypatch, tmp_path, writes):
    """A session whose transcript grows by one tool use per tick."""
    import json as _json

    from mnemo.core.sessions.jobs import Session

    p = tmp_path / "child.jsonl"
    p.write_bytes(b"")
    session = Session(short_id="abc", tempo="active", name="child",
                      link_scan_path=str(p))

    pending = list(writes)

    def fake_read(**kw):
        # Each read is a tick; append the next action before it is summarized.
        if pending:
            tool, target = pending.pop(0)
            with open(p, "ab") as fh:
                fh.write(_json.dumps({
                    "type": "assistant",
                    "timestamp": "2026-09-13T14:02:11.000Z",
                    "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": "t", "name": tool,
                         "input": {"file_path": target}}]},
                }).encode("utf-8") + b"\n")
        return [session]

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", fake_read)
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda *a, **kw: None)
    return session


def test_append_mode_never_clears_the_screen(
    monkeypatch, capsys, tmp_path: Path, _ticker
) -> None:
    """The whole point: scrollback must survive the watch."""
    _moving_session(monkeypatch, tmp_path, [("Edit", "/r/a.py"), ("Read", "/r/b.py")])
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    _ticker(3)

    assert sessions_cmd.cmd_sessions(_watch_args(append=True)) == 0

    assert "\033[2J" not in capsys.readouterr().out


def test_append_mode_stays_silent_when_nothing_moves(
    monkeypatch, capsys, tmp_path: Path, _ticker
) -> None:
    """94% of real row-renders are unchanged; they must not be reprinted."""
    _moving_session(monkeypatch, tmp_path, [("Edit", "/r/a.py")])
    _ticker(4)

    assert sessions_cmd.cmd_sessions(_watch_args(append=True)) == 0

    out = capsys.readouterr().out
    assert out.count("a.py") == 1, out


def test_append_mode_emits_a_row_that_moved(
    monkeypatch, capsys, tmp_path: Path, _ticker
) -> None:
    _moving_session(monkeypatch, tmp_path, [("Edit", "/r/a.py"), ("Read", "/r/b.py")])
    _ticker(3)

    assert sessions_cmd.cmd_sessions(_watch_args(append=True)) == 0

    out = capsys.readouterr().out
    assert "a.py" in out and "b.py" in out, out


def test_append_mode_identifies_which_session_moved(
    monkeypatch, capsys, tmp_path: Path, _ticker
) -> None:
    """A bare tool name is unreadable with four children in the log."""
    _moving_session(monkeypatch, tmp_path, [("Edit", "/r/a.py")])
    _ticker(2)

    assert sessions_cmd.cmd_sessions(_watch_args(append=True)) == 0

    assert "abc" in capsys.readouterr().out


def test_the_default_watch_still_clears(
    monkeypatch, capsys, tmp_path: Path, _ticker
) -> None:
    """Two modes, not a replacement — the redraw stays the default."""
    _moving_session(monkeypatch, tmp_path, [("Edit", "/r/a.py")])
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    _ticker(2)

    assert sessions_cmd.cmd_sessions(_watch_args()) == 0

    assert "\033[2J" in capsys.readouterr().out


def test_interval_is_honoured(monkeypatch, tmp_path: Path) -> None:
    """A twenty-minute dispatch does not need 600 redraws."""
    _moving_session(monkeypatch, tmp_path, [])
    slept: list[float] = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    assert sessions_cmd.cmd_sessions(_watch_args(interval=30.0)) == 0

    assert slept == [30.0]


def test_append_implies_watch(monkeypatch, capsys, tmp_path: Path, _ticker) -> None:
    """`--append` alone is a watch mode, not a silent no-op."""
    _moving_session(monkeypatch, tmp_path, [("Edit", "/r/a.py")])
    _ticker(2)

    assert sessions_cmd.cmd_sessions(_watch_args(watch=False, append=True)) == 0

    assert "a.py" in capsys.readouterr().out


# --- an empty scoped queue must not hide a non-empty machine (#281) --------


def _scoped_reads(monkeypatch, scoped, unscoped, cwd="/repo"):
    """Serve *scoped* to a scoped read and *unscoped* to an unscoped one."""
    def fake_read_sessions(root=None, *, cwd=None):
        return scoped if cwd is not None else unscoped

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", fake_read_sessions)
    monkeypatch.setattr("os.getcwd", lambda: cwd)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: Path("/vault"))
    monkeypatch.setattr(
        "mnemo.core.sessions.detector.sweep", lambda sessions, *, vault_root: 0
    )


def test_an_empty_scope_names_the_sessions_elsewhere(monkeypatch, capsys) -> None:
    # The failure #281 recorded: the queue answered "nothing is running" while
    # four sessions waited, one of them blocked for five hours. Being wrong
    # looked exactly like being right, so the empty case has to speak.
    _scoped_reads(monkeypatch, scoped=[], unscoped=[object(), object()])

    args = argparse.Namespace(json=False, watch=False, **{"all": False})
    assert sessions_cmd.cmd_sessions(args) == 0

    out = capsys.readouterr().out
    assert "2" in out
    assert "--all" in out


def test_an_empty_machine_says_nothing_extra(monkeypatch, capsys) -> None:
    # Nothing anywhere: the plain empty line is the whole truth, and a pointer
    # to `--all` would send the user after a queue that is also empty.
    _scoped_reads(monkeypatch, scoped=[], unscoped=[])

    args = argparse.Namespace(json=False, watch=False, **{"all": False})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert "--all" not in capsys.readouterr().out


def test_a_non_empty_scope_says_nothing_extra(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "mnemo.core.sessions.render.render_queue", lambda s, a=None: "the queue"
    )
    _scoped_reads(monkeypatch, scoped=[object()], unscoped=[object(), object()])

    args = argparse.Namespace(json=False, watch=False, **{"all": False})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert "--all" not in capsys.readouterr().out


def test_the_all_flag_never_points_at_itself(monkeypatch, capsys) -> None:
    _scoped_reads(monkeypatch, scoped=[], unscoped=[])

    args = argparse.Namespace(json=False, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0

    assert "--all" not in capsys.readouterr().out
