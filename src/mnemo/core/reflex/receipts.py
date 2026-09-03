"""Turn reflex log lines into sentences — the data layer behind ``mnemo why``.

Reflex decides in silence. It scores every rule scoped to the project, runs
three gates against the top candidate, and on failure writes one line naming
the gate. For most of this project's life that line carried ``scores: []``:
the ranking that produced the decision was computed and thrown away. So
``relative_gap_fail`` — by a wide margin the most common outcome on a real
vault — was unfalsifiable. You could see *that* nothing fired and never *what
nearly did*, *what beat it*, or *by how much*.

This module reads the receipts the hook now records and explains them. Two
rules it does not break:

- **The prompt is never printed.** The log stores a 12-hex-digit hash by
  design; reconstructing the text here would quietly turn a diagnostic command
  into a keylogger's output.
- **A missing receipt is reported as missing**, not as "nothing came close".
  Entries logged before receipts existed have no candidates, and inventing an
  empty ranking for them is a lie about the past.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Rotation renames the live log to `.jsonl.1` the instant it crosses 1MB, so on
# a busy vault the live file can be seconds old. Reading only it would answer
# "the last 10 decisions" with two, which reads as "reflex stopped running".
_LOG_NAME = "reflex-log.jsonl"
_ROTATED_NAME = "reflex-log.jsonl.1"


def read_decisions(
    vault_root: Path | str,
    *,
    project: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """The most recent reflex decisions, newest first.

    Entries are ordered by their position in the log rather than by parsing
    ``ts``: the hook appends under a lock-free open/append/close, so file order
    is arrival order, and a hand-edited or clock-skewed timestamp cannot
    reorder history.
    """
    root = Path(vault_root) / ".mnemo"
    entries: list[dict[str, Any]] = []
    for name in (_ROTATED_NAME, _LOG_NAME):  # oldest file first
        entries.extend(_read_file(root / name))

    if project is not None:
        entries = [e for e in entries if e.get("project") == project]

    entries.reverse()
    if limit is not None and limit >= 0:
        entries = entries[:limit]
    return entries


def _read_file(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except ValueError:
                    continue  # a torn line from a killed append
                if isinstance(entry, dict):
                    out.append(entry)
    except OSError:
        return out
    return out


def format_human(decisions: list[dict[str, Any]], *, project: str | None = None) -> str:
    """Render decisions as a receipt block, newest first."""
    if not decisions:
        scope = f" for {project}" if project else ""
        return (
            f"No reflex decisions recorded{scope} yet.\n"
            "Reflex logs one line per prompt once it is enabled and the rule "
            "index has been built."
        )

    lines: list[str] = []
    for entry in decisions:
        lines.extend(_format_one(entry))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format_one(entry: dict[str, Any]) -> list[str]:
    when = _clock(entry.get("ts"))
    reason = entry.get("silence_reason")
    emitted = entry.get("emitted") or []

    if not reason and emitted:
        return _format_emission(when, entry, emitted)
    return _format_silence(when, entry, str(reason or "unknown"))


def _exported_line(when: str, entry: dict) -> list[str]:
    exported = [str(s) for s in (entry.get("exported") or [])]
    if not exported:
        return []
    return [f"{' ' * len(when)}  exported  {', '.join(exported)} (already in your rules file)"]


def _format_emission(when: str, entry: dict, emitted: list) -> list[str]:
    scores = entry.get("scores") or []
    shown = ", ".join(
        f"{slug} ({_num(scores[i])})" if i < len(scores) else str(slug)
        for i, slug in enumerate(emitted)
    )
    lines = [f"{when}  injected  {shown}"]
    # The interesting half of a success is what it beat. Skip the ones already
    # named above so the runner-up line is never a repeat of the emission.
    runners = [c for c in _candidates(entry) if c[0] not in set(emitted)]
    if runners:
        beaten = ", ".join(f"{slug} ({_num(score)})" for slug, score in runners)
        lines.append(f"{' ' * len(when)}  ahead of  {beaten}")
    lines.extend(_exported_line(when, entry))
    return lines


def _format_silence(when: str, entry: dict, reason: str) -> list[str]:
    candidates = _candidates(entry)
    thresholds = entry.get("thresholds") or {}
    head, needs_table = _explain(reason, entry, candidates, thresholds)
    lines = [f"{when}  silent    {head}"]
    if reason != "all_exported":
        lines.extend(_exported_line(when, entry))
    if needs_table and candidates:
        pad = " " * (len(when) + 12)
        width = max(len(slug) for slug, _ in candidates)
        for slug, score in candidates:
            lines.append(f"{pad}{slug.ljust(width)}  {_num(score)}")
    return lines


def _explain(
    reason: str, entry: dict, candidates: list, thresholds: dict
) -> tuple[str, bool]:
    """One sentence for a silence, and whether to print the ranking under it.

    Returns the sentence plus a flag, because half these reasons happen before
    any rule is scored — printing an empty candidate table under them would
    suggest retrieval looked and found nothing, when retrieval never ran.
    """
    if reason == "below_min_tokens":
        n = entry.get("prompt_tokens")
        return (f"prompt had {n} distinct search terms — too few to rank on", False)

    if reason == "index_missing":
        return ("no rule index for this project yet — it builds in the "
                "background after a session starts", False)

    if reason == "session_cap_reached":
        return ("this session's injection cap is spent — a budget, not a miss",
                False)

    if reason == "all_exported":
        names = ", ".join(str(s) for s in (entry.get("exported") or []))
        return (f"every matching rule is already in your rules file ({names})", False)

    if not candidates:
        return (f"{reason} — no receipt recorded for this decision "
                "(logged before receipts existed)", False)

    top_slug, top_score = candidates[0]

    if reason == "relative_gap_fail":
        gap = float(thresholds.get("relative_gap", 1.5))
        runner = candidates[1][1] if len(candidates) > 1 else 0.0
        needed = gap * runner
        return (
            f"{top_slug} led at {_num(top_score)} but needed {_num(needed)} "
            f"({_num(gap)} x the runner-up's {_num(runner)}) to be clearly ahead",
            True,
        )

    if reason == "absolute_floor_fail":
        configured = float(thresholds.get("absolute_floor", 2.0))
        floor = float(thresholds.get("absolute_floor_effective", configured))
        note = ""
        if floor < configured:
            n = thresholds.get("doc_count")
            rules = "?" if n is None else f"{n} rule{'' if n == 1 else 's'}"
            note = f" (floor scaled down from {_num(configured)} — the vault has {rules})"
        return (
            f"{top_slug} led at {_num(top_score)}, under the {_num(floor)} floor{note} "
            "— nothing scored well enough to be worth saying",
            True,
        )

    if reason == "term_overlap_fail":
        need = thresholds.get("term_overlap_min", 2)
        return (
            f"{top_slug} scored {_num(top_score)} but its overlap with the "
            f"prompt is under {need} terms — a ranking artefact, not a match",
            True,
        )

    if reason == "deduped":
        return (f"{top_slug} won at {_num(top_score)} but was already injected "
                "today — suppressed as a repeat", True)

    return (f"{reason} — top candidate {top_slug} at {_num(top_score)}", True)


def _candidates(entry: dict) -> list[tuple[str, float]]:
    """Normalise the recorded ranking, tolerating anything hand-edited."""
    out: list[tuple[str, float]] = []
    for item in entry.get("candidates") or []:
        try:
            slug, score = item[0], float(item[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        out.append((str(slug), score))
    return out


def _num(value: Any) -> str:
    """Two decimals, uniformly.

    Trimming trailing zeros would print a threshold of 2.0 as ``2`` next to a
    score of ``4.21``, and a column where some numbers look like integers reads
    as two different kinds of quantity. Every number here is a BM25F score or a
    multiple of one.
    """
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _clock(ts: Any) -> str:
    """``HH:MM:SS`` out of an ISO stamp; the raw value if it is not one."""
    text = str(ts or "")
    if len(text) >= 19 and text[10] in ("T", " "):
        return text[11:19]
    return text or "--:--:--"
