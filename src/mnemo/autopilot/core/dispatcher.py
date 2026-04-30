"""Record-only autopilot job dispatcher.

Real CronCreate integration is intentionally deferred to whichever Tier
first needs it. For now we record intent in ``.mnemo/autopilot-jobs.json``
so tests + ``mnemo autopilot status`` can show pending jobs without
depending on the harness scheduler.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from mnemo.autopilot.core._dirs import (
    autopilot_jobs_path,
    ensure_autopilot_dir,
)

SCHEMA_VERSION = 1
NAMESPACE_PREFIX = "autopilot."


@dataclass
class JobInfo:
    name: str
    cron: str
    command: str
    created_at: str


JobHandle = JobInfo


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(vault_root: Path) -> dict:
    path = autopilot_jobs_path(vault_root)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "jobs": {}}
    return json.loads(path.read_text())


def _write(vault_root: Path, data: dict) -> None:
    ensure_autopilot_dir(vault_root)
    autopilot_jobs_path(vault_root).write_text(
        json.dumps(data, indent=2, sort_keys=True)
    )


def schedule_autopilot_job(
    *,
    vault_root: Path,
    name: str,
    cron: str,
    command: str,
) -> JobHandle:
    if not name.startswith(NAMESPACE_PREFIX):
        raise ValueError(f"job name must start with {NAMESPACE_PREFIX!r}: {name!r}")
    data = _read(vault_root)
    info = JobInfo(name=name, cron=cron, command=command, created_at=_now_iso())
    data["jobs"][name] = asdict(info)
    _write(vault_root, data)
    return info


def list_autopilot_jobs(*, vault_root: Path) -> list[JobInfo]:
    data = _read(vault_root)
    return [
        JobInfo(**v) for v in sorted(
            data.get("jobs", {}).values(), key=lambda j: j["name"]
        )
    ]


def cancel_autopilot_job(*, vault_root: Path, name: str) -> bool:
    data = _read(vault_root)
    if name not in data.get("jobs", {}):
        return False
    del data["jobs"][name]
    _write(vault_root, data)
    return True
