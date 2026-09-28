"""A second look at every verified correction before it is recorded (#524).

:func:`mnemo.core.corrections.verify` proves the user **typed** a quote. It
does not prove the words **correct** anything. On the maintainer's vault,
2026-08-31 to 09-27, two blind raters called 22 of 124 verified items real
corrections (#519): the rest were approvals ("pode mergear", "abre o PR e
mergeia"), new requests and answers. The evidence gate verifies a feedback page
against those same items, so "verified" could rest on a merge approval.

So after ``verify`` both capture paths — the briefing's ``## Corrections``
and the corrections-only pass — ask one more question of what survived: each
item's user turn is shown with the agent message it answered, the same view
the raters had, and :data:`SYSTEM_PROMPT` says whether it changes how the
agent works. An item answered "no" leaves the briefing and never reaches the
friction ledger, so the gate can no longer cite it. One call per session that
has at least one verified item; a session with none costs nothing.

The call fails open. A provider error, an unparseable answer or an item left
unanswered keeps the item, logged, exactly as it was before this check: a
lost real correction is not recoverable, a kept approval is what every item
was until now.

``tools/measure_correction_check.py`` runs :func:`build_prompt` and
:func:`parse_verdicts` — these, not a copy — over the raters' labelled items,
so the number in the PR is the number this stage produces.

**Off by default** (``extraction.correctionCheck.enabled``): it missed the bar
set before measuring — precision >= 80% and recall >= 90% of the items both
raters call a correction, on the held-out half. :data:`SYSTEM_PROMPT` was
written on the dev half (74 items, 12 real), where Opus 5.5 scored precision
100% and recall 92%, and 86% / 100% on a repeat. The test half (50 items, 10
real), scored once after: precision 61.5% [35.5, 82.3], recall 80.0% [49.0,
94.3], against 20% precision for keeping everything. Sonnet 5 scored 73% / 67%
on dev. The bar is near the raters' own ceiling: on test, one rater alone
scores 83% and the other 77% precision against their joint answer. Opus 5.5
is also one of those two raters, under a different prompt.
"""
from __future__ import annotations

import time as _time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from mnemo.core import corrections

#: The model when ``extraction.correctionCheck.model`` names none. Not the
#: briefing's Haiku: under the raters' own prompt it left 22 of 74 dev items
#: unanswered, and of the ones it answered it kept 7 of 11 real corrections
#: and 19 items in all.
DEFAULT_MODEL = "claude-opus-5-5"

#: How much of the answered agent message (its tail) and of the user's turn
#: (its head) the check sees: what the raters saw (#519).
CONTEXT_CHARS = 1500
TURN_CHARS = 1500

SYSTEM_PROMPT = """\
You check moments from coding sessions between a user and an AI coding agent.
Each numbered item shows the agent's last message and the user's reply to it.
Another model already marked each reply as a possible correction; most of those
marks are wrong. Decide, item by item, whether the reply really is a CORRECTION.

A CORRECTION changes how the agent works. The user:
- rejects or reverses something the agent did, decided or proposed ("no, don't
  remove it", "that's not what I asked", "I don't know what you're talking about");
- redirects the agent's method, often as a pointed question about what it did
  ("can't you check that through our integration?", "why didn't you use X?");
- constrains how work is to be done ("do it in a worktree", "don't open a release");
- states a standing rule or permission for how the agent should work from now on
  ("in this repo always write commits in English", "anything read-only you may run
  in prod", "stop asking me and use dispatch").

NOT a correction, even when phrased as an order or with "must"/"can't":
- approval or go-ahead: "ok", "yes", "go ahead", "pode mergear", "abre o PR e
  mergeia", "commit and open the PR", "pode fazer o merge você mesmo" — letting the
  agent merge or proceed is an approval, not a rule;
- the next task, a new feature, or a change to the PRODUCT's behaviour or business
  rules ("the renewal page must not offer a downgrade", "the button should be
  bigger") — these specify what to build, not how the agent should behave;
- answering the agent's question or choosing among options it offered;
- a question for information, a bug report, a status update, urgency ("run it
  please"), thanks, or thinking out loud.

Judge only what the user's words do to the agent's own conduct. When the reply
mixes an approval or a new request with a real correction, it is a correction.
When in doubt between an approval/request and a correction, answer false.

Reply with JSON only:
{"labels": [{"id": "<item number>", "correction": true|false}]}
"""


def _tail(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def _head(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def build_prompt(pairs: Sequence[Tuple[str, str]]) -> str:
    """One ``### item N`` block per ``(agent message, user turn)``, numbered from 1."""
    parts = []
    for n, (agent, turn) in enumerate(pairs, 1):
        parts.append("### item %d\nAGENT:\n%s\n\nUSER:\n%s\n" % (
            n, _tail(agent, CONTEXT_CHARS) or "(no text)", _head(turn, TURN_CHARS)))
    return "\n".join(parts)


def parse_verdicts(text: str, count: int) -> Dict[int, bool]:
    """``item number -> correction?`` for the items the answer names; never raises.

    An id is read leniently ("3", 3, "item 3") because models echo it back in
    all three shapes; anything else, or a number out of range, is ignored.
    """
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text or "")
    except Exception:
        return {}
    out: Dict[int, bool] = {}
    rows = payload.get("labels")
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get("correction"), bool):
            continue
        raw = str(row.get("id") if row.get("id") is not None else "").strip().lower()
        if raw.startswith("item"):
            raw = raw[4:].strip()
        if raw.isdigit() and 1 <= int(raw) <= count:
            out[int(raw)] = row["correction"]
    return out


def settings(cfg: dict) -> Tuple[bool, str]:
    """``(enabled, model)`` from ``extraction.correctionCheck``."""
    extraction = cfg.get("extraction") or {}
    raw = extraction.get("correctionCheck")
    check = raw if isinstance(raw, dict) else {}
    return bool(check.get("enabled", False)), str(check.get("model") or DEFAULT_MODEL)


def check(
    items: List[corrections.Correction],
    exchanges: Sequence[Tuple[str, str]],
    cfg: dict,
    *,
    vault_root: Optional[Path] = None,
    agent: str = "",
) -> Tuple[List[corrections.Correction], List[corrections.Correction]]:
    """Split verified *items* into ``(kept, dropped)`` by the check's answer.

    *exchanges* is :func:`mnemo.core.friction.capture.exchanges` of the
    session: turn ``n`` there is turn ``n`` in :func:`corrections.locate`.
    No call when there is nothing to check or the check is off. Never raises;
    on any failure every item is kept (see the module docstring).
    """
    enabled, model = settings(cfg)
    if not items or not enabled:
        return list(items), []
    turns = [turn for _, turn in exchanges]
    pairs: List[Tuple[str, str]] = []
    for item in items:
        index = corrections.locate(item.quote, turns)
        pairs.append(exchanges[index] if index is not None else ("", item.quote))
    try:
        from mnemo.core import llm

        timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 60)
        t0 = _time.perf_counter()
        response = llm.resolve(cfg)(
            build_prompt(pairs), system=SYSTEM_PROMPT, model=model, timeout=timeout,
        )
        elapsed_ms = (_time.perf_counter() - t0) * 1000
    except Exception as exc:
        _log(vault_root, "correction_check.call", exc)
        return list(items), []
    if vault_root is not None:
        try:
            from mnemo.core.mcp import access_log

            access_log.record_llm_call(
                vault_root=vault_root, response=response, purpose="correction_check",
                model=model, project=agent, agent=agent, elapsed_ms=elapsed_ms,
            )
        except Exception:
            pass  # telemetry must never cost the check
    verdicts = parse_verdicts(response.text or "", len(items))
    if len(verdicts) < len(items):
        _log(vault_root, "correction_check.unanswered", ValueError(
            f"{len(items) - len(verdicts)} of {len(items)} item(s) unanswered; kept"))
    kept = [it for n, it in enumerate(items, 1) if verdicts.get(n, True)]
    dropped = [it for n, it in enumerate(items, 1) if not verdicts.get(n, True)]
    return kept, dropped


def _log(vault_root: Optional[Path], where: str, exc: BaseException) -> None:
    if vault_root is None:
        return
    try:
        from mnemo.core import errors

        errors.log_error(vault_root, where, exc)
    except Exception:
        pass
