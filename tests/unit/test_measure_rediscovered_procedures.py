"""``tools/measure_rediscovered_procedures.py``: the #392 count, over synthetic
transcripts shaped like Claude Code's."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_rediscovered_procedures as tool  # noqa: E402


def _child(projects: Path, repo_root: Path, worktree: str, commands: list) -> None:
    cwd = str(repo_root.parent / worktree)
    records = [{
        "type": "assistant", "cwd": cwd, "timestamp": "2026-09-19T10:00:00Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]},
    } for i, c in enumerate(commands)]
    directory = projects / worktree
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{len(list(directory.iterdir()))}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _population(tmp_path: Path, *, claude_md: str = "") -> Path:
    projects = tmp_path / "projects"
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    if claude_md:
        (repo / "CLAUDE.md").write_text(claude_md, encoding="utf-8")
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}",
               ["cargo test", "SDKROOT=/sdk cargo test", "cargo test -- --nocapture"])
    return projects


def test_the_population_is_counted_per_repo(tmp_path: Path) -> None:
    report = tool.measure(str(_population(tmp_path)))
    assert report["children"] == {"app": 2}
    assert report["transcripts"] == 2


def test_a_candidate_is_reported_with_what_the_file_says(tmp_path: Path) -> None:
    report = tool.measure(str(_population(tmp_path)))
    row, = report["env"]
    assert (row["repo"], row["key"], row["children"]) == ("app", "cargo-test", 2)
    assert row["stated"] is False
    assert row["has_claude_md"] is False


def test_a_repo_that_already_states_it_is_counted_as_stated(tmp_path: Path) -> None:
    report = tool.measure(str(_population(tmp_path, claude_md="Use SDKROOT=/sdk.\n")))
    row, = report["env"]
    assert row["stated"] is True
    assert row["has_claude_md"] is True
    assert "1 already stated" in tool.format_report(report)


def test_the_rejected_half_counts_flags_the_same_bar_would_have_proposed(tmp_path: Path) -> None:
    report = tool.measure(str(_population(tmp_path)))
    flags = {row["key"]: [c["name"] for c in row["carriers"]] for row in report["flag"]}
    assert flags == {"cargo-test": ["--nocapture"]}
    text = tool.format_report(report, rejected=True)
    assert "--nocapture" in text
    assert "rejected" in text


def test_the_report_names_no_flag_unless_it_is_asked_for(tmp_path: Path) -> None:
    assert "--nocapture" not in tool.format_report(tool.measure(str(_population(tmp_path))))


def test_a_bare_run_that_failed_is_told_apart_from_one_that_passed(tmp_path: Path) -> None:
    """The finding the whole design rests on: the runs that matter succeed."""
    projects = tmp_path / "projects"
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    for n, failed in ((1, True), (2, False)):
        cwd = str(repo.parent / f"app-wt-{n}")
        records = [
            {"type": "assistant", "cwd": cwd, "timestamp": "2026-09-19T10:00:00Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "a", "name": "Bash",
                  "input": {"command": "cargo test"}}]}},
            {"type": "user", "cwd": cwd,
             "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "a", "is_error": failed,
                  "content": "out"}]}},
            {"type": "assistant", "cwd": cwd,
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "b", "name": "Bash",
                  "input": {"command": "SDKROOT=/sdk cargo test"}}]}},
        ]
        directory = projects / f"app-wt-{n}"
        directory.mkdir(parents=True)
        (directory / "0.jsonl").write_text(
            "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

    report = tool.measure(str(projects))
    assert report["silence"] == {"bare_runs": 2, "errors": 1}
    assert "2 runs of those shapes were missing one, and 1 of them failed" in \
        tool.format_report(report)


def test_the_cli_prints_the_report(tmp_path: Path, capsys) -> None:
    assert tool.main(["--projects", str(_population(tmp_path))]) == 0
    assert "1 procedure(s) rediscovered" in capsys.readouterr().out


def test_the_cli_can_print_json(tmp_path: Path, capsys) -> None:
    assert tool.main(["--projects", str(_population(tmp_path)), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["children"] == {"app": 2}
