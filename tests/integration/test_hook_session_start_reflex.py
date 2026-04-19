"""SessionStart must rebuild reflex-index.json when reflex.enabled."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import session_start


def test_session_start_writes_reflex_index(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": True},
        "injection": {"enabled": False},
        "enforcement": {"enabled": False},
        "enrichment": {"enabled": False},
    }))
    # Minimal feedback rule so the index has a doc.
    fb_dir = tmp_vault / "shared" / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    (fb_dir / "r.md").write_text(
        "---\nname: r\ndescription: d\ntags:\n  - t\n"
        "sources:\n  - bots/mnemo/memory/x.md\nstability: stable\n---\nbody\n",
        encoding="utf-8",
    )

    payload = {"cwd": str(tmp_vault), "session_id": "sid", "source": "startup"}
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        rc = session_start.main()

    assert rc == 0
    idx_path = tmp_vault / ".mnemo" / "reflex-index.json"
    assert idx_path.exists()
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert "r" in data["docs"]
