"""A second recall harness, built from the sessions rules were extracted from.

The telemetry harness in ``core.mcp.recall`` is capped by something no setting
can raise: it needs a ``read_mnemo_rule`` to prove a rule was wanted, and the
whole access log holds 71 of them. 56 cases is not enough to tell a real
ranking improvement from noise, and more only arrive with more use.

Every rule already records the sessions it was extracted from, which is ground
truth by construction, and the transcripts of those sessions hold the prompts
that produced the work. That is a query shaped exactly like what reflex scores
at ``UserPromptSubmit``, against an answer nobody had to label.

Two properties this must keep, because they are what make the harness honest:

- it never replaces the telemetry number, which stays the absolute measure;
- a query drawn from the *prompt* leaks far less than one drawn from the
  briefing, because the prompt did not author the rule's text — the work did.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mnemo.core.mcp import recall_sessions as rs


@pytest.fixture
def env(tmp_path: Path):
    """A vault with two rules from one session, plus that session's transcript."""
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    (vault / "bots" / "alpha" / "briefings" / "sessions").mkdir(parents=True)
    projects = tmp_path / "projects" / "-Users-me-alpha"
    projects.mkdir(parents=True)

    sid = "11111111-2222-3333-4444-555555555555"
    (vault / "bots" / "alpha" / "briefings" / "sessions" / f"{sid}.md").write_text(
        "# briefing\n", encoding="utf-8"
    )
    for slug, desc in (("prisma-mock", "Mock Prisma with jest-mock-extended"),
                       ("yarn-canonical", "Use yarn, never npm")):
        (vault / "shared" / "feedback" / f"{slug}.md").write_text(
            f"---\nname: {slug}\ndescription: {desc}\ntype: feedback\n"
            f"sources:\n  - bots/alpha/briefings/sessions/{sid}.md\n"
            f"stability: stable\n---\n{desc}.\n",
            encoding="utf-8",
        )
    return vault, projects, sid


def _transcript(projects: Path, sid: str, prompts: list[str]) -> Path:
    path = projects / f"{sid}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for p in prompts:
            fh.write(json.dumps({"type": "user", "message": {"content": p}}) + "\n")
    return path


# --- building cases --------------------------------------------------------

def test_a_session_with_a_transcript_becomes_one_case(env):
    vault, projects, sid = env
    _transcript(projects, sid, ["How should I mock the prisma client in tests here?"])

    cases = rs.bootstrap_cases(vault, projects_root=projects.parent)

    assert len(cases) == 1
    case = cases[0]
    assert case["project"] == "alpha"
    assert set(case["expect_slugs"]) == {"prisma-mock", "yarn-canonical"}
    assert "prisma" in case["prompt"].lower()


def test_every_rule_from_the_session_is_expected(env):
    """Multi-label on purpose: ranking is about getting all of them up, not one."""
    vault, projects, sid = env
    _transcript(projects, sid, ["How should I mock the prisma client in tests here?"])

    assert len(rs.bootstrap_cases(vault, projects_root=projects.parent)[0]["expect_slugs"]) == 2


def test_a_session_with_no_transcript_on_disk_is_skipped(env):
    """18% of this vault's sessions still have one; the rest cannot be queried."""
    vault, projects, _sid = env

    assert rs.bootstrap_cases(vault, projects_root=projects.parent) == []


def test_the_first_substantive_prompt_is_the_query(env):
    """A session's opening prompt states the intent that produced the work.

    Later prompts drift into the middle of the task and would be scored against
    rules the work had not reached yet — a false negative manufactured by the
    harness rather than a retrieval defect.
    """
    vault, projects, sid = env
    _transcript(projects, sid, [
        "ok",                                        # too short to rank on
        "How should I mock the prisma client in tests here?",
        "now fix the yarn lockfile as well",
    ])

    assert rs.bootstrap_cases(vault, projects_root=projects.parent)[0]["prompt"] == (
        "How should I mock the prisma client in tests here?"
    )


def test_a_session_whose_prompts_are_all_too_short_is_skipped(env):
    vault, projects, sid = env
    _transcript(projects, sid, ["ok", "yes", "go"])

    assert rs.bootstrap_cases(vault, projects_root=projects.parent) == []


def test_command_output_is_not_treated_as_a_prompt(env):
    """Slash-command stdout is replayed as a `user` turn and is not a query."""
    vault, projects, sid = env
    _transcript(projects, sid, [
        "<local-command-stdout>a wall of command output nobody typed</local-command-stdout>",
        "How should I mock the prisma client in tests here?",
    ])

    assert "command-stdout" not in rs.bootstrap_cases(
        vault, projects_root=projects.parent
    )[0]["prompt"]


@pytest.mark.parametrize("synthetic", [
    "<task-notification><task-id>abc</task-id>a background task finished</task-notification>",
    "<system-reminder>your memory directory contains the following</system-reminder>",
    "<command-name>/clear</command-name> and some more text after it",
    "Caveat: The messages below were generated by the user while running local commands.",
])
def test_machine_written_user_turns_are_not_prompts(env, synthetic):
    """Several things arrive as `user` turns that no human typed.

    Background-task notifications in particular are long, vocabulary-rich and
    land at the top of a session — exactly the shape that silently becomes the
    query for a whole case and measures retrieval against text the author never
    wrote.
    """
    vault, projects, sid = env
    _transcript(projects, sid, [
        synthetic,
        "How should I mock the prisma client in tests here?",
    ])

    cases = rs.bootstrap_cases(vault, projects_root=projects.parent)
    assert cases[0]["prompt"] == "How should I mock the prisma client in tests here?"


def test_tool_results_are_not_treated_as_prompts(env):
    """A `user` turn carrying tool_result blocks is the transcript's plumbing."""
    vault, projects, sid = env
    path = projects / f"{sid}.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "file contents here, at length"}]}}) + "\n")
        fh.write(json.dumps({"type": "user", "message": {
            "content": "How should I mock the prisma client in tests here?"}}) + "\n")

    cases = rs.bootstrap_cases(vault, projects_root=projects.parent)
    assert cases and "tool_result" not in cases[0]["prompt"]


def test_a_rule_whose_source_file_is_gone_contributes_nothing(env):
    vault, projects, sid = env
    _transcript(projects, sid, ["How should I mock the prisma client in tests here?"])
    (vault / "bots" / "alpha" / "briefings" / "sessions" / f"{sid}.md").unlink()

    assert rs.bootstrap_cases(vault, projects_root=projects.parent) == []


# --- scoring ---------------------------------------------------------------

def _index(vault: Path) -> None:
    from mnemo.core.reflex.index import build_index, write_index

    write_index(vault, build_index(vault, universal_threshold=1))


def test_a_case_records_the_rank_of_each_expected_rule(env):
    vault, projects, sid = env
    _transcript(projects, sid, ["How should I mock the prisma client in tests here?"])
    _index(vault)
    case = rs.bootstrap_cases(vault, projects_root=projects.parent)[0]

    result = rs.run_case(vault, case)

    assert set(result["ranks"]) == {"prisma-mock", "yarn-canonical"}
    assert result["best_rank"] == min(
        r for r in result["ranks"].values() if r is not None
    )


def test_an_expected_rule_that_never_appears_ranks_as_none(env):
    vault, projects, sid = env
    _transcript(projects, sid, ["zzz qqq xxx unrelated vocabulary entirely"])
    _index(vault)
    case = rs.bootstrap_cases(vault, projects_root=projects.parent)[0]

    result = rs.run_case(vault, case)
    assert all(r is None or r > 0 for r in result["ranks"].values())


# --- aggregation -----------------------------------------------------------

def _result(best, ranks, n=2):
    return {"id": "x", "project": "alpha", "best_rank": best, "ranks": ranks,
            "expected": n, "candidate_count": 10, "elapsed_ms": 1.0}


def test_any_at_k_counts_sessions_with_at_least_one_hit():
    rep = rs.aggregate([
        _result(1, {"a": 1, "b": 40}),
        _result(12, {"a": 12, "b": None}),
    ])
    assert rep["any_at_5"] == 1
    assert rep["cases"] == 2


def test_recall_at_k_counts_expected_rules_not_sessions():
    """The metric ranking work has to move: how many of them surface, not whether one did."""
    rep = rs.aggregate([_result(1, {"a": 1, "b": 3}), _result(2, {"a": 2, "b": None})])

    assert rep["recall_at_5"] == pytest.approx(0.75)  # 3 of 4 expected rules


def test_an_empty_run_aggregates_without_dividing_by_zero():
    rep = rs.aggregate([])
    assert rep["cases"] == 0 and rep["recall_at_5"] == 0.0


def test_the_report_names_the_harness_so_it_is_never_read_as_the_other_one():
    """Absolute numbers here are inflated by leakage; only deltas are meaningful."""
    rep = rs.aggregate([_result(1, {"a": 1})])
    assert rep["harness"] == "sessions"


# --- command wiring --------------------------------------------------------

def test_the_command_is_registered():
    from mnemo.cli.commands import recall_sessions as cmd
    from mnemo.cli.parser import COMMANDS

    assert COMMANDS.get("recall-sessions") is cmd.cmd_recall_sessions


def test_the_parser_accepts_the_command():
    from mnemo.cli.parser import _build_parser

    assert _build_parser().parse_args(["recall-sessions", "--json"]).json is True


def test_a_vault_with_no_reflex_index_exits_nonzero(tmp_path, monkeypatch, capsys):
    """Scoring against nothing would report 0% and read as a ranking collapse."""
    from mnemo.cli.commands import recall_sessions as cmd

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)

    assert cmd.cmd_recall_sessions(argparse.Namespace(json=False)) == 2
    assert "no reflex index" in capsys.readouterr().out
