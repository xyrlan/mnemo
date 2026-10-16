"""The agent ledger: which CLI, id, home and tree a dispatched Grok session has.

Written once at spawn and read by everyone else (the Grok round's contract,
``design/contracts/2026-10-16-grok-host-wave2.md``), so what matters here is
that a line comes back exactly as written, that a bad line costs only itself,
and that a Claude-only machine never sees the file at all.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import agent_cli
from mnemo.core.sessions import child_agents
from mnemo.core.sessions.child_agents import ChildAgent

SID = "01a144cc-77f5-7ea3-9060-06013224200f"


def _child(**kw) -> ChildAgent:
    fields = dict(short_id="3224200f", agent="grok", session_id=SID,
                  home=str(Path("/h/.grok")), cwd=str(Path("/w/proj-wt-c-x")))
    fields.update(kw)
    return ChildAgent(**fields)


def test_a_recorded_child_reads_back_as_it_was_written(tmp_path: Path) -> None:
    child = _child()
    child_agents.record(child, vault_root=tmp_path)

    assert child_agents.read(tmp_path) == {"3224200f": child}
    assert child_agents.log_path(tmp_path) == tmp_path / ".mnemo" / "child-agents.jsonl"


def test_a_home_of_none_survives_the_round_trip(tmp_path: Path) -> None:
    child = _child(home=None)
    child_agents.record(child, vault_root=tmp_path)

    assert child_agents.read(tmp_path)["3224200f"].home is None


def test_the_later_line_for_a_short_id_wins(tmp_path: Path) -> None:
    child_agents.record(_child(cwd="first"), vault_root=tmp_path)
    child_agents.record(_child(cwd="second"), vault_root=tmp_path)

    assert child_agents.read(tmp_path)["3224200f"].cwd == "second"


def test_the_default_vault_is_the_configured_one(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(child_agents, "_default_vault", lambda: tmp_path)
    child_agents.record(_child())

    assert "3224200f" in child_agents.read(tmp_path)


@pytest.mark.parametrize("missing", ["short_id", "session_id"])
def test_a_child_with_no_key_writes_nothing(tmp_path: Path, missing: str) -> None:
    child_agents.record(_child(**{missing: ""}), vault_root=tmp_path)

    assert not child_agents.log_path(tmp_path).exists()


def test_a_vault_that_cannot_be_written_does_not_raise(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")

    child_agents.record(_child(), vault_root=blocker)  # .mnemo cannot be made under a file


def test_no_ledger_reads_as_no_children(tmp_path: Path) -> None:
    assert child_agents.read(tmp_path) == {}


def test_a_bad_line_costs_only_itself(tmp_path: Path) -> None:
    good = _child()
    other = _child(short_id="aaaaaaaa", session_id="01a144cc-0000-7000-8000-0000aaaaaaaa")
    path = child_agents.log_path(tmp_path)
    path.parent.mkdir(parents=True)
    rows = [
        json.dumps({"short_id": good.short_id, "agent": "grok", "session_id": SID,
                    "home": good.home, "cwd": good.cwd}),
        "{torn",
        json.dumps(["not", "an", "object"]),
        json.dumps({"short_id": "bbbbbbbb", "agent": "codex", "session_id": "x", "home": None, "cwd": ""}),
        json.dumps({"short_id": "cccccccc", "agent": "grok", "session_id": "", "home": None, "cwd": ""}),
        json.dumps({"short_id": "dddddddd", "agent": "grok", "session_id": "x", "home": 3, "cwd": ""}),
        json.dumps({"short_id": "eeeeeeee", "agent": "grok", "session_id": "x", "home": None}),
        json.dumps({"short_id": other.short_id, "agent": "grok", "session_id": other.session_id,
                    "home": other.home, "cwd": other.cwd}),
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert child_agents.read(tmp_path) == {good.short_id: good, other.short_id: other}


def test_the_ledger_names_exactly_the_clis_mnemo_drives() -> None:
    assert child_agents.AGENTS == agent_cli.AGENT_CLIS
