"""A dispatched child runs in its parent's permission class (#741)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands import dispatch as cli_dispatch
from mnemo.core import child_mode, dispatch
from mnemo.core.config import DEFAULTS
from mnemo.core.sessions import inbound_policy

PARENT = "11111111-2222-3333-4444-555555555555"


def _home(tmp_path: Path, mode: str = "bypassPermissions") -> Path:
    """A config dir holding the parent's transcript, last turn in *mode*."""
    home = tmp_path / "claude-home"
    transcript = home / "projects" / "C--repo" / f"{PARENT}.jsonl"
    transcript.parent.mkdir(parents=True)
    lines = [{"type": "user", "cwd": str(tmp_path), "permissionMode": "auto"},
             {"type": "user", "permissionMode": mode}]
    transcript.write_text("".join(json.dumps(r) + "\n" for r in lines), encoding="utf-8")
    return home


def _choose(tmp_path: Path, cfg=None, *, env=None, mode="bypassPermissions", managed=()):
    home = _home(tmp_path, mode)
    tree = tmp_path / "tree"
    tree.mkdir(exist_ok=True)
    env = {"CLAUDE_CODE_SESSION_ID": PARENT} if env is None else env
    return child_mode.choose(cfg, cwd=tree, env=env, homes=[home], managed=managed)


# -- the mapping ----------------------------------------------------------------


def test_a_bypass_parent_starts_a_bypass_child(tmp_path) -> None:
    got = _choose(tmp_path)
    assert got.mode == "bypassPermissions"
    assert "dispatch.childMode: auto" in got.note


@pytest.mark.parametrize("mode", sorted(inbound_policy.PROMPTING_MODES))
def test_a_prompting_parent_leaves_the_child_in_auto(tmp_path, mode) -> None:
    """Never the exact mode: an unattended ``default`` child stops at its first prompt."""
    assert _choose(tmp_path, mode=mode) == child_mode.Choice()


@pytest.mark.parametrize("mode", ["plan", "manual", "somethingNew"])
def test_a_mode_whose_class_is_not_known_leaves_auto(tmp_path, mode) -> None:
    assert _choose(tmp_path, mode=mode) == child_mode.Choice()


def test_a_plain_terminal_leaves_auto(tmp_path) -> None:
    assert _choose(tmp_path, env={}) == child_mode.Choice()


def test_a_parent_with_no_transcript_leaves_auto(tmp_path) -> None:
    got = child_mode.choose(None, cwd=tmp_path, env={"CLAUDE_CODE_SESSION_ID": PARENT},
                            homes=[tmp_path / "nowhere"], managed=())
    assert got == child_mode.Choice()


def test_choose_never_raises(tmp_path, monkeypatch) -> None:
    def boom(*a, **k):
        raise RuntimeError("unreadable")

    monkeypatch.setattr(inbound_policy, "find_transcript", boom)
    assert _choose(tmp_path) == child_mode.Choice()


# -- the escape -----------------------------------------------------------------


def test_the_default_is_match_parent() -> None:
    assert DEFAULTS["dispatch"]["childMode"] == child_mode.MATCH_PARENT


@pytest.mark.parametrize("value", ["auto", "AUTO", " auto "])
def test_child_mode_auto_always_starts_auto(tmp_path, value) -> None:
    assert _choose(tmp_path, {"dispatch": {"childMode": value}}) == child_mode.Choice()


@pytest.mark.parametrize("cfg", [None, {}, {"dispatch": None}, {"dispatch": {"childMode": 3}},
                                 {"dispatch": {"childMode": "bypassPermissions"}}])
def test_an_unknown_value_keeps_the_default(tmp_path, cfg) -> None:
    assert _choose(tmp_path, cfg).mode == "bypassPermissions"


# -- bypass disabled by policy ----------------------------------------------------


def _disable(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": {"disableBypassPermissionsMode": "disable"}}),
                    encoding="utf-8")
    return path


@pytest.mark.parametrize("where", ["managed", "user", "project", "local"])
def test_bypass_disabled_by_a_setting_falls_back_to_auto_and_says_so(tmp_path, where) -> None:
    """Measured: the CLI downgrades to auto silently, so mnemo is the one to say it."""
    home = tmp_path / "claude-home"
    tree = tmp_path / "tree"
    path = {"managed": tmp_path / "managed" / "managed-settings.json",
            "user": home / "settings.json",
            "project": tree / ".claude" / "settings.json",
            "local": tree / ".claude" / "settings.local.json"}[where]
    _disable(path)

    got = _choose(tmp_path, managed=[tmp_path / "managed" / "managed-settings.json"])

    assert got.mode is None
    assert str(path) in got.note
    assert "disableBypassPermissionsMode" in got.note


def test_bypass_left_enabled_is_not_read_as_disabled(tmp_path) -> None:
    path = tmp_path / "tree" / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"permissions": {"disableBypassPermissionsMode": "enable"}}),
                    encoding="utf-8")
    assert _choose(tmp_path).mode == "bypassPermissions"


# -- the argv ---------------------------------------------------------------------


class _Spawn:
    def __init__(self) -> None:
        self.args: list = []

    def __call__(self, args, **kwargs):
        self.args = list(args)
        return type("R", (), {"returncode": 0, "stdout": "abc12345\n", "stderr": ""})()


@pytest.mark.real_spawn
@pytest.mark.parametrize("kwargs", [{}, {"lean": False}, {"read_only": True},
                                    {"read_only": True, "lean": False}],
                         ids=["lean", "full", "read-only", "read-only+full"])
def test_the_mode_reaches_the_cli_and_the_prompt_stays_last(monkeypatch, tmp_path, kwargs) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)

    dispatch.spawn_child("do the thing", cwd=tmp_path, permission_mode="bypassPermissions",
                         **kwargs)

    assert spawn.args[:4] == ["claude", "--bg", "--permission-mode", "bypassPermissions"]
    assert spawn.args[-1] == "do the thing"
    if kwargs.get("read_only"):
        at = spawn.args.index("--disallowedTools")
        assert spawn.args[at + 1:at + 4] == ["Edit", "Write", "NotebookEdit"]


@pytest.mark.real_spawn
def test_no_mode_leaves_the_argv_byte_identical(monkeypatch, tmp_path) -> None:
    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)

    dispatch.spawn_child("x", cwd=tmp_path, lean=False)
    without = spawn.args
    dispatch.spawn_child("x", cwd=tmp_path, lean=False, permission_mode=None)

    assert spawn.args == without
    assert "--permission-mode" not in without


def _fake_spawn_into(monkeypatch, tmp_path, choice):
    seen: dict = {}

    def fake_spawn(prompt, **kwargs):
        seen.update(kwargs)
        return "abc12345"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    monkeypatch.setattr(child_mode, "choose", lambda cfg, *, cwd: choice)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.parents, "record", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.grants, "record", lambda *a, **k: None)
    monkeypatch.setattr(dispatch, "_ensure_notice_watcher", lambda: None)
    result = dispatch._spawn_into(741, tmp_path, "p", repo_root=tmp_path, branch="b")
    return seen, result


def test_dispatch_starts_the_child_in_the_chosen_mode(monkeypatch, tmp_path) -> None:
    choice = child_mode.Choice("bypassPermissions", "bypassPermissions, like this session")
    seen, result = _fake_spawn_into(monkeypatch, tmp_path, choice)

    assert seen["permission_mode"] == "bypassPermissions"
    assert result.permission_mode == "bypassPermissions"
    assert result.mode_note == choice.note


def test_dispatch_in_auto_hands_spawn_child_the_call_it_always_did(monkeypatch, tmp_path) -> None:
    seen, result = _fake_spawn_into(monkeypatch, tmp_path, child_mode.Choice())

    assert "permission_mode" not in seen
    assert result.permission_mode is None


def test_a_grok_child_is_never_given_a_mode(monkeypatch, tmp_path) -> None:
    seen: dict = {}

    def fake_grok(prompt, **kwargs):
        seen.update(kwargs)
        return "sid"

    monkeypatch.setattr(dispatch, "_spawn_grok", fake_grok)
    dispatch.spawn_child("x", cwd=tmp_path, agent="grok", permission_mode="bypassPermissions")
    assert "permission_mode" not in seen


def test_the_report_says_which_mode_the_children_run(capsys, tmp_path) -> None:
    started = dispatch.Dispatched(issue=741, worktree=tmp_path, short_id="abc12345",
                                  permission_mode=None,
                                  mode_note="auto, not bypassPermissions like this session")
    cli_dispatch._report([started])
    assert "  mode:   auto, not bypassPermissions like this session" in capsys.readouterr().out


def test_the_report_is_unchanged_for_an_ordinary_auto_child(capsys, tmp_path) -> None:
    cli_dispatch._report([dispatch.Dispatched(issue=741, worktree=tmp_path, short_id="abc12345")])
    assert "mode:" not in capsys.readouterr().out


# -- re-wake --------------------------------------------------------------------


def test_a_woken_child_is_resumed_without_a_mode_flag(monkeypatch, tmp_path) -> None:
    """Measured on 2.1.306: ``--bg --resume`` restores the saved ``--permission-mode``.

    Passing it again is not needed, so the wake argv stays as it was.
    """
    from mnemo.core.sessions import wake

    calls = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return type("R", (), {"returncode": 0, "stdout": "backgrounded", "stderr": ""})()

    monkeypatch.setattr(wake.subprocess, "run", fake_run)
    assert wake._resume(PARENT, "hi", cwd=str(tmp_path), timeout=5, child=None) is None
    assert calls == [["claude", "--bg", "--resume", PARENT, "hi"]]


# -- no forging -------------------------------------------------------------------


def test_a_bypass_child_is_attested_only_because_it_is_one() -> None:
    """The attestation is still the child's own transcript mode, never the dispatch's."""
    look = inbound_policy.decide(setting=None, parent_mode="bypassPermissions",
                                 child_mode="bypassPermissions")
    assert look.accepted is True and look.attest == inbound_policy.BYPASS
    downgraded = inbound_policy.decide(setting=None, parent_mode="bypassPermissions",
                                       child_mode="auto")
    assert downgraded.accepted is False and downgraded.attest is None
