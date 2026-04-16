from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mnemo.core import config


def test_defaults_when_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MNEMO_CONFIG_PATH", raising=False)
    cfg = config.load_config(missing_path=tmp_path / "nope.json")
    assert cfg["vaultRoot"]  # always set
    assert cfg["capture"]["sessionStartEnd"] is True
    assert cfg["agent"]["strategy"] == "git-root"
    # v0.3.1 removed userPrompt, fileEdits, and the async block along with the
    # write-only user_prompt and post_tool_use hooks.
    assert "userPrompt" not in cfg["capture"]
    assert "fileEdits" not in cfg["capture"]
    assert "async" not in cfg


def test_user_overrides_defaults(tmp_path: Path):
    p = tmp_path / "mnemo.config.json"
    p.write_text(json.dumps({
        "vaultRoot": "~/somewhere",
        "capture": {"sessionStartEnd": False},
    }))
    cfg = config.load_config(p)
    assert cfg["vaultRoot"] == "~/somewhere"
    assert cfg["capture"]["sessionStartEnd"] is False


def test_unknown_keys_preserved(tmp_path: Path):
    p = tmp_path / "mnemo.config.json"
    p.write_text(json.dumps({"futureFeatureX": {"enabled": True}}))
    cfg = config.load_config(p)
    assert cfg["futureFeatureX"] == {"enabled": True}


def test_env_var_overrides_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    p = tmp_path / "elsewhere.json"
    p.write_text(json.dumps({"vaultRoot": "/env/vault"}))
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(p))
    cfg = config.load_config()
    assert cfg["vaultRoot"] == "/env/vault"


def test_corrupted_json_returns_defaults(tmp_path: Path):
    p = tmp_path / "mnemo.config.json"
    p.write_text("{not valid json")
    cfg = config.load_config(p)
    # Falls back silently to defaults; never raises
    assert cfg["vaultRoot"]
    assert cfg["capture"]["sessionStartEnd"] is True


def test_extraction_defaults_populated(tmp_path):
    from mnemo.core import config
    cfg = config.load_config(tmp_path / "nope.json")
    assert cfg["extraction"]["model"] == "claude-haiku-4-5"
    assert cfg["extraction"]["chunkSize"] == 10
    assert cfg["extraction"]["preferAPI"] is False
    assert cfg["extraction"]["subprocessTimeout"] == 60
    assert cfg["extraction"]["costSoftCap"] is None
    # v0.3.1 removed hintThreshold along with the hint fallback path.
    assert "hintThreshold" not in cfg["extraction"]


def test_extraction_user_override_preserved(tmp_path):
    from mnemo.core import config
    p = tmp_path / "cfg.json"
    p.write_text('{"extraction": {"model": "claude-sonnet-4-6", "chunkSize": 5}}')
    cfg = config.load_config(p)
    assert cfg["extraction"]["model"] == "claude-sonnet-4-6"
    assert cfg["extraction"]["chunkSize"] == 5
    # Defaults still present for non-overridden keys
    assert cfg["extraction"]["subprocessTimeout"] == 60


def test_extraction_auto_defaults_enabled():
    from mnemo.core.config import DEFAULTS

    assert "auto" in DEFAULTS["extraction"]
    auto = DEFAULTS["extraction"]["auto"]
    assert auto["enabled"] is True
    assert auto["minNewMemories"] == 1
    assert auto["minIntervalMinutes"] == 60


def test_load_config_populates_auto_defaults(tmp_path):
    from mnemo.core.config import load_config

    cfg_path = tmp_path / "mnemo.config.json"
    cfg_path.write_text('{"vaultRoot": "/tmp/test"}')
    cfg = load_config(cfg_path)

    assert cfg["extraction"]["auto"]["enabled"] is True
    assert cfg["extraction"]["auto"]["minNewMemories"] == 1
    assert cfg["extraction"]["auto"]["minIntervalMinutes"] == 60


def test_user_override_of_auto_enabled_preserved(tmp_path):
    from mnemo.core.config import load_config

    cfg_path = tmp_path / "mnemo.config.json"
    cfg_path.write_text(
        '{"extraction": {"auto": {"enabled": false, "minIntervalMinutes": 30}}}'
    )
    cfg = load_config(cfg_path)

    assert cfg["extraction"]["auto"]["enabled"] is False
    assert cfg["extraction"]["auto"]["minIntervalMinutes"] == 30
    assert cfg["extraction"]["auto"]["minNewMemories"] == 1  # untouched default


def test_briefings_defaults_enabled():
    from mnemo.core.config import DEFAULTS

    assert "briefings" in DEFAULTS
    assert DEFAULTS["briefings"]["enabled"] is True


def test_load_config_populates_briefings_defaults(tmp_path):
    from mnemo.core.config import load_config

    cfg_path = tmp_path / "mnemo.config.json"
    cfg_path.write_text('{"vaultRoot": "/tmp/test"}')
    cfg = load_config(cfg_path)

    assert cfg["briefings"]["enabled"] is True


def test_user_override_of_briefings_enabled_preserved(tmp_path):
    from mnemo.core.config import load_config

    cfg_path = tmp_path / "mnemo.config.json"
    cfg_path.write_text('{"briefings": {"enabled": false}}')
    cfg = load_config(cfg_path)

    assert cfg["briefings"]["enabled"] is False


def test_injection_defaults_enabled():
    from mnemo.core.config import DEFAULTS

    assert "injection" in DEFAULTS
    assert DEFAULTS["injection"]["enabled"] is True


def test_load_config_populates_injection_defaults(tmp_path):
    from mnemo.core.config import load_config

    cfg_path = tmp_path / "mnemo.config.json"
    cfg_path.write_text('{"vaultRoot": "/tmp/test"}')
    cfg = load_config(cfg_path)

    assert cfg["injection"]["enabled"] is True


def test_user_override_of_injection_enabled_preserved(tmp_path):
    from mnemo.core.config import load_config

    cfg_path = tmp_path / "mnemo.config.json"
    cfg_path.write_text('{"injection": {"enabled": false}}')
    cfg = load_config(cfg_path)

    assert cfg["injection"]["enabled"] is False


def test_defaults_include_activation_blocks():
    from mnemo.core.config import DEFAULTS

    assert "enforcement" in DEFAULTS
    assert DEFAULTS["enforcement"]["enabled"] is True
    assert DEFAULTS["enforcement"]["log"]["maxBytes"] == 1_048_576

    assert "enrichment" in DEFAULTS
    assert DEFAULTS["enrichment"]["enabled"] is True
    assert DEFAULTS["enrichment"]["maxRulesPerCall"] == 3
    assert DEFAULTS["enrichment"]["bodyPreviewChars"] == 300
    assert DEFAULTS["enrichment"]["log"]["maxBytes"] == 1_048_576
