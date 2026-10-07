"""Does the correction check keep the real corrections and drop the rest? (#524)

Usage:
    PYTHONPATH=src python3 tools/measure_correction_check.py                    # report, local
    PYTHONPATH=src python3 tools/measure_correction_check.py --score --part dev [--model M]
    PYTHONPATH=src python3 tools/measure_correction_check.py --score --part test [--model M]
    PYTHONPATH=src python3 tools/measure_correction_check.py --gate [--send]    # gate-verified pages

**The labelled set** is #519's (``tools/measure_repeated_corrections.py``),
read from its cache dir (``<vault>/.mnemo/repeated-corrections``, ``--dir``
for another): ``items.json``, every correction both capture paths verified
from 2026-08-31 to 09-27, each with its user turn and the agent message it
answered; and ``labels.json``, two blind raters' answer to "is this a
correction?" under :data:`measure_repeated_corrections.LABEL_SYSTEM`. The truth
is "both raters say yes".

**The split** is by the item id's parity: ``int(id, 16) % 2 == 0`` is dev.
:data:`mnemo.core.correction_check.SYSTEM_PROMPT` was written reading dev items
only. The test half is scored once: ``--score --part test`` records the column
that scored it in ``correction-check-test.json`` and refuses a second column
there unless ``--again`` — so a retune on test is on the record, not silent.

**``--score``** asks the check through the stage's own
:func:`correction_check.build_prompt` and :func:`correction_check.parse_verdicts`,
one call per session as the live pass batches it (a session's items in the
other half are not in the call, the one difference from live). Answers are
cached in ``correction-check-scores.json`` under ``<model>@<prompt hash>``, so
a rerun resumes and a new wording is a new column; each call's seconds, tokens
and notional USD go to ``correction-check-calls.jsonl`` for the cost line.

**The report** prints precision and recall of each column against "both
raters agree", with Wilson 95% intervals, per part and per capture path, and
beside it each rater alone scored against the same truth: the ceiling a check
exactly as good as one of the raters would reach.

**``--gate``** answers a question and changes nothing: how many of today's
gate-verified feedback pages (``shared/feedback``, ``confidence: verified``
that :func:`mnemo.core.extract.evidence.page_verifies` still accepts) rest on
an ``evidence.quote`` both raters call **not** a correction. Each quote is
located in its source session's transcript; a turn #519 already labelled
reuses those labels, and the rest are put to the same two raters with the same
prompt (``--send``), cached in ``gate-labels.json``. No page is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import statistics
import sys
import tempfile
import time
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


mrc = _sibling("measure_repeated_corrections")

DIR_NAME = "repeated-corrections"
SCORES_NAME = "correction-check-scores.json"
CALLS_NAME = "correction-check-calls.jsonl"
TEST_NAME = "correction-check-test.json"
GATE_ITEMS_NAME = "gate-items.json"
GATE_LABELS_NAME = "gate-labels.json"
DEV, TEST = "dev", "test"


def part_of(item_id: str) -> str:
    return DEV if int(item_id, 16) % 2 == 0 else TEST


def column(model: str, system: str) -> str:
    return "%s@%s" % (model, hashlib.sha256(system.encode("utf-8")).hexdigest()[:8])


def truth(items: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, bool]],
          raters: Sequence[str]) -> Dict[str, bool]:
    """``id -> both raters said correction``, for items every rater answered."""
    cols = [labels.get(mrc.column(r, mrc.LABEL_SYSTEM)) or {} for r in raters]
    out = {}
    for it in items:
        got = [c.get(it["id"]) for c in cols]
        if all(isinstance(g, bool) for g in got):
            out[it["id"]] = all(got)
    return out


def rater_answers(labels: Dict[str, Dict[str, bool]], rater: str) -> Dict[str, bool]:
    return dict(labels.get(mrc.column(rater, mrc.LABEL_SYSTEM)) or {})


def confusion(gold: Dict[str, bool], kept: Dict[str, bool], ids: Sequence[str]) -> Dict[str, int]:
    c = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for i in ids:
        if i not in gold or i not in kept:
            continue
        g, k = gold[i], kept[i]
        c["tp" if g and k else "fp" if k else "fn" if g else "tn"] += 1
    return c


def rates(c: Dict[str, int]) -> Dict[str, Any]:
    kept, real = c["tp"] + c["fp"], c["tp"] + c["fn"]
    return {
        "precision": c["tp"] / kept if kept else None,
        "precision_ci95": list(mrc.wilson(c["tp"], kept)),
        "recall": c["tp"] / real if real else None,
        "recall_ci95": list(mrc.wilson(c["tp"], real)),
    }


def sessions_of(items: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Items grouped by session, in turn order: one call each, as live."""
    by: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        by.setdefault(it["session_id"], []).append(it)
    return [sorted(b, key=lambda it: (it["turn_index"], it["id"])) for b in by.values()]


def score(
    items: Sequence[Dict[str, Any]],
    existing: Dict[str, bool],
    ask: Callable[[str], Any],
    check: Any,
    *,
    on_answer: Optional[Callable[[Dict[str, bool], Dict[str, Any]], None]] = None,
    pause: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, bool]:
    """``id -> kept?`` for every item, asking only sessions not fully answered.

    *ask* returns the provider's response; an item the answer leaves out is
    left unanswered here (live keeps it) so a rerun asks again.
    """
    out = dict(existing)
    todo = [b for b in sessions_of(items) if not all(it["id"] in out for it in b)]
    for n, batch in enumerate(todo):
        if n and pause > 0:
            sleep(pause)
        prompt = check.build_prompt([(it["answered"], it["turn"]) for it in batch])
        t0 = time.perf_counter()
        try:
            resp = ask(prompt)
        except Exception as exc:  # one failed call must not end the run
            print("  call %d: %s: %s" % (n + 1, type(exc).__name__, exc), file=sys.stderr)
            continue
        secs = time.perf_counter() - t0
        verdicts = check.parse_verdicts(getattr(resp, "text", "") or "", len(batch))
        got = {batch[k - 1]["id"]: v for k, v in verdicts.items()}
        out.update(got)
        if on_answer is not None:
            on_answer(got, {"items": len(batch), "answered": len(got), "secs": round(secs, 2),
                            "in": getattr(resp, "input_tokens", None),
                            "out": getattr(resp, "output_tokens", None),
                            "usd": getattr(resp, "total_cost_usd", None)})
        print("  call %d/%d" % (n + 1, len(todo)), file=sys.stderr)
    return out


def call_stats(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    secs = sorted(float(r.get("secs") or 0.0) for r in rows)
    if not secs:
        return {"calls": 0}
    return {
        "calls": len(rows),
        "secs_median": statistics.median(secs),
        "secs_p90": secs[min(len(secs) - 1, int(0.9 * len(secs)))],
        "usd_per_call": sum(float(r.get("usd") or 0.0) for r in rows) / len(rows),
        "in_median": statistics.median([int(r.get("in") or 0) for r in rows]),
    }


def _pct(x: Optional[float]) -> str:
    return "  -  " if x is None else "%5.1f%%" % (100 * x)


def _line(name: str, c: Dict[str, int]) -> str:
    r = rates(c)
    lo, hi = r["precision_ci95"]
    rlo, rhi = r["recall_ci95"]
    return ("  %-34s tp %2d fp %2d fn %2d tn %3d  precision %s [%s, %s]  recall %s [%s, %s]"
            % (name, c["tp"], c["fp"], c["fn"], c["tn"], _pct(r["precision"]), _pct(lo), _pct(hi),
               _pct(r["recall"]), _pct(rlo), _pct(rhi)))


def report_lines(items: Sequence[Dict[str, Any]], gold: Dict[str, bool],
                 signals: Dict[str, Dict[str, bool]],
                 stats: Dict[str, Dict[str, Any]]) -> List[str]:
    lines = []
    for part in (DEV, TEST):
        ids = [it["id"] for it in items if part_of(it["id"]) == part and it["id"] in gold]
        real = sum(gold[i] for i in ids)
        lines.append("%s half: %d items, %d real (both raters)" % (part, len(ids), real))
        for name, kept in signals.items():
            answered = [i for i in ids if i in kept]
            if not answered:
                continue
            tag = "" if len(answered) == len(ids) else "  (%d/%d answered)" % (len(answered), len(ids))
            lines.append(_line(name, confusion(gold, kept, ids)) + tag)
            if name.startswith("check "):
                for path in (mrc.PATH_BRIEFING, mrc.PATH_CORRECTIONS_ONLY):
                    sub = [it["id"] for it in items if it["id"] in ids and it["path"] == path]
                    lines.append(_line("    " + path, confusion(gold, kept, sub)))
        lines.append("")
    for col, s in stats.items():
        if s.get("calls"):
            lines.append("%s: %d calls, median %.1fs, p90 %.1fs, ~$%.4f notional per call, "
                         "median %d input tokens" % (col, s["calls"], s["secs_median"], s["secs_p90"],
                                                    s["usd_per_call"], s["in_median"]))
    return lines


# --- the gate-verified pages (report only) ----------------------------------------------

def gate_pages(vault: Path) -> List[Dict[str, Any]]:
    """Every consumer-visible ``shared/feedback`` page the evidence gate verifies today,
    by the predicate ``replay.rule_facts`` asks of it."""
    from mnemo.core import ci_corrections
    from mnemo.core.extract.evidence import page_verifies
    from mnemo.core.filters import derive_rule_slug, is_consumer_visible
    from mnemo.core.reclassify_types import split_frontmatter

    out = []
    for md in sorted((vault / "shared" / "feedback").glob("*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fm, _body = split_frontmatter(text)
        if not is_consumer_visible(md, fm, vault):
            continue
        if ci_corrections.origin_of(str(fm.get("confidence") or "")) != ci_corrections.ORIGIN_USER:
            continue
        sources = fm.get("sources") or []
        sources = [sources] if isinstance(sources, str) else [s for s in sources if isinstance(s, str)]
        evidence = fm.get("evidence")
        if not page_verifies(evidence, sources, vault):
            continue
        out.append({"slug": derive_rule_slug(fm, md.stem),
                    "quote": str(evidence.get("quote") or "").strip(),
                    "source": str(evidence.get("source") or "")})
    return out


def session_of_source(source: str) -> str:
    """``bots/<agent>/briefings/sessions/<sid>.md`` -> ``<sid>``."""
    name = source.rsplit("/", 1)[-1]
    return name[:-3] if name.endswith(".md") else name


def gate_items(pages: Sequence[Dict[str, Any]], projects_dir: Path,
               known: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per gate-verified page: its quote placed at its turn in its transcript.

    ``status`` is ``labelled`` when #519 already labelled that turn (``item``
    names the item), ``new`` when the raters have yet to see it, and
    ``transcript gone`` / ``not located`` when there is no turn to show.
    """
    from mnemo.core import corrections
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.core.friction import capture

    by_turn: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    for it in known:
        by_turn.setdefault((it["session_id"], it["turn_index"]), []).append(it)
    transcripts: Dict[str, Path] = {}
    for p in Path(projects_dir).glob("*/*.jsonl"):
        transcripts.setdefault(p.stem, p)
    out = []
    for page in pages:
        sid = session_of_source(page["source"])
        row = {"slug": page["slug"], "quote": page["quote"], "session_id": sid}
        path = transcripts.get(sid)
        if path is None:
            out.append(dict(row, status="transcript gone"))
            continue
        pairs = capture.exchanges(_load_jsonl_events(path))
        index = corrections.locate(page["quote"], [t for _, t in pairs])
        if index is None:
            out.append(dict(row, status="not located"))
            continue
        row.update(turn_index=index, answered=mrc._tail(pairs[index][0], mrc.CONTEXT_CHARS),
                   turn=turn_view(pairs[index][1], page["quote"]))
        # The raters judged the turn as they were shown it, so a label is
        # reused only when this page's own quote was in that text.
        seen = [it for it in by_turn.get((sid, index), [])
                if corrections.quote_matches_turn(page["quote"], it["turn"])]
        if seen:
            out.append(dict(row, status="labelled", id=seen[0]["id"]))
        else:
            out.append(dict(row, status="new", id=mrc.item_id(sid, index, page["quote"])))
    return out


def turn_view(turn: str, quote: str, lead: int = 300) -> str:
    """The turn as #519 shows it (its head), or, when the quote lies past that
    head, a window of the same size opening shortly before the quote."""
    from mnemo.core import corrections

    head = mrc._head(turn, mrc.TURN_CHARS)
    if corrections.quote_matches_turn(quote, head):
        return head
    at = turn.find(quote.strip())
    if at < 0:
        return head
    start = max(0, at - lead)
    return "…" + mrc._head(turn[start:], mrc.TURN_CHARS)


def gate_counts(rows: Sequence[Dict[str, Any]], answers: Dict[str, Dict[str, bool]]) -> Dict[str, int]:
    """``answers``: rater -> id -> correction?, #519's labels and the gate's merged."""
    c = {"pages": len(rows), "transcript gone": 0, "not located": 0, "unlabelled": 0,
         "both correction": 0, "both not": 0, "split": 0}
    for r in rows:
        if r["status"] in ("transcript gone", "not located"):
            c[r["status"]] += 1
            continue
        got = [a.get(r["id"]) for a in answers.values()]
        if not got or any(g is None for g in got):
            c["unlabelled"] += 1
        elif all(got):
            c["both correction"] += 1
        elif not any(got):
            c["both not"] += 1
        else:
            c["split"] += 1
    return c


# --- main -------------------------------------------------------------------------------

def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def _read_rows(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _in_scratch(fn: Callable[[], Any]) -> Any:
    """Run model calls from a scratch cwd, as #519 does: a ``claude --print``
    helper fires the hooks of whatever directory it starts in."""
    here = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="mnemo-check-") as scratch:
        os.chdir(scratch)
        try:
            return fn()
        finally:
            os.chdir(here)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, correction_check, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dir", default="", help="#519's cache dir (default <vault>/.mnemo/%s)" % DIR_NAME)
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--score", action="store_true", help="ask the check (model calls)")
    ap.add_argument("--part", choices=(DEV, TEST), default=DEV)
    ap.add_argument("--again", action="store_true",
                    help="score test with a column other than the one that already did")
    ap.add_argument("--model", default=None, help="default: extraction.correctionCheck.model")
    ap.add_argument("--pause", type=float, default=mrc.PAUSE_SECONDS)
    ap.add_argument("--gate", action="store_true", help="report the gate-verified pages")
    ap.add_argument("--send", action="store_true", help="with --gate: label unseen quotes")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.dir).expanduser() if args.dir else vault / ".mnemo" / DIR_NAME
    items = _read(out / "items.json", [])
    labels = _read(out / "labels.json", {})
    raters = list(mrc.RATERS)
    gold = truth(items, labels, raters)
    timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)

    if args.gate:
        return _gate(args, cfg, vault, out, items, labels, raters, timeout, argv=argv)

    scores = _read(out / SCORES_NAME, {})
    if args.score:
        model = args.model or correction_check.settings(cfg)[1]
        col = column(model, correction_check.SYSTEM_PROMPT)
        if args.part == TEST:
            scored = _read(out / TEST_NAME, [])
            others = [c for c in scored if c != col]
            if others and not args.again:
                print("refusing: the test half was already scored by %s; tune on dev, "
                      "or pass --again to put a second test score on the record" % ", ".join(others),
                      file=sys.stderr)
                return 2
            if col not in scored:
                _write(out / TEST_NAME, scored + [col])
        provider = llm.resolve(cfg)
        todo = [it for it in items if part_of(it["id"]) == args.part and it["id"] in gold]

        def ask(prompt: str) -> Any:
            return provider(prompt, system=correction_check.SYSTEM_PROMPT, model=model, timeout=timeout)

        def on_answer(got: Dict[str, bool], row: Dict[str, Any]) -> None:
            scores.setdefault(col, {}).update(got)
            _write(out / SCORES_NAME, scores)
            with open(out / CALLS_NAME, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(dict(row, column=col, part=args.part)) + "\n")

        _in_scratch(lambda: score(todo, scores.get(col, {}), ask, correction_check,
                                  on_answer=on_answer, pause=args.pause))

    signals: Dict[str, Dict[str, bool]] = {"keep everything (today)": {i: True for i in gold}}
    for r in raters:
        signals["rater %s alone" % r] = rater_answers(labels, r)
    current = column("", correction_check.SYSTEM_PROMPT).split("@")[1]
    for col in sorted(scores):
        tag = "" if col.endswith("@" + current) else " (old prompt)"
        signals["check " + col + tag] = scores[col]
    calls = _read_rows(out / CALLS_NAME)
    stats = {col: call_stats([r for r in calls if r.get("column") == col]) for col in sorted(scores)}
    prov = _provenance.provenance(__file__, argv, vault=vault)
    if args.json:
        data = {}
        for part in (DEV, TEST):
            ids = [it["id"] for it in items if part_of(it["id"]) == part and it["id"] in gold]
            data[part] = {n: dict(confusion(gold, k, ids), **rates(confusion(gold, k, ids)))
                          for n, k in signals.items()}
        data["calls"] = stats
        data["test_scored_by"] = _read(out / TEST_NAME, [])
        print(json.dumps(_provenance.stamp(data, prov), indent=1))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(items, gold, signals, stats):
        print(line)
    scored = _read(out / TEST_NAME, [])
    if scored:
        print("test half scored by: " + ", ".join(scored))
    return 0


def _gate(args: Any, cfg: dict, vault: Path, out: Path, items: List[Dict[str, Any]],
          labels: Dict[str, Dict[str, bool]], raters: Sequence[str], timeout: int,
          argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import llm

    rows = gate_items(gate_pages(vault), Path(args.projects), items)
    _write(out / GATE_ITEMS_NAME, rows)
    gate_labels = _read(out / GATE_LABELS_NAME, {})
    cols = {r: mrc.column(r, mrc.LABEL_SYSTEM) for r in raters}
    new = [r for r in rows if r["status"] == "new"]
    if args.send and new:
        provider = llm.resolve(cfg)
        for r in raters:
            col = gate_labels.setdefault(cols[r], {})

            def take(batch: List[Dict[str, Any]], col: Dict[str, bool] = col) -> Callable[[str], None]:
                def on_reply(text: str) -> None:
                    col.update(mrc.parse_labels(text, batch))
                    _write(out / GATE_LABELS_NAME, gate_labels)
                return on_reply

            calls = [(mrc.label_prompt(b), mrc.LABEL_SYSTEM, take(b))
                     for b in mrc.label_batches(new, col)]
            mrc.run_calls(provider, r, timeout, calls, None, out / "calls.jsonl", args.pause)
    answers = {r: dict(labels.get(cols[r]) or {}, **(gate_labels.get(cols[r]) or {})) for r in raters}
    counts = gate_counts(rows, answers)
    prov = _provenance.provenance(__file__, argv, vault=vault, blind_spots=[
        _provenance.transcripts_blind_spot(args.projects),
        "%d gate-verified page(s) whose transcript is gone" % counts["transcript gone"],
        "%d page(s) whose quote was not located in its transcript" % counts["not located"]])
    if args.json:
        print(json.dumps(_provenance.stamp({"counts": counts, "rows": [
            {k: v for k, v in r.items() if k not in ("answered", "turn")} for r in rows]}, prov), indent=1))
        return 0
    print(_provenance.line(prov))
    print("gate-verified feedback pages today: %d" % counts["pages"])
    for key in ("both not", "split", "both correction", "unlabelled", "transcript gone", "not located"):
        print("  %-16s %d" % (key, counts[key]))
    located = counts["pages"] - counts["transcript gone"] - counts["not located"]
    lo, hi = mrc.wilson(counts["both not"], located)
    if located:
        print("rest on a quote both raters call not a correction: %d/%d = %s [%s, %s]"
              % (counts["both not"], located, _pct(counts["both not"] / located), _pct(lo), _pct(hi)))
    print("labelled by #519 already: %d; new to the raters: %d"
          % (sum(r["status"] == "labelled" for r in rows), len(new)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
