"""backfill config block ships with safe defaults."""
from __future__ import annotations

from mnemo.core.config import DEFAULTS, load_config


def test_defaults_have_backfill_block():
    assert "backfill" in DEFAULTS
    bf = DEFAULTS["backfill"]
    assert bf["enabled"] is True
    assert bf["installCap"] == 20
    assert bf["minFileMutations"] == 1
    assert bf["autoOnFirstSession"] is True


def test_load_config_merges_backfill(tmp_path, monkeypatch):
    monkeypatch.delenv("MNEMO_CONFIG_PATH", raising=False)
    cfg = load_config(missing_path=tmp_path / "nope.json")
    assert cfg["backfill"]["installCap"] == 20
