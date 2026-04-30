"""Proposal queue for autopilot Tiers.

Storage: one JSON file per proposal under ``<vault>/.mnemo/proposals/``,
named ``<UTC-timestamp>-<6hex>.json``. Lockless append-only — parallel
agents can write concurrently; ID collisions are resolved by suffixing.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from mnemo.autopilot.core._dirs import ensure_proposals_dir, proposals_dir

SCHEMA_VERSION = 1

ProposalKind = Literal[
    "rule_candidate",
    "dead_rule",
    "doctor_warning",
    "bm25_tune",
    "telemetry_bug",
]
ProposalStatus = Literal["pending", "accepted", "rejected", "applied", "expired"]

_VALID_KINDS = {"rule_candidate", "dead_rule", "doctor_warning", "bm25_tune", "telemetry_bug"}
_VALID_STATUSES = {"pending", "accepted", "rejected", "applied", "expired"}


@dataclass
class Proposal:
    id: str
    kind: str
    source: str
    project: Optional[str]
    confidence: float
    payload: dict[str, Any]
    status: str
    created_at: str
    decided_at: Optional[str] = None
    applied_pr: Optional[int] = None
    schema_version: int = SCHEMA_VERSION


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_id(now: str) -> str:
    safe = now.replace(":", "-")
    return f"{safe}-{secrets.token_hex(3)}"


def _path_for(vault_root: Path, proposal_id: str) -> Path:
    return proposals_dir(vault_root) / f"{proposal_id}.json"


def write_proposal(
    *,
    vault_root: Path,
    kind: str,
    source: str,
    payload: dict[str, Any],
    project: Optional[str] = None,
    confidence: float = 0.0,
) -> Proposal:
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown kind: {kind!r} (valid: {sorted(_VALID_KINDS)})")
    ensure_proposals_dir(vault_root)
    now = _now_iso()
    proposal_id = _make_id(now)
    target = _path_for(vault_root, proposal_id)
    while target.exists():
        proposal_id = _make_id(now)
        target = _path_for(vault_root, proposal_id)

    p = Proposal(
        id=proposal_id,
        kind=kind,
        source=source,
        project=project,
        confidence=confidence,
        payload=payload,
        status="pending",
        created_at=now,
    )
    target.write_text(json.dumps(asdict(p), indent=2, sort_keys=True))
    return p


def _read_one(path: Path) -> Proposal:
    data = json.loads(path.read_text())
    return Proposal(
        id=data["id"],
        kind=data["kind"],
        source=data["source"],
        project=data.get("project"),
        confidence=float(data.get("confidence", 0.0)),
        payload=data.get("payload", {}),
        status=data.get("status", "pending"),
        created_at=data["created_at"],
        decided_at=data.get("decided_at"),
        applied_pr=data.get("applied_pr"),
        schema_version=data.get("schema_version", SCHEMA_VERSION),
    )


def list_proposals(
    *,
    vault_root: Path,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    project: Optional[str] = None,
) -> list[Proposal]:
    pdir = proposals_dir(vault_root)
    if not pdir.exists():
        return []
    items: list[Proposal] = []
    for f in sorted(pdir.iterdir()):
        if not f.name.endswith(".json"):
            continue
        try:
            p = _read_one(f)
        except (json.JSONDecodeError, KeyError):
            continue
        if status is not None and p.status != status:
            continue
        if kind is not None and p.kind != kind:
            continue
        if project is not None and p.project != project:
            continue
        items.append(p)
    return items


def update_status(
    *,
    vault_root: Path,
    proposal_id: str,
    status: str,
    applied_pr: Optional[int] = None,
) -> Proposal:
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown status: {status!r} (valid: {sorted(_VALID_STATUSES)})")
    target = _path_for(vault_root, proposal_id)
    if not target.exists():
        raise FileNotFoundError(f"proposal {proposal_id!r} not found at {target}")
    data = json.loads(target.read_text())
    data["status"] = status
    data["decided_at"] = _now_iso()
    if applied_pr is not None:
        data["applied_pr"] = applied_pr
    target.write_text(json.dumps(data, indent=2, sort_keys=True))
    return _read_one(target)


def expire_old(*, vault_root: Path, days: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    for p in list_proposals(vault_root=vault_root, status="pending"):
        try:
            created = datetime.strptime(p.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if created < cutoff:
            update_status(vault_root=vault_root, proposal_id=p.id, status="expired")
            count += 1
    return count
