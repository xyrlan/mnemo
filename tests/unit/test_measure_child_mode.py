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
