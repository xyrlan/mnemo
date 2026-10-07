"""How many human corrections restate a rule the vault already had at that moment (#519).

Usage:
    PYTHONPATH=src python3 tools/measure_repeated_corrections.py [--since YYYY-MM-DD]
        [--ledger FILE ...] [--out DIR]                  # collect; print the cost estimate
    PYTHONPATH=src python3 tools/measure_repeated_corrections.py ... --send
        [--rater MODEL ...] [--limit N] [--pause S]      # label and judge, then report
    PYTHONPATH=src python3 tools/measure_repeated_corrections.py ... --examples 10 [--json]

**The items.** Every human session since ``--since`` (the population
``measure_corrections_capture`` counts: typed turns, not ``sdk-cli``, not a
throwaway cwd, not a dispatched child) contributes its captured corrections:
its capture rows in the vault's friction ledger and every ``--ledger`` file,
or else its briefing's ``## Corrections`` re-checked with today's
``corrections.verify``. The corrections-only pass over sessions with no
briefing is ``measure_corrections_capture.py --capture-into DIR``; pass
``--ledger DIR/.mnemo/friction-ledger.jsonl`` here to count what it found.
Each item is placed at its turn (``corrections.locate``) and carries that
turn's timestamp.

**Two blind raters.** Every question is put to each model in :data:`RATERS`
(two different models, neither the briefing's). "Both agree" is the primary
reading; each rater's own numbers and Cohen's kappa are printed beside it.

**Step 1, is it a correction at all?** ``verify`` only proves the user typed
the quote. Each rater (:data:`LABEL_SYSTEM`) is shown the user's turn and the
agent message it answered, never which path captured it, and says whether the
user told the agent to stop, change or prefer something. The precision is
reported per capture path. Only items both raters call a correction go on.

**Step 2, did the vault already know it?** For each real correction the vault
is cut to what existed at the turn's timestamp. A rule's first-learned time
is the earliest of: its first ``learned.jsonl`` row; its ``extracted_at`` /
``promoted_at`` / ``extraction_run`` (all rewritten later, so upper bounds);
and, when ``learned.jsonl`` has no row for it, the end of the earliest source
session other than the correction's own. Measured on the maintainer's vault
2026-09-27: for the 572 rules with both, the source session's date is within
3 days of the ``learned.jsonl`` row for 95%, and 11 rules (2%) were extracted
29-50 days after their source — so the source date is used only where no row
exists. The candidates are the reflex's BM25F top :data:`BROAD_CANDIDATES`
over the as-of pool of the correction's project, plus the top
:data:`STRICT_CANDIDATES` of its gate-verified rules, bodies capped at
:data:`BODY_CHARS`. Alongside them the judge sees what Claude Code itself
loaded at that time: the repo's ``CLAUDE.md`` at the last commit before the
turn plus ``~/.claude/CLAUDE.md``, and the auto-memory ``MEMORY.md`` index
lines (and the closest memory notes) whose files were born before the turn.
One blind call per rater (:data:`JUDGE_SYSTEM`) names every note that
already said it.

Readings:

- **strict** — a named rule is backed by a correction quote today's evidence
  gate verifies (``replay.rule_facts().gate_verified``);
- **broad** — any named vault rule;
- **native** — a named ``CLAUDE.md`` or memory note, or a vault rule imported
  from auto-memory (every source under ``bots/*/memory/``).

What the as-of cut cannot undo: a rule's body is today's. When the correction's
own session is among a named rule's sources, the body may have absorbed the
correction after the fact, so the report also gives the broad rate without
those. A rule deleted since the turn is invisible, so the rates are lower
bounds on that account.

Only ``--send`` calls a model, and the notional estimate is printed first
(``--budget USD`` refuses a send above it; there is no cap by default — the
maintainer's dollars are notional, the usage window is the limit). Sends are
paced (``--pause``) and every answer is cached under ``--out`` (default
``<vault>/.mnemo/repeated-corrections``) per model and prompt the moment it
arrives, so a stopped run resumes where it stopped.

First run, 2026-09-27, on the maintainer's vault, ``--since 2026-08-31``, with
#522's corrections-only ledger passed as ``--ledger``: 124 captured items
(81 briefing, 43 corrections-only). Both raters call 22 of them corrections
(17.7%, CI 12.0-25.4%; briefing 12/81, corrections-only 10/43; kappa 0.67).
Of those 22, the vault already said it for 1 (4.5%, CI 0.8-21.8%) on both the
strict and the broad reading, native memory for 3 (13.6%), and the vault
alone, not native, for 0 (CI 0-14.9%). Opus 5.5 $6.20 and Fable 5.1 $15.22
notional over 70 calls.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
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


mcc = _sibling("measure_corrections_capture")

#: The two blind raters, on different models and neither the briefing's
#: (#519; the repo's audits used these two). "Both agree" is the primary reading.
RATERS = ("claude-opus-5-5", "claude-fable-5-1")
#: API list prices, USD per million tokens (in, out), for the notional cost
#: (``measure_demoted_keeps.PRICES``). An unknown model is priced as Fable.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-fable-5-1": (10.0, 50.0),
          "claude-sonnet-5": (2.0, 10.0)}
#: What ``claude --print`` adds to every call's input (``measure_rule_lift``).
CLI_OVERHEAD_TOKENS = 1000
#: Seconds between two sends: the account's usage window is the only limit.
PAUSE_SECONDS = 3.0

#: Items per labelling call.
LABEL_BATCH = 10
#: How much of the answered agent message, and of the user's turn, is shown.
CONTEXT_CHARS = 1500
TURN_CHARS = 1500

#: The reflex's BM25F top N over the as-of pool, as #519 suggests.
BROAD_CANDIDATES = 40
#: Plus the top N gate-verified rules: there are few (49 of 2433 on
#: 2026-09-27) and a lexical top 40 would bury them.
STRICT_CANDIDATES = 15
BODY_CHARS = 1200
#: Claude Code loads the first 200 lines of ``MEMORY.md``.
MEMORY_INDEX_LINES = 200
MEMORY_NOTES = 5
CLAUDE_MD_CHARS = 12000

PATH_BRIEFING = "briefing"
PATH_CORRECTIONS_ONLY = "corrections_only"

LABEL_SYSTEM = """\
You read moments from coding sessions between a user and an AI coding agent.
For each numbered item you see the agent's message and the user's reply to it.

Decide whether the user's reply is a CORRECTION: the user tells the agent to
stop doing something, to do something differently, or states a preference the
agent should follow from now on, because of what the agent did or proposed.

NOT a correction: approving or confirming ("ok", "pode mergear", "yes, commit
and open the PR"), a new request or next task, answering the agent's question,
a question for information, urgency ("run it please"), or thanks.

Judge each item on its own. Reply with JSON only:
{"labels": [{"id": "<item id>", "correction": true|false}]}
"""

JUDGE_SYSTEM = """\
A user corrected an AI coding agent. You are shown the correction (the user's
words, the agent message they answered, and the rule the correction implies)
and numbered notes the agent had available BEFORE the user typed it:
R* are rules from a memory vault, C* are project instruction files, M* are
memory notes.

Question: did any note ALREADY tell the agent what the user is now telling it?
Answer yes only when a note states the same behaviour or preference, so that an
agent following that note would not have needed this correction. Sharing a
topic is not enough; a note that says something more general counts only if it
clearly covers this case.

Reply with JSON only:
{"known": true|false, "notes": ["R3", "C1", ...], "why": "<one sentence>"}
List every note that already says it; an empty list when "known" is false.
"""


# --- small helpers ---------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float]]:
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def epoch(raw: Any) -> Optional[float]:
    """Unix seconds from an ISO string or datetime; naive means local time."""
    if isinstance(raw, datetime):
        return raw.timestamp()
    if not isinstance(raw, str) or not raw.strip():
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def iso_week(ts: float) -> str:
    year, week, _ = datetime.fromtimestamp(ts, timezone.utc).isocalendar()
    return "%d-W%02d" % (year, week)


def column(model: str, system: str) -> str:
    """Where one model-and-prompt's answers are filed."""
    return "%s@%s" % (model, hashlib.sha256(system.encode("utf-8")).hexdigest()[:8])


def item_id(session_id: str, turn_index: int, quote: str) -> str:
    from mnemo.core import corrections

    key = "%s|%d|%s" % (session_id, turn_index, corrections.normalize(quote))
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


def _tail(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def _head(text: str, n: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "…"


# --- step 0: the items -----------------------------------------------------------------

def capture_rows(ledger_files: Iterable[Path]) -> Dict[str, List[Any]]:
    """``session_id -> [FrictionRecord]`` written by the session-end capture,
    deduplicated on ``(session, turn, quote)`` across files."""
    from mnemo.core import corrections
    from mnemo.core.friction import ledger
    from mnemo.core.log_utils import iter_rotated_rows

    seen: Set[tuple] = set()
    out: Dict[str, List[Any]] = {}
    for path in ledger_files:
        for raw in iter_rotated_rows(Path(path)):
            rec = ledger._from_row(raw)
            if rec is None or not rec.capture:
                continue
            key = (rec.session_id, rec.turn_index, corrections.normalize(rec.quote))
            if key in seen:
                continue
            seen.add(key)
            out.setdefault(rec.session_id, []).append(rec)
    return out


def session_items(
    events: List[dict],
    session_id: str,
    *,
    project: str,
    briefing_text: Optional[str],
    rows: Sequence[Any],
) -> List[Dict[str, Any]]:
    """One session's corrections, each placed at its turn with that turn's time.

    Capture rows win over the briefing, as in ``measure_corrections_capture``.
    A briefing item is kept only if today's ``verify`` keeps it and it can be
    located at a reaction turn.
    """
    from mnemo.core import corrections
    from mnemo.core.friction import capture
    from mnemo.core.transcript import user_turn_records

    records = user_turn_records(events)
    turns = [r.text for r in records]
    answered = [a for a, _ in capture.exchanges(events)]
    found: List[Tuple[str, str, str, int, str]] = []  # path, quote, rule, index, project
    if rows:
        for rec in rows:
            index = rec.turn_index
            if index is None or not (0 <= index < len(turns)):
                index = corrections.locate(rec.quote, turns)
            if index is None:
                continue
            found.append((rec.capture, rec.quote, rec.rule_text, index, rec.project or project))
    elif briefing_text is not None:
        kept, _ = corrections.verify(corrections.parse_section(briefing_text), turns)
        for item in kept:
            index = corrections.locate(item.quote, turns)
            if index is not None:
                found.append((PATH_BRIEFING, item.quote, item.rule, index, project))
    out: List[Dict[str, Any]] = []
    for path, quote, rule, index, proj in found:
        ts = epoch(records[index].timestamp)
        if ts is None:
            continue
        out.append({
            "id": item_id(session_id, index, quote),
            "session_id": session_id,
            "project": proj,
            "path": path if path in (PATH_BRIEFING, PATH_CORRECTIONS_ONLY) else PATH_BRIEFING,
            "quote": quote,
            "rule": rule,
            "turn_index": index,
            "ts": ts,
            "week": iso_week(ts),
            "turn": _head(turns[index], TURN_CHARS),
            "answered": _tail(answered[index] if index < len(answered) else "", CONTEXT_CHARS),
            "cwd": mcc.session_cwd(events),
        })
    return out


def collect(
    projects_dir: Path,
    vault: Path,
    ledger_files: Sequence[Path],
    since: str,
) -> List[Dict[str, Any]]:
    from mnemo.core.briefing import _load_jsonl_events

    parents = mcc._parents(vault)
    briefings = mcc._briefing_index(vault)
    rows = capture_rows(ledger_files)
    items: List[Dict[str, Any]] = []
    for path in sorted(Path(projects_dir).glob("*/*.jsonl")):
        sid = path.stem
        brief = briefings.get(sid)
        if brief is None and sid not in rows:
            continue
        events = _load_jsonl_events(path)
        if not mcc.is_human(events, sid, parents):
            continue
        first = mcc.first_timestamp(events)
        if first is None or (since and first.astimezone(timezone.utc).date().isoformat() < since):
            continue
        text = brief.read_text(encoding="utf-8", errors="replace") if brief else None
        project = brief.parts[-4] if brief else mcc._project_of(path, events)
        items.extend(session_items(events, sid, project=project, briefing_text=text,
                                   rows=rows.get(sid, [])))
    items.sort(key=lambda it: (it["ts"], it["id"]))
    return items


# --- step 1: is it a correction? -------------------------------------------------------

def label_prompt(batch: Sequence[Dict[str, Any]]) -> str:
    parts = []
    for it in batch:
        parts.append("### item %s\nAGENT:\n%s\n\nUSER:\n%s\n" % (
            it["id"], it["answered"] or "(no text)", it["turn"]))
    return "\n".join(parts)


def parse_labels(text: str, batch: Sequence[Dict[str, Any]]) -> Dict[str, bool]:
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return {}
    wanted = {it["id"] for it in batch}
    out: Dict[str, bool] = {}
    for row in payload.get("labels") or []:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("id") or "").strip()
        if iid in wanted and isinstance(row.get("correction"), bool):
            out[iid] = row["correction"]
    return out


def label_batches(items: Sequence[Dict[str, Any]], done: Dict[str, bool]) -> List[List[Dict[str, Any]]]:
    todo = [it for it in items if it["id"] not in done]
    return [todo[i:i + LABEL_BATCH] for i in range(0, len(todo), LABEL_BATCH)]


# --- step 2: the vault as of the turn --------------------------------------------------

_BRIEFING_SID = re.compile(r"briefings/sessions/([^/\s]+?)\.md")
_MEMORY_SOURCE = re.compile(r"(^|/)bots/[^/]+/memory/[^/]+\.md$")


def transcript_end_times(projects_dir: Path) -> Dict[str, float]:
    """``session_id -> last timestamp`` from each transcript's tail."""
    out: Dict[str, float] = {}
    for p in glob.glob(str(Path(projects_dir) / "*" / "*.jsonl")):
        end = _last_timestamp(Path(p))
        if end is not None:
            out[Path(p).stem] = max(end, out.get(Path(p).stem, end))
    return out


def _last_timestamp(path: Path, chunk: int = 65536) -> Optional[float]:
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - chunk))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    best: Optional[float] = None
    for m in re.finditer(r'"timestamp"\s*:\s*"([^"]+)"', tail):
        t = epoch(m.group(1))
        if t is not None and (best is None or t > best):
            best = t
    return best


def _briefing_dates(vault: Path) -> Dict[str, float]:
    """``session_id -> the day after its briefing's date``: a late bound for a
    source session whose transcript is gone."""
    out: Dict[str, float] = {}
    for p in glob.glob(str(vault / "bots" / "*" / "briefings" / "sessions" / "*.md")):
        try:
            head = Path(p).read_text(encoding="utf-8", errors="replace")[:800]
        except OSError:
            continue
        m = re.search(r"^date:\s*'?(\d{4}-\d{2}-\d{2})", head, re.M)
        if m:
            day = datetime.strptime(m.group(1), "%Y-%m-%d") + timedelta(days=1)
            out[Path(p).stem] = day.timestamp()
    return out


def learned_first(vault: Path) -> Dict[str, float]:
    """``slug -> earliest learned.jsonl row``, rotated generation included."""
    out: Dict[str, float] = {}
    base = vault / ".mnemo" / "learned.jsonl"
    for path in (Path(str(base) + ".1"), base):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or not row.get("slug"):
                continue
            t = epoch(row.get("ts"))
            if t is not None and (row["slug"] not in out or t < out[row["slug"]]):
                out[row["slug"]] = t
    return out


def rule_dates(
    vault: Path,
    *,
    learned: Dict[str, float],
    session_end: Dict[str, float],
) -> Dict[str, Dict[str, Any]]:
    """Per live rule: ``stamped`` (min of learned row and frontmatter dates),
    ``has_row``, ``sources`` as ``[(session_id, end)]``, and ``imported``."""
    from mnemo.core.filters import derive_rule_slug, is_consumer_visible
    from mnemo.core.reclassify_types import split_frontmatter

    out: Dict[str, Dict[str, Any]] = {}
    for page_type in ("feedback", "user", "reference", "project"):
        type_dir = vault / "shared" / page_type
        if not type_dir.is_dir():
            continue
        for md in sorted(type_dir.glob("*.md")):
            try:
                fm, _ = split_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not is_consumer_visible(md, fm, vault):
                continue
            slug = derive_rule_slug(fm, md.stem)
            stamps = [epoch(fm.get(k)) for k in ("extracted_at", "promoted_at", "extraction_run")]
            if slug in learned:
                stamps.append(learned[slug])
            real = [t for t in stamps if t is not None]
            raw = fm.get("sources") or []
            srcs = [s for s in ([raw] if isinstance(raw, str) else raw) if isinstance(s, str)]
            sources: List[Tuple[str, Optional[float]]] = []
            for s in srcs:
                m = _BRIEFING_SID.search(s)
                if m:
                    sources.append((m.group(1), session_end.get(m.group(1))))
            out[slug] = {
                "stamped": min(real) if real else None,
                "has_row": slug in learned,
                "sources": sources,
                "imported": bool(srcs) and all(_MEMORY_SOURCE.search(s) for s in srcs),
            }
    return out


def first_learned(facts: Dict[str, Any], own_session: str) -> Optional[float]:
    """When the rule entered the vault, as far as a correction in
    ``own_session`` is concerned. See the module docstring."""
    stamped = facts.get("stamped")
    if facts.get("has_row"):
        return stamped
    foreign = [end for sid, end in facts.get("sources") or []
               if sid != own_session and end is not None]
    times = [t for t in [stamped] + foreign if t is not None]
    return min(times) if times else None


def cites_session(facts: Dict[str, Any], session_id: str) -> bool:
    return any(sid == session_id for sid, _ in facts.get("sources") or [])


def as_of_candidates(
    index: dict,
    item: Dict[str, Any],
    dates: Dict[str, Dict[str, Any]],
    verified: Set[str],
) -> List[Tuple[str, float]]:
    """The judge's rules for one correction: BM25F top over the as-of pool of
    its project, then the top gate-verified ones not already in."""
    from mnemo.core.reflex import bm25
    from mnemo.core.reflex.tokenizer import tokenize_query

    tokens = tokenize_query(item["quote"] + "\n" + item["rule"])
    if not tokens:
        return []
    docs = index.get("docs") or {}
    project = item["project"]
    pool = []
    for slug, doc in docs.items():
        if not (project in (doc.get("projects") or []) or doc.get("universal")):
            continue
        facts = dates.get(slug)
        if facts is None:
            continue
        t = first_learned(facts, item["session_id"])
        if t is not None and t < item["ts"]:
            pool.append(slug)
    scored = bm25.score_docs(index, query_tokens=tokens, candidate_slugs=pool)
    top = scored[:BROAD_CANDIDATES]
    have = {s for s, _ in top}
    extra = [(s, sc) for s, sc in scored if s in verified and s not in have][:STRICT_CANDIDATES]
    return top + extra


# --- native memory: CLAUDE.md and auto-memory ------------------------------------------

def _encode(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def repo_root_for(cwd: str) -> Optional[Path]:
    """The main checkout for a session's cwd, or None.

    An existing cwd asks git (a worktree resolves to its main checkout). A
    deleted one — most worktrees are — falls back to the longest existing
    sibling whose name prefixes the cwd's, ``mnemo`` for ``mnemo-wt-519``.
    """
    if not cwd:
        return None
    p = Path(cwd)
    if p.is_dir():
        try:
            common = subprocess.run(
                ["git", "-C", str(p), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            common = ""
        if common:
            return Path(common).parent
        return p
    parent = p.parent
    if not parent.is_dir():
        return None
    best: Optional[Path] = None
    for sib in parent.iterdir():
        if sib.is_dir() and (p.name == sib.name or p.name.startswith(sib.name + "-")):
            if best is None or len(sib.name) > len(best.name):
                best = sib
    return best


def memory_dir_for(cwd: str, root: Optional[Path], claude_home: Path) -> Optional[Path]:
    """Claude Code's auto-memory directory for a session, or None."""
    base = claude_home / "projects"
    for cand in ([str(root)] if root else []) + ([cwd] if cwd else []):
        d = base / _encode(cand) / "memory"
        if d.is_dir():
            return d
    return None


def _born(path: Path) -> Optional[float]:
    try:
        st = path.stat()
    except OSError:
        return None
    return float(getattr(st, "st_birthtime", st.st_mtime))


def claude_md_as_of(root: Optional[Path], ts: float, claude_home: Path) -> List[Tuple[str, str]]:
    """``[(label, text)]`` of the CLAUDE.md files Claude Code loaded at ``ts``."""
    out: List[Tuple[str, str]] = []
    if root is not None:
        for rel in ("CLAUDE.md", ".claude/CLAUDE.md"):
            text = _git_file_as_of(root, rel, ts)
            if text is None:
                f = root / rel
                if f.is_file() and (_born(f) or ts) < ts and not _tracked(root, rel):
                    text = f.read_text(encoding="utf-8", errors="replace")
            if text:
                out.append(("%s (project)" % rel, text))
    home_md = claude_home / "CLAUDE.md"
    if home_md.is_file() and (_born(home_md) or ts) < ts:
        out.append(("~/.claude/CLAUDE.md", home_md.read_text(encoding="utf-8", errors="replace")))
    return out


def _tracked(root: Path, rel: str) -> bool:
    try:
        r = subprocess.run(["git", "-C", str(root), "log", "-1", "--format=%H", "--", rel],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(r.stdout.strip())


def _git_file_as_of(root: Path, rel: str, ts: float) -> Optional[str]:
    when = datetime.fromtimestamp(ts, timezone.utc).isoformat()
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "log", "-1", "--all", "--format=%H", "--before=" + when, "--", rel],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if not sha:
            return None
        show = subprocess.run(["git", "-C", str(root), "show", "%s:%s" % (sha, rel)],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return show.stdout if show.returncode == 0 else None


_INDEX_LINK = re.compile(r"\]\(([^)\s]+\.md)\)")


def memory_as_of(mem_dir: Optional[Path], ts: float, query: str) -> Tuple[str, List[Tuple[str, str]]]:
    """``(MEMORY.md index as of ts, [(name, body)] closest notes born before ts)``.

    An index line is kept when every file it links was born before ``ts`` (a
    line linking nothing is kept once some linked line is). The notes are
    ranked by shared query terms.
    """
    from mnemo.core.reflex.tokenizer import tokenize_query

    if mem_dir is None:
        return "", []
    index_lines: List[str] = []
    idx = mem_dir / "MEMORY.md"
    # MEMORY.md itself is rewritten in place (its birth time is its last
    # rewrite), so each line is dated by the notes it links; with no line
    # dated before ts the index did not exist yet.
    if idx.is_file():
        dated = False
        for line in idx.read_text(encoding="utf-8", errors="replace").splitlines():
            links = _INDEX_LINK.findall(line)
            if all((_born(mem_dir / l) or float("inf")) < ts for l in links):
                index_lines.append(line)
                dated = dated or bool(links)
        if not dated:
            index_lines = []
    q = set(tokenize_query(query))
    notes: List[Tuple[int, str, str]] = []
    for f in sorted(mem_dir.glob("*.md")):
        if f.name == "MEMORY.md" or (_born(f) or float("inf")) >= ts:
            continue
        body = f.read_text(encoding="utf-8", errors="replace")
        overlap = len(q & set(tokenize_query(body)))
        if overlap:
            notes.append((overlap, f.stem, body))
    notes.sort(key=lambda n: (-n[0], n[1]))
    return ("\n".join(index_lines[:MEMORY_INDEX_LINES]),
            [(name, _head(body, BODY_CHARS)) for _, name, body in notes[:MEMORY_NOTES]])


# --- the judge prompt ------------------------------------------------------------------

def judge_unit(
    item: Dict[str, Any],
    rules: Sequence[Tuple[str, str, str]],
    claude_md: Sequence[Tuple[str, str]],
    memory_index: str,
    memory_notes: Sequence[Tuple[str, str]],
) -> Dict[str, Any]:
    """Everything the judge is shown for one correction, with note ids mapped."""
    notes: Dict[str, Dict[str, str]] = {}
    for n, (slug, name, body) in enumerate(rules, 1):
        notes["R%d" % n] = {"kind": "rule", "ref": slug, "title": name, "text": _head(body, BODY_CHARS)}
    for n, (label, text) in enumerate(claude_md, 1):
        notes["C%d" % n] = {"kind": "claude_md", "ref": label, "title": label,
                            "text": _head(text, CLAUDE_MD_CHARS)}
    m = 0
    if memory_index.strip():
        m += 1
        notes["M%d" % m] = {"kind": "memory", "ref": "MEMORY.md", "title": "memory index",
                            "text": memory_index}
    for name, body in memory_notes:
        m += 1
        notes["M%d" % m] = {"kind": "memory", "ref": name, "title": name, "text": body}
    return {"id": item["id"], "notes": notes}


def judge_prompt(item: Dict[str, Any], unit: Dict[str, Any]) -> str:
    lines = ["## The correction", "AGENT said:", item["answered"] or "(no text)", "",
             "USER replied:", item["turn"], "", "Rule it implies: " + (item["rule"] or "-"), "",
             "## Notes that existed before it"]
    for nid, note in unit["notes"].items():
        lines += ["### %s — %s" % (nid, note["title"]), note["text"], ""]
    if not unit["notes"]:
        lines.append("(none)")
    return "\n".join(lines)


def parse_verdict(text: str, unit: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from mnemo.core import llm

    try:
        payload = llm._parse_llm_json(text)
    except Exception:
        return None
    known = payload.get("known")
    if not isinstance(known, bool):
        return None
    named = [str(n).strip() for n in payload.get("notes") or [] if str(n).strip() in unit["notes"]]
    if not known:
        named = []
    return {"known": bool(named) if known else False, "notes": named,
            "why": str(payload.get("why") or "")[:300]}


def readings(
    verdict: Dict[str, Any],
    unit: Dict[str, Any],
    item: Dict[str, Any],
    dates: Dict[str, Dict[str, Any]],
    verified: Set[str],
) -> Dict[str, bool]:
    """strict / broad / native / broad_clean for one judged correction."""
    rules = [unit["notes"][n]["ref"] for n in verdict["notes"] if unit["notes"][n]["kind"] == "rule"]
    other = [n for n in verdict["notes"] if unit["notes"][n]["kind"] != "rule"]
    clean = [s for s in rules if not cites_session(dates.get(s, {}), item["session_id"])]
    return {
        "strict": any(s in verified for s in rules),
        "broad": bool(rules),
        "broad_clean": bool(clean),
        "native": bool(other) or any(dates.get(s, {}).get("imported") for s in rules),
        "vault_only": bool(rules) and not other
        and not any(dates.get(s, {}).get("imported") for s in rules),
    }


# --- report ----------------------------------------------------------------------------

READINGS = ("strict", "broad", "broad_clean", "native", "vault_only")
CONSENSUS = "both"


def _rate(k: int, n: int) -> Dict[str, Any]:
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci95": [lo, hi]}


def kappa(a: Sequence[bool], b: Sequence[bool]) -> Optional[float]:
    """Cohen's kappa of two raters' yes/no answers on the same items."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    observed = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    chance = pa * pb + (1 - pa) * (1 - pb)
    return None if chance == 1 else (observed - chance) / (1 - chance)


def consensus(answers: Dict[str, Dict[str, Any]], iid: str) -> Optional[bool]:
    """True when every rater said yes, False when all answered and one said
    no, None while some rater has not answered."""
    got = [col.get(iid) for col in answers.values()]
    if not got or any(g is None for g in got):
        return None
    return all(bool(g) for g in got)


def real_ids(items: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, bool]]) -> List[str]:
    """Items every rater called a correction — the ones the judge sees."""
    return [it["id"] for it in items if consensus(labels, it["id"])]


def report(
    items: Sequence[Dict[str, Any]],
    labels: Dict[str, Dict[str, bool]],
    judged: Dict[str, Dict[str, Dict[str, bool]]],
) -> Dict[str, Any]:
    """``labels``: rater -> id -> correction?; ``judged``: rater -> id -> readings.

    Every figure is given for :data:`CONSENSUS` (all raters say yes — the
    primary reading) and for each rater alone. Precision is over items every
    rater labelled; the "already known" rates are over the real corrections
    (consensus) every rater judged.
    """
    raters = list(labels)
    columns = [CONSENSUS] + raters if len(raters) > 1 else raters
    both = [it for it in items if all(it["id"] in labels[r] for r in raters)]

    def said(col: str, iid: str) -> bool:
        return bool(consensus(labels, iid)) if col == CONSENSUS else bool(labels[col][iid])

    precision: Dict[str, Dict[str, Any]] = {}
    for col in columns:
        precision[col] = {}
        for path in (PATH_BRIEFING, PATH_CORRECTIONS_ONLY, "all"):
            mine = [it for it in both if path in ("all", it["path"])]
            precision[col][path] = _rate(sum(said(col, it["id"]) for it in mine), len(mine))

    real = [it for it in items if consensus(labels, it["id"])]
    done = [it for it in real if all(it["id"] in judged.get(r, {}) for r in raters)]

    def reading(col: str, iid: str, key: str) -> bool:
        if col == CONSENSUS:
            return all(judged[r][iid][key] for r in raters)
        return judged[col][iid][key]

    rates = {col: {k: _rate(sum(reading(col, it["id"], k) for it in done), len(done))
                   for k in READINGS} for col in columns}
    weeks: Dict[str, Dict[str, int]] = {}
    for it in done:
        w = weeks.setdefault(it["week"], dict({"n": 0}, **{k: 0 for k in READINGS}))
        w["n"] += 1
        for k in READINGS:
            w[k] += int(reading(columns[0], it["id"], k))
    agreement: Dict[str, Optional[float]] = {}
    if len(raters) == 2:
        r1, r2 = raters
        agreement["label_kappa"] = kappa([labels[r1][it["id"]] for it in both],
                                         [labels[r2][it["id"]] for it in both])
        for k in ("strict", "broad"):
            agreement[k + "_kappa"] = kappa([judged[r1][it["id"]][k] for it in done],
                                            [judged[r2][it["id"]][k] for it in done])
    return {"raters": raters, "items": len(items),
            "paths": {p: sum(1 for it in items if it["path"] == p)
                      for p in (PATH_BRIEFING, PATH_CORRECTIONS_ONLY)},
            "labelled": len(both), "real": len(real), "judged": len(done),
            "precision": precision, "rates": rates, "agreement": agreement,
            "weeks": [dict(week=k, **weeks[k]) for k in sorted(weeks)]}


def _pct(x: Optional[float]) -> str:
    return "  n/a" if x is None else "%4.1f%%" % (100 * x)


def _cell(r: Dict[str, Any]) -> str:
    lo, hi = r["ci95"]
    return "%3d/%-3d %s [%s, %s]" % (r["k"], r["n"], _pct(r["rate"]), _pct(lo), _pct(hi))


_READING_NAMES = (
    ("strict", "strict: a gate-verified rule"),
    ("broad", "broad: any live rule"),
    ("broad_clean", "broad, rule not citing the session"),
    ("native", "native: CLAUDE.md / auto-memory"),
    ("vault_only", "vault only, not native"),
)


def report_lines(data: Dict[str, Any]) -> List[str]:
    cols = list(data["precision"])
    head = "  %-36s " % "" + "  ".join("%-30s" % c[:30] for c in cols)
    lines = ["%d captured items (briefing %d, corrections_only %d), %d labelled by every rater, "
             "%d real corrections (all raters agree), %d judged by every rater"
             % (data["items"], data["paths"][PATH_BRIEFING], data["paths"][PATH_CORRECTIONS_ONLY],
                data["labelled"], data["real"], data["judged"]),
             "", "is it a correction? precision, 95% CI", head]
    for path in (PATH_BRIEFING, PATH_CORRECTIONS_ONLY, "all"):
        lines.append("  %-36s " % path + "  ".join("%-30s" % _cell(data["precision"][c][path])
                                                   for c in cols))
    lines += ["", "already known when the user typed it (of the real corrections)", head]
    for key, name in _READING_NAMES:
        lines.append("  %-36s " % name + "  ".join("%-30s" % _cell(data["rates"][c][key])
                                                   for c in cols))
    ag = data["agreement"]
    if ag:
        lines += ["", "agreement (Cohen's kappa): " + ", ".join(
            "%s %s" % (k.replace("_kappa", ""), "n/a" if v is None else "%.2f" % v)
            for k, v in ag.items())]
    lines += ["", "per week, %s:" % cols[0],
              "  %-9s %4s %7s %6s %7s %7s" % ("week", "n", "strict", "broad", "native", "vault")]
    for w in data["weeks"]:
        lines.append("  %-9s %4d %7d %6d %7d %7d" % (w["week"], w["n"], w["strict"], w["broad"],
                                                     w["native"], w["vault_only"]))
    return lines


def examples(items: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, bool]],
             verdicts: Dict[str, Dict[str, Dict[str, Any]]], units: Dict[str, Dict[str, Any]],
             n: int) -> List[str]:
    """Up to ``n`` labelled items: known ones first, then real, then the rest."""
    raters = list(labels)

    def known(iid: str) -> Optional[bool]:
        return consensus({r: {i: v["known"] for i, v in verdicts.get(r, {}).items()}
                          for r in raters}, iid)

    def order(it):
        return (not known(it["id"]), not consensus(labels, it["id"]), it["ts"])

    out = []
    for it in sorted([it for it in items if consensus(labels, it["id"]) is not None], key=order)[:n]:
        named = []
        for r in raters:
            v = verdicts.get(r, {}).get(it["id"])
            for x in (v or {}).get("notes") or []:
                ref = "%s %s" % (units[it["id"]]["notes"][x]["kind"], units[it["id"]]["notes"][x]["ref"])
                if ref not in named:
                    named.append(ref)
        out.append("- [%s, %s] \"%s\" -> correction: %s; known: %s%s" % (
            it["week"], it["path"], _head(it["quote"], 160).replace("\n", " "),
            "/".join(str(labels[r].get(it["id"])) for r in raters),
            "/".join(str((verdicts.get(r, {}).get(it["id"]) or {}).get("known", "-")) for r in raters),
            " (%s)" % "; ".join(named) if named else ""))
    return out


# --- driver ----------------------------------------------------------------------------

def estimate(model: str, label_todo: Sequence[Sequence[Dict[str, Any]]],
             judge_prompts: Sequence[str]) -> Dict[str, Any]:
    """One rater's pending calls, tokens and notional USD from characters / 4,
    plus the CLI's floor."""
    price_in, price_out = PRICES.get(model, PRICES["claude-fable-5-1"])
    lab_in = sum(len(label_prompt(b)) // 4 + len(LABEL_SYSTEM) // 4 + CLI_OVERHEAD_TOKENS
                 for b in label_todo)
    lab_out = sum(len(b) * 25 for b in label_todo)
    jud_in = sum(len(p) // 4 + len(JUDGE_SYSTEM) // 4 + CLI_OVERHEAD_TOKENS for p in judge_prompts)
    jud_out = len(judge_prompts) * 120
    usd = lambda i, o: (i * price_in + o * price_out) / 1e6  # noqa: E731
    return {"label_calls": len(label_todo), "label_usd": usd(lab_in, lab_out),
            "judge_calls": len(judge_prompts), "judge_usd": usd(jud_in, jud_out),
            "usd": usd(lab_in + jud_in, lab_out + jud_out)}


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True, ensure_ascii=False), encoding="utf-8")


class _Context:
    """The as-of machinery, built once per run."""

    def __init__(self, vault: Path, projects_dir: Path, claude_home: Path) -> None:
        from mnemo.core.friction.candidates import _PageFinder
        from mnemo.core.reflex import index as reflex_index
        from mnemo.core.reflex import replay

        ends = transcript_end_times(projects_dir)
        for sid, t in _briefing_dates(vault).items():
            ends.setdefault(sid, t)
        self.dates = rule_dates(vault, learned=learned_first(vault), session_end=ends)
        self.verified = {s for s, f in replay.rule_facts(vault).items() if f.gate_verified}
        self.index = reflex_index.build_index(vault)
        self.pages = _PageFinder(vault)
        self.claude_home = claude_home
        self._roots: Dict[str, Optional[Path]] = {}

    def unit(self, item: Dict[str, Any]) -> Dict[str, Any]:
        rules = []
        for slug, _score in as_of_candidates(self.index, item, self.dates, self.verified):
            page = self.pages.get(slug)
            if page is not None:
                rules.append((slug, page[0], page[1]))
        cwd = item.get("cwd") or ""
        if cwd not in self._roots:
            self._roots[cwd] = repo_root_for(cwd)
        root = self._roots[cwd]
        md = claude_md_as_of(root, item["ts"], self.claude_home)
        index, notes = memory_as_of(memory_dir_for(cwd, root, self.claude_home), item["ts"],
                                    item["quote"] + "\n" + item["rule"])
        return judge_unit(item, rules, md, index, notes)


def run_calls(
    provider: Callable[..., Any],
    model: str,
    timeout: int,
    calls: Sequence[Tuple[str, str, Callable[[str], None]]],
    limit: Optional[int],
    log: Optional[Path] = None,
    pause: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> float:
    """Send ``(prompt, system, on_reply)`` calls from a scratch cwd; return USD.

    Each answered call's tokens and cost are appended to ``log``."""
    usd = 0.0
    todo = list(calls)[:limit] if limit else list(calls)
    with tempfile.TemporaryDirectory(prefix="mnemo-repeated-") as scratch:
        here = os.getcwd()
        os.chdir(scratch)
        try:
            for n, (prompt, system, on_reply) in enumerate(todo, 1):
                if n > 1 and pause > 0:
                    sleep(pause)
                try:
                    resp = provider(prompt, system=system, model=model, timeout=timeout)
                except Exception as exc:  # one failed call must not end the run
                    print("  call %d: %s: %s" % (n, type(exc).__name__, exc), file=sys.stderr)
                    continue
                usd += float(resp.total_cost_usd or 0.0)
                if log is not None:
                    with open(log, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"system": column(model, system), "chars": len(prompt),
                                             "in": resp.input_tokens, "out": resp.output_tokens,
                                             "usd": resp.total_cost_usd}) + "\n")
                on_reply(resp.text or "")
                print("  %s call %d/%d, $%.3f so far" % (model, n, len(todo), usd), file=sys.stderr)
        finally:
            os.chdir(here)
    return usd


def main(argv: Optional[List[str]] = None) -> int:
    from mnemo.core import config, llm, paths
    from mnemo.core.friction import ledger

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", default="", help="first session date, YYYY-MM-DD")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--claude-home", default=os.path.expanduser("~/.claude"))
    ap.add_argument("--vault", default="")
    ap.add_argument("--ledger", action="append", default=[],
                    help="another friction-ledger.jsonl with capture rows (e.g. --capture-into's)")
    ap.add_argument("--out", default="", help="cache dir (default <vault>/.mnemo/repeated-corrections)")
    ap.add_argument("--rater", action="append", default=[],
                    help="a rater model; repeat for each (default: %s)" % " and ".join(RATERS))
    ap.add_argument("--send", action="store_true", help="label and judge (model calls)")
    ap.add_argument("--limit", type=int, default=None, help="with --send: at most N calls per step and rater")
    ap.add_argument("--pause", type=float, default=PAUSE_SECONDS, help="seconds between sends")
    ap.add_argument("--budget", type=float, default=None,
                    help="refuse a --send whose notional estimate exceeds USD (default: no cap)")
    ap.add_argument("--examples", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    raters = args.rater or list(RATERS)

    cfg = config.load_config()
    vault = Path(args.vault).expanduser() if args.vault else paths.vault_root(cfg)
    out = Path(args.out).expanduser() if args.out else vault / ".mnemo" / "repeated-corrections"
    out.mkdir(parents=True, exist_ok=True)
    ledgers = [ledger.ledger_path(vault)] + [Path(p).expanduser() for p in args.ledger]

    items = collect(Path(args.projects), vault, ledgers, args.since)
    _write(out / "items.json", items)
    all_labels = _read(out / "labels.json", {})
    all_verdicts = _read(out / "verdicts.json", {})
    labels = {r: all_labels.setdefault(column(r, LABEL_SYSTEM), {}) for r in raters}
    verdicts = {r: all_verdicts.setdefault(column(r, JUDGE_SYSTEM), {}) for r in raters}

    ctx = _Context(vault, Path(args.projects), Path(args.claude_home))
    units = {it["id"]: ctx.unit(it) for it in items}
    _write(out / "units.json", units)
    by_id = {it["id"]: it for it in items}

    def pending_judge(r: str) -> List[str]:
        # Before every label is in, any item nobody has refused yet may reach
        # the judge: the estimate is an upper bound.
        return [i for i in by_id if i not in verdicts[r]
                and all(labels[x].get(i, True) for x in raters)]

    estimates = {r: estimate(r, label_batches(items, labels[r]),
                             [judge_prompt(by_id[i], units[i]) for i in pending_judge(r)])
                 for r in raters}
    total = sum(e["usd"] for e in estimates.values())
    for r, e in estimates.items():
        print("%s: pending %d label call(s) ~$%.2f, <= %d judge call(s) ~$%.2f"
              % (r, e["label_calls"], e["label_usd"], e["judge_calls"], e["judge_usd"]),
              file=sys.stderr)
    print("%d items; notional estimate <= ~$%.2f at API list price (subscription usage, not money)"
          % (len(items), total), file=sys.stderr)

    spent = 0.0
    if args.send:
        if args.budget is not None and total > args.budget:
            print("refusing: estimate $%.2f exceeds --budget $%.2f" % (total, args.budget),
                  file=sys.stderr)
            return 2
        provider = llm.resolve(cfg)
        timeout = int((cfg.get("extraction") or {}).get("subprocessTimeout") or 180)
        log = out / "calls.jsonl"

        def on_labels(r, batch):
            def take(text):
                labels[r].update(parse_labels(text, batch))
                _write(out / "labels.json", all_labels)
            return take

        for r in raters:
            spent += run_calls(provider, r, timeout,
                               [(label_prompt(b), LABEL_SYSTEM, on_labels(r, b))
                                for b in label_batches(items, labels[r])],
                               args.limit, log, args.pause)

        def on_verdict(r, iid):
            def take(text):
                v = parse_verdict(text, units[iid])
                if v is not None:
                    verdicts[r][iid] = v
                    _write(out / "verdicts.json", all_verdicts)
            return take

        real = real_ids(items, labels)
        for r in raters:
            spent += run_calls(provider, r, timeout,
                               [(judge_prompt(by_id[i], units[i]), JUDGE_SYSTEM, on_verdict(r, i))
                                for i in real if i not in verdicts[r]],
                               args.limit, log, args.pause)
        print("spent $%.2f notional this run" % spent, file=sys.stderr)

    judged = {r: {i: readings(v, units[i], by_id[i], ctx.dates, ctx.verified)
                  for i, v in verdicts[r].items() if i in by_id and i in units}
              for r in raters}
    data = report(items, labels, judged)
    data["cost"] = spent_by_rater(out / "calls.jsonl", raters)
    prov = _provenance.provenance(__file__, argv, vault=vault,
                                  blind_spots=[_provenance.transcripts_blind_spot(args.projects)])
    if args.json:
        data["examples"] = examples(items, labels, verdicts, units, args.examples)
        print(json.dumps(_provenance.stamp(data, prov), indent=1))
        return 0
    print(_provenance.line(prov))
    for line in report_lines(data):
        print(line)
    print("")
    print("notional cost of every answer on file: " + ", ".join(
        "%s $%.2f (%d calls)" % (r, c["usd"], c["calls"]) for r, c in data["cost"].items()))
    if args.examples:
        print("")
        for line in examples(items, labels, verdicts, units, args.examples):
            print(line)
    return 0


def spent_by_rater(log: Path, raters: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Notional USD and calls per rater, over every call logged in ``log``."""
    out = {r: {"usd": 0.0, "calls": 0} for r in raters}
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
        if model in out:
            out[model]["usd"] += float(row.get("usd") or 0.0)
            out[model]["calls"] += 1
    return out


if __name__ == "__main__":
    sys.exit(main())
