"""What a session briefing's prompt is made of, before and after #766

Usage:
    PYTHONPATH=src python3 tools/measure_briefing_input.py [--vault ~/mnemo]
        [--projects ~/.claude/projects] [--since 2026-10-13] [--limit N]
        [--sessions] [--json]

Read-only: no LLM calls, no writes.

**Which sessions.** Every briefing under ``bots/<agent>/briefings/sessions/``
whose file was written on or after ``--since`` (its mtime: when the briefing
was generated, not the session's own date), paired with the transcript
``<projects>/<dir>/<session-id>.jsonl`` it was generated from. A briefing
whose transcript is gone is counted, not measured.

**The prompt.** Rebuilt exactly as :func:`mnemo.core.briefing.generate_session_briefing`
builds it, from the same functions: :func:`mnemo.core.transcript.flatten_transcript_events`
for the transcript, :func:`mnemo.core.extract.prompts.build_briefing_prompt`
around it with the numbered user turns. ``after`` is that prompt today.
``before`` adds back what #766 stopped rendering: every event whose ``type``
is not in :data:`mnemo.core.transcript.CONVERSATION_TYPES`, rendered as the
flattener used to render it (its ``message``, if it has one).

**The parts.** Each rendered block of the prompt is put in one bucket
(:func:`breakdown`):

- ``dropped:<type>`` — an event the flattener no longer renders, by type
  (``api-request-blob``: Claude Code's log of the API request it sent, which
  carries the system prompt, CLAUDE.md, memory indexes and hook context);
- ``tool_result`` — a tool's output, cut at 400 characters, and per tool
  under ``by_tool`` (the tool named by the ``tool_use`` it answers);
- ``tool_use`` — the ``[tool_use: Name]`` marker;
- ``assistant text``;
- ``user typed`` — a user turn the person wrote;
- ``user synthetic`` — a user message the harness wrote (task notifications,
  hook output, slash-command output: :data:`mnemo.core.transcript.SYNTHETIC_TURN`);
- ``user meta`` — a user message Claude Code marks ``isMeta`` (skill bodies,
  messages from other sessions);
- ``compact summary`` — the summary a ``/compact`` leaves (``isCompactSummary``);
- ``frame`` — the task line, the numbered user-turn block and the markers.

Sizes are characters. ``~tokens`` is characters / 4, the estimate
``mnemo backfill`` already uses (``cli/commands/backfill.py``), and it runs
low. Three sessions sent through ``claude --print`` with the briefing's
system prompt on 2026-10-28 (Haiku 4.5, the ``usage`` the CLI reports)
cost 32,851 / 40,564 / 19,457 input tokens before and 16,161 / 23,818 /
8,233 after, from prompts of 96,002 / 120,373 / 57,672 and 39,062 / 62,218
/ 17,667 characters: about 3.4 characters a token, plus some 4-5K tokens a
call costs whatever the prompt.

**The gate.** A session is briefed only when it holds at least one file
mutation (``min_mutations=1``, :func:`mnemo.core.briefing._count_file_mutations`).
That count is over every event and #766 did not change it; ``gate`` reports
how many sessions it would pass counted over the conversation alone, so a
difference would show.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnemo.core import briefing  # noqa: E402
from mnemo.core.extract import prompts  # noqa: E402
from mnemo.core.transcript import (  # noqa: E402
    CONVERSATION_TYPES,
    SYNTHETIC_TURN,
    flatten_transcript_events,
    user_turns,
)

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: ``"\n\n"`` joins two rendered events in the flattened transcript.
_SEP = 2
FRAME = "frame"
BUCKETS = ("tool_result", "tool_use", "assistant text", "user typed", "user synthetic",
           "user meta", "compact summary", FRAME)


def _rendered(ev: Dict[str, Any]) -> int:
    """Characters ``ev`` adds to the flattened transcript as the flattener
    renders it today, separator included; 0 when it renders nothing."""
    n = len(flatten_transcript_events([ev]))
    return n + _SEP if n else 0


def _rendered_before(ev: Dict[str, Any]) -> int:
    """What ``ev`` added before #766, when its type was not checked."""
    return _rendered({k: v for k, v in ev.items() if k != "type"} if isinstance(ev, dict) else ev)


def _bucket(ev: Dict[str, Any], block: Dict[str, Any]) -> str:
    btype = block.get("type")
    if btype in ("tool_result", "tool_use"):
        return str(btype)
    if ev.get("type") == "assistant" or (ev.get("message") or {}).get("role") == "assistant":
        return "assistant text"
    if ev.get("isCompactSummary"):
        return "compact summary"
    if ev.get("isMeta"):
        return "user meta"
    if SYNTHETIC_TURN.search(str(block.get("text") or "")):
        return "user synthetic"
    return "user typed"


def breakdown(events: Iterable[Any]) -> Dict[str, Any]:
    """The briefing prompt of one session, in parts.

    ``before`` and ``after`` are the prompt's characters with and without the
    events #766 dropped; ``parts`` maps each bucket to its characters in
    ``after`` (a ``dropped:<type>`` bucket to what it added to ``before``),
    ``by_tool`` each tool's ``tool_result`` characters, ``gate`` the file
    mutations counted over every event and over the conversation alone.
    """
    events = [ev for ev in events if isinstance(ev, dict)]
    kept = [ev for ev in events if not ev.get("type") or ev.get("type") in CONVERSATION_TYPES]
    parts: Counter = Counter()
    by_tool: Counter = Counter()
    names: Dict[str, str] = {}
    for ev in events:
        etype = str(ev.get("type") or "")
        if etype and etype not in CONVERSATION_TYPES:
            n = _rendered_before(ev)
            if n:
                parts[f"dropped:{etype}"] += n
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        if not isinstance(blocks, list):
            continue
        rendered = [b for b in blocks if isinstance(b, dict) and _rendered(
            {"type": etype, "message": {**msg, "content": [b]}})]
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                names[str(block.get("id") or "")] = str(block.get("name") or "tool")
        for block in rendered:
            # One block alone, as its own event: its text, the role prefix and
            # the separator. Several blocks of one event share one prefix and
            # are joined by "\n", so this overcounts them slightly; ``frame``
            # takes the difference back, keeping the parts summed to ``after``.
            n = _rendered({"type": etype, "message": {**msg, "content": [block]}})
            bucket = _bucket(ev, block)
            parts[bucket] += n
            if bucket == "tool_result":
                by_tool[names.get(str(block.get("tool_use_id") or ""), "?")] += n
    turns = user_turns(events)
    after = len(prompts.build_briefing_prompt(flatten_transcript_events(events), user_turns=turns))
    body = sum(v for k, v in parts.items() if k in BUCKETS)
    parts[FRAME] += after - body
    dropped = sum(v for k, v in parts.items() if k.startswith("dropped:"))
    return {
        "before": after + dropped,
        "after": after,
        "parts": dict(parts),
        "by_tool": dict(by_tool),
        "gate": {"all_events": briefing._count_file_mutations(events),
                 "conversation": briefing._count_file_mutations(kept)},
    }


def _since_ts(since: Optional[str]) -> float:
    if not since:
        return 0.0
    dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def sessions(vault: Path, projects: Path, since: Optional[str] = None,
             limit: Optional[int] = None) -> Tuple[List[Tuple[str, str, Path]], int]:
    """``(agent, session id, transcript)`` of each briefing written since
    ``since``, newest first, and how many briefings had no transcript left."""
    transcripts: Dict[str, Path] = {}
    if projects.is_dir():
        for d in projects.iterdir():
            if d.is_dir():
                for p in d.glob("*.jsonl"):
                    transcripts.setdefault(p.stem, p)
    floor = _since_ts(since)
    found: List[Tuple[float, str, str]] = []
    for md in (vault / "bots").glob("*/briefings/sessions/*.md"):
        try:
            mtime = md.stat().st_mtime
        except OSError:
            continue
        if mtime >= floor:
            found.append((mtime, md.parent.parent.parent.name, md.stem))
    found.sort(reverse=True)
    out: List[Tuple[str, str, Path]] = []
    missing = 0
    for _mtime, agent, sid in found:
        if sid not in transcripts:
            missing += 1
            continue
        out.append((agent, sid, transcripts[sid]))
        if limit and len(out) >= limit:
            break
    return out, missing


def measure(rows: Iterable[Tuple[str, str, Path]]) -> Dict[str, Any]:
    per: List[Dict[str, Any]] = []
    parts: Counter = Counter()
    by_tool: Counter = Counter()
    gate: Counter = Counter()
    for agent, sid, path in rows:
        b = breakdown(briefing._load_jsonl_events(path))
        parts.update(b["parts"])
        by_tool.update(b["by_tool"])
        gate["passes_all_events"] += b["gate"]["all_events"] >= 1
        gate["passes_conversation"] += b["gate"]["conversation"] >= 1
        per.append({"agent": agent, "session": sid, "before": b["before"], "after": b["after"]})
    before = sum(r["before"] for r in per)
    after = sum(r["after"] for r in per)
    return {
        "sessions": len(per),
        "before": before,
        "after": after,
        "parts": dict(parts),
        "by_tool": dict(by_tool),
        "gate": dict(gate),
        "rows": per,
    }


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:5.1f}%" if d else "    -"


def _median(xs: List[int]) -> int:
    xs = sorted(xs)
    return xs[len(xs) // 2] if xs else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="~/mnemo")
    ap.add_argument("--projects", default="~/.claude/projects")
    ap.add_argument("--since", default=None, help="ISO date or time; briefings written since")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sessions", action="store_true", help="one line per session")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    vault = Path(args.vault).expanduser()
    rows, missing = sessions(vault, Path(args.projects).expanduser(), args.since, args.limit)
    result = measure(rows)
    result["no_transcript"] = missing
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        print(json.dumps(_provenance.stamp(result, prov), indent=2))
        return 0
    print(_provenance.line(prov))
    n, before, after = result["sessions"], result["before"], result["after"]
    print(f"sessions={n} no_transcript={missing}")
    if not n:
        return 0
    print(f"prompt chars  before={before:,} after={after:,}  cut={_pct(before - after, before)}")
    print(f"per session   ~tokens before={before // 4 // n:,} after={after // 4 // n:,}  "
          f"(median chars before={_median([r['before'] for r in result['rows']]):,} "
          f"after={_median([r['after'] for r in result['rows']]):,})")
    print("parts (share of before):")
    for k, v in sorted(result["parts"].items(), key=lambda kv: -kv[1]):
        print(f"  {k:<32} {v:>12,} {_pct(v, before)}")
    print("tool_result by tool (share of after):")
    for k, v in Counter(result["by_tool"]).most_common(8):
        print(f"  {k:<32} {v:>12,} {_pct(v, after)}")
    g = result["gate"]
    print(f"gate (>=1 file mutation): all events={g.get('passes_all_events', 0)}/{n} "
          f"conversation only={g.get('passes_conversation', 0)}/{n}")
    if args.sessions:
        aliases = _provenance.load_aliases(vault)
        for r in result["rows"]:
            print(f"  {r['session'][:8]} {_provenance.redact(r['agent'], aliases):<20} "
                  f"before={r['before']:>10,} after={r['after']:>9,} "
                  f"cut={_pct(r['before'] - r['after'], r['before'])}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
