"""What one machine remembers about its own syncing (#642).

Two files under ``.mnemo/``, both local and never tracked:

* ``sync-state.json`` — whether sync is on here, when the last pull and push
  succeeded, what was refused, the last error;
* ``sync-log.jsonl`` — one row per pull and push: time, outcome, duration.

And the three tracked files under ``.mnemo/sync/`` that every machine reads:
the primary record, the format version and the config seed.
"""
from __future__ import annotations

import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.log_utils import rotate_if_needed
from mnemo.core.sync import tracked

#: The layout of the hub this mnemo reads and writes. A hub recording any
#: other number is one a newer mnemo wrote, and is left alone.
FORMAT_VERSION = 1

STATE_FILE = ".mnemo/sync-state.json"
LOG_FILE = ".mnemo/sync-log.jsonl"
LOCK_DIR = ".mnemo/sync.lock"
#: In ``.git/``: says the repository is sync's own (:mod:`mnemo.core.sync.engine`).
REPO_MARKER = "mnemo-sync"
#: The branch sync commits on, ``engine.LOCAL_REF``.
_BRANCH_REF = "refs/heads/main"
_LOG_MAX_BYTES = 1_048_576

PRIMARY = "primary"
SATELLITE = "satellite"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seconds_since(stamp: Optional[str]) -> Optional[int]:
    """Whole seconds since an ``now_iso`` stamp, or ``None``."""
    if not stamp:
        return None
    try:
        then = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return max(0, int((datetime.now(timezone.utc) - then).total_seconds()))


def sanitise_machine_id(name: str) -> str:
    """Lower-case letters, digits and single dashes: safe in a file and a commit."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")[:63]


def machine_id(cfg: Dict[str, Any]) -> str:
    """This machine's id: ``sync.machineId`` when set, else the hostname."""
    section = cfg.get("sync") if isinstance(cfg, dict) else None
    configured = section.get("machineId") if isinstance(section, dict) else None
    if isinstance(configured, str) and sanitise_machine_id(configured):
        return sanitise_machine_id(configured)
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    return sanitise_machine_id(host) or "machine"


def lock_path(vault: Path) -> Path:
    """The directory whose existence means a sync is in flight on this machine."""
    return Path(vault).joinpath(*LOCK_DIR.split("/"))


def _path(vault: Path, rel: str) -> Path:
    return Path(vault).joinpath(*rel.split("/"))


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: Dict[str, Any], *, durable: bool = False) -> None:
    atomic_write_bytes(path, (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"),
                       durable=durable)


def _read_state(vault: Path) -> Tuple[Dict[str, Any], Optional[str]]:
    """The state file's contents, and what is wrong with it when it is there
    but cannot be read. A missing file is an empty state, not a problem."""
    try:
        raw = _path(vault, STATE_FILE).read_bytes()
    except FileNotFoundError:
        return {}, None
    except OSError as exc:
        return {}, f"cannot be read ({exc.__class__.__name__})"
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError:  # UnicodeDecodeError is one
        if raw and not raw.strip(b"\0"):
            return {}, f"holds {len(raw)} NUL bytes and nothing else"
        return {}, f"is not JSON ({len(raw)} bytes)"
    if not isinstance(data, dict):
        return {}, "holds JSON that is not an object"
    return data, None


def load(vault: Path) -> Dict[str, Any]:
    return _read_state(vault)[0]


def problem(vault: Path) -> Optional[str]:
    """Why the state file cannot be read, or ``None`` when it can or is absent."""
    trouble = _read_state(vault)[1]
    return f"{STATE_FILE} {trouble}" if trouble else None


def save(vault: Path, state: Dict[str, Any]) -> None:
    # Flushed before the rename: on NTFS a crash can keep the rename and lose
    # the bytes, leaving a file of the right size that is all NUL (#735).
    _write_json(_path(vault, STATE_FILE), state, durable=True)


def synced_before(vault: Path) -> bool:
    """Whether sync made this repository and committed in it: its marker in
    ``.git/`` and its branch. What ``init`` and ``join`` leave, read from
    the files alone."""
    git_dir = Path(vault) / ".git"
    if not (git_dir / REPO_MARKER).is_file():
        return False
    if git_dir.joinpath(*_BRANCH_REF.split("/")).is_file():
        return True
    try:
        packed = (git_dir / "packed-refs").read_bytes()
    except OSError:
        return False
    return any(line.endswith(b" " + _BRANCH_REF.encode("ascii")) for line in packed.splitlines())


def enabled(vault: Path) -> bool:
    """Whether this vault was ever initialised or joined on this machine.

    A state file that is there but cannot be read — a crash can leave it all
    NUL bytes (#735) — does not turn sync off when :func:`synced_before` says
    the vault synced: the next pull or push writes the state afresh. Read as
    off, it stopped every pull and push, and the satellite guards with them.
    """
    vault = Path(vault)
    if not (vault / ".git").exists():
        return False
    data, trouble = _read_state(vault)
    if trouble is None:
        return bool(data.get("enabled"))
    return synced_before(vault)


def append_log(vault: Path, row: Dict[str, Any]) -> None:
    """One row in the local log. Never raises: a log is not worth a sync."""
    path = _path(vault, LOG_FILE)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(path, _LOG_MAX_BYTES)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass


class Timer:
    """Milliseconds since it was made."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)


# --- the tracked records ---------------------------------------------------


def primary_of(vault: Path) -> Optional[str]:
    """The machine id the vault's primary record names, or ``None``."""
    value = _read_json(_path(vault, tracked.PRIMARY_FILE)).get("machine")
    return value if isinstance(value, str) and value else None


def write_primary(vault: Path, machine: str) -> None:
    _write_json(_path(vault, tracked.PRIMARY_FILE), {"machine": machine, "since": now_iso()})


def role_of(vault: Path, machine: str) -> str:
    """A machine is the primary only while the record names its own id."""
    return PRIMARY if primary_of(vault) == machine else SATELLITE


def parse_format(raw: Optional[bytes]) -> Optional[int]:
    """The version a format record holds; ``None`` when it holds none."""
    if raw is None:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    version = data.get("version") if isinstance(data, dict) else None
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def write_format(vault: Path) -> None:
    path = _path(vault, tracked.FORMAT_FILE)
    if parse_format(path.read_bytes() if path.is_file() else None) != FORMAT_VERSION:
        _write_json(path, {"version": FORMAT_VERSION})


def write_seed(vault: Path, config_path: Optional[Path]) -> None:
    """Publish the seed from this machine's config, rewriting only on change."""
    from mnemo.core.sync.seed import build_seed

    raw = _read_json(Path(config_path)) if config_path is not None else {}
    data = (json.dumps(build_seed(raw), indent=2, sort_keys=True) + "\n").encode("utf-8")
    path = _path(vault, tracked.SEED_FILE)
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    atomic_write_bytes(path, data)


def read_seed(vault: Path) -> Dict[str, Any]:
    return _read_json(_path(vault, tracked.SEED_FILE))
