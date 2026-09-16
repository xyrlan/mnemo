"""mnemo's own ``claude`` helpers must not fire mnemo's hooks (#329).

``core.llm`` runs ``claude --print`` under the user's full settings, which is
how a helper reaches the subscription the user already authorised — and those
settings carry mnemo's SessionStart and SessionEnd. So every briefing, every
extraction and every ``learn`` was a Claude Code session that scheduled another
briefing, extraction and sweep. Measured on the maintainer's machine: 42
sweeps, 42 session_start processes and 34 ``claude --print`` behind 7 real
sessions, load average 118.

Two facts these tests stand on were verified against the real CLI (2.1.273)
rather than assumed, since both are invisible from inside the code:

- a ``claude --print`` run *does* fire SessionStart and SessionEnd — probe
  hooks appending to a file produced one ``start`` and one ``end`` line;
- the variable reaches the hook two processes down —
  ``MNEMO_HOOKS_OFF=1 claude --print …`` with the same probe printed
  ``MNEMO_HOOKS_OFF=[1]`` from inside the hook.

What is left for the suite is the half that lives in this repo: llm sets it,
and every hook entry point obeys it.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.core import hook_guard

HOOK_MODULES = ("session_start", "session_end", "pre_tool_use", "user_prompt_submit")


# --- the variable ---------------------------------------------------------


def test_llm_sets_the_guard_on_every_helper(monkeypatch) -> None:
    """The one place mnemo launches a `claude` session for itself."""
    from mnemo.core import llm

    seen: dict = {}

    def _fake_run(argv, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here — the env is the whole assertion")

    monkeypatch.setattr(llm, "_subprocess_run", _fake_run)
    with pytest.raises(RuntimeError):
        llm.call("p", system=None)

    assert seen["env"][hook_guard.HOOKS_OFF_ENV] == "1"
    # The rest of the environment still reaches the helper: the flag is a
    # guard, not a sandbox, and auth lives in the variables around it.
    assert seen["env"]["CLAUDE_CODE_DISABLE_THINKING"] == "1"


def test_dispatched_children_are_not_guarded(monkeypatch) -> None:
    """A `claude --bg` child is a real session and must keep its hooks.

    The guard is set on the helper's env, never on mnemo's own process, so
    nothing mnemo spawns afterwards inherits it.
    """
    from mnemo.core import llm

    monkeypatch.delenv(hook_guard.HOOKS_OFF_ENV, raising=False)
    llm._build_env()

    assert not hook_guard.hooks_off()


@pytest.mark.parametrize("value,off", [
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("", False), ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_guard_reads_falsey_values_as_unset(monkeypatch, value: str, off: bool) -> None:
    """An empty or explicitly falsey value is not a guard — a shell that
    exports ``MNEMO_HOOKS_OFF=`` must not silence mnemo by accident."""
    monkeypatch.setenv(hook_guard.HOOKS_OFF_ENV, value)
    assert hook_guard.hooks_off() is off


# --- the hooks ------------------------------------------------------------


def _payload() -> str:
    return json.dumps({
        "session_id": "sid-1", "cwd": "/repo", "reason": "exit",
        "prompt": "hello", "tool_name": "Bash", "tool_input": {"command": "ls"},
    })


@pytest.mark.parametrize("module_name", HOOK_MODULES)
def test_guarded_hook_does_nothing_at_all(
    monkeypatch, capsys, module_name: str
) -> None:
    """Exit 0, no config read, no file written, no process spawned.

    ``config.load_config`` is the first thing every hook touches and the gate
    to everything after it, so failing there proves the return happens before
    any work — including before the stdin payload is parsed.
    """
    import importlib

    module = importlib.import_module(f"mnemo.hooks.{module_name}")

    def _forbidden(*args, **kwargs):
        pytest.fail(f"{module_name} did work under {hook_guard.HOOKS_OFF_ENV}")

    monkeypatch.setenv(hook_guard.HOOKS_OFF_ENV, "1")
    monkeypatch.setattr("mnemo.core.config.load_config", _forbidden)
    monkeypatch.setattr("mnemo.core.errors.should_run", _forbidden)
    monkeypatch.setattr("subprocess.Popen", _forbidden)
    monkeypatch.setattr("mnemo.core.llm.call", _forbidden)
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload()))

    assert module.main() == 0
    # SessionStart is the one hook that speaks: silence here is also the
    # promise that a helper's stdout carries no injection envelope.
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("module_name", HOOK_MODULES)
def test_unguarded_hook_still_runs(monkeypatch, module_name: str) -> None:
    """The guard is the only thing being added: with it unset, every hook
    still reaches the config load it always did."""
    import importlib

    module = importlib.import_module(f"mnemo.hooks.{module_name}")
    loaded: list[bool] = []

    def _load_config(*args, **kwargs):
        loaded.append(True)
        raise RuntimeError("far enough — the hook swallows this")

    monkeypatch.delenv(hook_guard.HOOKS_OFF_ENV, raising=False)
    monkeypatch.setattr("mnemo.core.config.load_config", _load_config)
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload()))

    assert module.main() == 0
    assert loaded, f"{module_name} never reached load_config with the guard unset"


def test_every_hook_entry_point_is_guarded() -> None:
    """Asserting over the package rather than a hand-written list: the next
    hook module added to ``mnemo/hooks/`` fails here instead of quietly
    reopening the loop.
    """
    import ast

    hooks_dir = Path(__file__).resolve().parents[2] / "src" / "mnemo" / "hooks"
    unguarded = []
    for path in sorted(hooks_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        main = next(
            (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"),
            None,
        )
        if main is None:
            continue
        head = ast.dump(ast.Module(body=main.body[:3], type_ignores=[]))
        if "hooks_off" not in head:
            unguarded.append(path.name)

    assert not unguarded, (
        f"hook entry point(s) with no recursion guard: {unguarded}. "
        "Call hook_guard.hooks_off() before anything else in main()."
    )
