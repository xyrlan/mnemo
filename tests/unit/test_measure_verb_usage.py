"""``tools/measure_verb_usage.py``: the #437 verb-call count, on transcripts
shaped like Claude Code's.

Every number this tool prints is the evidence for what `mnemo --help`
should lead with, so what counts as a call is pinned here: which channel
counts, which projects are excluded, and which commands are thrown out
before matching because their prose — not their argv — would otherwise
match.
"""
from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_verb_usage as tool  # noqa: E402


def _transcript(tmp_path: Path, *, uuid: str, cwd: str, at: str,
                 prompt: str = "hi", bash=(), bash_input=()) -> None:
    # Encoded the way Claude Code names its project dirs: every character that
    # is not a letter, digit or hyphen becomes "-". A raw Windows cwd
    # (`C:\\...`) is absolute, and pathlib would drop `projects/` for it.
    project = tmp_path / "projects" / re.sub(r"[^A-Za-z0-9-]", "-", cwd)
    project.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "user", "cwd": cwd, "timestamp": at,
         "message": {"role": "user", "content": prompt}},
    ]
    for command in bash:
        records.append({"type": "assistant", "cwd": cwd, "timestamp": at,
                        "message": {"role": "assistant", "content": [
                            {"type": "tool_use", "name": "Bash",
                             "input": {"command": command}}]}})
    for command in bash_input:
        records.append({"type": "user", "cwd": cwd, "timestamp": at,
                        "message": {"role": "user",
                                    "content": f"<bash-input>{command}</bash-input>"}})
    (project / f"{uuid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _measure(tmp_path: Path, **kwargs) -> dict:
    return tool.measure(str(tmp_path / "projects"), **kwargs)


def test_a_bash_tool_use_running_mnemo_counts_as_one_call(tmp_path) -> None:
    _transcript(tmp_path, uuid="a1", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash=["mnemo dispatch 12 13"])

    report = _measure(tmp_path, since_days=None)

    assert report["counted"] == 1
    verbs = {row["verb"]: row for row in report["verbs"]}
    assert verbs["dispatch"]["calls"] == 1
    assert verbs["dispatch"]["tool"] == 1
    assert verbs["dispatch"]["typed"] == 0


def test_a_bash_input_running_mnemo_counts_as_a_typed_call(tmp_path) -> None:
    _transcript(tmp_path, uuid="a1", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash_input=["mnemo status"])

    report = _measure(tmp_path, since_days=None)

    verbs = {row["verb"]: row for row in report["verbs"]}
    assert verbs["status"]["typed"] == 1
    assert verbs["status"]["tool"] == 0
    assert report["typed_calls"] == 1
    assert report["tool_calls"] == 0


def test_a_project_whose_cwd_contains_mnemo_is_excluded(tmp_path) -> None:
    _transcript(tmp_path, uuid="a1", cwd="/Users/x/github/mnemo", at="2026-09-01T10:00:00Z",
               bash=["mnemo dispatch 1"])
    _transcript(tmp_path, uuid="a2", cwd="/Users/x/github/mnemo-desktop", at="2026-09-01T10:00:00Z",
               bash=["mnemo dispatch 1"])
    _transcript(tmp_path, uuid="a3", cwd="/Users/x/github/mnemo-wt-437", at="2026-09-01T10:00:00Z",
               bash=["mnemo dispatch 1"])

    report = _measure(tmp_path, since_days=None)

    assert report["counted"] == 0
    assert report["excluded_mnemo_repos"] == 3
    assert report["verbs"] == []


def test_a_throwaway_cwd_is_excluded(tmp_path) -> None:
    # The platform's own temp dir: `/tmp` is a temp root on POSIX only, so a
    # literal `/tmp/probe-1` is an ordinary project on Windows.
    probe = str(Path(tempfile.gettempdir()) / "probe-1")
    _transcript(tmp_path, uuid="a1", cwd=probe, at="2026-09-01T10:00:00Z",
               bash=["mnemo dispatch 1"])

    report = _measure(tmp_path, since_days=None)

    assert report["transcripts"] == 1
    assert report["counted"] == 0
    assert report["excluded_temp"] == 1


def test_a_command_mentioning_mnemo_desktop_does_not_count_as_dispatch(tmp_path) -> None:
    """`mnemo-desktop` and `mnemo` share a prefix; the anchored regex must
    not read the hyphen as a space."""
    _transcript(tmp_path, uuid="a1", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash=["cd mnemo-desktop && cargo build"])

    report = _measure(tmp_path, since_days=None)

    assert report["verbs"] == []


def test_a_git_commit_mentioning_a_verb_is_excluded_entirely(tmp_path) -> None:
    """Mnemo's own commit history says things like 'run `mnemo doctor`
    first' — prose, not argv. The whole command is thrown out, not just
    de-duplicated, so a genuine call riding in the same string is lost too
    (there is none in practice: nobody chains a real mnemo call onto a
    commit)."""
    _transcript(tmp_path, uuid="a1", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash=['git commit -m "docs: tell people to run mnemo doctor first"'])
    _transcript(tmp_path, uuid="a2", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash=["gh pr create --body 'see mnemo dispatch docs'"])
    _transcript(tmp_path, uuid="a3", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash=["gh issue comment 1 --body 'mnemo sessions shows it'"])

    report = _measure(tmp_path, since_days=None)

    assert report["verbs"] == []


def test_calls_outside_the_window_are_not_counted(tmp_path) -> None:
    _transcript(tmp_path, uuid="old", cwd="/r/app", at="2026-01-01T10:00:00Z",
               bash=["mnemo dispatch 1"])
    _transcript(tmp_path, uuid="new", cwd="/r/app", at="2026-09-20T10:00:00Z",
               bash=["mnemo sessions"])

    report = _measure(tmp_path, since_days=30.0)

    verbs = {row["verb"] for row in report["verbs"]}
    assert verbs == {"sessions"}


def test_two_verbs_in_one_command_both_count(tmp_path) -> None:
    _transcript(tmp_path, uuid="a1", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash=["mnemo dispatch 1 2 && mnemo sessions"])

    report = _measure(tmp_path, since_days=None)

    verbs = {row["verb"] for row in report["verbs"]}
    assert verbs == {"dispatch", "sessions"}


def test_verbs_are_ranked_by_call_count_descending(tmp_path) -> None:
    _transcript(tmp_path, uuid="a1", cwd="/r/app", at="2026-09-01T10:00:00Z",
               bash=["mnemo dispatch 1", "mnemo dispatch 2", "mnemo sessions"])

    report = _measure(tmp_path, since_days=None)

    assert [row["verb"] for row in report["verbs"]] == ["dispatch", "sessions"]
    assert report["verbs"][0]["calls"] == 2


def test_format_report_names_the_top_verb() -> None:
    report = {
        "transcripts": 5, "excluded_mnemo_repos": 2, "excluded_temp": 0,
        "counted": 3, "since_days": 30.0,
        "verbs": [
            {"verb": "dispatch", "calls": 51, "tool": 51, "typed": 0},
            {"verb": "sessions", "calls": 24, "tool": 24, "typed": 0},
        ],
        "typed_calls": 0, "tool_calls": 75,
    }
    out = tool.format_report(report)
    assert "dispatch" in out
    assert "51" in out
