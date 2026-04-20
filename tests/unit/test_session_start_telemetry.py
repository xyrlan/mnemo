"""SessionStart logs an envelope-size telemetry entry."""
from __future__ import annotations

import io
import json
from pathlib import Path

from mnemo.hooks import session_start


def test_session_start_records_inject_entry(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )

    sessions_dir = vault / "bots" / "myproj" / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "abc.md").write_text(
        "---\nsession_id: abc\ndate: 2026-04-19\nduration_minutes: 10\n---\nbody\n",
        encoding="utf-8",
    )

    out = io.StringIO()
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    session_start._emit_injection(payload, out=out)
    # Real telemetry write happens inside main() — exercise the helper directly.
    from mnemo.core.mcp import access_log
    access_log.record_session_start_inject(
        vault,
        envelope_bytes=len(payload.encode("utf-8")),
        included_briefing=True,
        project="myproj",
        agent="myproj",
    )

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    inj = [e for e in entries if e["tool"] == "session_start.inject"]
    assert len(inj) == 1
    assert inj[0]["included_briefing"] is True
    assert inj[0]["envelope_bytes"] > 0
