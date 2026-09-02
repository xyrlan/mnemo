"""The two numbers the README quotes, read back out of the vault.

Every figure in the README has to be one ``mnemo status`` can print from the
user's own vault; these tests pin the readers that make that true.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.core import numbers

NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)


def _ts(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_log(vault: Path, lines: list, *, name: str = "reflex-log.jsonl") -> Path:
    path = vault / ".mnemo" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(
        raw if isinstance(raw, str) else json.dumps(raw) for raw in lines
    )
    path.write_text(payload + "\n", encoding="utf-8")
    return path


def _write_report(vault: Path, payload) -> Path:
    path = vault / ".mnemo" / "recall-report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return path


# ── reflex_emit_rate ────────────────────────────────────────────────────────

def test_emit_rate_counts_emitted_over_total(tmp_path: Path):
    vault = tmp_path / "v"
    _write_log(vault, [
        {"ts": _ts(1), "emitted": ["a"], "project": "p"},
        {"ts": _ts(2), "emitted": [], "project": "p", "silence_reason": "relative_gap_fail"},
        {"ts": _ts(3), "emitted": [], "project": "p", "silence_reason": "no_candidates"},
    ])
    assert numbers.reflex_emit_rate(vault, now=NOW) == (1, 3)


def test_emit_rate_ignores_rows_outside_the_window(tmp_path: Path):
    vault = tmp_path / "v"
    _write_log(vault, [
        {"ts": _ts(1), "emitted": ["a"]},
        {"ts": _ts(13.5), "emitted": []},
        {"ts": _ts(20), "emitted": ["old"]},   # outside — must not count
        {"ts": _ts(40), "emitted": []},        # outside — must not count
    ])
    assert numbers.reflex_emit_rate(vault, now=NOW) == (1, 2)


def test_emit_rate_honours_a_custom_window(tmp_path: Path):
    vault = tmp_path / "v"
    _write_log(vault, [
        {"ts": _ts(1), "emitted": ["a"]},
        {"ts": _ts(9), "emitted": ["b"]},
    ])
    assert numbers.reflex_emit_rate(vault, days=7, now=NOW) == (1, 1)


def test_emit_rate_skips_corrupt_lines(tmp_path: Path):
    vault = tmp_path / "v"
    _write_log(vault, [
        {"ts": _ts(1), "emitted": ["a"]},
        "{not json at all",
        "",
        "[1, 2, 3]",                    # valid JSON, wrong shape
        {"emitted": ["a"]},             # no ts → cannot be placed in the window
        {"ts": "gibberish", "emitted": ["a"]},
        {"ts": _ts(2), "emitted": []},
    ])
    assert numbers.reflex_emit_rate(vault, now=NOW) == (1, 2)


def test_emit_rate_counts_a_missing_emitted_key_as_a_silence(tmp_path: Path):
    vault = tmp_path / "v"
    _write_log(vault, [
        {"ts": _ts(1), "silence_reason": "reflex_disabled"},
        {"ts": _ts(1), "emitted": ["a"]},
    ])
    assert numbers.reflex_emit_rate(vault, now=NOW) == (1, 2)


def test_emit_rate_is_none_when_the_log_is_missing(tmp_path: Path):
    assert numbers.reflex_emit_rate(tmp_path / "v", now=NOW) is None


def test_emit_rate_is_none_when_every_row_is_outside_the_window(tmp_path: Path):
    vault = tmp_path / "v"
    _write_log(vault, [
        {"ts": _ts(20), "emitted": ["a"]},
        {"ts": _ts(30), "emitted": []},
    ])
    assert numbers.reflex_emit_rate(vault, now=NOW) is None


def test_emit_rate_is_none_for_an_empty_log(tmp_path: Path):
    vault = tmp_path / "v"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / "reflex-log.jsonl").write_text("", encoding="utf-8")
    assert numbers.reflex_emit_rate(vault, now=NOW) is None


def test_emit_rate_includes_the_rotated_log(tmp_path: Path):
    """Rotation at 1MB must not silently shrink a 14-day window."""
    vault = tmp_path / "v"
    _write_log(vault, [{"ts": _ts(6), "emitted": ["a"]},
                       {"ts": _ts(5), "emitted": []}], name="reflex-log.jsonl.1")
    _write_log(vault, [{"ts": _ts(1), "emitted": []}])
    assert numbers.reflex_emit_rate(vault, now=NOW) == (1, 3)


def test_emit_rate_defaults_now_to_the_clock(tmp_path: Path):
    vault = tmp_path / "v"
    recent = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _write_log(vault, [{"ts": recent, "emitted": ["a"]}])
    assert numbers.reflex_emit_rate(vault) == (1, 1)


def test_emit_rate_never_raises_on_a_directory_in_place_of_the_log(tmp_path: Path):
    vault = tmp_path / "v"
    (vault / ".mnemo" / "reflex-log.jsonl").mkdir(parents=True)
    assert numbers.reflex_emit_rate(vault, now=NOW) is None


# ── recall_primacy ──────────────────────────────────────────────────────────

def test_recall_primacy_reads_the_report(tmp_path: Path):
    vault = tmp_path / "v"
    _write_report(vault, {
        "generated_at": "2026-09-01T09:15:00Z",
        "report": {"cases": 72, "primacy_rate_at_5": 0.4167, "mrr": 0.5},
        "results": [],
    })
    assert numbers.recall_primacy(vault) == (0.4167, 72, "2026-09-01")


def test_recall_primacy_is_none_when_missing(tmp_path: Path):
    assert numbers.recall_primacy(tmp_path / "v") is None


def test_recall_primacy_is_none_when_corrupt(tmp_path: Path):
    vault = tmp_path / "v"
    _write_report(vault, "not json")
    assert numbers.recall_primacy(vault) is None


def test_recall_primacy_is_none_without_cases(tmp_path: Path):
    vault = tmp_path / "v"
    _write_report(vault, {"generated_at": "2026-09-01T09:15:00Z",
                          "report": {"cases": 0, "primacy_rate_at_5": 0.0}})
    assert numbers.recall_primacy(vault) is None


def test_recall_primacy_is_none_without_a_rate(tmp_path: Path):
    vault = tmp_path / "v"
    _write_report(vault, {"generated_at": "2026-09-01T09:15:00Z",
                          "report": {"cases": 12}})
    assert numbers.recall_primacy(vault) is None


def test_recall_primacy_tolerates_a_missing_generated_at(tmp_path: Path):
    vault = tmp_path / "v"
    _write_report(vault, {"report": {"cases": 5, "primacy_rate_at_5": 0.6}})
    assert numbers.recall_primacy(vault) == (0.6, 5, "")


def test_recall_primacy_is_none_when_the_top_level_is_not_a_dict(tmp_path: Path):
    vault = tmp_path / "v"
    _write_report(vault, [1, 2, 3])
    assert numbers.recall_primacy(vault) is None
