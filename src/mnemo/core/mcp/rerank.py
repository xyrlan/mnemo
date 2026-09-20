"""Opt-in rerank of a ``list_rules_by_topic`` result by a judge that reads the pair (#401).

**Off by default, and the only part of recall that can leave the machine.**
With ``recall.rerank.provider`` set, every ``list_rules_by_topic`` call that
carries a ``query`` posts that query and the first 800 characters of each
rule in the bucket to the provider — a third party. Nothing else is sent, and
nothing is sent on the prompt path (reflex), by ``mnemo recall`` or by any
hook: :func:`apply` has one caller, the MCP server.

Why it exists: BM25F is on a plateau no lexical knob moves (23 variants, none
with a CI excluding zero), and of the rules a judge marks "should read" that
sit outside the top 5, 20 of 22 are scored and outranked rather than missed.
A judge that reads the (task, rule) pair orders them better under labels it
did not make. ``tools/measure_rerank_judges.py`` is the number; it asks
:func:`question` of :func:`rule_text`, the same two this module sends, so the
measured ordering is the shipped one.

Every gap degrades to the order that came in — no provider, no key, a bucket
too small to reorder, a timeout, an HTTP error, a malformed answer. A
``query`` must never break the tool, and a provider being down must not
either. The second return value says which of those happened; the server
writes it to the access log so a silent fallback is still countable.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"

#: Pinned, never an alias: an ordering measured on one model says nothing
#: about the next one.
DEFAULT_MODEL = "jev-1.13.0"
DEFAULT_KEY_ENV = "TYPESAFE_API_KEY"
DEFAULT_TIMEOUT_S = 4.0

#: One request per list call. 65 bodies of 800 characters fit the provider's
#: 64k-token request; a larger bucket sends its first ``maxRules`` in BM25F
#: order and leaves the tail where it was.
DEFAULT_MAX_RULES = 64

BODY_CHARS = 800
GRAPH_SECTION = "<!-- mnemo:graph-section -->"

PROVIDERS = ("none", "typesafe")

#: ``client(state, questions) -> response``; raises on any failure.
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


def settings(cfg: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """The ``recall.rerank`` block with its defaults. An unknown provider is
    ``none``: a typo must not start sending rule bodies somewhere."""
    block = ((cfg or {}).get("recall") or {}).get("rerank") or {}
    provider = str(block.get("provider") or "none").lower()
    return {
        "provider": provider if provider in PROVIDERS else "none",
        "model": str(block.get("model") or DEFAULT_MODEL),
        "keyEnv": str(block.get("keyEnv") or DEFAULT_KEY_ENV),
        "timeoutSeconds": float(block.get("timeoutSeconds") or DEFAULT_TIMEOUT_S),
        "maxRules": int(block.get("maxRules") or DEFAULT_MAX_RULES),
    }


def typesafe_client(key: str, *, model: str, timeout: float) -> Client:
    def ask(state: Dict[str, Any], questions: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"state": state, "model": model, "questions": questions})
        request = urllib.request.Request(TYPESAFE_URL, data=body.encode("utf-8"), headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": "mnemo-recall-rerank",
        })
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    return ask


def scores(query: str, texts: Mapping[str, str], slugs: Sequence[str], client: Client) -> Dict[str, float]:
    """The judge's probability for every rule it answered for, in one request.

    A rule with no text is not asked about, and a rule the judge skipped is
    left out — absent means "not judged", never "irrelevant".
    """
    asked = [s for s in slugs if texts.get(s)]
    if not asked:
        return {}
    out = client({"developer_task": query},
                 {"r%d" % i: question(texts[s]) for i, s in enumerate(asked)})
    answers = out.get("answers") or {}
    found: Dict[str, float] = {}
    for i, slug in enumerate(asked):
        value = (answers.get("r%d" % i) or {}).get("noul")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            found[slug] = float(value)
    return found


def order(matches: Sequence[Any], signal: Mapping[str, float]) -> List[Any]:
    """Judged rules first, best first, ties in the incoming order; a rule the
    judge never scored keeps its place below every rule it did."""
    position = {m["slug"]: i for i, m in enumerate(matches)}
    return sorted(matches, key=lambda m: (
        -signal[m["slug"]] if m["slug"] in signal else float("inf"), position[m["slug"]]))


def apply(vault_root: Path, matches: List[Any], query: Optional[str], *,
          project: Optional[str], cfg: Optional[Mapping[str, Any]],
          client: Optional[Client] = None) -> Tuple[List[Any], Optional[Dict[str, Any]]]:
    """Reorder ``matches`` when a provider is configured; otherwise hand them back.

    Returns the list and what happened — ``None`` when the stage is off (the
    default, and then nothing here ran), else ``{"provider", "status",
    "judged"}`` with status ``ok``, ``no_query``, ``small``, ``no_key`` or
    ``error``.
    """
    chosen = settings(cfg)
    if chosen["provider"] == "none":
        return matches, None
    info: Dict[str, Any] = {"provider": chosen["provider"], "status": "ok", "judged": 0}
    if not query:
        return matches, dict(info, status="no_query")
    if len(matches) < 2:
        return matches, dict(info, status="small")
    if client is None:
        key = os.environ.get(chosen["keyEnv"])
        if not key:
            return matches, dict(info, status="no_key")
        client = typesafe_client(key, model=chosen["model"], timeout=chosen["timeoutSeconds"])

    from mnemo.core.mcp import tools

    head = list(matches[:chosen["maxRules"]])
    try:
        texts = {}
        for match in head:
            page = tools.read_mnemo_rule(vault_root, match["slug"], project=project) or {}
            texts[match["slug"]] = rule_text(page.get("body") or "")
        signal = scores(query, texts, [m["slug"] for m in head], client)
    except Exception:
        return matches, dict(info, status="error")
    if not signal:
        return matches, dict(info, status="error")
    # Same object back, reordered: a ``RuleRefs`` keeps what it withheld.
    matches[:] = order(head, signal) + list(matches[chosen["maxRules"]:])
    return matches, dict(info, judged=len(signal))
