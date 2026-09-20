"""Does a judge that reads the (task, rule) pair rank better than BM25F — graded by a judge that did not make the ranking? (#401)

Usage:
    PYTHONPATH=src python3 tools/measure_rerank_judges.py                  # compare, local
    PYTHONPATH=src python3 tools/measure_rerank_judges.py --judge          # dry run: calls, rough cost
    PYTHONPATH=src python3 tools/measure_rerank_judges.py --judge --send   # build the second judge's labels

``tools/measure_recall_judged.py`` gives every (query, rule) pair a relevance
probability from one judge (``jev-1.13.0``) and keeps it in
``<vault>/.mnemo/recall-qrels.json``. Ordering a bucket by those numbers and
then grading the order with the same numbers is a judge marking its own exam:
it scores 1.000 and says nothing. This tool builds a **second, independent set
of labels** and grades each judge's ordering with the other judge's labels.

The second judge is ``claude-haiku-4-5`` through :func:`mnemo.core.llm.call` —
the wrapper extraction already uses, so hooks are off, no tools are exposed,
and the text goes to the provider the user already runs mnemo against, not to
a new third party. **Only ``--judge --send`` makes those calls.** It needs
``recall-qrels.json`` to exist, judges the same rules with the same 800
characters of each, 20 rules per call, and writes
``<vault>/.mnemo/recall-qrels-second.json`` after every call, so an
interrupted run resumes instead of paying twice.

It is not cheap, which is itself a finding. Measured on 2026-09-19: 1,202
pairs took 82 calls, about 36 minutes and $2.49 — the CLI's own per-call
overhead dominates, not the rules. The first judge did the same pairs in one
minute for $0.014. ``--judge`` without ``--send`` prints the call count and a
cost estimate from that measured rate.

Reading the table: a row marked ``circular`` is a judge grading its own
order and is printed only so its 1.000 is not mistaken for a result. The rows
that count are the two crossed ones. The delta is against the shipped BM25F
order, with a paired bootstrap over queries; a gain is only a gain when its
95% interval excludes zero. ``read`` is the behavioural label ``mnemo
recall`` uses, reported beside the judged ones because it is the only label
that comes from real use — and it has so far not moved for any ordering.

Two limits to carry with any number from here. Both judges read the same
body text and BM25F does not, so part of a gain may be that shared view. And
a lenient second judge inflates every nDCG under it: read the delta, not the
level.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

_SIBLING = Path(__file__).resolve().with_name("measure_recall_judged.py")
_spec = importlib.util.spec_from_file_location("measure_recall_judged", _SIBLING)
mrj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrj)

SECOND_NAME = "recall-qrels-second.json"
SECOND_MODEL = "claude-haiku-4-5"

#: Bump when SYSTEM or the prompt shape changes: old labels answer an old question.
PROMPT_VERSION = 1

#: Rules per call. Kept at the size the 2026-09-19 run used, so a re-run is
#: comparable with it.
CHUNK = 20

#: USD per call, measured 2026-09-19 ($2.49 / 82 calls). Only for the dry run.
USD_PER_CALL = 0.0304

#: The first judge's "should read" bar, and the second's (label 2 -> gain 1.0).
FIRST_SHOULD_READ = mrj.SHOULD_READ
SECOND_SHOULD_READ = 1.0

SYSTEM = (
    "You grade search results for a developer tool. You are given a developer TASK and a "
    "numbered list of stored engineering RULES. For each rule decide whether a developer "
    "about to do the task should read it first. "
    "2 = should read: the rule addresses the same problem, component or pitfall the task "
    "involves and would change how the developer does it. "
    "1 = related: same area of the system or same kind of work, but not this task's problem. "
    "0 = not relevant: about something else, or too general to change anything in this task. "
    "Judge each rule on its own. Reply with ONLY a JSON object mapping every rule number to "
    '0, 1 or 2, e.g. {"1": 0, "2": 2}.'
)

#: ``caller(prompt, system) -> parsed JSON object``; raises on any failure.
Caller = Callable[[str, str], Dict[str, Any]]


def build_prompt(query: str, texts: Sequence[str]) -> str:
    return "TASK: " + query + "\n\nRULES:\n" + "\n".join(
        "%d. %s" % (i + 1, t) for i, t in enumerate(texts))


def parse_labels(answer: Dict[str, Any], chunk: Sequence[str]) -> Dict[str, int]:
    """Slug -> 0/1/2 for every rule the judge answered validly.

    Anything else — a missing number, a 3, a string that is not a digit — is
    left out. Absent means "not judged" and is asked again on the next run;
    it is never read as 0.
    """
    out: Dict[str, int] = {}
    for i, slug in enumerate(chunk):
        raw = answer.get(str(i + 1))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value in (0, 1, 2) and not isinstance(raw, bool):
            out[slug] = value
    return out


def pending_chunks(bodies: Dict[str, str], done: Dict[str, int]) -> List[List[str]]:
    """The rules still to judge, in stable order, ``CHUNK`` at a time."""
    todo = [s for s in sorted(bodies) if bodies[s] and s not in done]
    return [todo[i:i + CHUNK] for i in range(0, len(todo), CHUNK)]


def judge_chunk(query: str, bodies: Dict[str, str], chunk: Sequence[str],
                caller: Caller) -> Optional[Dict[str, int]]:
    """One call. ``None`` when it failed — one bad call must not lose the
    labels already paid for, so the caller counts it and moves on."""
    try:
        answer = caller(build_prompt(query, [bodies[s] for s in chunk]), SYSTEM)
    except Exception:
        return None
    return parse_labels(answer, chunk)


def judge_unit(query: str, bodies: Dict[str, str], done: Dict[str, int], caller: Caller,
               on_chunk: Optional[Callable[[], None]] = None) -> int:
    """Fill ``done`` in place; return how many calls failed.

    ``on_chunk`` runs after every call that added labels — the save point
    that makes an interrupted run resumable.
    """
    failed = 0
    for chunk in pending_chunks(bodies, done):
        got = judge_chunk(query, bodies, chunk, caller)
        if got is None:
            failed += 1
            continue
        done.update(got)
        if on_chunk is not None:
            on_chunk()
    return failed


def rerank(order: Sequence[str], signal: Dict[str, float]) -> List[str]:
    """Order by a judge's signal, best first. Ties keep ``order``; a rule the
    judge never scored sinks below every rule it did."""
    position = {s: i for i, s in enumerate(order)}
    return sorted(order, key=lambda s: (-signal[s] if s in signal else float("inf"), position[s]))


def bootstrap_ci(deltas: Sequence[float], *, draws: int = 4000, seed: int = 1) -> Tuple[float, float]:
    """95% interval of the mean of paired per-query deltas."""
    if not deltas:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(deltas)
    means = sorted(sum(deltas[rng.randrange(n)] for _ in range(n)) / n for _ in range(draws))
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def auc(positives: Sequence[float], negatives: Sequence[float]) -> Optional[float]:
    if not positives or not negatives:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in positives for n in negatives)
    return wins / (len(positives) * len(negatives))


def agreement(first: Sequence[Dict[str, float]], second: Sequence[Dict[str, int]]) -> Dict[str, Any]:
    """How the two judges relate on the pairs both scored."""
    pairs = [(f[s], l[s]) for f, l in zip(first, second) for s in f if s in l]
    return {
        "pairs": len(pairs),
        "second_counts": {str(k): sum(1 for _, x in pairs if x == k) for k in (0, 1, 2)},
        "first_should_read": sum(1 for x, _ in pairs if x >= FIRST_SHOULD_READ),
        "auc_should_read": auc([x for x, l in pairs if l == 2], [x for x, l in pairs if l < 2]),
        "auc_any": auc([x for x, l in pairs if l >= 1], [x for x, l in pairs if l == 0]),
    }


def compare(units: Sequence[Dict[str, Any]], second: Sequence[Dict[str, int]],
            shipped: Sequence[Sequence[str]], *, k: int = 5) -> List[Dict[str, Any]]:
    """Every ordering under every grader; deltas are against ``shipped``.

    A query where a grader finds nothing relevant has no ideal order and is
    left out of that grader's rows, counted in ``queries``.
    """
    first = [u["noul"] for u in units]
    orders = {
        "shipped": [list(o) for o in shipped],
        "rerank by first": [rerank(o, f) for o, f in zip(shipped, first)],
        "rerank by second": [rerank(o, {s: float(v) for s, v in l.items()})
                             for o, l in zip(shipped, second)],
    }
    graders = (
        ("second", [{s: v / 2.0 for s, v in l.items()} for l in second], SECOND_SHOULD_READ),
        ("first", first, FIRST_SHOULD_READ),
    )
    rows = []
    for grader, gains, bar in graders:
        keep = [i for i, g in enumerate(gains) if any(x > 0 for x in g.values())]
        base = {i: mrj.ndcg(orders["shipped"][i], gains[i], k) for i in keep}
        for name, ordering in orders.items():
            scores = [mrj.ndcg(ordering[i], gains[i], k) for i in keep]
            deltas = [s - base[i] for s, i in zip(scores, keep)]
            low, high = bootstrap_ci(deltas)
            rows.append({
                "grader": grader, "ordering": name, "queries": len(keep),
                "circular": name.endswith(grader),
                "ndcg": round(sum(scores) / len(scores), 4) if scores else 0.0,
                "delta": round(sum(deltas) / len(deltas), 4) if deltas else 0.0,
                "ci": [round(low, 4), round(high, 4)],
                "should_read_top": sum(1 for i in keep for s in ordering[i][:k]
                                       if gains[i].get(s, 0.0) >= bar),
                "should_read": sum(1 for i in keep for s in ordering[i]
                                   if gains[i].get(s, 0.0) >= bar),
                "read_top": sum(1 for i, u in enumerate(units)
                                for s in u["reads"] if s in ordering[i][:k]),
                "read": sum(1 for i, u in enumerate(units)
                            for s in u["reads"] if s in ordering[i]),
            })
    return rows


def format_rows(rows: Sequence[Dict[str, Any]]) -> str:
    lines = []
    for grader in ("second", "first"):
        lines.append(f"graded by the {grader} judge")
        for r in (r for r in rows if r["grader"] == grader):
            low, high = r["ci"]
            mark = " *" if (low > 0 or high < 0) and not r["circular"] else "  "
            lines.append(
                f"  {r['ordering']:17} nDCG@5 {r['ndcg']:.3f}  delta {r['delta']:+.3f} "
                f"[{low:+.3f}, {high:+.3f}]{mark} should-read {r['should_read_top']}/{r['should_read']}"
                f"  read {r['read_top']}/{r['read']}" + ("   circular" if r["circular"] else ""))
    lines.append("* = 95% interval of the paired delta excludes zero")
    return "\n".join(lines)


# --- the vault side ----------------------------------------------------------


def _cli_caller(prompt: str, system: str) -> Dict[str, Any]:
    from mnemo.core import llm

    response = llm.call(prompt, system=system, model=SECOND_MODEL, timeout=180)
    return llm._parse_llm_json(response.text)


def _load_first(vault: Path) -> Dict[str, Any]:
    path = vault / ".mnemo" / mrj.QRELS_NAME
    if not path.is_file():
        raise SystemExit(f"error: no first-judge labels at {path}; run measure_recall_judged.py --judge --send")
    return json.loads(path.read_text(encoding="utf-8"))


def _unit_key(unit: Dict[str, Any]) -> str:
    return json.dumps([unit["project"], unit["topic"], unit["query"]], ensure_ascii=False)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--judge", action="store_true", help="build the second judge's labels (dry run without --send)")
    parser.add_argument("--send", action="store_true", help="with --judge: call the claude CLI")
    parser.add_argument("--workers", type=int, default=3, help="concurrent claude calls (default 3)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli
    vault = cli._resolve_vault()
    first = _load_first(vault)
    units = first["units"]
    second_path = vault / ".mnemo" / SECOND_NAME
    saved: Dict[str, Any] = {}
    if second_path.is_file():
        saved = json.loads(second_path.read_text(encoding="utf-8"))
        if saved.get("prompt_version") != PROMPT_VERSION or saved.get("model") != SECOND_MODEL:
            saved = {}
    labels: Dict[str, Dict[str, int]] = saved.get("labels", {})

    if args.judge:
        # The same rules the first judge scored, with the same text.
        bodies = [{s: t for s, t in mrj._bodies(vault, u).items() if s in u["noul"]} for u in units]
        calls = sum(len(pending_chunks(b, labels.get(_unit_key(u), {}))) for u, b in zip(units, bodies))
        if not args.send:
            print(f"dry run: {calls} calls to {SECOND_MODEL} still to make "
                  f"(~${calls * USD_PER_CALL:.2f} at the 2026-09-19 rate, ~{calls * 26 // 60} min). "
                  f"--send makes them through the claude CLI")
            return 0

        def save() -> None:
            second_path.write_text(json.dumps({
                "model": SECOND_MODEL, "prompt_version": PROMPT_VERSION,
                "judged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "labels": labels}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")

        # A call is ~25-80 s of waiting on the CLI, so they overlap; the lock
        # keeps the labels and the file they are saved to in step.
        lock = threading.Lock()
        jobs = [(u, b, chunk) for u, b in zip(units, bodies)
                for chunk in pending_chunks(b, labels.setdefault(_unit_key(u), {}))]

        def work(job: Tuple[Dict[str, Any], Dict[str, str], List[str]]) -> bool:
            unit, unit_bodies, chunk = job
            got = judge_chunk(unit["query"], unit_bodies, chunk, _cli_caller)
            if got is None:
                return False
            with lock:
                labels[_unit_key(unit)].update(got)
                save()
            return True

        with ThreadPoolExecutor(max(1, args.workers)) as pool:
            failed = sum(1 for ok in pool.map(work, jobs) if not ok)
        save()
        judged = sum(len(v) for v in labels.values())
        print(f"second judge: {judged}/{sum(len(b) for b in bodies)} pairs, {failed} failed calls -> {second_path}")
        return 1 if failed else 0

    second = [labels.get(_unit_key(u), {}) for u in units]
    if not any(second):
        print(f"error: no second-judge labels at {second_path}; run with --judge first", file=sys.stderr)
        return 1
    shipped = mrj._orders(vault, units, None, shipped=True)
    agree = agreement([u["noul"] for u in units], second)
    rows = compare(units, second, shipped)
    if args.json:
        print(json.dumps({"first": first["model"], "second": SECOND_MODEL,
                          "agreement": agree, "rows": rows}, indent=2))
        return 0
    total = sum(len(u["noul"]) for u in units)
    print(f"first judge {first['model']}, second judge {SECOND_MODEL}; "
          f"{agree['pairs']}/{total} pairs scored by both, {len(units)} queries")
    print(f"second judge labels 0/1/2: {agree['second_counts']}; first judge >= {FIRST_SHOULD_READ}: "
          f"{agree['first_should_read']}")
    if agree["auc_should_read"] is not None and agree["auc_any"] is not None:
        print(f"agreement, AUC of the first judge's score: second=2 vs rest {agree['auc_should_read']:.3f}, "
              f"second>=1 vs 0 {agree['auc_any']:.3f}")
    print(format_rows(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
