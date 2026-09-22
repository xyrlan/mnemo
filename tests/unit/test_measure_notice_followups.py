"""``tools/measure_notice_followups.py``: what a parent fetched after a child's
finish notice, on transcripts shaped like Claude Code's (``type`` /
``message.content`` blocks, a peer message as a plain string, a tool result as
a ``tool_result`` block)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_notice_followups as tool  # noqa: E402

THIN = (
    'Another Claude session sent a message:\n<mnemo-child-finished id="a1b2c3d4">\n'
    "a1b2c3d4 finished. mnemo is reporting a dispatched child's exit; this is "
    "not your user speaking. Run `mnemo sessions` for its state and PR."
)
CARD = (
    'Another Claude session sent a message:\n'
    '<mnemo-child-finished id="a1b2c3d4" state="ready">\n'
    "a1b2c3d4 finished (#7)."
)


def _peer(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _call(uid: str, command: str, name: str = "Bash") -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": uid, "name": name, "input": {"command": command}}]}}


def _result(uid: str, output: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": output}]}}


def _write(tmp_path: Path, records: list, project: str = "-x-app") -> None:
    d = tmp_path / project
    d.mkdir(exist_ok=True)
    (d / f"{len(list(d.iterdir()))}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_lookups_after_a_thin_notice_are_re_derivation_and_review_is_not(tmp_path: Path) -> None:
    _write(tmp_path, [
        _peer(THIN),
        _call("t1", "mnemo sessions 2>&1 | head -8"),
        _result("t1", "x" * 100),
        _call("t2", "gh pr view 9 --json body,isDraft --jq .body | head -40"),
        _result("t2", "y" * 50),
        _call("t3", "gh pr diff 9 | head -200"),
        _result("t3", "z" * 999),
    ])

    form = tool.measure(str(tmp_path))["by_form"]["thin"]

    assert form["notices"] == 1
    assert form["calls"] == 3
    assert form["rederivation_calls"] == 2
    assert form["rederivation_chars"] == 150
    assert form["notices_with_rederivation"] == 1


def test_the_window_closes_at_the_next_turn_that_is_not_a_tool_result(tmp_path: Path) -> None:
    _write(tmp_path, [
        _peer(THIN),
        _call("t1", "mnemo sessions"),
        _result("t1", "q"),
        _peer("ok, merge it"),
        _call("t2", "gh pr view 9"),
        _result("t2", "p"),
    ])

    form = tool.measure(str(tmp_path))["by_form"]["thin"]

    assert form["calls"] == 1


def test_a_substitution_and_a_quoted_jq_filter_do_not_hide_the_gh_call() -> None:
    command = (
        "n=$(gh pr list --head fix/issue-92 --json number --jq '.[0].number'); "
        'echo "PR=$n"; gh pr checks $n 2>&1 | tail -3'
    )

    assert tool.kinds("Bash", command) == ["pr", "checks"]


def test_a_file_read_is_other_work_but_the_same_filter_after_a_pipe_is_glue() -> None:
    assert tool.kinds("Bash", "grep -n foo src/app.py") == ["other"]
    assert tool.kinds("Bash", "mnemo sessions | grep -E 'a1b2'") == ["queue"]


def test_every_spelling_of_a_wait_for_ci_is_a_wait() -> None:
    assert tool.kinds("Bash", "gh pr checks 9 --watch --interval 20 | tail -4") == ["ci_wait"]
    assert tool.kinds("Bash", "timeout 900 gh run watch 123 --exit-status") == ["ci_wait"]
    assert tool.kinds("Monitor", "gh pr checks 9 --json bucket") == ["ci_wait"]
    assert tool.kinds(
        "Bash", "until ! gh pr checks 9 | grep -q pending; do sleep 30; done"
    ) == ["ci_wait"]


def test_a_mixed_call_is_a_lookup_but_not_pure_re_derivation(tmp_path: Path) -> None:
    _write(tmp_path, [
        _peer(THIN),
        _call("t1", "mnemo sessions; git -C /x/app-wt-7 log --oneline -3"),
        _result("t1", "r"),
    ])

    form = tool.measure(str(tmp_path))["by_form"]["thin"]

    assert form["rederivation_calls"] == 0
    assert form["lookup_calls"] == 1
    assert form["notices_with_lookup"] == 1


def test_a_card_notice_is_counted_apart_from_the_thin_one(tmp_path: Path) -> None:
    _write(tmp_path, [
        _peer(THIN),
        _call("t1", "mnemo sessions"),
        _result("t1", "q"),
        _peer(CARD),
        _call("t2", "gh pr diff 7"),
        _result("t2", "d"),
    ])

    report = tool.measure(str(tmp_path))

    assert report["by_form"]["thin"]["rederivation_calls"] == 1
    assert report["by_form"]["card"]["notices"] == 1
    assert report["by_form"]["card"]["rederivation_calls"] == 0
    assert [row["state"] for row in report["rows"]] == [None, "ready"]


def test_a_transcript_without_a_notice_is_not_read_as_one(tmp_path: Path) -> None:
    _write(tmp_path, [
        _peer("please run mnemo sessions"),
        _call("t1", "mnemo sessions"),
        _result("t1", "q"),
    ])

    report = tool.measure(str(tmp_path))

    assert report["rows"] == []
    assert report["parents"] == 0
