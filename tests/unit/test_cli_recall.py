"""Tests for `mnemo recall` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli


def _write_log(vault: Path, entries: list[dict]) -> None:
    mnemo_dir = vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "mcp-access-log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8",
    )


def _seed_rule(vault: Path, slug: str, tags: list[str], project: str) -> None:
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: feedback", "tags:"]
    lines.extend(f"  - {t}" for t in tags)
    lines.append("sources:")
    lines.append(f"  - bots/{project}/memory/{slug}.md")
    lines.append("---")
    lines.append("")
    lines.append("body")
    (d / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _populated_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    _seed_rule(vault, "rule-a", ["workflow"], "proj-x")
    _write_log(vault, [
        {
            "timestamp": "2026-04-17T10:00:00Z",
            "tool": "list_rules_by_topic",
            "args": {"topic": "workflow", "scope": "project"},
            "project": "proj-x",
            "hit_slugs": ["rule-a"],
        },
        {
            "timestamp": "2026-04-17T10:00:05Z",
            "tool": "read_mnemo_rule",
            "args": {"slug": "rule-a"},
            "project": "proj-x",
        },
    ])
    return vault


def test_recall_registered_in_help(capsys: pytest.CaptureFixture):
    rc = cli.main(["help"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "recall" in out


def test_recall_bootstraps_and_prints_report(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = _populated_vault(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["recall"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cases              : 1" in out
    assert (vault / ".mnemo" / "recall-cases.json").is_file()
    assert (vault / ".mnemo" / "recall-report.json").is_file()
    payload = json.loads((vault / ".mnemo" / "recall-report.json").read_text())
    assert payload["report"]["primacy_at_3"] == 1
    assert "generated_at" in payload
    assert payload["report"]["log_entries"] == 2  # 2 entries in the seed log


def test_recall_json_flag_outputs_json(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = _populated_vault(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["recall", "--json"])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["report"]["cases"] == 1


def test_recall_missing_log_returns_error(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["recall"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "access log missing" in err


def test_recall_empty_pairs_returns_zero_without_crash(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_log(vault, [
        {
            "timestamp": "2026-04-17T10:00:00Z",
            "tool": "list_rules_by_topic",
            "args": {"topic": "workflow", "scope": "project"},
            "project": "proj-x",
            "hit_slugs": ["rule-a"],
        },
    ])
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["recall"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "no cases generated" in out


def test_recall_no_bootstrap_errors_without_existing_cases(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    rc = cli.main(["recall", "--no-bootstrap"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "cases file missing" in err


def test_recall_output_shows_unlock_footer(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = _populated_vault(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    cli.main(["recall"])
    out = capsys.readouterr().out
    assert "next ranking change unlocks at" in out
    assert "currently 2" in out


def test_recall_no_bootstrap_reuses_existing(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = _populated_vault(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    # First run: bootstrap
    cli.main(["recall"])
    capsys.readouterr()  # clear
    # Tamper with log — ensure --no-bootstrap ignores it
    (vault / ".mnemo" / "mcp-access-log.jsonl").write_text("garbage\n")
    rc = cli.main(["recall", "--no-bootstrap"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "cases              : 1" in out
