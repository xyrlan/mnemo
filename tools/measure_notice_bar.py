"""Skip machine notices, raise the reflex's bar for them, or leave them? (#789)

Usage:
    PYTHONPATH=src python3 tools/measure_notice_bar.py           # the report, local
    PYTHONPATH=src python3 tools/measure_notice_bar.py --json    # the same as data

Read-only. It labels nothing and calls nothing: it reads #764's frozen sample
and its blind labels (``<vault>/.mnemo/inject-band/``, written by
``tools/measure_inject_band.py``), and rebuilds the population's prompt texts
from the reflex log and the transcripts the way that tool does.

**Why.** #764 found 45% of the prompts the reflex injected into are machine
notices nobody typed, and the near-bar notices its rater read were on-point
20% of the time. #789 asks for a bar, written before it is read, that picks
one of three branches for those prompts.

**Kind.** A prompt is a notice when :func:`mnemo.core.reflex.notice.kind`
says so — the classifier the hook ships, not #764's prefix list: the whole
prompt is a ``<task-notification>``, ``<cross-session-message>`` or
``<agent-message>`` element, a ``<mnemo-child-finished`` notice, or a peer
message in Claude Code's ``Another Claude session sent a message:`` frame.
``<mnemo-resume`` and ``<mnemo-pr-follow`` wakes, which #764 counted as
notices, are work orders and count as typed here. Everything else is typed.

**Pre-registered (committed before this tool computed anything).** #764's
labels have been read and its per-cell rates are in #789's body, so this bar
is not blind to them; what it does instead is reuse #764's own committed
reading, :func:`measure_inject_band.verdict` — ``yes`` when the 95% CI of the
difference (resampled by whole prompt, #764's seed and draws) is above 0
*and* the low side is at most half the high side — and add nothing tuned.
The branches, checked in this order, the first ``yes`` winning:

1. **skip**: the reflex does not run on notices, when the verdict of
   ``on_point(typed) - on_point(notice)``, both bands, is ``yes``. (This is
   #764's secondary "skip notices" bar, re-read with the shipped classifier.)
2. **raise**: notices need the judge's 0.5 where typed prompts need
   ``injectAt``, when the verdict of
   ``on_point(notice, >= 0.5) - on_point(notice, 0.40-0.49)`` is ``yes``.
   (This is #764's primary "raise to 0.5" bar, read within notices.)
3. **leave**: otherwise.

Reported beside the verdict, from the same data, for the branch picked and
for the other two: the injected bytes each would cut (population pairs it
removes, their logged ``rule_bytes``) and the on-point injections it would
lose (pairs removed in each cell times that cell's labelled on-point rate,
as #764 estimated its own).

**The result on the maintainer's Windows vault (2026-10-28)**, over #764's
240 blind labels (``claude-fable-5-1``) and its population, 2026-10-13 to
10-28: 787 injected pairs on 526 prompts whose text was found, 1.67 MB; 253
of the 526 prompts are notices by the shipped classifier (176
``task-notification``, 38 ``mnemo-child-finished``, 37
``cross-session-message``, 2 ``agent-message``; no bare peer frame).

- on-point: typed 46% near / 66% above (n 54 / 76); notices 20% / 57%
  (n 66 / 44);
- **skip**: typed 58% against notices 35%, +23.1 pp, 95% CI [+10.0, +35.7]:
  the CI excludes 0 but 35% is not at most half of 58% — not ``yes``;
- **raise**: notices above 57% against near 20%, +37.1 pp, CI [+18.6, +54.9],
  and 20% is at most half of 57% — ``yes``. **Branch: raise.**

What each branch costs, from the same data: *raise* keeps 131 pairs on 121
prompts out, 13% of the injected bytes (0.22 MB), at about 26 of some 421
on-point injections (6%); *skip* would have cut 34% of the bytes and lost
about 141 on-point (34%).

A correction made after the bar was committed and before its verdict was
read, recorded here: the first run kinded the sample off the prompt it froze,
which is the judge's view cut at 1200 characters, and 14 long notices had lost
the closing tag the classifier asks for. The kind now comes from each prompt
whole, as the hook sees it. Both runs picked *raise*.

**Limits**, #764's, all of them: one model rater, the prompt alone with no
earlier turn, the rule as its page reads today, one vault, two weeks. And the
cost is an estimate by cell rate, not a count of labelled pairs lost.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

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


#: #764's tool: its files, its bootstrap and its verdict, so this bar reads
#: the labels exactly as that one did.
mib = _sibling("measure_inject_band")

TYPED = mib.TYPED
NOTICE = mib.NOTICE
NEAR = mib.NEAR
ABOVE = mib.ABOVE

SKIP = "skip"
RAISE = "raise"
LEAVE = "leave"
BRANCHES = (SKIP, RAISE, LEAVE)


# --- the pure part ------------------------------------------------------------

def kind_of(prompt: Optional[str]) -> Optional[str]:
    """``notice`` or ``typed`` by the shipped classifier; ``None`` when the
    prompt was not found."""
    from mnemo.core.reflex import notice

    if prompt is None:
        return None
    return NOTICE if notice.kind(prompt) else TYPED


def bar(rated: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The pre-registered reading: both comparisons and the branch they pick.

    ``rated`` is labelled pairs carrying ``kind`` and ``band``.
    """
    skip = mib.diff_ci(rated, lambda p: p["kind"] == TYPED, lambda p: p["kind"] == NOTICE)
    skip["verdict"] = mib.verdict(skip, yes=SKIP, no=LEAVE)
    notices = [p for p in rated if p["kind"] == NOTICE]
    raised = mib.diff_ci(notices, lambda p: p["band"] == ABOVE, lambda p: p["band"] == NEAR)
    raised["verdict"] = mib.verdict(raised, yes=RAISE, no=LEAVE)
    if skip["verdict"] == SKIP:
        branch = SKIP
    elif raised["verdict"] == RAISE:
        branch = RAISE
    else:
        branch = LEAVE
    return {"skip": skip, "raise": raised, "branch": branch}


def cells(rated: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """On-point rate and count per ``kind/band`` cell of the labelled pairs."""
    out: Dict[str, Dict[str, Any]] = {}
    for kind in (TYPED, NOTICE):
        for band in (NEAR, ABOVE):
            here = [p for p in rated if p["kind"] == kind and p["band"] == band]
            out["%s/%s" % (kind, band)] = {"n": len(here), "on_point": mib.on_point(here)}
    return out


def removes(branch: str, pair: Dict[str, Any]) -> bool:
    """Would ``branch`` have kept this injected pair out?"""
    if pair.get("kind") != NOTICE:
        return False
    if branch == SKIP:
        return True
    if branch == RAISE:
        return pair["band"] != ABOVE
    return False


def costs(population: Sequence[Dict[str, Any]],
          rates: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Per branch: pairs and bytes it cuts, and the on-point injections it
    would lose — each removed pair weighted by its cell's labelled rate (a
    pair under 0.4, which only a vault configured lower can have, takes the
    near cell's)."""
    known = [p for p in population if p.get("kind") is not None]
    total_bytes = sum(p.get("bytes") or 0 for p in known)

    def rate(p: Dict[str, Any]) -> float:
        band = ABOVE if p["band"] == ABOVE else NEAR
        return rates["%s/%s" % (p["kind"], band)]["on_point"] or 0.0

    on_point_total = sum(rate(p) for p in known)
    out: Dict[str, Dict[str, Any]] = {}
    for branch in BRANCHES:
        cut = [p for p in known if removes(branch, p)]
        cut_bytes = sum(p.get("bytes") or 0 for p in cut)
        lost = sum(rate(p) for p in cut)
        out[branch] = {
            "pairs": len(cut),
            "prompts": len({p["row"] for p in cut}),
            "bytes": cut_bytes,
            "byte_share": (cut_bytes / total_bytes) if total_bytes else None,
            "on_point_lost": lost,
            "on_point_share": (lost / on_point_total) if on_point_total else None,
        }
    return {"total": {"pairs": len(known), "prompts": len({p["row"] for p in known}),
                      "bytes": total_bytes, "on_point": on_point_total,
                      "unmatched_pairs": len(population) - len(known)},
            "branches": out}


def report(population: Sequence[Dict[str, Any]], sample: Sequence[Dict[str, Any]],
           labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """``population`` carries ``kind`` already, read off each prompt whole.

    The sample takes its pairs' kind from there, not from the prompt it froze:
    that is the judge's view, cut at 1200 characters, and a long notice cut
    there has lost the closing tag the classifier asks for.
    """
    kinds = {p["row"]: p.get("kind") for p in population}
    rated = [dict(p, kind=kinds.get(p["row"])) for p in mib.labelled(sample, labels)]
    rated = [p for p in rated if p["kind"] is not None]
    rates = cells(rated)
    return {"labelled": len(rated), "cells": rates, "bar": bar(rated),
            "notice_prompts": len({p["row"] for p in population if p.get("kind") == NOTICE}),
            "prompts": len({p["row"] for p in population if p.get("kind") is not None}),
            "costs": costs(population, rates)}


def _pct(value: Optional[float]) -> str:
    return "-" if value is None else "%.0f%%" % (100 * value)


def _pp(value: Optional[float]) -> str:
    return "-" if value is None else "%+.1f" % (100 * value)


def format_report(data: Dict[str, Any]) -> str:
    lines = ["%d labelled pairs (#764's sample and labels, kinded by the shipped "
             "classifier); %d of %d injected prompts are notices"
             % (data["labelled"], data["notice_prompts"], data["prompts"]), "",
             "on-point by cell:"]
    for name, cell in data["cells"].items():
        lines.append("  %-14s %4s  (n=%d)" % (name, _pct(cell["on_point"]), cell["n"]))
    lines.append("")
    for key, title in (("skip", "skip:  typed - notice, both bands"),
                       ("raise", "raise: notice >= 0.5 - notice 0.40-0.49")):
        cmp = data["bar"][key]
        ci = cmp["ci"] or [None, None]
        lines.append("%s: %s vs %s, %s pp, 95%% CI [%s, %s] -> %s" % (
            title, _pct(cmp["high"]), _pct(cmp["low"]), _pp(cmp["diff"]),
            _pp(ci[0]), _pp(ci[1]), cmp["verdict"]))
    lines.append("branch: %s" % data["bar"]["branch"])
    lines.append("")
    total = data["costs"]["total"]
    lines.append("population: %d pairs on %d prompts, %.2f MB injected, ~%.0f on-point "
                 "(%d pairs whose prompt was not found left out)"
                 % (total["pairs"], total["prompts"], total["bytes"] / 1e6,
                    total["on_point"], total["unmatched_pairs"]))
    for branch, c in data["costs"]["branches"].items():
        lines.append("  %-5s cuts %3d pairs on %3d prompts, %4s of bytes (%.2f MB); "
                     "loses ~%.0f on-point (%s)"
                     % (branch, c["pairs"], c["prompts"], _pct(c["byte_share"]),
                        c["bytes"] / 1e6, c["on_point_lost"], _pct(c["on_point_share"])))
    return "\n".join(lines)


# --- the vault side -----------------------------------------------------------

def _rekind(vault: Path, population: List[Dict[str, Any]],
            projects: Sequence[str]) -> List[Dict[str, Any]]:
    """The population with ``kind`` read again off each prompt's text, found in
    its transcript by hash exactly as ``measure_inject_band --build`` does."""
    from mnemo.core.mcp.recall_sessions import _transcripts_by_session
    from mnemo.hooks.user_prompt_submit import _prompt_hash

    transcripts: Dict[str, Path] = {}
    for root in mib._projects_roots(vault, projects):
        for sid, path in _transcripts_by_session(root).items():
            transcripts.setdefault(sid, path)
    prompts: Dict[str, Dict[str, str]] = {}
    for sid in sorted({p["session_id"] for p in population}):
        path = transcripts.get(sid)
        if path is None:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        prompts[sid] = mib.transcript_prompts(text.splitlines(), _prompt_hash)
    return [dict(p, kind=kind_of((prompts.get(p["session_id"]) or {}).get(p["prompt_hash"])))
            for p in population]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--projects", action="append", default=[],
                        help="transcript root (repeatable; default: every account's)")
    parser.add_argument("--rater", default=mib.DEFAULT_RATER, help="whose labels to read")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli

    vault = cli._resolve_vault()
    out = vault / ".mnemo" / mib.OUT_DIR
    paths = [out / mib.POPULATION_NAME, out / mib.SAMPLE_NAME,
             out / ("labels-%s.json" % mib.mrg._safe_rater(args.rater))]
    if not all(p.is_file() for p in paths):
        print("error: %s needs measure_inject_band's population, sample and labels" % out)
        return 1
    population = json.loads(paths[0].read_text(encoding="utf-8"))["pairs"]
    sample = json.loads(paths[1].read_text(encoding="utf-8"))["pairs"]
    labels = json.loads(paths[2].read_text(encoding="utf-8")).get("labels") or {}

    data = report(_rekind(vault, population, args.projects), sample, labels)
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=2))
    else:
        print(_provenance.line(prov))
        print(format_report(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
