"""A session started in a throwaway directory leaves the real vault alone (#420).

Every ``claude`` process runs the global hooks, wherever it starts: a
``live_claude`` test's ``tmp_path``, a background job's probe in
``$CLAUDE_JOB_DIR/tmp``, a scratchpad under ``/tmp/claude-<uid>/``. Each one
filed an agent directory, a log and a briefing in the user's vault — about 90
of 115 ``bots/`` entries on the maintainer's machine.

The rule is on two paths: the cwd is throwaway *and the vault is not*. A vault
that itself lives in the temp dir was put there on purpose — this suite, and
the recorded demo's project vault in ``/tmp/mnemo-demo``.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

from mnemo.core import hook_guard

HOOK_MODULES = ("session_start", "session_end", "pre_tool_use", "user_prompt_submit")


# --- the predicate --------------------------------------------------------


@pytest.mark.parametrize("path", [
    "/sys-tmp",
    "/sys-tmp/probe86",
    "/sys-tmp/claude-501/-Users-x-github-mnemo/abc/scratchpad/rotest2",
    "/elsewhere/pytest-of-x/pytest-881/test_bg_spawn0/live",
    "/Users/x/.claude/jobs/1f87be87/tmp/probe",
])
def test_throwaway_paths(path: str) -> None:
    assert hook_guard.is_throwaway(path, roots=["/sys-tmp"])


@pytest.mark.parametrize("path", [
    "",
    "/Users/x/github/mnemo",
    "/Users/x/github/mnemo/.claude/worktrees/fix-1",
    "/Users/x/github/mnemo-wt-420",
    "/sys-tmpfiles/app",          # a prefix of the name is not the dir
    "/Users/x/code/pytest-ofx/a",
    "/Users/x/.claude/jobs/1f87be87",
])
def test_real_work_is_not_throwaway(path: str) -> None:
    assert not hook_guard.is_throwaway(path, roots=["/sys-tmp"])


def test_the_platform_temp_dir_is_a_root(tmp_path: Path, monkeypatch) -> None:
    """With no explicit roots, ``tempfile.gettempdir()`` is one — compared
    after symlinks resolve, since macOS reports ``/private/var/...`` for a
    cwd and ``/var/...`` for ``gettempdir()``."""
    monkeypatch.setattr(hook_guard, "_BASETEMP_RE", re.compile(r"(?!)"))
    monkeypatch.setattr(hook_guard.tempfile, "gettempdir", lambda: str(tmp_path))
    assert hook_guard.is_throwaway(str(tmp_path.resolve() / "gone" / "probe"))


def test_a_vault_in_the_temp_dir_is_left_to_its_hooks() -> None:
    """This suite and the demo both keep the vault under the temp dir."""
    assert hook_guard.throwaway_session("/tmp/x/app", "/tmp/x/app/.mnemo") is False


# --- the hooks ------------------------------------------------------------


@pytest.fixture
def real_vault(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """A vault that reads as durable and a cwd that reads as temp.

    Both live in ``tmp_path``, so the markers the real machine uses — the
    temp dir and ``pytest-of-*`` — are narrowed to one directory here.
    """
    systmp = tmp_path / "systmp"
    cwd = systmp / "pytest-live" / "probe"
    cwd.mkdir(parents=True)
    vault = tmp_path / "home" / "mnemo"
    vault.mkdir(parents=True)
    monkeypatch.setattr(hook_guard, "_temp_roots", lambda: [str(systmp)])
    monkeypatch.setattr(hook_guard, "_BASETEMP_RE", re.compile(r"(?!)"))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: {
        "vaultRoot": str(vault),
        "reflex": {"enabled": True},
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": True},
    })
    monkeypatch.delenv(hook_guard.HOOKS_OFF_ENV, raising=False)
    return vault, cwd


def _payload(cwd: Path) -> str:
    return json.dumps({
        "session_id": "sid-420", "cwd": str(cwd), "reason": "exit",
        "prompt": "hello", "tool_name": "Bash", "tool_input": {"command": "ls"},
    })


@pytest.mark.parametrize("module_name", HOOK_MODULES)
def test_a_throwaway_session_writes_nothing_into_the_vault(
    real_vault, monkeypatch, capsys, module_name: str
) -> None:
    """No agent directory, no log, no briefing, no injection, no process."""
    import importlib

    vault, cwd = real_vault
    module = importlib.import_module(f"mnemo.hooks.{module_name}")

    def _forbidden(*args, **kwargs):
        pytest.fail(f"{module_name} did work for a session in {cwd}")

    monkeypatch.setattr("mnemo.core.errors.should_run", _forbidden)
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", _forbidden)
    monkeypatch.setattr("mnemo.core.session.save", _forbidden)
    monkeypatch.setattr("subprocess.Popen", _forbidden)
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload(cwd)))

    assert module.main() == 0
    assert capsys.readouterr().out == ""
    assert list(vault.iterdir()) == []


@pytest.mark.parametrize("module_name", HOOK_MODULES)
def test_a_real_session_still_runs(real_vault, monkeypatch, module_name: str) -> None:
    """The same vault, a cwd outside the temp dir: the hook goes on to the
    breaker check it always reached."""
    import importlib

    vault, _cwd = real_vault
    module = importlib.import_module(f"mnemo.hooks.{module_name}")
    reached: list[bool] = []

    def _should_run(*args, **kwargs):
        reached.append(True)
        return False

    monkeypatch.setattr("mnemo.core.errors.should_run", _should_run)
    monkeypatch.setattr("sys.stdin", io.StringIO(_payload(vault.parent / "github" / "app")))

    assert module.main() == 0
    assert reached, f"{module_name} stopped before the breaker for a real cwd"
