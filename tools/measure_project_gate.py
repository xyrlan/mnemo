"""Would the reference gate make backfill project pages good enough to route live? (#485)

Usage:
    PYTHONPATH=src python3 tools/measure_project_gate.py --dry-run          # pages, calls, cost; calls nothing
    PYTHONPATH=src python3 tools/measure_project_gate.py --send             # the gate judges what is pending
    PYTHONPATH=src python3 tools/measure_project_gate.py                    # report, local
    PYTHONPATH=src python3 tools/measure_project_gate.py --json

**Measure only**: no routing changes, no page is written, no new rating is
made. #471 let backfill pages take the normal gates; it passed on clubinho and
failed on clearframe (#477), and on both the weak route was ``project``, which
goes live with no gate. This asks what the reference gate would have done
with those project pages.

The pages and the truth. Each corpus is a directory
``tools/measure_backfill_routes.py`` left: ``sample.json`` (the frozen rows,
each with the exact ``text`` the raters saw — the gate's own
``reference_gate.view``) and ``labels.json`` (both blind raters' letters,
S/T good, G/N/W junk). Only the ``project`` rows are taken; both files are
read, never written.

- ``clubinho``: #471's run, ``~/.cache/mnemo/day-one/backfill-routes``;
- ``clearframe``: #477's ``--live`` run, ``~/.cache/mnemo/day-one-clearframe/backfill-live``.

The gate. The real one: ``reference_gate.judge_pages`` on
``ExtractedPage`` objects typed ``reference`` (the function only asks about
that type), answered by ``core.llm``'s configured provider with the shipped
model (``extraction.referenceGate.model``) and the shipped
``reference_gate.SYSTEM_PROMPT``. :data:`BATCH` pages a call, as an
extraction chunk would batch them. A page with no answer counts as held —
that is what ``reference_gate.cleared`` does with it — but a rerun asks it
again, since no answer is a failed call, not a verdict. Answers are filed per
corpus under ``<model>@<prompt hash>`` in ``--out``: a changed prompt is a
new column, and the run resumes.

The numbers, per corpus: the both-good rate among the pages the
gate lets through (S/T) with its 95% Wilson interval, against **the 85% bar
#471 declared** (:data:`BAR`, the point estimate decides); what it holds
(G/N/no answer) and how many of those both raters called good — the good
pages the gate would lose; and the ceiling: pages a rater called **W** (the
text shows it wrong) are junk a G/N gate is not asked to find, so even a
gate that held every other junk page and no good one would pass
``good / (good + W)``.

Budget: at most :data:`MAX_CALLS` model calls over every run, counted in
the answers file.

First run, 2026-09-24, ``claude-sonnet-5@5e907270``: 8 calls (7 planned, one
reply came back unparseable and its 10 pages were asked again), API-price
equivalent $0.17.

- clubinho: no gate 26/32 = 81.2%. The gate said S 25, T 5, G 2; through it
  **26/30 = 86.7% [70.3, 94.7] — PASS**, by one page (25/30 fails). It held
  2, both G/G by the raters: no good page lost. All 3 W pages went through;
  ceiling 26/29 = 89.7%.
- clearframe: no gate 22/30 = 73.3%. The gate said S 18, T 6, N 5, G 1;
  through it **20/24 = 83.3% [64.1, 93.3] — FAIL**, by one page (21/24
  would pass). It held 6: 4 N/N narratives ("specs updated", "smoke test
  outstanding") and 2 pages both raters called S — the good it loses. Junk
  through: the 2 W pages and 2 the raters split on (S/G, S/N); ceiling
  22/24 = 91.7%.

The gate does what the issue expected of it on narratives — it held all 4
pages both raters called N on clearframe — and the corpus still misses the
bar. No project
wording was tried (#485 step 3): the only junk clubinho still lets through
that a G/N wording could catch is one page, so there was nothing to tune on,
and the 7 calls left were exactly one clubinho + clearframe pass with no
room to re-ask a failed reply. At ~30 pages a corpus, a gate that caught
every catchable junk page and lost no good one would score ~90% with a
Wilson floor near 74%: this n cannot put any gate clearly over the bar.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dk = _sibling("measure_demoted_keeps")
mb = _sibling("measure_backfill_routes")

CORPORA = {
    "clubinho": mb.DEFAULT_OUT,
    "clearframe": Path.home() / ".cache" / "mnemo" / "day-one-clearframe" / "backfill-live",
}
DEFAULT_OUT = Path.home() / ".cache" / "mnemo" / "project-gate"
ANSWERS_NAME = "answers.json"

#: #471's bar, not moved.
BAR = mb.BAR
#: The issue's budget, over every run.
MAX_CALLS = 15
#: Pages per gate call — an extraction chunk is ``chunkSize`` 10 memory files.
BATCH = 10
PROJECT_TYPE = "project"
NO_ANSWER = ""


def _gate() -> Any:
    from mnemo.core.extract import reference_gate
    return reference_gate


def column(model: str, system: str) -> str:
    """Where one model + wording's answers are filed; a new wording is a new column."""
    return "%s@%s" % (model, hashlib.sha1(system.encode("utf-8")).hexdigest()[:8])


# --- the pages ------------------------------------------------------------------------

def project_rows(corpus_dir: Path) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, str]]]:
    """The project rows of a frozen sample and the raters' labels for them. Read-only."""
    sample = dk._read(corpus_dir / mb.SAMPLE_NAME, None)
    store = dk._read(corpus_dir / mb.LABELS_NAME, None)
    if sample is None or store is None:
        raise SystemExit("error: %s lacks %s or %s" % (corpus_dir, mb.SAMPLE_NAME, mb.LABELS_NAME))
    rows = sorted((r for r in sample["sample"] if r["type"] == PROJECT_TYPE),
                  key=lambda r: r["id"])
    return rows, store.get("labels", {})


def split_view(text: str) -> Tuple[str, str]:
    """(name, body) such that ``reference_gate.view(name, body) == text``.

    The frozen text is already the gate's view — ``"<name>. <body>"``,
    whitespace collapsed, cut at ``VIEW_CHARS`` — so splitting it at its first
    ``". "`` gives the gate back exactly what the raters read.
    """
    name, sep, body = text.partition(". ")
    if sep and _gate().view(name, body) == text:
        return name, body
    return text, ""


def as_page(row: Dict[str, Any]) -> Any:
    """An inferred ``reference`` page holding the row's text, so ``judge_pages`` asks about it."""
    from mnemo.core.extract.inbox.types import ExtractedPage

    name, body = split_view(row["text"])
    return ExtractedPage(slug=row["id"].split("/", 1)[-1], type="reference", name=name,
                         description="", body=body, source_files=[], source_hash="",
                         confidence="inferred")


def pending(rows: Sequence[Dict[str, Any]], done: Dict[str, str]) -> List[List[Dict[str, Any]]]:
    """Batches still to ask: rows never asked, and rows a call left unanswered.

    No answer is what a failed call leaves (an unparseable reply); the gate
    holds such a page, but that is the transport's verdict, not the judge's,
    so a rerun asks it again. The report still counts it as held until then.
    """
    todo = [r for r in rows if done.get(r["id"]) in (None, NO_ANSWER)]
    return [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]


def judge(batch: Sequence[Dict[str, Any]], ask: Callable[[str], str]) -> Dict[str, str]:
    """``{id: G|N|S|T|""}`` from the real ``judge_pages``; "" = no answer (held)."""
    judged = _gate().judge_pages([as_page(r) for r in batch], ask)
    return {r["id"]: (p.judged or NO_ANSWER) for r, p in zip(batch, judged)}


# --- the pure part ----------------------------------------------------------------------

def report(rows: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, str]],
           answers: Dict[str, str], cols: Sequence[str], *, bar: float = BAR) -> Dict[str, Any]:
    """Every number #485 asks for, from saved labels and gate answers only."""
    a, b = (labels.get(c, {}) for c in cols[:2])
    ids = [r["id"] for r in rows if r["id"] in a and r["id"] in b]
    good = {i for i in ids if dk.LETTERS[a[i]] == dk.GOOD and dk.LETTERS[b[i]] == dk.GOOD}
    wrong = {i for i in ids if "W" in (a[i], b[i])}
    keep = _gate().KEEP
    answered = [i for i in ids if i in answers]
    passed = [i for i in answered if answers[i] in keep]
    held = [i for i in answered if answers[i] not in keep]
    gate_cats: Dict[str, int] = {}
    for i in answered:
        key = answers[i] or "no answer"
        gate_cats[key] = gate_cats.get(key, 0) + 1
    through = dk._rate(len(good & set(passed)), len(passed))
    complete = bool(ids) and len(answered) == len(ids)
    if not complete:
        verdict = "incomplete: the gate answered %d of %d pages" % (len(answered), len(ids))
    elif through["rate"] is None:
        verdict = "FAIL: the gate lets no page through"
    elif through["rate"] >= bar:
        verdict = "PASS: %.1f%% >= %.0f%%" % (100 * through["rate"], 100 * bar)
    else:
        verdict = "FAIL: %.1f%% < %.0f%%" % (100 * through["rate"], 100 * bar)
    return {
        "pages": len(ids), "bar": bar, "complete": complete, "verdict": verdict,
        "no_gate": dk._rate(len(good), len(ids)),
        "gate": gate_cats,
        "passed": through,
        "held": {"n": len(held), "good": len(good & set(held)),
                 "ids": [{"id": i, "gate": answers[i] or "-", "a": a[i], "b": b[i]} for i in held]},
        "wrong": {"n": len(wrong), "passed": len(wrong & set(passed)),
                  "ceiling": dk._rate(len(good), len(good) + len(wrong))},
        "junk_passed": [{"id": i, "gate": answers[i], "a": a[i], "b": b[i]}
                        for i in passed if i not in good],
    }


def report_lines(name: str, col: str, data: Dict[str, Any]) -> List[str]:
    lines = [
        "%s — %d project pages, gate %s" % (name, data["pages"], col),
        "  no gate (today):         %s both-good" % dk._rate_line(data["no_gate"]),
        "  gate answers:            %s" % "  ".join(
            "%s %d" % kv for kv in sorted(data["gate"].items())),
        "  through the gate (S/T):  %s both-good" % dk._rate_line(data["passed"]),
        "  bar (#471): >= %.0f%%    verdict: %s" % (100 * data["bar"], data["verdict"]),
        "  held (G/N/no answer):    %d, of which both raters called good: %d"
        % (data["held"]["n"], data["held"]["good"]),
        "  W (either rater):        %d, %d of them let through; ceiling good/(good+W) %s"
        % (data["wrong"]["n"], data["wrong"]["passed"], dk._rate_line(data["wrong"]["ceiling"])),
    ]
    for row in data["held"]["ids"]:
        lines.append("    held %s  raters %s/%s  %s" % (row["gate"], row["a"], row["b"], row["id"]))
    for row in data["junk_passed"]:
        lines.append("    junk through %s  raters %s/%s  %s"
                     % (row["gate"], row["a"], row["b"], row["id"]))
    return lines


def estimate(batches: Sequence[Sequence[Dict[str, Any]]], system: str) -> Dict[str, Any]:
    """Tokens and API-price equivalent for the pending calls, Sonnet list price."""
    t_in = sum(len(_gate().build_prompt([r["text"] for r in b])) // 4 + len(system) // 4
               + dk.CLI_OVERHEAD_TOKENS for b in batches)
    t_out = sum(len(b) * dk.OUT_TOKENS_PER_PAGE for b in batches)
    return {"calls": len(batches), "input_tokens": t_in, "output_tokens": t_out,
            "usd": (t_in * 3.0 + t_out * 15.0) / 1e6}


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--corpus", action="append", choices=sorted(CORPORA),
                    help="corpus to measure (repeatable; default: both)")
    ap.add_argument("--dir", action="append", default=[], metavar="NAME=DIR",
                    help="read a corpus from another directory")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="where the gate answers live")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--send", action="store_true", help="ask the gate what is pending (model calls)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    model = str((cfg["extraction"].get("referenceGate") or {}).get("model")
                or cfg["extraction"]["model"])
    system = _gate().SYSTEM_PROMPT
    col = column(model, system)
    dirs = dict(CORPORA)
    for spec in args.dir:
        name, _, d = spec.partition("=")
        dirs[name] = Path(d).expanduser()
    names = args.corpus or sorted(CORPORA)
    answers_path = args.out / ANSWERS_NAME
    store: Dict[str, Any] = dk._read(answers_path, {"calls": 0, "usd": 0.0, "answers": {}})
    corpora = {n: project_rows(dirs[n]) for n in names}

    todo = [(n, b) for n in names for b in pending(
        corpora[n][0], store["answers"].get(n, {}).get(col, {}))]

    if args.dry_run:
        e = estimate([b for _, b in todo], system)
        print("gate: %s" % col)
        for n in names:
            print("  %-10s %d project pages, %d pending" % (
                n, len(corpora[n][0]), sum(len(b) for m, b in todo if m == n)))
        print("pending: %d call(s) of <= %d pages; %d of %d budgeted calls used"
              % (e["calls"], BATCH, store.get("calls", 0), MAX_CALLS))
        print("tokens: ~%d in, ~%d out; API-price equivalent ~$%.2f"
              % (e["input_tokens"], e["output_tokens"], e["usd"]))
        if store.get("calls", 0) + e["calls"] > MAX_CALLS:
            print("WARNING: pending calls exceed the %d-call budget; --send stops at it" % MAX_CALLS)
        return 0

    if args.send and todo:
        provider = llm.resolve(cfg)
        timeout = max(300, int(cfg["extraction"]["subprocessTimeout"]))
        mrl = _sibling("measure_rule_lift")

        def ask(prompt: str) -> str:
            resp = provider(prompt, system=system, model=model, timeout=timeout)
            store["usd"] = store.get("usd", 0.0) + float(resp.total_cost_usd or 0.0)
            store["last_reply"] = resp.text[-2000:]
            return resp.text

        args.out.mkdir(parents=True, exist_ok=True)
        # A scratch cwd: the gate's session must not land in a project's history.
        with tempfile.TemporaryDirectory(prefix="mnemo-project-gate-") as scratch:
            with mrl._chdir(scratch):
                for n, batch in todo:
                    if store.get("calls", 0) >= MAX_CALLS:
                        print("budget spent: %d calls; stopping" % MAX_CALLS, file=sys.stderr)
                        break
                    got = judge(batch, ask)
                    store["calls"] = store.get("calls", 0) + 1
                    store["answers"].setdefault(n, {}).setdefault(col, {}).update(got)
                    dk._write(answers_path, store)
                    print("call %d/%d %s: %d of %d answered" % (
                        store["calls"], MAX_CALLS, n, sum(1 for v in got.values() if v),
                        len(batch)), file=sys.stderr)
        print("%d call(s) used of %d, API-price equivalent $%.2f"
              % (store.get("calls", 0), MAX_CALLS, store.get("usd", 0.0)))

    cols = [dk.column(m) for m in dk.RATERS]
    out = {n: report(corpora[n][0], corpora[n][1],
                     store["answers"].get(n, {}).get(col, {}), cols) for n in names}
    if args.json:
        print(json.dumps({"gate": col, "corpora": out}, indent=1))
        return 0
    print("answers %s\n" % answers_path)
    for n in names:
        for line in report_lines(n, col, out[n]):
            print(line)
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
