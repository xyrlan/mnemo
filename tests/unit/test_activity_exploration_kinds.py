"""What a child's pre-mutation window was spent *on* (#382).

Every command below is one a real dispatch child ran before its first mutation,
taken from the transcripts on disk on 2026-09-19, and the cases that look
pedantic are the ones a first version of the classifier got wrong on them:
heredoc bodies splitting into a "command" per line of Python, loop bodies
disappearing behind ``do``, and mixed commands handing a repo map every byte
their co-located reads had returned.

How much the window cost is tested in ``test_activity_exploration.py``; this
is only the split.
"""
from __future__ import annotations

import json

import pytest

from mnemo.core.activity.exploration_kinds import (
    ANSWERABLE,
    KINDS,
    Budget,
    answerable_share,
    budget,
    classify,
    command_shares,
    shares,
)

CWD = "/Users/x/github/mnemo-wt-382"


# --- one question per command ------------------------------------------------

@pytest.mark.parametrize("kind, command", [
    # "where does X live": the grep roams, or names no file at all.
    ("search", 'grep -rn "relative_gap" src --include=*.py'),
    ("search", 'grep -rln "canonical" tests'),
    ("search", 'grep -rn "otdh_walkable" --include=*.c --include=*.h .'),
    ("search", 'grep "TODO"'),
    # "what is here".
    ("list", "ls .github/workflows"),
    ("list", "find . -name 'session_end*'"),
    ("list", "wc -l src/mnemo/core/sessions/render.py tests/unit/test_sessions_render.py"),
    # "what does this file say" — including a grep that names its files.
    ("read", "cat docs/troubleshooting.md"),
    ("read", "sed -n '60,95p' test/unit/cron/cron-timezone.spec.ts"),
    ("read", 'grep -n "confirm" src/cockpit/MissionMap.tsx src/cockpit/InboxRow.tsx'),
    ("read", "cat -n src/mnemo/core/extract/inbox/dedup.py"),
    # the issue and its history, not the repo.
    ("context", "gh issue view 333 --comments"),
    ("context", "git log --oneline -5"),
    # running something.
    ("run", "PYTHONPATH=src python3 -m pytest tests/ -q"),
    ("run", "npx vitest run src/chrome"),
    ("run", "pnpm install --frozen-lockfile"),
    # nothing this module recognises: the map earns nothing for it.
    ("other", "which claude"),
    ("other", "pkill -f 15433"),
    ("other", ""),
])
def test_a_single_question_is_classified_by_what_it_asks(kind: str, command: str) -> None:
    assert command_shares(command) == {kind: 1.0}
    assert classify("Bash", {"command": command}) == kind


def test_a_pipeline_is_the_kind_of_what_feeds_it() -> None:
    """`| head -20` is not a read: it trims the answer to the question before it."""
    assert command_shares('grep -rn "x" src/ | head -20') == {"search": 1.0}
    assert command_shares("ls src | sort | wc -l") == {"list": 1.0}
    assert command_shares("cat a.py | grep def") == {"read": 1.0}


# --- one command, several questions -------------------------------------------

def test_a_mixed_command_splits_evenly_across_what_it_asks() -> None:
    """The measured shape: children bundle a read and a listing into one use.

    Resolving it to a single kind by priority was the first version's rule, and
    on the 175 children measured it handed the map 96% of the bytes credited to
    `list` and `search` — bytes the `cat` had returned.
    """
    assert command_shares("cat src/a.py src/b.py; ls src/cockpit") == {"read": 0.5, "list": 0.5}
    assert command_shares("ls; ls; cat a") == pytest.approx({"list": 2 / 3, "read": 1 / 3})
    assert answerable_share("Bash", {"command": "cat a; ls b"}) == 0.5


def test_every_split_sums_to_one_whole_use() -> None:
    for command in ("ls && cat a && grep -rn x . && pytest && gh issue view 1",
                    "echo hi", "", "cd /tmp"):
        assert sum(command_shares(command).values()) == pytest.approx(1.0)


def test_classify_names_a_mixed_command_by_kinds_order() -> None:
    """One label for a listing; `shares` is what the arithmetic uses."""
    command = {"command": "cat a; ls b; grep -rn x ."}
    assert classify("Bash", command) == "search"
    assert set(shares("Bash", command)) == {"read", "list", "search"}


# --- what is not shell --------------------------------------------------------

def test_a_heredoc_body_is_not_a_sequence_of_commands() -> None:
    """A probe script split on its newlines into one "command" per line of Python.

    `import`, `def` and `return` became 1,400 segments across the window, a
    third of it landed in `other`, and every command carrying a heredoc had its
    real share diluted by its own body.
    """
    command = (
        "python3 - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        "def main():\n"
        "    return Path('x').read_text()\n"
        "PY"
    )
    assert command_shares(command) == {"run": 1.0}


def test_a_separator_inside_quotes_does_not_split_the_command() -> None:
    assert command_shares("""grep -n "a;b" src/x.py""") == {"read": 1.0}
    assert command_shares("""grep -rn "a|b" src/""") == {"search": 1.0}


def test_control_flow_keywords_are_not_commands() -> None:
    """Splitting on `;` leaves a loop body behind `do` and its end as `done`."""
    assert command_shares("for n in 1 2; do gh issue view $n; done") == {"context": 1.0}
    assert command_shares("if [ -f a ]; then cat a; fi") == {"read": 1.0}


# --- tools that are not Bash --------------------------------------------------

@pytest.mark.parametrize("name, kind", [
    ("Read", "read"),
    ("NotebookRead", "read"),
    ("Grep", "search"),
    ("Glob", "list"),
    ("mcp__mnemo__list_rules_by_topic", "vault"),
    ("WebFetch", "other"),
    ("", "other"),
])
def test_other_tools_carry_their_kind(name: str, kind: str) -> None:
    assert shares(name, {}) == {kind: 1.0}


def test_bash_with_no_command_is_other() -> None:
    assert shares("Bash", {}) == {"other": 1.0}
    assert shares("Bash", None) == {"other": 1.0}


# --- the window ----------------------------------------------------------------

def _turn(name, input_, use_id="t"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": use_id, "name": name, "input": input_}]}}


def _result(use_id, text):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": use_id, "content": text}]}}


def test_the_window_closes_at_the_first_mutation() -> None:
    found = budget([
        {"type": "assistant", "cwd": CWD, "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "Bash", "input": {"command": "ls"}}]}},
        _turn("Edit", {"file_path": f"{CWD}/src/x.py"}, "b"),
        _turn("Bash", {"command": "cat README.md"}, "c"),
    ], CWD)

    assert found.reached is True
    assert found.uses == {"list": 1.0}


def test_an_unfinished_window_is_a_floor_not_a_number() -> None:
    found = budget([_turn("Bash", {"command": "ls"}, "a")], CWD)
    assert found.reached is False
    assert found.total_uses == 1.0


def test_a_result_divides_the_way_its_use_did() -> None:
    found = budget([
        _turn("Bash", {"command": "cat a.py; ls src"}, "a"),
        _result("a", "x" * 400),
    ], CWD)

    assert found.chars == {"read": 200.0, "list": 200.0}
    assert found.answerable_chars() == 200.0
    assert found.total_chars == 400.0


def test_a_result_with_no_use_in_the_window_is_dropped() -> None:
    """A sidechain's result, or one whose use came after the mutation."""
    found = budget([_turn("Bash", {"command": "ls"}, "a"), _result("elsewhere", "x" * 99)], CWD)
    assert found.chars == {}


def test_a_sidechain_spends_the_subagents_window_not_the_childs() -> None:
    events = [dict(_turn("Bash", {"command": "grep -rn x ."}, "a"), isSidechain=True)]
    assert budget(events, CWD).total_uses == 0


def test_list_content_blocks_are_summed() -> None:
    found = budget([
        _turn("Read", {"file_path": "a"}, "a"),
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "a",
             "content": [{"type": "text", "text": "abc"}, {"type": "text", "text": "de"}]}]}},
    ], CWD)
    assert found.chars == {"read": 5.0}


def test_cwd_is_learned_from_the_transcript_when_not_given() -> None:
    """Without it, an Edit inside the worktree would not read as a mutation."""
    events = [{"type": "assistant", "cwd": CWD, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": "a", "name": "Edit", "input": {"file_path": f"{CWD}/a.py"}}]}}]
    assert budget(events).reached is True


def test_malformed_events_are_skipped_not_raised() -> None:
    assert budget([None, 7, {}, {"type": "assistant"}, {"type": "assistant", "message": []}]).uses == {}


# --- the ceiling ----------------------------------------------------------------

def test_the_ceiling_adds_the_turns_the_answered_uses_cost() -> None:
    """Half the uses are answerable, so half of what the results did not explain."""
    found = Budget(uses={"list": 1.0, "read": 1.0}, chars={"list": 400.0, "read": 400.0})

    # results are 200 tokens; growth is 1000, so 800 is turn overhead, halved.
    assert found.ceiling(1000) == pytest.approx(100.0 + 400.0)
    assert found.answerable_chars() / 4 == 100.0


@pytest.mark.parametrize("growth", [None, 0, -5])
def test_no_growth_is_no_ceiling(growth) -> None:
    assert Budget(uses={"list": 1.0}, chars={"list": 40.0}).ceiling(growth) == 0.0


def test_an_empty_window_has_no_ceiling() -> None:
    assert Budget().ceiling(50_000) == 0.0


def test_answerable_is_exactly_what_a_map_could_hold() -> None:
    """Reading the file the child is about to change is not on the list."""
    assert set(ANSWERABLE) == {"list", "search"}
    assert set(ANSWERABLE) < set(KINDS)
