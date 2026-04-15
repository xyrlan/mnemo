"""Tests for activation-related sections in cmd_status, cmd_doctor, and statusline.render()."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mnemo import cli, statusline as sl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_index(vault: Path, *, enforce_rules: list | None = None, enrich_rules: list | None = None, project: str = "myproject") -> None:
    """Write a minimal rule-activation-index.json to vault/.mnemo/."""
    index = {
        "schema_version": 1,
        "built_at": "2026-04-15T12:00:00Z",
        "vault_root": str(vault),
        "enforce_by_project": {project: enforce_rules or []},
        "enrich_by_project": {project: enrich_rules or []},
        "malformed": [],
    }
    mnemo_dir = vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "rule-activation-index.json").write_text(json.dumps(index))


def _write_denial_log(vault: Path, entries: list[dict]) -> None:
    """Write denial-log.jsonl to vault/.mnemo/."""
    mnemo_dir = vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(e) for e in entries]
    (mnemo_dir / "denial-log.jsonl").write_text("\n".join(lines) + ("\n" if lines else ""))


def _write_feedback_file(vault: Path, slug: str, *, enforce: dict | None = None, activates_on: dict | None = None, sources: list[str] | None = None) -> Path:
    """Write a minimal shared/feedback/<slug>.md with optional enforce/activates_on blocks."""
    feedback_dir = vault / "shared" / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in (sources or ["bots/proj/m.md"]))
    fm_lines = [
        "---",
        f"name: {slug}",
        f"slug: {slug}",
        "type: feedback",
        "stability: stable",
        "sources:",
        sources_yaml,
        "tags:",
        "  - auto-promoted",
    ]
    if enforce:
        fm_lines.append("enforce:")
        for k, v in enforce.items():
            if isinstance(v, list):
                fm_lines.append(f"  {k}:")
                for item in v:
                    fm_lines.append(f"    - {item}")
            else:
                fm_lines.append(f"  {k}: {v!r}")
    if activates_on:
        fm_lines.append("activates_on:")
        for k, v in activates_on.items():
            if isinstance(v, list):
                fm_lines.append(f"  {k}:")
                for item in v:
                    fm_lines.append(f"    - {item}")
            else:
                fm_lines.append(f"  {k}: {v!r}")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append("Rule body content here.")
    path = feedback_dir / f"{slug}.md"
    path.write_text("\n".join(fm_lines))
    return path


def _mock_config_and_vault(monkeypatch, vault: Path) -> None:
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}},
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": True},
    })


# ---------------------------------------------------------------------------
# cmd_status — Activation section
# ---------------------------------------------------------------------------


def test_cmd_status_prints_activation_section_when_flags_enabled(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_index(vault, enforce_rules=[{
        "slug": "no-force-push",
        "tool": "Bash",
        "deny_patterns": ["git push.*--force"],
        "deny_commands": [],
        "reason": "no force push",
        "source_files": ["bots/myproject/m.md"],
        "source_count": 1,
    }], enrich_rules=[{
        "slug": "react-hook",
        "tools": ["Edit"],
        "path_globs": ["**/*.tsx"],
        "topic_tags": [],
        "rule_body_preview": "Use hooks",
        "source_files": ["bots/myproject/m.md"],
        "source_count": 1,
    }, {
        "slug": "react-context",
        "tools": ["Edit"],
        "path_globs": ["**/*.tsx"],
        "topic_tags": [],
        "rule_body_preview": "Use context",
        "source_files": ["bots/myproject/m.md"],
        "source_count": 1,
    }])

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}},
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": True},
    })
    monkeypatch.setattr("mnemo.core.agent.resolve_agent", lambda cwd: type("A", (), {"name": "myproject"})())

    cli.main(["status"])
    out = capsys.readouterr().out
    assert "Activation:" in out
    assert "Enforcement:" in out
    assert "enabled" in out
    assert "Enforce rules:" in out
    assert "1" in out
    assert "Enrich rules:" in out


def test_cmd_status_prints_missing_index_when_absent(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    # No index file written

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}},
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": False},
    })

    cli.main(["status"])
    out = capsys.readouterr().out
    assert "Activation:" in out
    assert "missing" in out.lower()


def test_cmd_status_shows_last_denial_from_log(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_index(vault)
    _write_denial_log(vault, [
        {"timestamp": "2026-04-15T10:00:00Z", "slug": "r1", "project": "p", "reason": "old", "tool": "Bash", "command": "git status"},
        {"timestamp": "2026-04-15T18:32:11Z", "slug": "r2", "project": "p", "reason": "no force push", "tool": "Bash", "command": "git push --force"},
    ])

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}},
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": False},
    })

    cli.main(["status"])
    out = capsys.readouterr().out
    assert "Last denial:" in out
    assert "2026-04-15T18:32:11Z" in out
    assert "git push --force" in out


def test_cmd_status_no_activation_when_both_flags_disabled(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}},
        "enforcement": {"enabled": False},
        "enrichment": {"enabled": False},
    })

    cli.main(["status"])
    out = capsys.readouterr().out
    assert "Activation:" not in out


def test_cmd_status_last_denial_none_when_log_empty(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_index(vault)

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}},
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": False},
    })

    cli.main(["status"])
    out = capsys.readouterr().out
    assert "Last denial: none" in out


def test_cmd_status_recent_denials_today_count(tmp_path, monkeypatch, capsys):
    """Only denials from today UTC should be counted."""
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_index(vault)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_denial_log(vault, [
        {"timestamp": "2025-01-01T00:00:00Z", "slug": "r1", "project": "p", "reason": "old", "tool": "Bash", "command": "x"},
        {"timestamp": today, "slug": "r2", "project": "p", "reason": "recent", "tool": "Bash", "command": "y"},
    ])

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}},
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": False},
    })

    cli.main(["status"])
    out = capsys.readouterr().out
    assert "Recent denials (today): 1" in out


# ---------------------------------------------------------------------------
# cmd_doctor — new activation checks
# ---------------------------------------------------------------------------


def test_cmd_doctor_warns_on_malformed_enforce_block(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    # Write a feedback file with an enforce block missing the required 'reason'
    _write_feedback_file(vault, "bad-rule", enforce={
        "tool": "Bash",
        "deny_pattern": "git push --force",
        # Missing 'reason'
    })

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": False}}})
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "malformed" in out.lower() or "enforce" in out.lower() or "bad-rule" in out.lower()


def test_cmd_doctor_warns_on_stale_index(tmp_path, monkeypatch, capsys):
    import os
    import time

    vault = tmp_path / "vault"
    vault.mkdir()
    _write_index(vault)
    index_path = vault / ".mnemo" / "rule-activation-index.json"

    # Write a feedback file, then make the index older
    md_path = _write_feedback_file(vault, "some-rule", sources=["bots/proj/m.md"])

    # Make index older than the feedback file
    old_time = time.time() - 3600
    os.utime(index_path, (old_time, old_time))

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": False}}})
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "stale" in out.lower()
    assert "activation index" in out.lower() or "index" in out.lower()


def test_cmd_doctor_warns_on_suspicious_deny_pattern(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    # Write a feedback file with an enforce block whose deny_pattern is too short (< 5 chars)
    _write_feedback_file(vault, "short-rule", enforce={
        "tool": "Bash",
        "deny_pattern": "git",  # only 3 chars
        "reason": "some reason",
    })

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": False}}})
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "suspicious" in out.lower() or "deny_pattern" in out.lower() or "short" in out.lower() or "permissive" in out.lower()


def test_cmd_doctor_warns_on_suspicious_deny_pattern_benign_match(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    # deny_pattern that matches "echo hello" (too permissive)
    _write_feedback_file(vault, "permissive-rule", enforce={
        "tool": "Bash",
        "deny_pattern": "echo",  # matches "echo hello"
        "reason": "block echo",
    })

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": False}}})
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "suspicious" in out.lower() or "permissive" in out.lower() or "deny_pattern" in out.lower()


def test_cmd_doctor_warns_on_broad_path_glob(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    _write_feedback_file(vault, "broad-rule", activates_on={
        "tools": ["Edit"],
        "path_globs": ["**/*"],  # too broad
    })

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": False}}})
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "broad" in out.lower() or "**/*" in out or "path_glob" in out.lower()


def test_cmd_doctor_clean_vault_shows_all_checks_pass(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    # No feedback files, no index

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": False}}})
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    # Should not warn about activation checks; should pass
    assert rc == 0
    assert "malformed" not in out.lower() or "✓" in out
    assert "stale" not in out.lower()


def test_cmd_doctor_does_not_crash_on_unreadable_feedback_file(tmp_path, monkeypatch, capsys):
    """A single bad file should not crash the whole doctor check."""
    vault = tmp_path / "vault"
    vault.mkdir()
    feedback_dir = vault / "shared" / "feedback"
    feedback_dir.mkdir(parents=True)
    # Write a file with completely invalid YAML/content
    (feedback_dir / "corrupt.md").write_bytes(b"\xff\xfe INVALID BINARY GARBAGE")

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault), "extraction": {"auto": {"enabled": False}}})
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    # Should not raise
    rc = cli.main(["doctor"])
    assert rc in (0, 1)


# ---------------------------------------------------------------------------
# statusline.render() — activation segments
# ---------------------------------------------------------------------------

def _write_claude_json_with_mnemo(path: Path) -> None:
    path.write_text(json.dumps({
        "mcpServers": {
            "mnemo": {"command": "python", "args": ["-m", "mnemo", "mcp-server"]},
        },
    }))


def test_statusline_shows_activation_segments_when_enabled(tmp_vault, tmp_path, monkeypatch):
    """render() includes '⛔ rules' and '💡 active' segments when activation flags are on."""
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    _write_index(tmp_vault, enforce_rules=[{
        "slug": "no-force",
        "tool": "Bash",
        "deny_patterns": ["git push.*--force"],
        "deny_commands": [],
        "reason": "no force",
        "source_files": ["bots/myproject/m.md"],
        "source_count": 1,
    }], enrich_rules=[{
        "slug": "react",
        "tools": ["Edit"],
        "path_globs": ["**/*.tsx"],
        "topic_tags": [],
        "rule_body_preview": "body",
        "source_files": ["bots/myproject/m.md"],
        "source_count": 1,
    }], project="myproject")

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(tmp_vault),
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": True},
    })
    monkeypatch.setattr("mnemo.core.agent.resolve_agent", lambda cwd: type("A", (), {"name": "myproject"})())

    result = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))
    assert "⛔" in result
    assert "rules" in result
    assert "💡" in result
    assert "active" in result


def test_statusline_omits_activation_segments_when_disabled(tmp_vault, tmp_path, monkeypatch):
    """render() omits activation segments when both enforcement and enrichment are disabled."""
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    _write_index(tmp_vault)

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(tmp_vault),
        "enforcement": {"enabled": False},
        "enrichment": {"enabled": False},
    })

    result = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))
    assert "⛔" not in result
    assert "💡" not in result
    assert "blocks" not in result


def test_statusline_omits_activation_segments_when_index_missing(tmp_vault, tmp_path, monkeypatch):
    """render() silently omits activation segments when index doesn't exist."""
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)
    # No index written

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(tmp_vault),
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": True},
    })
    monkeypatch.setattr("mnemo.core.agent.resolve_agent", lambda cwd: type("A", (), {"name": "myproject"})())

    result = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))
    assert "⛔" not in result
    assert "💡" not in result
    # Base segment still renders
    assert "mnemo mcp" in result


def test_statusline_counts_today_denials_only(tmp_vault, tmp_path, monkeypatch):
    """render() 'blocks' count only includes denials from today UTC."""
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    _write_index(tmp_vault, project="myproject")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_denial_log(tmp_vault, [
        {"timestamp": "2024-01-01T00:00:00Z", "slug": "r1", "project": "p", "reason": "old", "tool": "Bash", "command": "x"},
        {"timestamp": today, "slug": "r2", "project": "p", "reason": "recent", "tool": "Bash", "command": "y"},
        {"timestamp": today, "slug": "r3", "project": "p", "reason": "recent2", "tool": "Bash", "command": "z"},
    ])

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(tmp_vault),
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": False},
    })
    monkeypatch.setattr("mnemo.core.agent.resolve_agent", lambda cwd: type("A", (), {"name": "myproject"})())

    result = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))
    # Should show "2 blocks" (only 2 of 3 entries are today)
    assert "2 blocks" in result


def test_statusline_omits_zero_count_segments(tmp_vault, tmp_path, monkeypatch):
    """Segments with count=0 should be omitted from the statusline."""
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    # Index with 0 enforce and 0 enrich rules for this project
    _write_index(tmp_vault, enforce_rules=[], enrich_rules=[], project="myproject")
    # No denial log

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(tmp_vault),
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": True},
    })
    monkeypatch.setattr("mnemo.core.agent.resolve_agent", lambda cwd: type("A", (), {"name": "myproject"})())

    result = sl.render(tmp_vault, claude_json, cwd=str(tmp_vault))
    # Zero-count segments omitted
    assert "⛔" not in result
    assert "💡" not in result
    assert "blocks" not in result
    # But base still renders
    assert "mnemo mcp" in result
