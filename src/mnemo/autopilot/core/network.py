"""The one switch for anything that leaves the machine."""
from __future__ import annotations

from typing import Any, Optional

OFF_MESSAGE = "[autopilot] network off (autopilot.network.enabled=false) — skipped"


def enabled(cfg: Optional[dict[str, Any]] = None) -> bool:
    if cfg is None:
        from mnemo.core import config as config_mod
        cfg = config_mod.load_config()
    return bool(((cfg.get("autopilot") or {}).get("network") or {}).get("enabled", False))
