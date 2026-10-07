"""Keep the transcripts mnemo learns from (#596).

Claude Code deletes every transcript under ``~/.claude/projects`` older than
``cleanupPeriodDays`` — 30 days unless a setting says otherwise. Extraction,
backfill, briefings, procedures, ``mnemo replay`` and every
``tools/measure_*`` read those files, so a default install keeps a month of
the history mnemo learns from and nothing older to measure against.

What Claude Code does with the key (read in the 2.1.293 bundle):

- the sweep runs at startup over *every* project's transcripts and reads the
  value from the merged settings of the directory it was launched in, so only
  the user file, ``~/.claude/settings.json``, protects all of them; a project
  file only changes the sweeps launched in that project;
- the value must be a positive integer: ``0`` is rejected as invalid, and a
  set-but-invalid value makes Claude Code skip the sweep altogether;
- a managed-settings value wins over all of the above, and is not the user's
  to change, so it is out of this module's reach.

``mnemo init`` raises a value below the floor and never lowers one above it.
The floor is ``install.keepTranscriptsDays`` in mnemo's config (365 by
default); 0 opts out, and then nothing here writes or warns.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, NamedTuple, Optional, Tuple

SETTING = "cleanupPeriodDays"
CLAUDE_DEFAULT_DAYS = 30
DEFAULT_FLOOR_DAYS = 365
CONFIG_KEY = "install.keepTranscriptsDays"
OPT_OUT_FLAG = "--no-transcript-retention"


def user_settings_path() -> Path:
    return Path(os.path.expanduser("~/.claude/settings.json"))


def floor_days(cfg: dict[str, Any]) -> int:
    """The configured floor; 0 when the user opted out (or wrote nonsense)."""
    value = (cfg.get("install") or {}).get("keepTranscriptsDays", DEFAULT_FLOOR_DAYS)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


def _valid_days(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def read_days(path: Path) -> Tuple[str, Any]:
    """``(state, value)`` for ``cleanupPeriodDays`` in one settings file.

    ``state`` is ``"unset"``, ``"valid"`` (a positive int), ``"invalid"`` (set
    to anything else) or ``"unreadable"`` (the file is not a JSON object).
    """
    if not path.is_file():
        return "unset", None
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text) if text.strip() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "unreadable", None
    if not isinstance(data, dict):
        return "unreadable", None
    if SETTING not in data:
        return "unset", None
    value = data[SETTING]
    return ("valid" if _valid_days(value) else "invalid"), value


class Outcome(NamedTuple):
    action: str  # "raised" | "kept" | "invalid" | "unreadable"
    before: Any  # the value found, None when unset
    after: Any


def ensure_floor(settings_path: Path, floor: int) -> Outcome:
    """Raise ``cleanupPeriodDays`` in *settings_path* to *floor* when lower.

    Unset counts as Claude Code's default of 30. A value at or above the floor
    is never touched, nor is one Claude Code would reject (with that, it skips
    the sweep and deletes nothing). Only this key changes: the file is read,
    backed up and written back the way every other ``mnemo init`` step writes
    it, and not written at all when nothing changes.
    """
    from mnemo.install import settings as inj

    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with inj._with_lock(settings_path) as held:
            if held:
                return _do_ensure(settings_path, floor)
        if time.time() > deadline:
            raise inj.SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_ensure(settings_path: Path, floor: int) -> Outcome:
    from mnemo.install import settings as inj

    state, value = read_days(settings_path)
    if state == "unreadable":
        return Outcome("unreadable", None, None)
    if state == "invalid":
        return Outcome("invalid", value, value)
    if state == "valid" and value >= floor:
        return Outcome("kept", value, value)
    data = inj._read_settings(settings_path)
    inj._backup(settings_path)
    data[SETTING] = floor
    settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return Outcome("raised", value, floor)


def effective_days(cwd: Optional[Path] = None) -> Tuple[Any, Optional[Path]]:
    """``(value, source)`` Claude Code would sweep with when launched in *cwd*.

    Precedence as Claude Code merges it, lowest first: the user file, then the
    project's ``.claude/settings.json`` and ``.claude/settings.local.json``.
    ``(None, None)`` when no file sets the key. Managed settings are left out:
    no fix printed here could change them.
    """
    base = Path(cwd) if cwd is not None else Path.cwd()
    found: Tuple[Any, Optional[Path]] = (None, None)
    for path in (user_settings_path(),
                 base / ".claude" / "settings.json",
                 base / ".claude" / "settings.local.json"):
        state, value = read_days(path)
        if state in ("valid", "invalid"):
            found = (value, path)
    return found


def fix_line(floor: int) -> str:
    return f'add "{SETTING}": {floor} to ~/.claude/settings.json'


def opt_out_line() -> str:
    return f"opt out: `mnemo init {OPT_OUT_FLAG}` or set {CONFIG_KEY} to 0 in mnemo's config"
