from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.core import errors


def test_status_clean(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vault" in out.lower()
    assert "hooks" in out.lower()
    assert "circuit breaker" in out.lower()
    assert "closed" in out.lower() or "ok" in out.lower()


def test_status_reports_open_breaker(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    vault = tmp_home / "v"
    for i in range(15):
        try:
            raise ValueError(f"e{i}")
        except ValueError as e:
            errors.log_error(vault, "test", e)
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "open" in out.lower()


def test_doctor_runs_preflight_and_reports(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "preflight" in out.lower() or "diagnostic" in out.lower()


def test_doctor_warns_about_statusline_drift(tmp_home: Path, capsys: pytest.CaptureFixture):
    """v0.5: doctor flags settings.json statusLine drift away from the mnemo composer."""
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    # Simulate user editing settings.json after init to replace our composer
    settings_path = tmp_home / ".claude" / "settings.json"
    data = json.loads(settings_path.read_text())
    data["statusLine"] = {"type": "command", "command": "/some/other/script.sh"}
    settings_path.write_text(json.dumps(data))

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "drift" in out.lower()
    assert "statusline" in out.lower()


def test_doctor_silent_when_statusline_state_absent(tmp_home: Path, capsys: pytest.CaptureFixture):
    """No state file → never installed (or already uninstalled) → no drift warning."""
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    cli.main(["uninstall", "--yes"])
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "drift" not in out.lower()


def test_doctor_warns_about_legacy_wiki_dirs(tmp_home: Path, capsys: pytest.CaptureFixture):
    """v0.4: doctor flags wiki/sources/ and wiki/compiled/ as orphaned v0.3 fossils."""
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    vault = tmp_home / "v"
    (vault / "wiki" / "sources").mkdir(parents=True)
    (vault / "wiki" / "compiled").mkdir()
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "legacy" in out.lower()
    assert "wiki/sources" in out
    assert "wiki/compiled" in out


def test_doctor_silent_when_no_legacy_wiki_dirs(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Legacy v0.3" not in out


def test_fix_resets_breaker(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    vault = tmp_home / "v"
    for i in range(15):
        try:
            raise ValueError("e")
        except ValueError as e:
            errors.log_error(vault, "test", e)
    assert not errors.should_run(vault)
    cli.main(["fix"])
    assert errors.should_run(vault) is True


def test_open_returns_zero_when_no_opener(tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    monkeypatch.setattr(cli, "_run_open", lambda path: None)
    rc = cli.main(["open"])
    assert rc == 0


def test_status_shows_auto_brain_disabled(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False, "minNewMemories": 5, "minIntervalMinutes": 60}},
    })

    cli.main(["status"])
    captured = capsys.readouterr()
    assert "Auto-brain:" in captured.out
    assert "disabled" in captured.out.lower() or "no" in captured.out.lower()


def test_status_shows_last_run_when_present(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)

    last_run = {
        "run_id": "2026-04-13T12:00:00-abc",
        "started_at": "2026-04-13T12:00:00",
        "finished_at": "2026-04-13T12:00:09",
        "mode": "background",
        "exit_code": 0,
        "summary": {
            "pages_written": 3,
            "auto_promoted": 2,
            "sibling_proposed": 0,
            "sibling_bounced": 0,
            "upgrade_proposed": 0,
            "update_proposed": 0,
            "failed_chunks": 0,
            "mode": "background",
        },
        "error": None,
    }
    (vault / ".mnemo" / "last-auto-run.json").write_text(json.dumps(last_run))

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": True, "minNewMemories": 5, "minIntervalMinutes": 60}},
    })

    cli.main(["status"])
    captured = capsys.readouterr()
    assert "Auto-brain:" in captured.out
    assert "enabled" in captured.out.lower() or "yes" in captured.out.lower()
    assert "3 pages" in captured.out
    assert "2 auto-promoted" in captured.out


def test_status_shows_running_now_when_lock_held(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo" / "extract.lock").mkdir(parents=True)

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": True, "minNewMemories": 5, "minIntervalMinutes": 60}},
    })

    cli.main(["status"])
    captured = capsys.readouterr()
    assert "running" in captured.out.lower()


def test_doctor_warns_on_recent_background_failure(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timedelta

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)

    last_run = {
        "run_id": "2026-04-13T12:00:00-abc",
        "started_at": "2026-04-13T12:00:00",
        "finished_at": (datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
        "mode": "background",
        "exit_code": 1,
        "summary": {"pages_written": 0, "failed_chunks": 1, "mode": "background"},
        "error": {"type": "LLMSubprocessError", "message": "timeout"},
    }
    (vault / ".mnemo" / "last-auto-run.json").write_text(json.dumps(last_run))

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": True, "minNewMemories": 5, "minIntervalMinutes": 60}},
    })
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    captured = capsys.readouterr()
    assert "Auto-brain" in captured.out or "auto-brain" in captured.out.lower()
    assert "failed" in captured.out.lower() or "FAIL" in captured.out
    assert "LLMSubprocessError" in captured.out


def test_doctor_warns_on_stale_lock(tmp_path, monkeypatch, capsys):
    import os
    import time

    vault = tmp_path / "vault"
    lock = vault / ".mnemo" / "extract.lock"
    lock.mkdir(parents=True)
    old = time.time() - 900
    os.utime(lock, (old, old))

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": True, "minNewMemories": 5, "minIntervalMinutes": 60}},
    })
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    captured = capsys.readouterr()
    assert "stale" in captured.out.lower()
    assert "extract.lock" in captured.out


def test_doctor_warns_when_auto_enabled_but_no_recent_run(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timedelta

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    last_run = {
        "run_id": "old",
        "started_at": "2026-04-01T00:00:00",
        "finished_at": (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds"),
        "mode": "background",
        "exit_code": 0,
        "summary": {"pages_written": 0, "mode": "background"},
        "error": None,
    }
    (vault / ".mnemo" / "last-auto-run.json").write_text(json.dumps(last_run))

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": True, "minNewMemories": 5, "minIntervalMinutes": 60}},
    })
    monkeypatch.setattr("mnemo.install.preflight.run_preflight",
                        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})())

    cli.main(["doctor"])
    captured = capsys.readouterr()
    assert "7" in captured.out or "days" in captured.out.lower()


def test_doctor_surfaces_recall_report_when_present(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    payload = {
        "generated_at": "2026-04-17T12:00:00Z",
        "report": {
            "cases": 8,
            "primacy_rate_at_5": 0.75,
        },
        "results": [],
    }
    (vault / ".mnemo" / "recall-report.json").write_text(json.dumps(payload))

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault)})
    monkeypatch.setattr(
        "mnemo.install.preflight.run_preflight",
        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})(),
    )

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Recall" in out
    assert "primacy@5 = 75.0%" in out
    assert "over 8 cases" in out
    assert "2026-04-17T12:00:00Z" in out


def test_doctor_silent_when_recall_report_absent(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault)})
    monkeypatch.setattr(
        "mnemo.install.preflight.run_preflight",
        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})(),
    )

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Recall" not in out
    assert "primacy" not in out


def test_doctor_silent_when_recall_report_malformed(
    tmp_path, monkeypatch, capsys: pytest.CaptureFixture,
):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / "recall-report.json").write_text("not json")
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault)})
    monkeypatch.setattr(
        "mnemo.install.preflight.run_preflight",
        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})(),
    )

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Recall" not in out


# --- Zero-hit doctor check -------------------------------------------------

def _seed_access_log(vault: Path, entries: list[dict]) -> None:
    mnemo_dir = vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "mcp-access-log.jsonl").write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8",
    )


def _preflight_noop(monkeypatch, vault: Path):
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(vault)})
    monkeypatch.setattr(
        "mnemo.install.preflight.run_preflight",
        lambda vault_root=None: type("R", (), {"issues": [], "ok": True})(),
    )


def test_doctor_zero_hit_silent_when_below_threshold(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    entries = [{"tool": "list_rules_by_topic", "result_count": 1, "project": "p"} for _ in range(18)]
    entries += [{"tool": "list_rules_by_topic", "result_count": 0, "project": "p"} for _ in range(2)]
    _seed_access_log(vault, entries)
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Zero-hit" not in out


def test_doctor_zero_hit_silent_when_few_calls(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    entries = [{"tool": "list_rules_by_topic", "result_count": 0, "project": "p"} for _ in range(4)]
    entries += [{"tool": "list_rules_by_topic", "result_count": 1, "project": "p"}]
    _seed_access_log(vault, entries)
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Zero-hit" not in out


def test_doctor_zero_hit_warns_when_above_threshold(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    entries = [{"tool": "list_rules_by_topic", "result_count": 0, "project": "proj-a"} for _ in range(10)]
    entries += [{"tool": "list_rules_by_topic", "result_count": 1, "project": "proj-a"} for _ in range(10)]
    _seed_access_log(vault, entries)
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "Zero-hit calls: 50.0%" in out
    assert "proj-a" in out
    assert "review the tag ontology" in out


# --- Activation fidelity doctor check --------------------------------------

def _seed_feedback_rule(
    vault: Path,
    slug: str,
    *,
    enforce: dict | None = None,
    activates_on: dict | None = None,
    body: str = "some body content\n",
) -> None:
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", "type: feedback", "tags:", "  - workflow", "sources:", f"  - bots/proj-x/memory/{slug}.md"]
    if enforce:
        lines.append("enforce:")
        for k, v in enforce.items():
            if isinstance(v, list):
                lines.append(f"  {k}:")
                for item in v:
                    lines.append(f"    - {item}")
            else:
                lines.append(f"  {k}: {v}")
    if activates_on:
        lines.append("activates_on:")
        for k, v in activates_on.items():
            if isinstance(v, list):
                lines.append(f"  {k}:")
                for item in v:
                    lines.append(f"    - {item}")
            else:
                lines.append(f"  {k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body)
    (d / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_doctor_activation_fidelity_silent_when_index_missing(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "absent from the activation index" not in out
    assert "does not self-activate" not in out


def test_doctor_activation_fidelity_warns_when_rule_absent_from_index(
    tmp_path, monkeypatch, capsys,
):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    _seed_feedback_rule(
        vault, "orphan-rule",
        enforce={"tool": "Bash", "reason": "blocked", "deny_patterns": ["dangerous-command"]},
    )
    (vault / ".mnemo" / "rule-activation-index.json").write_text(json.dumps({
        "schema_version": 1,
        "enforce_by_project": {},
        "enrich_by_project": {},
    }))
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "orphan-rule.md" in out
    assert "absent from the activation index" in out


def test_doctor_activation_fidelity_matches_index_slug_from_name_field(
    tmp_path, monkeypatch, capsys,
):
    """Regression: build_index uses ``fm.name`` (or fm.slug) as the index slug,
    not the filename stem. Fidelity must derive slug the same way, otherwise
    any rule with a human-readable ``name`` field is falsely flagged as absent.
    """
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    # Feedback file whose frontmatter declares a different `name` than filename
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True, exist_ok=True)
    (d / "my-rule.md").write_text(
        "---\n"
        "type: feedback\n"
        "name: Display Name For Rule\n"
        "tags:\n"
        "  - workflow\n"
        "sources:\n"
        "  - bots/proj-x/memory/my-rule.md\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  reason: blocked\n"
        "  deny_patterns:\n"
        "    - rm -rf /\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )
    # Index stores the display name as the slug — matching build_index
    (vault / ".mnemo" / "rule-activation-index.json").write_text(json.dumps({
        "schema_version": 1,
        "enforce_by_project": {
            "proj-x": [{"slug": "Display Name For Rule", "deny_patterns": ["rm -rf /"], "source_count": 1}],
        },
        "enrich_by_project": {},
    }))
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    # MUST NOT report absent — the slug is present under the name-field key
    assert "absent from the activation index" not in out


def test_doctor_activation_fidelity_info_line_for_complex_globs(
    tmp_path, monkeypatch, capsys,
):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / "rule-activation-index.json").write_text(json.dumps({
        "schema_version": 1,
        "enforce_by_project": {},
        "enrich_by_project": {
            "proj-x": [{
                "slug": "char-class-rule",
                "path_globs": ["src/[abc]*.py"],
                "tools": ["Edit"],
                "rule_body_preview": "",
                "source_count": 1,
            }],
        },
    }))
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "no auto-testable path_globs" in out
    assert "Warnings above." not in out


# --- Rule-integrity doctor check -------------------------------------------

def _seed_canonical_rule(
    vault: Path, page_type: str, slug: str,
    *, sources_exist: bool = True, body: str = "a" * 100,
) -> None:
    d = vault / "shared" / page_type
    d.mkdir(parents=True, exist_ok=True)
    source_path = f"bots/proj-x/memory/{slug}.md"
    if sources_exist:
        src_dir = vault / "bots" / "proj-x" / "memory"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / f"{slug}.md").write_text("original source\n")
    lines = ["---", f"type: {page_type}", "tags:", "  - workflow", "sources:", f"  - {source_path}", "---", "", body]
    (d / f"{slug}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_doctor_rule_integrity_warns_on_missing_source(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    _seed_canonical_rule(vault, "feedback", "ghost", sources_exist=False)
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "source path does not resolve" in out
    assert "feedback/ghost.md" in out


def test_doctor_rule_integrity_warns_on_short_body(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    _seed_canonical_rule(vault, "feedback", "tiny", body="too short")
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "body has" in out
    assert "feedback/tiny.md" in out


def test_doctor_rule_integrity_ignores_inbox_drafts(tmp_path, monkeypatch, capsys):
    """Drafts under shared/_inbox/ must be skipped even when malformed."""
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    inbox = vault / "shared" / "_inbox" / "feedback"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "draft.md").write_text("---\ntype: feedback\ntags: []\nsources: []\n---\n\nx\n")
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "draft.md" not in out


def test_doctor_rule_integrity_happy_path_silent(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    _seed_canonical_rule(vault, "feedback", "good-rule", body="b" * 200)
    _preflight_noop(monkeypatch, vault)

    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "good-rule.md" not in out
