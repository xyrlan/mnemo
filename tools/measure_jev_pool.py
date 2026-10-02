"""Does a deeper Jev judge pool (3 -> 10/25/50) double what the reflex delivers? (#563)

Usage:
    PYTHONPATH=src python3 tools/measure_jev_pool.py --dry-run     # build, then what would be sent and its cost
    PYTHONPATH=src python3 tools/measure_jev_pool.py --send        # Jev scores, latency sample, rater sample
        [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_jev_pool.py               # the report, local
    PYTHONPATH=src python3 tools/measure_jev_pool.py --json        # the same as data

The reflex judge (#412) sees only the top :data:`SHIPPED_POOL` rules of the
ranking. #455 found 20% of the prompts holding an on-point rule hold it only at
ranks 4-10, where the judge never looks. Jev is cheap and fast enough that the
pool could go far deeper; this asks whether a deeper pool roughly doubles how
many relevant rules reach the agent, at a precision and latency the hook can
live with. Whether a reached rule improves the answer is #545's question, not
this one.

**Ground truth: #520, reused.** ``measure_prevented_repeats`` rated every
typed prompt of its human sessions against the prompt's BM25F top 10 (plus the
strict pool), with two blind raters. Its ``units.json`` holds the relevant
(session, rule) units (both raters) and, per unit, whether SessionStart or MCP
had delivered it and whether Claude Code's own memory already said it
(``redundant``); ``chunks.json`` and ``frequency.json`` hold which rules each
prompt was shown and which both raters named. No rater pass over the
population is repeated. ``--units`` points at another run of #520's pipeline.

**Replay.** Every prompt of the sessions whose transcripts are still on disk
is re-read (``measure_prevented_repeats.walk``) and ranked with #520's own
as-of ranking (``Rules.top``: rules learned before the prompt, never from its
own session), :data:`DEPTH` deep, so ranks 1-10 are exactly the rules #520's
raters judged. Each prompt that passes the hook's minimum-token pre-gate is
scored by Jev **in one request over all :data:`DEPTH` rules**, with the shipped
question and rule text (``judge.ask``, ``judge.page_text``); a rule's Noul
probability does not depend on the other rules in the request (the latency
sample checks it). Every pool K in :data:`POOLS` and bar t in :data:`BARS` is
then replayed per session in typed order through the hook's dedupe and
``maxEmissionsPerSession`` cap (``measure_reflex_reach.replay_session``),
picking with the shipped ``judge.chosen``. Exported rules and the hook's
``bm25f`` config overrides are not simulated.

**Readings, per (K, t) and split:**

- **coverage** — a unit is *reached* when the replayed reflex injected the
  rule at or before a prompt it applies to, or #520 recorded SessionStart or
  MCP delivering it. Read over the units Claude Code's memory lacked (the
  verdict's population) and over every relevant unit. The baseline is what
  #520 recorded mnemo delivering on the same units (``delivered``);
- **precision** — of the injected (prompt, rule) pairs, the share both #520
  raters named. A pair the raters were never shown (rank > 10, or past
  #520's chunk cap) is *unlabelled*; the test split's unlabelled pairs of the
  chosen config are sampled (:data:`SAMPLE_MAX`, seeded) and rated by #520's
  raters with #520's own prompt (``FREQ_SYSTEM``), and precision is
  labelled-relevant plus the sample's rate times the unlabelled count;
- **load** — injected rules per prompt and per session;
- **latency** — every request's wall time at K = :data:`DEPTH`, and a seeded
  sample of :data:`LATENCY_PROMPTS` test prompts sent again at each K in
  :data:`LATENCY_POOLS`.

**Split and choice (pre-registered in #563).** Sessions split by hash, 2/3
dev and 1/3 test (:func:`split_of`). The config is chosen on dev only: the
highest coverage of lacked units among configs with labelled precision >=
:data:`PRECISION_BAR`, ties to the smaller pool, then the higher bar
(:func:`choose`). It is frozen in ``choice.json`` and read once on test.

**Bar.** On test the chosen config passes when all three hold: coverage of
lacked units >= :data:`COVERAGE_FACTOR` x #520's recorded delivery on the same
units; precision >= :data:`PRECISION_BAR`; Jev's p90 latency at that pool <=
:data:`P90_BAR_MS` ms (the hook's budget is 2.5 s). Otherwise it fails and the
report names the condition.

**Sending.** ``--send`` sends typed prompts and rule texts to TypeSafe, a
third party, with the key ``mnemo rerank --setup`` stored; the maintainer runs
it. ``--dry-run`` prints how many requests, their size and notional cost, and
one request's contents. The rater sample goes through ``core.llm`` (Max-plan
usage). Every answer is cached under ``<vault>/.mnemo/jev-pool`` as it arrives;
:data:`MAX_FAILURES` failures in a row stop the run, and a rerun resumes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mpr = _sibling("measure_prevented_repeats")
mrr = _sibling("measure_reflex_reach")
mrc = mpr.mrc

OUT_DIR = "jev-pool"
PROMPTS_NAME = "prompts.json"
SCORES_NAME = "scores.json"
LATENCY_NAME = "latency.json"
CHOICE_NAME = "choice.json"
SAMPLE_NAME = "sample.json"
REPORT_NAME = "report.json"

SEED = 563
DEPTH = 50
SHIPPED_POOL = 3
SHIPPED_AT = 0.4
POOLS = (3, 10, 25, 50)
BARS = (0.4, 0.5, 0.6, 0.7)
LATENCY_POOLS = (3, 10, 25)
LATENCY_PROMPTS = 40
SAMPLE_MAX = 200
PRECISION_BAR = 0.40
COVERAGE_FACTOR = 2.0
P90_BAR_MS = 1500
HOOK_BUDGET_MS = 2500
#: Generous on purpose: the measurement wants the true wall time, and reports
#: the share past the hook's budget, rather than cutting at it.
MEASURE_TIMEOUT_S = 30.0
MAX_FAILURES = 5
WORKERS = 4
PAUSE_SECONDS = 0.5
#: TypeSafe's list price, USD per million input tokens, for the notional cost.
JEV_USD_PER_M = 0.042
CHARS_PER_TOKEN = 4

DEV, TEST = "dev", "test"


# --- small pieces ---------------------------------------------------------------------------

def split_of(session_id: str) -> str:
    """1/3 of sessions to test, by hash, stable across runs and machines."""
    return TEST if int(hashlib.sha256(session_id.encode("utf-8")).hexdigest(), 16) % 3 == 0 else DEV


def labels_from(chunks: Dict[str, List[Dict[str, Any]]],
                freq: Dict[str, Dict[str, Dict[str, List[str]]]],
                raters: Sequence[str]) -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    """``(shown, named)``: per prompt key, the rules #520's raters were shown
    and the ones every rater named. A chunk some rater never answered labels
    nothing."""
    shown: Dict[str, Set[str]] = {}
    named: Dict[str, Set[str]] = {}
    for chs in chunks.values():
        for ch in chs:
            per = [(freq.get(mrc.column(r, mpr.FREQ_SYSTEM)) or {}).get(ch["id"]) for r in raters]
            if any(p is None for p in per):
                continue
            for it in ch["prompts"]:
                shown[it["key"]] = set(ch["rules"])
                named[it["key"]] = set.intersection(*[set(p.get(it["key"]) or []) for p in per])
    return shown, named


def picks(scores: Dict[str, float], ranking: Sequence[str], pool: int, at: float) -> List[str]:
    """The shipped judge's choice over the first ``pool`` rules of the ranking."""
    from mnemo.core.reflex import judge

    return judge.chosen({s: scores[s] for s in ranking[:pool] if s in scores}, at)


def simulate(prompts: Sequence[Dict[str, Any]], scores: Dict[str, Dict[str, Any]], pool: int, at: float,
             cap: int) -> Dict[Tuple[str, str], int]:
    """Replay every session's prompts in typed order through the hook's dedupe
    and cap. Returns ``(session, rule) -> the first prompt index it was
    injected at``. A prompt Jev did not score (pre-gate, failure) injects
    nothing, as the judge falls back to nothing it can be credited with here."""
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for p in prompts:
        by_session.setdefault(p["session_id"], []).append(p)
    out: Dict[Tuple[str, str], int] = {}
    for sid, ps in by_session.items():
        ps = sorted(ps, key=lambda p: p["i"])
        seq = []
        for p in ps:
            got = scores.get(p["key"]) or {}
            accepted = picks(got.get("scores") or {}, p["ranking"], pool, at) \
                if p["tokens_ok"] and got.get("status") == "ok" else []
            seq.append((p["key"], accepted))
        fates = mrr.replay_session(seq, cap)
        index = {p["key"]: p["i"] for p in ps}
        for key, fate in fates.items():
            for slug, what in fate.items():
                if what == "injected" and (sid, slug) not in out:
                    out[(sid, slug)] = index[key]
    return out


def injected_pairs(prompts: Sequence[Dict[str, Any]], scores: Dict[str, Dict[str, Any]], pool: int,
                   at: float, cap: int) -> List[Tuple[str, str, str]]:
    """``(session, prompt key, rule)`` for every injection the replay makes."""
    first = simulate(prompts, scores, pool, at, cap)
    key_of = {(p["session_id"], p["i"]): p["key"] for p in prompts}
    return sorted((sid, key_of[(sid, i)], slug) for (sid, slug), i in first.items())


def reached(unit: Dict[str, Any], first: Dict[Tuple[str, str], int], reflex_only: bool = False) -> bool:
    i = first.get((unit["session_id"], unit["slug"]))
    if i is not None and i <= max(unit["prompts"]):
        return True
    return False if reflex_only else bool(unit.get("session_start") or unit.get("mcp"))


def coverage(units: Sequence[Dict[str, Any]], first: Dict[Tuple[str, str], int],
             reflex_only: bool = False) -> Dict[str, Any]:
    k = sum(1 for u in units if reached(u, first, reflex_only))
    return {"n": len(units), "k": k, "share": k / len(units) if units else None,
            "ci": list(mrr.wilson(k, len(units)))}


def precision(pairs: Sequence[Tuple[str, str, str]], shown: Dict[str, Set[str]], named: Dict[str, Set[str]],
              sample: Optional[Dict[str, bool]] = None) -> Dict[str, Any]:
    """Labelled pairs read from #520; unlabelled ones estimated from ``sample``
    (``"key|slug" -> relevant``) when there is one."""
    lab = [(k, s) for _, k, s in pairs if s in shown.get(k, set())]
    rel = sum(1 for k, s in lab if s in named.get(k, set()))
    unl = len(pairs) - len(lab)
    out: Dict[str, Any] = {"pairs": len(pairs), "labelled": len(lab), "labelled_relevant": rel,
                           "labelled_precision": rel / len(lab) if lab else None,
                           "labelled_ci": list(mrr.wilson(rel, len(lab))), "unlabelled": unl,
                           "unlabelled_share": unl / len(pairs) if pairs else None}
    if sample:
        rate = sum(sample.values()) / len(sample)
        out.update(sample_n=len(sample), sample_rate=rate, sample_ci=list(mrr.wilson(sum(sample.values()),
                                                                                     len(sample))),
                   precision=(rel + rate * unl) / len(pairs) if pairs else None)
    elif unl == 0:
        out["precision"] = out["labelled_precision"]
    return out


def load(prompts: Sequence[Dict[str, Any]], first: Dict[Tuple[str, str], int]) -> Dict[str, Any]:
    sessions = {p["session_id"] for p in prompts}
    return {"per_prompt": len(first) / len(prompts) if prompts else None,
            "per_session": len(first) / len(sessions) if sessions else None}


def choose(rows: Sequence[Dict[str, Any]], bar: float = PRECISION_BAR) -> Optional[Dict[str, Any]]:
    """Dev's choice: highest lacked-unit coverage among configs whose labelled
    precision clears ``bar``; ties to the smaller pool, then the higher bar."""
    ok = [r for r in rows if (r["precision"].get("labelled_precision") or 0.0) >= bar]
    if not ok:
        return None
    return sorted(ok, key=lambda r: (-r["coverage_lacked"]["k"], r["pool"], -r["at"]))[0]


def latency_summary(ms: Sequence[float]) -> Dict[str, Any]:
    if not ms:
        return {"n": 0}
    xs = sorted(ms)
    return {"n": len(xs), "median": statistics.median(xs), "p90": xs[min(len(xs) - 1, int(0.9 * len(xs)))],
            "over_budget": sum(1 for x in xs if x > HOOK_BUDGET_MS) / len(xs)}


def verdict(test_cov: Dict[str, Any], baseline: Dict[str, Any], prec: Optional[float],
            p90: Optional[float]) -> Dict[str, Any]:
    failed = []
    if baseline.get("share") is None or test_cov.get("share") is None \
            or test_cov["share"] < COVERAGE_FACTOR * baseline["share"]:
        failed.append("coverage < %.0fx the recorded delivery" % COVERAGE_FACTOR)
    if prec is None:
        failed.append("precision not read yet (rater sample pending)")
    elif prec < PRECISION_BAR:
        failed.append("precision < %.0f%%" % (100 * PRECISION_BAR))
    if p90 is None:
        failed.append("latency not read yet")
    elif p90 > P90_BAR_MS:
        failed.append("p90 latency > %d ms" % P90_BAR_MS)
    return {"pass": not failed, "failed": failed}


def score_drift(latency: Dict[str, Dict[str, Any]], scores: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Mean |p(K) - p(DEPTH)| over pairs scored in both requests: whether a
    rule's probability depends on the company it is asked in."""
    diffs = []
    for key, per in latency.items():
        full = (scores.get(key) or {}).get("scores") or {}
        for row in per.values():
            for slug, p in (row.get("scores") or {}).items():
                if slug in full:
                    diffs.append(abs(p - full[slug]))
    return {"pairs": len(diffs), "mean_abs": statistics.mean(diffs) if diffs else None,
            "max_abs": max(diffs) if diffs else None}


# --- sending --------------------------------------------------------------------------------

def run_requests(jobs: Sequence[Tuple[str, Callable[[], Dict[str, Any]]]], on_done: Callable[[str, Dict[str, Any]], None],
                 workers: int = WORKERS, pause: float = PAUSE_SECONDS, max_failures: int = MAX_FAILURES,
                 sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Run ``(id, call)`` jobs on threads; ``on_done`` gets every ``ok`` row,
    under a lock. Stops after ``max_failures`` non-``ok`` rows in a row."""
    lock = threading.Lock()
    state = {"failures": 0, "stopped": False, "done": 0, "failed": 0}

    def one(job: Tuple[str, Callable[[], Dict[str, Any]]]) -> None:
        jid, call = job
        if state["stopped"]:
            return
        row = call()
        with lock:
            if row.get("status") == "ok":
                state["failures"] = 0
                state["done"] += 1
                on_done(jid, row)
            else:
                state["failures"] += 1
                state["failed"] += 1
                print("  %s: %s" % (jid, row.get("status")), file=sys.stderr)
                if state["failures"] >= max_failures:
                    state["stopped"] = True
                    print("  %d failures in a row: stopping; rerun to resume" % state["failures"], file=sys.stderr)
            n = state["done"] + state["failed"]
            if n % 50 == 0:
                print("  %d/%d requests" % (n, len(jobs)), file=sys.stderr)
        if pause > 0:
            sleep(pause)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        list(ex.map(one, jobs))
    return state


def jev_call(vault: Path, settings: Dict[str, Any], prompt: Dict[str, Any], slugs: Sequence[str],
             read_text: Callable[[str], str]) -> Callable[[], Dict[str, Any]]:
    from mnemo.core.reflex import judge

    def call() -> Dict[str, Any]:
        _picks, info = judge.ask(vault, prompt=prompt["text"], slugs=list(slugs), chosen_settings=settings,
                                 project=prompt["project"], read_text=read_text)
        return {"status": info.get("status"), "ms": info.get("ms"), "asked": info.get("asked"),
                "scores": {s: p for s, p in info.get("scores") or []}}
    return call


def request_chars(prompt: Dict[str, Any], slugs: Sequence[str], read_text: Callable[[str], str]) -> int:
    from mnemo.core.reflex import judge

    body = json.dumps(judge.state(prompt["text"])) + "".join(
        json.dumps(judge.question(read_text(s))) for s in slugs if read_text(s))
    return len(body)


def latency_prompts(prompts: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    test = [p for p in prompts if split_of(p["session_id"]) == TEST and p["tokens_ok"]
            and len(p["ranking"]) >= max(LATENCY_POOLS)]
    return random.Random(SEED).sample(sorted(test, key=lambda p: p["key"]), min(LATENCY_PROMPTS, len(test)))


def draw_sample(pairs: Sequence[Tuple[str, str, str]], shown: Dict[str, Set[str]],
                n: int = SAMPLE_MAX) -> List[Tuple[str, str]]:
    """Seeded sample of the test split's unlabelled injected pairs."""
    unl = sorted((k, s) for sid, k, s in pairs if split_of(sid) == TEST and s not in shown.get(k, set()))
    return sorted(random.Random(SEED).sample(unl, min(n, len(unl))))


def sample_chunks(sample: Sequence[Tuple[str, str]], prompts: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """#520's chunk shape, one prompt each, its sampled rules as the list."""
    by: Dict[str, List[str]] = {}
    for key, slug in sample:
        by.setdefault(key, []).append(slug)
    out = []
    for key in sorted(by):
        p = prompts[key]
        out.append({"id": mpr._sha("jev-pool", key, *sorted(by[key])), "session_id": p["session_id"],
                    "prompts": [{"key": key, "text": mpr._head(p["text"], mpr.PROMPT_CHARS),
                                 "answered": mpr._tail(p.get("answered") or "", mpr.CONTEXT_CHARS)}],
                    "rules": sorted(by[key])})
    return out


def sample_labels(chunks: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, Dict[str, List[str]]]],
                  raters: Sequence[str]) -> Dict[str, bool]:
    """``"key|slug" -> every rater named it``, for chunks every rater answered."""
    out: Dict[str, bool] = {}
    for ch in chunks:
        per = [(answers.get(r) or {}).get(ch["id"]) for r in raters]
        if any(p is None for p in per):
            continue
        key = ch["prompts"][0]["key"]
        named = set.intersection(*[set(p.get(key) or []) for p in per])
        for slug in ch["rules"]:
            out["%s|%s" % (key, slug)] = slug in named
    return out


# --- build ----------------------------------------------------------------------------------

def build(vault: Path, units_file: Path, projects: Path, claude_home: Path, min_tokens: int) -> Dict[str, Any]:
    """Every typed prompt of #520's rated sessions still on disk, ranked
    :data:`DEPTH` deep as of the prompt. Frozen by the caller."""
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.core.reflex.tokenizer import tokenize_query

    base = mrc._read(units_file, {})
    sessions = base.get("sessions") or {}
    rules = mpr.Rules(vault, projects, claude_home)
    out = []
    for sid in sorted(base.get("rated") or []):
        meta = sessions.get(sid) or {}
        path = Path(str(meta.get("path") or ""))
        if not path.is_file():
            continue
        for p in mpr.walk(_load_jsonl_events(path))["prompts"]:
            pool = rules.pool(meta.get("project") or "", p["ts"], sid)
            out.append({"key": "%s#%d" % (sid, p["i"]), "session_id": sid, "i": p["i"], "ts": p["ts"],
                        "project": meta.get("project") or "", "text": p["text"],
                        "answered": mpr._tail(p.get("answered") or "", mpr.CONTEXT_CHARS),
                        "tokens_ok": len(set(tokenize_query(p["text"]))) >= min_tokens,
                        "ranking": rules.top(p["text"], pool, DEPTH)})
    return {"units_file": str(units_file), "depth": DEPTH, "min_tokens": min_tokens, "prompts": out}


# --- report ---------------------------------------------------------------------------------

def rows_for(prompts: Sequence[Dict[str, Any]], units: Sequence[Dict[str, Any]], scores: Dict[str, Dict[str, Any]],
             shown: Dict[str, Set[str]], named: Dict[str, Set[str]], cap: int,
             sample: Optional[Dict[str, bool]] = None, only: Optional[Tuple[int, float]] = None) -> List[Dict[str, Any]]:
    lacked = [u for u in units if not u.get("redundant")]
    rows = []
    for pool in POOLS:
        for at in BARS:
            if only and (pool, at) != only:
                continue
            first = simulate(prompts, scores, pool, at, cap)
            pairs = injected_pairs(prompts, scores, pool, at, cap)
            rows.append({"pool": pool, "at": at, "coverage_lacked": coverage(lacked, first),
                         "coverage_relevant": coverage(units, first),
                         "reflex_only_lacked": coverage(lacked, first, reflex_only=True),
                         "precision": precision(pairs, shown, named, sample), "load": load(prompts, first)})
    return rows


def recorded(units: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    lacked = [u for u in units if not u.get("redundant")]
    k = sum(1 for u in lacked if u.get("delivered"))
    return {"n": len(lacked), "k": k, "share": k / len(lacked) if lacked else None,
            "ci": list(mrr.wilson(k, len(lacked)))}


def _p(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["#563: %d prompts in %d sessions (%s); %d relevant units, %d the native memory lacked"
             % (data["prompts"], data["sessions"], data["units_file"], data["units"], data["lacked"]),
             "Jev: %d of %d scorable prompts scored, %d pending; ranks 1-10 labelled by #520: %s"
             % (data["scored"], data["scorable"], data["pending"], _p(data["top10_labelled"]))]
    for split in (DEV, TEST):
        part = data["splits"][split]
        lines += ["", "%s (%d sessions): #520 recorded delivery on lacked units %s (%d/%d)"
                  % (split, part["sessions"], _p(part["recorded"]["share"]), part["recorded"]["k"],
                     part["recorded"]["n"]),
                  "  pool  bar   lacked   (reflex only)  relevant  precision(lab)  unlab   inj/prompt  inj/session"]
        for r in part["rows"]:
            pr = r["precision"]
            lines.append("  %4d  %.1f  %7s  (%7s)      %7s   %7s         %6s   %5.2f       %5.1f" % (
                r["pool"], r["at"], _p(r["coverage_lacked"]["share"]), _p(r["reflex_only_lacked"]["share"]),
                _p(r["coverage_relevant"]["share"]), _p(pr.get("labelled_precision")), _p(pr.get("unlabelled_share")),
                r["load"]["per_prompt"] or 0.0, r["load"]["per_session"] or 0.0))
    lines += ["", "latency by pool (ms): " + "; ".join(
        "K=%s median %s p90 %s over %d ms %s (n=%d)" % (k, v.get("median"), v.get("p90"), HOOK_BUDGET_MS,
                                                      _p(v.get("over_budget")), v.get("n", 0))
        for k, v in sorted(data["latency"].items(), key=lambda kv: int(kv[0])))]
    d = data.get("drift") or {}
    if d.get("pairs"):
        lines.append("score drift vs K=%d: mean |dp| %.3f, max %.3f over %d pairs" % (
            DEPTH, d["mean_abs"], d["max_abs"], d["pairs"]))
    ch = data.get("choice")
    if not ch:
        lines += ["", "choice: none yet (Jev scores incomplete, or no dev config clears %s labelled precision)"
                  % _p(PRECISION_BAR)]
        return lines
    t = data["test_read"]
    lines += ["", "chosen on dev: pool %d, bar %.1f" % (ch["pool"], ch["at"]),
              "test read: lacked coverage %s %s vs %.0fx recorded %s; precision %s (labelled %s, unlabelled %s, "
              "sample %s of %s); p90 %s ms"
              % (_p(t["coverage"]["share"]), [round(x, 3) if x is not None else None for x in t["coverage"]["ci"]],
                 COVERAGE_FACTOR, _p(t["recorded"]["share"]), _p(t["precision"].get("precision")),
                 _p(t["precision"].get("labelled_precision")), t["precision"].get("unlabelled"),
                 _p(t["precision"].get("sample_rate")), t["precision"].get("sample_n", 0), t["p90"]),
              "VERDICT: %s%s" % ("PASS" if t["verdict"]["pass"] else "FAIL",
                                 "" if t["verdict"]["pass"] else " (" + "; ".join(t["verdict"]["failed"]) + ")")]
    return lines


# --- driver ---------------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.reflex import judge

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--units", default="", help="#520's units.json (default <vault>/.mnemo/prevented-repeats)")
    ap.add_argument("--projects", default=str(Path("~/.claude/projects").expanduser()))
    ap.add_argument("--claude-home", default=str(Path("~/.claude").expanduser()))
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N Jev requests this run")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    src = vault / ".mnemo" / "prevented-repeats"
    units_file = Path(args.units).expanduser() if args.units else src / mpr.UNITS_NAME
    labels_dir = units_file.parent
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    reflex_cfg = cfg.get("reflex") or {}
    cap = int(reflex_cfg.get("maxEmissionsPerSession", 10))
    min_tokens = int((reflex_cfg.get("thresholds") or {}).get("minQueryTokens", 3))

    frozen = mrc._read(out / PROMPTS_NAME, None)
    if frozen is None:
        frozen = build(vault, units_file, Path(args.projects), Path(args.claude_home), min_tokens)
        mrc._write(out / PROMPTS_NAME, frozen)
    prompts = frozen["prompts"]
    by_key = {p["key"]: p for p in prompts}
    keep = {p["session_id"] for p in prompts}
    units = [u for u in (mrc._read(units_file, {}).get("columns") or {}).get(mpr.BOTH, []) if u["session_id"] in keep]
    raters = list(mpr.RATERS)
    shown, named = labels_from(mrc._read(labels_dir / "chunks.json", {}), mrc._read(labels_dir / "frequency.json", {}),
                               raters)

    scores = mrc._read(out / SCORES_NAME, {})
    latency = mrc._read(out / LATENCY_NAME, {})
    scorable = [p for p in prompts if p["tokens_ok"] and p["ranking"]]
    pending = [p for p in scorable if (scores.get(p["key"]) or {}).get("status") != "ok"]
    lat_jobs = [(p, k) for p in latency_prompts(prompts) for k in LATENCY_POOLS
                if (latency.get(p["key"]) or {}).get(str(k), {}).get("status") != "ok"]

    reads: Dict[str, Callable[[str], str]] = {}

    def read_text(project: str) -> Callable[[str], str]:
        if project not in reads:
            raw = judge.page_text(vault, project=project)
            memo: Dict[str, str] = {}
            reads[project] = lambda s: memo.setdefault(s, raw(s)) if s not in memo else memo[s]
        return reads[project]

    if args.dry_run:
        chars = [request_chars(p, p["ranking"], read_text(p["project"])) for p in pending]
        chars += [request_chars(p, p["ranking"][:k], read_text(p["project"])) for p, k in lat_jobs]
        tokens = sum(chars) / CHARS_PER_TOKEN
        settings = judge.settings(cfg)
        print("would send %d Jev requests (%d scoring at K=%d, %d latency) to TypeSafe model %s; "
              "~%.1fM input tokens, ~$%.2f at $%.3f/M"
              % (len(chars), len(pending), DEPTH, len(lat_jobs), settings.get("model"), tokens / 1e6,
                 tokens / 1e6 * JEV_USD_PER_M, JEV_USD_PER_M))
        if pending:
            p = pending[0]
            print("one request: the typed prompt (%d chars, sent up to %d) and %d rule texts, e.g. %s"
                  % (len(p["text"]), judge.PROMPT_CHARS, len(p["ranking"]), ", ".join(p["ranking"][:3])))
        print("then: a rater sample of up to %d unlabelled test pairs, %s, through core.llm (Max-plan usage)"
              % (SAMPLE_MAX, " + ".join(raters)))
        return 0

    if args.send:
        settings = dict(judge.settings(cfg), timeoutSeconds=MEASURE_TIMEOUT_S, injectAt=0.0)
        jobs = [(p["key"], jev_call(vault, settings, p, p["ranking"], read_text(p["project"]))) for p in pending]
        jobs += [("%s@%d" % (p["key"], k), jev_call(vault, settings, p, p["ranking"][:k], read_text(p["project"])))
                 for p, k in lat_jobs]
        if args.limit:
            jobs = jobs[:args.limit]

        def done(jid: str, row: Dict[str, Any]) -> None:
            if "@" in jid:
                key, k = jid.rsplit("@", 1)
                latency.setdefault(key, {})[k] = row
                mrc._write(out / LATENCY_NAME, latency)
            else:
                scores[jid] = row
                mrc._write(out / SCORES_NAME, scores)
        state = run_requests(jobs, done, args.workers, args.pause)
        print("Jev: %d ok, %d failed%s" % (state["done"], state["failed"], ", stopped" if state["stopped"] else ""),
              file=sys.stderr)
        pending = [p for p in scorable if (scores.get(p["key"]) or {}).get("status") != "ok"]

    # the readings
    data: Dict[str, Any] = {"units_file": str(units_file), "prompts": len(prompts), "sessions": len(keep),
                            "units": len(units), "lacked": sum(1 for u in units if not u.get("redundant")),
                            "scorable": len(scorable), "scored": len(scorable) - len(pending),
                            "pending": len(pending), "splits": {}}
    top10 = [(p["key"], s) for p in prompts for s in p["ranking"][:10]]
    data["top10_labelled"] = sum(1 for k, s in top10 if s in shown.get(k, set())) / len(top10) if top10 else None
    for split in (DEV, TEST):
        ps = [p for p in prompts if split_of(p["session_id"]) == split]
        us = [u for u in units if split_of(u["session_id"]) == split]
        data["splits"][split] = {"sessions": len({p["session_id"] for p in ps}), "recorded": recorded(us),
                                 "rows": rows_for(ps, us, scores, shown, named, cap)}
    lat: Dict[str, List[float]] = {str(k): [] for k in LATENCY_POOLS}
    for per in latency.values():
        for k, row in per.items():
            if row.get("status") == "ok" and isinstance(row.get("ms"), (int, float)):
                lat.setdefault(k, []).append(float(row["ms"]))
    lat[str(DEPTH)] = [float(r["ms"]) for r in scores.values() if r.get("status") == "ok"
                       and isinstance(r.get("ms"), (int, float))]
    data["latency"] = {k: latency_summary(v) for k, v in lat.items()}
    data["drift"] = score_drift(latency, scores)

    choice = mrc._read(out / CHOICE_NAME, None)
    if choice is None and not pending:
        best = choose(data["splits"][DEV]["rows"])
        if best is not None:
            choice = {"pool": best["pool"], "at": best["at"]}
            mrc._write(out / CHOICE_NAME, choice)
    data["choice"] = choice
    if choice:
        test_ps = [p for p in prompts if split_of(p["session_id"]) == TEST]
        test_us = [u for u in units if split_of(u["session_id"]) == TEST]
        pairs = injected_pairs(test_ps, scores, choice["pool"], choice["at"], cap)
        frozen_sample = mrc._read(out / SAMPLE_NAME, None)
        if frozen_sample is None:
            frozen_sample = {"pairs": [list(x) for x in draw_sample(pairs, shown)], "answers": {}}
            mrc._write(out / SAMPLE_NAME, frozen_sample)
        chunks = sample_chunks([tuple(x) for x in frozen_sample["pairs"]], by_key)
        answers = frozen_sample["answers"]
        if args.send:
            rule_text = mpr.Rules(vault, Path(args.projects), Path(args.claude_home)).text
            todo = [(r, ch) for r in raters for ch in chunks if ch["id"] not in (answers.get(r) or {})]
            sender = mpr.Sender(llm.resolve(cfg), 600, out / "calls.jsonl", 2, mpr.PAUSE_SECONDS)

            def take(r: str, ch: Dict[str, Any]) -> Callable[[str], None]:
                def _t(text: str) -> None:
                    got = mpr.parse_freq(text, ch)
                    if got is not None:
                        answers.setdefault(r, {})[ch["id"]] = got
                        mrc._write(out / SAMPLE_NAME, frozen_sample)
                return _t
            sender.run([(r, mpr.freq_prompt(ch, rule_text), mpr.FREQ_SYSTEM, take(r, ch)) for r, ch in todo])
        sample = sample_labels(chunks, answers, raters)
        complete = len(sample) == len(frozen_sample["pairs"])
        row = rows_for(test_ps, test_us, scores, shown, named, cap, sample if complete else None,
                       only=(choice["pool"], choice["at"]))[0]
        p90 = (data["latency"].get(str(choice["pool"])) or {}).get("p90")
        rec = recorded(test_us)
        data["test_read"] = {"coverage": row["coverage_lacked"], "recorded": rec, "precision": row["precision"],
                             "p90": p90, "sample_pending": len(frozen_sample["pairs"]) - len(sample),
                             "verdict": verdict(row["coverage_lacked"], rec, row["precision"].get("precision"), p90)}
    mrc._write(out / REPORT_NAME, data)
    print(json.dumps(data, indent=1, default=str) if args.json else "\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
