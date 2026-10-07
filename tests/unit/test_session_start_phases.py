"""SessionStart times its own phases when asked to (#610).

The hook took 5 s at the median and minutes at the p99 (#593's transcript
count), and nothing said where. With ``MNEMO_HOOK_PHASES`` set, each run
appends one row of per-phase wall times to the vault; without it, nothing is
written, so an ordinary session pays no extra write.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.hooks import session_start


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    (root / "HOME.md").write_text("# home\n", encoding="utf-8")
    return root


def _run_hook(monkeypatch, vault: Path, cwd: Path, cfg: dict) -> str:
    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda *a, **k: vault)
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(json.dumps({"session_id": "s" * 36, "cwd": str(cwd),
                                "source": "startup"})),
    )
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    session_start.main()
    return out.getvalue()


def _rows(vault: Path) -> list:
    path = vault / session_start.PHASES_LOG
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l]


def test_a_profiled_session_start_logs_one_row_of_phase_times(monkeypatch, vault, tmp_path):
    monkeypatch.setenv(session_start.PHASES_ENV, "1")
    _run_hook(monkeypatch, vault, tmp_path,
              {"vaultRoot": str(vault), "reflex": {"enabled": True},
               "injection": {"enabled": True}})
    rows = _rows(vault)
    assert len(rows) == 1
    row = rows[0]
    assert row["session_id"] == "s" * 36 and row["source"] == "startup"
    phases = row["phases"]
    # The phases #610 asked about, by name, each a non-negative time.
    for name in ("config", "rule_activation_index", "reflex_index", "deferred_spawn",
                 "injection", "autopilot"):
        assert name in phases, name
        assert phases[name] >= 0
    assert row["total_ms"] >= sum(phases.values()) - 1


def test_an_ordinary_session_start_writes_no_phase_row(monkeypatch, vault, tmp_path):
    monkeypatch.delenv(session_start.PHASES_ENV, raising=False)
    _run_hook(monkeypatch, vault, tmp_path,
              {"vaultRoot": str(vault), "reflex": {"enabled": True},
               "injection": {"enabled": True}})
    assert _rows(vault) == []
