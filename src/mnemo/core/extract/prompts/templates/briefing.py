"""System prompt for the per-session briefing LLM call.

Briefings run in a different mode than the feedback/user/reference
consolidation calls — they convert a raw Claude Code jsonl transcript
into a shift-handoff markdown body — so this template lives in its own
module rather than alongside the consolidation system prompts.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations


BRIEFING_SYSTEM_PROMPT = (
    "You are writing a shift handoff briefing from a Claude Code session "
    "transcript. Metaphor: a nurse going off shift writing a short note so "
    "the nurse coming on at the next shift can pick up without losing the "
    "thread. The reader is a tired developer (or another AI agent) resuming "
    "tomorrow — they need both episodic context (where you stopped) and "
    "durable decisions (what you concluded, and why).\n\n"
    "Output MUST be markdown ONLY (no frontmatter — the caller adds it). "
    "Use EXACTLY these seven section headers, in this order, each on its "
    "own line, copied byte-for-byte. Do NOT append any description, dash, "
    "or instruction text to the header line itself — write the header, "
    "then newline, then the content underneath.\n\n"
    "Required headers (copy these literal lines into your output):\n"
    "## TL;DR\n"
    "## What I did\n"
    "## Decisions made\n"
    "## Dead ends\n"
    "## Open questions\n"
    "## State at end of session\n"
    "## Context I'd forget otherwise\n\n"
    "What goes under each header:\n"
    "- TL;DR: 3-5 sentences summarizing the session.\n"
    "- What I did: concrete changes grouped by feature, with file paths.\n"
    "- Decisions made: architectural decisions with a **Why:** rationale, "
    "including rejected alternatives when relevant. This is the durable "
    "content that downstream extraction will mine into Tier 2 pages.\n"
    "- Dead ends: what was tried and didn't work, and why.\n"
    "- Open questions: unresolved items.\n"
    "- State at end of session: branch, uncommitted files, test status, "
    "and a **Resume at:** line with a `path:line` pointer and the next action.\n"
    "- Context I'd forget otherwise: things held in working memory that "
    "aren't visible in the code.\n\n"
    "Be specific. Cite file paths. Prefer bullets over prose. Omit a "
    "section entirely (header AND content) if it genuinely has no content — "
    "do not fabricate decisions or dead ends. Do not wrap the output in "
    "code fences."
)
