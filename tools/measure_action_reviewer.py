"""Can a judge that reads what the agent did find the rule a correction was about? (#599)

Usage:
    PYTHONPATH=src python3 tools/measure_action_reviewer.py [--controls N] [--out DIR]
        # dry: what would be sent and its notional cost; reports whatever is cached
    PYTHONPATH=src python3 tools/measure_action_reviewer.py --send [--judge MODEL]
        [--rater MODEL ...] [--limit N] [--pause S]   # targets, judge, raters, then report
    PYTHONPATH=src python3 tools/measure_action_reviewer.py --json

Every way mnemo picks a rule guesses *before* the agent acts, from the prompt.
A ``Stop`` hook could instead review the turn the agent just finished — its
tool calls and its final message — and raise the rule before the user has to.
This measures whether that can work, offline, before anything is built.

**Positives** are #519's verified corrections (``measure_repeated_corrections``'s
cache: ``items.json``, ``labels.json``, ``verdicts.json``, ``units.json``; read,
never written), each paired with the agent turn the correction answered: the
work between the user turn before it (the prompt that opened the turn) and the
correction itself.

- **primary** — every rater called it a correction and named, among the rules
  that existed at that moment (#519's as-of cut, no hindsight), a vault rule
  that already said it. The targets are the vault rules any rater named.
- **hindsight** (a capability upper bound, labelled as such) — every verified
  correction against the rule later extracted from it: the raters are shown the
  live rules whose ``sources`` cite the correction's session
  (:data:`TARGET_SYSTEM`) and the targets are the ones every rater names, plus
  the primary targets. The target is added to the as-of pool; nothing else is.

**Controls** are agent turns with at least one edit or command
(:data:`EDIT_TOOLS`, :data:`COMMAND_TOOLS`) from the positives' sessions whose
reply is not a captured correction (nor an item #519 has not settled), a seeded
sample of at most ``--controls``.

**Two arms, one ranking, one judge.** Both arms rank with the reflex itself
(``core/reflex/decide.decide``) over the index cut to the rules that existed
then; the pool is the top N of that ranking (N in :data:`POOLS`; 3 is today's
judge pool), the gates set aside as the shipped judge sets them aside.

- **prompt** — today's reflex query: the user prompt that opened the turn. A
  prompt under the reflex's minimum token count gets no pool, as today.
- **action** — :func:`action_text`: the turn's tool calls (paths, commands,
  edit content, each cut to :data:`CALL_CHARS`) and its final message.

One judge call per turn (``--judge``, default :data:`JUDGE`, through mnemo's
LLM helper so the call carries the hook guard) scores, 0-10, whether the turn
breaks each rule in the union of every pool of that turn, shuffled with a
seeded order and blind to the arm (:data:`JUDGE_SYSTEM`). Each (arm, N) flags
the rules of its own pool scoring at least its threshold.

**Split.** Sessions go to dev or test by a seeded hash (:func:`split_of`, half
each). Each (arm, N) takes its threshold on dev (:func:`choose_threshold`:
the one maximising hindsight judged recall minus the share of control turns
flagged, ties to the higher threshold) and is read once on test.

**Ground truth for flags.** Every test flag is put to each model in
:data:`RATERS` (:data:`RATE_SYSTEM`, one call per turn with all its flagged
rules, never which arm or what the user said next). A flag is right when every
rater says the turn breaks the rule; a flag of a positive's own target counts
right without asking, since the user corrected exactly that. Cohen's kappa of
the raters over the flags they rated is printed.

**The bar** (#599, declared before measuring), per N, on the test split:

1. the action arm recovers a primary positive's rule more often than the
   prompt arm, after the judge: Newcombe's paired 95% CI of the difference
   (:func:`paired_ci`) excludes 0;
2. at least :data:`PRECISION_BAR` of the action arm's flags are right.

When the primary test set cannot decide (1), the report says so, calls the run
a pilot, and sizes the sample that would decide (:func:`needed_pairs`): the
smallest number of primary positives at which the hindsight effect, if it held,
would exclude 0, and the weeks of corrections that takes at #519's rates.

The ``Stop`` hook itself (#599 asks for it confirmed, not built): on Claude
Code 2.1.293 a ``Stop`` hook receives ``stop_hook_active`` and
``last_assistant_message`` in its input and can return
``{"decision": "block", "reason": "..."}``, which hands the reason back to the
agent and the turn continues. The loop guard is the hook's to keep: the binary
tells hook authors "For Stop/SubagentStop hooks, check stop_hook_active in the
input and return success while it's true", and caps blocks per turn
(``CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`` raises it).

First run, 2026-10-07, the maintainer's vault over #519's cache (22 verified
corrections, 2026-08-31 to 09-27), Haiku 5.5 judge: **do not build; a pilot.**
Primary positives: 1 (it fell in dev, so test has 0). Hindsight positives:
5 (both raters agree on a later rule for 4; 12 of the 22 corrections never had a
rule written from their session). 223 controls (every edit turn of those
sessions). The candidate stage is where it fails: over both splits the action
arm's top 10 holds the corrected rule for 1 of 5 hindsight positives (rank 7,
so 0 of 5 at N=3), and the prompt arm's for 0 of 5. The judge scored that one
reachable rule 2/10, and Sonnet 5.5 scored it 0/10. Dev therefore catches
nothing at any threshold, the pre-declared rule lands on 10, and test has no
flags to be precise about. At t=5, N=3, the action arm would still flag 4.9
control edit turns per 100 (the prompt arm 3.6) for 0 of 5 positives. If the
action arm's 1-in-5 candidate edge held and a judge kept every hit, 18 primary
positives on test (about 36 in all) would be needed. At #519's rate of about one
primary positive in four weeks, that is about 2.8 years of corrections. Haiku
cost $0.56 notional over 245 calls, Sonnet $0.76 over the 22 positive turns,
and the target questions $0.26 (Opus 5.5) and $0.62 (Fable 5.1).

Only ``--send`` calls a model. The dry default prints the pending calls and
their notional cost at list price (:data:`PRICES`). Answers are cached under
``--out`` (default ``<vault>/.mnemo/action-reviewer``) the moment they arrive,
so a stopped run resumes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrc = _sibling("measure_repeated_corrections")
_provenance = _sibling("_provenance")

RATERS = mrc.RATERS
#: The model the product would run per turn, then the one #599 adds if it fails.
JUDGE = "claude-haiku-5-5"
SECOND_JUDGE = "claude-sonnet-5-5"
#: USD per million tokens (in, out), list price; an unknown model is priced as Fable.
PRICES = dict(mrc.PRICES)
PRICES.update({"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0)})
CHARS_PER_TOKEN = 3
OUT_TOKENS = 300

SEED = 599
POOLS = (3, 10)
ARMS = ("prompt", "action")
THRESHOLDS = tuple(range(1, 11))
PRECISION_BAR = 0.85
CONTROLS = 300

EDIT_TOOLS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
COMMAND_TOOLS = ("Bash",)
CALL_CHARS = 300
ACTIONS_CHARS = 6000
FINAL_CHARS = 1500
PROMPT_CHARS = 1500
RULE_CHARS = 800

DEV, TEST = "dev", "test"
PRIMARY, HINDSIGHT = "primary", "hindsight"
OUT_DIR = "action-reviewer"

TARGET_SYSTEM = """\
A user corrected an AI coding agent. You see the correction (the agent's
message, the user's reply, the rule it implies) and numbered rules that were
later written into a memory vault from the same session.

Which rules state what the user's correction asks for, so that an agent
following the rule would not have needed the correction? Sharing a topic is
not enough.

Reply with JSON only: {"rules": ["R2", ...]} (an empty list when none does).
"""

JUDGE_SYSTEM = """\
You review one finished turn of an AI coding agent: the user's prompt that
opened it, the tool calls the agent made (paths, commands, edited text, cut
short) and its final message. You also see numbered rules from the user's
memory vault.

For each rule, score 0-10 how clearly THIS turn breaks it: 0 = the rule does
not bear on what the agent did, 10 = the agent plainly did what the rule says
not to do (or failed to do what it says to do) in this turn. A rule that is
merely on the same topic scores low.

Reply with JSON only: {"scores": {"R1": <0-10>, "R2": <0-10>, ...}} with every rule.
"""

RATE_SYSTEM = """\
You review one finished turn of an AI coding agent: the user's prompt that
opened it, the tool calls the agent made (cut short) and its final message,
and numbered rules from the user's memory vault.

For each rule decide: does this turn break it, so that pointing the agent to
the rule now would have been right? A rule that is only on the same topic, or
that the agent followed, is not broken.

Reply with JSON only: {"broken": {"R1": true|false, ...}} with every rule.
"""


# --- the turns ---------------------------------------------------------------------------

def _head(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


def call_summary(name: str, args: Any) -> str:
    """One tool call as a line: what it touched and, for edits, what it wrote."""
    args = args if isinstance(args, dict) else {}
    if name in COMMAND_TOOLS:
        body = str(args.get("command") or "")
    elif name in ("Edit", "MultiEdit"):
        edits = args.get("edits") if name == "MultiEdit" else [args]
        new = " ".join(str(e.get("new_string") or "") for e in edits or [] if isinstance(e, dict))
        body = "%s: %s" % (args.get("file_path") or "", new)
    elif name in ("Write", "NotebookEdit"):
        body = "%s: %s" % (args.get("file_path") or args.get("notebook_path") or "",
                           args.get("content") or args.get("new_source") or "")
    else:
        keys = ("file_path", "path", "pattern", "url", "query", "description", "prompt")
        body = " ".join(str(args[k]) for k in keys if args.get(k))
    return _head("%s %s" % (name, " ".join(body.split())), CALL_CHARS)


def turn_work(events: List[dict]) -> List[Dict[str, Any]]:
    """The agent's work, per user turn it was answered by.

    Entry ``i`` is the work the user's turn ``i`` replied to (the same turn
    numbering as ``transcript.user_turn_records`` and #519's ``turn_index``),
    opened by turn ``i - 1`` (``prompt``, empty for the first). Work after the
    last user turn has ``reply`` None.
    """
    from mnemo.core.transcript import SYNTHETIC_TURN, plain_user_text

    out: List[Dict[str, Any]] = []
    calls: List[Tuple[str, str]] = []
    texts: List[str] = []
    prompt = ""
    for ev in events:
        if not isinstance(ev, dict) or not isinstance(ev.get("message"), dict):
            continue
        content = ev["message"].get("content")
        if ev.get("type") == "assistant":
            if isinstance(content, str):
                texts.append(content)
                continue
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and str(b.get("text") or "").strip():
                    texts.append(str(b["text"]))
                elif b.get("type") == "tool_use":
                    name = str(b.get("name") or "")
                    calls.append((name, call_summary(name, b.get("input"))))
        elif ev.get("type") == "user":
            text = plain_user_text(content)
            if not text or SYNTHETIC_TURN.search(text):
                continue
            out.append({"index": len(out), "prompt": prompt, "calls": calls,
                        "final": texts[-1] if texts else "", "reply": text})
            prompt, calls, texts = text, [], []
    if calls or texts:
        out.append({"index": len(out), "prompt": prompt, "calls": calls,
                    "final": texts[-1] if texts else "", "reply": None})
    return out


def is_edit_turn(work: Dict[str, Any]) -> bool:
    return any(name in EDIT_TOOLS + COMMAND_TOOLS for name, _ in work.get("calls") or [])


def action_text(work: Dict[str, Any], limit: int = ACTIONS_CHARS) -> str:
    """What the agent did: its calls, then its final message, cut to ``limit``."""
    lines = [line for _, line in work.get("calls") or []]
    final = _head(work.get("final") or "", FINAL_CHARS)
    text = "\n".join(lines)
    room = max(0, limit - len(final) - 1)
    if len(text) > room:
        text = text[:room]
    return (text + "\n" + final).strip()


# --- the pools -----------------------------------------------------------------------------

def as_of_pool(dates: Dict[str, Dict[str, Any]], session_id: str, ts: float) -> Set[str]:
    """Rules that existed before ``ts`` as far as ``session_id`` is concerned (#519)."""
    out = set()
    for slug, facts in dates.items():
        t = mrc.first_learned(facts, session_id)
        if t is not None and t < ts:
            out.add(slug)
    return out


def ranking(index: dict, pool: Set[str], project: str, query: str,
            reflex_cfg: Dict[str, Any]) -> List[str]:
    """The reflex's own ranking of ``query`` over the project's rules in ``pool``."""
    from mnemo.core.reflex.decide import decide

    docs = index.get("docs") or {}
    cut = dict(index, docs={s: d for s, d in docs.items() if s in pool})
    decision = decide(cut, project=project, prompt=query or "", reflex_cfg=reflex_cfg)
    return [slug for slug, _ in decision.scores]


def split_of(session_id: str, seed: int = SEED) -> str:
    h = hashlib.sha256(("%d|%s" % (seed, session_id)).encode("utf-8")).hexdigest()
    return TEST if int(h, 16) % 2 == 0 else DEV


# --- positives and controls ------------------------------------------------------------------

def primary_targets(items: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, bool]],
                    verdicts: Dict[str, Dict[str, Any]], units: Dict[str, Any]) -> Dict[str, List[str]]:
    """Item id -> vault rules a rater named, for the real corrections every
    rater called already known by a vault rule (#519's broad reading, both)."""
    out: Dict[str, List[str]] = {}
    for iid in mrc.real_ids(items, labels):
        unit = units.get(iid) or {}
        notes = unit.get("notes") or {}
        per = [verdicts[r].get(iid) for r in verdicts]
        if not per or any(v is None for v in per):
            continue
        named = [[notes[n]["ref"] for n in v.get("notes") or []
                  if n in notes and notes[n].get("kind") == "rule"] for v in per]
        if all(named):
            out[iid] = sorted({s for group in named for s in group})
    return out


def sources_of(dates: Dict[str, Dict[str, Any]], session_id: str) -> List[str]:
    return sorted(s for s, f in dates.items() if mrc.cites_session(f, session_id))


def target_prompt(item: Dict[str, Any], rules: Sequence[Tuple[str, str]]) -> str:
    lines = ["## The correction", "AGENT said:", item.get("answered") or "(no text)", "",
             "USER replied:", item.get("turn") or "", "", "Rule it implies: " + (item.get("rule") or "-"),
             "", "## Rules written later from this session"]
    for n, (_slug, text) in enumerate(rules, 1):
        lines += ["### R%d" % n, _head(text, RULE_CHARS), ""]
    return "\n".join(lines)


def parse_ids(text: str, key: str, n: int) -> Optional[List[int]]:
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    got = payload.get(key)
    if not isinstance(got, list):
        return None
    out = []
    for g in got:
        s = str(g).strip().upper()
        if s.startswith("R") and s[1:].isdigit() and 1 <= int(s[1:]) <= n:
            out.append(int(s[1:]) - 1)
    return sorted(set(out))


def hindsight_targets(item: Dict[str, Any], rules: Sequence[str],
                      answers: Sequence[Optional[List[int]]], primary: Sequence[str]) -> Optional[List[str]]:
    """Rules every rater named for one correction, plus its primary targets.
    None while some rater has not answered."""
    if not rules:
        return sorted(primary)
    if any(a is None for a in answers):
        return None
    common = set.intersection(*[set(a) for a in answers]) if answers else set()
    return sorted({rules[i] for i in common} | set(primary))


def control_keys(works: Dict[str, List[Dict[str, Any]]], excluded: Dict[str, Set[int]],
                 limit: int, seed: int = SEED) -> List[Tuple[str, int]]:
    """A seeded sample of edit/command turns whose reply is not excluded."""
    eligible = [(sid, w["index"]) for sid in sorted(works) for w in works[sid]
                if is_edit_turn(w) and w["index"] not in excluded.get(sid, set())]
    rng = random.Random(seed)
    rng.shuffle(eligible)
    return sorted(eligible[:limit])


# --- the judge -------------------------------------------------------------------------------

def turn_prompt(work: Dict[str, Any], rules: Sequence[Tuple[str, str]]) -> str:
    lines = ["## The user's prompt that opened the turn", _head(work.get("prompt") or "(none)", PROMPT_CHARS),
             "", "## What the agent did"]
    lines += [line for _, line in work.get("calls") or []] or ["(no tool calls)"]
    lines += ["", "## Its final message", _head(work.get("final") or "(none)", FINAL_CHARS), "", "## Rules"]
    for n, (_slug, text) in enumerate(rules, 1):
        lines += ["### R%d" % n, _head(text, RULE_CHARS), ""]
    return "\n".join(lines)


def shuffled(slugs: Iterable[str], key: str, seed: int = SEED) -> List[str]:
    out = sorted(set(slugs))
    random.Random("%d|%s" % (seed, key)).shuffle(out)
    return out


def parse_scores(text: str, slugs: Sequence[str]) -> Optional[Dict[str, float]]:
    from mnemo.core import llm

    try:
        got = llm._parse_llm_json(text).get("scores")
    except Exception:
        return None
    if not isinstance(got, dict):
        return None
    out: Dict[str, float] = {}
    for n, slug in enumerate(slugs, 1):
        v = got.get("R%d" % n)
        try:
            out[slug] = max(0.0, min(10.0, float(v)))
        except (TypeError, ValueError):
            return None
    return out


def parse_broken(text: str, slugs: Sequence[str]) -> Optional[Dict[str, bool]]:
    from mnemo.core import llm

    try:
        got = llm._parse_llm_json(text).get("broken")
    except Exception:
        return None
    if not isinstance(got, dict):
        return None
    out = {}
    for n, slug in enumerate(slugs, 1):
        v = got.get("R%d" % n)
        if not isinstance(v, bool):
            return None
        out[slug] = v
    return out


# --- counting ----------------------------------------------------------------------------------

def known_right(row: Dict[str, Any]) -> List[str]:
    """The rules a positive's user corrected: a flag of one is right unasked."""
    return sorted(set(row.get("primary_targets") or ()) | set(row.get("targets") or ()))


def flags(pool: Sequence[str], scores: Optional[Dict[str, float]], threshold: float) -> List[str]:
    if not scores:
        return []
    return [s for s in pool if scores.get(s, 0.0) >= threshold]


#: Which ranking and which targets each reading reads: the primary reading
#: ranks over the rules that existed then and nothing else; the hindsight
#: reading adds the later-extracted target to that pool.
READ = {PRIMARY: ("rank", "primary_targets"), HINDSIGHT: ("rank_h", "targets")}


def recall(rows: Sequence[Dict[str, Any]], arm: str, n: int, threshold: Optional[float],
           reading: str = HINDSIGHT) -> List[bool]:
    """Per positive row: a target is in the arm's top ``n`` (and, with a
    ``threshold``, the judge flags it)."""
    rank_key, target_key = READ[reading]
    out = []
    for row in rows:
        pool = row[rank_key][arm][:n]
        picked = pool if threshold is None else flags(pool, row.get("scores"), threshold)
        out.append(any(t in picked for t in row[target_key] or ()))
    return out


def flagged_share(rows: Sequence[Dict[str, Any]], arm: str, n: int, threshold: float) -> float:
    if not rows:
        return 0.0
    return sum(bool(flags(r["rank"][arm][:n], r.get("scores"), threshold)) for r in rows) / len(rows)


def choose_threshold(positives: Sequence[Dict[str, Any]], controls: Sequence[Dict[str, Any]],
                     arm: str, n: int) -> float:
    """Dev: maximise judged recall minus the share of control turns flagged;
    a tie goes to the higher threshold."""
    best, best_j = THRESHOLDS[-1], None
    for t in THRESHOLDS:
        hits = recall(positives, arm, n, t)
        r = sum(hits) / len(hits) if hits else 0.0
        j = r - flagged_share(controls, arm, n, t)
        if best_j is None or j >= best_j:
            best, best_j = t, j
    return best


def curve(positives: Sequence[Dict[str, Any]], controls: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Descriptive, both splits, never the verdict: per (N, arm, threshold)
    the hindsight positives whose target is flagged and the control turns
    flagged per 100 — how far from usable each arm is, whatever dev chose."""
    out: Dict[str, Any] = {}
    for n in POOLS:
        for arm in ARMS:
            pts = []
            for t in THRESHOLDS:
                pts.append({"t": t, "recalled": sum(recall(positives, arm, n, t)), "of": len(positives),
                            "flags_per_100": 100.0 * flagged_share(controls, arm, n, t) if controls else None})
            cand = sum(recall(positives, arm, n, None))
            out["%d/%s" % (n, arm)] = {"candidate": cand, "points": pts}
    return out


def paired_counts(a: Sequence[bool], b: Sequence[bool]) -> Tuple[int, int, int, int]:
    """(both, a only, b only, neither) over paired outcomes."""
    both = sum(x and y for x, y in zip(a, b))
    a_only = sum(x and not y for x, y in zip(a, b))
    b_only = sum(y and not x for x, y in zip(a, b))
    return both, a_only, b_only, len(a) - both - a_only - b_only


def paired_ci(both: int, a_only: int, b_only: int, neither: int,
              z: float = 1.96) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Difference p(a) - p(b) of paired proportions with Newcombe's hybrid
    score interval (1998, method 10), which stays honest at small n."""
    n = both + a_only + b_only + neither
    if n == 0:
        return None, None, None
    p1, p2 = (both + a_only) / n, (both + b_only) / n
    l1, u1 = mrc.wilson(both + a_only, n, z)
    l2, u2 = mrc.wilson(both + b_only, n, z)
    denom = (both + a_only) * (b_only + neither) * (both + b_only) * (a_only + neither)
    num = both * neither - a_only * b_only
    if denom == 0:
        phi = 0.0
    else:
        num = max(num - n / 2.0, 0.0) if num > 0 else num
        phi = num / math.sqrt(denom)
    delta = math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    eps = math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    d = p1 - p2
    return d, max(-1.0, d - delta), min(1.0, d + eps)


def needed_pairs(both: int, a_only: int, b_only: int, neither: int,
                 cap: int = 5000) -> Optional[int]:
    """Smallest n at which these paired proportions would put the CI's lower
    bound above 0; None if the effect is not positive or not within ``cap``."""
    n0 = both + a_only + b_only + neither
    if n0 == 0 or a_only <= b_only:
        return None
    shares = [x / n0 for x in (both, a_only, b_only, neither)]
    for n in range(2, cap + 1):
        counts = [int(round(s * n)) for s in shares]
        counts[3] = n - sum(counts[:3])
        if counts[3] < 0:
            continue
        _, lo, _ = paired_ci(*counts)
        if lo is not None and lo > 0:
            return n
    return None


def precision(flagged: Sequence[Tuple[str, str]], right: Dict[Tuple[str, str], Optional[bool]]) -> Dict[str, Any]:
    """Share of flags that are right, over the flags with a settled label."""
    settled = [right.get(f) for f in flagged if right.get(f) is not None]
    k = sum(bool(x) for x in settled)
    lo, hi = mrc.wilson(k, len(settled))
    return {"k": k, "n": len(settled), "pending": len(flagged) - len(settled),
            "rate": (k / len(settled)) if settled else None, "ci95": [lo, hi]}


def flag_truth(key: str, slug: str, targets: Sequence[str],
               rated: Dict[str, Dict[str, Dict[str, bool]]]) -> Optional[bool]:
    """Right when it is the positive's own target, else when every rater says
    the turn breaks it; None while a rater has not answered."""
    if slug in targets:
        return True
    per = [(rated.get(r) or {}).get(key, {}).get(slug) for r in rated]
    if not per or any(p is None for p in per):
        return None
    return all(per)


def verdict(per_n: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Build when, at some N, both conditions hold; else name what failed."""
    reasons = []
    for n, row in sorted(per_n.items()):
        lo = row["diff"][1]
        prec = row["precision"]["rate"]
        c1 = lo is not None and lo > 0
        c2 = prec is not None and prec >= PRECISION_BAR
        if c1 and c2:
            return {"build": True, "n": n, "reasons": []}
        if not c1:
            reasons.append("N=%d: action - prompt on primary test = %s, CI lower %s (needs > 0; n=%d)"
                           % (n, _pp(row["diff"][0]), _pp(lo), row["n_primary"]))
        if not c2:
            reasons.append("N=%d: action flag precision %s (needs >= %d%%)"
                           % (n, _pct(prec), int(PRECISION_BAR * 100)))
    return {"build": False, "n": None, "reasons": reasons}


def _pct(x: Optional[float]) -> str:
    return "-" if x is None else "%.1f%%" % (100 * x)


def _pp(x: Optional[float]) -> str:
    return "-" if x is None else "%+.1f pp" % (100 * x)


# --- the run ---------------------------------------------------------------------------------

def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


def notional(model: str, prompts: Sequence[Tuple[str, str]]) -> float:
    p_in, p_out = PRICES.get(model, PRICES["claude-fable-5-1"])
    tokens = sum(len(p) + len(s) for p, s in prompts) / CHARS_PER_TOKEN
    tokens += mrc.CLI_OVERHEAD_TOKENS * len(prompts)
    return (tokens * p_in + OUT_TOKENS * len(prompts) * p_out) / 1e6


def evaluate(rows: Sequence[Dict[str, Any]], rated: Dict[str, Dict[str, Dict[str, bool]]]) -> Dict[str, Any]:
    """Every number of the report from built rows (``kind``, ``split``,
    ``targets``, ``primary``, ``rank``, ``scores``)."""
    def pick(kind: str, split: str, reading: Optional[str] = None) -> List[Dict[str, Any]]:
        """Judged rows; for a reading, only the positives that have its targets."""
        return [r for r in rows if r["kind"] == kind and r["split"] == split and r.get("scores") is not None
                and (reading is None or r[READ[reading][1]])]

    out: Dict[str, Any] = {"pools": {}}
    known = [r for r in rows if r["kind"] == "positive"]
    out["counts"] = {
        "positives": len(known),
        "primary": sum(bool(r["primary_targets"]) for r in known),
        "hindsight_with_target": sum(bool(r["targets"]) for r in known),
        "hindsight_pending": sum(r["targets"] is None for r in known),
        "controls": sum(r["kind"] == "control" for r in rows),
        "judged": sum(r.get("scores") is not None for r in rows),
        "test_primary": len(pick("positive", TEST, PRIMARY)),
        "test_hindsight": len(pick("positive", TEST, HINDSIGHT)),
        "test_controls": len(pick("control", TEST)),
    }
    for n in POOLS:
        block: Dict[str, Any] = {"arms": {}}
        for arm in ARMS:
            t = choose_threshold(pick("positive", DEV, HINDSIGHT), pick("control", DEV), arm, n)
            arm_row: Dict[str, Any] = {"threshold": t}
            for label in (PRIMARY, HINDSIGHT):
                subset = pick("positive", TEST, label)
                cand, judged = recall(subset, arm, n, None, label), recall(subset, arm, n, t, label)
                arm_row[label] = {"n": len(subset), "candidate": sum(cand), "judged": sum(judged),
                                  "_hits": judged}
            flagged = []
            for r in pick("positive", TEST) + pick("control", TEST):
                for slug in flags(r["rank"][arm][:n], r["scores"], t):
                    flagged.append((r["key"], slug, tuple(known_right(r))))
            truth = {(k, s): flag_truth(k, s, tg, rated) for k, s, tg in flagged}
            arm_row["precision"] = precision([(k, s) for k, s, _ in flagged], truth)
            controls = pick("control", TEST)
            wrong = sum(1 for k, s, _ in flagged if k.startswith("c:") and truth[(k, s)] is False)
            raw = sum(1 for k, s, _ in flagged if k.startswith("c:"))
            arm_row["flags_per_100"] = (100.0 * raw / len(controls)) if controls else None
            arm_row["false_per_100"] = (100.0 * wrong / len(controls)) if controls else None
            arm_row["_flags"] = [(k, s) for k, s, _ in flagged]
            block["arms"][arm] = arm_row
        for label in (PRIMARY, HINDSIGHT):
            counts = paired_counts(block["arms"]["action"][label]["_hits"],
                                   block["arms"]["prompt"][label]["_hits"])
            block[label + "_paired"] = {"counts": counts, "diff": paired_ci(*counts)}
        block["n_primary"] = block["arms"]["action"][PRIMARY]["n"]
        block["diff"] = block[PRIMARY + "_paired"]["diff"]
        block["precision"] = block["arms"]["action"]["precision"]
        block["needed_primary"] = needed_pairs(*block[HINDSIGHT + "_paired"]["counts"])
        out["pools"][n] = block
    out["curve"] = curve(pick("positive", DEV, HINDSIGHT) + pick("positive", TEST, HINDSIGHT),
                         pick("control", DEV) + pick("control", TEST))
    # kappa over every rated flag
    pairs = sorted({f for b in out["pools"].values() for a in b["arms"].values() for f in a["_flags"]})
    a, b = list(rated)[:2] if len(rated) >= 2 else (None, None)
    xs, ys = [], []
    for key, slug in pairs:
        if a is None:
            break
        x = (rated[a].get(key) or {}).get(slug)
        y = (rated[b].get(key) or {}).get(slug)
        if x is not None and y is not None:
            xs.append(x)
            ys.append(y)
    out["kappa"] = {"n": len(xs), "value": mrc.kappa(xs, ys)}
    out["verdict"] = verdict({n: out["pools"][n] for n in POOLS})
    for blk in out["pools"].values():
        for arm_row in blk["arms"].values():
            arm_row.pop("_flags", None)
            for label in (PRIMARY, HINDSIGHT):
                arm_row[label].pop("_hits", None)
    return out


def report_lines(data: Dict[str, Any], weekly: Optional[float] = None) -> List[str]:
    c = data["counts"]
    out = ["positives %d (primary %d, with a hindsight target %d), controls %d, judged turns %d"
           % (c["positives"], c["primary"], c["hindsight_with_target"], c["controls"], c["judged"]),
           "test split: primary %d, hindsight %d, controls %d"
           % (c["test_primary"], c["test_hindsight"], c["test_controls"]), ""]
    for n, blk in sorted(data["pools"].items(), key=lambda kv: int(kv[0])):
        out.append("N = %s" % n)
        out.append("  %-7s %5s  %-22s %-22s %-24s %8s %8s"
                   % ("arm", "t", "primary cand/judged", "hindsight cand/judged", "flag precision",
                      "flags/100", "false/100"))
        for arm in ARMS:
            a = blk["arms"][arm]
            pr = a["precision"]
            out.append("  %-7s %5s  %-22s %-22s %-24s %8s %8s" % (
                arm, a["threshold"],
                "%d/%d of %d" % (a[PRIMARY]["candidate"], a[PRIMARY]["judged"], a[PRIMARY]["n"]),
                "%d/%d of %d" % (a[HINDSIGHT]["candidate"], a[HINDSIGHT]["judged"], a[HINDSIGHT]["n"]),
                "%d/%d %s%s" % (pr["k"], pr["n"], _pct(pr["rate"]),
                                (" (+%d pending)" % pr["pending"]) if pr["pending"] else ""),
                "-" if a["flags_per_100"] is None else "%.1f" % a["flags_per_100"],
                "-" if a["false_per_100"] is None else "%.1f" % a["false_per_100"]))
        for label in (PRIMARY, HINDSIGHT):
            d, lo, hi = blk[label + "_paired"]["diff"]
            out.append("  action - prompt, judged, %s: %s [%s, %s]  (both/action/prompt/neither %s)"
                       % (label, _pp(d), _pp(lo), _pp(hi), "/".join(str(x) for x in blk[label + "_paired"]["counts"])))
        need = blk.get("needed_primary")
        if need:
            line = "  primary positives needed if the hindsight effect held: %d" % need
            if weekly:
                line += " (~%.0f weeks at %.2f primary positives a week)" % (need / weekly, weekly)
            out.append(line)
        out.append("")
    if data.get("curve"):
        out.append("descriptive, both splits (not the verdict): hindsight recalled/of, control turns flagged per 100")
        out.append("  %-10s %5s " % ("N/arm", "cand") + " ".join("%10s" % ("t=%d" % t) for t in THRESHOLDS))
        for name, c in data["curve"].items():
            cells = ["%d/%d %4.1f" % (p["recalled"], p["of"], p["flags_per_100"] or 0.0) for p in c["points"]]
            out.append("  %-10s %5d " % (name, c["candidate"]) + " ".join("%10s" % x for x in cells))
        out.append("")
    k = data["kappa"]
    out.append("rater agreement on flags: kappa %s over %d"
               % ("-" if k["value"] is None else "%.2f" % k["value"], k["n"]))
    v = data["verdict"]
    if v["build"]:
        out.append("VERDICT: build (N=%s)" % v["n"])
    else:
        pilot = all(blk["n_primary"] < 10 for blk in data["pools"].values())
        out.append("VERDICT: do not build%s" % (" — pilot: the primary set is too small to decide" if pilot else ""))
        out += ["  - " + r for r in v["reasons"]]
    return out


class _Run:
    """Everything one run reads, builds and caches."""

    def __init__(self, vault: Path, projects: Path, claude_home: Path, source: Path, out: Path,
                 reflex_cfg: Dict[str, Any], controls: int, raters: Sequence[str] = RATERS) -> None:
        from mnemo.core.briefing import _load_jsonl_events

        self.out = out
        self.raters = list(raters)
        # #519's own columns: its cache was answered by its raters, whoever rates here.
        self.items = _read(source / "items.json", [])
        labels_all = _read(source / "labels.json", {})
        verdicts_all = _read(source / "verdicts.json", {})
        self.labels = {r: labels_all.get(mrc.column(r, mrc.LABEL_SYSTEM), {}) for r in RATERS}
        self.verdicts = {r: verdicts_all.get(mrc.column(r, mrc.JUDGE_SYSTEM), {}) for r in RATERS}
        self.units = _read(source / "units.json", {})
        self.ctx = mrc._Context(vault, projects, claude_home)
        self.reflex_cfg = reflex_cfg
        self.by_id = {it["id"]: it for it in self.items}
        self.real = mrc.real_ids(self.items, self.labels)
        self.primary = primary_targets(self.items, self.labels, self.verdicts, self.units)

        paths = {p.stem: p for p in projects.glob("*/*.jsonl")}
        sessions = sorted({self.by_id[i]["session_id"] for i in self.real})
        self.works = {}
        for sid in sessions:
            if sid in paths:
                self.works[sid] = turn_work(_load_jsonl_events(paths[sid]))
        # A reply that is a real correction, or one #519 has not settled, is no control.
        excluded: Dict[str, Set[int]] = {}
        for it in self.items:
            if mrc.consensus(self.labels, it["id"]) is not False:
                excluded.setdefault(it["session_id"], set()).add(it["turn_index"])
        self.controls = control_keys(self.works, excluded, controls)
        self.target_rules = {iid: sources_of(self.ctx.dates, self.by_id[iid]["session_id"])
                             for iid in self.real}

    def text(self, slug: str) -> str:
        page = self.ctx.pages.get(slug)
        return "" if page is None else "%s\n%s" % (page[0], page[1])

    def target_calls(self, answers: Dict[str, Dict[str, List[int]]]) -> List[Tuple[str, str, str, str]]:
        """``(rater, item id, prompt, system)`` for every pending target question."""
        out = []
        for iid in self.real:
            rules = self.target_rules[iid]
            if not rules:
                continue
            prompt = target_prompt(self.by_id[iid], [(s, self.text(s)) for s in rules])
            for r in self.raters:
                if iid not in answers.get(r, {}):
                    out.append((r, iid, prompt, TARGET_SYSTEM))
        return out

    def rows(self, answers: Dict[str, Dict[str, List[int]]]) -> List[Dict[str, Any]]:
        """One row per positive and control with both arms' rankings."""
        out = []
        for iid in self.real:
            it = self.by_id[iid]
            work = next((w for w in self.works.get(it["session_id"], []) if w["index"] == it["turn_index"]), None)
            if work is None:
                continue
            prim = self.primary.get(iid, [])
            rules = self.target_rules[iid]
            targets = hindsight_targets(it, rules, [answers.get(r, {}).get(iid) for r in self.raters], prim)
            pool = as_of_pool(self.ctx.dates, it["session_id"], it["ts"])
            out.append(self._row("p:" + iid, "positive", it["session_id"], it["project"], work, pool,
                                 pool | set(targets or ()), targets, prim))
        for sid, index in self.controls:
            work = next(w for w in self.works[sid] if w["index"] == index)
            it = next(i for i in self.items if i["session_id"] == sid)
            ts = self._ts(sid, index, it)
            pool = as_of_pool(self.ctx.dates, sid, ts)
            out.append(self._row("c:%s:%d" % (sid, index), "control", sid, it["project"], work, pool, pool,
                                 [], []))
        return out

    def _ts(self, sid: str, index: int, item: Dict[str, Any]) -> float:
        # Controls are dated by the session's items: the latest one at or before
        # the turn, else the session's first; the as-of cut is per session-day.
        dated = sorted((i["turn_index"], i["ts"]) for i in self.items if i["session_id"] == sid)
        before = [t for ti, t in dated if ti <= index]
        return before[-1] if before else dated[0][1]

    def _row(self, key, kind, sid, project, work, pool, pool_h, targets, primary) -> Dict[str, Any]:
        def rank(p: Set[str]) -> Dict[str, List[str]]:
            index = self.ctx.index
            return {"prompt": ranking(index, p, project, work["prompt"], self.reflex_cfg)[:max(POOLS)],
                    "action": ranking(index, p, project, action_text(work), self.reflex_cfg)[:max(POOLS)]}

        same = pool_h == pool
        rank_p = rank(pool)
        return {"key": key, "kind": kind, "session_id": sid, "split": split_of(sid),
                "targets": targets, "primary_targets": list(primary), "work": work,
                "rank": rank_p, "rank_h": rank_p if same else rank(pool_h)}


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, llm, paths

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--vault", default="")
    ap.add_argument("--source", default="", help="#519's cache (default <vault>/.mnemo/repeated-corrections)")
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/%s)" % OUT_DIR)
    ap.add_argument("--controls", type=int, default=CONTROLS)
    ap.add_argument("--judge", default=JUDGE)
    ap.add_argument("--rater", action="append", default=[])
    ap.add_argument("--send", action="store_true", help="call the models (default: dry)")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--pause", type=float, default=mrc.PAUSE_SECONDS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = args.rater or list(RATERS)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    source = Path(args.source).expanduser() if args.source else vault / ".mnemo" / "repeated-corrections"
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    run = _Run(vault, Path(args.projects), Path(args.claude_home), source, out,
               cfg.get("reflex") or {}, args.controls, raters)

    targets_all = _read(out / "targets.json", {})
    scores_all = _read(out / "scores.json", {})
    rated_all = _read(out / "rated.json", {})
    targets = {r: targets_all.setdefault(mrc.column(r, TARGET_SYSTEM), {}) for r in raters}
    scores = scores_all.setdefault(mrc.column(args.judge, JUDGE_SYSTEM), {})
    rated = {r: rated_all.setdefault(mrc.column(r, RATE_SYSTEM), {}) for r in raters}
    provider = llm.resolve(cfg) if args.send else None
    timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
    log = out / "calls.jsonl"

    def send(model: str, calls: Sequence[Tuple[str, str, Callable[[str], None]]]) -> None:
        if args.send and calls:
            mrc.run_calls(provider, model, timeout, calls, args.limit, log, args.pause)

    # 1. hindsight targets
    pending_t = run.target_calls(targets)
    for r in raters:
        mine = [(p, s, iid) for rr, iid, p, s in pending_t if rr == r]
        print("targets, %s: %d call(s) ~$%.2f" % (r, len(mine), notional(r, [(p, s) for p, s, _ in mine])),
              file=sys.stderr)

        def take_target(r=r):
            def bind(iid):
                def on(text):
                    got = parse_ids(text, "rules", len(run.target_rules[iid]))
                    if got is not None:
                        targets[r][iid] = got
                        _write(out / "targets.json", targets_all)
                return on
            return bind
        send(r, [(p, s, take_target()(iid)) for p, s, iid in mine])

    # 2. the judge
    rows = run.rows(targets)
    judge_calls = []
    for row in rows:
        slugs = shuffled([s for key in ("rank", "rank_h") for arm in ARMS for s in row[key][arm][:max(POOLS)]],
                         row["key"])
        row["_slugs"] = slugs
        # A turn whose union grew (a hindsight target answered later) is asked again.
        if slugs and not set(slugs) <= set(scores.get(row["key"]) or {}):
            judge_calls.append((row, turn_prompt(row["work"], [(s, run.text(s)) for s in slugs])))
    print("judge %s: %d turn(s) ~$%.2f" % (args.judge, len(judge_calls),
                                           notional(args.judge, [(p, JUDGE_SYSTEM) for _, p in judge_calls])),
          file=sys.stderr)

    def take_scores(row):
        def on(text):
            got = parse_scores(text, row["_slugs"])
            if got is not None:
                scores[row["key"]] = got
                _write(out / "scores.json", scores_all)
        return on
    send(args.judge, [(p, JUDGE_SYSTEM, take_scores(row)) for row, p in judge_calls])
    for row in rows:
        row["scores"] = scores.get(row["key"]) if row["_slugs"] else {}

    # 3. raters on the test flags
    rated_cols = {r: rated[r] for r in raters}
    data = evaluate(rows, rated_cols)
    flagged: Dict[str, Set[str]] = {}
    for row in rows:
        if row["split"] != TEST or row.get("scores") is None:
            continue
        for n, blk in data["pools"].items():
            for arm in ARMS:
                for slug in flags(row["rank"][arm][:n], row["scores"], blk["arms"][arm]["threshold"]):
                    if slug not in known_right(row):
                        flagged.setdefault(row["key"], set()).add(slug)
    by_key = {row["key"]: row for row in rows}
    for r in raters:
        todo = []
        for key, slugs in sorted(flagged.items()):
            have = rated[r].get(key) or {}
            if all(s in have for s in slugs):
                continue
            order = shuffled(slugs, key + "|rate")
            todo.append((key, order, turn_prompt(by_key[key]["work"], [(s, run.text(s)) for s in order])))
        print("raters, %s: %d turn(s) ~$%.2f" % (r, len(todo), notional(r, [(p, RATE_SYSTEM) for _, _, p in todo])),
              file=sys.stderr)

        def take_rate(key, order, r=r):
            def on(text):
                got = parse_broken(text, order)
                if got is not None:
                    rated[r].setdefault(key, {}).update(got)
                    _write(out / "rated.json", rated_all)
            return on
        send(r, [(p, RATE_SYSTEM, take_rate(k, o)) for k, o, p in todo])

    data = evaluate(rows, rated_cols)
    weekly = primary_per_week(run.items, run.labels, run.primary)
    data["primary_per_week"] = weekly
    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(Path(args.projects))])
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1, default=str))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(data, weekly):
        print(line)
    if not args.send:
        print("\n(dry: nothing was sent; --send calls the models)")
    return 0


def primary_per_week(items: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, bool]],
                     primary: Dict[str, List[str]]) -> Optional[float]:
    """Primary positives a week over the span #519's items cover."""
    ts = [it["ts"] for it in items]
    if len(ts) < 2:
        return None
    weeks = (max(ts) - min(ts)) / (7 * 86400)
    return len(primary) / weeks if weeks > 0 else None


if __name__ == "__main__":
    sys.exit(main())
