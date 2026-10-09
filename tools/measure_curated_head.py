"""Would mnemo curating what Claude Code loads from its own memory carry the rules it leaves on disk? (#633)

Usage:
    PYTHONPATH=src python3 tools/measure_curated_head.py [--units FILE] [--vault DIR] [--json]
                                                        # the report, from the cache; calls nothing
    PYTHONPATH=src python3 tools/measure_curated_head.py --dry-run
                                                        # the pending judge calls and their notional cost
    PYTHONPATH=src python3 tools/measure_curated_head.py --send [--workers 4] [--pause 2] [--limit N]
                                                        # judge the curated heads, then report

Every delivery test so far had mnemo compete with native memory. This one has
mnemo **feed** it: Claude Code loads the head of ``MEMORY.md`` (200 lines or
25,000 characters, :mod:`mnemo.core.native_memory`) next to ``CLAUDE.md`` in
every session, and #565 found 262 of #520's broad units that a note body on
disk says but the loaded head does not. Here the head is rewritten, as of each
session's start, and #520's redundancy judge reads it.

**Sessions and units.** #520's cache rerun with Text B as what Claude Code
loaded (#565's ``--native loaded``, ``--units``, default
``<vault>/.mnemo/null-audit/no-notes/units.json``), column ``both``, broad. Its
``redundant`` is the baseline. Each unit's texts are rebuilt as
``measure_prevented_repeats`` built them (:func:`unit_texts`), and the
rebuilt unit id is checked against that run's ``delivery.json``: the report
prints how many match, so a baseline the rebuild does not reproduce shows.

**The curated head, as of each session's start.** Nothing written after it:

- candidates, arm **native**: every auto-memory note in the session's memory
  directory born before the start, one line each (``- [name](file.md) —
  lead``, the lead its ``description:``, else its body's first sentence:
  #618's "line" shape), plus every ``MEMORY.md`` line of the index as of then
  that links no such note, verbatim. The index as of then is the one the
  transcript recorded (or #519's reconstruction), then the lines today's index
  dates before the start (:func:`index_lines`);
- arm **native + vault**: those, plus the live vault rules for the project
  and universal that existed then and that no candidate note is a source of,
  each as #618's line (``measure_relevance_block.entry_text``);
- ranked by the most earlier distinct sessions in which a rule the entry
  carries was relevant: a note carries the rules whose ``sources:`` name it
  (``measure_native_memory_cut.rule_sources``). Source **(a)**: #520's rater
  units from earlier sessions (#613's (a)); source **(b)**: the reflex judge's
  picks before the start (``picks_before``, #619, or #613's rebuilt history
  when the ledger does not cover the judge's life). Ties keep Claude Code's own
  order: the index position, then unindexed notes newest first, then vault
  rules by slug;
- fitted to Claude Code's load limit: entries in rank order while they fit
  200 lines and 25,000 characters; an entry that does not fit is left out.

**Judge.** #520's ``DELIVERY_SYSTEM``, both raters, unchanged, over Text A
(mnemo's SessionStart text, as before) and Text B (the session's loaded files,
``MEMORY.md`` replaced by the curated head). A unit is **gained** when both
raters say the curated Text B says it and the baseline did not, **lost** when
the baseline did and the curated one does not. Answers for an unchanged text
are the baseline's own. The judge never sees which text is curated.

**Estimate.** (gained − lost) per session × #618's one-line lift (+12.3 pp
[+0.0, +24.6], ``relevance-block/dilution-line-k15``), with ``combine``'s
joint session and lift-pair bootstrap. #618's lift is a 15-rule block's; a
200-line head dilutes more, so this is an upper bound on the lift.

**The bar, declared in #633 before measuring.** On arm native, source (a),
all must hold for **build**: (1) estimate ≥ 1/15 per session with CI lower
bound > 0; (2) CI lower bound > 0 without the rule that contributes the most;
(3) the head's p95 within 200 lines and 25,000 characters. Anything else is
do not build, the failed conditions named. The other arm and source are
printed beside and do not decide.

Note on (1), measured before any call: #618's line lift has 2.9% of its
bootstrap mass at or below zero (``python3`` over its 57 pair differences,
seed 520), so its 95% interval touches 0 and condition (1)'s lower bound is
at the mercy of that tail whatever the coverage.

Only ``--send`` calls a model, through ``core.llm`` and
``measure_prevented_repeats.Sender``, from a scratch directory so no
``CLAUDE.md`` or auto-memory reaches a rater; every answer is cached in
``<vault>/.mnemo/curated-head/delivery.json`` the moment it arrives, so a
rerun resumes. No real ``MEMORY.md`` or ``CLAUDE.md`` is written.
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
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrb = _sibling("measure_relevance_block")
mnc = _sibling("measure_native_memory_cut")
sb = mrb.sb
mpr = mrb.mpr
mrc = mrb.mrc
_provenance = mrb._provenance

from mnemo.core.native_memory import CHAR_LIMIT, INDEX, LINE_LIMIT, loaded_lines  # noqa: E402

THRESHOLD = mpr.THRESHOLD
BROAD = mpr.BROAD
ARMS = ("native", "native+vault")
SOURCES = ("a", "b")
PRIMARY = ("native", "a")
OUT_DIR = "curated-head"
DELIVERY_NAME = "delivery.json"
UNITS_DEFAULT = Path("null-audit") / "no-notes" / "units.json"
#: #618's one-line lift: the target among 14 other rules, line form.
LINE_LIFT_DIR = Path(mrb.OUT_DIR) / (mrb.DILUTION_DIR % ("line", 15))
WORKERS = 4
_WARNING = "> WARNING: MEMORY.md is "
_LINK = mrc._INDEX_LINK
_FRONT = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)


# --- the entries -----------------------------------------------------------------------------

def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def note_entry(name: str, text: str) -> Dict[str, Any]:
    """One auto-memory note as a head entry: ``- [title](file) — lead``, the
    lead its frontmatter ``description``, else its body's first sentence."""
    from mnemo.core.filters import parse_frontmatter

    fm = parse_frontmatter(text) or {}
    body = _FRONT.sub("", text, count=1)
    lead = _one_line(fm.get("description") or "") or _one_line(mrb.first_sentence(body))
    title = _one_line(fm.get("name") or "") or Path(name).stem
    line = "- [%s](%s)" % (title, name) + (" — %s" % lead if lead else "")
    return {"key": "note:" + name, "kind": "note", "file": name, "line": line}


def index_lines(recorded: str, reconstructed: str) -> List[str]:
    """The index as of the start, entries only: what the transcript recorded
    (cut where Claude Code cut it), then the lines dated before the start that
    it did not hold. Blank lines, headings and Claude Code's warning are no
    entries."""
    out: List[str] = []
    seen: Set[str] = set()
    for text in (recorded, reconstructed):
        for line in (text or "").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith(_WARNING) or s in seen:
                continue
            seen.add(s)
            out.append(s)
    return out


def native_entries(notes: Dict[str, str], born: Dict[str, float], index: Sequence[str]) -> List[Dict[str, Any]]:
    """Arm native's candidates, in Claude Code's own order: each note at the
    first index line that links it, the index lines that link no note in
    place, then the notes no line links, newest first."""
    out: List[Dict[str, Any]] = []
    placed: Set[str] = set()
    for pos, line in enumerate(index):
        links = [l for l in _LINK.findall(line) if l in notes]
        if not links:
            out.append({"key": "line:%d" % pos, "kind": "line", "file": None, "line": line, "pos": pos})
            continue
        for name in links:
            if name not in placed:
                placed.add(name)
                out.append(dict(note_entry(name, notes[name]), pos=pos))
    rest = sorted((n for n in notes if n not in placed), key=lambda n: (-born.get(n, 0.0), n))
    for i, name in enumerate(rest):
        out.append(dict(note_entry(name, notes[name]), pos=len(index) + i))
    return out


def vault_entries(slugs: Iterable[str], rules: Dict[str, Any], bodies: Dict[str, str], start_pos: int) -> List[Dict[str, Any]]:
    """Vault rules as #618's line, after every native entry in the tie order."""
    out = []
    for i, slug in enumerate(sorted(slugs)):
        text = mrb.entry_text(slug, "line", rules, bodies)
        line = "- " + text[2:] if text.startswith("• ") else text
        out.append({"key": "rule:" + slug, "kind": "rule", "file": None, "line": _one_line(line),
                    "slugs": [slug], "pos": start_pos + i})
    return out


def rank(entries: Sequence[Dict[str, Any]], score: Callable[[Dict[str, Any]], int]) -> List[Dict[str, Any]]:
    """By score, most first; ties keep the entries' own order."""
    return sorted(entries, key=lambda e: (-score(e), e["pos"], e["key"]))


def fit(entries: Sequence[Dict[str, Any]], line_limit: int = LINE_LIMIT,
        char_limit: int = CHAR_LIMIT) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(kept, left out): entries in order while they fit the load limit;
    one that would not fit is left out and the next is tried."""
    kept, out = [], []
    size = -1
    for e in entries:
        add = len(e["line"]) + 1
        if len(kept) < line_limit and size + add <= char_limit:
            kept.append(e)
            size += add
        else:
            out.append(e)
    return kept, out


def head_text(entries: Sequence[Dict[str, Any]]) -> str:
    return "\n".join(e["line"] for e in entries)


def swap_memory(files: Sequence[Tuple[str, str]], head: str, label: str) -> List[Tuple[str, str]]:
    """The session's loaded files with ``MEMORY.md`` replaced by ``head``; a
    head where nothing was loaded goes last under ``label``."""
    out, done = [], False
    for name, text in files:
        if INDEX in name:
            out.append((name, head))
            done = True
        else:
            out.append((name, text))
    if not done and head:
        out.append((label, head))
    return out


# --- ranking evidence ------------------------------------------------------------------------

def counts_before(events: Any, session_id: str, start: float) -> Dict[str, int]:
    """``{slug: earlier distinct sessions}`` from #613's events or the ledger."""
    if isinstance(events, dict):
        out = {s: sb.corrections_before(evs, session_id, start) for s, evs in events.items()}
        return {s: n for s, n in out.items() if n}
    return events.picks_before(None, start, session_id)


def scorer(counts: Dict[str, int], note_rules: Dict[str, Set[str]]) -> Callable[[Dict[str, Any]], int]:
    """An entry's evidence: the most any rule it carries has."""
    def score(e: Dict[str, Any]) -> int:
        slugs = e.get("slugs") or (note_rules.get(e["file"]) if e.get("file") else None) or ()
        return max((counts.get(s, 0) for s in slugs), default=0)
    return score


# --- outcomes and the estimate ---------------------------------------------------------------

def both(answers: Sequence[Optional[Dict[str, bool]]], key: str = "B") -> Optional[bool]:
    """Both raters' reading, None while one is missing."""
    if any(a is None for a in answers):
        return None
    return all(bool(a[key]) for a in answers)


def session_rows(units: Sequence[Dict[str, Any]], sessions: Sequence[str],
                 skip: str = "") -> List[Dict[str, int]]:
    """Per session, ``combine``'s shape with ``new`` = gained − lost. A unit
    carries ``base`` and ``cur`` (both raters' Text B readings); one not yet
    judged counts as unchanged. ``skip``: a slug left out."""
    rows = {s: {"units": 0, "new": 0, "delivered": 0, "redundant": 0, "prompts": 0, "gained": 0, "lost": 0}
            for s in sessions}
    for s in rows:
        rows[s].update({c: 0 for c in mpr.CHANNELS})
    for u in units:
        r = rows.get(u["session_id"])
        if r is None or u["slug"] == skip:
            continue
        r["units"] += 1
        gained, lost = outcome(u)
        r["gained"] += gained
        r["lost"] += lost
        r["new"] += gained - lost
        r["delivered"] += gained
        r["redundant"] += int(bool(u["base"]))
    return [rows[s] for s in sessions]


def outcome(u: Dict[str, Any]) -> Tuple[int, int]:
    cur = u.get("cur")
    if cur is None:
        return 0, 0
    return int(cur and not u["base"]), int(bool(u["base"]) and not cur)


def top_rule(units: Sequence[Dict[str, Any]]) -> Optional[str]:
    """The rule with the most gained − lost units, ties by slug."""
    net: Dict[str, int] = {}
    for u in units:
        g, l = outcome(u)
        if g or l:
            net[u["slug"]] = net.get(u["slug"], 0) + g - l
    best = [s for s in net if net[s] > 0]
    return min(best, key=lambda s: (-net[s], s)) if best else None


def carriers(units: Sequence[Dict[str, Any]], n: int = 10) -> List[Tuple[str, int, int]]:
    """``(slug, gained, lost)`` for the rules that move the most, net."""
    by: Dict[str, List[int]] = {}
    for u in units:
        g, l = outcome(u)
        if g or l:
            got = by.setdefault(u["slug"], [0, 0])
            got[0] += g
            got[1] += l
    return sorted(((s, g, l) for s, (g, l) in by.items()), key=lambda t: (-(t[1] - t[2]), t[0]))[:n]


def provenance_of(u: Dict[str, Any]) -> str:
    """Where a gained unit's rule was in native memory at the start:
    ``unloaded note`` (a note it comes from existed, but the loaded head did
    not link it), ``loaded note`` (it did: the line was rewritten), or
    ``absent`` (no note it comes from existed then)."""
    src = set(u.get("sources") or ())
    if not src:
        return "absent"
    return "loaded note" if src & set(u.get("loaded_notes") or ()) else "unloaded note"


def net_estimate(rows: Sequence[Dict[str, int]], diffs: Sequence[float],
                 n_boot: int = mpr.BOOTSTRAP, seed: int = mpr.SEED) -> Dict[str, Any]:
    """(gained − lost) per session × lift, sessions and lift pairs resampled
    jointly as ``combine`` does. No Poisson widening: a net difference is not
    a count, and it may be negative."""
    import random

    n, m = len(rows), len(diffs)
    if not n or not m:
        return {"sessions": n}
    rng = random.Random(seed)
    boots: Dict[str, List[float]] = {"N": [], "L": [], "E": []}
    for _ in range(n_boot):
        net = sum(rows[rng.randrange(n)]["new"] for _ in range(n)) / n
        lift = sum(diffs[rng.randrange(m)] for _ in range(m)) / m
        boots["N"].append(net)
        boots["L"].append(lift)
        boots["E"].append(net * lift)
    ci = {k: [mpr._pct(sorted(v), 0.025), mpr._pct(sorted(v), 0.975)] for k, v in boots.items()}
    net = sum(r["new"] for r in rows) / n
    lift = sum(diffs) / m
    est = net * lift
    return {"sessions": n, "N": net, "L": lift, "E": est, "ci": ci,
            "verdict": mpr.verdict(est, ci["E"][0], ci["E"][1])}


def estimate(units: Sequence[Dict[str, Any]], sessions: Sequence[str], diffs: Sequence[float]) -> Dict[str, Any]:
    rows = session_rows(units, sessions)
    e = net_estimate(rows, diffs) if diffs else {"sessions": len(sessions)}
    e["gained"] = sum(r["gained"] for r in rows)
    e["lost"] = sum(r["lost"] for r in rows)
    e["pending"] = sum(u.get("cur") is None for u in units)
    top = top_rule(units)
    if top is not None and diffs:
        e["without_top"] = dict(net_estimate(session_rows(units, sessions, skip=top), diffs), rule=top)
    return e


def decide(primary: Dict[str, Any], heads: Dict[str, Any], threshold: float = THRESHOLD) -> str:
    """#633's bar on arm native, source (a), declared before measuring."""
    if primary.get("pending"):
        return "pending: %d unit(s) not judged yet" % primary["pending"]
    failed = []
    if not mrb.positive(primary, threshold):
        failed.append("(1) estimate >= 1/15 with CI lower > 0")
    if not mrb.positive(primary.get("without_top"), threshold) or primary.get("without_top") is None:
        failed.append("(2) CI lower > 0 without the top rule")
    if not (heads["lines"]["p95"] <= LINE_LIMIT and heads["chars"]["p95"] <= CHAR_LIMIT):
        failed.append("(3) p95 within %d lines and %d characters" % (LINE_LIMIT, CHAR_LIMIT))
    return "build" if not failed else "do not build: " + "; ".join(failed)


# --- rebuilding #520's texts -----------------------------------------------------------------

def unit_prompts(columns: Dict[str, Sequence[Dict[str, Any]]]) -> Dict[Tuple[str, str], List[int]]:
    """``(session, slug) -> prompts`` over every column, as #520 judged a unit
    once at the last prompt any rater found it relevant to."""
    out: Dict[Tuple[str, str], Set[int]] = {}
    for rows in columns.values():
        for u in rows:
            out.setdefault((u["session_id"], u["slug"]), set()).update(u.get("prompts") or ())
    return {k: sorted(v) for k, v in out.items()}


def text_ids(session_id: str, ss: str, files: Sequence[Tuple[str, str]], slug: str) -> Tuple[str, str]:
    """``(texts, unit id)`` exactly as ``measure_prevented_repeats`` hashes them."""
    texts = mpr._sha(session_id, ss, *[t for f in files for t in f])
    return texts, mpr._sha(texts, slug)


def loaded_names(files: Sequence[Tuple[str, str]]) -> Set[str]:
    """The notes the loaded ``MEMORY.md`` links."""
    out: Set[str] = set()
    for name, text in files:
        if INDEX in name:
            out.update(_LINK.findall(mpr.loaded_memory(text)))
    return out


def stats(values: Sequence[int]) -> Dict[str, Optional[int]]:
    return {"median": sb.quantile(values, 0.5), "p95": sb.quantile(values, 0.95),
            "max": max(values) if values else None}


# --- report ----------------------------------------------------------------------------------

def _e(e: Dict[str, Any]) -> str:
    return sb._e(e) if e.get("E") is not None else "no estimate (%s)" % (
        "no lift" if e.get("pending") is None else "%d pending" % e["pending"])


def report_lines(data: Dict[str, Any]) -> List[str]:
    out = ["#633: a curated MEMORY.md head as of each session's start, judged by #520's redundancy judge",
           "units: %s (column both, broad), %d sessions, %d units; baseline redundant %d (--native loaded)" % (
               data["units_file"], data["sessions"], data["units"], data["baseline_redundant"]),
           "baseline rebuilt: %d of %d unit ids match the baseline's delivery cache, %d of those agree "
           "with its redundant flag; %d unit(s) are in transcripts from before Claude Code recorded what it "
           "loaded, rebuilt from today's files" % (data["rebuild"]["matched"], data["units"], data["rebuild"]["agree"],
                                                   data["rebuild"]["rebuilt"]),
           "lift: %s" % data["lift_source"],
           "bar: (1) estimate >= 1/15 (%.4f), CI lower > 0; (2) so without the top rule; (3) p95 <= %d lines, "
           "%d chars" % (THRESHOLD, LINE_LIMIT, CHAR_LIMIT), ""]
    for src in SOURCES:
        s = data["sources"][src]
        out.append("source (%s): %d rules, window %s .. %s; %d of %d sessions start after its first event" % (
            src, s["rules"], mrb._day(s["window"][0]), mrb._day(s["window"][1]), s["with_history"],
            data["sessions"]))
    out.append("hindsight: %d of %d candidate notes in the heads were modified after the session started "
               "(their text is today's)" % (data["hindsight"]["after"], data["hindsight"]["notes"]))
    for key, v in data["variants"].items():
        h = v["heads"]
        out += ["", "%s%s" % (key, "  (PRIMARY)" if key == "%s/%s" % PRIMARY else ""),
                "   head: entries median %s; lines median %s, p95 %s, max %s; chars median %s, p95 %s, max %s; "
                "%d session(s) leave entries out (median %s left out); %d head(s) differ from the baseline" % (
                    h["entries"]["median"], h["lines"]["median"], h["lines"]["p95"], h["lines"]["max"],
                    h["chars"]["median"], h["chars"]["p95"], h["chars"]["max"], h["cut_sessions"],
                    h["left_out"]["median"], h["changed"]),
                "   gained %d, lost %d, net %+d over %d sessions (%.3f per session); %d unit(s) pending" % (
                    v["estimate"]["gained"], v["estimate"]["lost"], v["estimate"]["gained"] - v["estimate"]["lost"],
                    data["sessions"], (v["estimate"]["gained"] - v["estimate"]["lost"]) / max(1, data["sessions"]),
                    v["estimate"]["pending"]),
                "   gained from: %s" % ", ".join("%s %d" % kv for kv in sorted(v["gained_from"].items())) or "-",
                "   estimate %s" % _e(v["estimate"])]
        w = v["estimate"].get("without_top")
        if w:
            out.append("   without %s: %s" % (w["rule"], _e(w)))
        if v["carriers"]:
            out.append("   rules that move it (gained/lost): " + "; ".join(
                "%s %d/%d" % c for c in v["carriers"]))
    out += ["", "DECISION (#633's bar, arm native, source (a)): %s" % data["decision"].upper()]
    return out


# --- main ------------------------------------------------------------------------------------

def _note_rules(vault: Path, docs: Dict[str, Any]) -> Dict[Tuple[str, str], Set[str]]:
    """``(agent, file) -> slugs`` whose ``sources:`` name that auto-memory note."""
    cache: Dict[str, Any] = {}
    out: Dict[Tuple[str, str], Set[str]] = {}
    for slug in docs:
        _reason, srcs = mnc.rule_sources(slug, docs, vault, cache)
        for src in srcs:
            out.setdefault(src, set()).add(slug)
    return out


def _born(path: Path) -> Optional[float]:
    return mrc._born(path)


def _notes_at(mem_dir: Optional[Path], start: float) -> Tuple[Dict[str, str], Dict[str, float], Dict[str, float]]:
    """(text, birth, mtime) of every note in ``mem_dir`` born before ``start``."""
    notes, born, mtime = {}, {}, {}
    if mem_dir is None:
        return notes, born, mtime
    for f in sorted(mem_dir.glob("*.md")):
        b = _born(f)
        if f.name == INDEX or b is None or b >= start:
            continue
        try:
            notes[f.name] = f.read_text(encoding="utf-8", errors="replace")
            mtime[f.name] = f.stat().st_mtime
        except OSError:
            continue
        born[f.name] = b
    return notes, born, mtime


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, llm, mirror, paths
    from mnemo.core.briefing import _load_jsonl_events

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--units", default="", help="#565's --native loaded units.json "
                                                "(default <vault>/.mnemo/%s)" % UNITS_DEFAULT.as_posix())
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--rater", action="append", default=[])
    ap.add_argument("--dry-run", action="store_true", help="the pending judge calls and their cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="judge the curated heads (model calls)")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=mpr.PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = args.rater or list(mpr.RATERS)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    projects, claude_home = Path(args.projects), Path(args.claude_home)
    units_file = Path(args.units).expanduser() if args.units else vault / ".mnemo" / UNITS_DEFAULT
    cache = mrc._read(units_file, None)
    if not cache:
        print("no #565 units at %s: run measure_prevented_repeats with --native loaded first" % units_file,
              file=sys.stderr)
        return 1
    units_raw = cache["columns"][mpr.BOTH]
    meta = cache["sessions"]
    sessions = sorted(cache["rated"])
    cols = {r: mrc.column(r, mpr.DELIVERY_SYSTEM) for r in raters}
    base_deliv = mrc._read(units_file.parent / DELIVERY_NAME, {})
    out_dir = vault / ".mnemo" / OUT_DIR
    deliv_path = out_dir / DELIVERY_NAME
    all_deliv = mrc._read(deliv_path, {})
    deliv = {r: all_deliv.setdefault(cols[r], {}) for r in raters}

    def answer(r: str, uid: str) -> Optional[Dict[str, bool]]:
        return deliv[r].get(uid) or (base_deliv.get(cols[r]) or {}).get(uid)

    rules = mpr.Rules(vault, projects, claude_home)
    docs = rules.ctx.index.get("docs") or {}
    dates = rules.ctx.dates
    note_rules = _note_rules(vault, docs)
    exported = mrb.export_rules(vault, docs)
    bodies = {s: (rules.ctx.pages.get(s) or ("", ""))[1] for s in exported}
    b_events, b_counts = mrb._source_b(vault, projects, mrb.INJECT_AT)
    events = {"a": mrb.rater_events(units_raw, meta), "b": b_events}

    prompts_of = unit_prompts(cache["columns"])
    by_sid: Dict[str, List[Dict[str, Any]]] = {}
    for u in units_raw:
        by_sid.setdefault(u["session_id"], []).append(u)

    units: Dict[str, List[Dict[str, Any]]] = {"%s/%s" % (a, s): [] for a in ARMS for s in SOURCES}
    heads: Dict[str, Dict[str, List[int]]] = {k: {"entries": [], "lines": [], "chars": [], "left_out": [],
                                                  "changed": []} for k in units}
    pending: Dict[str, Dict[str, Dict[str, Any]]] = {k: {} for k in units}
    rebuild = {"matched": 0, "agree": 0, "rebuilt": 0}
    hindsight = {"notes": 0, "after": 0}
    for sid in sessions:
        m = meta[sid]
        start = float(m["start"])
        w = mpr.walk(_load_jsonl_events(Path(m["path"])))
        by_i = {p["i"]: p for p in w["prompts"]}
        if m["cwd"] not in rules.ctx._roots:
            rules.ctx._roots[m["cwd"]] = mrc.repo_root_for(m["cwd"])
        root = rules.ctx._roots[m["cwd"]]
        mem_dir = mrc.memory_dir_for(m["cwd"], root, claude_home)
        agent = mirror._agent_from_project_dir(mem_dir.parent.name) if mem_dir else ""
        notes, born, mtime = _notes_at(mem_dir, start)
        n_rules = {name: note_rules.get((agent, name), set()) for name in notes}
        carried = set().union(*n_rules.values()) if n_rules else set()
        first = by_i.get(min(by_i)) if by_i else {}

        def fallback(ts: float, root=root, mem_dir=mem_dir) -> List[Tuple[str, str]]:
            files = mrc.claude_md_as_of(root, ts, claude_home)
            index, _ = mrc.memory_as_of(mem_dir, ts, "")
            return files + ([("MEMORY.md (as of then)", index)] if index else [])

        start_files = mpr.native_files(first or {}, lambda: fallback(start))
        recorded = next((t for n, t in start_files if INDEX in n), "")
        reconstructed, _ = mrc.memory_as_of(mem_dir, start, "")
        index = index_lines(recorded, reconstructed)
        natives = native_entries(notes, born, index)
        eligible = [s for s, doc in docs.items() if sb.eligible(doc, m["project"]) and s not in carried
                    and dates.get(s) is not None
                    and (mrc.first_learned(dates[s], sid) or float("inf")) < start]
        vaults = vault_entries(eligible, exported, bodies, len(natives) + len(notes) + 1)
        label = str((mem_dir or claude_home / "projects" / "?" / "memory") / INDEX)

        for src in SOURCES:
            score = scorer(counts_before(events[src], sid, start), n_rules)
            for arm in ARMS:
                key = "%s/%s" % (arm, src)
                kept, left = fit(rank(natives + (vaults if arm == "native+vault" else []), score))
                head = head_text(kept)
                h = heads[key]
                h["entries"].append(len(kept))
                h["lines"].append(len(head.splitlines()))
                h["chars"].append(len(head))
                h["left_out"].append(len(left))
                h["changed"].append(int(head != recorded))
                assert loaded_lines(head) == len(head.splitlines())
                in_head = {e["file"] for e in kept if e.get("file")}
                if key == "%s/%s" % PRIMARY:
                    hindsight["notes"] += len(in_head)
                    hindsight["after"] += sum(mtime[n] > start for n in in_head)
                for u in by_sid.get(sid, []):
                    idxs = prompts_of[(sid, u["slug"])]
                    last = by_i[max(idxs)]
                    ss = mpr._head("\n\n".join(w["session_start"][:last["session_start_n"]]),
                                   mpr.SESSION_START_CHARS)
                    files = mpr.native_files(last, lambda ts=last["ts"]: fallback(ts))
                    if key == "%s/%s" % PRIMARY:
                        _, bid = text_ids(sid, ss, files, u["slug"])
                        b = both([(base_deliv.get(cols[r]) or {}).get(bid) for r in raters])
                        rebuild["matched"] += int(b is not None)
                        rebuild["rebuilt"] += int(not last.get("native"))
                        rebuild["agree"] += int(b is not None and b == bool(u["redundant"]))
                    cur_files = swap_memory(files, head, label)
                    texts, uid = text_ids(sid, ss, cur_files, u["slug"])
                    cur = both([answer(r, uid) for r in raters])
                    sources = {n for n, slugs in n_rules.items() if u["slug"] in slugs}
                    units[key].append({"session_id": sid, "slug": u["slug"], "base": bool(u["redundant"]),
                                       "cur": cur, "sources": sorted(sources),
                                       "loaded_notes": sorted(loaded_names(files) & sources)})
                    if cur is None:
                        pending[key][uid] = {"id": uid, "texts": texts, "rule": rules.text(u["slug"]),
                                             "ss": ss, "files": cur_files, "notes": [],
                                             "empty": not ss and not cur_files}

    todo = {}
    for key in units:
        for uid, u in pending[key].items():
            todo.setdefault(uid, u)
    batches = {r: mpr.delivery_batches(todo, deliv[r]) for r in raters}
    if args.dry_run or args.send:
        for r in raters:
            usd = mpr.notional(r, [(mpr.batch_prompt(b, True), mpr.DELIVERY_SYSTEM) for b in batches[r]], 400)
            print("%s: %d pending delivery call(s) over %d unit text(s); notional ~$%.2f "
                  "(subscription usage, not money)" % (r, len(batches[r]), len(todo), usd), file=sys.stderr)
        if args.dry_run:
            sample = next((b for bs in batches.values() for b in bs), None)
            if sample:
                print("\nfirst call's prompt, its first 3,000 chars:\n%s" % mpr.batch_prompt(sample, True)[:3000],
                      file=sys.stderr)
            return 0
        provider = llm.resolve(cfg)
        timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
        out_dir.mkdir(parents=True, exist_ok=True)
        sender = mpr.Sender(provider, timeout, out_dir / "calls.jsonl", args.workers, args.pause)
        here = os.getcwd()
        scratch = tempfile.mkdtemp(prefix="mnemo-curated-head-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches a rater
        try:
            def on_deliv(r: str, batch: Sequence[Dict[str, Any]]) -> Callable[[str], None]:
                def take(text: str) -> None:
                    got = mpr.parse_delivery(text, [u["id"] for u in batch])
                    if got:
                        deliv[r].update(got)
                        mrc._write(deliv_path, all_deliv)
                return take
            calls = [(r, mpr.batch_prompt(b, True), mpr.DELIVERY_SYSTEM, on_deliv(r, b))
                     for r in raters for b in batches[r]]
            sender.run(calls[:args.limit] if args.limit else calls)
        finally:
            os.chdir(here)
            shutil.rmtree(scratch, ignore_errors=True)
        print("spent $%.2f notional this run; rerun without --send for the report" % sender.usd, file=sys.stderr)
        return 0

    rl = mrb.rl
    lift_dir = vault / ".mnemo" / LINE_LIFT_DIR
    arm_col = rl.column(rl.DEFAULT_MODEL, rl.ARM_SYSTEM)
    judge_col = "%s/%s" % (arm_col, rl.column(rl.DEFAULT_JUDGE, rl.JUDGE_SYSTEM))
    diffs, lift_stats = mrb.lift_diffs(mrc._read(lift_dir / mrb.PAIRS_NAME, []),
                                       mrc._read(lift_dir / mrb.ANSWERS_NAME, {}).get(arm_col, {}),
                                       mrc._read(lift_dir / mrb.VERDICTS_NAME, {}).get(judge_col, {}))
    lift_source = ("#618's line lift %+.1f pp [%+.1f, %+.1f] over %d pairs" % (
        100 * lift_stats["lift"], 100 * lift_stats["ci"][0], 100 * lift_stats["ci"][1], len(diffs))
        if diffs else "none: no %s cache" % LINE_LIFT_DIR.as_posix())

    variants: Dict[str, Any] = {}
    for key, us in units.items():
        h = heads[key]
        gained_from: Dict[str, int] = {}
        for u in us:
            if outcome(u)[0]:
                p = provenance_of(u)
                gained_from[p] = gained_from.get(p, 0) + 1
        variants[key] = {
            "heads": {"entries": stats(h["entries"]), "lines": stats(h["lines"]), "chars": stats(h["chars"]),
                      "left_out": stats([n for n in h["left_out"] if n]),
                      "cut_sessions": sum(1 for n in h["left_out"] if n), "changed": sum(h["changed"])},
            "estimate": estimate(us, sessions, diffs), "gained_from": gained_from, "carriers": carriers(us)}
    primary = variants["%s/%s" % PRIMARY]
    data = {
        "units_file": str(units_file).replace(os.path.expanduser("~"), "~"), "sessions": len(sessions),
        "units": len(units_raw), "baseline_redundant": sum(bool(u["redundant"]) for u in units_raw),
        "rebuild": rebuild, "lift_source": lift_source, "hindsight": hindsight,
        "sources": {src: {"rules": len(counts_before(evs, "", float("inf"))), "window": list(mrb.window(evs) or
                                                                                            (None, None)),
                          "with_history": mrb.with_history(sessions, meta, evs)} for src, evs in events.items()},
        "variants": variants,
        "decision": decide(primary["estimate"], primary["heads"]) if diffs else "pending: no lift",
    }
    prov = _provenance.provenance(__file__, argv, vault=vault, blind_spots=[
        _provenance.transcripts_blind_spot(projects),
        "notes are today's text, born before the session (deleted notes are gone); %d of %d in the primary "
        "heads were modified after their session started" % (hindsight["after"], hindsight["notes"]),
        "(b) read mnemo's judge-picks ledger" if b_counts["source"] == "ledger" else
        "(b) rebuilt from reflex logs and transcripts"])
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1, default=str))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
