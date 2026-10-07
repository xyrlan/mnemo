"""Does the full rule body help where the one-line reflex preview did not? (#535)

Usage:
    PYTHONPATH=src python3 tools/measure_full_body.py              # what is pending, its cost, the report so far
    PYTHONPATH=src python3 tools/measure_full_body.py --send       # build, answer, judge, report
        [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_full_body.py --json       # the report as data

``tools/measure_memory_panorama.py`` (#530) ranked why the rules mnemo
delivers do not help, and one lead was that **the agent acts on the reflex's
one-line preview and almost never reads the rule**: ``read_mnemo_rule``
followed a reflex injection in 8 of 1,336 (session, rule) pairs, and the
preview is the body cut at 300 characters (``text_utils.body_preview``).
That lead is post-hoc. This tests it with new answers on the same units,
against a bar set before measuring (:data:`BAR`):

- **full body helps** — ``h(full) − h(preview)`` ≥ +0.10 and its CI lower bound > 0;
- **full body hurts** — the CI upper bound of the difference < 0;
- **no difference** — the CI of the difference inside ±0.10;
- **inconclusive** — none of these. (Checked in that order: a CI inside
  ``[−0.10, 0)`` reads as hurts.)

**Units.** #527's reflex-channel units (``measure_broad_value``: measurable,
the reflex carried the rule, both raters judged): 82 on the maintainer's
vault. Read from #527's cache (``<vault>/.mnemo/broad-value/``), never
written to it.

**A third arm, ``full``.** #527's frozen *with* prompt, except that every
reflex line carrying the rule — ``• [[slug]]: <preview> (call
read_mnemo_rule …)`` — becomes ``• [[slug]]:`` followed by the rule's whole
body: what ``body_preview`` cuts, uncut (frontmatter, graph section and
advisories gone, newlines kept). Nothing else in the prompt changes. The
body is today's page, and the vault keeps no history: a rule rewritten since
the session (its recorded preview is not how today's body begins,
``preview_drift``) would carry text the session could not have had — a later
state, sometimes a later outcome. So, fixed before any answer was sent, the
verdict reads only the units whose rule is still the one the preview was cut
from, and the rewritten ones are reported beside it as a second reading. Answered on the unit's own recorded model,
:data:`SAMPLES` samples, #434's harness (``measure_rule_lift.ARM_SYSTEM``, no
tools, a scratch working directory), as #527 answered its arms. #527's
``with`` and ``without`` answers are reused, not regenerated.

**Judge ``full`` against ``without``** with #527's two blind raters and its
``JUDGE_SYSTEM``: the repository's ``CLAUDE.md``, the previous turn, the prompt
and two replies, never the rule, slugs and ``[[…]]`` masked; reply order
seeded per comparison (seed :data:`SEED`, so not #527's order) and flipped for
the second rater; ties allowed. Primary reading: both raters named the same
reply, else a tie.

**Numbers.** Per unit ``h = P(arm better) − P(without better)`` over its
comparisons: ``h(full)`` from this tool's verdicts, ``h(preview)`` from #527's
``with`` verdicts, on the same ``without`` replies. The difference is paired
per unit and bootstrapped over units. Split by page type, by the panorama's
text class (record: narrative, project fact; advice: procedure, preference,
generic practice), and by answering model.

Beside the verdict, the price of the fix: bytes per injection of the full
body line against the recorded preview line (median, p90; tokens ≈ bytes / 4).

First run, 2026-09-28, the maintainer's vault: 82 reflex units, every body
longer than the preview (so no unit carries the same text in both arms), 10
rules rewritten since their session, 72 read. Answered on the sessions' own
models (Opus 5.5 46, Opus 5 19, Fable 5.1 17). **Both raters: h(full) +0.215
[+0.062, +0.368] against h(preview) −0.021 [−0.160, +0.118] on the same
units; difference +0.236 [+0.090, +0.396], full higher on 30 units, preview
on 18: FULL BODY HELPS.** Of 144 full-vs-without comparisons the full body
made the reply better in 44.4%, worse in 22.9%, tied in 32.6%; the raters
agreed on 114 of 164 (kappa 0.37). Read alone, Opus 5.5 gives +0.382 [+0.188,
+0.583] and Fable 5.1 +0.111 [−0.090, +0.319] (inconclusive on its own). With
the 10 rewritten rules included: +0.201 [+0.049, +0.354], the same verdict.
Secondary readings, CI over units: project pages +0.297 [+0.047, +0.547]
(32), feedback +0.417 [+0.125, +0.708] (12), reference +0.089 [−0.125,
+0.304] (28); record classes +0.250 [+0.014, +0.500] (36) and advice classes
+0.222 [+0.028, +0.417] (36) alike; by answering model, Opus 5.5 +0.364
[+0.159, +0.568] (44) carries it, Opus 5 −0.067 [−0.300, +0.167] (15, the
sessions where the preview already helped), Fable 5.1 +0.154 [−0.192,
+0.538] (13). The price: the full body line is a median 1,209 bytes per
injection (p90 5,538, max 11,415; ~300 tokens median, ~1,400 p90) against
391 (p90 405) for the preview — a median 1,048 bytes more per unit. The
notional cost of every call on file was about $68 (Fable 5.1 $37, Opus 5.5
$25, Opus 5 $6): subscription usage, not money.

Only ``--send`` calls a model. Calls are paced and threaded
(``measure_prevented_repeats.Sender``), every answer and verdict is cached
under ``--out`` (default ``<vault>/.mnemo/full-body``) the moment it arrives,
so a rerun resumes; the full arms are frozen in ``arms.json`` once built.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from mnemo.core.reflex import render  # noqa: E402

pano = _sibling("measure_memory_panorama")
bv = pano.bv
mpr = bv.mpr
mrc = bv.mrc
rl = bv.rl

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

RATERS = bv.RATERS
SAMPLES = bv.SAMPLES
SEED = 535
BOOTSTRAP = 4000
#: The bar, set before measuring: the difference that counts as help, and the
#: band that counts as none.
BAR = 0.10
OUT_DIR = "full-body"
SOURCE_DIR = bv.OUT_DIR
PAUSE_SECONDS = bv.PAUSE_SECONDS
WORKERS = bv.WORKERS
BYTES_PER_TOKEN = pano.BYTES_PER_TOKEN
#: No real rule body is this long: ``body_preview`` with it returns the body uncut.
UNCUT = render.UNCUT

FULL, WITHOUT, TIE = "full", bv.WITHOUT, bv.TIE
BOTH = bv.BOTH
_HEADER = re.compile(r"^(.*?\[\[[^\]\s|]+\]\])")
#: How #527's arm prompt wraps a reflex block, earlier or current.
_WRAP = "UserPromptSubmit hook additional context: %s\n</system-reminder>"


# --- the arm ------------------------------------------------------------------------------

def full_body(page_text: str) -> str:
    """The rule body the reflex preview is cut from, uncut. The hook's own
    function since #542 (``mnemo.core.reflex.render``), so what was measured
    here is what ships."""
    return render.full_body(page_text)


def full_line(line: str, body: str) -> str:
    """A reflex line with its preview replaced by the whole body, under the
    same ``[[slug]]`` header."""
    m = _HEADER.match(line)
    head = m.group(1) if m else line
    return render.full_line(head, body)


def preview_of(line: str) -> str:
    """The preview text of a recorded reflex line, without its header and the
    ``read_mnemo_rule`` suffix."""
    m = _HEADER.match(line)
    rest = line[m.end():] if m else line
    rest = rest.lstrip(":").strip()
    cut = rest.rfind("(call read_mnemo_rule")
    return (rest[:cut] if cut != -1 else rest).strip()


def _squash(text: str) -> str:
    return " ".join(text.split())


def expand_block(text: str, slug: str, project: str, body: str) -> Tuple[str, List[str]]:
    """The reflex block with the rule's line(s) expanded, and the lines replaced."""
    out, replaced = [], []
    for ln in text.splitlines():
        if bv.carries_reflex(ln, slug, project):
            replaced.append(ln)
            out.append(full_line(ln, body))
        else:
            out.append(ln)
    return "\n".join(out), replaced


def build_full(arms: Dict[str, Any], ctx: Dict[str, Any], body: str) -> Dict[str, Any]:
    """The ``full`` arm from #527's frozen ``with`` prompt and the unit's
    placed context: each reflex block that carries the rule, as the prompt
    wraps it, swapped for its expanded form. ``replaced`` is 0 when no block
    was found in the prompt (the unit is skipped)."""
    slug, project = arms["slug"], arms.get("project", "")
    prompt = arms[bv.WITH]
    lines: List[str] = []
    replaced = 0
    for _, t in ctx["reflex"]:
        if not bv.carries_reflex(t, slug, project):
            continue
        wrapped = _WRAP % t
        if wrapped not in prompt:
            continue
        new, got = expand_block(t, slug, project, body)
        prompt = prompt.replace(wrapped, _WRAP % new)
        lines += got
        replaced += 1
    previews = [preview_of(ln) for ln in lines]
    return {"id": arms["id"], "slug": slug, "model": arms["model"], FULL: prompt, "replaced": replaced,
            "preview_bytes": [len(ln.encode("utf-8")) for ln in lines],
            "full_bytes": [len(full_line(ln, body).encode("utf-8")) for ln in lines],
            "body_bytes": len(body.encode("utf-8")),
            "cut": _squash(body) != _squash(previews[0]) if previews else None,
            "preview_drift": not all(_squash(body).startswith(_squash(p)) for p in previews)}


# --- the judge ----------------------------------------------------------------------------

def comparison_id(uid: str, k: int) -> str:
    return "%s|%s|%d" % (uid, FULL, k)


def full_first(cid: str, rater_index: int) -> bool:
    """Whether reply 1 is the *full* sample: seeded per comparison, flipped
    for the second rater."""
    first = int(hashlib.md5(("%d:%s" % (SEED, cid)).encode()).hexdigest(), 16) % 2 == 0
    return first if rater_index % 2 == 0 else not first


def comparison_prompt(source: Dict[str, Any], full: Sequence[Dict[str, Any]],
                      without: Sequence[Dict[str, Any]], k: int, rater_index: int) -> Optional[str]:
    """#527's judge prompt over the k-th full and k-th without reply."""
    if len(full) <= k or len(without) <= k:
        return None
    f = bv.mask(full[k]["text"], source["slug"])[0]
    wo = bv.mask(without[k]["text"], source["slug"])[0]
    pair = (f, wo) if full_first(comparison_id(source["id"], k), rater_index) else (wo, f)
    return bv.judge_prompt(source, pair)


def parse_judge(text: str, cid: str, rater_index: int) -> Optional[Dict[str, str]]:
    """``{"better": full|without|tie, "why"}``, or None when unreadable."""
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    said = str(payload.get("better") or "").strip().lower().strip('"').rstrip(".")
    said = {"reply 1": "1", "reply 2": "2", "neither": TIE}.get(said, said)
    if said not in ("1", "2", TIE):
        return None
    if said == TIE:
        better = TIE
    else:
        better = FULL if (said == "1") == full_first(cid, rater_index) else WITHOUT
    return {"better": better, "why": str(payload.get("why") or "")[:400]}


def unit_h(verdicts: Dict[str, Dict[str, Dict[str, str]]], uid: str, raters: Sequence[str],
           samples: int = SAMPLES) -> Optional[float]:
    """``h(full)`` over the unit's comparisons; None until every one is judged."""
    score = 0
    for k in range(samples):
        v = bv.agreed([(verdicts.get(r, {}).get(comparison_id(uid, k)) or {}).get("better") for r in raters])
        if v is None:
            return None
        score += {FULL: 1, WITHOUT: -1, TIE: 0}[v]
    return score / samples


# --- the numbers --------------------------------------------------------------------------

def paired(pairs: Sequence[Tuple[float, float]], n_boot: int = BOOTSTRAP, seed: int = SEED) -> Dict[str, Any]:
    """``pairs``: ``(h_full, h_preview)`` per unit. Means and the paired
    difference, each with a bootstrap CI from the same resamples of units."""
    n = len(pairs)
    if not n:
        return {"n": 0}
    rng = random.Random(seed)
    boots: Dict[str, List[float]] = {"full": [], "preview": [], "diff": []}
    for _ in range(n_boot):
        pick = [pairs[rng.randrange(n)] for _ in range(n)]
        f = sum(p[0] for p in pick) / n
        p = sum(p[1] for p in pick) / n
        boots["full"].append(f)
        boots["preview"].append(p)
        boots["diff"].append(f - p)
    f = sum(p[0] for p in pairs) / n
    p = sum(p[1] for p in pairs) / n
    return {"n": n, "full": f, "preview": p, "diff": f - p,
            "ci": {k: [bv._pct(sorted(v), 0.025), bv._pct(sorted(v), 0.975)] for k, v in boots.items()},
            "full_better": sum(a > b for a, b in pairs), "preview_better": sum(a < b for a, b in pairs)}


def verdict(st: Dict[str, Any], bar: float = BAR) -> str:
    if not st.get("n"):
        return "no estimate yet"
    lo, hi = st["ci"]["diff"]
    if st["diff"] >= bar and lo > 0:
        return "full body helps"
    if hi < 0:
        return "full body hurts"
    if lo >= -bar and hi <= bar:
        return "no difference"
    return "inconclusive"


def quantiles(values: Sequence[int]) -> Dict[str, Any]:
    """Median and p90 (nearest rank)."""
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {"n": len(s), "median": statistics.median(s), "p90": s[min(len(s) - 1, int(0.9 * len(s)))],
            "max": s[-1]}


def text_group(name: str) -> str:
    return "record" if name in pano.RECORD else "advice" if name in pano.ADVICE else name


def pages_raw(vault: Path) -> Dict[str, Dict[str, str]]:
    """``slug -> {type, name, raw, body}``: the page as written, and its body
    as the panorama classifies it."""
    from mnemo.core.filters import derive_rule_slug
    from mnemo.core.reclassify_types import split_frontmatter

    out: Dict[str, Dict[str, str]] = {}
    for page_type in ("feedback", "user", "reference", "project"):
        for md in sorted((vault / "shared" / page_type).glob("*.md")):
            try:
                raw = md.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fm, body = split_frontmatter(raw)
            out[derive_rule_slug(fm, md.stem)] = {"type": page_type, "name": str(fm.get("name") or md.stem),
                                                  "raw": raw, "body": body}
    return out


# --- report -------------------------------------------------------------------------------

def _st(st: Dict[str, Any]) -> str:
    if not st.get("n"):
        return "no units"
    ci = st["ci"]
    return ("full %+.3f [%+.3f, %+.3f]  preview %+.3f [%+.3f, %+.3f]  diff %+.3f [%+.3f, %+.3f]  (%d units)"
            % (st["full"], ci["full"][0], ci["full"][1], st["preview"], ci["preview"][0], ci["preview"][1],
               st["diff"], ci["diff"][0], ci["diff"][1], st["n"]))


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["#527's reflex-channel units: %d; built %d (skipped %d: page gone, %d: block not in the prompt); "
             "judged %d" % (data["units"], data["built"], data["skipped"]["page gone"],
                            data["skipped"]["not in prompt"], data["judged"]),
             "body cut by the preview: %d; fits in it: %d; rule rewritten since the session (its recorded "
             "preview is not how today's body begins): %d, so the verdict reads %d units"
             % (data["cut"], data["uncut"], data["preview_drift"], data["consistent"])]
    lines.append("answer models: " + ", ".join("%s %d" % kv for kv in sorted(data["models"].items())))
    for col, st in data["results"].items():
        lines += ["", "%s%s:" % (col, "  <- the verdict" if col == BOTH else "")]
        if not st.get("n"):
            lines.append("  no estimate yet")
            continue
        ci = st["ci"]
        lines += [
            "  h(full)     %+.3f  [%+.3f, %+.3f]" % (st["full"], ci["full"][0], ci["full"][1]),
            "  h(preview)  %+.3f  [%+.3f, %+.3f]   (#527's with)" % (st["preview"], ci["preview"][0],
                                                                  ci["preview"][1]),
            "  difference  %+.3f  [%+.3f, %+.3f]   over %d units (full higher %d, preview higher %d)"
            % (st["diff"], ci["diff"][0], ci["diff"][1], st["n"], st["full_better"], st["preview_better"]),
            "  verdict     %s   (bar: helps if diff >= +%.2f and CI lower > 0; hurts if CI upper < 0; "
            "no difference if CI inside ±%.2f)" % (verdict(st).upper(), BAR, BAR),
        ]
    wr = data.get("results_with_rewritten") or {}
    if wr.get("n"):
        lines += ["", "with the %d rewritten rules too (today's body; both raters): %s -> %s"
                  % (data["preview_drift"], _st(wr), data["verdict_with_rewritten"].upper())]
    sh = data.get("shares") or {}
    if sh.get("comparisons"):
        lines += ["", "full vs without comparisons (both raters): %d; full better %.1f%%, without better "
                  "%.1f%%, tie %.1f%%" % (sh["comparisons"], 100 * sh["share_full"], 100 * sh["share_without"],
                                          100 * sh["share_tie"])]
    for title, groups in data.get("breakdowns", {}).items():
        lines += ["", "by %s (both raters; CI over units):" % title]
        for name, st in groups.items():
            lines.append("  %-24s %s" % (name, _st(st)))
    ag = data.get("agreement")
    if ag and ag.get("n"):
        lines += ["", "rater agreement on full vs without: %d of %d the same (%s)"
                  % (ag["same"], ag["n"], "kappa %.2f" % ag["kappa"] if ag.get("kappa") is not None else "kappa n/a")]
    price = data.get("price") or {}
    if price.get("full", {}).get("n"):
        f, p, b = price["full"], price["preview"], price["added"]
        lines += ["", "the price, bytes per injection of the rule (tokens ~ bytes / %d):" % BYTES_PER_TOKEN,
                  "  full body line   median %d, p90 %d, max %d" % (f["median"], f["p90"], f["max"]),
                  "  preview line     median %d, p90 %d, max %d" % (p["median"], p["p90"], p["max"]),
                  "  added per unit   median %d, p90 %d (over every reflex line that carried it)"
                  % (b["median"], b["p90"])]
    cost = data.get("cost") or {}
    if cost:
        lines += ["", "notional cost of every call on file: " + ", ".join(
            "%s $%.2f (%d calls)" % (m, v["usd"], v["calls"]) for m, v in sorted(cost.items()))
            + " — subscription usage, not money"]
    return lines


# --- driver -------------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--units", default="", help="#520's units.json (default <vault>/.mnemo/prevented-repeats/units.json)")
    ap.add_argument("--source", default="", help="#527's cache (default <vault>/.mnemo/broad-value)")
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/full-body)")
    ap.add_argument("--rater", action="append", default=[])
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = args.rater or list(RATERS)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    src = Path(args.source).expanduser() if args.source else vault / ".mnemo" / SOURCE_DIR
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    units_file = Path(args.units).expanduser() if args.units else (
        vault / ".mnemo" / "prevented-repeats" / bv.UNITS_NAME)
    source = mrc._read(units_file, None)
    if source is None:
        raise SystemExit("error: no %s; run tools/measure_prevented_repeats.py first" % units_file)

    # #527's units, as #527 selects them, then its reflex-channel ones
    rows = [r for r in source["columns"].get(mpr.BOTH) or [] if r.get("new") and r.get("judged", True)]
    src_arms = mrc._read(src / "arms.json", {})
    placed = mrc._read(src / "placed.json", {})
    src_answers = mrc._read(src / "answers.json", {}).get(mrc.column("session-model", rl.ARM_SYSTEM), {})
    src_all_verdicts = mrc._read(src / "verdicts.json", {})
    src_verdicts = {r: src_all_verdicts.get(mrc.column(r, bv.JUDGE_SYSTEM), {}) for r in raters}
    uids = []
    for r in rows:
        uid = bv.unit_id(r)
        a = src_arms.get(uid)
        if (a and a.get("measurable") and a["carriers"].get("reflex") and placed.get(uid)
                and bv.unit_h(src_verdicts, uid, raters) is not None):
            uids.append(uid)
    pages = pages_raw(vault)

    arms_file = mrc._read(out / "arms.json", {})
    skipped = {"page gone": 0, "not in prompt": 0}
    for uid in uids:
        if uid in arms_file:
            continue
        a = src_arms[uid]
        page = pages.get(a["slug"])
        if page is None:
            skipped["page gone"] += 1
            continue
        built = build_full(a, placed[uid], full_body(page["raw"]))
        if not built["replaced"]:
            skipped["not in prompt"] += 1
            continue
        arms_file[uid] = built
    mrc._write(out / "arms.json", arms_file)
    todo_uids = [u for u in uids if u in arms_file]

    all_answers = mrc._read(out / "answers.json", {})
    answers = all_answers.setdefault(mrc.column("session-model", rl.ARM_SYSTEM), {})
    all_verdicts = mrc._read(out / "verdicts.json", {})
    verdicts = {r: all_verdicts.setdefault(mrc.column(r, bv.JUDGE_SYSTEM), {}) for r in raters}

    def answer_todo() -> List[Dict[str, Any]]:
        return [arms_file[u] for k in range(SAMPLES) for u in todo_uids if len(answers.get(u, [])) <= k]

    def judge_todo(r: str) -> List[Tuple[str, int, str]]:
        ri = raters.index(r)
        todo = []
        for u in todo_uids:
            for k in range(SAMPLES):
                if comparison_id(u, k) in verdicts[r]:
                    continue
                p = comparison_prompt(src_arms[u], answers.get(u, []), src_answers.get(u, {}).get(WITHOUT, []),
                                      k, ri)
                if p is not None:
                    todo.append((u, k, p))
        return todo

    sender = None
    if args.send:
        provider = llm.resolve(cfg)
        timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
        timeout = max(timeout, 600)  # an arm writes a full reply
        sender = mpr.Sender(provider, timeout, out / "calls.jsonl", args.workers, args.pause)
        here, scratch = os.getcwd(), tempfile.mkdtemp(prefix="mnemo-full-body-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches an arm or a rater
        try:
            def on_answer(a):
                def take(text):
                    if text.strip():
                        answers.setdefault(a["id"], []).append({"text": text, "model": a["model"]})
                        mrc._write(out / "answers.json", all_answers)
                return take
            todo = answer_todo()
            todo = todo[:args.limit] if args.limit else todo
            sender.run([(a["model"], a[FULL], rl.ARM_SYSTEM, on_answer(a)) for a in todo])

            def on_judge(r, u, k):
                ri, cid = raters.index(r), comparison_id(u, k)

                def take(text):
                    got = parse_judge(text, cid, ri)
                    if got is not None:
                        verdicts[r][cid] = got
                        mrc._write(out / "verdicts.json", all_verdicts)
                return take
            if not sender.stopped:
                calls = [(r, p, bv.JUDGE_SYSTEM, on_judge(r, u, k)) for r in raters for u, k, p in judge_todo(r)]
                sender.run(calls[:args.limit] if args.limit else calls)
        finally:
            os.chdir(here)
            shutil.rmtree(scratch, ignore_errors=True)

    # the numbers: the verdict reads the units whose rule is still the one the
    # session's preview was cut from; a rewritten rule's body would carry what
    # the session could not have known
    columns = ([BOTH] if len(raters) > 1 else []) + raters
    consistent = [u for u in todo_uids if not arms_file[u]["preview_drift"]]

    def per_unit(units: Sequence[str], rs: Sequence[str]) -> Dict[str, Tuple[float, float]]:
        got = {}
        for u in units:
            hf = unit_h(verdicts, u, rs)
            hp = bv.unit_h(src_verdicts, u, rs)
            if hf is not None and hp is not None:
                got[u] = (hf, hp)
        return got

    per_col = {col: per_unit(consistent, raters if col == BOTH else [col]) for col in columns}
    results = {col: paired([got[u] for u in sorted(got)]) for col, got in per_col.items()}
    everything = per_unit(todo_uids, raters if len(raters) > 1 else raters[:1])
    results_all = paired([everything[u] for u in sorted(everything)])
    primary = per_col[columns[0]]

    def klass(u: str) -> str:
        page = pages.get(arms_file[u]["slug"]) or {}
        return pano.classify("%s\n%s" % (page.get("name", ""), page.get("body", "")), page.get("type", ""))

    def group(key: Callable[[str], Iterable[str]]) -> Dict[str, Any]:
        groups: Dict[str, List[Tuple[float, float]]] = {}
        for u in sorted(primary):
            for name in key(u):
                groups.setdefault(name, []).append(primary[u])
        return {name: paired(ps) for name, ps in sorted(groups.items())}

    breakdowns = {
        "page type": group(lambda u: [(pages.get(arms_file[u]["slug"]) or {}).get("type", "gone")]),
        "record vs advice (panorama text class)": group(lambda u: [text_group(klass(u))]),
        "text class": group(lambda u: [klass(u)]),
        "answer model": group(lambda u: [arms_file[u]["model"]]),
    }
    shares = {FULL: 0, WITHOUT: 0, TIE: 0}
    for u in primary:
        for k in range(SAMPLES):
            v = bv.agreed([(verdicts[r].get(comparison_id(u, k)) or {}).get("better") for r in raters])
            if v is not None:
                shares[v] += 1
    total = sum(shares.values())
    shares_out = dict(shares, comparisons=total,
                      **{"share_" + k: (v / total if total else None) for k, v in list(shares.items())})
    agreement = None
    if len(raters) == 2:
        a_s, b_s = [], []
        for u in todo_uids:
            for k in range(SAMPLES):
                va, vb = (verdicts[r].get(comparison_id(u, k)) for r in raters)
                if va and vb:
                    a_s.append(va["better"])
                    b_s.append(vb["better"])
        agreement = {"n": len(a_s), "same": sum(x == y for x, y in zip(a_s, b_s)), "kappa": bv._kappa3(a_s, b_s)}
    built = [arms_file[u] for u in todo_uids]
    models: Dict[str, int] = {}
    for a in built:
        models[a["model"]] = models.get(a["model"], 0) + 1
    data = {
        "units": len(uids), "built": len(built), "skipped": skipped, "judged": len(primary),
        "cut": sum(1 for a in built if a["cut"]), "uncut": sum(1 for a in built if not a["cut"]),
        "preview_drift": sum(1 for a in built if a["preview_drift"]), "models": models,
        "consistent": len(consistent), "results": results, "verdict": verdict(results.get(columns[0], {})),
        "results_with_rewritten": results_all, "verdict_with_rewritten": verdict(results_all),
        "shares": shares_out, "breakdowns": breakdowns, "agreement": agreement,
        "price": {"full": quantiles([b for a in built for b in a["full_bytes"]]),
                  "preview": quantiles([b for a in built for b in a["preview_bytes"]]),
                  "added": quantiles([sum(a["full_bytes"]) - sum(a["preview_bytes"]) for a in built]),
                  "bytes_per_token": BYTES_PER_TOKEN},
        "cost": bv.spent(out / "calls.jsonl"),
    }

    pend = answer_todo()
    by_model: Dict[str, List[Tuple[str, str]]] = {}
    for a in pend:
        by_model.setdefault(a["model"], []).append((a[FULL], rl.ARM_SYSTEM))
    for m, ps in sorted(by_model.items()):
        print("%s: pending %d answer call(s); notional ~$%.2f" % (m, len(ps), bv.notional(m, ps, bv.ANSWER_TOKENS)),
              file=sys.stderr)
    for r in raters:
        jt = judge_todo(r)
        print("%s: pending %d judge call(s); notional ~$%.2f"
              % (r, len(jt), bv.notional(r, [(p, bv.JUDGE_SYSTEM) for _, _, p in jt], 80)), file=sys.stderr)
    if sender is not None:
        print("spent $%.2f notional this run (subscription usage, not money)" % sender.usd, file=sys.stderr)
    data["pending"] = {"answers": len(pend), **{r: {"judge": len(judge_todo(r))} for r in raters}}
    prov = _provenance.provenance(__file__, argv, vault=vault)
    mrc._write(out / "report.json", _provenance.stamp(data, prov))
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(data):
        print(line)
    print("")
    print("VERDICT (both raters): %s" % data["verdict"].upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
