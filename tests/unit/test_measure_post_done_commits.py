"""``tools/measure_post_done_commits.py``: commits on a child's PR the child
did not make (#436), over synthetic transcripts and a fake ``gh``."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_post_done_commits as tool  # noqa: E402

T0 = 1_790_000_000.0
URL = "https://github.com/o/r/pull/12"


def _iso(epoch: float, *, millis: bool = False) -> str:
    text = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch))
    return text + (".123Z" if millis else "Z")


def _transcript(directory: Path, name: str, *, pr: Optional[str] = URL,
                woken_at: Sequence[float] = (), start: float = T0,
                end: float = T0 + 600, after_wake: float = 900) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    records = [{"type": "user", "timestamp": _iso(start, millis=True),
                "message": {"content": "Work on issue #7"}}]
    records.append({"type": "assistant", "timestamp": _iso(start + 100, millis=True),
                    "message": {"content": [{"type": "tool_use", "id": "l", "name": "Bash",
                                             "input": {"command": "gh pr list"}}]}})
    records.append({"type": "user", "timestamp": _iso(start + 110, millis=True),
                    "message": {"content": [{"type": "tool_result", "tool_use_id": "l",
                                             "content": "https://github.com/o/r/pull/99\n"}]}})
    if pr:
        records.append({"type": "assistant", "timestamp": _iso(start + 290, millis=True),
                        "message": {"content": [{"type": "tool_use", "id": "p", "name": "Bash",
                                                 "input": {"command": "gh pr create --fill"}}]}})
        records.append({"type": "user", "timestamp": _iso(start + 300, millis=True),
                        "message": {"content": [{"type": "tool_result", "tool_use_id": "p",
                                                 "content": f"{pr}\n"}]}})
    records.append({"type": "assistant", "timestamp": _iso(end, millis=True),
                    "message": {"content": [{"type": "text", "text": "report"}]}})
    last = end
    for at in woken_at:
        records.append({"type": "user", "timestamp": _iso(at, millis=True),
                        "message": {"content": '<mnemo-pr-follow pr="12" events="ci-red">\n…'}})
        last = at + after_wake
        records.append({"type": "assistant", "timestamp": _iso(last, millis=True),
                        "message": {"content": [{"type": "text", "text": "fixed"}]}})
    path = directory / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


class Gh:
    def __init__(self, table: Dict[str, Sequence[float]]) -> None:
        self.table = table

    def __call__(self, argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
        url = argv[3]
        if url not in self.table:
            return 1, "", "not found"
        rows = [{"oid": str(i), "authoredDate": _iso(t), "committedDate": _iso(t + 10**6)}
                for i, t in enumerate(self.table[url])]
        return 0, json.dumps({"commits": rows}), ""


def test_a_commit_after_the_child_s_last_turn_is_counted_as_someone_else_s(tmp_path) -> None:
    """By author date: the fake's committer dates are all a rebase later."""
    _transcript(tmp_path / "-Users-me-app-wt-7", "a")
    report = tool.measure(str(tmp_path), run=Gh({URL: [T0 + 200, T0 + 3600]}))
    assert report["repos"]["o/r"]["with_after_child"] == 1
    (row,) = report["children"]
    assert row["commits"] == {"before": 1, "by_child_later": 0, "after_child": 1}


def test_a_woken_child_s_fix_is_the_child_s_not_someone_else_s(tmp_path) -> None:
    _transcript(tmp_path / "-Users-me-app-wt-7", "a", woken_at=[T0 + 2000])
    report = tool.measure(str(tmp_path), run=Gh({URL: [T0 + 200, T0 + 2500]}))
    (row,) = report["children"]
    assert row["wakes"] == 1
    assert row["first_stop"] == pytest.approx(T0 + 600, abs=1)
    assert row["commits"] == {"before": 1, "by_child_later": 1, "after_child": 0}
    assert report["repos"]["o/r"]["woken"] == 1
    assert report["repos"]["o/r"]["with_after_child"] == 0


def test_only_dispatch_worktrees_are_the_population(tmp_path) -> None:
    """…and only the URL the child's own `gh pr create` printed: every
    transcript here also ran `gh pr list`, which printed #99."""
    _transcript(tmp_path / "-Users-me-app", "main")
    _transcript(tmp_path / "-Users-me-app-wt-feature", "hand-made")
    _transcript(tmp_path / "-Users-me-app-wt-c-piece-one", "piece",
                pr="https://github.com/o/r/pull/13")
    _transcript(tmp_path / "-Users-me-app-wt-9", "no-pr", pr=None)
    rows = tool.children(str(tmp_path))
    assert [r["pr"] for r in rows] == ["https://github.com/o/r/pull/13"]


def test_two_transcripts_naming_one_pr_count_once_as_the_later(tmp_path) -> None:
    _transcript(tmp_path / "-Users-me-app-wt-7", "a", end=T0 + 600)
    _transcript(tmp_path / "-Users-me-app-wt-7", "b", start=T0 + 5000, end=T0 + 6000)
    (row,) = tool.children(str(tmp_path))
    assert row["last_turn"] == pytest.approx(T0 + 6000, abs=1)


def test_an_unreadable_pr_is_reported_not_counted(tmp_path) -> None:
    _transcript(tmp_path / "-Users-me-app-wt-7", "a")
    report = tool.measure(str(tmp_path), run=Gh({}))
    bucket = report["repos"]["o/r"]
    assert bucket["unreadable"] == 1 and bucket["with_after_child"] == 0
    text = tool.format_report(report)
    assert "o/r" not in text and "1 PR URLs" in text


def test_the_slack_absorbs_clock_difference(tmp_path) -> None:
    _transcript(tmp_path / "-Users-me-app-wt-7", "a")
    report = tool.measure(str(tmp_path), run=Gh({URL: [T0 + 600 + tool.SLACK_SECONDS - 1]}))
    assert report["children"][0]["commits"]["after_child"] == 0


def test_since_drops_older_children(tmp_path) -> None:
    _transcript(tmp_path / "-Users-me-app-wt-7", "a")
    assert tool.children(str(tmp_path), since=T0 + 10_000) == []


def test_the_report_prints_one_line_per_repo_and_lists_on_request(tmp_path) -> None:
    _transcript(tmp_path / "-Users-me-app-wt-7", "a")
    text = tool.format_report(
        tool.measure(str(tmp_path), run=Gh({URL: [T0 + 3600]})), listing=True)
    assert text.startswith("o/r: 1 of 1 PRs have commits made after the child's last turn")
    assert URL in text
