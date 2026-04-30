"""Proposal queue for autopilot Tiers.

Storage: one JSON file per proposal under ``<vault>/.mnemo/proposals/``,
named ``<UTC-timestamp>-<6hex>.json``. Lockless append-only — parallel
agents can write concurrently; ID collisions are resolved by suffixing.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
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
