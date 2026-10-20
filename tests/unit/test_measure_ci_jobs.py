"""``tools/measure_ci_jobs.py`` over synthetic ``gh`` output (#739)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_ci_jobs as tool  # noqa: E402


def _job(name, started, completed, conclusion="success"):
    return {"name": name, "startedAt": started, "completedAt": completed, "conclusion": conclusion}


def _run(run_id, created, *jobs):
    return {"databaseId": run_id, "createdAt": created, "event": "pull_request",
            "headBranch": "b", "conclusion": "success", "jobs": list(jobs)}


def test_queue_runs_from_creation_and_run_time_from_start():
    run = _run(1, "2026-10-20T10:00:00Z",
               _job("windows", "2026-10-20T10:00:04Z", "2026-10-20T10:13:24Z"))
    assert tool.job_times(run) == [
        {"job": "windows", "conclusion": "success", "queue": 4.0, "run": 800.0}]


def test_a_failed_or_unfinished_job_is_left_out_unless_asked_for():
    run = _run(1, "2026-10-20T10:00:00Z",
               _job("ok", "2026-10-20T10:00:01Z", "2026-10-20T10:01:01Z"),
               _job("red", "2026-10-20T10:00:01Z", "2026-10-20T10:00:31Z", "failure"),
               {"name": "never", "startedAt": None, "completedAt": None, "conclusion": "skipped"})
    assert [t["job"] for t in tool.job_times(run)] == ["ok"]
    assert [t["job"] for t in tool.job_times(run, include_failed=True)] == ["ok", "red"]


def test_the_summary_ranks_jobs_by_median_and_the_wall_is_the_slowest_job():
    runs = [
        _run(1, "2026-10-20T10:00:00Z",
             _job("windows", "2026-10-20T10:00:02Z", "2026-10-20T10:13:22Z"),   # 800
             _job("ubuntu", "2026-10-20T10:00:02Z", "2026-10-20T10:05:02Z")),   # 300
        _run(2, "2026-10-20T11:00:00Z",
             _job("windows", "2026-10-20T11:00:10Z", "2026-10-20T11:14:10Z"),   # 840
             _job("ubuntu", "2026-10-20T11:00:02Z", "2026-10-20T11:04:02Z")),   # 240
        _run(3, "2026-10-20T12:00:00Z",
             _job("windows", "2026-10-20T12:00:02Z", "2026-10-20T12:12:42Z"),   # 760
             _job("ubuntu", "2026-10-20T12:15:00Z", "2026-10-20T12:20:00Z")),   # queued 900
    ]
    summary = tool.summarize(runs)

    assert [j["job"] for j in summary["jobs"]] == ["windows", "ubuntu"]
    win = summary["jobs"][0]
    assert (win["samples"], win["run_median"], win["run_min"], win["run_max"]) == (3, 800, 760, 840)
    assert summary["jobs"][1]["queue_median"] == 2
    # Run 3's wall is its queued ubuntu job, not its slowest-running one.
    assert summary["wall"] == {"samples": 3, "median": 850, "min": 802, "max": 1200}


def test_a_run_missing_a_job_gives_no_wall_time():
    runs = [_run(1, "2026-10-20T10:00:00Z",
                 _job("ubuntu", "2026-10-20T10:00:02Z", "2026-10-20T10:05:02Z"),
                 _job("windows", "2026-10-20T10:00:02Z", "2026-10-20T10:25:02Z", "cancelled"))]
    summary = tool.summarize(runs)
    assert [j["job"] for j in summary["jobs"]] == ["ubuntu"]
    assert summary["wall"] is None


def test_fetch_asks_gh_for_completed_runs_then_each_run_s_jobs():
    calls = []
    job = _job("ubuntu", "2026-10-20T10:00:02Z", "2026-10-20T10:05:02Z")

    def gh(args):
        calls.append(list(args))
        if args[:2] == ["run", "list"]:
            return json.dumps([{"databaseId": 7, "createdAt": "2026-10-20T10:00:00Z"}])
        return json.dumps({"jobs": [job]})

    runs = tool.fetch("CI", limit=3, branch="ci/x", event="pull_request", gh=gh)

    assert runs == [{"databaseId": 7, "createdAt": "2026-10-20T10:00:00Z", "jobs": [job]}]
    assert calls[0][:8] == ["run", "list", "--workflow", "CI", "--status", "completed",
                            "--limit", "3"]
    assert calls[0][-4:] == ["--branch", "ci/x", "--event", "pull_request"]
    assert calls[1] == ["run", "view", "7", "--json", "jobs"]


def test_a_saved_file_reports_again_offline(tmp_path, capsys):
    saved = tmp_path / "runs.json"
    saved.write_text(json.dumps([_run(1, "2026-10-20T10:00:00Z",
                                      _job("ubuntu", "2026-10-20T10:00:02Z",
                                           "2026-10-20T10:05:02Z"))]), encoding="utf-8")
    assert tool.main(["--from", str(saved), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["jobs"][0]["run_median"] == 300
    assert "provenance" in report
