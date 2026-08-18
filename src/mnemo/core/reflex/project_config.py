"""Read side of per-project reflex calibration.

The autopilot calibrator (:mod:`mnemo.autopilot.tuner.reflex_calibrator`)
writes ``.mnemo/reflex-config.{project}.json`` targeting a 3-12% emit rate.
This module is the consumer: the UserPromptSubmit hook merges these values
over the global config before running the gates. The file format is the
contract between the two — the calibrator's ``min_tokens`` field holds the
gate's ``term_overlap_min`` (not the pre-scoring ``minQueryTokens``).

Any gap (missing file, corrupt JSON, wrong value types) yields an empty
override so the hook falls back to global config — a calibration file must
never break the prompt path.
"""
from __future__ import annotations

import json
from pathlib import Path


def load_project_thresholds(vault_root: Path, project: str) -> dict:
    """Return gate-threshold overrides for ``project``, keyed by gate names.

    Only keys present in the file with the right type are returned; callers
    fall back per-key to global config for anything absent.
    """
    path = vault_root / ".mnemo" / f"reflex-config.{project}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict = {}
    gap = data.get("relative_gap")
    if isinstance(gap, (int, float)) and not isinstance(gap, bool):
        out["relative_gap"] = float(gap)
    floor = data.get("absolute_floor")
    if isinstance(floor, (int, float)) and not isinstance(floor, bool):
        out["absolute_floor"] = float(floor)
    min_tokens = data.get("min_tokens")
    if isinstance(min_tokens, int) and not isinstance(min_tokens, bool):
        out["term_overlap_min"] = min_tokens
    return out
