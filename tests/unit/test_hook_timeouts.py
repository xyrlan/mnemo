"""#611: every path that writes mnemo's hooks declares the shipped timeout.

#593 gave the plugin's two ``hooks.json`` files a ``timeout`` per event, set
from measured durations. ``mnemo init`` and the lean child profile wrote none,
so Claude Code cut their ``SessionEnd`` at its 1.5 s default while PR #604
timed the hook at p50 3.7 s / p99 5.3 s under load. These tests pin the three
writers to one table, ``HOOK_DEFINITIONS``, and pin the upgrade path: an
install written before the timeouts existed is repaired once, at session
start, through the same narrow write the matcher repair uses (#303, #337).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import child_profile
from mnemo.install import hook_drift
from mnemo.install.settings import HOOK_DEFINITIONS, inject_hooks

REPO = Path(__file__).resolve().parents[2]
OTHER = '"/Users/x/Library/Application Support/GitKrakenCLI/gk" ai hook run'


def _plugin_timeouts(path: Path) -> dict:
    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    return {event: [h.get("timeout") for e in entries for h in e["hooks"]]
            for event, entries in hooks.items()}


def _installed_timeouts(path: Path) -> dict:
    """``{event: [timeout of each mnemo hook]}`` as written into settings.json."""
    from mnemo.install.settings import is_mnemo_hook_command

    hooks = json.loads(path.read_text(encoding="utf-8"))["hooks"]
    return {event: [h.get("timeout") for e in entries for h in e["hooks"]
                    if is_mnemo_hook_command(h.get("command", ""))]
            for event, entries in hooks.items()}


def _shipped() -> dict:
    return {event: defn.get("timeout") for event, defn in HOOK_DEFINITIONS.items()}


# --- one table, three writers ----------------------------------------------


@pytest.mark.parametrize("rel", ["hooks/hooks.json", "plugin/hooks/hooks.json"])
def test_init_and_both_plugins_ship_the_same_timeouts(rel):
    plugin = _plugin_timeouts(REPO / rel)
    assert {event: [t] for event, t in _shipped().items()} == plugin


def test_init_writes_the_shipped_timeout_on_every_hook(tmp_path: Path):
    path = tmp_path / ".claude" / "settings.json"
    inject_hooks(path)
    assert _installed_timeouts(path) == {event: [t] for event, t in _shipped().items()}


def test_session_end_gets_more_than_claude_codes_default():
    """The point of #611: 1.5 s is Claude Code's bound when none is declared."""
    assert HOOK_DEFINITIONS["SessionEnd"]["timeout"] > 1.5


# --- drift: an install written before the timeouts existed -----------------


def _pre_611(dir_: Path, *, timeout: object = ..., event: str = "SessionEnd") -> Path:
    """A settings.json ``init`` wrote before #611: current matcher, no timeout.

    Mixed into the same event as another tool's hook, with keys outside
    ``hooks`` that a repair must leave as they are.
    """
    dir_.mkdir(parents=True, exist_ok=True)
    data = {"statusLine": {"command": "mine"}, "model": "opus", "hooks": {}}
    for name, defn in HOOK_DEFINITIONS.items():
        hook: dict = {"type": "command",
                      "command": f"/usr/local/bin/python3 -m mnemo.hooks.{defn['module']}"}
        if name == event:
            if timeout is not ...:
                hook["timeout"] = timeout
        else:
            hook["timeout"] = defn["timeout"]
        entry: dict = {"hooks": [hook]}
        if defn.get("matcher"):
            entry["matcher"] = defn["matcher"]
        data["hooks"][name] = [entry]
    data["hooks"].setdefault(event, []).append(
        {"hooks": [{"type": "command", "command": OTHER, "timeout": 7}]})
    path = dir_ / "settings.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    (v / ".mnemo").mkdir(parents=True)
    return v


def test_a_missing_timeout_is_drift(tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    _pre_611(claude)

    (drift,) = hook_drift.scan(claude, tmp_path / "nowhere")
    assert drift.missing == {}
    assert drift.timeouts == {"SessionEnd": (None, HOOK_DEFINITIONS["SessionEnd"]["timeout"])}


def test_a_different_timeout_is_drift(tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    _pre_611(claude, timeout=5)

    (drift,) = hook_drift.scan(claude, tmp_path / "nowhere")
    assert drift.timeouts == {"SessionEnd": (5, HOOK_DEFINITIONS["SessionEnd"]["timeout"])}


def test_the_shipped_timeout_is_not_drift(tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    _pre_611(claude, timeout=HOOK_DEFINITIONS["SessionEnd"]["timeout"])
    assert hook_drift.scan(claude, tmp_path / "nowhere") == []


def test_another_tools_timeout_is_not_judged(tmp_path: Path):
    """The ``OTHER`` hook carries ``timeout: 7`` on the same event; not ours."""
    claude = tmp_path / "home" / ".claude"
    _pre_611(claude, timeout=HOOK_DEFINITIONS["SessionEnd"]["timeout"])
    assert hook_drift.scan(claude, tmp_path / "nowhere") == []


def test_repair_writes_the_timeout_and_nothing_else(vault: Path, tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    path = _pre_611(claude)

    notices = hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")

    assert _installed_timeouts(path) == {event: [t] for event, t in _shipped().items()}
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["statusLine"] == {"command": "mine"} and data["model"] == "opus"
    others = [h for e in data["hooks"]["SessionEnd"] for h in e["hooks"]
              if h["command"] == OTHER]
    assert others == [{"type": "command", "command": OTHER, "timeout": 7}]
    assert hook_drift.scan(claude, tmp_path / "nowhere") == []
    # What an existing install sees on upgrade: one line, naming the bound.
    assert len(notices) == 1
    assert "SessionEnd" in notices[0] and "timeout" in notices[0]
    assert "autoRepairHooks" in notices[0]


def test_the_timeout_repair_is_said_once(vault: Path, tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    _pre_611(claude)
    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere")

    # A user who sets their own bound afterwards keeps it.
    path = _pre_611(claude, timeout=5)
    assert hook_drift.auto_repair(vault, claude_dir=claude, cwd=tmp_path / "nowhere") == []
    assert _installed_timeouts(path)["SessionEnd"] == [5]


def test_a_matcher_only_signature_is_unchanged():
    """Markers written before #611 must still recognise a repaired matcher."""
    drift = hook_drift.Drift(path=Path("/s.json"), missing={"PreToolUse": ["Read"]},
                             project=False)
    assert drift.signature == "/s.json|PreToolUse:Read"


def test_doctor_and_status_name_the_timeout(tmp_path: Path):
    claude = tmp_path / "home" / ".claude"
    _pre_611(claude)
    (drift,) = hook_drift.scan(claude, tmp_path / "nowhere")

    shipped = HOOK_DEFINITIONS["SessionEnd"]["timeout"]
    (doctor_line,) = drift.doctor_lines()
    assert "SessionEnd" in doctor_line and f"{shipped}" in doctor_line
    (status_line,) = drift.status_lines()
    assert status_line.startswith("Hooks (global):") and "`mnemo init --hooks-only`" in status_line


# --- the lean child profile ------------------------------------------------


def test_a_child_gets_the_shipped_timeout_even_from_a_pre_611_install(tmp_path: Path):
    """A dispatched child copies the user's hooks; an install not yet repaired
    (or with ``autoRepairHooks: false``) has none to copy."""
    path = _pre_611(tmp_path / ".claude")
    settings = json.loads(path.read_text(encoding="utf-8"))

    hooks = child_profile.mnemo_hooks(settings)

    (entry,) = hooks["SessionEnd"]
    assert [h["timeout"] for h in entry["hooks"]] == [HOOK_DEFINITIONS["SessionEnd"]["timeout"]]


def test_a_childs_timeout_set_by_the_user_is_kept(tmp_path: Path):
    path = _pre_611(tmp_path / ".claude", timeout=120)
    settings = json.loads(path.read_text(encoding="utf-8"))

    (entry,) = child_profile.mnemo_hooks(settings)["SessionEnd"]
    assert [h["timeout"] for h in entry["hooks"]] == [120]


def test_the_child_profile_file_carries_the_timeout(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    _pre_611(home / ".claude")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    written = child_profile.write_profile(tmp_path / "tree")

    data = json.loads(written["settings"].read_text(encoding="utf-8"))
    for event, shipped in _shipped().items():
        assert [h["timeout"] for e in data["hooks"][event] for h in e["hooks"]] == [shipped]
