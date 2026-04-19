from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import session_end
from mnemo.core.mcp import session_state


def test_session_end_evicts_session_emissions_entry(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(
        json.dumps({"vaultRoot": str(tmp_vault)})
    )
    session_state.bump_emission(tmp_vault, sid="sid-to-evict", kind="reflex", now_ts=1)
    session_state.bump_emission(tmp_vault, sid="sid-survives", kind="reflex", now_ts=2)

    payload = {
        "cwd": str(tmp_vault),
        "session_id": "sid-to-evict",
        "reason": "exit",
    }
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        rc = session_end.main()
    assert rc == 0

    data = json.loads((tmp_vault / ".mnemo" / "mcp-call-counter.json").read_text(encoding="utf-8"))
    assert "sid-to-evict" not in data["session_emissions"]
    assert "sid-survives" in data["session_emissions"]
