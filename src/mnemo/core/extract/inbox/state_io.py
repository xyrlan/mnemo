"""State-file load + atomic write for the v0.2 extraction inbox.

Hosts the canonical ``SCHEMA_VERSION`` constant (single source of truth
for the on-disk extraction state file). ``extract/scanner.py`` imports
this constant rather than re-declaring it (D5 consolidation, v0.9 PR I).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from mnemo.core.extract.inbox.types import ExtractionIOError
from mnemo.core.extract.scanner import ExtractionState, StateEntry

SCHEMA_VERSION = 2


class StateSchemaError(Exception):
    """Unknown or incompatible state file schema version."""


def atomic_write_state(state: ExtractionState, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
            }
            for k, v in state.entries.items()
        },
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_bytes(json.dumps(payload, indent=2).encode("utf-8"))
        os.replace(tmp, path)
    except OSError as exc:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        raise ExtractionIOError(f"failed to write state file: {exc}") from exc


def load_state(path: Path) -> ExtractionState:
    if not path.exists():
        return ExtractionState(last_run=None, entries={}, schema_version=SCHEMA_VERSION)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # Back up and return empty
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        try:
            path.rename(path.with_name(f"{path.name}.bak.{stamp}"))
        except OSError:
            pass
        return ExtractionState(last_run=None, entries={}, schema_version=SCHEMA_VERSION)

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
        )
    return ExtractionState(
        last_run=payload.get("last_run"),
        entries=entries,
        schema_version=SCHEMA_VERSION,
    )
