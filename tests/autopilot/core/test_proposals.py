import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mnemo.autopilot.core.proposals import (
    write_proposal,
    Proposal,
    list_proposals,
    update_status,
    expire_old,
)


def test_write_proposal_creates_file(tmp_path: Path):
    p = write_proposal(
        vault_root=tmp_path,
        kind="rule_candidate",
        source="tier0.miss_detector",
        payload={"slug_hint": "foo-bar", "reason": "miss in recall"},
        project="mnemo",
        confidence=0.42,
    )
    assert isinstance(p, Proposal)
    assert p.kind == "rule_candidate"
    assert p.source == "tier0.miss_detector"
    assert p.project == "mnemo"
    assert p.confidence == 0.42
    assert p.status == "pending"
    assert p.created_at.endswith("Z")
    assert p.applied_pr is None

    files = list((tmp_path / ".mnemo" / "proposals").iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["schema_version"] == 1
    assert data["id"] == p.id
    assert data["payload"]["slug_hint"] == "foo-bar"


def test_write_proposal_id_format(tmp_path: Path):
    p = write_proposal(
        vault_root=tmp_path, kind="dead_rule", source="x", payload={}
    )
    # id format: YYYY-MM-DDTHH-MM-SSZ-<6hex>
    parts = p.id.split("-")
    assert len(parts) >= 4
    assert p.id.endswith(parts[-1])
    assert len(parts[-1]) == 6


def test_write_proposal_rejects_unknown_kind(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown kind"):
        write_proposal(
            vault_root=tmp_path, kind="not_a_kind", source="x", payload={}
        )


def test_list_proposals_filters(tmp_path: Path):
    write_proposal(vault_root=tmp_path, kind="rule_candidate", source="a",
                   payload={}, project="mnemo")
    write_proposal(vault_root=tmp_path, kind="dead_rule", source="b",
                   payload={}, project="mnemo")
    write_proposal(vault_root=tmp_path, kind="rule_candidate", source="c",
                   payload={}, project="other")

    all_p = list_proposals(vault_root=tmp_path)
    assert len(all_p) == 3

    by_kind = list_proposals(vault_root=tmp_path, kind="rule_candidate")
    assert len(by_kind) == 2

    by_proj = list_proposals(vault_root=tmp_path, project="mnemo")
    assert len(by_proj) == 2

    by_both = list_proposals(vault_root=tmp_path, kind="dead_rule", project="mnemo")
    assert len(by_both) == 1


def test_list_proposals_empty_when_dir_missing(tmp_path: Path):
    assert list_proposals(vault_root=tmp_path) == []


def test_update_status_persists(tmp_path: Path):
    p = write_proposal(vault_root=tmp_path, kind="doctor_warning", source="x",
                       payload={"warning": "foo"})
    updated = update_status(vault_root=tmp_path, proposal_id=p.id,
                            status="applied", applied_pr=99)
    assert updated.status == "applied"
    assert updated.applied_pr == 99
    assert updated.decided_at is not None

    reread = list_proposals(vault_root=tmp_path)[0]
    assert reread.status == "applied"
    assert reread.applied_pr == 99


def test_update_status_rejects_unknown_status(tmp_path: Path):
    p = write_proposal(vault_root=tmp_path, kind="doctor_warning", source="x",
                       payload={})
    with pytest.raises(ValueError, match="unknown status"):
        update_status(vault_root=tmp_path, proposal_id=p.id, status="bogus")


def test_update_status_raises_when_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        update_status(vault_root=tmp_path, proposal_id="nope", status="applied")


def test_expire_old_marks_pending_only(tmp_path: Path, monkeypatch):
    p1 = write_proposal(vault_root=tmp_path, kind="rule_candidate", source="x",
                        payload={})
    p2 = write_proposal(vault_root=tmp_path, kind="rule_candidate", source="y",
                        payload={})

    # backdate p1 by 40 days, leave p2 fresh
    from mnemo.autopilot.core._dirs import proposals_dir as _pd
    f1 = _pd(tmp_path) / f"{p1.id}.json"
    data = json.loads(f1.read_text())
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    data["created_at"] = old_ts
    f1.write_text(json.dumps(data, indent=2, sort_keys=True))

    # mark p2 as already applied — expire_old must not touch it
    update_status(vault_root=tmp_path, proposal_id=p2.id, status="applied")
    p3 = write_proposal(vault_root=tmp_path, kind="rule_candidate", source="z",
                        payload={})

    n = expire_old(vault_root=tmp_path, days=30)
    assert n == 1

    statuses = {p.id: p.status for p in list_proposals(vault_root=tmp_path)}
    assert statuses[p1.id] == "expired"
    assert statuses[p2.id] == "applied"
    assert statuses[p3.id] == "pending"
