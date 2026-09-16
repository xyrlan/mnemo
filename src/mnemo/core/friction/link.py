"""The contradiction pass — which live rules a correction contradicts.

The judgement, and only the judgement. Given one correction and the rules
``friction.candidates.rank`` put in front of it, one model call names the
slugs the correction contradicts. This module writes no ledger row, retires
nothing and touches no page; the caller turns a :class:`LinkResult` into a
``FrictionRecord``.

**It never raises and never loses a correction.** No ``claude`` on PATH, a
timeout, a non-zero exit, prose where JSON was asked for, a JSON shape that is
not the schema — every one of them yields ``contradicts: []`` and
``link_basis: "none"``. A correction whose link could not be resolved is still
a correction, and the caller records it unlinked.

A slug the model returns that is not among the candidates is dropped here,
not trusted: the prompt says so, but the guarantee is this filter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from mnemo.core.extract.prompts.templates.contradiction import (
    CONTRADICTION_PROMPT,
    RELATION_CONTRADICTS,
    build_contradiction_prompt,
)
from mnemo.core.friction.candidates import Candidate, correction_text
from mnemo.core.friction.ledger import LINK_EXTRACTOR, LINK_EXTRACTOR_INJECTED, LINK_NONE

#: ``runner(prompt, system=...) -> str`` — the model's reply text. Injected so
#: tests never spawn a model; it may raise, and a raise degrades to ``none``.
Runner = Callable[..., Any]

_DEFAULT_MODEL = "claude-haiku-4-5"
_DEFAULT_TIMEOUT = 60


@dataclass(frozen=True)
class LinkResult:
    """What the pass decided about one correction."""

    #: Candidate slugs the correction contradicts, in the model's order,
    #: deduplicated. Empty when nothing was contradicted or the pass failed.
    contradicts: list[str] = field(default_factory=list)
    #: ``ledger.LINK_EXTRACTOR``, ``LINK_EXTRACTOR_INJECTED`` or ``LINK_NONE``.
    link_basis: str = LINK_NONE
    #: The parsed model reply as returned, before filtering — or
    #: ``{"error": ...}`` when there was no usable reply. Kept so a report
    #: can show what the model said about refinements and dropped slugs.
    raw: dict = field(default_factory=dict)


def resolve(
    correction: Any,
    candidates: list[Candidate],
    *,
    runner: Runner | None = None,
    injected: Iterable[str] | None = None,
) -> LinkResult:
    """Run the contradiction pass for ``correction`` over ``candidates``.

    ``correction`` is a ``corrections.Correction`` or a ``FrictionRecord``
    (see ``candidates.correction_text``).

    ``injected`` is the slugs the reflex injected into the correction's
    session; the caller reads them, this module does not. When omitted, a
    ``FrictionRecord``'s own ``injected_in_session`` is used. A contradicted
    slug that was also injected makes the basis ``extractor+injected`` —
    corroboration is recorded, never required.

    No candidates means nothing can be contradicted, so no model is called.
    ``runner`` defaults to the ``claude --print`` path extraction uses
    (``core.llm.call``), which runs the helper under ``MNEMO_HOOKS_OFF=1``.
    """
    quote, rule = correction_text(correction)
    if not candidates or not quote.strip():
        return LinkResult(raw={"error": "no candidates" if quote.strip() else "empty quote"})

    prompt = build_contradiction_prompt(quote, rule, candidates)
    try:
        reply = (runner or _default_runner)(prompt, system=CONTRADICTION_PROMPT)
        payload = _as_payload(reply)
    except Exception as exc:  # the pass is best-effort; the correction is not
        return LinkResult(raw={"error": "{}: {}".format(type(exc).__name__, exc)})

    links = payload.get("links")
    if not isinstance(links, list):
        return LinkResult(raw={"error": "response has no 'links' list", "response": payload})

    known = {c.slug for c in candidates}
    contradicts: list[str] = []
    for link in links:
        if not isinstance(link, dict):
            continue
        slug = link.get("slug")
        relation = link.get("relation")
        if not isinstance(slug, str) or not isinstance(relation, str):
            continue
        slug = slug.strip()
        if relation.strip().lower() != RELATION_CONTRADICTS:
            continue
        if slug not in known or slug in contradicts:
            continue
        contradicts.append(slug)

    if not contradicts:
        return LinkResult(raw=payload)
    if injected is None:
        injected = getattr(correction, "injected_in_session", None) or ()
    injected_set = {s for s in injected if isinstance(s, str)}
    basis = LINK_EXTRACTOR_INJECTED if injected_set.intersection(contradicts) else LINK_EXTRACTOR
    return LinkResult(contradicts=contradicts, link_basis=basis, raw=payload)


def _as_payload(reply: Any) -> dict:
    """The reply as a JSON object, or raise.

    A runner may hand back the model's text or an already-parsed object;
    anything else, and text that holds no JSON object, is malformed.
    """
    from mnemo.core import llm

    if isinstance(reply, dict):
        return reply
    text = getattr(reply, "text", reply)
    if not isinstance(text, str):
        raise TypeError("runner returned {}, not text".format(type(reply).__name__))
    return llm._parse_llm_json(text)


def _default_runner(prompt: str, *, system: str) -> str:
    """One ``claude --print`` call, configured the way extraction is."""
    from mnemo.core import llm

    model, timeout = _default_model()
    return llm.call(prompt, system=system, model=model, timeout=timeout).text


def _default_model() -> tuple[str, int]:
    try:
        from mnemo.core.config import load_config

        extraction = load_config().get("extraction") or {}
        model = extraction.get("model") or _DEFAULT_MODEL
        timeout = int(extraction.get("subprocessTimeout") or _DEFAULT_TIMEOUT)
        return str(model), timeout
    except Exception:
        return _DEFAULT_MODEL, _DEFAULT_TIMEOUT


__all__ = ["LinkResult", "Runner", "resolve"]
