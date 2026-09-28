"""Session-end capture of the user's corrections, one ledger row each (#517).

A correction used to exist only as a line of briefing prose, and only for a
session that edited a file: ``briefing.generate_session_briefing`` skips a
session with no Edit/Write, so a session whose only product was the user
saying "never do X" was never read. The 2026-09-16 backfill found 11 of 50
corrections (22%) in exactly those sessions. And no correction carried its
turn or its time, so "did the vault already know this when the user said it?"
could not be asked.

So at SessionEnd every correction the detector verifies becomes one row in
the friction ledger (:mod:`mnemo.core.friction.ledger`), carrying the quoted
turn's index, timestamp and uuid, whether the session was a dispatched child,
its entrypoint, and which path found it:

- a session with an edit is briefed as before, and the briefing's verified
  ``## Corrections`` are recorded (:data:`ledger.CAPTURE_BRIEFING`);
- a session with none gets :func:`corrections_only`: the user's turns and the
  assistant text each one answered, asked for corrections alone, under the
  briefing's own definition (:data:`ledger.CAPTURE_CORRECTIONS_ONLY`). No
  briefing is written — there is nothing to hand off, and a briefing is
  extraction input — so a no-edit session teaches the vault nothing it did not
  before; it is only *counted*.

Measured before choosing (99 no-edit human transcripts on disk, 2026-09-27):
the full briefing prompt is a median 10.7k characters (p90 111k), the
corrections-only one 3.1k (p90 9.4k), and its answer is a few lines or empty
where a briefing's is a page.

Both paths go through :func:`corrections.verify`, so the rules are the same:
no quote from a dispatch brief, none from a ``!`` shell turn. Both then go
through :func:`mnemo.core.correction_check.check` (#524), which drops a verified
item that corrects nothing when ``extraction.correctionCheck`` is on (off by
default: it missed its bar, see that module).
"""
from __future__ import annotations

import time as _time
from pathlib import Path
from typing import List, Optional, Tuple

from mnemo.core import corrections
from mnemo.core.friction import ledger
from mnemo.core.redact import redact_secrets
from mnemo.core.transcript import (
    SYNTHETIC_TURN,
    plain_user_text,
    user_turn_records,
)


def entrypoint(events: list[dict]) -> str:
    """The first ``entrypoint`` the transcript records, or ``""``."""
    for ev in events:
        if isinstance(ev, dict) and isinstance(ev.get("entrypoint"), str) and ev["entrypoint"]:
            return ev["entrypoint"]
    return ""


def is_dispatched_child(turns: list[str], session_id: str, vault_root: Optional[Path]) -> bool:
    """True when ``mnemo dispatch`` started this session.

    Either sign is enough: the opening turn is a dispatch brief
    (:func:`corrections.is_dispatch_brief`), or the dispatch log names this
    session's short id as a child (``core.sessions.parents``). The first
    survives the vault; the second catches a brief whose wording changed.
    """
    if turns and corrections.is_dispatch_brief(turns[0]):
        return True
    if vault_root is None or not session_id:
        return False
    try:
        from mnemo.core.sessions import parents

        return session_id[:8] in parents.read(vault_root)
    except Exception:
        return False


def exchanges(events: list[dict]) -> List[Tuple[str, str]]:
    """``(assistant text since the previous turn, user turn)`` per user turn.

    The user side is exactly :func:`transcript.user_turns` — same filter, same
    order — so turn ``n`` here is turn ``n`` in the briefing and in
    :func:`corrections.locate`. The assistant side is every text block the
    assistant wrote since the previous user turn, tool calls left out.
    """
    out: List[Tuple[str, str]] = []
    pending: List[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        if ev.get("type") == "assistant":
            content = msg.get("content")
            if isinstance(content, str):
                pending.append(content)
            elif isinstance(content, list):
                pending.extend(
                    str(b.get("text") or "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
        elif ev.get("type") == "user":
            text = plain_user_text(msg.get("content"))
            if not text or SYNTHETIC_TURN.search(text):
                continue
            out.append((" ".join(p for p in pending if p.strip()), text))
            pending = []
    return out


def record_session(
    vault_root: Path,
    *,
    events: list[dict],
    session_id: str,
    project: str,
    items: List[corrections.Correction],
    capture: str,
    briefing: str = "",
) -> int:
    """Write one ledger row per verified correction; return how many landed.

    *items* must already have passed :func:`corrections.verify`; one whose
    quote no reaction turn holds is dropped here rather than recorded without
    a turn. A turn an *earlier* pass over this session already captured is
    skipped: a resumed session is briefed again at its next end, and the
    second pass may quote the same correction with different words. Two
    corrections in one turn from the same pass are both kept. Quote and rule go through
    :func:`redact_secrets`, as every other writer's text does (#418).

    Never raises; a lost row is logged by the ledger, never the session's.
    """
    try:
        records = user_turn_records(events)
        turns = [r.text for r in records]
        seen = {
            rec.turn_index for rec in ledger.iter_records(vault_root)
            if rec.session_id == session_id and rec.capture
        }
        child = is_dispatched_child(turns, session_id, vault_root)
        entry = entrypoint(events)
        written = 0
        for item in items:
            index = corrections.locate(item.quote, turns)
            if index is None or index in seen:
                continue
            turn = records[index]
            rid = ledger.record(vault_root, ledger.FrictionRecord(
                session_id=session_id,
                project=project,
                quote=redact_secrets(item.quote)[0],
                rule_text=redact_secrets(item.rule)[0],
                briefing=briefing,
                capture=capture,
                turn_index=index,
                turn_ts=turn.timestamp,
                turn_uuid=turn.uuid,
                child=child,
                entrypoint=entry,
            ))
            if rid is not None:
                written += 1
        return written
    except Exception as exc:
        try:
            from mnemo.core import errors

            errors.log_error(vault_root, "friction.capture.record", exc)
        except Exception:
            pass
        return 0


def has_reaction(turns: list[str]) -> bool:
    """True when some turn could hold a quote :func:`corrections.verify` keeps.

    A session whose only turns are its dispatch brief, ``!`` commands or
    one-word replies cannot yield a correction, so it costs no LLM call.
    """
    return any(
        len(corrections.normalize(turns[i])) >= corrections.MIN_QUOTE_CHARS
        for i in corrections.reaction_indexes(turns)
    )


def corrections_only(
    jsonl_path: Path, agent: str, cfg: dict, *, events: Optional[list[dict]] = None,
) -> List[corrections.Correction]:
    """Find, verify and record the corrections of a session with no edit.

    Returns the corrections recorded (verified; possibly empty). One LLM call
    at most, none when :func:`has_reaction` says there is nothing to find.
    Raises on I/O or LLM failure, as the briefing does — the CLI that runs it
    detached logs the error.
    """
    from mnemo.core import briefing as briefing_mod
    from mnemo.core import errors, llm, paths
    from mnemo.core.extract import prompts

    if events is None:
        events = briefing_mod._load_jsonl_events(jsonl_path)
    pairs = exchanges(events)
    turns = [turn for _, turn in pairs]
    if not has_reaction(turns):
        return []

    vault_root = paths.vault_root(cfg)
    extraction_cfg = cfg.get("extraction") or {}
    model = extraction_cfg.get("model") or "claude-haiku-4-5"
    timeout = int(extraction_cfg.get("subprocessTimeout") or 60)

    provider = llm.resolve(cfg)
    t0 = _time.perf_counter()
    response = provider(
        prompts.build_corrections_prompt(pairs),
        system=prompts.CORRECTIONS_SYSTEM_PROMPT,
        model=model,
        timeout=timeout,
    )
    elapsed_ms = (_time.perf_counter() - t0) * 1000
    try:
        from mnemo.core.mcp import access_log

        access_log.record_llm_call(
            vault_root=vault_root,
            response=response,
            purpose="corrections",
            model=model,
            project=agent,
            agent=agent,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        pass  # telemetry must never cost the corrections

    proposed = corrections.parse_section(response.text or "")
    kept, rejected = corrections.verify(proposed, turns)
    if rejected:
        errors.log_error(
            vault_root,
            "corrections_only.rejected",
            ValueError(f"{len(rejected)} correction quote(s) not found in user turns; dropped"),
        )
    from mnemo.core import correction_check

    kept, _dropped = correction_check.check(kept, pairs, cfg, vault_root=vault_root, agent=agent)
    record_session(
        vault_root,
        events=events,
        session_id=jsonl_path.stem,
        project=agent,
        items=kept,
        capture=ledger.CAPTURE_CORRECTIONS_ONLY,
    )
    return kept
