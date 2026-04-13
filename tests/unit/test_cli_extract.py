"""Unit tests for `mnemo extract` CLI command."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.core.extract import ExtractionSummary


@pytest.fixture
def patched_run_extraction(monkeypatch):
    captured: dict = {}

    def fake_run(cfg, *, dry_run=False, force=False):
        captured["cfg"] = cfg
        captured["dry_run"] = dry_run
        captured["force"] = force
        summary = ExtractionSummary()
        summary.projects_promoted = 2
        summary.pages_written = 4
        summary.llm_calls = 1
        summary.total_cost_usd = 0.0
        summary.total_input_tokens = 500
        summary.total_output_tokens = 200
        summary.wall_time_s = 9.5
        summary.all_calls_subscription = True
        return summary

    monkeypatch.setattr("mnemo.core.extract.run_extraction", fake_run)
    return captured


def test_extract_parses_subcommand(tmp_vault, tmp_home, patched_run_extraction, capsys, monkeypatch):
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({"vaultRoot": str(tmp_vault)}))
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))
    rc = cli.main(["extract"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "extraction complete" in out.lower()
    assert "subscription" in out  # cost UX path for all_calls_subscription
    assert patched_run_extraction["dry_run"] is False
    assert patched_run_extraction["force"] is False


def test_extract_dry_run_flag(tmp_vault, tmp_home, patched_run_extraction, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(tmp_vault)}))
    cli.main(["extract", "--dry-run"])
    assert patched_run_extraction["dry_run"] is True


def test_extract_force_flag(tmp_vault, tmp_home, patched_run_extraction, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(tmp_vault)}))
    cli.main(["extract", "--force"])
    assert patched_run_extraction["force"] is True


def test_extract_prints_dollar_amount_when_not_subscription(tmp_vault, tmp_home, monkeypatch, capsys):
    def fake_run(cfg, *, dry_run=False, force=False):
        s = ExtractionSummary()
        s.pages_written = 1
        s.llm_calls = 1
        s.total_cost_usd = 0.025
        s.total_input_tokens = 1000
        s.total_output_tokens = 300
        s.all_calls_subscription = False
        return s
    monkeypatch.setattr("mnemo.core.extract.run_extraction", fake_run)
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(tmp_vault)}))

    cli.main(["extract"])
    out = capsys.readouterr().out
    assert "$0.0250" in out or "$0.025" in out
    # tokens figure in output (flexible match)
    assert "tokens" in out
