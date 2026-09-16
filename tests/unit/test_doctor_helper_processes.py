"""``mnemo doctor`` counts mnemo's own helper processes (#329).

The storm this row watches for has no other symptom a user can act on: the
machine goes slow and mnemo goes quiet (the circuit breaker is what finally
stopped the real one). So the row is a census — sweeps and ``claude --print``
helpers — with a threshold at the shape that only the bug produces.

The process table is injected rather than read: the real one belongs to
whatever else is running on the box.
"""
from __future__ import annotations

import pytest

from mnemo.cli.commands.doctor_checks import helper_processes as hp
from mnemo.core import hook_guard

_SWEEP = "/usr/bin/python3 -m mnemo sessions --consume-unblocks"
_HELPER = "/Users/x/.local/bin/claude --print --strict-mcp-config --output-format json"
_SPARE = "claude bg-spare --bg-spare /tmp/cc-daemon-501/a/spare/1.claim.sock"
_SESSION = "claude --resume 889a978d-0ccc-46a1-a9b6-686ce2ac84d7"


def _ps(*commands: str) -> str:
    return "\n".join(
        f"{1000 + n} 1 120000 05:0{n} {command}" for n, command in enumerate(commands)
    )


@pytest.fixture(autouse=True)
def _no_guard_in_the_environment(monkeypatch) -> None:
    """The env row is asserted on its own; every other test wants it silent."""
    monkeypatch.delenv(hook_guard.HOOKS_OFF_ENV, raising=False)


# --- what counts ----------------------------------------------------------


def test_recognises_a_sweep_however_it_was_launched() -> None:
    """``mnemo``, ``python -m mnemo`` and a frozen build spell the verb three
    ways; the flag is the token all three share."""
    assert hp.is_sweep(_SWEEP)
    assert hp.is_sweep("/opt/mnemo/mnemo sessions --consume-unblocks")
    assert not hp.is_sweep("mnemo sessions --watch")


def test_only_claudes_own_print_flag_counts() -> None:
    """A ``--print`` belonging to some other program is not a helper, and the
    daemon's resident processes are `claude` but carry no print flag — those
    are the background_processes row's business, not this one."""
    assert hp.is_helper(_HELPER)
    assert hp.is_helper("claude -p 'what changed'")
    assert not hp.is_helper(_SPARE)
    assert not hp.is_helper(_SESSION)
    assert not hp.is_helper("/usr/bin/lp --print report.pdf")
    assert not hp.is_helper("")


# --- the row --------------------------------------------------------------


def test_quiet_machine_says_so(capsys) -> None:
    assert hp._doctor_check_helper_processes(ps_stdout=_ps(_SESSION, _SPARE)) is True
    assert "no mnemo helper processes" in capsys.readouterr().out


def test_an_ordinary_sweep_is_informational(capsys) -> None:
    """One sweep and a couple of helpers is mnemo working, not mnemo looping."""
    ok = hp._doctor_check_helper_processes(ps_stdout=_ps(_SWEEP, _HELPER, _HELPER))

    out = capsys.readouterr().out
    assert ok is True
    assert "1 unblock sweep(s)" in out and "2 claude --print" in out
    assert "⚠" not in out


def test_the_storm_shape_is_a_warning(capsys) -> None:
    """42 sweeps behind 7 sessions was the real reading. Two is already more
    than the lock allows, so the threshold is one."""
    ok = hp._doctor_check_helper_processes(ps_stdout=_ps(*([_SWEEP] * 6), *([_HELPER] * 9)))

    out = capsys.readouterr().out
    assert ok is False
    assert "6 unblock sweep(s)" in out and "9 claude --print" in out
    assert "#329" in out


def test_a_guard_left_set_in_the_shell_is_reported(monkeypatch, capsys) -> None:
    """mnemo sets the variable on its helpers only. Exported in a user's
    shell it silences every hook, and nothing else would ever say why."""
    monkeypatch.setenv(hook_guard.HOOKS_OFF_ENV, "1")

    ok = hp._doctor_check_helper_processes(ps_stdout=_ps())

    out = capsys.readouterr().out
    assert ok is False
    assert hook_guard.HOOKS_OFF_ENV in out
    assert "unset it" in out


def test_no_ps_is_not_a_failure(monkeypatch, capsys) -> None:
    """Windows has no ``ps``: the row goes quiet rather than guessing, and
    never turns doctor into a warning for a measurement it cannot take."""
    monkeypatch.setattr("mnemo.core.sessions.residents.read_ps", lambda: None)

    assert hp._doctor_check_helper_processes() is True
    assert capsys.readouterr().out == ""


def test_the_row_is_registered_in_doctor() -> None:
    """A check nobody calls is the writer-with-no-reader this repo has
    shipped twice."""
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert ("helper_processes", hp._doctor_check_helper_processes) in DOCTOR_CHECKS
