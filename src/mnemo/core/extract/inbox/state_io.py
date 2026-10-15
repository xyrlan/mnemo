"""State-file load + atomic write for the v0.2 extraction inbox.

Hosts the canonical ``SCHEMA_VERSION`` constant (single source of truth
for the on-disk extraction state file). ``extract/scanner.py`` imports
this constant rather than re-declaring it (D5 consolidation, v0.9 PR I).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from mnemo.core.extract.inbox.types import ExtractionIOError
from mnemo.core.extract.scanner import ExtractionState, StateEntry

SCHEMA_VERSION = 2


class StateSchemaError(Exception):
    """Unknown or incompatible state file schema version."""


def replica_path(path: Path) -> Path:
    """The second copy of the state file at *path* (#665)."""
    return path.with_name(f"{path.name}.replica")


def write_state_bytes(path: Path, data: bytes) -> None:
    """Write the extraction state *data* to *path*, then to its replica.

    The one door every writer of ``extraction-state.json`` goes through, so
    the replica never lags the file it backs (#665). Each write is atomic on
    its own; a crash between the two leaves the replica one write behind,
    which only matters if the main file is then lost as well. Raises
    :class:`OSError`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    for dest in (path, replica_path(path)):
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        try:
            tmp.write_bytes(data)
            os.replace(tmp, dest)
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise


def atomic_write_state(state: ExtractionState, path: Path) -> None:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "last_run": state.last_run,
        "entries": {
            k: {
                "source_files": v.source_files,
                "source_hash": v.source_hash,
                "written_hash": v.written_hash,
                "written_at": v.written_at,
                "last_sync": v.last_sync,
                "status": v.status,
                "origin_backfill": v.origin_backfill,
                "unverified_feedback": v.unverified_feedback,
            }
            for k, v in state.entries.items()
        },
    }
    try:
        write_state_bytes(path, json.dumps(payload, indent=2).encode("utf-8"))
    except OSError as exc:
        raise ExtractionIOError(f"failed to write state file: {exc}") from exc


def _read_payload(path: Path):
    """The parsed state at *path*, or None when it is missing or unreadable."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def load_state(path: Path) -> ExtractionState:
    """Read the extraction state at *path*.

    A missing or corrupt file is not an empty ledger while its replica (see
    :func:`write_state_bytes`) still reads: with no entries every page the
    extractor promoted would read as a person's (#248) and never be refreshed
    again, a ``dismissed`` page would come back, and a backfill origin would
    be forgotten (#665). So the replica is read instead and copied back over
    *path*. A corrupt file is kept as ``.bak.<stamp>`` either way.
    """
    if path.exists():
        payload = _read_payload(path)
        if payload is None:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            try:
                path.rename(path.with_name(f"{path.name}.bak.{stamp}"))
            except OSError:
                pass
    else:
        payload = None
    if payload is None:
        replica = replica_path(path)
        payload = _read_payload(replica) if replica.exists() else None
        if payload is None:
            return ExtractionState(last_run=None, entries={}, schema_version=SCHEMA_VERSION)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(replica, path)
        except OSError:
            pass

    version = int(payload.get("schema_version", 0) or 0)
    if version > SCHEMA_VERSION:
        raise StateSchemaError(
            f"state file schema_version={version} was written by a newer mnemo version"
        )
    if version < 1:
        raise StateSchemaError(
            f"state file schema_version={version}, this mnemo supports {SCHEMA_VERSION}"
        )

    entries: dict[str, StateEntry] = {}
    for k, v in payload.get("entries", {}).items():
        written_at = str(v.get("written_at") or "")
        last_sync = str(v.get("last_sync") or written_at)
        entries[k] = StateEntry(
            source_files=list(v.get("source_files") or []),
            source_hash=str(v.get("source_hash") or ""),
            written_hash=str(v.get("written_hash") or ""),
            written_at=written_at,
            status=str(v.get("status") or "inbox"),
            last_sync=last_sync,
            # Additive optional field (Task 9b), NOT a schema bump: it is
            # readable by older mnemo (unknown keys are ignored here) and
            # absent in files older mnemo wrote, which load as False.
            # Bumping SCHEMA_VERSION would instead make every older mnemo
            # raise StateSchemaError on a vault a newer one has touched — a
            # hard downgrade break, versus a field that merely goes missing.
            #
            # Missing, though, is not free: an older mnemo's
            # ``atomic_write_state`` builds the payload without this key, so a
            # single run of an older binary ERASES the flag from every entry.
            # The only backstop is the staged page in ``shared/_inbox/``, which
            # still carries ``origin: backfill`` and is what
            # ``apply._resolve_sticky_origin`` reads — which is also why
            # ``_force_clear_inbox_cluster_dirs`` must not delete it.
            origin_backfill=bool(v.get("origin_backfill", False)),
            # Same older-binary erasure risk as origin_backfill above; the
            # same backstop applies, via ``demotion.is_demoted_markdown``
            # on the staged page (#177).
            unverified_feedback=bool(v.get("unverified_feedback", False)),
        )
    return ExtractionState(
        last_run=payload.get("last_run"),
        entries=entries,
        schema_version=SCHEMA_VERSION,
    )
