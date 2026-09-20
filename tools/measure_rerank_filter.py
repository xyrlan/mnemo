"""Is the pair-reading judge a better ranking, or a filter? (#404)

Usage:
    PYTHONPATH=src python3 tools/measure_rerank_filter.py                        # report, local
    PYTHONPATH=src python3 tools/measure_rerank_filter.py --part dev --json      # the dev split, as JSON
    PYTHONPATH=src python3 tools/measure_rerank_filter.py --mine                 # build the units, local
    PYTHONPATH=src python3 tools/measure_rerank_filter.py --export-blind DIR     # chunks for a rater, local
    PYTHONPATH=src python3 tools/measure_rerank_filter.py --import-labels DIR    # fold the labels back, local
    PYTHONPATH=src python3 tools/measure_rerank_filter.py --score                # dry run: pairs, tokens, cost
    PYTHONPATH=src python3 tools/measure_rerank_filter.py --score --send         # ask the judge

**Only ``--score --send`` leaves the machine.** It posts each unit's query and
the first 800 characters of each rule in its list to TypeSafe's
``/v1/systemone``, a third party, and needs ``TYPESAFE_API_KEY``. Everything
else — mining, exporting, importing, the whole report — reads files on disk
and calls nothing.

What this measures, and why it is not ``measure_rerank_judges.py``. That tool
asks whether the judge *reorders* a topic better than BM25F. This one asks the
prior question: what is in the list at all. The population here is the real
thing rather than a replay — every ``list_rules_by_topic`` call in the session
transcripts on this machine that carried a ``query``, with the slugs it
actually returned and the rules read after it, so no ranking has to be
re-derived and the list under test is the one an agent was handed.

Those lists are mostly junk: 15 rules, three quarters of them about something
else, and the agent chooses from slugs alone. Reordering junk leaves junk, so
the report has three parts:

- the **filter** table — at a threshold, how many rules per query survive, how
  much of what survives is irrelevant, how many "should read" rules are lost,
  and how many queries come back empty (an empty answer is a legitimate one:
  "nothing in this topic is about this task");
- the **reranker** rows, over queries with enough rules to reorder, with a
  paired bootstrap interval on the nDCG@5 delta against the order the agent
  was shown — the comparison ``measure_rerank_judges.py`` makes, on this
  population;
- pooled **average precision** for "should read" per signal, which is the
  filter's ceiling: how well each signal separates the rules worth reading
  from the rest, before any threshold is chosen.

The fused signal and its ordering are ``mnemo.core.mcp.rerank``'s
:func:`~mnemo.core.mcp.rerank.fuse`, :func:`~mnemo.core.mcp.rerank.marks` and
:func:`~mnemo.core.mcp.rerank.ranked`, so the numbers here are the shipped
stage's and a test pins that they cannot drift apart.

Limits to carry with any number from here. The labels are a model's
(``claude-fable-5-1``, blind, one label per pair, 0/1/2); they were checked
against a second label set and against hand labels, not against a user. The
thresholds were fixed on the dev split and the test split was opened once, so
a threshold tuned after reading the test rows is no longer measured. And
whether an agent offered two marked rules reads them is not measured here at
all — the read column counts what happened under the order that was actually
shown.

Files, all under ``<vault>/.mnemo``:

- ``recall-units-transcripts.json`` — the population, keyed by ``uid``.
  ``--mine`` builds it and refuses to overwrite one that exists: every label
  and every score is filed under those ids.
- ``recall-labels-full-<rater>.json`` — one rater's 0/1/2 per pair.
- ``recall-scores-full.json`` — the judge's probabilities, under a key per
  question variant; this tool reads and writes ``v0_current``, the question
  ``rerank.question`` ships.

On a python.org macOS build ``urllib`` has no CA bundle;
``SSL_CERT_FILE=/etc/ssl/cert.pem`` fixes it. Do not disable verification.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


#: ``ndcg`` and the cost rate come from the first judged-ranking tool, the
#: bootstrap from the second: one definition of each estimator, so two tools
#: cannot report the same number two ways.
mrj = _sibling("measure_recall_judged")
mrk = _sibling("measure_rerank_judges")

UNITS_NAME = "recall-units-transcripts.json"
SCORES_NAME = "recall-scores-full.json"

#: The question ``rerank.question`` ships. The file keeps one key per variant
#: tried; a report must not mix them.
SCORES_KEY = "v0_current"

#: The label set the 2026-09-20 measurement used. A person's labels would be
#: ``--rater human``, and are a different file.
DEFAULT_RATER = "claude-fable-5-1"

#: Rules per request to the judge. ``measure_recall_judged``'s, so a pair
#: costs the same in both.
CHUNK = mrj.CHUNK

#: Rules per blind chunk file, and a unit is never split across two of them:
#: a rater must see one task with all of its rules, so a label cannot depend
#: on which half of a list it landed in. A single unit larger than this is its
#: own chunk, over the limit.
BLIND_CHUNK = 55

#: The label scale, as the second judge's rubric states it.
SHOULD_READ = 2
JUNK = 0

#: Reordering needs something to reorder; below this a list has no top 5 to
#: get wrong. The filter table uses every query.
MIN_RULES_TO_RERANK = 6
K = 5

#: BM25F's own dev threshold (the loosest keeping >= 90% of should-read rules
#: on dev), and the judge's. The fused bar is the shipped
#: ``recall.rerank.relevantAt``, so the table grades the config.
BM25F_AT = 1.94
JEV_AT = 0.42

SIGNALS = ("bm25f", "jev", "fused")


def unit_id(cwd: str, topic: str, query: str, ts: str) -> str:
    """The id a label and a score are filed under.

    The identity of one list call — the tree it ran in, the topic, the query,
    the moment — so re-mining the same transcripts re-derives the same ids and
    the labels still fit. Changing this orphans every label on disk.
    """
    return hashlib.md5(json.dumps([cwd, topic, query, ts]).encode("utf-8")).hexdigest()[:10]


def part_of(uid: str) -> str:
    """dev or test, by the id alone: a locked split, not a seed.

    A unit lands in the same part whoever runs the tool and whenever, so the
    test split cannot quietly be re-drawn after a threshold has been read off
    the dev rows.
    """
    return "test" if int(uid, 16) % 3 == 0 else "dev"


def result_slugs(content: Any) -> List[str]:
    """The slugs a ``list_rules_by_topic`` result block returned, in its order.

    The block holds the tool's JSON either as a string or inside text blocks,
    and anything that is not a list of ``{"slug": ...}`` returns nothing —
    a truncated or errored result must drop the call, not invent one.
    """
    texts: List[str] = []
    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                texts.append(block["text"])
    out: List[str] = []
    for text in texts:
        try:
            parsed = json.loads(text)
        except ValueError:
            continue
        if not isinstance(parsed, list):
            continue
        for item in parsed:
            if isinstance(item, dict) and isinstance(item.get("slug"), str):
                out.append(item["slug"])
    return out


def calls_from_records(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every queried ``list_rules_by_topic`` in one transcript, with what it
    returned and the rules read after it.

    A call is paired with its result by ``tool_use_id``, so a list whose result
    never came back is dropped rather than counted empty. A ``read_mnemo_rule``
    is credited to the latest *earlier* call in this transcript that showed
    that slug — ``mnemo recall``'s pairing without its time window, which
    inside one transcript has no other session to confuse it with. A call
    without a ``query`` is not a unit: there is no task to judge a rule
    against.
    """
    calls: List[Dict[str, Any]] = []
    pending: Dict[Any, Dict[str, Any]] = {}
    reads: List[Tuple[int, str]] = []
    for record in records:
        content = (record.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "tool_use":
                name = str(block.get("name") or "")
                args = block.get("input") or {}
                if not isinstance(args, dict):
                    continue
                if name.endswith("list_rules_by_topic") and args.get("topic") and args.get("query"):
                    pending[block.get("id")] = {
                        "cwd": str(record.get("cwd") or ""),
                        "topic": str(args["topic"]), "query": str(args["query"]),
                        "ts": str(record.get("timestamp") or ""), "shown": [], "reads": []}
                elif name.endswith("read_mnemo_rule") and args.get("slug"):
                    reads.append((len(calls), str(args["slug"])))
            elif kind == "tool_result" and block.get("tool_use_id") in pending:
                call = pending.pop(block["tool_use_id"])
                call["shown"] = result_slugs(block.get("content"))
                if call["shown"]:
                    calls.append(call)
    for cut, slug in reads:
        for call in reversed(calls[:cut]):
            if slug in call["shown"]:
                if slug not in call["reads"]:
                    call["reads"].append(slug)
                break
    return calls


def unit_from_call(call: Dict[str, Any], texts: Dict[str, str],
                   name_map: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """One call as a unit, or ``None`` when it cannot be one.

    ``shown`` is resolved through ``name_map`` first: lists logged before
    2026-09-08 hold rule *names*, and a name-keyed slug matches nothing in the
    vault. A rule with no text on disk is dropped — the judge and the rater
    both read the text, so a rule neither can see is not a pair — and a list
    left with fewer than two rules is dropped whole: there is nothing to
    filter or to order.
    """
    resolved: List[str] = []
    for raw in call["shown"]:
        slug = (name_map or {}).get(raw, raw)
        if slug not in resolved and texts.get(slug):
            resolved.append(slug)
    if len(resolved) < 2:
        return None
    reads = []
    for raw in call["reads"]:
        slug = (name_map or {}).get(raw, raw)
        if slug in resolved and slug not in reads:
            reads.append(slug)
    return {
        "uid": unit_id(call["cwd"], call["topic"], call["query"], call["ts"]),
        "repo": os.path.basename(call["cwd"].rstrip("/")),
        "topic": call["topic"], "query": call["query"], "ts": call["ts"],
        "shown": resolved, "reads": reads,
        "texts": {slug: texts[slug] for slug in resolved},
    }


def unit_signals(unit: Dict[str, Any], noul: Dict[str, Any],
                 bm25f: Dict[str, float], *, weight: float) -> Dict[str, Dict[str, float]]:
    """The three signals over one unit's rules, by slug.

    ``fused`` is the shipped stage's, computed by ``rerank.fuse`` over the same
    rules the judge was asked about — the BM25F term is normalised by the best
    score *in this list*, so it cannot be built from a pooled scale.
    """
    from mnemo.core.mcp import rerank

    local = {slug: float(bm25f.get(slug, 0.0)) for slug in unit["texts"]}
    judged = {slug: float(values[0]) for slug, values in noul.items()
              if slug in unit["texts"] and values}
    return {"bm25f": local, "jev": judged,
            "fused": rerank.fuse(judged, local, weight=weight)}


def kept_slugs(unit: Dict[str, Any], signal: Optional[Dict[str, float]],
               at: Optional[float]) -> List[str]:
    """What a filter at ``at`` would leave in the list, in the shown order.

    ``None`` is no filter at all — the whole list, which is what ships today.
    A rule the signal has no number for is not kept: for the judge that is a
    rule it never scored, and the stage marks nothing on it either.
    """
    shown = [s for s in unit["shown"] if s in unit["texts"]]
    if signal is None or at is None:
        return shown
    from mnemo.core.mcp import rerank

    marked = rerank.marks(signal, at)
    return [s for s in shown if marked.get(s)]


def filter_row(name: str, at: Optional[float], units: Sequence[Dict[str, Any]],
               labels: Dict[str, Dict[str, int]],
               signals: Dict[str, Dict[str, Dict[str, float]]]) -> Dict[str, Any]:
    """One line of the filter table: what the agent would be offered.

    ``junk`` is the irrelevant share of what survives, over the kept rules a
    rater labelled. ``empty`` counts the queries the filter answers with
    nothing, and ``empty_with_should_read`` how many of those held a rule the
    rater called "should read" — the only one of the two that is a failure.
    """
    kept_total = labelled = junk = 0
    should_kept = should_total = 0
    empty = empty_with_should = 0
    for unit in units:
        label = labels.get(unit["uid"], {})
        signal = signals[unit["uid"]].get(name) if at is not None else None
        kept = kept_slugs(unit, signal, at)
        kept_total += len(kept)
        labelled += sum(1 for s in kept if s in label)
        junk += sum(1 for s in kept if label.get(s) == JUNK)
        should = [s for s in unit["texts"] if label.get(s) == SHOULD_READ]
        should_total += len(should)
        should_kept += sum(1 for s in kept if label.get(s) == SHOULD_READ)
        if not kept:
            empty += 1
            empty_with_should += bool(should)
    n = len(units) or 1
    return {
        "signal": name, "threshold": at, "queries": len(units),
        "rules_per_query": round(kept_total / n, 2),
        "junk_share": round(junk / labelled, 4) if labelled else 0.0,
        "should_read_kept": should_kept, "should_read": should_total,
        "empty": empty, "empty_with_should_read": empty_with_should,
    }


def pairwise(order: Sequence[str], label: Dict[str, int]) -> Optional[float]:
    """Share of label-disagreeing pairs this order puts the right way round.

    Within one query only: a rule ranked in one list says nothing about a rule
    in another. ``None`` when every labelled rule in the list has the same
    label — there is no pair to get right.
    """
    graded = [s for s in order if s in label]
    right = total = 0
    for i, first in enumerate(graded):
        for second in graded[i + 1:]:
            if label[first] == label[second]:
                continue
            total += 1
            right += label[first] > label[second]
    return right / total if total else None


def rerank_rows(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
                orders: Dict[str, List[List[str]]], *, k: int = K) -> List[Dict[str, Any]]:
    """Every ordering graded against the labels, delta against ``shown``.

    ``shown`` is the order the agent was handed, so its delta is zero by
    construction and every other row is paired against it per query. Gain is
    ``label / 2``, so a "should read" rule is worth twice a "related" one. A
    gain is only a gain when the bootstrap interval excludes zero.

    An ``oracle`` row is ordered by the labels themselves and is circular — its
    nDCG@5 is 1.000 by construction and means nothing. Its ``junk_top`` is the
    point: what the *best possible order* still leaves in the top 5, which is
    the ceiling every reranker in this table is working under.
    """
    base: Dict[str, float] = {}
    rows = []
    for name in ["shown"] + [s for s in orders if s != "shown"]:
        scores, deltas, accuracies = [], [], []
        should_top = should_all = junk_top = read_top = read_all = 0
        for unit, order in zip(units, orders[name]):
            label = labels.get(unit["uid"], {})
            gain = {slug: value / 2.0 for slug, value in label.items()}
            value = mrj.ndcg(order, gain, k)
            scores.append(value)
            if name == "shown":
                base[unit["uid"]] = value
            deltas.append(value - base.get(unit["uid"], 0.0))
            accuracy = pairwise(order, label)
            if accuracy is not None:
                accuracies.append(accuracy)
            should_all += sum(1 for s in order if label.get(s) == SHOULD_READ)
            should_top += sum(1 for s in order[:k] if label.get(s) == SHOULD_READ)
            junk_top += sum(1 for s in order[:k] if label.get(s) == JUNK)
            read_all += sum(1 for s in unit["reads"] if s in order)
            read_top += sum(1 for s in unit["reads"] if s in order[:k])
        low, high = mrk.bootstrap_ci(deltas)
        n = len(scores) or 1
        rows.append({
            "ordering": name, "queries": len(scores),
            "ndcg": round(sum(scores) / n, 4),
            "delta": round(sum(deltas) / n, 4), "ci": [round(low, 4), round(high, 4)],
            "pairwise": round(sum(accuracies) / len(accuracies), 4) if accuracies else None,
            "should_read_top": should_top, "should_read": should_all,
            "junk_top": junk_top, "read_top": read_top, "read": read_all,
        })
    return rows


def average_precision(scored: Sequence[Tuple[float, bool]]) -> float:
    """Pooled AP for the positives. Ties keep the incoming order.

    Pooled across queries on purpose: this is the signal's own separation of
    "should read" from the rest, before a threshold turns it into a filter, and
    a threshold is one number for every list.
    """
    ranked = sorted(range(len(scored)), key=lambda i: (-scored[i][0], i))
    positives = sum(1 for _, hit in scored if hit)
    hits = 0
    total = 0.0
    for rank, i in enumerate(ranked, start=1):
        if scored[i][1]:
            hits += 1
            total += hits / rank
    return round(total / positives, 4) if positives else 0.0


def retest(sample: Sequence[Dict[str, Any]], units: Sequence[Dict[str, Any]],
           labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """How often one rater gave the same pair the same label twice.

    ``label_recall_pairs.py`` draws a stratified sample of (task, rule) pairs
    and files a label per pair; this tool files a label per (uid, slug). The
    two are joined on the text the rater actually saw — the task and the rule —
    which is the only key both files carry. A pair in both was answered twice,
    in a different context and a different order, so the exact-agreement rate
    here is the label noise the filter table inherits: a number to read the
    "24/24" with, not a measurement of the rater's skill.
    """
    seen: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for unit in units:
        for slug, text in unit["texts"].items():
            seen.setdefault((unit["query"], text), (unit["uid"], slug))
    both: List[Tuple[int, int]] = []
    for pair in sample:
        if pair.get("label") is None:
            continue
        found = seen.get((pair.get("task"), pair.get("rule")))
        if found is None:
            continue
        uid, slug = found
        if slug in labels.get(uid, {}):
            both.append((int(pair["label"]), int(labels[uid][slug])))
    return {
        "pairs": len(both),
        "exact": sum(1 for first, second in both if first == second),
        "confusion": {"%d->%d" % (a, b): sum(1 for x, y in both if (x, y) == (a, b))
                      for a in (0, 1, 2) for b in (0, 1, 2)},
    }


def blind_chunks(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]],
                 *, limit: int = BLIND_CHUNK) -> Tuple[List[List[Dict[str, Any]]], Dict[str, List[str]]]:
    """Chunk files for a rater that is not on this machine, and the id map.

    A chunk carries the task and the rule text and nothing else: no slug, no
    uid, no judge's score, no whether the rule was read. The rater answers
    with ids, and only the id map on this machine can turn one back into a
    pair. Pairs a ``--rater`` has already labelled are left out, so an
    interrupted round continues instead of being re-asked.
    """
    chunks: List[List[Dict[str, Any]]] = []
    ids: Dict[str, List[str]] = {}
    current: List[Dict[str, Any]] = []
    size = 0
    for unit in units:
        done = labels.get(unit["uid"], {})
        rules = [s for s in sorted(unit["texts"]) if unit["texts"][s] and s not in done]
        if not rules:
            continue
        if current and size + len(rules) > limit:
            chunks.append(current)
            current, size = [], 0
        entry: Dict[str, Any] = {"task": unit["query"], "rules": []}
        for slug in rules:
            key = str(len(ids))
            ids[key] = [unit["uid"], slug]
            entry["rules"].append({"id": int(key), "rule": unit["texts"][slug]})
        current.append(entry)
        size += len(rules)
    if current:
        chunks.append(current)
    return chunks, ids


def imported_labels(ids: Dict[str, List[str]],
                    given: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, int]], List[str]]:
    """Fold ``{id: 0|1|2}`` back onto (uid, slug), or say what is wrong with it.

    Everything or nothing. A missing rule or a 3 would quietly become a
    different measurement than the one the chunks asked for, so the caller
    writes nothing while any problem stands.
    """
    problems: List[str] = []
    labels: Dict[str, Dict[str, int]] = {}
    answers = {str(key): value for key, value in given.items()}
    for key in sorted(ids, key=lambda k: int(k)):
        if key not in answers:
            problems.append("no label for id %s" % key)
    for key in sorted(answers, key=lambda k: int(k) if k.isdigit() else -1):
        value = answers[key]
        if key not in ids:
            problems.append("id %s is not in the export" % key)
        elif isinstance(value, bool) or value not in (0, 1, 2):
            problems.append("id %s: %r is not 0, 1 or 2" % (key, value))
        else:
            uid, slug = ids[key]
            labels.setdefault(uid, {})[slug] = int(value)
    return labels, problems


def pending_chunks(unit: Dict[str, Any], done: Dict[str, Any],
                   *, size: int = CHUNK) -> List[List[str]]:
    """The rules of one unit still to score, in stable order, ``size`` at a time.

    A slug that already has a number is never asked again: a run interrupted
    after 60 units resumes at 61 instead of paying for all 85.
    """
    todo = [s for s in sorted(unit["texts"]) if unit["texts"][s] and not done.get(s)]
    return [todo[i:i + size] for i in range(0, len(todo), size)]


def format_report(report: Dict[str, Any]) -> str:
    lines = [
        "%d queried list calls, %d (query, rule) pairs; labels: %s%s"
        % (report["units"], report["pairs"], report["rater"],
           ", blind" if report["blind"] else ""),
        "labels 0/1/2: %s; scores: %s, %d pairs"
        % (report["label_counts"], report["scores_key"], report["scored"]),
        "same rater asked twice: %d/%d pairs labelled identically"
        % (report["retest"]["exact"], report["retest"]["pairs"]),
        "part %s: %d queries (dev %d, test %d)"
        % (report["part"], report["queries"], report["dev"], report["test"]),
        "",
        "what the list offers            rules/query   junk   should-read kept   empty (had one)",
    ]
    for row in report["filter"]:
        label = row["signal"] if row["threshold"] is None else "%s >= %g" % (row["signal"], row["threshold"])
        lines.append("  %-28s %7.1f  %5.0f%%   %14s   %5d (%d)" % (
            label, row["rules_per_query"], 100 * row["junk_share"],
            "%d/%d" % (row["should_read_kept"], row["should_read"]),
            row["empty"], row["empty_with_should_read"]))
    lines.append("")
    rows = report["rerank"]
    lines.append("as a reranker, %d queries with >= %d rules"
                 % (rows[0]["queries"] if rows else 0, MIN_RULES_TO_RERANK))
    for row in rows:
        low, high = row["ci"]
        circular = row["ordering"] == "oracle"
        mark = " *" if (low > 0 or high < 0) and row["ordering"] != "shown" and not circular else "  "
        lines.append(
            "  %-8s nDCG@5 %.3f  delta %+.3f [%+.3f, %+.3f]%s should-read top%d %d/%d"
            "  pairwise %s  junk top%d %3d  read top%d %d/%d" % (
                row["ordering"], row["ndcg"], row["delta"], low, high, mark, K,
                row["should_read_top"], row["should_read"],
                "n/a" if row["pairwise"] is None else "%.3f" % row["pairwise"],
                K, row["junk_top"], K, row["read_top"], row["read"])
            + ("   circular: the ceiling, graded by its own labels" if circular else ""))
    lines.append("* = 95% paired bootstrap interval of the delta excludes zero")
    lines.append("")
    lines.append("pooled average precision for \"should read\": " + "  ".join(
        "%s %.3f" % (name, value) for name, value in report["average_precision"].items()))
    return "\n".join(lines)


# --- the vault side: everything below reads mnemo, nothing above does -------


def _records(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _transcripts(projects: str) -> List[Path]:
    return sorted(Path(projects).glob("*/*.jsonl"))


def _rule_text(vault: Path, slug: str, cache: Dict[str, str]) -> str:
    from mnemo.core.mcp import rerank
    from mnemo.core.mcp import tools

    if slug not in cache:
        page = tools.read_mnemo_rule(vault, slug, scope="vault") or {}
        cache[slug] = rerank.rule_text(page.get("body") or "")
    return cache[slug]


def _mine(vault: Path, projects: str) -> List[Dict[str, Any]]:
    from mnemo.core.mcp import recall

    name_map = recall._name_to_slug(vault) or {}
    cache: Dict[str, str] = {}
    units: Dict[str, Dict[str, Any]] = {}
    for path in _transcripts(projects):
        for call in calls_from_records(_records(path)):
            texts = {}
            for raw in call["shown"]:
                slug = name_map.get(raw, raw)
                texts[slug] = _rule_text(vault, slug, cache)
            unit = unit_from_call(call, texts, name_map)
            if unit is not None:
                units.setdefault(unit["uid"], unit)
    return sorted(units.values(), key=lambda u: (u["ts"], u["uid"]))


def _bm25f(vault: Path, units: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """BM25F per unit, over the rules that unit showed.

    Through the stage's own :func:`rerank.bm25f_scores`, which loads the reflex
    index per call — a fifth of a second each, so a report over 85 units spends
    a few seconds reloading it. Worth it: the alternative is a second
    implementation of the scoring path, and then the table grades something
    that does not ship.
    """
    from mnemo.core.mcp import rerank

    return {u["uid"]: rerank.bm25f_scores(vault, u["query"], sorted(u["texts"])) for u in units}


def _safe_rater(rater: str) -> str:
    """``label_recall_pairs.labels_name``'s spelling, so both tools name one
    rater's files the same way."""
    return "".join(c for c in rater.lower() if c.isalnum() or c in "-_") or "human"


def _labels_path(vault: Path, rater: str) -> Path:
    return vault / ".mnemo" / ("recall-labels-full-%s.json" % _safe_rater(rater))


def _load_units(vault: Path) -> List[Dict[str, Any]]:
    path = vault / ".mnemo" / UNITS_NAME
    if not path.is_file():
        raise SystemExit("error: no units at %s; run with --mine first" % path)
    return json.loads(path.read_text(encoding="utf-8"))["units"]


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _report(vault: Path, units: Sequence[Dict[str, Any]], labels: Dict[str, Any],
            scores: Dict[str, Any], sample: Sequence[Dict[str, Any]], *,
            part: str, bars: Tuple[float, float, float]) -> Dict[str, Any]:
    from mnemo.core.mcp import rerank

    label_map = labels.get("labels") or {}
    chosen = [u for u in units if part == "all" or part_of(u["uid"]) == part]
    local = _bm25f(vault, chosen)
    signals = {u["uid"]: unit_signals(u, (scores.get(u["uid"]) or {}), local[u["uid"]],
                                      weight=rerank.DEFAULT_BM25_WEIGHT) for u in chosen}
    big = [u for u in chosen if len(u["texts"]) >= MIN_RULES_TO_RERANK]
    orders = {"shown": [[s for s in u["shown"] if s in u["texts"]] for u in big]}
    for name in SIGNALS:
        orders[name] = [rerank.ranked([s for s in u["shown"] if s in u["texts"]],
                                      signals[u["uid"]][name]) for u in big]
    # The ceiling: the same rules ordered by the labels themselves.
    orders["oracle"] = [rerank.ranked(
        [s for s in u["shown"] if s in u["texts"]],
        {s: float(v) for s, v in label_map.get(u["uid"], {}).items()}) for u in big]
    pooled = {}
    for name in ("shown",) + SIGNALS:
        scored: List[Tuple[float, bool]] = []
        for unit in chosen:
            label = label_map.get(unit["uid"], {})
            shown = [s for s in unit["shown"] if s in unit["texts"]]
            for i, slug in enumerate(shown):
                value = -float(i) if name == "shown" else signals[unit["uid"]][name].get(slug, 0.0)
                scored.append((value, label.get(slug) == SHOULD_READ))
        pooled[name] = average_precision(scored)
    return {
        "units": len(units), "pairs": sum(len(u["texts"]) for u in units),
        "retest": retest(sample, units, label_map),
        "rater": labels.get("rater", "?"), "blind": bool(labels.get("blind")),
        "label_counts": {str(k): sum(1 for v in label_map.values() for x in v.values() if x == k)
                         for k in (0, 1, 2)},
        "scores_key": SCORES_KEY, "scored": sum(len(v) for v in scores.values()),
        "part": part, "queries": len(chosen),
        "dev": sum(1 for u in units if part_of(u["uid"]) == "dev"),
        "test": sum(1 for u in units if part_of(u["uid"]) == "test"),
        "thresholds": list(bars),
        "filter": [filter_row("whole list", None, chosen, label_map, signals)]
                  + [filter_row(name, at, chosen, label_map, signals)
                     for name, at in zip(SIGNALS, bars)],
        "rerank": rerank_rows(big, label_map, orders),
        "average_precision": pooled,
    }


def thresholds(raw: str) -> Tuple[float, float, float]:
    """The three bars, from ``--thresholds`` or the defaults.

    The fused one defaults to the shipped ``recall.rerank.relevantAt``, so the
    table grades the configuration rather than a number typed here.
    """
    from mnemo.core.mcp import rerank

    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if not parts:
        return BM25F_AT, JEV_AT, rerank.DEFAULT_RELEVANT_AT
    if len(parts) != 3:
        raise SystemExit("error: --thresholds takes three numbers: bm25f,jev,fused")
    return tuple(float(p) for p in parts)  # type: ignore[return-value]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mine", action="store_true",
                        help="build the units from session transcripts (local)")
    parser.add_argument("--force", action="store_true",
                        help="with --mine: overwrite the units file every label is keyed to")
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--export-blind", metavar="DIR",
                        help="write blind chunk files and the id map for a rater (local)")
    parser.add_argument("--import-labels", metavar="DIR",
                        help="read the rater's *.labels.json back from DIR (local)")
    parser.add_argument("--score", action="store_true",
                        help="ask the judge for a probability per pair (dry run without --send)")
    parser.add_argument("--send", action="store_true",
                        help="with --score: post queries and rule texts to TypeSafe (third party)")
    parser.add_argument("--rater", default=DEFAULT_RATER, help="whose labels to read or write")
    parser.add_argument("--part", default="test", choices=("dev", "test", "all"),
                        help="which split to report (default: test, the locked one)")
    parser.add_argument("--thresholds", default="",
                        help="bm25f,jev,fused bars for the filter table")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo import cli
    from mnemo.core.mcp import rerank

    vault = cli._resolve_vault()
    units_path = vault / ".mnemo" / UNITS_NAME
    scores_path = vault / ".mnemo" / SCORES_NAME
    labels_path = _labels_path(vault, args.rater)

    if args.mine:
        if units_path.is_file() and not args.force:
            print("error: %s exists and every label is keyed to its uids; --force to rebuild"
                  % units_path, file=sys.stderr)
            return 1
        units = _mine(vault, args.projects)
        _write_json(units_path, {
            "built_from": "session transcripts, list_rules_by_topic tool_use + tool_result",
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "units": units})
        print("%d units, %d (query, rule) pairs -> %s"
              % (len(units), sum(len(u["texts"]) for u in units), units_path))
        return 0

    units = _load_units(vault)
    labels = json.loads(labels_path.read_text(encoding="utf-8")) if labels_path.is_file() else {}
    label_map = labels.get("labels") or {}

    if args.export_blind:
        out = Path(args.export_blind)
        out.mkdir(parents=True, exist_ok=True)
        chunks, ids = blind_chunks(units, label_map)
        for i, chunk in enumerate(chunks, start=1):
            _write_json(out / ("chunk-%03d.json" % i), chunk)
        _write_json(out / "ids.json", {"units_file": UNITS_NAME, "ids": ids})
        print("%d rules in %d chunk file(s) -> %s\n"
              "the rater answers each chunk-NNN.json with a chunk-NNN.labels.json "
              "next to it: {\"<id>\": 0|1|2}" % (len(ids), len(chunks), out))
        return 0

    if args.import_labels:
        source = Path(args.import_labels)
        id_file = source / "ids.json"
        if not id_file.is_file():
            print("error: no ids.json in %s; --export-blind writes it" % source, file=sys.stderr)
            return 1
        ids = json.loads(id_file.read_text(encoding="utf-8"))["ids"]
        given: Dict[str, Any] = {}
        for path in sorted(source.glob("*.labels.json")):
            given.update(json.loads(path.read_text(encoding="utf-8")))
        folded, problems = imported_labels(ids, given)
        if problems:
            print("error: %d problem(s) with the labels in %s; nothing written"
                  % (len(problems), source), file=sys.stderr)
            for problem in problems[:10]:
                print("  " + problem, file=sys.stderr)
            return 1
        for uid, pairs in folded.items():
            label_map.setdefault(uid, {}).update(pairs)
        _write_json(labels_path, {
            "rater": args.rater, "blind": True,
            "scale": "0 not relevant / 1 related / 2 should read (second judge rubric)",
            "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "units_file": UNITS_NAME, "labels": label_map})
        print("%d labels over %d queries -> %s"
              % (sum(len(v) for v in label_map.values()), len(label_map), labels_path))
        return 0

    saved = json.loads(scores_path.read_text(encoding="utf-8")) if scores_path.is_file() else {}
    scores: Dict[str, Dict[str, List[float]]] = saved.get(SCORES_KEY) or {}

    if args.score:
        jobs = [(u, chunk) for u in units for chunk in pending_chunks(u, scores.get(u["uid"], {}))]
        pairs = sum(len(chunk) for _, chunk in jobs)
        if not args.send:
            tokens = sum(len(u["query"]) + sum(len(u["texts"][s]) + 420 for s in chunk)
                         for u, chunk in jobs) // 4
            print("dry run: %d pairs still to score in %d request(s), ~%d tokens (~$%.4f). "
                  "--send posts them to %s" % (pairs, len(jobs), tokens,
                                               tokens * mrj.USD_PER_MTOK / 1e6, rerank.TYPESAFE_URL))
            return 0
        chosen = rerank.settings(None)
        key = os.environ.get(chosen["keyEnv"])
        if not key:
            print("error: --score --send needs %s" % chosen["keyEnv"], file=sys.stderr)
            return 1
        client = rerank.typesafe_client(key, model=chosen["model"], timeout=120.0)
        failed = got_pairs = 0
        for unit, chunk in jobs:
            try:
                answered = rerank.scores(unit["query"], unit["texts"], chunk, client)
            except Exception:
                # One failed request must not lose the answers already paid
                # for: the file is written after every chunk and a re-run asks
                # only for what is still missing.
                failed += 1
                continue
            for slug, value in answered.items():
                scores.setdefault(unit["uid"], {}).setdefault(slug, []).append(value)
            got_pairs += len(answered)
            saved[SCORES_KEY] = scores
            _write_json(scores_path, saved)
        print("scored %d of %d pending pairs, %d failed request(s) -> %s"
              % (got_pairs, pairs, failed, scores_path))
        return 1 if failed else 0

    if not label_map:
        print("error: no labels at %s; --export-blind then --import-labels" % labels_path,
              file=sys.stderr)
        return 1
    if not scores:
        print("error: no %s scores at %s; run with --score" % (SCORES_KEY, scores_path),
              file=sys.stderr)
        return 1
    # The same rater's stratified sample, when ``label_recall_pairs.py`` drew
    # one: the pairs it holds were answered twice and say how stable a label is.
    sample_path = vault / ".mnemo" / ("recall-labels-%s.json" % _safe_rater(args.rater))
    sample = (json.loads(sample_path.read_text(encoding="utf-8")).get("pairs") or []
              if sample_path.is_file() else [])
    report = _report(vault, units, labels, scores, sample,
                     part=args.part, bars=thresholds(args.thresholds))
    print(json.dumps(report, indent=2) if args.json else format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
