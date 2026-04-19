"""Denial and enrichment JSONL activity logs.

Both helpers are fail-open: never raise. Log-path rotation uses
:func:`mnemo.core.log_utils.rotate_if_needed`.

Extracted from the v0.8 rule_activation.py monolith in v0.9 PR G.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.log_utils import rotate_if_needed
from mnemo.core.rule_activation.matching import EnforceHit, EnrichHit


def log_denial(vault_root: Path, hit: EnforceHit, tool_input: dict) -> None:
    """Append a JSON line to <vault>/.mnemo/denial-log.jsonl. Never raises."""
    try:
        from mnemo.core.config import load_config  # lazy import
        cfg = load_config()
        max_bytes: int = cfg.get("enforcement", {}).get("log", {}).get(
            "maxBytes", 1_048_576
        )

        log_path = vault_root / ".mnemo" / "denial-log.jsonl"
        rotate_if_needed(log_path, max_bytes)

        command = tool_input.get("command", "")
        if isinstance(command, str):
            command = command[:500]

        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "slug": hit.slug,
            "project": hit.project,
            "reason": hit.reason,
            "tool": "Bash",
            "command": command,
        }

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001 — never propagate
        pass


def log_enrichment(
    vault_root: Path,
    hits: list[EnrichHit],
    tool_name: str,
    tool_input: dict,
) -> None:
    """Append a JSON line to <vault>/.mnemo/enrichment-log.jsonl. Never raises."""
    try:
        from mnemo.core.config import load_config  # lazy import
        cfg = load_config()
        max_bytes: int = cfg.get("enrichment", {}).get("log", {}).get(
            "maxBytes", 1_048_576
        )

        log_path = vault_root / ".mnemo" / "enrichment-log.jsonl"
        rotate_if_needed(log_path, max_bytes)

        project = hits[0].project if hits else ""
        entry = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "project": project,
            "hit_slugs": [h.slug for h in hits],
            "tool_name": tool_name,
            "file_path": tool_input.get("file_path", ""),
        }

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
    except Exception:  # noqa: BLE001 — never propagate
        pass


__all__ = ["log_denial", "log_enrichment"]
