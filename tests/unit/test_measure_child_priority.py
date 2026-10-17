"""``tools/measure_child_priority.py`` over synthetic records (#723)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Dict, List

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_child_priority.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("measure_child_priority", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


ENTRY = {"pid": 53360, "kind": "bg"}


def test_the_probe_records_an_entry_found_at_once(tool) -> None:
    clock = _Clock()
    lowered: List[str] = []
    record = tool.probe({"session_id": "s1"}, find=lambda sid: ENTRY,
                        lower=lambda sid: lowered.append(sid), clock=clock,
                        sleep=clock.sleep)
    assert record == {"session_id": "s1", "registry_at_first_try": True,
                      "registry_waited_ms": 0, "pid": 53360, "kind": "bg",
                      "lower_failed": None}
    assert lowered == ["s1"]


def test_the_probe_says_how_long_a_late_registry_took(tool) -> None:
    clock = _Clock()
    answers = iter([None, None, None, ENTRY])
    record = tool.probe({"session_id": "s1"}, find=lambda sid: next(answers),
                        lower=lambda sid: None, clock=clock, sleep=clock.sleep)
    assert record["registry_at_first_try"] is False
    assert record["registry_waited_ms"] == 300
    assert record["pid"] == 53360


def test_the_probe_gives_up_and_still_reports_the_lowering(tool) -> None:
    clock = _Clock()
    record = tool.probe({}, find=lambda sid: None, lower=lambda sid: "no live entry",
                        clock=clock, sleep=clock.sleep, wait=1.0)
    assert record["pid"] is None
    assert record["lower_failed"] == "no live entry"
    assert record["registry_waited_ms"] >= 1000


def _seen(**classes: Any) -> Dict[str, Any]:
    base = {"session": "BelowNormal", "command": "BelowNormal",
            "pty_host": "Normal", "daemon": "Normal"}
    return {"probe": {"registry_at_first_try": True, "lower_failed": None},
            "classes": {**base, **classes}}


def test_a_lowered_start_and_wake_pass(tool) -> None:
    assert tool.verdict({"start": _seen(), "wake": _seen()}) == []


def test_a_lowered_daemon_fails(tool) -> None:
    assert tool.verdict({"start": _seen(daemon="BelowNormal"), "wake": _seen()}) == [
        "daemon is BelowNormal, expected Normal"]


def test_a_wake_that_comes_back_normal_fails(tool) -> None:
    why = tool.verdict({"start": _seen(), "wake": _seen(session="Normal", command="Normal")})
    assert why == ["after the wake: session is Normal, expected BelowNormal",
                   "after the wake: command is Normal, expected BelowNormal"]


def test_a_probe_that_never_ran_and_a_missing_wake_fail(tool) -> None:
    why = tool.verdict({"start": {"classes": {}}})
    assert why[0] == "the SessionStart probe never ran"
    assert "after the wake: the SessionStart probe never ran" in why


def test_a_late_registry_and_a_failed_lowering_are_named(tool) -> None:
    seen = _seen()
    seen["probe"] = {"registry_at_first_try": False, "lower_failed": "denied"}
    why = tool.verdict({"start": seen, "wake": _seen()})
    assert why == ["the registry did not name the session when the hook ran",
                   "lowering failed: denied"]
