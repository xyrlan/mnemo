"""Authoritative on/off/paused state for autopilot."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from mnemo.autopilot.core._dirs import (
    autopilot_state_path,
    ensure_autopilot_dir,
)

SCHEMA_VERSION = 1
State = Literal["on", "off", "paused"]
_VALID_STATES = {"on", "off", "paused"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(vault_root: Path) -> dict:
    path = autopilot_state_path(vault_root)
    if not path.exists():
        # Default = "on" so fresh installs get the full autopilot loop without
        # opt-in. Explicit `mnemo autopilot off` writes the state file, so a
        # user's prior choice is preserved across upgrades — only vaults that
        # never wrote a state file inherit the new default.
        return {
            "schema_version": SCHEMA_VERSION,
            "state": "on",
            "paused_until": None,
            "last_changed_at": None,
            "last_changed_by": None,
        }
    return json.loads(path.read_text())


def get_state(*, vault_root: Path) -> str:
    return _read(vault_root)["state"]


def is_active(*, vault_root: Path) -> bool:
    data = _read(vault_root)
    if data["state"] == "on":
        return True
    if data["state"] == "off":
        return False
    # paused: active iff paused_until expired
    pu = data.get("paused_until")
    if not pu:
        return False
    try:
        until = datetime.strptime(pu, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return datetime.now(timezone.utc) > until


def set_state(
    *,
    vault_root: Path,
    state: str,
    paused_until: Optional[str] = None,
    source: str = "cli",
) -> None:
    if state not in _VALID_STATES:
        raise ValueError(f"unknown state: {state!r} (valid: {sorted(_VALID_STATES)})")
    ensure_autopilot_dir(vault_root)
    data = {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "paused_until": paused_until,
        "last_changed_at": _now_iso(),
        "last_changed_by": source,
    }
    autopilot_state_path(vault_root).write_text(
        json.dumps(data, indent=2, sort_keys=True)
    )
