"""How concentrated is the reflex's noise — do a few generic rules cause most of it? (#480)

Usage:
    PYTHONPATH=src python3 tools/measure_noise_concentration.py              # the report, local
    PYTHONPATH=src python3 tools/measure_noise_concentration.py --json
    PYTHONPATH=src python3 tools/measure_noise_concentration.py --dry-run    # rules to rate, calls, cost; calls nothing
    PYTHONPATH=src python3 tools/measure_noise_concentration.py --send      # both raters label what is pending

**Measure only.** Nothing here retires, stamps or edits a rule, nor changes
the reflex. Every write goes under ``<vault>/.mnemo/noise-concentration/``.

No new (prompt, rule) label is asked for: the injections and their 0/1/2
labels already exist. The corpora, each an injected pair with its label:

- **day-one b** — #467's arm (b), clubinho replayed from an empty vault that
  grows session by session, judge off, Sonnet labels
  (``~/.cache/mnemo/day-one/``);
- **day-one c** and **day-one ac** — #472's arms (c) and (a)+(c): the
  auto-memory mirror at install, then 50 prompts and 35 first turns;
- **mature #411** — the shipped gate's choice on #411's 300 sampled prompts
  (the vault as of 2026-09-20), Fable labels, no session replay;
- **mature #455** — the same prompts against the 09-22 vault, the shipped
  gate *after* ``measure_reflex_reach``'s session replay (cap and dedupe),
  Sonnet labels. The most realistic of the two mature views.

Noise is label 0. An unlabelled pair is never guessed: it counts in
``pairs`` and in nothing else.

**1. Concentration.** Rules ranked by noise injections (ties by slug); the
share of all noise the top 5, 10 and 20 cause, next to how many distinct
rules were injected at all — top 20 of 23 is not top 20 of 150. Ranking and
measuring on the same pairs flatters the top k, so a held-out row picks the
top k on one part and measures it on the other: day-one b picks on the
sessions before ``install_after`` and measures the rest (the vault grows, so
the rest also holds rules the pick never saw — as an offline removal would);
the mature corpora pick on #411's locked dev split (``part_of``) and measure
on test.

**2. What those rules are.** The union of every corpus's top 20 noisy rules,
deduplicated by the text a rater sees, is rated blind with #465's rubric
(``measure_demoted_keeps``: ``rater_system``, ``rater_prompt``,
``parse_labels``, ``send``, the same two raters, the second reading in
reverse). The text is ``reference_gate.view`` of the page's name and
retrieval body, read from the corpus's own vault; a page that is gone (the
09-22 cleanup removed some of #411's) falls back to the text the corpus
froze, and the report counts those. The headline: the share of each
corpus's noise caused by top-20 rules **both** raters call G or N.

**3. What removing them would cost.** Per corpus, the on-point injections
(label 2) those same top-k rules made, of all on-point injections; and, per
rated rule, its on-point count beside its noise.

**4. What already flags them.** Four signals, read, never computed anew:

- the ``reference_gate`` stamp on the page (#432): ``generic`` or
  ``narrative`` flags it;
- the friction ledger (``friction-ledger.jsonl``): a row whose
  ``contradicts`` names the rule;
- #410's generic sample (``generic-sample.json``): membership, and its
  labels if any rater file exists;
- the receipts ``mnemo why`` reads (``reflex-log.jsonl*``): how often a rule
  was emitted, no label needed. The mature vault's live log is used as is;
  day-one b uses its own injections before ``install_after``. The rule "the
  k most-emitted rules" is measured like the held-out row: noise caught and
  on-point lost where it was not picked.

For every signal: how many of the corpus's top 20 noisy rules it flags, the
share of the corpus's noise and of its on-point injections that flagged rules
made.

**Budget.** At most :data:`MAX_CALLS` rater calls ever, counted across reruns
in ``labels.json``; ``--dry-run`` first. The rated set is frozen in
``rated.json`` on first run and never redrawn under labels.

**First run, 2026-09-24.** The dry run gave 70 distinct rules (the five top-20s
overlap), 10 calls; the run took 10, $1.55 API-price equivalent. 4 texts came
from frozen text (#411 slugs the 09-22 cleanup deleted). Raters agree on
64/70 letters, kappa 0.87 on G/N vs rest.

- **Day one, judge off (arm b): yes.** 276 noise injections come from 23
  rules; the top 5 cause 48.2% and made 0 of the 11 on-point injections.
  15 of the top 20 are G/N by both raters (13 are ``feedback`` pages) and
  cause **229/276 = 83.0%** of the noise, at a cost of 3 of 11 on-point.
  Held out (picked on sessions 1-44, measured on 45-88): the top 5 catch
  35.8% of later noise, the top 20 80.4%.
- **Day one, auto-memory mirror (arms c, ac): no.** Its noise is project
  status pages; none of its top 20 is G/N by both raters.
- **Mature vault: no.** Noise is spread over 102 (#411) / 62 (#455) rules;
  top 20 cause 50.0% / 47.5% in sample, 29.5% / 20.0% held out. Both-G/N
  rules cause 18.1% / 10.0% of the noise, 0 on-point; 4 of #411's 5 were
  already deleted by the 09-22 cleanup. Most top noisy rules are S: system
  knowledge on the wrong prompt, not aphorisms.
- **No existing signal finds them.** No injected page carries a
  ``reference_gate`` stamp: the gate stamps what it holds back, and never
  judges a ``feedback`` page. A stamped staged twin flags 6 of arm b's top 20
  but those carry 8 of its 11 on-point injections. The friction ledger flags
  0-1 of 20; #410's sample drew none of them and was never labelled.
  Emission count finds arm b's (14/20, 87.3% of noise) but takes 10 of 11
  on-point with them, and finds 1-2 of 20 in the mature vault.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mdk = _sibling("measure_demoted_keeps")
mrg = _sibling("measure_reflex_gate")

OUT_DIR = "noise-concentration"
RATED_NAME = "rated.json"
LABELS_NAME = "labels.json"

DAY_ONE_WORK = Path(".cache") / "mnemo" / "day-one"

NOISE, MARGINAL, ON_POINT = 0, 1, 2
TOPS = (5, 10, 20)
RATED_TOP = 20
#: The issue's budget, over every run of this tool.
MAX_CALLS = 15
#: Rules per rater call; with two raters the union must fit MAX_CALLS.
BATCH = 15
#: #465's letters that make a rule junk-by-kind; W (wrong) is reported apart.
GENERIC_OR_NARRATIVE = ("G", "N")
FLAG_STAMPS = ("generic", "narrative")


# --- the pure part ---------------------------------------------------------------

def pair(uid: str, slug: str, label: Optional[int], *, part: Optional[str] = None,
         session: Optional[str] = None) -> Dict[str, Any]:
    return {"uid": uid, "slug": slug, "label": label, "part": part, "session": session}


def tally(pairs: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    """slug -> {n, noise, marginal, on_point, unlabelled}."""
    out: Dict[str, Dict[str, int]] = {}
    for p in pairs:
        row = out.setdefault(p["slug"], {"n": 0, "noise": 0, "marginal": 0, "on_point": 0,
                                         "unlabelled": 0})
        row["n"] += 1
        key = {NOISE: "noise", MARGINAL: "marginal", ON_POINT: "on_point"}.get(p["label"],
                                                                              "unlabelled")
        row[key] += 1
    return out


def ranked_noisy(pairs: Iterable[Dict[str, Any]]) -> List[str]:
    """Rules with at least one noise injection, most noise first, ties by slug."""
    t = tally(pairs)
    return sorted((s for s, r in t.items() if r["noise"]), key=lambda s: (-t[s]["noise"], s))


def shares(pairs: Sequence[Dict[str, Any]], chosen: Iterable[str]) -> Dict[str, Any]:
    """What the rules in ``chosen`` made: noise and on-point, of the corpus's."""
    chosen = set(chosen)
    noise = [p for p in pairs if p["label"] == NOISE]
    on = [p for p in pairs if p["label"] == ON_POINT]
    nk = sum(1 for p in noise if p["slug"] in chosen)
    ok = sum(1 for p in on if p["slug"] in chosen)
    return {"rules": len(chosen), "noise": nk, "noise_of": len(noise),
            "noise_share": nk / len(noise) if noise else None,
            "on_point": ok, "on_point_of": len(on),
            "on_point_share": ok / len(on) if on else None}


def concentration(pairs: Sequence[Dict[str, Any]], tops: Sequence[int] = TOPS) -> Dict[str, Any]:
    """Every top-k row of question 1 and 3, in sample."""
    ranked = ranked_noisy(pairs)
    return {
        "pairs": len(pairs),
        "labelled": sum(1 for p in pairs if p["label"] is not None),
        "noise": sum(1 for p in pairs if p["label"] == NOISE),
        "on_point": sum(1 for p in pairs if p["label"] == ON_POINT),
        "rules_injected": len({p["slug"] for p in pairs}),
        "rules_noisy": len(ranked),
        "top": {str(k): shares(pairs, ranked[:k]) for k in tops},
    }


def held_out(pairs: Sequence[Dict[str, Any]], pick: str, measure: str,
             tops: Sequence[int] = TOPS) -> Optional[Dict[str, Any]]:
    """Top k picked on part ``pick``, measured on part ``measure``."""
    a = [p for p in pairs if p["part"] == pick]
    b = [p for p in pairs if p["part"] == measure]
    if not a or not b:
        return None
    ranked = ranked_noisy(a)
    return {"pick": pick, "measure": measure, "pick_pairs": len(a), "measure_pairs": len(b),
            "top": {str(k): shares(b, ranked[:k]) for k in tops}}


def most_emitted(counts: Dict[str, int], k: int) -> List[str]:
    """The k rules the receipts show emitted most, ties by slug — no label read."""
    return sorted(counts, key=lambda s: (-counts[s], s))[:k]


def text_id(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def rated_set(tops: Dict[str, List[Tuple[str, str, bool]]]) -> List[Dict[str, Any]]:
    """The union of every corpus's top rules, one row per distinct text.

    ``tops`` is corpus -> [(slug, text, fallback)] in rank order. A row keeps
    every (corpus, slug) it stands for, so a label reaches each of them.
    """
    rows: Dict[str, Dict[str, Any]] = {}
    for corpus in sorted(tops):
        for slug, text, fallback in tops[corpus]:
            rid = text_id(text)
            row = rows.setdefault(rid, {"id": rid, "text": text, "slugs": [], "fallback": False})
            row["slugs"].append([corpus, slug])
            row["fallback"] = row["fallback"] or fallback
    return sorted(rows.values(), key=lambda r: r["id"])


def plan(rated: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, str]],
         raters: Sequence[str] = mdk.RATERS, size: int = BATCH) -> List[Tuple[str, List[Dict[str, Any]]]]:
    """Every pending call, rater by rater — ``mdk.plan`` with this tool's batch size."""
    out = []
    for k, model in enumerate(raters):
        for b in mdk.batches(k, rated, labels.get(mdk.column(model), {}), size=size):
            out.append((model, b))
    return out


def verdicts(rated: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, str]],
             cols: Sequence[str]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """(corpus, slug) -> both raters' letters and whether both said G or N."""
    out = {}
    for row in rated:
        letters = [labels.get(c, {}).get(row["id"]) for c in cols]
        complete = all(letters)
        both = complete and all(x in GENERIC_OR_NARRATIVE for x in letters)
        either = any(x in GENERIC_OR_NARRATIVE for x in letters if x)
        for corpus, slug in row["slugs"]:
            out[(corpus, slug)] = {"letters": letters, "complete": complete,
                                   "both_gn": both, "either_gn": either}
    return out


def kind_rows(corpus: str, pairs: Sequence[Dict[str, Any]], top: Sequence[str],
              v: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
    """Question 2 for one corpus: what the top rules' kinds account for."""
    rated = [s for s in top if (corpus, s) in v and v[(corpus, s)]["complete"]]
    both = [s for s in rated if v[(corpus, s)]["both_gn"]]
    either = [s for s in rated if v[(corpus, s)]["either_gn"]]
    return {"rated": len(rated), "of": len(top), "both_gn_rules": len(both),
            "both_gn": shares(pairs, both), "either_gn": shares(pairs, either),
            "top": shares(pairs, top)}


def signal_row(pairs: Sequence[Dict[str, Any]], top: Sequence[str],
               flagged: Optional[Set[str]]) -> Optional[Dict[str, Any]]:
    """Question 4 for one signal: how many of the top it flags, and what flagged rules made."""
    if flagged is None:
        return None
    injected = {p["slug"] for p in pairs}
    hit = flagged & injected
    return {"flags_top": sum(1 for s in top if s in flagged), "top": len(top),
            "flags_injected": len(hit), **shares(pairs, hit)}


# --- the corpora ------------------------------------------------------------------

def day_one_corpora(work: Path) -> Dict[str, Dict[str, Any]]:
    """Day one's arms as corpora: pairs, the arm's vault, and frozen texts (none)."""
    progress = _read(work / "progress.json", {})
    store = _read(work / "labels.json", {})
    labels = next(iter(store.values()), {}) if store else {}
    out: Dict[str, Dict[str, Any]] = {}

    def lab(uid: str, slug: str) -> Optional[int]:
        return (labels.get(uid) or {}).get(slug)

    b = progress.get("b") or {}
    if b.get("rows"):
        cut = int((progress.get("meta") or {}).get("install_after") or 0)
        pairs = []
        for n, row in enumerate(b["rows"], 1):
            part = "history" if n <= cut else "future"
            for u in row["units"]:
                for c in u["pool"]:
                    pairs.append(pair(u["uid"], c["slug"], lab(u["uid"], c["slug"]),
                                      part=part, session=row["sid"]))
        out["day-one b"] = {"pairs": pairs, "vault": work / "arm-b" / "vault",
                            "split": ("history", "future"), "texts": {}}
    for arm in ("c", "ac"):
        m = progress.get(arm) or {}
        if m.get("units") is None:
            continue
        pairs = [pair(u["uid"], c["slug"], lab(u["uid"], c["slug"]))
                 for u in m["units"] + (m.get("first_turns") or []) for c in u["pool"]]
        out["day-one " + arm] = {"pairs": pairs, "vault": work / ("arm-" + arm) / "vault",
                                 "split": None, "texts": {}}
    return out


def mature_corpora(vault: Path) -> Dict[str, Dict[str, Any]]:
    """#411's shipped gate (Fable labels) and #455's after the session replay (Sonnet)."""
    out: Dict[str, Dict[str, Any]] = {}
    meta = vault / ".mnemo"
    units_path, sample_path = meta / mrg.UNITS_NAME, meta / mrg.SAMPLE_NAME
    fable = _read(mrg._labels_path(vault, mrg.DEFAULT_RATER), {}).get("labels") or {}
    if units_path.is_file() and sample_path.is_file() and fable:
        units = mrg._load_units(vault)
        sample = mrg._load_sample(vault, units)
        pairs, texts = [], {}
        for u in sample:
            for c in u["candidates"]:
                texts.setdefault(c["slug"], c.get("text") or "")
            for slug in mrg.shipped_slugs(u):
                pairs.append(pair(u["uid"], slug, (fable.get(u["uid"]) or {}).get(slug),
                                  part=mrg.part_of(u["uid"]), session=u.get("session_id")))
        out["mature #411"] = {"pairs": pairs, "vault": vault, "split": ("dev", "test"),
                              "texts": texts}
    pools = _read(meta / "reflex-reach" / "pools.json", {})
    reach = _read(meta / "reflex-reach" / "labels.json", {})
    sonnet = next(iter(reach.values()), {}) if reach else {}
    if pools.get("units") and sonnet:
        pairs, texts = [], {}
        for u in pools["units"]:
            for c in u["pool"]:
                texts.setdefault(c["slug"], c.get("text") or "")
            fate = u.get("fate") or {}
            for slug in u["shipped"]:
                if fate.get(slug) == "injected":
                    pairs.append(pair(u["uid"], slug, (sonnet.get(u["uid"]) or {}).get(slug),
                                      part=mrg.part_of(u["uid"]), session=u["session_id"]))
        out["mature #455"] = {"pairs": pairs, "vault": vault, "split": ("dev", "test"),
                              "texts": texts}
    return out


# --- pages and signals --------------------------------------------------------------

def page_of(vault: Path, slug: str) -> Optional[Path]:
    """The page file a reflex slug names, through the rule index the reflex reads."""
    idx = _read(vault / ".mnemo" / "rule-activation-index.json", {})
    rule = (idx.get("rules") or {}).get(slug)
    if rule:
        stem = rule.get("file_stem")
        page_type = rule.get("type", "feedback")
        if stem and (vault / "shared" / page_type / (stem + ".md")).is_file():
            return vault / "shared" / page_type / (stem + ".md")
        if rule.get("path") and Path(rule["path"]).is_file():
            return Path(rule["path"])
    return None


def page_facts(path: Optional[Path]) -> Dict[str, Any]:
    """Name, type, gate stamp and the rater's view of one page; empty when gone."""
    if path is None:
        return {}
    from mnemo.core.extract import reference_gate
    from mnemo.core.extract.scanner import parse_frontmatter as split_frontmatter
    from mnemo.core.filters import parse_frontmatter
    from mnemo.core.text_utils import retrieval_body

    raw = path.read_text(encoding="utf-8", errors="replace")
    fm = parse_frontmatter(raw) or {}
    _, body = split_frontmatter(raw)
    name = str(fm.get("name") or path.stem)
    return {"name": name, "type": str(fm.get("type") or path.parent.name),
            "stamp": _stamp(fm),
            "twin_stamps": sorted(twin_stamps(path)),
            "view": reference_gate.view(name, retrieval_body(body))}


def _stamp(fm: Dict[str, Any]) -> Optional[str]:
    return str(fm.get("reference_gate") or "").strip().lower() or None


def twin_stamps(path: Path) -> Set[str]:
    """Gate stamps on the page's staged twins: the same stem in ``_inbox/<type>/``
    or a ``.proposed.md`` sibling. The gate stamps what it holds back, so a live
    page is never stamped itself, but its demoted or re-emitted copy can be."""
    from mnemo.core.filters import parse_frontmatter

    shared = path.parent.parent
    stem = path.stem
    found: Set[str] = set()
    candidates = list((shared / "_inbox").glob("*/%s.md" % stem))
    candidates += list((shared / "_inbox").glob("*/%s.proposed.md" % stem))
    candidates += list(shared.glob("*/%s.proposed.md" % stem))
    for twin in candidates:
        try:
            stamp = _stamp(parse_frontmatter(twin.read_text(encoding="utf-8", errors="replace")) or {})
        except OSError:
            continue
        if stamp:
            found.add(stamp)
    return found


def fallback_view(slug: str, text: str) -> str:
    from mnemo.core.extract import reference_gate

    name = slug.split("__", 1)[-1].replace("-", " ")
    return reference_gate.view(name, text)


def friction_flags(vault: Path) -> Optional[Set[str]]:
    path = vault / ".mnemo" / "friction-ledger.jsonl"
    if not path.is_file():
        return None
    out: Set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        out.update(row.get("contradicts") or [])
    return out


def generic_sample(vault: Path) -> Optional[Dict[str, Any]]:
    """#410's sample: which slugs it drew, and which a rater called generic (0)."""
    meta = vault / ".mnemo"
    sample = _read(meta / "generic-sample.json", None)
    if not sample:
        return None
    ids = {r["id"]: r["slug"] for r in sample.get("rules") or []}
    generic: Set[str] = set()
    raters = []
    for path in sorted(meta.glob("generic-labels-*.json")):
        raters.append(path.name)
        for rid, value in ((_read(path, {}) or {}).get("labels") or {}).items():
            if value == 0 and rid in ids:
                generic.add(ids[rid])
    return {"sampled": set(ids.values()), "generic": generic, "raters": raters}


def receipt_counts(vault: Path) -> Dict[str, int]:
    """Times each rule was emitted, from the receipts ``mnemo why`` reads."""
    counts: Counter = Counter()
    for path in sorted((vault / ".mnemo").glob("reflex-log.jsonl*")):
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            counts.update(row.get("emitted") or [])
    return dict(counts)


# --- the report --------------------------------------------------------------------

def build(corpora: Dict[str, Dict[str, Any]], rated: Sequence[Dict[str, Any]],
          labels: Dict[str, Dict[str, str]], signals: Dict[str, Dict[str, Optional[Set[str]]]],
          facts: Dict[Tuple[str, str], Dict[str, Any]]) -> Dict[str, Any]:
    """Every number the issue asks for, from what is on disk."""
    cols = [mdk.column(m) for m in mdk.RATERS]
    v = verdicts(rated, labels, cols)
    out: Dict[str, Any] = {"corpora": {}}
    for name, c in corpora.items():
        pairs = c["pairs"]
        top = ranked_noisy(pairs)[:RATED_TOP]
        t = tally(pairs)
        row: Dict[str, Any] = {"concentration": concentration(pairs)}
        if c.get("split"):
            row["held_out"] = held_out(pairs, *c["split"])
        row["kinds"] = kind_rows(name, pairs, top, v)
        row["rules"] = [dict(t[s], slug=s, letters=(v.get((name, s)) or {}).get("letters"),
                             type=(facts.get((name, s)) or {}).get("type"),
                             stamp=(facts.get((name, s)) or {}).get("stamp")
                             or "/".join("twin:" + x for x in
                                         (facts.get((name, s)) or {}).get("twin_stamps") or []) or None,
                             gone=not facts.get((name, s)))
                        for s in top]
        row["signals"] = {sig: signal_row(pairs, top, flagged)
                          for sig, flagged in (signals.get(name) or {}).items()}
        out["corpora"][name] = row
    agree = [(row["id"], [labels.get(c, {}).get(row["id"]) for c in cols]) for row in rated]
    both = [(a, b) for _, (a, b) in agree if a and b]
    out["raters"] = {
        "rated": len(rated), "labelled_by_both": len(both),
        "fallback_texts": sum(1 for r in rated if r["fallback"]),
        "letters": {c: dict(Counter(labels.get(c, {}).get(r["id"]) for r in rated
                                    if labels.get(c, {}).get(r["id"])))
                    for c in cols},
        "exact": (sum(a == b for a, b in both) / len(both)) if both else None,
        "kappa_gn": mdk.kappa(["J" if a in GENERIC_OR_NARRATIVE else mdk.GOOD for a, _ in both],
                              ["J" if b in GENERIC_OR_NARRATIVE else mdk.GOOD for _, b in both]),
    }
    return out


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def _share(s: Dict[str, Any]) -> str:
    return "noise %d/%d = %s, on-point %d/%d = %s" % (
        s["noise"], s["noise_of"], _pct(s["noise_share"]),
        s["on_point"], s["on_point_of"], _pct(s["on_point_share"]))


def report_lines(data: Dict[str, Any]) -> List[str]:
    r = data["raters"]
    lines = ["raters %s: %d distinct rules rated, %d by both, %d from frozen text (page gone); "
             "exact agreement %s, kappa on G/N vs rest %s"
             % (", ".join(mdk.RATERS), r["rated"], r["labelled_by_both"], r["fallback_texts"],
                _pct(r["exact"]), "n/a" if r["kappa_gn"] is None else "%.2f" % r["kappa_gn"])]
    for col, letters in r["letters"].items():
        lines.append("  %s: %s" % (col, " ".join("%s %d" % kv for kv in sorted(letters.items()))))
    for name, c in data["corpora"].items():
        k = c["concentration"]
        lines += ["", "== %s: %d injected pairs (%d labelled), %d noise, %d on-point; "
                  "%d rules injected, %d of them noisy"
                  % (name, k["pairs"], k["labelled"], k["noise"], k["on_point"],
                     k["rules_injected"], k["rules_noisy"])]
        for top, s in k["top"].items():
            lines.append("  top %-2s (%2d rules): %s" % (top, s["rules"], _share(s)))
        h = c.get("held_out")
        if h:
            lines.append("  held out — picked on %s (%d pairs), measured on %s (%d pairs):"
                         % (h["pick"], h["pick_pairs"], h["measure"], h["measure_pairs"]))
            for top, s in h["top"].items():
                lines.append("    top %-2s: %s" % (top, _share(s)))
        kd = c["kinds"]
        lines.append("  kinds: %d of top %d rated by both; %d both-G/N -> %s"
                     % (kd["rated"], kd["of"], kd["both_gn_rules"], _share(kd["both_gn"])))
        lines.append("         either-G/N -> %s" % _share(kd["either_gn"]))
        lines.append("  signals (flags top-20 / flagged rules make):")
        for sig, s in c["signals"].items():
            if s is None:
                lines.append("    %-28s n/a here" % sig)
            else:
                lines.append("    %-28s %2d/%d; %s" % (sig, s["flags_top"], s["top"], _share(s)))
        lines.append("  top rules: noise/marginal/on-point, raters, type, gate stamp")
        for row in c["rules"]:
            lines.append("    %3d/%d/%d  %-7s %-9s %-10s %s" % (
                row["noise"], row["marginal"], row["on_point"],
                "/".join(x or "-" for x in (row["letters"] or ["-", "-"])),
                row["type"] or "gone", row["stamp"] or "-", row["slug"]))
    return lines


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False),
                    encoding="utf-8")


def gather(vault: Path, work: Path) -> Tuple[Dict[str, Dict[str, Any]],
                                             Dict[Tuple[str, str], Dict[str, Any]],
                                             Dict[str, List[Tuple[str, str, bool]]]]:
    """The corpora, each top rule's page facts, and the text a rater would see."""
    corpora = dict(day_one_corpora(work))
    corpora.update(mature_corpora(vault))
    facts: Dict[Tuple[str, str], Dict[str, Any]] = {}
    tops: Dict[str, List[Tuple[str, str, bool]]] = {}
    for name, c in corpora.items():
        tops[name] = []
        for slug in ranked_noisy(c["pairs"])[:RATED_TOP]:
            f = page_facts(page_of(c["vault"], slug))
            facts[(name, slug)] = f
            if f:
                tops[name].append((slug, f["view"], False))
            elif c["texts"].get(slug):
                tops[name].append((slug, fallback_view(slug, c["texts"][slug]), True))
    return corpora, facts, tops


def signals_for(corpora: Dict[str, Dict[str, Any]], facts: Dict[Tuple[str, str], Dict[str, Any]],
                vault: Path) -> Dict[str, Dict[str, Optional[Set[str]]]]:
    friction = friction_flags(vault)
    gs = generic_sample(vault)
    live = receipt_counts(vault)
    out: Dict[str, Dict[str, Optional[Set[str]]]] = {}
    for name, c in corpora.items():
        injected = {p["slug"] for p in c["pairs"]}
        stamps, twins = set(), set()
        for slug in injected:
            f = facts.get((name, slug))
            if f is None:
                f = page_facts(page_of(c["vault"], slug))
                facts[(name, slug)] = f
            if f.get("stamp") in FLAG_STAMPS:
                stamps.add(slug)
            if set(f.get("twin_stamps") or ()) & set(FLAG_STAMPS):
                twins.add(slug)
        mature = name.startswith("mature")
        if mature:
            emitted: Optional[Dict[str, int]] = live
        elif c.get("split"):
            emitted = dict(Counter(p["slug"] for p in c["pairs"] if p["part"] == c["split"][0]))
        else:
            emitted = None
        out[name] = {
            "reference_gate generic/narr.": stamps,
            "  ... on a staged twin": twins,
            "friction contradicts": friction if mature else None,
            "#410 generic sample (drawn)": gs["sampled"] if (gs and mature) else None,
            "#410 labelled generic": gs["generic"] if (gs and mature and gs["raters"]) else None,
        }
        for k in (5, 10, 20):
            out[name]["receipts: %d most emitted" % k] = (
                set(most_emitted(emitted, k)) if emitted else None)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="rules to rate, calls, cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="both raters label what is pending (model calls)")
    ap.add_argument("--work", type=Path, default=None, help="day one's work dir (default ~/%s)" % DAY_ONE_WORK)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    work = args.work or (Path.home() / DAY_ONE_WORK)
    out = vault / ".mnemo" / OUT_DIR
    rated_path, labels_path = out / RATED_NAME, out / LABELS_NAME
    store: Dict[str, Any] = _read(labels_path, {"calls": 0, "usd": 0.0, "labels": {}})

    corpora, facts, tops = gather(vault, work)
    if not corpora:
        print("error: no labelled corpus found under %s or %s" % (work, vault), file=sys.stderr)
        return 1
    if rated_path.exists():
        rated = _read(rated_path, {}).get("rated") or []
    else:
        if any(store.get("labels", {}).values()):
            raise SystemExit("error: %s holds labels but %s is gone; refusing to redraw"
                             % (labels_path, rated_path))
        rated = rated_set(tops)
        out.mkdir(parents=True, exist_ok=True)
        _write(rated_path, {"frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "top": RATED_TOP, "rated": rated})
    todo = plan(rated, store.get("labels", {}))

    if args.dry_run:
        for name, c in corpora.items():
            print("%-12s %4d pairs, %3d rules injected, top %d kept for rating (%d from frozen text)"
                  % (name, len(c["pairs"]), len({p["slug"] for p in c["pairs"]}), len(tops[name]),
                     sum(1 for t in tops[name] if t[2])))
        e = mdk.estimate(todo)
        print("rated: %d distinct rules; pending %d labels in %d call(s) of <= %d; %d of %d "
              "budgeted calls used" % (len(rated), e["pages"], e["calls"], BATCH,
                                       store.get("calls", 0), MAX_CALLS))
        print("tokens: ~%d in, ~%d out; API-price equivalent ~$%.2f — subscription usage on a "
              "Max plan, not money" % (e["input_tokens"], e["output_tokens"], e["usd"]))
        if store.get("calls", 0) + e["calls"] > MAX_CALLS:
            print("WARNING: pending calls exceed the %d-call budget; --send stops at it" % MAX_CALLS)
        return 0

    if args.send:
        provider = llm.resolve(cfg)
        timeout = max(300, int(cfg["extraction"]["subprocessTimeout"]))
        mrl = _sibling("measure_rule_lift")

        def ask(prompt: str, system: str, model: str) -> Tuple[str, float]:
            resp = provider(prompt, system=system, model=model, timeout=timeout)
            return resp.text, float(resp.total_cost_usd or 0.0)

        with tempfile.TemporaryDirectory(prefix="mnemo-noise-concentration-") as scratch:
            with mrl._chdir(scratch):
                for line in mdk.send(todo, store, ask, lambda s: _write(labels_path, s),
                                     max_calls=MAX_CALLS):
                    print(line, file=sys.stderr)
        print("%d call(s) used of %d, API-price equivalent $%.2f"
              % (store.get("calls", 0), MAX_CALLS, store.get("usd", 0.0)))

    data = build(corpora, rated, store.get("labels", {}), signals_for(corpora, facts, vault), facts)
    data["calls"] = {"used": store.get("calls", 0), "budget": MAX_CALLS, "usd": store.get("usd", 0.0)}
    if args.json:
        print(json.dumps(data, indent=1, default=sorted))
        return 0
    print("labels %s\n" % labels_path)
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
