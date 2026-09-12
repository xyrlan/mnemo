"""Whether a background session's process is still alive.

``state.json`` records no pid — the issue was right about that. But the
daemon keeps ``~/.claude/daemon/roster.json``, whose ``workers`` map is keyed
by the *same short id* as the job directories and carries a real pid. That is
a liveness probe, not a proxy for one, so it is what the queue uses.

Measured against the real roster on 2026-09-12: 3 workers, 2 with job dirs,
every pid live under :func:`liveness.pid_alive`.

The probe is platform-split: signal 0 on POSIX, ``OpenProcess`` +
``GetExitCodeProcess`` on Windows, where ``os.kill(pid, 0)`` is a console
Ctrl-C rather than a probe (#196).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

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


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX branch; Windows uses OpenProcess")
def test_pid_alive_is_false_for_a_reaped_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pid the OS no longer knows is dead, not alive.

    The condition is injected rather than produced. Earlier revisions made a
    real corpse and both ways broke Windows: ``os.fork`` does not exist there,
    and ``subprocess.Popen`` + ``wait()`` hung the runner. What is under test is
    the mapping from the OS's answer to a bool, so ask ``os.kill`` to give that
    answer — which also keeps this test on the branch it describes.
    """
    def _reaped(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _reaped)

    assert liveness.pid_alive(4242) is False


# --- classify --------------------------------------------------------------

def test_session_in_roster_with_a_live_pid_is_live(tmp_path: Path) -> None:
    _roster(tmp_path, {"a3f1": {"pid": os.getpid()}})

    assert liveness.is_live("a3f1", claude_home=tmp_path) is True


def test_session_absent_from_a_readable_roster_is_dead(tmp_path: Path) -> None:
    _roster(tmp_path, {"other": {"pid": os.getpid()}})

    assert liveness.is_live("a3f1", claude_home=tmp_path) is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX branch; Windows uses OpenProcess")
def test_session_in_roster_with_a_dead_pid_is_dead(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _reaped(pid: int, sig: int) -> None:
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", _reaped)
    _roster(tmp_path, {"a3f1": {"pid": 4242}})

    assert liveness.is_live("a3f1", claude_home=tmp_path) is False


def test_unknown_when_the_roster_cannot_be_read(tmp_path: Path) -> None:
    """No roster means we must not accuse: unknown, never dead.

    Downstream treats unknown as live, because a false 'dead' hides a session
    that really is waiting for a human — the exact failure the queue exists to
    prevent.
    """
    assert liveness.is_live("a3f1", claude_home=tmp_path) is None


def test_windows_never_probes_with_os_kill() -> None:
    """``os.kill(pid, 0)`` must never run on Windows (#196).

    ``signal 0`` is ``CTRL_C_EVENT`` there, so CPython takes the
    console-control branch: ``GenerateConsoleCtrlEvent`` ignores the pid and
    Ctrl-Cs every process sharing the console, then reports success. It is not
    a probe — it is a self-inflicted interrupt that also always answers
    "alive".

    On the Windows runner this killed the suite from inside: a Ctrl-C fired by
    one test surfaced as ``KeyboardInterrupt`` in an unrelated one a second
    later (delivery is asynchronous), landing somewhere different every run.
    In production `jobs.read_sessions` probes every rostered session, and the
    statusline calls it on every render.

    Asserting the branch rather than the symptom, since the symptom is only
    reproducible on Windows.
    """
    import ast
    from pathlib import Path

    mod = Path(liveness.__file__)
    tree = ast.parse(mod.read_text(encoding="utf-8"))

    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "pid_alive"
    )
    guards = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Attribute) and node.attr == "platform"
    ]

    assert guards, (
        "pid_alive() has no sys.platform guard — os.kill(pid, 0) on Windows "
        "sends a console Ctrl-C instead of probing the pid (#196)"
    )


def test_windows_probe_declares_its_ctypes_signatures() -> None:
    """Every kernel32 call must declare ``restype`` and ``argtypes``.

    ctypes assumes ``c_int`` for an undeclared return, so on 64-bit Windows the
    HANDLE from ``OpenProcess`` is truncated to 32 bits — and the code then
    closes the wrong handle, or fails to close it at all. It survives whenever
    a handle value happens to be small, which is why it passes CI and would
    surface as an intermittent leak on a real machine.

    Checked by parsing the source: the calls cannot run on this platform, and
    the failure they guard against is silent rather than raising.
    """
    import ast
    from pathlib import Path

    tree = ast.parse(Path(liveness.__file__).read_text(encoding="utf-8"))
    fn = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_pid_alive_windows"
    )

    called = {
        node.func.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "kernel32"
    }
    declared = {
        node.targets[0].value.attr
        for node in ast.walk(fn)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Attribute)
        and node.targets[0].attr in ("restype", "argtypes")
        and isinstance(node.targets[0].value, ast.Attribute)
    }

    assert called <= declared, (
        f"kernel32 call(s) without declared restype/argtypes: {sorted(called - declared)}. "
        "An undeclared HANDLE return is truncated to 32 bits on 64-bit Windows."
    )
