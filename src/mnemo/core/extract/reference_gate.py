"""The bar a ``reference`` page clears before it goes live (#417).

The evidence gate (:mod:`mnemo.core.extract.evidence`) decides ``feedback``
pages by a checkable fact — a user quote found in a source briefing. A
``reference`` page has no such fact: it is what the model *inferred* from a
briefing, and until #417 a single-source one went straight into
``shared/reference/`` on nothing but the model's say-so. On the real vault
that door carried ~55 pages a day, and two blind raters put 40% of the live
reference+feedback stock as junk: generic aphorisms ("dependency injection
keeps boundaries clean") and narratives (a record of what one session did).

This module asks a second question of every such page before it is written:
what is it? One call per extraction chunk, all of that chunk's reference pages
judged together, each answered with one of :data:`CATEGORIES` — the same four
the raters used. Only :data:`KEEP` goes live; the rest stages in
``shared/_inbox/reference/`` for review, never dropped.

``tools/measure_reference_gate.py`` runs :func:`build_prompt` and
:func:`parse_verdicts` — these, not a copy — over the rater-labelled sample,
so the number in the PR is the number this stage produces.
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence

from mnemo.core import llm

#: The four categories the 2026-09-22 raters sorted rules into, worded for
#: the judge. The G and T lines were sharpened on the dev half of the sample
#: only; the test half was scored once, after (see the tool).
CATEGORIES = {
    "G": "generic: standard engineering practice an experienced engineer "
         "already knows and follows — validate before bulk changes, run cheap "
         "checks first, verify the output, filter by owner, keep state apart "
         "from the log. Examples, file names or the incident it was drawn "
         "from do not rescue it: strip them and ask whether the lesson itself "
         "is news",
    "T": "transferable technique: a specific, non-obvious fact about a tool, "
         "library, API or platform (a quirk, a pitfall with its cause, a "
         "command that works where the obvious one does not) that holds "
         "beyond this one system",
    "S": "system knowledge: how one particular system works — its components, "
         "data, conventions, decisions or incidents — that exists nowhere else",
    "N": "narrative: a record of what one session did or changed (UI tweaks "
         "shipped, a task list), not guidance anyone would reuse",
}

#: What goes live. G and N are what both raters called junk.
KEEP = frozenset({"T", "S"})

#: How much of a page the judge reads — the raters' view, name + body, cut
#: where the audit sample cut it.
VIEW_CHARS = 1500

SYSTEM_PROMPT = (
    "You grade entries in an engineering knowledge vault. Each entry was "
    "written by a model from one coding session. Classify every entry into "
    "exactly one category:\n"
    + "".join(f"- {k}: {v}\n" for k, v in CATEGORIES.items())
    + "\nJudge what the entry teaches, not how it is formatted: naming a file "
    "or a number does not make generic advice specific, and a **Why:** / "
    "**How to apply:** structure does not make a narrative into guidance.\n\n"
    "Output ONLY JSON: {\"verdicts\": [{\"i\": <entry number>, \"cat\": "
    "\"G|T|S|N\"}, ...]} with one verdict per entry. No prose."
)


def view(name: str, body: str) -> str:
    """Name and body on one line, the way the raters saw a page."""
    joined = "%s. %s" % (name.strip().rstrip("."), body.strip())
    return " ".join(joined.split())[:VIEW_CHARS]


def build_prompt(views: Sequence[str]) -> str:
    """The user message: the entries, numbered from 1."""
    lines = ["Entries:"]
    for i, text in enumerate(views, 1):
        lines.append(f"[{i}] {text}")
    return "\n\n".join(lines)


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def parse_verdicts(text: str, n: int) -> List[Optional[str]]:
    """One category per entry, ``None`` where the answer carried none.

    Lenient about the wrapper (a code fence, stray prose), strict about the
    content: a category outside :data:`CATEGORIES` or an index out of range
    is no answer at all, and the caller treats no answer as "not cleared".
    """
    out: List[Optional[str]] = [None] * n
    match = _JSON_RE.search(text or "")
    if not match:
        return out
    try:
        payload = json.loads(match.group(0))
    except ValueError:
        return out
    verdicts = payload.get("verdicts") if isinstance(payload, dict) else None
    if not isinstance(verdicts, list):
        return out
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        i, cat = v.get("i"), str(v.get("cat") or "").strip().upper()
        if isinstance(i, int) and not isinstance(i, bool) and 1 <= i <= n and cat in CATEGORIES:
            out[i - 1] = cat
    return out


def needs_judging(page) -> bool:
    """A reference page the model inferred, headed for the live directory.

    Pages the evidence gate already routed are left to it: a demoted feedback
    page stages anyway, and a quote-verified page is ``feedback`` by now.
    """
    return (
        page.type == "reference"
        and page.confidence != "verified"
        and not page.unverified_feedback
    )


Judge = Callable[[str], str]


def judge_pages(pages: list, ask: Judge) -> list:
    """Return *pages* with a ``judged`` category on every reference page.

    ``ask`` takes the user prompt and returns the model's text. Any failure
    to get an answer leaves the category ``""`` — not cleared, so the page
    stages: the reversible direction, the same one the evidence gate takes.
    """
    idx = [i for i, p in enumerate(pages) if needs_judging(p)]
    if not idx:
        return pages
    views = [view(pages[i].name, pages[i].body) for i in idx]
    try:
        cats = parse_verdicts(ask(build_prompt(views)), len(views))
    except (llm.LLMSubprocessError, llm.LLMParseError, OSError):
        cats = [None] * len(views)
    out = list(pages)
    for i, cat in zip(idx, cats):
        out[i] = replace(out[i], judged=cat or "")
    return out


def cleared(page) -> bool:
    """True when the judge let this page through, or never judged it."""
    judged = getattr(page, "judged", None)
    return judged is None or judged in KEEP


def held(page, vault_root: Path) -> bool:
    """True when a judged page must stage in ``shared/_inbox/`` this run.

    - a page already live in ``shared/<type>/`` is never held: the stock is
      not this gate's to change, and a re-emission reinforces it as before;
    - a page already staged stays staged whatever today's answer: the review
      queue owns it now, and re-deriving the verdict each run would let a
      second opinion walk it out of ``_inbox`` and leave the staged copy
      behind (the #177 shape);
    - otherwise the judge's category decides.
    """
    if getattr(page, "judged", None) is None:
        return False
    if (vault_root / "shared" / page.type / f"{page.slug}.md").exists():
        return False
    if (vault_root / "shared" / "_inbox" / page.type / f"{page.slug}.md").exists():
        return True
    return not cleared(page)


def counts(pages: list) -> Dict[str, int]:
    """How many pages the judge put in each category ("" = no answer)."""
    out: Dict[str, int] = {}
    for p in pages:
        judged = getattr(p, "judged", None)
        if judged is not None:
            out[judged] = out.get(judged, 0) + 1
    return out
