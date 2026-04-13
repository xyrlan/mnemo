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
    assert cfg["capture"]["userPrompt"] is True
    assert cfg["capture"]["fileEdits"] is True
    assert cfg["agent"]["strategy"] == "git-root"
    assert cfg["async"]["userPrompt"] is True
    assert cfg["async"]["postToolUse"] is True


def test_user_overrides_defaults(tmp_path: Path):
    p = tmp_path / "mnemo.config.json"
    p.write_text(json.dumps({
        "vaultRoot": "~/somewhere",
        "capture": {"userPrompt": False},
    }))
    cfg = config.load_config(p)
    assert cfg["vaultRoot"] == "~/somewhere"
    assert cfg["capture"]["userPrompt"] is False
    # Other capture keys still get defaults
    assert cfg["capture"]["sessionStartEnd"] is True
    assert cfg["capture"]["fileEdits"] is True


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
    assert cfg["extraction"]["hintThreshold"] == 5
    assert cfg["extraction"]["preferAPI"] is False
    assert cfg["extraction"]["subprocessTimeout"] == 60
    assert cfg["extraction"]["costSoftCap"] is None


def test_extraction_user_override_preserved(tmp_path):
    from mnemo.core import config
    p = tmp_path / "cfg.json"
    p.write_text('{"extraction": {"model": "claude-sonnet-4-6", "chunkSize": 5}}')
    cfg = config.load_config(p)
    assert cfg["extraction"]["model"] == "claude-sonnet-4-6"
    assert cfg["extraction"]["chunkSize"] == 5
    # Defaults still present for non-overridden keys
    assert cfg["extraction"]["hintThreshold"] == 5
    assert cfg["extraction"]["subprocessTimeout"] == 60
