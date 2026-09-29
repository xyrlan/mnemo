"""When mnemo delivers a rule Claude Code's own memory lacked, is the answer better? (#527)

Usage:
    PYTHONPATH=src python3 tools/measure_broad_value.py              # what is pending, its cost, the report so far
    PYTHONPATH=src python3 tools/measure_broad_value.py --send       # locate, answer both arms, judge, report
        [--workers W] [--pause S] [--limit N]
    PYTHONPATH=src python3 tools/measure_broad_value.py --json       # the report as data

#520 counted the answers a delivered rule would *change*: relevant units
times delivery times #434's lift, and #434's lift measures whether an answer
*follows the rule*, not whether it is *better*. This asks the second question
of the same units, with a verdict against the maintainer's bar, set before any
measurement (:data:`THRESHOLD`):

- **positive** — helpful changes ≥ 1 per 15 human sessions and CI lower bound > 0;
- **null** — CI upper bound < 1 per 15;
- **negative** — the CI upper bound of ``h`` < 0: delivered rules make answers worse;
- **inconclusive** — none of these: extend the sessions once, then it counts as null.

**Units.** Every (session, rule) that #520's broad reading, both raters,
counted as relevant **and** delivered-and-new: ``units.json``, which
``measure_prevented_repeats`` writes beside its report (a run without
``--send`` rewrites it from its cache). A unit's prompt is the first of its
relevant prompts at which the rule was in context.

**Two arms at that prompt**, :data:`SAMPLES` samples each, #434's harness
(``measure_rule_lift.ARM_SYSTEM``, no tools, a scratch working directory, the
previous assistant turn), with what the session had in context around it, as
the transcript recorded it:

- the SessionStart hook's mnemo envelope, and what Claude Code loaded itself
  (the ``instructions`` attachment: ``CLAUDE.md`` files, ``MEMORY.md``), in
  both arms;
- every earlier block that carried the rule — a reflex block of an earlier
  prompt, a ``read_mnemo_rule`` or ``list_rules_by_topic`` result — and this
  prompt's own reflex block.

**with** is those texts as mnemo delivered them. **without** is the same
minus the rule's bytes: its entry in a reflex block (the head and, in #542's
full format, its whole body; #545), its line in the envelope, its entry in a
listing, the whole ``read_mnemo_rule`` result for it, and — when #520's
delivery judge found the envelope saying what the rule says without naming it
— the envelope lines a locator call (:data:`LOCATE_SYSTEM`, both raters,
union) says carry it. A block left with nothing but its header is dropped.
Each unit is answered with the model its session ran on (the assistant
message after the prompt), and the report says which.

**Blind pairwise judge.** Each rater (:data:`RATERS`, two models) compares one
*with* sample against one *without* sample (``k``-th with ``k``-th), seeing the
repository's ``CLAUDE.md``, the previous turn, the prompt and the two replies
— never the rule — with slugs, ``[[…]]`` and ``read_mnemo_rule`` masked in
both as #434 does. Which reply comes first is seeded per comparison and
flipped for the second rater, so a rater's position bias cancels rather than
agrees. Primary reading: both raters named the same reply; a disagreement
is a tie.

**Metric.** Per unit, net help ``h = P(with better) − P(without better)`` over
its comparisons. Helpful changes per human session = (delivered-and-new units
per session, #520) × mean ``h``, both from the same resample of sessions.

First run, 2026-09-28, the maintainer's vault. At 120 sessions #520 gave 52
units, under the ~100 the issue asked for, so #520 was first extended to all
265 human sessions in its window: 138 units. Every one is measurable (the
locator found the envelope lines for all 44 SessionStart units). The answers
came from the session's own model: Opus 5.5 61, Fable 5.1 38, Opus 5 35,
others 4. **Both raters: h = +0.069 [−0.038, +0.174]; 0.52 units per session
[0.40, 0.67]; helpful changes 0.036 per human session (1 per 28) [−0.019,
+0.094], INCONCLUSIVE.** Only 271 human transcripts are on disk, 6 more than
the window holds, so the one extension the bar allows has nothing left to add,
and by the bar the result counts as **null**. Of the 276 comparisons, the rule
made the reply better in 34.1%, worse in 27.2% (the losses) and tied in 38.8%.
The raters agreed on 169 of 276 (kappa 0.24); read alone, Opus 5.5 gives
h +0.018 and Fable 5.1 gives +0.120 (0.062 per session [−0.006, +0.138]).
Secondary readings, each with a CI over units:

- by channel: reflex +0.037 (82 units), SessionStart +0.102 (44), MCP +0.175 (20);
- sessions before the reflex judge went live, +0.203 [+0.074, +0.331] (74);
  after, −0.086 [−0.227, +0.055] (64);
- by page type: project +0.189 [+0.019, +0.358], reference +0.025, feedback −0.077;
- the 2026-09-22 audit labelled only 6 of the 138 rules, too few to read.

The notional cost of every call on file was about $174: Fable 5.1 $87, Opus 5.5
$63, Opus 5 $22, others $2. That is subscription usage, not money.

Only ``--send`` calls a model. Calls are paced and threaded
(``measure_prevented_repeats.Sender``) and every answer is cached under
``--out`` (default ``<vault>/.mnemo/broad-value``) the moment it arrives, so a
rerun resumes. A unit's two arms are frozen in ``arms.json`` once built.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import random
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mpr = _sibling("measure_prevented_repeats")
rl = _sibling("measure_rule_lift")
mrc = mpr.mrc

RATERS = mpr.RATERS
PRICES = dict(mrc.PRICES, **{"claude-opus-5": (4.0, 20.0), "claude-fable-5": (10.0, 50.0)})
#: The maintainer's bar, set before any measurement: one helpful change per 15
#: human sessions, the bar #520 used.
THRESHOLD = mpr.THRESHOLD
JUDGE_LIVE = mpr.JUDGE_LIVE
SEED = 527
BOOTSTRAP = 4000
SAMPLES = 2
#: When the session's model cannot be read: #434's arms.
FALLBACK_MODEL = rl.DEFAULT_MODEL

UNITS_NAME = mpr.UNITS_NAME
OUT_DIR = "broad-value"
NATIVE_CHARS = 40000
CLAUDE_MD_CHARS = 20000
PAUSE_SECONDS = 2.0
WORKERS = 3
#: Tokens per character, for the notional estimate of pending calls.
CHARS_PER_TOKEN = mpr.CHARS_PER_TOKEN
ANSWER_TOKENS = rl.ANSWER_TOKENS

WITH, WITHOUT, TIE = "with", "without", "tie"
ARMS = (WITH, WITHOUT)
BOTH = "both"
CHANNELS = mpr.CHANNELS

LOCATE_SYSTEM = """\
You are shown a text an AI coding agent had in its context, with numbered
lines, and a rule. List every line that tells the agent what the rule says:
the same behaviour, fact or preference, or a part of it, so that an agent that
read the line would act as the rule says. Sharing a topic is not enough.

Reply with JSON only: {"lines": [3, 4]} (an empty list if no line does).
"""

JUDGE_SYSTEM = """\
You compare two replies an AI coding agent could send at the same moment of a
working session with a developer. You are shown the repository's CLAUDE.md,
the agent's previous message, the developer's message, and the two replies.

Which reply better serves this developer in this repository: more correct,
more useful, a better fit for what they asked and for how this repository
works? Length and polish are not merit on their own. If neither is better,
say tie. Some text may be masked as [...]; ignore that.

Reply with JSON only: {"better": "1" | "2" | "tie", "why": "<one sentence>"}
"""


# --- small helpers ---------------------------------------------------------------------

def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def same_rule(written: str, slug: str, project: str) -> bool:
    """A slug a hook wrote then names today's ``slug``: rules were renamed to
    ``<project>__<slug>`` after some transcripts were written."""
    return written == slug or (bool(project) and "%s__%s" % (project, written) == slug)


def short(slug: str) -> str:
    return slug.split("__", 1)[-1]


# --- what the session had in context at the unit's prompt ------------------------------

def context_at(events: List[dict], target: int) -> Optional[Dict[str, Any]]:
    """Everything recorded in context before typed turn ``target`` was answered.

    The turn index and the snapshot point are ``measure_prevented_repeats.walk``'s:
    a prompt's context runs up to the first assistant event after it (its own
    ``UserPromptSubmit`` context follows it in the file). Returns the prompt,
    the assistant text it answered, the mnemo SessionStart texts, the native
    ``instructions`` files, every reflex block with the prompt it came with,
    every mnemo MCP call with its result, and the model that answered.
    """
    from mnemo.core.friction import capture
    from mnemo.core.transcript import SYNTHETIC_TURN, plain_user_text

    answered = [a for a, _ in capture.exchanges(events)]
    session_start: List[str] = []
    native: Dict[str, str] = {}
    reflex: List[Tuple[int, str]] = []
    mcp: List[Dict[str, Any]] = []
    calls: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    current = -1
    found: Optional[Dict[str, Any]] = None
    n = 0
    tail_events: List[dict] = []
    for pos, ev in enumerate(events):
        if not isinstance(ev, dict):
            continue
        att = ev.get("attachment")
        if isinstance(att, dict):
            kind = att.get("type")
            hook = str(att.get("hookName") or att.get("hookEvent") or "")
            if kind == "hook_additional_context":
                for text in mpr._hook_texts(att):
                    if mpr._REFLEX in text:
                        reflex.append((current, text))
                    elif (hook.startswith("SessionStart") or not hook) and any(
                            m in text for m in mpr._MNEMO_START):
                        if text not in session_start:
                            session_start.append(text)
            elif kind == "instructions":
                for f in att.get("files") or []:
                    if isinstance(f, dict) and f.get("content"):
                        native[str(f.get("path") or "")] = str(f["content"])
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        if ev.get("type") == "assistant":
            if found is not None:
                tail_events = events[pos:]
                break
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    m = mpr._MCP_TOOL.search(str(b.get("name") or ""))
                    if m:
                        calls[str(b.get("id"))] = (m.group(1), b.get("input") or {})
            continue
        if ev.get("type") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    called = calls.get(str(b.get("tool_use_id")))
                    if called is not None:
                        mcp.append({"tool": called[0], "input": called[1], "text": mpr._result_text(b)})
        text = plain_user_text(content)
        if not text or SYNTHETIC_TURN.search(text):
            continue
        n += 1
        if mpr._SHELL_TURN.match(text):
            continue
        if found is not None:
            tail_events = events[pos:]
            break
        current = n - 1
        if current == target:
            found = {"i": target, "ts": mrc.epoch(ev.get("timestamp")), "text": text,
                     "answered": answered[target] if target < len(answered) else ""}
    if found is None:
        return None
    model = ""
    for ev in tail_events:
        msg = ev.get("message") if isinstance(ev, dict) else None
        m = str(msg.get("model") or "") if isinstance(msg, dict) and ev.get("type") == "assistant" else ""
        if m.startswith("claude-"):
            model = m
            break
    return dict(found, session_start=session_start, native=[[k, native[k]] for k in sorted(native)],
                reflex=reflex, mcp=mcp, model=model)


def carries_reflex(text: str, slug: str, project: str) -> bool:
    """Whether a reflex block delivered the rule (``measure_prevented_repeats.reflex_slugs``):
    in the full format an entry's head names it, not a ``[[link]]`` in
    another rule's body."""
    return any(same_rule(s, slug, project) for s in mpr.reflex_slugs(text))


def carries_mcp(call: Dict[str, Any], slug: str, project: str) -> bool:
    if call["tool"] == "read_mnemo_rule" and same_rule(str(call["input"].get("slug") or ""), slug, project):
        return True
    names = mpr._SLUG_KEY.findall(call["text"]) + mpr._WIKI.findall(call["text"])
    return any(same_rule(s, slug, project) for s in names)


def strip_reflex(text: str, slug: str, project: str) -> str:
    """The block without the rule's bytes; empty when no rule is left in it.

    In the full format that is the rule's whole entry, its head and every
    line of its body. An old one-line block loses the lines that name the
    rule, as it always did, so #527's frozen arms rebuild byte for byte.
    """
    if not mpr.is_full_block(text):
        kept = [ln for ln in text.splitlines() if not carries_reflex(ln, slug, project)]
        return "\n".join(kept) if any(mpr._WIKI.search(ln) for ln in kept) else ""
    lines = text.splitlines()
    first = next(i for i, ln in enumerate(lines) if mpr._ENTRY_HEAD.match(ln))
    entries = mpr.reflex_entries(text)
    kept = [(s, t) for s, t in entries if not (s and same_rule(s, slug, project))]
    if not any(s for s, _ in kept):
        return ""
    return "\n".join(lines[:first] + [t for _, t in kept])


def _drop_slug(obj: Any, slug: str, project: str) -> Any:
    if isinstance(obj, list):
        return [_drop_slug(x, slug, project) for x in obj
                if not (isinstance(x, dict) and same_rule(str(x.get("slug") or ""), slug, project))]
    if isinstance(obj, dict):
        return {k: _drop_slug(v, slug, project) for k, v in obj.items()}
    return obj


def strip_mcp(call: Dict[str, Any], slug: str, project: str) -> Optional[Dict[str, Any]]:
    """The call without the rule's bytes; None when nothing of it is left:
    the rule's own ``read_mnemo_rule``, or a listing of it alone."""
    if call["tool"] == "read_mnemo_rule" and same_rule(str(call["input"].get("slug") or ""), slug, project):
        return None
    try:
        payload = json.loads(call["text"])
    except ValueError:
        kept = [ln for ln in call["text"].splitlines() if not mpr._named(ln, slug)]
        return dict(call, text="\n".join(kept)) if "".join(kept).strip() else None
    stripped = _drop_slug(payload, slug, project)
    if stripped == [] or stripped == {}:
        return None
    return dict(call, text=json.dumps(stripped, ensure_ascii=False))


def envelope_lines(texts: Sequence[str]) -> List[str]:
    return [ln for t in texts for ln in t.splitlines()]


def named_lines(texts: Sequence[str], slug: str) -> List[int]:
    return [n for n, ln in enumerate(envelope_lines(texts), 1) if mpr._named(ln, slug)]


def strip_envelope(texts: Sequence[str], drop: Iterable[int]) -> List[str]:
    """The SessionStart texts without the numbered lines in ``drop``
    (numbered as :func:`envelope_lines` does, from 1)."""
    drop = set(drop)
    out, n = [], 0
    for t in texts:
        kept = []
        for ln in t.splitlines():
            n += 1
            if n not in drop:
                kept.append(ln)
        if "".join(kept).strip():
            out.append("\n".join(kept))
    return out


# --- the unit ------------------------------------------------------------------------

def unit_id(row: Dict[str, Any]) -> str:
    return _sha(row["session_id"], row["slug"])


def row_key(row: Dict[str, Any]) -> str:
    """What #520 said of the unit; a unit built on another reading is rebuilt."""
    return _sha(json.dumps([sorted(row["prompts"])] + [bool(row.get(c)) for c in CHANNELS]))


def place(row: Dict[str, Any], events: List[dict], project: str) -> Optional[Dict[str, Any]]:
    """The unit's prompt — the first relevant one with the rule in context —
    and that prompt's context. None when the transcript no longer has it."""
    slug = row["slug"]
    placed = [c for c in (context_at(events, i) for i in sorted(row["prompts"])) if c is not None]
    for ctx in placed:
        if (any(carries_reflex(t, slug, project) for _, t in ctx["reflex"])
                or any(carries_mcp(c, slug, project) for c in ctx["mcp"])
                or named_lines(ctx["session_start"], slug)):
            return ctx
    if placed and needs_locator(row):
        # #520's delivery judge found the envelope saying it by content, reading
        # every envelope up to the last relevant prompt: the first prompt with
        # all of them in context
        most = max(len(c["session_start"]) for c in placed)
        return next(c for c in placed if len(c["session_start"]) == most)
    return placed[0] if placed else None


def needs_locator(row: Dict[str, Any]) -> bool:
    """#520 said the envelope delivered it: find which lines, named or not."""
    return bool(row.get("session_start"))


def locate_prompt(rule: str, texts: Sequence[str]) -> str:
    lines = ["## Rule", rule, "", "## Text"]
    lines += ["%d: %s" % (n, ln) for n, ln in enumerate(envelope_lines(texts), 1)]
    return "\n".join(lines)


def parse_locate(text: str, n_lines: int) -> Optional[List[int]]:
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    got = payload.get("lines")
    if not isinstance(got, list):
        return None
    out = set()
    for x in got:
        try:
            v = int(x)
        except (TypeError, ValueError):
            continue
        if 1 <= v <= n_lines:
            out.add(v)
    return sorted(out)


def build_arms(row: Dict[str, Any], ctx: Dict[str, Any], project: str,
               located: Sequence[int]) -> Dict[str, Any]:
    """The unit's two arm prompts, and what the judge needs. ``located`` is the
    envelope lines the locator said carry the rule (``[]`` when none ran)."""
    slug = row["slug"]
    ss = ctx["session_start"]
    drop = sorted(set(named_lines(ss, slug)) | set(located))
    ss_without = strip_envelope(ss, drop)
    earlier_reflex = [t for owner, t in ctx["reflex"] if owner != ctx["i"] and carries_reflex(t, slug, project)]
    now_reflex = [t for owner, t in ctx["reflex"] if owner == ctx["i"]]
    mcp = [c for c in ctx["mcp"] if carries_mcp(c, slug, project)]
    with_parts = {"ss": ss, "earlier": earlier_reflex, "mcp": mcp, "now": now_reflex}
    without_parts = {
        "ss": ss_without,
        "earlier": [t for t in (strip_reflex(t, slug, project) for t in earlier_reflex) if t],
        "mcp": [c for c in (strip_mcp(c, slug, project) for c in mcp) if c is not None],
        "now": [t for t in (strip_reflex(t, slug, project) for t in now_reflex) if t],
    }
    carriers = {"session_start": bool(drop), "reflex": bool(earlier_reflex) or any(
        carries_reflex(t, slug, project) for t in now_reflex), "mcp": bool(mcp)}
    native = mpr._head("\n\n".join("Contents of %s:\n\n%s" % (p, t) for p, t in ctx["native"]), NATIVE_CHARS)
    claude_md = mpr._head("\n\n".join("### %s\n%s" % (p, t) for p, t in ctx["native"]
                                      if Path(p).name == "CLAUDE.md"), CLAUDE_MD_CHARS)
    previous = rl.tail(ctx["answered"])
    out = {"id": unit_id(row), "session_id": row["session_id"], "slug": slug, "project": project,
           "i": ctx["i"], "ts": ctx["ts"], "model": ctx["model"] or FALLBACK_MODEL,
           "model_from": "transcript" if ctx["model"] else "fallback",
           "prompt": ctx["text"], "previous": previous, "claude_md": claude_md,
           "carriers": carriers, "located": list(located), "dropped_lines": drop}
    out[WITH] = arm_prompt(ctx["text"], previous, native, with_parts)
    out[WITHOUT] = arm_prompt(ctx["text"], previous, native, without_parts)
    out["measurable"] = out[WITH] != out[WITHOUT]
    return out


def arm_prompt(prompt: str, previous: str, native: str, parts: Dict[str, Any]) -> str:
    """One arm: context blocks in the order Claude Code recorded them, then
    #434's previous turn and prompt, then the prompt's own hook context."""
    out = []
    for t in parts["ss"]:
        out.append("<system-reminder>\nSessionStart hook additional context: %s\n</system-reminder>" % t)
    if native:
        out.append("<system-reminder>\nAs you answer the developer's questions, you can use the "
                   "following context:\n%s\n</system-reminder>" % native)
    for t in parts["earlier"]:
        out.append("<system-reminder>\nEarlier in this session, UserPromptSubmit hook additional "
                   "context: %s\n</system-reminder>" % t)
    for c in parts["mcp"]:
        out.append("Earlier in this session you called mnemo's %s tool (%s); it returned:\n"
                   "<tool_result>\n%s\n</tool_result>"
                   % (c["tool"], json.dumps(c["input"], ensure_ascii=False, sort_keys=True), c["text"]))
    if previous:
        out.append("Your previous reply in this session:\n<assistant>\n%s\n</assistant>" % previous)
    out.append("The developer's next message:\n<user>\n%s\n</user>" % prompt)
    for t in parts["now"]:
        out.append("<system-reminder>\nUserPromptSubmit hook additional context: %s\n</system-reminder>" % t)
    return "\n\n".join(out)


# --- the judge ---------------------------------------------------------------------------

def mask(text: str, slug: str) -> Tuple[str, bool]:
    """#434's mask, and the slug without its project prefix too."""
    masked, hit = rl.mask(text, slug)
    s = short(slug)
    if s != slug and len(s) >= 8:
        masked, hit2 = rl.mask(masked, s)
        hit = hit or hit2
    return masked, hit


def comparison_id(uid: str, k: int) -> str:
    return "%s|%d" % (uid, k)


def with_first(cid: str, rater_index: int) -> bool:
    """Whether reply 1 is the *with* sample: seeded per comparison, flipped
    for the second rater."""
    first = int(hashlib.md5(("%d:%s" % (SEED, cid)).encode()).hexdigest(), 16) % 2 == 0
    return first if rater_index % 2 == 0 else not first


def judge_prompt(arms: Dict[str, Any], replies: Tuple[str, str]) -> str:
    return "\n\n".join([
        "## The repository's CLAUDE.md", arms["claude_md"] or "(none)",
        "## The agent's previous message", mask(arms["previous"], arms["slug"])[0] or "(none)",
        "## The developer's message", arms["prompt"],
        "## Reply 1", replies[0], "## Reply 2", replies[1]])


def comparison_prompt(arms: Dict[str, Any], answers: Dict[str, List[Dict[str, Any]]], k: int,
                      rater_index: int) -> Optional[str]:
    if any(len(answers.get(a, [])) <= k for a in ARMS):
        return None
    w = mask(answers[WITH][k]["text"], arms["slug"])[0]
    wo = mask(answers[WITHOUT][k]["text"], arms["slug"])[0]
    pair = (w, wo) if with_first(comparison_id(arms["id"], k), rater_index) else (wo, w)
    return judge_prompt(arms, pair)


def parse_judge(text: str, cid: str, rater_index: int) -> Optional[Dict[str, str]]:
    """``{"better": with|without|tie, "why"}``, or None when unreadable."""
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    said = str(payload.get("better") or "").strip().lower().strip('"').rstrip(".")
    said = {"reply 1": "1", "reply 2": "2", "neither": TIE}.get(said, said)
    if said not in ("1", "2", TIE):
        return None
    if said == TIE:
        better = TIE
    else:
        one_is_with = with_first(cid, rater_index)
        better = WITH if (said == "1") == one_is_with else WITHOUT
    return {"better": better, "why": str(payload.get("why") or "")[:400]}


# --- the numbers -------------------------------------------------------------------------

def agreed(votes: Sequence[Optional[str]]) -> Optional[str]:
    """Both raters' reading: the reply they both named, else a tie; None while one is missing."""
    if not votes or any(v is None for v in votes):
        return None
    return votes[0] if all(v == votes[0] for v in votes) else TIE


def unit_h(verdicts: Dict[str, Dict[str, Dict[str, str]]], uid: str, raters: Sequence[str],
           samples: int = SAMPLES) -> Optional[float]:
    """Net help over the unit's comparisons; None until every one is judged."""
    score = 0
    for k in range(samples):
        cid = comparison_id(uid, k)
        v = agreed([(verdicts.get(r, {}).get(cid) or {}).get("better") for r in raters])
        if v is None:
            return None
        score += {WITH: 1, WITHOUT: -1, TIE: 0}[v]
    return score / samples


def _pct(values: List[float], q: float) -> float:
    return values[min(len(values) - 1, max(0, int(q * len(values))))]


def combine(per_session: Sequence[Dict[str, Any]], n_boot: int = BOOTSTRAP,
            seed: int = SEED) -> Dict[str, Any]:
    """``per_session``: ``{"new": delivered-and-new units, "h": [h of the measured ones]}``
    per rated session. Rate, mean h and their product, resampled over sessions."""
    n = len(per_session)
    hs = [h for s in per_session for h in s["h"]]
    if not n or not hs:
        return {"sessions": n, "measured": len(hs)}
    new = sum(s["new"] for s in per_session)
    rng = random.Random(seed)
    boots: Dict[str, List[float]] = {"rate": [], "h": [], "E": []}
    for _ in range(n_boot):
        pick = [per_session[rng.randrange(n)] for _ in range(n)]
        rate = sum(s["new"] for s in pick) / n
        ph = [h for s in pick for h in s["h"]]
        mh = sum(ph) / len(ph) if ph else 0.0
        boots["rate"].append(rate)
        boots["h"].append(mh)
        boots["E"].append(rate * mh)
    ci = {k: [_pct(sorted(v), 0.025), _pct(sorted(v), 0.975)] for k, v in boots.items()}
    h = sum(hs) / len(hs)
    return {"sessions": n, "new": new, "measured": len(hs), "rate": new / n, "h": h,
            "E": new / n * h, "ci": ci}


def verdict(stats: Dict[str, Any], threshold: float = THRESHOLD) -> str:
    if stats.get("E") is None:
        return "no estimate yet"
    lo, hi = stats["ci"]["E"]
    if stats["ci"]["h"][1] < 0:
        return "negative"
    if stats["E"] >= threshold and lo > 0:
        return "positive"
    if hi < threshold:
        return "null"
    return "inconclusive"


def h_ci(hs: Sequence[float], n_boot: int = BOOTSTRAP, seed: int = SEED) -> Dict[str, Any]:
    """Mean h of a group of units with a bootstrap over the units (secondary readings)."""
    if not hs:
        return {"n": 0}
    rng = random.Random(seed)
    m = len(hs)
    boots = sorted(sum(hs[rng.randrange(m)] for _ in range(m)) / m for _ in range(n_boot))
    return {"n": m, "h": sum(hs) / m, "ci": [_pct(boots, 0.025), _pct(boots, 0.975)],
            "wins": sum(h > 0 for h in hs), "losses": sum(h < 0 for h in hs)}


def per_session_rows(rated: Sequence[str], new_units: Dict[str, List[str]],
                     h: Dict[str, Optional[float]]) -> List[Dict[str, Any]]:
    """``new_units``: session → unit ids; ``h``: unit id → its h (None: unmeasured)."""
    out = []
    for sid in sorted(rated):
        ids = new_units.get(sid, [])
        out.append({"new": len(ids), "h": [h[u] for u in ids if h.get(u) is not None]})
    return out


def outcome_shares(verdicts: Dict[str, Dict[str, Dict[str, str]]], uids: Sequence[str],
                   raters: Sequence[str], samples: int = SAMPLES) -> Dict[str, Any]:
    """Share of comparisons each way, on the both-raters reading."""
    got = {WITH: 0, WITHOUT: 0, TIE: 0}
    for uid in uids:
        for k in range(samples):
            v = agreed([(verdicts.get(r, {}).get(comparison_id(uid, k)) or {}).get("better")
                        for r in raters])
            if v is not None:
                got[v] += 1
    total = sum(got.values())
    return dict(got, comparisons=total, **{"share_" + k: (got[k] / total if total else None) for k in got})


# --- the vault -----------------------------------------------------------------------------

def page_facts(vault: Path) -> Dict[str, Dict[str, str]]:
    """``slug -> {type, name, text}`` for every page under ``shared/``."""
    from mnemo.core.filters import derive_rule_slug
    from mnemo.core.reclassify_types import split_frontmatter

    out: Dict[str, Dict[str, str]] = {}
    for page_type in ("feedback", "user", "reference", "project"):
        for md in sorted((vault / "shared" / page_type).glob("*.md")):
            try:
                fm, body = split_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            slug = derive_rule_slug(fm, md.stem)
            name = str(fm.get("name") or md.stem)
            out[slug] = {"type": page_type, "name": name,
                         "text": "%s\n%s" % (name, mpr._head(body, mpr.RULE_CHARS))}
    return out


def audit_labels(vault: Path) -> Dict[str, str]:
    """``short slug -> "junk" | "other"`` from the 2026-09-22 audit: junk when
    both blind raters called it generic (G) or narrative (N)."""
    base = vault / ".mnemo" / "audit-2026-09-22"
    key = mrc._read(base / "key.json", {})
    a = mrc._read(base / "labels-A.json", {})
    b = mrc._read(base / "labels-B.json", {})
    out = {}
    for aid, slug in key.items():
        if aid in a and aid in b:
            junk = all(str(x[aid].get("cat")) in ("G", "N") for x in (a, b))
            out[short(str(slug))] = "junk" if junk else "other"
    return out


def spent(log: Path) -> Dict[str, Dict[str, Any]]:
    """Notional USD and calls per model over every call logged."""
    out: Dict[str, Dict[str, Any]] = {}
    try:
        lines = log.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        model = str(row.get("system") or "").split("@")[0]
        got = out.setdefault(model, {"usd": 0.0, "calls": 0})
        got["usd"] += float(row.get("usd") or 0.0)
        got["calls"] += 1
    return out


def notional(model: str, prompts: Sequence[Tuple[str, str]], out_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, PRICES["claude-fable-5-1"])
    tin = sum((len(p) + len(s)) / CHARS_PER_TOKEN for p, s in prompts)
    return (tin * price_in + len(prompts) * out_tokens * price_out) / 1e6


# --- report --------------------------------------------------------------------------------

def _per(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return "%.4f (1 per %s)" % (x, "%.0f" % (1 / x) if x > 0 else "∞")


def _h(st: Dict[str, Any]) -> str:
    if not st.get("n"):
        return "no units"
    return "h %+.3f [%+.3f, %+.3f] over %d units (%d better, %d worse)" % (
        st["h"], st["ci"][0], st["ci"][1], st["n"], st["wins"], st["losses"])


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["%d rated human sessions (#520, broad reading, both raters): %d delivered-and-new units; "
             "%d placed, %d measurable, %d judged"
             % (data["sessions"], data["units"], data["placed"], data["measurable"], data["judged"])]
    if data.get("provisional"):
        lines.append("PROVISIONAL: %d more unit(s) wait for #520's delivery judge (rerun it with --send)"
                     % data["provisional"])
    if data.get("unmeasurable"):
        lines.append("  %d unit(s) had no rule bytes to remove (with == without): counted in the "
                     "rate, not in h" % data["unmeasurable"])
    lines.append("answer models: " + ", ".join("%s %d%s" % (m, n, "" if src == "transcript" else " (fallback)")
                                               for (m, src), n in sorted(data["models"].items())))
    for col, st in data["results"].items():
        primary = col == BOTH
        lines.append("")
        lines.append("%s%s:" % (col, "  <- the verdict" if primary else ""))
        if st.get("E") is None:
            lines.append("  no estimate yet")
            continue
        ci = st["ci"]
        lines += [
            "  rate      %.3f delivered-and-new units per session  [%.3f, %.3f]"
            % (st["rate"], ci["rate"][0], ci["rate"][1]),
            "  h         %+.3f net help per unit  [%+.3f, %+.3f]   (%d units)"
            % (st["h"], ci["h"][0], ci["h"][1], st["measured"]),
            "  estimate  %s helpful changes per human session  [%.4f, %.4f]"
            % (_per(st["E"]), ci["E"][0], ci["E"][1]),
            "  verdict   %s   (bar: >= %.4f = 1 per 15, CI lower bound > 0; negative if h's CI upper < 0)"
            % (verdict(st).upper(), THRESHOLD),
        ]
    sh = data.get("shares") or {}
    if sh.get("comparisons"):
        lines += ["", "comparisons (both raters): %d; with better %.1f%%, without better (losses) %.1f%%, "
                  "tie %.1f%%" % (sh["comparisons"], 100 * sh["share_with"], 100 * sh["share_without"],
                                  100 * sh["share_tie"])]
    for title, groups in data.get("breakdowns", {}).items():
        lines.append("")
        lines.append("h by %s (both raters; CI over units):" % title)
        for name, st in groups.items():
            lines.append("  %-22s %s" % (name, _h(st)))
    ag = data.get("agreement")
    if ag:
        lines += ["", "rater agreement on comparisons: %d of %d the same (%s)"
                  % (ag["same"], ag["n"], "kappa %.2f" % ag["kappa"] if ag.get("kappa") is not None else "kappa n/a")]
    for kind in ("wins", "losses"):
        ex = data.get("examples", {}).get(kind) or []
        if ex:
            lines += ["", "%s (h %s 0):" % (kind, ">" if kind == "wins" else "<")]
            for e in ex:
                lines.append("  %+.1f  %s  rule: %s" % (e["h"], e["model"], e["rule"]))
                lines.append("        prompt: %s" % mpr._head(e["prompt"].replace("\n", " "), 160))
                for why in e["why"]:
                    lines.append("        rater: %s" % mpr._head(why, 240))
    cost = data.get("cost") or {}
    if cost:
        lines += ["", "notional cost of every call on file: " + ", ".join(
            "%s $%.2f (%d calls)" % (m, v["usd"], v["calls"]) for m, v in sorted(cost.items()))
            + " — subscription usage, not money"]
    return lines


def _kappa3(a: Sequence[str], b: Sequence[str]) -> Optional[float]:
    n = len(a)
    if not n:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    cats = set(a) | set(b)
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    return None if pe >= 1 else (po - pe) / (1 - pe)


# --- driver --------------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.briefing import _load_jsonl_events

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--units", default="", help="#520's units.json (default <vault>/.mnemo/prevented-repeats/units.json)")
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/broad-value)")
    ap.add_argument("--rater", action="append", default=[])
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = args.rater or list(RATERS)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    units_file = Path(args.units).expanduser() if args.units else (
        vault / ".mnemo" / "prevented-repeats" / UNITS_NAME)
    source = mrc._read(units_file, None)
    if source is None:
        raise SystemExit("error: no %s; run tools/measure_prevented_repeats.py first" % units_file)
    sessions = source["sessions"]
    rated = list(source["rated"])
    both_rows = source["columns"].get(mpr.BOTH) or []
    # a unit #520's delivery judge has not answered yet may still turn out
    # redundant, or delivered by the envelope: it waits
    rows = [r for r in both_rows if r.get("new") and r.get("judged", True)]
    provisional = sum(1 for r in both_rows if r.get("new") and not r.get("judged", True))
    pages = page_facts(vault)

    # the units, placed at their prompt
    placed_file = mrc._read(out / "placed.json", {})
    events_cache: Dict[str, List[dict]] = {}
    stale: Set[str] = set()
    for r in sorted(rows, key=lambda r: r["session_id"]):
        uid = unit_id(r)
        if uid in placed_file and (placed_file[uid] or {}).get("key", row_key(r)) == row_key(r):
            continue
        if uid in placed_file:
            stale.add(uid)  # #520's outcome for it changed: everything built on it goes
        sid = r["session_id"]
        if sid not in events_cache:
            events_cache = {sid: _load_jsonl_events(Path(sessions[sid]["path"]))}
        ctx = place(r, events_cache[sid], sessions[sid]["project"])
        placed_file[uid] = dict(ctx, key=row_key(r)) if ctx else ctx
    mrc._write(out / "placed.json", placed_file)

    all_locate = mrc._read(out / "locate.json", {})
    locate = {r: all_locate.setdefault(mrc.column(r, LOCATE_SYSTEM), {}) for r in raters}
    arms_file = mrc._read(out / "arms.json", {})
    all_answers = mrc._read(out / "answers.json", {})
    answers = all_answers.setdefault(mrc.column("session-model", rl.ARM_SYSTEM), {})
    all_verdicts = mrc._read(out / "verdicts.json", {})
    verdicts = {r: all_verdicts.setdefault(mrc.column(r, JUDGE_SYSTEM), {}) for r in raters}
    for uid in stale:
        arms_file.pop(uid, None)
        answers.pop(uid, None)
        for r in raters:
            locate[r].pop(uid, None)
            for k in range(SAMPLES):
                verdicts[r].pop(comparison_id(uid, k), None)

    def rule_text(slug: str) -> str:
        return (pages.get(slug) or {}).get("text", slug)

    def locate_todo(r: str) -> List[Tuple[str, Dict[str, Any], Dict[str, Any]]]:
        return [(unit_id(u), u, placed_file[unit_id(u)]) for u in rows
                if unit_id(u) not in arms_file and placed_file.get(unit_id(u)) and needs_locator(u)
                and unit_id(u) not in locate[r] and placed_file[unit_id(u)]["session_start"]]

    def freeze() -> None:
        for u in rows:
            uid = unit_id(u)
            ctx = placed_file.get(uid)
            if uid in arms_file or not ctx:
                continue
            located: List[int] = []
            if needs_locator(u) and ctx["session_start"]:
                if any(uid not in locate[r] for r in raters):
                    continue
                located = sorted({x for r in raters for x in locate[r][uid]})
            arms_file[uid] = build_arms(u, ctx, sessions[u["session_id"]]["project"], located)
        mrc._write(out / "arms.json", arms_file)

    def answer_todo() -> List[Tuple[Dict[str, Any], str]]:
        todo = []
        for k in range(SAMPLES):
            for uid in sorted(arms_file):
                a = arms_file[uid]
                if not a["measurable"]:
                    continue
                order = ARMS if int(hashlib.md5(uid.encode()).hexdigest(), 16) % 2 else ARMS[::-1]
                for arm in order:
                    if len(answers.get(uid, {}).get(arm, [])) <= k:
                        todo.append((a, arm))
        return todo

    def judge_todo(r: str) -> List[Tuple[Dict[str, Any], int, str]]:
        ri = raters.index(r)
        todo = []
        for uid in sorted(arms_file):
            a = arms_file[uid]
            for k in range(SAMPLES):
                cid = comparison_id(uid, k)
                if cid in verdicts[r]:
                    continue
                p = comparison_prompt(a, answers.get(uid, {}), k, ri)
                if p is not None:
                    todo.append((a, k, p))
        return todo

    freeze()
    sender = None
    here, scratch = os.getcwd(), ""
    if args.send:
        provider = llm.resolve(cfg)
        timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
        timeout = max(timeout, 600)  # an arm writes a full reply
        sender = mpr.Sender(provider, timeout, out / "calls.jsonl", args.workers, args.pause)
        scratch = tempfile.mkdtemp(prefix="mnemo-broad-value-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches an arm or a rater

        def on_locate(r, uid, n_lines):
            def take(text):
                got = parse_locate(text, n_lines)
                if got is not None:
                    locate[r][uid] = got
                    mrc._write(out / "locate.json", all_locate)
            return take
        calls = []
        for r in raters:
            for uid, u, ctx in locate_todo(r):
                calls.append((r, locate_prompt(rule_text(u["slug"]), ctx["session_start"]), LOCATE_SYSTEM,
                              on_locate(r, uid, len(envelope_lines(ctx["session_start"])))))
        sender.run(calls[:args.limit] if args.limit else calls)
        freeze()

        def on_answer(a, arm):
            def take(text):
                if text.strip():
                    answers.setdefault(a["id"], {}).setdefault(arm, []).append({"text": text, "model": a["model"]})
                    mrc._write(out / "answers.json", all_answers)
            return take
        if not sender.stopped:
            todo = answer_todo()
            todo = todo[:args.limit] if args.limit else todo
            sender.run([(a["model"], a[arm], rl.ARM_SYSTEM, on_answer(a, arm)) for a, arm in todo])

        def on_judge(r, a, k):
            ri = raters.index(r)
            cid = comparison_id(a["id"], k)

            def take(text):
                got = parse_judge(text, cid, ri)
                if got is not None:
                    verdicts[r][cid] = got
                    mrc._write(out / "verdicts.json", all_verdicts)
            return take
        if not sender.stopped:
            calls = [(r, p, JUDGE_SYSTEM, on_judge(r, a, k)) for r in raters for a, k, p in judge_todo(r)]
            sender.run(calls[:args.limit] if args.limit else calls)
        os.chdir(here)
        shutil.rmtree(scratch, ignore_errors=True)

    # the numbers
    audit = audit_labels(vault)
    new_units: Dict[str, List[str]] = {}
    for u in rows:
        new_units.setdefault(u["session_id"], []).append(unit_id(u))
    columns = ([BOTH] if len(raters) > 1 else []) + raters
    results: Dict[str, Any] = {}
    h_by: Dict[str, Dict[str, Optional[float]]] = {}
    for col in columns:
        rs = raters if col == BOTH else [col]
        h_by[col] = {uid: (unit_h(verdicts, uid, rs) if arms_file.get(uid, {}).get("measurable") else None)
                     for uid in (unit_id(u) for u in rows)}
        results[col] = combine(per_session_rows(rated, new_units, h_by[col]))
    primary = h_by[columns[0]]
    measured = [u for u in rows if primary.get(unit_id(u)) is not None]

    def group(key: Callable[[Dict[str, Any]], Iterable[str]]) -> Dict[str, Any]:
        groups: Dict[str, List[float]] = {}
        for u in measured:
            for name in key(u):
                groups.setdefault(name, []).append(primary[unit_id(u)])
        return {name: h_ci(hs) for name, hs in sorted(groups.items())}

    cut = mrc.epoch(JUDGE_LIVE)
    breakdowns = {
        "channel": group(lambda u: [c for c in CHANNELS if arms_file[unit_id(u)]["carriers"].get(c)]),
        "reflex judge (%s)" % JUDGE_LIVE: group(
            lambda u: ["judge live" if sessions[u["session_id"]]["start"] >= cut else "before the judge"]),
        "page type": group(lambda u: [(pages.get(u["slug"]) or {}).get("type", "gone")]),
        "2026-09-22 audit label": group(lambda u: [
            {"junk": "generic or narrative", "other": "labelled, not junk"}.get(
                audit.get(short(u["slug"]), ""), "no label")]),
        "answer model": group(lambda u: [arms_file[unit_id(u)]["model"]]),
    }
    agreement = None
    if len(raters) == 2:
        a_s, b_s = [], []
        for uid in sorted(arms_file):
            for k in range(SAMPLES):
                cid = comparison_id(uid, k)
                va, vb = (verdicts[r].get(cid) for r in raters)
                if va and vb:
                    a_s.append(va["better"])
                    b_s.append(vb["better"])
        agreement = {"n": len(a_s), "same": sum(x == y for x, y in zip(a_s, b_s)), "kappa": _kappa3(a_s, b_s)}

    def example(u: Dict[str, Any]) -> Dict[str, Any]:
        uid = unit_id(u)
        a = arms_file[uid]
        whys = [verdicts[r][comparison_id(uid, k)]["why"] for k in range(SAMPLES) for r in raters
                if comparison_id(uid, k) in verdicts[r]]
        return {"h": primary[uid], "model": a["model"], "rule": (pages.get(u["slug"]) or {}).get("name", u["slug"]),
                "slug": u["slug"], "prompt": a["prompt"], "why": whys[:2]}

    rng = random.Random(SEED)
    wins = [u for u in measured if primary[unit_id(u)] > 0]
    losses = [u for u in measured if primary[unit_id(u)] < 0]
    models: Dict[Tuple[str, str], int] = {}
    for a in arms_file.values():
        models[(a["model"], a["model_from"])] = models.get((a["model"], a["model_from"]), 0) + 1
    data = {
        "sessions": len(rated), "units": len(rows), "placed": sum(1 for u in rows if placed_file.get(unit_id(u))),
        "measurable": sum(1 for a in arms_file.values() if a["measurable"]),
        "unmeasurable": sum(1 for a in arms_file.values() if not a["measurable"]),
        "judged": len(measured), "models": models, "provisional": provisional,
        "results": results, "verdict": verdict(results.get(columns[0], {})),
        "shares": outcome_shares(verdicts, [unit_id(u) for u in measured], raters),
        "breakdowns": breakdowns, "agreement": agreement,
        "examples": {"wins": [example(u) for u in rng.sample(wins, min(5, len(wins)))],
                     "losses": [example(u) for u in rng.sample(losses, min(5, len(losses)))]},
        "cost": spent(out / "calls.jsonl"),
    }

    pend_answers = answer_todo()
    for r in raters:
        lt, jt = locate_todo(r), judge_todo(r)
        est = notional(r, [(locate_prompt(rule_text(u["slug"]), c["session_start"]), LOCATE_SYSTEM)
                           for _, u, c in lt], 60) + notional(r, [(p, JUDGE_SYSTEM) for _, _, p in jt], 80)
        print("%s: pending %d locate, %d judge call(s); notional ~$%.2f" % (r, len(lt), len(jt), est),
              file=sys.stderr)
    by_model: Dict[str, List[Tuple[str, str]]] = {}
    for a, arm in pend_answers:
        by_model.setdefault(a["model"], []).append((a[arm], rl.ARM_SYSTEM))
    for m, ps in sorted(by_model.items()):
        print("%s: pending %d answer call(s); notional ~$%.2f" % (m, len(ps), notional(m, ps, ANSWER_TOKENS)),
              file=sys.stderr)
    if sender is not None:
        print("spent $%.2f notional this run (subscription usage, not money)" % sender.usd, file=sys.stderr)
    data["pending"] = {"answers": len(pend_answers),
                       **{r: {"locate": len(locate_todo(r)), "judge": len(judge_todo(r))} for r in raters}}
    serial = dict(data, models={"%s (%s)" % k: v for k, v in models.items()})
    mrc._write(out / "report.json", serial)
    if args.json:
        print(json.dumps(serial, indent=1))
        return 0
    for line in report_lines(data):
        print(line)
    print("")
    print("VERDICT (both raters): %s" % data["verdict"].upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
