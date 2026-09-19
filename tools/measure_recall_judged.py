"""Score the ranking against judged relevance, not against what happened to be read.

Usage:
    PYTHONPATH=src python3 tools/measure_recall_judged.py                     # evaluate, local
    PYTHONPATH=src python3 tools/measure_recall_judged.py --gates 0,1,3       # sweep the rerank gate, local
    PYTHONPATH=src python3 tools/measure_recall_judged.py --judge             # dry run: pairs, tokens, cost
    PYTHONPATH=src python3 tools/measure_recall_judged.py --judge --send      # build the judgments

**Only ``--judge --send`` leaves the machine.** It posts each logged query
and the bodies of the rules in its topic bucket to TypeSafe's
``/v1/systemone``, a third party, and needs ``TYPESAFE_API_KEY``. The answers
are written to ``<vault>/.mnemo/recall-qrels.json``; every later evaluation
reads that file and calls nothing. Nothing in ``src/`` uses this tool.

``mnemo recall`` takes its ground truth from behaviour: a rule is "expected"
when the agent read it within 120 s of a list call that returned it. Measured
on 2026-09-19 that label has three properties a ranking cannot answer for:

- the agent picks from **slugs alone** (the list carries no bodies), with the
  whole task in its head, not just the query the harness replays;
- 56% of reads come from the top 3 of the list that was shown, so the label
  is partly an echo of the ranking it is used to grade;
- a list followed by three reads becomes three cases with one expected rule
  each, and only one of them can be first. Crediting the co-read rules moves
  primacy@5 from 38 to 39 of 102: real, and not the main defect.

The read label says which rules were opened. It says nothing about the rules
that should have been and were never seen, which is the failure a ranking
exists to prevent. So this tool asks a judge one atomic yes/no question per
(query, rule) pair — would a developer about to do this task need to read
this rule first — and keeps the probability as a graded label.

The judge was checked before it was trusted. On 38 logged queries (1,202
pairs) its answers separate read from not-read rules inside a list with mean
AUC 0.822. Against 60 pairs labelled blind by a second reader, stratified by
the judge's score, AUC was 0.913 for "should read" and 0.943 for "any
relevance", with no pair the judge scored >= 0.8 labelled irrelevant and none
it scored < 0.2 labelled "should read". One rater, one day: enough to use the
labels for comparison between rankings, not to quote them as truth.

What the judged labels showed that the read labels could not: the query
rerank lifts nDCG@5 from 0.573 to 0.767; the gate is flat between 0 and 3;
and of 22 should-read rules outside the top 5, 20 have a BM25F score >= 1.0
— they are scored and outranked, not lost to a vocabulary gap — and 21 were
never read.

On a python.org macOS build ``urllib`` has no CA bundle;
``SSL_CERT_FILE=/etc/ssl/cert.pem`` fixes it. Do not disable verification.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

URL = "https://api.typesafe.ai/v1/systemone"

#: Pinned, never an alias: judgments made by one model are only comparable
#: with judgments made by the same one.
MODEL = "jev-1.13.0"

#: USD per million input tokens; output is free (docs.typesafe.ai/models,
#: read 2026-09-19).
USD_PER_MTOK = 0.042

#: Bump when the question below changes: old judgments answer an old question.
QUESTION_VERSION = 1

#: A rule the judge puts here or above is one the developer should have read.
SHOULD_READ = 0.8
#: Any relevance at all; the precision@k threshold.
RELEVANT = 0.5

#: Rules per request. A bucket of 65 bodies fits the 64k-token request limit;
#: chunking keeps a larger one inside it. Questions are judged in isolation,
#: so chunk boundaries do not change an answer.
CHUNK = 40

BODY_CHARS = 800
GRAPH_SECTION = "<!-- mnemo:graph-section -->"
QRELS_NAME = "recall-qrels.json"

Client = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


def question(body: str) -> Dict[str, Any]:
    return {
        "type": "noul",
        "instructions": (
            "A developer about to do the task in the state should read this stored "
            "engineering rule first, because it addresses the same problem, component "
            "or pitfall the task involves. Rule: " + body
        ),
        "criteria": {
            "true": "The rule is about what this task touches and would change how the developer does it",
            "false": "The rule is about something else, or is too general to change anything in this task",
        },
    }


def rule_text(body: str) -> str:
    """What the judge reads: the rule without its link section, bounded."""
    return " ".join(body.split(GRAPH_SECTION)[0].split())[:BODY_CHARS]


def units_from_log(entries: Iterable[Dict[str, Any]], *, window_s: float,
                   name_map: Optional[Dict[str, str]] = None,
                   parse_ts: Callable[[str], float]) -> List[Dict[str, Any]]:
    """One unit per distinct (project, topic, query), with every rule read after it.

    The pairing is ``bootstrap_cases``'s: a read belongs to the latest list
    call in its project, inside the window, that returned the slug. Only
    queried list calls make units — without a query there is no task to judge
    a rule against.
    """
    entries = list(entries)
    lists = []
    for e in entries:
        if e.get("tool") != "list_rules_by_topic":
            continue
        args = e.get("args") or {}
        if not e.get("project") or not args.get("topic") or not args.get("query"):
            continue
        lists.append({"ts": parse_ts(e.get("timestamp", "")), "project": e["project"],
                      "topic": args["topic"], "query": args["query"],
                      "slugs": e.get("hit_slugs") or []})
    units: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for call in lists:
        units.setdefault((call["project"], call["topic"], call["query"]), {
            "project": call["project"], "topic": call["topic"],
            "query": call["query"], "reads": []})
    for e in entries:
        if e.get("tool") != "read_mnemo_rule":
            continue
        slug = (e.get("args") or {}).get("slug")
        ts = parse_ts(e.get("timestamp", ""))
        if not slug or ts == 0.0:
            continue
        best = None
        for call in lists:
            if (call["project"] == e.get("project") and call["ts"] <= ts
                    and ts - call["ts"] <= window_s and slug in call["slugs"]
                    and (best is None or call["ts"] > best["ts"])):
                best = call
        if best is None:
            continue
        reads = units[(best["project"], best["topic"], best["query"])]["reads"]
        resolved = (name_map or {}).get(slug, slug)
        if resolved not in reads:
            reads.append(resolved)
    return sorted(units.values(), key=lambda u: (u["project"], u["topic"], u["query"]))


def judge_unit(query: str, bodies: Dict[str, str], client: Client) -> Tuple[Dict[str, float], int]:
    """The judge's probability for every rule with a body, and the tokens spent.

    A rule the client did not answer for is left out — absent means "not
    judged", and :func:`evaluate` counts it apart and never reads it as
    "irrelevant".
    """
    slugs = [s for s in sorted(bodies) if bodies[s]]
    scores: Dict[str, float] = {}
    tokens = 0
    for start in range(0, len(slugs), CHUNK):
        chunk = slugs[start:start + CHUNK]
        out = client({"developer_task": query},
                     {"r%d" % i: question(bodies[s]) for i, s in enumerate(chunk)})
        answers = out.get("answers") or {}
        tokens += (out.get("usage") or {}).get("input_tokens", 0)
        for i, slug in enumerate(chunk):
            value = (answers.get("r%d" % i) or {}).get("noul")
            if value is not None:
                scores[slug] = float(value)
    return scores, tokens


def ndcg(order: Sequence[str], gain: Dict[str, float], k: int = 5) -> float:
    dcg = sum(gain.get(s, 0.0) / math.log2(i + 2) for i, s in enumerate(order[:k]))
    ideal = sum(g / math.log2(i + 2)
                for i, g in enumerate(sorted(gain.values(), reverse=True)[:k]))
    return dcg / ideal if ideal else 0.0


def gated_order(base: Sequence[str], scores: Dict[str, float], gate: Optional[float]) -> List[str]:
    """``tools._query_rerank`` on slugs: hits at or over the gate rise by
    score, the rest keep ``base`` order. ``None`` is no rerank at all."""
    if gate is None:
        return list(base)
    hits = sorted((s for s in base if scores.get(s, 0.0) >= gate),
                  key=lambda s: -scores.get(s, 0.0))
    return hits + [s for s in base if scores.get(s, 0.0) < gate]


def evaluate(units: Sequence[Dict[str, Any]], orders: Sequence[Sequence[str]],
             *, k: int = 5) -> Dict[str, Any]:
    """Grade one ranking (``orders[i]`` answers ``units[i]``) against the judgments.

    Both ground truths side by side, because the point is where they differ:
    ``read_*`` is the behavioural label ``mnemo recall`` uses, the rest is
    the judge's. A rule in a ranking with no judgment (added to the vault
    since) is counted in ``unjudged`` and earns nothing.
    """
    ndcgs, precisions = [], []
    should_total = should_top = reads_total = reads_top = unjudged = 0
    for unit, order in zip(units, orders):
        gain = unit["noul"]
        unjudged += sum(1 for s in order if s not in gain)
        ndcgs.append(ndcg(order, gain, k))
        precisions.append(sum(1 for s in order[:k] if gain.get(s, 0.0) >= RELEVANT) / k)
        should = [s for s in order if gain.get(s, 0.0) >= SHOULD_READ]
        should_total += len(should)
        should_top += sum(1 for s in should if s in order[:k])
        reads = [s for s in unit["reads"] if s in order]
        reads_total += len(reads)
        reads_top += sum(1 for s in reads if s in order[:k])
    n = len(ndcgs)
    return {
        "units": n,
        "ndcg": round(sum(ndcgs) / n, 4) if n else 0.0,
        "precision": round(sum(precisions) / n, 4) if n else 0.0,
        "should_read_top": should_top, "should_read": should_total,
        "read_top": reads_top, "read": reads_total,
        "unjudged": unjudged,
    }


def format_row(label: str, r: Dict[str, Any]) -> str:
    return (f"{label:>10}  nDCG@5 {r['ndcg']:.3f}  P@5 {r['precision']:.3f}  "
            f"should-read in top5 {r['should_read_top']}/{r['should_read']}  |  "
            f"read in top5 {r['read_top']}/{r['read']}"
            + (f"  ({r['unjudged']} unjudged)" if r["unjudged"] else ""))


def http_client(key: str, *, model: str = MODEL, timeout: float = 120.0) -> Client:
    def ask(state: Dict[str, Any], questions: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"state": state, "model": model, "questions": questions})
        request = urllib.request.Request(URL, data=body.encode("utf-8"), headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "mnemo-measure-recall-judged",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {"error": exc.code}
    return ask


# --- the vault side: everything below reads mnemo, nothing above does -------


def _vault_units(vault: Path) -> List[Dict[str, Any]]:
    from mnemo.core.mcp import recall

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    return units_from_log(recall._read_log(log), window_s=recall._DEFAULT_PAIR_WINDOW_S,
                          name_map=recall._name_to_slug(vault), parse_ts=recall._parse_ts)


def _bodies(vault: Path, unit: Dict[str, Any]) -> Dict[str, str]:
    from mnemo.core.mcp import tools

    out = {}
    for rule in tools.list_rules_by_topic(vault, unit["topic"], scope="project",
                                          project=unit["project"], query=unit["query"]):
        page = tools.read_mnemo_rule(vault, rule["slug"], project=unit["project"]) or {}
        out[rule["slug"]] = rule_text(page.get("body") or "")
    return out


def _orders(vault: Path, units: Sequence[Dict[str, Any]], gate: Optional[float],
            *, shipped: bool = False) -> List[List[str]]:
    from mnemo.core.mcp import tools
    from mnemo.core.reflex import bm25
    from mnemo.core.reflex import index as reflex_index
    from mnemo.core.reflex.tokenizer import tokenize_query

    orders = []
    index = None if shipped else reflex_index.load_index(vault)
    for unit in units:
        kwargs = {"scope": "project", "project": unit["project"]}
        if shipped:
            rules = tools.list_rules_by_topic(vault, unit["topic"], query=unit["query"], **kwargs)
            orders.append([r["slug"] for r in rules])
            continue
        base = [r["slug"] for r in tools.list_rules_by_topic(vault, unit["topic"], **kwargs)]
        scores: Dict[str, float] = {}
        if index is not None and gate is not None:
            scores = dict(bm25.score_docs(index, query_tokens=tokenize_query(unit["query"]),
                                          candidate_slugs=base) or [])
        orders.append(gated_order(base, scores, gate))
    return orders


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--judge", action="store_true", help="build the judgments (dry run without --send)")
    parser.add_argument("--send", action="store_true",
                        help="with --judge: post queries and rule bodies to TypeSafe (third party)")
    parser.add_argument("--gates", default="", help="comma-separated rerank gates to compare, e.g. 0,1,3")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli
    vault = cli._resolve_vault()
    qrels_path = vault / ".mnemo" / QRELS_NAME

    if args.judge:
        units = [u for u in _vault_units(vault) if u["reads"]]
        bodies = [_bodies(vault, u) for u in units]
        pairs = sum(len(b) for b in bodies)
        tokens = sum(len(u["query"]) + sum(len(t) + 420 for t in b.values())
                     for u, b in zip(units, bodies)) // 4
        if not args.send:
            print(f"dry run: {len(units)} queries, {pairs} (query, rule) pairs, ~{tokens} tokens "
                  f"(~${tokens * USD_PER_MTOK / 1e6:.4f}). --send posts them to {URL}")
            return 0
        key = os.environ.get("TYPESAFE_API_KEY")
        if not key:
            print("error: --send needs TYPESAFE_API_KEY", file=sys.stderr)
            return 1
        client = http_client(key)
        spent = 0
        for unit, unit_bodies in zip(units, bodies):
            unit["noul"], used = judge_unit(unit["query"], unit_bodies, client)
            spent += used
        qrels = {"model": MODEL, "question_version": QUESTION_VERSION,
                 "judged_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "units": units}
        qrels_path.write_text(json.dumps(qrels, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        judged = sum(len(u["noul"]) for u in units)
        print(f"judged {judged}/{pairs} pairs over {len(units)} queries, {spent} input tokens "
              f"(${spent * USD_PER_MTOK / 1e6:.4f}) -> {qrels_path}")
        return 0

    if not qrels_path.is_file():
        print(f"error: no judgments at {qrels_path}; run with --judge first", file=sys.stderr)
        return 1
    qrels = json.loads(qrels_path.read_text(encoding="utf-8"))
    if qrels.get("question_version") != QUESTION_VERSION:
        print("error: the judgments answer an older question; re-run --judge --send", file=sys.stderr)
        return 1
    units = qrels["units"]
    rows = [("shipped", evaluate(units, _orders(vault, units, None, shipped=True))),
            ("no rerank", evaluate(units, _orders(vault, units, None)))]
    for raw in filter(None, (g.strip() for g in args.gates.split(","))):
        rows.append(("gate " + raw, evaluate(units, _orders(vault, units, float(raw)))))
    if args.json:
        print(json.dumps({"model": qrels["model"], "judged_at": qrels["judged_at"],
                          "rows": dict(rows)}, indent=2))
    else:
        print(f"judgments: {qrels['model']}, {qrels['judged_at']}, {len(units)} queries")
        for label, result in rows:
            print(format_row(label, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
