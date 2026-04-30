"""Per-category daily PR caps + auto-pause on consecutive closed PRs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from mnemo.autopilot.core._dirs import (
    autopilot_budget_path,
    ensure_autopilot_dir,
)
from mnemo.autopilot.core.kill_switch import is_active, set_state

SCHEMA_VERSION = 1
DAILY_CAP_PER_CATEGORY = 1
PAUSE_HOURS_AFTER_TWO_CLOSED = 24
RECENT_OUTCOMES_LIMIT = 10

Outcome = Literal["merged", "closed", "abandoned"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_start_iso() -> str:
    n = _now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _read(vault_root: Path) -> dict:
    path = autopilot_budget_path(vault_root)
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "window_start": _today_start_iso(),
            "counts": {},
            "recent_outcomes": [],
        }
    data = json.loads(path.read_text())
    # roll over if window aged out
    try:
        ws = datetime.strptime(data["window_start"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, KeyError):
        ws = _now() - timedelta(days=2)
    if (_now() - ws).total_seconds() >= 24 * 3600:
        data["window_start"] = _today_start_iso()
        data["counts"] = {}
    return data


def _write(vault_root: Path, data: dict) -> None:
    ensure_autopilot_dir(vault_root)
    autopilot_budget_path(vault_root).write_text(
        json.dumps(data, indent=2, sort_keys=True)
    )


def can_open(*, vault_root: Path, category: str) -> tuple[bool, str]:
    if not is_active(vault_root=vault_root):
        return False, "autopilot kill switch is off or paused"
    data = _read(vault_root)
    used = data["counts"].get(category, 0)
    if used >= DAILY_CAP_PER_CATEGORY:
        return False, f"daily cap reached for {category} ({used}/{DAILY_CAP_PER_CATEGORY})"
    return True, ""


def record_opened(*, vault_root: Path, category: str, pr_number: int) -> None:
    data = _read(vault_root)
    data["counts"][category] = data["counts"].get(category, 0) + 1
    _write(vault_root, data)


def record_outcome(
    *, vault_root: Path, pr_number: int, outcome: str
) -> None:
    data = _read(vault_root)
    data["recent_outcomes"].append({
        "pr": pr_number,
        "outcome": outcome,
        "ts": _now_iso(),
    })
    data["recent_outcomes"] = data["recent_outcomes"][-RECENT_OUTCOMES_LIMIT:]
    _write(vault_root, data)

    # auto-pause: if last 2 outcomes are both 'closed', pause
    last_two = data["recent_outcomes"][-2:]
    if len(last_two) == 2 and all(o["outcome"] == "closed" for o in last_two):
        until = (_now() + timedelta(hours=PAUSE_HOURS_AFTER_TWO_CLOSED)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        set_state(
            vault_root=vault_root,
            state="paused",
            paused_until=until,
            source="auto",
        )
