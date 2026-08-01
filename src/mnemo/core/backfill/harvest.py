"""Turn one archived transcript into memory files.

Sibling of :mod:`mnemo.core.briefing` — same shape, same helpers, same config
keys. Where briefing produces one narrative markdown file, harvest produces N
structured memory pages under ``bots/<agent>/memory/``.

Every page written carries ``metadata.origin: backfill`` so the extraction
pipeline can hold reconstructed material to a higher bar than live-authored
memory (see ``extract/inbox/paths.py``).

Never overwrites an existing memory file. Live-authored memory always wins: it
was written by the session that learned the lesson, not reconstructed after.
"""
from __future__ import annotations

import time as _time
from pathlib import Path

from mnemo.core import llm, paths
from mnemo.core.briefing import (
    _atomic_write,
    _count_file_mutations,
    _load_jsonl_events,
)
from mnemo.core.extract import prompts
from mnemo.core.extract.scanner import _normalize_slug
from mnemo.core.transcript import flatten_transcript_events

VALID_TYPES = frozenset({"feedback", "user", "reference", "project"})


def _render_memory_file(
    *,
    slug: str,
    page_type: str,
    name: str,
    description: str,
    body: str,
    session_id: str,
) -> str:
    """Render a memory file matching the shape live capture writes."""
    safe_desc = str(description).replace('"', "'").strip()
    return (
        "---\n"
        f"name: {slug}\n"
        f'description: "{safe_desc}"\n'
        "metadata:\n"
        "  node_type: memory\n"
        f"  type: {page_type}\n"
        f"  originSessionId: {session_id}\n"
        "  origin: backfill\n"
        "---\n\n"
        f"# {name}\n\n"
        f"{body.strip()}\n"
    )


def harvest_session(jsonl_path: Path, agent: str, cfg: dict) -> list[Path]:
    """Harvest one transcript into memory files. Returns the paths written.

    Returns an empty list when the session is below the mutation threshold,
    when the LLM emitted no usable pages, or when every page collided with an
    existing memory file. Raises on LLM or I/O failure — callers that want
    fire-and-forget semantics wrap this in a try/except, as the CLI does.
    """
    events = _load_jsonl_events(jsonl_path)

    backfill_cfg = cfg.get("backfill") or {}
    min_mutations = int(backfill_cfg.get("minFileMutations", 1))
    if _count_file_mutations(events) < min_mutations:
        return []

    extraction_cfg = cfg.get("extraction") or {}
    model = extraction_cfg.get("model") or "claude-haiku-4-5"
    timeout = int(extraction_cfg.get("subprocessTimeout") or 60)

    transcript = flatten_transcript_events(events)
    t0 = _time.perf_counter()
    response = llm.call(
        prompts.build_harvest_prompt(transcript),
        system=prompts.HARVEST_SYSTEM_PROMPT,
        model=model,
        timeout=timeout,
    )
    elapsed_ms = (_time.perf_counter() - t0) * 1000

    try:
        from mnemo.core.mcp import access_log as _al

        _al.record_llm_call(
            vault_root=paths.vault_root(cfg),
            response=response,
            purpose="backfill:harvest",
            model=model,
            project=agent,
            agent=agent,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        pass  # telemetry must never break a harvest

    payload = llm._parse_llm_json(response.text or "")
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        return []

    session_id = jsonl_path.stem
    memory_dir = paths.memory_dir(cfg, agent)
    written: list[Path] = []

    for rp in raw_pages:
        if not isinstance(rp, dict):
            continue
        raw_slug = str(rp.get("slug") or "").strip()
        if not raw_slug:
            continue  # _normalize_slug would turn "" into "untitled.md"
        slug = _normalize_slug(raw_slug)
        page_type = str(rp.get("type") or "").strip().lower()
        if page_type not in VALID_TYPES:
            continue
        body = str(rp.get("body") or "")
        if not body.strip():
            continue

        target = memory_dir / f"{slug}.md"
        if target.exists():
            continue  # live-authored memory wins

        content = _render_memory_file(
            slug=slug,
            page_type=page_type,
            name=str(rp.get("name") or slug),
            description=str(rp.get("description") or ""),
            body=body,
            session_id=session_id,
        )
        _atomic_write(target, content)
        written.append(target)

    return written
