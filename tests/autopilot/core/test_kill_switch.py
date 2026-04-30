import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mnemo.autopilot.core.kill_switch import (
    get_state,
    is_active,
    set_state,
)


def test_default_state_is_off(tmp_path: Path):
    assert get_state(vault_root=tmp_path) == "off"
    assert is_active(vault_root=tmp_path) is False


def test_set_state_persists(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    assert get_state(vault_root=tmp_path) == "on"
    assert is_active(vault_root=tmp_path) is True

    data = json.loads((tmp_path / ".mnemo" / "autopilot.json").read_text())
    assert data["state"] == "on"
    assert data["schema_version"] == 1
    assert data["last_changed_by"] == "cli"


def test_paused_state_blocks_active_until_expiry(tmp_path: Path):
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    set_state(vault_root=tmp_path, state="paused", paused_until=until,
              source="auto")
    assert get_state(vault_root=tmp_path) == "paused"
    assert is_active(vault_root=tmp_path) is False


def test_paused_state_resumes_after_expiry(tmp_path: Path):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    set_state(vault_root=tmp_path, state="paused", paused_until=past)
    # state still reads paused, but is_active treats expiry as on-equivalent
    assert get_state(vault_root=tmp_path) == "paused"
    assert is_active(vault_root=tmp_path) is True


def test_set_state_rejects_unknown(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown state"):
        set_state(vault_root=tmp_path, state="bogus")
