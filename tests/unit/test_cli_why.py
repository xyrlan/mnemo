"""`mnemo why` — the command behind `/mnemo:why`.

A thin reader over the reflex log. The explaining lives in
``core.reflex.receipts``; what is tested here is that the command is reachable,
scoped to the repo the user is standing in, and honest when there is nothing
to show.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from mnemo.cli.commands import why as cmd


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: root)
    monkeypatch.setattr(cmd, "_current_project", lambda: "mnemo")
    return root


def _args(**over) -> argparse.Namespace:
    base = {"json": False, "limit": 10, "all_projects": False}
    base.update(over)
    return argparse.Namespace(**base)


def _log(vault: Path, entries: list[dict]) -> None:
    with (vault / ".mnemo" / "reflex-log.jsonl").open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _entry(**over) -> dict:
    base = {"session_id": "s", "project": "mnemo", "prompt_hash": "sha256:a",
            "prompt_tokens": 8, "emitted": [], "scores": [],
            "silence_reason": "relative_gap_fail", "ts": "2026-08-04T09:30:07Z",
            "candidates": [["top-rule", 4.21], ["runner-up", 3.85]],
            "thresholds": {"relative_gap": 1.5, "absolute_floor": 2.0,
                           "term_overlap_min": 2}}
    base.update(over)
    return base


def test_it_prints_the_recent_decisions(vault, capsys):
    _log(vault, [_entry()])

    assert cmd.cmd_why(_args()) == 0
    out = capsys.readouterr().out
    assert "top-rule" in out and "4.21" in out


def test_it_is_scoped_to_the_current_repo_by_default(vault, capsys):
    _log(vault, [_entry(project="clubinho", candidates=[["other-repo", 9.0]])])

    cmd.cmd_why(_args())
    assert "other-repo" not in capsys.readouterr().out


def test_all_projects_drops_the_scope(vault, capsys):
    _log(vault, [_entry(project="clubinho", candidates=[["other-repo", 9.0]])])

    cmd.cmd_why(_args(all_projects=True))
    assert "other-repo" in capsys.readouterr().out


def test_the_limit_is_honoured(vault, capsys):
    _log(vault, [_entry(ts=f"2026-08-04T09:0{i}:00Z",
                        candidates=[[f"rule-{i}", 4.21], ["r", 3.85]])
                 for i in range(4)])

    cmd.cmd_why(_args(limit=1))
    out = capsys.readouterr().out
    assert "rule-3" in out and "rule-0" not in out


def test_an_empty_log_says_so_and_still_exits_zero(vault, capsys):
    assert cmd.cmd_why(_args()) == 0
    assert capsys.readouterr().out.strip()


def test_json_mode_emits_the_raw_decisions(vault, capsys):
    _log(vault, [_entry()])

    assert cmd.cmd_why(_args(json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["candidates"][0] == ["top-rule", 4.21]


def test_the_command_is_registered():
    from mnemo.cli.parser import COMMANDS

    assert COMMANDS.get("why") is cmd.cmd_why


def test_the_parser_accepts_the_flags():
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["why", "--limit", "3", "--all-projects"])
    assert args.limit == 3 and args.all_projects is True
