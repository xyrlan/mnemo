"""Ledger: idempotency, resume, re-harvest on change, 3-strike skip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.backfill import ledger


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _transcript(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / f"{name}.jsonl"
    p.write_text(text, encoding="utf-8")
    return p


def test_unseen_transcript_should_harvest(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    assert ledger.should_harvest(led, t) is True


def test_marking_done_makes_it_skip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)
    assert ledger.should_harvest(led, t) is False


def test_survives_a_save_load_roundtrip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)
    ledger.save(vault, led)

    reloaded = ledger.load(vault)
    assert ledger.should_harvest(reloaded, t) is False


def test_changed_transcript_is_reharvested(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)

    t.write_text('{"type":"user"}\n{"type":"assistant"}\n', encoding="utf-8")
    assert ledger.should_harvest(led, t) is True


def test_three_failures_permanently_skip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    for _ in range(2):
        ledger.mark_failed(led, t, "boom")
    assert ledger.should_harvest(led, t) is True

    ledger.mark_failed(led, t, "boom")
    assert ledger.should_harvest(led, t) is False


def test_corrupt_ledger_file_starts_clean(vault, tmp_path):
    (vault / ".mnemo" / "backfill-state.json").write_text("{not json", encoding="utf-8")
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    assert ledger.should_harvest(led, t) is True


def test_save_writes_schema_version(vault, tmp_path):
    led = ledger.load(vault)
    ledger.save(vault, led)
    data = json.loads((vault / ".mnemo" / "backfill-state.json").read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1
    assert "sessions" in data
