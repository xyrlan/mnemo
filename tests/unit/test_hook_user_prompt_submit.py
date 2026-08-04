"""UserPromptSubmit retrieval flow + silence reasons + dedupe."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import user_prompt_submit as hook


def _run_hook(stdin_payload: dict) -> tuple[int, str]:
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))), \
         patch("sys.stdout", out):
        rc = hook.main()
    return rc, out.getvalue()


def test_hook_returns_silence_on_disabled_reflex(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": False},
    }))

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault),
        "session_id": "sid-xyz",
        "prompt": "Use Prisma mock for the new test",
    })
    assert rc == 0
    assert stdout == ""


def test_hook_returns_silence_on_short_prompt(tmp_vault, monkeypatch):
    # Pre-gate: < 3 distinct non-stopword tokens.
    _enable_reflex(tmp_vault, monkeypatch)
    rc, stdout = _run_hook({
        "cwd": str(tmp_vault), "session_id": "sid", "prompt": "ok",
    })
    assert rc == 0 and stdout == ""


def test_hook_emits_on_confident_match(tmp_vault, monkeypatch, synthetic_index):
    """Uses fixture that writes a reflex-index.json whose top match is 'use-prisma-mock'."""
    _enable_reflex(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault),
        "session_id": "sid-1",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })
    assert rc == 0
    payload = json.loads(stdout)
    text = payload["hookSpecificOutput"]["additionalContext"]
    assert "mnemo reflex context:" in text
    assert "[[use-prisma-mock]]" in text


def _log_entries(vault) -> list[dict]:
    path = vault / ".mnemo" / "reflex-log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_a_silenced_decision_records_the_scores_that_silenced_it(
    tmp_vault, monkeypatch, synthetic_index
):
    """Without this the gate name is unfalsifiable.

    `relative_gap_fail` is the most common outcome on a real vault and the log
    recorded it with `scores: []` — the ranking that produced it was computed
    and discarded, so nobody could see which rule nearly fired or what beat it.
    """
    _enable_reflex(tmp_vault, monkeypatch, thresholds={"absoluteFloor": 999.0})
    synthetic_index(tmp_vault)

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault), "session_id": "sid-r",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })

    assert rc == 0 and stdout == ""
    entry = _log_entries(tmp_vault)[-1]
    assert entry["silence_reason"] == "absolute_floor_fail"
    assert entry["candidates"], "the ranking that caused the silence was discarded"
    top_slug, top_score = entry["candidates"][0]
    assert top_slug == "use-prisma-mock"
    assert isinstance(top_score, float) and top_score > 0
    assert entry["thresholds"]["absolute_floor"] == 999.0


def test_the_recorded_ranking_is_capped_so_the_log_stays_small(
    tmp_vault, monkeypatch, synthetic_index
):
    """The log rotates at 1MB; a full ranking per prompt would shrink the
    window every other consumer reads."""
    _enable_reflex(tmp_vault, monkeypatch, thresholds={"absoluteFloor": 999.0})
    synthetic_index(tmp_vault)

    _run_hook({"cwd": str(tmp_vault), "session_id": "s",
               "prompt": "How do I mock prisma in a jest test with typescript"})

    assert len(_log_entries(tmp_vault)[-1]["candidates"]) <= 3


def test_near_misses_never_land_in_the_emitted_field(
    tmp_vault, monkeypatch, synthetic_index
):
    """`dead_rule_sweep` counts `emitted` as proof a rule is alive.

    A suppressed candidate written there would keep dead rules alive forever —
    the receipt has to be a separate key.
    """
    _enable_reflex(tmp_vault, monkeypatch, thresholds={"absoluteFloor": 999.0})
    synthetic_index(tmp_vault)

    _run_hook({"cwd": str(tmp_vault), "session_id": "s",
               "prompt": "How do I mock prisma in a jest test with typescript"})

    entry = _log_entries(tmp_vault)[-1]
    assert entry["emitted"] == []
    assert entry["scores"] == []


def test_a_pre_scoring_silence_records_no_candidates(tmp_vault, monkeypatch):
    """Nothing was scored, so there is nothing honest to put there."""
    _enable_reflex(tmp_vault, monkeypatch)

    _run_hook({"cwd": str(tmp_vault), "session_id": "s", "prompt": "ok"})

    entry = _log_entries(tmp_vault)[-1]
    assert entry["silence_reason"] == "below_min_tokens"
    assert not entry.get("candidates")


def test_an_emission_records_the_runners_up_too(
    tmp_vault, monkeypatch, synthetic_index
):
    """"What else nearly fired" is the interesting half of a success."""
    _enable_reflex(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault), "session_id": "sid-e",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })

    assert rc == 0 and stdout
    entry = _log_entries(tmp_vault)[-1]
    assert entry["emitted"] == ["use-prisma-mock"]
    assert entry["candidates"][0][0] == "use-prisma-mock"


def _enable_reflex(vault, monkeypatch, thresholds=None):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    reflex: dict = {"enabled": True}
    if thresholds:
        reflex["thresholds"] = thresholds
    (vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(vault),
        "reflex": reflex,
    }))
