"""Which briefing should a session be handed, if any? Five selectors against two blind raters (#534).

Usage:
    PYTHONPATH=src python3 tools/measure_briefing_pick.py                  # build the set, pending, cost; report
    PYTHONPATH=src python3 tools/measure_briefing_pick.py --send           # label with the two raters (Claude)
    PYTHONPATH=src python3 tools/measure_briefing_pick.py --lock           # tune on dev, lock
    PYTHONPATH=src python3 tools/measure_briefing_pick.py --score-test     # score the test half, once
    PYTHONPATH=src python3 tools/measure_briefing_pick.py --json           # the report, from the cache

At SessionStart the hook pastes one briefing, the one its sort puts first.
#529 had two blind raters read 80 of them against what their session went on
to do: both called 14 about that work. This asks the question the other way
round — *of the briefings this session could have been handed, which one, if
any, is about its work?* — and scores selectors against the answer.

**Population.** Human sessions (#517's definition, job scratch left out) that
started in the last :data:`DAYS` days, on ``startup`` or ``clear`` (a resume
or a compact is never handed a briefing), with a substantive typed message
and at least one candidate.

**Candidates.** The briefings of the session's project whose file existed when
it started (mtime earlier than its first event): the :data:`POOL_SIZE` newest
by mtime, the one the hook's sort put first then (:func:`hook_order`), and any
older one a deterministic signal matches (:func:`signals`). Each briefing's
writer — its cwd at start, its branch at end — is read from the writer's own
transcript, or from ``cwd:``/``branch:`` in its frontmatter where SessionEnd
wrote them.

**Truth.** The two raters of #529 (``claude-opus-5-5``, ``claude-fable-5-1``),
blind: the same view of the session (the developer's typed messages, the
agent's last message) and each candidate's body without mnemo's envelope,
head :data:`CANDIDATE_CHARS`, in a seeded shuffle under letters, with no
date. Each lists every note about the session's work and the one that would
help most (:data:`SYSTEM`). A candidate is *right* when both list it. #529's
labels are not merged in — they judged one briefing in a different view — but
are compared: for the sessions both sets share, did each rater list the
briefing #529 showed it?

**Selectors.** ``newest`` is today's hook (its sort, which within one date
orders by session id); ``latest`` the newest by mtime; ``signals`` layer 1
alone; ``lexical`` layer 2 alone, BM25 of the first typed prompt over the ten
newest (reflex's scorer, as ``briefing_select``); ``combined`` layer 1, else
layer 2, else none. Jev is a selector only when #529's report says it
qualified for ``briefing`` (:func:`jev_qualified`); on 2026-09-28 it had not
been sent.

**Protocol.** Sessions split by a hash of their id (:func:`is_dev`). ``--lock``
reads the dev half only: it picks the signals and the lexical thresholds that
maximise the combined policy's coverage at a dev precision of at least
:data:`PRECISION_BAR`, and writes ``lock.json``. ``--score-test`` then scores
the test half with the lock, once, into ``test.json``; neither file is ever
rewritten. **The bar, set by the maintainer before measuring, on test, for
``combined``:** precision >= 80% (an injected briefing is right) and coverage
>= 60% (of sessions with a right candidate, the right one is injected).

**State on 2026-09-28, the maintainer's machine: the bar fails, and both
layers fail it.** 257 human sessions since 2026-08-29, 227 units (median 10
candidates), every one labelled by both raters: kappa 0.91 on "some candidate
is about this session", and each rater lists the briefing #529 showed it
exactly when it told #529 that briefing was about the work in 97% / 95% of
the 65 shared sessions. Today's hook is right in 40 of 227 (17.6%, #529's
17.5% again). **A right candidate exists in 110 of 227 (48%)**, and on dev it
is always among the ten newest — so there is something to choose, but:

- *layer 1 (signals)* reaches no precision worth locking. On dev the same task
  branch is right 1 time in 10 (in a private repo a feature branch stayed
  checked out through about a dozen sessions of unrelated work), the same cwd
  within 4 h 10 in 42, the same issue and the same worktree never occur in
  human sessions. The lock turns it off; alone at its dev optimum it scores
  13.5% precision on test;
- *layer 2 (first-prompt lexical)* is precise only where it is rare. Its best
  dev gate (floor 8, ratio 1.25) was right 10 of 11 on dev and **7 of 12 on
  test (58.3%, coverage 14.0%)**. Coverage can never reach the bar: ungated,
  its top pick is right in 28 of the 60 dev sessions with a right candidate
  (47%), the ceiling for any gate over this scorer;
- *first-prompt Jev* did not run: #529's report shows no Jev answer yet.

The raters cost $35.17 (Opus 5.5) and $84.86 (Fable 5.1) notional over 454
calls. The hook's own sort is also not newest-first: it orders one date by
session id, so the briefing it pasted was the latest only in 29 of the 65
sessions where #529's copy could be matched — but true newest is right no
more often (18.1%), so fixing the sort would not change what the agent gets.

Only ``--send`` leaves the machine: the two raters are called through
:func:`mnemo.core.llm.resolve` from a scratch cwd, paced, with every answer
cached under ``--out`` (default ``<vault>/.mnemo/briefing-pick``) as it
arrives, so a rerun resumes.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import random
import re
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
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


mja = _sibling("measure_jev_agreement")
mpr = mja.mpr
mrc = mja.mrc
mcc = mpr.mcc

RATERS = mja.RATERS
OUT_DIR = "briefing-pick"
SEED = 534
DAYS = 30
POOL_SIZE = 10
CANDIDATE_CHARS = 4000
PRECISION_BAR = 0.80
COVERAGE_BAR = 0.60
WORKERS = 4
PAUSE_SECONDS = 1.0

#: The layer-1 signals, in the order a match is trusted, and the gap grid for
#: the one that needs a threshold.
SIGNALS = ("issue", "branch", "worktree", "cwd_gap")
GAP_GRID = (5, 15, 30, 60, 120, 240)
#: The lexical grid: BM25 floor, top/second ratio, query terms the top shares.
FLOOR_GRID = (0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0)
RATIO_GRID = (1.0, 1.1, 1.25, 1.5, 2.0, 3.0)
OVERLAP_GRID = (1, 2, 3, 4, 6)

DEV, TEST = "dev", "test"
SELECTOR_ORDER = ("newest", "latest", "signals", "lexical", "combined")

SYSTEM = """\
You are shown what an AI coding session went on to do: the developer's typed
messages in order, and the agent's last message. You are also shown several
notes, each left by an earlier session in the same project, in no particular
order, each under a letter.

Decide which notes, if any, are ABOUT the work this session goes on to do: the
same task, issue, branch, component or line of work, so that an agent that read
that note first would be better placed for what follows. Sharing only the
project, or general background about it, is not enough. Often no note is.

Reply with JSON only:
{"about": ["<letter>", ...], "best": "<letter>" or null, "why": "<one sentence>"}
"about" lists every note about this session's work, empty when none is; "best"
is the one of those that would help most, null when "about" is empty.
"""

#: Long-lived branches every session of a repo shares: naming one says nothing about the task.
_TRUNK = frozenset({"", "HEAD", "master", "main", "develop", "development", "dev", "trunk", "staging",
                    "stage", "release", "production", "prod", "qa"})
_ISSUE = re.compile(r"(?:^|[/_-])(?:issue|gh|fix|bug)[-_/]?(\d{1,6})(?:\b|[-_/])", re.I)


# --- small helpers -------------------------------------------------------------------------

def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def is_dev(sid: str) -> bool:
    return int(hashlib.sha256(("%d:%s" % (SEED, sid)).encode("utf-8")).hexdigest()[:8], 16) % 2 == 0


def part_of(sid: str) -> str:
    return DEV if is_dev(sid) else TEST


def issue_of(branch: str) -> str:
    """The issue number a branch names (``fix/issue-534``), or ''."""
    m = _ISSUE.search(branch or "")
    return m.group(1) if m else ""


def is_task_branch(branch: str) -> bool:
    return (branch or "").strip() not in _TRUNK


def is_linked_worktree(cwd: str) -> bool:
    """A linked worktree has a ``.git`` *file*; one removed since is known by
    the paths mnemo and Claude Code give them."""
    if not cwd:
        return False
    try:
        dot_git = Path(cwd) / ".git"
        if dot_git.exists():
            return dot_git.is_file()
    except OSError:
        pass
    return "/.claude/worktrees/" in cwd or bool(re.search(r"-wt-[^/]+$", cwd))


# --- what the transcripts say --------------------------------------------------------------

def session_facts(events: Sequence[dict]) -> Dict[str, Any]:
    """cwd at start, branch at start and at end, SessionStart source."""
    branches = [e["gitBranch"] for e in events if isinstance(e, dict) and isinstance(e.get("gitBranch"), str)
                and e["gitBranch"]]
    source = ""
    for e in events:
        att = e.get("attachment") if isinstance(e, dict) else None
        hook = str((att or {}).get("hookName") or "") if isinstance(att, dict) else ""
        if hook.startswith("SessionStart:"):
            source = hook.split(":", 1)[1]
            break
    return {"cwd": mcc.session_cwd(list(events)), "branch": branches[0] if branches else "",
            "branch_end": branches[-1] if branches else "", "source": source}


def first_prompt(prompts: Sequence[Dict[str, Any]]) -> str:
    """The first message the developer typed, as UserPromptSubmit gets it."""
    for p in prompts:
        text = (p.get("text") or "").strip()
        if text and not mja._NOT_TYPED.search(text):
            return text
    return ""


# --- candidates and signals ----------------------------------------------------------------

def hook_order(b: Dict[str, Any]) -> Tuple:
    """``briefing_select.recent_briefings``' key as it shipped: date, then session id."""
    return (1, b["date"], b["id"], 0.0) if b.get("date") else (0, "", "", b["mtime"])


def signals(session: Dict[str, Any], cand: Dict[str, Any]) -> Dict[str, Any]:
    """Which deterministic signals tie ``cand`` to ``session``, from what the
    SessionStart hook can know: its cwd, its branch, the clock."""
    out: Dict[str, Any] = {}
    sb, cb = session.get("branch") or "", cand.get("branch") or ""
    si, ci = issue_of(sb), issue_of(cb)
    if si and si == ci:
        out["issue"] = True
    if is_task_branch(sb) and sb == cb:
        out["branch"] = True
    same_cwd = bool(session.get("cwd")) and session.get("cwd") == cand.get("cwd")
    if same_cwd and session.get("worktree"):
        out["worktree"] = True
    if same_cwd:
        out["cwd_gap"] = max(0.0, (session["start"] - cand["mtime"]) / 60.0)
    return out


def candidates_for(session: Dict[str, Any], briefings: Sequence[Dict[str, Any]],
                   pool: int = POOL_SIZE, max_gap: float = max(GAP_GRID)) -> List[Dict[str, Any]]:
    """The briefings ``session`` could have been handed, newest by mtime first,
    each with its signals and whether the hook's sort put it first.

    Past the ``pool`` newest, only the newest match of each signal is kept: a
    signal picks its newest match, so an older one is never anyone's pick.
    """
    before = [b for b in briefings if b["mtime"] < session["start"] and b["id"] != session["id"]]
    if not before:
        return []
    by_mtime = sorted(before, key=lambda b: -b["mtime"])
    hook_first = max(before, key=hook_order)
    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for rank, b in enumerate(by_mtime):
        sig = signals(session, b)
        kinds = {k for k in ("issue", "branch", "worktree") if sig.get(k)}
        if sig.get("cwd_gap", 1e9) <= max_gap:
            kinds.add("cwd_gap")
        fresh = kinds - seen
        seen |= kinds
        if rank < pool or b is hook_first or fresh:
            out.append({"id": b["id"], "rank": rank, "signals": sig, "hook_first": b is hook_first,
                        "body": b["body"]})
    return out


# --- the rater's view ----------------------------------------------------------------------

_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def letters_for(unit: Dict[str, Any]) -> List[str]:
    """Candidate ids in the order the raters see them: a seeded shuffle, so
    neither recency nor the hook's pick sits in a fixed place."""
    ids = [c["id"] for c in unit["candidates"]]
    random.Random("%d:%s" % (SEED, unit["id"])).shuffle(ids)
    return ids[:len(_LETTERS)]


def rater_prompt(unit: Dict[str, Any]) -> str:
    bodies = {c["id"]: c["body"] for c in unit["candidates"]}
    parts = ["## What the session went on to do", mja.session_work(unit), "", "## Notes"]
    for letter, cid in zip(_LETTERS, letters_for(unit)):
        parts += ["", "### Note %s" % letter, mpr._head(bodies[cid], CANDIDATE_CHARS)]
    return "\n".join(parts)


def parse_answer(text: str, order: Sequence[str]) -> Optional[Dict[str, Any]]:
    """``{"about": [ids], "best": id|None}`` from a rater's reply, or None."""
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("about"), list):
        return None
    by_letter = dict(zip(_LETTERS, order))
    about = []
    for x in payload["about"]:
        cid = by_letter.get(str(x).strip().upper())
        if cid is None:
            return None
        if cid not in about:
            about.append(cid)
    best = payload.get("best")
    best_id = by_letter.get(str(best).strip().upper()) if best not in (None, "", "null") else None
    if best_id is not None and best_id not in about:
        about.append(best_id)
    return {"about": about, "best": best_id}


def right_set(unit_id: str, labels: Dict[str, Dict[str, Any]], raters: Sequence[str] = RATERS
              ) -> Optional[Set[str]]:
    """The candidates both raters listed, or None when one has not answered."""
    said = [labels.get(r, {}).get(unit_id) for r in raters]
    if any(s is None for s in said):
        return None
    sets = [set(s["about"]) for s in said]
    return set.intersection(*sets)


# --- selectors -----------------------------------------------------------------------------

def _record(c: Dict[str, Any]) -> Any:
    from mnemo.core.briefing import BriefingRecord

    return BriefingRecord(path=Path(c["id"] + ".md"), frontmatter={"session_id": c["id"]}, body=c["body"])


def lexical_scores(query: str, cands: Sequence[Dict[str, Any]], pool: int = POOL_SIZE
                   ) -> Tuple[List[Tuple[str, float, int]], int]:
    """``([(id, bm25, query terms shared)], query length)``, best first, over
    the ``pool`` newest candidates — what layer 2 ranks at the first prompt."""
    from mnemo.core import briefing_select
    from mnemo.core.reflex import bm25

    recent = sorted(cands, key=lambda c: c["rank"])[:pool]
    q = briefing_select.query_tokens(query)
    if not q or not recent:
        return [], len(q)
    index, terms = briefing_select._index([_record(c) for c in recent])
    scores = bm25.score_docs(index, query_tokens=q, candidate_slugs=list(index["docs"]),
                             weights=briefing_select._WEIGHTS, params=bm25.DEFAULT_PARAMS)
    qs = set(q)
    return [(recent[int(k)]["id"], v, len(qs & terms[k])) for k, v in scores], len(q)


def pick_newest(unit: Dict[str, Any]) -> Optional[str]:
    return next((c["id"] for c in unit["candidates"] if c["hook_first"]), None)


def pick_latest(unit: Dict[str, Any]) -> Optional[str]:
    return min(unit["candidates"], key=lambda c: c["rank"])["id"] if unit["candidates"] else None


def pick_signals(unit: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """(id, signal) of the newest candidate the strongest enabled signal
    matches, or None. ``cfg``: ``{"signals": [...], "gap": minutes}``."""
    enabled = cfg.get("signals") or []
    by_rank = sorted(unit["candidates"], key=lambda c: c["rank"])
    for name in SIGNALS:
        if name not in enabled:
            continue
        for c in by_rank:
            s = c["signals"]
            if name == "cwd_gap":
                if s.get("cwd_gap", 1e9) <= float(cfg.get("gap") or 0):
                    return c["id"], name
            elif s.get(name):
                return c["id"], name
    return None


def pick_lexical(unit: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Tuple[str, float]]:
    """(id, score) when the top BM25 score clears ``floor``, beats the second
    by ``ratio`` and shares ``overlap`` query terms (capped at the query's
    length); else None."""
    scores = unit.get("lexical") or []
    if not scores:
        return None
    top_id, top, shared = scores[0]
    second = scores[1][1] if len(scores) > 1 else 0.0
    need = min(int(cfg.get("overlap", 1)), max(1, int(unit.get("query_len") or 1)))
    if top < float(cfg.get("floor", 0.0)) or shared < need:
        return None
    if second > 0 and top / second < float(cfg.get("ratio", 1.0)):
        return None
    return top_id, top


def pick_combined(unit: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """Layer 1, else layer 2, else nothing: ``(id, layer)`` or None."""
    s = pick_signals(unit, cfg.get("signal") or {})
    if s is not None:
        return s[0], "signal:" + s[1]
    lx = pick_lexical(unit, cfg.get("lexical") or {})
    if lx is not None:
        return lx[0], "lexical"
    return None


# --- scoring -------------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> Optional[List[float]]:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


def score(units: Sequence[Dict[str, Any]], truth: Dict[str, Set[str]],
          pick: Callable[[Dict[str, Any]], Optional[str]]) -> Dict[str, Any]:
    """Precision (an injected pick is right) and coverage (of sessions with a
    right candidate, the pick is one), over the units ``truth`` covers."""
    n = injected = right = with_right = covered = 0
    for u in units:
        t = truth.get(u["id"])
        if t is None:
            continue
        n += 1
        p = pick(u)
        if t:
            with_right += 1
        if p is None:
            continue
        injected += 1
        if p in t:
            right += 1
            covered += 1
    return {"sessions": n, "injected": injected, "right": right, "with_right": with_right,
            "precision": right / injected if injected else None, "precision_ci": wilson(right, injected),
            "coverage": covered / with_right if with_right else None, "coverage_ci": wilson(covered, with_right),
            "wrong_injected": injected - right}


def _first(x: Any) -> Optional[str]:
    return x[0] if x else None


def selectors(lock: Dict[str, Any]) -> Dict[str, Callable[[Dict[str, Any]], Optional[str]]]:
    out: Dict[str, Callable[[Dict[str, Any]], Optional[str]]] = {
        "newest": pick_newest, "latest": pick_latest}
    if lock:
        out["signals"] = lambda u: _first(pick_signals(u, lock["signal"]))
        out["lexical"] = lambda u: _first(pick_lexical(u, lock["lexical"]))
        out["combined"] = lambda u: _first(pick_combined(u, lock))
    return out


def configs() -> Iterable[Dict[str, Any]]:
    """Every combined policy the dev half may choose among."""
    subsets: List[List[str]] = []
    base = ["issue", "branch", "worktree"]
    for mask in range(1 << len(base)):
        subsets.append([s for i, s in enumerate(base) if mask >> i & 1])
    signal_cfgs = [{"signals": s, "gap": 0} for s in subsets]
    signal_cfgs += [{"signals": s + ["cwd_gap"], "gap": g} for s in subsets for g in GAP_GRID]
    lexical_cfgs = [None] + [{"floor": f, "ratio": r, "overlap": o}
                             for f in FLOOR_GRID for r in RATIO_GRID for o in OVERLAP_GRID]
    for sc in signal_cfgs:
        for lc in lexical_cfgs:
            yield {"signal": sc, "lexical": lc or {"floor": 1e9}}


def tune(units: Sequence[Dict[str, Any]], truth: Dict[str, Set[str]]) -> Dict[str, Any]:
    """The policy with the most dev coverage at dev precision >= the bar;
    ties go to the one that injects less. None qualifying: the most precise."""
    best: Optional[Tuple[Tuple, Dict[str, Any], Dict[str, Any]]] = None
    fallback: Optional[Tuple[Tuple, Dict[str, Any], Dict[str, Any]]] = None
    for cfg in configs():
        s = score(units, truth, lambda u, cfg=cfg: _first(pick_combined(u, cfg)))
        prec, cov = s["precision"], s["coverage"] or 0.0
        if prec is not None and prec >= PRECISION_BAR:
            key = (cov, -s["injected"])
            if best is None or key > best[0]:
                best = (key, cfg, s)
        key2 = (prec or 0.0, cov)
        if fallback is None or key2 > fallback[0]:
            fallback = (key2, cfg, s)
    chosen = best or fallback
    assert chosen is not None
    return {"config": chosen[1], "dev": chosen[2], "met_bar_on_dev": best is not None}


def tune_layers(units: Sequence[Dict[str, Any]], truth: Dict[str, Set[str]]) -> Dict[str, Any]:
    """Each layer on its own, at the dev optimum under the same rule: what a
    layer can do when the other is off, for the report."""
    out: Dict[str, Any] = {}
    for layer in ("signal", "lexical"):
        best = None
        for cfg in configs():
            if layer == "signal" and cfg["lexical"].get("floor") != 1e9:
                continue
            if layer == "lexical" and (cfg["signal"]["signals"] or cfg["signal"]["gap"]):
                continue
            s = score(units, truth, lambda u, cfg=cfg: _first(pick_combined(u, cfg)))
            if s["precision"] is None:
                continue
            key = (s["precision"] >= PRECISION_BAR, s["coverage"] or 0.0, s["precision"], -s["injected"])
            if best is None or key > best[0]:
                best = (key, cfg, s)
        out[layer] = {"config": best[1], "dev": best[2]} if best else None
    return out


# --- building the set ----------------------------------------------------------------------

def load_briefings(vault: Path, transcripts: Dict[str, Path]) -> Dict[str, List[Dict[str, Any]]]:
    """``project -> briefings``, each with its writer's cwd and final branch."""
    from mnemo.core.briefing import _load_jsonl_events
    from mnemo.core.extract.scanner import parse_frontmatter

    out: Dict[str, List[Dict[str, Any]]] = {}
    for md in sorted((vault / "bots").glob("*/briefings/sessions/*.md")):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
            mtime = md.stat().st_mtime
        except OSError:
            continue
        fm, body = parse_frontmatter(text)
        sid = str(fm.get("session_id") or md.stem)
        cwd, branch = str(fm.get("cwd") or ""), str(fm.get("branch") or "")
        if not cwd and sid in transcripts:
            facts = session_facts(_load_jsonl_events(transcripts[sid]))
            cwd, branch = facts["cwd"], facts["branch_end"]
        out.setdefault(md.parent.parent.parent.name, []).append({
            "id": sid, "mtime": mtime, "date": str(fm.get("date") or ""), "cwd": cwd, "branch": branch,
            "body": body.strip()})
    return out


def build_units(vault: Path, projects: Path, claude_home: Path, since: str) -> Dict[str, Any]:
    from mnemo.core.briefing import _load_jsonl_events

    transcripts = {p.stem: p for p in projects.glob("*/*.jsonl")}
    briefings = load_briefings(vault, transcripts)
    sessions = mpr.collect_sessions(projects, vault, since)
    home_claude = str(claude_home.resolve())
    counts = {"human": len(sessions), "scratch": 0, "not_startup": 0, "no_typed": 0, "no_candidate": 0}
    units = []
    for sid in sorted(sessions):
        m = sessions[sid]
        if (m.get("cwd") or "").startswith(home_claude):
            counts["scratch"] += 1
            continue
        events = _load_jsonl_events(Path(m["path"]))
        facts = session_facts(events)
        if facts["source"] not in ("", "startup", "clear"):
            counts["not_startup"] += 1
            continue
        walked = mpr.walk(events)
        prompts, short = mja.typed_prompts(walked.get("prompts") or [])
        if not prompts:
            counts["no_typed"] += 1
            continue
        session = {"id": sid, "start": m["start"], "cwd": facts["cwd"], "branch": facts["branch"],
                   "worktree": is_linked_worktree(facts["cwd"])}
        cands = candidates_for(session, briefings.get(m["project"], []))
        if not cands:
            counts["no_candidate"] += 1
            continue
        handed = mja.briefing_block(walked.get("session_start") or [])
        query = first_prompt(walked.get("prompts") or [])
        lex, qlen = lexical_scores(query, cands)
        units.append(dict(session, project=m["project"], source=facts["source"], candidates=cands,
                          prompts=[mpr._head(t, mja.BRIEFING_PROMPT_CHARS) for t in prompts[:mja.BRIEFING_PROMPTS]],
                          more_prompts=max(0, len(prompts) - mja.BRIEFING_PROMPTS), short_replies=short,
                          final=mpr._tail(mja.last_agent_text(events), mja.BRIEFING_FINAL_CHARS),
                          first_prompt=mpr._head(query, 2000), query_len=qlen, lexical=lex,
                          handed_head=handed[:200]))
    counts["units"] = len(units)
    return {"since": since, "counts": counts, "units": units}


# --- #529's labels, compared ---------------------------------------------------------------

def compare_529(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, Any]], jev_out: Path
                ) -> Dict[str, Any]:
    """For sessions both sets hold: did each rater list the briefing #529 showed
    it exactly when it told #529 that briefing was about the work?"""
    records = {r["id"]: r for r in (_read(jev_out / "briefing-items.json", {}).get("sessions") or [])}
    old_all = _read(jev_out / "briefing-labels.json", {})
    old = {r: old_all.get(mrc.column(r, mja.BRIEFING_SYSTEM), {}) for r in RATERS}
    out: Dict[str, Any] = {"shared": 0, "shown_found": 0}
    agree = {r: [0, 0] for r in RATERS}
    for u in units:
        rec = records.get(u["id"])
        if rec is None:
            continue
        out["shared"] += 1
        head = rec["briefing"][:300]
        shown = next((c["id"] for c in u["candidates"] if c["body"].strip()[:300] == head), None)
        if shown is None:
            continue
        out["shown_found"] += 1
        for r in RATERS:
            new = labels.get(r, {}).get(u["id"])
            if new is None or u["id"] not in old[r]:
                continue
            agree[r][1] += 1
            agree[r][0] += (shown in new["about"]) == bool(old[r][u["id"]])
    out["agreement"] = {r: (a / n if n else None) for r, (a, n) in agree.items()}
    out["compared"] = {r: n for r, (_, n) in agree.items()}
    return out


def jev_qualified(jev_out: Path) -> Tuple[bool, str]:
    rows = _read(jev_out / "report.json", {}).get("rows") or []
    row = next((r for r in rows if r.get("question") == "briefing"), None)
    if row is None:
        return False, "no #529 report"
    status = str(row.get("status") or "")
    return status == "qualified", status or "unknown"


# --- driver --------------------------------------------------------------------------------

def label(units: Sequence[Dict[str, Any]], out: Path, send: bool, workers: int, pause: float,
          limit: Optional[int], provider: Any = None, timeout: int = 180) -> Dict[str, Any]:
    labels_path = out / "labels.json"
    all_labels = _read(labels_path, {})
    cols = {r: all_labels.setdefault(mrc.column(r, SYSTEM), {}) for r in RATERS}
    calls = [(r, u) for u in units for r in RATERS if u["id"] not in cols[r]]
    est = sum(mpr.notional(r, [(rater_prompt(u), SYSTEM)], 120) for r, u in calls)
    print("labels: %d unit(s), %d rater call(s) pending, notional ~$%.2f" % (len(units), len(calls), est),
          file=sys.stderr)
    if send and calls:
        if provider is None:
            from mnemo.core import config, llm

            cfg = config.load_config()
            provider = llm.resolve(cfg)
            timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
        sender = mpr.Sender(provider, max(timeout, 300), out / "calls.jsonl", workers, pause)

        def on_reply(r: str, u: Dict[str, Any]) -> Callable[[str], None]:
            order = letters_for(u)

            def take(text: str) -> None:
                got = parse_answer(text, order)
                if got is not None:
                    cols[r][u["id"]] = got
                    _write(labels_path, all_labels)
            return take

        todo = calls[:limit] if limit else calls
        here = os.getcwd()
        scratch = tempfile.mkdtemp(prefix="mnemo-briefing-pick-")
        os.chdir(scratch)  # no project CLAUDE.md or auto-memory reaches a rater
        try:
            sender.run([(r, rater_prompt(u), SYSTEM, on_reply(r, u)) for r, u in todo])
        finally:
            os.chdir(here)
            shutil.rmtree(scratch, ignore_errors=True)
        print("labels: spent $%.2f notional this run" % sender.usd, file=sys.stderr)
    return {r: cols[r] for r in RATERS}


def truth_of(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, Any]]) -> Dict[str, Set[str]]:
    out = {}
    for u in units:
        t = right_set(u["id"], labels)
        if t is not None:
            out[u["id"]] = t
    return out


def kappa_any(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, Any]]) -> Optional[float]:
    """Cohen's kappa of the raters on 'some candidate is about this session'."""
    counts: Dict[Tuple[bool, bool], int] = {}
    for u in units:
        a, b = (labels.get(r, {}).get(u["id"]) for r in RATERS)
        if a is None or b is None:
            continue
        k = (bool(a["about"]), bool(b["about"]))
        counts[k] = counts.get(k, 0) + 1
    return mja.kappa_from(counts)


def by_layer(units: Sequence[Dict[str, Any]], truth: Dict[str, Set[str]], lock: Dict[str, Any]
             ) -> Dict[str, Dict[str, int]]:
    out: Dict[str, Dict[str, int]] = {}
    for u in units:
        t = truth.get(u["id"])
        if t is None:
            continue
        got = pick_combined(u, lock)
        layer = got[1] if got else "none"
        row = out.setdefault(layer, {"picks": 0, "right": 0, "had_right": 0})
        row["picks"] += 1
        row["had_right"] += bool(t)
        row["right"] += bool(got and got[0] in t)
    return out


def diagnostics(units: Sequence[Dict[str, Any]], truth: Dict[str, Set[str]]) -> Dict[str, Any]:
    """Why a layer reaches what it reaches, read on the half it is given (dev):
    where the right candidate sits, how often the ungated lexical top is right
    (the most coverage any lexical gate can reach), and each signal alone."""
    with_right = [u for u in units if truth.get(u["id"])]
    out: Dict[str, Any] = {"with_right": len(with_right), "right_is_latest": 0, "right_in_pool": 0,
                           "lexical_top1_right": 0, "lexical_top3_right": 0, "signals": {}}
    for u in with_right:
        t = truth[u["id"]]
        ranks = sorted(c["rank"] for c in u["candidates"] if c["id"] in t)
        out["right_is_latest"] += ranks[0] == 0
        out["right_in_pool"] += ranks[0] < POOL_SIZE
        lex = u.get("lexical") or []
        out["lexical_top1_right"] += bool(lex) and lex[0][0] in t
        out["lexical_top3_right"] += any(x[0] in t for x in lex[:3])
    probes = [(name, {"signals": [name]}) for name in ("issue", "branch", "worktree")]
    probes += [("cwd_gap<=%d" % g, {"signals": ["cwd_gap"], "gap": g}) for g in GAP_GRID]
    for name, cfg in probes:
        picks = right = 0
        for u in units:
            if u["id"] not in truth:
                continue
            got = pick_signals(u, cfg)
            if got is not None:
                picks += 1
                right += got[0] in truth[u["id"]]
        out["signals"][name] = {"picks": picks, "right": right}
    return out


def report(built: Dict[str, Any], labels: Dict[str, Dict[str, Any]], out: Path, jev_out: Path,
           lock: Dict[str, Any], test: Dict[str, Any]) -> Dict[str, Any]:
    units = built["units"]
    truth = truth_of(units, labels)
    dev = [u for u in units if part_of(u["id"]) == DEV]
    data: Dict[str, Any] = {
        "since": built["since"], "counts": built["counts"],
        "labelled": len(truth), "with_right": sum(1 for t in truth.values() if t),
        "kappa_any": kappa_any(units, labels),
        "dev_sessions": sum(1 for u in dev if u["id"] in truth),
        "candidates_median": sorted(len(u["candidates"]) for u in units)[len(units) // 2] if units else 0,
        "jev": dict(zip(("qualified", "status"), jev_qualified(jev_out))),
        "compare_529": compare_529(units, labels, jev_out),
        "dev": {name: score(dev, truth, f) for name, f in selectors(lock).items()},
        "all_baselines": {name: score(units, truth, f) for name, f in selectors({}).items()},
        "lock": lock, "test": test,
        "cost": mrc.spent_by_rater(out / "calls.jsonl", RATERS),
    }
    data["dev_diagnostics"] = diagnostics(dev, truth)
    if lock:
        data["dev_by_layer"] = by_layer(dev, truth, lock)
    return data


def _p(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.1f%%" % (100 * x)


def _ci(ci: Optional[List[float]]) -> str:
    return "" if not ci else " [%.0f%%, %.0f%%]" % (100 * ci[0], 100 * ci[1])


def _row(name: str, s: Dict[str, Any]) -> str:
    return ("  %-9s injects %3d of %3d  precision %6s%-12s coverage %6s%-12s (%d right of %d with a right one)"
            % (name, s["injected"], s["sessions"], _p(s["precision"]), _ci(s["precision_ci"]),
               _p(s["coverage"]), _ci(s["coverage_ci"]), s["right"], s["with_right"]))


def report_lines(data: Dict[str, Any]) -> List[str]:
    c = data["counts"]
    lines = ["%d human sessions since %s: %d in job scratch, %d resumed or compacted, %d with no typed "
             "message, %d with no briefing to hand; %d units, median %d candidates"
             % (c["human"], data["since"], c["scratch"], c["not_startup"], c["no_typed"], c["no_candidate"],
                c["units"], data["candidates_median"]),
             "labelled by both raters: %d; a right candidate exists in %d; kappa on 'any is about it' %s"
             % (data["labelled"], data["with_right"], "n/a" if data["kappa_any"] is None
                else "%.2f" % data["kappa_any"])]
    cmp_ = data["compare_529"]
    lines.append("#529 cross-check: %d shared sessions, shown briefing found in %d; agreement %s"
                 % (cmp_["shared"], cmp_["shown_found"], ", ".join(
                     "%s %s (n=%d)" % (r, _p(a), cmp_["compared"][r]) for r, a in cmp_["agreement"].items())))
    lines.append("Jev for 'briefing': %s (%s)" % ("qualified" if data["jev"]["qualified"] else "not qualified",
                                                 data["jev"]["status"]))
    lines.append("all labelled sessions, the two baselines:")
    for name, s in data["all_baselines"].items():
        lines.append(_row(name, s))
    lines.append("dev half%s:" % ("" if data["lock"] else " (no lock yet)"))
    for name, s in data["dev"].items():
        lines.append(_row(name, s))
    if data.get("dev_by_layer"):
        lines.append("  combined on dev, by the layer that chose: " + ", ".join(
            "%s %d/%d right" % (k, v["right"], v["picks"]) for k, v in sorted(data["dev_by_layer"].items())))
    d = data["dev_diagnostics"]
    if d["with_right"]:
        lines.append("  dev, of %d sessions with a right candidate: it is the latest in %d, in the %d newest in %d; "
                     "the ungated lexical top is right in %d (top 3: %d) — the most any lexical gate can cover"
                     % (d["with_right"], d["right_is_latest"], POOL_SIZE, d["right_in_pool"],
                        d["lexical_top1_right"], d["lexical_top3_right"]))
        lines.append("  dev, each signal alone: " + ", ".join(
            "%s %d/%d right" % (k, v["right"], v["picks"]) for k, v in d["signals"].items()))
    if data["lock"]:
        lines.append("lock: %s" % json.dumps(data["lock"]["config"], sort_keys=True))
    t = data["test"]
    if t:
        lines.append("TEST half, scored once at %s:" % t["scored_at"])
        for name in SELECTOR_ORDER:
            if name in t["selectors"]:
                lines.append(_row(name, t["selectors"][name]))
        for layer, s in sorted((t.get("layers_alone") or {}).items()):
            lines.append(_row(layer + "*", s))
        lines.append("  (* a layer alone at its own dev optimum, the other off)")
        lines.append("  combined by layer: " + ", ".join(
            "%s %d/%d right" % (k, v["right"], v["picks"]) for k, v in sorted(t["by_layer"].items())))
        lines.append("BAR (precision >= %.0f%%, coverage >= %.0f%%): %s" % (
            100 * PRECISION_BAR, 100 * COVERAGE_BAR, "PASS" if t["passed"] else "FAIL — " + t["verdict"]))
    for r, v in data["cost"].items():
        lines.append("rater %s: %d calls, $%.2f notional" % (r, v["calls"], v["usd"]))
    return lines


def verdict(s: Dict[str, Any]) -> Tuple[bool, str]:
    p, c = s["precision"], s["coverage"]
    fails = []
    if p is None or p < PRECISION_BAR:
        fails.append("precision %s < %.0f%%" % (_p(p), 100 * PRECISION_BAR))
    if c is None or c < COVERAGE_BAR:
        fails.append("coverage %s < %.0f%%" % (_p(c), 100 * COVERAGE_BAR))
    return not fails, "; ".join(fails)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default="")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--out", default="")
    ap.add_argument("--since", default="")
    ap.add_argument("--send", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS)
    ap.add_argument("--lock", action="store_true", help="tune on dev and lock (never rewrites a lock)")
    ap.add_argument("--score-test", action="store_true", help="score the test half once, with the lock")
    ap.add_argument("--rebuild", action="store_true", help="rebuild units.json (labels are kept)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from mnemo.core import config, paths

    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(config.load_config())
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / OUT_DIR
    jev_out = vault / ".mnemo" / mja.OUT_DIR
    units_path = out / "units.json"
    built = _read(units_path, {})
    if not built.get("units") or args.rebuild:
        since = args.since or (datetime.now(timezone.utc) - timedelta(days=DAYS)).date().isoformat()
        print("  building the units since %s…" % since, file=sys.stderr)
        built = build_units(vault, Path(args.projects), Path(args.claude_home), since)
        _write(units_path, built)
    units = built["units"]
    labels = label(units, out, args.send, args.workers, args.pause, args.limit)
    truth = truth_of(units, labels)

    lock_path, test_path = out / "lock.json", out / "test.json"
    lock = _read(lock_path, {})
    if args.lock:
        if lock:
            print("lock.json exists; delete it by hand to tune again", file=sys.stderr)
        else:
            dev = [u for u in units if part_of(u["id"]) == DEV]
            tuned = tune(dev, truth)
            lock = dict(tuned, layers=tune_layers(dev, truth), locked_at=datetime.now(timezone.utc).isoformat(),
                        dev_labelled=sum(1 for u in dev if u["id"] in truth))
            lock["signal"], lock["lexical"] = tuned["config"]["signal"], tuned["config"]["lexical"]
            _write(lock_path, lock)
    test = _read(test_path, {})
    if args.score_test:
        if not lock:
            print("no lock: run --lock first", file=sys.stderr)
            return 2
        if test:
            print("test.json exists: the test half is scored once", file=sys.stderr)
        else:
            tst = [u for u in units if part_of(u["id"]) == TEST]
            sel = {name: score(tst, truth, f) for name, f in selectors(lock).items()}
            layers = {}
            for layer in ("signal", "lexical"):
                lc = (lock.get("layers") or {}).get(layer)
                if lc:
                    layers[layer] = score(tst, truth, lambda u, cfg=lc["config"]: _first(pick_combined(u, cfg)))
            ok, why = verdict(sel["combined"])
            test = {"scored_at": datetime.now(timezone.utc).isoformat(), "selectors": sel,
                    "layers_alone": layers, "by_layer": by_layer(tst, truth, lock), "passed": ok, "verdict": why}
            _write(test_path, test)
    data = report(built, labels, out, jev_out, lock, test)
    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    _write(out / "report.json", _provenance.stamp(data, prov))
    if args.json:
        print(json.dumps(_provenance.stamp(data, prov), indent=1, sort_keys=True))
    else:
        print(_provenance.line(prov))
        print("\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
