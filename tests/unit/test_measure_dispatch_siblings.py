"""``tools/measure_dispatch_siblings.py``: the #384 count, on transcripts shaped
like Claude Code's, against a vault holding a real ``dispatch-parents.jsonl``.

Every number this tool prints is an argument for or against shipping the
roster, so the classifications those numbers rest on are pinned here: what
counts as one batch, what counts as a collision, and which children are
excluded from the reach rate and why.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_dispatch_siblings as tool  # noqa: E402


def _child(tmp_path: Path, *, uuid: str, cwd: str, at: str, prompt: str,
           edits=(), bash=()) -> None:
    project = tmp_path / "projects" / cwd.strip("/").replace("/", "-")
    project.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "user", "cwd": cwd, "timestamp": at,
         "message": {"role": "user", "content": prompt}},
    ]
    for path in edits:
        records.append({"type": "assistant", "cwd": cwd, "timestamp": at,
                        "message": {"role": "assistant", "content": [
                            {"type": "tool_use", "name": "Edit",
                             "input": {"file_path": path}}]}})
    for command in bash:
        records.append({"type": "assistant", "cwd": cwd, "timestamp": at,
                        "message": {"role": "assistant", "content": [
                            {"type": "tool_use", "name": "Bash",
                             "input": {"command": command}}]}})
    (project / f"{uuid}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def _vault(tmp_path: Path, pairs: dict) -> Path:
    root = tmp_path / "vault" / ".mnemo"
    root.mkdir(parents=True, exist_ok=True)
    (root / "dispatch-parents.jsonl").write_text(
        "\n".join(json.dumps({"short_id": s, "parent_session": p})
                  for s, p in pairs.items()) + "\n", encoding="utf-8")
    return tmp_path / "vault"


def _measure(tmp_path: Path, **kwargs) -> dict:
    return tool.measure(str(tmp_path / "projects"), str(tmp_path / "vault"), **kwargs)


def test_two_children_of_one_parent_writing_one_file_is_a_collision(tmp_path) -> None:
    for short_id, issue in (("aaaa0001", 1), ("aaaa0002", 2)):
        _child(tmp_path, uuid=f"{short_id}-x", cwd=f"/r/app-wt-{issue}",
               at="2026-09-19T10:00:00Z", prompt=f"Work on issue #{issue} in this repo: t",
               edits=[f"/r/app-wt-{issue}/src/shared.py",
                      f"/r/app-wt-{issue}/src/own{issue}.py"])
    _vault(tmp_path, {"aaaa0001": "p", "aaaa0002": "p"})

    report = _measure(tmp_path)

    assert report["by_kind"]["issue"]["with_a_collision"] == 1
    assert report["rows"][0]["collisions"] == {
        "src/shared.py": ["app-wt-1", "app-wt-2"]
    }


def test_the_same_path_in_two_worktrees_is_one_file(tmp_path) -> None:
    """Children never share a tree, so an absolute path is always a different
    string; a comparison that did not strip the worktree would find nothing."""
    assert tool._relative("/r/app-wt-1/src/a.py") == "src/a.py"
    assert tool._relative("/r/app-wt-c-parser/src/a.py") == "src/a.py"
    assert tool._relative("/elsewhere/src/a.py") == "/elsewhere/src/a.py"


def test_dispatches_hours_apart_are_two_batches(tmp_path) -> None:
    """A child in the morning batch has no sibling in the afternoon one: the
    roster is what was live while it ran, not what that session ever started."""
    _child(tmp_path, uuid="aaaa0001-x", cwd="/r/app-wt-1", at="2026-09-19T10:00:00Z",
           prompt="Work on issue #1 in this repo: t", edits=["/r/app-wt-1/src/a.py"])
    _child(tmp_path, uuid="aaaa0002-x", cwd="/r/app-wt-2", at="2026-09-19T16:00:00Z",
           prompt="Work on issue #2 in this repo: t", edits=["/r/app-wt-2/src/a.py"])
    _vault(tmp_path, {"aaaa0001": "p", "aaaa0002": "p"})

    report = _measure(tmp_path)

    assert report["batches"] == 2
    assert report["multi_child_batches"] == 0
    assert report["by_kind"]["issue"]["with_a_collision"] == 0


def test_children_of_different_parents_are_never_one_batch(tmp_path) -> None:
    _child(tmp_path, uuid="aaaa0001-x", cwd="/r/app-wt-1", at="2026-09-19T10:00:00Z",
           prompt="Work on issue #1 in this repo: t", edits=["/r/app-wt-1/src/a.py"])
    _child(tmp_path, uuid="aaaa0002-x", cwd="/r/app-wt-2", at="2026-09-19T10:01:00Z",
           prompt="Work on issue #2 in this repo: t", edits=["/r/app-wt-2/src/a.py"])
    _vault(tmp_path, {"aaaa0001": "one", "aaaa0002": "two"})

    assert _measure(tmp_path)["multi_child_batches"] == 0


def test_a_contract_piece_is_counted_apart_from_an_issue_child(tmp_path) -> None:
    """The split is the argument: pieces are already told what the others own."""
    _child(tmp_path, uuid="aaaa0001-x", cwd="/r/app-wt-c-one", at="2026-09-19T10:00:00Z",
           prompt="You are building one piece", edits=["/r/app-wt-c-one/src/a.py"])
    _child(tmp_path, uuid="aaaa0002-x", cwd="/r/app-wt-c-two", at="2026-09-19T10:01:00Z",
           prompt="You are building one piece", edits=["/r/app-wt-c-two/src/a.py"])
    _vault(tmp_path, {"aaaa0001": "p", "aaaa0002": "p"})

    report = _measure(tmp_path)

    assert report["by_kind"]["piece"]["with_a_collision"] == 1
    assert report["by_kind"]["issue"]["batches"] == 0


def test_reading_the_issue_is_counted_from_the_command(tmp_path) -> None:
    _child(tmp_path, uuid="aaaa0001-x", cwd="/r/app-wt-1", at="2026-09-19T10:00:00Z",
           prompt="Work on issue #1 in this repo: t",
           bash=["gh issue view 1 --comments"])
    _child(tmp_path, uuid="aaaa0002-x", cwd="/r/app-wt-2", at="2026-09-19T10:01:00Z",
           prompt="Work on issue #2 in this repo: t", bash=["pytest -q"])
    _vault(tmp_path, {"aaaa0001": "p", "aaaa0002": "p"})

    report = _measure(tmp_path)

    assert report["issue_children"] == 2
    assert report["issue_children_that_read_their_issue"] == 1


def test_a_child_whose_prompt_never_arrived_is_not_scored(tmp_path) -> None:
    """`mnemo-wt-361`'s only user turn is the literal `--setting-sources` — the
    argv bug #371 fixed. Nothing told it to read its issue, so counting it as a
    child that ignored one would score a different bug."""
    _child(tmp_path, uuid="aaaa0001-x", cwd="/r/app-wt-1", at="2026-09-19T10:00:00Z",
           prompt="Work on issue #1 in this repo: t", bash=["gh issue view 1"])
    _child(tmp_path, uuid="aaaa0002-x", cwd="/r/app-wt-2", at="2026-09-19T10:01:00Z",
           prompt="--setting-sources")
    _vault(tmp_path, {"aaaa0001": "p", "aaaa0002": "p"})

    report = _measure(tmp_path)

    assert report["issue_children_never_prompted"] == 1
    assert report["issue_children_that_read_their_issue"] == 1
    assert "1/1" in tool.format_report(report)


def test_a_child_with_no_transcript_is_simply_absent(tmp_path) -> None:
    """The log outlives the job (#288), so most of it names children Claude
    Code has already pruned; a missing transcript is not an error."""
    _child(tmp_path, uuid="aaaa0001-x", cwd="/r/app-wt-1", at="2026-09-19T10:00:00Z",
           prompt="Work on issue #1 in this repo: t")
    _vault(tmp_path, {"aaaa0001": "p", "deadbeef": "p"})

    assert _measure(tmp_path)["children"] == 1
