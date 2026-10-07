"""An index of the ten newest briefings' TL;DRs instead of one briefing: does it help without picking? (#548)

Usage:
    PYTHONPATH=src python3 tools/measure_briefing_index.py              # build the arm, pending, cost; report
    PYTHONPATH=src python3 tools/measure_briefing_index.py --send       # answer the arm, judge, report
        [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_briefing_index.py --json       # the report as data

#540 (``measure_briefing_value``) found that the *right* briefing helps a
session pick up its work; #534 found that choosing it is the hard part, but
that when a right one exists it is always among the ten newest. So one way
needs no choice at all: hand the session an index of the ten newest
briefings, each one's date and its ``## TL;DR`` section. This measures that
index against #540's own arms.

**Units** are #540's (``<vault>/.mnemo/briefing-value``: ``arms.json``, its
frozen prompts, answers and verdicts). Nothing of #540's cache is written.

**Arm ``index``** is #540's arm with the ``[last-briefing]`` slot replaced by
:func:`index_block`: the briefings #534 found at the session's start ranked
among the :data:`POOL` newest by mtime (``candidates`` with ``rank`` <
:data:`POOL`), newest first, each as its date and its TL;DR
(:func:`tldr`). The whole envelope fits #533's cap
(``session_start.ENVELOPE_MAX_BYTES``); when it would not, the TL;DRs share
the room fairly (:func:`fair_cut`: each gets an equal share, and what a short
one leaves goes to the longer ones), cut on a word. Everything else is
rebuilt from the transcript by #540's own functions; the rebuilt ``none``
arm is checked against #540's frozen one (``none_rebuilt``), and a unit
whose rebuild differs is left out rather than compared unfairly. Answered
:data:`SAMPLES` times on the session's own model with #434/#527's harness.

**Grounded judge,** #540's (``JUDGE_SYSTEM``, the same two blind raters,
who never see a briefing): ``index`` against #540's cached ``none``, and
``index`` against #540's cached ``right``, the ``k``-th sample with the
``k``-th. Both framing lines and every briefing id in the index are masked in
the replies. Primary reading: both raters agree; a disagreement is a tie, and
a reply is *wrong* when both mark it.

**Metrics,** bootstrap CIs over sessions: ``h_index = P(index better) −
P(none better)``; the paired ``index`` vs ``right`` reading, ``P(index
better) − P(right better)``; ``h_right`` from #540's verdicts on the same
units, and the share of it the index keeps (``h_index / h_right``, paired in
each resample); wrong-state rates per arm; kappa; the index's size in bytes.

**The bar, set before measuring** (:func:`verdict`): *the index helps* —
``h_index`` >= +0.15 and CI lower bound > 0, #540's bar for the right
briefing; *does not measurably help* — CI upper bound < +0.15; kappa < 0.40
reads nothing. ``index`` vs ``right`` is descriptive.

**Result, 2026-09-29, the maintainer's machine: inconclusive. The index helps
a little, measurably less than the right briefing, and it plants no wrong
assumptions.** All 110 of #540's units rebuilt their ``none`` arm byte for
byte, so all 110 are compared. The right briefing is in the index in every
one. The index has 10 entries in 107 units and 2 in 3. It is a median 4,623
bytes (p90 6,298, max 6,441), and the whole envelope a median 5,461 and max
7,781 against the 9,000 cap, so no TL;DR was cut. The ruler holds: kappa 0.56
on "which reply" (0.57 index vs none, 0.55 index vs right), 0.65 on the
wrong-state marks. Both raters:

- ``h_index`` **+0.109 [+0.005, +0.218]** (index better 34.5%, none 23.6%,
  tie 41.8%). The CI clears 0 but the point estimate is under +0.15 and the
  upper bound is over it: **inconclusive** by the bar. Each rater alone gives
  +0.118 (Opus 5.5) and +0.132 (Fable 5.1);
- ``index`` vs ``right`` **−0.118 [−0.223, −0.018]** (index better 24.5%,
  right 36.4%, tie 39.1%). The right briefing beats the index. On the same
  units #540's ``h_right`` is +0.218 [+0.105, +0.327], and the index keeps
  **50% [3%, 108%]** of it;
- the help sits where the newest briefing was the right one: +0.212
  [+0.013, +0.400] over 40 units, against +0.050 [−0.071, +0.171] over the
  70 where it was not;
- wrong-state replies: index 5.9%, none 6.8% (−0.009 [−0.045, +0.032]); index
  5.0%, right 6.8%. Nine other TL;DRs next to the right one do not plant the
  wrong assumptions a wrong full briefing did in #540 (5.0% → 10.0%).

The notional cost of every call on file was about $114: Fable 5.1 $64, Opus
5.5 $37, Opus 5 $13. That is subscription usage, not money.

Only ``--send`` calls a model. Calls are paced and threaded
(``measure_prevented_repeats.Sender``) from a scratch cwd, and every answer is
cached under ``--out`` (default ``<vault>/.mnemo/briefing-index``) the moment
it arrives, so a rerun resumes. A unit's index arm is frozen in
``arms.json`` once built.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bvl = _sibling("measure_briefing_value")
from mnemo.core import briefing_index as bix  # noqa: E402
bp = bvl.bp
bv = bvl.bv
mpr = bvl.mpr
mrc = bvl.mrc
rl = bvl.rl

RATERS = bvl.RATERS
SAMPLES = bvl.SAMPLES
JUDGE_SYSTEM = bvl.JUDGE_SYSTEM
JUDGE_TOKENS = bvl.JUDGE_TOKENS
OUT_DIR = "briefing-index"
VALUE_DIR = bvl.OUT_DIR
PICK_DIR = bvl.PICK_DIR
PAUSE_SECONDS = bvl.PAUSE_SECONDS
WORKERS = bvl.WORKERS
#: How many of the newest briefings the index lists (#534's pool).
POOL = bp.POOL_SIZE

INDEX, NONE, RIGHT, TIE = "index", bvl.NONE, bvl.RIGHT, bvl.TIE
#: What the index is compared with.
CONTROLS = (NONE, RIGHT)
BOTH = bvl.BOTH

HELP_BAR = bvl.HELP_BAR
KAPPA_BAR = bvl.KAPPA_BAR

# The index itself lives in ``mnemo.core.briefing_index``, which the SessionStart
# hook builds it with since #551: one function, so what ships is what this measured.
INDEX_OPEN = bix.INDEX_OPEN
INDEX_CLOSE = bix.INDEX_CLOSE
_INDEX_FRAMING = bix.INDEX_FRAMING
ELLIPSIS = bix.ELLIPSIS
_utf8 = bix._utf8
tldr = bix.tldr
cut_to = bix.cut_to
fair_cut = bix.fair_cut
entry_head = bix.entry_head
index_block = bix.index_block


# --- the index -----------------------------------------------------------------------------

def pool_of(unit: Dict[str, Any], pool: int = POOL) -> List[Dict[str, Any]]:
    """The briefings among the ``pool`` newest at the session's start, newest first."""
    return sorted((c for c in unit["candidates"] if int(c["rank"]) < pool), key=lambda c: int(c["rank"]))


def build_index_arm(unit: Dict[str, Any], ctx: Dict[str, Any], metas: Dict[str, Dict[str, Any]],
                    read_file: Any = None) -> Dict[str, Any]:
    """The unit's ``index`` prompt and, built the same way, its ``none``
    prompt, which must match #540's frozen one for the comparison to be fair."""
    from mnemo.hooks import session_start as ss

    heads, host, env_from = bvl.envelope_heads(ctx["session_start"], read_file)
    native = mpr._head("\n\n".join("Contents of %s:\n\n%s" % (p, t) for p, t in ctx["native"]), bv.NATIVE_CHARS)
    now = [t for owner, t in ctx["reflex"] if owner == ctx["i"]]
    previous = rl.tail(ctx["answered"])
    pool = pool_of(unit)
    entries = [((metas.get(c["id"]) or {}).get("meta") or {}, tldr(c["body"])) for c in pool]
    envs = list(heads) or [""]
    h = max(host, 0)
    head = envs[h]
    block, cut = index_block(entries, ss.ENVELOPE_MAX_BYTES - _utf8(head))

    def prompt(slot: str) -> str:
        e = list(envs)
        e[h] = head + slot if head else slot.lstrip("\n")
        return bv.arm_prompt(ctx["text"], previous, native, {"ss": [x for x in e if x], "earlier": [], "mcp": [],
                                                             "now": now})
    return {"id": unit["id"], "ids": [c["id"] for c in pool], "envelope_from": env_from,
            "index_bytes": _utf8(block.lstrip("\n")), "envelope_bytes": _utf8(head + block),
            "cut": cut, "entries": len(pool), "prompt": prompt(block), "none": prompt("")}


# --- the judge ---------------------------------------------------------------------------

def comparison_id(uid: str, control: str, k: int) -> str:
    return "%s|%s-vs-%s|%d" % (uid, INDEX, control, k)


def mask(text: str, ids: Sequence[str]) -> str:
    """A reply without either framing line or any briefing id it could name."""
    return bvl.mask(_INDEX_FRAMING.sub(bvl.MASK, text), ids)


def comparison_prompt(value_arms: Dict[str, Any], index_arm: Dict[str, Any], index_answers: Sequence[Dict[str, Any]],
                      value_answers: Dict[str, List[Dict[str, Any]]], control: str, k: int,
                      rater_index: int) -> Optional[str]:
    """Reply ``k`` of the index against reply ``k`` of ``control``, as #540's judge sees a pair."""
    if len(index_answers) <= k or len(value_answers.get(control, [])) <= k:
        return None
    ids = list(index_arm["ids"]) + [x for x in value_arms["briefing"].values() if x]
    t = mask(index_answers[k]["text"], ids)
    c = mask(value_answers[control][k]["text"], ids)
    pair = (t, c) if bvl.treatment_first(comparison_id(value_arms["id"], control, k), rater_index) else (c, t)
    return bvl.judge_prompt(value_arms, pair)


def parse_judge(text: str, control: str, cid: str, rater_index: int) -> Optional[Dict[str, Any]]:
    """``{"better": index|control|tie, "wrong": {index: bool, control: bool}, "why"}``, or None."""
    got = bvl.parse_judge(text, INDEX, cid, rater_index)
    if got is None:
        return None
    rename = {NONE: control}
    return {"better": rename.get(got["better"], got["better"]),
            "wrong": {rename.get(a, a): w for a, w in got["wrong"].items()}, "why": got["why"]}


# --- the numbers -------------------------------------------------------------------------

def unit_scores(verdicts: Dict[str, Dict[str, Dict[str, Any]]], uid: str, control: str, raters: Sequence[str],
                samples: int = SAMPLES) -> Optional[Dict[str, float]]:
    """Over the unit's comparisons of the index with ``control``: ``h``, the
    share each way, and each side's wrong-state rate. None until all are judged."""
    got = {"h": 0.0, "arm": 0.0, "control": 0.0, "tie": 0.0, "wrong_arm": 0.0, "wrong_control": 0.0}
    for k in range(samples):
        v = bvl.agreed([verdicts.get(r, {}).get(comparison_id(uid, control, k)) for r in raters])
        if v is None:
            return None
        got[{INDEX: "arm", control: "control", TIE: "tie"}[v["better"]]] += 1.0 / samples
        got["wrong_arm"] += float(v["wrong"][INDEX]) / samples
        got["wrong_control"] += float(v["wrong"][control]) / samples
    got["h"] = got["arm"] - got["control"]
    got["wrong_diff"] = got["wrong_arm"] - got["wrong_control"]
    return got


KEYS = ("h", "arm", "control", "tie", "wrong_arm", "wrong_control", "wrong_diff")


def pair_stats(verdicts: Dict[str, Dict[str, Dict[str, Any]]], uids: Sequence[str], control: str,
               raters: Sequence[str]) -> Dict[str, Any]:
    rows = [s for s in (unit_scores(verdicts, u, control, raters) for u in uids) if s is not None]
    return bvl.bootstrap(rows, KEYS)


def kept(verdicts: Dict[str, Dict[str, Dict[str, Any]]], value_verdicts: Dict[str, Dict[str, Dict[str, Any]]],
         uids: Sequence[str], raters: Sequence[str], n_boot: int = bvl.BOOTSTRAP,
         seed: int = bvl.SEED) -> Dict[str, Any]:
    """``h_right`` (#540's verdicts) and ``h_index`` on the same units, and the
    share of the right briefing's help the index keeps, resampled in pairs."""
    import random

    rows = []
    for u in uids:
        a = unit_scores(verdicts, u, NONE, raters)
        r = bvl.unit_scores(value_verdicts, u, RIGHT, raters)
        if a is not None and r is not None:
            rows.append((a["h"], r["h"]))
    n = len(rows)
    if not n:
        return {"n": 0}
    rng = random.Random(seed)
    ratios = []
    for _ in range(n_boot):
        pick = [rows[rng.randrange(n)] for _ in range(n)]
        den = sum(r for _, r in pick)
        if den > 0:
            ratios.append(sum(a for a, _ in pick) / den)
    ratios.sort()
    h_index, h_right = sum(a for a, _ in rows) / n, sum(r for _, r in rows) / n
    return {"n": n, "h_index": h_index, "h_right": h_right,
            "h_right_ci": bvl.bootstrap([{"h": r} for _, r in rows], ["h"], n_boot, seed)["h_ci"],
            "share": h_index / h_right if h_right > 0 else None,
            "share_ci": [bvl._pct(ratios, 0.025), bvl._pct(ratios, 0.975)] if ratios else None}


def kappa(verdicts: Dict[str, Dict[str, Dict[str, Any]]], cids: Sequence[str],
          raters: Sequence[str]) -> Dict[str, Any]:
    """The two raters' agreement on the three-way question and on the wrong-state marks."""
    a, b, wa, wb = [], [], [], []
    for cid in cids:
        va, vb = (verdicts.get(r, {}).get(cid) for r in raters)
        if not va or not vb:
            continue
        a.append(va["better"] if va["better"] in (INDEX, TIE) else "control")
        b.append(vb["better"] if vb["better"] in (INDEX, TIE) else "control")
        for x in va["wrong"]:
            wa.append(str(va["wrong"][x]))
            wb.append(str(vb["wrong"][x]))
    return {"n": len(a), "same": sum(x == y for x, y in zip(a, b)), "kappa": bv._kappa3(a, b),
            "wrong_n": len(wa), "wrong_kappa": bv._kappa3(wa, wb)}


def verdict(st: Dict[str, Any], k: Optional[float]) -> Dict[str, str]:
    """The pre-set bar on ``h_index``. A kappa under :data:`KAPPA_BAR` reads nothing."""
    if k is None or not st.get("n"):
        return {"ruler": "no estimate yet", "index": "no estimate yet"}
    if k < KAPPA_BAR:
        return {"ruler": "too weak", "index": "no verdict (ruler too weak)"}
    lo, hi = st["h_ci"]
    if st["h"] >= HELP_BAR and lo > 0:
        v = "the index helps"
    elif hi < HELP_BAR:
        v = "the index does not measurably help"
    else:
        v = "inconclusive"
    return {"ruler": "ok", "index": v}


def sizes(values: Sequence[int]) -> Dict[str, Any]:
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {"n": len(s), "median": s[len(s) // 2], "p90": s[min(len(s) - 1, int(0.9 * len(s)))], "max": s[-1],
            "min": s[0]}


# --- report --------------------------------------------------------------------------------

def report_lines(data: Dict[str, Any]) -> List[str]:
    c = data["counts"]
    ib, eb = data["size"]["index_bytes"], data["size"]["envelope_bytes"]
    lines = ["%d #540 units; %d built, %d with the none arm rebuilt as #540 froze it (%d left out); "
             "the right briefing is in the index in %d" % (c["units"], c["built"], c["fair"], c["unfair"],
                                                          c["right_in_index"]),
             "index entries: " + ", ".join("%s %d" % kv for kv in sorted(data["entries"].items())),
             "answer models: " + ", ".join("%s %d" % kv for kv in sorted(data["models"].items()))]
    if ib.get("n"):
        lines.append("index size: median %d bytes, p90 %d, max %d; whole envelope median %d, max %d (cap %d); "
                     "TL;DRs cut to fit in %d units" % (ib["median"], ib["p90"], ib["max"], eb["median"], eb["max"],
                                                        data["size"]["cap"], c["cut_units"]))
    for col, res in data["results"].items():
        lines += ["", "%s%s:" % (col, "  <- the verdict" if col == BOTH else "")]
        for control, name in ((NONE, "h_index"), (RIGHT, "vs right")):
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
    kp = data["kept"]
    if kp.get("n"):
        share = "n/a" if kp["share"] is None else "%.0f%%" % (100 * kp["share"])
        ci = kp.get("share_ci")
        lines += ["", "#540's h_right on the same %d units: %+.3f [%+.3f, %+.3f]; the index keeps %s of it%s"
                  % (kp["n"], kp["h_right"], kp["h_right_ci"][0], kp["h_right_ci"][1], share,
                     " [%.0f%%, %.0f%%]" % (100 * ci[0], 100 * ci[1]) if ci else "")]
    for key, title in (("kappa", "both comparisons"), ("kappa_none", "index vs none"),
                       ("kappa_right", "index vs right")):
        k = data[key]
        if k.get("n"):
            lines.append("rater agreement (%s): 'which reply' %d of %d the same, kappa %s; wrong-state marks "
                         "kappa %s over %d" % (title, k["same"], k["n"],
                                               "n/a" if k["kappa"] is None else "%.2f" % k["kappa"],
                                               "n/a" if k["wrong_kappa"] is None else "%.2f" % k["wrong_kappa"],
                                               k["wrong_n"]))
    for title, groups in (data.get("breakdowns") or {}).items():
        lines += ["", "h_index by %s (both raters):" % title]
        for name, st in groups.items():
            lines.append("  %-22s %s over %d" % (name, bvl._ci(st, "h"), st.get("n", 0)))
    v = data["verdict"]
    lines += ["", "BAR (set before measuring): the index helps if h_index >= +%.2f and CI lower > 0; does not "
              "measurably help if CI upper < +%.2f; kappa < %.2f reads nothing; index vs right is descriptive"
              % (HELP_BAR, HELP_BAR, KAPPA_BAR), "  ruler: %s" % v["ruler"], "  index: %s" % v["index"]]
    cost = data.get("cost") or {}
    if cost:
        lines += ["", "notional cost of every call on file: " + ", ".join(
            "%s $%.2f (%d calls)" % (m, x["usd"], x["calls"]) for m, x in sorted(cost.items()))
            + " — subscription usage, not money"]
    return lines


# --- driver --------------------------------------------------------------------------------

def run(argv: Optional[List[str]] = None, provider: Any = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.hooks import session_start as ss

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--pick", default="", help="#534's cache (default <vault>/.mnemo/briefing-pick)")
    ap.add_argument("--value", default="", help="#540's cache (default <vault>/.mnemo/briefing-value)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/briefing-index)")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = list(RATERS)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    pick = Path(args.pick).expanduser() if args.pick else vault / ".mnemo" / PICK_DIR
    value = Path(args.value).expanduser() if args.value else vault / ".mnemo" / VALUE_DIR
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    value_arms = bp._read(value / "arms.json", {})
    if not value_arms:
        raise SystemExit("error: no %s; run tools/measure_briefing_value.py first" % (value / "arms.json"))
    out.mkdir(parents=True, exist_ok=True)
    units = {u["id"]: u for u in bp._read(pick / "units.json", {}).get("units", [])}
    value_answers = bp._read(value / "answers.json", {}).get(mrc.column("session-model", rl.ARM_SYSTEM), {})
    all_value_verdicts = bp._read(value / "verdicts.json", {})
    value_verdicts = {r: all_value_verdicts.get(mrc.column(r, JUDGE_SYSTEM), {}) for r in raters}

    arms_file = bp._read(out / "arms.json", {})
    transcripts = None
    metas = None
    for uid in sorted(value_arms):
        if uid in arms_file or uid not in units:
            continue
        if transcripts is None:
            transcripts = {p.stem: p for p in Path(args.projects).expanduser().glob("*/*.jsonl")}
            metas = bvl.briefing_metas(vault)
        path = transcripts.get(uid)
        ctx = bvl.first_context(_load_jsonl_events(path)) if path else None
        if ctx is not None:
            arm = build_index_arm(units[uid], ctx, metas or {})
            arm["fair"] = arm.pop("none") == value_arms[uid]["prompts"].get(NONE)
            arms_file[uid] = arm
    bp._write(out / "arms.json", arms_file)
    fair = sorted(u for u, a in arms_file.items() if a["fair"])

    all_answers = bp._read(out / "answers.json", {})
    answers = all_answers.setdefault(mrc.column("session-model", rl.ARM_SYSTEM), {})
    all_verdicts = bp._read(out / "verdicts.json", {})
    verdicts_ = {r: all_verdicts.setdefault(mrc.column(r, JUDGE_SYSTEM), {}) for r in raters}

    def answer_todo() -> List[str]:
        return [uid for k in range(SAMPLES) for uid in fair if len(answers.get(uid, [])) <= k]

    def judge_todo(r: str) -> List[Tuple[str, str, int, str]]:
        ri = raters.index(r)
        todo = []
        for uid in fair:
            for control in CONTROLS:
                for k in range(SAMPLES):
                    if comparison_id(uid, control, k) in verdicts_[r]:
                        continue
                    p = comparison_prompt(value_arms[uid], arms_file[uid], answers.get(uid, []),
                                          value_answers.get(uid, {}), control, k, ri)
                    if p is not None:
                        todo.append((uid, control, k, p))
        return todo

    sender = None
    if args.send:
        if provider is None:
            provider = llm.resolve(cfg)
        timeout = max(int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180), 600)
        sender = mpr.Sender(provider, timeout, out / "calls.jsonl", args.workers, args.pause)
        here = os.getcwd()
        scratch = tempfile.mkdtemp(prefix="mnemo-briefing-index-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches an arm or a rater
        try:
            def on_answer(uid: str, model: str) -> Callable[[str], None]:
                def take(text: str) -> None:
                    if text.strip():
                        answers.setdefault(uid, []).append({"text": text, "model": model})
                        bp._write(out / "answers.json", all_answers)
                return take
            todo = answer_todo()
            todo = todo[:args.limit] if args.limit else todo
            sender.run([(value_arms[u]["model"], arms_file[u]["prompt"], rl.ARM_SYSTEM,
                         on_answer(u, value_arms[u]["model"])) for u in todo])

            def on_judge(r: str, uid: str, control: str, k: int) -> Callable[[str], None]:
                cid = comparison_id(uid, control, k)

                def take(text: str) -> None:
                    got = parse_judge(text, control, cid, raters.index(r))
                    if got is not None:
                        verdicts_[r][cid] = got
                        bp._write(out / "verdicts.json", all_verdicts)
                return take
            if not sender.stopped:
                calls = [(r, p, JUDGE_SYSTEM, on_judge(r, uid, control, k))
                         for r in raters for uid, control, k, p in judge_todo(r)]
                sender.run(calls[:args.limit] if args.limit else calls)
        finally:
            os.chdir(here)
            shutil.rmtree(scratch, ignore_errors=True)

    # the numbers
    results = {col: {c: pair_stats(verdicts_, fair, c, raters if col == BOTH else [col]) for c in CONTROLS}
               for col in [BOTH] + raters}
    cids = {c: [comparison_id(u, c, k) for u in fair for k in range(SAMPLES)] for c in CONTROLS}

    def count(key: Callable[[str], str]) -> Dict[str, int]:
        got: Dict[str, int] = {}
        for u in fair:
            got[key(u)] = got.get(key(u), 0) + 1
        return got

    def by(key: Callable[[str], str]) -> Dict[str, Any]:
        groups: Dict[str, List[str]] = {}
        for u in fair:
            groups.setdefault(key(u), []).append(u)
        return {name: pair_stats(verdicts_, g, NONE, raters) for name, g in sorted(groups.items())}

    kp = kappa(verdicts_, cids[NONE] + cids[RIGHT], raters)
    data = {
        "counts": {"units": len(value_arms), "built": len(arms_file), "fair": len(fair),
                   "unfair": len(arms_file) - len(fair),
                   "right_in_index": sum(1 for u in fair if value_arms[u]["briefing"][RIGHT] in arms_file[u]["ids"]),
                   "cut_units": sum(1 for u in fair if arms_file[u]["cut"])},
        "entries": count(lambda u: "%d entries" % arms_file[u]["entries"]),
        "models": count(lambda u: value_arms[u]["model"]),
        "size": {"cap": ss.ENVELOPE_MAX_BYTES, "index_bytes": sizes([arms_file[u]["index_bytes"] for u in fair]),
                 "envelope_bytes": sizes([arms_file[u]["envelope_bytes"] for u in fair])},
        "results": results, "kappa": kp, "kappa_none": kappa(verdicts_, cids[NONE], raters),
        "kappa_right": kappa(verdicts_, cids[RIGHT], raters),
        "kept": kept(verdicts_, value_verdicts, fair, raters),
        "verdict": verdict(results[BOTH][NONE], kp.get("kappa")),
        "breakdowns": {"answer model": by(lambda u: value_arms[u]["model"]),
                       "the hook's pick": by(lambda u: "was wrong" if value_arms[u]["briefing"].get(bvl.NEWEST)
                                             else "was right"),
                       "TL;DRs cut": by(lambda u: "cut" if arms_file[u]["cut"] else "whole")},
        "cost": bv.spent(out / "calls.jsonl"),
    }

    pend = answer_todo()
    by_model: Dict[str, List[Tuple[str, str]]] = {}
    for u in pend:
        by_model.setdefault(value_arms[u]["model"], []).append((arms_file[u]["prompt"], rl.ARM_SYSTEM))
    for m, ps in sorted(by_model.items()):
        print("%s: pending %d answer call(s); notional ~$%.2f" % (m, len(ps), bv.notional(m, ps, bv.ANSWER_TOKENS)),
              file=sys.stderr)
    for r in raters:
        jt = judge_todo(r)
        print("%s: pending %d judge call(s); notional ~$%.2f"
              % (r, len(jt), bv.notional(r, [(p, JUDGE_SYSTEM) for _, _, _, p in jt], JUDGE_TOKENS)), file=sys.stderr)
    if sender is not None:
        print("spent $%.2f notional this run (subscription usage, not money)" % sender.usd, file=sys.stderr)
    data["pending"] = {"answers": len(pend), **{r: len(judge_todo(r)) for r in raters}}
    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    bp._write(out / "report.json", _provenance.stamp(data, prov))
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1, sort_keys=True))
    else:
        print(_provenance.line(prov))
        print("\n".join(report_lines(data)))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    return run(argv)


if __name__ == "__main__":
    sys.exit(main())
