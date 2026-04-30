import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.frozen_recall import (
    FrozenSetMissing,
    freeze_current,
    load_frozen,
    frozen_path,
)


def _write_recall(vault_root: Path, payload: dict) -> Path:
    d = vault_root / ".mnemo"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "recall-cases.json"
    p.write_text(json.dumps(payload))
    return p


def test_freeze_copies_current(tmp_path: Path):
    _write_recall(tmp_path, {"cases": [{"id": "a"}], "v": 1})
    out = freeze_current(vault_root=tmp_path)
    assert out == frozen_path(vault_root=tmp_path)
    assert json.loads(out.read_text()) == {"cases": [{"id": "a"}], "v": 1}


def test_freeze_is_idempotent_unless_force(tmp_path: Path):
    _write_recall(tmp_path, {"v": 1})
    freeze_current(vault_root=tmp_path)
    # mutate source
    _write_recall(tmp_path, {"v": 2})
    freeze_current(vault_root=tmp_path)
    assert json.loads(load_frozen(vault_root=tmp_path).read()) == {"v": 1}

    freeze_current(vault_root=tmp_path, force=True)
    assert json.loads(load_frozen(vault_root=tmp_path).read()) == {"v": 2}


def test_load_frozen_raises_when_missing(tmp_path: Path):
    with pytest.raises(FrozenSetMissing):
        load_frozen(vault_root=tmp_path)


def test_freeze_raises_when_recall_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        freeze_current(vault_root=tmp_path)
