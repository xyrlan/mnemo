"""Fill the SessionStart block by relevance history, and measure a rule's lift at session start (#613, #618)

Usage:
    PYTHONPATH=src python3 tools/measure_relevance_block.py [--units FILE] [--k 5 ...]
        [--vault DIR] [--json]                          # the report, local; calls nothing
    PYTHONPATH=src python3 tools/measure_relevance_block.py --dry-run
                                                        # the lift arms' pending calls and notional cost
    PYTHONPATH=src python3 tools/measure_relevance_block.py --send [--samples 2] [--workers 4]
                                                        # answer both lift arms, judge, then report

#598 (``tools/measure_settled_block.py``) filled a SessionStart block with the
rules that had a verified correction, and found the limit is the ranking
source: only 19 rules ever had one, so the block held a median 2. This ranks
by **relevance history** instead, and asks whether a rule shown at session
start, many turns before the prompt it bears on, still moves the answer.

**1. Ranking by relevance history.** For each of #520's sessions (``--units``,
column ``both``), the block as it would have been at its start: rules that
existed then, for its project or universal, not retired (#598's
``block_at``), ranked by the number of **earlier distinct sessions** in which
the rule was relevant, ties by slug, first K (:data:`KS`). Only what happened
before the session's start counts, and never the session itself. Two sources:

- **(a) #520's rater labels**: a session counts for a rule when both raters
  named the rule relevant to one of its prompts (a unit of ``units.json``,
  built from ``frequency.json``), dated at that session's start. Only the
  sessions #520 rated count. The upper bound of what a relevance-history
  ranking could do;
- **(b) the live reflex judge's picks**: a session counts for a rule when the
  judge scored it at or above ``injectAt`` (:data:`INJECT_AT`) in that
  session, dated at the pick. The signal a product could run on. Read from
  every ``reflex-log.jsonl{,.1}`` row and the ``full-body-fresh`` archive
  (``reflex-rows.jsonl``), and, because those start on 2026-09-28, after
  every #520 session, from the reflex blocks Claude Code recorded in every
  transcript on disk since the judge went live (:data:`JUDGE_LIVE`): the
  rules a reflex block carried are the judge's picks at the ``injectAt`` in
  force. Once mnemo's own judge-picks ledger (#619,
  ``mnemo.core.reflex.picks``) holds every pick since the judge went live —
  after ``tools/backfill_judge_picks.py`` — (b) reads it through
  ``picks_before`` instead, the count a SessionStart block would read, and
  rebuilds nothing.

So (b) has history only for the sessions that started after the judge went
live, over a window of days, while (a) has the whole of #520's window. The
report prints each source's window, and (a) cut to (b)'s window, which
separates "the judge's signal is worse" from "the judge has not run long
enough".

Every number is printed over two sets of sessions: the **covered** ones,
those that started after (b)'s first pick, where (b)'s ranking existed, and
**all** of #520's. Outside the covered sessions (b) can only score zero, which
measures how long the log has run, not the ranking, so the bar is read on the
covered sessions, every source over the same ones. Then, as #598 reads it: coverage of the units and of the non-redundant ones,
#520's estimate with the block as the delivery, the same estimate without the
rule that carried the most units, and the bytes the block adds, with how
many blocks would overflow mnemo's SessionStart envelope
(``hook_envelope.ENVELOPE_MAX_BYTES``). Rules
#520's delivery judge found the session's loaded ``CLAUDE.md`` /
``MEMORY.md`` already says are kept out of the block.

**2. Lift at a distance.** #520's and #598's estimates use #434's lift, the
rule injected beside the prompt. Here #434's 64 pairs (``rule-lift/pairs.json``)
are re-asked with the **session's real preceding turns**: every typed prompt
and every assistant text before the prompt, the last :data:`HISTORY_CHARS`
characters of them whole turns at a time, in two arms from one scratch
directory, with #434's model, system prompt and blind judge:

- A: the preceding turns and the prompt;
- B: the same, behind a ``SessionStart`` ``<system-reminder>`` holding the
  rule as the block renders it (:data:`HEADER`, ``• [[slug]]:`` over the
  rule's text), ahead of the first turn.

Lift = mean(B − A) over pairs the judge did not call ``na`` in both arms,
with #434's paired bootstrap. The block holds one rule here, not K, and the
turns carry no tool calls or results: both make the distance shorter and the
block cleaner than a real one, so this lift is an upper bound on what a
SessionStart rule does. (1)'s estimates are printed with both lifts.

**The bar (declared in #613 before measuring).** Build the block only if, with
the distance lift, the best K from source (b), over the covered sessions, gives a broad estimate
≥ 1/15 per session with a CI lower bound over 0, **and** it stays positive
without its top rule. If only source (a) passes, the report says how far
(b) is from (a). Otherwise, do not build.

First run, 2026-10-07, the maintainer's vault, on #565's ``--native loaded``
rerun of #520's cache (``--units .mnemo/null-audit/no-notes/units.json``,
245 sessions, 1,513 broad units). **Distance lift +28.3 pp [+15.8, +40.8]**
over 60 pairs (median 12 turns and 14,059 characters between the block and the
prompt; follow 55.0% → 83.3%), against #434's +31.1 pp beside the prompt, so
distance costs little. (b) has history only for the 86 sessions after the
judge went live, with at most a week of picks behind them. Over those 86, with
the distance lift: **(b) K=15 0.181 [0.098, 0.285] per session, 0.073
[0.035, 0.122] without ``run-git-commands-yourself``; K=25 0.231 [0.122,
0.366], 0.119 [0.058, 0.200] without it: the bar says build.** K=5 and K=10
fail without that rule (0.030, 0.059). (a) over the same sessions: 0.382 at
K=25, 0.310 when cut to (b)'s window, so a week of history, not the judge's
signal, is most of the gap. Over all 245 sessions (b) reaches 0.081 at K=25,
0.042 without the top rule. **The cost is the catch:** at K=15 every covered
block is over mnemo's 9,000-byte SessionStart envelope, median 44,792 bytes
(K=25: 62,950), and the lift was measured with one rule in the block, not
15–25. Strict stays inconclusive (0.053 at best), as #598 found. Arms: 256
calls, $8.67 API-price equivalent, plus 26 judge calls.

**3. A block that fits, and its lift with the other rules beside it (#618).**
(b)'s blocks are also rendered ``compact`` (``mnemo export``'s
``render_entry(rule, full=False)``: heading, lead paragraph, quote) and
``line`` (bullet and first sentence). A K fits a budget when its p95 bytes
over the covered sessions do. There are two budgets:

- (a): the room today's envelope leaves at its p95, read from mcp-access-log's
  ``session_start.inject`` rows since :data:`ROOM_SINCE`;
- (b): a separate SessionStart hook entry, ``ENVELOPE_MAX_BYTES``.

Claude Code holds each hook entry to its 10,000-character threshold on its
own; it does not sum them. The report counts SessionStart firings whose
contexts sum past it while each stays under it, and how many were persisted
(:func:`hook_split`). A throwaway probe on 2.1.293 agreed: three hooks of
5,600, 9,000 and 11,000 characters left the first two inline and persisted
only the third.

The dilution arm re-asks (2)'s pairs, arm B holding the target at a seeded
position among the K − 1 rules (b)'s whole log ranks highest for the pair's
project (:func:`mates`), among rules that existed at the prompt. #434's
prompts predate (b)'s log, so no as-of ranking exists for them. Arm A is (2)'s
prompt unchanged, so its answers and verdicts are copied (:func:`seed_arm_a`),
and every mate's slug is masked for the judge. Each budget gets the form that
fits it: line for (a), compact for (b). The bar, declared in #618: build if
some K that fits a budget, in that budget's form and with that form's
diluted lift, passes #613's bar over the covered sessions; otherwise name the
constraint (bytes, dilution, coverage).

First run, 2026-10-08, same cache. Today's envelope is median 4,788 and p95
5,891 bytes over 549 rows, which leaves budget (a) 3,107 bytes. Compact fits
only (b), at K ≤ 15 (K=15 p95 7,732). Line fits (a) at K ≤ 15 (p95 2,999).
Transcripts: 194 firings summed past 10,000 with each context under it, 0
persisted. **Diluted lift at K=15: compact +14.9 pp [+2.6, +27.2], line +12.3
pp [+0.0, +24.6]**, over 57 pairs, against +28.3 alone and #434's +31.1: the
block's other 14 rules halve it. With it, (b) over the 86 covered sessions:
compact K=15 0.095 [0.020, 0.184], 0.038 without ``run-git-commands-yourself``;
line K=15 0.079 [0.000, 0.166], 0.031 without it. **Do not build: dilution.**
Arms: 256 B calls, $10.34 API-price equivalent, plus 26 judge calls.

Only ``--send`` calls a model, through ``core.llm``'s provider, which carries
mnemo's hook guard. Answers and verdicts are saved after every call under
``<vault>/.mnemo/relevance-block/``, so a rerun resumes. The dollar figure is
the API-price equivalent of subscription usage, not money spent.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sb = _sibling("measure_settled_block")
rl = _sibling("measure_rule_lift")
mpr = sb.mpr
mrc = sb.mrc
_provenance = sb._provenance

THRESHOLD = sb.THRESHOLD
STRICT, BROAD = sb.STRICT, sb.BROAD
KS = (5, 10, 15, 25)
HEADER = sb.HEADER
JUDGE_LIVE = mpr.JUDGE_LIVE
#: The reflex judge's default ``injectAt``, the value in force on the
#: maintainer's vault since 2026-09-23.
INJECT_AT = 0.4
SOURCES = ("a", "b")
OUT_DIR = "relevance-block"
PAIRS_NAME = "lift-pairs.json"
ANSWERS_NAME = "lift-answers.json"
VERDICTS_NAME = "lift-verdicts.json"
#: How much of the session before the prompt both arms see: its last
#: characters, whole turns at a time.
HISTORY_CHARS = 24000
WORKERS = 4
#: #618's renderings of a rule in the block, and the dilution arm's cache.
FORMS = ("full", "compact", "line")
#: The form each budget's dilution arm renders the block in: ``line``, the
#: stricter form, is the only one that fits inside today's envelope (a);
#: ``compact`` is what #618 asks for, in a hook entry of its own (b). The bar
#: tries (a) first: it needs no new hook entry.
BUDGET_FORMS = (("a", "line"), ("b", "compact"))
DILUTION_DIR = "dilution-%s-k%d"
#: Claude Code persists a hook context of this many characters or more (#533),
#: and marks it so.
PERSIST_CHARS = 10000
PERSISTED = "<persisted-output>"
#: The SessionStart envelope as it is today: the ten-TL;DR briefing index went
#: live on the main checkout then (#551).
ROOM_SINCE = "2026-09-29T11:04:00Z"

#: ``slug -> [(session_id, ts)]``: the moments a rule was relevant.
Events = Dict[str, List[Tuple[str, Optional[float]]]]


# --- 1. the two sources ---------------------------------------------------------------

def rater_events(units: Sequence[Dict[str, Any]], sessions: Dict[str, Dict[str, Any]],
                 since: Optional[float] = None) -> Events:
    """Source (a): each unit is its rule relevant in its session, dated at the
    session's start. With ``since``, only sessions that started at or after it."""
    out: Dict[str, Set[Tuple[str, float]]] = {}
    for u in units:
        meta = sessions.get(u["session_id"])
        if not meta:
            continue
        start = float(meta["start"])
        if since is not None and start < since:
            continue
        out.setdefault(u["slug"], set()).add((u["session_id"], start))
    return {s: sorted(v) for s, v in sorted(out.items())}


def log_picks(rows: Iterable[Any], inject_at: float = INJECT_AT) -> List[Tuple[str, str, float]]:
    """``(slug, session, ts)`` for every rule a reflex-log row's judge scored at
    or above ``inject_at``. Rows without a working judge pick nothing."""
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        judge = row.get("judge") or {}
        ts = mrc.epoch(row.get("ts"))
        sid = row.get("session_id")
        if judge.get("status") != "ok" or ts is None or not sid:
            continue
        for pair in judge.get("scores") or []:
            if (isinstance(pair, (list, tuple)) and len(pair) == 2 and isinstance(pair[0], str)
                    and isinstance(pair[1], (int, float)) and pair[1] >= inject_at):
                out.append((pair[0], sid, ts))
    return out


def transcript_picks(lines: Iterable[str], session_id: str, since: float) -> List[Tuple[str, str, float]]:
    """``(slug, session, ts)`` for every rule a reflex block recorded in a
    transcript at or after ``since`` carried."""
    out = []
    for raw in lines:
        if mpr._REFLEX not in raw:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(ev, dict) or ev.get("isSidechain"):
            continue
        att = ev.get("attachment")
        ts = mrc.epoch(ev.get("timestamp"))
        if not isinstance(att, dict) or att.get("type") != "hook_additional_context" or ts is None or ts < since:
            continue
        for text in mpr._hook_texts(att):
            if mpr._REFLEX in text:
                out.extend((slug, session_id, ts) for slug in mpr.reflex_slugs(text))
    return out


def pick_events(picks: Iterable[Tuple[str, str, float]]) -> Events:
    """``slug -> [(session, first pick ts)]``: one entry per session a rule was picked in."""
    first: Dict[Tuple[str, str], float] = {}
    for slug, sid, ts in picks:
        key = (slug, sid)
        if key not in first or ts < first[key]:
            first[key] = ts
    out: Dict[str, List[Tuple[str, Optional[float]]]] = {}
    for (slug, sid), ts in sorted(first.items()):
        out.setdefault(slug, []).append((sid, ts))
    return out


def ledger_covers(ledger: Any) -> bool:
    """Whether mnemo's judge-picks ledger (#619) holds every pick since the
    judge went live, so (b) reads it instead of rebuilding the history."""
    since = ledger.since()
    return since is not None and since <= mrc.epoch(JUDGE_LIVE)


def ledger_block_at(session_id: str, project: str, start: float, ledger: Any,
                    docs: Dict[str, Dict[str, Any]], dates: Dict[str, Dict[str, Any]], k: int,
                    exclude: Iterable[str] = ()) -> List[str]:
    """#598's ``block_at``, ranked by ``picks_before`` as of ``start``: the
    count a SessionStart block would read from the ledger."""
    skip = set(exclude)
    scored = []
    for slug, n in ledger.picks_before(None, start, session_id).items():
        if slug in skip or not sb.eligible(docs.get(slug), project):
            continue
        facts = dates.get(slug)
        learned = mrc.first_learned(facts, session_id) if facts is not None else None
        if learned is None or learned >= start:
            continue
        scored.append((-n, slug))
    return [slug for _, slug in sorted(scored)[:k]]


def window(events: Any) -> Optional[Tuple[float, float]]:
    """The first and last moment a source saw anything."""
    if not isinstance(events, dict):
        return events.window()
    ts = [t for evs in events.values() for _, t in evs if t is not None]
    return (min(ts), max(ts)) if ts else None


def with_history(sessions: Sequence[str], meta: Dict[str, Dict[str, Any]], events: Any) -> int:
    """Sessions that started after the source's first event: the ones it could fill a block for."""
    w = window(events)
    return 0 if w is None else sum(float(meta[s]["start"]) > w[0] for s in sessions)


# --- 1. the block per source, and its numbers --------------------------------------------

def blocks_for(sessions: Sequence[str], meta: Dict[str, Dict[str, Any]], events: Any,
               docs: Dict[str, Dict[str, Any]], dates: Dict[str, Dict[str, Any]], k: int,
               redundant: Dict[str, Set[str]]) -> Dict[str, List[str]]:
    """Each session's block as of its start (#598's ``block_at``, fed relevance
    history), or :func:`ledger_block_at` when ``events`` is the ledger."""
    at = sb.block_at if isinstance(events, dict) else ledger_block_at
    return {sid: at(sid, meta[sid]["project"], float(meta[sid]["start"]), events, docs, dates, k,
                    redundant.get(sid, ()))
            for sid in sessions}


def block_numbers(units: Sequence[Dict[str, Any]], sessions: Sequence[str], per: Dict[str, Sequence[str]],
                  lifts: Dict[str, Sequence[float]], bodies: Dict[str, str],
                  envelope: Optional[int] = None, rules: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Over ``sessions`` only: size, bytes (and how many blocks overflow
    ``envelope``), with ``rules`` the bytes in every form of :data:`FORMS`,
    coverage, and per lift the estimate with and without the rule that
    carried the most units."""
    per = {s: list(per.get(s) or ()) for s in sessions}
    sizes = [len(per[s]) for s in sessions]
    nbytes = [sb.block_bytes(per[s], bodies) for s in sessions]
    out: Dict[str, Any] = {
        "size": {"median": sb.quantile(sizes, 0.5), "max": max(sizes) if sizes else None},
        "bytes": {"median": sb.quantile(nbytes, 0.5), "p95": sb.quantile(nbytes, 0.95),
                  "max": max(nbytes) if nbytes else None,
                  "over_envelope": None if envelope is None else sum(n > envelope for n in nbytes)},
        "coverage": {}, "coverage_fresh": {}, "estimate": {}}
    if rules is not None:
        out["bytes_by_form"] = {}
        for form in FORMS:
            nb = [form_bytes(per[s], form, rules, bodies) for s in sessions]
            out["bytes_by_form"][form] = {"median": sb.quantile(nb, 0.5), "p95": sb.quantile(nb, 0.95),
                                          "max": max(nb) if nb else None}
    for r in (STRICT, BROAD):
        rows = sb.session_rows(units, sessions, per, r)
        out["coverage"][r] = sb.ratio_ci(rows, "delivered", "units")
        out["coverage_fresh"][r] = sb.ratio_ci(rows, "covered_fresh", "fresh")
        top = sb.top_carrier(units, per, r)
        alone = sb.session_rows(units, sessions, sb.without(per, top), r) if top else None
        out["estimate"][r] = {}
        for name, diffs in lifts.items():
            e = sb.estimate(rows, diffs)
            if alone is not None:
                e["without_top"] = dict(sb.estimate(alone, diffs), rule=top,
                                        carried=out["coverage_fresh"][r]["k"] - sum(x["covered_fresh"] for x in alone))
            out["estimate"][r][name] = e
    return out


def positive(stats: Optional[Dict[str, Any]], threshold: float = THRESHOLD) -> bool:
    if not stats or stats.get("E") is None:
        return False
    lo = (stats.get("ci", {}).get("E") or [None])[0]
    return stats["E"] >= threshold and lo is not None and lo > 0


def passes(stats: Optional[Dict[str, Any]], threshold: float = THRESHOLD) -> bool:
    """#613's bar on one broad estimate: positive, and positive without its top rule."""
    return positive(stats, threshold) and positive((stats or {}).get("without_top"), threshold)


def best_k(by_k: Dict[str, Dict[str, Any]], lift: str) -> Optional[str]:
    """The K whose broad estimate under ``lift`` is highest, ties by the smaller K."""
    scored = [(-(v["estimate"][BROAD][lift].get("E") or 0.0), int(k), k) for k, v in by_k.items()
              if lift in v["estimate"][BROAD]]
    return min(scored)[2] if scored else None


def decide(results: Dict[str, Dict[str, Dict[str, Any]]], lift: str = "distance") -> str:
    """#613's bar, declared before measuring; ``results[source][K]``."""
    picks = {s: best_k(results.get(s) or {}, lift) for s in SOURCES}
    if picks["b"] is None and picks["a"] is None:
        return "pending: no distance lift yet"
    ok = {s: picks[s] is not None and passes(results[s][picks[s]]["estimate"][BROAD][lift]) for s in SOURCES}
    if ok["b"]:
        return "build"
    if ok["a"]:
        return "only source (a) passes: do not build on the judge's picks yet"
    return "do not build"


# --- 2. lift at a distance ----------------------------------------------------------------

def session_turns(lines: Sequence[str], session_id: str, uid: str,
                  uid_of: Callable[[str, float, str], str]) -> Optional[Tuple[List[Tuple[str, str]], str]]:
    """(every turn before the prompt whose id is ``uid``, as ``(role, text)``; the
    prompt). Typed prompts as ``measure_rule_lift.prompt_in_context`` reads
    them, so the ids line up; one assistant turn per run of text blocks
    between two prompts. None when the prompt is not in the transcript."""
    from mnemo.core.reflex.replay import _parse_ts
    from mnemo.core.transcript import SYNTHETIC_TURN, plain_user_text

    turns: List[Tuple[str, str]] = []
    reply: List[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if kind == "assistant":
            text = rl._assistant_text(entry.get("message"))
            if text:
                reply.append(text)
            continue
        if kind != "user" or entry.get("isMeta"):
            continue
        msg = entry.get("message")
        if not isinstance(msg, dict):
            continue
        text = plain_user_text(msg.get("content"))
        if not text or SYNTHETIC_TURN.search(text):
            continue
        ts = _parse_ts(entry.get("timestamp"))
        if ts is None:
            continue
        if reply:
            turns.append(("assistant", "\n\n".join(reply)))
            reply = []
        if uid_of(session_id, ts, text) == uid:
            return turns, text
        turns.append(("user", text))
    return None


def history(turns: Sequence[Tuple[str, str]], limit: int = HISTORY_CHARS) -> Tuple[List[Tuple[str, str]], int]:
    """The last turns that fit in ``limit`` characters, whole (the oldest one
    kept may be cut at its start), and how many earlier turns were left out."""
    kept: List[Tuple[str, str]] = []
    room = limit
    for role, text in reversed(turns):
        if room <= 0:
            break
        if len(text) > room:
            text = "…" + text[-room:]
        kept.append((role, text))
        room -= len(text)
    kept.reverse()
    return kept, len(turns) - len(kept)


def block_text(slug: str, rule: str) -> str:
    from mnemo.core.reflex import render

    return "%s\n%s" % (HEADER, render.full_line(render.bullet(slug), rule))


def distance_pair(pair: Dict[str, Any], found: Optional[Tuple[List[Tuple[str, str]], str]]) -> Dict[str, Any]:
    """#434's pair with the session before the prompt; a prompt whose
    transcript is gone keeps #434's single previous turn, flagged."""
    if found is None:
        turns = [("assistant", pair["previous"])] if pair.get("previous") else []
        prompt = pair["prompt"]
    else:
        turns, prompt = found
    kept, dropped = history(turns)
    return {"id": pair["id"], "uid": pair["uid"], "slug": pair["slug"], "project": pair.get("project"),
            "rule": pair["rule"], "prompt": prompt, "turns": [list(t) for t in kept],
            "turns_before": len(turns), "turns_dropped": dropped,
            "chars_before": sum(len(t) for _, t in turns), "context": found is not None,
            "block": block_text(pair["slug"], pair["rule"])}


def arm_prompt(pair: Dict[str, Any], arm: str) -> str:
    """What one arm sends. A and B differ by the SessionStart block and nothing else."""
    parts = []
    if arm == "B":
        parts.append("<system-reminder>\nSessionStart hook additional context: %s\n</system-reminder>"
                     % pair["block"])
    if pair["turns"]:
        if pair["turns_dropped"]:
            parts.append("[%d earlier turns of this session not shown]" % pair["turns_dropped"])
        parts.append("The session so far:\n" + "\n\n".join(
            "<%s>\n%s\n</%s>" % (role, text, role) for role, text in pair["turns"]))
    parts.append("The developer's next message:\n<user>\n%s\n</user>" % pair["prompt"])
    return "\n\n".join(parts)


def mask(answer: str, slug: str) -> str:
    """#434's mask, plus the block's own header."""
    masked, _ = rl.mask(answer, slug)
    return masked.replace(HEADER.rstrip(":"), rl.MASK)


def judge_items(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
                done: Dict[str, str]) -> List[Dict[str, str]]:
    items = rl.judge_items(pairs, answers, done)
    for it in items:
        it["answer"] = it["answer"].replace(HEADER.rstrip(":"), rl.MASK)
    return items


def lift_diffs(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
               verdicts: Dict[str, str]) -> Tuple[List[float], Dict[str, Any]]:
    rows = rl.pair_rows(pairs, answers, verdicts)
    return [r["B"] - r["A"] for r in rows if not r["both_na"]], rl.lift(rows)


def notional(pairs: Sequence[Dict[str, Any]], pending: int, samples: int) -> Dict[str, Any]:
    """Calls and API-price equivalent of the pending arm calls and their judging."""
    def tok(s: str) -> int:
        return len(s) // 4 + 1

    per_call = (sum(tok(rl.ARM_SYSTEM) + rl.CLI_OVERHEAD_TOKENS + tok(arm_prompt(p, a))
                    for p in pairs for a in rl.ARMS) / max(1, 2 * len(pairs)))
    arm_in = int(per_call * pending)
    arm_out = pending * rl.ANSWER_TOKENS
    judge_calls = -(-pending // rl.JUDGE_CHUNK)
    judge_in = judge_calls * (tok(rl.JUDGE_SYSTEM) + rl.CLI_OVERHEAD_TOKENS) + pending * (
        rl.RULE_CHARS // 4 + rl.ANSWER_TOKENS)
    usd = ((arm_in + judge_in) * rl.USD_IN_PER_MTOK
           + (arm_out + judge_calls * rl.JUDGE_OUT_TOKENS) * rl.USD_OUT_PER_MTOK) / 1e6
    return {"arm_calls": pending, "judge_calls": judge_calls, "input_tokens": arm_in + judge_in,
            "usd": usd, "samples": samples}


# --- #618: a block that fits, and the lift with K − 1 other rules beside it ---------------

def export_rules(vault: Path, docs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """``slug -> ExportRule`` for every live rule page, as ``mnemo export``
    reads it (lead paragraph, the evidence quote), universal as the reflex
    index says."""
    from mnemo.core.export.select import ExportRule, _is_imported
    from mnemo.core.filters import derive_rule_slug, is_consumer_visible, iter_shared_pages
    from mnemo.core.reclassify_types import split_frontmatter
    from mnemo.core.text_utils import retrieval_body

    out: Dict[str, Any] = {}
    for md in iter_shared_pages(vault, include_inbox=False):
        try:
            fm, body = split_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not fm or not is_consumer_visible(md, fm, vault):
            continue
        slug = derive_rule_slug(fm, md.stem)
        evidence = fm.get("evidence")
        quote = evidence.get("quote") if isinstance(evidence, dict) else None
        out[slug] = ExportRule(slug=slug, name=str(fm.get("name") or slug),
                               body=retrieval_body(body).strip() + "\n",
                               quote=str(quote).strip() if quote else None,
                               universal=bool((docs.get(slug) or {}).get("universal")),
                               source_count=0, page_type=md.parent.name, imported=_is_imported(fm))
    return out


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`*\[(\"'])")


def first_sentence(body: str) -> str:
    """The lead paragraph's first sentence, on one line."""
    from mnemo.core.export.render import first_paragraph

    lead = " ".join(first_paragraph(body).split())
    return _SENTENCE_END.split(lead, maxsplit=1)[0]


def entry_text(slug: str, form: str, rules: Dict[str, Any], bodies: Dict[str, str]) -> str:
    """One rule in the block: ``full`` as #616 rendered it (the reflex's whole
    body), ``compact`` as ``mnemo export`` writes it (``render_entry(rule,
    full=False)``: heading, lead paragraph, quote), ``line`` its bullet and
    first sentence."""
    from mnemo.core.export.render import render_entry
    from mnemo.core.reflex import render

    if form == "full":
        return render.full_line(render.bullet(slug), bodies.get(slug, ""))
    rule = rules.get(slug)
    if rule is None:
        return render.bullet(slug)
    if form == "compact":
        return render_entry(rule, full=False).rstrip("\n")
    return "%s: %s" % (render.bullet(slug), first_sentence(rule.body))


def form_block(slugs: Sequence[str], form: str, rules: Dict[str, Any], bodies: Dict[str, str]) -> str:
    """The block's text under its header; empty for no rules."""
    if not slugs:
        return ""
    sep = "\n\n" if form == "compact" else "\n"
    return HEADER + "\n" + sep.join(entry_text(s, form, rules, bodies) for s in slugs)


def form_bytes(slugs: Sequence[str], form: str, rules: Dict[str, Any], bodies: Dict[str, str]) -> int:
    from mnemo.core.hook_envelope import utf8_len

    return utf8_len(form_block(slugs, form, rules, bodies))


def envelope_room(rows: Iterable[Any], since: float, cap: int) -> Dict[str, Any]:
    """Today's SessionStart envelope, from the ``session_start.inject`` rows of
    mcp-access-log at or after ``since``, and the room it leaves under ``cap``:
    at its median, and at its p95 (the room a block has in 95% of sessions)."""
    sizes = []
    for r in rows:
        if not isinstance(r, dict) or r.get("tool") != "session_start.inject":
            continue
        ts = mrc.epoch(r.get("timestamp"))
        if ts is None or ts < since or not isinstance(r.get("envelope_bytes"), int):
            continue
        sizes.append(r["envelope_bytes"])
    med, p95 = sb.quantile(sizes, 0.5), sb.quantile(sizes, 0.95)
    return {"n": len(sizes), "median": med, "p95": p95, "max": max(sizes) if sizes else None,
            "room_median": None if med is None else cap - med, "room_p95": None if p95 is None else cap - p95}


def fits(stats: Dict[str, Any], budget: Optional[int]) -> bool:
    """A block fits a budget when its p95 bytes do (and it is ever non-empty)."""
    p95 = stats.get("p95")
    return budget is not None and p95 is not None and 0 < p95 <= budget


def hook_split(lines: Iterable[str], limit: int = PERSIST_CHARS) -> Dict[str, int]:
    """Over one transcript's SessionStart firings with two or more hook
    contexts: how many summed to ``limit`` or more with each one under it,
    and how many of those had any context persisted. If Claude Code summed
    the contexts against its threshold, those would be persisted; if each
    hook is held to it alone, none would be."""
    groups: Dict[Any, List[str]] = {}
    for raw in lines:
        if '"hook_additional_context"' not in raw:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            continue
        att = ev.get("attachment") if isinstance(ev, dict) else None
        if (not isinstance(att, dict) or att.get("type") != "hook_additional_context"
                or att.get("hookEvent") != "SessionStart"):
            continue
        content = att.get("content")
        texts = content if isinstance(content, list) else [content]
        groups.setdefault(ev.get("parentUuid") or id(ev), []).extend(str(t) for t in texts if t)
    out = {"firings": 0, "summed_over_each_under": 0, "persisted": 0}
    for texts in groups.values():
        if len(texts) < 2:
            continue
        out["firings"] += 1
        sizes = [original_chars(t) for t in texts]
        if sum(sizes) >= limit and max(sizes) < limit:
            out["summed_over_each_under"] += 1
            out["persisted"] += any(PERSISTED in t for t in texts)
    return out


_TOO_LARGE = re.compile(r"Output too large \(([0-9.]+)(KB|MB|B)\)")


def original_chars(text: str) -> int:
    """A context's size before Claude Code persisted it: its own length, or,
    for a persisted one, the size its preview names (``10.4KB`` = 10,650)."""
    if PERSISTED not in text:
        return len(text)
    m = _TOO_LARGE.search(text)
    if not m:
        return len(text)
    return int(float(m.group(1)) * {"B": 1, "KB": 1024, "MB": 1024 * 1024}[m.group(2)])


def mates(slug: str, project: str, as_of: float, events: Any, docs: Dict[str, Dict[str, Any]],
          dates: Dict[str, Dict[str, Any]], n: int, session_id: str = "") -> List[str]:
    """The ``n`` rules other than ``slug`` that source (b)'s whole log ranks
    highest for ``project``, among rules that existed at ``as_of``: the block a
    rule would sit in. #434's prompts predate (b)'s log, so no as-of ranking
    exists for them; their own session never counts. ``events`` is the
    rebuilt history or mnemo's judge-picks ledger (#619)."""
    if isinstance(events, dict):
        counts = {s: len({sid for sid, _ in evs if sid and sid != session_id}) for s, evs in events.items()}
    else:
        counts = events.picks_before(None, float("inf"), session_id or None)
    scored = []
    for s, k in counts.items():
        if s == slug or not sb.eligible(docs.get(s), project):
            continue
        facts = dates.get(s)
        learned = mrc.first_learned(facts, session_id) if facts is not None else None
        if learned is None or learned >= as_of:
            continue
        if k:
            scored.append((-k, s))
    return [s for _, s in sorted(scored)[:n]]


def diluted_pair(pair: Dict[str, Any], others: Sequence[str], k: int, rules: Dict[str, Any],
                 bodies: Dict[str, str], form: str = "compact") -> Dict[str, Any]:
    """#616's pair with its block holding the target among ``others``, at a
    position seeded by the pair's id; everything arm A sees is unchanged."""
    import random

    others = [s for s in others if s != pair["slug"]][:k - 1]
    pos = random.Random("%s:%d" % (pair["id"], k)).randrange(len(others) + 1)
    slugs = others[:pos] + [pair["slug"]] + others[pos:]
    local = dict(bodies, **{pair["slug"]: pair["rule"]})
    return dict(pair, block=form_block(slugs, form, rules, local), mates=list(others), position=pos, k=len(slugs))


def seed_arm_a(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
               verdicts: Dict[str, str], old_answers: Dict[str, Dict[str, List[Any]]],
               old_verdicts: Dict[str, str]) -> int:
    """Copy #616's arm A answers and their verdicts: arm A's prompt does not
    hold the block, so it is the same prompt. Returns the answers copied."""
    n = 0
    for p in pairs:
        old = (old_answers.get(p["id"]) or {}).get("A") or []
        mine = answers.setdefault(p["id"], {})
        if old and not mine.get("A"):
            mine["A"] = [dict(a) for a in old]
            n += len(old)
            for i in range(len(old)):
                aid = rl.answer_id(p["id"], "A", i)
                if aid in old_verdicts:
                    verdicts.setdefault(aid, old_verdicts[aid])
    return n


def diluted_judge_items(pairs: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, List[Any]]],
                        done: Dict[str, str]) -> List[Dict[str, str]]:
    """``judge_items``, with every block mate's slug masked too, so no answer
    names a rule only arm B saw."""
    by_id = {p["id"]: p for p in pairs}
    items = judge_items(pairs, answers, done)
    for it in items:
        for s in by_id[it["id"].split("|")[0]].get("mates") or ():
            it["answer"] = re.sub(re.escape(s), rl.MASK, it["answer"], flags=re.IGNORECASE)
    return items


def fitting(by_k: Dict[str, Dict[str, Any]], form: str, budget: Optional[int]) -> List[str]:
    """The Ks whose ``form`` block fits ``budget`` at p95."""
    return [k for k, v in by_k.items() if fits(v["bytes_by_form"][form], budget)]


def decide_fit(by_k: Dict[str, Dict[str, Any]], budgets: Dict[str, Optional[int]],
               forms: Sequence[Tuple[str, str]] = BUDGET_FORMS) -> Tuple[str, Optional[str], Optional[str]]:
    """#618's bar, declared before measuring: (verdict, K, budget). Build if
    some K whose block fits a budget, in that budget's form, passes #613's bar
    with the lift measured in that form (``diluted-<form>``); otherwise name
    the constraint that failed."""
    fit = [(name, k, form) for name, form in forms
           for k in sorted(fitting(by_k, form, budgets.get(name)), key=int)]
    for name, k, form in fit:
        if passes(by_k[k]["estimate"][BROAD].get("diluted-" + form)):
            return "build", k, name
    if not fit:
        return "do not build: bytes (no K fits a budget)", None, None
    if any(passes(v["estimate"][BROAD].get(lift)) for v in by_k.values()
           for lift in ["diluted-" + f for _, f in forms]):
        return "do not build: bytes (only a K that does not fit passes)", None, None
    if any(passes(by_k[k]["estimate"][BROAD].get("distance")) for _, k, _ in fit):
        return "do not build: dilution (a fitting K passes with #616's lift alone, not with its block)", None, None
    return "do not build: coverage (no fitting K passes even with #616's lift alone)", None, None


# --- report -------------------------------------------------------------------------------

def _day(ts: Optional[float]) -> str:
    import datetime as _dt

    return "-" if ts is None else _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ")


def report_lines(data: Dict[str, Any]) -> List[str]:
    out = ["#613: a SessionStart block filled by relevance history, from #520's cache",
           "units: %s (column both), %d sessions" % (data["units_file"], data["sessions"]),
           "bar: positive = estimate >= 1/15 (%.4f) and CI lower > 0, and so without the top rule" % THRESHOLD,
           ""]
    lt = data["lift"]
    out.append("2. LIFT AT A DISTANCE (arms %s)" % lt["column"])
    d = lt.get("stats") or {}
    if d.get("n"):
        lo, hi = d["ci"]
        out.append("   %d pairs judged (%d na in both arms); follow A %.1f%%, B %.1f%%; lift %+.1f pp [%+.1f, %+.1f]; "
                   "gained %d, lost %d" % (d["n"], d["excluded_na"], 100 * d["follow_a"], 100 * d["follow_b"],
                                          100 * d["lift"], 100 * lo, 100 * hi, d["gained"], d["lost"]))
    else:
        out.append("   not measured yet: run with --dry-run, then --send")
    out.append("   distance: median %s turns / %s chars before the prompt (%d pairs without a transcript); "
               "#434's lift beside the prompt: %s" % (lt["turns_median"], lt["chars_median"], lt["no_context"],
                                                      lt["near_source"]))
    for src, title in (("a", "(a) #520's rater labels"), ("b", "(b) the reflex judge's picks"),
                       ("a_window", "(a) cut to (b)'s window")):
        s = data["sources"][src]
        out += ["", "1. %s: %d rules, %d sessions-with-rule, window %s .. %s; %d of %d sessions start after it" % (
            title, s["rules"], s["events"], _day(s["window"][0]), _day(s["window"][1]),
            s["with_history"], data["sessions"])]
        for sc, n in data["scopes"].items():
            out.append("  over the %d %s sessions" % (n, sc))
            for k in data["ks"]:
                b = s["blocks"][sc][str(k)]
                out.append("   K=%-2d rules median %s, max %s; bytes median %s, p95 %s; %s block(s) over the %d-byte envelope" % (
                    k, b["size"]["median"], b["size"]["max"], b["bytes"]["median"], b["bytes"]["p95"],
                    b["bytes"]["over_envelope"], data["envelope_max"]))
                for r in (STRICT, BROAD):
                    out.append("        %-6s coverage %s; non-redundant %s" % (
                        r, sb._share(b["coverage"][r]), sb._share(b["coverage_fresh"][r])))
                    for name, e in sorted(b["estimate"][r].items()):
                        out.append("        %-6s %-8s %s" % (r, name, sb._e(e)))
                        w = e.get("without_top")
                        if w and r == BROAD:
                            out.append("               without %s (%d unit(s)): %s" % (w["rule"], w["carried"], sb._e(w)))
    out += ["", "DECISION (#613's bar: distance lift, best K per source, over the %d sessions (b) covers): %s" % (
        data["scopes"]["covered"], data["decision"].upper())]
    out += ["   %s" % g for g in data.get("gap") or [] if g]
    fit = data.get("fit")
    if fit:
        out += fit_lines(data, fit)
    return out


def fit_lines(data: Dict[str, Any], fit: Dict[str, Any]) -> List[str]:
    """#618's part of the report: bytes per form, the budgets, the diluted lift, the bar."""
    room, budgets, h = fit["room"], fit["budgets"], fit["hooks"]
    out = ["", "#618: A BLOCK THAT FITS, AND ITS LIFT WITH THE OTHER RULES BESIDE IT",
           "   today's SessionStart envelope (%d inject rows since %s): median %s, p95 %s, max %s bytes; "
           "room under %d: %s at the median, %s at p95" % (
               room["n"], ROOM_SINCE, room["median"], room["p95"], room["max"], data["envelope_max"],
               room["room_median"], room["room_p95"]),
           "   budget (a), inside today's envelope: %s bytes (room at p95, less the blank line); "
           "budget (b), a separate hook entry: %s bytes" % (budgets["a"], budgets["b"]),
           "   two hook entries in transcripts (%d transcripts): %d SessionStart firings with 2+ contexts, %d summed "
           "to >= %d chars with each under it, %d of those persisted" % (
               h["transcripts"], h["firings"], h["summed_over_each_under"], PERSIST_CHARS, h["persisted"]),
           "   (b)'s blocks over the %d covered sessions, bytes median / p95 per form; a K fits when p95 does:" % (
               data["scopes"]["covered"])]
    by_k = data["sources"]["b"]["blocks"]["covered"]
    for k in data["ks"]:
        b = by_k[str(k)]["bytes_by_form"]
        out.append("   K=%-2d %s" % (k, "; ".join("%s %s / %s" % (f, b[f]["median"], b[f]["p95"]) for f in FORMS)))
    for f in FORMS:
        out.append("   fits, %-7s (a) K=%s; (b) K=%s" % (
            f, ",".join(fit["fitting"][f]["a"]) or "none", ",".join(fit["fitting"][f]["b"]) or "none"))
    far = data["lift"].get("stats") or {}
    for arm in fit["arms"]:
        d = arm.get("lift") or {}
        out.append("2'. LIFT IN THE REAL BLOCK, budget (%s): the target among %d other rules, %s form, K=%d "
                   "(median %s mates; %d pair(s) with fewer than K-1)" % (
                       arm["budget"], arm["k"] - 1, arm["form"], arm["k"], arm["mates_median"], arm["short_blocks"]))
        if d.get("n"):
            lo, hi = d["ci"]
            out.append("   %d pairs judged (%d na in both arms); follow A %.1f%%, B %.1f%%; lift %+.1f pp [%+.1f, %+.1f]; "
                       "gained %d, lost %d" % (d["n"], d["excluded_na"], 100 * d["follow_a"], 100 * d["follow_b"],
                                              100 * d["lift"], 100 * lo, 100 * hi, d["gained"], d["lost"]))
        else:
            out.append("   not measured yet: run with --dry-run, then --send")
    if far.get("n"):
        out.append("   against #616's alone, full body: %+.1f pp [%+.1f, %+.1f]; #434's beside the prompt: +31.1 pp (%s)" % (
            100 * far["lift"], 100 * far["ci"][0], 100 * far["ci"][1], data["lift"]["near_source"]))
    for budget, form in BUDGET_FORMS:
        out.append("3. (b) WITH THE %s LIFT, every K that fits budget (%s)" % (form.upper(), budget))
        for sc in data["scopes"]:
            blocks = data["sources"]["b"]["blocks"][sc]
            for k in fit["fitting"][form][budget]:
                e = blocks[k]["estimate"][BROAD].get("diluted-" + form)
                if e:
                    out.append("   %-7s K=%-2s %s" % (sc, k, sb._e(e)))
                    w = e.get("without_top")
                    if w:
                        out.append("                without %s (%d unit(s)): %s" % (w["rule"], w["carried"], sb._e(w)))
    tail = "" if not fit.get("k") else " (K=%s, budget (%s))" % (fit["k"], fit["budget"])
    out += ["", "DECISION (#618's bar, over the %d covered sessions): %s%s" % (
        data["scopes"]["covered"], fit["decision"].upper(), tail)]
    return out


def gap_line(results: Dict[str, Dict[str, Dict[str, Any]]], lift: str, scope: str = "") -> Optional[str]:
    ka, kb = best_k(results.get("a") or {}, lift), best_k(results.get("b") or {}, lift)
    if ka is None or kb is None:
        return None
    ea = results["a"][ka]["estimate"][BROAD][lift].get("E") or 0.0
    eb = results["b"][kb]["estimate"][BROAD][lift].get("E") or 0.0
    aw = best_k(results.get("a_window") or {}, lift)
    ew = (results["a_window"][aw]["estimate"][BROAD][lift].get("E") or 0.0) if aw else None
    return "%s sessions, broad, %s lift: (a) best K=%s %.4f; (a) cut to (b)'s window best K=%s %s; " \
        "(b) best K=%s %.4f per session" % (scope, lift, ka, ea, aw, "-" if ew is None else "%.4f" % ew, kb, eb)


# --- main ---------------------------------------------------------------------------------

def _source_b(vault: Path, projects: Path, inject_at: float) -> Tuple[Any, Dict[str, Any]]:
    """The judge's picks: mnemo's ledger (#619) when it covers the judge's
    whole life, else rebuilt from the reflex logs and transcripts."""
    from mnemo.core.log_utils import iter_rotated_rows
    from mnemo.core.reflex import picks as pick_ledger

    ledger = pick_ledger.load(vault)
    if ledger_covers(ledger):
        return ledger, {"source": "ledger", "log_since": ledger.since()}

    m = vault / ".mnemo"
    picks = log_picks(iter_rotated_rows(m / "reflex-log.jsonl"), inject_at)
    archived = m / "full-body-fresh" / "reflex-rows.jsonl"
    if archived.is_file():
        picks += log_picks(iter_rotated_rows(archived), inject_at)
    n_log = len(picks)
    log_since = min((ts for _, _, ts in picks), default=None)
    since = mrc.epoch(JUDGE_LIVE)
    for path in sorted(projects.glob("*/*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            picks += transcript_picks(fh, path.stem, since)
    return pick_events(picks), {"source": "rebuilt", "log_picks": n_log,
                                "transcript_picks": len(picks) - n_log, "log_since": log_since}


def _build_pairs(vault: Path, projects: Path) -> List[Dict[str, Any]]:
    from mnemo.core.mcp.recall_sessions import _transcripts_by_session

    mrg = _sibling("measure_reflex_gate")
    base = mrc._read(vault / ".mnemo" / rl.OUT_DIR / rl.PAIRS_NAME, None)
    if not base:
        raise SystemExit("error: no #434 pairs; run tools/measure_rule_lift.py first")
    units = {u["uid"]: u for u in mrg._load_units(vault)}
    transcripts = _transcripts_by_session(projects)
    out = []
    for p in base:
        sid = (units.get(p["uid"]) or {}).get("session_id")
        path = transcripts.get(sid) if sid else None
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines() if path else []
        out.append(distance_pair(p, session_turns(lines, sid, p["uid"], mrg.unit_id) if lines else None))
    return out


def _build_diluted(vault: Path, pairs: Sequence[Dict[str, Any]], events: Any, docs: Dict[str, Dict[str, Any]],
                   dates: Dict[str, Dict[str, Any]], k: int, rules: Dict[str, Any],
                   bodies: Dict[str, str], form: str) -> List[Dict[str, Any]]:
    mrg = _sibling("measure_reflex_gate")
    units = {u["uid"]: u for u in mrg._load_units(vault)}
    out = []
    for p in pairs:
        u = units.get(p["uid"]) or {}
        as_of = float(u.get("ts") or 0.0)
        others = mates(p["slug"], p.get("project") or u.get("project") or "", as_of, events, docs, dates,
                       k - 1, u.get("session_id") or "")
        out.append(diluted_pair(p, others, k, rules, bodies, form))
    return out


def _hook_split_all(projects: Path) -> Dict[str, int]:
    total = {"firings": 0, "summed_over_each_under": 0, "persisted": 0, "transcripts": 0}
    for path in sorted(projects.glob("*/*.jsonl")):
        with open(path, encoding="utf-8", errors="replace") as fh:
            got = hook_split(fh)
        for key, n in got.items():
            total[key] += n
        total["transcripts"] += 1
    return total


def _send(pairs: List[Dict[str, Any]], all_answers: Dict[str, Any], answers: Dict[str, Any],
          all_verdicts: Dict[str, Any], col: str, paths: Tuple[Path, Path], args: Any, cfg: Any,
          items_of: Callable[..., List[Dict[str, str]]] = judge_items) -> None:
    from mnemo.core import llm

    provider = llm.resolve(cfg)
    timeout = int(cfg["extraction"]["subprocessTimeout"])
    answers_path, verdicts_path = paths
    lock = threading.Lock()
    calls = rl.pending_calls(pairs, answers, args.samples)

    def one(job: Tuple[int, Tuple[Dict[str, Any], str]]) -> None:
        n, (p, arm) = job
        resp = provider(arm_prompt(p, arm), system=rl.ARM_SYSTEM, model=args.model, timeout=timeout)
        with lock:
            answers.setdefault(p["id"], {}).setdefault(arm, []).append({
                "text": resp.text, "usd": resp.total_cost_usd, "in": resp.input_tokens,
                "out": resp.output_tokens})
            rl._write(answers_path, all_answers)
        print("arm %d/%d %s %s" % (n, len(calls), arm, p["id"]), file=sys.stderr)

    with tempfile.TemporaryDirectory(prefix="mnemo-relevance-block-") as scratch:
        with rl._chdir(scratch):
            with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
                list(pool.map(one, enumerate(calls, 1)))
            verdicts = all_verdicts.setdefault(col, {})
            items = items_of(pairs, answers, verdicts)
            for start in range(0, len(items), rl.JUDGE_CHUNK):
                batch = items[start:start + rl.JUDGE_CHUNK]
                resp = provider(rl.judge_prompt(batch), system=rl.JUDGE_SYSTEM, model=args.judge_model,
                                timeout=timeout)
                verdicts.update(rl.parse_verdicts(resp.text, batch))
                rl._write(verdicts_path, all_verdicts)
                print("judge %d/%d" % (start // rl.JUDGE_CHUNK + 1, -(-len(items) // rl.JUDGE_CHUNK)),
                      file=sys.stderr)


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, paths
    from mnemo.core.hook_envelope import ENVELOPE_MAX_BYTES

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--units", default="", help="#520's units.json (default <vault>/.mnemo/prevented-repeats/units.json)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--k", type=int, action="append", default=[])
    ap.add_argument("--inject-at", type=float, default=INJECT_AT)
    ap.add_argument("--dry-run", action="store_true", help="the lift arms' pending calls and cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="answer both lift arms and judge (model calls)")
    ap.add_argument("--samples", type=int, default=2, help="samples per arm (default 2, as #434's published lift)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--model", default=rl.DEFAULT_MODEL)
    ap.add_argument("--judge-model", default=rl.DEFAULT_JUDGE)
    ap.add_argument("--dilution-k", type=int, default=0,
                    help="K of the dilution arm's block (default: the largest K whose compact block fits 9,000 bytes)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ks = sorted(set(args.k or KS))

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    projects = Path(args.projects)
    out_dir = vault / ".mnemo" / OUT_DIR
    pairs_path, answers_path, verdicts_path = out_dir / PAIRS_NAME, out_dir / ANSWERS_NAME, out_dir / VERDICTS_NAME
    all_answers = mrc._read(answers_path, {})
    all_verdicts = mrc._read(verdicts_path, {})
    arm_col = rl.column(args.model, rl.ARM_SYSTEM)
    col = "%s/%s" % (arm_col, rl.column(args.judge_model, rl.JUDGE_SYSTEM))
    answers = all_answers.setdefault(arm_col, {})
    if not pairs_path.exists():
        if any(all_answers.values()):
            raise SystemExit("error: %s holds answers but %s is gone; refusing to rebuild" % (answers_path, pairs_path))
        out_dir.mkdir(parents=True, exist_ok=True)
        rl._write(pairs_path, _build_pairs(vault, projects))
    pairs = mrc._read(pairs_path, [])

    units_file = Path(args.units).expanduser() if args.units else vault / ".mnemo" / "prevented-repeats" / mpr.UNITS_NAME
    cache = mrc._read(units_file, None)
    if not cache:
        print("no #520 units at %s: run tools/measure_prevented_repeats.py first" % units_file, file=sys.stderr)
        return 1
    units = cache["columns"][mpr.BOTH]
    meta = cache["sessions"]
    sessions = sorted(cache["rated"])

    rules = mpr.Rules(vault, projects, Path(args.claude_home))
    docs = rules.ctx.index.get("docs") or {}
    dates = rules.ctx.dates
    redundant: Dict[str, Set[str]] = {}
    for u in units:
        if u.get("redundant"):
            redundant.setdefault(u["session_id"], set()).add(u["slug"])

    b_events, b_counts = _source_b(vault, projects, args.inject_at)
    b_window = window(b_events)
    events = {"a": rater_events(units, meta), "b": b_events,
              "a_window": rater_events(units, meta, since=b_window[0] if b_window else float("inf"))}
    totals = {src: ({s: len(v) for s, v in evs.items()} if isinstance(evs, dict)
                    else evs.picks_before(None, float("inf"))) for src, evs in events.items()}
    slugs = {s for t in totals.values() for s in t}
    bodies = {s: (rules.ctx.pages.get(s) or ("", ""))[1] for s in slugs}
    exported = export_rules(vault, docs)
    scopes = {"covered": [s for s in sessions if b_window and float(meta[s]["start"]) > b_window[0]],
              "all": sessions}

    # #618, 1: the room today's envelope leaves, and the K the dilution arm uses.
    from mnemo.core.log_utils import iter_rotated_rows

    room = envelope_room(iter_rotated_rows(vault / ".mnemo" / "mcp-access-log.jsonl"), mrc.epoch(ROOM_SINCE),
                         ENVELOPE_MAX_BYTES)
    # The block joins the envelope after a blank line.
    budgets = {"a": None if room["room_p95"] is None else room["room_p95"] - 2, "b": ENVELOPE_MAX_BYTES}
    covered_blocks = {k: blocks_for(scopes["covered"], meta, b_events, docs, dates, k, redundant) for k in ks}
    arms: List[Dict[str, Any]] = []
    for budget, form in BUDGET_FORMS:
        sizes = {k: {"p95": sb.quantile([form_bytes(covered_blocks[k][s], form, exported, bodies)
                                         for s in scopes["covered"]], 0.95)} for k in ks}
        k_arm = args.dilution_k or max([k for k in ks if fits(sizes[k], budgets[budget])] or [min(ks)])
        d_dir = out_dir / (DILUTION_DIR % (form, k_arm))
        d_paths = (d_dir / PAIRS_NAME, d_dir / ANSWERS_NAME, d_dir / VERDICTS_NAME)
        d_all_answers = mrc._read(d_paths[1], {})
        d_all_verdicts = mrc._read(d_paths[2], {})
        if not d_paths[0].exists():
            if any(d_all_answers.values()):
                raise SystemExit("error: %s holds answers but %s is gone; refusing to rebuild" % (d_paths[1], d_paths[0]))
            d_dir.mkdir(parents=True, exist_ok=True)
            rl._write(d_paths[0], _build_diluted(vault, pairs, b_events, docs, dates, k_arm, exported, bodies, form))
        d_pairs = mrc._read(d_paths[0], [])
        d_answers = d_all_answers.setdefault(arm_col, {})
        d_verdicts = d_all_verdicts.setdefault(col, {})
        if seed_arm_a(d_pairs, d_answers, d_verdicts, answers, all_verdicts.get(col, {})):
            rl._write(d_paths[1], d_all_answers)
            rl._write(d_paths[2], d_all_verdicts)
        arms.append({"budget": budget, "form": form, "k": k_arm, "pairs": d_pairs, "answers": d_answers,
                     "all_answers": d_all_answers, "all_verdicts": d_all_verdicts, "paths": d_paths[1:]})

    if args.dry_run or args.send:
        for name, ps, ans in [("lift at a distance", pairs, answers)] + [
                ("lift in a K=%d %s block" % (a["k"], a["form"]), a["pairs"], a["answers"]) for a in arms]:
            pending = len(rl.pending_calls(ps, ans, args.samples))
            e = notional(ps, pending, args.samples)
            print("%s: %d pairs, %d pending arm call(s) + ~%d judge call(s), ~%d input tokens; "
                  "notional ~$%.2f (subscription usage, not money)" % (
                      name, len(ps), e["arm_calls"], e["judge_calls"], e["input_tokens"], e["usd"]), file=sys.stderr)
        if args.dry_run:
            for a in arms:
                sample = next((p for p in a["pairs"] if p.get("mates")), None)
                if sample:
                    print("\nfirst %s arm B prompt (%s, target at position %d of %d), its first 3,000 chars:\n%s" % (
                        a["form"], sample["id"], sample["position"], sample["k"], arm_prompt(sample, "B")[:3000]),
                        file=sys.stderr)
            return 0
        _send(pairs, all_answers, answers, all_verdicts, col, (answers_path, verdicts_path), args, cfg)
        for a in arms:
            _send(a["pairs"], a["all_answers"], a["answers"], a["all_verdicts"], col, a["paths"], args, cfg,
                  items_of=diluted_judge_items)

    near, near_source = mpr.lift_diffs(vault)
    far, far_stats = lift_diffs(pairs, answers, all_verdicts.get(col, {}))
    lifts: Dict[str, Sequence[float]] = {"beside": near}
    if far:
        lifts["distance"] = far
    for a in arms:
        diffs, a["lift"] = lift_diffs(a["pairs"], a["answers"], a["all_verdicts"].get(col, {}))
        if diffs:
            lifts["diluted-" + a["form"]] = diffs

    results: Dict[str, Dict[str, Dict[str, Dict[str, Any]]]] = {sc: {} for sc in scopes}
    sources: Dict[str, Any] = {}
    for src, evs in events.items():
        w = window(evs)
        sources[src] = {"rules": len(totals[src]), "events": sum(totals[src].values()),
                        "window": list(w) if w else [None, None],
                        "with_history": with_history(sessions, meta, evs), "blocks": {}}
        for k in ks:
            per = blocks_for(sessions, meta, evs, docs, dates, k, redundant)
            for sc, subset in scopes.items():
                results[sc].setdefault(src, {})[str(k)] = block_numbers(units, subset, per, lifts, bodies,
                                                                       ENVELOPE_MAX_BYTES, exported)
        for sc in scopes:
            sources[src]["blocks"][sc] = results[sc][src]
    sources["b"].update(b_counts)

    fit = {"room": room, "budgets": budgets, "hooks": _hook_split_all(projects),
           "fitting": {f: {b: fitting(results["covered"]["b"], f, budgets[b]) for b in budgets} for f in FORMS},
           "arms": [{"budget": a["budget"], "form": a["form"], "k": a["k"], "lift": a["lift"],
                     "mates_median": sb.quantile([len(p.get("mates") or ()) for p in a["pairs"]], 0.5),
                     "short_blocks": sum(len(p.get("mates") or ()) < a["k"] - 1 for p in a["pairs"])}
                    for a in arms]}
    if all("diluted-" + f in lifts for _, f in BUDGET_FORMS):
        verdict, k_pass, budget = decide_fit(results["covered"]["b"], budgets)
        fit.update(decision=verdict, k=k_pass, budget=budget)
    else:
        fit["decision"] = "pending: no diluted lift yet (run --dry-run, then --send)"

    turns = [p["turns_before"] for p in pairs]
    chars = [p["chars_before"] for p in pairs]
    decision = decide(results["covered"]) if far else "pending: no distance lift yet"
    data = {
        "units_file": str(units_file).replace(os.path.expanduser("~"), "~"), "sessions": len(sessions),
        "ks": ks, "scopes": {sc: len(v) for sc, v in scopes.items()}, "inject_at": args.inject_at, "envelope_max": ENVELOPE_MAX_BYTES,
        "lift": {"column": col, "stats": far_stats, "near_source": near_source,
                 "turns_median": sb.quantile(turns, 0.5), "chars_median": sb.quantile(chars, 0.5),
                 "no_context": sum(not p["context"] for p in pairs)},
        "sources": sources, "decision": decision, "fit": fit,
        "gap": [gap_line(results[sc], "distance" if far else "beside", sc) for sc in scopes],
    }
    prov = _provenance.provenance(__file__, argv, vault=vault, blind_spots=[
        _provenance.transcripts_blind_spot(projects),
        "(b) read mnemo's judge-picks ledger, complete since %s" % _day(b_counts["log_since"])
        if b_counts["source"] == "ledger" else
        "reflex-log and archived rows begin %s; (b) before that reads transcripts" % _day(b_counts["log_since"])])
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1, default=str))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
