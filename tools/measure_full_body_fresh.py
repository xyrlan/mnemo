"""Does the full-body reflex help in the sessions that ran it? #543's pre-registered fresh check (#545)

Usage:
    PYTHONPATH=src python3 tools/measure_full_body_fresh.py            # dry: fresh emissions, units so far, when it is due
    PYTHONPATH=src python3 tools/measure_full_body_fresh.py --send     # once due: rate, place, answer, judge, report
        [--force] [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_full_body_fresh.py --json     # the report as data

#535 (``measure_full_body``) measured the full rule body against the one-line
preview on #527's old units, answered anew. #542 (PR #543) then shipped it:
the reflex writes ``• [[slug]]:`` and the rule's body under it
(``core/reflex/render.py``). It went live on the maintainer's machine at
:data:`LIVE`, when the checkout the hooks import was fast-forwarded. PR #543
fixed this check before any data existed:

- **When:** at least :data:`MIN_DAYS` days of sessions with the change, and
  :data:`MIN_UNITS` fresh units, whichever comes later. Before both hold the
  tool **refuses to send** unless ``--force``, and it prints how many fresh
  units exist and the date the condition should be met.
- **Units:** #527's units (``measure_broad_value``) in human sessions started
  after :data:`LIVE`, whose reflex carried the rule in the full format (a
  ``format: "full"`` row in ``reflex-log.jsonl``). #520's pipeline
  (``measure_prevented_repeats``: both relevance raters, the delivery judge)
  runs on those sessions alone, into this tool's cache, and its delivered-
  and-new broad units whose reflex block carried the rule are kept.
- **Arms:** ``full`` is the prompt as shipped (#527's *with*: the rule's entry
  as the hook wrote it, whole or cut), ``without`` the same prompt without
  the rule's bytes (#527's *without*: its whole entry, read by entry, so no
  body line is left behind). Both are answered on the unit's own recorded
  model, by ``measure_broad_value`` unchanged.
- **Judge:** #527's and #535's two blind raters, rule-blind with slugs and
  ``[[…]]`` masked, reply order seeded and flipped for the second rater, ties
  allowed; primary reading both raters agreeing, else a tie.
- **Bars**, each reported whatever its sign:
  - **confirmed** — ``h(full)`` ≥ :data:`BAR_H` with its CI lower bound > 0
    (bootstrap over units); **not confirmed** when the CI upper bound is
    under the bar;
  - **product (#527's)** — helpful changes ≥ 1 per 15 human sessions with a
    CI lower bound > 0 (bootstrap over sessions): the fresh units per rated
    fresh session times their mean ``h``. The change touched only the
    reflex, so this counts what the reflex delivered.
- **Split:** rules delivered whole against rules cut to fit, from the log
  row's ``rule_whole``.

The hook keeps one rotated generation of ``reflex-log.jsonl`` (1 MB each, about
4.5 days of rows on the maintainer's machine), so by the due date the first
days' rows are gone. Every run copies the fresh ``format: "full"`` rows into
this tool's cache (``reflex-rows.jsonl``), and where a row is gone anyway the
block itself says the same: a full block has a head alone on its line, and a
cut entry ends with the hook's ``[cut to fit the prompt limit …]`` pointer.
The report says which source each unit's reading came from.

A session that started under :data:`SETTLE_HOURS` ago may still be running;
#520 freezes a session's prompts the first time it reads it, so those
sessions wait for a later run.

Only ``--send`` calls a model. Every answer is cached under ``--out``
(default ``<vault>/.mnemo/full-body-fresh``) as it arrives, so a rerun
resumes; nothing is written to #520's, #527's or #535's caches.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bv = _sibling("measure_broad_value")
mpr = bv.mpr
mrc = bv.mrc

#: When the full-body reflex went live on the maintainer's machine: the main
#: checkout the hooks import was fast-forwarded to ``72edf85`` (#543).
LIVE = "2026-09-28T23:09:00Z"
MIN_DAYS = 14
MIN_UNITS = 60
#: The confirmation bar on ``h(full)``, fixed in PR #543.
BAR_H = 0.10
#: #527's product bar: one helpful change per 15 human sessions.
THRESHOLD = bv.THRESHOLD
SETTLE_HOURS = 24
OUT_DIR = "full-body-fresh"
ROWS_NAME = "reflex-rows.jsonl"
FRESH_UNITS_NAME = "units-fresh.json"
#: What ``render.cut_line`` writes under a body cut to fit.
CUT_MARK = "[cut to fit the prompt limit"
#: A log row is the hook's for a prompt when written within this many seconds
#: of it (the judge answers in about a second).
ROW_SLACK = 120
WHOLE, CUT, UNKNOWN = "whole", "cut", "unknown"


# --- the log -------------------------------------------------------------------------------

def fresh_rows(rows: Iterable[Dict[str, Any]], live: str = LIVE) -> List[Dict[str, Any]]:
    """The reflex-log rows the full format wrote since ``live``."""
    return [r for r in rows if isinstance(r, dict) and r.get("format") == "full"
            and r.get("emitted") and str(r.get("ts") or "") >= live]


def _row_key(row: Dict[str, Any]) -> str:
    return json.dumps([row.get("session_id"), row.get("prompt_hash"), row.get("ts"), row.get("emitted")])


def merge_rows(kept: Sequence[Dict[str, Any]], live_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The archive with the log's rows it lacks, oldest first."""
    seen = {_row_key(r) for r in kept}
    out = list(kept)
    for r in live_rows:
        k = _row_key(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return sorted(out, key=lambda r: str(r.get("ts") or ""))


def read_rows(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def write_rows(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(path)


# --- the blocks ----------------------------------------------------------------------------

def entry_whole(entry: str) -> bool:
    """Whether a full-format entry carries the rule's whole body: its head
    stands alone and no cut pointer ends it. A preview line inside a full
    block (the page could not be read) is not whole."""
    first = entry.split("\n", 1)[0]
    return bool(mpr._ENTRY_HEAD.fullmatch(first)) and CUT_MARK not in entry


def full_emissions(events: List[dict]) -> List[Dict[str, Any]]:
    """Every rule a full-format reflex block delivered in a transcript:
    ``{slug, whole, ts}`` in order, one per entry."""
    out: List[Dict[str, Any]] = []
    ts = None
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("timestamp"):
            ts = ev.get("timestamp")
        att = ev.get("attachment")
        if not (isinstance(att, dict) and att.get("type") == "hook_additional_context"):
            continue
        for text in mpr._hook_texts(att):
            if mpr._REFLEX in text and mpr.is_full_block(text):
                for slug, entry in mpr.reflex_entries(text):
                    if slug:
                        out.append({"slug": slug, "whole": entry_whole(entry), "ts": ts})
    return out


def carriers(ctx: Dict[str, Any], slug: str, project: str) -> List[str]:
    """The reflex blocks in a placed unit's context that carried the rule, in order."""
    return [t for _, t in ctx["reflex"] if bv.carries_reflex(t, slug, project)]


def entry_of(block: str, slug: str, project: str) -> Optional[str]:
    for s, entry in mpr.reflex_entries(block):
        if s and bv.same_rule(s, slug, project):
            return entry
    return None


def log_whole(rows: Sequence[Dict[str, Any]], session_id: str, block: str, slug: str,
              project: str, before: Optional[float]) -> Optional[bool]:
    """``rule_whole`` for the rule in the log rows that wrote ``block``: same
    session, the same emitted rules in order, written by ``before`` (plus
    :data:`ROW_SLACK`). None when no such row is left, or the rows disagree."""
    emitted = mpr.reflex_slugs(block)
    got: Set[bool] = set()
    for r in rows:
        if r.get("session_id") != session_id or list(r.get("emitted") or []) != emitted:
            continue
        at = mrc.epoch(r.get("ts"))
        if before is not None and at is not None and at > before + ROW_SLACK:
            continue
        whole = r.get("rule_whole")
        for i, s in enumerate(emitted):
            if bv.same_rule(s, slug, project) and isinstance(whole, list) and i < len(whole):
                got.add(bool(whole[i]))
    return got.pop() if len(got) == 1 else None


def unit_reading(row: Dict[str, Any], ctx: Dict[str, Any], project: str,
                 log: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A fresh unit's reading: the last full-format block that carried the
    rule, whether the rule was whole in it (the log's ``rule_whole``, else
    the entry itself) and where that came from. None when no full block
    carried it — the old format, or ``reflex.body: preview``."""
    slug = row["slug"]
    full = [b for b in carriers(ctx, slug, project) if mpr.is_full_block(b)]
    if not full:
        return None
    block = full[-1]
    entry = entry_of(block, slug, project) or ""
    text_whole = entry_whole(entry)
    whole = log_whole(log, row["session_id"], block, slug, project, ctx.get("ts"))
    return {"whole": WHOLE if (text_whole if whole is None else whole) else CUT,
            "source": "transcript" if whole is None else "log",
            "agrees": None if whole is None else whole == text_whole}


# --- when it is due ------------------------------------------------------------------------

def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def due(now: float, units: Optional[int], live: str = LIVE, days: int = MIN_DAYS,
        min_units: int = MIN_UNITS) -> Dict[str, Any]:
    """The due condition: ``days`` since ``live`` and ``min_units`` units.
    ``units`` is None until #520's raters have read the fresh sessions."""
    start = mrc.epoch(live) or 0.0
    date = start + days * 86400
    return {"date": _iso(date), "date_at": date, "date_ok": now >= date, "units": units,
            "units_ok": units is not None and units >= min_units,
            "due": now >= date and units is not None and units >= min_units,
            "days_in": (now - start) / 86400}


def projected(now: float, per_day: Optional[float], live: str = LIVE, days: int = MIN_DAYS,
              min_units: int = MIN_UNITS) -> Optional[str]:
    """The date both conditions hold, at ``per_day`` units a day since ``live``."""
    if not per_day or per_day <= 0:
        return None
    start = mrc.epoch(live) or 0.0
    return _iso(max(start + days * 86400, start + min_units / per_day * 86400))


def historical_yield(source: Optional[Dict[str, Any]],
                     load: Callable[[str], List[dict]]) -> Optional[Dict[str, Any]]:
    """How many reflex emissions became a unit before the change: #520's frozen
    reflex-channel delivered-and-new units over the distinct (session, rule)
    pairs its rated sessions' reflex blocks delivered. None without #520's file."""
    if not source:
        return None
    units = sum(1 for r in source["columns"].get(mpr.BOTH) or []
                if r.get("new") and r.get("judged", True) and r.get("reflex"))
    pairs = 0
    for sid in source["rated"]:
        slugs: Set[str] = set()
        for ev in load(source["sessions"][sid]["path"]):
            att = ev.get("attachment") if isinstance(ev, dict) else None
            if isinstance(att, dict) and att.get("type") == "hook_additional_context":
                for text in mpr._hook_texts(att):
                    if mpr._REFLEX in text:
                        slugs.update(mpr.reflex_slugs(text))
        pairs += len(slugs)
    return {"units": units, "pairs": pairs, "per_pair": units / pairs if pairs else None}


# --- the numbers ---------------------------------------------------------------------------

def h_verdict(st: Dict[str, Any], bar: float = BAR_H) -> str:
    """The confirmation bar on ``h(full)``, CI over units."""
    if not st.get("n"):
        return "no estimate yet"
    lo, hi = st["ci"]
    if st["h"] >= bar and lo > 0:
        return "confirmed"
    if hi < bar:
        return "not confirmed"
    return "inconclusive"


def readings(units: Sequence[Dict[str, Any]], rated: Sequence[str], arms: Dict[str, Any],
             verdicts: Dict[str, Dict[str, Dict[str, str]]], raters: Sequence[str]) -> Dict[str, Any]:
    """Both bars for both raters together and each alone, and the whole/cut
    split on the primary reading. ``units``: ``{id, session_id, whole}``."""
    columns = ([bv.BOTH] if len(raters) > 1 else []) + list(raters)
    new_units: Dict[str, List[str]] = {}
    for u in units:
        new_units.setdefault(u["session_id"], []).append(u["id"])
    out: Dict[str, Any] = {}
    split: Dict[str, Any] = {}
    for col in columns:
        rs = list(raters) if col == bv.BOTH else [col]
        h = {u["id"]: (bv.unit_h(verdicts, u["id"], rs) if (arms.get(u["id"]) or {}).get("measurable") else None)
             for u in units}
        hs = [x for x in h.values() if x is not None]
        st_h = bv.h_ci(hs)
        st_e = bv.combine(bv.per_session_rows(rated, new_units, h))
        out[col] = {"h": st_h, "h_verdict": h_verdict(st_h), "product": st_e,
                    "product_verdict": bv.verdict(st_e)}
        if col == columns[0]:
            for kind in (WHOLE, CUT):
                split[kind] = bv.h_ci([h[u["id"]] for u in units
                                       if u["whole"] == kind and h[u["id"]] is not None])
    return {"columns": out, "split": split, "primary": columns[0]}


# --- report --------------------------------------------------------------------------------

def _h(st: Dict[str, Any]) -> str:
    if not st.get("n"):
        return "no units judged"
    return "h %+.3f [%+.3f, %+.3f] over %d units (%d better, %d worse)" % (
        st["h"], st["ci"][0], st["ci"][1], st["n"], st["wins"], st["losses"])


def report_lines(data: Dict[str, Any]) -> List[str]:
    d = data["due"]
    lines = [
        "full-body reflex live since %s (%.1f days)" % (data["live"], d["days_in"]),
        "reflex-log rows in the full format since then: %d on disk, %d kept in this tool's archive; "
        "%d from human sessions" % (data["rows"]["log"], data["rows"]["archive"], data["rows"]["human"]),
        "fresh human sessions: %d started since then, %d with a full-format reflex block; "
        "%d (session, rule) emissions, %d whole, %d cut"
        % (data["sessions"], data["sessions_with_full"], data["emissions"]["n"],
           data["emissions"]["whole"], data["emissions"]["cut"]),
    ]
    if data.get("rated") is None:
        lines.append("fresh units: not counted yet: #520's raters have not read the fresh sessions "
                     "(that is the first --send once the date holds)")
    else:
        lines.append("fresh units: %d, in %d rated fresh sessions (#520 broad reading, both raters, "
                     "delivered-and-new, the reflex carried the rule in the full format)%s"
                     % (data["units"], data["rated"],
                        "; %d more wait for #520's delivery judge" % data["provisional"]
                        if data.get("provisional") else ""))
        if data.get("sources"):
            lines.append("  whole/cut read from: %s" % ", ".join(
                "%s %d" % kv for kv in sorted(data["sources"].items())))
    lines.append("due: from %s (%s) and at %d fresh units (%s); %s"
                 % (d["date"], "reached" if d["date_ok"] else "not yet", MIN_UNITS,
                    "reached" if d["units_ok"] else "not yet", "DUE" if d["due"] else "NOT DUE"))
    p = data.get("projection") or {}
    if p.get("date"):
        lines.append("  both conditions should hold by %s (%s)" % (p["date"], p["how"]))
    elif p.get("how"):
        lines.append("  the date both conditions hold: unknown (%s)" % p["how"])
    res = data.get("results")
    if not res:
        return lines
    for col, st in res["columns"].items():
        primary = col == res["primary"]
        lines += ["", "%s%s:" % (col, "  <- the verdict" if primary else "")]
        lines.append("  h(full)   %s" % _h(st["h"]))
        lines.append("            %s   (bar: h >= %+.2f with CI lower bound > 0, bootstrap over units)"
                     % (st["h_verdict"].upper(), BAR_H))
        e = st["product"]
        if e.get("E") is None:
            lines.append("  product   no estimate yet")
        else:
            ci = e["ci"]
            lines.append("  product   %.3f fresh units per session [%.3f, %.3f] x h %+.3f = %s helpful changes "
                         "per human session [%.4f, %.4f]" % (e["rate"], ci["rate"][0], ci["rate"][1], e["h"],
                                                             bv._per(e["E"]), ci["E"][0], ci["E"][1]))
            lines.append("            %s   (#527's bar: >= %.4f = 1 per 15, CI lower bound > 0, "
                         "bootstrap over sessions)" % (st["product_verdict"].upper(), THRESHOLD))
    lines += ["", "h(full) by delivery (%s, rule_whole):" % res["primary"]]
    for kind in (WHOLE, CUT):
        lines.append("  %-6s %s" % (kind, _h(res["split"][kind])))
    return lines


# --- driver --------------------------------------------------------------------------------

def _quiet(fn: Callable[[List[str]], int], argv: List[str], log: Path) -> int:
    """Run a sibling tool's ``main`` with its report written to ``log``."""
    with log.open("w", encoding="utf-8") as fh, contextlib.redirect_stdout(fh):
        return fn(argv)


def main(argv: Optional[List[str]] = None, now: Optional[float] = None) -> int:
    from mnemo.core import config, paths
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.core.log_utils import iter_rotated_rows

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=str(Path("~/.claude/projects").expanduser()))
    ap.add_argument("--claude-home", default=str(Path("~/.claude").expanduser()))
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/%s)" % OUT_DIR)
    ap.add_argument("--baseline", default="",
                    help="#520's frozen units.json, for the yield estimate "
                         "(default <vault>/.mnemo/prevented-repeats/units.json)")
    ap.add_argument("--rater", action="append", default=[])
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--force", action="store_true", help="send before the due condition holds")
    ap.add_argument("--workers", type=int, default=bv.WORKERS)
    ap.add_argument("--pause", type=float, default=bv.PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = args.rater or list(bv.RATERS)
    now = time.time() if now is None else now

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    pr_out, bv_out = out / "prevented-repeats", out / "broad-value"

    # the log's fresh rows, kept before the hook rotates them away
    live_rows = fresh_rows(iter_rotated_rows(vault / ".mnemo" / "reflex-log.jsonl"))
    archive = merge_rows(read_rows(out / ROWS_NAME), live_rows)
    write_rows(out / ROWS_NAME, archive)

    # what the full format delivered so far, no model call
    sessions = mpr.collect_sessions(Path(args.projects), vault, LIVE)
    events: Dict[str, List[dict]] = {}

    def load(sid: str) -> List[dict]:
        if sid not in events:
            events[sid] = _load_jsonl_events(Path(sessions[sid]["path"]))
        return events[sid]

    emissions = {"n": 0, "whole": 0, "cut": 0}
    with_full = 0
    for sid in sorted(sessions):
        pairs: Dict[str, bool] = {}
        for e in full_emissions(load(sid)):
            pairs[e["slug"]] = pairs.get(e["slug"], True) and e["whole"]
        with_full += bool(pairs)
        emissions["n"] += len(pairs)
        emissions["whole"] += sum(pairs.values())
        emissions["cut"] += sum(not w for w in pairs.values())
    human_rows = sum(1 for r in archive if r.get("session_id") in sessions)

    # step 1, #520 on the fresh sessions (sends only when the date holds)
    state = due(now, None)
    send_rate = args.send and (state["date_ok"] or args.force)
    if args.send and not send_rate:
        print("refusing to send: the check is due from %s, %.1f days from now (--force sends anyway)"
              % (state["date"], (state["date_at"] - now) / 86400), file=sys.stderr)
    if send_rate:
        pr_out.mkdir(parents=True, exist_ok=True)
        _quiet(mpr.main, ["--since", LIVE, "--until", _iso(now - SETTLE_HOURS * 3600), "--projects", args.projects,
                          "--claude-home", args.claude_home, "--vault", str(vault), "--out", str(pr_out),
                          "--send", "--workers", str(args.workers), "--pause", str(args.pause)]
               + (["--limit", str(args.limit)] if args.limit else [])
               + [x for r in raters for x in ("--rater", r)], pr_out / "report.txt")

    # the fresh units, from #520's cache when there is one
    source = mrc._read(pr_out / mpr.UNITS_NAME, None)
    data: Dict[str, Any] = {"live": LIVE, "sessions": len(sessions), "sessions_with_full": with_full,
                            "emissions": emissions,
                            "rows": {"log": len(live_rows), "archive": len(archive), "human": human_rows}}
    units: List[Dict[str, Any]] = []
    rows: List[Dict[str, Any]] = []
    rated: List[str] = []
    if source is not None:
        rated = [s for s in source["rated"] if s in sessions]
        both = [r for r in source["columns"].get(mpr.BOTH) or [] if r["session_id"] in sessions]
        cands = [r for r in both if r.get("new") and r.get("reflex") and r.get("judged", True)]
        sources: Dict[str, int] = {}
        for r in sorted(cands, key=lambda r: r["session_id"]):
            project = sessions[r["session_id"]]["project"]
            ctx = bv.place(r, load(r["session_id"]), project)
            reading = unit_reading(r, ctx, project, archive) if ctx else None
            if reading is None:
                continue
            rows.append(r)
            units.append({"id": bv.unit_id(r), "session_id": r["session_id"], "slug": r["slug"],
                          "whole": reading["whole"], "source": reading["source"]})
            sources[reading["source"]] = sources.get(reading["source"], 0) + 1
            if reading["agrees"] is False:
                sources["log and block disagree"] = sources.get("log and block disagree", 0) + 1
        data.update(rated=len(rated), units=len(units), sources=sources,
                    provisional=sum(1 for r in both if r.get("new") and r.get("reflex")
                                    and not r.get("judged", True)))
    state = due(now, len(units) if source is not None else None)
    data["due"] = state

    # when both conditions should hold
    if source is not None and units:
        last = max(sessions[s]["start"] for s in rated)
        span = max((last - (mrc.epoch(LIVE) or 0.0)) / 86400, 1.0)
        data["projection"] = {"date": projected(now, len(units) / span),
                              "how": "%d units over the first %.1f days" % (len(units), span)}
    elif emissions["n"]:
        base_file = Path(args.baseline).expanduser() if args.baseline else (
            vault / ".mnemo" / "prevented-repeats" / mpr.UNITS_NAME)
        hy = historical_yield(mrc._read(base_file, None), _load_jsonl_events)
        if hy and hy["per_pair"]:
            per_day = emissions["n"] / max(state["days_in"], 1.0 / 24) * hy["per_pair"]
            data["projection"] = {"date": projected(now, per_day), "how": "estimated: %d emissions so far "
                                  "x #520's reflex yield before the change (%d units of %d emissions)"
                                  % (emissions["n"], hy["units"], hy["pairs"])}
        else:
            data["projection"] = {"date": None, "how": "no baseline yield to estimate from"}
    else:
        data["projection"] = {"date": None, "how": "no full-format emission in a human session yet"}

    # step 2, #527 on the fresh units
    if source is not None and units:
        mrc._write(out / FRESH_UNITS_NAME, {
            "since": LIVE, "rated": sorted(rated),
            "sessions": {s: source["sessions"][s] for s in sorted(rated)},
            "columns": {mpr.BOTH: rows}})
        send_arms = args.send and (state["due"] or args.force)
        if args.send and send_rate and not send_arms:
            print("refusing to answer and judge: %d fresh unit(s), the check needs %d (--force sends anyway)"
                  % (len(units), MIN_UNITS), file=sys.stderr)
        bv_out.mkdir(parents=True, exist_ok=True)
        _quiet(bv.main, ["--vault", str(vault), "--units", str(out / FRESH_UNITS_NAME), "--out", str(bv_out),
                         "--workers", str(args.workers), "--pause", str(args.pause)]
               + (["--send"] if send_arms else []) + (["--limit", str(args.limit)] if args.limit else [])
               + [x for r in raters for x in ("--rater", r)], bv_out / "report.txt")
        arms = mrc._read(bv_out / "arms.json", {})
        all_verdicts = mrc._read(bv_out / "verdicts.json", {})
        verdicts = {r: all_verdicts.get(mrc.column(r, bv.JUDGE_SYSTEM), {}) for r in raters}
        data["results"] = readings(units, rated, arms, verdicts, raters)

    mrc._write(out / "report.json", data)
    if args.json:
        print(json.dumps(data, indent=1))
        return 0
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
