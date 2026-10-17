"""``tools/measure_windows_inbox.py`` over synthetic transcripts (#725)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_windows_inbox.py"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("measure_windows_inbox", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _user(content) -> str:
    return json.dumps({"type": "user", "message": {"role": "user", "content": content}})


def test_a_nonce_in_a_user_turn_arrived(tool) -> None:
    lines = [_user("PROBE-AUTH-1: reply OK"),
             json.dumps({"type": "assistant", "message": {"content": "PROBE-NOAUTH-2"}})]
    assert tool.arrived(lines, ["PROBE-AUTH-1", "PROBE-NOAUTH-2"]) == {
        "PROBE-AUTH-1": True, "PROBE-NOAUTH-2": False}


def test_a_wrapped_turn_still_counts_and_junk_lines_do_not_break_it(tool) -> None:
    lines = ["{not json", "", _user([{"type": "text", "text": "peer says: PROBE-AUTH-1"}])]
    assert tool.arrived(lines, ["PROBE-AUTH-1"]) == {"PROBE-AUTH-1": True}


PASSING = {"address": {"pid": 1}, "key_found": True, "noauth_arrived": False,
           "auth_error": None, "auth_arrived": True}


def test_auth_required_and_honoured_passes(tool) -> None:
    assert tool.verdict(PASSING) == []


def test_an_unauthenticated_turn_that_arrives_fails(tool) -> None:
    assert tool.verdict({**PASSING, "noauth_arrived": True}) == [
        "the turn without an auth line arrived; auth is not required"]


def test_a_missing_key_and_a_lost_turn_are_named(tool) -> None:
    why = tool.verdict({**PASSING, "key_found": False, "auth_arrived": False,
                        "auth_error": "no peer key for pid 1"})
    assert why == ["no peer key with the entry's start time and host",
                   "the authenticated write failed: no peer key for pid 1",
                   "the authenticated turn never reached the transcript"]


def test_no_session_and_a_failed_start_fail_first(tool) -> None:
    assert tool.verdict({}) == ["the registry never named a live session"]
    assert tool.verdict({"error": "claude: not found"}) == ["claude: not found"]
