"""#426: the report card a finished child's parent receives, and the watch on
its checks. Every git and ``gh`` call goes through a fake runner that answers
from a table, so what each fact is read from is part of the test."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import pytest

from mnemo.core.sessions import inbox, report_card as rc

REPORT = "## Closing report — #7\n\nFixed the thing.\n\nSuite: 12 passed."


class Fake:
    """Answers ``(code, stdout, stderr)`` by the first matching argv prefix."""

    def __init__(self, table: Dict[Tuple[str, ...], Tuple[int, str, str]]) -> None:
        self.table = table
        self.calls: List[Tuple[Sequence[str], Optional[str]]] = []

    def __call__(self, argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
        self.calls.append((tuple(argv), cwd))
        for prefix, answer in self.table.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return answer
        return 1, "", "unexpected call"


def _pr_json(**over) -> str:
    row = {
        "number": 12, "url": "https://github.com/o/r/pull/12", "state": "OPEN",
        "isDraft": False, "additions": 40, "deletions": 3, "changedFiles": 2,
        "headRefOid": "abc123",
    }
    row.update(over)
    return json.dumps(row)


def _checks_json(*buckets: str) -> str:
    return json.dumps([{"name": f"job{i}", "bucket": b} for i, b in enumerate(buckets)])


def _transcript(tmp_path: Path, *texts: str, pr_output: str = "") -> Path:
    records = []
    if pr_output:
        records.append({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "p1", "name": "Bash",
             "input": {"command": "gh pr create --fill"}}]}})
        records.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "p1", "content": pr_output}]}})
    for text in texts:
        records.append({"type": "assistant", "message": {"content": [
            {"type": "text", "text": text}]}})
    records.append({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "s1", "name": "Bash",
         "input": {"command": "claude stop ${CLAUDE_CODE_SESSION_ID:0:8}"}}]}})
    path = tmp_path / "child.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _tree(tmp_path: Path) -> Path:
    tree = tmp_path / "app-wt-7"
    tree.mkdir()
    return tree


def _open_pr_runner(*buckets: str, status: str = "", head: str = "abc123",
                    checks_code: int = 0, **pr) -> Fake:
    return Fake({
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "fix/issue-7\n", ""),
        ("git", "status", "--porcelain"): (0, status, ""),
        ("git", "rev-parse", "HEAD"): (0, head + "\n", ""),
        ("gh", "pr", "list"): (0, "[" + _pr_json(**pr) + "]", ""),
        ("gh", "pr", "checks"): (checks_code, _checks_json(*buckets), ""),
    })


# --- gathering -----------------------------------------------------------------

def test_a_finished_child_with_an_open_pr_and_green_checks_is_ready(tmp_path: Path) -> None:
    run = _open_pr_runner("pass", "pass", "skipping")
    card = rc.gather("c0da0f55", cwd=str(_tree(tmp_path)),
                     transcript=_transcript(tmp_path, "Working on it.", REPORT), run=run)

    assert card.target == 7
    assert card.pr is not None and card.pr.number == 12
    assert card.checks == {"pass": 2, "skipping": 1}
    assert card.report == REPORT
    assert rc.state(card) == "ready"
    # The PR is looked up by the branch the tree has checked out.
    lookups = [argv for argv, _ in run.calls if argv[:3] == ("gh", "pr", "list")]
    assert [argv[3:5] for argv in lookups] == [("--head", "fix/issue-7")]


# Measured on gh 2.92: with `--json`, a failing check still exits 0 (PRs
# #414, #422, #424); 8 is accepted too, as `landing.failing_checks` does.
@pytest.mark.parametrize("buckets, code, expected", [
    (("pass", "fail"), 0, "ci-red"),
    (("pass", "pending"), 8, "ci-running"),
    (("pass", "pending"), 0, "ci-running"),
])
def test_the_checks_decide_between_red_and_running(
    tmp_path: Path, buckets, code, expected,
) -> None:
    run = _open_pr_runner(*buckets, checks_code=code)
    card = rc.gather("c0da0f55", cwd=str(_tree(tmp_path)), run=run)

    assert rc.state(card) == expected
    if expected == "ci-red":
        assert card.failing == ["job1"]


def test_a_pr_with_no_checks_yet_is_running_not_ready(tmp_path: Path) -> None:
    run = _open_pr_runner()
    run.table[("gh", "pr", "checks")] = (1, "", "no checks reported on the 'fix/issue-7' branch")
    card = rc.gather("c0da0f55", cwd=str(_tree(tmp_path)), run=run)

    assert card.checks == {}
    assert rc.state(card) == "ci-running"


def test_a_failure_to_ask_for_checks_is_not_read_as_none(tmp_path: Path) -> None:
    run = _open_pr_runner()
    run.table[("gh", "pr", "checks")] = (1, "", "HTTP 502: Bad Gateway")
    card = rc.gather("c0da0f55", cwd=str(_tree(tmp_path)), run=run)

    assert card.checks is None
    assert rc.state(card) == "unknown"


def test_local_work_the_pr_lacks_outranks_green_checks(tmp_path: Path) -> None:
    dirty = rc.gather("c0da0f55", cwd=str(_tree(tmp_path)),
                      run=_open_pr_runner("pass", status=" M src/app.py\n"))
    unpushed = rc.gather("c0da0f55", cwd=str(tmp_path / "app-wt-7"),
                         run=_open_pr_runner("pass", head="def456"))

    assert rc.state(dirty) == "unpublished"
    assert rc.state(unpushed) == "unpublished"


def test_draft_merged_and_closed_prs_are_named_as_such(tmp_path: Path) -> None:
    tree = str(_tree(tmp_path))

    assert rc.state(rc.gather("x", cwd=tree, run=_open_pr_runner("pass", isDraft=True))) == "draft"
    assert rc.state(rc.gather("x", cwd=tree, run=_open_pr_runner(state="MERGED"))) == "merged"
    assert rc.state(rc.gather("x", cwd=tree, run=_open_pr_runner(state="CLOSED"))) == "closed"


def test_a_merged_pr_is_not_asked_for_checks(tmp_path: Path) -> None:
    run = _open_pr_runner(state="MERGED")
    rc.gather("x", cwd=str(_tree(tmp_path)), run=run)

    assert not any(argv[:3] == ("gh", "pr", "checks") for argv, _ in run.calls)


def test_no_pr_and_no_commits_is_no_change_and_commits_are_unpublished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mnemo.core.sessions import delivery

    monkeypatch.setattr(delivery, "base_branch", lambda **k: "master")
    tree = str(_tree(tmp_path))

    def runner(ahead: str) -> Fake:
        return Fake({
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "fix/issue-7\n", ""),
            ("git", "status", "--porcelain"): (0, "", ""),
            ("gh", "pr", "list"): (0, "[]", ""),
            ("git", "rev-list", "--count", "master..fix/issue-7"): (0, ahead, ""),
        })

    refused = rc.gather("x", cwd=tree, run=runner("0\n"))
    unpublished = rc.gather("x", cwd=tree, run=runner("2\n"))

    assert rc.state(refused) == "no-change"
    assert rc.state(unpublished) == "unpublished"
    assert unpublished.ahead == 2


def test_a_removed_tree_falls_back_to_the_url_gh_pr_create_printed(tmp_path: Path) -> None:
    transcript = _transcript(
        tmp_path, "See https://github.com/o/r/pull/99 for the old one.", REPORT,
        pr_output="https://github.com/o/r/pull/12\n",
    )
    run = Fake({
        ("gh", "pr", "view", "https://github.com/o/r/pull/12"): (0, _pr_json(), ""),
        ("gh", "pr", "checks"): (0, _checks_json("pass"), ""),
    })

    card = rc.gather("x", cwd=str(tmp_path / "gone-wt-7"), transcript=transcript, run=run)

    assert not card.tree_seen
    assert card.pr is not None and card.pr.number == 12
    # A URL the child only mentioned in prose is not the PR it created.
    assert rc.pr_urls_in(transcript) == ["https://github.com/o/r/pull/12"]
    assert rc.state(card) == "ready"


def test_nothing_readable_is_unknown_and_gathering_never_raises(tmp_path: Path) -> None:
    def broken(argv, cwd):
        raise RuntimeError("boom")

    card = rc.gather("x", cwd=str(_tree(tmp_path)), run=broken)

    assert rc.state(card) == "unknown"


def test_the_closing_report_is_the_last_text_the_child_wrote(tmp_path: Path) -> None:
    limit = "You've hit your session limit · resets 1:20pm"
    assert rc.closing_report(_transcript(tmp_path, "first", REPORT)) == REPORT
    assert rc.closing_report(_transcript(tmp_path, REPORT, limit)) == limit
    assert rc.closing_report(tmp_path / "missing.jsonl") == ""


# --- the text --------------------------------------------------------------------

def test_the_card_opens_with_the_marker_and_says_it_is_not_the_user(tmp_path: Path) -> None:
    card = rc.gather("c0da0f55", cwd=str(_tree(tmp_path)),
                     transcript=_transcript(tmp_path, REPORT),
                     run=_open_pr_runner("pass", "pending", checks_code=8))
    text = rc.render(card, watch_minutes=30)

    assert text.startswith(inbox.NOTICE_PREFIX)
    assert text.splitlines()[0] == '<mnemo-child-finished id="c0da0f55" state="ci-running">'
    assert "not your user speaking" in text
    assert "c0da0f55 finished (#7)" in text
    assert "PR #12 open, ready for review, +40 −3 in 2 files" in text
    assert "checks: 1 pass, 1 pending; mnemo will post again when they settle" in text
    assert "the child's own words (mnemo did not check them)" in text
    assert "  > Fixed the thing." in text
    assert "maintainer" in text


def test_the_card_does_not_promise_a_follow_up_nobody_will_send(tmp_path: Path) -> None:
    card = rc.gather("x", cwd=str(_tree(tmp_path)),
                     run=_open_pr_runner("pending", checks_code=8))

    text = rc.render(card, watch_minutes=0)

    assert "will post again" not in text
    assert "not watching" in text


def test_a_long_report_is_cut_on_a_line_and_points_at_the_rest(tmp_path: Path) -> None:
    long_report = "\n".join(f"line {i} " + "x" * 60 for i in range(60))
    transcript = _transcript(tmp_path, long_report)
    card = rc.gather("x", cwd=str(_tree(tmp_path)), transcript=transcript,
                     run=_open_pr_runner("pass"))

    text = rc.render(card)
    quoted = [line for line in text.splitlines() if line.startswith("  > ")]

    assert sum(len(line) - 4 for line in quoted) <= rc.REPORT_CHARS
    assert all(line.endswith("x") for line in quoted)
    assert f"of {len(long_report)} characters" in text
    assert str(transcript) in text


def test_the_checks_notice_names_what_failed(tmp_path: Path) -> None:
    card = rc.gather("x", cwd=str(_tree(tmp_path)), run=_open_pr_runner("pending", checks_code=8))

    red = rc.render_checks(card, {"pass": 3, "fail": 1}, ["windows / py3.11"])
    green = rc.render_checks(card, {"pass": 4}, [])
    none = rc.render_checks(card, {}, [])
    late = rc.render_checks(card, {"pending": 2}, [], timed_out_after=30)

    assert red.splitlines()[0] == '<mnemo-child-finished id="x" state="ci-red" event="checks">'
    assert "- failing: windows / py3.11" in red
    assert 'state="ready"' in green and "3 pass" not in green and "4 pass" in green
    assert "no checks reported" in none
    assert "still running after 30 min" in late
    for text in (red, green, none, late):
        assert text.startswith(inbox.NOTICE_PREFIX)
        assert "not your user speaking" in text


# --- the watch -------------------------------------------------------------------

class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _watched_card(tmp_path: Path) -> rc.Card:
    return rc.gather("x", cwd=str(_tree(tmp_path)), run=_open_pr_runner("pending", checks_code=8))


def _answers(*replies: Tuple[int, str, str]):
    queue = list(replies)

    def run(argv, cwd):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return run


def test_the_watch_posts_once_when_the_checks_settle(tmp_path: Path) -> None:
    clock, posted = Clock(), []
    run = _answers((8, _checks_json("pass", "pending"), ""), (0, _checks_json("pass", "fail"), ""))

    ended = rc.watch_checks(_watched_card(tmp_path), minutes=30, alive=lambda: True,
                            post=lambda t: posted.append(t) or True, run=run,
                            sleep=clock.sleep, clock=clock)

    assert ended == "settled"
    assert len(posted) == 1 and 'state="ci-red"' in posted[0]
    assert clock.now == 2 * rc.POLL_SECONDS


def test_the_watch_waits_out_the_grace_before_calling_a_pr_checkless(tmp_path: Path) -> None:
    clock, posted = Clock(), []
    run = _answers((1, "", "no checks reported on the 'b' branch"))

    ended = rc.watch_checks(_watched_card(tmp_path), minutes=30, alive=lambda: True,
                            post=lambda t: posted.append(t) or True, run=run,
                            sleep=clock.sleep, clock=clock)

    assert ended == "no-checks"
    assert clock.now >= rc.NO_CHECKS_GRACE_SECONDS
    assert len(posted) == 1 and "no checks reported" in posted[0]


def test_the_watch_says_so_when_it_gives_up(tmp_path: Path) -> None:
    clock, posted = Clock(), []
    run = _answers((8, _checks_json("pending"), ""))

    ended = rc.watch_checks(_watched_card(tmp_path), minutes=2, alive=lambda: True,
                            post=lambda t: posted.append(t) or True, run=run,
                            sleep=clock.sleep, clock=clock)

    assert ended == "timeout"
    assert clock.now <= 2 * 60
    assert len(posted) == 1 and "still running after 2 min" in posted[0]


def test_the_watch_stops_silently_when_the_parent_exits(tmp_path: Path) -> None:
    clock, posted = Clock(), []
    run = _answers((8, _checks_json("pending"), ""))

    ended = rc.watch_checks(_watched_card(tmp_path), minutes=30, alive=lambda: False,
                            post=lambda t: posted.append(t) or True, run=run,
                            sleep=clock.sleep, clock=clock)

    assert ended == "parent-gone"
    assert posted == []


def test_the_watch_stops_after_repeated_unreadable_answers(tmp_path: Path) -> None:
    clock, posted = Clock(), []
    run = _answers((1, "", "HTTP 401"))

    ended = rc.watch_checks(_watched_card(tmp_path), minutes=30, alive=lambda: True,
                            post=lambda t: posted.append(t) or True, run=run,
                            sleep=clock.sleep, clock=clock)

    assert ended == "unreadable"
    assert clock.now == rc.MAX_READ_FAILURES * rc.POLL_SECONDS
    assert len(posted) == 1 and "could not be read" in posted[0]


@pytest.mark.parametrize("configured, expected", [
    (None, rc.DEFAULT_WATCH_MINUTES), (0, 0), (15, 15), (-5, 0),
    (10_000, rc.MAX_WATCH_MINUTES), ("soon", rc.DEFAULT_WATCH_MINUTES),
    (True, rc.DEFAULT_WATCH_MINUTES),
])
def test_the_configured_watch_is_bounded(configured, expected) -> None:
    cfg = {} if configured is None else {"dispatch": {"watchChecksMinutes": configured}}
    assert rc.watch_minutes(cfg) == expected


def test_what_was_sent_is_recorded_one_row_per_event(tmp_path: Path) -> None:
    rc.record(tmp_path, {"short_id": "x", "event": "finished", "delivered": True})
    rc.record(tmp_path, {"short_id": "x", "event": "settled", "delivered": False})

    raw = (tmp_path / ".mnemo" / rc.LOG_NAME).read_bytes()
    rows = [json.loads(line) for line in raw.decode("utf-8").splitlines()]

    assert b"\r\n" not in raw
    assert [r["event"] for r in rows] == ["finished", "settled"]
    assert all("ts" in r for r in rows)
