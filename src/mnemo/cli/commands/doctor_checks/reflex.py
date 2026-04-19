"""Reflex + statusLine doctor checks (v0.5 + v0.8).

Hosts the four reflex/statusLine drift detectors:
  * :func:`_doctor_check_statusline_drift` — settings.json drift away
    from the mnemo composer.
  * :func:`_doctor_check_reflex_index` — missing reflex-index.json when
    reflex is enabled.
  * :func:`_doctor_check_reflex_session_cap_hits` — >20% of recent
    sessions hitting the per-session emission cap.
  * :func:`_doctor_check_reflex_bilingual_gap` — >=3 non-ASCII rules
    without ``aliases:`` to bridge EN↔PT matching.
"""
from __future__ import annotations

import json
from pathlib import Path


def _doctor_check_statusline_drift(vault: Path) -> bool:
    """v0.5: warn when settings.json statusLine drifted away from our composer.

    Three states:
    - composer present + state file present → healthy (return True)
    - state file present but settings.json statusLine is something else → drift
    - no state file at all → mnemo init never ran or already uninstalled (skip)
    """
    import os
    import json as _json

    state_path = vault / ".mnemo" / "statusline-original.json"
    if not state_path.exists():
        return True  # never installed or already uninstalled — nothing to drift from

    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    if not settings_path.exists():
        print("  ⚠ statusLine state file present but ~/.claude/settings.json is missing")
        print("       → run `mnemo init` to reinstall, or `mnemo uninstall` to clean up state")
        return False

    try:
        data = _json.loads(settings_path.read_text())
    except (OSError, _json.JSONDecodeError):
        return True  # other doctor checks will report the malformed file

    current = data.get("statusLine")
    is_ours = (
        isinstance(current, dict)
        and isinstance(current.get("command"), str)
        and current["command"].strip().endswith("statusline-compose")
    )
    if is_ours:
        return True

    print("  ⚠ statusLine drift: settings.json no longer points at the mnemo composer")
    print("       → if you edited statusLine manually after `mnemo init`, run")
    print("         `mnemo init` again to re-wrap, or `mnemo uninstall` to clean up state")
    return False


def _doctor_check_reflex_index(vault: Path) -> bool:
    """v0.8: flag missing reflex-index.json when reflex.enabled is true.

    Returns True (silent) when reflex is disabled or when the index is
    present. The index is rebuilt opportunistically on SessionStart; a
    persistent absence means SessionStart never fired or that extraction
    hasn't run — either way, the emission pipeline is dark and the user
    deserves to know.
    """
    from mnemo.core.config import load_config

    cfg = load_config()
    if not bool((cfg.get("reflex") or {}).get("enabled", False)):
        return True
    idx_path = vault / ".mnemo" / "reflex-index.json"
    if not idx_path.exists():
        print("  \u2717 reflex-index missing \u2014 run `mnemo extract` to rebuild")
        return False
    return True


def _doctor_check_reflex_session_cap_hits(vault: Path) -> bool:
    """v0.8: flag when >20% of sessions in the last 7d hit the emission cap.

    Silent when there is no reflex-log.jsonl yet, when the file is empty, or
    when no session has fired in the last 7 days. Reading is capped at the
    last 5_000 lines to keep doctor lightweight on large vaults.
    """
    from datetime import datetime, timedelta, timezone

    log_path = vault / ".mnemo" / "reflex-log.jsonl"
    if not log_path.exists():
        return True

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    lines = text.splitlines()
    if len(lines) > 5000:
        lines = lines[-5000:]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    sessions: dict[str, dict] = {}
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            entry = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        ts = entry.get("ts") or ""
        if not isinstance(ts, str) or ts < cutoff:
            continue
        sid = str(entry.get("session_id") or "")
        if not sid:
            continue
        bucket = sessions.setdefault(sid, {"hit_cap": False})
        if entry.get("silence_reason") == "session_cap_reached":
            bucket["hit_cap"] = True

    total = len(sessions)
    if total == 0:
        return True
    hit = sum(1 for v in sessions.values() if v["hit_cap"])
    if hit / total > 0.20:
        print(
            f"  \u26a0 reflex-session-cap-hit: {hit}/{total} sessions in last 7d hit cap "
            f"(>{0.20:.0%} threshold). Tune reflex.maxEmissionsPerSession up or "
            f"raise thresholds.absoluteFloor to reduce noise."
        )
        return False
    return True


def _doctor_check_reflex_bilingual_gap(vault: Path) -> bool:
    """v0.8: flag >=3 rules with non-ASCII description but no aliases: field.

    The bilingual (EN↔PT) extractor seeds aliases to bridge the
    tokenizer's language-agnostic matching. When non-ASCII rules
    accumulate without aliases, the reflex pipeline will silently miss
    prompts phrased in the opposite language.
    """
    from mnemo.core.filters import parse_frontmatter

    count_missing = 0
    for type_dir in ("feedback", "user", "reference"):
        d = vault / "shared" / type_dir
        if not d.is_dir():
            continue
        for md in d.glob("*.md"):
            try:
                text = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fm = parse_frontmatter(text)
            except Exception:
                continue
            desc = fm.get("description") or ""
            if not isinstance(desc, str):
                continue
            if any(ord(c) > 127 for c in desc) and not fm.get("aliases"):
                count_missing += 1
    if count_missing >= 3:
        print(
            f"  \u26a0 reflex-bilingual-gap: {count_missing} rules with non-ASCII description "
            f"lack aliases: \u2014 run extraction to refresh."
        )
        return False
    return True
