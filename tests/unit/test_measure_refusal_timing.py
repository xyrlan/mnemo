"""``tools/measure_refusal_timing.py``: the #383 timing count, on transcripts
shaped like Claude Code's (``cwd`` on every event, ``message.content`` blocks,
a human prompt as a plain string)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_refusal_timing as tool  # noqa: E402

CWD = "/x/app-wt-197"


def _human(text: str) -> dict:
    return {"type": "user", "cwd": CWD, "message": {"role": "user", "content": text}}


def _say(*blocks: dict) -> dict:
    return {"type": "assistant", "cwd": CWD,
            "message": {"role": "assistant", "content": list(blocks)}}


def _text(text: str) -> dict:
    return {"type": "text", "text": text}


def _tool(uid: str, command: str, name: str = "Bash") -> dict:
    return {"type": "tool_use", "id": uid, "name": name, "input": {"command": command}}


def _write(tmp_path: Path, records: list, *, project: str = "-x-app-wt-197") -> None:
    directory = tmp_path / project
    directory.mkdir(exist_ok=True)
    (directory / f"{len(list(directory.iterdir()))}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _opening(issue: int = 197) -> dict:
    return _human(f"Work on issue #{issue} in this repo")


# --- what counts as a refusal ----------------------------------------------


def test_a_section_that_says_nothing_was_refused_is_not_a_refusal() -> None:
    assert tool.is_refusal("## What I refused\n\nNothing. The plan held up.") is None
    assert tool.is_refusal("**What I refused:** nada, a issue batia com o código.") is None


def test_a_section_naming_what_was_refused_is_one() -> None:
    assert tool.is_refusal(
        "## What I refused\n\nBullet 3 — the guard is dead code.") == "section"


def test_a_contradicted_premise_counts_even_when_the_child_shipped() -> None:
    """A child that opens a PR *and* corrects the issue is the case a rating
    would have had to catch, so it is in the population."""
    assert tool.is_refusal(
        "Committed `abc1234`. **Premise half wrong.** The gate is not the debt."
    ) == "premise"


def test_a_rescope_counts() -> None:
    assert tool.is_refusal("Issue re-scoped by its own comments.") == "rescope"


# --- when the doubt is dated ------------------------------------------------


def test_tool_uses_are_counted_per_block_not_per_turn(tmp_path: Path) -> None:
    _write(tmp_path, [
        _opening(),
        _say(_tool("a", "gh issue view 197"), _tool("b", "git log"), _tool("c", "ls")),
        _say(_text("Done.\n\n## What I refused\n\nBullet 2.")),
    ])

    report = tool.measure(str(tmp_path))

    assert report["refusals"] == 1
    assert report["rows"][0]["tools"] == 3


def test_doubt_only_in_the_closing_report_is_dated_at_the_end(tmp_path: Path) -> None:
    _write(tmp_path, [
        _opening(),
        _say(_tool("a", "gh issue view 197")),
        _say(_tool("b", "sed -n 1,80p src/app.py")),
        _say(_text("Done. **The issue's premise is wrong:** the flag is read first.")),
    ])

    report = tool.measure(str(tmp_path))
    row = report["rows"][0]

    assert (row["doubt_at"], row["tools"]) == (2, 2)
    assert report["only_in_the_report"] == 1
    assert report["within"] == {3: 1, 5: 1, 10: 1}


def test_doubt_stated_mid_run_is_dated_where_it_was_said(tmp_path: Path) -> None:
    _write(tmp_path, [
        _opening(),
        _say(_tool("a", "gh issue view 197 --comments")),
        _say(_text("Comment re-scopes it. Let me read the code."),
             _tool("b", "sed -n 1,80p src/app.py")),
        _say(_text("Done.\n\n## What I refused\n\nThe original bullet 1.")),
    ])

    report = tool.measure(str(tmp_path))

    assert report["rows"][0]["doubt_at"] == 1
    assert report["within"][3] == 1
    assert report["only_in_the_report"] == 0


def test_doubt_before_the_first_mutation_is_counted_as_such(tmp_path: Path) -> None:
    _write(tmp_path, [
        _opening(),
        _say(_tool("a", "gh issue view 197 --comments")),
        _say(_text("The issue says the guard is missing, but it does not exist as described."),
             _tool("b", "cat >> src/app.py <<'EOF'\nx = 1\nEOF")),
        _say(_text("Done.\n\n## What I refused\n\nBullet 1.")),
    ])

    report = tool.measure(str(tmp_path))
    row = report["rows"][0]

    assert row["mutation_at"] == 1 and row["doubt_at"] == 1
    # Said in the same turn that issued the mutation: not *before* it.
    assert report["before_first_mutation"] == 0


def test_a_child_with_no_doubt_marker_is_still_a_refusal(tmp_path: Path) -> None:
    """The population is what the report records; ``doubt_at`` is separate, and
    a silent child has none."""
    _write(tmp_path, [
        _opening(),
        _say(_tool("a", "gh issue view 197")),
        _say(_text("## What I refused\n\nBullet 3 — dead code.")),
    ])

    report = tool.measure(str(tmp_path))

    assert report["refusals"] == 1
    assert report["rows"][0]["doubt_at"] is None
    assert report["only_in_the_report"] == 1
    assert report["within"] == {3: 0, 5: 0, 10: 0}


# --- who actually answered --------------------------------------------------


def test_a_peer_message_is_not_the_maintainer_answering() -> None:
    assert tool.second_turn_kind("Another Claude session sent a message: push it") == "peer"
    assert tool.second_turn_kind("[Image: original 3360x312, displayed") == "image"
    assert tool.second_turn_kind("Continue from where you left off. Note:") == "auto_restart"
    assert tool.second_turn_kind("yes, go ahead") == "typed"


def test_the_second_human_turn_is_dated_and_classified(tmp_path: Path) -> None:
    _write(tmp_path, [
        _opening(),
        _say(_tool("a", "gh issue view 197")),
        _say(_tool("b", "ls")),
        _human("Another Claude session sent a message: done"),
        _say(_text("Noted.")),
    ])

    report = tool.measure(str(tmp_path))

    assert report["blocked"]["children"] == 1
    assert report["blocked"]["earliest"] == 2
    assert report["blocked"]["by_kind"] == {
        "typed": 0, "peer": 1, "image": 0, "auto_restart": 0}


# --- the population ---------------------------------------------------------


def test_a_tree_that_is_not_a_dispatch_worktree_is_out(tmp_path: Path) -> None:
    directory = tmp_path / "-x-app"
    directory.mkdir()
    (directory / "s.jsonl").write_text(json.dumps({
        "type": "user", "cwd": "/x/app",
        "message": {"role": "user", "content": "hi"}}) + "\n", encoding="utf-8")

    assert tool.measure(str(tmp_path))["population"] == 0


def test_a_child_that_ran_no_tool_is_excluded_from_the_denominator(tmp_path: Path) -> None:
    _write(tmp_path, [_opening(), _say(_text("Ready. What task?"))])

    report = tool.measure(str(tmp_path))

    assert (report["population"], report["started"]) == (1, 0)
