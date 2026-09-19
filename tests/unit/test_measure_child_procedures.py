"""``tools/measure_child_procedures.py``: the #385 count, on transcripts shaped
like Claude Code's.

The classifier decides three things, and each one was wrong at least once
while the tool was being written, so each has a test here:

- what counts as *running* a command (a heredoc that writes a test file
  quoting ``pytest`` is not a pytest run),
- what counts as *carrying* the repo's requirement (an env assignment in
  front, a flag after, an ``export`` earlier — but not the next statement's
  assignment), and
- which of the three verdicts a child lands in.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_child_procedures as tool  # noqa: E402


def _call(uid: str, command: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": uid, "name": "Bash", "input": {"command": command}}]}}


def _result(uid: str, output: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": output}]}}


def _instructions(*files: tuple) -> dict:
    return {"type": "attachment", "attachment": {"type": "instructions", "files": [
        {"type": kind, "path": path} for kind, path in files]}}


def _write(tmp_path: Path, worktree: str, records: list) -> None:
    """One child's transcript, under a project directory named for its cwd."""
    cwd = f"/Users/x/github/{worktree}"
    directory = tmp_path / f"-Users-x-github-{worktree}"
    directory.mkdir(exist_ok=True)
    head = {"type": "user", "cwd": cwd, "timestamp": "2026-09-15T10:00:00Z",
            "message": {"role": "user", "content": "go"}}
    (directory / f"{len(list(directory.iterdir()))}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in [head, *records]) + "\n", encoding="utf-8")


# --- the population --------------------------------------------------------


def test_only_dispatch_worktrees_are_counted(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [_call("t1", "PYTHONPATH=src pytest -q")])
    _write(tmp_path, "mnemo", [_call("t1", "pytest -q")])
    _write(tmp_path, "mnemo-wt-feature", [_call("t1", "pytest -q")])

    report = tool.measure(str(tmp_path))

    assert set(report["repos"]) == {"mnemo"}
    assert report["repos"]["mnemo"]["children"] == 1


def test_a_contract_piece_worktree_counts_under_its_repo(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-c-publish", [_call("t1", "PYTHONPATH=src pytest -q")])

    report = tool.measure(str(tmp_path))

    assert report["repos"]["mnemo"]["probes"]["run the suite"]["children"] == 1


# --- what counts as a run --------------------------------------------------


def test_a_heredoc_that_quotes_the_command_is_not_a_run(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [_call("t1", (
        "python3 - <<'EOF'\n"
        "open('t.py','w').write('run pytest -q here')\n"
        "EOF"))])

    report = tool.measure(str(tmp_path))

    assert report["repos"]["mnemo"]["probes"] == {}


def test_a_run_inside_a_heredoc_body_is_still_not_a_run(tmp_path: Path) -> None:
    """The body is stripped whole — what it says about pytest is text, not a run."""
    _write(tmp_path, "mnemo-wt-197", [_call("t1", (
        "cat >> tests/unit/test_x.py <<'EOF'\n"
        "# run with: pytest -q\n"
        "EOF\n"
        "PYTHONPATH=src python3 -m pytest -q"))])

    probe = tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]

    assert probe["runs"] == 1 and probe["missing"] == 0


# --- what counts as carrying the requirement -------------------------------


def test_an_assignment_in_front_carries_it(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [_call("t1", "PYTHONPATH=src python3 -m pytest -q")])

    assert tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]["missing"] == 0


def test_an_export_earlier_in_the_command_carries_it(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [_call("t1", "export PYTHONPATH=src && python3 -m pytest -q")])

    assert tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]["missing"] == 0


def test_a_flag_after_the_command_carries_it(tmp_path: Path) -> None:
    """clubinho's requirement is a flag, not an env var: it follows the command."""
    _write(tmp_path, "clubinho-wt-181", [_call("t1", "npm test -- --runInBand 2>&1 | tail -8")])

    probes = tool.measure(str(tmp_path))["repos"]["clubinho"]["probes"]
    assert probes["run the suite"]["missing"] == 0
    assert probes["run the suite without running out of heap"]["missing"] == 1


def test_the_next_statements_assignment_does_not_carry_the_previous_run(tmp_path: Path) -> None:
    """The bug this test pins: searching the whole command credits a bare run
    with the fix that came after it."""
    _write(tmp_path, "mnemo-wt-197", [
        _call("t1", "python3 -m pytest -q; PYTHONPATH=src python3 -m pytest -q")])

    probe = tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]

    assert probe["runs"] == 2 and probe["missing"] == 1


def test_a_pipe_does_not_end_the_statement(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [_call("t1", "PYTHONPATH=src pytest -q | tail -5")])

    assert tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]["missing"] == 0


# --- the three verdicts ----------------------------------------------------


def test_a_child_that_always_carried_it_kept(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [
        _call("t1", "PYTHONPATH=src pytest -q"),
        _call("t2", "PYTHONPATH=src pytest -q tests/unit"),
    ])

    probe = tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]

    assert (probe["kept"], probe["rediscovered"], probe["missed"]) == (1, 0, 0)


def test_bare_first_then_fixed_is_rediscovery(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [
        _call("t1", "pytest -q"),
        _call("t2", "PYTHONPATH=src pytest -q"),
    ])

    probe = tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]

    assert (probe["kept"], probe["rediscovered"], probe["missed"]) == (0, 1, 0)


def test_correct_first_then_bare_is_not_rediscovery(tmp_path: Path) -> None:
    """A child that knew, then ran one bare on purpose, did not rediscover
    anything — the #329 child proved the failure that way."""
    _write(tmp_path, "mnemo-wt-197", [
        _call("t1", "PYTHONPATH=src pytest -q"),
        _call("t2", "pytest -q"),
    ])

    probe = tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]

    assert (probe["kept"], probe["rediscovered"], probe["missed"]) == (1, 0, 0)


def test_a_child_that_never_carried_it_missed(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [_call("t1", "pytest -q"), _call("t2", "pytest -q -x")])

    probe = tool.measure(str(tmp_path))["repos"]["mnemo"]["probes"]["run the suite"]

    assert (probe["kept"], probe["rediscovered"], probe["missed"]) == (0, 0, 1)
    assert probe["runs"] == 2 and probe["missing"] == 2


# --- channels and failures -------------------------------------------------


def test_the_channels_a_child_was_given_are_read_off_its_attachments(tmp_path: Path) -> None:
    _write(tmp_path, "clubinho-wt-181", [
        _instructions(("Project", "/Users/x/github/clubinho-wt-181/CLAUDE.md"),
                      ("AutoMem", "/Users/x/.claude/projects/-x-clubinho/memory/MEMORY.md")),
    ])
    _write(tmp_path, "mnemo-wt-197", [
        _instructions(("AutoMem", "/Users/x/.claude/projects/-x-mnemo/memory/MEMORY.md")),
    ])

    repos = tool.measure(str(tmp_path))["repos"]

    assert (repos["clubinho"]["claude_md"], repos["clubinho"]["auto_mem"]) == (1, 1)
    assert (repos["mnemo"]["claude_md"], repos["mnemo"]["auto_mem"]) == (0, 1)


def test_a_setup_failure_is_counted_once_per_child(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-desktop-wt-103", [
        _call("t1", "pnpm test"), _result("t1", "Error: Cannot find module 'vitest'"),
        _call("t2", "pnpm test"), _result("t2", "Error: Cannot find module 'react'"),
    ])

    failures = tool.measure(str(tmp_path))["repos"]["mnemo-desktop"]["failures"]

    assert failures == {"worktree has no node_modules": 1}


def test_a_transcript_with_no_probe_of_its_own_still_counts_as_a_child(tmp_path: Path) -> None:
    """A repo with no probes declared is still a population — its channels and
    the failures it hit are the measurement."""
    _write(tmp_path, "clearframe-wt-82", [
        _call("t1", "npm run build"), _result("t1", "Cannot find module 'vite'")])

    bucket = tool.measure(str(tmp_path))["repos"]["clearframe"]

    assert bucket["children"] == 1 and bucket["probes"] == {}
    assert bucket["failures"] == {"worktree has no node_modules": 1}


# --- the report ------------------------------------------------------------


def test_the_listing_prints_every_missing_run_and_why_the_probe_exists(tmp_path: Path) -> None:
    _write(tmp_path, "mnemo-wt-197", [_call("t1", "pytest -q tests/unit")])

    text = tool.format_report(tool.measure(str(tmp_path)), listing=True)

    assert "pytest -q tests/unit" in text
    assert "imports master's `src`" in text


def test_malformed_events_do_not_raise(tmp_path: Path) -> None:
    directory = tmp_path / "-Users-x-github-mnemo-wt-197"
    directory.mkdir()
    (directory / "a.jsonl").write_text(
        "not json\n"
        + json.dumps({"type": "user", "cwd": "/Users/x/github/mnemo-wt-197"}) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": "a string"}}) + "\n"
        + json.dumps({"type": "assistant", "message": {"content": [None, {"type": "tool_use"}]}}) + "\n"
        + json.dumps([1, 2, 3]) + "\n",
        encoding="utf-8")

    assert tool.measure(str(tmp_path))["repos"]["mnemo"]["children"] == 1
