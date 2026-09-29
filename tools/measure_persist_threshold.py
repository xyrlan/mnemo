"""Where Claude Code stops handing a hook's output to the model (#533).

Usage:
    PYTHONPATH=src python3 tools/measure_persist_threshold.py [--projects ~/.claude/projects] [--since 2026-09-28] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

Claude Code does not always put a hook's ``additionalContext`` in context.
When it is too large it saves the text to
``<session>/tool-results/hook-…-additionalContext.txt`` and puts a
``<persisted-output>`` stub in its place: the reported size and the first
2 KB. The agent reads the stub. This finds the size at which that happens
from the transcripts themselves rather than from a guess, which is what
``session_start.ENVELOPE_MAX_BYTES`` is set against.

**What it reads.** Every ``hook_additional_context`` attachment in every
transcript under ``--projects``, one text per hook. A text kept inline is
measured as it stands. A persisted one is measured by the file Claude Code
saved it to, when that file is still on disk; otherwise by the size the stub
reports (``Output too large (9.8KB)``), which is characters / 1024 rounded to
a tenth and so good to about ±50 characters: those are counted apart and kept
out of the cut.

**What it reports.** The largest text kept inline and the smallest one
persisted, each in characters and UTF-8 bytes with the Claude Code version it
came from, and whether the two overlap (``clean``). Also the same two for
mnemo's own SessionStart envelopes, and, with ``--since``, how many of
mnemo's envelopes since that date were persisted: #533's target is 0.

**The result on the maintainer's machine (2026-09-28, 8,879 hook texts in
1,819 transcripts).** The largest text kept inline was **9,872 characters**
(9,981 bytes, Claude Code 2.1.269) and the smallest persisted one **10,044
characters** (10,344 bytes, 2.1.273); 59 of the 63 persisted texts were
measured by their saved file. The cut is clean, in bytes too (largest kept
9,981, smallest persisted 10,225). All 63 persisted texts were mnemo's own
SessionStart envelope, from every version between 2.1.241 and 2.1.283, so the
limit did not move in that range. The stub's ``KB`` is characters / 1024: the
six smallest match it to the tenth, and bytes / 1024 matches one of them.
Everything is consistent with a limit of 10,000 characters. A text counts
once per transcript it appears in, so a resumed session's copy counts again.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

PERSISTED = "<persisted-output>"
_REPORTED = re.compile(r"Output too large \(([\d.]+)\s*KB\)")
_SAVED = re.compile(r"Full output saved to: (\S+)")
#: What marks a text as mnemo's SessionStart envelope
#: (``measure_prevented_repeats._MNEMO_START``).
#: ``[recent-briefings`` is the index SessionStart hands a session since #551;
#: an envelope can be that block alone.
_MNEMO_START = ("mnemo://", "[last-briefing", "[recent-briefings", "[mnemo learned", "[predicted-rules")


def hook_texts(attachment: Dict[str, Any]) -> List[str]:
    content = attachment.get("content")
    if isinstance(content, str):
        return [content]
    return [c for c in content or [] if isinstance(c, str)]


def measure_text(text: str, read_file=None) -> Dict[str, Any]:
    """One hook text: whether Claude Code persisted it, and its size.

    ``how`` says where the size came from: ``inline`` (the text itself),
    ``saved`` (the file Claude Code persisted it to) or ``reported`` (the
    stub's rounded KB, when the file is gone).
    """
    if PERSISTED not in text:
        return {"persisted": False, "chars": len(text), "bytes": len(text.encode("utf-8")),
                "how": "inline", "text": text}
    read = read_file or _read
    saved = _SAVED.search(text)
    full = read(saved.group(1)) if saved else None
    if full is not None:
        return {"persisted": True, "chars": len(full), "bytes": len(full.encode("utf-8")),
                "how": "saved", "text": full}
    reported = _REPORTED.search(text)
    chars = int(round(float(reported.group(1)) * 1024)) if reported else None
    return {"persisted": True, "chars": chars, "bytes": None, "how": "reported", "text": text}


def _read(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def is_mnemo_envelope(hook: str, text: str) -> bool:
    return (hook.startswith("SessionStart") or not hook) and any(m in text for m in _MNEMO_START)


def scan(events: Iterable[dict], read_file=None) -> List[Dict[str, Any]]:
    """Every hook text in one transcript, measured."""
    out: List[Dict[str, Any]] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        att = ev.get("attachment")
        if not isinstance(att, dict) or att.get("type") != "hook_additional_context":
            continue
        hook = str(att.get("hookName") or att.get("hookEvent") or "")
        for text in hook_texts(att):
            row = measure_text(text, read_file)
            row.update({"hook": hook, "version": ev.get("version"),
                        "timestamp": str(ev.get("timestamp") or ""),
                        "session_id": ev.get("sessionId")})
            row["mnemo"] = is_mnemo_envelope(hook, row.pop("text"))
            out.append(row)
    return out


def _slim(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {k: row[k] for k in ("chars", "bytes", "version", "hook", "how")}


def cut(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The largest kept and smallest persisted text, by characters.

    Only exact sizes enter the cut: a persisted text measured by its rounded
    stub is counted in ``persisted_reported_only`` and left out.
    """
    kept = [r for r in rows if not r["persisted"]]
    exact = [r for r in rows if r["persisted"] and r["how"] == "saved"]
    largest = max(kept, key=lambda r: r["chars"], default=None)
    smallest = min(exact, key=lambda r: r["chars"], default=None)
    clean = None
    if largest is not None and smallest is not None:
        clean = largest["chars"] < smallest["chars"]
    largest_b = max(kept, key=lambda r: r["bytes"], default=None)
    smallest_b = min(exact, key=lambda r: r["bytes"], default=None)
    return {
        "kept": len(kept),
        "persisted": sum(1 for r in rows if r["persisted"]),
        "persisted_reported_only": sum(1 for r in rows if r["persisted"] and r["how"] != "saved"),
        "largest_kept": _slim(largest),
        "smallest_persisted": _slim(smallest),
        "clean_in_chars": clean,
        "clean_in_bytes": (None if largest_b is None or smallest_b is None
                           else largest_b["bytes"] < smallest_b["bytes"]),
        "kept_at_or_above_smallest_persisted": (
            [] if smallest is None else
            [_slim(r) for r in kept if r["chars"] >= smallest["chars"]]),
        "persisted_versions": sorted({str(r["version"]) for r in rows if r["persisted"]}),
    }


def since(rows: List[Dict[str, Any]], date: str) -> Dict[str, Any]:
    """mnemo's envelopes at or after ``date`` (ISO, UTC): how many were persisted."""
    got = [r for r in rows if r["mnemo"] and r["timestamp"] >= date]
    sizes = sorted(r["chars"] for r in got if r["chars"] is not None)
    return {"since": date, "envelopes": len(got), "persisted": sum(1 for r in got if r["persisted"]),
            "largest_chars": sizes[-1] if sizes else None}


def load(path: Path) -> List[dict]:
    events = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "hook_additional_context" not in line:
                continue
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
    return events


def run(projects: Path, since_date: str = "") -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    files = sorted(projects.rglob("*.jsonl")) if projects.is_dir() else []
    for f in files:
        rows.extend(scan(load(f)))
    out = {"transcripts": len(files), "hook_texts": len(rows), "all_hooks": cut(rows),
           "mnemo_envelopes": cut([r for r in rows if r["mnemo"]])}
    if since_date:
        out["mnemo_since"] = since(rows, since_date)
    return out


def _line(label: str, row: Optional[Dict[str, Any]]) -> str:
    if row is None:
        return "  %-22s none" % label
    return "  %-22s %6s chars %6s bytes  %s  (%s, %s)" % (
        label, row["chars"], row["bytes"], row["version"], row["hook"], row["how"])


def report_lines(d: Dict[str, Any]) -> List[str]:
    L = ["%d hook texts in %d transcripts" % (d["hook_texts"], d["transcripts"])]
    for key, title in (("all_hooks", "every hook"), ("mnemo_envelopes", "mnemo's SessionStart envelopes")):
        c = d[key]
        L.append("%s: %d kept inline, %d persisted (%d measured only by the stub)"
                 % (title, c["kept"], c["persisted"], c["persisted_reported_only"]))
        L.append(_line("largest kept", c["largest_kept"]))
        L.append(_line("smallest persisted", c["smallest_persisted"]))
        L.append("  clean cut: chars %s, bytes %s; persisted on versions %s"
                 % (c["clean_in_chars"], c["clean_in_bytes"], ", ".join(c["persisted_versions"]) or "-"))
        for r in c["kept_at_or_above_smallest_persisted"]:
            L.append(_line("OVERLAP kept", r))
    s = d.get("mnemo_since")
    if s:
        L.append("mnemo envelopes since %s: %d, persisted %d (target 0), largest %s chars"
                 % (s["since"], s["envelopes"], s["persisted"], s["largest_chars"]))
    return L


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--since", default="", help="ISO date: count mnemo's persisted envelopes from here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = run(Path(args.projects), args.since)
    if args.json:
        json.dump(d, sys.stdout, indent=2)
        print()
    else:
        print("\n".join(report_lines(d)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
