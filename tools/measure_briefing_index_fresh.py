"""Index, last or none at session start: the pre-registered fresh check of #551

Usage:
    PYTHONPATH=src python3 tools/measure_briefing_index_fresh.py            # dry: fresh sessions, units, when it is due
    PYTHONPATH=src python3 tools/measure_briefing_index_fresh.py --send     # once due: build, answer, judge, report
        [--force] [--live ISO] [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_briefing_index_fresh.py --json     # the report as data

#548 (``measure_briefing_index``) measured an index of the ten newest
briefings' TL;DRs against no briefing and got ``h_index`` +0.109 [+0.005,
+0.218]: inconclusive against its own bar of +0.15. Split by whether the
newest briefing was the right one, the index matched or beat today's
``[last-briefing]`` in both strata, so on 2026-09-29 the maintainer replaced
the newest briefing with the index (#551, ``core/briefing_index.py``). This is
the check that confirms or reverses that decision. PR #551 fixed it before any
data existed:

- **When:** at least :data:`MIN_DAYS` days of sessions with the change, and
  :data:`MIN_UNITS` fresh units, whichever comes later. The change is live
  from the first human session whose SessionStart carried the index (or
  ``--live``). Before both hold the tool **refuses to send** unless
  ``--force``; a dry run prints how many fresh sessions and units exist and
  when the condition should hold.
- **Units:** human sessions (#517's definition, job scratch left out) that
  started on ``startup`` or ``clear`` at least :data:`SETTLE_HOURS` ago, whose
  recorded SessionStart envelope carried the ``[recent-briefings]`` index and
  no ``[last-briefing]``, with a typed first prompt (#540's
  ``first_context``). One unit per session.
- **Arms,** rebuilt from each unit's recorded envelope; only the briefing
  slot varies:

  - ``index``: the envelope as it was recorded, the index as shipped;
  - ``last``: the index's slot holding the newest full briefing of the
    project at the session's start (by mtime, the file written before the
    session's first event), framed and cut to #533's cap by the hook's own
    ``_fit_briefing``, as ``briefings.sessionStart: "last"`` would have;
  - ``none``: the envelope without the index.

  Each is answered :data:`SAMPLES` times on the session's own model with
  #434/#527's harness, at the first typed prompt (#540's ``arm_prompt``).
- **Judge:** #540's grounded judge (``JUDGE_SYSTEM``) and its two blind
  raters, who never see a briefing and do see what the session went on to do
  (#534's view). ``index`` is compared with ``last`` and with ``none``, the
  ``k``-th sample with the ``k``-th; reply order seeded per comparison and
  flipped for the second rater; both framing lines and every briefing id of
  the pool masked. Primary reading: both raters agree, a disagreement is a
  tie, and a reply is *wrong* (a wrong assumption about the state of the
  work) when both mark it.
- **Readings,** bootstrap CIs over sessions, all reported whatever their sign:

  - **index vs last, the decision:** ``P(index better) − P(last better)``.
    *Confirmed* if its CI lower bound > 0; *reversed* if its CI upper bound
    < 0, and then the default goes back to ``last``; else inconclusive;
  - **index vs none, #548's bar:** ``h_index`` >= +0.15 with CI lower bound
    > 0 helps; CI upper bound < +0.15 does not measurably help;
  - wrong-state rates per arm, in each comparison;
  - a rater kappa under 0.40 reads no verdict from either, as in #540 and #548.

The report also says how many recorded indexes the vault still rebuilds byte
for byte (``briefing_index.fit`` over the pool by mtime): where one does not,
a briefing file was rewritten after the session or deleted since, and the
``last`` arm stands on the same pool.

Only ``--send`` calls a model. Calls are paced and threaded
(``measure_prevented_repeats.Sender``) from a scratch cwd, and every answer is
cached under ``--out`` (default ``<vault>/.mnemo/briefing-index-fresh``) the
moment it arrives, so a rerun resumes. A unit's arms are frozen in
``arms.json`` once built.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mbi = _sibling("measure_briefing_index")
bvl = mbi.bvl
bp = mbi.bp
bv = mbi.bv
mpr = mbi.mpr
mrc = mbi.mrc
rl = mbi.rl
mja = bp.mja
mpt = bvl.mpt
from mnemo.core import briefing_index as bix  # noqa: E402

RATERS = bvl.RATERS
SAMPLES = bvl.SAMPLES
JUDGE_SYSTEM = bvl.JUDGE_SYSTEM
JUDGE_TOKENS = bvl.JUDGE_TOKENS
OUT_DIR = "briefing-index-fresh"
PAUSE_SECONDS = bvl.PAUSE_SECONDS
WORKERS = bvl.WORKERS

#: The due condition, pre-registered in PR #551.
MIN_DAYS = 14
MIN_UNITS = 100
#: A session younger than this may still be running: what it did next is not in yet.
SETTLE_HOURS = 24
#: No session before this day can carry the index (#551 was written on it).
FLOOR = "2026-09-29T00:00:00Z"

INDEX, LAST, NONE, TIE = "index", "last", bvl.NONE, bvl.TIE
ARMS = (INDEX, LAST, NONE)
#: What the index is compared with: the decision first.
CONTROLS = (LAST, NONE)
BOTH = bvl.BOTH
HELP_BAR = bvl.HELP_BAR
KAPPA_BAR = bvl.KAPPA_BAR

_INDEX_OPEN = "[recent-briefings"


# --- the recorded envelope -------------------------------------------------------------------

def split_index(text: str) -> Tuple[str, str]:
    """(``text`` without its ``[recent-briefings]`` block, the block as the hook
    appended it — with its blank-line separator — or '')."""
    i = text.find(_INDEX_OPEN)
    if i < 0:
        return text, ""
    j = text.find(bix.INDEX_CLOSE, i)
    end = j + len(bix.INDEX_CLOSE) if j >= 0 else len(text)
    head = text[:i].rstrip("\n")
    return (head + text[end:]).strip("\n") if head else text[end:].strip("\n"), "\n\n" + text[i:end]


def envelope_parts(session_start: Sequence[str], read_file: Any = None) -> Dict[str, Any]:
    """The recorded envelopes without the index, which one carried it, the
    index, where the texts were read, and whether any carried a ``[last-briefing]``.

    A persisted envelope is read from the file Claude Code saved it to, else
    from its preview (#540's ``envelope_heads``). A session whose hook fired
    twice keeps both texts; the index goes back into the one that carried it.
    """
    heads: List[str] = []
    host, block, how, last = -1, "", "inline", False
    for text in session_start:
        if mpt.PERSISTED in text:
            got = mpt.measure_text(text, read_file)
            if got["how"] == "saved":
                text, how = got["text"], "saved"
            else:
                text = re.sub(r"</?persisted-output>|Output too large[^\n]*\n|Preview \(first [^)]*\):\n", "", text)
                text, how = text.strip().rstrip(".").rstrip(), "preview"
        last = last or "[last-briefing" in text
        head, got_block = split_index(text)
        if got_block and host < 0:
            host, block = len(heads), got_block
        heads.append(head)
    return {"heads": heads, "host": host, "index": block, "how": how, "last": last}


def with_slot(head: str, slot: str) -> str:
    """``head`` with a briefing block appended the way the hook appends it."""
    return head + slot if head else slot.lstrip("\n")


# --- the pool --------------------------------------------------------------------------------

def pool_at(briefings: Sequence[Dict[str, Any]], sid: str, start: float) -> List[Dict[str, Any]]:
    """The project's briefings written before ``start``, newest by mtime first
    (``briefing_select.recent_briefings``' order, and #534's)."""
    before = [b for b in briefings if b["mtime"] < start and b["id"] != sid]
    return sorted(before, key=lambda b: (b["mtime"], b.get("date") or "", b["id"]), reverse=True)


def rebuilt_index(pool: Sequence[Dict[str, Any]], head: str) -> str:
    """The index the hook would build today from ``pool`` next to ``head``."""
    from mnemo.hooks import session_start as ss

    recs = [SimpleNamespace(frontmatter={"date": b.get("date") or ""}, body=b["body"]) for b in pool[:bix.POOL]]
    block, _, _ = bix.fit(recs, ss.ENVELOPE_MAX_BYTES - ss._utf8_len(head))
    return block


# --- the unit --------------------------------------------------------------------------------

def later_work(walked: Dict[str, Any], events: List[dict]) -> Dict[str, Any]:
    """What the session did after its first prompt, in #534's view."""
    prompts, short = mja.typed_prompts(walked.get("prompts") or [])
    unit = {"first_prompt": mpr._head(bp.first_prompt(walked.get("prompts") or []), 2000),
            "prompts": [mpr._head(t, mja.BRIEFING_PROMPT_CHARS) for t in prompts[:mja.BRIEFING_PROMPTS]],
            "more_prompts": max(0, len(prompts) - mja.BRIEFING_PROMPTS), "short_replies": short,
            "final": mpr._tail(mja.last_agent_text(events), mja.BRIEFING_FINAL_CHARS)}
    return bvl.later_work(unit)


def build_arms(sid: str, project: str, ctx: Dict[str, Any], parts: Dict[str, Any],
               pool: Sequence[Dict[str, Any]], work: Dict[str, Any]) -> Dict[str, Any]:
    """The unit's three arm prompts and what the judge needs."""
    native = mpr._head("\n\n".join("Contents of %s:\n\n%s" % (p, t) for p, t in ctx["native"]), bv.NATIVE_CHARS)
    now = [t for owner, t in ctx["reflex"] if owner == ctx["i"]]
    previous = rl.tail(ctx["answered"])
    envs = list(parts["heads"]) or [""]
    h = max(parts["host"], 0)
    head = envs[h]
    newest = pool[0] if pool else None
    last_text, trimmed = head, False
    if newest is not None:
        meta = {"session_id": newest["id"], "date": newest.get("date") or "",
                "duration_minutes": newest.get("duration_minutes") or "0"}
        last_text, trimmed = bvl.envelope(head, newest["body"], meta, newest["id"], newest.get("path") or "")
    slots = {INDEX: with_slot(head, parts["index"]), LAST: last_text, NONE: head}

    def prompt(arm: str) -> str:
        e = list(envs)
        e[h] = slots[arm]
        return bv.arm_prompt(ctx["text"], previous, native, {"ss": [x for x in e if x], "earlier": [], "mcp": [],
                                                             "now": now})
    index_bytes = mbi._utf8(parts["index"].lstrip("\n"))
    return {"id": sid, "project": project, "i": ctx["i"], "model": ctx["model"] or bvl.FALLBACK_MODEL,
            "model_from": "transcript" if ctx["model"] else "fallback", "prompt": ctx["text"], "work": work,
            "envelope_from": parts["how"], "ids": [b["id"] for b in pool[:bix.POOL]],
            "last": newest["id"] if newest else None, "last_trimmed": trimmed,
            "entries": parts["index"].count("\n### "), "index_bytes": index_bytes,
            "last_bytes": mbi._utf8(last_text) - mbi._utf8(head),
            "index_rebuilt": rebuilt_index(pool, head) == parts["index"],
            "prompts": {arm: prompt(arm) for arm in ARMS}}


# --- the judge -------------------------------------------------------------------------------

def comparison_prompt(arms: Dict[str, Any], answers: Dict[str, List[Dict[str, Any]]], control: str, k: int,
                      rater_index: int) -> Optional[str]:
    """Reply ``k`` of the index against reply ``k`` of ``control``, as #540's judge sees a pair."""
    if any(len(answers.get(a, [])) <= k for a in (INDEX, control)):
        return None
    ids = list(arms["ids"])
    t = mbi.mask(answers[INDEX][k]["text"], ids)
    c = mbi.mask(answers[control][k]["text"], ids)
    pair = (t, c) if bvl.treatment_first(mbi.comparison_id(arms["id"], control, k), rater_index) else (c, t)
    return bvl.judge_prompt(arms, pair)


# --- the numbers -----------------------------------------------------------------------------

def decision(st: Dict[str, Any], k: Optional[float]) -> str:
    """The pre-registered reading of index vs last."""
    if k is None or not st.get("n"):
        return "no estimate yet"
    if k < KAPPA_BAR:
        return "no verdict (ruler too weak)"
    lo, hi = st["h_ci"]
    if lo > 0:
        return "confirmed: the index is better than the last briefing"
    if hi < 0:
        return "reversed: the last briefing is better; revert briefings.sessionStart to \"last\""
    return "inconclusive: neither confirmed nor reversed"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def due(now: float, live: Optional[float], units: int, days: int = MIN_DAYS,
        min_units: int = MIN_UNITS) -> Dict[str, Any]:
    """The due condition: ``days`` since ``live`` and ``min_units`` units."""
    if live is None:
        return {"live": None, "date": None, "date_ok": False, "units": units, "units_ok": units >= min_units,
                "due": False, "days_in": 0.0}
    date = live + days * 86400
    return {"live": _iso(live), "date": _iso(date), "date_ok": now >= date, "units": units,
            "units_ok": units >= min_units, "due": now >= date and units >= min_units,
            "days_in": (now - live) / 86400}


def projected(now: float, live: Optional[float], units: int, days: int = MIN_DAYS,
              min_units: int = MIN_UNITS) -> Optional[str]:
    """When both conditions should hold, at the rate units arrived so far."""
    if live is None or units <= 0 or now <= live:
        return None
    per_day = units / ((now - live) / 86400)
    return _iso(max(live + days * 86400, live + min_units / per_day * 86400))


# --- report ----------------------------------------------------------------------------------

def report_lines(data: Dict[str, Any]) -> List[str]:
    d, c = data["due"], data["counts"]
    lines = ["live since %s (%s); %d human session(s) since carried the index, %d of them unit(s)"
             % (d["live"] or "never: no session has carried the index yet", data["live_from"], c["carried"],
                c["units"]),
             "  left out: " + ", ".join("%s %d" % kv for kv in sorted(c["left_out"].items())),
             "due when %d days have passed (%s: %s) and %d units exist (%s): %s"
             % (MIN_DAYS, d["date"] or "n/a", "reached" if d["date_ok"] else "not yet", MIN_UNITS,
                "reached" if d["units_ok"] else "not yet", "DUE" if d["due"] else "NOT DUE")]
    if data.get("projected"):
        lines.append("  at the rate so far, due about %s" % data["projected"])
    if c["built"]:
        lines.append("%d unit(s) built; the vault rebuilds the recorded index byte for byte in %d; the last arm's "
                     "briefing trimmed to #533's cap in %d" % (c["built"], c["rebuilt"], c["last_trimmed"]))
        ib, lb = data["size"]["index_bytes"], data["size"]["last_bytes"]
        if ib.get("n"):
            lines.append("index %s bytes median (max %d), last %s median (max %d)"
                         % (ib["median"], ib["max"], lb.get("median", 0), lb.get("max", 0)))
        lines.append("answer models: " + ", ".join("%s %d" % kv for kv in sorted(data["models"].items())))
    for col, res in data["results"].items():
        lines += ["", "%s%s:" % (col, "  <- the verdict" if col == BOTH else "")]
        for control, name in ((LAST, "vs last"), (NONE, "h_index")):
            st = res[control]
            if not st.get("n"):
                lines.append("  %-8s no estimate yet" % name)
                continue
            lines.append("  %-8s %s over %d sessions (index better %.1f%%, %s better %.1f%%, tie %.1f%%)"
                         % (name, bvl._ci(st, "h"), st["n"], 100 * st["arm"], control, 100 * st["control"],
                            100 * st["tie"]))
            lines.append("  %-8s wrong-state replies: index %s, %s %s; difference %s"
                         % ("", bvl._ci(st, "wrong_arm", True), control, bvl._ci(st, "wrong_control", True),
                            bvl._ci(st, "wrong_diff")))
    k = data["kappa"]
    if k.get("n"):
        lines += ["", "rater agreement: 'which reply' %d of %d the same, kappa %s; wrong-state marks kappa %s over %d"
                  % (k["same"], k["n"], "n/a" if k["kappa"] is None else "%.2f" % k["kappa"],
                     "n/a" if k["wrong_kappa"] is None else "%.2f" % k["wrong_kappa"], k["wrong_n"])]
    v = data["verdict"]
    lines += ["", "PRE-REGISTERED (PR #551): index vs last confirmed if CI lower > 0, reversed if CI upper < 0; "
              "index vs none helps if h_index >= +%.2f and CI lower > 0; kappa < %.2f reads nothing"
              % (HELP_BAR, KAPPA_BAR),
              "  index vs last: %s" % v["decision"], "  index vs none: %s" % v["index"]]
    cost = data.get("cost") or {}
    if cost:
        lines += ["", "notional cost of every call on file: " + ", ".join(
            "%s $%.2f (%d calls)" % (m, x["usd"], x["calls"]) for m, x in sorted(cost.items()))
            + " — subscription usage, not money"]
    return lines


# --- driver ----------------------------------------------------------------------------------

def run(argv: Optional[List[str]] = None, provider: Any = None, now: Optional[float] = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.briefing import _load_jsonl_events

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/%s)" % OUT_DIR)
    ap.add_argument("--live", default="", help="when the change went live (ISO); default: the first session "
                                               "whose SessionStart carried the index")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force", action="store_true", help="send before the due condition holds")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = list(RATERS)
    now = time.time() if now is None else now

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    home_claude = str(Path(args.claude_home).expanduser().resolve())

    # the fresh sessions, no model call
    sessions = mpr.collect_sessions(Path(args.projects).expanduser(), vault, args.live or FLOOR)
    left: Dict[str, int] = {"not_startup": 0, "also_last": 0, "settling": 0, "no_typed": 0}
    carried: List[Tuple[str, Dict[str, Any], List[dict], Dict[str, Any]]] = []
    for sid in sorted(sessions):
        m = sessions[sid]
        if (m.get("cwd") or "").startswith(home_claude):
            continue
        events = _load_jsonl_events(Path(m["path"]))
        walked = mpr.walk(events)
        parts = envelope_parts(walked.get("session_start") or [])
        if not parts["index"]:
            continue
        carried.append((sid, m, events, dict(parts, walked=walked)))
    live = mrc.epoch(args.live) if args.live else (min(m["start"] for _, m, _, _ in carried) if carried else None)

    arms_file = mrc._read(out / "arms.json", {})
    briefings = None
    units: List[str] = []
    for sid, m, events, parts in carried:
        if live is not None and m["start"] < live:
            continue
        if sid in arms_file:
            units.append(sid)
            continue
        if bp.session_facts(events)["source"] not in ("", "startup", "clear"):
            left["not_startup"] += 1
            continue
        if parts["last"]:
            left["also_last"] += 1
            continue
        if m["start"] > now - SETTLE_HOURS * 3600:
            left["settling"] += 1
            continue
        ctx = bvl.first_context(events)
        if ctx is None:
            left["no_typed"] += 1
            continue
        if briefings is None:
            briefings = bp.load_briefings(vault, {p.stem: p for p in Path(args.projects).expanduser().glob("*/*.jsonl")})
            metas = bvl.briefing_metas(vault)
            for group in briefings.values():
                for b in group:
                    meta = metas.get(b["id"]) or {}
                    b["path"] = meta.get("path") or ""
                    b["duration_minutes"] = (meta.get("meta") or {}).get("duration_minutes") or "0"
        pool = pool_at(briefings.get(m["project"], []), sid, m["start"])
        arms_file[sid] = build_arms(sid, m["project"], ctx, parts, pool, later_work(parts["walked"], events))
        units.append(sid)
    mrc._write(out / "arms.json", arms_file)
    units = sorted(set(units))

    all_answers = mrc._read(out / "answers.json", {})
    answers = all_answers.setdefault(mrc.column("session-model", rl.ARM_SYSTEM), {})
    all_verdicts = mrc._read(out / "verdicts.json", {})
    verdicts_ = {r: all_verdicts.setdefault(mrc.column(r, JUDGE_SYSTEM), {}) for r in raters}

    def answer_todo() -> List[Tuple[Dict[str, Any], str]]:
        return [(arms_file[u], arm) for k in range(SAMPLES) for u in units for arm in ARMS
                if len(answers.get(u, {}).get(arm, [])) <= k]

    def judge_todo(r: str) -> List[Tuple[str, str, int, str]]:
        ri = raters.index(r)
        todo = []
        for u in units:
            for control in CONTROLS:
                for k in range(SAMPLES):
                    if mbi.comparison_id(u, control, k) in verdicts_[r]:
                        continue
                    p = comparison_prompt(arms_file[u], answers.get(u, {}), control, k, ri)
                    if p is not None:
                        todo.append((u, control, k, p))
        return todo

    state = due(now, live, len(units))
    sender = None
    if args.send and not (state["due"] or args.force):
        print("refusing to send: %d of %d units, due from %s (--force sends anyway)"
              % (len(units), MIN_UNITS, state["date"] or "the first session that carries the index"),
              file=sys.stderr)
    elif args.send:
        if provider is None:
            provider = llm.resolve(cfg)
        timeout = max(int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180), 600)
        sender = mpr.Sender(provider, timeout, out / "calls.jsonl", args.workers, args.pause)
        here = os.getcwd()
        scratch = tempfile.mkdtemp(prefix="mnemo-briefing-index-fresh-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches an arm or a rater
        try:
            def on_answer(a: Dict[str, Any], arm: str) -> Callable[[str], None]:
                def take(text: str) -> None:
                    if text.strip():
                        answers.setdefault(a["id"], {}).setdefault(arm, []).append({"text": text, "model": a["model"]})
                        mrc._write(out / "answers.json", all_answers)
                return take
            todo = answer_todo()
            todo = todo[:args.limit] if args.limit else todo
            sender.run([(a["model"], a["prompts"][arm], rl.ARM_SYSTEM, on_answer(a, arm)) for a, arm in todo])

            def on_judge(r: str, uid: str, control: str, k: int) -> Callable[[str], None]:
                cid = mbi.comparison_id(uid, control, k)

                def take(text: str) -> None:
                    got = mbi.parse_judge(text, control, cid, raters.index(r))
                    if got is not None:
                        verdicts_[r][cid] = got
                        mrc._write(out / "verdicts.json", all_verdicts)
                return take
            if not sender.stopped:
                calls = [(r, p, JUDGE_SYSTEM, on_judge(r, uid, control, k))
                         for r in raters for uid, control, k, p in judge_todo(r)]
                sender.run(calls[:args.limit] if args.limit else calls)
        finally:
            os.chdir(here)
            shutil.rmtree(scratch, ignore_errors=True)

    # the numbers
    results = {col: {c: mbi.pair_stats(verdicts_, units, c, raters if col == BOTH else [col]) for c in CONTROLS}
               for col in [BOTH] + raters}
    cids = [mbi.comparison_id(u, c, k) for u in units for c in CONTROLS for k in range(SAMPLES)]
    kp = mbi.kappa(verdicts_, cids, raters)
    models: Dict[str, int] = {}
    for u in units:
        models[arms_file[u]["model"]] = models.get(arms_file[u]["model"], 0) + 1
    data = {
        "due": state, "projected": projected(now, live, len(units)),
        "live_from": "--live" if args.live else "first session carrying the index",
        "counts": {"carried": len(carried), "units": len(units), "left_out": left, "built": len(units),
                   "rebuilt": sum(1 for u in units if arms_file[u]["index_rebuilt"]),
                   "last_trimmed": sum(1 for u in units if arms_file[u]["last_trimmed"])},
        "size": {"index_bytes": mbi.sizes([arms_file[u]["index_bytes"] for u in units]),
                 "last_bytes": mbi.sizes([arms_file[u]["last_bytes"] for u in units])},
        "models": models, "results": results, "kappa": kp,
        "verdict": {"decision": decision(results[BOTH][LAST], kp.get("kappa")),
                    "index": mbi.verdict(results[BOTH][NONE], kp.get("kappa"))["index"]},
        "cost": bv.spent(out / "calls.jsonl"),
    }

    pend = answer_todo()
    by_model: Dict[str, List[Tuple[str, str]]] = {}
    for a, arm in pend:
        by_model.setdefault(a["model"], []).append((a["prompts"][arm], rl.ARM_SYSTEM))
    for mdl, ps in sorted(by_model.items()):
        print("%s: pending %d answer call(s); notional ~$%.2f" % (mdl, len(ps), bv.notional(mdl, ps, bv.ANSWER_TOKENS)),
              file=sys.stderr)
    for r in raters:
        jt = judge_todo(r)
        print("%s: pending %d judge call(s); notional ~$%.2f"
              % (r, len(jt), bv.notional(r, [(p, JUDGE_SYSTEM) for _, _, _, p in jt], JUDGE_TOKENS)), file=sys.stderr)
    if sender is not None:
        print("spent $%.2f notional this run (subscription usage, not money)" % sender.usd, file=sys.stderr)
    data["pending"] = {"answers": len(pend), **{r: len(judge_todo(r)) for r in raters}}
    mrc._write(out / "report.json", data)
    if args.json:
        print(json.dumps(data, indent=1, sort_keys=True))
    else:
        print("\n".join(report_lines(data)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
