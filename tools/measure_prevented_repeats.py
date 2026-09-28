"""Does mnemo prevent a repeated correction? frequency × delivery × lift (#520).

Usage:
    PYTHONPATH=src python3 tools/measure_prevented_repeats.py [--sessions N]
        [--since YYYY-MM-DD] [--out DIR]               # what is pending, and its notional cost
    PYTHONPATH=src python3 tools/measure_prevented_repeats.py --sessions N --send
        [--rater MODEL ...] [--workers W] [--pause S]  # label, judge, then report
    PYTHONPATH=src python3 tools/measure_prevented_repeats.py [--json]  # the report, from the cache

The estimate is **prevented repeated mistakes per human session**, as a
product of three factors, each with its own 95% CI, and a verdict against
thresholds the maintainer set before any measurement (:data:`THRESHOLD`):

- **positive** — estimate ≥ 1 per 15 human sessions and the CI lower bound > 0;
- **null** — CI upper bound < 1 per 15;
- **inconclusive** — neither: one more sample round, then it counts as null.

**The population.** Every human session since ``--since`` (the population of
``measure_corrections_capture``: typed turns, not ``sdk-cli``, not a throwaway
cwd, not a dispatched child), in a seeded shuffle; ``--sessions N`` takes the
first N, so a pilot's sessions stay in the full run. The session is the unit
of resampling. Every typed turn of a sampled session is a prompt.

**1. Frequency.** For each prompt, which rule the user had *already taught at
that moment* applies to it. Two blind raters (:data:`RATERS`, different
models) read the prompt, the agent message it answered and numbered rules,
never which rule is which kind or whether mnemo delivered it
(:data:`FREQ_SYSTEM`). "Both raters named it" is the primary reading. Prompts
go :data:`CHUNK_PROMPTS` at a time, consecutive in one session; the rules shown
are the union of

- the **strict pool**: rules whose evidence quote is a real correction by
  *both* raters, with #519's labeller (``measure_repeated_corrections``'s
  ``LABEL_SYSTEM``) and its cached labels; quotes it has not seen are labelled
  here, with the agent message they answered when the source transcript is
  still on disk. Gate-verified alone is not enough (#519: 17.7% of captured
  "corrections" are real);
- each prompt's BM25F top :data:`BROAD_TOP` over the rules that existed then
  (``measure_repeated_corrections.first_learned``: no hindsight, and never the
  prompt's own session), for the **broad** reading (any live rule).

A rule counts once per session (a *unit*): the session's mistake is prevented
once, however many prompts it bears on.

**2. Delivery.** Did the rule reach the agent's context by a prompt it applies
to? Not a replay of the hooks: the transcript records what the hooks actually
put in context, with the vault as it was and the judge setting in force at the
time, so this reads it. Per channel:

- **SessionStart** — the ``mnemo://`` envelope (``[last-briefing]``,
  ``[mnemo learned]``, predicted rules) names the slug, or a blind judge
  (:data:`DELIVERY_SYSTEM`, one call per session with all its rules, since
  they share the two texts) says it already tells the agent what the rule says;
- **reflex** — a ``mnemo reflex context`` for this or an earlier prompt of the
  session names it;
- **MCP** — a ``read_mnemo_rule`` or ``list_rules_by_topic`` result names it.

**Redundant.** If what Claude Code itself loaded — the ``instructions``
attachment (``CLAUDE.md`` files, ``MEMORY.md``) or, in transcripts from before
it was recorded, #519's reconstruction as of the session's start, plus the
closest auto-memory notes born before it — already says it (same judge),
mnemo delivered nothing new: the unit is redundant, not delivered.

The reflex judge went live mid-window (:data:`JUDGE_LIVE`), so frequency,
delivery and the estimate are also printed for the sessions that started
before it and after it, beside the pooled figure the verdict reads.

**3. Lift.** #434's per-pair follow differences (``measure_rule_lift``'s cache,
+31.1 pp [+19.7, +42.6]), resampled jointly with the sessions. Its pairs are
reflex-injected rules a rater called on point, any kind; how many of its rules
are in this study's strict pool is printed.

**The combination.** Per bootstrap draw: sessions resampled, lift pairs
resampled; frequency F = relevant units per session, delivery D = share of
them delivered and not redundant, lift L; estimate E = F × D × L. With fewer
than :data:`SMALL_K` delivered units the percentile interval understates the
upper bound, so it is widened to the exact Poisson bound of that count times
L's upper bound.

**Cost, beside the number, never in the verdict:** the bytes mnemo adds to a
session's context (its SessionStart block, reflex blocks and MCP results, read
from the transcripts) and the reflex judge's latency (``reflex-log.jsonl``).

First run, 2026-09-28, the maintainer's vault, ``--since 2026-08-26``: a
40-session pilot said the pooled strict CI already sat under the bar, and the
run went to 120 of the 265 sessions so the sessions after the judge went live
(38) could be read on their own too. The strict pool is 16 of the 74 rules
with a typed evidence quote. **Strict, both raters: 0.0052 prevented repeats per
human session, 95% CI [0, 0.026], NULL.** Frequency 0.40 relevant rules per
session [0.29, 0.51]; delivery as new 2 of 48 units, 4.2% [0, 10.3%]
(SessionStart 12.5%, reflex 0%, MCP 0%; 62.5% were already in ``CLAUDE.md`` or
auto-memory); lift #434's +31.1 pp. Before the judge: 0.0038 [0, 0.029]; with
it: 0.0082 [0, 0.0625], both null. The bottleneck is frequency: even with every
relevant rule Claude Code lacked delivered, F x (1 - redundant) x L = 0.047,
one per 21 sessions. The broad reading (any live rule) is 0.135 [0.070, 0.225],
over the bar, carried by the reflex (17.1% of relevant units). Kappa: relevance
0.75 strict / 0.73 broad, strict labels 0.85, delivery 0.88 / 0.94. Opus 5.5
$70.02 and Fable 5.1 $155.72 notional over 1,013 calls, including a 16-call
smoke run and a per-unit delivery pass that was stopped and replaced by the
per-session batch. mnemo adds a median ~2.2k tokens per session; the reflex
judge answers in a median 835 ms (p90 1,753 ms).

Only ``--send`` calls a model. Sends run on ``--workers`` threads with
``--pause`` seconds between one worker's calls, every answer is cached under
``--out`` (default ``<vault>/.mnemo/prevented-repeats``) the moment it arrives,
and :data:`MAX_FAILURES` failures in a row (the usage window) stop the run, so
a rerun resumes where it stopped. The chunks shown to the raters are frozen in
``chunks.json`` once built.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import queue
import random
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrc = _sibling("measure_repeated_corrections")
mcc = mrc.mcc

RATERS = mrc.RATERS
PRICES = mrc.PRICES
SINCE = "2026-08-26"
#: The maintainer's bar, set before any measurement: one prevented repeat per
#: 15 human sessions (about one a week for a typical user).
THRESHOLD = 1.0 / 15
SEED = 520
BOOTSTRAP = 4000
#: Below this many delivered units the bootstrap's upper bound is widened.
SMALL_K = 10

#: When the reflex judge went live on the maintainer's machine (#412, the
#: first ``judge`` row in ``reflex-log.jsonl``): lexical gates before, the
#: judge at injectAt 0.6 and, from 2026-09-23, 0.4 after.
JUDGE_LIVE = "2026-09-20T18:56:00Z"

CHUNK_PROMPTS = 6
BROAD_TOP = 10
MAX_RULES = 50
RULE_CHARS = 500
PROMPT_CHARS = 1500
CONTEXT_CHARS = 800
SESSION_START_CHARS = 16000
NATIVE_CHARS = 40000
#: Characters per token for the notional estimate: #519's calls.jsonl measured
#: 13.6k characters as 7.5k input tokens.
CHARS_PER_TOKEN = 2
PAUSE_SECONDS = 2.0
WORKERS = 2
MAX_FAILURES = 5

BOTH = "both"
STRICT, BROAD = "strict", "broad"
CHANNELS = ("session_start", "reflex", "mcp")

FREQ_SYSTEM = """\
You read moments from a user's coding sessions with an AI coding agent, and a
numbered list of rules: lessons the user taught the agent in earlier sessions.

For each numbered prompt (shown with the agent message it answered), name every
rule that APPLIES to it: the agent answering this prompt, or doing the work it
asks for, should act on that rule, and could get it wrong without it. Sharing a
topic or keywords is not enough; the rule must bear on what the agent is about
to do next. Most prompts have no rule that applies.

Reply with JSON only:
{"prompts": [{"id": "P1", "rules": ["R3", "R7"]}, {"id": "P2", "rules": []}]}
Include every prompt id.
"""

DELIVERY_SYSTEM = """\
You are shown two texts an AI coding agent had in its context, and numbered
rules the agent should follow. For each rule and each text, decide whether the
text ALREADY tells the agent what the rule says: the same behaviour, fact or
preference, so that an agent that read the text would act as the rule says.
Sharing a topic is not enough; a text that says something more general counts
only if it clearly covers the rule's case.

Reply with JSON only, one row per rule:
{"rules": [{"id": "R1", "A": true|false, "B": true|false}]}
"""
#: Rules per delivery call: a session's units share its two texts.
DELIVERY_BATCH = 20
#: Closest auto-memory notes per rule added to text B.
NOTES_PER_RULE = 2


# --- small helpers ---------------------------------------------------------------------

def _sha(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]


def _head(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def _tail(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def session_order(session_ids: Iterable[str], seed: int = SEED) -> List[str]:
    """A seeded shuffle, so the first N of a larger run are a pilot's N."""
    ids = sorted(set(session_ids))
    random.Random(seed).shuffle(ids)
    return ids


def canonical(slug: str, project: str, live: Set[str]) -> str:
    """Today's slug for one a hook wrote then: rules were renamed to
    ``<project>__<slug>`` after some transcripts were written."""
    if slug in live or not project:
        return slug
    prefixed = "%s__%s" % (project, slug)
    return prefixed if prefixed in live else slug


# --- 0. what each prompt had in context ------------------------------------------------

_MNEMO_START = ("mnemo://", "[last-briefing", "[mnemo learned", "[predicted-rules")
_REFLEX = "mnemo reflex context"
_WIKI = re.compile(r"\[\[([^\]\s|]+)\]\]")
_SLUG_KEY = re.compile(r'"slug"\s*:\s*"([^"]+)"')
_SHELL_TURN = re.compile(r"\s*<(bash-input|bash-stdout|bash-stderr|local-command-stdout)>")
_MCP_TOOL = re.compile(r"mnemo.*__(read_mnemo_rule|list_rules_by_topic)$")


def _hook_texts(att: Dict[str, Any]) -> List[str]:
    content = att.get("content")
    if isinstance(content, str):
        return [content]
    return [c for c in content or [] if isinstance(c, str)]


def _result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    parts = []
    for c in content or []:
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            parts.append(c["text"])
    return "\n".join(parts)


def walk(events: List[dict]) -> Dict[str, Any]:
    """What mnemo and Claude Code had put in context before each typed prompt.

    A prompt's own ``UserPromptSubmit`` context follows it in the file, so a
    prompt's snapshot is taken at the first assistant event after it (or at
    the next typed turn). Returns
    ``prompts`` (per typed turn the agent answers — not a ``!`` shell command
    or its output — indexed in :func:`transcript.user_turn_records`' order:
    index, ts, text, the agent message it answered, how many SessionStart and
    native texts existed, and the reflex and MCP slugs so far), the mnemo
    ``session_start`` texts, the ``native`` files (``[path, content]``, the
    latest per path) and ``mnemo_chars``, what mnemo added in all.
    """
    from mnemo.core.friction import capture
    from mnemo.core.transcript import SYNTHETIC_TURN, plain_user_text

    answered = [a for a, _ in capture.exchanges(events)]
    session_start: List[str] = []
    native: Dict[str, str] = {}
    reflex: Set[str] = set()
    mcp: Set[str] = set()
    tool_names: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    mnemo_chars = 0
    prompts: List[Dict[str, Any]] = []
    open_prompt: Optional[Dict[str, Any]] = None

    def snapshot(p: Dict[str, Any]) -> None:
        p.update(session_start_n=len(session_start), native=[[k, native[k]] for k in sorted(native)],
                 reflex=sorted(reflex), mcp=sorted(mcp))

    n = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        att = ev.get("attachment")
        if isinstance(att, dict):
            kind = att.get("type")
            hook = str(att.get("hookName") or att.get("hookEvent") or "")
            if kind == "hook_additional_context":
                for text in _hook_texts(att):
                    if _REFLEX in text:
                        mnemo_chars += len(text)
                        reflex.update(_WIKI.findall(text))
                    elif (hook.startswith("SessionStart") or not hook) and any(
                            m in text for m in _MNEMO_START):
                        if text not in session_start:
                            mnemo_chars += len(text)
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
            if open_prompt is not None:
                snapshot(open_prompt)
                open_prompt = None
            for b in msg.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    m = _MCP_TOOL.search(str(b.get("name") or ""))
                    if m:
                        tool_names[str(b.get("id"))] = (m.group(1), b.get("input") or {})
            continue
        if ev.get("type") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if not (isinstance(b, dict) and b.get("type") == "tool_result"):
                    continue
                called = tool_names.get(str(b.get("tool_use_id")))
                if called is None:
                    continue
                text = _result_text(b)
                mnemo_chars += len(text)
                name, args = called
                if name == "read_mnemo_rule" and args.get("slug"):
                    mcp.add(str(args["slug"]))
                mcp.update(_SLUG_KEY.findall(text))
                mcp.update(_WIKI.findall(text))
        # the filter of transcript.user_turn_records, so ``i`` is its index
        text = plain_user_text(content)
        if not text or SYNTHETIC_TURN.search(text):
            continue
        n += 1
        if _SHELL_TURN.match(text):
            continue  # a ``!`` command or its output: the agent answers nothing
        if open_prompt is not None:
            snapshot(open_prompt)
        open_prompt = {
            "i": n - 1,
            "ts": mrc.epoch(ev.get("timestamp")),
            "text": text,
            "answered": answered[n - 1] if n - 1 < len(answered) else "",
        }
        prompts.append(open_prompt)
    if open_prompt is not None:
        snapshot(open_prompt)
    return {"prompts": [p for p in prompts if p["ts"] is not None],
            "session_start": session_start, "mnemo_chars": mnemo_chars}


def collect_sessions(projects_dir: Path, vault: Path, since: str) -> Dict[str, Dict[str, Any]]:
    """Every human session since ``since``: ``sid -> {path, project, cwd, start}``."""
    from mnemo.core.briefing import _load_jsonl_events

    parents = mcc._parents(vault)
    out: Dict[str, Dict[str, Any]] = {}
    for path in sorted(Path(projects_dir).glob("*/*.jsonl")):
        events = _load_jsonl_events(path)
        if not mcc.is_human(events, path.stem, parents):
            continue
        first = mcc.first_timestamp(events)
        if first is None or (since and first.astimezone(timezone.utc).date().isoformat() < since):
            continue
        out[path.stem] = {"path": str(path), "project": mcc._project_of(path, events),
                          "cwd": mcc.session_cwd(events), "start": first.timestamp()}
    return out


# --- 1a. the strict pool ---------------------------------------------------------------

def evidence_items(vault: Path, facts: Dict[str, Any], transcripts: Dict[str, Path]) -> Dict[str, Dict[str, Any]]:
    """``slug -> label item`` for every rule whose evidence quote a person typed.

    The item is shaped like #519's so its labeller and cache apply: placed at
    its turn in the first source transcript still on disk that contains it,
    with the agent message it answered; otherwise the quote alone.
    """
    from mnemo.core import corrections
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.core.filters import derive_rule_slug, is_consumer_visible
    from mnemo.core.friction import capture
    from mnemo.core.reclassify_types import split_frontmatter
    from mnemo.core.transcript import user_turns

    out: Dict[str, Dict[str, Any]] = {}
    for page_type in ("feedback", "user", "reference", "project"):
        for md in sorted((vault / "shared" / page_type).glob("*.md")):
            fm, _ = split_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            if not is_consumer_visible(md, fm, vault):
                continue
            slug = derive_rule_slug(fm, md.stem)
            f = facts.get(slug)
            if f is None or f.origin != "user" or not f.correction_backed:
                continue
            ev = fm.get("evidence") if isinstance(fm.get("evidence"), dict) else {}
            quote = str(ev.get("quote") or "").strip()
            if not quote:
                continue
            item = None
            for sid in sorted(f.taught_by):
                if sid not in transcripts:
                    continue
                events = _load_jsonl_events(transcripts[sid])
                turns = user_turns(events)
                index = corrections.locate(quote, turns)
                if index is None:
                    continue
                answered = [a for a, _ in capture.exchanges(events)]
                item = {"id": mrc.item_id(sid, index, quote), "session_id": sid, "quote": quote,
                        "turn": _head(turns[index], mrc.TURN_CHARS),
                        "answered": _tail(answered[index] if index < len(answered) else "",
                                          mrc.CONTEXT_CHARS),
                        "on_disk": True}
                break
            if item is None:
                sid = sorted(f.taught_by)[0] if f.taught_by else ""
                item = {"id": mrc.item_id(sid, -1, quote), "session_id": sid, "quote": quote,
                        "turn": quote, "answered": "(the agent's message is no longer on disk)",
                        "on_disk": False}
            item["slug"] = slug
            item["gate_verified"] = bool(f.gate_verified)
            out[slug] = item
    return out


def strict_pool(evidence: Dict[str, Dict[str, Any]], labels: Dict[str, Dict[str, bool]]) -> Optional[Set[str]]:
    """Slugs whose quote every rater called a correction; None while one is unlabelled."""
    out: Set[str] = set()
    for slug, it in evidence.items():
        said = mrc.consensus(labels, it["id"])
        if said is None:
            return None
        if said:
            out.add(slug)
    return out


# --- 1b. the rules each prompt is judged against ---------------------------------------

class Rules:
    """Today's vault, cut to what existed at a moment (#519's machinery)."""

    def __init__(self, vault: Path, projects_dir: Path, claude_home: Path) -> None:
        from mnemo.core.reflex import replay

        self.ctx = mrc._Context(vault, projects_dir, claude_home)
        self.facts = replay.rule_facts(vault)
        self.live = set(self.facts)
        self._text: Dict[str, str] = {}

    def text(self, slug: str) -> str:
        if slug not in self._text:
            page = self.ctx.pages.get(slug)
            self._text[slug] = "" if page is None else "%s\n%s" % (page[0], _head(page[1], RULE_CHARS))
        return self._text[slug]

    def pool(self, project: str, ts: float, session_id: str) -> List[str]:
        docs = self.ctx.index.get("docs") or {}
        out = []
        for slug, doc in docs.items():
            if doc.get("retired") or not (project in (doc.get("projects") or []) or doc.get("universal")):
                continue
            facts = self.ctx.dates.get(slug)
            if facts is None:
                continue
            t = mrc.first_learned(facts, session_id)
            if t is not None and t < ts:
                out.append(slug)
        return out

    def top(self, prompt: str, pool: Sequence[str], n: int = BROAD_TOP) -> List[str]:
        from mnemo.core.reflex import bm25
        from mnemo.core.reflex.tokenizer import tokenize_query

        tokens = tokenize_query(prompt)
        if not tokens or not pool:
            return []
        return [s for s, _ in bm25.score_docs(self.ctx.index, query_tokens=tokens,
                                              candidate_slugs=list(pool))[:n]]


def build_chunks(session_id: str, meta: Dict[str, Any], walked: Dict[str, Any], rules: Any,
                 strict: Set[str]) -> List[Dict[str, Any]]:
    """The session's prompts, :data:`CHUNK_PROMPTS` at a time, each chunk with
    its as-of strict pool and its prompts' broad top, strict first."""
    prompts = walked["prompts"]
    out = []
    for start in range(0, len(prompts), CHUNK_PROMPTS):
        part = prompts[start:start + CHUNK_PROMPTS]
        shown: List[str] = []
        first_pool = set(rules.pool(meta["project"], part[0]["ts"], session_id))
        for slug in sorted(strict & first_pool):
            shown.append(slug)
        for p in part:
            pool = rules.pool(meta["project"], p["ts"], session_id)
            for slug in rules.top(p["text"], pool):
                if slug not in shown:
                    shown.append(slug)
        shown = [s for s in shown if rules.text(s)][:MAX_RULES]
        items = [{"key": "%s#%d" % (session_id, p["i"]), "text": _head(p["text"], PROMPT_CHARS),
                  "answered": _tail(p["answered"], CONTEXT_CHARS)} for p in part]
        out.append({"id": _sha(session_id, str(start), *shown, *[it["key"] for it in items]),
                    "session_id": session_id, "prompts": items, "rules": shown})
    return out


def freq_prompt(chunk: Dict[str, Any], rule_text: Callable[[str], str]) -> str:
    lines = ["## Rules"]
    for n, slug in enumerate(chunk["rules"], 1):
        lines += ["### R%d" % n, rule_text(slug), ""]
    if not chunk["rules"]:
        lines.append("(none)")
    lines.append("## Prompts")
    for n, it in enumerate(chunk["prompts"], 1):
        lines += ["### P%d" % n, "AGENT said before:", it["answered"] or "(nothing)", "",
                  "USER:", it["text"], ""]
    return "\n".join(lines)


def parse_freq(text: str, chunk: Dict[str, Any]) -> Optional[Dict[str, List[str]]]:
    """``prompt key -> [slug]``; None unless every prompt is answered."""
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    rules = {"R%d" % n: s for n, s in enumerate(chunk["rules"], 1)}
    keys = {"P%d" % n: it["key"] for n, it in enumerate(chunk["prompts"], 1)}
    out: Dict[str, List[str]] = {}
    for row in payload.get("prompts") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if pid not in keys or not isinstance(row.get("rules"), list):
            continue
        out[keys[pid]] = sorted({rules[str(r).strip()] for r in row["rules"] if str(r).strip() in rules})
    return out if set(out) == set(keys.values()) else None


# --- 2. delivery -----------------------------------------------------------------------

def relevant(freq: Dict[str, Dict[str, Dict[str, List[str]]]], raters: Sequence[str],
             chunks: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Set[str]]]:
    """``column -> prompt key -> slugs``; the ``both`` column is the
    intersection, over prompts every rater answered."""
    out: Dict[str, Dict[str, Set[str]]] = {r: {} for r in raters}
    for ch in chunks:
        for r in raters:
            got = freq.get(r, {}).get(ch["id"])
            if got is not None:
                for key, slugs in got.items():
                    out[r][key] = set(slugs)
    if len(raters) > 1:
        both: Dict[str, Set[str]] = {}
        for key in out[raters[0]]:
            if all(key in out[r] for r in raters):
                both[key] = set.intersection(*[out[r][key] for r in raters])
        out[BOTH] = both
    return out


def native_files(walked_prompt: Dict[str, Any],
                 fallback: Callable[[], List[Tuple[str, str]]]) -> List[Tuple[str, str]]:
    """What Claude Code itself had loaded: the recorded ``instructions`` files,
    or #519's reconstruction when the transcript predates that record."""
    return [(p, t) for p, t in walked_prompt.get("native") or []] or list(fallback())


def native_text(files: Sequence[Tuple[str, str]], notes: Sequence[Tuple[str, str]]) -> str:
    """Text B: the loaded files, then the auto-memory notes closest to the rules."""
    parts = ["### %s\n%s" % (label, text) for label, text in files]
    parts += ["### memory note %s\n%s" % (name, body) for name, body in notes]
    return _head("\n\n".join(parts), NATIVE_CHARS)


def delivery_prompt(rules: Sequence[str], session_start: str, native: str) -> str:
    lines = ["## Text A", session_start or "(none)", "", "## Text B", native or "(none)", "",
             "## Rules"]
    for n, rule in enumerate(rules, 1):
        lines += ["### R%d" % n, rule, ""]
    return "\n".join(lines)


def parse_delivery(text: str, ids: Sequence[str]) -> Dict[str, Dict[str, bool]]:
    """``unit id -> {"A", "B"}`` for the rows that parse; ``ids`` in R order."""
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return {}
    by_r = {"R%d" % n: uid for n, uid in enumerate(ids, 1)}
    out: Dict[str, Dict[str, bool]] = {}
    for row in payload.get("rules") or []:
        if not isinstance(row, dict):
            continue
        uid = by_r.get(str(row.get("id") or "").strip())
        if uid and all(isinstance(row.get(k), bool) for k in ("A", "B")):
            out[uid] = {"A": row["A"], "B": row["B"]}
    return out


def delivery_batches(units: Dict[Tuple[str, str], Dict[str, Any]], done: Dict[str, Any],
                     size: int = DELIVERY_BATCH) -> List[List[Dict[str, Any]]]:
    """Pending units grouped by the texts they share, ``size`` at a time."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for u in units.values():
        if not u["empty"] and u["id"] not in done:
            groups.setdefault(u["texts"], []).append(u)
    out = []
    for key in sorted(groups):
        g = groups[key]
        out += [g[i:i + size] for i in range(0, len(g), size)]
    return out


def batch_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    notes: List[Tuple[str, str]] = []
    for u in batch:
        for note in u["notes"]:
            if note not in notes:
                notes.append(note)
    return delivery_prompt([u["rule"] for u in batch], batch[0]["ss"],
                           native_text(batch[0]["files"], notes))


def _named(text: str, slug: str) -> bool:
    short = slug.split("__", 1)[-1]
    return any(re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(s), text) for s in {slug, short})


def unit_outcome(slug: str, prompts: Sequence[Dict[str, Any]], session_start: Sequence[str],
                 judged: Optional[Dict[str, Any]], project: str, live: Set[str]) -> Dict[str, bool]:
    """Channels that carried ``slug`` by the relevant ``prompts``, and whether
    it was redundant. ``judged`` is the delivery judge's reading (None: unknown)."""
    got = {c: False for c in CHANNELS}
    for p in prompts:
        if slug in {canonical(s, project, live) for s in p["reflex"]}:
            got["reflex"] = True
        if slug in {canonical(s, project, live) for s in p["mcp"]}:
            got["mcp"] = True
        if any(_named(t, slug) for t in session_start[:p["session_start_n"]]):
            got["session_start"] = True
    if judged and judged.get("A"):
        got["session_start"] = True
    redundant = bool(judged and judged.get("B"))
    delivered = any(got.values())
    return dict(got, delivered=delivered, redundant=redundant, new=delivered and not redundant)


# --- 3. the numbers --------------------------------------------------------------------

def lift_diffs(vault: Path) -> Tuple[List[float], str]:
    """#434's per-pair B − A follow differences from its cache, or a normal
    approximation of its published result when the cache is absent."""
    try:
        rl = _sibling("measure_rule_lift")
        out = vault / ".mnemo" / rl.OUT_DIR
        pairs = mrc._read(out / rl.PAIRS_NAME, [])
        answers = mrc._read(out / rl.ANSWERS_NAME, {})
        verdicts = mrc._read(out / rl.VERDICTS_NAME, {})
        arm_col = rl.column(rl.DEFAULT_MODEL, rl.ARM_SYSTEM)
        col = "%s/%s" % (arm_col, rl.column(rl.DEFAULT_JUDGE, rl.JUDGE_SYSTEM))
        rows = rl.pair_rows(pairs, answers.get(arm_col, {}), verdicts.get(col, {}))
        diffs = [r["B"] - r["A"] for r in rows if not r["both_na"]]
        if diffs:
            return diffs, "measure_rule_lift cache (%d pairs)" % len(diffs)
    except Exception:
        pass
    rng = random.Random(SEED)
    return [rng.gauss(0.311, 0.0584 * math.sqrt(61)) for _ in range(61)], "normal approximation of #434"


def lift_slugs(vault: Path) -> Set[str]:
    try:
        rl = _sibling("measure_rule_lift")
        return {p["slug"] for p in mrc._read(vault / ".mnemo" / rl.OUT_DIR / rl.PAIRS_NAME, [])}
    except Exception:
        return set()


def poisson_upper(k: int, alpha: float = 0.05) -> float:
    """Exact upper bound of a Poisson mean given ``k`` events."""
    def cdf(lam: float) -> float:
        term = total = math.exp(-lam)
        for i in range(1, k + 1):
            term *= lam / i
            total += term
        return total
    lo, hi = float(k), float(k) + 10 + 10 * math.sqrt(k + 1)
    for _ in range(100):
        mid = (lo + hi) / 2
        if cdf(mid) > alpha / 2:
            lo = mid
        else:
            hi = mid
    return hi


def _pct(values: List[float], q: float) -> float:
    return values[min(len(values) - 1, max(0, int(q * len(values))))]


def combine(per_session: Sequence[Dict[str, int]], diffs: Sequence[float],
            n_boot: int = BOOTSTRAP, seed: int = SEED) -> Dict[str, Any]:
    """``per_session``: ``{units, new, prompts, <channel>, redundant, delivered}``
    per sampled session. Factors, their CIs and the estimate per session."""
    n = len(per_session)
    if not n or not diffs:
        return {"sessions": n}
    keys = ("units", "new", "delivered", "redundant", "prompts") + CHANNELS
    tot = {k: sum(s.get(k, 0) for s in per_session) for k in keys}
    lift = sum(diffs) / len(diffs)
    rng = random.Random(seed)
    boots: Dict[str, List[float]] = {k: [] for k in ("F", "D", "L", "E", "per_prompt")}
    m = len(diffs)
    for _ in range(n_boot):
        pick = [per_session[rng.randrange(n)] for _ in range(n)]
        units = sum(s["units"] for s in pick)
        new = sum(s["new"] for s in pick)
        prompts = sum(s["prompts"] for s in pick)
        lb = sum(diffs[rng.randrange(m)] for _ in range(m)) / m
        boots["F"].append(units / n)
        boots["D"].append(new / units if units else 0.0)
        boots["L"].append(lb)
        boots["E"].append(new / n * lb)
        boots["per_prompt"].append(units / prompts if prompts else 0.0)
    ci = {k: [_pct(sorted(v), 0.025), _pct(sorted(v), 0.975)] for k, v in boots.items()}
    est = tot["new"] / n * lift
    widened = False
    if tot["new"] < SMALL_K:
        upper = poisson_upper(tot["new"]) / n * ci["L"][1]
        if upper > ci["E"][1]:
            ci["E"] = [ci["E"][0], upper]
            widened = True
    units = tot["units"]
    return {
        "sessions": n, "prompts": tot["prompts"], "units": units, "new": tot["new"],
        "F": units / n, "D": tot["new"] / units if units else None, "L": lift, "E": est,
        "per_prompt": units / tot["prompts"] if tot["prompts"] else None,
        "ci": ci, "widened": widened,
        "channels": {c: (tot[c] / units if units else None) for c in CHANNELS + ("delivered", "redundant")},
        "counts": tot,
    }


def verdict(est: Optional[float], lo: Optional[float], hi: Optional[float],
            threshold: float = THRESHOLD) -> str:
    if est is None or lo is None or hi is None:
        return "no estimate yet"
    if est >= threshold and lo > 0:
        return "positive"
    if hi < threshold:
        return "null"
    return "inconclusive"


def needed_sessions(n: int, stats: Dict[str, Any], threshold: float = THRESHOLD) -> Optional[int]:
    """Sessions for the CI to land on one side of the threshold, scaling the
    pilot's half-widths by 1/sqrt(n); None when it cannot tell."""
    if not stats.get("sessions") or stats.get("E") is None:
        return None
    est, (lo, hi) = stats["E"], stats["ci"]["E"]
    if est < threshold:
        half, gap = hi - est, threshold - est
    else:
        half, gap = est - lo, est
    if gap <= 0:
        return None
    return max(n, int(math.ceil(n * (half / gap) ** 2)))


def per_session_counts(session_units: Dict[str, List[Dict[str, Any]]], prompts: Dict[str, int],
                       reading: str) -> List[Dict[str, int]]:
    out = []
    for sid in sorted(prompts):
        row = {"units": 0, "new": 0, "delivered": 0, "redundant": 0, "prompts": prompts[sid]}
        row.update({c: 0 for c in CHANNELS})
        for u in session_units.get(sid, []):
            if reading == STRICT and not u["strict"]:
                continue
            row["units"] += 1
            for k in ("new", "delivered", "redundant") + CHANNELS:
                row[k] += int(bool(u[k]))
        out.append(row)
    return out


def regime_split(sessions: Dict[str, Dict[str, Any]], prompts: Dict[str, int],
                 at: str = JUDGE_LIVE) -> Dict[str, Dict[str, int]]:
    """``prompts`` split by whether the session started before the reflex
    judge went live (#412) or after."""
    cut = mrc.epoch(at)
    before = {s: n for s, n in prompts.items() if sessions[s]["start"] < cut}
    after = {s: n for s, n in prompts.items() if sessions[s]["start"] >= cut}
    return {"before the judge": before, "judge live": after}


def mnemo_cost(walked: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    chars = sorted(w["mnemo_chars"] for w in walked.values())
    if not chars:
        return {}
    return {"sessions": len(chars), "median_bytes": chars[len(chars) // 2],
            "mean_bytes": sum(chars) / len(chars),
            "median_tokens": chars[len(chars) // 2] / 4, "mean_tokens": sum(chars) / len(chars) / 4}


def judge_latency(vault: Path, since: str) -> Dict[str, Any]:
    from mnemo.core.log_utils import iter_rotated_rows

    ms = []
    for row in iter_rotated_rows(vault / ".mnemo" / "reflex-log.jsonl"):
        j = row.get("judge") if isinstance(row, dict) else None
        if isinstance(j, dict) and isinstance(j.get("ms"), (int, float)) and str(row.get("ts") or "") >= since:
            ms.append(float(j["ms"]))
    ms.sort()
    if not ms:
        return {}
    return {"calls": len(ms), "median_ms": ms[len(ms) // 2], "p90_ms": ms[int(0.9 * (len(ms) - 1))]}


# --- sending ---------------------------------------------------------------------------

class Sender:
    """Paced, threaded sends with every answer logged and a stop after
    :data:`MAX_FAILURES` failures in a row (the usage window)."""

    def __init__(self, provider: Callable[..., Any], timeout: int, log: Optional[Path],
                 workers: int = WORKERS, pause: float = 0.0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.provider, self.timeout, self.log = provider, timeout, log
        self.workers, self.pause, self.sleep = max(1, workers), pause, sleep
        self.lock = threading.Lock()
        self.usd = 0.0
        self.failures = 0
        self.stopped = False

    def run(self, calls: Sequence[Tuple[str, str, str, Callable[[str], None]]]) -> None:
        """``(model, prompt, system, on_reply)``; ``on_reply`` runs under the lock."""
        todo: "queue.Queue[Tuple[int, Tuple[str, str, str, Callable[[str], None]]]]" = queue.Queue()
        for n, c in enumerate(calls, 1):
            todo.put((n, c))
        total = len(calls)

        def worker() -> None:
            first = True
            while not self.stopped:
                try:
                    n, (model, prompt, system, on_reply) = todo.get_nowait()
                except queue.Empty:
                    return
                if not first and self.pause > 0:
                    self.sleep(self.pause)
                first = False
                try:
                    resp = self.provider(prompt, system=system, model=model, timeout=self.timeout)
                except Exception as exc:  # one failed call must not end the run
                    with self.lock:
                        self.failures += 1
                        print("  call %d: %s: %s" % (n, type(exc).__name__, str(exc)[:200]), file=sys.stderr)
                        if self.failures >= MAX_FAILURES:
                            self.stopped = True
                            print("  %d failures in a row: stopping; rerun to resume" % self.failures,
                                  file=sys.stderr)
                    continue
                with self.lock:
                    self.failures = 0
                    self.usd += float(resp.total_cost_usd or 0.0)
                    if self.log is not None:
                        with open(self.log, "a", encoding="utf-8") as fh:
                            fh.write(json.dumps({"system": mrc.column(model, system), "chars": len(prompt),
                                                 "in": resp.input_tokens, "out": resp.output_tokens,
                                                 "usd": resp.total_cost_usd}) + "\n")
                    on_reply(resp.text or "")
                    print("  %s call %d/%d, $%.2f so far" % (model, n, total, self.usd), file=sys.stderr)

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(self.workers, total))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


def notional(model: str, prompts: Sequence[Tuple[str, str]], out_tokens: int) -> float:
    price_in, price_out = PRICES.get(model, PRICES["claude-fable-5-1"])
    tin = sum((len(p) + len(s)) / CHARS_PER_TOKEN for p, s in prompts)
    return (tin * price_in + len(prompts) * out_tokens * price_out) / 1e6


# --- report ----------------------------------------------------------------------------

def _p(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def _per(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return "%.4f (1 per %s)" % (x, "%.0f" % (1 / x) if x > 0 else "∞")


def report_lines(data: Dict[str, Any]) -> List[str]:
    lines = ["%d human sessions since %s; %d sampled, %d with every prompt rated by every rater"
             % (data["population"], data["since"], data["sampled"], data["rated"]),
             *(["PROVISIONAL: %s call(s) still pending (%s); a unit the delivery judge has not "
                "answered counts as not delivered by content and not redundant" % (
                    sum(sum(v.values()) for v in data["pending"].values()),
                    ", ".join("%s %s" % (r, v) for r, v in data["pending"].items()))]
               if any(sum(v.values()) for v in (data.get("pending") or {}).values()) else []),
             "strict pool: %s rules whose evidence quote both raters call a correction "
             "(of %d with a typed quote; %d labelled without the agent message on disk)"
             % (data["strict_size"] if data["strict_size"] is not None else "?",
                data["evidence"], data["evidence_off_disk"]),
             "lift: %s; %d of #434's %d rules are in the strict pool"
             % (data["lift_source"], data["lift_overlap"], data["lift_rules"]), ""]
    for reading in (STRICT, BROAD):
        for col, stats in data["results"][reading].items():
            if not stats.get("units") and not stats.get("sessions"):
                continue
            ci = stats.get("ci") or {}
            primary = reading == STRICT and col == BOTH
            lines.append("%s reading, %s%s:" % (reading, col, "  <- the verdict" if primary else ""))
            if stats.get("E") is None:
                lines.append("  no estimate yet")
                continue
            lines += [
                "  frequency  %.3f relevant rules per session  [%.3f, %.3f]   (%s of prompts)"
                % (stats["F"], ci["F"][0], ci["F"][1], _p(stats["per_prompt"])),
                "  delivery   %s of them delivered and not redundant  [%s, %s]   (%d of %d units)"
                % (_p(stats["D"]), _p(ci["D"][0]), _p(ci["D"][1]), stats["new"], stats["units"]),
                "             per channel: " + ", ".join(
                    "%s %s" % (c, _p(stats["channels"][c])) for c in CHANNELS)
                + "; any %s; redundant with CLAUDE.md / auto-memory %s"
                % (_p(stats["channels"]["delivered"]), _p(stats["channels"]["redundant"])),
                "  lift       %+.1f pp  [%+.1f, %+.1f]" % (100 * stats["L"], 100 * ci["L"][0], 100 * ci["L"][1]),
                "  estimate   %s prevented per human session  [%.4f, %.4f]%s"
                % (_per(stats["E"]), ci["E"][0], ci["E"][1],
                   "  (upper bound widened: few delivered units)" if stats["widened"] else ""),
                "  verdict    %s   (bar: >= %.4f = 1 per 15, CI lower bound > 0)"
                % (verdict(stats["E"], ci["E"][0], ci["E"][1]).upper(), THRESHOLD),
                "  bottleneck %s" % bottleneck(stats),
                "",
            ]
    for name, per in (data.get("regimes") or {}).items():
        lines.append("%s (%s, sessions %s %s):" % (
            name, (list(data["results"][STRICT]) or ["?"])[0],
            "before" if name.startswith("before") else "from", JUDGE_LIVE))
        for reading in (STRICT, BROAD):
            st = per.get(reading) or {}
            if st.get("E") is None:
                lines.append("  %-6s no sessions" % reading)
                continue
            ci = st["ci"]
            lines.append(
                "  %-6s %d sessions, F %.3f, D %s (%d/%d; session_start %s, reflex %s, mcp %s), "
                "E %.4f [%.4f, %.4f] %s"
                % (reading, st["sessions"], st["F"], _p(st["D"]), st["new"], st["units"],
                   _p(st["channels"]["session_start"]), _p(st["channels"]["reflex"]),
                   _p(st["channels"]["mcp"]), st["E"], ci["E"][0], ci["E"][1],
                   verdict(st["E"], ci["E"][0], ci["E"][1]).upper()))
    lines.append("")
    ag = data.get("agreement") or {}
    if ag:
        lines.append("agreement (Cohen's kappa): " + ", ".join(
            "%s %s" % (k, "n/a" if v is None else "%.2f" % v) for k, v in ag.items()))
    need = data.get("needed_sessions")
    if need is not None:
        lines.append("sessions needed for the strict/both CI to clear the bar: ~%d (population %d)"
                     % (need, data["population"]))
    cost = data.get("cost") or {}
    if cost.get("mnemo"):
        c = cost["mnemo"]
        lines.append("cost, not the verdict: mnemo adds a median %d bytes (~%d tokens) per session, "
                     "mean %d (~%d)" % (c["median_bytes"], c["median_tokens"], c["mean_bytes"], c["mean_tokens"]))
    if cost.get("judge"):
        j = cost["judge"]
        lines.append("reflex judge latency: median %d ms, p90 %d ms over %d calls"
                     % (j["median_ms"], j["p90_ms"], j["calls"]))
    if cost.get("study"):
        lines.append("notional cost of every answer on file: " + ", ".join(
            "%s $%.2f (%d calls)" % (r, v["usd"], v["calls"]) for r, v in cost["study"].items()))
    return lines


def bottleneck(stats: Dict[str, Any], threshold: float = THRESHOLD) -> str:
    """Which factor keeps the estimate under the bar.

    Delivery and lift cannot exceed 1, frequency is the user's own history,
    and a rule Claude Code already had (redundant) can never count as new
    whatever mnemo does. So the most mnemo could reach is F x (share not
    redundant) x L. If that ceiling stays under the bar, the bottleneck is
    frequency: too few rules Claude Code lacked bear on real prompts for
    delivery to matter. Otherwise it is delivery, and the ceiling is printed.
    """
    if stats.get("E") is None:
        return "n/a"
    if stats["E"] >= threshold:
        return "none: the estimate clears the bar"
    redundant = (stats.get("channels") or {}).get("redundant") or 0.0
    ceiling = stats["F"] * (1 - redundant) * max(stats["L"], 0.0)
    if ceiling < threshold:
        return ("frequency: with every relevant rule Claude Code lacked delivered, "
                "F x (1 - redundant %s) x L = %.4f (1 per %s) is still under the bar"
                % (_p(redundant), ceiling, "%.0f" % (1 / ceiling) if ceiling else "∞"))
    return ("delivery (%s): delivering every relevant rule Claude Code lacked would give "
            "%.4f, over the bar" % (_p(stats["D"]), ceiling))


# --- driver ----------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.briefing import _load_jsonl_events

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default=SINCE)
    ap.add_argument("--sessions", type=int, default=None, help="the first N of the seeded order (default all)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--vault", default="")
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/prevented-repeats)")
    ap.add_argument("--labels", default="",
                    help="#519's label cache, read only (default <vault>/.mnemo/repeated-corrections/labels.json)")
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
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / "prevented-repeats"
    out.mkdir(parents=True, exist_ok=True)
    seed_labels = Path(args.labels).expanduser() if args.labels else (
        vault / ".mnemo" / "repeated-corrections" / "labels.json")

    projects = Path(args.projects)
    sessions = collect_sessions(projects, vault, args.since)
    order = session_order(sessions)
    sample = order[:args.sessions] if args.sessions else order
    rules = Rules(vault, projects, Path(args.claude_home))
    transcripts = {p.stem: p for p in projects.glob("*/*.jsonl")}

    # step 1a: the strict pool
    evidence = evidence_items(vault, rules.facts, transcripts)
    all_labels = mrc._read(out / "labels.json", {})
    seeded = mrc._read(seed_labels, {})
    labels = {}
    for r in raters:
        col = mrc.column(r, mrc.LABEL_SYSTEM)
        labels[r] = dict(seeded.get(col, {}), **all_labels.setdefault(col, {}))

    sender = None
    here, scratch = os.getcwd(), ""
    if args.send:
        provider = llm.resolve(cfg)
        timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
        sender = Sender(provider, timeout, out / "calls.jsonl", args.workers, args.pause)
        scratch = tempfile.mkdtemp(prefix="mnemo-prevented-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches a rater

    items = list(evidence.values())
    label_todo = {r: mrc.label_batches(items, labels[r]) for r in raters}
    if sender is not None:
        def on_labels(r, batch):
            def take(text):
                got = mrc.parse_labels(text, batch)
                labels[r].update(got)
                all_labels[mrc.column(r, mrc.LABEL_SYSTEM)].update(got)
                mrc._write(out / "labels.json", all_labels)
            return take
        calls = [(r, mrc.label_prompt(b), mrc.LABEL_SYSTEM, on_labels(r, b))
                 for r in raters for b in label_todo[r]]
        sender.run(calls[:args.limit] if args.limit else calls)
        label_todo = {r: mrc.label_batches(items, labels[r]) for r in raters}
    strict = strict_pool(evidence, labels)

    # step 1b: the chunks, frozen once the strict pool is known
    chunks_file = mrc._read(out / "chunks.json", {})
    walked: Dict[str, Dict[str, Any]] = {}
    for sid in sample:
        walked[sid] = walk(_load_jsonl_events(Path(sessions[sid]["path"])))
        if sid not in chunks_file and strict is not None:
            chunks_file[sid] = build_chunks(sid, sessions[sid], walked[sid], rules, strict)
    if strict is not None:
        mrc._write(out / "chunks.json", chunks_file)
    chunks = [c for sid in sample for c in chunks_file.get(sid, [])]
    rule_text = rules.text

    all_freq = mrc._read(out / "frequency.json", {})
    freq = {r: all_freq.setdefault(mrc.column(r, FREQ_SYSTEM), {}) for r in raters}

    def freq_todo(r):
        return [c for c in chunks if c["id"] not in freq[r]]

    if sender is not None and not sender.stopped:
        def on_freq(r, ch):
            def take(text):
                got = parse_freq(text, ch)
                if got is not None:
                    freq[r][ch["id"]] = got
                    mrc._write(out / "frequency.json", all_freq)
            return take
        calls = [(r, freq_prompt(c, rule_text), FREQ_SYSTEM, on_freq(r, c))
                 for c in (chunks[:args.limit] if args.limit else chunks) for r in raters
                 if c["id"] not in freq[r]]
        sender.run(calls)

    # step 2: the delivery judge, for units any rater found relevant
    rel = relevant(freq, raters, chunks)
    rated = {sid for sid in sample if chunks_file.get(sid) and all(
        c["id"] in freq[r] for c in chunks_file[sid] for r in raters)}
    unit_prompts: Dict[Tuple[str, str], List[int]] = {}
    for col, per in rel.items():
        for key, slugs in per.items():
            sid, i = key.rsplit("#", 1)
            if sid not in rated:
                continue
            for slug in slugs:
                unit_prompts.setdefault((sid, slug), [])
                if int(i) not in unit_prompts[(sid, slug)]:
                    unit_prompts[(sid, slug)].append(int(i))
    all_deliv = mrc._read(out / "delivery.json", {})
    deliv = {r: all_deliv.setdefault(mrc.column(r, DELIVERY_SYSTEM), {}) for r in raters}
    units: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for (sid, slug), idxs in sorted(unit_prompts.items()):
        w = walked[sid]
        by_i = {p["i"]: p for p in w["prompts"]}
        last = by_i[max(idxs)]
        meta = sessions[sid]
        if meta["cwd"] not in rules.ctx._roots:
            rules.ctx._roots[meta["cwd"]] = mrc.repo_root_for(meta["cwd"])
        root = rules.ctx._roots[meta["cwd"]]
        mem_dir = mrc.memory_dir_for(meta["cwd"], root, rules.ctx.claude_home)
        fallback_ts = last["ts"]

        def fallback(root=root, mem_dir=mem_dir, ts=fallback_ts):
            files = mrc.claude_md_as_of(root, ts, rules.ctx.claude_home)
            index, _ = mrc.memory_as_of(mem_dir, ts, "")
            return files + ([("MEMORY.md (as of then)", index)] if index else [])
        _, notes = mrc.memory_as_of(mem_dir, last["ts"], rule_text(slug))
        ss = _head("\n\n".join(w["session_start"][:last["session_start_n"]]), SESSION_START_CHARS)
        files = native_files(last, fallback)
        texts = _sha(sid, ss, *[t for f in files for t in f])
        units[(sid, slug)] = {"id": _sha(texts, slug), "texts": texts, "rule": rule_text(slug),
                              "ss": ss, "files": files, "notes": notes[:NOTES_PER_RULE],
                              "empty": not ss and not files}

    if sender is not None and not sender.stopped:
        def on_deliv(r, batch):
            def take(text):
                got = parse_delivery(text, [u["id"] for u in batch])
                if got:
                    deliv[r].update(got)
                    mrc._write(out / "delivery.json", all_deliv)
            return take
        calls = [(r, batch_prompt(b), DELIVERY_SYSTEM, on_deliv(r, b))
                 for r in raters for b in delivery_batches(units, deliv[r])]
        sender.run(calls[:args.limit] if args.limit else calls)

    if scratch:
        os.chdir(here)
        shutil.rmtree(scratch, ignore_errors=True)

    # the numbers
    columns = ([BOTH] if len(raters) > 1 else []) + raters
    prompts_per = {sid: len(walked[sid]["prompts"]) for sid in rated}
    results: Dict[str, Dict[str, Any]] = {STRICT: {}, BROAD: {}}
    regimes: Dict[str, Dict[str, Any]] = {}
    diffs, lift_source = lift_diffs(vault)
    for col in columns:
        session_units: Dict[str, List[Dict[str, Any]]] = {}
        for key, slugs in rel.get(col, {}).items():
            sid, i = key.rsplit("#", 1)
            if sid not in rated:
                continue
            for slug in slugs:
                session_units.setdefault(sid, {}).setdefault(slug, []).append(int(i))
        flat: Dict[str, List[Dict[str, Any]]] = {}
        for sid, per_slug in session_units.items():
            w = walked[sid]
            by_i = {p["i"]: p for p in w["prompts"]}
            for slug, idxs in per_slug.items():
                u = units[(sid, slug)]
                answers = [deliv[r].get(u["id"]) for r in (raters if col == BOTH else [col])]
                if u["empty"]:
                    judged = {"A": False, "B": False}
                elif any(a is None for a in answers):
                    judged = None
                else:
                    judged = {k: all(a[k] for a in answers) for k in ("A", "B")}
                o = unit_outcome(slug, [by_i[i] for i in idxs], w["session_start"], judged,
                                 sessions[sid]["project"], rules.live)
                o["strict"] = bool(strict and slug in strict)
                o["judged"] = judged is not None
                flat.setdefault(sid, []).append(o)
        for reading in (STRICT, BROAD):
            results[reading][col] = combine(per_session_counts(flat, prompts_per, reading), diffs)
        if col == columns[0]:
            for name, keep in regime_split(sessions, prompts_per).items():
                regimes[name] = {reading: combine(per_session_counts(flat, keep, reading), diffs)
                                 for reading in (STRICT, BROAD)}

    agreement: Dict[str, Optional[float]] = {}
    if len(raters) == 2:
        r1, r2 = raters
        keys = sorted(k for k in rel.get(r1, {}) if k in rel.get(r2, {}))
        for reading in (STRICT, BROAD):
            a, b = [], []
            for key in keys:
                sid = key.rsplit("#", 1)[0]
                shown = {s for c in chunks_file.get(sid, []) if any(p["key"] == key for p in c["prompts"])
                         for s in c["rules"]}
                if reading == STRICT:
                    shown &= strict or set()
                for s in sorted(shown):
                    a.append(s in rel[r1][key])
                    b.append(s in rel[r2][key])
            agreement["relevance_" + reading] = mrc.kappa(a, b)
        ids = [it["id"] for it in items if all(it["id"] in labels[r] for r in raters)]
        agreement["strict_label"] = mrc.kappa([labels[r1][i] for i in ids], [labels[r2][i] for i in ids])
        us = [u["id"] for u in units.values() if all(u["id"] in deliv[r] for r in raters)]
        for k in ("A", "B"):
            agreement["delivery_" + k] = mrc.kappa([deliv[r1][i][k] for i in us], [deliv[r2][i][k] for i in us])

    primary = results[STRICT].get(BOTH if len(raters) > 1 else raters[0], {})
    lslugs = lift_slugs(vault)
    data = {
        "since": args.since, "population": len(sessions), "sampled": len(sample), "rated": len(rated),
        "strict_size": None if strict is None else len(strict), "evidence": len(evidence),
        "evidence_off_disk": sum(1 for it in items if not it["on_disk"]),
        "strict_gate_verified": None if strict is None else sum(evidence[s]["gate_verified"] for s in strict),
        "lift_source": lift_source, "lift_rules": len(lslugs), "lift_overlap": len(lslugs & (strict or set())),
        "results": results, "regimes": regimes, "agreement": agreement,
        "verdict": verdict(primary.get("E"), *(primary.get("ci", {}).get("E") or [None, None])),
        "needed_sessions": needed_sessions(len(rated), primary) if primary.get("E") is not None else None,
        "cost": {"mnemo": mnemo_cost({s: walked[s] for s in rated}), "judge": judge_latency(vault, args.since),
                 "study": mrc.spent_by_rater(out / "calls.jsonl", raters)},
    }

    pending = {r: {"label": len(label_todo[r]), "frequency": len(freq_todo(r)),
                   "delivery": len(delivery_batches(units, deliv[r]))}
               for r in raters}
    for r in raters:
        est = notional(r, [(mrc.label_prompt(b), mrc.LABEL_SYSTEM) for b in label_todo[r]], 250) + notional(
            r, [(freq_prompt(c, rule_text), FREQ_SYSTEM) for c in freq_todo(r)], 150) + notional(
            r, [(batch_prompt(b), DELIVERY_SYSTEM) for b in delivery_batches(units, deliv[r])], 400)
        print("%s: pending %d label, %d frequency, %d delivery call(s); notional ~$%.2f "
              "(subscription usage, not money)" % (r, pending[r]["label"], pending[r]["frequency"],
                                                  pending[r]["delivery"], est), file=sys.stderr)
    if strict is None:
        print("the strict pool needs its labels first (--send); chunks are not built until then",
              file=sys.stderr)
    if sender is not None:
        print("spent $%.2f notional this run" % sender.usd, file=sys.stderr)
    data["pending"] = pending
    mrc._write(out / "report.json", data)
    if args.json:
        print(json.dumps(data, indent=1))
        return 0
    for line in report_lines(data):
        print(line)
    print("")
    print("VERDICT (strict reading, both raters): %s" % data["verdict"].upper())
    return 0


if __name__ == "__main__":
    sys.exit(main())
