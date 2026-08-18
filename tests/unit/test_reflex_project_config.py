"""Per-project reflex threshold overrides.

The autopilot calibrator has been writing .mnemo/reflex-config.{project}.json
since v0.17 targeting a 3-12% emit rate — but nothing on the hook path ever
read them, so every calibration was dead on arrival. These tests pin the read
side: the hook consumes the file, the file wins over global config, and a
missing/corrupt file changes nothing.
"""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.core.agent import resolve_canonical_agent
from mnemo.core.reflex.project_config import load_project_thresholds
from mnemo.hooks import user_prompt_submit as hook


def _write_config(vault, project, **kwargs):
    path = vault / ".mnemo" / f"reflex-config.{project}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"project": project, **kwargs}))
    return path


# --- load_project_thresholds ---


def test_maps_calibrator_keys_to_gate_threshold_names(tmp_vault):
    _write_config(
        tmp_vault, "proj",
        relative_gap=1.33, absolute_floor=1.35, min_tokens=2,
    )
    assert load_project_thresholds(tmp_vault, "proj") == {
        "relative_gap": 1.33,
        "absolute_floor": 1.35,
        "term_overlap_min": 2,
    }


def test_missing_file_returns_empty(tmp_vault):
    assert load_project_thresholds(tmp_vault, "nope") == {}


def test_corrupt_file_returns_empty(tmp_vault):
    path = tmp_vault / ".mnemo" / "reflex-config.broken.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json")
    assert load_project_thresholds(tmp_vault, "broken") == {}


def test_non_numeric_values_are_dropped_not_propagated(tmp_vault):
    _write_config(
        tmp_vault, "proj",
        relative_gap="wide", absolute_floor=1.1, min_tokens=2,
    )
    assert load_project_thresholds(tmp_vault, "proj") == {
        "absolute_floor": 1.1,
        "term_overlap_min": 2,
    }


# --- hook integration: the file is actually consumed ---


def _run_hook(stdin_payload: dict) -> tuple[int, str]:
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))), \
         patch("sys.stdout", out):
        rc = hook.main()
    return rc, out.getvalue()


def _enable_reflex(vault, monkeypatch, thresholds=None):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    reflex: dict = {"enabled": True}
    if thresholds:
        reflex["thresholds"] = thresholds
    (vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(vault),
        "reflex": reflex,
    }))


def _log_entries(vault) -> list[dict]:
    path = vault / ".mnemo" / "reflex-log.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_hook_applies_per_project_calibration_over_defaults(
    tmp_vault, monkeypatch, synthetic_index
):
    """Global config keeps the 2.0 default floor; the project file raises it
    to 999. If the file were still unread, this prompt would emit."""
    _enable_reflex(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)
    project = resolve_canonical_agent(str(tmp_vault)).name
    _write_config(
        tmp_vault, project,
        relative_gap=1.5, absolute_floor=999.0, min_tokens=2,
    )

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault), "session_id": "sid-cal",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })

    assert rc == 0 and stdout == ""
    entry = _log_entries(tmp_vault)[-1]
    assert entry["silence_reason"] == "absolute_floor_fail"
    assert entry["thresholds"]["absolute_floor"] == 999.0


def test_project_file_wins_over_global_thresholds(
    tmp_vault, monkeypatch, synthetic_index
):
    """Named mutation guard on precedence: global says floor 999 (would
    silence), project file says 0.1 (emits). Swapping the precedence order
    flips the outcome."""
    _enable_reflex(tmp_vault, monkeypatch, thresholds={"absoluteFloor": 999.0})
    synthetic_index(tmp_vault)
    project = resolve_canonical_agent(str(tmp_vault)).name
    _write_config(
        tmp_vault, project,
        relative_gap=1.5, absolute_floor=0.1, min_tokens=2,
    )

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault), "session_id": "sid-prec",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })

    assert rc == 0 and stdout
    entry = _log_entries(tmp_vault)[-1]
    assert entry["emitted"] == ["use-prisma-mock"]
    assert entry["thresholds"]["absolute_floor"] == 0.1
