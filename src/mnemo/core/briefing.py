"""Per-session briefing generation for v0.3.1.

Reads a Claude Code session jsonl transcript, asks the LLM to produce a
shift-handoff markdown body, and writes it under
`bots/<agent>/briefings/sessions/<session-id>.md` with spec frontmatter.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo.core import llm, paths
from mnemo.core.extract import prompts
from mnemo.core.extract.scanner import parse_frontmatter as _parse_fm
from mnemo.core.transcript import flatten_transcript_events


MUTATION_TOOL_NAMES = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})


def _count_file_mutations(events: list[dict]) -> int:
    count = 0
    for ev in events:
        msg = ev.get("message") if isinstance(ev, dict) else None
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") in MUTATION_TOOL_NAMES:
                count += 1
    return count


def _load_jsonl_events(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return events
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value)
    # Claude Code jsonl uses ISO 8601 with trailing 'Z' for UTC.
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _compute_duration_minutes(events: list[dict]) -> int:
    timestamps: list[datetime] = []
    for ev in events:
        ts = _parse_timestamp(ev.get("timestamp"))
        if ts is not None:
            timestamps.append(ts)
    if len(timestamps) < 2:
        return 0
    delta = max(timestamps) - min(timestamps)
    return int(delta.total_seconds() // 60)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(content.encode("utf-8"))
    os.replace(tmp, path)


def _render_briefing(
    *,
    agent: str,
    session_id: str,
    date: str,
    duration_minutes: int,
    body: str,
) -> str:
    header = f"# Briefing — {agent} — {session_id}\n"
    return (
        "---\n"
        "type: briefing\n"
        f"agent: {agent}\n"
        f"session_id: {session_id}\n"
        f"date: {date}\n"
        f"duration_minutes: {duration_minutes}\n"
        "---\n\n"
        f"{header}\n"
        f"{body.strip()}\n"
    )


def generate_session_briefing(jsonl_path: Path, agent: str, cfg: dict) -> Path | None:
    """Produce a briefing markdown file for one Claude Code session.

    Returns the filesystem path of the written briefing, or ``None`` when
    the session produced no file mutations and was therefore skipped (the
    signal threshold: at least one Edit/Write/MultiEdit/NotebookEdit
    tool_use in the transcript). Raises on I/O or LLM failure — callers
    that want fire-and-forget semantics should wrap this in a try/except.
    """
    events = _load_jsonl_events(jsonl_path)

    if _count_file_mutations(events) == 0:
        return None

    extraction_cfg = cfg.get("extraction") or {}
    model = extraction_cfg.get("model") or "claude-haiku-4-5"
    timeout = int(extraction_cfg.get("subprocessTimeout") or 60)

    transcript = flatten_transcript_events(events)
    prompt_text = prompts.build_briefing_prompt(transcript)
    response = llm.call(
        prompt_text,
        system=prompts.BRIEFING_SYSTEM_PROMPT,
        model=model,
        timeout=timeout,
    )
    body = (response.text or "").strip() or "*(empty briefing — LLM returned no content)*"

    session_id = jsonl_path.stem
    duration_minutes = _compute_duration_minutes(events)

    timestamps = [_parse_timestamp(ev.get("timestamp")) for ev in events]
    real_times = [t for t in timestamps if t is not None]
    if real_times:
        date_str = min(real_times).date().isoformat()
    else:
        date_str = datetime.now().date().isoformat()

    vault_root = paths.vault_root(cfg)
    out_path = vault_root / "bots" / agent / "briefings" / "sessions" / f"{session_id}.md"

    content = _render_briefing(
        agent=agent,
        session_id=session_id,
        date=date_str,
        duration_minutes=duration_minutes,
        body=body,
    )
    _atomic_write(out_path, content)
    return out_path


@dataclass(frozen=True)
class BriefingRecord:
    path: Path
    frontmatter: dict
    body: str


def _parse_briefing_file(path: Path) -> BriefingRecord | None:
    """Read and parse a briefing markdown file. Returns None on any I/O error."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, body = _parse_fm(text)
    return BriefingRecord(path=path, frontmatter=fm, body=body.lstrip("\n"))


def pick_latest_briefing(vault_root: Path, agent_name: str) -> BriefingRecord | None:
    """Return the most recent briefing for ``agent_name``, or None if there are none.

    Ordering: frontmatter ``date`` (ISO YYYY-MM-DD) descending, tie-break by
    ``session_id`` lexicographic descending. Files without a parseable date
    fall back to file mtime — they sort below any dated briefing.
    """
    sessions_dir = vault_root / "bots" / agent_name / "briefings" / "sessions"
    if not sessions_dir.is_dir():
        return None

    records: list[tuple[tuple, BriefingRecord]] = []
    for md in sessions_dir.glob("*.md"):
        rec = _parse_briefing_file(md)
        if rec is None:
            continue
        date = rec.frontmatter.get("date", "")
        session_id = rec.frontmatter.get("session_id", md.stem)
        # Sort key: (has_date, date, session_id, mtime). has_date=1 outranks 0.
        if date:
            key = (1, date, session_id, 0.0)
        else:
            try:
                mtime = md.stat().st_mtime
            except OSError:
                mtime = 0.0
            key = (0, "", "", mtime)
        records.append((key, rec))

    if not records:
        return None
    records.sort(key=lambda kv: kv[0], reverse=True)
    return records[0][1]
