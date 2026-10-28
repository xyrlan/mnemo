"""Who may write what, once a vault syncs (#643).

The rule that keeps two machines from ever needing a merge is one writer per
path. The **primary** does everything it does today. A **satellite** reads the
whole vault and adds only its own sessions' briefings — one file per session,
so two machines never write the same one — and whatever a command the user
types there changes under live ``shared/``.

Everything automatic that writes a tracked file asks :func:`is_satellite`
first: extraction, the memory mirror, the weekly briefing prune, inbox apply
and expiry, auto-retire, the vault migrations, the install backfill and the
``HOME.md`` dashboard. With sync off (the default) every answer here is
``False`` at the cost of one missing-file read, so nothing changes.

:func:`satellite_may_publish` is the other half, read by the engine: what a
satellite's push may carry, and what the primary refuses when it arrives in a
satellite's commit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

from mnemo.core.sync import state as st
from mnemo.core.sync import tracked

OFF = "off"
PRIMARY = st.PRIMARY
SATELLITE = st.SATELLITE

#: The frontmatter line a briefing carries once sync is on (item 3 of #643).
MACHINE_KEY = "machine"
_MACHINE_LINE = re.compile(rb"^machine:[ \t]*([^\r\n]*)", re.MULTILINE)


def machine_of(vault: Path, cfg: Optional[Dict[str, Any]] = None) -> str:
    """This machine's id as the vault knows it.

    ``init`` and ``join`` record it in the local sync state; a vault synced
    before that was recorded falls back to the config, then the hostname.
    """
    recorded = st.load(vault).get("machineId")
    if isinstance(recorded, str) and recorded:
        return recorded
    if cfg is None:
        from mnemo.core.config import load_config

        try:
            cfg = load_config()
        except Exception:  # noqa: BLE001 — a role must always come back
            cfg = {}
    return st.machine_id(cfg)


def role(vault: Path, cfg: Optional[Dict[str, Any]] = None) -> str:
    """``off`` while the vault never synced, else ``primary`` or ``satellite``."""
    vault = Path(vault)
    if not st.enabled(vault):
        return OFF
    return st.role_of(vault, machine_of(vault, cfg))


def is_satellite(vault: Path, cfg: Optional[Dict[str, Any]] = None) -> bool:
    """True on a machine that may add only its own briefings. Never raises."""
    try:
        return role(vault, cfg) == SATELLITE
    except Exception:  # noqa: BLE001 — refusing to write is the safe side
        return True if _looks_synced(vault) else False


def is_primary(vault: Path, cfg: Optional[Dict[str, Any]] = None) -> bool:
    try:
        return role(vault, cfg) == PRIMARY
    except Exception:  # noqa: BLE001
        return False


def _looks_synced(vault: Path) -> bool:
    try:
        return (Path(vault) / ".git").exists() and st.load(vault).get("enabled") is True
    except Exception:  # noqa: BLE001
        return False


def briefing_machine(cfg: Dict[str, Any], vault: Path) -> str:
    """The id a briefing written here is stamped with, or ``""`` with sync off.

    Sync off leaves a briefing byte for byte what it was (item 1).
    """
    try:
        if not st.enabled(Path(vault)):
            return ""
        return machine_of(vault, cfg)
    except Exception:  # noqa: BLE001
        return ""


def machine_in_briefing(data: bytes) -> str:
    """The ``machine:`` a briefing's frontmatter names, or ``""``."""
    if not data.startswith(b"---"):
        return ""
    end = data.find(b"\n---", 3)
    head = data[:end] if end > 0 else data[:4096]
    found = _MACHINE_LINE.search(head)
    return found.group(1).decode("utf-8", errors="replace").strip() if found else ""


def is_briefing_path(rel: str) -> bool:
    parts = rel.split("/")
    return len(parts) >= 4 and parts[0] == "bots" and parts[2] == "briefings"


def is_live_shared_path(rel: str) -> bool:
    """A page under ``shared/`` that is neither staged nor archived."""
    parts = rel.split("/")
    return len(parts) >= 2 and parts[0] == "shared" and parts[1] not in ("_inbox", "_archive")


def satellite_may_publish(rel: str, data: Optional[bytes], machine: str) -> bool:
    """Whether a satellite's push may carry ``rel`` with content ``data``.

    Only its own sessions' briefings — the ``machine:`` line names it, and a
    briefing another machine stamped is never its — and live pages under
    ``shared/``, which only a typed command changes there. Never
    ``.mnemo/extraction-state.json``, ``HOME.md``, ``bots/*/memory``,
    ``shared/_inbox`` or sync's own records, and never a deletion (``data``
    is ``None``). A briefing with no ``machine:`` line was written by hand or
    by a mnemo from before the stamp, and is let through: the stamp is what
    this machine's own writer always adds.
    """
    if data is None or not tracked.is_tracked(rel):
        return False
    if is_live_shared_path(rel):
        return True
    if is_briefing_path(rel):
        stamped = machine_in_briefing(data)
        return not stamped or stamped == machine
    return False


def primary_refuses(rel: str, deleted: bool) -> bool:
    """Whether the primary refuses this change when it arrives from the hub.

    Everything in ``head..theirs`` on the primary was pushed by some other
    machine, so this is the mirror of :func:`satellite_may_publish` without
    the briefing ownership check: a satellite may not take anything away.
    """
    if deleted:
        return True
    return not (is_live_shared_path(rel) or is_briefing_path(rel))


__all__ = [
    "MACHINE_KEY", "OFF", "PRIMARY", "SATELLITE", "briefing_machine", "is_briefing_path",
    "is_live_shared_path", "is_primary", "is_satellite", "machine_in_briefing", "machine_of",
    "primary_refuses", "role", "satellite_may_publish",
]


def primary_last_extraction(vault: Path) -> Optional[str]:
    """When the primary last extracted the whole vault, as this machine has
    it: ``last_run`` in ``.mnemo/extraction-state.json``, which only the
    primary writes and every pull brings (#756). ``None`` when there is none
    or it cannot be read. Read-only: a diagnostic must not repair the file.
    """
    import json

    try:
        data = json.loads((Path(vault) / ".mnemo" / "extraction-state.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stamp = data.get("last_run") if isinstance(data, dict) else None
    return stamp if isinstance(stamp, str) and stamp else None
