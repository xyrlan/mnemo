"""Opt-in marking and rerank of a ``list_rules_by_topic`` result by a judge that reads the pair (#401, #404).

**Off by default, and the only part of recall that can leave the machine.**
With ``recall.rerank.provider`` set, every ``list_rules_by_topic`` call that
carries a ``query`` posts that query and the first 800 characters of each
rule in the bucket to the provider — a third party. Nothing else is sent, and
nothing is sent on the prompt path (reflex), by ``mnemo recall`` or by any
hook: :func:`apply` has one caller, the MCP server.

Why it exists, and why it *marks* before it reorders (#404). The list an
agent is handed is 15 rules of which three quarters are about something else,
and the agent picks from slugs alone. Reordering that is worth little — on a
locked test split of 30 real queried calls the fused signal below moves
nDCG@5 by +0.081 [-0.024, +0.185], an interval that includes zero — because
most topics do not hold five rules worth reading in the first place. Used as
a filter the same signal is decisive: 15.0 rules per query become 2.3, the
irrelevant share falls from 75% to 13%, and all 24 rules the labels call
"should read" survive. So :func:`apply` writes ``relevant`` on each rule it
judged and leaves every rule in the list — the agent is told what to read
first, and a wrong mark costs one line of scrolling, not a rule.

``tools/measure_rerank_filter.py`` is that measurement, and it computes the
signal and the order with :func:`fuse` and :func:`ranked` from here, so what
was measured is what ships. It asks :func:`question` of :func:`rule_text`,
the same two this module sends.

The key is resolved by :func:`resolve_key`: the environment variable
``keyEnv`` names, then :mod:`mnemo.core.secrets` — because Claude Code spawns
this server and an ``export`` in a shell profile reaches it only when
``claude`` was started from that shell (#406). ``mnemo rerank --setup`` writes
the second source; with the provider at ``none`` neither is ever read.

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

#: #404: the signal is the judge's probability with BM25F as a tie-breaker,
#: and a rule at or over ``relevantAt`` is marked worth reading. Both were
#: fixed on a dev split of real queried calls and measured once on a locked
#: test split (``tools/measure_rerank_filter.py``).
DEFAULT_BM25_WEIGHT = 0.5
DEFAULT_RELEVANT_AT = 0.69

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


def _number(value: Any, fallback: float) -> float:
    """A configured number, or the default. An explicit ``0`` is a value, not a
    missing one, and a string nobody meant must not move a threshold."""
    if value is None or isinstance(value, bool):
        return fallback
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


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
        "bm25Weight": _number(block.get("bm25Weight"), DEFAULT_BM25_WEIGHT),
        "relevantAt": _number(block.get("relevantAt"), DEFAULT_RELEVANT_AT),
    }


#: Where a key was found, in the order they are tried. ``"none"`` is an
#: answer, not a failure: the stage falls back to the BM25F order.
KEY_SOURCES = ("env", "secrets", "none")


def resolve_key(chosen: Mapping[str, Any]) -> Tuple[Optional[str], str]:
    """The provider's key and where it came from, environment first (#406).

    The environment variable keeps priority: a key exported for one shell
    session must still beat what is stored on the machine, so a maintainer can
    try a second key without editing anything. The secrets file exists because
    the process that reads this is the MCP server, which Claude Code spawns —
    it inherits the shell's environment only when ``claude`` was started from
    that shell, and never when the desktop app was opened from the Dock.

    Callers that must not see the key use :func:`key_source`.
    """
    from mnemo.core import secrets

    key = os.environ.get(str(chosen.get("keyEnv") or DEFAULT_KEY_ENV))
    if key:
        return key, "env"
    try:
        stored = secrets.read(str(chosen.get("provider") or ""))
    except Exception:  # noqa: BLE001 — a secrets file must never break a tool call
        stored = None
    if stored:
        return stored, "secrets"
    return None, "none"


def key_source(chosen: Mapping[str, Any]) -> str:
    """Which of :data:`KEY_SOURCES` would answer, without handing the key over.

    ``mnemo rerank``, ``mnemo status`` and ``mnemo doctor`` all report where
    the key comes from and none of them may hold one, so the only value that
    leaves here is the label.
    """
    return resolve_key(chosen)[1]


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


def fuse(judged: Mapping[str, float], bm25f: Mapping[str, float], *,
         weight: float = DEFAULT_BM25_WEIGHT) -> Dict[str, float]:
    """``noul + weight * bm25f / max(bm25f here)``, for every rule the judge scored.

    The judge's number is the signal; BM25F only separates the rules it puts
    close together, which is why it is normalised by the best score *in this
    list* rather than by an absolute scale. The term is 0 when there is no
    reflex index, when the query tokenizes to nothing, or when nothing scored
    — then the signal is the judge's probability alone.
    """
    top = max(bm25f.values()) if bm25f else 0.0
    if not top > 0:
        return {slug: float(value) for slug, value in judged.items()}
    return {slug: float(value) + weight * float(bm25f.get(slug, 0.0)) / top
            for slug, value in judged.items()}


def marks(signal: Mapping[str, float], at: float) -> Dict[str, bool]:
    """Which judged rules are worth reading. A rule that is not a key here was
    not judged, which is not the same answer as ``False``."""
    return {slug: value >= at for slug, value in signal.items()}


def ranked(slugs: Sequence[str], signal: Mapping[str, float]) -> List[str]:
    """Judged slugs first, best first, ties in the incoming order; a slug the
    judge never scored keeps its place below every slug it did."""
    position = {slug: i for i, slug in enumerate(slugs)}
    return sorted(slugs, key=lambda s: (
        -signal[s] if s in signal else float("inf"), position[s]))


def order(matches: Sequence[Any], signal: Mapping[str, float]) -> List[Any]:
    """:func:`ranked`, applied to a list of ``RuleRef``."""
    place = {slug: i for i, slug in enumerate(ranked([m["slug"] for m in matches], signal))}
    return sorted(matches, key=lambda m: place[m["slug"]])


def bm25f_scores(vault_root: Path, query: str, slugs: Sequence[str]) -> Dict[str, float]:
    """BM25F over the rules the judge is sent, the way ``tools._rerank_by_query``
    gets them — and ``{}`` on every gap that one degrades on."""
    from mnemo.core.reflex import bm25
    from mnemo.core.reflex import index as reflex_index
    from mnemo.core.reflex.tokenizer import tokenize_query

    index = reflex_index.load_index(vault_root)
    if index is None:
        return {}
    tokens = tokenize_query(query)
    if not tokens:
        return {}
    return dict(bm25.score_docs(index, query_tokens=tokens,
                                candidate_slugs=list(slugs)) or [])


def apply(vault_root: Path, matches: List[Any], query: Optional[str], *,
          project: Optional[str], cfg: Optional[Mapping[str, Any]],
          client: Optional[Client] = None) -> Tuple[List[Any], Optional[Dict[str, Any]]]:
    """Mark and reorder ``matches`` when a provider is configured; otherwise hand
    them back untouched, with no ``relevant`` key on anything.

    Nothing is ever dropped. Every rule the judge scored gets ``relevant``
    (:func:`marks`), the judged rules move to the front in :func:`fuse` order,
    and a rule the judge never saw — one past ``maxRules``, one with an empty
    body, one the judge skipped — carries no ``relevant`` key at all: absent
    means "not judged", never "irrelevant".

    An empty relevant set is an answer, not a failure: 11 of 30 real queries
    had nothing in their topic about the task, and none of those 11 held a
    rule the labels call "should read". The status is ``ok``.

    Returns the list and what happened — ``None`` when the stage is off (the
    default, and then nothing here ran), else ``{"provider", "status",
    "judged", "relevant"}`` with status ``ok``, ``no_query``, ``small``,
    ``no_key`` or ``error``.
    """
    chosen = settings(cfg)
    if chosen["provider"] == "none":
        return matches, None
    info: Dict[str, Any] = {"provider": chosen["provider"], "status": "ok",
                            "judged": 0, "relevant": 0}
    if not query:
        return matches, dict(info, status="no_query")
    if len(matches) < 2:
        return matches, dict(info, status="small")
    if client is None:
        key, _source = resolve_key(chosen)
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
        judged = scores(query, texts, [m["slug"] for m in head], client)
    except Exception:
        return matches, dict(info, status="error")
    if not judged:
        return matches, dict(info, status="error")
    # Local, and after the judge: a broken index costs the tie-break, not the
    # answer already paid for.
    try:
        local = bm25f_scores(vault_root, query, [m["slug"] for m in head])
    except Exception:
        local = {}
    signal = fuse(judged, local, weight=chosen["bm25Weight"])
    flags = marks(signal, chosen["relevantAt"])
    for match in head:
        if match["slug"] in flags:
            match["relevant"] = flags[match["slug"]]
    # Same object back, reordered: a ``RuleRefs`` keeps what it withheld.
    matches[:] = order(head, signal) + list(matches[chosen["maxRules"]:])
    return matches, dict(info, judged=len(judged), relevant=sum(flags.values()))
