"""A zero-token panorama of why mnemo's memory does not help (#530).

Usage:
    PYTHONPATH=src python3 tools/measure_memory_panorama.py            # print the report, save report.json
        [--days 30] [--vault V] [--projects DIR] [--out DIR]
    PYTHONPATH=src python3 tools/measure_memory_panorama.py --json     # the report as data

#520 (``measure_prevented_repeats``) and #527 (``measure_broad_value``) both
came out null against bars set before they ran. This reads what they left on
disk, plus mnemo's own logs and the transcripts, and **calls no model**. Every
number is post-hoc on data already seen, so each one is a **lead for a fresh,
pre-registered test, never a finding**; the report says so beside each block.

It reads:

- ``<vault>/.mnemo/broad-value/`` — #527's arms, answers, verdicts;
- ``<vault>/.mnemo/prevented-repeats/`` — #520's ``units.json`` (every rated
  unit and its outcome) and ``chunks.json`` (which rules the raters were shown);
- ``<vault>/.mnemo/reflex-log.jsonl*`` — the judge's per-rule scores and the
  silence reasons; ``mcp-access-log.jsonl*``, ``enrichment-log.jsonl``,
  ``denial-log.jsonl`` when present;
- ``learned.jsonl``, the pages under ``shared/`` and the 2026-09-22 audit;
- the human transcripts (``measure_prevented_repeats.collect_sessions``).

The report has five parts:

1. **Entry points, last ``--days`` of human sessions.** For each — the
   SessionStart envelope split into its topic menu, ``[last-briefing]``,
   ``[predicted-rules]``, ``[mnemo learned]`` and offers; the reflex; each MCP
   tool; PreToolUse enrichment and enforcement — how many sessions it fires
   in, how often, and the bytes it adds (tokens ≈ bytes / 4, the convention of
   ``measure_prevented_repeats.mnemo_cost``), as the transcript recorded it.
   An envelope Claude Code *persisted* (``<persisted-output>``: too large, the
   agent sees a 2 KB preview) is counted by what the agent saw. Then, over
   #520's rated sessions, what share of the rules each channel put in context
   #520's raters were ever shown, found relevant, and found new or redundant.
   #520's raters saw a prompt's BM25 top 10 and the strict pool only, so a
   delivered rule they were never shown could not be found relevant.
2. **Why #527's net help is ~0**: ``h = P(with better) − P(without better)``
   (both raters) sliced by page type × channel, reflex-judge era × answering
   model (with a within-model era difference), the judge's own score for the
   delivered rule, rule age, body length, gate-verified, #519/#524's real
   correction, the 2026-09-22 audit label and the text class of part 3. CIs
   are a bootstrap over units.
3. **The wins and losses themselves**, grouped by a transparent rule set over
   the rule's text (:data:`CLASS_RULES`, printed with the report): project
   fact or state, procedure, preference, generic practice, narrative.
4. **The redundancy the reflex spends**: the bytes spent on #520's relevant
   units that were delivered but already in ``CLAUDE.md`` or auto-memory, per
   session, and the rules that repeat most; beside it, every rule byte mnemo
   put in context by what #520 found it to be.
5. **Hypotheses** for why the memory does not help, ranked, each with its
   evidence and the cheapest fresh test that would confirm or kill it.

The text-class rule set was written after one look at the delivered texts,
so the class split is post-hoc like everything else here.

Private project names never leave the machine: every string of the report is
passed through ``<vault>/.mnemo/private-names.tsv`` (``name<TAB>alias``) when
that file exists, email addresses are masked, and the home directory becomes
``~``.

First run, 2026-09-28, the maintainer's vault (257 human sessions in the last
30 days; #520's 265 rated sessions; #527's 138 units). Every figure is a lead:

- **The before/after-judge gap is not the judge's.** #527's +0.20 → −0.09
  cannot be split within a model (no model answered ≥ 5 units in both eras:
  Opus 5 and Fable 5.1 before, Opus 5.5 after). But units the judge never
  selected (SessionStart, MCP) dropped *more*, −0.38 [−0.63, −0.13], than the
  reflex's own, −0.24 [−0.50, +0.02]. The gap goes with the answering model or
  the period, not with the reflex.
- **Most rule bytes go to rules no rater found relevant**: 78% of the rule
  bytes delivered in #520's sessions (42% to rules the raters were never
  shown), 6.1% to relevant rules Claude Code lacked. Of the relevant ones
  delivered, 64% were already in ``CLAUDE.md`` or auto-memory: 421 bytes per
  session, 3.9% of mnemo's bytes.
- **The agent reads the preview, not the rule**: 8 of 1,336 reflexed
  (session, rule) pairs were ever followed by ``read_mnemo_rule``. Among
  reflex units, long bodies beat short ones by +0.36 [+0.04, +0.68].
- **The menu is rarely taken**: 37 of 257 sessions ever call
  ``list_rules_by_topic``, and MCP units carry the highest h (+0.18). When a
  session does list, 39 of 58 listings lead to a read.
- **28 of 343 envelopes were persisted** by Claude Code (median 10.4 KB): the
  agent saw a 2 KB preview, and ``[mnemo learned]`` survived in none of them.
- By page type, project +0.19 [+0.02, +0.36], reference +0.03, feedback −0.08
  (these are the same 26 units as gate-verified). Record classes (narrative,
  project fact) minus advice classes: +0.16 [−0.04, +0.35]. The judge's score
  does not rise with help: ≥ 0.8 gives −0.25 (n 14), < 0.6 gives −0.08 (n 13).
- Enforcement never fired (no ``denial-log.jsonl``, no deny in a transcript),
  and enrichment carried 5 relevant units that #520 counted as undelivered.

Section 5 of the printed report ranks eleven hypotheses from these, each
with the cheapest fresh test.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bv = _sibling("measure_broad_value")
mpr = bv.mpr
mrc = bv.mrc

OUT_DIR = "memory-panorama"
DAYS = 30
SEED = 530
BOOTSTRAP = 4000
#: Tokens per byte of hook text: ``measure_prevented_repeats.mnemo_cost``'s convention.
BYTES_PER_TOKEN = 4
JUDGE_LIVE = mpr.JUDGE_LIVE
#: When ``injectAt`` went from 0.6 to 0.4 on the maintainer's machine (the
#: config backup ``mnemo.config.before-injectAt-2026-09-23.json``).
INJECT_AT_CHANGE = "2026-09-23T00:00:00Z"
EXAMPLES = 5
#: Fewest units per (model, era) cell for a model to enter the within-model difference.
MIN_CELL = 5
LEAD = "post-hoc on data already seen: a lead for a fresh test, not a finding"

WITH, WITHOUT, TIE = bv.WITH, bv.WITHOUT, bv.TIE
CHANNELS = mpr.CHANNELS
SECTIONS = ("menu", "last_briefing", "predicted", "learned", "offers")
SECTION_NAMES = {"menu": "topic menu", "last_briefing": "[last-briefing]",
                 "predicted": "[predicted-rules]", "learned": "[mnemo learned]", "offers": "offers"}
PERSISTED = "<persisted-output>"
_PERSISTED_SIZE = re.compile(r"Output too large \(([\d.]+)\s*KB\)")
_PREVIEW = re.compile(r"Preview \(first [^)]*\):\n(.*?)(?:\n\.\.\.)?\n?</persisted-output>", re.S)
_MCP_ANY = re.compile(r"mnemo.*__(read_mnemo_rule|list_rules_by_topic|get_mnemo_topics)$")
_ENRICH = "mnemo rule [["
#: The last line of a deny ``hooks/pre_tool_use._emit_deny`` writes; a line
#: start, so a file or grep that merely mentions the command is no denial.
_DENY = re.compile(r"(?m)^Fix: edit the file to remove or narrow the enforce block, "
                   r"or run `mnemo disable-rule [^`\s]+`")
_SLUGLIKE = re.compile(r"[a-z0-9][a-z0-9_-]*[-_][a-z0-9_-]*[a-z0-9]")

# block openers and closers of the SessionStart envelope (hooks/session_start.py)
_BLOCKS = (("[last-briefing", "[/last-briefing]", "last_briefing"),
           ("[predicted-rules", "[/predicted-rules]", "predicted"),
           ("[mnemo learned", "[/mnemo learned]", "learned"),
           ("[mnemo staged", "[/mnemo staged]", "offers"),
           ("[mnemo procedure", "[/mnemo procedures]", "offers"))


def _bytes(text: str) -> int:
    return len((text or "").encode("utf-8"))


def _tokens(n_bytes: float) -> float:
    return n_bytes / BYTES_PER_TOKEN


def _median(xs: Sequence[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def _share(k: int, n: int) -> Optional[float]:
    return k / n if n else None


# --- 0. the SessionStart envelope -------------------------------------------------------

def seen_text(text: str) -> Tuple[str, bool, Optional[float]]:
    """What the agent saw of a hook text: the preview when Claude Code
    persisted it, whether it did, and the size it reported (KB)."""
    if PERSISTED not in text:
        return text, False, None
    size = _PERSISTED_SIZE.search(text)
    preview = _PREVIEW.search(text)
    return (preview.group(1) if preview else ""), True, (float(size.group(1)) if size else None)


def split_envelope(text: str) -> Dict[str, str]:
    """The envelope's text per section: the bracketed blocks ``session_start``
    writes, one-paragraph ``[mnemo] …`` notices as offers, the rest the menu."""
    out: Dict[str, List[str]] = {s: [] for s in SECTIONS}
    closer, section = "", ""
    notice = False
    for ln in text.splitlines():
        if closer:
            out[section].append(ln)
            if ln.strip().startswith(closer):
                closer = ""
            continue
        if notice:
            if ln.strip():
                out["offers"].append(ln)
                continue
            notice = False
        opened = next(((c, s) for o, c, s in _BLOCKS if ln.strip().startswith(o)), None)
        if opened is not None:
            closer, section = opened
            out[section].append(ln)
            continue
        if ln.strip().startswith("[mnemo]"):
            notice = True
            out["offers"].append(ln)
            continue
        out["menu"].append(ln)
    return {s: "\n".join(v).strip("\n") for s, v in out.items()}


# --- 0. one transcript -------------------------------------------------------------------

def scan_session(events: List[dict]) -> Dict[str, Any]:
    """Everything mnemo put in one session's context, in order.

    Prompt indices are ``measure_prevented_repeats.walk``'s (a ``!`` shell
    turn takes an index but is no prompt). A hook block or MCP result belongs
    to the prompt it came with.
    """
    from mnemo.core.transcript import SYNTHETIC_TURN, plain_user_text

    ss: List[Dict[str, Any]] = []
    seen_ss: Set[str] = set()
    reflex: List[Dict[str, Any]] = []
    enrich: List[Dict[str, Any]] = []
    mcp: List[Dict[str, Any]] = []
    calls: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    cancelled = 0
    denials: List[int] = []
    n, current, prompts = 0, -1, 0
    seq = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        att = ev.get("attachment")
        if isinstance(att, dict):
            kind = att.get("type")
            hook = str(att.get("hookName") or att.get("hookEvent") or "")
            if kind == "hook_cancelled" and hook.startswith("UserPromptSubmit"):
                cancelled += 1
            if kind != "hook_additional_context":
                continue
            for text in mpr._hook_texts(att):
                if mpr._REFLEX in text:
                    seq += 1
                    reflex.append({"i": current, "seq": seq, "text": text,
                                   "slugs": mpr._WIKI.findall(text)})
                elif hook.startswith("PreToolUse") and _ENRICH in text:
                    seq += 1
                    enrich.append({"i": current, "seq": seq, "text": text,
                                   "slugs": mpr._WIKI.findall(text)})
                elif (hook.startswith("SessionStart") or not hook) and any(
                        m in text for m in mpr._MNEMO_START + (PERSISTED,)):
                    shown, persisted, size = seen_text(text)
                    if not any(m in shown for m in mpr._MNEMO_START) and not persisted:
                        continue
                    if persisted and not any(m in text for m in mpr._MNEMO_START):
                        continue
                    if text in seen_ss:
                        continue
                    seen_ss.add(text)
                    seq += 1
                    ss.append({"i": current, "seq": seq, "text": shown, "persisted": persisted,
                               "reported_kb": size, "bytes": _bytes(shown),
                               "sections": {s: _bytes(t) for s, t in split_envelope(shown).items()}})
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        if ev.get("type") == "assistant":
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    m = _MCP_ANY.search(str(b.get("name") or ""))
                    if m:
                        calls[str(b.get("id"))] = (m.group(1), b.get("input") or {})
            continue
        if ev.get("type") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                text = mpr._result_text(b)
                called = calls.get(str(b.get("tool_use_id")))
                if called is not None:
                    seq += 1
                    tool, inp = called
                    slugs = mpr._SLUG_KEY.findall(text) + mpr._WIKI.findall(text)
                    mcp.append({"i": current, "seq": seq, "tool": tool, "input": inp, "text": text,
                                "slugs": slugs})
                elif _DENY.search(text):
                    denials.append(_bytes(text))
        text = plain_user_text(content)
        if not text or SYNTHETIC_TURN.search(text):
            continue
        n += 1
        if mpr._SHELL_TURN.match(text):
            continue
        current = n - 1
        prompts += 1
    return {"prompts": prompts, "ss": ss, "reflex": reflex, "enrich": enrich, "mcp": mcp,
            "reflex_cancelled": cancelled, "denials": denials}


# --- 1. entry points ---------------------------------------------------------------------

def entry_points(scans: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per entry point: sessions it fired in, events, bytes (total, mean and
    median per session over *all* sessions) and tokens."""
    per: Dict[str, Dict[str, List[int]]] = {}

    def add(point: str, sid: str, n_bytes: int) -> None:
        per.setdefault(point, {}).setdefault(sid, []).append(n_bytes)

    for sid, sc in scans.items():
        for env in sc["ss"]:
            for s in SECTIONS:
                if env["sections"].get(s):
                    add("session_start: " + SECTION_NAMES[s], sid, env["sections"][s])
            add("session_start: whole envelope", sid, env["bytes"])
        for r in sc["reflex"]:
            add("reflex", sid, _bytes(r["text"]))
        for c in sc["mcp"]:
            add("mcp: " + c["tool"], sid, _bytes(c["text"]))
        for e in sc["enrich"]:
            add("pretooluse: enrichment", sid, _bytes(e["text"]))
        for d in sc["denials"]:
            add("pretooluse: enforcement (deny)", sid, d)
    n = len(scans)
    order = (["session_start: whole envelope"] + ["session_start: " + SECTION_NAMES[s] for s in SECTIONS]
             + ["reflex", "mcp: list_rules_by_topic", "mcp: read_mnemo_rule", "mcp: get_mnemo_topics",
                "pretooluse: enrichment", "pretooluse: enforcement (deny)"])
    out: Dict[str, Dict[str, Any]] = {}
    for point in order:
        got = per.get(point, {})
        totals = [sum(got.get(sid, [])) for sid in scans]
        total = sum(totals)
        out[point] = {"sessions": len(got), "share_sessions": _share(len(got), n),
                      "events": sum(len(v) for v in got.values()), "bytes": total,
                      "mean_bytes": total / n if n else 0.0, "median_bytes": _median(totals),
                      "mean_tokens": _tokens(total / n) if n else 0.0,
                      "median_bytes_when_fired": _median([sum(v) for v in got.values()])}
    return out


def envelope_facts(scans: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    envs = [e for sc in scans.values() for e in sc["ss"]]
    persisted = [e for e in envs if e["persisted"]]
    lost = [e for e in persisted if e["reported_kb"]]
    return {"envelopes": len(envs), "persisted": len(persisted),
            "persisted_reported_kb_median": _median([e["reported_kb"] for e in lost]),
            "persisted_seen_bytes_median": _median([e["bytes"] for e in persisted]),
            "learned_seen_in_persisted": sum(1 for e in persisted if e["sections"]["learned"])}


def reflex_facts(scans: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    prompts = sum(sc["prompts"] for sc in scans.values())
    fired = sum(len({r["i"] for r in sc["reflex"]}) for sc in scans.values())
    lines = [ln for sc in scans.values() for r in sc["reflex"] for ln in r["text"].splitlines()
             if mpr._WIKI.search(ln)]
    return {"prompts": prompts, "prompts_with_block": fired, "share": _share(fired, prompts),
            "rules_injected": len(lines), "median_line_bytes": _median([_bytes(x) for x in lines]),
            "cancelled": sum(sc["reflex_cancelled"] for sc in scans.values())}


def mcp_chain(scans: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Whether a listing is ever followed by a read, and where reads come from."""
    lists = reads = list_then_read = list_then_read_listed = 0
    read_listed = read_reflexed = read_enveloped = 0
    reflexed = reflexed_read = 0
    menu_sessions = menu_listed = 0
    for sc in scans.values():
        calls = sorted(sc["mcp"], key=lambda c: c["seq"])
        read_slugs = [(c["seq"], str(c["input"].get("slug") or "")) for c in calls
                      if c["tool"] == "read_mnemo_rule"]
        for c in calls:
            if c["tool"] == "list_rules_by_topic":
                lists += 1
                later = [s for q, s in read_slugs if q > c["seq"]]
                if later:
                    list_then_read += 1
                    if any(_in_names(s, c["slugs"]) for s in later):
                        list_then_read_listed += 1
        for q, slug in read_slugs:
            reads += 1
            if any(_in_names(slug, c["slugs"]) for c in calls
                   if c["tool"] == "list_rules_by_topic" and c["seq"] < q):
                read_listed += 1
            if any(_in_names(slug, r["slugs"]) for r in sc["reflex"] if r["seq"] < q):
                read_reflexed += 1
            if any(mpr._named(e["text"], slug) for e in sc["ss"] if e["seq"] < q):
                read_enveloped += 1
        pairs = {}
        for r in sc["reflex"]:
            for s in r["slugs"]:
                pairs.setdefault(s, r["seq"])
        for s, q in pairs.items():
            reflexed += 1
            if any(qq > q and _in_names(slug, [s]) for qq, slug in read_slugs):
                reflexed_read += 1
        if any(e["sections"]["menu"] for e in sc["ss"]):
            menu_sessions += 1
            if any(c["tool"] == "list_rules_by_topic" for c in calls):
                menu_listed += 1
    return {"lists": lists, "list_then_any_read": list_then_read,
            "list_then_read_of_a_listed_rule": list_then_read_listed,
            "reads": reads, "reads_of_a_listed_rule": read_listed,
            "reads_of_a_reflexed_rule": read_reflexed, "reads_named_by_envelope": read_enveloped,
            "reflexed_rules": reflexed, "reflexed_then_read": reflexed_read,
            "sessions_with_menu": menu_sessions, "menu_sessions_that_listed": menu_listed}


def _in_names(slug: str, names: Iterable[str]) -> bool:
    short = slug.split("__", 1)[-1]
    return any(n == slug or n.split("__", 1)[-1] == short for n in names)


def access_log_chain(rows: Iterable[Dict[str, Any]], since: str,
                     human: Optional[Set[str]] = None) -> Dict[str, Any]:
    """The same list → read question from ``mcp-access-log.jsonl``, which
    records every agent's calls (``human``: keep those sessions only)."""
    by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        if str(r.get("timestamp") or "") < since or r.get("tool") not in (
                "list_rules_by_topic", "read_mnemo_rule"):
            continue
        sid = str(r.get("session_id") or "")
        if human is not None and sid not in human:
            continue
        by_sid.setdefault(sid, []).append(r)
    lists = followed = followed_listed = reads = 0
    for sid, rs in by_sid.items():
        rs.sort(key=lambda r: str(r.get("timestamp") or ""))
        for n, r in enumerate(rs):
            if r["tool"] == "read_mnemo_rule":
                reads += 1
                continue
            lists += 1
            later = [x for x in rs[n + 1:] if x["tool"] == "read_mnemo_rule"]
            if later:
                followed += 1
                hits = set(r.get("hit_slugs") or [])
                if any(str((x.get("args") or {}).get("slug") or "") in hits for x in later):
                    followed_listed += 1
    return {"sessions": len(by_sid), "lists": lists, "list_then_any_read": followed,
            "list_then_read_of_a_listed_rule": followed_listed, "reads": reads}


def reflex_log_facts(rows: Iterable[Dict[str, Any]], since: str) -> Dict[str, Any]:
    """Silence reasons, judge status and the judge's scores, from ``reflex-log.jsonl*``."""
    silence: Dict[str, int] = {}
    status: Dict[str, int] = {}
    injected: List[float] = []
    dropped: List[float] = []
    n = emitted = 0
    first = ""
    for r in rows:
        ts = str(r.get("ts") or "")
        if ts < since:
            continue
        first = min(first, ts) if first else ts
        n += 1
        if r.get("emitted"):
            emitted += 1
        reason = r.get("silence_reason")
        if reason:
            silence[str(reason)] = silence.get(str(reason), 0) + 1
        j = r.get("judge")
        if isinstance(j, dict):
            status[str(j.get("status"))] = status.get(str(j.get("status")), 0) + 1
            kept = set(r.get("emitted") or [])
            for pair in j.get("scores") or []:
                try:
                    slug, score = str(pair[0]), float(pair[1])
                except (TypeError, ValueError, IndexError):
                    continue
                (injected if slug in kept else dropped).append(score)
    return {"rows": n, "since": first, "emitted": emitted, "share_emitted": _share(emitted, n),
            "silence": dict(sorted(silence.items(), key=lambda kv: -kv[1])), "judge_status": status,
            "injected_scores": _quantiles(injected), "dropped_scores": _quantiles(dropped)}


def _quantiles(xs: List[float]) -> Dict[str, Any]:
    if not xs:
        return {"n": 0}
    xs = sorted(xs)
    return {"n": len(xs), "p10": xs[int(0.1 * (len(xs) - 1))], "median": _median(xs),
            "p90": xs[int(0.9 * (len(xs) - 1))]}


# --- 1b. what #520 found of each channel's rules -----------------------------------------

def delivered_pairs(scans: Dict[str, Dict[str, Any]], projects: Dict[str, str], live: Set[str]
                    ) -> Dict[str, Dict[Tuple[str, str], Dict[str, int]]]:
    """``channel -> (session, today's slug) -> {"bytes", "section"...}`` for
    every rule a channel put in context. Rule bytes are the rule's own: its
    reflex or enrichment line, its read, its listing entry, its envelope line."""
    out: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]] = {
        "reflex": {}, "mcp read": {}, "mcp listing": {}, "session_start": {}, "enrichment": {}}
    shorts: Dict[str, Set[str]] = {}
    for s in live:
        shorts.setdefault(s.split("__", 1)[-1], set()).add(s)

    def add(channel: str, sid: str, slug: str, n_bytes: int, section: str = "") -> None:
        slug = mpr.canonical(slug, projects.get(sid, ""), live)
        got = out[channel].setdefault((sid, slug), {"bytes": 0, "times": 0, "sections": []})
        got["bytes"] += n_bytes
        got["times"] += 1
        if section and section not in got["sections"]:
            got["sections"].append(section)

    for sid, sc in scans.items():
        project = projects.get(sid, "")
        for kind, blocks in (("reflex", sc["reflex"]), ("enrichment", sc["enrich"])):
            for r in blocks:
                for part in _rule_parts(r["text"]):
                    for s in set(mpr._WIKI.findall(part)):
                        add(kind, sid, s, _bytes(part))
        for c in sc["mcp"]:
            if c["tool"] == "read_mnemo_rule" and c["input"].get("slug"):
                add("mcp read", sid, str(c["input"]["slug"]), _bytes(c["text"]))
                continue
            for slug, n_bytes in _listing_entries(c["text"]):
                add("mcp listing", sid, slug, n_bytes)
        for env in sc["ss"]:
            parts = split_envelope(env["text"])
            for section, text in parts.items():
                for ln in text.splitlines():
                    for word in set(_SLUGLIKE.findall(ln)):
                        cands = ({word} & live) or ({mpr.canonical(word, project, live)} & live) or {
                            s for s in shorts.get(word, set()) if s.startswith(project + "__")}
                        for s in cands:
                            add("session_start", sid, s, _bytes(ln), section)
    return out


def _rule_parts(text: str) -> List[str]:
    """A reflex or enrichment block cut into its rules: from one ``•`` to the next."""
    parts, cur = [], []
    for ln in text.splitlines():
        if ln.lstrip().startswith("•") and cur:
            parts.append("\n".join(cur))
            cur = []
        cur.append(ln)
    if cur:
        parts.append("\n".join(cur))
    return [p for p in parts if mpr._WIKI.search(p)]


def _listing_entries(text: str) -> List[Tuple[str, int]]:
    try:
        payload = json.loads(text)
    except ValueError:
        return [(s, _bytes(ln)) for ln in text.splitlines() for s in mpr._WIKI.findall(ln)]
    out: List[Tuple[str, int]] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if isinstance(obj.get("slug"), str):
                out.append((obj["slug"], _bytes(json.dumps(obj, ensure_ascii=False))))
                return
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)
    walk(payload)
    return out


def shown_pairs(chunks: Dict[str, List[Dict[str, Any]]]) -> Set[Tuple[str, str]]:
    """(session, slug) every rule #520's raters were shown in that session."""
    return {(sid, s) for sid, chs in chunks.items() for c in chs for s in c.get("rules") or []}


def channel_outcomes(pairs: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]],
                     units: Dict[Tuple[str, str], Dict[str, Any]], shown: Set[Tuple[str, str]],
                     rated: Set[str]) -> Dict[str, Dict[str, Any]]:
    """Per channel, of the (session, rule) pairs it delivered in #520's rated
    sessions: shown to the raters, relevant (a #520 unit), new, redundant."""
    out: Dict[str, Dict[str, Any]] = {}
    for channel, got in pairs.items():
        keys = [k for k in got if k[0] in rated]
        rel = [k for k in keys if k in units]
        new = [k for k in rel if units[k].get("new")]
        red = [k for k in rel if units[k].get("redundant")]
        row = {"delivered": len(keys), "shown": sum(1 for k in keys if k in shown),
               "relevant": len(rel), "new": len(new), "redundant": len(red)}
        row["share_shown"] = _share(row["shown"], row["delivered"])
        row["share_relevant_of_shown"] = _share(row["relevant"], row["shown"])
        row["share_new_of_relevant"] = _share(row["new"], row["relevant"])
        row["share_redundant_of_relevant"] = _share(row["redundant"], row["relevant"])
        if channel == "session_start":
            by: Dict[str, Dict[str, int]] = {}
            for k in keys:
                for sec in got[k]["sections"] or ["?"]:
                    b = by.setdefault(sec, {"delivered": 0, "relevant": 0, "new": 0, "redundant": 0})
                    b["delivered"] += 1
                    if k in units:
                        b["relevant"] += 1
                        b["new"] += int(bool(units[k].get("new")))
                        b["redundant"] += int(bool(units[k].get("redundant")))
            row["by_section"] = by
            judged = [k for k, u in units.items() if u.get("session_start") and k not in got]
            row["by_content_only"] = {"units": len(judged),
                                      "new": sum(1 for k in judged if units[k].get("new"))}
        if channel == "enrichment":
            missed = [k for k in rel if not units[k].get("delivered")]
            row["units_520_counted_undelivered"] = len(missed)
        out[channel] = row
    return out


def spend_by_outcome(pairs: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]],
                     units: Dict[Tuple[str, str], Dict[str, Any]], shown: Set[Tuple[str, str]],
                     rated: Set[str]) -> Dict[str, Dict[str, int]]:
    """Every rule byte mnemo delivered in #520's rated sessions, by what #520 found."""
    out: Dict[str, Dict[str, int]] = {}
    for channel, got in pairs.items():
        row = {"new": 0, "redundant": 0, "relevant, not new": 0, "shown, not relevant": 0,
               "never shown to the raters": 0}
        for k, v in got.items():
            if k[0] not in rated:
                continue
            u = units.get(k)
            if u is not None:
                key = "new" if u.get("new") else "redundant" if u.get("redundant") else "relevant, not new"
            else:
                key = "shown, not relevant" if k in shown else "never shown to the raters"
            row[key] += v["bytes"]
        out[channel] = row
    return out


# --- 2. slicing h -------------------------------------------------------------------------

def slice_h(units: Sequence[Dict[str, Any]], key: Callable[[Dict[str, Any]], Iterable[str]]
            ) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[float]] = {}
    for u in units:
        for name in key(u):
            groups.setdefault(name, []).append(u["h"])
    return {name: bv.h_ci(hs) for name, hs in sorted(groups.items())}


def era_model(units: Sequence[Dict[str, Any]], n_boot: int = BOOTSTRAP, seed: int = SEED,
              min_cell: int = MIN_CELL) -> Dict[str, Any]:
    """h per (answering model, judge era), and the before → after difference
    within models that answered at least ``min_cell`` units in each era,
    weighted by ``nb·na/(nb+na)``, with a bootstrap inside each cell.

    The reflex judge selects only what the reflex delivers, so the same split
    over units the reflex did not carry (SessionStart, MCP) separates the
    judge from everything else that changed with time: an era gap there is
    not the judge's."""
    cells: Dict[str, Dict[str, List[float]]] = {}
    for u in units:
        cells.setdefault(u["model"], {}).setdefault(u["era"], []).append(u["h"])
    table = {m: {e: bv.h_ci(hs) for e, hs in sorted(per.items())} for m, per in sorted(cells.items())}
    both = {m: per for m, per in cells.items()
            if len(per.get("before", [])) >= min_cell and len(per.get("after", [])) >= min_cell}

    def diff(get: Callable[[List[float]], List[float]]) -> Optional[float]:
        num = den = 0.0
        for per in both.values():
            b, a = get(per["before"]), get(per["after"])
            w = len(b) * len(a) / (len(b) + len(a))
            num += w * (sum(a) / len(a) - sum(b) / len(b))
            den += w
        return num / den if den else None

    out: Dict[str, Any] = {"table": table, "models_in_both_eras": sorted(both)}
    before = [u["h"] for u in units if u["era"] == "before"]
    after = [u["h"] for u in units if u["era"] == "after"]
    if before and after:
        out["raw_difference"] = sum(after) / len(after) - sum(before) / len(before)
    est = diff(lambda xs: xs)
    if est is not None:
        rng = random.Random(seed)
        boots = sorted(diff(lambda xs: [xs[rng.randrange(len(xs))] for _ in xs]) for _ in range(n_boot))
        out["within_model_difference"] = {"estimate": est, "ci": [bv._pct(boots, 0.025), bv._pct(boots, 0.975)],
                                          "units": sum(len(p["before"]) + len(p["after"]) for p in both.values())}
    by_carrier: Dict[str, Dict[str, Any]] = {}
    for name, keep in (("carried by the reflex", True), ("not carried by the reflex", False)):
        per = {e: [u["h"] for u in units if u["era"] == e and ("reflex" in u["carriers"]) == keep]
               for e in ("before", "after")}
        by_carrier[name] = {e: bv.h_ci(hs) for e, hs in per.items()}
    out["by_carrier"] = by_carrier
    return out


def score_bucket(score: Optional[float]) -> str:
    if score is None:
        return "no judge score"
    if score < 0.6:
        return "judge < 0.6"
    if score < 0.8:
        return "judge 0.6-0.8"
    return "judge >= 0.8"


def judge_index(rows: Iterable[Dict[str, Any]]) -> Dict[str, List[Tuple[float, Dict[str, float]]]]:
    """``session -> [(ts, {emitted slug: judge score})]``."""
    out: Dict[str, List[Tuple[float, Dict[str, float]]]] = {}
    for r in rows:
        j = r.get("judge")
        ts = mrc.epoch(r.get("ts"))
        if not isinstance(j, dict) or ts is None or not r.get("emitted"):
            continue
        kept = set(r.get("emitted") or [])
        scores = {}
        for pair in j.get("scores") or []:
            try:
                if str(pair[0]) in kept:
                    scores[str(pair[0])] = float(pair[1])
            except (TypeError, ValueError, IndexError):
                continue
        if scores:
            out.setdefault(str(r.get("session_id") or ""), []).append((ts, scores))
    return out


def unit_judge_score(index: Dict[str, List[Tuple[float, Dict[str, float]]]], sid: str, slug: str,
                     project: str, ts: Optional[float]) -> Optional[float]:
    """The judge's highest score for the rule in the session, up to the unit's prompt."""
    best = None
    for t, scores in index.get(sid, []):
        if ts is not None and t > ts + 120:
            continue
        for s, v in scores.items():
            if bv.same_rule(s, slug, project):
                best = v if best is None else max(best, v)
    return best


def age_bucket(days: Optional[float]) -> str:
    if days is None:
        return "age unknown"
    if days < 3:
        return "< 3 days"
    if days < 14:
        return "3-14 days"
    return ">= 14 days"


def terciles(values: Sequence[float]) -> Tuple[float, float]:
    xs = sorted(values)
    if not xs:
        return 0.0, 0.0
    return xs[len(xs) // 3], xs[(2 * len(xs)) // 3]


def length_bucket(n: int, cuts: Tuple[float, float]) -> str:
    if n < cuts[0]:
        return "short (< %d chars)" % cuts[0]
    if n < cuts[1]:
        return "medium (%d-%d)" % cuts
    return "long (>= %d)" % cuts[1]


# --- 3. what the delivered text was ---------------------------------------------------------

_DATE = re.compile(r"\b20\d\d-\d\d-\d\d\b|\b\d{1,2}/\d{1,2}/20\d\d\b|\b\d{1,2}/\d{1,2}\b")
_REF = re.compile(r"(?:\bPR\s*)?#\d{2,}\b")
_SHA = re.compile(r"`[0-9a-f]{7,12}`")
_EVENT = re.compile(
    r"\b(shipped|merged|released|landed|measured|found|discovered|fixed|verified|reverted|dispatched|"
    r"settled|corrected|published|mergeado|feito|rodei|descoberto|encerrad[oa]|despachad[oa]|aconteceu|"
    r"reportou|investiga[cç][aã]o|sess[aã]o de)\b", re.I)
_STEP = re.compile(r"(?m)^\s*\d+[.)]\s+\S")
_COMMAND = re.compile(r"`(?:git|gh|npm|npx|pnpm|yarn|bun|node|python3?|pip|pytest|mnemo|claude|cargo|"
                      r"psql|docker|curl|make|uv|brew|tauri|SET)\b[^`]*`")
_RECIPE = re.compile(r"\b(recipe|steps?|to reproduce|run it with|how to run|procedure)\b", re.I)
_PERSON = re.compile(
    r"\b(?:(?:the )?(?:user|maintainer|owner) (?:wants|prefers|asked|likes|dislikes|owns|decides|approves)|"
    r"prefer(?:s|red)?|preference|o usu[aá]rio (?:quer|prefere|pediu)|"
    r"ask (?:the user|for (?:their )?approval|before)|user(?:'s)? approval)\b", re.I)
_PATH = re.compile(r"[\w.-]+/[\w./-]+\.\w{1,5}\b")
_CODE = re.compile(r"`[^`\n]{2,80}`")


def text_signals(text: str) -> Dict[str, int]:
    return {"events": (len(set(_DATE.findall(text))) + len(set(_REF.findall(text)))
                       + len(set(_SHA.findall(text))) + len(set(m.lower() for m in _EVENT.findall(text)))),
            "steps": len(_STEP.findall(text)), "commands": len(set(_COMMAND.findall(text))),
            "recipe": len(_RECIPE.findall(text)), "person": len(_PERSON.findall(text)),
            "identifiers": len(set(_PATH.findall(text))) + len(set(_CODE.findall(text)))}


#: The rule set, first match wins. Printed with the report, so a reader can
#: check any unit's class by hand.
CLASS_RULES: Tuple[Tuple[str, str, Callable[[Dict[str, int], str], bool]], ...] = (
    ("narrative", "recounts what happened: >= 6 event markers (dates, PR/issue numbers, commit SHAs, "
                  "distinct event verbs such as shipped/merged/found/descoberto)",
     lambda s, t: s["events"] >= 6),
    ("preference", "says what the person wants or decides: user/maintainer wants, prefers, owns, approves; "
                   "a preference; ask the user / for approval / before",
     lambda s, t: s["person"] >= 1),
    ("procedure", "says how to do something: >= 3 numbered steps, or >= 2 distinct shell commands in "
                  "backticks, or a recipe/steps word with >= 1 command",
     lambda s, t: s["steps"] >= 3 or s["commands"] >= 2 or bool(s["recipe"] and s["commands"])),
    ("project fact or state", "names this project's specifics: a project page, or >= 3 distinct file paths "
                              "or code spans",
     lambda s, t: t == "project" or s["identifiers"] >= 3),
    ("generic practice", "none of the above: advice that holds in any repository",
     lambda s, t: True),
)


def classify(text: str, page_type: str) -> str:
    sig = text_signals(text)
    return next(name for name, _, rule in CLASS_RULES if rule(sig, page_type))


# --- 4. redundancy ---------------------------------------------------------------------------

def redundancy(pairs: Dict[str, Dict[Tuple[str, str], Dict[str, Any]]],
               unit_rows: Sequence[Dict[str, Any]], rated: Set[str], total_bytes: int,
               emitted_counts: Dict[str, int]) -> Dict[str, Any]:
    """#520's relevant units delivered but already in CLAUDE.md / auto-memory:
    what mnemo spent on them and which rules repeat."""
    red = [u for u in unit_rows if u.get("delivered") and u.get("redundant") and u["session_id"] in rated]
    delivered = [u for u in unit_rows if u.get("delivered") and u["session_id"] in rated]
    per_rule: Dict[str, Dict[str, Any]] = {}
    per_channel = {c: 0 for c in pairs}
    spent = 0
    for u in red:
        k = (u["session_id"], u["slug"])
        b = 0
        for c, got in pairs.items():
            if k in got:
                per_channel[c] += got[k]["bytes"]
                b += got[k]["bytes"]
        spent += b
        r = per_rule.setdefault(u["slug"], {"sessions": 0, "bytes": 0})
        r["sessions"] += 1
        r["bytes"] += b
    n = len(rated)
    top = sorted(per_rule.items(), key=lambda kv: (-kv[1]["sessions"], -kv[1]["bytes"], kv[0]))[:10]
    return {"units": len(red), "delivered_relevant": len(delivered),
            "share_of_delivered_relevant": _share(len(red), len(delivered)),
            "bytes": spent, "bytes_per_session": spent / n if n else 0.0,
            "tokens_per_session": _tokens(spent / n) if n else 0.0,
            "share_of_all_mnemo_bytes": _share(spent, total_bytes), "by_channel": per_channel,
            "rules": len(per_rule),
            "top": [{"slug": s, "sessions": v["sessions"], "bytes": v["bytes"],
                     "reflex_emissions_logged": emitted_counts.get(s, 0)} for s, v in top]}


# --- 5. hypotheses ----------------------------------------------------------------------------

def diff_ci(a: Sequence[float], b: Sequence[float], n_boot: int = BOOTSTRAP, seed: int = SEED
            ) -> Optional[Dict[str, Any]]:
    """mean(a) − mean(b), with a bootstrap over the units of each group."""
    if not a or not b:
        return None
    rng = random.Random(seed)
    boots = sorted(sum(a[rng.randrange(len(a))] for _ in a) / len(a)
                   - sum(b[rng.randrange(len(b))] for _ in b) / len(b) for _ in range(n_boot))
    return {"estimate": sum(a) / len(a) - sum(b) / len(b), "ci": [bv._pct(boots, 0.025), bv._pct(boots, 0.975)],
            "n": [len(a), len(b)]}


#: Text classes that tell the agent what to do, and those that record what is.
ADVICE = ("procedure", "preference", "generic practice")
RECORD = ("narrative", "project fact or state")


def _signal(diff: Optional[Dict[str, Any]], direction: int) -> int:
    """2 when the CI excludes 0 in ``direction`` (+1/−1), 1 when the point
    estimate lies that way, else 0."""
    if not diff:
        return 0
    lo, hi = diff["ci"]
    if (direction > 0 and lo > 0) or (direction < 0 and hi < 0):
        return 2
    return 1 if diff["estimate"] * direction > 0 else 0


def _diff_text(diff: Optional[Dict[str, Any]]) -> str:
    if not diff:
        return "n/a"
    return "%+.3f [%+.3f, %+.3f] (n %d vs %d)" % (diff["estimate"], diff["ci"][0], diff["ci"][1],
                                                  diff["n"][0], diff["n"][1])


def hypotheses(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Ranked hypotheses, each with the evidence behind it and the cheapest
    fresh test. Ranked by ``signal`` (2: a count, or a CI that excludes 0 in
    the stated direction; 1: a point estimate that way; 0: none, or the
    evidence runs the other way), then ``reach`` (the share of #527's units,
    or of mnemo's bytes, it bears on). Each claim is stated in the direction
    the evidence points, so a signal 0 is a hypothesis the data does not
    support, kept so a reader sees it was looked at."""
    out: List[Dict[str, Any]] = []
    s2 = d.get("slices") or {}
    groups = d.get("unit_groups") or {}
    units = max(1, d.get("units_measured") or 0)

    spend = d.get("spend") or {}
    tot = {k: sum(row.get(k, 0) for row in spend.values())
           for k in ("new", "redundant", "relevant, not new", "shown, not relevant", "never shown to the raters")}
    all_bytes = sum(tot.values())
    if all_bytes:
        off = tot["shown, not relevant"] + tot["never shown to the raters"]
        refl = spend.get("reflex") or {}
        refl_all = sum(refl.values())
        out.append({
            "id": "H-relevance",
            "claim": "Most of what mnemo puts in context bears on nothing the user is doing, so most "
                     "deliveries have nothing to improve.",
            "evidence": ["%.0f%% of the rule bytes mnemo delivered in #520's sessions went to rules #520's raters "
                         "did not find relevant (%.0f%% to rules they were never shown: unknown, not irrelevant); "
                         "%.1f%% went to relevant rules Claude Code lacked"
                         % (100 * off / all_bytes, 100 * tot["never shown to the raters"] / all_bytes,
                            100 * tot["new"] / all_bytes)]
            + (["reflex alone: %.0f%% of its bytes were shown to the raters and not found relevant"
                % (100 * refl.get("shown, not relevant", 0) / refl_all)] if refl_all else []),
            "test": "Label ~150 delivered (prompt, rule) pairs from sessions after today, two blind raters, "
                    "relevant yes/no, with no BM25 pre-filter so every delivered rule is judged. Kill it if "
                    ">= 40% are relevant.",
            "signal": 2, "reach": off / all_bytes})
    red = d.get("redundancy") or {}
    if red.get("delivered_relevant"):
        out.append({
            "id": "H-redundant",
            "claim": "When mnemo does deliver a relevant rule, Claude Code's own CLAUDE.md or auto-memory "
                     "usually says it already, so the delivery adds nothing.",
            "evidence": ["%d of %d relevant delivered units (%.0f%%) were redundant; they cost %.0f bytes "
                         "(~%.0f tokens) per session, %.1f%% of all mnemo bytes"
                         % (red["units"], red["delivered_relevant"], 100 * red["share_of_delivered_relevant"],
                            red["bytes_per_session"], red["tokens_per_session"],
                            100 * (red.get("share_of_all_mnemo_bytes") or 0))],
            "test": "Offline, run #520's delivery judge (text B only) on the next ~100 reflex injections. If "
                    ">= 50% are redundant, a native-memory dedupe before injecting is worth building; if < 30%, "
                    "drop the hypothesis.",
            "signal": 2, "reach": red["share_of_delivered_relevant"]})
    em = s2.get("era_model") or {}
    bc = em.get("by_carrier") or {}
    carried = bc.get("carried by the reflex") or {}
    other = bc.get("not carried by the reflex") or {}
    era_r = diff_ci(groups.get("after reflex") or [], groups.get("before reflex") or [])
    era_o = diff_ci(groups.get("after other") or [], groups.get("before other") or [])
    if era_r or era_o:
        judge_only = bool(era_r and era_o and era_r["estimate"] < 0 and era_o["estimate"] > era_r["estimate"] / 2)
        wm = em.get("within_model_difference")
        ev = ["raw after − before h %+.3f; answering models: %s" % (
            em.get("raw_difference", 0.0), "; ".join(
                "%s %s" % (m, ", ".join("%s %d" % (e, st["n"]) for e, st in per.items()))
                for m, per in em.get("table", {}).items()))]
        ev.append("within a model with >= %d units in each era: %s" % (
            MIN_CELL, "none — era and answering model cannot be separated on these units" if not wm else
            "%+.3f [%+.3f, %+.3f] (%s)" % (wm["estimate"], wm["ci"][0], wm["ci"][1],
                                           ", ".join(em["models_in_both_eras"]))))
        ev.append("after − before, units the reflex carried: %s; units it did not (SessionStart, MCP — the "
                  "judge never saw them): %s" % (_diff_text(era_r), _diff_text(era_o)))
        out.append({
            "id": "H-judge-era",
            "claim": ("The reflex judge lets through rules that help less than the lexical gates' did: the drop "
                      "after it went live is confined to what the reflex carries."
                      if judge_only else
                      "The before/after-judge drop is not the judge's: units the judge never selected dropped "
                      "too, so it goes with the answering model or the time, not with the reflex."),
            "evidence": ev,
            "test": "Re-answer #527's 138 units on one model (both arms, 2 samples, the same raters), so era and "
                    "model separate by design: ~550 answer + ~550 judge calls. Kill the judge reading if the "
                    "reflex units' era gap on one model is inside ±0.1.",
            "signal": _signal(era_r, -1) if judge_only else max(_signal(era_o, -1), 1 if era_r else 0),
            "reach": ((carried.get("before") or {}).get("n", 0) + (carried.get("after") or {}).get("n", 0)
                      + (other.get("before") or {}).get("n", 0) + (other.get("after") or {}).get("n", 0)) / units})
    js = s2.get("judge score (reflex units)") or {}
    lo, hi = groups.get("judge < 0.6") or [], groups.get("judge >= 0.8") or []
    if lo or hi:
        dd = diff_ci(hi, lo)
        out.append({
            "id": "H-judge-score",
            "claim": "The judge's score does not predict help: a rule it scores high is on-topic, not useful.",
            "evidence": ["; ".join("%s %s" % (k, _h_text(v)) for k, v in sorted(js.items())),
                         "judge >= 0.8 minus judge < 0.6: %s" % _diff_text(dd)],
            "test": "Stratify next week's reflex injections by judge score and run #527's two arms on 20 per "
                    "stratum; kill if h rises with the score by >= 0.2 from the lowest to the highest.",
            "signal": 0 if dd is None else 2 if dd["ci"][1] < 0.2 and dd["estimate"] <= 0 else
            1 if dd["estimate"] <= 0 else 0,
            "reach": sum(v.get("n", 0) for v in js.values()) / units})
    adv = [h for c in ADVICE for h in groups.get("class " + c) or []]
    rec = [h for c in RECORD for h in groups.get("class " + c) or []]
    pt = s2.get("page type") or {}
    if adv and rec:
        dd = diff_ci(rec, adv)
        pd = diff_ci(groups.get("type project") or [], groups.get("type feedback") or [])
        out.append({
            "id": "H-advice-does-not-help",
            "claim": "What helps is a record of this project (its state, what happened); advice about how "
                     "to work (procedures, preferences, general practice) does not.",
            "evidence": ["record (%s) minus advice (%s), by this tool's text classes: %s"
                         % (" + ".join(RECORD), " + ".join(ADVICE), _diff_text(dd)),
                         "by the vault's page type: project %s; reference %s; feedback %s; project minus "
                         "feedback %s" % (_h_text(pt.get("project") or {}), _h_text(pt.get("reference") or {}),
                                          _h_text(pt.get("feedback") or {}), _diff_text(pd))],
            "test": "Pre-register the classes by this rule set; one week with the reflex limited to record "
                    "pages, then #527's arms on 60 fresh units of each class. Kill if record − advice is "
                    "inside ±0.1.",
            "signal": max(_signal(dd, 1), _signal(pd, 1)), "reach": (len(adv) + len(rec)) / units})
    env = d.get("envelope") or {}
    if env.get("persisted"):
        out.append({
            "id": "H-envelope-truncated",
            "claim": "Part of the SessionStart envelope never reaches the agent: Claude Code persists an "
                     "oversized hook output and shows a 2 KB preview.",
            "evidence": ["%d of %d envelopes in the last %s days were persisted (median reported %.1f KB, the "
                         "agent saw a median %d bytes); [mnemo learned] survived in %d of them"
                         % (env["persisted"], env["envelopes"], d.get("days"), env["persisted_reported_kb_median"],
                            env["persisted_seen_bytes_median"], env["learned_seen_in_persisted"])],
            "test": "Zero-token: cap the envelope under the persist limit and rerun this tool after a week; "
                    "the persisted count must reach 0. Whether the uncut envelope then helps is #527's arms "
                    "again.",
            "signal": 2, "reach": env["persisted"] / max(1, env["envelopes"])})
    ch = d.get("mcp_chain") or {}
    if ch.get("reflexed_rules"):
        long_short = diff_ci(groups.get("reflex long") or [], groups.get("reflex short") or [])
        out.append({
            "id": "H-preview-only",
            "claim": "The agent acts on a one-line preview: it almost never reads the full rule the reflex "
                     "points at, so a rule whose value is in its body cannot help.",
            "evidence": ["%d of %d reflexed (session, rule) pairs were followed by read_mnemo_rule; median "
                         "reflex line %d bytes" % (ch["reflexed_then_read"], ch["reflexed_rules"],
                                                  (d.get("reflex") or {}).get("median_line_bytes", 0)),
                         "reflex units, long body minus short body: %s" % _diff_text(long_short)],
            "test": "Re-answer #527's reflex units with the full body in place of the preview line (one "
                    "extra arm, the same raters); kill if h(full) − h(preview) is inside ±0.1.",
            "signal": 2 if ch["reflexed_then_read"] / ch["reflexed_rules"] < 0.1 else 1,
            "reach": ((s2.get("channel") or {}).get("reflex") or {}).get("n", 0) / units})
    if ch.get("sessions_with_menu"):
        took = ch["menu_sessions_that_listed"] / ch["sessions_with_menu"]
        out.append({
            "id": "H-menu-ignored",
            "claim": "The MCP path, the one whose units help most, is rarely taken: the menu asks for "
                     "list_rules_by_topic before any code and most sessions never call it.",
            "evidence": ["%d of %d sessions with the topic menu ever listed; when one lists, %d of %d listings "
                         "lead to a read of a rule they listed" % (
                             ch["menu_sessions_that_listed"], ch["sessions_with_menu"],
                             ch["list_then_read_of_a_listed_rule"], ch["lists"]),
                         "MCP units %s" % _h_text((s2.get("channel") or {}).get("mcp") or {})],
            "test": "Zero-token: rerun this tool a month after any change to the menu's wording; the share "
                    "of sessions that list must move from %.0f%%, or the menu is inert." % (100 * took),
            "signal": 2 if took < 0.3 else 0,
            "reach": ((s2.get("channel") or {}).get("mcp") or {}).get("n", 0) / units})
    base = d.get("base") or {}
    if base.get("rate") and base.get("h") is not None:
        need = mpr.THRESHOLD / base["rate"]
        upper = (base.get("h_ci") or [None, None])[1]
        out.append({
            "id": "H-too-few-chances",
            "claim": "Delivery volume caps the result as much as quality: with so few relevant, new "
                     "deliveries per session, each one must help far more often than it hurts.",
            "evidence": ["%.2f delivered-and-new units per session, so clearing 1 per 15 needs h >= %.3f; "
                         "observed %+.3f%s" % (base["rate"], need, base["h"],
                                              "" if upper is None else " (CI upper %+.3f)" % upper)],
            "test": "Arithmetic, not a test. It dies if a fresh #520 run after a delivery change shows "
                    ">= %.2f delivered-and-new units per session." % (2 * base["rate"]),
            "signal": 2 if upper is not None and need > upper else 1 if need > base["h"] else 0,
            "reach": 1.0})
    ag = d.get("agreement") or {}
    if ag.get("kappa") is not None:
        out.append({
            "id": "H-rater-noise",
            "claim": "The raters cannot see a small help: two models agree barely above chance, so real "
                     "wins are read as ties.",
            "evidence": ["kappa %.2f on %d comparisons; ties %.0f%% on the both-raters reading"
                         % (ag["kappa"], ag["n"], 100 * ((d.get("shares") or {}).get("share_tie") or 0))],
            "test": "The maintainer labels 30 of #527's comparisons blind; kill if the maintainer agrees "
                    "with the both-raters reading >= 70% of the time.",
            "signal": 1 if ag["kappa"] < 0.4 else 0, "reach": 1.0})
    enr = (d.get("channels") or {}).get("enrichment") or {}
    if enr.get("units_520_counted_undelivered"):
        out.append({
            "id": "H-enrichment-uncounted",
            "claim": "#520 undercounted delivery: PreToolUse enrichment carried relevant rules it counted as "
                     "undelivered.",
            "evidence": ["%d relevant units were enriched in-session but counted undelivered"
                         % enr["units_520_counted_undelivered"]],
            "test": "Zero-token: add enrichment as a channel to #520's unit outcome and rerun it from its cache.",
            "signal": 2, "reach": enr["units_520_counted_undelivered"] / units})
    out.sort(key=lambda h: (-h["signal"], -h["reach"], h["id"]))
    for n, h in enumerate(out, 1):
        h["rank"] = n
    return out


def _h_text(st: Dict[str, Any]) -> str:
    if not st.get("n"):
        return "no units"
    return "h %+.3f [%+.3f, %+.3f] (n %d)" % (st["h"], st["ci"][0], st["ci"][1], st["n"])


# --- privacy ---------------------------------------------------------------------------------

def load_aliases(path: Path) -> List[Tuple[str, str]]:
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for row in rows:
        parts = row.split("\t")
        if len(parts) >= 2 and parts[0].strip():
            out.append((parts[0].strip(), parts[1].strip() or "a private repo"))
    return sorted(out, key=lambda p: -len(p[0]))


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


def redact(text: str, aliases: Sequence[Tuple[str, str]], home: str = "") -> str:
    """Home directory to ``~``, email addresses masked, each private name to its alias."""
    if home:
        text = text.replace(home, "~")
    text = _EMAIL.sub("<email>", text)
    for name, alias in aliases:
        text = re.sub(r"(?i)(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), alias, text)
    return text


# --- report ------------------------------------------------------------------------------------

def _p(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def report_lines(d: Dict[str, Any]) -> List[str]:
    L: List[str] = []
    L.append("Zero model calls. Every number below is %s." % LEAD)
    L.append("")
    L.append("== 1. Entry points: %d human sessions in the last %d days (since %s), %d typed prompts =="
             % (d["sessions_window"], d["days"], d["since"], d["reflex"]["prompts"]))
    L.append("  %-38s %9s %8s %11s %11s %9s" % ("", "sessions", "events", "bytes/sess", "median", "tok/sess"))
    for point, r in d["entry_points"].items():
        L.append("  %-38s %4d %4s %8d %11.0f %11.0f %9.0f" % (
            point, r["sessions"], _p(r["share_sessions"]).rjust(6)[:6], r["events"], r["mean_bytes"],
            r["median_bytes"], r["mean_tokens"]))
    env = d["envelope"]
    L.append("  envelopes persisted by Claude Code (agent saw a 2 KB preview): %d of %d; reported size median "
             "%.1f KB; [mnemo learned] visible in %d of them"
             % (env["persisted"], env["envelopes"], env["persisted_reported_kb_median"],
                env["learned_seen_in_persisted"]))
    rf = d["reflex"]
    L.append("  reflex: a block on %d of %d prompts (%s), %d rule lines, median line %d bytes; "
             "%d UserPromptSubmit hook(s) cancelled" % (rf["prompts_with_block"], rf["prompts"], _p(rf["share"]),
                                                        rf["rules_injected"], rf["median_line_bytes"], rf["cancelled"]))
    lg = d["reflex_log"]
    if lg.get("rows"):
        L.append("  reflex-log since %s: %d prompts, %s emitted; silence %s; judge %s"
                 % (lg["since"][:10], lg["rows"], _p(lg["share_emitted"]),
                    ", ".join("%s %d" % kv for kv in lg["silence"].items()),
                    ", ".join("%s %d" % kv for kv in sorted(lg["judge_status"].items()))))
        for k in ("injected_scores", "dropped_scores"):
            q = lg[k]
            if q.get("n"):
                L.append("    judge scores %-8s n %d, p10 %.2f, median %.2f, p90 %.2f"
                         % (k.split("_")[0], q["n"], q["p10"], q["median"], q["p90"]))
        L.append("    injectAt now %s (0.6 from the judge's start to %s)" % (d.get("inject_at"), INJECT_AT_CHANGE[:10]))
    ch = d["mcp_chain"]
    L.append("  MCP in transcripts: %d list_rules_by_topic, %d followed by any read, %d by a read of a rule it "
             "listed; %d read_mnemo_rule (%d of a listed rule, %d of a reflexed rule, %d named by the envelope)"
             % (ch["lists"], ch["list_then_any_read"], ch["list_then_read_of_a_listed_rule"], ch["reads"],
                ch["reads_of_a_listed_rule"], ch["reads_of_a_reflexed_rule"], ch["reads_named_by_envelope"]))
    L.append("    sessions with the topic menu: %d, of which %d ever called list_rules_by_topic; reflexed "
             "(session, rule) pairs later read in full: %d of %d"
             % (ch["sessions_with_menu"], ch["menu_sessions_that_listed"], ch["reflexed_then_read"],
                ch["reflexed_rules"]))
    for name, al in d["access_log"].items():
        L.append("    mcp-access-log (%s): %d sessions, %d lists, %d followed by any read, %d by a listed read"
                 % (name, al["sessions"], al["lists"], al["list_then_any_read"], al["list_then_read_of_a_listed_rule"]))
    L.append("  denial-log.jsonl: %s; enrichment-log rows since then: %d"
             % ("%d rows" % d["denial_log"] if d["denial_log"] is not None else "absent (no deny ever logged)",
                d["enrichment_log"]))
    L.append("")
    L.append("  What #520 (%d rated sessions since %s) found of each channel's rules, (session, rule) pairs:"
             % (d["sessions_520"], d["since_520"]))
    L.append("    %-14s %9s %14s %16s %11s %13s" % ("", "delivered", "shown raters", "relevant|shown",
                                                 "new|rel", "redundant|rel"))
    for c, r in d["channels"].items():
        L.append("    %-14s %9d %6d %7s %8d %7s %4d %6s %6d %6s" % (
            c, r["delivered"], r["shown"], _p(r["share_shown"]), r["relevant"], _p(r["share_relevant_of_shown"]),
            r["new"], _p(r["share_new_of_relevant"]), r["redundant"], _p(r["share_redundant_of_relevant"])))
        for sec, b in sorted((r.get("by_section") or {}).items()):
            L.append("      %-20s delivered %d, relevant %d, new %d, redundant %d"
                     % (SECTION_NAMES.get(sec, sec), b["delivered"], b["relevant"], b["new"], b["redundant"]))
        if r.get("by_content_only"):
            L.append("      said by content only (#520's delivery judge), not named: %d units, %d new"
                     % (r["by_content_only"]["units"], r["by_content_only"]["new"]))
        if "units_520_counted_undelivered" in r:
            L.append("      relevant units enriched in-session that #520 counted undelivered: %d"
                     % r["units_520_counted_undelivered"])
    L.append("")
    L.append("== 2. Why #527's net help is ~0: h = P(with better) − P(without better), both raters, CI over units ==")
    b = d["base"]
    L.append("  all %d units: h %+.3f; %.2f delivered-and-new units per session (#527's report)"
             % (d["units_measured"], b.get("h") or 0.0, b.get("rate") or 0.0))
    for title, groups in d["slices"].items():
        if title == "era_model":
            continue
        L.append("  by %s:" % title)
        for name, st in groups.items():
            L.append("    %-34s %s" % (name, bv._h(st)))
    em = d["slices"]["era_model"]
    L.append("  by answering model x reflex judge era (before/after %s):" % JUDGE_LIVE)
    for m, per in em["table"].items():
        L.append("    %-22s %s" % (m, "; ".join("%s %s" % (e, _h_text(st)) for e, st in per.items())))
    if "raw_difference" in em:
        L.append("    raw after − before: %+.3f" % em["raw_difference"])
    wm = em.get("within_model_difference")
    if wm:
        L.append("    within models with >= %d units in each era (%s): %+.3f [%+.3f, %+.3f] over %d units"
                 % (MIN_CELL, ", ".join(em["models_in_both_eras"]), wm["estimate"], wm["ci"][0], wm["ci"][1],
                    wm["units"]))
    else:
        L.append("    no model answered >= %d units in each era: era and model cannot be separated within a model"
                 % MIN_CELL)
    for name, per in (em.get("by_carrier") or {}).items():
        L.append("    %-26s %s" % (name, "; ".join("%s %s" % (e, _h_text(st)) for e, st in per.items())))
    for note in d.get("same_units") or []:
        L.append("  note: %s" % note)
    L.append("  (%s)" % LEAD)
    L.append("")
    L.append("== 3. The wins and losses by what the delivered text was ==")
    L.append("  the rule set, first match wins, over the rule page's name + body:")
    for name, desc, _ in CLASS_RULES:
        L.append("    %-22s %s" % (name, desc))
    for name, g in d["classes"].items():
        L.append("  %s: %d units, %d wins, %d losses, %d ties; %s"
                 % (name, g["units"], len(g["wins_all"]), len(g["losses_all"]), g["ties"],
                    _h_text(d["slices"]["text class"].get(name) or {})))
        for kind in ("wins", "losses"):
            for e in g[kind]:
                L.append("    %s %+.1f  %s — %s" % ("+" if kind == "wins" else "-", e["h"], e["rule"], e["head"]))
                L.append("          prompt: %s" % e["prompt"])
    L.append("  (%s)" % LEAD)
    L.append("")
    L.append("== 4. The redundancy mnemo spends ==")
    r = d["redundancy"]
    L.append("  %d of %d relevant delivered units (%s) were already in CLAUDE.md / auto-memory: %d bytes, "
             "%.0f bytes (~%.0f tokens) per rated session, %s of every mnemo byte in those sessions"
             % (r["units"], r["delivered_relevant"], _p(r["share_of_delivered_relevant"]), r["bytes"],
                r["bytes_per_session"], r["tokens_per_session"], _p(r["share_of_all_mnemo_bytes"])))
    L.append("  by channel: " + ", ".join("%s %d" % kv for kv in r["by_channel"].items()))
    L.append("  rules that repeat most (sessions where delivered and redundant; bytes; reflex emissions in the log):")
    for t in r["top"]:
        L.append("    %3d  %6d  %4d  %s" % (t["sessions"], t["bytes"], t["reflex_emissions_logged"], t["slug"]))
    L.append("  every rule byte delivered in #520's sessions, by what #520 found it to be:")
    for c, row in d["spend"].items():
        tot = sum(row.values())
        if tot:
            L.append("    %-12s %8d bytes: " % (c, tot) + ", ".join("%s %s" % (k, _p(v / tot)) for k, v in row.items()))
    L.append("  (%s)" % LEAD)
    L.append("")
    L.append("== 5. Hypotheses for why the memory does not help, ranked ==")
    L.append("  rank: signal first (2 = a count, or a CI that excludes 0; 1 = a point estimate in the stated "
             "direction; 0 = none), then reach (share of #527's units or of mnemo's bytes it bears on)")
    for h in d["hypotheses"]:
        L.append("  %d. [%s] signal %d, reach %.0f%% — %s" % (h["rank"], h["id"], h["signal"], 100 * h["reach"],
                                                             h["claim"]))
        for e in h["evidence"]:
            L.append("       evidence: %s" % e)
        L.append("       cheapest fresh test: %s" % h["test"])
    L.append("  Every hypothesis is a lead; none is a finding until its fresh test runs.")
    return L


# --- driver ----------------------------------------------------------------------------------------

def _jsonl(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    out = []
    for p in paths:
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for ln in lines:
            try:
                row = json.loads(ln)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def _rotated(path: Path) -> List[Path]:
    """``path`` and its rotations, oldest first."""
    olds = sorted(path.parent.glob(path.name + ".*"), key=lambda p: p.name, reverse=True)
    return olds + [path]


def page_bodies(vault: Path) -> Dict[str, Dict[str, str]]:
    """``slug -> {type, name, body}``, the whole body."""
    from mnemo.core.filters import derive_rule_slug
    from mnemo.core.reclassify_types import split_frontmatter

    out: Dict[str, Dict[str, str]] = {}
    for page_type in ("feedback", "user", "reference", "project"):
        for md in sorted((vault / "shared" / page_type).glob("*.md")):
            try:
                fm, body = split_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            out[derive_rule_slug(fm, md.stem)] = {"type": page_type, "name": str(fm.get("name") or md.stem),
                                                  "body": body}
    return out


def examples(units: Sequence[Dict[str, Any]], rng: random.Random, n: int = EXAMPLES) -> List[Dict[str, Any]]:
    """Up to ``n`` units, seeded, one per rule: a rule delivered in several
    sessions shows once."""
    picked: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for u in rng.sample(list(units), len(units)):
        if u["slug"] not in seen:
            seen.add(u["slug"])
            picked.append(u)
        if len(picked) == n:
            break
    return picked


def unit_groups(measured: Sequence[Dict[str, Any]], cuts: Tuple[float, float]) -> Dict[str, List[float]]:
    """The h values the hypotheses compare, by name."""
    g: Dict[str, List[float]] = {}

    def add(name: str, m: Dict[str, Any]) -> None:
        g.setdefault(name, []).append(m["h"])

    for m in measured:
        reflexed = "reflex" in m["carriers"]
        add("%s %s" % (m["era"], "reflex" if reflexed else "other"), m)
        add("class " + m["class"], m)
        add("type " + m["type"], m)
        if reflexed:
            add(score_bucket(m["judge"]), m)
            if m["length"] >= cuts[1]:
                add("reflex long", m)
            elif m["length"] < cuts[0]:
                add("reflex short", m)
    return g


def same_units(measured: Sequence[Dict[str, Any]]) -> List[str]:
    """Slices that are the very same units, so a reader does not count one
    effect twice."""
    out = []
    gated = {m["id"] for m in measured if m["gate_verified"]}
    feedback = {m["id"] for m in measured if m["type"] == "feedback"}
    if gated and gated == feedback:
        out.append("gate-verified and feedback are the same %d units" % len(gated))
    return out


def first_learned(rows: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for r in rows:
        t = mrc.epoch(r.get("ts"))
        s = str(r.get("slug") or "")
        if t is not None and s and (s not in out or t < out[s]):
            out[s] = t
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, paths
    from mnemo.core.briefing import _load_jsonl_events

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--now", default="", help="ISO date the window ends (default today)")
    ap.add_argument("--out", default="", help="where report.json goes (default <vault>/.mnemo/memory-panorama)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    state = vault / ".mnemo"
    out = Path(args.out).expanduser() if args.out else state / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.fromisoformat(args.now).replace(tzinfo=timezone.utc) if args.now else datetime.now(timezone.utc)
    since = (now - timedelta(days=args.days)).date().isoformat()
    projects = Path(args.projects)

    units_src = mrc._read(state / "prevented-repeats" / mpr.UNITS_NAME, None)
    if units_src is None:
        raise SystemExit("error: no %s; run tools/measure_prevented_repeats.py first"
                         % (state / "prevented-repeats" / mpr.UNITS_NAME))
    chunks = mrc._read(state / "prevented-repeats" / "chunks.json", {})
    bvdir = state / bv.OUT_DIR
    arms = mrc._read(bvdir / "arms.json", {})
    all_verdicts = mrc._read(bvdir / "verdicts.json", {})
    raters = list(bv.RATERS)
    verdicts = {r: all_verdicts.get(mrc.column(r, bv.JUDGE_SYSTEM), {}) for r in raters}
    bv_report = mrc._read(bvdir / "report.json", {})

    # the transcripts: the window, and #520's rated sessions
    window = mpr.collect_sessions(projects, vault, since)
    sessions_520 = units_src["sessions"]
    rated = set(units_src["rated"])
    scans: Dict[str, Dict[str, Any]] = {}
    for sid, meta in sorted(list(window.items()) + list(sessions_520.items())):
        if sid not in scans and Path(meta["path"]).exists():
            scans[sid] = scan_session(_load_jsonl_events(Path(meta["path"])))
    win_scans = {s: scans[s] for s in window if s in scans}
    rated_scans = {s: scans[s] for s in rated if s in scans}

    reflex_rows = _jsonl(_rotated(state / "reflex-log.jsonl"))
    access_rows = _jsonl(_rotated(state / "mcp-access-log.jsonl"))
    enrich_rows = _jsonl(_rotated(state / "enrichment-log.jsonl"))
    denial_path = state / "denial-log.jsonl"
    denial_rows = _jsonl(_rotated(denial_path)) if denial_path.exists() else None

    # part 1
    human_ids = set(window)
    data: Dict[str, Any] = {
        "days": args.days, "since": since, "sessions_window": len(win_scans),
        "entry_points": entry_points(win_scans), "envelope": envelope_facts(win_scans),
        "reflex": reflex_facts(win_scans), "reflex_log": reflex_log_facts(reflex_rows, since),
        "inject_at": ((cfg.get("reflex") or {}).get("judge") or {}).get("injectAt"),
        "mcp_chain": mcp_chain(win_scans),
        "access_log": {"every agent": access_log_chain(access_rows, since),
                       "human sessions of the window": access_log_chain(access_rows, since, human_ids)},
        "denial_log": None if denial_rows is None else len(denial_rows),
        "enrichment_log": sum(1 for r in enrich_rows if str(r.get("timestamp") or "") >= since),
    }
    pages = page_bodies(vault)
    live = set(pages)
    projects_of = {s: m["project"] for s, m in list(window.items()) + list(sessions_520.items())}
    unit_rows = units_src["columns"].get(mpr.BOTH) or []
    units = {(u["session_id"], u["slug"]): u for u in unit_rows}
    pairs = delivered_pairs(rated_scans, projects_of, live)
    shown = shown_pairs(chunks)
    data["sessions_520"] = len(rated)
    data["since_520"] = units_src.get("since")
    data["channels"] = channel_outcomes(pairs, units, shown, rated)
    data["spend"] = spend_by_outcome(pairs, units, shown, rated)

    # part 2: the units #527 measured
    from mnemo.core.reflex import replay

    try:
        facts = replay.rule_facts(vault)
    except Exception:
        facts = {}
    learned = first_learned(_jsonl([state / "learned.jsonl"]))
    audit = bv.audit_labels(vault)
    jidx = judge_index(reflex_rows)
    cut = mrc.epoch(JUDGE_LIVE)
    measured: List[Dict[str, Any]] = []
    for u in unit_rows:
        if not (u.get("new") and u.get("judged", True)):
            continue
        uid = bv.unit_id(u)
        a = arms.get(uid)
        if not a or not a.get("measurable"):
            continue
        h = bv.unit_h(verdicts, uid, raters)
        if h is None:
            continue
        slug, sid = u["slug"], u["session_id"]
        page = pages.get(slug) or {}
        text = "%s\n%s" % (page.get("name", slug), page.get("body", ""))
        f = facts.get(slug)
        born = [t for t in (learned.get(slug), getattr(f, "learned_at", None)) if t is not None]
        start = (sessions_520.get(sid) or {}).get("start") or 0
        measured.append({
            "id": uid, "slug": slug, "session_id": sid, "h": h, "model": a["model"], "ts": a.get("ts"),
            "era": "after" if start >= cut else "before", "type": page.get("type", "gone"),
            "carriers": [c for c in CHANNELS if a["carriers"].get(c)],
            "judge": unit_judge_score(jidx, sid, slug, a.get("project", ""), a.get("ts"))
            if a["carriers"].get("reflex") else None,
            "age_days": ((a.get("ts") or 0) - min(born)) / 86400 if born and a.get("ts") else None,
            "length": len(page.get("body", "")), "gate_verified": bool(getattr(f, "gate_verified", False)),
            "strict": bool(u.get("strict")), "audit": audit.get(bv.short(slug), ""),
            "class": classify(text, page.get("type", "")), "text": text, "prompt": a.get("prompt", ""),
        })
    cuts = terciles([m["length"] for m in measured])
    data["units_measured"] = len(measured)
    base = (bv_report.get("results") or {}).get(bv.BOTH) or {}
    data["base"] = {"h": (sum(m["h"] for m in measured) / len(measured)) if measured else None,
                    "rate": base.get("rate"), "h_ci": (base.get("ci") or {}).get("h")}
    data["shares"] = bv_report.get("shares") or {}
    data["agreement"] = bv_report.get("agreement") or {}
    data["slices"] = {
        "page type": slice_h(measured, lambda m: [m["type"]]),
        "channel": slice_h(measured, lambda m: m["carriers"]),
        "page type x channel": slice_h(measured, lambda m: ["%s x %s" % (m["type"], c) for c in m["carriers"]]),
        "judge score (reflex units)": slice_h([m for m in measured if "reflex" in m["carriers"]],
                                              lambda m: [score_bucket(m["judge"])]),
        "rule age at the prompt": slice_h(measured, lambda m: [age_bucket(m["age_days"])]),
        "body length": slice_h(measured, lambda m: [length_bucket(m["length"], cuts)]),
        "gate-verified": slice_h(measured, lambda m: ["gate-verified" if m["gate_verified"] else "not verified"]),
        "evidence a real correction (#519/#524 raters, both)": slice_h(
            measured, lambda m: ["real correction" if m["strict"] else "not (or no quote)"]),
        "2026-09-22 audit label": slice_h(measured, lambda m: [
            {"junk": "generic or narrative", "other": "labelled, not junk"}.get(m["audit"], "no label")]),
        "text class": slice_h(measured, lambda m: [m["class"]]),
        "era_model": era_model(measured),
    }
    data["unit_groups"] = unit_groups(measured, cuts)
    data["same_units"] = same_units(measured)

    # part 3
    rng = random.Random(SEED)
    classes: Dict[str, Dict[str, Any]] = {}
    for name, _, _ in CLASS_RULES:
        group = [m for m in measured if m["class"] == name]
        wins = [m for m in group if m["h"] > 0]
        losses = [m for m in group if m["h"] < 0]

        def ex(m: Dict[str, Any]) -> Dict[str, Any]:
            page = pages.get(m["slug"]) or {}
            body = " ".join((page.get("body") or "").split())
            return {"h": m["h"], "rule": page.get("name", m["slug"]), "slug": m["slug"],
                    "head": mpr._head(body, 160), "prompt": mpr._head(" ".join(m["prompt"].split()), 120)}
        classes[name] = {"units": len(group), "ties": len(group) - len(wins) - len(losses),
                         "wins_all": [m["id"] for m in wins], "losses_all": [m["id"] for m in losses],
                         "wins": [ex(m) for m in examples(wins, rng)],
                         "losses": [ex(m) for m in examples(losses, rng)]}
    data["classes"] = classes

    # part 4
    total_bytes = 0
    for sc in rated_scans.values():
        total_bytes += sum(e["bytes"] for e in sc["ss"]) + sum(_bytes(r["text"]) for r in sc["reflex"])
        total_bytes += sum(_bytes(c["text"]) for c in sc["mcp"]) + sum(_bytes(e["text"]) for e in sc["enrich"])
    emitted: Dict[str, int] = {}
    for r in reflex_rows:
        for s in r.get("emitted") or []:
            emitted[s] = emitted.get(s, 0) + 1
    data["redundancy"] = redundancy(pairs, unit_rows, rated, total_bytes, emitted)

    # part 5
    data["hypotheses"] = hypotheses(data)
    data["class_rules"] = [[n, desc] for n, desc, _ in CLASS_RULES]
    data["lead"] = LEAD

    aliases = load_aliases(state / "private-names.tsv")
    home = os.path.expanduser("~")
    serial = json.loads(redact(json.dumps(data, ensure_ascii=False, default=str), aliases, home))
    mrc._write(out / "report.json", serial)
    if args.json:
        print(json.dumps(serial, indent=1, ensure_ascii=False))
        return 0
    for line in report_lines(data):
        print(redact(line, aliases, home))
    print("")
    print("saved %s" % redact(str(out / "report.json"), aliases, home))
    return 0


if __name__ == "__main__":
    sys.exit(main())
