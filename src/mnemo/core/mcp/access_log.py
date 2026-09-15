"""MCP access-log writer — JSONL telemetry for tool calls."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from mnemo.core.llm import LLMResponse
from mnemo.core.log_utils import rotate_if_needed

if TYPE_CHECKING:
    from mnemo.core.briefing import BriefingRecord

_LOG_FILENAME = "mcp-access-log.jsonl"
_BRIEFING_LOG_FILENAME = "briefing-log.jsonl"
_TRUNCATE_AT = 1024


def _load_telemetry_config() -> tuple[bool, int]:
    """Return (enabled, max_bytes) from config. Never raises."""
    try:
        from mnemo.core.config import load_config
        cfg = load_config()
        tel = cfg.get("injection", {}).get("telemetry", {})
        enabled = bool(tel.get("enabled", True))
        max_bytes = int(tel.get("log", {}).get("maxBytes", 1_048_576))
        return enabled, max_bytes
    except Exception:
        return False, 1_048_576


_TRUNCATE_SUFFIX = "…[truncated]"
_TRUNCATE_KEEP = _TRUNCATE_AT - len(_TRUNCATE_SUFFIX)


def _sanitize(entry: dict) -> dict:
    """Truncate long string values in-place. Returns the same dict."""
    for key, val in entry.items():
        if isinstance(val, str) and len(val) > _TRUNCATE_AT:
            entry[key] = val[:_TRUNCATE_KEEP] + _TRUNCATE_SUFFIX
        elif isinstance(val, dict):
            _sanitize(val)
    return entry


def record(vault_root: Path, entry: dict) -> None:
    """Append one JSON line to .mnemo/mcp-access-log.jsonl. Never raises."""
    _append(vault_root, _LOG_FILENAME, entry)


def _append(vault_root: Path, filename: str, entry: dict) -> None:
    """Append one JSON line to ``.mnemo/<filename>``. Never raises."""
    try:
        enabled, max_bytes = _load_telemetry_config()
        if not enabled:
            return

        log_dir = vault_root / ".mnemo"
        log_dir.mkdir(parents=True, exist_ok=True)

        log_path = log_dir / filename
        rotate_if_needed(log_path, max_bytes)

        line = json.dumps(_sanitize(entry)) + "\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
    except Exception:
        pass


def _utc_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_llm_call(
    vault_root: Path,
    response: LLMResponse,
    *,
    purpose: str,
    model: str,
    project: str | None,
    agent: str,
    elapsed_ms: float,
) -> None:
    """Append an `llm.call` entry to mcp-access-log.jsonl. Never raises."""
    entry = {
        "timestamp": _utc_iso_z(),
        "tool": "llm.call",
        "purpose": purpose,
        "model": model,
        "project": project,
        "agent": agent,
        "usage": {
            "input_tokens": int(response.input_tokens or 0),
            "output_tokens": int(response.output_tokens or 0),
        },
        "elapsed_ms": float(elapsed_ms),
        "result_count": 1,
    }
    record(vault_root, entry)


def record_session_start_inject(
    vault_root: Path,
    *,
    envelope_bytes: int,
    included_briefing: bool,
    project: str | None,
    agent: str,
) -> None:
    """Append a `session_start.inject` entry. Never raises."""
    entry = {
        "timestamp": _utc_iso_z(),
        "tool": "session_start.inject",
        "envelope_bytes": int(envelope_bytes),
        "included_briefing": bool(included_briefing),
        "project": project,
        "agent": agent,
        "result_count": 1,
    }
    record(vault_root, entry)


def briefing_read_entry(vault_root: Path, record: "BriefingRecord") -> dict:
    """The ``briefing-log.jsonl`` row for one injected briefing.

    ``path`` is vault-relative so the row survives a vault move, and joins a
    ``session_start.inject`` row on ``project`` plus a timestamp seconds away.
    ``body_sha256`` covers exactly the text the session saw, so the read stays
    attributable after ``mnemo learn`` rewrites that file or ``prune`` deletes
    it — the path alone cannot tell two versions of one briefing apart.
    """
    path = Path(record.path)
    try:
        rel = path.relative_to(vault_root).as_posix()
    except ValueError:
        rel = path.as_posix()
    # bots/<project>/briefings/sessions/<id>.md — the directory the hook read
    # from, which is the canonical project even when a migrated worktree
    # briefing still says ``agent: mnemo-wt-225`` in its frontmatter.
    parts = path.parts
    project = parts[-4] if len(parts) >= 4 else ""
    fm = record.frontmatter or {}
    body = (record.body or "").rstrip().encode("utf-8")
    return {
        "timestamp": _utc_iso_z(),
        "project": project,
        "path": rel,
        "session_id": str(fm.get("session_id") or path.stem),
        "date": str(fm.get("date") or ""),
        "body_bytes": len(body),
        "body_sha256": "sha256:" + hashlib.sha256(body).hexdigest()[:16],
    }


def record_briefing_read(vault_root: Path, record: "BriefingRecord") -> None:
    """Append one row to ``.mnemo/briefing-log.jsonl`` for an injected briefing.

    Call it where a briefing is actually handed to a session, not where one is
    picked: ``autopilot/proposer/preempt.py`` picks briefings too, and those
    picks are not consumption.

    A file of its own rather than a ``tool`` in ``mcp-access-log.jsonl``:
    a briefing read is not an MCP call, and at ~80 briefed session starts a
    day these rows would push the access log across its 1 MiB rotation often
    enough to shorten the window ``mnemo recall`` reads its queried cases
    from. Same telemetry switch and size cap as the access log. Never raises.
    """
    try:
        entry = briefing_read_entry(Path(vault_root), record)
    except Exception:
        return
    _append(Path(vault_root), _BRIEFING_LOG_FILENAME, entry)
