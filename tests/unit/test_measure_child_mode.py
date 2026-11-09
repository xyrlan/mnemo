"""``tools/measure_child_mode.py`` over synthetic transcripts (#741)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_child_mode.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("measure_child_mode", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _user(text: str, mode: str = "bypassPermissions") -> str:
    return json.dumps({"type": "user", "permissionMode": mode,
                       "message": {"role": "user", "content": text}})


def _assistant(*blocks: Dict[str, Any]) -> str:
    return json.dumps({"type": "assistant", "message": {"content": list(blocks)}})


def test_summarize_reads_modes_tools_and_turns(tool) -> None:
    lines = [_user("make probe.txt"),
             _assistant({"type": "tool_use", "name": "ToolSearch", "input": {}}),
             _assistant({"type": "text", "text": "Write was not available"}),
             "not json", json.dumps({"type": "api-request-blob", "permissionMode": "auto"})]
    seen = tool.summarize(lines)
    assert seen["modes"] == ["bypassPermissions"]
    assert seen["tools"] == ["ToolSearch"]
    assert seen["assistant_turns"] == 2
    assert seen["users"] == ["make probe.txt"]


def test_summarize_after_skips_what_came_before_the_wake(tool) -> None:
    lines = [_user("first", "auto"), _assistant({"type": "text", "text": "x"}),
             _user("WAKEPROBE: one word"), _assistant({"type": "text", "text": "ok"})]
    seen = tool.summarize(lines, after="WAKEPROBE")
    assert seen["modes"] == ["bypassPermissions"]
    assert seen["assistant_turns"] == 1


def test_summarize_counts_a_held_peer_message_and_not_as_a_turn(tool) -> None:
    held = json.dumps({"type": "system", "subtype": "informational",
                       "content": "Held peer message — preview: «PLAINPROBE notice»"})
    seen = tool.summarize([held])
    assert seen["held"] and "PLAINPROBE" in seen["held"][0]
    assert seen["users"] == []


def _passing() -> Dict[str, Any]:
    return {
        "read_only": {"modes": ["bypassPermissions"], "tools": ["ToolSearch"],
                      "file_written": False, "assistant_turns": 1},
        "wake": {"modes": ["bypassPermissions"]},
        "disabled": {"modes": ["auto"]},
        "delivery": {"users": ["... ATTESTEDPROBE notice"],
                     "held": ["Held peer message — «PLAINPROBE notice»"]},
    }


def test_a_full_pass(tool) -> None:
    assert tool.verdict(_passing()) == []


def test_a_read_only_child_that_wrote_fails(tool) -> None:
    report = _passing()
    report["read_only"].update(tools=["Write"], file_written=True)
    assert tool.verdict(report) == ["read_only: called Write",
                                    "read_only: the file was written"]


def test_a_wake_that_lost_its_mode_fails(tool) -> None:
    report = _passing()
    report["wake"] = {"modes": ["auto"]}
    assert tool.verdict(report) == [
        "wake: the woken turns ran ['auto'], expected bypassPermissions"]


def test_bypass_running_despite_the_setting_fails_and_a_refusal_passes(tool) -> None:
    report = _passing()
    report["disabled"] = {"modes": ["bypassPermissions"]}
    assert tool.verdict(report) == ["disabled: the session ran bypassPermissions anyway"]
    report["disabled"] = {"refused": "bypass is disabled"}
    assert tool.verdict(report) == []


def test_delivery_needs_the_hold_and_the_accept(tool) -> None:
    report = _passing()
    report["delivery"] = {"users": ["PLAINPROBE notice", "ATTESTEDPROBE notice"]}
    assert tool.verdict(report) == [
        "delivery: the plain notice became a turn, so nothing was held and the "
        "attestation was not what decided"]
    report["delivery"] = {"users": [], "held": []}
    assert tool.verdict(report) == [
        "delivery: the attested notice did not become a turn",
        "delivery: the plain notice was neither a turn nor held"]


# --- what the tool launches (#827) --------------------------------------------

CASES = ("read_only", "disabled", "delivery")
REFUSAL = ("--bg with bypassPermissions requires accepting the disclaimer first. "
           "Acceptance is kept in your user settings, which this launch does not read. "
           "Launch again with user settings on.")


class _Spawn:
    def __init__(self) -> None:
        self.args: list = []

    def __call__(self, args, **kwargs):
        self.args = list(args)
        return type("R", (), {"returncode": 0, "stdout": "abc12345\n", "stderr": ""})()


@pytest.mark.real_spawn
@pytest.mark.parametrize("case", CASES)
def test_the_tool_launches_what_dispatch_spawns(tool, monkeypatch, tmp_path, case) -> None:
    from mnemo.core import dispatch

    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)
    dispatch.spawn_child(tool.PROMPTS[case], cwd=tmp_path, **tool.spawn_kwargs(case, "haiku"))

    assert tool.argv(case, tmp_path, "haiku") == spawn.args


@pytest.mark.parametrize("case", CASES)
def test_every_probe_starts_in_bypass_with_user_settings(tool, tmp_path, case) -> None:
    from mnemo.core import child_profile

    args = tool.argv(case, tmp_path, "haiku")
    at = args.index("--permission-mode")
    assert args[at + 1] == "bypassPermissions"
    at = args.index("--setting-sources")
    assert tuple(args[at:at + 3]) == child_profile.BYPASS_LEAN_FLAGS
    assert args[-1] == tool.PROMPTS[case]


def test_the_write_tools_are_the_ones_a_read_only_dispatch_closes(tool) -> None:
    from mnemo.core import dispatch

    assert tool.WRITE_TOOLS == dispatch.READ_ONLY_TOOLS


@pytest.mark.parametrize("windows", [True, False])
def test_mnemo_hooks_are_off_except_where_delivery_needs_them(tool, monkeypatch,
                                                               windows) -> None:
    from mnemo.core import hook_guard
    from mnemo.core.sessions import inbox

    monkeypatch.setattr(inbox, "registry_reachable", lambda: windows)
    off = {hook_guard.HOOKS_OFF_ENV: "1"}
    for case in ("read_only", "disabled"):
        assert tool.spawn_kwargs(case, "haiku")["settings"]["env"] == off
    delivery = tool.spawn_kwargs("delivery", "haiku")["settings"]
    assert (delivery or {}).get("env") == (off if windows else None)
    assert tool.spawn_kwargs("disabled", "haiku")["settings"]["permissions"] == {
        "disableBypassPermissionsMode": "disable"}


def test_the_launch_is_shown_without_the_prompt_or_its_paths(tool, tmp_path) -> None:
    settings = tmp_path / ".mnemo-child-profile" / "settings.json"
    args = ["claude", "--bg", "--settings", str(settings), "--", "the prompt"]
    assert tool.shown(args) == "claude --bg --settings settings.json --"


def test_no_recorded_acceptance_starts_nothing(tool, monkeypatch, tmp_path) -> None:
    from mnemo.core import child_mode
    from mnemo.core.sessions import inbound_policy

    def boom(*args, **kwargs):
        raise AssertionError("nothing may be started")

    monkeypatch.setattr(inbound_policy, "managed_settings_paths", lambda: [])
    monkeypatch.setattr(tool.subprocess, "run", boom)
    report = tool.measure(tmp_path, "haiku", keep=False)

    assert report["accepted_in"] is None
    assert child_mode.CONSENT_KEY in report["skipped"]
    assert list(tmp_path.glob(".mnemo-741-*")) == []
    assert tool.verdict(report) == [f"not run: {report['skipped']}"]


def test_the_refusals_the_old_flags_met_all_fail(tool) -> None:
    """The report the pre-#827 tool printed on 2.1.321: every bypass launch
    refused. Its ``disabled`` case used to pass on that refusal."""
    report = {case: {"refused": REFUSAL} for case in CASES}
    assert tool.verdict(report) == [
        f"read_only: refused: {REFUSAL[:200]}",
        "wake: the woken turns ran unknown, expected bypassPermissions",
        f"disabled: refused for the disclaimer, not by the setting: {REFUSAL[:200]}",
        f"delivery: refused: {REFUSAL[:200]}",
    ]


def test_a_cross_session_setting_makes_delivery_unmeasurable(tool) -> None:
    report = _passing()
    report["delivery"]["setting"] = "accept"
    assert tool.verdict(report) == [
        "delivery: not measurable here: crossSessionInbound is 'accept', which "
        "decides before any attestation"]


def test_a_notice_the_inbox_did_not_write_says_why(tool) -> None:
    report = _passing()
    report["delivery"]["sent"] = {"plain": "", "attested": "no address recorded"}
    assert tool.verdict(report) == [
        "delivery: a notice was not written: attested: no address recorded"]
