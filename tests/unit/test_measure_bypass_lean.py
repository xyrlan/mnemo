"""``tools/measure_bypass_lean.py`` over synthetic transcripts (#822)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Dict

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_bypass_lean.py"

REFUSED = ("--bg with bypassPermissions requires accepting the disclaimer first. "
           "Acceptance is kept in your user settings, which this launch does not read. "
           "Launch again with user settings on.")


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("measure_bypass_lean", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _user(text: str, mode: str = "bypassPermissions") -> str:
    return json.dumps({"type": "user", "permissionMode": mode,
                       "message": {"role": "user", "content": text}})


def _assistant(usage: Dict[str, int], model: str = "claude-haiku-5-5") -> str:
    return json.dumps({"type": "assistant", "message": {
        "model": model, "usage": usage, "content": [{"type": "text", "text": "OK"}]}})


def _listing(*names: str) -> str:
    content = "\n".join(f"- {name}: does {name}" for name in names)
    return json.dumps({"type": "attachment",
                       "attachment": {"type": "skill_listing", "content": content}})


def test_summarize_reads_modes_first_turn_input_and_skills(tool) -> None:
    lines = [_listing("dataviz", "vercel:deploy", "grilling"),
             _user("Reply OK"),
             _assistant({"input_tokens": 3, "cache_creation_input_tokens": 49000,
                         "cache_read_input_tokens": 500, "output_tokens": 4}),
             _assistant({"input_tokens": 1, "cache_read_input_tokens": 9}),
             "not json",
             json.dumps({"type": "permission-mode", "permissionMode": "auto"})]
    seen = tool.summarize(lines)
    assert seen["modes"] == ["bypassPermissions"]
    assert seen["first_turn_input"] == 49503
    assert seen["skills"] == ["dataviz", "vercel:deploy", "grilling"]
    assert seen["assistant_turns"] == 2


def test_a_synthetic_error_record_is_not_the_first_turn(tool) -> None:
    lines = [_user("Reply OK"),
             _assistant({"input_tokens": 0}, model="<synthetic>"),
             _assistant({"input_tokens": 7})]
    seen = tool.summarize(lines)
    assert seen["first_turn_input"] == 7
    assert seen["assistant_turns"] == 1


def test_summarize_after_reads_only_the_woken_turns(tool) -> None:
    lines = [_user("Reply OK", "default"), _assistant({"input_tokens": 5}),
             _user("WAKEPROBE: again", "bypassPermissions"), _assistant({"input_tokens": 9})]
    seen = tool.summarize(lines, after="WAKEPROBE")
    assert seen["modes"] == ["bypassPermissions"]
    assert seen["first_turn_input"] == 9


def test_plugin_skills_are_matched_on_the_plugin_name(tool) -> None:
    skills = ["vercel:deploy", "vercel:env", "dataviz", "vercelish"]
    assert tool.plugin_skills(skills, ["vercel@claude-plugins-official"]) == [
        "vercel:deploy", "vercel:env"]
    assert tool.plugin_skills(skills, []) == []


def _passing() -> Dict[str, Any]:
    return {
        "lean": {"refused": REFUSED},
        "flag_consent": {"refused": REFUSED},
        "local_consent": {"modes": ["default"]},
        "old_wake": {"modes": ["default"]},
        "profile": {"modes": ["bypassPermissions"], "skills": ["dataviz", "grilling"],
                    "closed": ["vercel@m"]},
        "wake": {"modes": ["bypassPermissions"]},
        "baseline": {"modes": ["auto"]},
        "full": {"modes": ["bypassPermissions"]},
    }


def test_the_measured_shape_passes(tool) -> None:
    assert tool.verdict(_passing()) == []


@pytest.mark.parametrize("arm, value, expect", [
    ("lean", {"short_id": "abc12345", "modes": ["bypassPermissions"]}, "lean: not refused"),
    ("flag_consent", {"refused": "Workspace not trusted."}, "another reason"),
    ("local_consent", {"modes": ["bypassPermissions"]}, "old shape works again"),
    ("old_wake", {}, "neither refused nor seen running"),
    ("profile", {"refused": REFUSED}, "profile: refused"),
    ("profile", {"modes": ["default"], "closed": []}, "profile: ran ['default']"),
    ("profile", {"modes": ["bypassPermissions"], "skills": ["vercel:deploy"],
                 "closed": ["vercel@m"]}, "closed plugin's skills"),
    ("wake", {"modes": ["default"]}, "wake: the woken turns ran"),
])
def test_each_departure_is_named(tool, arm, value, expect) -> None:
    report = _passing()
    report[arm] = value
    found = tool.verdict(report)
    assert any(expect in line for line in found), found
