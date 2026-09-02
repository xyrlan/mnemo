from mnemo.autopilot.core import network
from mnemo.core.config import DEFAULTS


def test_default_is_off():
    assert DEFAULTS["autopilot"]["network"]["enabled"] is False
    assert DEFAULTS["backfill"]["autoOnFirstSession"] is False


def test_enabled_reads_cfg():
    assert network.enabled({"autopilot": {"network": {"enabled": True}}}) is True
    assert network.enabled({"autopilot": {"network": {"enabled": False}}}) is False
    assert network.enabled({}) is False


def test_enabled_loads_config_when_none(monkeypatch):
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"autopilot": {"network": {"enabled": True}}})
    assert network.enabled() is True
