"""Integration: full session_end hook body with background scheduling."""
from __future__ import annotations

import io
import json


def _session_payload(session_id="s1"):
    return json.dumps({"session_id": session_id, "cwd": "/tmp", "reason": "exit"})


def _is_extract_spawn(argv) -> bool:
    """True iff *argv* is the deferred ``mnemo extract --background`` spawn.

    Filters out incidental subprocess calls from the autopilot Tier 3 git
    signal path (``git log --since …``), which goes through ``subprocess.run``
    and is implemented on top of the same ``subprocess.Popen`` we want to
    inspect — without filtering, those calls drown the actual spawn.
    """
    return list(argv[-2:]) == ["extract", "--background"]


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

    spawns = []

    class FakePopen:
        def __init__(self, argv, **kwargs):
            spawns.append({"argv": list(argv), "kwargs": kwargs})

    monkeypatch.setattr("subprocess.Popen", FakePopen)

    monkeypatch.setattr("sys.stdin", io.StringIO(_session_payload()))
    exit_code = session_end.main()

    assert exit_code == 0
    extract_spawns = [s for s in spawns if _is_extract_spawn(s["argv"])]
    assert len(extract_spawns) == 1, f"expected one extract spawn, got: {spawns}"


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

    spawns = []
    monkeypatch.setattr(
        "subprocess.Popen",
        lambda argv, *a, **kw: spawns.append(list(argv)) or None,
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(_session_payload()))

    session_end.main()

    extract_spawns = [a for a in spawns if _is_extract_spawn(a)]
    assert extract_spawns == [], f"no extract spawn when auto disabled, got: {spawns}"
