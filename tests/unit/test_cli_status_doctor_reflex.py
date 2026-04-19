"""Task F2: status + doctor surfaces for the Prompt Reflex pipeline."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from mnemo import cli
from mnemo.core.mcp import session_state


def test_status_shows_reflex_section(tmp_vault, monkeypatch, capsys):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": True},
    }))
    session_state.bump_emission(tmp_vault, sid="s", kind="reflex", now_ts=1)

    cli.main(["status"])

    captured = capsys.readouterr()
    # Strip the vault-path prefix so "reflex" in "…/test_status_shows_reflex…"
    # doesn't create a false positive — the assertion must hit the new
    # Reflex section we add to status output.
    output_lines = [ln for ln in captured.out.splitlines() if not ln.startswith("Vault:")]
    stripped = "\n".join(output_lines).lower()
    assert "reflex" in stripped
    assert "1 emission" in stripped  # bumped once above


def test_doctor_check_reflex_index_stale_detects_missing(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": True},
    }))
    # No reflex-index.json file exists — check should flag.
    ok = cli._doctor_check_reflex_index(tmp_vault)
    assert ok is False


def test_doctor_check_reflex_index_ok_when_disabled(tmp_vault, monkeypatch):
    """When reflex.enabled is false, the missing index is not a warning."""
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": False},
    }))
    assert cli._doctor_check_reflex_index(tmp_vault) is True


def test_doctor_check_reflex_index_ok_when_present(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": True},
    }))
    idx = tmp_vault / ".mnemo" / "reflex-index.json"
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text("{}", encoding="utf-8")
    assert cli._doctor_check_reflex_index(tmp_vault) is True


def test_doctor_check_reflex_session_cap_hits_flags_above_threshold(tmp_vault, capsys):
    """More than 20% of recent sessions hitting cap should warn."""
    log = tmp_vault / ".mnemo" / "reflex-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # 5 sessions; 2 hit cap → 40% > 20%
    entries = []
    for sid in ("a", "b"):
        entries.append({"ts": now, "session_id": sid, "silence_reason": "session_cap_reached"})
    for sid in ("c", "d", "e"):
        entries.append({"ts": now, "session_id": sid, "silence_reason": None, "emitted": ["x"]})
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

    ok = cli._doctor_check_reflex_session_cap_hits(tmp_vault)
    assert ok is False
    out = capsys.readouterr().out
    assert "session-cap" in out.lower() or "session cap" in out.lower()


def test_doctor_check_reflex_session_cap_hits_ok_when_no_log(tmp_vault):
    assert cli._doctor_check_reflex_session_cap_hits(tmp_vault) is True


def test_doctor_check_reflex_session_cap_hits_ignores_old_entries(tmp_vault):
    log = tmp_vault / ".mnemo" / "reflex-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = [
        {"ts": old, "session_id": "a", "silence_reason": "session_cap_reached"},
        {"ts": old, "session_id": "b", "silence_reason": "session_cap_reached"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    # All entries older than 7d → no sessions counted → return True.
    assert cli._doctor_check_reflex_session_cap_hits(tmp_vault) is True


def test_doctor_check_reflex_bilingual_gap_flags_missing_aliases(tmp_vault, capsys):
    """3+ rules with non-ASCII description but no aliases should warn."""
    feedback = tmp_vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    for i, desc in enumerate([
        "Use português always",
        "Configurações padrão",
        "Execução paralela preferida",
    ]):
        (feedback / f"rule-{i}.md").write_text(
            f"---\nname: rule-{i}\ndescription: {desc}\ntype: feedback\n"
            f"tags:\n  - x\n"
            f"sources:\n  - bots/x/memory/y.md\n---\nBody.\n",
            encoding="utf-8",
        )
    ok = cli._doctor_check_reflex_bilingual_gap(tmp_vault)
    assert ok is False
    out = capsys.readouterr().out
    assert "bilingual" in out.lower()


def test_doctor_check_reflex_bilingual_gap_ok_when_aliases_present(tmp_vault):
    feedback = tmp_vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    for i, desc in enumerate([
        "Use português always",
        "Configurações padrão",
        "Execução paralela preferida",
    ]):
        (feedback / f"rule-{i}.md").write_text(
            f"---\nname: rule-{i}\ndescription: {desc}\ntype: feedback\n"
            f"tags:\n  - x\n"
            f"aliases:\n  - english-alias\n"
            f"sources:\n  - bots/x/memory/y.md\n---\nBody.\n",
            encoding="utf-8",
        )
    assert cli._doctor_check_reflex_bilingual_gap(tmp_vault) is True


def test_doctor_check_reflex_bilingual_gap_ok_when_below_threshold(tmp_vault):
    feedback = tmp_vault / "shared" / "feedback"
    feedback.mkdir(parents=True, exist_ok=True)
    # Only 2 → below threshold of 3
    for i, desc in enumerate([
        "Use português always",
        "Configurações padrão",
    ]):
        (feedback / f"rule-{i}.md").write_text(
            f"---\nname: rule-{i}\ndescription: {desc}\ntype: feedback\n"
            f"tags:\n  - x\n"
            f"sources:\n  - bots/x/memory/y.md\n---\nBody.\n",
            encoding="utf-8",
        )
    assert cli._doctor_check_reflex_bilingual_gap(tmp_vault) is True
