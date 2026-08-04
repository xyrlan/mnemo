"""Reading and explaining reflex decisions — the data behind `mnemo why`.

The reflex log has always recorded *that* a prompt was silenced and under which
gate name. It never recorded the numbers, so `relative_gap_fail` — the single
most common outcome on a real vault — was unfalsifiable: no way to see which
rule nearly fired, what beat it, or by how much. These are the tests for the
layer that turns a log line into a sentence a human can act on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.reflex import receipts


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _write(vault: Path, entries: list[dict]) -> None:
    path = vault / ".mnemo" / "reflex-log.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(json.dumps(e) + "\n")


def _entry(**over) -> dict:
    base = {
        "session_id": "sid-1",
        "project": "mnemo",
        "prompt_hash": "sha256:abc123",
        "prompt_tokens": 8,
        "emitted": [],
        "scores": [],
        "silence_reason": None,
        "ts": "2026-08-04T09:30:07Z",
    }
    base.update(over)
    return base


# --- reading ---------------------------------------------------------------

def test_a_missing_log_reads_as_no_decisions(vault):
    assert receipts.read_decisions(vault) == []


def test_decisions_come_back_newest_first(vault):
    _write(vault, [
        _entry(ts="2026-08-04T09:00:00Z", prompt_hash="sha256:old"),
        _entry(ts="2026-08-04T09:30:00Z", prompt_hash="sha256:new"),
    ])
    got = receipts.read_decisions(vault)
    assert [d["prompt_hash"] for d in got] == ["sha256:new", "sha256:old"]


def test_the_limit_keeps_the_newest(vault):
    _write(vault, [_entry(ts=f"2026-08-04T09:0{i}:00Z", prompt_hash=f"h{i}")
                   for i in range(5)])
    got = receipts.read_decisions(vault, limit=2)
    assert [d["prompt_hash"] for d in got] == ["h4", "h3"]


def test_a_project_filter_excludes_other_repos(vault):
    _write(vault, [_entry(project="mnemo"), _entry(project="clubinho")])
    got = receipts.read_decisions(vault, project="mnemo")
    assert [d["project"] for d in got] == ["mnemo"]


def test_corrupt_lines_are_skipped_not_fatal(vault):
    path = vault / ".mnemo" / "reflex-log.jsonl"
    path.write_text('{"bad json\n' + json.dumps(_entry()) + "\n", encoding="utf-8")
    assert len(receipts.read_decisions(vault)) == 1


def test_the_rotated_log_is_read_when_the_live_one_is_short(vault):
    """A 1MB rotation must not make `why` forget everything a minute ago.

    ``rotate_if_needed`` renames the log to ``.jsonl.1`` the moment it crosses
    the cap, so on a busy vault the live file can be seconds old. Asking for
    the last 10 decisions and getting 2 would read as "reflex stopped running".
    """
    (vault / ".mnemo" / "reflex-log.jsonl.1").write_text(
        json.dumps(_entry(ts="2026-08-04T08:00:00Z", prompt_hash="h-old")) + "\n",
        encoding="utf-8",
    )
    _write(vault, [_entry(ts="2026-08-04T09:00:00Z", prompt_hash="h-new")])

    got = receipts.read_decisions(vault, limit=10)
    assert [d["prompt_hash"] for d in got] == ["h-new", "h-old"]


# --- explaining ------------------------------------------------------------

def test_an_emission_names_the_rule_and_its_score(vault):
    text = receipts.format_human([_entry(
        emitted=["use-prisma-mock"], scores=[4.21], silence_reason=None,
    )])
    assert "use-prisma-mock" in text
    assert "4.21" in text
    assert "injected" in text


def test_a_relative_gap_silence_shows_the_arithmetic_that_failed(vault):
    """The whole point: "relative_gap_fail" alone tells the user nothing."""
    text = receipts.format_human([_entry(
        silence_reason="relative_gap_fail",
        candidates=[["recall-degrades", 4.21], ["roadmap", 3.85]],
        thresholds={"relative_gap": 1.5, "absolute_floor": 2.0,
                    "term_overlap_min": 2},
    )])
    assert "recall-degrades" in text
    assert "4.21" in text and "3.85" in text
    assert "5.78" in text, "the score the top rule needed is the missing number"
    assert "1.5" in text


def test_an_absolute_floor_silence_names_the_floor_it_missed(vault):
    text = receipts.format_human([_entry(
        silence_reason="absolute_floor_fail",
        candidates=[["weak-rule", 1.10]],
        thresholds={"relative_gap": 1.5, "absolute_floor": 2.0,
                    "term_overlap_min": 2},
    )])
    assert "weak-rule" in text
    assert "1.1" in text and "2.0" in text


def test_a_term_overlap_silence_says_so_in_words(vault):
    text = receipts.format_human([_entry(
        silence_reason="term_overlap_fail",
        candidates=[["off-topic", 9.9]],
        thresholds={"relative_gap": 1.5, "absolute_floor": 2.0,
                    "term_overlap_min": 2},
    )])
    assert "off-topic" in text
    assert "overlap" in text.lower()


def test_a_dedupe_silence_says_it_already_fired(vault):
    """Not a retrieval failure — the rule won and was already on screen."""
    text = receipts.format_human([_entry(
        silence_reason="deduped",
        candidates=[["already-seen", 5.0]],
    )])
    assert "already-seen" in text
    assert "already" in text.lower()


def test_a_pre_scoring_silence_explains_itself_without_scores(vault):
    """`below_min_tokens` happens before any rule is scored — say that plainly
    instead of printing an empty candidate table."""
    text = receipts.format_human([_entry(
        silence_reason="below_min_tokens", prompt_tokens=2,
    )])
    assert "2" in text
    assert "candidates" not in text.lower()


def test_a_missing_index_is_explained_as_cold_start(vault):
    text = receipts.format_human([_entry(silence_reason="index_missing")])
    assert "index" in text.lower()


def test_the_session_cap_is_explained_as_a_budget_not_a_miss(vault):
    text = receipts.format_human([_entry(silence_reason="session_cap_reached")])
    low = text.lower()
    assert "cap" in low or "limit" in low


def test_an_old_entry_without_a_receipt_says_so(vault):
    """Every line logged before this feature existed has no candidates.

    Printing those as "no rule came close" would be a lie about the past — the
    scores were computed and thrown away.
    """
    text = receipts.format_human([_entry(silence_reason="relative_gap_fail")])
    assert "no receipt" in text.lower() or "not recorded" in text.lower()


def test_an_unknown_future_reason_still_prints_the_reason(vault):
    """Forward compatibility: a new gate must not render as a blank line."""
    text = receipts.format_human([_entry(silence_reason="some_new_gate")])
    assert "some_new_gate" in text


def test_no_decisions_at_all_is_a_sentence_not_an_empty_string(vault):
    text = receipts.format_human([])
    assert text.strip()


def test_the_prompt_itself_is_never_printed(vault):
    """The log stores a hash by design; `why` must not reintroduce the text."""
    text = receipts.format_human([_entry(
        emitted=["r"], scores=[3.0],
        prompt="how do I rotate the production database credentials",
    )])
    assert "production database" not in text
