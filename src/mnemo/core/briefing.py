"""Per-session briefing generation for v0.3.1.

Reads a Claude Code session jsonl transcript, asks the LLM to produce a
shift-handoff markdown body, and writes it under
`bots/<agent>/briefings/sessions/<session-id>.md` with spec frontmatter.
"""
from __future__ import annotations

import hashlib
import json
import os
import time as _time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from mnemo.core import corrections as corrections_mod
from mnemo.core import errors as errors_mod
from mnemo.core import llm, paths
from mnemo.core.extract import prompts
from mnemo.core.extract.scanner import parse_frontmatter as _parse_fm
from mnemo.core.transcript import flatten_transcript_events, user_turns


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


def transcript_sha256(jsonl_path: Path) -> str:
    """Content hash of a transcript file, or ``""`` when it cannot be read.

    Stamped into the briefing frontmatter so a second ``mnemo learn`` on an
    unchanged session can reuse the briefing it already wrote instead of
    paying for an identical LLM call.
    """
    try:
        return hashlib.sha256(jsonl_path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _render_briefing(
    *,
    agent: str,
    session_id: str,
    date: str,
    duration_minutes: int,
    corrections: int,
    body: str,
    source_sha256: str = "",
) -> str:
    header = f"# Briefing — {agent} — {session_id}\n"
    sha_line = f"transcript_sha256: {source_sha256}\n" if source_sha256 else ""
    return (
        "---\n"
        "type: briefing\n"
        f"agent: {agent}\n"
        f"session_id: {session_id}\n"
        f"date: {date}\n"
        f"duration_minutes: {duration_minutes}\n"
        f"corrections: {corrections}\n"
        f"{sha_line}"
        "---\n\n"
        f"{header}\n"
        f"{body.strip()}\n"
    )


def generate_session_briefing(
    jsonl_path: Path,
    agent: str,
    cfg: dict,
    *,
    min_mutations: int = 1,
    reuse_unchanged: bool = False,
) -> Path | None:
    """Produce a briefing markdown file for one Claude Code session.

    Returns the filesystem path of the written briefing, or ``None`` when
    the session produced fewer than ``min_mutations`` file mutations and was
    therefore skipped (the default signal threshold: at least one
    Edit/Write/MultiEdit/NotebookEdit tool_use in the transcript). Pass
    ``min_mutations=0`` to brief a session regardless — ``core.learn`` does,
    because a session whose only product is a *correction* touches no files
    and is exactly the session a user runs ``mnemo learn`` on. Raises on I/O
    or LLM failure — callers that want fire-and-forget semantics should wrap
    this in a try/except.
    """
    events = _load_jsonl_events(jsonl_path)

    if _count_file_mutations(events) < min_mutations:
        return None

    session_id = jsonl_path.stem
    vault_root = paths.vault_root(cfg)
    out_path = (
        vault_root / "bots" / agent / "briefings" / "sessions" / f"{session_id}.md"
    )
    source_sha = transcript_sha256(jsonl_path)

    # An unchanged transcript already has its briefing on disk. Regenerating
    # it would spend an LLM call to produce a different-but-equivalent body,
    # which re-dirties the file and makes the *second* `mnemo learn` on a
    # session look like fresh material. Reuse it instead.
    if reuse_unchanged and source_sha and out_path.exists():
        try:
            existing_fm, _ = _parse_fm(out_path.read_text(encoding="utf-8"))
        except OSError:
            existing_fm = {}
        if str(existing_fm.get("transcript_sha256") or "") == source_sha:
            return out_path

    extraction_cfg = cfg.get("extraction") or {}
    model = extraction_cfg.get("model") or "claude-haiku-4-5"
    timeout = int(extraction_cfg.get("subprocessTimeout") or 60)

    transcript = flatten_transcript_events(events)
    turns = user_turns(events)
    prompt_text = prompts.build_briefing_prompt(transcript, user_turns=turns)
    t0 = _time.perf_counter()
    response = llm.call(
        prompt_text,
        system=prompts.BRIEFING_SYSTEM_PROMPT,
        model=model,
        timeout=timeout,
    )
    elapsed_ms = (_time.perf_counter() - t0) * 1000
    try:
        from mnemo.core.mcp import access_log as _al
        _al.record_llm_call(
            vault_root=paths.vault_root(cfg),
            response=response,
            purpose="briefing",
            model=model,
            project=agent,  # briefing's project == its agent name
            agent=agent,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        pass  # telemetry must never break the briefing
    body = (response.text or "").strip() or "*(empty briefing — LLM returned no content)*"

    # Corrections are a bonus on top of the briefing — never lose the whole
    # briefing to a parsing failure. On error, drop the section and move on.
    try:
        proposed = corrections_mod.parse_section(body)
        kept, rejected = corrections_mod.verify(proposed, turns)
        body = corrections_mod.replace_section(body, kept)
        if rejected:
            errors_mod.log_error(
                vault_root,
                "briefing.corrections_rejected",
                ValueError(
                    f"{len(rejected)} correction quote(s) not found in user turns; dropped"
                ),
            )
    except Exception as exc:
        kept = []
        try:
            body = corrections_mod.strip_section(body)
        except Exception:
            pass  # leave the body as-is rather than lose the briefing
        errors_mod.log_error(vault_root, "briefing.corrections", exc)

    duration_minutes = _compute_duration_minutes(events)

    timestamps = [_parse_timestamp(ev.get("timestamp")) for ev in events]
    real_times = [t for t in timestamps if t is not None]
    if real_times:
        date_str = min(real_times).date().isoformat()
    else:
        date_str = datetime.now().date().isoformat()

    content = _render_briefing(
        agent=agent,
        session_id=session_id,
        date=date_str,
        duration_minutes=duration_minutes,
        corrections=len(kept),
        body=body,
        source_sha256=source_sha,
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


# ---------------------------------------------------------------------------
# Retention (#116)
# ---------------------------------------------------------------------------


@dataclass
class PruneReport:
    scanned: int = 0
    agents: int = 0
    bytes: int = 0
    protected_by_sources: int = 0
    kept_recent: int = 0
    kept_min: int = 0
    deleted: list[Path] = field(default_factory=list)


def _normalise_source(entry: str, vault_root: Path) -> str:
    """One ``sources:`` entry in the vault-relative POSIX form briefings use.

    Protection must not depend on how the writer spelled the path: strip
    whitespace, use forward slashes, drop a leading ``./`` and redundant
    separators, and fold an absolute path under ``vault_root`` back to
    vault-relative. An absolute path outside the vault is returned as-is —
    it can never name a briefing, so it simply never matches.
    """
    s = entry.strip().replace("\\", "/")
    if not s:
        return s
    if Path(s).is_absolute():
        try:
            root = Path(vault_root).resolve()
            return Path(s).resolve().relative_to(root).as_posix()
        except (OSError, ValueError):
            return s
    if s.startswith("./"):
        s = s[2:]
    return PurePosixPath(s).as_posix()


def _protected_briefings(vault_root: Path) -> set[str]:
    """Vault-relative POSIX paths named in ``sources:`` of any live page."""
    from mnemo.core.filters import iter_shared_pages, parse_frontmatter

    out: set[str] = set()
    for md in iter_shared_pages(vault_root, include_inbox=True):
        try:
            fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        for s in fm.get("sources") or []:
            if isinstance(s, str):
                out.add(_normalise_source(s, vault_root))
    return out


def prune(
    vault_root: Path,
    cfg: dict,
    *,
    now: float | None = None,
    dry_run: bool = False,
) -> PruneReport:
    """Delete briefings older than ``briefings.retentionDays`` (#116).

    Per agent the newest ``briefings.keepPerAgent`` always survive, and any
    briefing a live rule cites in ``sources:`` is never deleted — it is the
    evidence trail for that rule. ``retentionDays`` of 0 disables pruning
    (the report is still computed, for ``mnemo status``). Age is file mtime:
    briefings are written once and carry no trustworthy date of their own.
    """
    bcfg = cfg.get("briefings") or {}
    retention_days = int(bcfg.get("retentionDays", 180) or 0)
    keep_n = max(0, int(bcfg.get("keepPerAgent", 20)))
    now = _time.time() if now is None else now
    cutoff = now - retention_days * 86400
    rep = PruneReport()
    vault_root = Path(vault_root)
    bots = vault_root / "bots"
    if not bots.is_dir():
        return rep
    protected = _protected_briefings(vault_root)
    for agent_dir in sorted(p for p in bots.iterdir() if p.is_dir()):
        sessions = agent_dir / "briefings" / "sessions"
        if not sessions.is_dir():
            continue
        files = []
        for md in sessions.glob("*.md"):
            try:
                st = md.stat()
            except OSError:
                continue
            files.append((st.st_mtime, st.st_size, md))
        if not files:
            continue
        rep.agents += 1
        files.sort(key=lambda t: -t[0])
        for i, (mtime, size, md) in enumerate(files):
            rep.scanned += 1
            rep.bytes += size
            rel = md.relative_to(vault_root).as_posix()
            if rel in protected:
                rep.protected_by_sources += 1
                continue
            if i < keep_n:
                rep.kept_min += 1
                continue
            if retention_days <= 0 or mtime >= cutoff:
                rep.kept_recent += 1
                continue
            rep.deleted.append(md)
            if not dry_run:
                try:
                    md.unlink()
                except OSError:
                    pass
    return rep
