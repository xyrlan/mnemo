"""``tools/measure_merge_wait.py``: how long a child's green PR waited for its
merge and who merged it (#638), over synthetic PRs and a fake ``gh``."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_merge_wait as tool  # noqa: E402

T0 = 1_790_000_000.0
REPO = "o/r"


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


def _run(conclusion: str = "SUCCESS", at: float = T0) -> Dict[str, Any]:
    return {"__typename": "CheckRun", "name": "ci", "status": "COMPLETED",
            "conclusion": conclusion, "completedAt": _iso(at)}


def _pr(number: int, *, wait: Optional[float] = 10, branch: Optional[str] = None,
        rollup: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """A PR whose last check completed at ``T0`` and that merged *wait* minutes later."""
    return {
        "number": number, "url": f"https://github.com/{REPO}/pull/{number}",
        "createdAt": _iso(T0 - 3600), "mergedAt": _iso(T0 + (wait or 0) * 60),
        "headRefName": branch or f"fix/issue-{number}",
        "statusCheckRollup": [_run(at=T0 - 300), _run()] if rollup is None else rollup,
    }


def _gh(prs: Sequence[Dict[str, Any]], code: int = 0):
    calls: List[Tuple[str, ...]] = []

    def run(argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
        calls.append(tuple(argv))
        return code, json.dumps(list(prs)), ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _vault(tmp_path: Path, children: Dict[str, Dict[str, Any]]) -> Path:
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / "pr-follow.json").write_text(
        json.dumps({"schema_version": 1, "children": children}), encoding="utf-8")
    return vault


def _measure(prs, vault: Path, **kw) -> Dict[str, Any]:
    return tool.measure([REPO], limit=100, vault=vault, run=_gh(prs), **kw)["repos"][REPO]


def test_the_wait_is_from_the_last_check_completing_to_the_merge(tmp_path) -> None:
    s = _measure([_pr(1, wait=10), _pr(2, wait=30), _pr(3, wait=50)], tmp_path)
    w = s["wait_minutes"]
    assert (w["n"], w["median"], w["max"]) == (3, 30, 50)
    assert w["sum_hours"] == pytest.approx(1.5)


def test_the_last_check_is_the_latest_one_not_the_first_listed(tmp_path) -> None:
    rollup = [_run(at=T0), _run(at=T0 - 900)]
    assert tool.check_facts(rollup) == (tool.GREEN, T0)


def test_only_a_childs_branch_is_counted(tmp_path) -> None:
    s = _measure([_pr(1), _pr(2, branch="release/1.2"), _pr(3, branch="fix/issue-3-3fa9c1")],
                 tmp_path)
    assert (s["merged_read"], s["child_prs"], s["numbers"]) == (3, 2, [1, 3])


@pytest.mark.parametrize("conclusion", ["FAILURE", "SKIPPED", "NEUTRAL", "CANCELLED", "", None])
def test_only_success_is_green(tmp_path, conclusion) -> None:
    """What the watcher holds a merge to: a check that never ran is not green."""
    s = _measure([_pr(1, rollup=[_run(), _run(conclusion)])], tmp_path)
    assert s["checks"] == {"green": 0, "not-green": 1, "none": 0}
    assert s["wait_minutes"]["n"] == 0


def test_a_commit_status_is_read_by_its_state(tmp_path) -> None:
    status = {"__typename": "StatusContext", "context": "ci/x", "state": "SUCCESS",
              "startedAt": _iso(T0)}
    assert tool.check_facts([status]) == (tool.GREEN, T0)
    assert tool.check_facts([dict(status, state="PENDING")])[0] == tool.NOT_GREEN


def test_a_pr_with_no_checks_is_counted_apart(tmp_path) -> None:
    s = _measure([_pr(1, rollup=[])], tmp_path)
    assert s["checks"] == {"green": 0, "not-green": 0, "none": 1}


def test_a_pr_merged_before_its_last_check_is_not_a_wait(tmp_path) -> None:
    s = _measure([_pr(1, wait=10), _pr(2, wait=-12)], tmp_path)
    assert s["merged_early"] == [2] and s["merged_early_minutes"] == [12.0]
    assert (s["wait_minutes"]["n"], s["wait_minutes"]["sum_hours"]) == (1, pytest.approx(10 / 60))


def test_who_merged_is_read_from_the_follow_ledger(tmp_path) -> None:
    url = f"https://github.com/{REPO}/pull/"
    vault = _vault(tmp_path, {
        "aaaaaaaa": {"pr": url + "1", "closed": "merged", "merged_by": "mnemo"},
        "bbbbbbbb": {"pr": url + "2", "closed": "merged", "merged_by": "other"},
        "cccccccc": {"pr": url + "3", "closed": "merged"},            # before #638
        "dddddddd": {"pr": url + "4", "closed": "window", "merged_by": "mnemo"},
    })
    s = _measure([_pr(1, wait=2), _pr(2, wait=40), _pr(3, wait=60), _pr(4, wait=80)], vault)
    by = s["merged_by"]
    assert (by["mnemo"]["prs"], by["person"]["prs"]) == (1, 3)
    assert by["mnemo"]["wait_minutes"]["median"] == 2
    assert by["person"]["wait_minutes"]["median"] == 60


def test_a_missing_ledger_credits_nobody_but_a_person(tmp_path) -> None:
    s = _measure([_pr(1)], tmp_path / "no-vault")
    assert (s["merged_by"]["mnemo"]["prs"], s["merged_by"]["person"]["prs"]) == (0, 1)


def test_percentiles_are_linear_between_ranks() -> None:
    values = [0, 10, 20, 30, 40]
    assert tool.percentile(values, 0.5) == 20
    assert tool.percentile(values, 0.75) == 30
    assert tool.percentile(values, 0.9) == pytest.approx(36)
    assert tool.percentile([], 0.5) is None


def test_a_repo_gh_could_not_list_says_so(tmp_path) -> None:
    report = tool.measure([REPO], limit=5, vault=tmp_path, run=_gh([], code=1))
    assert report["repos"][REPO] is None
    assert "could not list" in tool.format_report(report)


def test_it_asks_gh_for_merged_prs_of_that_repo_only(tmp_path) -> None:
    run = _gh([_pr(1)])
    tool.measure([REPO], limit=7, vault=tmp_path, run=run)
    call, = run.calls
    assert call[:3] == ("gh", "pr", "list")
    assert call[call.index("-R") + 1] == REPO
    assert call[call.index("--state") + 1] == "merged"
    assert call[call.index("--limit") + 1] == "7"


def test_the_report_names_the_watcher_and_the_person(tmp_path) -> None:
    url = f"https://github.com/{REPO}/pull/1"
    vault = _vault(tmp_path, {"aaaaaaaa": {"pr": url, "closed": "merged", "merged_by": "mnemo"}})
    report = tool.measure([REPO], limit=100, vault=vault,
                          run=_gh([_pr(1, wait=3), _pr(2, wait=45)]))
    text = tool.format_report(report, listing=True)
    assert "2 of the last 2 merged PRs" in text
    assert "merged by the watcher: 1 — median 3 min" in text
    assert "merged by a person: 1 — median 45 min" in text
    assert f"{REPO}#1" in text and "mnemo" in text


def _gh_by_repo(prs_of: Dict[str, Sequence[Dict[str, Any]]]):
    """A fake ``gh pr list`` answering for each ``-R`` repository; one it
    does not know fails as ``gh`` would."""
    calls: List[Tuple[str, ...]] = []

    def run(argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
        calls.append(tuple(argv))
        repo = argv[list(argv).index("-R") + 1]
        if repo not in prs_of:
            return 1, "", "not found"
        return 0, json.dumps(list(prs_of[repo])), ""

    run.calls = calls  # type: ignore[attr-defined]
    return run


def _in(repo: str, pr: Dict[str, Any]) -> Dict[str, Any]:
    return dict(pr, url=f"https://github.com/{repo}/pull/{pr['number']}")


def test_repo_takes_several_after_one_flag_or_repeated(monkeypatch, tmp_path) -> None:
    seen: List[Sequence[str]] = []
    monkeypatch.setattr(tool.report_card, "_run", _gh_by_repo({}))
    monkeypatch.setattr(tool, "measure", lambda repos, **kw: seen.append(list(repos))
                        or {"prefix": tool.PREFIX, "limit": 1, "repos": {}, "rows": [],
                            "total": None})
    vault = ["--vault", str(tmp_path)]
    assert tool.main(["--repo", "o/a", "o/b"] + vault) == 0
    assert tool.main(["--repo", "o/a", "--repo", "o/b", "o/c"] + vault) == 0
    assert seen == [["o/a", "o/b"], ["o/a", "o/b", "o/c"]]


def test_two_repositories_get_a_block_each_and_a_total(tmp_path) -> None:
    a, b = "o/a", "o/b"
    run = _gh_by_repo({
        a: [_in(a, _pr(1, wait=10)), _in(a, _pr(2, wait=30)),
            _in(a, _pr(3, branch="feature/x"))],
        b: [_in(b, _pr(1, wait=50)), _in(b, _pr(7, wait=-5))],
    })
    report = tool.measure([a, b], limit=100, vault=tmp_path, run=run)
    assert [c[c.index("-R") + 1] for c in run.calls] == [a, b]
    assert report["repos"][a]["wait_minutes"]["n"] == 2
    assert report["repos"][b]["wait_minutes"]["n"] == 1
    total = report["total"]
    assert (total["merged_read"], total["child_prs"], total["repos_read"]) == (5, 4, 2)
    w = total["wait_minutes"]
    assert (w["n"], w["median"], w["max"]) == (3, 30, 50)
    assert total["merged_early"] == ["o/b#7"]
    assert total["numbers"] is None
    text = tool.format_report(report)
    assert text.count("\no/a: ") + text.startswith("o/a: ") == 1
    assert "\no/b: 2 of the last 2 merged PRs" in text
    assert "total over 2 of 2 repositories: 4 of the 5 merged PRs" in text
    assert "merged before the last check completed: 1 (o/b#7)" in text


def test_the_total_counts_only_the_repositories_gh_could_list(tmp_path) -> None:
    a = "o/a"
    report = tool.measure([a, "o/gone"], limit=100, vault=tmp_path,
                          run=_gh_by_repo({a: [_in(a, _pr(1))]}))
    assert report["repos"]["o/gone"] is None
    assert report["total"]["repos_read"] == 1
    text = tool.format_report(report)
    assert "o/gone: gh could not list its merged PRs" in text
    assert "total over 1 of 2 repositories: 1 of the 1 merged PRs" in text


def test_one_repository_has_no_total(tmp_path) -> None:
    report = tool.measure([REPO], limit=100, vault=tmp_path, run=_gh([_pr(1)]))
    assert report["total"] is None
    assert "total over" not in tool.format_report(report)
