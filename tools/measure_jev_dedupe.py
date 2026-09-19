"""Can a calibrated judge see the synonym duplicates Jaccard cannot (#187)?

Usage:
    PYTHONPATH=src python3 tools/measure_jev_dedupe.py --project mnemo --topic measurement
    PYTHONPATH=src python3 tools/measure_jev_dedupe.py --project mnemo --topic measurement --send [--list 8] [--json]

**Without ``--send`` nothing leaves the machine.** The default run counts the
pairs, estimates the tokens and the cost, and prints the Jaccard side alone.
``--send`` posts every pair of rule bodies in the bucket to TypeSafe's
``/v1/systemone`` endpoint, a third party, and needs ``TYPESAFE_API_KEY``.
mnemo's README says "100% local"; this tool is a measurement and is the only
thing in the repo that would break that sentence, which is why it is opt-in
per run and lives in ``tools/``, not ``src/``.

#187 measured why the dedupe body gate misses real duplicates: the gate is
Jaccard >= 0.6 on word tokens, a real duplicate scored 0.136, and the p90 of
79,800 unrelated pairs was 0.131. Token overlap cannot see two pages that say
one thing in different words, so no threshold separates them. This asks the
other question: given the same pairs, does a model built for calibrated
judgments put the duplicates at the top?

One ``score`` question per pair, three ordered levels, so the answer is the
decision and not a number to tune: 0 a developer needs both, 1 related, 2 one
can be deleted with no loss. Each row carries the pair's Jaccard ratio and
its **rank by Jaccard** among the bucket's pairs. A pair the judge calls a
duplicate that Jaccard ranks 17th of 325 is the finding; a pair both put
first is not.

What this does not give: precision or recall. There is no labelled set of
duplicates in a vault, so ``--list`` prints the top pairs with both openings
for a person to read. A count from this tool is a queue for a curator, never
a merge list.

On a python.org macOS build ``urllib`` has no CA bundle and the call fails
with ``CERTIFICATE_VERIFY_FAILED``; ``SSL_CERT_FILE=/etc/ssl/cert.pem`` fixes
it. Do not disable verification.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

URL = "https://api.typesafe.ai/v1/systemone"

#: Pinned, never ``jev-latest``: an alias that moves would move every score
#: under a number somebody wrote down.
MODEL = "jev-1.13.0"

#: USD per million input tokens; output is free (docs.typesafe.ai/models,
#: read 2026-09-19).
USD_PER_MTOK = 0.042

#: The gate the inbox dedupe applies today (``dedup._bodies_similar``).
JACCARD_GATE = 0.6

#: Halfway between "related" and "same lesson": rounds to a duplicate.
DUPLICATE_SCORE = 1.5

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

Client = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


def jaccard(a: str, b: str) -> float:
    """The ratio ``dedup._bodies_similar`` thresholds, same tokenisation."""
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def pairs_of(bodies: Dict[str, str]) -> List[Tuple[str, str]]:
    """Every unordered pair of slugs with a body; an empty body judges nothing."""
    return list(itertools.combinations(sorted(s for s, b in bodies.items() if b), 2))


def estimate(bodies: Dict[str, str]) -> Dict[str, Any]:
    """Pairs, tokens and cost of a ``--send``, from characters / 4.

    A rough bound so the price is known before the call, not a bill: the
    question's own text rides along with every pair and is counted too.
    """
    pairs = pairs_of(bodies)
    overhead = len(json.dumps(QUESTION))
    chars = sum(len(bodies[a]) + len(bodies[b]) + overhead for a, b in pairs)
    tokens = chars // 4
    return {"pairs": len(pairs), "tokens": tokens,
            "usd": round(tokens * USD_PER_MTOK / 1e6, 4)}


def http_client(key: str, *, model: str = MODEL, timeout: float = 60.0) -> Client:
    def ask(state: Dict[str, Any], questions: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"state": state, "model": model, "questions": questions})
        request = urllib.request.Request(URL, data=body.encode("utf-8"), headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "mnemo-measure-jev-dedupe",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return {"error": exc.code}
    return ask


def judge(bodies: Dict[str, str], client: Client, *, workers: int = 8) -> List[Dict[str, Any]]:
    """One row per pair: the judge's score next to Jaccard and its rank.

    A pair the client could not answer keeps its Jaccard columns and carries
    ``score: None`` — "not measured", which the summary counts apart and never
    folds into "not a duplicate".
    """
    pairs = pairs_of(bodies)
    ratios = {p: jaccard(bodies[p[0]], bodies[p[1]]) for p in pairs}
    by_ratio = sorted(pairs, key=lambda p: (-ratios[p], p))
    rank = {p: i + 1 for i, p in enumerate(by_ratio)}

    def one(pair: Tuple[str, str]) -> Dict[str, Any]:
        out = client({"rule_a": bodies[pair[0]], "rule_b": bodies[pair[1]]}, QUESTION)
        answer = (out.get("answers") or {}).get("same") or {}
        return {
            "a": pair[0], "b": pair[1],
            "score": answer.get("score"),
            "confidence": answer.get("confidence"),
            "jaccard": round(ratios[pair], 4),
            "jaccard_rank": rank[pair],
            "input_tokens": (out.get("usage") or {}).get("input_tokens", 0),
        }

    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(one, pairs))


def summarize(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    measured = [r for r in rows if r["score"] is not None]
    duplicates = [r for r in measured if r["score"] >= DUPLICATE_SCORE]
    tokens = sum(r["input_tokens"] for r in rows)
    return {
        "pairs": len(rows),
        "unmeasured": len(rows) - len(measured),
        "levels": {str(level): sum(1 for r in measured if round(r["score"]) == level)
                   for level in (0, 1, 2)},
        "judged_duplicate": len(duplicates),
        # The point of the tool: duplicates the shipped gate would wave through.
        "judged_duplicate_under_gate": sum(1 for r in duplicates if r["jaccard"] < JACCARD_GATE),
        "pairs_over_gate": sum(1 for r in rows if r["jaccard"] >= JACCARD_GATE),
        "input_tokens": tokens,
        "usd": round(tokens * USD_PER_MTOK / 1e6, 4),
    }


def format_report(summary: Dict[str, Any], rows: List[Dict[str, Any]],
                  bodies: Dict[str, str], *, listing: int = 0) -> str:
    lines = [
        f"{summary['pairs']} pairs, {summary['unmeasured']} unmeasured, "
        f"{summary['input_tokens']} input tokens (${summary['usd']})",
        "levels  different {0}  related {1}  same {2}".format(
            *(summary["levels"][k] for k in ("0", "1", "2"))),
        f"judged duplicate (score >= {DUPLICATE_SCORE}): {summary['judged_duplicate']}, "
        f"of which under the Jaccard gate ({JACCARD_GATE}): "
        f"{summary['judged_duplicate_under_gate']}",
        f"pairs the Jaccard gate itself catches: {summary['pairs_over_gate']}",
    ]
    ranked = sorted((r for r in rows if r["score"] is not None),
                    key=lambda r: (-r["score"], r["jaccard_rank"]))
    for r in ranked[:listing]:
        lines.append("")
        lines.append(f"[{r['score']:.2f} conf {r['confidence'] or 0:.2f}  jaccard "
                     f"{r['jaccard']:.3f} rank {r['jaccard_rank']}/{summary['pairs']}]  "
                     f"{r['a']}  <>  {r['b']}")
        for slug in (r["a"], r["b"]):
            lines.append("    " + " ".join(bodies[slug].split())[:200])
    return "\n".join(lines)


def load_bodies(project: str, topic: str, max_chars: int) -> Dict[str, str]:
    from mnemo import cli
    from mnemo.core.mcp import tools

    vault = cli._resolve_vault()
    bodies = {}
    for rule in tools.list_rules_by_topic(vault, topic, scope="project", project=project):
        page = tools.read_mnemo_rule(vault, rule["slug"], project=project) or {}
        bodies[rule["slug"]] = " ".join((page.get("body") or "").split())[:max_chars]
    return bodies


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument("--topic", required=True)
    parser.add_argument("--max-chars", type=int, default=1200,
                        help="body prefix sent per rule (default 1200)")
    parser.add_argument("--send", action="store_true",
                        help="post the rule bodies to TypeSafe (third party)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--list", type=int, default=0, metavar="N", dest="listing",
                        help="print the top N pairs with both openings")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    bodies = load_bodies(args.project, args.topic, args.max_chars)
    if not args.send:
        cost = estimate(bodies)
        over = sum(1 for a, b in pairs_of(bodies) if jaccard(bodies[a], bodies[b]) >= JACCARD_GATE)
        print(f"dry run: {len(bodies)} rules, {cost['pairs']} pairs, ~{cost['tokens']} tokens "
              f"(~${cost['usd']}); Jaccard gate catches {over}. "
              f"--send posts the bodies to {URL}")
        return 0

    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        print("error: --send needs TYPESAFE_API_KEY", file=sys.stderr)
        return 1
    rows = judge(bodies, http_client(key), workers=args.workers)
    summary = summarize(rows)
    if args.json:
        print(json.dumps({"summary": summary, "rows": rows}, indent=2, ensure_ascii=False))
    else:
        print(format_report(summary, rows, bodies, listing=args.listing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
