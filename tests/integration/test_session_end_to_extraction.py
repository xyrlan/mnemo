"""Integration: full session_end hook body with background scheduling."""
from __future__ import annotations

import io
import json


def _session_payload(session_id="s1"):
    return json.dumps({"session_id": session_id, "cwd": "/tmp", "reason": "exit"})


def test_session_end_spawns_background_when_auto_enabled(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / "bots" / "agent_a" / "memory").mkdir(parents=True)
    (vault / "bots" / "agent_a" / "memory" / "feedback_x.md").write_text(
        "---\ntype: feedback\n---\nbody\n"
    )

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"auto": {
            "enabled": True,
            "minNewMemories": 1,
            "minIntervalMinutes": 0,
        }},
        "capture": {"sessionStartEnd": False},
    }
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg: vault)
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda _cfg: None)

    captured = {}

    class FakePopen:
        def __init__(self, argv, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    monkeypatch.setattr("sys.stdin", io.StringIO(_session_payload()))
    exit_code = session_end.main()

    assert exit_code == 0
    assert "argv" in captured
    assert captured["argv"][-2:] == ["extract", "--background"]


def test_session_end_does_not_spawn_when_auto_disabled(tmp_path, monkeypatch):
    from mnemo.hooks import session_end

    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / "bots" / "agent_a" / "memory").mkdir(parents=True)
    (vault / "bots" / "agent_a" / "memory" / "feedback_x.md").write_text(
        "---\ntype: feedback\n---\nbody\n"
    )

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"auto": {"enabled": False}, "hintThreshold": 100},
        "capture": {"sessionStartEnd": False},
    }
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg: vault)
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda _cfg: None)

    calls = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr("sys.stdin", io.StringIO(_session_payload()))

    session_end.main()

    assert calls == [], "no spawn when auto disabled"
