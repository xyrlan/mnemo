"""The pair question behind ``mnemo dedup-rules --judge`` (#187, #409).

Two rules that say one thing in different words survive every dedupe mnemo
has. #187 measured why: the inbox gate is Jaccard >= 0.6 on word tokens, a
real duplicate scored 0.136, and the p90 of 79,800 unrelated pairs was 0.131 —
no threshold separates them. ``mnemo dedup-rules`` folds pages that share a
``name:``; ``mnemo reclassify``'s ``merge`` verdict grades ten rules per call,
so it only sees a duplicate whose twin landed in the same batch of ten.

``tools/measure_jev_dedupe.py`` (#400) showed that a judge reading one pair at
a time does see them. This module is that question, moved into ``src/`` so
the tool and the command send the same bytes — the pattern #404 used for
``rerank.question``, pinned by a test. Everything here is pure: no argparse,
no network, no printing. The client is a function the caller hands in, and
:func:`ask` is the only place a request is shaped.

What the judge is asked, and why it is a queue. One ``score`` question per
pair, three ordered levels, so the answer is the decision and not a number to
tune: 0 a developer needs both, 1 related, 2 one can be deleted with no loss.
There is no labelled set of duplicates in a vault, so this has no precision
and no recall to quote — :func:`build_queue` ranks pairs for a curator to
read, and nothing here deletes, merges or stages anything.

Pairs come from one topic bucket of one project scope — what
``list_rules_by_topic(topic, scope="project", project=P)`` returns — because
that is the list a duplicate actually costs something in: since #404 an answer
is 15 rules of which three quarters are about something else, and two
near-copies of one rule take two of the slots that are not. A pair is asked
about once even when both rules share several topics, or when both are
universal and every project's bucket holds them.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.mcp.rerank import GRAPH_SECTION

#: The gate the inbox dedupe applies today (``dedup._bodies_similar``), kept
#: here so a row can say whether the shipped filter would have caught a pair.
JACCARD_GATE = 0.6

#: Halfway between "related" and "same lesson": rounds to a duplicate.
DUPLICATE_SCORE = 1.5

#: USD per million input tokens; output is free (docs.typesafe.ai/models,
#: read 2026-09-19).
USD_PER_MTOK = 0.042

#: Body prefix per rule, both for what the judge reads and for the Jaccard
#: column, so the two columns of a row are computed over the same text.
DEFAULT_MAX_CHARS = 1200

#: A spend guard, not a quality bar: the 2,203 live rules on this machine's
#: vault are 48,090 pairs across 679 buckets. A ``--send`` over this refuses
#: and names the largest buckets, so narrowing is the obvious next move.
DEFAULT_MAX_PAIRS = 5000

DEFAULT_WORKERS = 8

#: One request per pair, in a batch nobody is waiting on — unlike the recall
#: stage's four seconds, which is tuned for an agent's tool call.
DEFAULT_TIMEOUT_S = 60.0

#: Which provider's stored key is used when ``recall.rerank.provider`` is
#: ``none``. The judged queue does not require the recall stage to be on.
DEFAULT_PROVIDER = "typesafe"

#: How much of each body the queue file carries, for a curator reading the
#: table rather than opening both pages.
OPENING_CHARS = 200

ANSWERS_FILENAME = "dedupe-answers.json"
QUEUE_FILENAME = "dedupe-queue.json"

QUESTION = {
    "same": {
        "type": "score",
        "instructions": (
            "Do these two stored engineering rules state the same lesson, "
            "so that keeping both is redundant?"
        ),
        "criteria": [
            "Different lessons: a developer needs both",
            "Related: overlapping theme but each adds something the other lacks",
            "Same lesson in different words: one can be deleted with no loss",
        ],
    }
}

#: ``client(state, questions) -> response``. May raise, or answer with
#: something that is not a score; :func:`ask` treats both as "not measured".
Client = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]

Pair = Tuple[str, str]


# --------------------------------------------------------------- the text


def rule_text(body: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """What is judged and what Jaccard is computed over: the rule without its
    link section, whitespace collapsed, bounded.

    The link section goes for the same reason the recall stage drops it: it is
    a list of neighbours, and rules in one cluster link to each other, so two
    unrelated pages in the same corner of the graph share tokens that say
    nothing about what they claim. On ``mnemo``/``measurement`` (435 pairs)
    keeping it moves the p90 of the Jaccard column from 0.117 to 0.142 and
    changes 5 of the top 20 pairs by ratio.
    """
    return " ".join(body.split(GRAPH_SECTION)[0].split())[:max_chars]


def jaccard(a: str, b: str) -> float:
    """The ratio ``dedup._bodies_similar`` thresholds, same tokenisation."""
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def pairs_of(bodies: Mapping[str, str]) -> List[Pair]:
    """Every unordered pair of slugs with a body; an empty body judges nothing."""
    slugs = sorted(s for s, b in bodies.items() if b)
    return [(slugs[i], slugs[j])
            for i in range(len(slugs)) for j in range(i + 1, len(slugs))]


# ---------------------------------------------------------- the arithmetic


def estimate_pairs(pairs: Sequence[Pair], bodies: Mapping[str, str]) -> Dict[str, Any]:
    """Pairs, tokens and cost of asking about *pairs*, from characters / 4.

    A rough bound so the price is known before the call, not a bill: the
    question's own text rides along with every pair and is counted too.
    """
    overhead = len(json.dumps(QUESTION))
    chars = sum(len(bodies.get(a) or "") + len(bodies.get(b) or "") + overhead
                for a, b in pairs)
    tokens = chars // 4
    return {"pairs": len(pairs), "tokens": tokens,
            "usd": round(tokens * USD_PER_MTOK / 1e6, 4)}


def estimate(bodies: Mapping[str, str]) -> Dict[str, Any]:
    """:func:`estimate_pairs` over every pair in one bucket."""
    return estimate_pairs(pairs_of(bodies), bodies)


# ------------------------------------------------------------- the request


def ask(client: Client, body_a: str, body_b: str) -> Dict[str, Any]:
    """One pair, one request. The only place the state and the question meet.

    A pair the client could not answer comes back with ``score: None`` —
    "not measured", which callers count apart and never fold into "not a
    duplicate". Every failure is that answer: a raised exception, an error
    body, a score that is not a number.
    """
    try:
        out = client({"rule_a": body_a, "rule_b": body_b}, QUESTION)
    except Exception:  # noqa: BLE001 — a provider failure is an unmeasured pair
        out = {}
    answer = ((out or {}).get("answers") or {}).get("same") or {}
    score = answer.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        score = None
    confidence = answer.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        confidence = None
    return {
        "score": None if score is None else float(score),
        "confidence": None if confidence is None else float(confidence),
        "input_tokens": int(((out or {}).get("usage") or {}).get("input_tokens", 0) or 0),
    }


# ---------------------------------------------------------- the provider


def provider_settings(cfg: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """``recall.rerank``'s block, with a provider that is never ``none``.

    The judged queue is a maintenance command a person runs by hand, not the
    recall path, so it does not require ``recall.rerank.provider`` to be on —
    it only needs somewhere to find a key, and ``mnemo rerank --setup`` stores
    one under the provider's name whether or not the stage is enabled. The
    model, the key's environment variable and the key itself stay
    :mod:`mnemo.core.mcp.rerank`'s, which is also where the verifying TLS
    context comes from.
    """
    from mnemo.core.mcp import rerank

    chosen = rerank.settings(cfg)
    if chosen["provider"] == "none":
        chosen = dict(chosen, provider=DEFAULT_PROVIDER)
    return chosen


# --------------------------------------------------------------- the plan


def projects(vault_root: Path) -> List[str]:
    """Every project the rule-activation index knows, sorted."""
    from mnemo.core import rule_activation

    index = rule_activation.load_index(vault_root)
    return sorted((index or {}).get("by_project") or {})


def buckets(vault_root: Path, *, project: Optional[str] = None,
            topic: Optional[str] = None) -> List[Dict[str, Any]]:
    """The (project, topic) buckets with at least two live rules in them.

    One bucket is exactly ``list_rules_by_topic(topic, scope="project",
    project=P)``: local rules plus universal ones, retired rules withheld.
    """
    from mnemo.core.mcp import tools

    out: List[Dict[str, Any]] = []
    for proj in ([project] if project else projects(vault_root)):
        topics = ([topic] if topic
                  else tools.get_mnemo_topics(vault_root, scope="project", project=proj))
        for name in topics:
            slugs = [r["slug"] for r in
                     tools.list_rules_by_topic(vault_root, name, scope="project", project=proj)]
            if len(slugs) > 1:
                out.append({"project": proj, "topic": name, "slugs": slugs})
    return out


def load_bodies(vault_root: Path, bucket_list: Sequence[Mapping[str, Any]], *,
                max_chars: int = DEFAULT_MAX_CHARS) -> Tuple[Dict[str, str], Dict[str, str]]:
    """``(bodies, names)`` for every slug in *bucket_list*, each page read once.

    A slug is the same page in every bucket that holds it, so the cache is
    keyed by slug alone — on this machine's vault that is 2,203 reads instead
    of 54,000.
    """
    from mnemo.core.mcp import tools

    bodies: Dict[str, str] = {}
    names: Dict[str, str] = {}
    for bucket in bucket_list:
        for slug in bucket["slugs"]:
            if slug in bodies:
                continue
            page = tools.read_mnemo_rule(
                vault_root, slug, scope="project", project=bucket["project"]) or {}
            bodies[slug] = rule_text(page.get("body") or "", max_chars)
            names[slug] = str(page.get("name") or slug)
    return bodies, names


def plan_pairs(bucket_list: Sequence[Mapping[str, Any]],
               bodies: Mapping[str, str]) -> Tuple[List[Dict[str, Any]], int]:
    """``(pairs, bucket_pair_total)`` — every pair to ask about, once.

    Each row carries the bucket it was first seen in, its Jaccard ratio and
    its **rank by that ratio inside that bucket**: a pair the judge calls a
    duplicate that Jaccard ranks 41st of 435 is the finding, a pair both put
    first is not. The buckets are walked in sorted order, so which bucket a
    pair is attributed to does not depend on the order they came back in.
    """
    rows: List[Dict[str, Any]] = []
    seen = set()
    total = 0
    for bucket in sorted(bucket_list, key=lambda b: (b["project"], b["topic"])):
        local = {s: bodies.get(s) or "" for s in bucket["slugs"]}
        pairs = pairs_of(local)
        total += len(pairs)
        ratios = {p: jaccard(local[p[0]], local[p[1]]) for p in pairs}
        order = sorted(pairs, key=lambda p: (-ratios[p], p))
        rank = {p: i + 1 for i, p in enumerate(order)}
        for pair in order:
            if pair in seen:
                continue
            seen.add(pair)
            rows.append({
                "a": pair[0], "b": pair[1],
                "project": bucket["project"], "topic": bucket["topic"],
                "jaccard": round(ratios[pair], 4),
                "jaccard_rank": rank[pair],
                "bucket_pairs": len(pairs),
            })
    return rows, total


def largest_buckets(bucket_list: Sequence[Mapping[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
    """The buckets that dominate a pair count, worst first — what a refusal
    over ``--max-pairs`` has to name for the cap to be actionable."""
    sized = [{"project": b["project"], "topic": b["topic"], "rules": len(b["slugs"]),
              "pairs": len(b["slugs"]) * (len(b["slugs"]) - 1) // 2}
             for b in bucket_list]
    sized.sort(key=lambda b: (-b["pairs"], b["project"], b["topic"]))
    return sized[:limit]


# ------------------------------------------------------- answers on disk


def answers_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / ANSWERS_FILENAME


def queue_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / QUEUE_FILENAME


def load_answers(vault_root: Path, *, model: str) -> Dict[Pair, Dict[str, Any]]:
    """Every answer already paid for, under *model*.

    An answer from another model is left on disk and not reused: a score under
    one model says nothing about the next one, and silently mixing the two
    would put a number in the queue that no single run produced. A file that
    cannot be read costs the resume and nothing else.
    """
    path = answers_path(vault_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: Dict[Pair, Dict[str, Any]] = {}
    for row in (raw or {}).get("answers") or []:
        if not isinstance(row, dict) or row.get("model") != model:
            continue
        a, b = row.get("a"), row.get("b")
        if isinstance(a, str) and isinstance(b, str):
            out[(a, b) if a <= b else (b, a)] = row
    return out


def save_answers(vault_root: Path, answers: Mapping[Pair, Mapping[str, Any]]) -> None:
    """Replace the store with *answers*, atomically.

    Written after every batch rather than at the end: a run over thousands of
    pairs that dies halfway must leave what it already paid for behind, and a
    re-run then asks only for what is missing.
    """
    rows = [dict(value) for _pair, value in sorted(answers.items())]
    data = json.dumps({"answers": rows}, indent=2, ensure_ascii=False, sort_keys=True)
    atomic_write_bytes(answers_path(vault_root), data.encode("utf-8"))


def missing(pairs: Iterable[Mapping[str, Any]],
            answers: Mapping[Pair, Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """The rows of *pairs* the store has no answer for, in order."""
    return [row for row in pairs if (row["a"], row["b"]) not in answers]


# ------------------------------------------------------------- the asking


def judge_pairs(pairs: Sequence[Mapping[str, Any]], bodies: Mapping[str, str],
                client: Client, *, model: str, workers: int = DEFAULT_WORKERS,
                flush: Optional[Callable[[Dict[Pair, Dict[str, Any]]], None]] = None,
                flush_every: int = 25) -> Dict[Pair, Dict[str, Any]]:
    """Ask about every row of *pairs*; return what was answered, by pair.

    Answers are collected in the calling thread and handed to *flush* as they
    arrive, so an interrupted run keeps what it already paid for: the pool
    only makes requests. It is also drained a few requests at a time rather
    than in one map over thousands, so a ``Ctrl-C`` waits for the batch in
    flight instead of for the whole run.

    A pair the judge could not answer is **not** returned, and so is asked
    again next run — an error is not a score of zero.
    """
    from concurrent.futures import ThreadPoolExecutor

    out: Dict[Pair, Dict[str, Any]] = {}
    rows = list(pairs)
    if not rows:
        return out
    since_flush = 0
    batch = max(1, workers)

    def one(row: Mapping[str, Any]) -> Tuple[Mapping[str, Any], Dict[str, Any]]:
        return row, ask(client, bodies.get(row["a"]) or "", bodies.get(row["b"]) or "")

    try:
        for start in range(0, len(rows), batch):
            with ThreadPoolExecutor(batch) as pool:
                answered = list(pool.map(one, rows[start:start + batch]))
            for row, answer in answered:
                if answer["score"] is None:
                    continue
                out[(row["a"], row["b"])] = {
                    "a": row["a"], "b": row["b"], "model": model,
                    "score": answer["score"], "confidence": answer["confidence"],
                    "input_tokens": answer["input_tokens"],
                    "asked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                since_flush += 1
            if flush is not None and since_flush >= max(1, flush_every):
                flush(out)
                since_flush = 0
    finally:
        if flush is not None and since_flush:
            flush(out)
    return out


# -------------------------------------------------------------- the queue


def build_queue(pairs: Sequence[Mapping[str, Any]],
                answers: Mapping[Pair, Mapping[str, Any]],
                bodies: Mapping[str, str], names: Mapping[str, str], *,
                at: float = DUPLICATE_SCORE) -> List[Dict[str, Any]]:
    """The rows a curator reads: scored at or over *at*, best first.

    Ties break on the Jaccard rank, so of two pairs the judge scored the same
    the one the shipped gate ranks lowest — the duplicate no token overlap
    would ever have surfaced — is read first.
    """
    out: List[Dict[str, Any]] = []
    for row in pairs:
        answer = answers.get((row["a"], row["b"]))
        if answer is None or answer.get("score") is None:
            continue
        if float(answer["score"]) < at:
            continue
        out.append({
            "a": row["a"], "b": row["b"],
            "name_a": names.get(row["a"], row["a"]),
            "name_b": names.get(row["b"], row["b"]),
            "opening_a": (bodies.get(row["a"]) or "")[:OPENING_CHARS],
            "opening_b": (bodies.get(row["b"]) or "")[:OPENING_CHARS],
            "score": float(answer["score"]),
            "confidence": answer.get("confidence"),
            "jaccard": row["jaccard"],
            "jaccard_rank": row["jaccard_rank"],
            "bucket_pairs": row["bucket_pairs"],
            "project": row["project"], "topic": row["topic"],
            "over_gate": row["jaccard"] >= JACCARD_GATE,
        })
    out.sort(key=lambda r: (-r["score"], -r["jaccard_rank"], r["a"], r["b"]))
    return out


def summarize(pairs: Sequence[Mapping[str, Any]],
              answers: Mapping[Pair, Mapping[str, Any]],
              queue: Sequence[Mapping[str, Any]], *,
              at: float = DUPLICATE_SCORE) -> Dict[str, Any]:
    """What a run of the judge came to. ``unmeasured`` is counted apart and
    never folded into "not a duplicate"."""
    scored = [answers[(r["a"], r["b"])] for r in pairs if (r["a"], r["b"]) in answers]
    tokens = sum(int(a.get("input_tokens") or 0) for a in scored)
    return {
        "pairs": len(pairs),
        "judged": len(scored),
        "unmeasured": len(pairs) - len(scored),
        "levels": {str(level): sum(1 for a in scored if round(float(a["score"])) == level)
                   for level in (0, 1, 2)},
        "at": at,
        "queued": len(queue),
        "queued_under_gate": sum(1 for r in queue if not r["over_gate"]),
        "pairs_over_gate": sum(1 for r in pairs if r["jaccard"] >= JACCARD_GATE),
        "input_tokens": tokens,
        "usd": round(tokens * USD_PER_MTOK / 1e6, 4),
    }


def write_queue(vault_root: Path, payload: Mapping[str, Any]) -> Path:
    path = queue_path(vault_root)
    atomic_write_bytes(path, json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
    return path
