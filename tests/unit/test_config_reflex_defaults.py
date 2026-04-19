from __future__ import annotations

from mnemo.core.config import load_config


def test_reflex_defaults_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_path / "mnemo.config.json"))
    (tmp_path / "mnemo.config.json").write_text("{}")
    cfg = load_config()
    reflex = cfg["reflex"]
    assert reflex["enabled"] is False  # v0.8.0-alpha ships off-by-default
    assert reflex["maxEmissionsPerSession"] == 10
    assert reflex["thresholds"]["termOverlapMin"] == 2
    assert reflex["thresholds"]["relativeGap"] == 1.5
    assert reflex["thresholds"]["absoluteFloor"] == 2.0
    assert reflex["bm25f"]["fieldWeights"]["aliases"] == 2.5


def test_enrichment_cap_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_path / "mnemo.config.json"))
    (tmp_path / "mnemo.config.json").write_text("{}")
    cfg = load_config()
    assert cfg["enrichment"]["maxEmissionsPerSession"] == 15
