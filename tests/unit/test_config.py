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
