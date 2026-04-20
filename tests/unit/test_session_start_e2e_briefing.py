"""End-to-end: SessionStart hook emits envelope + writes telemetry."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path


def test_session_start_main_emits_briefing_and_logs_telemetry(
    tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch, capsys
) -> None:
    from mnemo.hooks import session_start

    # 1. Seed: vault config (already present from tmp_vault fixture) +
    #    briefing on disk + injection enabled.
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "injection": {"enabled": True, "telemetry": {"enabled": True}},
        "briefings": {"enabled": True, "injectLastOnSessionStart": True},
        "capture": {"sessionStartEnd": False},
    }))
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))

    sessions_dir = tmp_vault / "bots" / "vault" / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "abc123.md").write_text(
        "---\n"
        "type: briefing\n"
        "session_id: abc123\n"
        "date: 2026-04-19\n"
        "duration_minutes: 17\n"
        "---\n\n"
        "# Briefing\n\nStopped at line 42 of auth.ts\n",
        encoding="utf-8",
    )

    # 2. Stub stdin with a SessionStart payload pointing at the vault dir
    #    so resolve_canonical_agent returns the vault dir's basename ("vault").
    payload = {
        "session_id": "new-session-id",
        "cwd": str(tmp_vault),
        "source": "startup",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    # 3. Run the hook.
    rc = session_start.main()
    assert rc == 0

    # 4. Assert stdout envelope contains the briefing block.
    out = capsys.readouterr().out
    envelope = json.loads(out)
    additional = envelope["hookSpecificOutput"]["additionalContext"]
    assert "[last-briefing session=abc123 date=2026-04-19 duration_minutes=17]" in additional
    assert "Stopped at line 42 of auth.ts" in additional

    # 5. Assert telemetry entry was written.
    log = tmp_vault / ".mnemo" / "mcp-access-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    inj = [e for e in entries if e.get("tool") == "session_start.inject"]
    assert len(inj) == 1
    assert inj[0]["included_briefing"] is True
    assert inj[0]["envelope_bytes"] > 0
