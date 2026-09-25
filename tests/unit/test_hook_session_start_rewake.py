"""SessionStart starts the watcher, and never wakes anything itself (#396).

Two guarantees, and the second is the one that keeps this off the path that
decides how fast a session starts:

1. the hook reaches :func:`mnemo.core.sessions.rewake.on_session_start` at all
   — the whole feature is a trigger, and a trigger nothing calls is #195's
   recorded-and-discarded unblock marker again;
2. the hook itself wakes nothing. Waking is up to five ``claude --bg
   --resume`` subprocesses, and the hook has to return before the session's
   first prompt. The watcher's first tick does the pass, a second later, in a
   process nobody is waiting on.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.core.sessions import rewake
from mnemo.hooks import session_start


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    (root / "HOME.md").write_text("# home\n", encoding="utf-8")
    return root


def _run_hook(monkeypatch, vault: Path, cwd: Path, cfg: dict) -> None:
    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda *a, **k: vault)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "a" * 36, "cwd": str(cwd),
                                "source": "startup"})),
    )
    session_start.main()


def test_the_hook_asks_rewake_to_make_sure_a_watcher_exists(monkeypatch, vault, tmp_path):
    seen: list = []
    monkeypatch.setattr(rewake, "on_session_start",
                        lambda cfg, **kw: seen.append(kw) or "nothing")
    _run_hook(monkeypatch, vault, tmp_path,
              {"vaultRoot": str(vault), "resume": {"auto": True}})
    assert seen and seen[0]["vault_root"] == vault


def test_the_hook_wakes_nothing_itself(monkeypatch, vault, tmp_path):
    """No ``claude --bg --resume`` on the session-start path, whatever is stalled."""
    woken: list = []
    monkeypatch.setattr(rewake.wake_mod, "wake",
                        lambda *a, **k: woken.append(a) or None)
    spawned: list = []
    monkeypatch.setattr(rewake, "_spawn_watcher", lambda: spawned.append(1))

    tree = tmp_path / "mnemo-wt-380"
    tree.mkdir()
    from mnemo.core.sessions.jobs import Session
    from mnemo.core.sessions.stalls import Kind, Stall

    stalled = Session(short_id="594436f2", state="blocked", tempo="blocked",
                      live=False, cwd=str(tree),
                      session_id="594436f2-a282-4a1c-b04a-08441296047e")
    free = Stall(kind=Kind.RATE_LIMIT, error="rate_limit",
                 resets_at=1, limit="five_hour")
    monkeypatch.setattr(rewake, "_read_sessions", lambda: [stalled])
    monkeypatch.setattr(rewake.stalls_mod, "stalls_for",
                        lambda found, **k: {"594436f2": free})

    _run_hook(monkeypatch, vault, tmp_path,
              {"vaultRoot": str(vault), "resume": {"auto": True}})

    assert woken == []      # the hook spends no tokens
    assert spawned == [1]   # it starts the thing that will


def test_off_in_config_starts_nothing(monkeypatch, vault, tmp_path):
    spawned: list = []
    monkeypatch.setattr(rewake, "_spawn_watcher", lambda: spawned.append(1))
    monkeypatch.setattr(rewake, "_read_sessions", lambda: [])
    _run_hook(monkeypatch, vault, tmp_path,
              {"vaultRoot": str(vault), "resume": {"auto": False}})
    assert spawned == []


def test_a_broken_rewake_never_costs_the_session_its_start(monkeypatch, vault, tmp_path):
    """Fail-silent, like every other block on this path."""
    def _boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(rewake, "on_session_start", _boom)
    _run_hook(monkeypatch, vault, tmp_path,
              {"vaultRoot": str(vault), "resume": {"auto": True}})
    assert (vault / ".errors.log").exists()


def test_the_hook_asks_child_notices_to_make_sure_a_watcher_exists(monkeypatch, vault, tmp_path):
    """#502: a dispatched child's stop is noticed outside its own SessionEnd."""
    from mnemo.core.sessions import child_notices

    seen: list = []
    monkeypatch.setattr(child_notices, "on_session_start",
                        lambda cfg, **kw: seen.append(kw) or "nothing")
    _run_hook(monkeypatch, vault, tmp_path,
              {"vaultRoot": str(vault), "dispatch": {"notifyParent": True}})
    assert seen and seen[0]["vault_root"] == vault
