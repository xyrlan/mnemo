"""With the Jev judge on, do generic rules still make day one's noise? (#484)

Usage:
    PYTHONPATH=src python3 tools/measure_judge_noise_kinds.py --arm-a-work ~/.cache/mnemo/day-one-471
    PYTHONPATH=src python3 tools/measure_judge_noise_kinds.py ... --dry-run   # rules to rate, calls; calls nothing
    PYTHONPATH=src python3 tools/measure_judge_noise_kinds.py ... --send      # both raters label what is pending
    PYTHONPATH=src python3 tools/measure_judge_noise_kinds.py ... --json

**Measure only**, and no Jev request: #479's judge-on replay is read from
``judge.json`` under ``--work`` (what ``measure_day_one.py --judge`` reprints)
and its labels from ``labels.json`` there and under ``--arm-a-work``. Nothing
retires or edits a rule; every write goes under
``<vault>/.mnemo/noise-concentration/``, beside #480's ratings.

**Kinds.** A rule's kind is #465's letter (G/N/S/T/W) from both of #480's
raters, read from #480's ``labels.json`` by the text a rater sees
(``measure_noise_concentration.page_facts`` over the arm's own vault — the
same view, so the same id). A rule #480 never rated is rated the same way
(``measure_demoted_keeps``'s rubric, raters and batching) into
``judge-on-labels.json``, at most :data:`MAX_CALLS` calls ever. What is rated:
every rule an arm injected with the judge on, and every rule in an arm-(b)
Jev pool. Arms (a) and (c) put 38 and 45 rules in their pools, which the
budget cannot cover; a pool rule there without a kind counts as *unrated*.

**1. By rule.** Every injected pair, noise (0) / marginal (1) / on-point (2)
by rule, with the two letters and the page type.

**2. The counterfactual, two ways.** *Dropped*: the pairs G/N rules made are
struck out of the labelled record — the arithmetic the issue asks for. *Replayed*:
the same prompts go through :func:`mnemo.core.reflex.replay.run` again, over
the same (rewound) vaults, with the G/N rules marked ``retired`` in the index —
the flag :mod:`mnemo.core.reflex.decide` already drops, so this is what keeping
them out of the reflex would do. Other rules then move into the top 3, and
the cap and the dedupe fall differently. The stage answers from the scores
Jev gave in #479's run for that prompt; a rule Jev never scored on that
prompt is *unknown* and, like a rule the judge skipped, is not injected — so
the replayed row is a floor on injections and each unknown is counted. An
injected pair #479 never labelled counts as unlabelled. The replay with
nothing dropped must reproduce #479's judge-on injections, and the report
says on how many prompts it does. Both rows are read against #479's bar
(:data:`NOISE_BAR`), for "both raters" and for "either rater".

**3. Requests.** The Jev requests #479 spent on pools made only of G/N rules
(by both raters), and the requests the replayed counterfactual sends — the
difference is what removing them saves.

**4. Arms (a) and (c)** get the same rows. Their 6-7 injected pairs are too
few to decide anything.

**First run, 2026-09-24.** 10 rules #480 never rated, 2 calls ($0.22 API-price
equivalent), raters agree on 10/10. No Jev request. The replay with nothing
dropped reproduces #479's judge-on injections on 1,275/1,275 arm-(b) prompts
and 50/50 in (a) and (c).

- **Arm (b): yes, the same kind of rule.** 17 of the 18 noise pairs come from
  11 rules both raters call G, all ``feedback`` pages; the 18th is an S
  reference page. But those rules also made **20 of the 32 on-point pairs**
  (and 26 of 37 marginal): with the judge on, a generic rule is on the point
  as often as not.
- **Dropped, as arithmetic:** noise 1/24 = 4.2% [0.7-20.2], clears the 20%
  bar, with 12 of 32 on-point left. "Either rater" is the same set here.
- **Replayed, it is not decided.** Retiring the G/N rules saves 305 of 844
  Jev requests (553 were spent on pools made only of them), but other rules
  move into 361 pools: 468 (prompt, rule) pairs Jev never scored, 193 of them
  ``per-risk-backup-strategy``, which #479's judge picked 19 of 122 times.
  At each rule's own pick rate that is ~34 more injections; 4 more noise
  pairs would miss the bar. Deciding it needs those ~361 requests sent and
  the new pairs labelled.
- **Arms (a) and (c):** 6-7 pairs, no G/N rule among them; their noise is S
  project and reference pages. Nothing to remove, and too few to decide.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    # measure_day_one declares dataclasses, which look their module up here.
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


nc = _sibling("measure_noise_concentration")
mdk = nc.mdk
mdo = _sibling("measure_day_one")

RATED_NAME = "judge-on-rated.json"
LABELS_NAME = "judge-on-labels.json"
#: The issue's budget, over every run of this tool: only rules #480 did not rate.
MAX_CALLS = 5
BATCH = nc.BATCH
GN = nc.GENERIC_OR_NARRATIVE
NOISE_BAR = mdo.NOISE_BAR
ARMS = ("b", "a", "c")
NOISE, MARGINAL, ON_POINT = 0, 1, 2


# --- the pure part ---------------------------------------------------------------

def arm_units(state: Dict[str, Any], arm: str) -> List[Dict[str, Any]]:
    """The judge-on units #479 recorded for ``arm``, in replay order."""
    st = (state.get("arms") or {}).get(arm) or {}
    if arm == "b":
        return [u for r in st.get("rows") or [] for u in r["units"]]
    return list(st.get("units") or [])


def injected(units: Iterable[Dict[str, Any]], labels: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    """One pair per injected (prompt, rule), with its 0/1/2 label or None."""
    return [nc.pair(u["uid"], c["slug"], (labels.get(u["uid"]) or {}).get(c["slug"]),
                    session=u.get("session_id"))
            for u in units for c in u["pool"]]


def asked_pool(unit: Dict[str, Any]) -> Optional[List[str]]:
    """The rules Jev was asked about for this prompt; None when nothing was sent."""
    j = unit.get("judge") or {}
    if not j.get("asked"):
        return None
    return [s for s, _ in j.get("scores") or []]


def kind(letters: Sequence[Optional[str]]) -> Dict[str, bool]:
    """Both / either rater said G or N; ``rated`` only with both letters."""
    rated = len(letters) == 2 and all(letters)
    return {"rated": rated, "both": rated and all(x in GN for x in letters),
            "either": any(x in GN for x in letters if x)}


def rate(pairs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Labelled pairs, noise, on-point; the noise share, its Wilson interval, the bar."""
    labelled = [p for p in pairs if p["label"] is not None]
    noise = sum(1 for p in labelled if p["label"] == NOISE)
    lo, hi = mdk.wilson(noise, len(labelled))
    share = noise / float(len(labelled)) if labelled else None
    return {"pairs": len(pairs), "labelled": len(labelled), "noise": noise,
            "marginal": sum(1 for p in labelled if p["label"] == MARGINAL),
            "on_point": sum(1 for p in labelled if p["label"] == ON_POINT),
            "unlabelled": len(pairs) - len(labelled),
            "noise_share": share, "ci": [lo, hi],
            "clears": None if share is None else share <= NOISE_BAR}


def by_rule(pairs: Sequence[Dict[str, Any]], letters: Dict[str, List[Optional[str]]],
            types: Dict[str, Optional[str]]) -> List[Dict[str, Any]]:
    """Question 1: per injected rule, its tally and kind; most noise first, ties by slug."""
    t = nc.tally(pairs)
    rows = [dict(t[s], slug=s, letters=letters.get(s) or [None, None], type=types.get(s),
                 **{"kind_" + k: v for k, v in kind(letters.get(s) or [None, None]).items()})
            for s in t]
    return sorted(rows, key=lambda r: (-r["noise"], -r["on_point"], r["slug"]))


def dropped(pairs: Sequence[Dict[str, Any]], drop: Set[str]) -> Dict[str, Any]:
    """Question 2, as arithmetic: the record without the pairs ``drop`` made."""
    out = rate([p for p in pairs if p["slug"] not in drop])
    out["rules_dropped"] = len(drop & {p["slug"] for p in pairs})
    return out


def requests(units: Sequence[Dict[str, Any]], drop: Set[str], known: Set[str]) -> Dict[str, int]:
    """Question 3: requests sent, and those whose whole pool is in ``drop``.

    ``known`` is every rule with a kind; a pool holding a rule without one is
    counted apart, since whether it was all G/N is not known.
    """
    pools = [p for p in (asked_pool(u) for u in units) if p is not None]
    return {"requests": len(pools),
            "all_dropped": sum(1 for p in pools if p and all(s in drop for s in p)),
            "any_dropped": sum(1 for p in pools if any(s in drop for s in p)),
            "with_unrated": sum(1 for p in pools if any(s not in known for s in p))}


def retire(index: Dict[str, Any], drop: Set[str]) -> Dict[str, Any]:
    """``index`` with the ``drop`` rules retired: postings kept, ``decide`` skips them."""
    docs = {s: (dict(d, retired=True) if s in drop else d) for s, d in (index.get("docs") or {}).items()}
    return dict(index, docs=docs)


def cached_scores(units: Iterable[Dict[str, Any]]) -> Dict[Tuple[str, float], Dict[str, float]]:
    """(session, ts) -> the scores Jev gave that prompt's pool in #479's run."""
    out = {}
    for u in units:
        j = u.get("judge") or {}
        if j.get("asked") and not j.get("fallback"):
            out[(u["session_id"], u["ts"])] = {s: float(v) for s, v in j.get("scores") or []}
    return out


def cached_stage(cached: Dict[Tuple[str, float], Dict[str, float]], at: float,
                 log: Dict[Tuple[str, float], Dict[str, Any]]) -> Callable[[Any, List[str]], List[str]]:
    """The judge stage, answered from ``cached``. A rule never scored on the prompt
    is unknown and not injected, as :func:`mnemo.core.reflex.judge.scores` treats
    a rule the judge skipped."""
    from mnemo.core.reflex import judge

    def _stage(prompt: Any, pool: List[str]) -> List[str]:
        known = cached.get((prompt.session_id, prompt.ts)) or {}
        log[(prompt.session_id, prompt.ts)] = {
            "pool": list(pool), "unknown": [s for s in pool if s not in known]}
        return judge.chosen({s: known[s] for s in pool if s in known}, at)

    return _stage


def headroom(r: Dict[str, Any], bar: float = NOISE_BAR) -> Optional[int]:
    """Noise pairs that could join ``r`` before its share passes ``bar``; None if it already has."""
    if r["noise_share"] is None or r["noise_share"] > bar:
        return None
    x = 0
    while (r["noise"] + x + 1) <= bar * (r["labelled"] + x + 1):
        x += 1
    return x


def pick_rate(units: Sequence[Dict[str, Any]], drop: Set[str], at: float) -> List[int]:
    """[scored, picked]: how often #479's judge injected a rule it scored, ``drop`` left out."""
    scored = [v for u in units for s, v in ((u.get("judge") or {}).get("scores") or []) if s not in drop]
    return [len(scored), sum(1 for v in scored if float(v) >= at)]


def rule_picks(units: Sequence[Dict[str, Any]], at: float) -> Dict[str, List[int]]:
    """slug -> [scored, at or over ``at``] over #479's run."""
    out: Dict[str, List[int]] = {}
    for u in units:
        for s, v in (u.get("judge") or {}).get("scores") or []:
            row = out.setdefault(s, [0, 0])
            row[0] += 1
            row[1] += float(v) >= at
    return out


def unknown_by_rule(mine: Sequence[Dict[str, Any]], picks: Dict[str, List[int]]) -> List[Dict[str, Any]]:
    """Per rule, the (prompt, rule) pairs Jev never scored, and what its own as-run
    pick rate would make of them — an estimate, not a measurement."""
    n: Dict[str, int] = {}
    for u in mine:
        for s in (u.get("stage") or {}).get("unknown") or []:
            n[s] = n.get(s, 0) + 1
    rows = []
    for s in sorted(n, key=lambda x: (-n[x], x)):
        scored, picked = picks.get(s, [0, 0])
        rows.append({"slug": s, "pairs": n[s], "scored": scored, "picked": picked,
                     "expected": n[s] * picked / float(scored) if scored else None})
    return rows


def replayed(recorded: Sequence[Dict[str, Any]], mine: Sequence[Dict[str, Any]],
             labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """Question 2 and 3, replayed: the counterfactual units against #479's.

    The row is a floor: a rule Jev never scored on the prompt is not injected.
    ``unknown`` says how many such (prompt, rule) pairs there are, over how
    many rules, and what labels the judge-off runs already gave any of them.
    """
    out = rate(injected(mine, labels))
    pools = [u for u in mine if u.get("stage")]
    unknown = [(u["uid"], s) for u in pools for s in u["stage"]["unknown"]]
    known_labels = [(labels.get(uid) or {}).get(s) for uid, s in unknown]
    out.update({"requests": len(pools), "requests_recorded": sum(1 for u in recorded if asked_pool(u)),
                "requests_with_unknown": sum(1 for u in pools if u["stage"]["unknown"]),
                "unknown": {"pairs": len(unknown), "rules": len({s for _, s in unknown}),
                            "labelled": {str(k): known_labels.count(k) for k in (NOISE, MARGINAL, ON_POINT)
                                         if known_labels.count(k)}},
                "headroom": headroom(out)})
    return out


def reproduces(recorded: Sequence[Dict[str, Any]], mine: Sequence[Dict[str, Any]]) -> List[int]:
    """[prompts whose injected rules match #479's, prompts] — the replay's self-check."""
    theirs = {(u["session_id"], u["ts"]): [c["slug"] for c in u["pool"]] for u in recorded}
    same = sum(1 for u in mine if theirs.get((u["session_id"], u["ts"])) == [c["slug"] for c in u["pool"]])
    return [same, len(mine)]


def rated_rows(texts: Dict[str, str], have: Dict[str, List[Optional[str]]],
               owners: Dict[str, List[List[str]]]) -> List[Dict[str, Any]]:
    """Rules to rate: one row per distinct view #480 did not rate by both raters."""
    rows: Dict[str, Dict[str, Any]] = {}
    for key in sorted(texts):
        rid = nc.text_id(texts[key])
        if all(have.get(rid) or [None]):
            continue
        row = rows.setdefault(rid, {"id": rid, "text": texts[key], "slugs": [], "fallback": False})
        row["slugs"] += owners[key]
    return sorted(rows.values(), key=lambda r: r["id"])


def letters_for(rid: str, stores: Sequence[Dict[str, Dict[str, str]]], cols: Sequence[str]) -> List[Optional[str]]:
    """Each rater's letter for a view id, #480's first, then this tool's."""
    out = []
    for c in cols:
        out.append(next((s[c][rid] for s in stores if rid in (s.get(c) or {})), None))
    return out


# --- reading the run ---------------------------------------------------------------

def arm_vault_path(arm: str, work: Path, arm_a_work: Path) -> Path:
    return (arm_a_work if arm == "a" else work) / ("arm-" + arm) / "vault"


def gather(state: Dict[str, Any], work: Path, arm_a_work: Path) -> Dict[str, Dict[str, Any]]:
    """Per arm: its units, and the rules it injected or asked Jev about, with their view."""
    out: Dict[str, Dict[str, Any]] = {}
    for arm in ARMS:
        units = arm_units(state, arm)
        if not units:
            continue
        vault = arm_vault_path(arm, work, arm_a_work)
        inj = {c["slug"] for u in units for c in u["pool"]}
        pool = {s for u in units for s in (asked_pool(u) or [])}
        facts = {s: nc.page_facts(nc.page_of(vault, s)) for s in sorted(inj | pool)}
        out[arm] = {"units": units, "vault": vault, "injected": inj, "pool": pool, "facts": facts}
    return out


def to_rate(arms: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, str], Dict[str, List[List[str]]]]:
    """(arm/slug -> view, arm/slug -> [[arm, slug]]) for every rule the issue needs a kind of."""
    texts, owners = {}, {}
    for arm, a in arms.items():
        wanted = a["injected"] | (a["pool"] if arm == "b" else set())
        for s in sorted(wanted):
            f = a["facts"].get(s) or {}
            if f.get("view"):
                texts["%s/%s" % (arm, s)] = f["view"]
                owners["%s/%s" % (arm, s)] = [[arm, s]]
    return texts, owners


def replay_arm(arm: str, units: Sequence[Dict[str, Any]], state: Dict[str, Any], sources: Dict[str, Any],
               rewinds: Path, drop: Set[str], at: float, candidates: int) -> List[Dict[str, Any]]:
    """#479's prompts again, the ``drop`` rules retired, the judge answered from cache."""
    from mnemo.core import config
    from mnemo.core.reflex import replay
    from mnemo.core.reflex.index import load_index

    src = sources[arm]
    cached = cached_scores(units)
    uid = {(u["session_id"], u["ts"]): u["uid"] for u in units}
    if arm == "b":
        rows = (state["arms"]["b"].get("rows") or [])
        parts = []
        for k, row in enumerate(rows):
            n = row["live"]
            vault = mdo.rewind(src["vault"], rewinds / ("arm-b-live-%03d" % n) / "vault", src["order"][:n])
            s = src["sessions"][k]
            parts.append((vault, [(s, ts, t) for ts, t in s.prompts]))
    else:
        parts = [(src["vault"], src["items"])]
    out: List[Dict[str, Any]] = []
    for vault, items in parts:
        with mdo.arm_vault(vault):
            cfg = config.load_config()
            index = retire(load_index(vault) or {"docs": {}, "postings": {}, "doc_count": 0}, drop)
            prompts = [replay.Prompt(session_id=s.sid, project=src["agent"], ts=ts, text=text)
                       for s, ts, text in items]
            log: Dict[Tuple[str, float], Dict[str, Any]] = {}
            result = replay.run(prompts, index, {}, reflex_cfg=cfg.get("reflex") or {},
                                judge=cached_stage(cached, at, log), judge_candidates=candidates)
        got: Dict[Tuple[str, float], List[str]] = {}
        for inj in result.injections:
            got.setdefault((inj.session_id, inj.ts), []).append(inj.slug)
        for p in prompts:
            key = (p.session_id, p.ts)
            out.append({"uid": uid.get(key) or mdo.mrg.unit_id(p.session_id, p.ts, p.text),
                        "session_id": p.session_id, "ts": p.ts,
                        "pool": [{"slug": s} for s in got.get(key, [])], "stage": log.get(key)})
    return out


# --- the report --------------------------------------------------------------------

def build(arms: Dict[str, Dict[str, Any]], letters: Dict[str, Dict[str, List[Optional[str]]]],
          labels: Dict[str, Dict[str, int]],
          replays: Dict[str, Dict[str, List[Dict[str, Any]]]], at: float = 0.4) -> Dict[str, Any]:
    out: Dict[str, Any] = {"arms": {}}
    for arm, a in arms.items():
        lt = letters.get(arm) or {}
        pairs = injected(a["units"], labels)
        both = {s for s, x in lt.items() if kind(x)["both"]}
        either = {s for s, x in lt.items() if kind(x)["either"]}
        known = {s for s, x in lt.items() if kind(x)["rated"]}
        row: Dict[str, Any] = {
            "on": rate(pairs),
            "rules": by_rule(pairs, lt, {s: (a["facts"].get(s) or {}).get("type") for s in a["facts"]}),
            "pool_rules": len(a["pool"]), "pool_rules_rated": len(a["pool"] & known),
            "dropped": {"both": dropped(pairs, both), "either": dropped(pairs, either)},
            "requests": {"both": requests(a["units"], both, known),
                         "either": requests(a["units"], either, known)},
        }
        rp = replays.get(arm) or {}
        if rp:
            row["replay_check"] = reproduces(a["units"], rp.get("none") or [])
            row["replayed"] = {k: replayed(a["units"], rp[k], labels) for k in ("both", "either") if k in rp}
            row["pick_rate"] = {k: pick_rate(a["units"], d, at) for k, d in (("both", both), ("either", either))}
            picks = rule_picks(a["units"], at)
            for k, r in row["replayed"].items():
                r["unknown"]["by_rule"] = unknown_by_rule(rp[k], picks)
        out["arms"][arm] = row
    return out


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def _rate_line(r: Dict[str, Any]) -> str:
    lo, hi = r["ci"]
    verdict = "n/a" if r["clears"] is None else ("clears" if r["clears"] else "misses")
    return ("noise %d/%d = %s [%s-%s], on-point %d, marginal %d, unlabelled %d -> %s"
            % (r["noise"], r["labelled"], _pct(r["noise_share"]), _pct(lo), _pct(hi),
               r["on_point"], r["marginal"], r["unlabelled"], verdict))


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["BAR (declared in #479): noise <= %d%% of labelled judge-on injections" % int(NOISE_BAR * 100)]
    for arm, r in data["arms"].items():
        small = "" if arm == "b" else " — too few pairs to decide"
        lines += ["", "== arm (%s), judge on%s" % (arm, small),
                  "  as run:              " + _rate_line(r["on"]),
                  "  pool rules %d, %d with a kind from both raters" % (r["pool_rules"], r["pool_rules_rated"]),
                  "  by rule: noise/marginal/on-point/unlabelled  raters  type  slug"]
        for x in r["rules"]:
            lines.append("    %2d/%2d/%2d/%d  %-5s %-10s %s" % (
                x["noise"], x["marginal"], x["on_point"], x["unlabelled"],
                "/".join(y or "-" for y in x["letters"]), x["type"] or "-", x["slug"]))
        for who in ("both", "either"):
            d = r["dropped"][who]
            lines.append("  drop G/N (%s rater%s), %d injected rule(s), pairs struck: %s"
                         % (who, "s" if who == "both" else "", d["rules_dropped"], _rate_line(d)))
            rp = (r.get("replayed") or {}).get(who)
            if rp:
                lines.append("  drop G/N (%s), replayed:%s %s" % (who, " " * (5 - len(who)), _rate_line(rp)))
                un = rp["unknown"]
                sp = r["pick_rate"][who]
                lines.append("      Jev requests %d (as run %d, saved %d); %d request(s) hold %d (prompt, rule) "
                             "pair(s) over %d rule(s) Jev never scored there — not injected, so a floor"
                             % (rp["requests"], rp["requests_recorded"], rp["requests_recorded"] - rp["requests"],
                                rp["requests_with_unknown"], un["pairs"], un["rules"]))
                room = ("none, already over" if rp["headroom"] is None
                        else "%d more noise pair(s)" % rp["headroom"])
                lines.append("      headroom before the bar is missed: %s; as run, the judge injected %d of %d "
                             "scored non-dropped rule(s); judge-off labels on the unknown pairs: %s"
                             % (room, sp[1], sp[0], un["labelled"] or "none"))
                for x in un["by_rule"]:
                    lines.append("        %4d unscored pair(s), as run picked %d of %d scored -> ~%s injected: %s"
                                 % (x["pairs"], x["picked"], x["scored"],
                                    "?" if x["expected"] is None else "%.1f" % x["expected"], x["slug"]))
            q = r["requests"][who]
            lines.append("      as run: %d request(s), %d on pools made only of those rules, %d with one "
                         "or more, %d with a rule of no kind" % (q["requests"], q["all_dropped"],
                                                                q["any_dropped"], q["with_unrated"]))
        if r.get("replay_check"):
            c = r["replay_check"]
            lines.append("  replay with nothing dropped reproduces #479's injections on %d of %d prompt(s)"
                         % (c[0], c[1]))
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="rules to rate, calls, cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="both raters label what is pending (model calls)")
    ap.add_argument("--work", type=Path, default=None, help="#479's --work (default ~/%s)" % mdo.DEFAULT_WORK)
    ap.add_argument("--arm-a-work", type=Path, default=None, help="#479's --arm-a-work (default: --work)")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="transcripts, for the replay (default ~/%s)" % mdo.DEFAULT_CORPUS)
    ap.add_argument("--no-replay", action="store_true", help="skip the replayed counterfactual")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    home = Path.home()
    work = (args.work or home / mdo.DEFAULT_WORK).expanduser().resolve()
    arm_a_work = (args.arm_a_work or work).expanduser().resolve()
    corpus = (args.corpus or home / mdo.DEFAULT_CORPUS).expanduser()
    state = nc._read(work / mdo.JUDGE_NAME, {})
    if not state.get("arms"):
        print("error: no judge-on run in %s (tools/measure_day_one.py --judge)" % work, file=sys.stderr)
        return 1
    labels: Dict[str, Dict[str, int]] = {}
    for base in (work, arm_a_work):
        store = nc._read(base / "labels.json", {}).get(mdo.mrr.column(mdo.DEFAULT_RATER), {})
        for uid, row in store.items():
            for slug, value in row.items():
                labels.setdefault(uid, {}).setdefault(slug, value)

    vault = paths.vault_root(config.load_config())
    out_dir = vault / ".mnemo" / nc.OUT_DIR
    theirs = nc._read(out_dir / nc.LABELS_NAME, {}).get("labels") or {}
    rated_path, labels_path = out_dir / RATED_NAME, out_dir / LABELS_NAME
    mine: Dict[str, Any] = nc._read(labels_path, {"calls": 0, "usd": 0.0, "labels": {}})
    cols = [mdk.column(m) for m in mdk.RATERS]

    arms = gather(state, work, arm_a_work)
    texts, owners = to_rate(arms)
    if rated_path.exists():
        rated = nc._read(rated_path, {}).get("rated") or []
    else:
        if any(mine.get("labels", {}).values()):
            raise SystemExit("error: %s holds labels but %s is gone; refusing to redraw" % (labels_path, rated_path))
        have = {nc.text_id(t): letters_for(nc.text_id(t), [theirs], cols) for t in texts.values()}
        rated = rated_rows(texts, have, owners)
        out_dir.mkdir(parents=True, exist_ok=True)
        nc._write(rated_path, {"rated": rated})
    todo = nc.plan(rated, mine.get("labels", {}))

    if args.dry_run:
        for arm, a in arms.items():
            print("arm (%s): %d injected rule(s), %d pool rule(s)" % (arm, len(a["injected"]), len(a["pool"] | a["injected"])))
        e = mdk.estimate(todo)
        print("rules to rate (#480 never did): %d; pending %d label(s) in %d call(s) of <= %d; %d of %d "
              "budgeted calls used" % (len(rated), e["pages"], e["calls"], BATCH, mine.get("calls", 0), MAX_CALLS))
        print("tokens: ~%d in, ~%d out; API-price equivalent ~$%.2f — subscription usage on a Max plan"
              % (e["input_tokens"], e["output_tokens"], e["usd"]))
        print("Jev requests: 0 — the replay answers from #479's cached scores")
        if mine.get("calls", 0) + e["calls"] > MAX_CALLS:
            print("WARNING: pending calls exceed the %d-call budget; --send stops at it" % MAX_CALLS)
        return 0

    if args.send:
        provider = llm.resolve(config.load_config())
        timeout = max(300, int(config.load_config()["extraction"]["subprocessTimeout"]))

        def ask(prompt: str, system: str, model: str) -> Tuple[str, float]:
            resp = provider(prompt, system=system, model=model, timeout=timeout)
            return resp.text, float(resp.total_cost_usd or 0.0)

        with tempfile.TemporaryDirectory(prefix="mnemo-judge-noise-kinds-") as scratch:
            with mdo.mrr.mrl._chdir(scratch):
                for line in mdk.send(todo, mine, ask, lambda s: nc._write(labels_path, s), max_calls=MAX_CALLS):
                    print(line, file=sys.stderr)

    stores = [theirs, mine.get("labels", {})]
    letters = {arm: {s: letters_for(nc.text_id(f["view"]), stores, cols)
                     for s, f in a["facts"].items() if f.get("view")} for arm, a in arms.items()}
    replays: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    if not args.no_replay:
        sources = mdo.judge_sources(work, arm_a_work, corpus)
        chosen = state.get("settings") or mdo.shipped_judge()
        for arm, a in arms.items():
            if arm not in sources:
                continue
            lt = letters[arm]
            drops = {"none": set(), "both": {s for s, x in lt.items() if kind(x)["both"]},
                     "either": {s for s, x in lt.items() if kind(x)["either"]}}
            replays[arm] = {k: replay_arm(arm, a["units"], state, sources, work / "judge-rewind", d,
                                          float(chosen["injectAt"]), int(chosen["candidates"]))
                            for k, d in drops.items()}
    data = build(arms, letters, labels, replays, at=float((state.get("settings") or {}).get("injectAt", 0.4)))
    data["calls"] = {"used": mine.get("calls", 0), "budget": MAX_CALLS, "usd": mine.get("usd", 0.0),
                     "rated_here": len(rated)}
    if args.json:
        print(json.dumps(data, indent=1, default=sorted))
        return 0
    print("kinds: #480's %s, plus %d rule(s) rated here in %d of %d call(s)\n"
          % (out_dir / nc.LABELS_NAME, len(rated), mine.get("calls", 0), MAX_CALLS))
    print("\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
