"""Doctor check — Claude Code will delete transcripts mnemo learns from (#596).

Claude Code sweeps transcripts older than ``cleanupPeriodDays`` (30 when
unset) at every startup. This row warns when the value Claude Code would use
launched here is below mnemo's floor (``install.keepTranscriptsDays``, 365 by
default), and is silent when the user opted out with 0.
:mod:`mnemo.install.transcript_retention` has what the sweep reads.
"""
from __future__ import annotations

from pathlib import Path


def _doctor_check_transcript_retention(vault: Path) -> bool:
    """Doctor-registry adapter — True when silent, False on a warning."""
    from mnemo.core import config as cfg_mod
    from mnemo.install import transcript_retention as tr

    try:
        floor = tr.floor_days(cfg_mod.load_config())
        if floor == 0:
            return True
        value, source = tr.effective_days()
    except Exception:  # noqa: BLE001 — a diagnostic must not abort the run
        return True
    if value is None:
        what = f"{tr.SETTING} is unset, so Claude Code deletes transcripts after {tr.CLAUDE_DEFAULT_DAYS} days"
    elif isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return True  # Claude Code rejects it and skips the sweep: nothing is deleted
    elif value >= floor:
        return True
    else:
        what = f"{tr.SETTING} is {value} in {source}, so Claude Code deletes transcripts after {value} days"
    print(f"  ⚠ Transcript retention: {what}; mnemo learns from them (floor {floor})")
    print(f"       → {tr.fix_line(floor)} ({tr.opt_out_line()})")
    if source is not None and source != tr.user_settings_path():
        print(f"       → and raise or remove the lower value in {source}")
    return False
