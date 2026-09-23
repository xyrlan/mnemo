"""How often does an on-point rule reach the prompt, and where is the rest lost? (#455)

Usage:
    PYTHONPATH=src python3 tools/measure_reflex_reach.py --dry-run          # pools, calls, tokens; calls nothing
    PYTHONPATH=src python3 tools/measure_reflex_reach.py --send             # label every pending pair
    PYTHONPATH=src python3 tools/measure_reflex_reach.py --send --limit 2   # a smoke run: the first 2 calls
    PYTHONPATH=src python3 tools/measure_reflex_reach.py                    # the report, local
    PYTHONPATH=src python3 tools/measure_reflex_reach.py --json              # the same as data

#434 showed an injected on-point rule changes the answer (+31.1 pp). #411
labelled only the top 3 of the ranking, so a rule ranked 4th or lower was
invisible to every number about the reflex. This asks the question those
numbers could not: of the prompts that have an on-point rule anywhere in the
top 10, how many get one, and at which stage the others lose it.

**Population.** The 300 prompts of ``reflex-sample.json`` (#411), same
``uid``s, re-read from their transcripts and replayed through
``reflex.decide`` with today's index and config, keeping the top
:data:`DEPTH` rules the vault had already learned when the prompt was typed
(``measure_reflex_gate.held_at`` — no hindsight). Frozen on first run into
``pools.json`` and never rebuilt under existing labels.

**Why every rank is labelled, not only 4–10.** The issue asked for ranks
4–10 on top of #411's top-3 labels. Between 2026-09-20 and 09-22 the vault
was audited and deduplicated, and the index lost 58 of the labelled slugs:
111 of the 870 labelled top-3 pairs are no longer rankable at all, and only
191 of the 290 recoverable prompts keep the same top-3 set. Mixing
Fable-5.1 labels on the old top 3 with a second rater on a new 4–10 would
make "reach" a ratio of two raters over two vaults. So one blind rater
(:data:`DEFAULT_RATER`, through ``core.llm``) labels every pair of today's top
10, and the Fable labels still in the pool are the agreement check — the
report prints the confusion matrix, and a mixed-rater reach beside the
single-rater one.

**The wording.** #411's 0/1/2 scale: 0 noise, 1 marginal ("same area, not
this prompt"), 2 on-point. The instruction Fable's raters were given was not
saved in the repo, so :data:`RATER_SYSTEM` restates the scale with the
shipped judge's own true/false criteria (``judge.question``) as the line
between 2 and the rest. How close that is to what Fable was told is exactly
what the agreement check measures.

**Stages, per prompt with ≥1 on-point rule in the top 10** (first stage that
loses it, in pipeline order):

- **ranking** — on-point rules only at ranks 4–10: the judge's pool
  (``reflex.judge.candidates`` = 3) never contains one;
- **gate** — one is in the top 3 and the gate injects none of them. Shipped:
  ``decide``'s own accepted list. Judge: ``judge.chosen`` over the
  ``v0_reflex`` probabilities in ``reflex-scores.json`` at ``injectAt``. A top-3
  pair Jev never scored (it entered the top 3 after the audit) gives a range:
  low counts it dropped, high counts it kept;
- **cap / dedupe** — shipped gate only: every prompt of the prompt's session
  is replayed in order through the hook's cache and ``maxEmissionsPerSession``.
  A deduped on-point rule *was* in the session already, so it is shown apart
  from the cap. The replay cannot tell this for the judge (its probabilities
  exist for the sampled prompts only) nor for exported rules, so neither is
  simulated;
- **reached**.

**Headline.** reach = prompts that get ≥1 on-point rule / prompts with ≥1 in
the top 10, with a Wilson 95% interval, and a population-weighted point that
undoes #411's 220-fired / 80-silenced stratification. Times the #434 lift
(:data:`LIFT_PP`) it is the expected share of those prompts whose answer an
injected rule changes — which is what each lever is worth.

**Cost.** Max-plan usage; the dollar figure is the API-price equivalent the
CLI reports (#441), never money spent.

First run, 2026-09-23, rater ``claude-sonnet-5``. The dry run predicted 2,709
pairs in 72 calls, ~642k input tokens, ~$1.45; the run took 72 calls (one
smoke call, then 71) at $5.99 by the CLI's own count. 290 of the 300 prompts
were recovered from transcripts and 286 were fully labelled. 172 hold an
on-point rule in the top 10, 137 of them in the top 3. On-point pairs by rank
1..10: 95 65 36 32 26 25 21 19 16 14.

- **The raters disagree.** On the 748 pairs both labelled, exact agreement
  is 66% and kappa for on-point is 0.38. Sonnet calls 196 of them on-point
  and Fable 62; 106 of the gap are Fable's "marginal". Every row is therefore
  read under Sonnet's labels and again under mixed labels (Fable's wherever
  it labelled the pair).
- **Reach, Sonnet / mixed labels.** Shipped gate: 48.8% / 21.8%, and 29.7% /
  15.8% after the session replay. Judge at 0.6: 36.0% / 28.6%. Judge at 0.4:
  57.6% / 34.6%. Every top 3: 79.7% / 41.4%.
- **Where it is lost (Sonnet labels).** Ranking loses 35 of 172 (20%): the
  on-point rule sits only at ranks 4–10. Past that, the shipped gate loses 53
  and the judge at 0.6 loses 75. After the shipped gate, the session cap
  loses 7. Dedupe accounts for 26 more, but each of those rules had already
  been injected earlier in the same session. Under mixed labels "ranking"
  is inflated by construction (strict labels above rank 3, generous below).
- **Levers, worst case over both label sets, times the +31.1 pp lift.**
  Judge on by default at injectAt 0.4 instead of the shipped gate: at least
  +8.7 pp reach, about +2.7 changed answers per 100 such prompts. Judge on
  at the shipped 0.6: −12.8 / +6.8 pp; its sign depends on the rater, so it
  is not a finding. Lowering injectAt 0.6 → 0.4 with the judge on: +21.5 /
  +6.0 pp. Lifting the cap: +4.1 / +0.8 pp. Widening the judge's pool to 10:
  +9.1 pp under Sonnet's labels, but only as a projection, because Jev never
  scored a rule below rank 3.
- **What a lower bar costs is #411's number, not this one:** at 0.4 the judge
  injects 209 rules at 15% noise, against 111 at 10% noise at 0.6 and 298
  at 56% noise for the shipped gate.

Files, under ``<vault>/.mnemo/reflex-reach/``: ``pools.json`` and
``labels.json`` (per rater column — model and :data:`RATER_SYSTEM` hash — per
uid, per slug; saved after every call).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrg = _sibling("measure_reflex_gate")
mrl = _sibling("measure_rule_lift")

OUT_DIR = "reflex-reach"
POOLS_NAME = "pools.json"
LABELS_NAME = "labels.json"

DEFAULT_RATER = "claude-sonnet-5"
#: The rater whose top-3 labels the agreement check reads.
FABLE_RATER = mrg.DEFAULT_RATER

#: How deep the ranking is labelled.
DEPTH = 10
#: The judge's pool (``reflex.judge.candidates``) and #411's labelled depth.
POOL = 3

ON_POINT, MARGINAL, NOISE = mrg.ON_POINT, mrg.MARGINAL, mrg.NOISE

#: Rules per rater call. A prompt is never split across two calls.
BATCH = 40

#: The #434 lift and its 95% CI, in percentage points (PR #451).
LIFT_PP = (31.1, 19.7, 42.6)

#: The judge bars the report grades; 0.6 is the shipped ``injectAt``.
JUDGE_BARS = (0.4, 0.6, 0.7)
SHIPPED_AT = 0.6

#: Output tokens per labelled rule, for the dry run: ``"12": 2,`` and change.
LABEL_OUT_TOKENS = 6

RATER_SYSTEM = (
    "You label whether a stored engineering rule should be shown to an AI coding "
    "assistant before it answers a developer's message. You see only the message "
    "and the rules; nothing else about the session. Each MESSAGE is followed by "
    "numbered RULES. Label every rule against its own message only:\n"
    "2 - on-point: the rule is about the same problem, component or pitfall the "
    "message is about, and would change what the assistant does;\n"
    "1 - marginal: same area or project, but not what this message is about;\n"
    "0 - noise: about something else, too general to change anything, or the "
    "message is too short or context-dependent to tell what it is about.\n"
    "Reply with one JSON object mapping each rule number to 0, 1 or 2, and "
    "nothing else."
)

STAGES = ("reached", "cap", "deduped", "gate", "ranking")

#: The one lever that is a projection, not a measured row: Jev never scored a
#: rule below rank 3, so its keep rate there is borrowed from the top 3.
PROJECTED = "judge pool 3 -> 10 (ranking x keep)"


# --- the pure part ---------------------------------------------------------------

def column(model: str) -> str:
    """Where one rater's labels are filed: model plus a hash of the instruction."""
    return "%s@%s" % (model, hashlib.sha256(RATER_SYSTEM.encode("utf-8")).hexdigest()[:8])


def pool_of(scores: Sequence[Tuple[str, float]], learned_at: Dict[str, Optional[float]],
            ts: float, *, depth: int = DEPTH) -> List[Tuple[str, float]]:
    """The top ``depth`` rules the vault held at ``ts`` — #411's no-hindsight cut."""
    return mrg.held_at(scores, learned_at, ts, top=depth)


def shipped_of(accepted: Iterable[str], pool: Sequence[str]) -> List[str]:
    """What the shipped gate injects, in rank order, of rules the vault held.

    ``decide`` ranks today's vault; an accepted rule learned after the prompt
    could not have been injected into it, so it is not counted as reach.
    """
    accepted = set(accepted)
    return [slug for slug in pool if slug in accepted]


def replay_session(prompts: Sequence[Tuple[str, List[str]]], cap: int) -> Dict[str, Dict[str, str]]:
    """The hook's cap and dedupe over one session, in the order it was typed.

    ``prompts`` is ``(uid, what the gate accepted)`` per prompt, oldest first.
    Returns, per uid, each accepted slug's fate: ``injected``, ``deduped``
    (already injected earlier in the session) or ``cap`` (the session had
    reached ``maxEmissionsPerSession`` before this prompt). Mirrors the hook:
    the cap is checked once, before the prompt, against rules injected so
    far, so one prompt can take the count past it.
    """
    told: set = set()
    count = 0
    out: Dict[str, Dict[str, str]] = {}
    for uid, accepted in prompts:
        fate: Dict[str, str] = {}
        if count >= cap:
            fate = {slug: "cap" for slug in accepted}
        else:
            for slug in accepted:
                if slug in told:
                    fate[slug] = "deduped"
                else:
                    fate[slug] = "injected"
                    told.add(slug)
                    count += 1
        out[uid] = fate
    return out


def batches(units: Sequence[Dict[str, Any]], done: Dict[str, Dict[str, int]],
            *, size: int = BATCH) -> List[List[Dict[str, Any]]]:
    """Unlabelled pairs grouped into calls; a prompt is never split.

    Each entry is ``{"uid", "prompt", "rules": [(slug, text)]}``. A pair with
    a label is never asked again, so an interrupted run resumes at the call
    it stopped on.
    """
    out: List[List[Dict[str, Any]]] = []
    current: List[Dict[str, Any]] = []
    n = 0
    for unit in units:
        have = done.get(unit["uid"], {})
        rules = [(c["slug"], c["text"]) for c in unit["pool"]
                 if c.get("text") and c["slug"] not in have]
        if not rules:
            continue
        if current and n + len(rules) > size:
            out.append(current)
            current, n = [], 0
        current.append({"uid": unit["uid"], "prompt": unit["prompt"], "rules": rules})
        n += len(rules)
    if current:
        out.append(current)
    return out


def numbered(batch: Sequence[Dict[str, Any]]) -> List[Tuple[str, str]]:
    """(uid, slug) per rule number, 1-based, in the order the call lists them."""
    return [(entry["uid"], slug) for entry in batch for slug, _ in entry["rules"]]


def rater_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    """The call's text: messages and rule bodies, no slug, rank, score or gate."""
    parts: List[str] = []
    n = 0
    for m, entry in enumerate(batch, 1):
        parts.append("## MESSAGE %d\n%s\n\nRULES:" % (m, entry["prompt"]))
        for _, text in entry["rules"]:
            n += 1
            parts.append("[%d] %s" % (n, text))
        parts.append("")
    return "\n".join(parts).strip()


def parse_labels(text: str, batch: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """``{uid: {slug: 0|1|2}}`` for every rule the rater answered legibly.

    A missing number or a value off the scale stays pending and is asked
    again on the next run; nothing is guessed.
    """
    s = (text or "").strip()
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        obj = json.loads(s[start:end + 1])
    except ValueError:
        return {}
    out: Dict[str, Dict[str, int]] = {}
    for n, (uid, slug) in enumerate(numbered(batch), 1):
        value = obj.get(str(n))
        if isinstance(value, str) and value.strip().isdigit():
            value = int(value.strip())
        if isinstance(value, bool) or value not in (NOISE, MARGINAL, ON_POINT):
            continue
        out.setdefault(uid, {})[slug] = int(value)
    return out


def judge_kept(unit: Dict[str, Any], jev: Dict[str, float], at: float,
               *, unscored: bool) -> List[str]:
    """What the judge injects from the top :data:`POOL`, by ``judge.chosen``.

    ``unscored`` decides a top-3 pair Jev was never asked about: ``True``
    counts it kept (the high end of the range), ``False`` dropped.
    """
    from mnemo.core.reflex import judge

    here: Dict[str, float] = {}
    for c in unit["pool"][:POOL]:
        if c["slug"] in jev:
            here[c["slug"]] = float(jev[c["slug"]])
        elif unscored:
            here[c["slug"]] = 1.0
    return judge.chosen(here, at)


def stage(unit: Dict[str, Any], label: Dict[str, int], injected: Sequence[str],
          *, gated: Optional[Sequence[str]] = None,
          fate: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Where this prompt's on-point rule is lost, or ``None`` when it has none.

    ``gated`` is what the gate chose, ``fate`` what the session replay did to
    each of those; without ``fate`` the gate's choice is what is injected.
    """
    hits = {c["slug"] for c in unit["pool"] if label.get(c["slug"]) == ON_POINT}
    if not hits:
        return None
    if hits & set(injected):
        return "reached"
    if fate is not None and gated is not None:
        lost = [fate.get(slug) for slug in gated if slug in hits]
        if "cap" in lost:
            return "cap"
        if "deduped" in lost:
            return "deduped"
    if hits & {c["slug"] for c in unit["pool"][:POOL]}:
        return "gate"
    return "ranking"


def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def weights(units: Sequence[Dict[str, Any]], population: Dict[str, int]) -> Dict[str, float]:
    """Per-uid weight undoing the fired/silenced stratification of the sample.

    ``population`` is how many units of each stratum the whole replay had;
    a unit weighs its stratum's population over its stratum's count here.
    """
    here = {"fired": 0, "silenced": 0}
    for u in units:
        here["fired" if u["stratum_fired"] else "silenced"] += 1
    out = {}
    for u in units:
        key = "fired" if u["stratum_fired"] else "silenced"
        out[u["uid"]] = population.get(key, 0) / here[key] if here[key] else 0.0
    return out


def tally(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
          staged: Callable[[Dict[str, Any]], Optional[str]], w: Dict[str, float]) -> Dict[str, Any]:
    """One gate's row: prompts per stage, reach with its interval and weighted point."""
    counts = {s: 0 for s in STAGES}
    wsum = {s: 0.0 for s in STAGES}
    for u in units:
        if u["uid"] not in labels:
            continue
        s = staged(u)
        if s is None:
            continue
        counts[s] += 1
        wsum[s] += w.get(u["uid"], 1.0)
    n = sum(counts.values())
    wn = sum(wsum.values())
    lo, hi = wilson(counts["reached"], n)
    return {"counts": counts, "n": n, "reach": counts["reached"] / n if n else None,
            "ci": [lo, hi], "reach_weighted": wsum["reached"] / wn if wn else None,
            "weighted_shares": {s: (wsum[s] / wn if wn else None) for s in STAGES}}


def agreement(a: Dict[str, Dict[str, int]], b: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """3×3 confusion over pairs both raters labelled, exact agreement, and
    Cohen's kappa for on-point against the rest — the only line reach reads."""
    matrix = [[0] * 3 for _ in range(3)]
    for uid, row in a.items():
        for slug, x in row.items():
            y = (b.get(uid) or {}).get(slug)
            if y is not None:
                matrix[x][y] += 1
    n = sum(map(sum, matrix))
    if not n:
        return {"n": 0, "matrix": matrix, "exact": None, "kappa_on_point": None}
    exact = sum(matrix[i][i] for i in range(3)) / n
    both = matrix[2][2]
    a2 = sum(matrix[2])
    b2 = sum(row[2] for row in matrix)
    observed = (both + (n - a2 - b2 + both)) / n
    expected = (a2 * b2 + (n - a2) * (n - b2)) / (n * n)
    kappa = (observed - expected) / (1 - expected) if expected < 1 else None
    return {"n": n, "matrix": matrix, "exact": exact, "kappa_on_point": kappa,
            "on_point": {"a": a2, "b": b2, "both": both}}


def rows_for(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
             jev: Dict[str, Dict[str, float]], w: Dict[str, float]) -> Dict[str, Any]:
    """Every gate's row and the judge's keep rate under one set of labels."""

    def shipped(u, *, session: bool):
        lab = labels[u["uid"]]
        if not session:
            return stage(u, lab, u["shipped"])
        fate = u.get("fate") or {}
        injected = [s for s in u["shipped"] if fate.get(s) == "injected"]
        return stage(u, lab, injected, gated=u["shipped"], fate=fate)

    def judged(at, unscored):
        return lambda u: stage(u, labels[u["uid"]],
                               judge_kept(u, jev.get(u["uid"], {}), at, unscored=unscored))

    rows: Dict[str, Any] = {
        "shipped, gate only": tally(units, labels, lambda u: shipped(u, session=False), w),
        "shipped, with cap/dedupe": tally(units, labels, lambda u: shipped(u, session=True), w),
    }
    for at in JUDGE_BARS:
        rows["judge >= %g, low" % at] = tally(units, labels, judged(at, False), w)
        rows["judge >= %g, high" % at] = tally(units, labels, judged(at, True), w)
    rows["every top 3"] = tally(units, labels, lambda u: stage(
        u, labels[u["uid"]], [c["slug"] for c in u["pool"][:POOL]]), w)

    # How often the judge keeps an on-point rule it is shown, on scored pairs
    # only — the rate the "ranking depth" projection multiplies by.
    keep: Dict[str, Tuple[int, int]] = {}
    for at in JUDGE_BARS:
        k = n = 0
        for u in units:
            here = jev.get(u["uid"], {})
            for c in u["pool"][:POOL]:
                if labels[u["uid"]].get(c["slug"]) == ON_POINT and c["slug"] in here:
                    n += 1
                    k += here[c["slug"]] >= at
        keep["%g" % at] = (k, n)
    return {"rows": rows, "judge_keep": keep}


def report(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
           jev: Dict[str, Dict[str, float]], population: Dict[str, int],
           *, fable: Optional[Dict[str, Dict[str, int]]] = None) -> Dict[str, Any]:
    """Every row the recommendation reads, on the fully labelled prompts.

    With ``fable`` the same rows are read a second time under mixed labels —
    Fable's wherever it labelled the pair, this rater's elsewhere — because
    the two raters draw the on-point line in different places and a lever
    that only one of them supports is not a finding.
    """
    w = weights(units, population)
    labelled = [u for u in units if u["uid"] in labels
                and all(c["slug"] in labels[u["uid"]] for c in u["pool"] if c.get("text"))]

    def has(lab, u, depth=DEPTH):
        return any(lab[u["uid"]].get(c["slug"]) == ON_POINT for c in u["pool"][:depth])

    out: Dict[str, Any] = {
        "prompts": len(units), "labelled_prompts": len(labelled),
        "pairs": sum(len(u["pool"]) for u in units),
        "with_on_point": sum(1 for u in labelled if has(labels, u)),
        "with_on_point_top3": sum(1 for u in labelled if has(labels, u, POOL)),
        "unscored_top3": sum(1 for u in labelled for c in u["pool"][:POOL]
                             if c["slug"] not in jev.get(u["uid"], {})),
        "on_point_by_rank": [sum(1 for u in labelled if len(u["pool"]) > r
                                 and labels[u["uid"]].get(u["pool"][r]["slug"]) == ON_POINT)
                             for r in range(DEPTH)],
    }
    out.update(rows_for(labelled, labels, jev, w))
    if fable is not None:
        out["agreement"] = agreement(fable, labels)
        mixed = {uid: dict(row, **(fable.get(uid) or {})) for uid, row in labels.items()}
        out["mixed"] = rows_for(labelled, mixed, jev, w)
    return out


def lever_values(part: Dict[str, Any]) -> Dict[str, float]:
    """Δreach per lever, each against the row it would replace, like for like.

    The judge rows have no session replay (its probabilities exist for the
    sampled prompts only), so the judge is compared with the shipped gate's
    gate-only row, not with the row after cap and dedupe. A deduped rule was
    injected earlier in the same session, so dedupe is not a loss and not a
    lever; the cap is.
    """
    rows = part["rows"]

    def reach(name):
        return rows[name]["reach"] or 0.0

    j = rows["judge >= %g, low" % SHIPPED_AT]
    k, n = part["judge_keep"]["%g" % SHIPPED_AT]
    capped = rows["shipped, with cap/dedupe"]
    return {
        "judge default-on (injectAt 0.6)": reach("judge >= 0.6, low") - reach("shipped, gate only"),
        "judge default-on (injectAt 0.4)": reach("judge >= 0.4, low") - reach("shipped, gate only"),
        "judge on, injectAt 0.6 -> 0.4": reach("judge >= 0.4, low") - reach("judge >= 0.6, low"),
        "judge on, injectAt 0.6 -> 0.7": reach("judge >= 0.7, low") - reach("judge >= 0.6, low"),
        PROJECTED:
            (j["counts"]["ranking"] / j["n"] * (k / n)) if j["n"] and n else 0.0,
        "lift the session cap (shipped)":
            capped["counts"]["cap"] / capped["n"] if capped["n"] else 0.0,
    }


def levers(part: Dict[str, Any]) -> List[str]:
    """Each lever as Δreach × the #434 lift: answers changed per 100 prompts
    that hold an on-point rule in the top 10."""
    lift, lo, hi = LIFT_PP
    return ["  %-38s %+5.1f pp reach -> %+4.1f answers per 100 [%+.1f, %+.1f]" % (
        name, 100 * d, d * lift, d * lo, d * hi) for name, d in lever_values(part).items()]


def recommendation(data: Dict[str, Any]) -> str:
    """The measured lever whose *worst* reach gain over the label sets is
    largest.

    The two raters draw the on-point line in different places (the report's
    agreement block), so a lever is only as good as it is under the stricter
    of them; one whose sign flips between them is named as unsettled rather
    than recommended. The pool-depth lever is a projection and is reported
    beside the pick, never as it.
    """
    parts = [lever_values(data)] + ([lever_values(data["mixed"])] if "mixed" in data else [])
    worst = {name: min(p[name] for p in parts) for name in parts[0]}
    flips = sorted(name for name in parts[0]
                   if min(p[name] for p in parts) < 0 < max(p[name] for p in parts))
    measured = [name for name in worst if name != PROJECTED]
    best = max(measured, key=lambda name: worst[name])
    line = "%s: at least %+.1f pp reach under every label set, about %+.1f changed answers " \
           "per 100 prompts that hold an on-point rule" % (best, 100 * worst[best],
                                                          worst[best] * LIFT_PP[0])
    line += "; projected, not measured: %s %+.1f pp" % (PROJECTED, 100 * parts[0][PROJECTED])
    if flips:
        line += "; sign depends on the rater: " + ", ".join(flips)
    return line


def estimate(units: Sequence[Dict[str, Any]], done: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    todo = batches(units, done)
    chars = sum(len(rater_prompt(b)) for b in todo)
    rules = sum(len(numbered(b)) for b in todo)
    tokens_in = chars // 4 + len(todo) * (len(RATER_SYSTEM) // 4 + mrl.CLI_OVERHEAD_TOKENS)
    tokens_out = rules * LABEL_OUT_TOKENS
    return {"calls": len(todo), "pairs": rules, "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "usd": (tokens_in * mrl.USD_IN_PER_MTOK + tokens_out * mrl.USD_OUT_PER_MTOK) / 1e6}


def _pct(x: Optional[float]) -> str:
    return "  n/a" if x is None else "%4.1f%%" % (100 * x)


def _table(part: Dict[str, Any]) -> List[str]:
    lines = ["  %-26s %5s  %-15s %8s   %s" % ("gate", "reach", "95% CI", "weighted",
                                             "  ".join("%7s" % s for s in STAGES))]
    for name, row in part["rows"].items():
        lo, hi = row["ci"]
        lines.append("  %-26s %5s  [%s, %s]  %8s   %s" % (
            name, _pct(row["reach"]), _pct(lo), _pct(hi), _pct(row["reach_weighted"]),
            "  ".join("%7d" % row["counts"][s] for s in STAGES)))
    for at, (k, n) in part["judge_keep"].items():
        lines.append("  judge >= %s keeps %d of %d scored on-point top-%d pairs" % (at, k, n, POOL))
    lines += ["  levers, x the #434 lift %+.1f pp [%+.1f, %+.1f]:" % LIFT_PP]
    lines += ["  " + line for line in levers(part)]
    return lines


def report_lines(data: Dict[str, Any], rater: str) -> List[str]:
    lines = [
        "%d prompts (%d fully labelled by %s), %d (prompt, rule) pairs over the top %d"
        % (data["prompts"], data["labelled_prompts"], rater, data["pairs"], DEPTH),
        "%d prompts hold >=1 on-point rule in the top %d; %d of them in the top %d"
        % (data["with_on_point"], DEPTH, data["with_on_point_top3"], POOL),
        "on-point pairs by rank 1..%d: %s" % (DEPTH, " ".join(map(str, data["on_point_by_rank"]))),
        "top-%d pairs Jev never scored: %d (judge rows give a low/high range)"
        % (POOL, data["unscored_top3"]),
        "",
        "--- labels: %s" % rater,
    ]
    lines += _table(data)
    if "agreement" in data:
        a = data["agreement"]
        lines += ["", "--- agreement with %s on %d shared pairs: exact %s, kappa(on-point) %s"
                  % (FABLE_RATER, a["n"], _pct(a["exact"]),
                     "n/a" if a["kappa_on_point"] is None else "%.2f" % a["kappa_on_point"]),
                  "  rows %s 0/1/2, columns %s 0/1/2: %s" % (FABLE_RATER, rater, a["matrix"]),
                  "", "--- labels: %s where it labelled the pair, %s elsewhere"
                  % (FABLE_RATER, rater)]
        lines += _table(data["mixed"])
    lines += ["", "recommendation: " + recommendation(data)]
    return lines


# --- the vault ----------------------------------------------------------------------

def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8")


def _build(vault: Path) -> Dict[str, Any]:
    """The frozen pools: today's top 10 per sampled prompt, the shipped gate's
    choice, and what the session replay did to it."""
    from mnemo.core import config as cfg_mod
    from mnemo.core.mcp import rerank, tools as mcp_tools
    from mnemo.core.reflex import replay
    from mnemo.core.reflex.decide import decide, doc_token_sets
    from mnemo.core.reflex.index import load_index

    cfg = cfg_mod.load_config()
    reflex_cfg = cfg.get("reflex") or {}
    cap = int(reflex_cfg.get("maxEmissionsPerSession", 10))
    index = load_index(vault)
    if index is None:
        raise SystemExit("error: no reflex index in %s; run `mnemo index` first" % vault)
    all_units = mrg._load_units(vault)
    sample = mrg._load_sample(vault, all_units)
    wanted = {u["uid"]: u for u in sample}
    learned_at = {slug: f.learned_at for slug, f in replay.rule_facts(vault).items()}
    tokens = doc_token_sets(index)

    by_session: Dict[str, List[Any]] = {}
    for p in replay.collect_prompts(vault):
        by_session.setdefault(p.session_id, []).append(p)
    sessions = {u["session_id"] for u in sample}

    texts: Dict[str, str] = {}
    units: List[Dict[str, Any]] = []
    for sid in sorted(sessions):
        order: List[Tuple[str, List[str]]] = []
        mine: Dict[str, Dict[str, Any]] = {}
        for p in sorted(by_session.get(sid, []), key=lambda p: p.ts):
            uid = mrg.unit_id(p.session_id, p.ts, p.text)
            d = decide(index, project=p.project, prompt=p.text, reflex_cfg=reflex_cfg,
                       doc_tokens=tokens)
            pool = pool_of(d.scores, learned_at, p.ts)
            gated = shipped_of(d.accepted, [s for s, _ in pool])
            order.append((uid, gated))
            if uid not in wanted:
                continue
            entries = []
            for rank, (slug, score) in enumerate(pool, 1):
                if slug not in texts:
                    page = mcp_tools.read_mnemo_rule(vault, slug, scope="vault") or {}
                    texts[slug] = rerank.rule_text(page.get("body") or "")
                entries.append({"slug": slug, "rank": rank, "bm25f": round(float(score), 4),
                                "text": texts[slug]})
            mine[uid] = {"uid": uid, "session_id": sid, "project": p.project, "ts": p.ts,
                         "prompt": wanted[uid]["prompt"],
                         "stratum_fired": bool(wanted[uid].get("fired")),
                         "pool": entries, "shipped": gated}
        fates = replay_session(order, cap)
        for uid, unit in mine.items():
            unit["fate"] = fates.get(uid, {})
            units.append(unit)
    units.sort(key=lambda u: [x["uid"] for x in sample].index(u["uid"]))
    found = {u["uid"] for u in units}
    return {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "index_doc_count": index.get("doc_count"), "cap": cap, "depth": DEPTH,
        "population": {"fired": sum(1 for u in all_units if u.get("fired")),
                       "silenced": sum(1 for u in all_units if not u.get("fired"))},
        "missing": [u["uid"] for u in sample if u["uid"] not in found],
        "units": units,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="pools, calls, tokens; calls nothing")
    ap.add_argument("--send", action="store_true", help="label every pending pair (model calls)")
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls")
    ap.add_argument("--rater", default=DEFAULT_RATER)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    out = vault / ".mnemo" / OUT_DIR
    pools_path, labels_path = out / POOLS_NAME, out / LABELS_NAME
    all_labels: Dict[str, Dict[str, Dict[str, int]]] = _read(labels_path, {})
    col = column(args.rater)
    labels = all_labels.setdefault(col, {})

    if not pools_path.exists():
        if any(all_labels.values()):
            raise SystemExit("error: %s holds labels but %s is gone; refusing to rebuild"
                             % (labels_path, pools_path))
        out.mkdir(parents=True, exist_ok=True)
        _write(pools_path, _build(vault))
    pools = _read(pools_path, {})
    units = pools["units"]

    if args.dry_run:
        e = estimate(units, labels)
        print("%d of %d sampled prompts recovered (%d not found in transcripts), "
              "%d pairs, index %s docs, cap %d"
              % (len(units), len(units) + len(pools["missing"]), len(pools["missing"]),
                 sum(len(u["pool"]) for u in units), pools["index_doc_count"], pools["cap"]))
        print("pending: %(pairs)d pairs in %(calls)d call(s) of <= %(batch)d rules"
              % dict(e, batch=BATCH))
        print("tokens: ~%d in, ~%d out; API-price equivalent ~$%.2f at $%g/$%g per MTok — "
              "subscription usage on a Max plan, not money"
              % (e["input_tokens"], e["output_tokens"], e["usd"],
                 mrl.USD_IN_PER_MTOK, mrl.USD_OUT_PER_MTOK))
        return 0

    if args.send:
        provider = llm.resolve(cfg)
        timeout = int(cfg["extraction"]["subprocessTimeout"])
        todo = batches(units, labels)[:args.limit] if args.limit else batches(units, labels)
        usd = 0.0
        with tempfile.TemporaryDirectory(prefix="mnemo-reflex-reach-") as scratch:
            with mrl._chdir(scratch):
                for n, batch in enumerate(todo, 1):
                    resp = provider(rater_prompt(batch), system=RATER_SYSTEM, model=args.rater,
                                    timeout=timeout)
                    usd += float(resp.total_cost_usd or 0.0)
                    got = parse_labels(resp.text, batch)
                    for uid, row in got.items():
                        labels.setdefault(uid, {}).update(row)
                    _write(labels_path, all_labels)
                    print("call %d/%d: %d of %d labelled" % (
                        n, len(todo), sum(len(r) for r in got.values()), len(numbered(batch))),
                        file=sys.stderr)
        print("%d call(s), API-price equivalent $%.2f" % (len(todo), usd))

    if not labels:
        print("no labels yet in %s; --dry-run, then --send" % labels_path, file=sys.stderr)
        return 1
    saved = _read(vault / ".mnemo" / mrg.SCORES_NAME, {})
    jev = mrg._normalise_scores(saved.get(mrg.SCORES_KEY))
    fable = (_read(mrg._labels_path(vault, FABLE_RATER), {}) or {}).get("labels")
    data = report(units, labels, jev, pools["population"], fable=fable)
    if args.json:
        data["levers"] = lever_values(data)
        data["recommendation"] = recommendation(data)
        print(json.dumps(data, indent=1))
        return 0
    print("rater %s, %s\n" % (col, labels_path))
    for line in report_lines(data, args.rater):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
