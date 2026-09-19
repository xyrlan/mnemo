"""``tools/measure_procedure_offer.py``: what the #397 block costs, over a
synthetic vault and synthetic transcripts.

The tool exists because this repo's standing rule is that a measured claim
ships with the thing that measured it, and the claim in question is in the PR
body and in a docstring: so many bytes on the prompt, so many milliseconds on
the hook. These pin the two ways that measurement could quietly become a lie —
by rendering something other than what a session receives, and by writing to
the ledger it is supposed to be reading.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_procedure_offer as tool  # noqa: E402

from mnemo.core import procedures as P  # noqa: E402


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


@pytest.fixture
def world(tmp_path: Path, tmp_home) -> tuple:
    """A vault, a repo, and two children of it that paid for the same line."""
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    projects = tmp_path / "projects"
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}", ["cargo test", "SDKROOT=/sdk cargo test"])
    P.write_cache(vault, P.scan(str(projects)), projects=str(projects))
    return vault, projects, repo


def test_it_reports_the_block_a_session_would_receive(world) -> None:
    vault, projects, _repo = world

    report = tool.measure(str(vault), projects=str(projects), repeats=2)

    assert report["cached"] == 1
    [block] = report["blocks"]
    assert block["repo"] == "app"
    assert "mnemo procedures --accept cargo-test" in block["block"]
    assert block["block"].startswith("[mnemo procedure candidate — repo=app")
    assert block["block"].splitlines()[-1] == "[/mnemo procedures]"


def test_tokens_are_bytes_over_four_which_is_what_the_repo_already_quotes(world) -> None:
    vault, _projects, _repo = world

    [block] = tool.measure(str(vault), repeats=2)["blocks"]

    assert block["bytes"] == len(block["block"].encode("utf-8"))
    assert block["tokens"] == round(block["bytes"] / 4.0)


def test_measuring_writes_no_ledger_row(world) -> None:
    """A measurement that marked a candidate offered would spend the very
    thing it is measuring, and `--stats` would then count the tool's runs."""
    vault, projects, _repo = world

    tool.measure(str(vault), projects=str(projects), repeats=2)

    assert P.ledger_rows(vault) == []
    # …and the real writer is put back, so a measurement is not a silencer.
    P.record(vault, event=P.OFFERED, repo="app", key="cargo-test")
    assert len(P.ledger_rows(vault)) == 1


def test_it_separates_what_the_hook_pays_from_what_the_scan_costs(world) -> None:
    """The whole design rests on that split, so the report has to show it."""
    vault, projects, _repo = world

    report = tool.measure(str(vault), projects=str(projects), repeats=2)

    hook = report["hook_ms"]
    assert hook["added"] == pytest.approx(
        hook["arbitrated_offer"] - hook["staged_offer_only"], abs=0.001
    )
    assert report["scan"]["candidates"] == 1
    assert report["scan"]["seconds"] >= 0


def test_a_vault_with_nothing_cached_is_a_report_not_a_crash(tmp_path: Path, tmp_home) -> None:
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)

    report = tool.measure(str(vault), repeats=2)

    assert report["blocks"] == [] and report["generated_at"] is None
    assert "no repo has an undecided candidate" in tool.format_report(report)


def test_the_report_reads_as_a_number_somebody_can_act_on(world) -> None:
    vault, projects, _repo = world

    text = tool.format_report(tool.measure(str(vault), projects=str(projects), repeats=2))

    assert "bytes" in text and "tokens" in text
    assert "ms per session start" in text
    assert "detached, never on the session-start path" in text


def test_main_runs_and_can_print_json(world, capsys) -> None:
    vault, projects, _repo = world

    assert tool.main(["--vault", str(vault), "--projects", str(projects),
                      "--repeats", "2", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["cached"] == 1
