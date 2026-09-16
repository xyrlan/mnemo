"""System prompt and response schema for the contradiction pass.

One correction — the user's verbatim words and the rule the briefing derived
from them — is set against a ranked list of the vault's live rules, and the
model names the ones the correction *contradicts*. Its own pass, not a
question folded into the consolidation prompt: the consolidation prompt's
``existing_rules`` hint is a ``source_count``-ordered slice of the vault, and
a contradiction asked against that slice would produce a ledger whose silence
means nothing (see ``docs/superpowers/specs/2026-09-16-friction-ledger-design.md``).
The consolidation prompt is untouched by this module.

The response is a list of *links*, each classified, rather than a bare list
of slugs. Asking for a relation per link makes the model commit to
contradicts / refines / unrelated out loud, and the caller keeps only
``contradicts``. A refinement recorded as a contradiction would retire a rule
that is still right, so the distinction is carried in structure, not trusted
to the model's restraint.
"""
from __future__ import annotations

from typing import Any

RELATION_CONTRADICTS = "contradicts"
RELATION_REFINES = "refines"
RELATION_UNRELATED = "unrelated"
RELATIONS = (RELATION_CONTRADICTS, RELATION_REFINES, RELATION_UNRELATED)

#: Per-candidate body cap in the user message. Forty full bodies can run long;
#: the head of a rule carries its imperative, which is what a contradiction is
#: judged against.
MAX_BODY_CHARS = 1200

#: The shape the model is asked to return. Documentation for the prompt and
#: for readers — the caller parses defensively and does not validate against it.
CONTRADICTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["links"],
    "properties": {
        "links": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["slug", "relation"],
                "properties": {
                    "slug": {"type": "string"},
                    "relation": {"type": "string", "enum": list(RELATIONS)},
                    "why": {"type": "string"},
                },
            },
        },
    },
}


CONTRADICTION_PROMPT = (
    "You are checking one correction a user gave an AI coding assistant against "
    "the rules a memory vault already holds. The vault may be holding a rule the "
    "user has just told the assistant is wrong. Your job is to find it, and only "
    "it.\n\n"
    "You receive:\n"
    "- QUOTE — the user's verbatim words. Often Portuguese; the rules are usually "
    "English. Judge meaning, not vocabulary.\n"
    "- RULE — the imperative a previous step derived from that quote.\n"
    "- CANDIDATES — live vault rules, each with a slug, a name and its body.\n\n"
    "For each candidate the correction bears on, decide its relation:\n"
    "- contradicts — following the candidate would now be wrong: it prescribes "
    "the opposite action, or a fact it states is the one the user just "
    "corrected. Near-identical wording does not rule this out — 'merges need "
    "--admin' and 'run the merge directly, without --admin' share almost every "
    "word and contradict each other.\n"
    "- refines — the candidate stays right and the correction adds a condition, "
    "an exception, a detail or a stronger emphasis to it. The two can both be "
    "followed at once. This is NOT a contradiction.\n"
    "- unrelated — same topic or same words, but neither rule changes what the "
    "other asks for.\n\n"
    "Only contradicts counts. A rule marked contradicts may be retired from the "
    "vault, so when you are unsure whether a candidate is contradicted or "
    "refined, choose refines. Returning no contradiction is a correct and common "
    "answer; most corrections contradict nothing the vault holds.\n\n"
    "Use slugs exactly as written in CANDIDATES. A slug that is not in the list "
    "is discarded by the caller, so never invent one and never name a rule from "
    "memory.\n\n"
    "Respond with ONLY a JSON object, no prose and no code fence:\n"
    '{"links": [{"slug": "<candidate slug>", "relation": '
    '"contradicts" | "refines" | "unrelated", "why": "<one short sentence>"}]}\n'
    "List only candidates you judged contradicts or refines; unrelated ones may "
    "be omitted. With nothing to report, return {\"links\": []}.\n\n"
    "Example — a contradiction:\n"
    "QUOTE: \"não precisa de --admin, roda o merge direto\"\n"
    "RULE: Run gh pr merge directly; --admin is not required.\n"
    "CANDIDATE merge-requires-admin: Merges into master require --admin because "
    "of code-owner approval.\n"
    'Output: {"links": [{"slug": "merge-requires-admin", "relation": '
    '"contradicts", "why": "The rule requires --admin; the user says it is not '
    'needed."}]}\n\n'
    "Example — a refinement:\n"
    "QUOTE: \"sempre roda os testes com PYTHONPATH=src dentro de worktree\"\n"
    "RULE: In a worktree, run pytest with PYTHONPATH=src.\n"
    "CANDIDATE run-full-suite-before-push: Run the full test suite before "
    "pushing.\n"
    'Output: {"links": [{"slug": "run-full-suite-before-push", "relation": '
    '"refines", "why": "Still run the suite; the correction adds how."}]}\n'
)


def build_contradiction_prompt(quote: str, rule: str, candidates: list) -> str:
    """The user message: the correction, then every candidate with its body.

    ``candidates`` is a list of objects with ``slug``, ``name`` and ``body``
    (``friction.candidates.Candidate``). Order is kept — it is the ranking —
    and each body is cut at :data:`MAX_BODY_CHARS`.
    """
    lines = [
        "QUOTE: " + _one_line(quote),
        "RULE: " + _one_line(rule),
        "",
        "CANDIDATES:",
    ]
    for cand in candidates:
        body = (getattr(cand, "body", "") or "").strip()
        if len(body) > MAX_BODY_CHARS:
            body = body[:MAX_BODY_CHARS].rstrip() + " …"
        lines.append("")
        lines.append("### {} — {}".format(cand.slug, _one_line(getattr(cand, "name", "") or cand.slug)))
        lines.append(body)
    return "\n".join(lines) + "\n"


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


__all__ = [
    "CONTRADICTION_PROMPT",
    "CONTRADICTION_SCHEMA",
    "MAX_BODY_CHARS",
    "RELATIONS",
    "RELATION_CONTRADICTS",
    "RELATION_REFINES",
    "RELATION_UNRELATED",
    "build_contradiction_prompt",
]
