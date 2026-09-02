"""Prompt rendering — unified consolidation builder + briefing builder.

Three near-identical ``build_{feedback,user,reference}_prompt`` functions
were unified in v0.9 PR F2 into a single ``build_consolidation_prompt``
that dispatches on a ``kind -> (label, cluster_clause, few_shot)`` table.
The legacy three are kept as thin wrappers so existing call-sites
(``mnemo.core.extract.__init__`` and the prompts shim) keep working.

``build_briefing_prompt`` also lives here. Its signature changed in PR F2:
it now accepts a pre-flattened ``transcript: str`` rather than
``events: list[dict]``. The events-to-transcript flattening lives in
``mnemo.core.transcript`` (SRP fix — event parsing is not prompt
composition).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from mnemo.core.extract.prompts.encoding import _render_files
from mnemo.core.extract.prompts.existing_rules import existing_rules_fragment
from mnemo.core.extract.prompts.templates.briefing import BRIEFING_SYSTEM_PROMPT  # noqa: F401  (re-export anchor for the package shim)
from mnemo.core.extract.prompts.templates.few_shot_feedback import _FEW_SHOT_FEEDBACK
from mnemo.core.extract.prompts.templates.few_shot_simple import (
    _FEW_SHOT_REFERENCE,
    _FEW_SHOT_USER,
)
from mnemo.core.extract.prompts.templates.harvest import HARVEST_SYSTEM_PROMPT  # noqa: F401  (re-export anchor for the package shim)
from mnemo.core.extract.prompts.templates.schema import _SCHEMA_EXAMPLE
from mnemo.core.extract.prompts.vault_tags import _existing_tags_fragment
from mnemo.core.extract.scanner import MemoryFile

ConsolidationKind = Literal["feedback", "user", "reference"]

# kind -> (task_label, cluster_clause, few_shot)
# task_label is interpolated into "consolidate these {LABEL} memory files"
# cluster_clause completes the "Cluster files ..." sentence in the prompt
# preamble. Few-shot is the calibration example bank for that mode.
_CONSOLIDATION_TABLE: dict[ConsolidationKind, tuple[str, str, str]] = {
    "feedback": (
        "FEEDBACK",
        "Cluster files that express the same conceptual rule.",
        _FEW_SHOT_FEEDBACK,
    ),
    "user": (
        "USER-profile",
        "Cluster files describing the same user trait.",
        _FEW_SHOT_USER,
    ),
    "reference": (
        "REFERENCE",
        "Cluster files that point to the same external resource.",
        _FEW_SHOT_REFERENCE,
    ),
}


def build_consolidation_prompt(
    kind: ConsolidationKind,
    files: list[MemoryFile],
    *,
    vault_root: Path | None = None,
) -> str:
    """Render the user-message consolidation prompt for one cluster type.

    The three legacy ``build_*_prompt`` wrappers below dispatch into this
    builder; new code should call this directly.
    """
    label, cluster_clause, few_shot = _CONSOLIDATION_TABLE[kind]
    agents = {f.agent for f in files if getattr(f, "agent", None)}
    return (
        f"Task: consolidate these {label} memory files into canonical Tier 2 "
        f"pages. {cluster_clause}\n\n"
        f"{_existing_tags_fragment(vault_root, kind)}"
        f"{existing_rules_fragment(vault_root, kind, agents=agents)}"
        f"{_SCHEMA_EXAMPLE}\n"
        f"{few_shot}\n"
        "Now consolidate these input files:\n\n"
        f"{_render_files(files)}\n"
        "Respond with JSON only."
    )


# Thin wrappers preserve existing call-sites (prompts.build_feedback_prompt,
# prompts.build_user_prompt, prompts.build_reference_prompt) and the
# extraction loop's ``(kind, builder, system_prompt)`` triple table. New
# code should prefer ``build_consolidation_prompt``.
def build_feedback_prompt(
    files: list[MemoryFile],
    *,
    vault_root: Path | None = None,
) -> str:
    return build_consolidation_prompt("feedback", files, vault_root=vault_root)


def build_user_prompt(
    files: list[MemoryFile],
    *,
    vault_root: Path | None = None,
) -> str:
    return build_consolidation_prompt("user", files, vault_root=vault_root)


def build_reference_prompt(
    files: list[MemoryFile],
    *,
    vault_root: Path | None = None,
) -> str:
    return build_consolidation_prompt("reference", files, vault_root=vault_root)


_USER_TURN_MAX_CHARS = 600


def _render_user_turn(index: int, turn: str) -> str:
    """One numbered turn, with any truncation marker kept off the quotable line.

    A quote is verified by substring match against the real turn, so the
    shown line must contain nothing the user did not type. An over-long
    turn is cut at a word boundary and the "continues" note goes on its
    own following line, where it cannot be quoted by mistake.
    """
    if len(turn) <= _USER_TURN_MAX_CHARS:
        return f"[{index}] {turn}"
    head = turn[:_USER_TURN_MAX_CHARS]
    cut = head.rstrip()
    space = cut.rfind(" ")
    if space > 0:
        cut = cut[:space].rstrip()
    if not cut:  # no whitespace to break on — fall back to a hard cut
        cut = head
    return (
        f"[{index}] {cut}\n"
        f"    (turn continues — {len(turn)} chars total; "
        "quote only the text shown above)"
    )


def build_briefing_prompt(transcript: str, *, user_turns: list[str] | None = None) -> str:
    """Render a briefing prompt from a pre-flattened transcript string.

    ``user_turns`` are the person's own messages, numbered, so the model can
    quote a correction verbatim and the caller can verify it did.

    SIGNATURE CHANGE in v0.9 PR F2: this function used to accept
    ``events: list[dict]`` and flattened them internally. Flattening is
    now a separate concern; see :func:`mnemo.core.transcript.flatten_transcript_events`.
    Callers wanting the old behaviour should compose the two:

        transcript = flatten_transcript_events(events)
        prompt = build_briefing_prompt(transcript, user_turns=user_turns(events))
    """
    turns_block = ""
    if user_turns:
        lines = [_render_user_turn(i, turn) for i, turn in enumerate(user_turns, 1)]
        turns_block = (
            "=== USER TURNS (verbatim, numbered — quote these for Corrections) ===\n"
            + "\n".join(lines)
            + "\n=== END USER TURNS ===\n\n"
        )
    return (
        "Task: write the shift handoff briefing markdown body for the "
        "following Claude Code session transcript. Follow the section "
        "structure from the system prompt exactly. Output markdown only, "
        "no frontmatter, no code fences.\n\n"
        f"{turns_block}"
        "=== TRANSCRIPT ===\n"
        f"{transcript}\n"
        "=== END TRANSCRIPT ===\n"
    )


def build_harvest_prompt(transcript: str) -> str:
    """Render a harvest prompt from a pre-flattened transcript string.

    Mirrors :func:`build_briefing_prompt` — same transcript delimiters — but
    asks for structured memory pages instead of a handoff narrative.
    """
    return (
        "Task: extract durable memory pages from the following archived "
        "Claude Code session transcript. Follow the JSON schema from the "
        "system prompt exactly. Output JSON only, no prose, no code fences.\n\n"
        "=== TRANSCRIPT ===\n"
        f"{transcript}\n"
        "=== END TRANSCRIPT ===\n"
    )
