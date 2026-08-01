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
from mnemo.core.extract.scanner import _VALID_TYPES, _normalize_slug
from mnemo.core.transcript import flatten_transcript_events

# The four types the extraction scanner accepts. Derived rather than restated
# so harvest can never drift from what `scanner._read_memory_file` will honour.
VALID_TYPES = frozenset(_VALID_TYPES)


def _render_memory_file(
    *,
    slug: str,
    page_type: str,
    name: str,
    description: str,
    body: str,
    session_id: str,
) -> str:
    """Render a memory file matching the shape live capture writes.

    ``description`` is the only LLM-authored value that lands *inside* the
    frontmatter block, so it is the only one that has to be sanitised. The
    frontmatter reader is a flat line parser bounded by
    ``^---\\r?\\n(.*?)\\r?\\n---\\r?\\n`` (``scanner._FRONTMATTER_RE``), and a
    newline in the description would truncate the value, spill the rest of the
    text into the block as stray keys, and — if the description happens to
    contain a ``---`` line — close the frontmatter early, burying the
    ``origin: backfill`` stamp in the body where the task-6 gate cannot see it.
    Collapsing all whitespace to single spaces closes newline truncation, the
    early ``---`` break, and stray-key injection at once; the quote swap keeps
    the value from ending its own double-quoted string. Colons, leading
    hyphens, backslashes and unicode all round-trip through the flat parser
    unharmed and are left alone.

    ``name`` (rendered as an ``# {name}`` heading) and ``body`` both sit after
    the closing ``---``, so neither can perturb the frontmatter — at worst a
    multi-line ``name`` looks untidy. They are written through unmodified.
    """
    safe_desc = " ".join(str(description).split()).replace('"', "'")
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

    Returns ``[]`` — "nothing to write", not an error — when:

    * the transcript is unreadable or unparseable. ``_load_jsonl_events``
      swallows ``OSError`` and bad JSON lines (matching ``briefing.py``), so a
      permission-denied or truncated transcript yields zero events, falls
      through the mutation gate, and is indistinguishable from a quiet
      session. Callers cannot tell the two apart from the return value.
    * the session is below ``backfill.minFileMutations``.
    * the response parsed as JSON but carried no usable pages: ``pages``
      missing or not a list, every page dropped by validation, or every slug
      already present on disk.

    Raises:

    * whatever ``llm.call`` raises — ``LLMError``/``LLMRateLimitError`` on a
      failed or timed-out subprocess.
    * ``LLMParseError`` when the response text contains no parseable JSON
      object. Note the asymmetry with the list above: malformed JSON raises,
      well-formed JSON with a nonsense ``pages`` value returns ``[]``.
    * ``OSError`` from writing a page.

    Callers that want fire-and-forget semantics wrap this in a try/except, as
    the CLI does.
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
    seen_slugs: set[str] = set()

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

        if slug in seen_slugs:
            # Two pages in THIS response whose slugs normalise to the same
            # filename. Nothing is overwritten; the later page is dropped.
            # Kept distinct from the on-disk case below so a debugger chasing
            # a missing page looks in the right place.
            continue

        target = memory_dir / f"{slug}.md"
        if target.exists():
            continue  # live-authored memory wins over reconstruction

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
        seen_slugs.add(slug)

    return written
