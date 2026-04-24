"""Frontmatter block parsing for ``enforce`` and ``activates_on``.

The v0.8 monolith had three companion helpers per block: a strict
``parse_*_block`` that returned ``None`` on any rejection, plus a
``_describe_*_error`` walker that re-walked the same validation steps to
produce a human-readable diagnostic. v0.9 PR G collapses both pairs into a
single :func:`parse_block` walker that returns ``(parsed, error_message)``,
with thin wrappers preserving the legacy ``parse_*_block`` callers.

ReDoS guards (``_pattern_is_redos_safe``, ``_REDOS_*``,
``_CATASTROPHIC_SUBSTRINGS``) live here because the timing probe is part
of the same per-pattern validation step.
"""
from __future__ import annotations

import re
import time
from typing import Literal

from mnemo.core.rule_activation.globs import _glob_to_regex

# Valid tool names for activates_on blocks.
_VALID_ENRICH_TOOLS: frozenset[str] = frozenset({"Edit", "Write", "MultiEdit"})

# Shared regex flags used by BOTH parse-time compile and match-time search.
# Keeping them identical guarantees a pattern that parses will never raise
# re.error at match time purely from flag divergence.
_MATCH_FLAGS = re.IGNORECASE | re.DOTALL

# Catastrophic-backtracking heuristics — substrings that are rejected as a
# cheap first pass. They catch the obvious ``.*.*`` / ``(.+)+`` shapes but
# are NOT sufficient on their own — see _pattern_is_redos_safe for the
# empirical timing probe that catches the rest.
_CATASTROPHIC_SUBSTRINGS: tuple[str, ...] = (
    ".*.*",
    "(.+)+",
    "(.*)+" ,
    "(.+)*",
    "(.*)*",
)

# ReDoS timing-probe configuration. We compile the pattern then run a single
# `re.search` on a deliberately small adversarial input and reject anything
# that exceeds the budget.
#
# Input size matters here: Python's `re` has no timeout, so the probe runs
# to completion. Known ReDoS patterns like `(a+)+b` grow exponentially in
# the length of the matched prefix — on a 2 KB input they'd take hours.
# We pick a probe length short enough that the worst offenders resolve in
# a few seconds but long enough that the exponential blow-up decisively
# exceeds the budget. 22 'a's makes `(a+)+b` take ~300 ms on modern
# hardware while benign patterns like `git commit.*Co-Authored-By` take
# microseconds — a 1000x gap on either side of the 150 ms cutoff.
#
# Trailing ``X1`` triggers BOTH non-letter-terminator failure modes
# (e.g. `[a-z]+$` backtracking against a digit) and non-word-terminator
# failure modes (e.g. `(a+)+b` backtracking against an uppercase letter).
#
# The budget is hardcoded (not a config setting) so rule authors can't
# widen it to sneak bad patterns through.
_REDOS_PROBE_INPUT = "a" * 22 + "X1"
_REDOS_BUDGET_SECONDS = 0.150


def _pattern_is_redos_safe(pattern: str) -> bool:
    """Empirical ReDoS probe: compile then time a search on adversarial input.

    Returns False if the pattern fails to compile OR the probe search exceeds
    _REDOS_BUDGET_SECONDS. The probe uses the same _MATCH_FLAGS the match
    path uses, so a pattern that passes here is guaranteed compilable at
    match time.
    """
    try:
        compiled = re.compile(pattern, _MATCH_FLAGS)
    except re.error:
        return False
    start = time.perf_counter()
    try:
        compiled.search(_REDOS_PROBE_INPUT)
    except Exception:  # noqa: BLE001 — probe is defensive
        return False
    return (time.perf_counter() - start) < _REDOS_BUDGET_SECONDS


def _parse_enforce(fm: dict) -> tuple[dict | None, str | None]:
    """Validate ``fm['enforce']``. Return (parsed, err).

    Mirrors the legacy ``parse_enforce_block`` + ``_describe_enforce_error``
    pair exactly: when the raw ``enforce`` key is absent from *fm*, both
    legs return ``None`` so the orchestrator records no malformed entry.
    An empty or non-dict block is a genuine parse failure and returns a
    diagnostic string.
    """
    if "enforce" not in fm:
        return None, None
    block = fm.get("enforce")
    if not isinstance(block, dict):
        return None, "enforce: block is not a dict"
    if not block:
        # Empty dict: original parse_enforce_block returned None; original
        # describe-error reported tool-not-Bash (tool is absent → None).
        return None, "enforce: tool must be 'Bash', got None"

    # --- tool ---
    tool = block.get("tool")
    if tool != "Bash":
        return None, f"enforce: tool must be 'Bash', got {tool!r}"

    # --- deny_pattern → deny_patterns list ---
    raw_patterns = block.get("deny_pattern") or block.get("deny_patterns")
    if isinstance(raw_patterns, str):
        raw_patterns = [raw_patterns]
    elif not isinstance(raw_patterns, list):
        raw_patterns = []

    validated_patterns: list[str] = []
    for p in raw_patterns:
        if not isinstance(p, str):
            return None, "enforce: block failed validation"
        if len(p) > 500:
            return None, "deny_pattern exceeds 500 chars"
        # Catastrophic backtracking — cheap substring heuristic first pass.
        if any(bad in p for bad in _CATASTROPHIC_SUBSTRINGS):
            return None, f"deny_pattern failed catastrophic-backtracking heuristic: {p!r}"
        # Compile check (mirrors the legacy describe-error path).
        try:
            re.compile(p, _MATCH_FLAGS)
        except re.error as exc:
            return None, f"deny_pattern failed re.compile: {exc}"
        # Then the empirical timing probe (must compile AND not exceed
        # _REDOS_BUDGET_SECONDS on an adversarial input).
        if not _pattern_is_redos_safe(p):
            return None, f"deny_pattern timed out on adversarial input: {p!r}"
        validated_patterns.append(p)

    # --- deny_command → deny_commands list ---
    raw_commands = block.get("deny_command") or block.get("deny_commands")
    if isinstance(raw_commands, str):
        raw_commands = [raw_commands]
    elif not isinstance(raw_commands, list):
        raw_commands = []

    validated_commands: list[str] = []
    for c in raw_commands:
        if not isinstance(c, str) or not c:
            return None, "enforce: block failed validation"
        if len(c) > 200:
            return None, "enforce: block failed validation"
        validated_commands.append(c)

    # Must have at least one pattern or command
    if not validated_patterns and not validated_commands:
        return None, "enforce: must have at least one deny_pattern or deny_command"

    # --- qualifier requirement (C2 safety rail, 2026-04-23) ---
    # Bare `deny_command` is a coarse prefix match — `git push` blocks all
    # pushes regardless of context. Force every enforce block that uses
    # deny_command to also supply a deny_pattern qualifier. deny_pattern
    # alone is fine (regex is already specific).
    if validated_commands and not validated_patterns:
        return None, (
            "enforce: deny_command requires a qualifier — "
            "add a deny_pattern regex that narrows the match, "
            "or drop the enforce block entirely"
        )

    # --- reason (required, truncate at 300) ---
    reason = block.get("reason", "")
    if not isinstance(reason, str) or not reason:
        return None, "enforce: reason is required"
    reason = reason[:300]

    return {
        "tool": tool,
        "deny_patterns": validated_patterns,
        "deny_commands": validated_commands,
        "reason": reason,
    }, None


def _parse_activates_on(fm: dict) -> tuple[dict | None, str | None]:
    """Validate ``fm['activates_on']``. Return (parsed, err).

    Same contract as :func:`_parse_enforce`: absent raw key returns
    ``(None, None)``; an empty or non-dict block returns a diagnostic.
    """
    if "activates_on" not in fm:
        return None, None
    block = fm.get("activates_on")
    if not isinstance(block, dict):
        return None, "activates_on: block is not a dict"
    if not block:
        # Empty dict: original parse returned None; original describe-error
        # reported tools-must-be-non-empty (tools is absent → None).
        return None, "activates_on: tools must be a non-empty list"

    # --- tools ---
    tools_raw = block.get("tools")
    if not isinstance(tools_raw, list) or not tools_raw:
        return None, "activates_on: tools must be a non-empty list"
    for t in tools_raw:
        if t not in _VALID_ENRICH_TOOLS:
            return None, f"activates_on: unknown tool {t!r} (must be Edit, Write, or MultiEdit)"

    # --- path_globs ---
    globs_raw = block.get("path_globs")
    if not isinstance(globs_raw, list) or not globs_raw:
        return None, "activates_on: path_globs must be a non-empty list"
    for g in globs_raw:
        if not isinstance(g, str) or len(g) > 200:
            return None, "activates_on: block failed validation"
        # Validate structural correctness by actually translating the glob.
        # Unterminated brackets, bad classes, etc. all surface here as an err.
        if _glob_to_regex(g) is None:
            return None, f"path_glob invalid or unterminated: {g}"

    return {
        "tools": list(tools_raw),
        "path_globs": list(globs_raw),
    }, None


def parse_block(
    kind: Literal["enforce", "activates_on"],
    fm: dict,
) -> tuple[dict | None, str | None]:
    """Single walker producing ``(parsed, error_message)`` for both block kinds.

    The error message is the human-readable diagnostic previously generated by
    the now-deleted ``_describe_enforce_error`` / ``_describe_enrich_error``
    helpers; it is computed at the failing step rather than re-walked from
    scratch, so no separate describe-error pass is needed.

    Both legs return ``(None, None)`` when the block is simply absent or
    not a dict-shaped value — that is not a parse error, just "no block to
    parse". The orchestrator distinguishes by checking whether the raw
    frontmatter key was present at all before invoking ``parse_block``.
    """
    if kind == "enforce":
        return _parse_enforce(fm)
    if kind == "activates_on":
        return _parse_activates_on(fm)
    raise ValueError(f"parse_block: unknown kind {kind!r}")


def parse_enforce_block(fm: dict) -> tuple[dict | None, str | None]:
    """Return ``(parsed, error)`` where ``parsed`` is a normalised dict
    ``{tool, deny_patterns, deny_commands, reason}`` or None.

    Thin wrapper around :func:`parse_block` that surfaces the error message
    to callers. Returns ``(None, None)`` when the enforce key is absent.
    """
    return parse_block("enforce", fm)


def parse_activates_on_block(fm: dict) -> dict | None:
    """Return a normalised dict {tools: list, path_globs: list} or None.

    Thin back-compat wrapper around :func:`parse_block`. Returns None if the
    block is missing or invalid.
    """
    parsed, _err = parse_block("activates_on", fm)
    return parsed
