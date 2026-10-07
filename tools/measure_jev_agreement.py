"""Which of the raters' questions can Jev answer in their place? Per question, calibrated on existing labels (#529).

Usage:
    PYTHONPATH=src python3 tools/measure_jev_agreement.py [--question Q ...]      # items, split, pending, cost; report
    PYTHONPATH=src python3 tools/measure_jev_agreement.py --label-briefings --send # the new two-rater set (Claude)
    PYTHONPATH=src python3 tools/measure_jev_agreement.py --send [--question Q ...] [--workers W] [--pause S]
    PYTHONPATH=src python3 tools/measure_jev_agreement.py --json                  # the report, from the cache

#520 and #527 asked their questions of two blind raters on ``claude-opus-5-5``
and ``claude-fable-5-1``, about $660 notional over the two studies. Taking the
memory apart to see why each piece fails needs the same questions asked across
the whole history, for every entry point, which at that price is not a tool.
TypeSafe's ``jev-1.13.0`` answers a classification question for about $0.04
per million input tokens in about a second. This finds out, **per question**,
whether its answers can stand in for the raters'.

The questions and the labels they are graded against (:data:`QUESTIONS`):

- ``relevant_strict`` / ``relevant_broad`` — does this already-taught rule
  apply to this prompt? #520's frequency labels
  (``prevented-repeats/frequency.json``), one item per (prompt, rule shown),
  with the chunks it froze (``chunks.json``). Strict keeps the rules in #520's
  strict pool, which is rebuilt with its own functions and label caches;
- ``redundant`` — do ``CLAUDE.md`` and auto-memory already say this rule?
  #520's delivery judge, column B (``delivery.json``). The units are rebuilt
  the way #520 built them, and a unit is kept only when its rebuilt id — a
  hash of the exact texts and the slug — is a key the raters answered, so a
  kept unit shows Jev what the raters saw. One difference, stated: Text B
  carries the auto-memory notes of every unit sharing its texts, where #520's
  batches carried those of the units that happened to be pending together;
- ``correction`` — is this quoted user turn a correction at all? #519's
  labels over ``repeated-corrections/items.json``;
- ``generic`` — is this rule standard practice or a story, not a lesson? The
  2026-09-22 audit's two blind raters (``audit-2026-09-22/labels-A.json``,
  ``labels-B.json``): a rule is junk when its category is G or N. Those raters
  were Claude subagents on one prompt, not the two models above;
- ``better`` — which of two replies serves the developer better, rule-blind?
  #527's verdicts over its frozen arms and answers, with, without or tie. Jev
  is asked both orders of every pair and the two answers are averaged, so a
  position preference cancels instead of being scored;
- ``briefing`` — is this ``[last-briefing]`` about the work this session goes
  on to do? No labels existed: ``--label-briefings --send`` draws
  :data:`BRIEFING_N` human sessions from the last :data:`BRIEFING_DAYS` days
  whose SessionStart carried one, and asks the two raters
  (:data:`BRIEFING_SYSTEM`). They see the briefing's body without mnemo's
  envelope, the developer's typed messages and the agent's last message —
  never what mnemo is, or which answer anyone hopes for.

**The protocol, per question.** Items are split by a hash of their group (the
session, or the item when items are independent): :func:`is_dev`. Every
wording in :data:`VARIANTS` is asked on the dev half; the threshold that
maximises Jev's mean Cohen's kappa against the two raters is read off dev, the
best wording at its best threshold is **locked** in ``lock.json``, and only
then is the test half sent, for the locked wording alone. A lock is never
rewritten: to tune again, delete that question's entry by hand and say so.

**Jev qualifies for a question** when, on the test half, its mean kappa
against each rater is at least the two raters' kappa with each other — the
maintainer's rule in #529, on point estimates. Beside it: both kappas and
their difference with 95% bootstrap CIs over groups, and the AUC of Jev's
probability against the label the two raters agree on.

**State on 2026-09-28, the maintainer's vault.** Every loader reproduces the
raters' own agreement: relevance kappa 0.78 strict / 0.74 broad and delivery B
0.94 (#520's report at 265 sessions — the issue quotes the 120-session 0.75 /
0.73), correction 0.67, generic 0.89, better 0.24. 2172 of the 2202 delivery
units rebuild byte for byte; the rest changed on disk since and are left out.
The briefing set is labelled: 80 of 237 eligible sessions since 2026-08-29,
kappa 0.88, and both raters call only **14 of 80 briefings about the work
their session goes on to do** (63 not; Opus 5.5 $3.40 and Fable 5.1 $8.02
notional over 160 calls). **No Jev answer exists yet**: the session that built
this tool was refused the send by its own safety classifier, as the issue
foresaw, so ``--send`` is the maintainer's to run. The dev half over every
wording is 13,130 requests, about $2.56 by the tool's estimate; the test half
adds one wording per question.

**Only ``--send`` leaves the machine.** Jev's requests carry prompts, agent
messages, rule bodies, memory files and briefing excerpts to TypeSafe
(``https://api.typesafe.ai/v1/systemone``), a third party; the maintainer
authorised that on 2026-09-28 for this measurement. ``--label-briefings
--send`` calls the two Claude raters through :func:`mnemo.core.llm.resolve`,
from a scratch cwd. Sends run on ``--workers`` threads with ``--pause``
seconds between one worker's requests; every answer is cached under ``--out``
(default ``<vault>/.mnemo/jev-agreement``) the moment it arrives, and
:data:`MAX_FAILURES` failures in a row stop the run, so a rerun resumes.
A request the provider rejects (HTTP 4xx) is cached as unanswered and counted,
never retried in a loop and never read as "no".
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import queue
import random
import re
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mpr = _sibling("measure_prevented_repeats")
mrc = mpr.mrc
mbv = _sibling("measure_broad_value")
mgr = _sibling("measure_generic_rules")

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

MODEL = "jev-1.13.0"
#: USD per million input tokens (``mnemo.core.dedup_judge.USD_PER_MTOK``).
USD_PER_MTOK = 0.042
#: For the estimate before a send; the provider's own count is logged after.
CHARS_PER_TOKEN = 3.5
RATERS = mpr.RATERS
AUDIT_RATERS = ("A", "B")
OUT_DIR = "jev-agreement"
SEED = 529
BOOTSTRAP = 2000

#: One request's limits: the provider takes 64k tokens, and a request that
#: fails costs every question in it.
MAX_QUESTIONS = 32
MAX_REQUEST_CHARS = 120000

WORKERS = 4
PAUSE_SECONDS = 0.2
TIMEOUT_S = 60.0
MAX_FAILURES = 5

#: Threshold grid, and the AUC's resolution: Jev answers in hundredths, and
#: an average of two answers lands on half-hundredths.
GRID = [i / 200 for i in range(201)]
BINS = 1000

DEV, TEST = "dev", "test"

# --- the new briefing set ----------------------------------------------------------------

BRIEFING_N = 80
BRIEFING_DAYS = 30
BRIEFING_CHARS = 6000
BRIEFING_PROMPTS = 8
BRIEFING_PROMPT_CHARS = 600
BRIEFING_FINAL_CHARS = 1200

BRIEFING_SYSTEM = """\
You are shown notes an AI coding agent received when a coding session started,
left by an earlier session in the same project, and what this session went on
to do: the developer's typed messages in order, and the agent's last message.

Decide whether the notes are ABOUT the work this session goes on to do: the
same task, issue, branch, component or line of work, so that an agent that read
them first would be better placed for what follows. Sharing only the project,
or general background about it, is not enough.

Reply with JSON only: {"about": true|false, "why": "<one sentence>"}
"""


# --- small helpers -----------------------------------------------------------------------

def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def is_dev(group: str) -> bool:
    """The split: a hash of the group, so every item of one session lands on
    one side and tuning cannot read the test half through a sibling item."""
    return int(hashlib.sha256(("%d:%s" % (SEED, group)).encode("utf-8")).hexdigest()[:8], 16) % 2 == 0


def part_of(item: Dict[str, Any]) -> str:
    return DEV if is_dev(item["group"]) else TEST


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


# --- the wordings --------------------------------------------------------------------------
#
# A variant is a wording *and* how its answer becomes the probability of the
# question's positive class. ``subject`` is what one question quotes (a rule,
# a briefing, a pair of replies); everything the questions of one request share
# rides in the state. The column an answer is filed under hashes the wording,
# so an edited wording is a new column and never reuses an old answer.

def _shipped_reflex_question() -> Dict[str, Any]:
    from mnemo.core.reflex import judge

    q = judge.question("")
    return {"type": q["type"], "instructions": q["instructions"], "criteria": q["criteria"]}


_reflex_q = _shipped_reflex_question()

VARIANTS: Dict[str, List[Dict[str, Any]]] = {
    "relevant": [
        {"name": "applies", "type": "noul",
         "instructions": (
             "The AI coding agent answering the user's prompt in the state, or doing the work it "
             "asks for, should act on this rule the user taught it in an earlier session, and "
             "could get it wrong without it. Sharing a topic or keywords is not enough: the rule "
             "must bear on what the agent is about to do next. Rule: "),
         "criteria": {"true": "The rule bears on what the agent does next and could change it",
                      "false": "The rule is about something else, or only shares a topic"}},
        # The reflex judge's wording as shipped, imported rather than copied.
        dict(_reflex_q, name="reflex"),
        {"name": "loss", "type": "noul",
         "instructions": (
             "Had the agent never been taught this rule, its answer to the user's prompt in the "
             "state, or the work that prompt asks for, could go wrong in a way the rule prevents. "
             "Rule: "),
         "criteria": {"true": "Without the rule the agent could make the mistake the rule is about here",
                      "false": "The rule makes no difference to this prompt"}},
    ],
    "redundant": [
        {"name": "covers", "type": "noul",
         "instructions": (
             "The memory text in the state already tells the agent what this rule says: the same "
             "behaviour, fact or preference, so an agent that read the text would act as the rule "
             "says. Sharing a topic is not enough; a more general statement counts only if it "
             "clearly covers the rule's case. Rule: "),
         "criteria": {"true": "The text already says what the rule says",
                      "false": "The rule says something the text does not"}},
        {"name": "new", "type": "noul", "invert": True,
         "instructions": (
             "An agent that has read the memory text in the state would still learn something from "
             "this rule: a behaviour, fact or preference the text does not state. Rule: "),
         "criteria": {"true": "The rule adds something the text does not say",
                      "false": "Everything the rule says is already in the text"}},
    ],
    "correction": [
        {"name": "label", "type": "noul",
         "instructions": (
             "The user's reply in the state is a CORRECTION: the user tells the agent to stop doing "
             "something, to do something differently, or states a preference the agent should "
             "follow from now on, because of what the agent did or proposed. Approving or "
             "confirming, a new request or next task, answering the agent's question, asking for "
             "information, urgency and thanks are not corrections."),
         "criteria": {"true": "A correction of what the agent did or proposed",
                      "false": "An approval, a new request, an answer, a question, urgency or thanks"}},
        {"name": "course", "type": "noul",
         "instructions": (
             "With the reply in the state, the user signals that something the agent did or "
             "proposed in its message was wrong or unwanted, and that the agent must change "
             "course."),
         "criteria": {"true": "The agent must change what it did or planned",
                      "false": "The agent may carry on as it was"}},
    ],
    "generic": [
        # measure_generic_rules' pre-registered wording (#410), imported; its
        # levels ascend in worth, so the junk probability is the flipped score.
        {"name": "loss", "type": "score", "invert": True,
         "instructions": mgr.LOSS_INSTRUCTIONS, "criteria": list(mgr.LOSS_CRITERIA)},
        {"name": "junk", "type": "noul",
         "instructions": (
             "This stored engineering rule is not a lesson: it is either standard practice any "
             "competent engineer already follows, or a story of what happened in one session with "
             "nothing to act on next time. Rule: "),
         "criteria": {"true": "Standard practice, or a story with no lesson",
                      "false": "A lesson an engineer would act on: a technique or knowledge of a system"}},
    ],
    "better": [
        {"name": "choice", "type": "choice", "pairwise": True,
         "instructions": (
             "Which reply better serves the developer in the state, in this repository: more "
             "correct, more useful, a better fit for what they asked and for how this repository "
             "works? Length and polish are not merit on their own. Some text may be masked as "
             "[...]; ignore that. "),
         "criteria": {"1": "Reply 1 is better", "2": "Reply 2 is better", "tie": "Neither is better"}},
        {"name": "noul", "type": "noul", "pairwise": True,
         "instructions": (
             "Reply 1 serves the developer in the state better than Reply 2 does, in this "
             "repository: more correct, more useful, a better fit for what they asked and for how "
             "this repository works. Length and polish are not merit on their own. Some text may "
             "be masked as [...]; ignore that. "),
         "criteria": {"true": "Reply 1 is better", "false": "Reply 2 is better, or neither is"}},
    ],
    "briefing": [
        {"name": "about", "type": "noul",
         "instructions": (
             "These notes, left by the developer's previous session and shown to the agent when "
             "this session started, are about the work this session goes on to do (in the state): "
             "the same task, issue, branch, component or line of work, so an agent that read them "
             "first would be better placed for what follows. Sharing only the project is not "
             "enough. Notes: "),
         "criteria": {"true": "The notes are about the work this session does",
                      "false": "The notes are about other work, or only the project in general"}},
        {"name": "useful", "type": "noul",
         "instructions": (
             "An agent starting the session in the state would do its work better for having read "
             "these notes from the previous session first. Notes: "),
         "criteria": {"true": "The notes help with the work this session does",
                      "false": "The notes do not bear on this session's work"}},
    ],
}


def column(variant: Dict[str, Any]) -> str:
    spec = {k: v for k, v in variant.items() if k != "name"}
    return "%s@%s" % (variant["name"], _sha(json.dumps(spec, sort_keys=True)))


def _question(variant: Dict[str, Any], text: str) -> Dict[str, Any]:
    return {"type": variant["type"], "instructions": variant["instructions"] + text,
            "criteria": variant["criteria"]}


def pair_text(first: str, second: str) -> str:
    return "\n\nReply 1:\n%s\n\nReply 2:\n%s" % (first, second)


def questions_for(variant: Dict[str, Any], item: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """``(suffix, question)`` per question this item needs under ``variant``.

    A pairwise item is asked in both orders — ``ab`` shows the positive reply
    first, ``ba`` second — so a judge that prefers a position scores 0.5 on it
    instead of a win."""
    if variant.get("pairwise"):
        pos, neg = item["subject"]
        return [("ab", _question(variant, pair_text(pos, neg))),
                ("ba", _question(variant, pair_text(neg, pos)))]
    return [("", _question(variant, item["subject"]))]


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def first_better(variant: Dict[str, Any], answer: Dict[str, Any]) -> Optional[float]:
    """For a pairwise wording: the probability reply 1 is the better one."""
    if variant["type"] == "choice":
        probs = answer.get("probabilities") or {}
        return _number(probs.get("1"))
    return _number(answer.get("noul"))


def probability(variant: Dict[str, Any], answers: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """The item's probability of the positive class, from its answers by suffix;
    None when any is missing or not a number."""
    if variant.get("pairwise"):
        ab = first_better(variant, answers.get("ab") or {})
        ba = first_better(variant, answers.get("ba") or {})
        if ab is None or ba is None:
            return None
        return (ab + (1.0 - ba)) / 2.0
    a = answers.get("") or {}
    if variant["type"] == "noul":
        p = _number(a.get("noul"))
    elif variant["type"] == "score":
        s = _number(a.get("score"))
        p = None if s is None else s / float(len(variant["criteria"]) - 1)
    else:
        p = _number((a.get("probabilities") or {}).get(variant.get("positive", "true")))
    if p is None:
        return None
    p = max(0.0, min(1.0, p))
    return 1.0 - p if variant.get("invert") else p


# --- the questions -------------------------------------------------------------------------
#
# An item: ``id``, ``group`` (the split and bootstrap unit), ``state`` (what
# every question of its request shares) and ``state_key``, ``subject`` (what
# its own question quotes), and ``labels`` (rater -> category). ``positive``
# is the category Jev's probability is of, ``negative`` the one it says below
# the threshold.

QUESTIONS: Dict[str, Dict[str, Any]] = {
    "relevant_strict": {"variants": "relevant", "positive": True, "negative": False, "raters": RATERS,
                        "about": "does this already-taught rule apply to this prompt? (strict pool)"},
    "relevant_broad": {"variants": "relevant", "positive": True, "negative": False, "raters": RATERS,
                       "about": "does this already-taught rule apply to this prompt? (any live rule)"},
    "redundant": {"variants": "redundant", "positive": True, "negative": False, "raters": RATERS,
                  "about": "do CLAUDE.md / auto-memory already say this rule?"},
    "correction": {"variants": "correction", "positive": True, "negative": False, "raters": RATERS,
                   "about": "is this quoted user turn a correction at all?"},
    "generic": {"variants": "generic", "positive": True, "negative": False, "raters": AUDIT_RATERS,
                "about": "is this rule standard practice or a story, not a lesson?"},
    "better": {"variants": "better", "positive": mbv.WITH, "negative": mbv.WITHOUT, "raters": RATERS,
               "about": "which reply serves the developer better? (pairwise, rule-blind)"},
    "briefing": {"variants": "briefing", "positive": True, "negative": False, "raters": RATERS,
                 "about": "is this [last-briefing] about the work this session goes on to do?"},
}


def variants_of(question: str) -> List[Dict[str, Any]]:
    return VARIANTS[QUESTIONS[question]["variants"]]


def relevance_items(chunks: Dict[str, List[Dict[str, Any]]], freq: Dict[str, Dict[str, Any]],
                    rule_text: Callable[[str], str], strict: Optional[Set[str]],
                    raters: Sequence[str] = RATERS) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(broad, strict) items: one per (prompt, rule shown), for every chunk
    every rater answered. ``freq``: rater -> chunk id -> prompt key -> slugs."""
    broad: List[Dict[str, Any]] = []
    for sid in sorted(chunks):
        for ch in chunks[sid]:
            said = [freq.get(r, {}).get(ch["id"]) for r in raters]
            if any(s is None for s in said):
                continue
            for p in ch["prompts"]:
                state = {"agent_said_before": p["answered"] or "(nothing)", "user_prompt": p["text"]}
                for slug in ch["rules"]:
                    text = rule_text(slug)
                    if not text:
                        continue
                    broad.append({
                        "id": _sha(p["key"], slug), "group": sid, "state": state,
                        "state_key": p["key"], "subject": text, "slug": slug,
                        "labels": {r: slug in (s.get(p["key"]) or []) for r, s in zip(raters, said)}})
    # A prompt can sit in one chunk only, but guard against a slug shown twice.
    seen: Set[str] = set()
    broad = [it for it in broad if not (it["id"] in seen or seen.add(it["id"]))]
    return broad, [it for it in broad if strict and it["slug"] in strict]


def redundant_items(units: Sequence[Dict[str, Any]], deliv: Dict[str, Dict[str, Dict[str, bool]]],
                    raters: Sequence[str] = RATERS) -> List[Dict[str, Any]]:
    """One per rebuilt unit whose id every rater answered; the label is column B."""
    out = []
    for u in units:
        said = [deliv.get(r, {}).get(u["id"]) for r in raters]
        if u.get("empty") or any(s is None for s in said):
            continue
        out.append({"id": u["id"], "group": u["session_id"], "state": {"memory_text": u["text_b"]},
                    "state_key": u["texts"], "subject": u["rule"],
                    "labels": {r: bool(s["B"]) for r, s in zip(raters, said)}})
    return out


def correction_items(items: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, bool]],
                     raters: Sequence[str] = RATERS) -> List[Dict[str, Any]]:
    out = []
    for it in items:
        said = [labels.get(r, {}).get(it["id"]) for r in raters]
        if any(s is None for s in said):
            continue
        out.append({"id": it["id"], "group": it["id"],
                    "state": {"agent_message": it["answered"] or "(no text)", "user_reply": it["turn"]},
                    "state_key": it["id"], "subject": "",
                    "labels": {r: bool(s) for r, s in zip(raters, said)}})
    return out


JUNK = ("G", "N")


def generic_items(sample: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, Any]],
                  raters: Sequence[str] = AUDIT_RATERS) -> List[Dict[str, Any]]:
    out = []
    for it in sample:
        said = [labels.get(r, {}).get(it["id"]) for r in raters]
        if any(not s or "cat" not in s for s in said):
            continue
        out.append({"id": it["id"], "group": it["id"], "state": {"vault": (
            "A vault of engineering rules extracted from one developer's past sessions and offered "
            "back to an AI coding agent. Each question quotes one rule as the agent is shown it.")},
                    "state_key": "vault", "subject": it["text"],
                    "labels": {r: s["cat"] in JUNK for r, s in zip(raters, said)}})
    return out


def better_items(arms: Dict[str, Dict[str, Any]], answers: Dict[str, Dict[str, List[Dict[str, Any]]]],
                 verdicts: Dict[str, Dict[str, Dict[str, str]]],
                 raters: Sequence[str] = RATERS) -> List[Dict[str, Any]]:
    """One per #527 comparison both raters judged; the subject is (with, without)
    as the raters read them, rule masked."""
    out = []
    for uid in sorted(arms):
        a = arms[uid]
        got = answers.get(uid) or {}
        for k in range(mbv.SAMPLES):
            cid = mbv.comparison_id(uid, k)
            said = [(verdicts.get(r, {}).get(cid) or {}).get("better") for r in raters]
            if any(s is None for s in said) or any(len(got.get(x, [])) <= k for x in mbv.ARMS):
                continue
            w = mbv.mask(got[mbv.WITH][k]["text"], a["slug"])[0]
            wo = mbv.mask(got[mbv.WITHOUT][k]["text"], a["slug"])[0]
            state = {"repository_claude_md": a.get("claude_md") or "(none)",
                     "agent_previous_message": mbv.mask(a.get("previous") or "", a["slug"])[0] or "(none)",
                     "developer_message": a.get("prompt") or ""}
            out.append({"id": cid, "group": a.get("session_id") or uid, "state": state,
                        "state_key": cid, "subject": (w, wo),
                        "labels": {r: s for r, s in zip(raters, said)}})
    return out


# --- the briefing set ------------------------------------------------------------------------

_BRIEF_OPEN = "[last-briefing"
_BRIEF_CLOSE = "[/last-briefing]"


def briefing_block(session_start: Sequence[str]) -> str:
    """The body of the first ``[last-briefing]`` block the session was handed,
    without its header line (session id, date) or mnemo's other sections."""
    for text in session_start:
        i = text.find(_BRIEF_OPEN)
        if i < 0:
            continue
        j = text.find(_BRIEF_CLOSE, i)
        block = text[i:j] if j > 0 else text[i:]
        body = block.split("\n", 1)[1] if "\n" in block else ""
        body = re.sub(r"</?persisted-output>", "", body).strip()
        return mpr._head(body, BRIEFING_CHARS)
    return ""


def last_agent_text(events: Iterable[dict]) -> str:
    last = ""
    for ev in events:
        if not isinstance(ev, dict) or ev.get("type") != "assistant":
            continue
        content = (ev.get("message") or {}).get("content")
        if isinstance(content, str):
            parts = [content]
        else:
            parts = [str(b.get("text") or "") for b in content or []
                     if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(p for p in parts if p.strip())
        if text.strip():
            last = text
    return last


#: Turns that reach the transcript as user text but were not typed by the
#: developer: a skill's body, another session's hand-back, harness notices.
_NOT_TYPED = re.compile(
    r"^\s*(Base directory for this skill:|Another Claude session sent a message|\[Request interrupted"
    r"|\[Cross-session |Continue from where you left off\.|# Claude in Chrome|<agent-message"
    r"|<task-notification|<command-(name|message)|## Context Usage)")
_IMAGE = re.compile(r"\[Image: source: [^\]]*\]")
#: Shorter replies ("ok", "sim", "1") say nothing about the work on their own
#: and would take the few slots a rater is shown; they are counted, not shown.
BRIEFING_MIN_PROMPT = 12


def typed_prompts(prompts: Sequence[Dict[str, Any]]) -> Tuple[List[str], int]:
    """(the developer's substantive typed messages, how many shorter ones were left out)."""
    shown: List[str] = []
    short = 0
    for p in prompts:
        text = p.get("text") or ""
        if _NOT_TYPED.search(text):
            continue
        text = _IMAGE.sub("[image]", text).strip()
        if len(text) < BRIEFING_MIN_PROMPT:
            short += bool(text)
            continue
        shown.append(text)
    return shown, short


def briefing_record(sid: str, walked: Dict[str, Any], events: List[dict]) -> Optional[Dict[str, Any]]:
    """What a rater and Jev see of one session, or None when it has no
    briefing or no substantive typed message."""
    body = briefing_block(walked.get("session_start") or [])
    prompts, short = typed_prompts(walked.get("prompts") or [])
    if not body or not prompts:
        return None
    return {"id": sid, "briefing": body,
            "prompts": [mpr._head(t, BRIEFING_PROMPT_CHARS) for t in prompts[:BRIEFING_PROMPTS]],
            "more_prompts": max(0, len(prompts) - BRIEFING_PROMPTS), "short_replies": short,
            "final": mpr._tail(last_agent_text(events), BRIEFING_FINAL_CHARS)}


def session_work(rec: Dict[str, Any]) -> str:
    lines = ["Developer's messages, in order:"]
    for n, t in enumerate(rec["prompts"], 1):
        lines.append("%d. %s" % (n, t))
    if rec.get("more_prompts"):
        lines.append("(%d more messages)" % rec["more_prompts"])
    if rec.get("short_replies"):
        lines.append("(and %d short replies such as \"ok\" left out)" % rec["short_replies"])
    lines += ["", "Agent's last message:", rec.get("final") or "(none)"]
    return "\n".join(lines)


def briefing_prompt(rec: Dict[str, Any]) -> str:
    return "## Notes shown at session start\n%s\n\n## What the session went on to do\n%s" % (
        rec["briefing"], session_work(rec))


def parse_briefing(text: str) -> Optional[bool]:
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    value = payload.get("about") if isinstance(payload, dict) else None
    return value if isinstance(value, bool) else None


def briefing_items(records: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, bool]],
                   raters: Sequence[str] = RATERS) -> List[Dict[str, Any]]:
    out = []
    for rec in records:
        said = [labels.get(r, {}).get(rec["id"]) for r in raters]
        if any(s is None for s in said):
            continue
        out.append({"id": rec["id"], "group": rec["id"], "state": {"this_session": session_work(rec)},
                    "state_key": rec["id"], "subject": rec["briefing"],
                    "labels": {r: bool(s) for r, s in zip(raters, said)}})
    return out


def draw_briefing_sessions(candidates: Sequence[str], n: int = BRIEFING_N, seed: int = SEED) -> List[str]:
    ids = sorted(set(candidates))
    random.Random(seed).shuffle(ids)
    return ids[:n]


# --- requests -------------------------------------------------------------------------------

def requests_for(variant: Dict[str, Any], items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Items grouped by the state they share, :data:`MAX_QUESTIONS` questions
    and :data:`MAX_REQUEST_CHARS` characters at most per request."""
    by_state: Dict[str, List[Dict[str, Any]]] = {}
    for it in items:
        by_state.setdefault(it["state_key"], []).append(it)
    out: List[Dict[str, Any]] = []
    for key in sorted(by_state):
        group = by_state[key]
        state = group[0]["state"]
        base = len(json.dumps(state, ensure_ascii=False))
        cur: Dict[str, Any] = {"state": state, "questions": {}, "items": {}}
        size = base
        for it in group:
            qs = questions_for(variant, it)
            add = sum(len(json.dumps(q, ensure_ascii=False)) for _, q in qs)
            if cur["questions"] and (len(cur["questions"]) + len(qs) > MAX_QUESTIONS
                                     or size + add > MAX_REQUEST_CHARS):
                out.append(cur)
                cur = {"state": state, "questions": {}, "items": {}}
                size = base
            n = len(cur["items"])
            for suffix, q in qs:
                cur["questions"]["q%d%s" % (n, suffix)] = q
            cur["items"]["q%d" % n] = it["id"]
            size += add
        if cur["questions"]:
            out.append(cur)
    return out


def request_chars(req: Dict[str, Any]) -> int:
    return len(json.dumps({"state": req["state"], "questions": req["questions"]}, ensure_ascii=False))


def read_answers(variant: Dict[str, Any], req: Dict[str, Any], out: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """item id -> ``{"p": float|None}`` for every item in the request."""
    answers = out.get("answers") or {}
    got: Dict[str, Dict[str, Any]] = {}
    for qid, iid in req["items"].items():
        suffixes = ("ab", "ba") if variant.get("pairwise") else ("",)
        by_suffix = {s: answers.get(qid + s) or {} for s in suffixes}
        got[iid] = {"p": probability(variant, by_suffix)}
    return got


# --- sending ------------------------------------------------------------------------------------

#: ``client(state, questions) -> response``; raises on failure.
Client = Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]]


class JevSender:
    """Paced, threaded requests; every answer written the moment it arrives."""

    def __init__(self, client: Client, cache: Dict[str, Dict[str, Any]], save: Callable[[], None],
                 log: Optional[Path], workers: int = WORKERS, pause: float = 0.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.client, self.cache, self.save, self.log = client, cache, save, log
        self.workers, self.pause, self.sleep = max(1, workers), pause, sleep
        self.lock = threading.Lock()
        self.tokens = 0
        self.failures = 0
        self.stopped = False
        self.sent = 0

    def run(self, jobs: Sequence[Tuple[Dict[str, Any], Dict[str, Any]]]) -> None:
        """``(variant, request)`` pairs."""
        todo: "queue.Queue[Tuple[int, Tuple[Dict[str, Any], Dict[str, Any]]]]" = queue.Queue()
        for n, job in enumerate(jobs, 1):
            todo.put((n, job))
        total = len(jobs)

        def worker() -> None:
            first = True
            while not self.stopped:
                try:
                    n, (variant, req) = todo.get_nowait()
                except queue.Empty:
                    return
                if not first and self.pause > 0:
                    self.sleep(self.pause)
                first = False
                col = column(variant)
                try:
                    out = self.client(req["state"], req["questions"])
                except urllib.error.HTTPError as exc:
                    if 400 <= exc.code < 500 and exc.code != 429:
                        # Rejected, not down: cached as unanswered so a rerun
                        # does not pay for the same refusal.
                        with self.lock:
                            got = self.cache.setdefault(col, {})
                            for iid in req["items"].values():
                                got[iid] = {"p": None, "error": exc.code}
                            self.save()
                            print("  request %d: HTTP %d, %d item(s) unanswered"
                                  % (n, exc.code, len(req["items"])), file=sys.stderr)
                        continue
                    self._fail(n, exc)
                    continue
                except Exception as exc:  # one failed request must not end the run
                    self._fail(n, exc)
                    continue
                tokens = int(((out or {}).get("usage") or {}).get("input_tokens") or 0)
                with self.lock:
                    self.failures = 0
                    self.tokens += tokens
                    self.sent += 1
                    self.cache.setdefault(col, {}).update(read_answers(variant, req, out or {}))
                    self.save()
                    if self.log is not None:
                        with open(self.log, "a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"column": col, "questions": len(req["questions"]),
                                                 "chars": request_chars(req), "in": tokens}) + "\n")
                    if n % 50 == 0 or n == total:
                        print("  jev request %d/%d, %d tokens so far (~$%.4f)"
                              % (n, total, self.tokens, self.tokens * USD_PER_MTOK / 1e6), file=sys.stderr)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(self.workers, total))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def _fail(self, n: int, exc: Exception) -> None:
        with self.lock:
            self.failures += 1
            print("  request %d: %s: %s" % (n, type(exc).__name__, str(exc)[:200]), file=sys.stderr)
            if self.failures >= MAX_FAILURES:
                self.stopped = True
                print("  %d failures in a row: stopping; rerun to resume" % self.failures, file=sys.stderr)


# --- the numbers -----------------------------------------------------------------------------

def kappa_from(counts: Dict[Tuple[Any, Any], int]) -> Optional[float]:
    """Cohen's kappa from a confusion count ``(a, b) -> n``, any categories."""
    n = sum(counts.values())
    if not n:
        return None
    po = sum(v for (a, b), v in counts.items() if a == b) / n
    left: Counter = Counter()
    right: Counter = Counter()
    for (a, b), v in counts.items():
        left[a] += v
        right[b] += v
    pe = sum(left[c] * right[c] for c in set(left) | set(right)) / (n * n)
    return None if pe >= 1 else (po - pe) / (1 - pe)


def auc_from(pos: Dict[int, int], neg: Dict[int, int]) -> Optional[float]:
    """Mann-Whitney AUC from score histograms (bin -> count); ties count half."""
    np_, nn = sum(pos.values()), sum(neg.values())
    if not np_ or not nn:
        return None
    below = 0
    total = 0.0
    for b in sorted(set(pos) | set(neg)):
        p, q = pos.get(b, 0), neg.get(b, 0)
        total += p * (below + 0.5 * q)
        below += q
    return total / (np_ * nn)


def _bin(p: float) -> int:
    return int(round(max(0.0, min(1.0, p)) * BINS))


def decide(p: float, t: float, positive: Any, negative: Any) -> Any:
    return positive if p >= t else negative


def agreed_label(labels: Dict[str, Any], raters: Sequence[str]) -> Optional[Any]:
    vals = [labels[r] for r in raters]
    return vals[0] if all(v == vals[0] for v in vals) else None


def group_stats(items: Sequence[Dict[str, Any]], ps: Dict[str, float], t: float, q: Dict[str, Any]
                ) -> Dict[str, Dict[str, Any]]:
    """Per group, the sufficient statistics of every number in the report:
    confusion counts Jev-vs-each-rater and rater-vs-rater, and the AUC's two
    histograms. A bootstrap over groups sums these instead of re-reading items."""
    r1, r2 = q["raters"]
    out: Dict[str, Dict[str, Any]] = {}
    for it in items:
        p = ps.get(it["id"])
        if p is None:
            continue
        g = out.setdefault(it["group"], {"j1": Counter(), "j2": Counter(), "rr": Counter(),
                                         "pos": Counter(), "neg": Counter(), "n": 0})
        j = decide(p, t, q["positive"], q["negative"])
        a, b = it["labels"][r1], it["labels"][r2]
        g["j1"][(j, a)] += 1
        g["j2"][(j, b)] += 1
        g["rr"][(a, b)] += 1
        g["n"] += 1
        both = agreed_label(it["labels"], q["raters"])
        if both == q["positive"]:
            g["pos"][_bin(p)] += 1
        elif both is not None:
            g["neg"][_bin(p)] += 1
    return out


def summarise(groups: Sequence[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    j1: Counter = Counter()
    j2: Counter = Counter()
    rr: Counter = Counter()
    pos: Counter = Counter()
    neg: Counter = Counter()
    n = 0
    for g in groups:
        j1.update(g["j1"])
        j2.update(g["j2"])
        rr.update(g["rr"])
        pos.update(g["pos"])
        neg.update(g["neg"])
        n += g["n"]
    k1, k2, krr = kappa_from(j1), kappa_from(j2), kappa_from(rr)
    mean = None if k1 is None or k2 is None else (k1 + k2) / 2
    return {"n": n, "kappa_jev_r1": k1, "kappa_jev_r2": k2, "kappa_jev": mean, "kappa_raters": krr,
            "diff": None if mean is None or krr is None else mean - krr,
            "auc": auc_from(pos, neg), "both_pos": sum(pos.values()), "both_neg": sum(neg.values())}


def _pct(values: List[float], q: float) -> float:
    values = sorted(values)
    return values[min(len(values) - 1, max(0, int(q * len(values))))]


def bootstrap(stats: Dict[str, Dict[str, Any]], draws: int = BOOTSTRAP, seed: int = SEED
              ) -> Dict[str, Optional[List[float]]]:
    """95% percentile CIs of every summary number, groups resampled."""
    keys = sorted(stats)
    rng = random.Random(seed)
    seen: Dict[str, List[float]] = {}
    for _ in range(draws if keys else 0):
        s = summarise([stats[keys[rng.randrange(len(keys))]] for _ in keys])
        for k, v in s.items():
            if v is not None and k not in ("n", "both_pos", "both_neg"):
                seen.setdefault(k, []).append(v)
    out: Dict[str, Optional[List[float]]] = {}
    for k in ("kappa_jev_r1", "kappa_jev_r2", "kappa_jev", "kappa_raters", "diff", "auc"):
        vals = seen.get(k) or []
        out[k] = [_pct(vals, 0.025), _pct(vals, 0.975)] if vals else None
    return out


def tune(items: Sequence[Dict[str, Any]], ps: Dict[str, float], q: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """The threshold that maximises Jev's mean kappa on these items; ties go
    to the threshold nearest 0.5. Read on dev, never on test.

    Items are folded once into counts by (score bin, rater labels), so each
    threshold on the grid costs a pass over at most ``BINS`` x labels cells,
    not over the items: #520's relevance dev half is 61k pairs."""
    r1, r2 = q["raters"]
    cells: Counter = Counter()
    for it in items:
        p = ps.get(it["id"])
        if p is not None:
            cells[(_bin(p), it["labels"][r1], it["labels"][r2])] += 1
    best: Optional[Tuple[float, float, float]] = None
    for t in GRID:
        j1: Counter = Counter()
        j2: Counter = Counter()
        for (b, a, c), n in cells.items():
            j = decide(b / float(BINS), t, q["positive"], q["negative"])
            j1[(j, a)] += n
            j2[(j, c)] += n
        k1, k2 = kappa_from(j1), kappa_from(j2)
        if k1 is None or k2 is None:
            continue
        key = ((k1 + k2) / 2, -abs(t - 0.5), t)
        if best is None or key > best:
            best = key
    t = 0.5 if best is None else best[2]
    return t, summarise(list(group_stats(items, ps, t, q).values()))


def answered(cache: Dict[str, Dict[str, Any]], variant: Dict[str, Any], items: Sequence[Dict[str, Any]]
             ) -> Tuple[Dict[str, float], int, int]:
    """(probabilities by item id, items asked, items unanswerable)."""
    got = cache.get(column(variant)) or {}
    ps = {}
    unanswerable = 0
    asked = 0
    for it in items:
        if it["id"] in got:
            asked += 1
            p = got[it["id"]].get("p")
            if p is None:
                unanswerable += 1
            else:
                ps[it["id"]] = float(p)
    return ps, asked, unanswerable


def split(items: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {DEV: [], TEST: []}
    for it in items:
        out[part_of(it)].append(it)
    return out


def pending(question: str, items: Sequence[Dict[str, Any]], cache: Dict[str, Dict[str, Any]],
            lock: Dict[str, Any]) -> List[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
    """``(variant, items still to ask)``: every wording on dev until the
    question is locked, then the locked wording on test — never test before."""
    halves = split(items)
    if question not in lock:
        out = []
        for v in variants_of(question):
            got = cache.get(column(v)) or {}
            todo = [it for it in halves[DEV] if it["id"] not in got]
            if todo:
                out.append((v, todo))
        return out
    v = locked_variant(question, lock)
    if v is None:
        return []
    got = cache.get(column(v)) or {}
    todo = [it for it in halves[TEST] if it["id"] not in got]
    return [(v, todo)] if todo else []


def locked_variant(question: str, lock: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    entry = lock.get(question) or {}
    for v in variants_of(question):
        if column(v) == entry.get("column"):
            return v
    return None


def try_lock(question: str, items: Sequence[Dict[str, Any]], cache: Dict[str, Dict[str, Any]],
             lock: Dict[str, Any], now: Optional[str] = None) -> bool:
    """Lock the best wording and threshold once every wording has answered
    every dev item (answered or refused). Returns whether a lock was written;
    an existing lock is never touched."""
    if question in lock:
        return False
    q = QUESTIONS[question]
    dev = split(items)[DEV]
    if not dev:
        return False
    rows = []
    for v in variants_of(question):
        ps, asked, bad = answered(cache, v, dev)
        if asked < len(dev):
            return False
        t, s = tune(dev, ps, q)
        rows.append({"variant": v["name"], "column": column(v), "threshold": t, "unanswered": bad,
                     "kappa_jev": s["kappa_jev"], "kappa_raters": s["kappa_raters"], "auc": s["auc"], "n": s["n"]})
    scored = [r for r in rows if r["kappa_jev"] is not None]
    if not scored:
        return False
    best = max(scored, key=lambda r: (r["kappa_jev"], -rows.index(r)))
    lock[question] = {"column": best["column"], "variant": best["variant"], "threshold": best["threshold"],
                      "dev": rows, "dev_items": len(dev),
                      "locked_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return True


def evaluate(question: str, items: Sequence[Dict[str, Any]], cache: Dict[str, Dict[str, Any]],
             lock: Dict[str, Any], draws: int = BOOTSTRAP) -> Dict[str, Any]:
    """The question's report row: the split, the lock, and the test half."""
    q = QUESTIONS[question]
    halves = split(items)
    everything = summarise(list(group_stats(items, {it["id"]: 0.0 for it in items}, 0.5, q).values()))
    row: Dict[str, Any] = {"question": question, "about": q["about"], "raters": list(q["raters"]),
                           "items": len(items), "dev": len(halves[DEV]), "test": len(halves[TEST]),
                           "kappa_raters_all": everything["kappa_raters"],
                           "both_positive_all": everything["both_pos"], "both_negative_all": everything["both_neg"],
                           "status": "pending"}
    entry = lock.get(question)
    if entry is None:
        return row
    row.update(variant=entry["variant"], threshold=entry["threshold"], dev_table=entry["dev"])
    v = locked_variant(question, lock)
    if v is None:
        row["status"] = "stale lock: its wording is no longer in VARIANTS"
        return row
    test = halves[TEST]
    ps, asked, bad = answered(cache, v, test)
    row.update(test_asked=asked, test_unanswered=bad)
    if asked < len(test):
        row["status"] = "test pending (%d of %d asked)" % (asked, len(test))
        return row
    stats = group_stats(test, ps, entry["threshold"], q)
    s = summarise(list(stats.values()))
    row.update(test_summary=s, test_ci=bootstrap(stats, draws))
    if s["kappa_jev"] is None or s["kappa_raters"] is None:
        row["status"] = "unmeasurable"
    else:
        row["status"] = "done"
        row["qualifies"] = s["kappa_jev"] >= s["kappa_raters"]
    return row


# --- the report ----------------------------------------------------------------------------------

def _f(x: Optional[float], d: int = 2) -> str:
    return "n/a" if x is None else ("%+.*f" % (d, x) if d < 0 else "%.*f" % (d, x))


def _ci(ci: Optional[List[float]]) -> str:
    return "" if not ci else " [%.2f, %.2f]" % (ci[0], ci[1])


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["Jev (%s) against two raters, per question (#529)" % MODEL, ""]
    for row in data["rows"]:
        r1, r2 = row["raters"]
        lines.append("%s — %s" % (row["question"], row["about"]))
        lines.append("  items %d (dev %d, test %d); raters' kappa on all items %s; both say %s on %d, "
                     "both say %s on %d" % (row["items"], row["dev"], row["test"], _f(row.get("kappa_raters_all")),
                                            QUESTIONS[row["question"]]["positive"], row.get("both_positive_all", 0),
                                            QUESTIONS[row["question"]]["negative"], row.get("both_negative_all", 0)))
        for d in row.get("dev_table") or []:
            lines.append("  dev  %-8s t=%.3f  Jev mean kappa %s  (raters %s)  AUC %s  n=%d%s" % (
                d["variant"], d["threshold"], _f(d["kappa_jev"]), _f(d["kappa_raters"]), _f(d["auc"]),
                d["n"], ", %d unanswered" % d["unanswered"] if d["unanswered"] else ""))
        if row["status"] != "done":
            lines.append("  status: %s" % row["status"])
            lines.append("")
            continue
        s, ci = row["test_summary"], row["test_ci"]
        lines.append("  TEST locked %s t=%.3f, n=%d%s" % (row["variant"], row["threshold"], s["n"],
                     ", %d unanswered" % row["test_unanswered"] if row["test_unanswered"] else ""))
        lines.append("    raters' kappa with each other  %s%s" % (_f(s["kappa_raters"]), _ci(ci["kappa_raters"])))
        lines.append("    Jev mean kappa                 %s%s  (vs %s %s, vs %s %s)" % (
            _f(s["kappa_jev"]), _ci(ci["kappa_jev"]), r1, _f(s["kappa_jev_r1"]), r2, _f(s["kappa_jev_r2"])))
        lines.append("    difference                     %s%s" % (_f(s["diff"]), _ci(ci["diff"])))
        lines.append("    AUC vs both-agree label        %s%s  (%d positive, %d negative)" % (
            _f(s["auc"]), _ci(ci["auc"]), s["both_pos"], s["both_neg"]))
        lines.append("    QUALIFIES: %s" % ("yes" if row["qualifies"] else "no — stays with the two raters"))
        lines.append("")
    done = [r for r in data["rows"] if r["status"] == "done"]
    if done:
        lines.append("Jev qualifies for: %s" % (", ".join(r["question"] for r in done if r["qualifies"]) or "none"))
        lines.append("Stays with the raters: %s" % (", ".join(r["question"] for r in done if not r["qualifies"]) or "none"))
    lines.append("Jev spend on file: %d input tokens, $%.4f notional over %d requests"
                 % (data["cost"]["jev_tokens"], data["cost"]["jev_usd"], data["cost"]["jev_requests"]))
    for r, c in sorted((data["cost"].get("briefing_raters") or {}).items()):
        lines.append("Briefing labels, %s: $%.2f notional over %d calls (subscription usage)" % (r, c["usd"], c["calls"]))
    return lines


def jev_spent(log: Path) -> Dict[str, Any]:
    tokens = requests = 0
    try:
        rows = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        rows = []
    for line in rows:
        try:
            tokens += int(json.loads(line).get("in") or 0)
            requests += 1
        except ValueError:
            continue
    return {"jev_tokens": tokens, "jev_usd": round(tokens * USD_PER_MTOK / 1e6, 4), "jev_requests": requests}


# --- loading what exists on the vault ------------------------------------------------------------

class Loader:
    """Builds each question's items from the caches on disk, sharing the
    expensive pieces (#520's rule index, transcript walks) between questions."""

    def __init__(self, vault: Path, projects: Path, claude_home: Path, out: Path) -> None:
        self.vault, self.projects, self.claude_home, self.out = vault, projects, claude_home, out
        self.pr = vault / ".mnemo" / "prevented-repeats"
        self._rules: Any = None
        self._walked: Dict[str, Dict[str, Any]] = {}
        self._relevance: Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = None

    def rules(self) -> Any:
        if self._rules is None:
            print("  building #520's rule index…", file=sys.stderr)
            self._rules = mpr.Rules(self.vault, self.projects, self.claude_home)
        return self._rules

    def walk(self, sid: str, path: str) -> Dict[str, Any]:
        from mnemo.core.briefing import _load_jsonl_events

        if sid not in self._walked:
            self._walked[sid] = mpr.walk(_load_jsonl_events(Path(path)))
        return self._walked[sid]

    def freq(self) -> Dict[str, Dict[str, Any]]:
        all_freq = mrc._read(self.pr / "frequency.json", {})
        return {r: all_freq.get(mrc.column(r, mpr.FREQ_SYSTEM), {}) for r in RATERS}

    def strict(self) -> Optional[Set[str]]:
        rules = self.rules()
        transcripts = {p.stem: p for p in self.projects.glob("*/*.jsonl")}
        evidence = mpr.evidence_items(self.vault, rules.facts, transcripts)
        seeded = mrc._read(self.vault / ".mnemo" / "repeated-corrections" / "labels.json", {})
        own = mrc._read(self.pr / "labels.json", {})
        labels = {}
        for r in RATERS:
            col = mrc.column(r, mrc.LABEL_SYSTEM)
            labels[r] = dict(seeded.get(col, {}), **own.get(col, {}))
        return mpr.strict_pool(evidence, labels)

    def relevance(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        if self._relevance is None:
            chunks = mrc._read(self.pr / "chunks.json", {})
            self._relevance = relevance_items(chunks, self.freq(), self.rules().text, self.strict())
        return self._relevance

    def units(self) -> List[Dict[str, Any]]:
        """#520's delivery units, rebuilt as its ``main`` builds them."""
        meta = mrc._read(self.pr / mpr.UNITS_NAME, {}).get("sessions") or {}
        chunks_file = mrc._read(self.pr / "chunks.json", {})
        freq = self.freq()
        chunks = [c for sid in sorted(meta) for c in chunks_file.get(sid, [])]
        rel = mpr.relevant(freq, RATERS, chunks)
        rated = set(meta)
        unit_prompts: Dict[Tuple[str, str], List[int]] = {}
        for per in rel.values():
            for key, slugs in per.items():
                sid, i = key.rsplit("#", 1)
                if sid not in rated:
                    continue
                for slug in slugs:
                    lst = unit_prompts.setdefault((sid, slug), [])
                    if int(i) not in lst:
                        lst.append(int(i))
        rules = self.rules()
        out: List[Dict[str, Any]] = []
        for (sid, slug), idxs in sorted(unit_prompts.items()):
            m = meta[sid]
            w = self.walk(sid, m["path"])
            by_i = {p["i"]: p for p in w["prompts"]}
            if max(idxs) not in by_i:
                continue
            last = by_i[max(idxs)]
            if m["cwd"] not in rules.ctx._roots:
                rules.ctx._roots[m["cwd"]] = mrc.repo_root_for(m["cwd"])
            root = rules.ctx._roots[m["cwd"]]
            mem_dir = mrc.memory_dir_for(m["cwd"], root, rules.ctx.claude_home)

            def fallback(root: Any = root, mem_dir: Any = mem_dir, ts: float = last["ts"]) -> List[Tuple[str, str]]:
                files = mrc.claude_md_as_of(root, ts, rules.ctx.claude_home)
                index, _ = mrc.memory_as_of(mem_dir, ts, "")
                return files + ([("MEMORY.md (as of then)", index)] if index else [])
            _, notes = mrc.memory_as_of(mem_dir, last["ts"], rules.text(slug))
            ss = mpr._head("\n\n".join(w["session_start"][:last["session_start_n"]]), mpr.SESSION_START_CHARS)
            files = mpr.native_files(last, fallback)
            texts = mpr._sha(sid, ss, *[t for f in files for t in f])
            out.append({"id": mpr._sha(texts, slug), "texts": texts, "session_id": sid, "slug": slug,
                        "rule": rules.text(slug), "files": files, "notes": notes[:mpr.NOTES_PER_RULE],
                        "empty": not ss and not files})
        # Text B carries the notes of every unit that shares its texts, as a batch did.
        notes_by: Dict[str, List[Tuple[str, str]]] = {}
        for u in out:
            lst = notes_by.setdefault(u["texts"], [])
            for note in u["notes"]:
                if note not in lst:
                    lst.append(note)
        for u in out:
            u["text_b"] = mpr.native_text(u["files"], notes_by[u["texts"]])
        return out

    def items(self, question: str) -> List[Dict[str, Any]]:
        if question == "relevant_broad":
            return self.relevance()[0]
        if question == "relevant_strict":
            return self.relevance()[1]
        if question == "redundant":
            all_deliv = mrc._read(self.pr / "delivery.json", {})
            deliv = {r: all_deliv.get(mrc.column(r, mpr.DELIVERY_SYSTEM), {}) for r in RATERS}
            units = self.units()
            items = redundant_items(units, deliv)
            answered_ids = set.intersection(*[set(deliv[r]) for r in RATERS]) if all(deliv.values()) else set()
            print("  redundant: %d of %d units the raters answered were rebuilt byte for byte"
                  % (len(items), len(answered_ids)), file=sys.stderr)
            return items
        if question == "correction":
            base = self.vault / ".mnemo" / "repeated-corrections"
            all_labels = mrc._read(base / "labels.json", {})
            labels = {r: all_labels.get(mrc.column(r, mrc.LABEL_SYSTEM), {}) for r in RATERS}
            return correction_items(mrc._read(base / "items.json", []), labels)
        if question == "generic":
            base = self.vault / ".mnemo" / "audit-2026-09-22"
            labels = {r: _read(base / ("labels-%s.json" % r), {}) for r in AUDIT_RATERS}
            return generic_items(_read(base / "sample.json", []), labels)
        if question == "better":
            base = self.vault / ".mnemo" / mbv.OUT_DIR
            arms = mrc._read(base / "arms.json", {})
            answers_file = mrc._read(base / "answers.json", {})
            answers = next(iter(answers_file.values()), {}) if len(answers_file) == 1 else {}
            all_verdicts = mrc._read(base / "verdicts.json", {})
            verdicts = {r: all_verdicts.get(mrc.column(r, mbv.JUDGE_SYSTEM), {}) for r in RATERS}
            return better_items(arms, answers, verdicts)
        if question == "briefing":
            records = _read(self.out / "briefing-items.json", {}).get("sessions") or []
            all_labels = _read(self.out / "briefing-labels.json", {})
            labels = {r: all_labels.get(mrc.column(r, BRIEFING_SYSTEM), {}) for r in RATERS}
            return briefing_items(records, labels)
        raise KeyError(question)

    def briefing_candidates(self, since: str) -> Dict[str, Dict[str, Any]]:
        """Every human session since ``since`` that was handed a briefing."""
        from mnemo.core.briefing import _load_jsonl_events

        sessions = mpr.collect_sessions(self.projects, self.vault, since)
        home_claude = str(self.claude_home.resolve())
        out = {}
        for sid, m in sessions.items():
            if (m.get("cwd") or "").startswith(home_claude):
                continue  # model probes and job scratch, not a person's work
            events = _load_jsonl_events(Path(m["path"]))
            rec = briefing_record(sid, mpr.walk(events), events)
            if rec is not None:
                out[sid] = dict(rec, project=m["project"], start=m["start"])
        return out


# --- driver ----------------------------------------------------------------------------------------

def label_briefings(loader: Loader, out: Path, send: bool, workers: int, pause: float,
                    limit: Optional[int], since: str) -> None:
    from mnemo.core import config, llm

    frozen_path = out / "briefing-items.json"
    frozen = _read(frozen_path, {})
    if not frozen.get("sessions"):
        print("  drawing the briefing sessions since %s…" % since, file=sys.stderr)
        cands = loader.briefing_candidates(since)
        drawn = draw_briefing_sessions(list(cands))
        frozen = {"since": since, "population": len(cands), "seed": SEED,
                  "sessions": [cands[s] for s in drawn]}
        _write(frozen_path, frozen)
    records = frozen["sessions"]
    labels_path = out / "briefing-labels.json"
    all_labels = _read(labels_path, {})
    cols = {r: all_labels.setdefault(mrc.column(r, BRIEFING_SYSTEM), {}) for r in RATERS}
    calls = [(r, rec) for rec in records for r in RATERS if rec["id"] not in cols[r]]
    if not send:
        est = sum(mpr.notional(r, [(briefing_prompt(rec), BRIEFING_SYSTEM)], 80) for r, rec in calls)
        print("briefing set: %d sessions drawn of %d since %s; %d rater call(s) pending, notional ~$%.2f"
              % (len(records), frozen.get("population", 0), frozen.get("since"), len(calls), est), file=sys.stderr)
        return
    cfg = config.load_config()
    provider = llm.resolve(cfg)
    timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
    sender = mpr.Sender(provider, timeout, out / "briefing-calls.jsonl", workers, max(pause, 1.0))

    def on_reply(r: str, rec: Dict[str, Any]) -> Callable[[str], None]:
        def take(text: str) -> None:
            got = parse_briefing(text)
            if got is not None:
                cols[r][rec["id"]] = got
                _write(labels_path, all_labels)
        return take

    todo = calls[:limit] if limit else calls
    here = os.getcwd()
    scratch = tempfile.mkdtemp(prefix="mnemo-jev-agreement-")
    os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches a rater
    try:
        sender.run([(r, briefing_prompt(rec), BRIEFING_SYSTEM, on_reply(r, rec)) for r, rec in todo])
    finally:
        os.chdir(here)
        shutil.rmtree(scratch, ignore_errors=True)
    print("briefing labels: spent $%.2f notional this run" % sender.usd, file=sys.stderr)


def run(items_by_q: Dict[str, List[Dict[str, Any]]], out: Path, client: Optional[Client], *,
        workers: int = WORKERS, pause: float = PAUSE_SECONDS, limit: Optional[int] = None,
        draws: int = BOOTSTRAP, sleep: Callable[[float], None] = time.sleep) -> Dict[str, Any]:
    """Send what is pending (when ``client`` is given), lock what dev settles,
    send the locked test halves, and return the report."""
    cache_path, lock_path = out / "answers.json", out / "lock.json"
    cache: Dict[str, Dict[str, Any]] = _read(cache_path, {})
    lock: Dict[str, Any] = _read(lock_path, {})
    save = lambda: _write(cache_path, cache)  # noqa: E731

    def jobs_now() -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        seen: Set[Tuple[str, str]] = set()
        jobs = []
        for qn, items in items_by_q.items():
            for v, todo in pending(qn, items, cache, lock):
                fresh = [it for it in todo if (column(v), it["id"]) not in seen]
                seen.update((column(v), it["id"]) for it in fresh)
                jobs += [(v, req) for req in requests_for(v, fresh)]
        return jobs

    def lock_ready() -> None:
        changed = False
        for qn, items in items_by_q.items():
            changed = try_lock(qn, items, cache, lock) or changed
        if changed:
            _write(lock_path, lock)

    lock_ready()
    if client is not None:
        sender = JevSender(client, cache, save, out / "calls.jsonl", workers, pause, sleep)
        for _phase in (DEV, TEST):
            jobs = jobs_now()
            if limit is not None:
                jobs = jobs[:max(0, limit - sender.sent)]
            if jobs and not sender.stopped:
                sender.run(jobs)
            lock_ready()
    todo = jobs_now()
    rows = [evaluate(qn, items, cache, lock, draws) for qn, items in items_by_q.items()]
    return {"rows": rows, "lock": lock, "cost": jev_spent(out / "calls.jsonl"),
            "pending_requests": len(todo),
            "pending_usd": round(sum(request_chars(r) for _, r in todo) / CHARS_PER_TOKEN * USD_PER_MTOK / 1e6, 4)}


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, paths
    from mnemo.core.mcp import rerank

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--question", action="append", choices=sorted(QUESTIONS), default=[])
    ap.add_argument("--vault", default="")
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/jev-agreement)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--label-briefings", action="store_true",
                    help="draw and label the briefing set with the two Claude raters")
    ap.add_argument("--since", default="", help="briefing sessions since (default: %d days ago)" % BRIEFING_DAYS)
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N requests or calls")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    loader = Loader(vault, Path(args.projects), Path(args.claude_home), out)

    if args.label_briefings:
        since = args.since or (datetime.now(timezone.utc) - timedelta(days=BRIEFING_DAYS)).date().isoformat()
        label_briefings(loader, out, args.send, args.workers, args.pause, args.limit, since)
        return 0

    wanted = args.question or list(QUESTIONS)
    items_by_q = {}
    for qn in wanted:
        print("loading %s…" % qn, file=sys.stderr)
        items_by_q[qn] = loader.items(qn)

    client = None
    if args.send:
        key, _source = rerank.resolve_key({"provider": "typesafe"})
        if not key:
            print("error: --send needs a TypeSafe key (`mnemo rerank --setup` or TYPESAFE_API_KEY)", file=sys.stderr)
            return 1
        client = rerank.typesafe_client(key, model=MODEL, timeout=TIMEOUT_S)
    data = run(items_by_q, out, client, workers=args.workers, pause=args.pause, limit=args.limit)
    print("pending: %d Jev request(s), ~$%.4f notional" % (data["pending_requests"], data["pending_usd"]),
          file=sys.stderr)
    data["cost"]["briefing_raters"] = mrc.spent_by_rater(out / "briefing-calls.jsonl", RATERS)
    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    _write(out / "report.json", _provenance.stamp(data, prov))
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(data):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
