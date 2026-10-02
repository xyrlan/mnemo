"""Does the full-body reflex help in the sessions that ran it? #543's pre-registered fresh check (#545)

Usage:
    PYTHONPATH=src python3 tools/measure_full_body_fresh.py            # dry: fresh emissions, units so far, when it is due
    PYTHONPATH=src python3 tools/measure_full_body_fresh.py --send     # once due: rate, place, answer, judge, report
        [--force] [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_full_body_fresh.py --json     # the report as data
    PYTHONPATH=src python3 tools/measure_full_body_fresh.py --history [--send]
                                        # #535's cached pairs re-judged on the grounded question

#535 (``measure_full_body``) measured the full rule body against the one-line
preview on #527's old units, answered anew. #542 (PR #543) then shipped it:
the reflex writes ``• [[slug]]:`` and the rule's body under it
(``core/reflex/render.py``). It went live on the maintainer's machine at
:data:`LIVE`, when the checkout the hooks import was fast-forwarded. PR #543
fixed this check before any data existed:

- **When:** at least :data:`MIN_DAYS` days of sessions with the change, and
  :data:`MIN_UNITS` fresh units, whichever comes later. Before both hold the
  tool **refuses to send** unless ``--force``, and it prints how many fresh
  units exist and the date the condition should be met.
- **Units:** #527's units (``measure_broad_value``) in human sessions started
  after :data:`LIVE`, whose reflex carried the rule in the full format (a
  ``format: "full"`` row in ``reflex-log.jsonl``). #520's pipeline
  (``measure_prevented_repeats``: both relevance raters, the delivery judge)
  runs on those sessions alone, into this tool's cache, and its delivered-
  and-new broad units whose reflex block carried the rule are kept.
- **Arms:** ``full`` is the prompt as shipped (#527's *with*: the rule's entry
  as the hook wrote it, whole or cut), ``without`` the same prompt without
  the rule's bytes (#527's *without*: its whole entry, read by entry, so no
  body line is left behind). Both are answered on the unit's own recorded
  model, by ``measure_broad_value`` unchanged.
- **Judge (amended 2026-09-29, see below):** two blind raters, the models
  #527, #535 and #540 used, rule-blind with slugs and ``[[…]]`` masked in the
  replies and everything else they see, reply order seeded and flipped for
  the second rater, ties allowed. Two readings of the same replies:

  - **grounded, the verdict** — #540's question and wrong-state marks
    (``measure_briefing_value.JUDGE_SYSTEM``: which reply is more consistent
    with where the work actually went, and does either assume a wrong state
    of the work). The rater sees the agent's previous message, the unit's
    prompt, the two replies and what the session did after that prompt: the
    developer's later typed messages and the agent's last message in the
    session (:func:`work_after`, #534's view). #540's rater prompt frames a
    session's *first* message, so its first paragraph is replaced
    (:data:`GROUNDED_SYSTEM`); the question, the marks and the reply format
    are #540's text itself. A reply is *wrong* when both raters mark it;
  - **preference, secondary** — #527's question (which reply better serves
    this developer in this repository), unchanged, so the fresh result stays
    comparable with #535's +0.215.

  Each reading: both raters agreeing, else a tie; each rater alone beside it;
  the raters' kappa on its question; the grounded one adds each arm's
  wrong-state rate.
- **Bars**, unchanged by the amendment, read on each reading and each
  reported whatever its sign:
  - **confirmed** — ``h(full)`` ≥ :data:`BAR_H` with its CI lower bound > 0
    (bootstrap over units); **not confirmed** when the CI upper bound is
    under the bar;
  - **product (#527's)** — helpful changes ≥ 1 per 15 human sessions with a
    CI lower bound > 0 (bootstrap over sessions): the fresh units per rated
    fresh session times their mean ``h``. The change touched only the
    reflex, so this counts what the reflex delivered.
- **Split:** rules delivered whole against rules cut to fit, from the log
  row's ``rule_whole``.

The hook keeps one rotated generation of ``reflex-log.jsonl`` (1 MB each, about
4.5 days of rows on the maintainer's machine), so by the due date the first
days' rows are gone. Every run copies the fresh ``format: "full"`` rows into
this tool's cache (``reflex-rows.jsonl``), and where a row is gone anyway the
block itself says the same: a full block has a head alone on its line, and a
cut entry ends with the hook's ``[cut to fit the prompt limit …]`` pointer.
The report says which source each unit's reading came from.

**Amendment, 2026-09-29 (#554), before any fresh unit was judged.** PR #543
fixed #527's question as the judge. On it the two raters agree poorly:
kappa 0.24 in #527 and 0.37 in #535. On #540's grounded question they agreed
at 0.52 (#540) and 0.56 (#548). The maintainer made the grounded question
primary on 2026-09-29, and #527's stays as the secondary reading. The bars,
units, arms and due condition do not change. No fresh unit had been judged:
the dry run that day counted 0 fresh human sessions and 0 full-format
emissions in them, #520 had not rated any fresh session, so there were no
fresh units, and ``<vault>/.mnemo/full-body-fresh`` held only the log archive
and the dry run's report, with no ``broad-value/verdicts.json`` and no
``grounded/verdicts.json``.

**#535's pairs, re-judged** (``--history``). As a check on history, #535's
cached ``full`` vs ``without`` replies (``<vault>/.mnemo/full-body``) are
judged again on the grounded question into ``<out>/history-535``, with #540's
wrong-state marks. They are read next to #535's frozen +0.215 on the same
72 units, re-read from #535's own verdicts. #535's and #527's caches are read
and never written. #520's ``units.json`` (``--baseline``) gives each
session's transcript, from which what the session did next is read.

A session that started under :data:`SETTLE_HOURS` ago may still be running;
#520 freezes a session's prompts the first time it reads it, so those
sessions wait for a later run.

Only ``--send`` calls a model. Every answer is cached under ``--out``
(default ``<vault>/.mnemo/full-body-fresh``) as it arrives, so a rerun
resumes. What each unit's session did next is frozen in
``grounded/work.json`` the first time it is read. Nothing is written to
#520's, #527's or #535's caches.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bv = _sibling("measure_broad_value")
mbv = _sibling("measure_briefing_value")
mfb = _sibling("measure_full_body")
mja = mbv.mja
mpr = bv.mpr
mrc = bv.mrc
rl = bv.rl

#: When the full-body reflex went live on the maintainer's machine: the main
#: checkout the hooks import was fast-forwarded to ``72edf85`` (#543).
LIVE = "2026-09-28T23:09:00Z"
MIN_DAYS = 14
MIN_UNITS = 60
#: The confirmation bar on ``h(full)``, fixed in PR #543.
BAR_H = 0.10
#: #527's product bar: one helpful change per 15 human sessions.
THRESHOLD = bv.THRESHOLD
SETTLE_HOURS = 24
OUT_DIR = "full-body-fresh"
ROWS_NAME = "reflex-rows.jsonl"
FRESH_UNITS_NAME = "units-fresh.json"
#: What ``render.cut_line`` writes under a body cut to fit.
CUT_MARK = "[cut to fit the prompt limit"
#: A log row is the hook's for a prompt when written within this many seconds
#: of it (the judge answers in about a second).
ROW_SLACK = 120
WHOLE, CUT, UNKNOWN = "whole", "cut", "unknown"

#: The two readings: #540's grounded question, primary since 2026-09-29, and
#: #527's "which reply better serves", kept beside it for #535.
GROUNDED, PREFERENCE = "grounded", "preference"
#: The treatment arm's name in the grounded verdicts; the control is #540's ``none``.
FULL = "full"
GROUNDED_DIR = "grounded"
HISTORY_DIR = "history-535"
#: #535's cache and the reading this tool sets the grounded one against.
FULL_BODY_DIR = mfb.OUT_DIR
#: #540's ruler bar, reported (not a gate: the bars of PR #543 do not change).
KAPPA_BAR = mbv.KAPPA_BAR
#: The raters' agreement on each question where they were asked it before.
KAPPA_SEEN = {PREFERENCE: "0.24 in #527, 0.37 in #535", GROUNDED: "0.52 in #540, 0.56 in #548"}

#: #540's rater sees a session's *first* message; a reflex unit sits at any
#: prompt, so its first paragraph says so and the agent's previous message is
#: shown. The question, the wrong-state marks and the reply format are
#: #540's own text, not a copy of it.
_FRAME_540, _QUESTION_540 = mbv.JUDGE_SYSTEM.split("\n\n", 1)
GROUNDED_SYSTEM = """\
You are shown a message a developer typed during an AI coding session, the
agent's previous message, two replies the agent could have sent to it, and
what the session actually went on to do: the developer's later messages, in
order, and the agent's last message.

""" + _QUESTION_540


# --- the log -------------------------------------------------------------------------------

def fresh_rows(rows: Iterable[Dict[str, Any]], live: str = LIVE) -> List[Dict[str, Any]]:
    """The reflex-log rows the full format wrote since ``live``."""
    return [r for r in rows if isinstance(r, dict) and r.get("format") == "full"
            and r.get("emitted") and str(r.get("ts") or "") >= live]


def _row_key(row: Dict[str, Any]) -> str:
    return json.dumps([row.get("session_id"), row.get("prompt_hash"), row.get("ts"), row.get("emitted")])


def merge_rows(kept: Sequence[Dict[str, Any]], live_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The archive with the log's rows it lacks, oldest first."""
    seen = {_row_key(r) for r in kept}
    out = list(kept)
    for r in live_rows:
        k = _row_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return sorted(out, key=lambda r: str(r.get("ts") or ""))


def read_rows(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def write_rows(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


# --- the blocks ----------------------------------------------------------------------------

def entry_whole(entry: str) -> bool:
    """Whether a full-format entry carries the rule's whole body: its head
    stands alone and no cut pointer ends it. A preview line inside a full
    block (the page could not be read) is not whole."""
    first = entry.split("\n", 1)[0]
    return bool(mpr._ENTRY_HEAD.fullmatch(first)) and CUT_MARK not in entry


def full_emissions(events: List[dict]) -> List[Dict[str, Any]]:
    """Every rule a full-format reflex block delivered in a transcript:
    ``{slug, whole, ts}`` in order, one per entry."""
    out: List[Dict[str, Any]] = []
    ts = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("timestamp"):
            ts = ev.get("timestamp")
        att = ev.get("attachment")
        if not (isinstance(att, dict) and att.get("type") == "hook_additional_context"):
            continue
        for text in mpr._hook_texts(att):
            if mpr._REFLEX in text and mpr.is_full_block(text):
                for slug, entry in mpr.reflex_entries(text):
                    if slug:
                        out.append({"slug": slug, "whole": entry_whole(entry), "ts": ts})
    return out


def carriers(ctx: Dict[str, Any], slug: str, project: str) -> List[str]:
    """The reflex blocks in a placed unit's context that carried the rule, in order."""
    return [t for _, t in ctx["reflex"] if bv.carries_reflex(t, slug, project)]


def entry_of(block: str, slug: str, project: str) -> Optional[str]:
    for s, entry in mpr.reflex_entries(block):
        if s and bv.same_rule(s, slug, project):
            return entry
    return None


def log_whole(rows: Sequence[Dict[str, Any]], session_id: str, block: str, slug: str,
              project: str, before: Optional[float]) -> Optional[bool]:
    """``rule_whole`` for the rule in the log rows that wrote ``block``: same
    session, the same emitted rules in order, written by ``before`` (plus
    :data:`ROW_SLACK`). None when no such row is left, or the rows disagree."""
    emitted = mpr.reflex_slugs(block)
    got: Set[bool] = set()
    for r in rows:
        if r.get("session_id") != session_id or list(r.get("emitted") or []) != emitted:
            continue
        at = mrc.epoch(r.get("ts"))
        if before is not None and at is not None and at > before + ROW_SLACK:
            continue
        whole = r.get("rule_whole")
        for i, s in enumerate(emitted):
            if bv.same_rule(s, slug, project) and isinstance(whole, list) and i < len(whole):
                got.add(bool(whole[i]))
    return got.pop() if len(got) == 1 else None


def unit_reading(row: Dict[str, Any], ctx: Dict[str, Any], project: str,
                 log: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A fresh unit's reading: the last full-format block that carried the
    rule, whether the rule was whole in it (the log's ``rule_whole``, else
    the entry itself) and where that came from. None when no full block
    carried it — the old format, or ``reflex.body: preview``."""
    slug = row["slug"]
    full = [b for b in carriers(ctx, slug, project) if mpr.is_full_block(b)]
    if not full:
        return None
    block = full[-1]
    entry = entry_of(block, slug, project) or ""
    text_whole = entry_whole(entry)
    whole = log_whole(log, row["session_id"], block, slug, project, ctx.get("ts"))
    return {"whole": WHOLE if (text_whole if whole is None else whole) else CUT,
            "source": "transcript" if whole is None else "log",
            "agrees": None if whole is None else whole == text_whole}


# --- when it is due ------------------------------------------------------------------------

def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def due(now: float, units: Optional[int], live: str = LIVE, days: int = MIN_DAYS,
        min_units: int = MIN_UNITS) -> Dict[str, Any]:
    """The due condition: ``days`` since ``live`` and ``min_units`` units.
    ``units`` is None until #520's raters have read the fresh sessions."""
    start = mrc.epoch(live) or 0.0
    date = start + days * 86400
    return {"date": _iso(date), "date_at": date, "date_ok": now >= date, "units": units,
            "units_ok": units is not None and units >= min_units,
            "due": now >= date and units is not None and units >= min_units,
            "days_in": (now - start) / 86400}


def projected(now: float, per_day: Optional[float], live: str = LIVE, days: int = MIN_DAYS,
              min_units: int = MIN_UNITS) -> Optional[str]:
    """The date both conditions hold, at ``per_day`` units a day since ``live``."""
    if not per_day or per_day <= 0:
        return None
    start = mrc.epoch(live) or 0.0
    return _iso(max(start + days * 86400, start + min_units / per_day * 86400))


def historical_yield(source: Optional[Dict[str, Any]],
                     load: Callable[[Path], List[dict]]) -> Optional[Dict[str, Any]]:
    """How many reflex emissions became a unit before the change: #520's frozen
    reflex-channel delivered-and-new units over the distinct (session, rule)
    pairs its rated sessions' reflex blocks delivered. None without #520's file.

    #520 stores each session's path as a string; *load* gets a ``Path``, as
    the briefing module's loader the dry run passes in needs."""
    if not source:
        return None
    units = sum(1 for r in source["columns"].get(mpr.BOTH) or []
                if r.get("new") and r.get("judged", True) and r.get("reflex"))
    pairs = 0
    for sid in source["rated"]:
        slugs: Set[str] = set()
        for ev in load(Path(source["sessions"][sid]["path"])):
            att = ev.get("attachment") if isinstance(ev, dict) else None
            if isinstance(att, dict) and att.get("type") == "hook_additional_context":
                for text in mpr._hook_texts(att):
                    if mpr._REFLEX in text:
                        slugs.update(mpr.reflex_slugs(text))
        pairs += len(slugs)
    return {"units": units, "pairs": pairs, "per_pair": units / pairs if pairs else None}


# --- the grounded judge --------------------------------------------------------------------

def work_after(events: List[dict], i: int) -> Dict[str, Any]:
    """What the session did after typed turn ``i``, in #534's and #540's view:
    the developer's later typed messages (shorter ones counted, not shown)
    and the agent's last message in the session."""
    later = [p for p in mpr.walk(events)["prompts"] if p["i"] > i]
    prompts, short = mja.typed_prompts(later)
    return {"prompts": [mpr._head(t, mja.BRIEFING_PROMPT_CHARS) for t in prompts[:mja.BRIEFING_PROMPTS]],
            "more_prompts": max(0, len(prompts) - mja.BRIEFING_PROMPTS), "short_replies": short,
            "final": mpr._tail(mja.last_agent_text(events), mja.BRIEFING_FINAL_CHARS)}


def masked_work(work: Dict[str, Any], slug: str) -> Dict[str, Any]:
    """The work with the rule's slug masked, so the rater stays rule-blind."""
    return dict(work, prompts=[bv.mask(t, slug)[0] for t in work["prompts"]],
                final=bv.mask(work["final"], slug)[0])


def grounded_id(uid: str, k: int) -> str:
    return mbv.comparison_id(uid, FULL, k)


def grounded_prompt(arms: Dict[str, Any], work: Dict[str, Any], replies: Sequence[str]) -> str:
    """#540's judge prompt at a reflex unit's prompt, with the agent's previous
    message before it (the prompt alone is often "ok, do it")."""
    slug = arms["slug"]
    return "\n\n".join([
        "## The agent's previous message", bv.mask(arms["previous"], slug)[0] or "(none)",
        "## The developer's message", arms["prompt"],
        "## Reply 1", replies[0], "## Reply 2", replies[1],
        "## What the session actually went on to do", mbv.work_text(masked_work(work, slug))])


def grounded_comparison(arms: Dict[str, Any], work: Dict[str, Any], full: Sequence[Dict[str, Any]],
                        without: Sequence[Dict[str, Any]], k: int, rater_index: int) -> Optional[str]:
    """The k-th full reply against the k-th without reply, rule-blind, in
    #540's seeded order (flipped for the second rater). None until both exist."""
    if len(full) <= k or len(without) <= k:
        return None
    f = bv.mask(full[k]["text"], arms["slug"])[0]
    wo = bv.mask(without[k]["text"], arms["slug"])[0]
    pair = (f, wo) if mbv.treatment_first(grounded_id(arms["id"], k), rater_index) else (wo, f)
    return grounded_prompt(arms, work, pair)


def parse_grounded(text: str, uid: str, k: int, rater_index: int) -> Optional[Dict[str, Any]]:
    """#540's parse: ``{"better": full|none|tie, "wrong": {full, none}, "why"}``;
    ``none`` is the *without* arm."""
    return mbv.parse_judge(text, FULL, grounded_id(uid, k), rater_index)


def grounded_scores(verdicts: Dict[str, Dict[str, Dict[str, Any]]], uid: str,
                    raters: Sequence[str]) -> Optional[Dict[str, float]]:
    """#540's per-unit reading: ``h``, the shares, each arm's wrong-state rate."""
    return mbv.unit_scores(verdicts, uid, FULL, raters, samples=bv.SAMPLES)


def preference_scores(verdicts: Dict[str, Dict[str, Dict[str, str]]], uid: str,
                      raters: Sequence[str]) -> Optional[Dict[str, float]]:
    """#527's per-unit reading: ``h`` alone (the question has no wrong-state mark)."""
    h = bv.unit_h(verdicts, uid, raters)
    return None if h is None else {"h": h}


def grounded_todo(pairs: Sequence[Tuple[Dict[str, Any], Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]],
                  work: Dict[str, Dict[str, Any]], verdicts: Dict[str, Dict[str, Any]], raters: Sequence[str],
                  rater: str) -> List[Tuple[str, int, str]]:
    """``(uid, k, prompt)`` still to judge by ``rater``. ``pairs``: ``(arms,
    full replies, without replies)`` per unit."""
    ri = raters.index(rater)
    todo = []
    for arms, full, without in pairs:
        uid = arms["id"]
        if uid not in work:
            continue
        for k in range(bv.SAMPLES):
            if grounded_id(uid, k) in verdicts[rater]:
                continue
            p = grounded_comparison(arms, work[uid], full, without, k, ri)
            if p is not None:
                todo.append((uid, k, p))
    return todo


def judge_grounded(pairs: Sequence[Tuple[Dict[str, Any], Sequence[Dict[str, Any]], Sequence[Dict[str, Any]]]],
                   work: Dict[str, Dict[str, Any]], out: Path, raters: Sequence[str],
                   send: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The grounded verdicts cached under ``out`` (``verdicts.json``, one
    column per rater and system), judging what is pending when ``send``
    carries ``{provider, timeout, workers, pause, limit}``. Prints what is
    pending and its notional cost."""
    all_verdicts = mrc._read(out / "verdicts.json", {})
    verdicts = {r: all_verdicts.setdefault(mrc.column(r, GROUNDED_SYSTEM), {}) for r in raters}
    if send is not None:
        sender = mpr.Sender(send["provider"], send["timeout"], out / "calls.jsonl", send["workers"], send["pause"])

        def on_judge(r: str, uid: str, k: int) -> Callable[[str], None]:
            def take(text: str) -> None:
                got = parse_grounded(text, uid, k, raters.index(r))
                if got is not None:
                    verdicts[r][grounded_id(uid, k)] = got
                    mrc._write(out / "verdicts.json", all_verdicts)
            return take
        calls = [(r, p, GROUNDED_SYSTEM, on_judge(r, uid, k)) for r in raters
                 for uid, k, p in grounded_todo(pairs, work, verdicts, raters, r)]
        with scratch_cwd():
            sender.run(calls[:send["limit"]] if send.get("limit") else calls)
        print("grounded judge: spent $%.2f notional this run (subscription usage, not money)" % sender.usd,
              file=sys.stderr)
    for r in raters:
        jt = grounded_todo(pairs, work, verdicts, raters, r)
        print("%s: pending %d grounded judge call(s); notional ~$%.2f"
              % (r, len(jt), bv.notional(r, [(p, GROUNDED_SYSTEM) for _, _, p in jt], mbv.JUDGE_TOKENS)),
              file=sys.stderr)
    return verdicts


@contextlib.contextmanager
def scratch_cwd() -> Iterator[None]:
    """No project CLAUDE.md or auto-memory reaches a rater."""
    here, scratch = os.getcwd(), tempfile.mkdtemp(prefix="mnemo-full-body-fresh-")
    os.chdir(scratch)
    try:
        yield
    finally:
        os.chdir(here)
        shutil.rmtree(scratch, ignore_errors=True)


def agreement(verdicts: Dict[str, Dict[str, Dict[str, Any]]], uids: Sequence[str], raters: Sequence[str],
              reading: str) -> Optional[Dict[str, Any]]:
    """The two raters' kappa on a reading's question (and, grounded, on the
    wrong-state marks). None without two raters."""
    if len(raters) != 2:
        return None
    if reading == GROUNDED:
        return mbv.kappa(verdicts, [grounded_id(u, k) for u in uids for k in range(bv.SAMPLES)], raters)
    a, b = [], []
    for u in uids:
        for k in range(bv.SAMPLES):
            va, vb = (verdicts.get(r, {}).get(bv.comparison_id(u, k)) for r in raters)
            if va and vb:
                a.append(va["better"])
                b.append(vb["better"])
    return {"n": len(a), "same": sum(x == y for x, y in zip(a, b)), "kappa": bv._kappa3(a, b)}


# --- the numbers ---------------------------------------------------------------------------

def h_verdict(st: Dict[str, Any], bar: float = BAR_H) -> str:
    """The confirmation bar on ``h(full)``, CI over units."""
    if not st.get("n"):
        return "no estimate yet"
    lo, hi = st["ci"]
    if st["h"] >= bar and lo > 0:
        return "confirmed"
    if hi < bar:
        return "not confirmed"
    return "inconclusive"


#: What the grounded reading bootstraps beside ``h``: the shares each way
#: (``arm`` is *full*, ``none`` is *without*) and each arm's wrong-state rate.
GROUNDED_KEYS = ("arm", "none", "tie", "wrong_arm", "wrong_none", "wrong_diff")


def readings(units: Sequence[Dict[str, Any]], rated: Sequence[str], arms: Dict[str, Any],
             verdicts: Dict[str, Dict[str, Dict[str, Any]]], raters: Sequence[str],
             scores: Callable[..., Optional[Dict[str, float]]] = preference_scores) -> Dict[str, Any]:
    """Both bars for both raters together and each alone, the whole/cut split
    on the both-raters column, and — when ``scores`` reads wrong-state marks
    (:func:`grounded_scores`) — each arm's wrong-state rate, CI over units.
    ``units``: ``{id, session_id, whole}``."""
    columns = ([bv.BOTH] if len(raters) > 1 else []) + list(raters)
    new_units: Dict[str, List[str]] = {}
    for u in units:
        new_units.setdefault(u["session_id"], []).append(u["id"])
    out: Dict[str, Any] = {}
    split: Dict[str, Any] = {}
    for col in columns:
        rs = list(raters) if col == bv.BOTH else [col]
        got = {u["id"]: (scores(verdicts, u["id"], rs) if (arms.get(u["id"]) or {}).get("measurable") else None)
               for u in units}
        h = {uid: (s["h"] if s is not None else None) for uid, s in got.items()}
        hs = [x for x in h.values() if x is not None]
        st_h = bv.h_ci(hs)
        st_e = bv.combine(bv.per_session_rows(rated, new_units, h))
        out[col] = {"h": st_h, "h_verdict": h_verdict(st_h), "product": st_e,
                    "product_verdict": bv.verdict(st_e)}
        marked = [dict(s, wrong_diff=s["wrong_arm"] - s["wrong_none"]) for s in got.values()
                  if s is not None and "wrong_arm" in s]
        if marked:
            out[col]["wrong_state"] = mbv.bootstrap(marked, GROUNDED_KEYS)
        if col == columns[0]:
            for kind in (WHOLE, CUT):
                split[kind] = bv.h_ci([h[u["id"]] for u in units
                                       if u["whole"] == kind and h[u["id"]] is not None])
    return {"columns": out, "split": split, "primary": columns[0]}


def both_readings(units: Sequence[Dict[str, Any]], rated: Sequence[str], arms: Dict[str, Any],
                  grounded: Dict[str, Dict[str, Dict[str, Any]]], preference: Dict[str, Dict[str, Dict[str, str]]],
                  raters: Sequence[str]) -> Dict[str, Any]:
    """The grounded reading (the verdict since 2026-09-29) and #527's beside
    it, each with the raters' kappa on its own question."""
    uids = [u["id"] for u in units if (arms.get(u["id"]) or {}).get("measurable")]
    out: Dict[str, Any] = {"primary": GROUNDED}
    for name, verdicts, scores in ((GROUNDED, grounded, grounded_scores),
                                   (PREFERENCE, preference, preference_scores)):
        out[name] = dict(readings(units, rated, arms, verdicts, raters, scores),
                         kappa=agreement(verdicts, uids, raters, name))
    return out


# --- report --------------------------------------------------------------------------------

def _h(st: Dict[str, Any]) -> str:
    if not st.get("n"):
        return "no units judged"
    return "h %+.3f [%+.3f, %+.3f] over %d units (%d better, %d worse)" % (
        st["h"], st["ci"][0], st["ci"][1], st["n"], st["wins"], st["losses"])


def report_lines(data: Dict[str, Any]) -> List[str]:
    d = data["due"]
    lines = [
        "full-body reflex live since %s (%.1f days)" % (data["live"], d["days_in"]),
        "reflex-log rows in the full format since then: %d on disk, %d kept in this tool's archive; "
        "%d from human sessions" % (data["rows"]["log"], data["rows"]["archive"], data["rows"]["human"]),
        "fresh human sessions: %d started since then, %d with a full-format reflex block; "
        "%d (session, rule) emissions, %d whole, %d cut"
        % (data["sessions"], data["sessions_with_full"], data["emissions"]["n"],
           data["emissions"]["whole"], data["emissions"]["cut"]),
    ]
    if data.get("rated") is None:
        lines.append("fresh units: not counted yet: #520's raters have not read the fresh sessions "
                     "(that is the first --send once the date holds)")
    else:
        lines.append("fresh units: %d, in %d rated fresh sessions (#520 broad reading, both raters, "
                     "delivered-and-new, the reflex carried the rule in the full format)%s"
                     % (data["units"], data["rated"],
                        "; %d more wait for #520's delivery judge" % data["provisional"]
                        if data.get("provisional") else ""))
        if data.get("sources"):
            lines.append("  whole/cut read from: %s" % ", ".join(
                "%s %d" % kv for kv in sorted(data["sources"].items())))
    lines.append("due: from %s (%s) and at %d fresh units (%s); %s"
                 % (d["date"], "reached" if d["date_ok"] else "not yet", MIN_UNITS,
                    "reached" if d["units_ok"] else "not yet", "DUE" if d["due"] else "NOT DUE"))
    p = data.get("projection") or {}
    if p.get("date"):
        lines.append("  both conditions should hold by %s (%s)" % (p["date"], p["how"]))
    elif p.get("how"):
        lines.append("  the date both conditions hold: unknown (%s)" % p["how"])
    res = data.get("results")
    if not res:
        return lines
    for name, title in ((GROUNDED, "GROUNDED (#540's question: which reply is more consistent with where the "
                                    "work went; primary since 2026-09-29)"),
                        (PREFERENCE, "PREFERENCE (#527's question: which reply better serves; secondary, "
                                     "comparable with #535)")):
        lines += [""] + reading_lines(res[name], name, title, name == res["primary"])
    return lines


def _pct(st: Dict[str, Any], key: str) -> str:
    return "%.1f%% [%.1f%%, %.1f%%]" % (100 * st[key], 100 * st[key + "_ci"][0], 100 * st[key + "_ci"][1])


def kappa_line(k: Optional[Dict[str, Any]], reading: str) -> Optional[str]:
    if not k or not k.get("n"):
        return None
    line = "  rater agreement: %d of %d the same, kappa %s (seen before: %s; #540's ruler bar %.2f, reported, " \
           "not a gate)" % (k["same"], k["n"], "n/a" if k["kappa"] is None else "%.2f" % k["kappa"],
                            KAPPA_SEEN[reading], KAPPA_BAR)
    if k.get("wrong_n"):
        line += "; wrong-state marks kappa %s over %d" % (
            "n/a" if k["wrong_kappa"] is None else "%.2f" % k["wrong_kappa"], k["wrong_n"])
    return line


def reading_lines(res: Dict[str, Any], name: str, title: str, primary: bool) -> List[str]:
    """One reading: each column's two bars, its wrong-state rates, the
    raters' kappa and the whole/cut split."""
    lines = ["%s%s" % (title, "  <- the verdict" if primary else "")]
    for col, st in res["columns"].items():
        lines += ["%s:" % col]
        lines.append("  h(full)   %s" % _h(st["h"]))
        lines.append("            %s   (bar: h >= %+.2f with CI lower bound > 0, bootstrap over units)"
                     % (st["h_verdict"].upper(), BAR_H))
        e = st["product"]
        if e.get("E") is None:
            lines.append("  product   no estimate yet")
        else:
            ci = e["ci"]
            lines.append("  product   %.3f fresh units per session [%.3f, %.3f] x h %+.3f = %s helpful changes "
                         "per human session [%.4f, %.4f]" % (e["rate"], ci["rate"][0], ci["rate"][1], e["h"],
                                                             bv._per(e["E"]), ci["E"][0], ci["E"][1]))
            lines.append("            %s   (#527's bar: >= %.4f = 1 per 15, CI lower bound > 0, "
                         "bootstrap over sessions)" % (st["product_verdict"].upper(), THRESHOLD))
        w = st.get("wrong_state") or {}
        if w.get("n"):
            lines.append("  wrong-state replies: full %s, without %s; difference %+.3f [%+.3f, %+.3f] over %d units"
                         % (_pct(w, "wrong_arm"), _pct(w, "wrong_none"), w["wrong_diff"],
                            w["wrong_diff_ci"][0], w["wrong_diff_ci"][1], w["n"]))
    k = kappa_line(res.get("kappa"), name)
    if k:
        lines.append(k)
    lines.append("h(full) by delivery (%s, rule_whole):" % res["primary"])
    for kind in (WHOLE, CUT):
        lines.append("  %-6s %s" % (kind, _h(res["split"][kind])))
    return lines


# --- #535's pairs, re-judged ---------------------------------------------------------------

def history_columns(uids: Sequence[str], grounded: Dict[str, Dict[str, Dict[str, Any]]],
                    frozen: Dict[str, Dict[str, Dict[str, str]]], raters: Sequence[str]) -> Dict[str, Any]:
    """Per column, ``h(full)`` on #540's grounded question and on #527's
    (#535's frozen verdicts), over the same units, and the grounded
    wrong-state rates. CI over units."""
    columns = ([bv.BOTH] if len(raters) > 1 else []) + list(raters)
    out: Dict[str, Any] = {}
    for col in columns:
        rs = list(raters) if col == bv.BOTH else [col]
        g = [x for x in (grounded_scores(grounded, u, rs) for u in uids) if x is not None]
        p = [x for x in (mfb.unit_h(frozen, u, rs) for u in uids) if x is not None]
        out[col] = {GROUNDED: bv.h_ci([x["h"] for x in g]), PREFERENCE: bv.h_ci(p)}
        if g:
            out[col]["wrong_state"] = mbv.bootstrap([dict(x, wrong_diff=x["wrong_arm"] - x["wrong_none"])
                                                     for x in g], GROUNDED_KEYS)
    return out


def history_lines(data: Dict[str, Any]) -> List[str]:
    f = data["frozen"]
    lines = ["#535's full vs without pairs (%s, read only): %d units, %d whose rule was not rewritten (#535's "
             "verdict set); grounded work read for %d; judged on the grounded question: %d"
             % (data["source"], data["units"], data["consistent"], data["work"], data["judged"])]
    if f.get("h") is not None:
        lines.append("#535's frozen verdict (both raters, #527's question): h(full) %+.3f [%+.3f, %+.3f] over %d "
                     "units, kappa %s; not rewritten" % (f["h"], f["ci"][0], f["ci"][1], f["n"],
                                                         "n/a" if f.get("kappa") is None else "%.2f" % f["kappa"]))
    for name, title in (("consistent", "the %d units #535's verdict reads" % data["consistent"]),
                        ("all", "all %d units, rewritten rules too" % data["units"])):
        lines += ["", "%s:" % title]
        for col, st in data["results"][name].items():
            lines.append("  %-20s grounded %s" % (col, _h(st[GROUNDED])))
            lines.append("  %-20s #527's   %s" % ("", _h(st[PREFERENCE])))
            w = st.get("wrong_state") or {}
            if w.get("n"):
                lines.append("  %-20s grounded shares: full better %.1f%%, without better %.1f%%, tie %.1f%%; "
                             "wrong-state replies: full %s, without %s" % (
                                 "", 100 * w["arm"], 100 * w["none"], 100 * w["tie"],
                                 _pct(w, "wrong_arm"), _pct(w, "wrong_none")))
    k = kappa_line(data.get("kappa"), GROUNDED)
    if k:
        lines += ["", k.strip()]
    return lines


def history_main(args: argparse.Namespace, cfg: Dict[str, Any], vault: Path, out: Path,
                 raters: Sequence[str], provider: Any = None) -> int:
    """Re-judge #535's cached ``full`` vs ``without`` replies on the grounded
    question, into ``<out>/history-535``. #535's and #527's caches are read,
    never written."""
    from mnemo.core.briefing import _load_jsonl_events

    fb = vault / ".mnemo" / FULL_BODY_DIR
    src = vault / ".mnemo" / bv.OUT_DIR
    fb_arms = mrc._read(fb / "arms.json", None)
    if fb_arms is None:
        raise SystemExit("error: no %s; #535 (tools/measure_full_body.py) has not run here" % (fb / "arms.json"))
    base_file = Path(args.baseline).expanduser() if args.baseline else (
        vault / ".mnemo" / "prevented-repeats" / mpr.UNITS_NAME)
    sessions = (mrc._read(base_file, {}) or {}).get("sessions") or {}
    src_arms = mrc._read(src / "arms.json", {})
    fb_answers = mrc._read(fb / "answers.json", {}).get(mrc.column("session-model", rl.ARM_SYSTEM), {})
    src_answers = mrc._read(src / "answers.json", {}).get(mrc.column("session-model", rl.ARM_SYSTEM), {})
    fb_all = mrc._read(fb / "verdicts.json", {})
    frozen = {r: fb_all.get(mrc.column(r, bv.JUDGE_SYSTEM), {}) for r in raters}
    fb_report = mrc._read(fb / "report.json", {})

    h_out = out / HISTORY_DIR
    h_out.mkdir(parents=True, exist_ok=True)
    uids = sorted(u for u in fb_arms if u in src_arms)
    work = mrc._read(h_out / "work.json", {})
    for uid in uids:
        a = src_arms[uid]
        path = (sessions.get(a["session_id"]) or {}).get("path")
        if uid not in work and path and Path(path).is_file():
            work[uid] = work_after(_load_jsonl_events(Path(path)), a["i"])
    mrc._write(h_out / "work.json", work)
    pairs = [(src_arms[u], fb_answers.get(u, []), (src_answers.get(u) or {}).get(bv.WITHOUT, [])) for u in uids]
    send = send_opts(args, cfg, provider) if args.send else None
    grounded = judge_grounded(pairs, work, h_out, raters, send)

    consistent = [u for u in uids if not fb_arms[u].get("preview_drift")]
    both = (fb_report.get("results") or {}).get(bv.BOTH) or {}
    data = {"source": str(Path("<vault>") / ".mnemo" / FULL_BODY_DIR), "units": len(uids),
            "consistent": len(consistent), "work": sum(1 for u in uids if u in work),
            "judged": sum(1 for u in consistent if grounded_scores(grounded, u, raters) is not None),
            "frozen": {"h": both.get("full"), "ci": (both.get("ci") or {}).get("full"), "n": both.get("n"),
                       "kappa": (fb_report.get("agreement") or {}).get("kappa")},
            "results": {"consistent": history_columns(consistent, grounded, frozen, raters),
                        "all": history_columns(uids, grounded, frozen, raters)},
            "kappa": agreement(grounded, consistent, raters, GROUNDED)}
    mrc._write(h_out / "report.json", data)
    if args.json:
        print(json.dumps(data, indent=1))
    else:
        print("\n".join(history_lines(data)))
    return 0


# --- driver --------------------------------------------------------------------------------

def _quiet(fn: Callable[[List[str]], int], argv: List[str], log: Path) -> int:
    """Run a sibling tool's ``main`` with its report written to ``log``."""
    with log.open("w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
        return fn(argv)


def send_opts(args: argparse.Namespace, cfg: Dict[str, Any], provider: Any = None) -> Dict[str, Any]:
    from mnemo.core import llm

    return {"provider": provider or llm.resolve(cfg), "workers": args.workers, "pause": args.pause,
            "limit": args.limit,
            "timeout": max(int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180), 600)}


def main(argv: Optional[List[str]] = None, now: Optional[float] = None) -> int:
    from mnemo.core import config, paths
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.core.log_utils import iter_rotated_rows

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=str(Path("~/.claude/projects").expanduser()))
    ap.add_argument("--claude-home", default=str(Path("~/.claude").expanduser()))
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/%s)" % OUT_DIR)
    ap.add_argument("--baseline", default="",
                    help="#520's frozen units.json, for the yield estimate and --history's session paths "
                         "(default <vault>/.mnemo/prevented-repeats/units.json)")
    ap.add_argument("--rater", action="append", default=[])
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force", action="store_true", help="send before the due condition holds")
    ap.add_argument("--workers", type=int, default=bv.WORKERS)
    ap.add_argument("--pause", type=float, default=bv.PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--history", action="store_true",
                    help="re-judge #535's cached full vs without pairs on the grounded question")
    args = ap.parse_args(argv)
    raters = args.rater or list(bv.RATERS)
    now = time.time() if now is None else now

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    if args.history:
        return history_main(args, cfg, vault, out, raters)
    pr_out, bv_out = out / "prevented-repeats", out / "broad-value"

    # the log's fresh rows, kept before the hook rotates them away
    live_rows = fresh_rows(iter_rotated_rows(vault / ".mnemo" / "reflex-log.jsonl"))
    archive = merge_rows(read_rows(out / ROWS_NAME), live_rows)
    write_rows(out / ROWS_NAME, archive)

    # what the full format delivered so far, no model call
    sessions = mpr.collect_sessions(Path(args.projects), vault, LIVE)
    events: Dict[str, List[dict]] = {}

    def load(sid: str) -> List[dict]:
        if sid not in events:
            events[sid] = _load_jsonl_events(Path(sessions[sid]["path"]))
        return events[sid]

    emissions = {"n": 0, "whole": 0, "cut": 0}
    with_full = 0
    for sid in sorted(sessions):
        pairs: Dict[str, bool] = {}
        for e in full_emissions(load(sid)):
            pairs[e["slug"]] = pairs.get(e["slug"], True) and e["whole"]
        with_full += bool(pairs)
        emissions["n"] += len(pairs)
        emissions["whole"] += sum(pairs.values())
        emissions["cut"] += sum(not w for w in pairs.values())
    human_rows = sum(1 for r in archive if r.get("session_id") in sessions)

    # step 1, #520 on the fresh sessions (sends only when the date holds)
    state = due(now, None)
    send_rate = args.send and (state["date_ok"] or args.force)
    if args.send and not send_rate:
        print("refusing to send: the check is due from %s, %.1f days from now (--force sends anyway)"
              % (state["date"], (state["date_at"] - now) / 86400), file=sys.stderr)
    if send_rate:
        pr_out.mkdir(parents=True, exist_ok=True)
        _quiet(mpr.main, ["--since", LIVE, "--until", _iso(now - SETTLE_HOURS * 3600), "--projects", args.projects,
                          "--claude-home", args.claude_home, "--vault", str(vault), "--out", str(pr_out),
                          "--send", "--workers", str(args.workers), "--pause", str(args.pause)]
               + (["--limit", str(args.limit)] if args.limit else [])
               + [x for r in raters for x in ("--rater", r)], pr_out / "report.txt")

    # the fresh units, from #520's cache when there is one
    source = mrc._read(pr_out / mpr.UNITS_NAME, None)
    data: Dict[str, Any] = {"live": LIVE, "sessions": len(sessions), "sessions_with_full": with_full,
                            "emissions": emissions,
                            "rows": {"log": len(live_rows), "archive": len(archive), "human": human_rows}}
    units: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    rated: List[str] = []
    if source is not None:
        rated = [s for s in source["rated"] if s in sessions]
        both = [r for r in source["columns"].get(mpr.BOTH) or [] if r["session_id"] in sessions]
        cands = [r for r in both if r.get("new") and r.get("reflex") and r.get("judged", True)]
        sources: Dict[str, int] = {}
        for r in sorted(cands, key=lambda r: r["session_id"]):
            project = sessions[r["session_id"]]["project"]
            ctx = bv.place(r, load(r["session_id"]), project)
            reading = unit_reading(r, ctx, project, archive) if ctx else None
            if reading is None:
                continue
            rows.append(r)
            units.append({"id": bv.unit_id(r), "session_id": r["session_id"], "slug": r["slug"],
                          "whole": reading["whole"], "source": reading["source"]})
            sources[reading["source"]] = sources.get(reading["source"], 0) + 1
            if reading["agrees"] is False:
                sources["log and block disagree"] = sources.get("log and block disagree", 0) + 1
        data.update(rated=len(rated), units=len(units), sources=sources,
                    provisional=sum(1 for r in both if r.get("new") and r.get("reflex")
                                    and not r.get("judged", True)))
    state = due(now, len(units) if source is not None else None)
    data["due"] = state

    # when both conditions should hold
    if source is not None and units:
        last = max(sessions[s]["start"] for s in rated)
        span = max((last - (mrc.epoch(LIVE) or 0.0)) / 86400, 1.0)
        data["projection"] = {"date": projected(now, len(units) / span),
                              "how": "%d units over the first %.1f days" % (len(units), span)}
    elif emissions["n"]:
        base_file = Path(args.baseline).expanduser() if args.baseline else (
            vault / ".mnemo" / "prevented-repeats" / mpr.UNITS_NAME)
        hy = historical_yield(mrc._read(base_file, None), _load_jsonl_events)
        if hy and hy["per_pair"]:
            per_day = emissions["n"] / max(state["days_in"], 1.0 / 24) * hy["per_pair"]
            data["projection"] = {"date": projected(now, per_day), "how": "estimated: %d emissions so far "
                                  "x #520's reflex yield before the change (%d units of %d emissions)"
                                  % (emissions["n"], hy["units"], hy["pairs"])}
        else:
            data["projection"] = {"date": None, "how": "no baseline yield to estimate from"}
    else:
        data["projection"] = {"date": None, "how": "no full-format emission in a human session yet"}

    # step 2, #527 on the fresh units
    if source is not None and units:
        mrc._write(out / FRESH_UNITS_NAME, {
            "since": LIVE, "rated": sorted(rated),
            "sessions": {s: source["sessions"][s] for s in sorted(rated)},
            "columns": {mpr.BOTH: rows}})
        send_arms = args.send and (state["due"] or args.force)
        if args.send and send_rate and not send_arms:
            print("refusing to answer and judge: %d fresh unit(s), the check needs %d (--force sends anyway)"
                  % (len(units), MIN_UNITS), file=sys.stderr)
        bv_out.mkdir(parents=True, exist_ok=True)
        _quiet(bv.main, ["--vault", str(vault), "--units", str(out / FRESH_UNITS_NAME), "--out", str(bv_out),
                         "--workers", str(args.workers), "--pause", str(args.pause)]
               + (["--send"] if send_arms else []) + (["--limit", str(args.limit)] if args.limit else [])
               + [x for r in raters for x in ("--rater", r)], bv_out / "report.txt")
        arms = mrc._read(bv_out / "arms.json", {})
        all_verdicts = mrc._read(bv_out / "verdicts.json", {})
        preference = {r: all_verdicts.get(mrc.column(r, bv.JUDGE_SYSTEM), {}) for r in raters}

        # step 3, the grounded judge on the same replies: what each unit's
        # session did after its prompt, frozen the first time it is read
        g_out = out / GROUNDED_DIR
        g_out.mkdir(parents=True, exist_ok=True)
        work = mrc._read(g_out / "work.json", {})
        for u in units:
            a = arms.get(u["id"])
            if a and a.get("measurable") and u["id"] not in work:
                work[u["id"]] = work_after(load(u["session_id"]), a["i"])
        mrc._write(g_out / "work.json", work)
        answers = mrc._read(bv_out / "answers.json", {}).get(mrc.column("session-model", rl.ARM_SYSTEM), {})
        pairs = [(arms[u["id"]], answers.get(u["id"], {}).get(bv.WITH, []),
                  answers.get(u["id"], {}).get(bv.WITHOUT, [])) for u in units if u["id"] in work]
        grounded = judge_grounded(pairs, work, g_out, raters, send_opts(args, cfg) if send_arms else None)
        data["results"] = both_readings(units, rated, arms, grounded, preference, raters)

    mrc._write(out / "report.json", data)
    if args.json:
        print(json.dumps(data, indent=1))
        return 0
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
