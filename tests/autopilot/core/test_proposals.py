import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.proposals import write_proposal, Proposal


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
