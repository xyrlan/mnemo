"""``mnemo learn`` — the CLI surface of the five-minute loop.

The verb's whole job is to make one session's teaching visible, so what it
prints *is* the feature: a `learned:` line with the slug, the name, and the
quote the user actually said. These tests pin those lines exactly. The core
run is stubbed — :mod:`tests.unit.test_learn` covers the pipeline; here we
only care that a :class:`LearnReport` becomes the right stdout and exit code.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mnemo.cli.commands import learn as cmd_mod
from mnemo.core import learn as learn_mod
from mnemo.core.learn import LearnReport


@pytest.fixture(autouse=True)
def _stub_vault(tmp_path, monkeypatch):
    """Every case runs against a tmp vault; no config, no real transcripts."""
    from mnemo import cli as cli_mod
    from mnemo.core import config as cfg_mod

    monkeypatch.setattr(cli_mod, "_resolve_vault", lambda: tmp_path)
    monkeypatch.setattr(cfg_mod, "load_config", lambda: {"paths": {"vaultRoot": str(tmp_path)}})
    return tmp_path


def _args(**kw) -> argparse.Namespace:
    base = {"session": None, "dry_run": False}
    base.update(kw)
    return argparse.Namespace(**base)


def _install(monkeypatch, report: LearnReport) -> dict:
    """Patch the core verb; return the kwargs it was called with."""
    seen: dict = {}

    def fake_learn(cfg, **kwargs):
        seen["cfg"] = cfg
        seen.update(kwargs)
        return report

    monkeypatch.setattr(learn_mod, "learn", fake_learn)
    return seen


def test_prints_read_briefing_and_one_line_per_learned_rule(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", "/Users/dev")
    report = LearnReport(
        transcript=Path("/Users/dev/.claude/projects/-repo/abc.jsonl"),
        briefing=tmp_path / "bots" / "repo" / "briefings" / "sessions" / "abc.md",
        corrections=2,
        learned=[
            {
                "slug": "retry-5xx-only",
                "name": "Retry only on 5xx",
                "type": "feedback",
                "confidence": "verified",
                "quote": "never retry on 4xx, only on 5xx",
            },
            {
                "slug": "prefer-yarn",
                "name": "Prefer yarn",
                "type": "convention",
                "confidence": "inferred",
                "quote": "",
            },
        ],
        staged=1,
    )
    _install(monkeypatch, report)

    rc = cmd_mod.cmd_learn(_args())

    assert rc == 0
    assert capsys.readouterr().out.splitlines() == [
        "read: ~/.claude/projects/-repo/abc.jsonl",
        "briefing: bots/repo/briefings/sessions/abc.md (2 correction(s))",
        'learned: retry-5xx-only — Retry only on 5xx (evidence: "never retry on 4xx, only on 5xx")',
        "learned: prefer-yarn — Prefer yarn",
        "staged for review: 1 (shared/_inbox/reference/)",
        "next prompt about this will surface it — check with `mnemo why`",
    ]


def test_staged_line_is_omitted_when_nothing_was_staged(tmp_path, monkeypatch, capsys):
    report = LearnReport(
        transcript=tmp_path / "abc.jsonl",
        briefing=tmp_path / "bots" / "repo" / "b.md",
        corrections=1,
        learned=[{"slug": "s", "name": "N", "confidence": "verified", "quote": "q"}],
        staged=0,
    )
    _install(monkeypatch, report)

    assert cmd_mod.cmd_learn(_args()) == 0
    assert "staged for review" not in capsys.readouterr().out


def test_nothing_learned_prints_the_hint_and_exits_zero(tmp_path, monkeypatch, capsys):
    report = LearnReport(
        transcript=tmp_path / "abc.jsonl",
        briefing=tmp_path / "bots" / "repo" / "b.md",
        corrections=0,
        learned=[],
        hint=learn_mod.NOTHING_NEW_HINT,
    )
    _install(monkeypatch, report)

    rc = cmd_mod.cmd_learn(_args())

    out = capsys.readouterr().out
    assert rc == 0
    assert out.splitlines() == [
        f"read: {tmp_path / 'abc.jsonl'}",
        "briefing: bots/repo/b.md (0 correction(s))",
        learn_mod.NOTHING_NEW_HINT,
    ]
    assert "next prompt about this" not in out


def test_briefing_line_is_omitted_when_no_briefing_was_written(tmp_path, monkeypatch, capsys):
    report = LearnReport(transcript=tmp_path / "abc.jsonl", learned=[], hint="nope")
    _install(monkeypatch, report)

    assert cmd_mod.cmd_learn(_args()) == 0
    assert "briefing:" not in capsys.readouterr().out


def test_lock_error_exits_one_and_tells_the_user_to_retry(monkeypatch, capsys):
    _install(monkeypatch, LearnReport(error=learn_mod.LOCK_HELD))

    rc = cmd_mod.cmd_learn(_args())

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.splitlines() == [
        f"error: {learn_mod.LOCK_HELD}",
        "wait a minute and run `mnemo learn` again",
    ]
    assert captured.out == ""


def test_missing_transcript_error_exits_one_without_the_wait_line(monkeypatch, capsys):
    _install(monkeypatch, LearnReport(error=learn_mod.NO_TRANSCRIPT))

    rc = cmd_mod.cmd_learn(_args())

    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.strip() == f"error: {learn_mod.NO_TRANSCRIPT}"
    assert "wait a minute" not in captured.err


def test_dry_run_reports_what_it_would_read_and_stops(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", "/Users/dev")
    path = Path("/Users/dev/.claude/projects/-repo/abc.jsonl")
    seen = _install(monkeypatch, LearnReport(transcript=path, would_read=path))

    rc = cmd_mod.cmd_learn(_args(dry_run=True))

    assert rc == 0
    assert seen["dry_run"] is True
    assert capsys.readouterr().out.splitlines() == [
        "would read: ~/.claude/projects/-repo/abc.jsonl"
    ]


def test_session_id_and_cwd_are_forwarded_to_the_core_verb(tmp_path, monkeypatch):
    import os

    seen = _install(monkeypatch, LearnReport(transcript=tmp_path / "a.jsonl", hint="x"))

    cmd_mod.cmd_learn(_args(session="abc"))

    assert seen["session_id"] == "abc"
    assert seen["cwd"] == os.getcwd()
    assert seen["dry_run"] is False


# --- parser registration -------------------------------------------------


def test_parser_registers_learn_with_session_and_dry_run():
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["learn", "--session", "abc", "--dry-run"])

    assert args.command == "learn"
    assert args.session == "abc"
    assert args.dry_run is True


def test_learn_defaults_are_none_and_false():
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["learn"])

    assert args.session is None
    assert args.dry_run is False


def test_learn_is_registered_and_public():
    from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, INTERNAL_COMMANDS

    assert "learn" in COMMANDS
    assert "learn" not in ADVANCED_COMMANDS
    assert "learn" not in INTERNAL_COMMANDS
