"""Can a model pick the right briefing, or none, from the first prompt? Haiku and Sonnet against #534's labels (#547).

Usage:
    PYTHONPATH=src python3 tools/measure_briefing_judge_pick.py                 # build the inputs, pending, cost; report
    PYTHONPATH=src python3 tools/measure_briefing_judge_pick.py --send          # dev half: every prompt variant, both models
    PYTHONPATH=src python3 tools/measure_briefing_judge_pick.py --lock          # choose each model's variant on dev, lock
    PYTHONPATH=src python3 tools/measure_briefing_judge_pick.py --score-test    # test half, locked variant only, scored once
    PYTHONPATH=src python3 tools/measure_briefing_judge_pick.py --json          # the report, from the cache

#534 (PR #539) found a right briefing in 110 of its 227 labelled sessions,
always among the ten newest on dev, but no deterministic signal or lexical
gate that finds it: its best policy scored 58.3% precision and 14.0% coverage
on test. #540 (PR #544) then showed that a right briefing helps. This asks
whether a model, shown what a first-prompt hook would have, can do the picking.

**Population and truth.** #534's units and labels, read from its cache
(``<vault>/.mnemo/briefing-pick``, :data:`SOURCE_DIR`), unchanged: a candidate
is right when both of its raters listed it, and the dev/test split is its
:func:`measure_briefing_pick.is_dev`.

**The picker's input** (:func:`picker_prompt`) is what UserPromptSubmit has
on the session's first prompt: the developer's first typed message (#534
keeps its head, 2,000 characters), and the :data:`POOL_SIZE` newest candidate
briefings by mtime at session start, each with the date in its frontmatter (and the
session's own date), its ``## TL;DR`` (head :data:`TLDR_CHARS`) and, when that block is
:data:`STATE_CHARS` characters or shorter, its ``## State at end of session``.
The whole note is capped at :data:`NOTE_CHARS`. The notes are shuffled under
letters, seeded per session (:func:`letters_for`), as #534's raters had them.
The picker never sees the rest of the session.

**The picker's output** is one letter or ``none`` (:func:`parse_pick`). A
reply that is neither counts as ``none`` — a hook would inject nothing — and
is counted in the report.

**Models.** :data:`MODELS`, ``claude-haiku-4-5`` (the runtime candidate) and
``claude-sonnet-5``, called through :func:`mnemo.core.llm.resolve`, the path
a hook would take, from a scratch cwd so no project CLAUDE.md or auto-memory
reaches them. Every call is timed on the wall clock around the provider call
(``claude --print`` start to exit, what a hook would wait) and its
``duration_api_ms`` kept; its notional cost is what the CLI reports.

**Protocol.** ``--send`` answers the dev half with every wording in
:data:`VARIANTS`. ``--lock`` reads the dev half only and locks, per model, the
wording with the most dev coverage at a dev precision of at least
:data:`PRECISION_BAR` (ties to the one that injects less; none qualifying, the
most precise), into ``lock.json``. ``--score-test`` then sends the test half
with each model's locked wording only and, once every test session has an
answer, scores it into ``test.json``; neither file is ever rewritten. **The
bar, the same as #534's, set before measuring, on test:** precision >= 80%
(an injected briefing is one both raters called right) and coverage >= 60%
(of the sessions with a right candidate, the picker names one).

**State on 2026-09-28, the maintainer's machine: the bar fails for both
models.** #534's 227 units (dev 123, test 104; a right note in 60 dev and 50
test sessions, every dev one among the ten newest). Five wordings on dev:

- *Haiku* names a note in 0 to 9 of 123 dev sessions whatever the wording,
  right 3 times at best (``base``: 33.3% precision, 5.0% coverage). Locked
  as the most precise, on test it named 2 notes, **both wrong (0.0%
  precision, 0.0% coverage)**. It is not a picker at this input;
- *Sonnet* is precise but covers under half: ``base`` 82.1% / 38.3% on dev,
  ``lenient`` 80.6% / 41.7% (locked, the only one at the bar with more
  coverage), ``recent`` 72.2% / 43.3%. **On test, locked: 17 right of 24
  injected, 70.8% precision [51%, 85%], 34.0% coverage [22%, 48%].**

Coverage has a ceiling that no wording reached: of the 32 dev sessions
``lenient`` left without a note while a right one existed, most open with
nothing to match against: "go ahead", a screenshot, "look at the open
issue", a pasted table or a client's message. The raters had the whole
session; the first prompt does not say which work it continues. Next to
today's hook on the same test sessions, Sonnet hands the right note about as
often (17 vs 16 of 50) but a wrong one in 7 sessions instead of 88. That
reading was not the bar and does not pass it.

Price per session, measured on the test half with one call at a time: Haiku
2.3 s median, 4.2 s p90, $0.0039 notional; Sonnet 2.8 s median, 4.7 s p90,
$0.0187 notional (the API's own share is a 0.7 s / 1.3 s median; the rest is
``claude --print`` starting up). The whole measurement spent $16.80 notional over 1,438 calls.

Only ``--send`` and ``--score-test`` leave the machine: calls are paced, and
every answer is cached under ``--out`` (default
``<vault>/.mnemo/briefing-judge-pick``) as it arrives, so a rerun resumes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mbp = _sibling("measure_briefing_pick")
mpr = mbp.mpr
mrc = mbp.mrc

OUT_DIR = "briefing-judge-pick"
SOURCE_DIR = mbp.OUT_DIR
SEED = 547
MODELS = ("claude-haiku-4-5", "claude-sonnet-5")
POOL_SIZE = mbp.POOL_SIZE
#: Per-note caps. On #534's 354 distinct pool briefings the TL;DR is at most
#: 767 characters in 99% (median 418), the state block at most 576 in 75%
#: (median 445): so every TL;DR fits whole and three state blocks in four do.
TLDR_CHARS = 800
STATE_CHARS = 600
NOTE_CHARS = 1400
PRECISION_BAR = mbp.PRECISION_BAR
COVERAGE_BAR = mbp.COVERAGE_BAR
WORKERS = 2
PAUSE_SECONDS = 1.0
TIMEOUT = 180
DEV, TEST = mbp.DEV, mbp.TEST

_TASK = """\
A developer has just started a new AI coding session and typed the first message shown below. \
Earlier sessions in the same project each left a note: its TL;DR, and sometimes the state the \
work was left in. The notes are under letters, in no particular order, each with the date it was \
written.

One note may be pasted into the new session before the agent answers. It should be a note ABOUT \
the work this session is starting: the same task, issue, branch, component or line of work, so \
that an agent that read it first would be better placed for what follows. Sharing only the \
project, or general background about it, is not enough. Often no note is about it. A wrong note \
is worse than none: it makes the agent assume a wrong state of the work.
"""
_REPLY = """
Reply with one token only: the letter of that note, or none."""

#: The wordings the dev half chooses among, by name. The name, not the text,
#: is what ``lock.json`` records; the answers are filed under the text's hash
#: (:func:`measure_repeated_corrections.column`), so editing one re-asks it.
VARIANTS: Dict[str, str] = {
    "base": _TASK + _REPLY,
    "strict": _TASK + """
Name a note only when the first message clearly continues it: it names the same issue, PR, \
branch, file, feature or problem, or it refers back to earlier work that the note describes. \
When you are unsure, reply none.
""" + _REPLY,
    "lenient": _TASK + """
Name a note whenever the first message plausibly continues the work it describes, even if the \
message does not name it; when several do, prefer the most recent. Reply none only when the \
message starts something no note covers.
""" + _REPLY,
}
#: Added after the first dev pass, still on dev only: Haiku named a note in
#: under one session in ten with any of the three above, so ``neutral`` drops
#: the warnings and states the dev base rate instead (a right note in 60 of 123
#: dev sessions); and Sonnet's misses were mostly continuations with nothing to
#: match ("go ahead", a screenshot, "look at the open issue"), so ``recent``
#: sends those to the newest note on that line of work.
VARIANTS["neutral"] = _TASK.split(" Often no note is about it.")[0] + """ About half of new sessions \
continue the work one of these notes describes; the other half start something none of them covers.
""" + _REPLY
VARIANTS["recent"] = VARIANTS["lenient"].replace(_REPLY, """
When the first message continues earlier work without naming it (it says to go ahead or carry on, \
answers a question, pastes an image, a log or a table, or points at "the issue" or "the PR"), name \
the most recent note whose work it could continue.
""" + _REPLY)
VARIANT_ORDER = ("base", "strict", "lenient", "neutral", "recent")


# --- small helpers -------------------------------------------------------------------------

_read, _write = mbp._read, mbp._write


def _quantile(xs: Sequence[float], q: float) -> Optional[float]:
    """Nearest-rank quantile, None for no data."""
    if not xs:
        return None
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(-(-q * len(s) // 1)) - 1))
    return s[k]


def section(body: str, name: str) -> Optional[str]:
    """The text under ``## <name>`` up to the next ``## `` heading, or None."""
    m = re.search(r"^## " + re.escape(name) + r"[ \t]*$(.*?)(?=^## |\Z)", body or "", re.M | re.S)
    return m.group(1).strip() if m else None


def excerpt(body: str) -> str:
    """What the picker reads of one briefing: its TL;DR, and its state block when short."""
    tldr = section(body, "TL;DR")
    if tldr is None:  # an older shape: the body without its title line
        tldr = re.sub(r"\A#[^\n]*\n", "", (body or "").strip()).strip()
    parts = ["TL;DR: " + mpr._head(tldr, TLDR_CHARS)]
    state = section(body, "State at end of session")
    if state and len(state) <= STATE_CHARS:
        parts.append("State at end of session:\n" + state)
    return mpr._head("\n\n".join(parts), NOTE_CHARS)


# --- the picker's view ---------------------------------------------------------------------

def briefing_dates(vault: Path) -> Dict[str, str]:
    """``session id -> the date in its frontmatter`` for every briefing in the vault.

    Not the mtime: on #534's pools a third of the files were touched a day or
    more after the session they record, so an mtime age would contradict the
    date the note carries."""
    from mnemo.core.extract.scanner import parse_frontmatter

    out: Dict[str, str] = {}
    for md in sorted((vault / "bots").glob("*/briefings/sessions/*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _ = parse_frontmatter(text)
        out[str(fm.get("session_id") or md.stem)] = str(fm.get("date") or "")
    return out


def build_inputs(source_units: Sequence[Dict[str, Any]], dates: Dict[str, str]) -> List[Dict[str, Any]]:
    """Per #534 unit, what a first-prompt hook would have: the first typed
    message and the :data:`POOL_SIZE` newest candidates, each excerpted."""
    out = []
    for u in source_units:
        pool = sorted((c for c in u["candidates"] if c["rank"] < POOL_SIZE), key=lambda c: c["rank"])
        notes = []
        for c in pool:
            notes.append({"id": c["id"], "rank": c["rank"], "date": dates.get(c["id"], ""), "text": excerpt(c["body"])})
        # local, as the briefing writer dates its notes
        day = datetime.fromtimestamp(u["start"]).date().isoformat() if u.get("start") else ""
        out.append({"id": u["id"], "part": mbp.part_of(u["id"]), "date": day,
                    "first_prompt": u.get("first_prompt") or "", "notes": notes})
    return out


_LETTERS = mbp._LETTERS


def letters_for(unit: Dict[str, Any]) -> List[str]:
    """Note ids in the order the picker sees them: a seeded shuffle, so
    neither recency nor the hook's pick sits in a fixed place."""
    ids = [n["id"] for n in unit["notes"]]
    random.Random("%d:%s" % (SEED, unit["id"])).shuffle(ids)
    return ids[:len(_LETTERS)]


def picker_prompt(unit: Dict[str, Any]) -> str:
    notes = {n["id"]: n for n in unit["notes"]}
    head = "## The developer's first message" + (" (today is %s)" % unit["date"] if unit.get("date") else "")
    parts = [head, unit["first_prompt"].strip() or "(empty)", "", "## Notes from earlier sessions"]
    for letter, nid in zip(_LETTERS, letters_for(unit)):
        n = notes[nid]
        parts += ["", "### Note %s%s" % (letter, " (written %s)" % n["date"] if n.get("date") else ""), n["text"]]
    return "\n".join(parts)


_LETTER = re.compile(r"^(?:note\s+)?\(?([a-z])\)?(?:$|[.:,;)]|\s+[-\u2013\u2014(])", re.I)
_NONE = re.compile(r"^(?:none|null|no note)\b", re.I)


def parse_pick(text: str, order: Sequence[str]) -> Tuple[Optional[str], bool]:
    """``(note id or None, understood)`` from a reply's first line: a letter,
    or none. A model that explains itself after the answer is still read, the
    way a hook would read it; a first line that is neither is not."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    s = lines[0].strip("`*_\"'# ") if lines else ""
    if s.startswith("{"):
        from mnemo.core import llm

        try:
            payload = llm._parse_llm_json(text)
        except Exception:
            return None, False
        s = str(payload.get("pick") if isinstance(payload, dict) else "").strip()
    if _NONE.match(s):
        return None, True
    m = _LETTER.match(s)
    if m:
        idx = _LETTERS.find(m.group(1).upper())
        if 0 <= idx < len(order):
            return order[idx], True
    return None, False


# --- scoring -------------------------------------------------------------------------------

def load_truth(source: Path, units: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """#534's right sets, from its ``labels.json``: both raters listed it."""
    all_labels = _read(source / "labels.json", {})
    labels = {r: all_labels.get(mrc.column(r, mbp.SYSTEM), {}) for r in mbp.RATERS}
    return mbp.truth_of(units, labels)


def answered(units: Sequence[Dict[str, Any]], col: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [u for u in units if u["id"] in col]


def score_column(units: Sequence[Dict[str, Any]], truth: Dict[str, Any], col: Dict[str, Any]) -> Dict[str, Any]:
    """#534's precision and coverage over the units ``col`` answered, plus
    how many replies the parser did not understand. Each pick is read again
    from the kept reply, so a parser fix needs no new call."""
    got = answered(units, col)
    read = {u["id"]: parse_pick(col[u["id"]].get("reply") or "", letters_for(u)) for u in got}
    s = mbp.score(got, truth, lambda u: read[u["id"]][0])
    s["answered"] = len(got)
    s["unparsed"] = sum(1 for u in got if not read[u["id"]][1])
    s["said_none_with_right"] = sum(1 for u in got if truth.get(u["id"]) and read[u["id"]][0] is None)
    return s


def choose(scores: Dict[str, Dict[str, Any]]) -> Tuple[str, bool]:
    """The variant with the most coverage at precision >= the bar, ties to
    the one that injects less; none qualifying, the most precise."""
    best: Optional[Tuple[Tuple, str]] = None
    fallback: Optional[Tuple[Tuple, str]] = None
    for name in VARIANT_ORDER:
        s = scores.get(name)
        if not s:
            continue
        prec, cov = s["precision"], s["coverage"] or 0.0
        if prec is not None and prec >= PRECISION_BAR:
            key = (cov, -s["injected"])
            if best is None or key > best[0]:
                best = (key, name)
        key2 = (prec or 0.0, cov)
        if fallback is None or key2 > fallback[0]:
            fallback = (key2, name)
    chosen = best or fallback
    assert chosen is not None
    return chosen[1], best is not None


def verdict(s: Dict[str, Any]) -> Tuple[bool, str]:
    return mbp.verdict(s)


def timing(answers: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Latency per call (wall and API, median and p90) and notional USD per call."""
    wall = [float(a["wall_s"]) for a in answers if a.get("wall_s") is not None]
    api = [float(a["api_ms"]) / 1000.0 for a in answers if a.get("api_ms") is not None]
    usd = [float(a["usd"]) for a in answers if a.get("usd") is not None]
    return {"calls": len(answers), "wall_median_s": _quantile(wall, 0.5), "wall_p90_s": _quantile(wall, 0.9),
            "api_median_s": _quantile(api, 0.5), "api_p90_s": _quantile(api, 0.9),
            "usd_per_session": sum(usd) / len(usd) if usd else None, "usd_total": sum(usd),
            "workers": sorted({int(a.get("workers") or 0) for a in answers})}


# --- sending -------------------------------------------------------------------------------

def api_ms(raw: Any) -> Optional[float]:
    """``duration_api_ms`` from the CLI's result envelope, wherever it sits."""
    rows = raw.get("events") if isinstance(raw, dict) and isinstance(raw.get("events"), list) else [raw]
    for row in reversed(rows):
        if isinstance(row, dict) and isinstance(row.get("duration_api_ms"), (int, float)):
            return float(row["duration_api_ms"])
    return None


def timed(provider: Callable[..., Any], clock: Callable[[], float] = time.monotonic
          ) -> Tuple[Callable[..., Any], Dict[Tuple[str, str, str], float]]:
    """``provider`` wrapped so each call's wall time is kept, by (model, system, prompt)."""
    seen: Dict[Tuple[str, str, str], float] = {}
    lock = threading.Lock()

    def call(prompt: str, *, system: str, model: str, timeout: int) -> Any:
        t0 = clock()
        resp = provider(prompt, system=system, model=model, timeout=timeout)
        with lock:
            seen[(model, system, prompt)] = clock() - t0
        return resp

    return call, seen


def send(jobs: Sequence[Tuple[str, str, Dict[str, Any]]], answers: Dict[str, Dict[str, Any]], path: Path,
         provider: Any, workers: int, pause: float, timeout: int = TIMEOUT) -> float:
    """Answer ``(model, variant, unit)`` jobs, filing each reply under its
    column as it arrives. Returns the notional USD spent."""
    if not jobs:
        return 0.0
    wrapped, walls = timed(provider)
    responses: Dict[Tuple[str, str, str], Any] = {}

    def keep(prompt: str, *, system: str, model: str, timeout: int) -> Any:
        resp = wrapped(prompt, system=system, model=model, timeout=timeout)
        responses[(model, system, prompt)] = resp
        return resp

    sender = mpr.Sender(keep, timeout, path.parent / "calls.jsonl", workers, pause)

    def on_reply(model: str, variant: str, unit: Dict[str, Any], prompt: str) -> Callable[[str], None]:
        system = VARIANTS[variant]
        order = letters_for(unit)

        def take(text: str) -> None:
            key = (model, system, prompt)
            resp = responses.pop(key, None)
            pick, understood = parse_pick(text, order)
            answers.setdefault(mrc.column(model, system), {})[unit["id"]] = {
                "pick": pick, "understood": understood, "reply": (text or "")[:400],
                "wall_s": walls.pop(key, None), "api_ms": api_ms(getattr(resp, "raw", None)),
                "usd": getattr(resp, "total_cost_usd", None), "workers": workers, "part": unit["part"]}
            _write(path, answers)
        return take

    calls = []
    for model, variant, unit in jobs:
        prompt = picker_prompt(unit)
        calls.append((model, prompt, VARIANTS[variant], on_reply(model, variant, unit, prompt)))
    here = os.getcwd()
    scratch = tempfile.mkdtemp(prefix="mnemo-briefing-judge-")
    os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches the picker
    try:
        sender.run(calls)
    finally:
        os.chdir(here)
        shutil.rmtree(scratch, ignore_errors=True)
    return sender.usd


def pending(units: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, Any]],
            plan: Sequence[Tuple[str, str]]) -> List[Tuple[str, str, Dict[str, Any]]]:
    """The ``(model, variant, unit)`` calls ``plan`` still needs."""
    out = []
    for model, variant in plan:
        col = answers.get(mrc.column(model, VARIANTS[variant]), {})
        out += [(model, variant, u) for u in units if u["id"] not in col]
    return out


def estimate(jobs: Sequence[Tuple[str, str, Dict[str, Any]]]) -> float:
    prices = dict(mrc.PRICES, **{"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-5": (2.0, 10.0)})
    total = 0.0
    for model, variant, u in jobs:
        p_in, p_out = prices.get(model, (10.0, 50.0))
        tokens = (len(picker_prompt(u)) + len(VARIANTS[variant])) / 4.0 + mrc.CLI_OVERHEAD_TOKENS
        total += (tokens * p_in + 5 * p_out) / 1e6
    return total


# --- report --------------------------------------------------------------------------------

def report(inputs: Sequence[Dict[str, Any]], truth: Dict[str, Any], answers: Dict[str, Dict[str, Any]],
           lock: Dict[str, Any], test: Dict[str, Any]) -> Dict[str, Any]:
    labelled = [u for u in inputs if u["id"] in truth]
    dev = [u for u in labelled if u["part"] == DEV]
    tst = [u for u in labelled if u["part"] == TEST]
    right_in_pool = sum(1 for u in dev if truth[u["id"]] & {n["id"] for n in u["notes"]})
    data: Dict[str, Any] = {
        "units": len(inputs), "labelled": len(labelled), "dev_sessions": len(dev), "test_sessions": len(tst),
        "dev_with_right": sum(1 for u in dev if truth[u["id"]]), "dev_right_in_pool": right_in_pool,
        "caps": {"tldr": TLDR_CHARS, "state": STATE_CHARS, "note": NOTE_CHARS, "pool": POOL_SIZE},
        "note_chars_median": _quantile([len(n["text"]) for u in inputs for n in u["notes"]], 0.5),
        "prompt_chars_median": _quantile([len(picker_prompt(u)) for u in inputs], 0.5),
        "dev": {}, "lock": lock, "test": test, "timing": {},
    }
    for model in MODELS:
        data["dev"][model] = {v: score_column(dev, truth, answers.get(mrc.column(model, VARIANTS[v]), {}))
                              for v in VARIANT_ORDER}
        rows = [a for v in VARIANT_ORDER for a in answers.get(mrc.column(model, VARIANTS[v]), {}).values()]
        data["timing"][model] = timing(rows)
    return data


_p, _ci = mbp._p, mbp._ci


def _row(name: str, s: Dict[str, Any]) -> str:
    extra = " [%d replies unparsed, counted as none]" % s["unparsed"] if s.get("unparsed") else ""
    return mbp._row(name, s) + extra


def _secs(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1fs" % x


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["#534's units: %d, labelled by both raters %d (dev %d, test %d); on dev %d have a right candidate, "
             "%d of them among the %d newest the picker sees"
             % (data["units"], data["labelled"], data["dev_sessions"], data["test_sessions"],
                data["dev_with_right"], data["dev_right_in_pool"], POOL_SIZE),
             "picker input: first message + %d notes, each TL;DR <= %d chars and state block when <= %d, "
             "note <= %d (median %s chars); prompt median %s chars"
             % (POOL_SIZE, TLDR_CHARS, STATE_CHARS, NOTE_CHARS, data["note_chars_median"],
                data["prompt_chars_median"])]
    for model in MODELS:
        lines.append("dev half, %s:" % model)
        for v in VARIANT_ORDER:
            s = data["dev"][model][v]
            if s["answered"]:
                lines.append(_row(v, s))
            else:
                lines.append("  %-9s not sent" % v)
    lock = data["lock"]
    if lock:
        lines.append("lock (%s): %s" % (lock.get("locked_at", ""), ", ".join(
            "%s -> %s%s" % (m, lock["models"][m]["variant"], "" if lock["models"][m]["met_bar_on_dev"]
                            else " (none met the dev bar: the most precise)") for m in MODELS if m in lock["models"])))
    t = data["test"]
    if t:
        lines.append("TEST half, scored once at %s:" % t["scored_at"])
        for model in MODELS:
            r = t["models"].get(model)
            if r:
                lines.append(_row(model.replace("claude-", ""), r["score"]))
                lines.append("  BAR (precision >= %.0f%%, coverage >= %.0f%%): %s" % (
                    100 * PRECISION_BAR, 100 * COVERAGE_BAR, "PASS" if r["passed"] else "FAIL — " + r["verdict"]))
        base = t.get("baselines") or {}
        for name in ("newest", "latest"):
            if name in base:
                lines.append(_row(name, base[name]) + "  (#534 baseline, same test sessions)")
    for model in MODELS:
        tm = data["timing"][model]
        if tm["calls"]:
            lines.append("%s: %d calls; latency wall median %s p90 %s (API median %s p90 %s; workers %s); "
                         "notional $%.4f per session, $%.2f in all"
                         % (model, tm["calls"], _secs(tm["wall_median_s"]), _secs(tm["wall_p90_s"]),
                            _secs(tm["api_median_s"]), _secs(tm["api_p90_s"]),
                            ",".join(str(w) for w in tm["workers"]), tm["usd_per_session"] or 0.0, tm["usd_total"]))
    for model, tm in sorted(((t or {}).get("timing") or {}).items()):
        lines.append("%s on the test half: latency wall median %s p90 %s, notional $%.4f per session"
                     % (model, _secs(tm["wall_median_s"]), _secs(tm["wall_p90_s"]), tm["usd_per_session"] or 0.0))
    return lines


# --- driver --------------------------------------------------------------------------------

def _provider(provider: Any) -> Any:
    if provider is not None:
        return provider
    from mnemo.core import config, llm

    return llm.resolve(config.load_config())


def score_test(inputs: Sequence[Dict[str, Any]], truth: Dict[str, Any], answers: Dict[str, Dict[str, Any]],
               lock: Dict[str, Any], source_units: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The test half's scores, or None while a locked column still lacks an answer."""
    tst = [u for u in inputs if u["part"] == TEST and u["id"] in truth]
    models: Dict[str, Any] = {}
    timings: Dict[str, Any] = {}
    for model in MODELS:
        variant = lock["models"][model]["variant"]
        col = answers.get(mrc.column(model, VARIANTS[variant]), {})
        if any(u["id"] not in col for u in tst):
            return None
        s = score_column(tst, truth, col)
        ok, why = verdict(s)
        models[model] = {"variant": variant, "score": s, "passed": ok, "verdict": why}
        timings[model] = timing([col[u["id"]] for u in tst])
    src_test = [u for u in source_units if mbp.part_of(u["id"]) == TEST]
    baselines = {name: mbp.score(src_test, truth, f) for name, f in mbp.selectors({}).items()}
    return {"scored_at": datetime.now(timezone.utc).isoformat(), "models": models, "timing": timings,
            "baselines": baselines, "passed_any": any(m["passed"] for m in models.values())}


def main(argv: Optional[List[str]] = None, provider: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--source", default="", help="#534's cache (default <vault>/.mnemo/briefing-pick)")
    ap.add_argument("--out", default="")
    ap.add_argument("--send", action="store_true", help="answer the dev half, every variant, both models")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--lock", action="store_true", help="choose each model's variant on dev (never rewrites)")
    ap.add_argument("--score-test", action="store_true", help="answer and score the test half once, with the lock")
    ap.add_argument("--rebuild", action="store_true", help="rebuild inputs.json (answers are kept)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from mnemo.core import config, paths

    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(config.load_config())
    source = Path(args.source).expanduser() if args.source else vault / ".mnemo" / SOURCE_DIR
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    source_units = _read(source / "units.json", {}).get("units") or []
    if not source_units:
        print("no #534 units at %s: run tools/measure_briefing_pick.py first" % source, file=sys.stderr)
        return 2
    inputs_path = out / "inputs.json"
    inputs = _read(inputs_path, {}).get("units") or []
    if not inputs or args.rebuild:
        inputs = build_inputs(source_units, briefing_dates(vault))
        _write(inputs_path, {"built_at": datetime.now(timezone.utc).isoformat(), "units": inputs})
    truth = load_truth(source, source_units)
    labelled = [u for u in inputs if u["id"] in truth]
    answers_path = out / "answers.json"
    answers: Dict[str, Dict[str, Any]] = _read(answers_path, {})

    dev = [u for u in labelled if u["part"] == DEV]
    dev_jobs = pending(dev, answers, [(m, v) for m in MODELS for v in VARIANT_ORDER])
    print("dev: %d call(s) pending, notional ~$%.2f" % (len(dev_jobs), estimate(dev_jobs)), file=sys.stderr)
    if args.send and dev_jobs:
        todo = dev_jobs[:args.limit] if args.limit else dev_jobs
        usd = send(todo, answers, answers_path, _provider(provider), args.workers, args.pause)
        print("dev: spent $%.2f notional this run" % usd, file=sys.stderr)

    lock_path, test_path = out / "lock.json", out / "test.json"
    lock = _read(lock_path, {})
    if args.lock:
        if lock:
            print("lock.json exists; delete it by hand to choose again", file=sys.stderr)
        elif pending(dev, answers, [(m, v) for m in MODELS for v in VARIANT_ORDER]):
            print("the dev half is not fully answered: run --send first", file=sys.stderr)
            return 2
        else:
            models = {}
            for model in MODELS:
                scores = {v: score_column(dev, truth, answers.get(mrc.column(model, VARIANTS[v]), {}))
                          for v in VARIANT_ORDER}
                name, met = choose(scores)
                models[model] = {"variant": name, "met_bar_on_dev": met, "dev": scores[name],
                                 "system_sha": mrc.column(model, VARIANTS[name]).split("@")[1]}
            lock = {"locked_at": datetime.now(timezone.utc).isoformat(), "models": models}
            _write(lock_path, lock)

    test = _read(test_path, {})
    if args.score_test:
        if not lock:
            print("no lock: run --lock first", file=sys.stderr)
            return 2
        if test:
            print("test.json exists: the test half is scored once", file=sys.stderr)
        else:
            tst = [u for u in labelled if u["part"] == TEST]
            plan = [(m, lock["models"][m]["variant"]) for m in MODELS]
            jobs = pending(tst, answers, plan)
            print("test: %d call(s) pending, notional ~$%.2f" % (len(jobs), estimate(jobs)), file=sys.stderr)
            if jobs:
                todo = jobs[:args.limit] if args.limit else jobs
                send(todo, answers, answers_path, _provider(provider), args.workers, args.pause)
            scored = score_test(inputs, truth, answers, lock, source_units)
            if scored is None:
                print("test: answers still missing; rerun --score-test to resume", file=sys.stderr)
            else:
                test = scored
                _write(test_path, test)

    data = report(inputs, truth, answers, lock, test)
    _write(out / "report.json", data)
    if args.json:
        print(json.dumps(data, indent=1, sort_keys=True))
    else:
        print("\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
