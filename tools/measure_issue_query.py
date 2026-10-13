"""Would ranking on the issue an agent reads reach the rules a pointer prompt can't? (#635)

Usage:
    PYTHONPATH=src python3 tools/measure_issue_query.py --dry-run     # build, counters, pending calls and cost
    PYTHONPATH=src python3 tools/measure_issue_query.py --send        # Jev scores, then the rater's labels
        [--workers W] [--pause S] [--limit N] [--rater MODEL]
    PYTHONPATH=src python3 tools/measure_issue_query.py               # the report, local
    PYTHONPATH=src python3 tools/measure_issue_query.py --json        # the same as data

A prompt that only points at an issue ("read issue 255") gives the reflex
nothing to rank on. This asks whether ranking and judging on the issue text
the agent reads next would deliver the on-point rules the pointer cannot.

**Counters (#635's scratch scripts, shipped here).** Over every transcript on
disk (sidechains skipped): typed prompts; prompts of at most
:data:`POINTER_MAX` characters that name an issue (:data:`ISSUE_RE`); how many
of those the agent followed with ``gh issue view`` in the same turn; and how
many of those it followed with ``list_rules_by_topic``. For dispatch children
(opening prompt ``_PROMPT`` / ``_ANALYSIS_PROMPT``): how many, in how many the
reflex fired, and in how many it fired before the first tool use. The scratch
counters read raw text; these read the repo's typed-turn filter
(``transcript.plain_user_text``) and hook attachments, so a count can differ
by a few from the scratch numbers.

**Population.**

- *human*: sessions where a typed prompt of at most :data:`POINTER_MAX`
  characters names an issue and the agent then runs ``gh issue view`` on a
  number that prompt names, in that turn, and gets a result. The first such
  pointer per session. The issue text is that call's ``tool_result``, as a
  ``PostToolUse`` hook would receive it.
- *children*: every child whose opening prompt is ``_PROMPT`` or
  ``_ANALYSIS_PROMPT``; the issue text is the title and body embedded in it.
  Sampled (#635's amendment, fixed before measuring): sorted by
  ``sha256(session_id)`` hex, the first :data:`CHILD_SAMPLE`.

A transcript whose pointer (or opening prompt) repeats another's byte for
byte is a resumed copy and is dropped, the earliest kept.

**Arms.** Each runs the shipped path as ``measure_jev_pool`` replays it: BM25F
over the rules the vault held at the session's start, never learned from the
session itself (``measure_prevented_repeats.Rules``); the token pre-gate; the
judge over the top :data:`POOL` at ``injectAt`` :data:`AT` (``BASELINE``),
picking with ``judge.chosen``; the session cap and dedupe against the rules
the transcript shows the reflex had injected before the pointer prompt (none,
for a child's opening prompt). Exported rules and per-project ``bm25f``
overrides are not simulated.

- **A, pointer:** what the human typed; for a child, its opening line
  (``Work on issue #N in this repo: {title}``), the most a pointer carries.
- **B, issue:** BM25F ranks the whole issue text; the judge reads its first
  1,200 characters, as ``judge.message`` cuts any prompt.
- **B-head:** B, with BM25F also ranking only those 1,200 characters (#401's
  dilution).

**Labels.** One blind rater (:data:`DEFAULT_RATER`, through ``core.llm``)
labels 0/1/2 with ``measure_reflex_reach.RATER_SYSTEM`` every rule any arm
injects, against the issue text (its first :data:`RATER_ISSUE_CHARS`
characters), so one label per (session, rule) serves every arm.

**Readings, per arm and population:** reach (sessions with >= 1 on-point
injection), precision (on-point share of injected rules), injections per
session, and the rules that carry B - A. Differences are paired over
sessions, with a seeded session bootstrap (:data:`BOOT_REPS`).

**Bar (declared in #635 before measuring).** For BUILD all must hold, human
and children pooled: (1) reach B - A >= :data:`REACH_PP` with the CI lower
bound > 0; (2) B's precision >= :data:`PRECISION_BAR`; (3) B - A > 0 on the
human sessions alone; (4) (1)'s CI lower bound stays > 0 without the single
rule that contributes most to the difference. If B-head passes where B
fails, B-head is the build target. Anything else is DO NOT BUILD, naming the
failed condition.

**First dry run, 2026-10-13, the maintainer's machine (zero tokens).**
Counters: 5,779 typed prompts; 41 of <= 200 characters name an issue, in 39
sessions; the agent ran ``gh issue view`` after 16 of them (16 sessions) and
``list_rules_by_topic`` after 2 of those. 522 children have an issue opening
prompt; the reflex fired in 410, before the first tool use in 348. Population:
**12 human sessions**, not 16: three of the 16 read a range of issues in a
shell loop (``for i in 114 ... 120; do gh issue view $i``), and one opened a
different issue from the one its prompt named, so none of the four has a
single issue text to rank. 509 children after 13 resumed copies, sample 200
(first hash ``00fa523a…``, last ``59f7f0f5…``). Pending: 423 Jev requests
(~0.55M input tokens, ~US$ 0.02 at list price) and at most 1,139 rater pairs.

**First send, 2026-10-13, the maintainer's vault (population frozen by a
second dry run: 12 human, 200 of 511 children, last hash ``5997f64f…``).**
423 Jev requests, 0 failed; 31 rater calls (``claude-sonnet-5``), US$ 2.50
API-price equivalent; 212 of 212 sessions complete.

=========  ==============  ==============  ==============
reach      pooled (212)    human (12)      children (200)
=========  ==============  ==============  ==============
A          48.6%           25.0% (3)       50.0%
B          52.8%           75.0% (9)       51.5%
B-head     52.8%           75.0% (9)       51.5%
=========  ==============  ==============  ==============

B - A pooled +4.2 pp [-2.8, +11.3]; human +50.0 pp [+25.0, +75.0]; children
+1.5 pp [-6.0, +9.0]. Precision pooled A 55.1% (146/265), B 72.0% (172/239),
B-head 68.2% (163/239). Without the top rule (``contract-dispatch-design-pattern``)
B - A is +3.3 pp [-3.8, +10.4]. **VERDICT: DO NOT BUILD (B and B-head fail
conditions 1 and 4).**

What carries the pooled result is the children's arm A: their opening line
carries the issue's **title**, and the title alone already reaches 50% of
the sample, which the body leaves unchanged. The body still raises their
precision from 55.0% to 71.0% at the same reach. A human pointer carries no
title. On the 12 human sessions, B reaches an on-point rule in 9 where A
reaches 3, and precision is 86.7% (13/15). That reading was not the
pre-registered decision, and its n is 12. It is also bounded by frequency:
16 sessions in the 48 days of transcripts on disk followed a pointer prompt
with ``gh issue view``, so even at +50 pp that is about 8 more sessions
reached in 48 days.

**Sending.** ``--send`` sends issue text and rule text to TypeSafe (the judge,
with the key ``mnemo rerank --setup`` stored) and to the rater. Issue bodies
come from every project on disk, private ones included: the maintainer runs
it. ``--dry-run`` prints the pending calls and their notional cost. Answers
are cached under ``<vault>/.mnemo/issue-query`` as they arrive; a rerun
resumes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import random
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mjp = _sibling("measure_jev_pool")
mrr = mjp.mrr
mpr = mjp.mpr
mrc = mjp.mrc
mrl = mrr.mrl

OUT_DIR = "issue-query"
POPULATION_NAME = "population.json"
SCORES_NAME = "scores.json"
LABELS_NAME = "labels.json"
REPORT_NAME = "report.json"

SEED = 635
#: The shipped judge config (``measure_jev_pool.BASELINE``): pool and bar.
POOL, AT = mjp.BASELINE
#: How deep each arm's ranking is kept, for the report; the judge reads POOL.
DEPTH = 10
POINTER_MAX = 200
CHILD_SAMPLE = 200
RATER_ISSUE_CHARS = 6000
#: Rules per rater call; a session is never split across calls.
RATER_BATCH = 15
DEFAULT_RATER = mrr.DEFAULT_RATER
BOOT_REPS = 5000

REACH_PP = 0.15
#: #411's test reading for the Jev gate at 0.40.
PRECISION_BAR = 0.23

ARMS = ("A", "B", "B-head")
#: Which text the judge reads per arm: B and B-head read the same 1,200
#: characters, so one Jev request per session serves both.
JUDGE_TEXT = {"A": "pointer", "B": "issue", "B-head": "issue"}
HUMAN, CHILDREN = "human", "children"
POPULATIONS = ("pooled", HUMAN, CHILDREN)

#: #635's scratch regex: a prompt that names an issue.
ISSUE_RE = re.compile(r"(?i)(\bissues?\b\s*#?\s*\d{2,4}|#\d{2,4}\b)")
_DIGITS = re.compile(r"\d+")
_VIEW = re.compile(r"\bgh\s+issue\s+view\b")
_SEGMENT_END = re.compile(r"\|\||&&|[|;\n]")
_ISSUE_URL = re.compile(r"/issues/(\d+)")
_CHILD_HEAD = re.compile(r"\A(?:Work on|Investigate) issue #(\d+) in this repo: ([^\n]*)")
_CHILD_BODY_START = "The body as it stands:\n\n---\n"
_CHILD_BODY_END = "\n---\n\nYou are in a git worktree"
_LIST_TOOL = re.compile(r"mnemo.*__list_rules_by_topic$")


# --- reading transcripts ------------------------------------------------------------

def named_issues(text: str) -> Set[int]:
    """The issue numbers a prompt names, by #635's regex."""
    return {int(n) for m in ISSUE_RE.finditer(text or "") for n in _DIGITS.findall(m.group(0))}


def viewed_issues(command: str) -> List[int]:
    """The issue numbers each ``gh issue view`` in a shell command opens.

    The argument is the first one that is a number, ``#N`` or an issue URL,
    flags and their values skipped; a segment ends at a pipe, ``&&``, ``;`` or
    a newline.
    """
    out = []
    for m in _VIEW.finditer(command or ""):
        rest = command[m.end():]
        end = _SEGMENT_END.search(rest)
        for tok in (rest[:end.start()] if end else rest).split():
            tok = tok.strip("'\"")
            url = _ISSUE_URL.search(tok)
            if url:
                out.append(int(url.group(1)))
                break
            if tok.lstrip("#").isdigit():
                out.append(int(tok.lstrip("#")))
                break
    return out


def scan(events: Iterable[Any]) -> Dict[str, Any]:
    """One transcript, main thread only: typed turns with the tool calls the
    agent made before the next one and the reflex rules injected before each;
    tool results by id; where the reflex first fired and the first tool use
    (event positions)."""
    from mnemo.core.transcript import SYNTHETIC_TURN, plain_user_text

    turns: List[Dict[str, Any]] = []
    results: Dict[str, Dict[str, Any]] = {}
    reflex: List[str] = []
    first_reflex = first_tool = None
    pos = 0
    for ev in events:
        if not isinstance(ev, dict) or ev.get("isSidechain"):
            continue
        pos += 1
        att = ev.get("attachment")
        if isinstance(att, dict):
            if att.get("type") == "hook_additional_context":
                for text in mpr._hook_texts(att):
                    if mpr._REFLEX in text:
                        if first_reflex is None:
                            first_reflex = pos
                        reflex.extend(s for s in mpr.reflex_slugs(text) if s not in reflex)
            continue
        msg = ev.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if ev.get("type") == "assistant":
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    if first_tool is None:
                        first_tool = pos
                    if turns:
                        turns[-1]["uses"].append({"id": str(b.get("id") or ""), "name": str(b.get("name") or ""),
                                                  "input": b.get("input") or {}})
            continue
        if ev.get("type") != "user" or ev.get("isMeta"):
            continue
        for b in content if isinstance(content, list) else []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                results[str(b.get("tool_use_id") or "")] = {"text": mpr._result_text(b),
                                                            "error": bool(b.get("is_error"))}
        text = plain_user_text(content)
        if not text or SYNTHETIC_TURN.search(text):
            continue
        turns.append({"i": len(turns), "ts": mrc.epoch(ev.get("timestamp")), "text": text, "uses": [],
                      "reflex_before": list(reflex)})
    return {"turns": turns, "results": results, "first_reflex": first_reflex, "first_tool": first_tool}


def child_opening(scanned: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The issue a dispatch child was opened on, or None for any other session."""
    turns = scanned["turns"]
    if not turns:
        return None
    text = turns[0]["text"]
    head = _CHILD_HEAD.match(text)
    start = text.find(_CHILD_BODY_START)
    end = text.rfind(_CHILD_BODY_END)
    if not head or start < 0 or end < start:
        return None
    title = head.group(2).strip()
    body = text[start + len(_CHILD_BODY_START):end]
    return {"issue": int(head.group(1)), "pointer": head.group(0), "issue_text": "%s\n\n%s" % (title, body),
            "opening": text, "ts": turns[0]["ts"], "prior": []}


def is_bash_view(use: Dict[str, Any]) -> bool:
    return use["name"] == "Bash" and bool(_VIEW.search(str((use.get("input") or {}).get("command") or "")))


def human_pointer(scanned: Dict[str, Any]) -> Tuple[Dict[str, int], Optional[Dict[str, Any]]]:
    """The scratch counter's tallies over one session, and its first pointer
    followed by ``gh issue view`` on a number the pointer names."""
    counts = {"prompts": 0, "short_issue_prompts": 0, "read_issue": 0, "listed_after_read": 0}
    found = None
    for t in scanned["turns"]:
        counts["prompts"] += 1
        if len(t["text"]) > POINTER_MAX or not ISSUE_RE.search(t["text"]):
            continue
        counts["short_issue_prompts"] += 1
        at = next((k for k, u in enumerate(t["uses"]) if is_bash_view(u)), None)
        if at is None:
            continue
        counts["read_issue"] += 1
        if any(_LIST_TOOL.search(u["name"]) for u in t["uses"][at:]):
            counts["listed_after_read"] += 1
        if found is not None:
            continue
        named = named_issues(t["text"])
        for u in t["uses"]:
            if not is_bash_view(u):
                continue
            hit = [n for n in viewed_issues(str(u["input"].get("command") or "")) if n in named]
            res = scanned["results"].get(u["id"]) or {}
            if hit and not res.get("error") and (res.get("text") or "").strip():
                found = {"issue": hit[0], "pointer": t["text"], "issue_text": res["text"], "ts": t["ts"],
                         "prior": list(t["reflex_before"])}
                break
    return counts, found


def project_of(cwd: str) -> str:
    """The project the hook ranked under. A dispatch child's tree is usually
    gone by now, and an unresolvable path names itself; its main checkout
    beside it resolves to the same project the hook saw."""
    from mnemo.core import agent, dispatch

    m = dispatch._WT_RE.search(cwd or "")
    return agent.resolve_canonical_agent(cwd[:m.start()] if m else cwd).name


def session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def sample_children(children: Sequence[Dict[str, Any]], n: int = CHILD_SAMPLE) -> Dict[str, Any]:
    """#635's amendment: sorted by sha256(session_id) hex, the first ``n``."""
    ordered = sorted(children, key=lambda c: session_hash(c["sid"]))
    taken = ordered[:n]
    return {"eligible": len(ordered), "taken": taken,
            "first_hash": session_hash(taken[0]["sid"]) if taken else None,
            "last_hash": session_hash(taken[-1]["sid"]) if taken else None}


def dedupe(entries: Sequence[Dict[str, Any]], key: Callable[[Dict[str, Any]], str]) -> Tuple[List[Dict[str, Any]], int]:
    """One entry per key, the earliest; and how many were dropped."""
    seen: Dict[str, Dict[str, Any]] = {}
    for e in sorted(entries, key=lambda e: (e.get("start") or 0.0, e["sid"])):
        seen.setdefault(key(e), e)
    kept = sorted(seen.values(), key=lambda e: e["sid"])
    return kept, len(entries) - len(kept)


def collect(projects_dir: Path, load: Optional[Callable[[Path], List[Any]]] = None) -> Dict[str, Any]:
    """Every transcript on disk: the counters and both populations, unranked."""
    if load is None:
        from mnemo.core.briefing import _load_jsonl_events as load
    counters = {"prompts": 0, "short_issue_prompts": 0, "read_issue": 0, "listed_after_read": 0,
                "short_issue_sessions": 0, "read_sessions": 0,
                "children": 0, "children_reflex_fired": 0, "children_fired_before_first_tool": 0}
    human: List[Dict[str, Any]] = []
    children: List[Dict[str, Any]] = []
    for path in sorted(Path(projects_dir).glob("*/*.jsonl")):
        events = load(path)
        scanned = scan(events)
        cwd = mpr.mcc.session_cwd(events)
        first = mpr.mcc.first_timestamp(events)
        base = {"sid": path.stem, "path": str(path), "cwd": cwd,
                "start": first.timestamp() if first is not None else None}
        child = child_opening(scanned)
        if child is not None:
            counters["children"] += 1
            fired, tool = scanned["first_reflex"], scanned["first_tool"]
            counters["children_reflex_fired"] += fired is not None
            counters["children_fired_before_first_tool"] += fired is not None and (tool is None or fired < tool)
            children.append(dict(base, population=CHILDREN, **child))
            continue
        counts, found = human_pointer(scanned)
        for k, v in counts.items():
            counters[k] += v
        counters["short_issue_sessions"] += counts["short_issue_prompts"] > 0
        counters["read_sessions"] += counts["read_issue"] > 0
        if found is not None:
            human.append(dict(base, population=HUMAN, **found))
    return {"counters": counters, "human": human, "children": children}


# --- ranking ------------------------------------------------------------------------

def arm_queries(entry: Dict[str, Any]) -> Dict[str, str]:
    """What each arm's BM25F ranks."""
    from mnemo.core.reflex import judge

    return {"A": entry["pointer"], "B": entry["issue_text"], "B-head": judge.message(entry["issue_text"])[0]}


def judge_texts(entry: Dict[str, Any]) -> Dict[str, str]:
    """What the judge is handed per text; ``judge.ask`` cuts it to 1,200."""
    return {"pointer": entry["pointer"], "issue": entry["issue_text"]}


def rank(entry: Dict[str, Any], rules: Any, min_tokens: int) -> Dict[str, Any]:
    from mnemo.core.reflex.tokenizer import tokenize_query

    pool = rules.pool(entry["project"], entry["start"], entry["sid"])
    return {arm: {"tokens_ok": len(set(tokenize_query(q))) >= min_tokens,
                  "ranking": rules.top(q, pool, DEPTH)}
            for arm, q in arm_queries(entry).items()}


def build(projects_dir: Path, rules: Any, cap: int, min_tokens: int,
          load: Optional[Callable[[Path], List[Any]]] = None) -> Dict[str, Any]:
    """The frozen population: counters, the human sessions, the sampled
    children, each with its project and every arm's ranking."""
    got = collect(projects_dir, load)
    human, human_dupes = dedupe(got["human"], lambda e: "%s\x00%s" % (e["pointer"], e["ts"]))
    children, child_dupes = dedupe(got["children"], lambda e: e["opening"])
    sample = sample_children(children)
    entries = []
    for e in human + sample["taken"]:
        e = {k: v for k, v in e.items() if k != "opening"}
        e["project"] = project_of(e["cwd"])
        if e.get("start") is None:
            e["start"] = e.get("ts")
        e["arms"] = rank(e, rules, min_tokens)
        entries.append(e)
    return {"counters": got["counters"], "cap": cap, "min_tokens": min_tokens, "pool": POOL, "at": AT,
            "human_eligible": len(human), "human_duplicates": human_dupes,
            "children_eligible": sample["eligible"], "children_duplicates": child_dupes,
            "children_sample": len(sample["taken"]), "sample_first_hash": sample["first_hash"],
            "sample_last_hash": sample["last_hash"], "sessions": entries}


# --- the replay ---------------------------------------------------------------------

def candidates(entry: Dict[str, Any], arm: str, cap: int) -> List[str]:
    """What the hook would hand the judge: the top :data:`POOL` past the
    pre-gate and the cap, minus what the session was already told."""
    a = entry["arms"][arm]
    if not a["tokens_ok"] or len(entry.get("prior") or []) >= cap:
        return []
    return [s for s in a["ranking"][:POOL] if s not in set(entry.get("prior") or [])]


def needed(entry: Dict[str, Any], scores: Dict[str, Any], cap: int) -> Dict[str, List[str]]:
    """Per judge text, the rules Jev has not been asked about yet."""
    asked = scores.get(entry["sid"]) or {}
    out: Dict[str, List[str]] = {}
    for arm in ARMS:
        text = JUDGE_TEXT[arm]
        done = set((asked.get(text) or {}).get("slugs") or [])
        for s in candidates(entry, arm, cap):
            if s not in done and s not in out.setdefault(text, []):
                out[text].append(s)
    return {k: v for k, v in out.items() if v}


def injected(entry: Dict[str, Any], arm: str, scores: Dict[str, Any], cap: int) -> Optional[List[str]]:
    """What the arm injects, best first; None while Jev has not answered for
    every candidate. A rule asked about and never scored (no text) is not
    injected, as in ``judge.ask``."""
    from mnemo.core.reflex import judge

    cand = candidates(entry, arm, cap)
    row = (scores.get(entry["sid"]) or {}).get(JUDGE_TEXT[arm]) or {}
    if any(s not in (row.get("slugs") or []) for s in cand):
        return None
    got = row.get("scores") or {}
    return judge.chosen({s: got[s] for s in cand if s in got}, AT)


def merge_score(scores: Dict[str, Any], sid: str, text: str, slugs: Sequence[str], row: Dict[str, Any]) -> None:
    """Fold one answered request into the cache."""
    cur = scores.setdefault(sid, {}).setdefault(text, {"slugs": [], "scores": {}, "ms": []})
    cur["slugs"] = sorted(set(cur["slugs"]) | set(slugs))
    cur["scores"].update(row.get("scores") or {})
    cur["ms"].append(row.get("ms"))


def session_rows(entries: Sequence[Dict[str, Any]], scores: Dict[str, Any], labels: Dict[str, Dict[str, int]],
                 cap: int) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Per complete session, per arm, what was injected and which of it is
    on-point; and how many sessions wait on Jev or on labels."""
    rows, pending = [], {"jev": 0, "labels": 0}
    for e in entries:
        inj = {arm: injected(e, arm, scores, cap) for arm in ARMS}
        if any(v is None for v in inj.values()):
            pending["jev"] += 1
            continue
        lab = labels.get(e["sid"]) or {}
        if any(s not in lab for v in inj.values() for s in v):
            pending["labels"] += 1
            continue
        rows.append({"sid": e["sid"], "population": e["population"],
                     "arms": {arm: {"inj": list(v), "on": [s for s in v if lab[s] == mrr.ON_POINT]}
                              for arm, v in inj.items()}})
    return rows, pending


def rater_units(entries: Sequence[Dict[str, Any]], scores: Dict[str, Any], cap: int,
                read_text: Callable[[str, str], str]) -> List[Dict[str, Any]]:
    """``measure_reflex_reach.batches``' unit shape: the issue text and every
    rule any arm injects, for sessions Jev has fully answered."""
    out = []
    for e in entries:
        inj = [injected(e, arm, scores, cap) for arm in ARMS]
        if any(v is None for v in inj):
            continue
        slugs = sorted({s for v in inj for s in v})
        if slugs:
            out.append({"uid": e["sid"], "prompt": e["issue_text"][:RATER_ISSUE_CHARS],
                        "pool": [{"slug": s, "text": read_text(e["project"], s)} for s in slugs]})
    return out


# --- readings -----------------------------------------------------------------------

def _drop(rows: Sequence[Dict[str, Any]], rule: Optional[str]) -> List[Dict[str, Any]]:
    if rule is None:
        return list(rows)
    return [dict(r, arms={a: {"inj": [s for s in v["inj"] if s != rule], "on": [s for s in v["on"] if s != rule]}
                          for a, v in r["arms"].items()}) for r in rows]


def reach(rows: Sequence[Dict[str, Any]], arm: str) -> Optional[float]:
    return sum(1 for r in rows if r["arms"][arm]["on"]) / len(rows) if rows else None


def precision(rows: Sequence[Dict[str, Any]], arm: str) -> Optional[float]:
    n = sum(len(r["arms"][arm]["inj"]) for r in rows)
    return sum(len(r["arms"][arm]["on"]) for r in rows) / n if n else None


def per_session(rows: Sequence[Dict[str, Any]], arm: str) -> Optional[float]:
    return sum(len(r["arms"][arm]["inj"]) for r in rows) / len(rows) if rows else None


def _quantiles(xs: List[float]) -> List[Optional[float]]:
    if not xs:
        return [None, None]
    xs = sorted(xs)
    return [xs[int(0.025 * (len(xs) - 1))], xs[int(round(0.975 * (len(xs) - 1)))]]


def bootstrap(rows: Sequence[Dict[str, Any]], stat: Callable[[Sequence[Dict[str, Any]]], Optional[float]],
              reps: int = BOOT_REPS, seed: int = SEED) -> List[Optional[float]]:
    """Percentile 95% interval of ``stat`` over sessions resampled with
    replacement; both arms of a session travel together (paired)."""
    if not rows:
        return [None, None]
    rnd = random.Random(seed)
    n = len(rows)
    out = []
    for _ in range(reps):
        got = stat([rows[rnd.randrange(n)] for _ in range(n)])
        if got is not None:
            out.append(got)
    return _quantiles(out)


def delta(x: str, y: str = "A") -> Callable[[Sequence[Dict[str, Any]]], Optional[float]]:
    def stat(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
        a, b = reach(rows, x), reach(rows, y)
        return None if a is None or b is None else a - b
    return stat


def carriers(rows: Sequence[Dict[str, Any]], x: str, y: str = "A") -> List[Tuple[str, int, int]]:
    """``(rule, sessions it reaches in x but not in y, sessions in y but not
    in x)``, the biggest net carriers first; ties by slug."""
    gain: Dict[str, int] = {}
    loss: Dict[str, int] = {}
    for r in rows:
        on_x, on_y = set(r["arms"][x]["on"]), set(r["arms"][y]["on"])
        for s in on_x - on_y:
            gain[s] = gain.get(s, 0) + 1
        for s in on_y - on_x:
            loss[s] = loss.get(s, 0) + 1
    slugs = set(gain) | set(loss)
    return sorted(((s, gain.get(s, 0), loss.get(s, 0)) for s in slugs), key=lambda t: (-(t[1] - t[2]), t[0]))


def top_rule(rows: Sequence[Dict[str, Any]], x: str, y: str = "A") -> Optional[str]:
    """The rule whose removal lowers reach(x) - reach(y) the most; ties by slug."""
    base = delta(x, y)(rows)
    if base is None:
        return None
    best: Optional[Tuple[float, str]] = None
    for s in sorted({s for r in rows for a in (x, y) for s in r["arms"][a]["on"]}):
        drop = base - (delta(x, y)(_drop(rows, s)) or 0.0)
        if best is None or drop > best[0]:
            best = (drop, s)
    return best[1] if best else None


def arm_reading(rows: Sequence[Dict[str, Any]], arm: str) -> Dict[str, Any]:
    on = sum(len(r["arms"][arm]["on"]) for r in rows)
    n = sum(len(r["arms"][arm]["inj"]) for r in rows)
    return {"sessions": len(rows), "reached": sum(1 for r in rows if r["arms"][arm]["on"]),
            "reach": reach(rows, arm), "injected": n, "on_point": on, "precision": precision(rows, arm),
            "precision_ci": bootstrap(rows, lambda rs: precision(rs, arm)), "per_session": per_session(rows, arm)}


def verdict(pooled: Sequence[Dict[str, Any]], human: Sequence[Dict[str, Any]], x: str) -> Dict[str, Any]:
    """#635's four conditions for arm ``x`` against A."""
    d = delta(x)(pooled)
    ci = bootstrap(pooled, delta(x))
    prec = precision(pooled, x)
    d_human = delta(x)(human)
    rule = top_rule(pooled, x)
    without = _drop(pooled, rule)
    ci_without = bootstrap(without, delta(x))
    failed = []
    if d is None or d < REACH_PP or ci[0] is None or ci[0] <= 0:
        failed.append("1 reach: B - A < +%.0f pp or its CI lower bound <= 0" % (100 * REACH_PP))
    if prec is None or prec < PRECISION_BAR:
        failed.append("2 precision < %.0f%%" % (100 * PRECISION_BAR))
    if d_human is None or d_human <= 0:
        failed.append("3 human sessions alone: B - A <= 0")
    if ci_without[0] is None or ci_without[0] <= 0:
        failed.append("4 without the top rule the reach CI lower bound <= 0")
    return {"arm": x, "delta": d, "ci": ci, "precision": prec, "human_delta": d_human, "top_rule": rule,
            "delta_without_top": delta(x)(without), "ci_without_top": ci_without,
            "pass": not failed, "failed": failed}


def decision(b: Dict[str, Any], b_head: Dict[str, Any]) -> str:
    if b["pass"]:
        return "BUILD (B: the whole issue text)"
    if b_head["pass"]:
        return "BUILD (B-head: the issue's first 1,200 characters)"
    return "DO NOT BUILD (B failed %s; B-head failed %s)" % (
        ", ".join(f.split(" ", 1)[0] for f in b["failed"]), ", ".join(f.split(" ", 1)[0] for f in b_head["failed"]))


def readings(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by = {"pooled": list(rows), HUMAN: [r for r in rows if r["population"] == HUMAN],
          CHILDREN: [r for r in rows if r["population"] == CHILDREN]}
    out: Dict[str, Any] = {"arms": {p: {arm: arm_reading(rs, arm) for arm in ARMS} for p, rs in by.items()},
                           "deltas": {p: {x: {"delta": delta(x)(rs), "ci": bootstrap(rs, delta(x))}
                                          for x in ("B", "B-head")} for p, rs in by.items()},
                           "carriers": {x: [list(t) for t in carriers(rows, x)[:8]] for x in ("B", "B-head")}}
    if rows:
        out["verdicts"] = {x: verdict(by["pooled"], by[HUMAN], x) for x in ("B", "B-head")}
        out["decision"] = decision(out["verdicts"]["B"], out["verdicts"]["B-head"])
    return out


# --- report -------------------------------------------------------------------------

def _p(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def _pp(x: Optional[float]) -> str:
    return "n/a" if x is None else "%+.1f pp" % (100 * x)


def _ci(ci: Sequence[Optional[float]], signed: bool = True) -> str:
    return "[%s, %s]" % tuple("n/a" if v is None else ("%+.1f" if signed else "%.1f") % (100 * v) for v in ci)


def report_lines(data: Dict[str, Any]) -> List[str]:
    c = data["counters"]
    lines = [
        "counters (every transcript on disk): %d typed prompts; %d of <= %d chars name an issue, in %d sessions; "
        "the agent ran `gh issue view` after %d of them (%d sessions), then list_rules_by_topic after %d"
        % (c["prompts"], c["short_issue_prompts"], POINTER_MAX, c["short_issue_sessions"], c["read_issue"],
           c["read_sessions"], c["listed_after_read"]),
        "dispatch children with an issue opening prompt: %d; the reflex fired in %d, before the first tool use in %d"
        % (c["children"], c["children_reflex_fired"], c["children_fired_before_first_tool"]),
        "population: human %d (%d resumed copies dropped); children %d eligible (%d copies dropped), sample %d "
        "by sha256(session_id), first %s, last %s"
        % (data["human_eligible"], data["human_duplicates"], data["children_eligible"], data["children_duplicates"],
           data["children_sample"], (data["sample_first_hash"] or "-")[:16], (data["sample_last_hash"] or "-")[:16]),
        "replay: pool %d, injectAt %.1f, cap %d, minQueryTokens %d; complete %d of %d sessions (%d wait on Jev, "
        "%d on labels); rater %s" % (data["pool"], data["at"], data["cap"], data["min_tokens"], data["complete"],
                                     data["sessions"], data["pending"]["jev"], data["pending"]["labels"],
                                     data["rater"]),
    ]
    r = data.get("readings") or {}
    for pop in POPULATIONS:
        arms = (r.get("arms") or {}).get(pop) or {}
        if not arms:
            continue
        lines += ["", "%s (%d sessions)" % (pop, arms["A"]["sessions"]),
                  "  arm      reach            precision  [95% CI]          inj/session"]
        for arm in ARMS:
            a = arms[arm]
            lines.append("  %-7s  %6s (%3d)   %6s (%d/%d) %-16s  %5.2f" % (
                arm, _p(a["reach"]), a["reached"], _p(a["precision"]), a["on_point"], a["injected"],
                _ci(a["precision_ci"], signed=False), a["per_session"] or 0.0))
        for x, d in r["deltas"][pop].items():
            lines.append("  %s - A: %s %s" % (x, _pp(d["delta"]), _ci(d["ci"])))
    for x, rows in (r.get("carriers") or {}).items():
        if rows:
            lines += ["", "rules carrying %s - A (sessions reached only in %s / only in A):" % (x, x)]
            lines += ["  %-48s +%d / -%d" % (s, g, l) for s, g, l in rows]
    for x, v in (r.get("verdicts") or {}).items():
        lines += ["", "%s: reach %s %s (bar +%.0f pp, CI lower > 0); precision %s (bar %s); human alone %s; "
                  "without %s: %s %s -> %s%s"
                  % (x, _pp(v["delta"]), _ci(v["ci"]), 100 * REACH_PP, _p(v["precision"]), _p(PRECISION_BAR),
                     _pp(v["human_delta"]), v["top_rule"], _pp(v["delta_without_top"]), _ci(v["ci_without_top"]),
                     "PASS" if v["pass"] else "FAIL", "" if v["pass"] else " (" + "; ".join(v["failed"]) + ")")]
    if r.get("decision"):
        lines += ["", "VERDICT: " + r["decision"]]
    else:
        lines += ["", "VERDICT: none yet (no session complete; --dry-run, then --send)"]
    return lines


# --- driver -------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.reflex import judge

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=str(Path("~/.claude/projects").expanduser()))
    ap.add_argument("--claude-home", default=str(Path("~/.claude").expanduser()))
    ap.add_argument("--out", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--workers", type=int, default=mjp.WORKERS)
    ap.add_argument("--pause", type=float, default=mjp.PAUSE_SECONDS)
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N Jev requests and N rater calls")
    ap.add_argument("--rater", default=DEFAULT_RATER)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    reflex_cfg = cfg.get("reflex") or {}

    frozen = mrc._read(out / POPULATION_NAME, None)
    if frozen is None:
        rules = mpr.Rules(vault, Path(args.projects), Path(args.claude_home))
        frozen = build(Path(args.projects), rules, int(reflex_cfg.get("maxEmissionsPerSession", 10)),
                       int((reflex_cfg.get("thresholds") or {}).get("minQueryTokens", 3)))
        mrc._write(out / POPULATION_NAME, frozen)
    entries = frozen["sessions"]
    cap = int(frozen["cap"])
    by_sid = {e["sid"]: e for e in entries}
    scores = mrc._read(out / SCORES_NAME, {})
    all_labels = mrc._read(out / LABELS_NAME, {})
    labels = all_labels.setdefault(mrr.column(args.rater), {})

    readers: Dict[str, Callable[[str], str]] = {}

    def read_text(project: str, slug: Optional[str] = None) -> Any:
        if project not in readers:
            raw = judge.page_text(vault, project=project)
            memo: Dict[str, str] = {}

            def cached(s: str) -> str:
                if s not in memo:
                    memo[s] = raw(s)
                return memo[s]
            readers[project] = cached
        return readers[project] if slug is None else readers[project](slug)

    def jev_jobs() -> List[Tuple[str, str, str, List[str]]]:
        return [(e["sid"], text, judge_texts(e)[text], slugs) for e in entries
                for text, slugs in sorted(needed(e, scores, cap).items())]

    if args.dry_run:
        jobs = jev_jobs()
        chars = [mjp.request_chars({"text": t}, slugs, read_text(by_sid[sid]["project"])) for sid, _, t, slugs in jobs]
        tokens = sum(chars) / mjp.CHARS_PER_TOKEN
        print("\n".join(report_lines(dict(frozen, complete=0, sessions=len(entries),
                                          pending={"jev": len(entries), "labels": 0}, rater=args.rater))[:4]))
        print("would send %d Jev requests (one per session and judge text, <= %d rules each) to TypeSafe model %s; "
              "~%.3fM input tokens, ~$%.2f at $%.3f/M"
              % (len(jobs), POOL * 2, judge.settings(cfg).get("model"), tokens / 1e6,
                 tokens / 1e6 * mjp.JEV_USD_PER_M, mjp.JEV_USD_PER_M))
        if jobs:
            sid, text, t, slugs = jobs[0]
            print("one request: the %s text (%d chars, sent up to %d) and %d rule texts, e.g. %s"
                  % (text, len(t), judge.PROMPT_CHARS, len(slugs), ", ".join(slugs)))
        units = rater_units(entries, scores, cap, read_text)
        if jobs:
            bound = sum(len({s for arm in ARMS for s in candidates(e, arm, cap)}) for e in entries)
            print("then the rater (%s): at most %d (session, rule) pairs, the union of every arm's judge pool; "
                  "the exact count once Jev has answered" % (args.rater, bound))
        else:
            e = mrr.estimate(units, labels)
            print("rater (%s): %d pending pairs; ~%d calls of <= %d rules" % (
                args.rater, e["pairs"], len(mrr.batches(units, labels, size=RATER_BATCH)), RATER_BATCH))
        return 0

    if args.send:
        settings = dict(judge.settings(cfg), timeoutSeconds=mjp.MEASURE_TIMEOUT_S, injectAt=0.0)
        jobs = jev_jobs()[:args.limit] if args.limit else jev_jobs()
        asked = {"%s|%s" % (sid, text): slugs for sid, text, _, slugs in jobs}
        calls = [("%s|%s" % (sid, text),
                  mjp.jev_call(vault, settings, {"text": t, "project": by_sid[sid]["project"]}, slugs,
                               read_text(by_sid[sid]["project"])))
                 for sid, text, t, slugs in jobs]

        def done(jid: str, row: Dict[str, Any]) -> None:
            sid, text = jid.split("|", 1)
            merge_score(scores, sid, text, asked[jid], row)
            mrc._write(out / SCORES_NAME, scores)
        state = mjp.run_requests(calls, done, args.workers, args.pause)
        print("Jev: %d ok, %d failed%s" % (state["done"], state["failed"], ", stopped" if state["stopped"] else ""),
              file=sys.stderr)

        units = rater_units(entries, scores, cap, read_text)
        todo = mrr.batches(units, labels, size=RATER_BATCH)
        todo = todo[:args.limit] if args.limit else todo
        if todo:
            provider = llm.resolve(cfg)
            timeout = int(cfg["extraction"]["subprocessTimeout"])
            usd = 0.0
            with tempfile.TemporaryDirectory(prefix="mnemo-issue-query-") as scratch:
                with mrl._chdir(scratch):
                    for n, batch in enumerate(todo, 1):
                        resp = provider(mrr.rater_prompt(batch), system=mrr.RATER_SYSTEM, model=args.rater,
                                        timeout=timeout)
                        usd += float(resp.total_cost_usd or 0.0)
                        for uid, row in mrr.parse_labels(resp.text, batch).items():
                            labels.setdefault(uid, {}).update(row)
                        mrc._write(out / LABELS_NAME, all_labels)
                        print("rater call %d/%d" % (n, len(todo)), file=sys.stderr)
            print("rater: %d call(s), API-price equivalent $%.2f" % (len(todo), usd), file=sys.stderr)

    rows, pending = session_rows(entries, scores, labels, cap)
    data = {k: v for k, v in frozen.items() if k != "sessions"}
    data.update(sessions=len(entries), complete=len(rows), pending=pending, rater=mrr.column(args.rater),
                readings=readings(rows) if rows else {})
    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    mrc._write(out / REPORT_NAME, _provenance.stamp(data, prov))
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1, default=str))
    else:
        print(_provenance.line(prov))
        print("\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
