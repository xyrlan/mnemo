"""Would a fixed SessionStart block of the most-corrected rules have delivered #520's units? (#598)

Usage:
    PYTHONPATH=src python3 tools/measure_settled_block.py [--units FILE] [--k 15 --k 5 --k 10]
        [--vault DIR] [--json]

Offline, from #520's cache (``measure_prevented_repeats``' ``units.json``, column
``both``); no model is called, so there is no ``--send``. ``--units`` picks the
reading: ``<vault>/.mnemo/prevented-repeats/units.json`` is #520's published
``--native notes`` run; a ``--native loaded`` run of the same cache (#565) has
the same unit ids and only the ``redundant`` flags differ.

**1. The ceiling.** #520's estimate (frequency × delivery × lift,
``measure_prevented_repeats.combine``) with delivery set to 100% for every unit
Claude Code's own loaded memory did not already say: the most any delivery
channel could prevent. Its point estimate below :data:`THRESHOLD` means no
delivery change can make the reading *positive*.

**2. The block, with no hindsight.** For each session, the block as it would
have been at that session's start:

- rules that existed then (``measure_repeated_corrections.first_learned``,
  the session's own sources excluded), for the session's project or universal,
  not retired;
- with at least one **verified correction** before the start, counted in
  **distinct sessions** (never the session itself): the rule's own evidence
  quote when both of #520's raters call it a correction (#520's strict pool),
  counted from the moment the rule exists; a #519 correction both raters call
  real whose both-rater judge verdict names the rule as already saying it; and
  a friction-ledger row whose ``contradicts`` names the rule and whose quote is
  one of #519's both-rater-real corrections;
- minus the rules #520's delivery judge found the session's loaded
  ``CLAUDE.md`` / ``MEMORY.md`` already says (``redundant`` units). That
  judgement exists only for rules relevant in the session, so the other rules
  keep their slot; the reading without this exclusion is printed beside it;
- ranked by that count, ties by slug; the first K (:data:`KS`, primary
  :data:`PRIMARY_K`).

**3. Coverage and estimate.** The share of units, and of non-redundant units,
whose rule was in the session's block, with session-bootstrap CIs; then #520's
estimate with the block as the delivery (a unit is delivered and new when its
rule is in the block and it is not redundant), and the verdict against #520's
thresholds, on the strict reading (rules backed by a real correction) and the
broad one (any live rule).

**4. Cost.** The bytes the block adds to SessionStart: each rule rendered as
the reflex renders it (``reflex.render.full_line``, the whole body, uncut),
under one header line.

The bar (declared in #598 before measuring): build the block only if its strict
estimate is positive, or its broad estimate clears 1/15 with a CI lower bound
over 0; do not build if the ceiling is under the bar on both readings;
anything else is inconclusive.

First run, 2026-10-07, the maintainer's vault, on #565's ``--native loaded``
rerun of #520's cache (245 sessions, 1,513 units). **Strict ceiling 0.0585
per session [0.032, 0.091], under 1/15**: perfect delivery cannot make the
strict reading positive. 19 rules had a verified correction (18 evidence
quotes, 1 later #519 correction), and every one had exactly one session, so
the ranking is the slug tie-break. No session's block held more than 4 rules,
and K = 5, 10 and 15 give the same block. The block carries 44 of the 46
non-redundant strict units: strict 0.0559 [0.030, 0.087], inconclusive.
**Broad: 0.117 [0.070, 0.169], positive, so #598's bar says build.** But 48 of
its 92 units are one universal rule, ``run-git-commands-yourself``, which
enters the block on that single #519 correction. Without it, broad is 0.0559
[0.030, 0.087], inconclusive. The block adds a median 2,645 bytes (p95 3,328)
to SessionStart, or 1,570 bytes with redundant rules dropped. The published
``--native notes`` cache gives the same decision: strict ceiling 0.054, broad
0.112 [0.067, 0.161], and 0.054 without that rule.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mpr = _sibling("measure_prevented_repeats")
mrc = mpr.mrc
_provenance = _sibling("_provenance")

THRESHOLD = mpr.THRESHOLD
BOOTSTRAP = mpr.BOOTSTRAP
SEED = 598
KS = (5, 10, 15)
PRIMARY_K = 15
STRICT, BROAD = mpr.STRICT, mpr.BROAD
HEADER = "mnemo settled rules:"

#: One verified correction of a rule: ``(session_id, ts)``. ``ts`` None means
#: "counted from the moment the rule exists" (the rule's own evidence quote).
Event = Tuple[str, Optional[float]]


# --- 2. the block ----------------------------------------------------------------------

def correction_events(
    strict: Iterable[str],
    evidence: Dict[str, Dict[str, Any]],
    known: Iterable[Tuple[str, str, float]] = (),
    linked: Iterable[Tuple[str, str, float]] = (),
) -> Dict[str, List[Event]]:
    """``slug -> [(session, ts)]``: every verified correction of a rule.

    ``strict``: slugs whose evidence quote both raters call a correction, each
    counted in its evidence session from the moment the rule exists.
    ``known`` / ``linked``: ``(slug, session, ts)`` of later real corrections
    #519's judge named the rule for, or the ledger linked to it."""
    out: Dict[str, List[Event]] = {}
    for slug in sorted(strict):
        out.setdefault(slug, []).append((evidence[slug]["session_id"], None))
    for slug, sid, ts in list(known) + list(linked):
        out.setdefault(slug, []).append((sid, ts))
    return out


def corrections_before(events: Sequence[Event], session_id: str, start: float) -> int:
    """Distinct sessions, other than ``session_id``, with a correction before ``start``."""
    return len({sid for sid, ts in events if sid and sid != session_id and (ts is None or ts < start)})


def eligible(doc: Optional[Dict[str, Any]], project: str) -> bool:
    return bool(doc) and not doc.get("retired") and (
        project in (doc.get("projects") or []) or bool(doc.get("universal")))


def block_at(session_id: str, project: str, start: float, events: Dict[str, List[Event]],
             docs: Dict[str, Dict[str, Any]], dates: Dict[str, Dict[str, Any]], k: int,
             exclude: Iterable[str] = ()) -> List[str]:
    """The K rules the block holds at ``start``: rules that existed then, by
    distinct sessions with a verified correction before it, ties by slug."""
    skip = set(exclude)
    scored = []
    for slug, evs in events.items():
        if slug in skip or not eligible(docs.get(slug), project):
            continue
        facts = dates.get(slug)
        learned = mrc.first_learned(facts, session_id) if facts is not None else None
        if learned is None or learned >= start:
            continue
        n = corrections_before(evs, session_id, start)
        if n:
            scored.append((-n, slug))
    return [slug for _, slug in sorted(scored)[:k]]


# --- 3. coverage and the estimate -------------------------------------------------------

def session_rows(units: Sequence[Dict[str, Any]], sessions: Sequence[str],
                 blocks: Dict[str, Sequence[str]], reading: str, ceiling: bool = False) -> List[Dict[str, int]]:
    """One row per session in ``sessions``, in the shape ``combine`` reads.

    A unit is delivered when its rule is in the session's block (every unit,
    with ``ceiling``); new when delivered and not redundant."""
    by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for u in units:
        if reading == STRICT and not u.get("strict"):
            continue
        by_sid.setdefault(u["session_id"], []).append(u)
    out = []
    for sid in sessions:
        block = set(blocks.get(sid) or ())
        row = {"units": 0, "new": 0, "delivered": 0, "redundant": 0, "prompts": 0,
               "covered_fresh": 0, "fresh": 0}
        row.update({c: 0 for c in mpr.CHANNELS})
        for u in by_sid.get(sid, []):
            got = ceiling or u["slug"] in block
            fresh = not u.get("redundant")
            row["units"] += 1
            row["delivered"] += int(got)
            row["redundant"] += int(not fresh)
            row["fresh"] += int(fresh)
            row["new"] += int(got and fresh)
            row["covered_fresh"] += int(got and fresh)
        out.append(row)
    return out


def ratio_ci(rows: Sequence[Dict[str, int]], num: str, den: str,
             n_boot: int = BOOTSTRAP, seed: int = SEED) -> Dict[str, Any]:
    """``sum(num) / sum(den)`` with a session-bootstrap 95% CI."""
    k, n = sum(r[num] for r in rows), sum(r[den] for r in rows)
    if not n:
        return {"k": k, "n": n, "share": None, "ci": [None, None]}
    rng = random.Random(seed)
    m = len(rows)
    boots = []
    for _ in range(n_boot):
        pick = [rows[rng.randrange(m)] for _ in range(m)]
        d = sum(r[den] for r in pick)
        boots.append(sum(r[num] for r in pick) / d if d else 0.0)
    boots.sort()
    return {"k": k, "n": n, "share": k / n, "ci": [mpr._pct(boots, 0.025), mpr._pct(boots, 0.975)]}


def estimate(rows: Sequence[Dict[str, int]], diffs: Sequence[float]) -> Dict[str, Any]:
    stats = mpr.combine(rows, diffs)
    lo, hi = (stats.get("ci", {}).get("E") or [None, None])
    stats["verdict"] = mpr.verdict(stats.get("E"), lo, hi)
    stats["needed_sessions"] = mpr.needed_sessions(len(rows), stats) if stats.get("E") is not None else None
    return stats


def top_carrier(units: Sequence[Dict[str, Any]], blocks: Dict[str, Sequence[str]],
                reading: str) -> Optional[str]:
    """The rule whose block slot delivered the most non-redundant units, ties by slug."""
    count: Dict[str, int] = {}
    for u in units:
        if (reading == STRICT and not u.get("strict")) or u.get("redundant"):
            continue
        if u["slug"] in set(blocks.get(u["session_id"]) or ()):
            count[u["slug"]] = count.get(u["slug"], 0) + 1
    return min(count, key=lambda s: (-count[s], s)) if count else None


def without(blocks: Dict[str, Sequence[str]], slug: str) -> Dict[str, List[str]]:
    return {sid: [s for s in b if s != slug] for sid, b in blocks.items()}


def decide(ceiling: Dict[str, Dict[str, Any]], block: Dict[str, Dict[str, Any]],
           threshold: float = THRESHOLD) -> str:
    """#598's bar, declared before measuring."""
    s, b = block.get(STRICT, {}), block.get(BROAD, {})
    b_lo = (b.get("ci", {}).get("E") or [None])[0]
    if s.get("verdict") == "positive" or (
            b.get("E") is not None and b["E"] >= threshold and b_lo is not None and b_lo > 0):
        return "build"
    if all((ceiling.get(r, {}).get("E") or 0.0) < threshold for r in (STRICT, BROAD)):
        return "do not build"
    return "inconclusive"


# --- 4. cost ------------------------------------------------------------------------------

def block_bytes(block: Sequence[str], bodies: Dict[str, str]) -> int:
    """The block's UTF-8 bytes, each rule as the reflex renders a whole body."""
    from mnemo.core.hook_envelope import utf8_len
    from mnemo.core.reflex import render

    if not block:
        return 0
    lines = [render.full_line(render.bullet(s), bodies.get(s, "")) for s in block]
    return utf8_len("\n".join([HEADER] + lines))


def quantile(values: Sequence[int], q: float) -> Optional[int]:
    v = sorted(values)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))] if v else None


# --- reading #519's and the ledger's corrections ------------------------------------------

def known_corrections(rc_dir: Path, raters: Sequence[str]) -> List[Tuple[str, str, float]]:
    """``(slug, session, ts)`` for #519 corrections both raters call real whose
    both-rater judge verdict names the rule."""
    items = {it["id"]: it for it in mrc._read(rc_dir / "items.json", [])}
    labels = {r: mrc._read(rc_dir / "labels.json", {}).get(mrc.column(r, mrc.LABEL_SYSTEM), {}) for r in raters}
    verdicts = mrc._read(rc_dir / "verdicts.json", {})
    units = mrc._read(rc_dir / "units.json", {})
    out = []
    for iid, it in sorted(items.items()):
        if not mrc.consensus(labels, iid) or iid not in units:
            continue
        named = None
        for r in raters:
            v = verdicts.get(mrc.column(r, mrc.JUDGE_SYSTEM), {}).get(iid)
            refs = {units[iid]["notes"][n]["ref"] for n in (v or {}).get("notes") or []
                    if units[iid]["notes"].get(n, {}).get("kind") == "rule"}
            named = refs if named is None else named & refs
        for slug in sorted(named or ()):
            out.append((slug, it["session_id"], float(it["ts"])))
    return out


def linked_corrections(ledger: Path, rc_dir: Path, raters: Sequence[str]) -> List[Tuple[str, str, float]]:
    """``(slug, session, ts)`` for ledger rows whose ``contradicts`` names a
    rule and whose quote is a #519 correction both raters call real."""
    from mnemo.core import corrections
    from mnemo.core.log_utils import iter_rotated_rows

    labels = {r: mrc._read(rc_dir / "labels.json", {}).get(mrc.column(r, mrc.LABEL_SYSTEM), {}) for r in raters}
    real = {(it["session_id"], corrections.normalize(it["quote"])): float(it["ts"])
            for it in mrc._read(rc_dir / "items.json", []) if mrc.consensus(labels, it["id"])}
    out = []
    for row in iter_rotated_rows(ledger):
        if not isinstance(row, dict):
            continue
        ts = real.get((row.get("session_id"), corrections.normalize(str(row.get("quote") or ""))))
        if ts is None:
            continue
        for slug in row.get("contradicts") or []:
            if isinstance(slug, str) and slug:
                out.append((slug, row["session_id"], ts))
    return sorted(set(out))


# --- report -------------------------------------------------------------------------------

def _e(stats: Dict[str, Any]) -> str:
    if stats.get("E") is None:
        return "no estimate"
    lo, hi = stats["ci"]["E"]
    return "%.4f per session [%.4f, %.4f] (1 per %s) -> %s" % (
        stats["E"], lo, hi, ("%.0f" % (1 / stats["E"])) if stats["E"] else "inf", stats["verdict"].upper())


def _share(c: Dict[str, Any]) -> str:
    if c["share"] is None:
        return "%d/%d" % (c["k"], c["n"])
    return "%d/%d = %.1f%% [%.1f, %.1f]" % (c["k"], c["n"], 100 * c["share"], 100 * c["ci"][0], 100 * c["ci"][1])


def report_lines(data: Dict[str, Any]) -> List[str]:
    out = ["#598: a fixed SessionStart block of the most-corrected rules, from #520's cache",
           "units: %s (column both), %d sessions, lift %s" % (data["units_file"], data["sessions"], data["lift_source"]),
           "verified corrections: %d rules (%d evidence quotes, %d #519 verdicts, %d ledger links)" % (
               data["events"]["rules"], data["events"]["evidence"], data["events"]["known"],
               data["events"]["linked"]),
           "bar: positive = estimate >= 1/15 (%.4f) and CI lower > 0; null = CI upper < 1/15" % THRESHOLD, ""]
    out.append("1. CEILING (delivery 100% of the units Claude Code's loaded memory did not say)")
    for r in (STRICT, BROAD):
        c = data["ceiling"][r]
        out.append("   %-6s %d of %d units not redundant; %s" % (r, c["counts"]["new"], c["units"], _e(c)))
    for name, excl in (("2-3. BLOCK", "excluded"), ("     without the redundancy exclusion", "kept")):
        out += ["", "%s (as of each session's start; K rules; redundant rules %s)" % (name, excl)]
        for k in data["ks"]:
            b = data["blocks"][excl][str(k)]
            out.append("   K=%-2d rules in block: median %s, max %s; bytes median %s, p95 %s, max %s" % (
                k, b["size"]["median"], b["size"]["max"], b["bytes"]["median"], b["bytes"]["p95"],
                b["bytes"]["max"]))
            for r in (STRICT, BROAD):
                out.append("        %-6s coverage %s; non-redundant %s" % (r, _share(b["coverage"][r]),
                                                                          _share(b["coverage_fresh"][r])))
                out.append("        %-6s estimate %s" % (r, _e(b["estimate"][r])))
                if b["estimate"][r].get("verdict") == "inconclusive" and b["estimate"][r].get("needed_sessions"):
                    out.append("               sessions to decide: ~%d" % b["estimate"][r]["needed_sessions"])
                w = b["without_top"].get(r)
                if w:
                    out.append("               without %s (%d unit(s)): %s" % (w["rule"], w["carried"], _e(w)))
    out.append("")
    for excl in ("excluded", "kept"):
        b = data["blocks"][excl][str(data["primary_k"])]["bytes"]
        out.append("4. COST at K=%d, redundant rules %s: median %s B, p95 %s B, "
                   "max %s B added to SessionStart (mnemo's envelope budget: %d bytes)" % (
                       data["primary_k"], excl, b["median"], b["p95"], b["max"], data["envelope_max"]))
    out += ["", "DECISION (#598's bar, K=%d): %s" % (data["primary_k"], data["decision"].upper())]
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, paths
    from mnemo.core.hook_envelope import ENVELOPE_MAX_BYTES

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--units", default="", help="#520's units.json (default <vault>/.mnemo/prevented-repeats/units.json)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--k", type=int, action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    ks = sorted(set(args.k or KS))
    primary = PRIMARY_K if PRIMARY_K in ks else ks[-1]

    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(config.load_config())
    units_file = Path(args.units).expanduser() if args.units else vault / ".mnemo" / "prevented-repeats" / mpr.UNITS_NAME
    cache = mrc._read(units_file, None)
    if not cache:
        print("no #520 units at %s: run tools/measure_prevented_repeats.py first" % units_file, file=sys.stderr)
        return 1
    units = cache["columns"][mpr.BOTH]
    sessions_meta = cache["sessions"]
    sessions = sorted(cache["rated"])

    projects = Path(args.projects)
    rules = mpr.Rules(vault, projects, Path(args.claude_home))
    transcripts = {p.stem: p for p in projects.glob("*/*.jsonl")}
    evidence = mpr.evidence_items(vault, rules.facts, transcripts)
    raters = list(mpr.RATERS)
    rc_dir = vault / ".mnemo" / "repeated-corrections"
    seeded = mrc._read(rc_dir / "labels.json", {})
    cached = mrc._read(units_file.parent / "labels.json", {})
    labels = {}
    for r in raters:
        col = mrc.column(r, mrc.LABEL_SYSTEM)
        labels[r] = dict(seeded.get(col, {}), **cached.get(col, {}))
    unlabelled = {s for s, it in evidence.items() if mrc.consensus(labels, it["id"]) is None}
    strict = {s for s, it in evidence.items() if mrc.consensus(labels, it["id"])}
    known = known_corrections(rc_dir, raters)
    linked = linked_corrections(vault / ".mnemo" / "friction-ledger.jsonl", rc_dir, raters)
    events = correction_events(strict, evidence, known, linked)

    docs = rules.ctx.index.get("docs") or {}
    dates = rules.ctx.dates
    redundant: Dict[str, Set[str]] = {}
    for u in units:
        if u.get("redundant"):
            redundant.setdefault(u["session_id"], set()).add(u["slug"])
    bodies = {}
    for slug in events:
        page = rules.ctx.pages.get(slug)
        bodies[slug] = page[1] if page else ""

    diffs, lift_source = mpr.lift_diffs(vault)
    ceiling = {r: estimate(session_rows(units, sessions, {}, r, ceiling=True), diffs) for r in (STRICT, BROAD)}
    blocks: Dict[str, Dict[str, Any]] = {}
    for excl in ("excluded", "kept"):
        blocks[excl] = {}
        for k in ks:
            per = {sid: block_at(sid, sessions_meta[sid]["project"], float(sessions_meta[sid]["start"]),
                                 events, docs, dates, k, redundant.get(sid, ()) if excl == "excluded" else ())
                   for sid in sessions}
            sizes = [len(b) for b in per.values()]
            nbytes = [block_bytes(b, bodies) for b in per.values()]
            entry: Dict[str, Any] = {
                "size": {"median": quantile(sizes, 0.5), "max": max(sizes) if sizes else None},
                "bytes": {"median": quantile(nbytes, 0.5), "p95": quantile(nbytes, 0.95),
                          "max": max(nbytes) if nbytes else None},
                "coverage": {}, "coverage_fresh": {}, "estimate": {}, "without_top": {}}
            for r in (STRICT, BROAD):
                rows = session_rows(units, sessions, per, r)
                entry["coverage"][r] = ratio_ci(rows, "delivered", "units")
                entry["coverage_fresh"][r] = ratio_ci(rows, "covered_fresh", "fresh")
                entry["estimate"][r] = estimate(rows, diffs)
                top = top_carrier(units, per, r)
                if top is not None:
                    alone = session_rows(units, sessions, without(per, top), r)
                    entry["without_top"][r] = dict(estimate(alone, diffs), rule=top,
                                                   carried=entry["coverage_fresh"][r]["k"]
                                                   - sum(x["covered_fresh"] for x in alone))
            blocks[excl][str(k)] = entry

    data = {
        "units_file": str(units_file).replace(os.path.expanduser("~"), "~"),
        "sessions": len(sessions), "lift_source": lift_source, "ks": ks, "primary_k": primary,
        "events": {"rules": len(events), "evidence": len(strict), "known": len(known), "linked": len(linked),
                   "unlabelled_evidence": len(unlabelled)},
        "ceiling": ceiling, "blocks": blocks, "envelope_max": ENVELOPE_MAX_BYTES,
        "decision": decide(ceiling, blocks["excluded"][str(primary)]["estimate"]),
    }
    if unlabelled:
        print("%d evidence quote(s) unlabelled by #520's raters count as unverified" % len(unlabelled),
              file=sys.stderr)
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1, default=str))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
