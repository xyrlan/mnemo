"""Day one: what an empty vault carries, with and without Claude Code history (#467, #472).

Usage:
    PYTHONPATH=src python3 tools/measure_day_one.py --dry-run     # plan, calls and cost per arm; calls nothing
    PYTHONPATH=src python3 tools/measure_day_one.py --send        # run every arm (resumes), then label
    PYTHONPATH=src python3 tools/measure_day_one.py --send --arm b
    PYTHONPATH=src python3 tools/measure_day_one.py --send --arm c   # arm (c) and (a)+(c)
    PYTHONPATH=src python3 tools/measure_day_one.py               # the report, from saved results
    PYTHONPATH=src python3 tools/measure_day_one.py --json
    PYTHONPATH=src python3 tools/measure_day_one.py --judge --dry-run   # judge on (#479): bounds, sends nothing
    PYTHONPATH=src python3 tools/measure_day_one.py --judge --send --arm-a-work ~/.cache/mnemo/day-one-471
    PYTHONPATH=src python3 tools/measure_day_one.py --judge             # its report, from judge.json

Every number that says mnemo works was measured on the maintainer's vault.
This starts two vaults from nothing and replays one repository's real
transcripts through them — :data:`DEFAULT_CORPUS`, the maintainer's clubinho
sessions, standing in for somebody else's history — with the judge off in
both, because a new user has no TypeSafe key.

**Arm (a), with history.** The first :data:`--install-after` sessions are the
history that exists at install. ``mnemo backfill --install-run`` runs over
them in-process, the real ``cmd_backfill`` with its real ``installCap``,
mutation gate and ledger; two seams are pointed elsewhere, both in the
discovery step: ``discover.projects_root`` at a directory that links only
the history transcripts, and ``backfill._current_project`` at the corpus's
own agent name (the tool runs from a scratch directory, not the repo, so
that its helpers' transcripts never land among the repo's). Then the
extraction the first SessionEnd would run (a never-extracted vault is never
debounced), then the SessionStart payload, then the next ``--prompts``
prompts through the hook's own decision (``reflex.replay.run``: gates,
per-session cap and dedupe) against that vault, held fixed. The vault does
not grow during those prompts; arm (b) is where it grows.

**Arm (b), without history.** Sessions in the order they started, one at a
time. Per session: the prompts through the reflex against the vault as it
stands, then the SessionEnd path — ``run_extraction`` when
``session_end._debounce_passes`` says so, then ``generate_session_briefing``
— then the indexes rebuilt and the SessionStart payload the next session
would get. Two things keep that the hook's path and not a tidier one:

- **Extraction before briefing.** SessionEnd spawns both detached, the
  extraction first; it scans the vault at once, while the briefing is still
  a model call away. So a session's own briefing is consolidated at the next
  extraction, not this one.
- **The transcript's clock.** The debounce reads ``last_run`` and briefing
  mtimes; a replay taking minutes would otherwise extract on every session.
  After each extraction ``last_run`` is set to the session's end, and each
  briefing's mtime to a minute after it (:data:`BRIEFING_LAG`), so ``minIntervalMinutes`` and
  ``minNewMemories`` see the time the sessions actually had.

**Arm (c), Claude Code's auto-memory (#472).** A real SessionEnd mirrors
every ``~/.claude/projects/<project>/memory`` directory into the vault, so a
user who ran Claude Code with auto-memory on starts with those files. Arm (c)
takes the corpus's own directory *as it stood at the install point* — the
start of the first session after ``--install-after``, the first one the
mirror could reach — and runs the real ``mirror.mirror_all`` over it, with
one seam: ``_claude_projects_root`` points at a directory holding only that
snapshot, so no other project on the machine is copied. Then the first
extraction, the SessionStart payload and arm (a)'s prompts, as in arm (a).
**Arms (a)+(c)** is what a real install with the backfill gets: arm (a)'s
harvested memory files copied into a fresh vault (the same pages, not a
second harvest), then the same mirror and the same steps.

The snapshot comes from history, not from today's directory, which has
grown since. :func:`snapshot_at` reads every transcript under
``~/.claude/projects`` (a worktree's session writes the same directory): a
file not modified since the install point is today's; otherwise the first
Write/Edit after it decides — a create means the file did not exist then, an
``originalFile`` is the text it had. A file changed afterwards only by a
shell command (``sed -i``, ``cat >``) has no recorded prior text: taken as
created after when no transcript named it before and it was born after,
otherwise kept as today's text and reported as "approximate". The snapshot
is written once under ``--work`` and reused, because the live directory
keeps changing.

**What "carries" means.** The SessionStart payload is built by the hook's own
block builders (topics, ``[last-briefing]``, the staged-backfill notice, the
learned block, the review offer) over the arm's vault. The hook's ``main``
is not run, in any arm: it mirrors every ``~/.claude/projects/*/memory``
directory on the machine into the vault and repairs ``settings.json`` hook
matchers, neither of which may touch a measurement; arm (c) calls the mirror
itself, over its snapshot only. The first-run invitation is not counted as
carrying anything: it is an offer to run the backfill.

**On-point.** #411's 0/1/2 rubric, with ``measure_reflex_reach``'s rater
instruction, blind: the rater sees the prompt and the injected rule's text
and nothing else. Only injected (prompt, rule) pairs are labelled, since the
question is what reached the prompt. Headline: the first arm-(b) session
whose prompts got an on-point rule, or "> N" for the sessions run.

**Isolation.** Each arm scaffolds its own vault under ``--work`` and runs with
``MNEMO_CONFIG_PATH`` at that vault's config; ``HOME`` is untouched, because
``claude`` authenticates from it. Every model call goes through
``core.llm.call``, which already hands helpers ``MNEMO_HOOKS_OFF``; a hook that
fired anyway would still read this process's ``MNEMO_CONFIG_PATH``, i.e. the
arm's vault. The run snapshots ``~/mnemo`` before and after and reports what
changed there, since the maintainer's own sessions keep writing to it.

**Budget.** ``--dry-run`` prints the plan, with a bound on calls and an
API-price estimate per arm. ``--send`` refuses a plan over ``--budget``
(default :data:`BUDGET`) and meters every call: arm (b) stops before a
session whose worst case, plus the labels still owed, would cross it, and
says where it stopped. Arms (c) and (a)+(c) have their own budget,
``--budget-c`` (default :data:`C_BUDGET`), refused up front when the dry
run's bound is over it. At the budget the meter raises
``LLMSubprocessError``, which backfill and extraction both treat as an
environmental stop. Cost is the CLI's API-price equivalent (#441), not money.

**First run, 2026-09-23**, clubinho, 88 sessions (08-22 to 09-23), 1,549
prompts, install after session 44, rater ``claude-sonnet-5``. The dry run
bounded it at 150 calls; it took 127 (126 metered, one briefing lost to a
crash the rerun fixed), 98 Haiku and 28 Sonnet, $10.46 API-price
equivalent.

- **Arm (a) carries nothing the reflex can use.** The install run took the
  20 newest history transcripts, 11 calls (one timed out twice; nine had no
  file mutations), and wrote 59 memory pages. The first extraction, 8
  calls, staged 56 of them and promoted none: backfill-origin pages always
  stage (``extract/inbox/paths.py``). Live pages 0, so the next 50 prompts
  and the next 35 first turns fire 0 times. The first SessionStart carries
  the staged notice, a review offer, and a ``[mnemo learned]`` block
  announcing 32 of the staged ``project`` pages as learned with a ``veto``
  line, though none is live: ``promote_projects`` reports staged backfill
  project pages in ``written_fresh`` and ``_record_learned`` records them.
- **Arm (b)**: first live page after session 2, first injection in session
  5, first on-point injection in **session 24** (09-09, 18 days in). Over
  all 88: the reflex fires on 252 of 1,549 prompts (16.3%), 328 pairs, 11
  on-point (3.4%), 41 marginal, 276 noise. Sessions 1-20: 19 fired, none
  on-point. 21-44: 8 on-point prompts of 81 fired. 45-88: 3 of 152. Live
  pages end at 31, staged 38. The five most-injected rules are generic
  (the top one, 37 times). Every SessionStart from session 2 on carries a
  ``[last-briefing]``; the topic line holds 2 topics throughout.
- **Arm (c), 2026-09-23** (``--send --arm c``, 17 calls against a dry-run
  bound of 26, $0.54). The directory had 140 files today; at the install
  point (09-14 16:18) it held 90: 84 unchanged since, 2 restored from an
  Edit's ``originalFile``, 50 created after (37 by a Write, 13 inferred), 3
  approximate plus ``MEMORY.md``, which the scanner skips. By type: 80
  project, 4 feedback, 4 reference, 1 user. The mirror filed all 89 under
  ``clubinho``, the corpus's own agent. The first extraction put **81 pages
  live**: 80 ``project`` pages go direct (``promote_projects``, no model and
  no review), plus 1 ``user`` page auto-promoted. 6 staged, all evidence
  demotions of the feedback pages. The reference chunk timed out
  (``extract.chunk:LLMTimeoutError``), so the 4 reference files wait for the
  next SessionEnd. The first SessionStart carries 2 local topics and a
  learned block. The reflex fires on **7 of the next 50 prompts** (14.0%),
  11 pairs, **2 on-point** (both ``sgp-acesso-api``; 10 of 11 pairs
  labelled). On the 35 first turns it fires on 27, 52 pairs, 1 on-point.
- **Arms (a)+(c)**: arm (a)'s 59 harvested files plus the same mirror,
  giving 148 memory files. 9 calls: 86 live (80 direct project pages, 6
  auto-promoted), 57 staged (54 of them backfill origin). Next 50 prompts:
  6 fire (12.0%), 10 pairs, 2 on-point. First turns: 27 of 35 fire, 1
  on-point. The backfill adds nothing the reflex reaches. Day one's reach
  is the auto-memory's, and most of what it injects is project status
  notes that are not on-point.
- ``~/mnemo`` changed in 52 files during the run. The 7 naming clubinho were
  the maintainer's own clubinho session ending at 13:47 and 14:12 (its log
  lines, and its briefing differing from this run's), not this run.

**Judge on (#479).** ``--judge`` re-extracts nothing: it replays the prompts
the arms above already replayed, over the vaults they left, with
``reflex.judge`` on as a user who turns it on gets it (:func:`shipped_judge`:
``jev-1.13.0``, ``injectAt`` 0.4, 2.5 s, the top 3 of the ranking). The stage
runs in the hook's order inside ``reflex.replay.run`` — the pool is the top 3
of the *ranking*, including prompts the gates silenced, minus what the
session was already told; the real ``judge.ask`` decides; a timeout or an
error falls back to the gates' decision and is counted. Only ``--send``
leaves the machine: it posts what the hook posts (the prompt's first 1,200
characters, each rule's first 800) to TypeSafe with the key
``judge.resolve_key`` finds. Arm (a) is read from ``--arm-a-work``, since
master's arm (a) (after #471) was run into a directory of its own.

Arm (b)'s vault grew during its run and only its end state is kept, so each
session gets it rewound (:func:`rewind`): the pages ``learned.jsonl`` says
went live later are removed, and a page's text is its final one. Every
prompt is also replayed judge-off over the same vault and compared with what
the run recorded; the judge-off numbers in the table are that replay's, so
on and off always share a vault. Transcripts Claude Code has since pruned are
rebuilt from the prompts the run recorded (:func:`from_record`).

``--dry-run`` bounds the requests locally (a stage that answers "none" to
everything asks on every ranked prompt; real answers can only ask on fewer)
and the label calls. ``--send`` stops arm (b) before a session whose bound
would cross ``--jev-budget`` (1,000), and labels at most ``--label-budget``
(40) calls' worth of injected pairs, judge on and off together, with the same
blind rater as the arms above. ``judge.json`` under ``--work`` resumes.

**Judge on, first run 2026-09-24** (dry run bound 950 requests; sent 940, all
answered, none timed out or errored, so nothing fell back; median 788 ms, max
1,753 ms; 4 label calls, every pair labelled). Arm (b) ran 69 of 88
sessions, 1,275 prompts; session 70 would have crossed the budget. Its
rewound vaults reproduce the recorded judge-off injections on 1,233 of 1,275
prompts (57 of 69 sessions exactly); the 42 others fall in sessions 39-64,
where a page that later gained a second source ranks with its final text.
Three sessions were rebuilt from the record (transcripts pruned).

- **Arm (b)**: judge off (replayed) fires on 186 prompts, 236 pairs, 16
  on-point, 181 noise (77%). Judge on fires on 78 (6.1%), 87 pairs, 32
  on-point, 37 marginal, **18 noise (20.7%, Wilson 13.5-30.4%)**. Prompts
  holding an on-point rule: 16 off, 29 on. The first on-point injection
  moves from session 24 to session 23.
- **Arm (a)** (master, after #471): 6 of 50 fire either way; off 10 pairs,
  all noise; on 6 pairs, 2 on-point, 2 noise.
- **Arm (c)**: off 7 of 50, 11 pairs, 2 on-point, 9 noise; on 6 of 50, 7
  pairs, 3 on-point, 2 noise.
- Against the bar declared in #479 (noise <= 20% of labelled injections):
  arm (b) misses it by one pair, with an interval that straddles it; arms (a)
  and (c) have 6 and 7 pairs, too few to call either way (2/6 and 2/7).

**Resume.** Everything lives under ``--work`` (default
:data:`DEFAULT_WORK`): each arm's vault, ``progress.json`` after every
session, ``labels.json`` after every rater call. A rerun continues; a session
interrupted halfway is replayed whole (briefings skip unchanged
transcripts, extraction skips clean files).
"""
from __future__ import annotations

import argparse
import contextlib
import importlib.util
import json
import math
import os
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

_SIBLINGS = Path(__file__).resolve().parent


def _sibling(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _SIBLINGS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrr = _sibling("measure_reflex_reach")
mrg = mrr.mrg

#: Relative to ``$HOME``, resolved when the tool runs.
DEFAULT_CORPUS = Path(".claude") / "projects" / "-Users-xyrlan-github-clubinho"
DEFAULT_WORK = Path(".cache") / "mnemo" / "day-one"
REAL_VAULT = Path("mnemo")

#: Model calls both arms may spend together (#467).
BUDGET = 200
#: Model calls arms (c) and (a)+(c) may spend together (#472), apart from BUDGET.
C_BUDGET = 60
#: The arms that mirror the auto-memory snapshot: (c) alone, and (a)+(c).
MIRROR_ARMS = ("c", "ac")
#: Arm (a)'s replay length.
PROMPTS = 50
DEFAULT_RATER = mrr.DEFAULT_RATER
ON_POINT = mrr.ON_POINT

#: Dry-run assumptions, printed beside the numbers they produce.
PAGES_PER_HARVEST = 3
TOKENS_PER_BRIEFING = 1500
TOKENS_PER_PAGE = 400
OUT_TOKENS = {"harvest": 1500, "briefing": 1200, "extract": 2000, "gate": 300, "label": 250}
#: USD per MTok (in, out) for the estimate; unknown models price as Sonnet.
PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0)}

LIVE_TYPES = ("feedback", "user", "reference", "project")

#: Seconds between a session's end and its briefing landing: the briefing is a
#: model call spawned beside the extraction, so it is written after the
#: extraction's ``last_run`` watermark, and the next debounce counts it.
BRIEFING_LAG = 60


# --- the corpus -----------------------------------------------------------------

@dataclass
class Session:
    sid: str
    path: Path
    start: float
    end: float
    mutations: int
    prompts: List[Tuple[float, str]] = field(default_factory=list)
    mtime: float = 0.0


def read_session(path: Path) -> Optional[Session]:
    """One transcript as the replay needs it, or None when it holds no event time."""
    from mnemo.core.briefing import _count_file_mutations, _load_jsonl_events
    from mnemo.core.reflex.replay import _parse_ts, read_prompts

    events = _load_jsonl_events(path)
    stamps = [t for t in (_parse_ts(e.get("timestamp")) for e in events
                          if isinstance(e, dict)) if t is not None]
    if not stamps:
        return None
    prompts = [(p.ts, p.text) for p in read_prompts(path, path.stem, lambda _c, _s: "")]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = max(stamps)
    return Session(sid=path.stem, path=path, start=min(stamps), end=max(stamps),
                   mutations=_count_file_mutations(events), prompts=prompts, mtime=mtime)


def load_sessions(corpus: Path) -> List[Session]:
    """Every transcript of the corpus directory, in the order the sessions started."""
    out = [s for s in (read_session(p) for p in sorted(corpus.glob("*.jsonl"))) if s]
    out.sort(key=lambda s: (s.start, s.sid))
    return out


def corpus_agent(corpus: Path) -> str:
    """The agent name the corpus's own recorded cwd resolves to — what the hooks file it under."""
    from mnemo.core.backfill import discover

    cwd = discover.recorded_cwd(sorted(corpus.glob("*.jsonl")))
    if not cwd:
        raise SystemExit("error: no transcript in %s records a cwd" % corpus)
    return discover.agent_for_cwd(cwd)


# --- the pure part ----------------------------------------------------------------

def install_selection(history: Sequence[Session], cap: int, min_mutations: int) -> List[Session]:
    """What ``--install-run`` harvests: the newest ``cap`` by mtime, then the mutation gate.

    Mirrors ``discover.find_transcripts`` (newest-first by mtime, cap as a
    prefix) and ``harvest_session``'s gate, for the dry run's count only; the
    run itself calls the real code.
    """
    newest = sorted(history, key=lambda s: s.mtime, reverse=True)[:max(0, cap)]
    return [s for s in newest if s.mutations >= min_mutations]


def first_prompts(future: Sequence[Session], n: int) -> List[Tuple[Session, float, str]]:
    """The next ``n`` prompts after the install point, in the order they were typed."""
    out: List[Tuple[Session, float, str]] = []
    for s in future:
        for ts, text in s.prompts:
            if len(out) >= n:
                return out
            out.append((s, ts, text))
    return out


def first_turns(future: Sequence[Session], n: int) -> List[Tuple[Session, float, str]]:
    """The first prompt of each of the next ``n`` sessions that has one."""
    return [(s, s.prompts[0][0], s.prompts[0][1]) for s in future if s.prompts][:n]


def plan_b(sessions: Sequence[Session], *, min_interval_min: int, min_new: int,
           min_mutations: int, chunk: int) -> List[Dict[str, Any]]:
    """The SessionEnd schedule arm (b) will follow, and each session's worst-case calls.

    Per session, in the hook's order: extraction if the debounce passes on
    the transcript's clock — never before a first extraction, afterwards
    ``min_interval_min`` since the last and ``min_new`` briefings written
    since — consuming the briefings written so far; then a briefing if the
    session mutated files. Worst case per extraction: one call per chunk of
    ``chunk`` briefings plus one reference-gate call per chunk.
    """
    rows: List[Dict[str, Any]] = []
    last_run: Optional[float] = None
    pending = 0
    for s in sessions:
        extract = last_run is None or (
            s.end - last_run >= min_interval_min * 60 and pending >= min_new)
        consumed = pending if extract else 0
        chunks = int(math.ceil(consumed / float(chunk))) if consumed else 0
        briefing = s.mutations >= min_mutations
        rows.append({"sid": s.sid, "extract": extract, "consumed": consumed,
                     "extract_calls": 2 * chunks, "briefing": briefing,
                     "worst": 2 * chunks + (1 if briefing else 0),
                     "prompts": len(s.prompts)})
        if extract:
            last_run, pending = s.end, 0
        if briefing:
            pending += 1
    return rows


def label_calls(pairs: int, batch: int = mrr.BATCH) -> int:
    return int(math.ceil(pairs / float(batch))) if pairs else 0


def max_pairs(prompts: int, cap: int) -> int:
    """Upper bound on injected pairs in one session: two per prompt, ``cap`` per session."""
    return min(cap, 2 * prompts)


def price(model: str, tokens_in: int, tokens_out: int) -> float:
    rates = PRICES["haiku"] if "haiku" in model else PRICES["sonnet"]
    return (tokens_in * rates[0] + tokens_out * rates[1]) / 1e6


def first_on_point(rows: Sequence[Dict[str, Any]]) -> Optional[int]:
    """1-based session number of the first row with an on-point injection."""
    for n, row in enumerate(rows, 1):
        if row.get("on_point", 0) > 0:
            return n
    return None


def rates(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """Emit and on-point counts over replayed prompts.

    ``units`` are prompts, each with the ``pool`` of rules injected into it
    (empty when the reflex stayed silent). emit = prompts with an injection;
    on-point = injected pairs the rater called 2; ``unlabelled`` pairs are
    reported, never guessed.
    """
    fired = [u for u in units if u["pool"]]
    pairs = [(u["uid"], c["slug"]) for u in fired for c in u["pool"]]
    lab = [labels.get(uid, {}).get(slug) for uid, slug in pairs]
    on = sum(1 for v in lab if v == ON_POINT)
    return {"prompts": len(units), "fired": len(fired), "pairs": len(pairs),
            "on_point": on, "labelled": sum(1 for v in lab if v is not None),
            "marginal": sum(1 for v in lab if v == mrr.MARGINAL),
            "noise": sum(1 for v in lab if v == mrr.NOISE),
            "prompts_on_point": sum(
                1 for u in fired
                if any(labels.get(u["uid"], {}).get(c["slug"]) == ON_POINT for c in u["pool"]))}


def carries(blocks: Dict[str, Any]) -> bool:
    """Whether a SessionStart payload carries anything learned (the invitation does not count)."""
    return bool(blocks.get("local_topics") or blocks.get("universal_topics")
                or blocks.get("last_briefing") or blocks.get("learned")
                or blocks.get("staged_notice") or blocks.get("offer"))


def pct(k: int, n: int) -> str:
    return "%.1f%%" % (100.0 * k / n) if n else "n/a"


def fingerprint(root: Path) -> Dict[str, Tuple[float, int]]:
    """relpath -> (mtime, size) for every file under ``root`` (empty when absent)."""
    out: Dict[str, Tuple[float, int]] = {}
    if not root.is_dir():
        return out
    for dirpath, _dirs, files in os.walk(str(root)):
        for name in files:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                continue
            out[p.relative_to(root).as_posix()] = (st.st_mtime, st.st_size)
    return out


def changed(before: Dict[str, Tuple[float, int]], after: Dict[str, Tuple[float, int]]) -> List[str]:
    return sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))


def suspects(paths: Iterable[str], markers: Sequence[str]) -> List[str]:
    """Changed real-vault paths that name the corpus or this run — a hook that leaked."""
    return [p for p in paths if any(m and m in p for m in markers)]


# --- arm (c): the auto-memory directory as it stood at install -------------------

#: A shell command that may have written a memory file it names: a redirect
#: (not ``2>/dev/null``), an in-place edit, a move, copy or delete, or a script.
_SHELL_WRITES = re.compile(r"(?<![0-9&>])>|\bsed\s+-i|\bperl\s+-i|\b(?:mv|cp|rm|tee|touch|python3?)\b")
_MD_NAME = re.compile(r"([A-Za-z0-9_][A-Za-z0-9_.\-]*\.md)\b")


@dataclass
class MemEvent:
    """One transcript record touching a memory file.

    ``create``/``edit``: a Write/Edit tool result (``originalFile`` is the
    file as it was just before, None on a create or when Claude Code left it
    out). ``shell``: a Bash command that may have written it — no prior
    content. ``mention``: anything else naming it (a Read, a ``cat``, text).
    """
    ts: float
    name: str
    kind: str
    original: Optional[str] = None


def _strings(obj: Any) -> Iterable[str]:
    """Every string value in a decoded JSON event."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def memory_markers(memory_dir: Path) -> List[str]:
    """The spellings a transcript uses for ``memory_dir``: absolute, and ``~/``-relative."""
    out = [str(memory_dir)]
    try:
        out.append("~/" + memory_dir.relative_to(Path.home()).as_posix())
    except ValueError:
        pass
    return out


def memory_events(transcripts: Iterable[Path], memory_dir: Path) -> List[MemEvent]:
    """Every record in ``transcripts`` that writes or names a file of ``memory_dir``, by time."""
    from mnemo.core.reflex.replay import _parse_ts

    markers = memory_markers(memory_dir)
    prefix = str(memory_dir) + os.sep
    names = [re.compile(re.escape(m) + r"[/\\]" + _MD_NAME.pattern) for m in markers]
    # The raw line is JSON: a Windows path sits in it with its backslashes
    # escaped, so the prefilter looks for that spelling too, and everything
    # after it reads the decoded strings.
    raw = markers + [json.dumps(m)[1:-1] for m in markers]
    out: List[MemEvent] = []
    for path in transcripts:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if not any(m in text for m in raw):
            continue
        for line in text.splitlines():
            if not any(m in line for m in raw):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            ts = _parse_ts(event.get("timestamp"))
            if ts is None:
                continue
            result = event.get("toolUseResult")
            written = None
            if isinstance(result, dict) and str(result.get("filePath") or "").startswith(prefix) and (
                    "originalFile" in result or result.get("type") in ("create", "update")):
                written = Path(result["filePath"]).name
                original = result.get("originalFile")
                out.append(MemEvent(ts, written, "create" if result.get("type") == "create" else "edit",
                                    original if isinstance(original, str) else None))
            content = (event.get("message") or {}).get("content")
            for block in content if isinstance(content, list) else []:
                if not (isinstance(block, dict) and block.get("type") == "tool_use"
                        and block.get("name") == "Bash"):
                    continue
                command = str((block.get("input") or {}).get("command") or "")
                if any(m in command for m in markers) and _SHELL_WRITES.search(command):
                    for name in sorted(set(_MD_NAME.findall(command))):
                        out.append(MemEvent(ts, name, "shell"))
            found = {n for value in _strings(event) for rx in names for n in rx.findall(value)}
            for name in sorted(found - {written}):
                out.append(MemEvent(ts, name, "mention"))
    out.sort(key=lambda e: (e.ts, e.name))
    return out


#: Snapshot statuses. A file "created after" did not exist at install and is
#: left out; "approximate" existed but was changed afterwards by a shell
#: command, which records no prior content, so today's text stands in.
PRESENT = ("unchanged", "restored", "approximate")


def snapshot_at(current: Dict[str, Tuple[str, float, Optional[float]]], events: Sequence[MemEvent],
                at: float) -> Dict[str, Tuple[Optional[str], str]]:
    """name -> (content at ``at``, or None when absent then; how it was recovered).

    ``current`` is name -> (text today, mtime, birth time or None). A file
    not modified since ``at`` is today's. Otherwise the first write after
    ``at`` decides: a Write that created it (absent then), or a Write/Edit
    whose ``originalFile`` is the text it had (restored). With neither, a
    file no transcript named before ``at`` and born after it is taken as
    created after; one known before is kept as today's text, "approximate".
    A file named by path or written before ``at`` that is gone today is
    "gone": listed, not restored.
    """
    by_name: Dict[str, List[MemEvent]] = {}
    for e in sorted(events, key=lambda e: e.ts):
        by_name.setdefault(e.name, []).append(e)
    out: Dict[str, Tuple[Optional[str], str]] = {}
    for name, (text, mtime, birth) in current.items():
        evs = by_name.get(name, [])
        if mtime <= at:
            out[name] = (text, "unchanged")
            continue
        first = next((e for e in evs if e.ts > at and e.kind != "mention"), None)
        known_before = any(e.ts <= at for e in evs) or (birth is not None and birth <= at)
        if first is not None and first.kind == "create":
            out[name] = (None, "created after")
        elif first is not None and first.kind == "edit" and first.original is not None:
            out[name] = (first.original, "restored")
        elif not known_before:
            out[name] = (None, "created after (inferred)")
        else:
            out[name] = (text, "approximate")
    for name, evs in by_name.items():
        # A shell command's ``*.md`` tokens may name files elsewhere (a
        # ``README.md`` beside the ``cd``); only a path or a tool result says
        # the file was in this directory.
        if name not in current and any(e.ts <= at and e.kind != "shell" for e in evs):
            out[name] = (None, "gone")
    return out


def read_memory_dir(memory_dir: Path) -> Dict[str, Tuple[str, float, Optional[float]]]:
    out: Dict[str, Tuple[str, float, Optional[float]]] = {}
    for p in sorted(memory_dir.glob("*.md")) if memory_dir.is_dir() else []:
        try:
            st = p.stat()
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out[p.name] = (text, st.st_mtime, getattr(st, "st_birthtime", None))
    return out


def status_counts(snapshot: Dict[str, Tuple[Optional[str], str]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for _text, status in snapshot.values():
        out[status] = out.get(status, 0) + 1
    return out


def memory_types(texts: Iterable[Tuple[str, str]]) -> Dict[str, int]:
    """Type -> count of memory files as extraction's scanner reads them (``MEMORY.md`` skipped)."""
    from mnemo.core.extract import scanner

    out: Dict[str, int] = {}
    for name, text in texts:
        if name == "MEMORY.md":
            continue
        fm, _body = scanner.parse_frontmatter(text)
        t = str(fm.get("type") or "").strip()
        t = t if t in LIVE_TYPES else "feedback"
        out[t] = out.get(t, 0) + 1
    return out


def extraction_bound(types: Dict[str, int], chunk: int) -> int:
    """Worst-case calls of one extraction over new memory files of these types.

    ``project`` files are promoted without a model; each other type costs one
    call per chunk plus one reference-gate call per chunk.
    """
    return sum(2 * int(math.ceil(n / float(chunk))) for t, n in types.items() if t != "project" and n)


# --- the vault side -----------------------------------------------------------------

class Meter:
    """Counts and bounds every model call, by wrapping ``core.llm.call``."""

    def __init__(self, budget: int, spent: int = 0, usd: float = 0.0):
        self.budget, self.calls, self.usd = budget, spent, usd
        self.by_model: Dict[str, int] = {}

    @contextlib.contextmanager
    def installed(self):
        from mnemo.core import llm

        real = llm.call

        def metered(prompt: str, **kw: Any):
            if self.calls >= self.budget:
                raise llm.LLMSubprocessError("day-one budget of %d model calls reached" % self.budget)
            self.calls += 1
            model = str(kw.get("model") or "")
            self.by_model[model] = self.by_model.get(model, 0) + 1
            resp = real(prompt, **kw)
            self.usd += float(resp.total_cost_usd or 0.0)
            return resp

        llm.call = metered
        try:
            yield self
        finally:
            llm.call = real


@contextlib.contextmanager
def arm_vault(vault: Path):
    """Scaffold ``vault`` (idempotent) and point every config read at it."""
    from mnemo.install import scaffold

    scaffold.scaffold_vault(vault)
    cfg_path = vault / "mnemo.config.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["vaultRoot"] = str(vault)
    # The judge is off by default already; pinned so a default change cannot
    # quietly turn a new user's key-less day one into a keyed one.
    raw.setdefault("reflex", {}).setdefault("judge", {})["provider"] = "none"
    raw.setdefault("recall", {}).setdefault("rerank", {})["provider"] = "none"
    cfg_path.write_text(json.dumps(raw, indent=2), encoding="utf-8")
    old = os.environ.get("MNEMO_CONFIG_PATH")
    os.environ["MNEMO_CONFIG_PATH"] = str(cfg_path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("MNEMO_CONFIG_PATH", None)
        else:
            os.environ["MNEMO_CONFIG_PATH"] = old


def vault_counts(vault: Path) -> Dict[str, int]:
    """Live rule pages (``shared/<type>/``), staged pages, and memory files."""
    from mnemo.core import filters

    shared = vault / "shared"
    live = staged = 0
    for md in filters.iter_shared_pages(vault, include_inbox=True):
        rel = md.relative_to(shared).parts
        if rel[0] == "_inbox":
            staged += 1
        elif rel[0] in LIVE_TYPES:
            live += 1
    memory = sum(1 for p in vault.glob("bots/*/memory/*.md") if p.name != "MEMORY.md")
    briefings = sum(1 for _ in vault.glob("bots/*/briefings/sessions/*.md"))
    return {"live": live, "staged": staged, "memory": memory, "briefings": briefings}


def error_counts(vault: Path) -> Dict[str, int]:
    """``where:kind`` -> count from the arm vault's error log.

    The meter counts ``llm.call``s; a call that timed out twice was two CLI
    invocations and no answer, and this is where that shows.
    """
    from mnemo.core import errors

    out: Dict[str, int] = {}
    try:
        lines = (vault / errors.ERROR_LOG_NAME).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        key = "%s:%s" % (row.get("where"), row.get("kind"))
        out[key] = out.get(key, 0) + 1
    return out


def rebuild_indexes(vault: Path) -> None:
    """What SessionStart rebuilds before anything reads them."""
    from mnemo.core import rule_activation
    from mnemo.core.reflex import index as reflex_index

    rule_activation.write_index(vault, rule_activation.build_index(vault))
    reflex_index.write_index(vault, reflex_index.build_index(vault))


def session_start_blocks(vault: Path, project: str, cwd: str) -> Dict[str, Any]:
    """The hook's own blocks for ``project``, summarised; the envelope text is kept too."""
    from mnemo.core import config
    from mnemo.hooks import session_start as ss

    cfg = config.load_config()
    rebuild_indexes(vault)
    out: Dict[str, Any] = {}
    envelope = ss._build_injection_payload(vault, current_project=project, inject_briefing=True,
                                           session_id=None, source="startup")
    for line in envelope.splitlines():
        for key in ("local", "universal"):
            if line.startswith(key + ": ["):
                out[key + "_topics"] = [t for t in line[len(key) + 3:-1].split(", ") if t]
    out["last_briefing"] = "[last-briefing" in envelope
    out["staged_notice"] = ss._staged_backfill_notice(vault)
    out["learned"] = ss._learned_block(vault, cfg, project)
    out["offer"] = ss._offer_block(vault, cfg, project, cwd, None)
    out["text"] = "\n\n".join(x for x in (envelope, out["staged_notice"], out["learned"],
                                          out["offer"]) if x)
    out["carries"] = carries(out)
    return out


def replay_prompts(vault: Path, project: str, items: Sequence[Tuple[Session, float, str]],
                   texts: Dict[str, str], jev: Optional["Jev"] = None) -> List[Dict[str, Any]]:
    """The prompts through the hook's decision, in order; one unit per prompt.

    With ``jev``, the judge stage runs as the hook runs it (#479): each unit
    then carries ``judge``, the stage's log row for that prompt (None when
    nothing was asked), with ``fallback`` set when the gates answered instead.
    """
    from mnemo.core import config
    from mnemo.core.mcp import rerank, tools as mcp_tools
    from mnemo.core.reflex import replay
    from mnemo.core.reflex.index import load_index

    cfg = config.load_config()
    index = load_index(vault) or {"docs": {}, "postings": {}, "doc_count": 0}
    prompts = [replay.Prompt(session_id=s.sid, project=project, ts=ts, text=text)
               for s, ts, text in items]
    rows: Dict[Tuple[str, float], Dict[str, Any]] = {}
    stage = None
    if jev is not None:
        stage = jev.stage(vault, project, rows)
    result = replay.run(prompts, index, {}, reflex_cfg=cfg.get("reflex") or {}, judge=stage,
                        judge_candidates=jev.chosen["candidates"] if jev else 3)
    injected: Dict[Tuple[str, float], List[str]] = {}
    for inj in result.injections:
        injected.setdefault((inj.session_id, inj.ts), []).append(inj.slug)
    units = []
    for p in prompts:
        pool = []
        for slug in injected.get((p.session_id, p.ts), []):
            if slug not in texts:
                page = mcp_tools.read_mnemo_rule(vault, slug, scope="vault") or {}
                texts[slug] = rerank.rule_text(page.get("body") or "")
            pool.append({"slug": slug, "text": texts[slug]})
        units.append({"uid": mrg.unit_id(p.session_id, p.ts, p.text), "session_id": p.session_id,
                      "ts": p.ts, "prompt": mrg.judge_state(p.text), "pool": pool})
        if jev is not None:
            units[-1]["judge"] = rows.get((p.session_id, p.ts))
    return units


def set_last_run(vault: Path, when: float) -> None:
    """Move extraction's ``last_run`` watermark onto the transcript's clock."""
    path = vault / ".mnemo" / "extraction-state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    state["last_run"] = datetime.fromtimestamp(when).isoformat(timespec="seconds")
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def extraction_row(summary: Any) -> Dict[str, Any]:
    return {k: getattr(summary, k, None) for k in (
        "llm_calls", "pages_written", "auto_promoted", "demoted_unverified",
        "reference_held", "failed_chunks", "total_cost_usd")}


def routes(vault: Path) -> Dict[str, Dict[str, int]]:
    """route -> type -> pages, for every rule page in the vault.

    Live: ``project`` pages go direct (``promote_projects``, no model); any
    other live page was auto-promoted. Staged, by the first reason its
    frontmatter carries: backfill origin, the evidence gate's demotion, the
    reference gate's generic/narrative hold; otherwise multi-source (or a
    gate-kept page staged for that reason).
    """
    from mnemo.core import filters
    from mnemo.core.backfill.origin import is_backfill_frontmatter
    from mnemo.core.extract import reference_gate, scanner
    from mnemo.core.extract.demotion import is_demoted_frontmatter

    shared = vault / "shared"
    out: Dict[str, Dict[str, int]] = {}
    for md in filters.iter_shared_pages(vault, include_inbox=True):
        rel = md.relative_to(shared).parts
        staged = rel[0] == "_inbox"
        ptype = rel[1] if staged and len(rel) > 2 else rel[0]
        if ptype not in LIVE_TYPES:
            continue
        if not staged:
            route = "live: direct" if ptype == "project" else "live: auto-promoted"
        else:
            try:
                fm, _ = scanner.parse_frontmatter(md.read_text(encoding="utf-8"))
            except OSError:
                fm = {}
            if is_backfill_frontmatter(fm):
                route = "staged: backfill origin"
            elif is_demoted_frontmatter(fm):
                route = "staged: evidence demotion"
            elif reference_gate.is_held_frontmatter(fm):
                route = "staged: reference gate"
            else:
                route = "staged: multi-source/other"
        by_type = out.setdefault(route, {})
        by_type[ptype] = by_type.get(ptype, 0) + 1
    return out


def write_snapshot(snapshot: Dict[str, Tuple[Optional[str], str]],
                   current: Dict[str, Tuple[str, float, Optional[float]]], dest: Path, at: float) -> int:
    """Write the files present at ``at`` into ``dest``, dated no later than ``at``."""
    dest.mkdir(parents=True, exist_ok=True)
    n = 0
    for name, (text, status) in sorted(snapshot.items()):
        if text is None:
            continue
        p = dest / name
        p.write_text(text, encoding="utf-8")
        when = current[name][1] if status == "unchanged" else at
        os.utime(str(p), (when, when))
        n += 1
    return n


def mirror_snapshot(projects_root: Path) -> None:
    """SessionEnd's mirror step, the real ``mirror_all``, over ``projects_root`` only.

    The one seam is ``_claude_projects_root``: pointed at a directory whose
    only project is the snapshot, it mirrors that and nothing else on the
    machine, into the vault ``MNEMO_CONFIG_PATH`` names.
    """
    from mnemo.core import config, mirror

    real = mirror._claude_projects_root
    mirror._claude_projects_root = lambda: projects_root
    try:
        mirror.mirror_all(config.load_config())
    finally:
        mirror._claude_projects_root = real


# --- the arms -----------------------------------------------------------------------

def run_arm_a(ctx: Dict[str, Any], progress: Dict[str, Any], save: Callable[[], None]) -> None:
    from mnemo.cli.commands import backfill as backfill_cmd
    from mnemo.core import config, extract
    from mnemo.core.backfill import discover

    a = progress.setdefault("a", {})
    vault = ctx["work"] / "arm-a" / "vault"
    history, future = ctx["history"], ctx["future"]
    with arm_vault(vault):
        if not a.get("backfill"):
            stage = ctx["work"] / "arm-a" / "projects" / ctx["corpus"].name
            stage.mkdir(parents=True, exist_ok=True)
            for s in history:
                link = stage / s.path.name
                if not link.exists():
                    link.symlink_to(s.path)
            real_root, real_proj = discover.projects_root, backfill_cmd._current_project
            discover.projects_root = lambda: stage.parent
            backfill_cmd._current_project = lambda: ctx["agent"]
            before = ctx["meter"].calls
            try:
                args = argparse.Namespace(install_run=True, dry_run=False, yes=True,
                                          retry_failed=False, project=None, all=False, limit=None)
                code = backfill_cmd.cmd_backfill(args)
            finally:
                discover.projects_root, backfill_cmd._current_project = real_root, real_proj
            a["backfill"] = {"exit": code, "calls": ctx["meter"].calls - before,
                             "counts": vault_counts(vault),
                             "carry": session_start_blocks(vault, ctx["agent"], ctx["cwd"])}
            save()
        if not a.get("extract"):
            before = ctx["meter"].calls
            summary = extract.run_extraction(config.load_config(), background=True)
            a["extract"] = {"summary": extraction_row(summary),
                            "calls": ctx["meter"].calls - before,
                            "counts": vault_counts(vault),
                            "carry": session_start_blocks(vault, ctx["agent"], ctx["cwd"])}
            save()
        if not a.get("units"):
            texts: Dict[str, str] = {}
            a["units"] = replay_prompts(vault, ctx["agent"], first_prompts(future, ctx["prompts"]), texts)
            a["first_turns"] = replay_prompts(vault, ctx["agent"], first_turns(future, ctx["prompts"]), texts)
            save()


def run_arm_b(ctx: Dict[str, Any], progress: Dict[str, Any], save: Callable[[], None]) -> None:
    from mnemo.core import briefing, config, extract
    from mnemo.core import errors as err_mod
    from mnemo.hooks import session_end

    b = progress.setdefault("b", {"rows": []})
    vault = ctx["work"] / "arm-b" / "vault"
    sessions, plan = ctx["sessions"], ctx["plan_b"]
    cap = ctx["cap_emissions"]
    with arm_vault(vault):
        if "carry0" not in b:
            b["carry0"] = session_start_blocks(vault, ctx["agent"], ctx["cwd"])
            save()
        texts: Dict[str, str] = {}
        while len(b["rows"]) < min(len(sessions), ctx["max_sessions"]):
            k = len(b["rows"])
            s, step = sessions[k], plan[k]
            owed = sum(len(u["pool"]) for r in b["rows"] for u in r["units"])
            reserve = label_calls(owed + max_pairs(len(s.prompts), cap)) + ctx["label_reserve_a"]
            if ctx["meter"].calls + step["worst"] + reserve > ctx["meter"].budget:
                b["stopped"] = "budget: session %d would need up to %d call(s) with %d spent and %d owed to labels" % (
                    k + 1, step["worst"], ctx["meter"].calls, reserve)
                save()
                break
            before = ctx["meter"].calls
            units = replay_prompts(vault, ctx["agent"], [(s, ts, t) for ts, t in s.prompts], texts)
            cfg = config.load_config()
            state_path = vault / ".mnemo" / "extraction-state.json"
            ran: Optional[Dict[str, Any]] = None
            if session_end._debounce_passes(state_path, vault, cfg, now=datetime.fromtimestamp(s.end)):
                ran = extraction_row(extract.run_extraction(cfg, background=True))
                set_last_run(vault, s.end)
            # reuse_unchanged only matters to a resumed session: a first run
            # has no briefing on disk, so it is the hook's call exactly.
            # ``mnemo briefing``'s own handling: a failed briefing is logged and
            # the session simply has none — the hook never retries it.
            try:
                out = briefing.generate_session_briefing(s.path, ctx["agent"], cfg,
                                                         reuse_unchanged=True)
            except Exception as exc:
                err_mod.log_error(vault, "briefing.cli", exc)
                out = None
            if out is not None and Path(out).exists():
                os.utime(str(out), (s.end + BRIEFING_LAG, s.end + BRIEFING_LAG))
            b["rows"].append({
                "sid": s.sid, "start": s.start, "end": s.end, "extraction": ran,
                "briefed": out is not None, "calls": ctx["meter"].calls - before,
                "counts": vault_counts(vault),
                "carry_next": {k2: v for k2, v in session_start_blocks(
                    vault, ctx["agent"], ctx["cwd"]).items() if k2 != "text"},
                "units": units})
            save()
            print("arm b: session %d/%d %s — %d prompt(s), %d call(s) so far"
                  % (k + 1, len(sessions), s.sid[:8], len(s.prompts), ctx["meter"].calls),
                  file=sys.stderr)


def run_mirror_arm(ctx: Dict[str, Any], state: Dict[str, Any], save: Callable[[], None],
                   vault: Path, backfill_from: Optional[Path]) -> None:
    """Arm (c), or (a)+(c) when ``backfill_from`` names arm (a)'s vault.

    (a)+(c) starts from arm (a)'s harvested memory files, copied as they are
    (the same pages arm (a) measured, not a second harvest), because the
    install run happens at the first SessionStart and the mirror at the first
    SessionEnd. Then, in both: the mirror, the first extraction, the
    SessionStart payload, and arm (a)'s prompts.
    """
    from mnemo.core import config, extract, mirror

    with arm_vault(vault):
        if not state.get("mirror"):
            if backfill_from is not None:
                copied = 0
                for src in sorted(backfill_from.glob("bots/*/memory/*.md")):
                    dst = vault / src.relative_to(backfill_from)
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(src), str(dst))
                    copied += 1
                state["backfill_copied"] = copied
            before = vault_counts(vault)["memory"]
            mirror_snapshot(ctx["mirror_root"])
            state["mirror"] = {"agent": mirror._agent_from_project_dir(ctx["corpus"].name),
                               "memory_before": before, "counts": vault_counts(vault)}
            save()
        if not state.get("extract"):
            before = ctx["meter"].calls
            summary = extract.run_extraction(config.load_config(), background=True)
            state["extract"] = {"summary": extraction_row(summary),
                                "calls": ctx["meter"].calls - before,
                                "counts": vault_counts(vault), "routes": routes(vault),
                                "carry": session_start_blocks(vault, ctx["agent"], ctx["cwd"])}
            save()
        if not state.get("units"):
            texts: Dict[str, str] = {}
            state["units"] = replay_prompts(vault, ctx["agent"], first_prompts(ctx["future"], ctx["prompts"]), texts)
            state["first_turns"] = replay_prompts(vault, ctx["agent"], first_turns(ctx["future"], ctx["prompts"]), texts)
            save()


def label(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]], rater: str,
          save: Callable[[], None], limit: Optional[int] = None) -> int:
    from mnemo.core import config, llm

    cfg = config.load_config()
    provider = llm.resolve(cfg)
    timeout = int(cfg["extraction"]["subprocessTimeout"])
    todo = mrr.batches([u for u in units if u["pool"]], labels)
    if limit is not None:
        todo = todo[:max(0, limit)]
    for n, batch in enumerate(todo, 1):
        resp = provider(mrr.rater_prompt(batch), system=mrr.RATER_SYSTEM, model=rater,
                        timeout=timeout)
        for uid, row in mrr.parse_labels(resp.text, batch).items():
            labels.setdefault(uid, {}).update(row)
        save()
        print("labels: call %d/%d" % (n, len(todo)), file=sys.stderr)
    return len(todo)


# --- the report ---------------------------------------------------------------------

def arm_b_rows(b: Dict[str, Any], labels: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    rows = []
    for n, r in enumerate(b.get("rows", []), 1):
        rt = rates(r["units"], labels)
        rows.append({"n": n, "sid": r["sid"][:8],
                     "date": datetime.fromtimestamp(r["start"]).strftime("%m-%d"),
                     "live": r["counts"]["live"], "staged": r["counts"]["staged"],
                     "carries_next": bool(r["carry_next"].get("carries")),
                     "topics_next": len(r["carry_next"].get("local_topics") or [])
                     + len(r["carry_next"].get("universal_topics") or []),
                     "extracted": r["extraction"] is not None, "calls": r["calls"], **rt})
    return rows


def report(progress: Dict[str, Any], labels: Dict[str, Dict[str, int]],
           work: Optional[Path] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"meta": progress.get("meta", {}), "calls": progress.get("calls", {}),
                           "calls_c": progress.get("calls_c", {})}
    if work is not None:
        out["errors"] = {arm: error_counts(work / ("arm-" + arm) / "vault")
                         for arm in ("a", "b") + MIRROR_ARMS if (work / ("arm-" + arm)).is_dir()}
    a = progress.get("a") or {}
    if a.get("units") is not None:
        out["a"] = {
            "backfill": {k: a["backfill"][k] for k in ("exit", "calls", "counts")},
            "carry_after_backfill": {k: v for k, v in a["backfill"]["carry"].items() if k != "text"},
            "extract": {k: a["extract"][k] for k in ("summary", "calls", "counts")},
            "carry_first_session": {k: v for k, v in a["extract"]["carry"].items() if k != "text"},
            "carry_first_session_text": a["extract"]["carry"].get("text", ""),
            "next_prompts": rates(a["units"], labels),
            "first_turns": rates(a.get("first_turns") or [], labels),
        }
    b = progress.get("b") or {}
    if b.get("rows"):
        rows = arm_b_rows(b, labels)
        first = first_on_point(rows)
        first_fire = next((r["n"] for r in rows if r["fired"]), None)
        first_live = next((r["n"] for r in rows if r["live"]), None)
        units = [u for r in b["rows"] for u in r["units"]]
        out["b"] = {"rows": rows, "sessions": len(rows), "stopped": b.get("stopped"),
                    "first_on_point": first, "first_fire": first_fire, "first_live": first_live,
                    "carry0": bool((b.get("carry0") or {}).get("carries")),
                    "total": rates(units, labels)}
    for arm in MIRROR_ARMS:
        m = progress.get(arm) or {}
        if m.get("units") is None:
            continue
        out[arm] = {
            "snapshot": progress.get("snapshot") or {},
            "backfill_copied": m.get("backfill_copied"),
            "mirror": m["mirror"],
            "extract": {k: m["extract"][k] for k in ("summary", "calls", "counts", "routes")},
            "carry_first_session": {k: v for k, v in m["extract"]["carry"].items() if k != "text"},
            "carry_first_session_text": m["extract"]["carry"].get("text", ""),
            "next_prompts": rates(m["units"], labels),
            "first_turns": rates(m.get("first_turns") or [], labels),
            "injected": injected_slugs(m["units"] + (m.get("first_turns") or []), labels),
        }
    return out


def injected_slugs(units: Sequence[Dict[str, Any]], labels: Dict[str, Dict[str, int]]) -> List[Dict[str, Any]]:
    """slug -> times injected and its labels, most-injected first."""
    by: Dict[str, Dict[str, Any]] = {}
    for u in units:
        for c in u["pool"]:
            row = by.setdefault(c["slug"], {"slug": c["slug"], "n": 0, "on_point": 0, "labelled": 0})
            row["n"] += 1
            v = labels.get(u["uid"], {}).get(c["slug"])
            row["labelled"] += v is not None
            row["on_point"] += v == ON_POINT
    return sorted(by.values(), key=lambda r: (-r["n"], r["slug"]))


def mirror_lines(name: str, m: Dict[str, Any]) -> List[str]:
    lines = ["", name]
    if m.get("backfill_copied") is not None:
        lines.append("  arm (a)'s harvested memory files copied in: %d" % m["backfill_copied"])
    mi = m["mirror"]
    lines.append("  mirror (as agent %r): memory files %d -> %d; after it: %s"
                 % (mi["agent"], mi["memory_before"], mi["counts"]["memory"], mi["counts"]))
    ex = m["extract"]
    lines.append("  first extraction: %d call(s), %s; after it: %s" % (ex["calls"], ex["summary"], ex["counts"]))
    for route, by_type in sorted(ex["routes"].items()):
        lines.append("    %-28s %3d  %s" % (route, sum(by_type.values()), by_type))
    c = m["carry_first_session"]
    lines.append("  first SessionStart: carries %s; topics local %d / universal %d, last-briefing %s, "
                 "staged notice %s, learned block %s, offer %s"
                 % ("something" if c.get("carries") else "nothing",
                    len(c.get("local_topics") or []), len(c.get("universal_topics") or []),
                    c.get("last_briefing"), bool(c.get("staged_notice")), bool(c.get("learned")),
                    bool(c.get("offer"))))
    for key, label_ in (("next_prompts", "next prompts"), ("first_turns", "first turns")):
        r = m[key]
        lines.append("  %s: %d, fired %d (%s), pairs %d, on-point %d of %d labelled (%s of pairs); "
                     "prompts with an on-point rule %d (%s)"
                     % (label_, r["prompts"], r["fired"], pct(r["fired"], r["prompts"]), r["pairs"],
                        r["on_point"], r["labelled"], pct(r["on_point"], r["labelled"]),
                        r["prompts_on_point"], pct(r["prompts_on_point"], r["prompts"])))
    top = m.get("injected") or []
    if top:
        lines.append("  most injected: " + ", ".join(
            "%s x%d (%d on-point)" % (r["slug"], r["n"], r["on_point"]) for r in top[:5]))
    return lines


def report_lines(data: Dict[str, Any]) -> List[str]:
    meta = data.get("meta", {})
    lines = ["corpus %s: %s sessions, %s prompts; install after session %s; judge off"
             % (meta.get("corpus"), meta.get("sessions"), meta.get("prompts"), meta.get("install_after"))]
    calls = data.get("calls") or {}
    if calls:
        lines.append("model calls: %s total (budget %s), API-price equivalent $%.2f; by model %s"
                     % (calls.get("total"), calls.get("budget"), calls.get("usd", 0.0),
                        calls.get("by_model")))
    calls_c = data.get("calls_c") or {}
    if calls_c:
        lines.append("  of which arms (c) and (a)+(c): %s (budget %s), $%.2f"
                     % (calls_c.get("total"), calls_c.get("budget"), calls_c.get("usd", 0.0)))
    b = data.get("b")
    a = data.get("a")
    lines.append("")
    lines.append("HEADLINE")
    if b:
        n = b["sessions"]
        lines.append("  arm (b): first on-point injection at session %s (of %d run)%s"
                     % (b["first_on_point"] if b["first_on_point"] else "> %d" % n, n,
                        "; %s" % b["stopped"] if b["stopped"] else ""))
    if a:
        np_ = a["next_prompts"]
        lines.append("  arm (a): after the install backfill, %d live page(s), %d staged; the first session "
                     "%s; reflex fires on %d of %d prompts (%s), %d on-point pair(s)"
                     % (a["extract"]["counts"]["live"], a["extract"]["counts"]["staged"],
                        "carries something" if a["carry_first_session"]["carries"] else "carries nothing",
                        np_["fired"], np_["prompts"], pct(np_["fired"], np_["prompts"]), np_["on_point"]))
    for arm, name in (("c", "arm (c)"), ("ac", "arms (a)+(c)")):
        m = data.get(arm)
        if m:
            np_ = m["next_prompts"]
            lines.append("  %s: after the mirror and first extraction, %d live page(s), %d staged; the first "
                         "session %s; reflex fires on %d of %d prompts (%s), %d on-point pair(s) of %d"
                         % (name, m["extract"]["counts"]["live"], m["extract"]["counts"]["staged"],
                            "carries something" if m["carry_first_session"]["carries"] else "carries nothing",
                            np_["fired"], np_["prompts"], pct(np_["fired"], np_["prompts"]),
                            np_["on_point"], np_["pairs"]))
    if a:
        lines += ["", "ARM (a) — with history"]
        bf = a["backfill"]
        lines.append("  backfill: exit %s, %d call(s); after it: %s"
                     % (bf["exit"], bf["calls"], bf["counts"]))
        lines.append("  first extraction: %d call(s), %s; after it: %s"
                     % (a["extract"]["calls"], a["extract"]["summary"], a["extract"]["counts"]))
        c = a["carry_first_session"]
        lines.append("  first SessionStart: topics local %d / universal %d, last-briefing %s, "
                     "staged notice %s, learned block %s, offer %s"
                     % (len(c.get("local_topics") or []), len(c.get("universal_topics") or []),
                        c.get("last_briefing"), bool(c.get("staged_notice")), bool(c.get("learned")),
                        bool(c.get("offer"))))
        for key, name in (("next_prompts", "next prompts"), ("first_turns", "first turns")):
            r = a[key]
            lines.append("  %s: %d, fired %d (%s), pairs %d, on-point %d of %d labelled"
                         % (name, r["prompts"], r["fired"], pct(r["fired"], r["prompts"]),
                            r["pairs"], r["on_point"], r["labelled"]))
    if b:
        t = b["total"]
        lines += ["", "ARM (b) — without history",
                  "  first live page after session %s; first injection in session %s; "
                  "first on-point in session %s" % (b["first_live"], b["first_fire"], b["first_on_point"]),
                  "  all sessions: %d prompts, fired %d (%s), pairs %d, on-point %d of %d labelled"
                  % (t["prompts"], t["fired"], pct(t["fired"], t["prompts"]), t["pairs"],
                     t["on_point"], t["labelled"]),
                  "",
                  "   n  date  sid       live staged extr calls carries topics  prompts fired on-point"]
        for r in b["rows"]:
            lines.append("  %2d  %s  %s  %4d %6d %4s %5d %7s %6d  %7d %5d %8d"
                         % (r["n"], r["date"], r["sid"], r["live"], r["staged"],
                            "yes" if r["extracted"] else "-", r["calls"],
                            "yes" if r["carries_next"] else "no", r["topics_next"],
                            r["prompts"], r["fired"], r["on_point"]))
    snap = next((data[k]["snapshot"] for k in MIRROR_ARMS if data.get(k)), None)
    if snap:
        lines += ["", "ARM (c) — Claude Code auto-memory, mirrored at the first SessionEnd",
                  "  %s as it stood at %s (the first session after install): %d file(s) present, %s; "
                  "today %d" % (snap.get("dir"), snap.get("at_iso"), snap.get("present", 0),
                                snap.get("status"), snap.get("today", 0)),
                  "  by type (scanner's reading, MEMORY.md skipped): %s" % snap.get("types")]
        if snap.get("approximate"):
            lines.append("  approximate (changed after install by a shell command; today's text used): %s"
                         % ", ".join(snap["approximate"]))
    for arm, name in (("c", "ARM (c)"), ("ac", "ARMS (a)+(c) — what a real install with the backfill gets")):
        if data.get(arm):
            lines += mirror_lines(name, data[arm])
    for arm, counts in sorted((data.get("errors") or {}).items()):
        if counts:
            lines.append("")
            lines.append("arm (%s) error log: %s" % (arm, ", ".join(
                "%s x%d" % kv for kv in sorted(counts.items()))))
    guard = (data.get("meta") or {}).get("real_vault")
    if guard:
        lines += ["", "~/mnemo during the run: %d file(s) changed, %d naming the corpus or this run%s"
                  % (guard["changed"], len(guard["suspects"]),
                     (": " + ", ".join(guard["suspects"][:10])) if guard["suspects"] else "")]
    return lines


# --- the judge on (#479) ------------------------------------------------------------

#: Jev requests and Claude label calls one ``--judge`` run may spend (#479).
JEV_BUDGET = 1000
JUDGE_LABEL_BUDGET = 40
#: Run order: the two arms with a fixed vault first; arm (b) takes what is left.
JUDGE_ARMS = ("a", "c", "b")
JUDGE_NAME = "judge.json"
#: The judge-off bar declared in #479 before measuring: at most this share of
#: the labelled injections may be noise for day one to count as "decent".
NOISE_BAR = 0.20


class BudgetReached(Exception):
    """A request past ``--jev-budget``. ``judge.ask`` reads it as an error and falls back."""


def shipped_judge() -> Dict[str, Any]:
    """``reflex.judge`` as a user who turns it on gets it: every setting at its default."""
    from mnemo.core.reflex import judge

    return judge.settings({"reflex": {"judge": {"provider": "typesafe"}}})


class Jev:
    """The judge stage the hook runs, counting every request it sends.

    ``client`` is ``rerank.typesafe_client`` over the key ``judge.resolve_key``
    finds (a stub in tests). Each prompt goes through the real
    :func:`mnemo.core.reflex.judge.ask`: the same prompt and rule truncation,
    the shipped timeout on its own thread, None on any failure — which
    :func:`mnemo.core.reflex.replay.run` turns into the gates' decision, as
    the hook does.
    """

    def __init__(self, client: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
                 chosen: Dict[str, Any], budget: int, sent: int = 0):
        self.client, self.chosen, self.budget, self.sent = client, dict(chosen), budget, sent

    def _counted(self, state: Dict[str, Any], questions: Dict[str, Any]) -> Dict[str, Any]:
        if self.sent >= self.budget:
            raise BudgetReached("--jev-budget of %d requests reached" % self.budget)
        self.sent += 1
        return self.client(state, questions)

    def stage(self, vault: Path, project: str,
              rows: Dict[Tuple[str, float], Dict[str, Any]]) -> Callable[[Any, List[str]], Optional[List[str]]]:
        from mnemo.core.reflex import judge

        def _stage(prompt: Any, pool: List[str]) -> Optional[List[str]]:
            picks, info = judge.ask(vault, prompt=prompt.text, slugs=pool,
                                    chosen_settings=self.chosen, project=project,
                                    client=self._counted)
            rows[(prompt.session_id, prompt.ts)] = dict(info, fallback=picks is None)
            return picks

        return _stage


def request_bound(vault: Path, project: str, items: Sequence[Tuple[Session, float, str]]) -> List[int]:
    """Per session, the most requests the stage can send over ``items``.

    Probed locally with a stage that answers "none" to every pool: nothing is
    injected, so neither the session cap nor the dedupe keeps a ranked prompt
    from being asked. Real answers inject, and injecting only removes prompts
    (the cap) or rules (the dedupe) from later pools.
    """
    from mnemo.core import config
    from mnemo.core.reflex import replay
    from mnemo.core.reflex.index import load_index

    asked: Dict[str, int] = {}

    def probe(prompt: Any, _pool: List[str]) -> List[str]:
        asked[prompt.session_id] = asked.get(prompt.session_id, 0) + 1
        return []

    prompts = [replay.Prompt(session_id=s.sid, project=project, ts=ts, text=text)
               for s, ts, text in items]
    replay.run(prompts, load_index(vault) or {"docs": {}, "postings": {}, "doc_count": 0}, {},
               reflex_cfg=config.load_config().get("reflex") or {}, judge=probe)
    return list(asked.values())


def pairs_bound(asks: Sequence[int], candidates: int, cap: int) -> int:
    """Most pairs the judge can inject: ``candidates`` per request, the cap per session.

    The cap is checked before a prompt, so the last prompt can take a session
    ``candidates - 1`` past it, as in the hook.
    """
    return sum(min(candidates * n, cap - 1 + candidates) for n in asks if n)


def learned_order(vault: Path) -> List[str]:
    """``type/slug`` of every page that went live, in the order it did (``learned.jsonl``)."""
    rows = []
    try:
        lines = (vault / ".mnemo" / "learned.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("slug") and row.get("type"):
            rows.append((int(row.get("seq") or 0), "%s/%s" % (row["type"], row["slug"])))
    out: List[str] = []
    for _seq, key in sorted(rows):
        if key not in out:
            out.append(key)
    return out


def rewind(src: Path, dest: Path, keep: Sequence[str]) -> Path:
    """``src`` as it stood when only the live pages ``keep`` existed, built at ``dest``.

    Arm (b)'s vault grows session by session and only its end state is kept.
    ``learned.jsonl`` orders the pages that went live, and a row's live count
    says how many there were, so the vault a session met is the final one
    minus the pages that went live later; staged pages stay, since nothing
    reads them. A page's text is its final text. The judge-off replay over the
    rewound vault is compared with the injections arm (b) recorded, which is
    what says whether that approximation holds.
    """
    if (dest / ".mnemo" / "rewound.json").exists():
        return dest
    tmp = dest.parent / (dest.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(str(tmp))
    shutil.copytree(str(src), str(tmp))
    wanted = set(keep)
    left = 0
    for ptype in LIVE_TYPES:
        for md in sorted((tmp / "shared" / ptype).glob("*.md")):
            if "%s/%s" % (ptype, md.stem) in wanted:
                left += 1
            else:
                md.unlink()
    if left != len(wanted):
        raise SystemExit("error: rewinding %s to %d live page(s) left %d; learned.jsonl and the "
                         "pages disagree" % (src, len(wanted), left))
    with arm_vault(tmp):
        rebuild_indexes(tmp)
    (tmp / ".mnemo" / "rewound.json").write_text(json.dumps(sorted(wanted)), encoding="utf-8")
    raw = json.loads((tmp / "mnemo.config.json").read_text(encoding="utf-8"))
    raw["vaultRoot"] = str(dest)
    (tmp / "mnemo.config.json").write_text(json.dumps(raw, indent=2), encoding="utf-8")
    if dest.exists():
        shutil.rmtree(str(dest))
    dest.parent.mkdir(parents=True, exist_ok=True)
    os.replace(str(tmp), str(dest))
    return dest


def reproduced(mine: Sequence[Dict[str, Any]], cached: Sequence[Dict[str, Any]]) -> List[int]:
    """[prompts whose injected rules match, prompts compared] — the replay against the record."""
    theirs = {u["uid"]: [c["slug"] for c in u["pool"]] for u in cached}
    same = sum(1 for u in mine if theirs.get(u["uid"]) == [c["slug"] for c in u["pool"]])
    return [same, len(mine)]


def jev_stats(units: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Requests the stage sent over ``units``, how they ended, and their median wall time."""
    rows = [u["judge"] for u in units if u.get("judge") and u["judge"].get("asked")]
    by: Dict[str, int] = {}
    for r in rows:
        by[r["status"]] = by.get(r["status"], 0) + 1
    ms = sorted(int(r.get("ms") or 0) for r in rows)
    median = None
    if ms:
        mid = len(ms) // 2
        median = ms[mid] if len(ms) % 2 else (ms[mid - 1] + ms[mid]) / 2.0
    return {"requests": len(rows), "status": by,
            "fallback": sum(1 for r in rows if r.get("fallback")),
            "said_none": sum(1 for r in rows if not r.get("fallback") and not r.get("injected")),
            "median_ms": median, "max_ms": ms[-1] if ms else None}


def noise_share(r: Dict[str, Any]) -> Optional[float]:
    return r["noise"] / float(r["labelled"]) if r["labelled"] else None


def _sessions_by_sid(corpus: Path) -> Dict[str, Session]:
    return {s.sid: s for s in load_sessions(corpus)}


def from_record(row: Dict[str, Any]) -> Session:
    """An arm-(b) session whose transcript is gone, rebuilt from the units it recorded.

    Claude Code prunes old transcripts. A unit keeps the prompt as the judge
    would send it (whitespace collapsed, the first 1,200 characters), so the
    ranking of a longer prompt can differ from the one recorded; the
    judge-off check says whether it did.
    """
    return Session(sid=row["sid"], path=Path(row["sid"] + ".jsonl"), start=row["start"],
                   end=row["end"], mutations=0,
                   prompts=[(u["ts"], u["prompt"]) for u in row["units"]])


def recorded_only(session: Session, row: Dict[str, Any]) -> Session:
    """``session`` with only the prompts the arm-(b) row recorded."""
    seen = {u["ts"] for u in row["units"]}
    return Session(sid=session.sid, path=session.path, start=session.start, end=session.end,
                   mutations=session.mutations, mtime=session.mtime,
                   prompts=[(ts, t) for ts, t in session.prompts if ts in seen])


def adopt_uids(mine: List[Dict[str, Any]], recorded: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """File each unit under the uid the judge-off run gave the same prompt, so labels line up.

    Matched on (session, time); a transcript still on disk gives the same uid
    anyway, a session rebuilt :func:`from_record` would not.
    """
    by = {(u["session_id"], u["ts"]): u["uid"] for u in recorded}
    for u in mine:
        u["uid"] = by.get((u["session_id"], u["ts"]), u["uid"])
    return mine


def judge_sources(work: Path, arm_a_work: Path, corpus: Path) -> Dict[str, Dict[str, Any]]:
    """Per arm: its vault, its prompts and the judge-off units recorded for them."""
    by_sid = None
    out: Dict[str, Dict[str, Any]] = {}
    for arm, base in (("a", arm_a_work), ("c", work), ("b", work)):
        progress = _read(base / "progress.json", {})
        meta, st = progress.get("meta") or {}, progress.get(arm) or {}
        if arm == "b" and not st.get("rows") or arm != "b" and st.get("units") is None:
            continue
        if by_sid is None:
            by_sid = _sessions_by_sid(corpus)
        src = {"base": base, "vault": base / ("arm-" + arm) / "vault", "agent": meta["agent"]}
        if arm == "b":
            src["rows"] = st["rows"]
            # A transcript still being written has grown since the run; only
            # the prompts it recorded are replayed.
            src["sessions"] = [recorded_only(by_sid[r["sid"]], r) if r["sid"] in by_sid
                               else from_record(r) for r in st["rows"]]
            src["from_record"] = sum(1 for r in st["rows"] if r["sid"] not in by_sid)
            src["order"] = learned_order(src["vault"])
        else:
            future_sids = meta["sids"][int(meta["install_after"]):]
            missing = [sid for sid in future_sids if sid not in by_sid]
            if missing:
                raise SystemExit("error: %d transcript(s) after %s's install point are gone from %s"
                                 % (len(missing), base, corpus))
            future = [by_sid[sid] for sid in future_sids]
            src["items"] = first_prompts(future, len(st["units"]))
            src["off"] = st["units"]
        out[arm] = src
    return out


def b_live_before(rows: Sequence[Dict[str, Any]], k: int) -> int:
    """Live pages session ``k`` (0-based) met: what the previous session's end left."""
    return int(rows[k - 1]["counts"]["live"]) if k else 0


def plan_judge(sources: Dict[str, Dict[str, Any]], rewinds: Path, *, arms: Sequence[str],
               budget: int, max_sessions: int, chosen: Dict[str, Any], cap: int) -> Dict[str, Any]:
    """Request and pair bounds per arm, and how many arm-(b) sessions fit what is left."""
    plan: Dict[str, Any] = {}
    left = budget
    for arm in arms:
        src = sources.get(arm)
        if src is None:
            continue
        if arm != "b":
            with arm_vault(src["vault"]):
                asks = request_bound(src["vault"], src["agent"], src["items"])
            plan[arm] = {"requests": sum(asks), "prompts": len(src["items"]),
                         "pairs": pairs_bound(asks, chosen["candidates"], cap)}
            left -= sum(asks)
            continue
        fit = requests = pairs = prompts = 0
        for k, s in enumerate(src["sessions"][:max_sessions]):
            n = b_live_before(src["rows"], k)
            vault = rewind(src["vault"], rewinds / ("arm-b-live-%03d" % n) / "vault", src["order"][:n])
            with arm_vault(vault):
                asks = request_bound(vault, src["agent"], [(s, ts, t) for ts, t in s.prompts])
            if requests + sum(asks) > left:
                break
            fit += 1
            requests += sum(asks)
            pairs += pairs_bound(asks, chosen["candidates"], cap)
            prompts += len(s.prompts)
        plan["b"] = {"requests": requests, "prompts": prompts, "pairs": pairs, "sessions": fit,
                     "of": len(src["sessions"])}
    return plan


def run_judge(sources: Dict[str, Dict[str, Any]], state: Dict[str, Any], jev: Jev, rewinds: Path, *,
              arms: Sequence[str], max_sessions: int, save: Callable[[], None]) -> None:
    """Each arm's prompts with the stage on, and the same prompts off as a check on the replay."""
    texts: Dict[str, str] = {}
    for arm in arms:
        src = sources.get(arm)
        if src is None:
            continue
        st = state["arms"].setdefault(arm, {})
        if arm != "b":
            if st.get("units") is not None:
                continue
            with arm_vault(src["vault"]):
                bound = sum(request_bound(src["vault"], src["agent"], src["items"]))
                if jev.sent + bound > jev.budget:
                    st["skipped"] = "budget: up to %d request(s) with %d sent of %d" % (
                        bound, jev.sent, jev.budget)
                    save()
                    continue
                st.pop("skipped", None)
                off = adopt_uids(replay_prompts(src["vault"], src["agent"], src["items"], texts), src["off"])
                st["off_check"] = reproduced(off, src["off"])
                st["off_units"] = off
                st["units"] = adopt_uids(
                    replay_prompts(src["vault"], src["agent"], src["items"], texts, jev), src["off"])
            state["requests"] = jev.sent
            save()
            print("judge: arm (%s) done, %d request(s) so far" % (arm, jev.sent), file=sys.stderr)
            continue
        rows = st.setdefault("rows", [])
        st.pop("stopped", None)
        while len(rows) < min(len(src["sessions"]), max_sessions):
            k = len(rows)
            s = src["sessions"][k]
            n = b_live_before(src["rows"], k)
            vault = rewind(src["vault"], rewinds / ("arm-b-live-%03d" % n) / "vault", src["order"][:n])
            items = [(s, ts, t) for ts, t in s.prompts]
            with arm_vault(vault):
                bound = sum(request_bound(vault, src["agent"], items))
                if jev.sent + bound > jev.budget:
                    st["stopped"] = "budget: session %d may need %d request(s) with %d sent of %d" % (
                        k + 1, bound, jev.sent, jev.budget)
                    save()
                    break
                recorded = src["rows"][k]["units"]
                off = adopt_uids(replay_prompts(vault, src["agent"], items, texts), recorded)
                units = adopt_uids(replay_prompts(vault, src["agent"], items, texts, jev), recorded)
            rows.append({"sid": s.sid, "live": n, "bound": bound,
                         "off_check": reproduced(off, recorded), "off_units": off, "units": units})
            state["requests"] = jev.sent
            save()
            print("judge: arm (b) session %d/%d, %d request(s) so far"
                  % (k + 1, len(src["sessions"]), jev.sent), file=sys.stderr)


def judge_report(state: Dict[str, Any], sources: Dict[str, Dict[str, Any]],
                 labels: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    """Per arm, the stage off (as recorded) next to on, over the same prompts."""
    out: Dict[str, Any] = {"requests": state.get("requests", 0), "budget": state.get("budget"),
                           "label_calls": state.get("label_calls", 0),
                           "label_budget": state.get("label_budget"),
                           "settings": state.get("settings"), "arms": {}}
    everything: List[Dict[str, Any]] = []
    for arm in JUDGE_ARMS:
        st = (state.get("arms") or {}).get(arm) or {}
        src = sources.get(arm)
        if src is None:
            continue
        if arm != "b":
            if st.get("units") is None:
                if st.get("skipped"):
                    out["arms"][arm] = {"skipped": st["skipped"]}
                continue
            on_units, off_units, recorded = st["units"], st["off_units"], src["off"]
            row: Dict[str, Any] = {"off_check": st.get("off_check")}
        else:
            rows = st.get("rows") or []
            if not rows:
                continue
            on_units = [u for r in rows for u in r["units"]]
            off_units = [u for r in rows for u in r["off_units"]]
            recorded = [u for r in src["rows"][:len(rows)] for u in r["units"]]
            checks = [r["off_check"] for r in rows]
            row = {"off_check": [sum(c[0] for c in checks), sum(c[1] for c in checks)],
                   "sessions": len(rows), "of": len(src["rows"]), "stopped": st.get("stopped"),
                   "first_on_point_recorded": first_on_point(
                       [rates(r["units"], labels) for r in src["rows"][:len(rows)]]),
                   "first_on_point_off": first_on_point([rates(r["off_units"], labels) for r in rows]),
                   "first_on_point_on": first_on_point([rates(r["units"], labels) for r in rows]),
                   "sessions_off_check": sum(1 for c in checks if c[0] == c[1]),
                   "from_record": src.get("from_record", 0)}
        everything += on_units
        row.update({"recorded": rates(recorded, labels), "off": rates(off_units, labels),
                    "on": rates(on_units, labels), "jev": jev_stats(on_units)})
        row["noise_off"], row["noise_on"] = noise_share(row["off"]), noise_share(row["on"])
        out["arms"][arm] = row
    out["jev"] = jev_stats(everything)
    return out


def _share(x: Optional[float]) -> str:
    return "n/a" if x is None else "%.0f%%" % (100 * x)


def judge_lines(data: Dict[str, Any]) -> List[str]:
    s = data.get("settings") or {}
    j = data["jev"]
    lines = ["day one with the judge on: %s, injectAt %s, timeout %ss, pool top %s; rater %s"
             % (s.get("model"), s.get("injectAt"), s.get("timeoutSeconds"), s.get("candidates"),
                data.get("rater")),
             "Jev: %d request(s) of a %s budget; %s; fell back to the gates %d; median %s ms, max %s ms"
             % (j["requests"], data.get("budget"), j["status"] or "none", j["fallback"],
                j["median_ms"], j["max_ms"]),
             "labels: %d call(s) of a %s budget" % (data.get("label_calls", 0), data.get("label_budget")),
             "",
             "arm  judge  prompts  fired  emit    pairs  labelled  on-point  marginal  noise  noise%",
             "(off = judge-off replayed here, on the same vault as on; recorded = #467's run, "
             "shown where the two differ)"]
    names = {"a": "(a)", "b": "(b)", "c": "(c)"}
    for arm in ("a", "b", "c"):
        row = data["arms"].get(arm)
        if not row:
            continue
        if row.get("skipped"):
            lines.append("%-4s skipped: %s" % (names[arm], row["skipped"]))
            continue
        for mode in ("recorded", "off", "on") if row["off_check"][0] != row["off_check"][1] else ("off", "on"):
            r = row[mode]
            lines.append("%-4s %-5s  %7d  %5d  %-6s %5d  %8d  %8d  %8d  %5d  %6s"
                         % (names[arm], mode[:5], r["prompts"], r["fired"], pct(r["fired"], r["prompts"]),
                            r["pairs"], r["labelled"], r["on_point"], r["marginal"], r["noise"],
                            _share(noise_share(r))))
    lines.append("")
    for arm in ("a", "b", "c"):
        row = data["arms"].get(arm)
        if not row or row.get("skipped"):
            continue
        jv = row["jev"]
        lines.append("arm %s: Jev %d request(s), %s, %d fallback(s), %d answered none; median %s ms"
                     % (names[arm], jv["requests"], jv["status"] or "none", jv["fallback"],
                        jv["said_none"], jv["median_ms"]))
        oc = row.get("off_check") or [0, 0]
        lines.append("        judge-off replay here matches the recorded injections on %d of %d prompt(s)"
                     % (oc[0], oc[1]))
        if arm == "b":
            never = "> %d" % row["sessions"]
            lines.append("        %d of %d session(s) run%s; first on-point injection: recorded at session %s, "
                         "off at %s, on at %s; sessions whose rewound vault reproduces the record "
                         "exactly: %d" % (row["sessions"], row["of"],
                                          " (%s)" % row["stopped"] if row.get("stopped") else "",
                                          row["first_on_point_recorded"] or never,
                                          row["first_on_point_off"] or never,
                                          row["first_on_point_on"] or never,
                                          row["sessions_off_check"]))
            if row.get("from_record"):
                lines.append("        %d session(s) rebuilt from the recorded prompts: their transcripts "
                             "are gone" % row["from_record"])
    lines.append("")
    lines.append("BAR (declared in #479): day one is decent when noise <= %d%% of labelled injections"
                 % int(NOISE_BAR * 100))
    for arm in ("a", "b", "c"):
        row = data["arms"].get(arm)
        if not row or row.get("skipped"):
            continue
        share = row["noise_on"]
        verdict = ("no labelled injection" if share is None
                   else "clears" if share <= NOISE_BAR else "misses")
        lines.append("  arm %s, judge on: %s noise over %d labelled of %d pair(s) -> %s"
                     % (names[arm], _share(share), row["on"]["labelled"], row["on"]["pairs"], verdict))
    return lines


def main_judge(args: argparse.Namespace, work: Path, labels_path: Path,
               all_labels: Dict[str, Dict[str, Dict[str, int]]], labels: Dict[str, Dict[str, int]]) -> int:
    from mnemo.core import config
    from mnemo.core.mcp import rerank
    from mnemo.core.reflex import judge

    arm_a_work = (args.arm_a_work or work).expanduser().resolve()
    # Arm (a)'s judge-off labels may live beside its own run; the rater and
    # its instruction are the same, so they are the same column.
    if arm_a_work != work:
        other = _read(arm_a_work / "labels.json", {}).get(mrr.column(args.rater), {})
        for uid, row in other.items():
            for slug, value in row.items():
                labels.setdefault(uid, {}).setdefault(slug, value)
    arms = [a for a in JUDGE_ARMS if args.arm in (a, "all")]
    state_path = work / JUDGE_NAME
    state: Dict[str, Any] = _read(state_path, {})
    sources = judge_sources(work, arm_a_work, args.corpus)
    chosen = shipped_judge()
    cfg = config.load_config(missing_path=Path("/nonexistent/mnemo.config.json"))
    cap = int((cfg.get("reflex") or {}).get("maxEmissionsPerSession", 10))

    if not args.dry_run and not args.send:
        if not state:
            print("no judge results in %s; --judge --dry-run, then --judge --send" % work, file=sys.stderr)
            return 1
        data = judge_report(state, sources, labels)
        data["rater"] = args.rater
        print(json.dumps(data, indent=1, default=str) if args.json else "\n".join(judge_lines(data)))
        return 0

    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="mnemo-day-one-rewind-") as tmp:
            plan = plan_judge(sources, Path(tmp), arms=arms, budget=args.jev_budget,
                              max_sessions=args.max_sessions, chosen=chosen, cap=cap)
        print("judge: %s, injectAt %s, timeout %ss, pool top %d; sends the first %d chars of a prompt "
              "and %d of each rule" % (chosen["model"], chosen["injectAt"], chosen["timeoutSeconds"],
                                       chosen["candidates"], judge.PROMPT_CHARS, rerank.BODY_CHARS))
        requests = pairs = 0
        for arm in arms:
            p = plan.get(arm)
            if p is None:
                print("arm (%s): no judge-off run to compare with; skipped" % arm)
                continue
            extra = (", %d of %d session(s) fit" % (p["sessions"], p["of"])) if arm == "b" else ""
            print("arm (%s): %d prompt(s)%s: <= %d Jev request(s), <= %d injected pair(s)"
                  % (arm, p["prompts"], extra, p["requests"], p["pairs"]))
            requests += p["requests"]
            pairs += p["pairs"]
        print("total: <= %d Jev request(s) of a %d budget; labels <= %d Claude call(s) for <= %d pair(s) "
              "(pairs already labelled judge-off are not asked again) of a %d budget"
              % (requests, args.jev_budget, label_calls(pairs), pairs, args.label_budget))
        return 0

    chosen_key = dict(chosen)
    key, source = judge.resolve_key(chosen_key)
    if not key:
        print("error: no TypeSafe key (%s or ~/.mnemo/secrets.json)" % chosen["keyEnv"], file=sys.stderr)
        return 1
    client = rerank.typesafe_client(key, model=chosen["model"], timeout=float(chosen["timeoutSeconds"]))
    jev = Jev(client, chosen, args.jev_budget, sent=int(state.get("requests", 0)))
    state.update({"settings": {k: v for k, v in chosen.items() if k != "keyEnv"}, "key_source": source,
                  "budget": args.jev_budget, "label_budget": args.label_budget})
    state.setdefault("arms", {})

    def save() -> None:
        _write(state_path, state)
        _write(labels_path, all_labels)

    rewinds = work / "judge-rewind"
    before = fingerprint(Path.home() / REAL_VAULT)
    run_judge(sources, state, jev, rewinds, arms=arms, max_sessions=args.max_sessions, save=save)
    state["requests"] = jev.sent
    save()
    # Judge-on and judge-off pairs in one pass, arm by arm; the rater sees a
    # prompt and a rule's text and nothing that says which mode injected it.
    units = []
    for arm in arms:
        st = state["arms"].get(arm) or {}
        for part in ([st] if arm != "b" else st.get("rows") or []):
            units += (part.get("units") or []) + (part.get("off_units") or [])
    left = args.label_budget - int(state.get("label_calls", 0))
    any_vault = next(iter(sources.values()))["vault"] if sources else None
    if any_vault is not None and left > 0:
        with tempfile.TemporaryDirectory(prefix="mnemo-day-one-") as scratch, mrr.mrl._chdir(scratch), \
                arm_vault(any_vault):
            state["label_calls"] = int(state.get("label_calls", 0)) + label(
                units, labels, args.rater, save, limit=left)
        save()
    diff = changed(before, fingerprint(Path.home() / REAL_VAULT))
    state["real_vault"] = {"changed": len(diff), "suspects": suspects(
        diff, [args.corpus.name, "day-one", "mnemo-day-one"])}
    save()
    data = judge_report(state, sources, labels)
    data["rater"] = args.rater
    print("\n".join(judge_lines(data)))
    return 0


# --- main -----------------------------------------------------------------------------

def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(path))


def estimate(sessions: Sequence[Session], history: Sequence[Session], future: Sequence[Session],
             plan: Sequence[Dict[str, Any]], cfg: Dict[str, Any], *, prompts: int,
             max_sessions: int, budget: int) -> Dict[str, Any]:
    from mnemo.cli.commands.backfill import _estimate_input_tokens

    ext = cfg["extraction"]
    model, gate = ext["model"], (ext.get("referenceGate") or {}).get("model") or ext["model"]
    bf = cfg["backfill"]
    cap = int((cfg.get("reflex") or {}).get("maxEmissionsPerSession", 10))
    chosen = install_selection(history, int(bf["installCap"]), int(bf["minFileMutations"]))
    harvest_in = _estimate_input_tokens(chosen)
    pages = PAGES_PER_HARVEST * len(chosen)
    a_extract = 2 * 3 * int(math.ceil(pages / 3.0 / ext["chunkSize"])) if pages else 0
    a_pairs = sum(max_pairs(1, cap) for _ in first_prompts(future, prompts))
    a_labels = label_calls(min(a_pairs, 2 * prompts)) + label_calls(2 * len(first_turns(future, prompts)))
    a_calls = len(chosen) + a_extract + a_labels
    a_usd = (price(model, harvest_in, OUT_TOKENS["harvest"] * len(chosen))
             + price(model, pages * TOKENS_PER_PAGE, OUT_TOKENS["extract"] * a_extract // 2)
             + price(gate, pages * TOKENS_PER_PAGE, OUT_TOKENS["gate"] * a_extract // 2)
             + price(DEFAULT_RATER, a_labels * 40 * 200, OUT_TOKENS["label"] * a_labels))

    fit = 0
    spent = a_calls
    pairs = 0
    for s, step in zip(sessions, plan):
        if fit >= max_sessions:
            break
        need = spent + step["worst"] + label_calls(pairs + max_pairs(len(s.prompts), cap))
        if need > budget:
            break
        spent += step["worst"]
        pairs += max_pairs(len(s.prompts), cap)
        fit += 1
    run = list(zip(sessions, plan))[:fit]
    brief = [s for s, st in run if st["briefing"]]
    b_extract = sum(st["extract_calls"] for _, st in run)
    b_labels = label_calls(pairs)
    b_usd = (price(model, _estimate_input_tokens(brief), OUT_TOKENS["briefing"] * len(brief))
             + price(model, sum(st["consumed"] for _, st in run) * TOKENS_PER_BRIEFING,
                     OUT_TOKENS["extract"] * b_extract // 2)
             + price(gate, sum(st["consumed"] for _, st in run) * TOKENS_PER_PAGE,
                     OUT_TOKENS["gate"] * b_extract // 2)
             + price(DEFAULT_RATER, pairs * 200, OUT_TOKENS["label"] * b_labels))
    return {"a": {"history": len(history), "install_selected": len(chosen),
                  "harvest_calls": len(chosen), "extract_calls_bound": a_extract,
                  "label_calls_bound": a_labels, "calls": a_calls, "usd": a_usd,
                  "harvest_tokens_in": harvest_in},
            "b": {"sessions_fit": fit, "of": len(sessions), "briefing_calls": len(brief),
                  "extractions": sum(1 for _, st in run if st["extract"]),
                  "extract_calls_bound": b_extract, "label_calls_bound": b_labels,
                  "calls": len(brief) + b_extract + b_labels, "usd": b_usd,
                  "prompts": sum(len(s.prompts) for s, _ in run)},
            "a_label_reserve": a_labels}


def build_snapshot(corpus: Path, at: float) -> Tuple[Dict[str, Tuple[Optional[str], str]],
                                                    Dict[str, Tuple[str, float, Optional[float]]],
                                                    Dict[str, Any]]:
    """The corpus's auto-memory directory at ``at``, from today's files and every transcript's record.

    Every transcript under the corpus's parent (all of Claude Code's
    projects) is read, since a worktree's session writes the same directory.
    """
    memory_dir = corpus / "memory"
    current = read_memory_dir(memory_dir)
    events = memory_events(sorted(corpus.parent.rglob("*.jsonl")), memory_dir)
    snap = snapshot_at(current, events, at)
    present = [(n, t) for n, (t, st) in snap.items() if t is not None]
    info = {"dir": str(memory_dir), "at": at,
            "at_iso": datetime.fromtimestamp(at).isoformat(timespec="seconds"),
            "today": len(current), "present": len(present), "status": status_counts(snap),
            "types": memory_types(present), "types_today": memory_types((n, v[0]) for n, v in current.items()),
            "approximate": sorted(n for n, (_t, st) in snap.items() if st == "approximate"),
            "events": len(events)}
    return snap, current, info


def estimate_c(snap_types: Dict[str, int], backfill_types: Dict[str, int], chunk: int,
               future: Sequence[Session], prompts: int) -> Dict[str, Any]:
    """Worst-case calls for arm (c) and (a)+(c): one extraction each, then labels."""
    labels_each = label_calls(2 * len(first_prompts(future, prompts))) + label_calls(
        2 * len(first_turns(future, prompts)))
    merged = dict(snap_types)
    for t, n in backfill_types.items():
        merged[t] = merged.get(t, 0) + n
    c = extraction_bound(snap_types, chunk) + labels_each
    ac = extraction_bound(merged, chunk) + labels_each
    return {"c": c, "ac": ac, "calls": c + ac, "labels_each": labels_each}


def backfill_types(work: Path) -> Optional[Dict[str, int]]:
    """Types of arm (a)'s harvested memory files, or None before arm (a) has run."""
    files = sorted((work / "arm-a" / "vault").glob("bots/*/memory/*.md"))
    if not files:
        return None
    return memory_types((f.name, f.read_text(encoding="utf-8", errors="replace")) for f in files)


def main(argv: Optional[Sequence[str]] = None) -> int:
    from mnemo.core import config

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="plan, calls and cost; calls nothing")
    ap.add_argument("--send", action="store_true", help="run the arms (model calls), then label")
    ap.add_argument("--arm", choices=("a", "b", "c", "both", "all"), default="all",
                    help="both = a and b; c = arm (c) and (a)+(c); all = every arm "
                    "(--judge: a, b, c or all; (a)+(c) is not judged)")
    ap.add_argument("--corpus", type=Path, default=None,
                    help="transcript directory (default: ~/%s)" % DEFAULT_CORPUS)
    ap.add_argument("--work", type=Path, default=None,
                    help="vaults, progress and labels (default: ~/%s)" % DEFAULT_WORK)
    ap.add_argument("--install-after", type=int, default=None,
                    help="sessions of history at install (default: half the corpus)")
    ap.add_argument("--prompts", type=int, default=PROMPTS)
    ap.add_argument("--max-sessions", type=int, default=10 ** 6, help="arm (b) session cap")
    ap.add_argument("--budget", type=int, default=BUDGET)
    ap.add_argument("--budget-c", type=int, default=C_BUDGET,
                    help="model calls arms (c) and (a)+(c) may spend, apart from --budget")
    ap.add_argument("--rater", default=DEFAULT_RATER)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--judge", action="store_true",
                    help="replay the saved arms with reflex.judge on (#479); sends to TypeSafe with --send")
    ap.add_argument("--jev-budget", type=int, default=JEV_BUDGET, help="--judge: Jev requests")
    ap.add_argument("--label-budget", type=int, default=JUDGE_LABEL_BUDGET,
                    help="--judge: Claude label calls")
    ap.add_argument("--arm-a-work", type=Path, default=None,
                    help="--judge: the --work whose arm (a) to judge (default: --work)")
    args = ap.parse_args(argv)

    home = Path.home()
    args.corpus = (args.corpus or home / DEFAULT_CORPUS).expanduser()
    work = (args.work or home / DEFAULT_WORK).expanduser().resolve()
    real_vault = home / REAL_VAULT
    progress_path, labels_path = work / "progress.json", work / "labels.json"
    progress: Dict[str, Any] = _read(progress_path, {})
    all_labels: Dict[str, Dict[str, Dict[str, int]]] = _read(labels_path, {})
    labels = all_labels.setdefault(mrr.column(args.rater), {})

    if args.judge:
        return main_judge(args, work, labels_path, all_labels, labels)

    if not args.dry_run and not args.send:
        if not progress:
            print("no results in %s; --dry-run, then --send" % work, file=sys.stderr)
            return 1
        data = report(progress, labels, work)
        if args.json:
            print(json.dumps(data, indent=1, default=str))
        else:
            print("\n".join(report_lines(data)))
        return 0

    sessions = load_sessions(args.corpus)
    meta = progress.get("meta") or {}
    # The corpus is live: a session still running in that repo grows it. A
    # resumed run keeps the sessions its first run saw, in the same order.
    if meta.get("sids"):
        by_sid = {s.sid: s for s in sessions}
        sessions = [by_sid[sid] for sid in meta["sids"] if sid in by_sid]
    if not sessions:
        raise SystemExit("error: no transcripts in %s" % args.corpus)
    install_after = args.install_after if args.install_after is not None else meta.get(
        "install_after", len(sessions) // 2)
    if meta and meta.get("install_after") != install_after:
        raise SystemExit("error: %s was run with --install-after %s; use another --work"
                         % (work, meta.get("install_after")))
    history, future = sessions[:install_after], sessions[install_after:]
    cfg = config.load_config(missing_path=Path("/nonexistent/mnemo.config.json"))  # defaults: a new user's config
    plan = plan_b(sessions, min_interval_min=int(cfg["extraction"]["auto"]["minIntervalMinutes"]),
                  min_new=int(cfg["extraction"]["auto"]["minNewMemories"]),
                  min_mutations=1, chunk=int(cfg["extraction"]["chunkSize"]))
    est = estimate(sessions, history, future, plan, cfg, prompts=args.prompts,
                   max_sessions=args.max_sessions, budget=args.budget)
    run_ab = [x for x in ("a", "b") if args.arm in (x, "both", "all")]
    run_c = args.arm in ("c", "all")
    # The install point: the first session after install is the first one the
    # mirrored memory could reach. A rerun keeps the snapshot it first wrote.
    at = future[0].start if future else sessions[-1].end
    snap_info = progress.get("snapshot")
    snap = current = None
    if run_c and not snap_info:
        snap, current, snap_info = build_snapshot(args.corpus, at)
    est_c = None
    if run_c:
        bt = backfill_types(work)
        assumed = bt is None
        if assumed:
            bt = {"feedback": PAGES_PER_HARVEST * est["a"]["install_selected"]}
        est_c = estimate_c(snap_info["types"], bt, int(cfg["extraction"]["chunkSize"]), future, args.prompts)
        est_c["backfill_types"], est_c["backfill_assumed"] = bt, assumed

    if args.dry_run:
        ea, eb = est["a"], est["b"]
        memory_dir = args.corpus / "memory"
        print("corpus %s: %d sessions, %d prompts, %s to %s"
              % (args.corpus.name, len(sessions), sum(len(s.prompts) for s in sessions),
                 datetime.fromtimestamp(sessions[0].start).date(),
                 datetime.fromtimestamp(sessions[-1].start).date()))
        print("arm (a): %d session(s) of history; the install run harvests %d (cap %d, >= %d mutation); "
              "~%dk input tokens" % (ea["history"], ea["install_selected"], cfg["backfill"]["installCap"],
                                     cfg["backfill"]["minFileMutations"], ea["harvest_tokens_in"] // 1000))
        print("         calls: %d harvest + <= %d extraction (assumes %d pages per harvest) + <= %d label "
              "= <= %d, ~$%.2f" % (ea["harvest_calls"], ea["extract_calls_bound"], PAGES_PER_HARVEST,
                                   ea["label_calls_bound"], ea["calls"], ea["usd"]))
        print("arm (b): %d of %d session(s) fit the budget (%d prompts): %d briefing + <= %d extraction "
              "(%d run(s) on the transcript clock) + <= %d label = <= %d, ~$%.2f"
              % (eb["sessions_fit"], eb["of"], eb["prompts"], eb["briefing_calls"],
                 eb["extract_calls_bound"], eb["extractions"], eb["label_calls_bound"],
                 eb["calls"], eb["usd"]))
        print("total: <= %d model call(s) of a %d budget, ~$%.2f API-price equivalent "
              "(Max-plan usage, not money)" % (ea["calls"] + eb["calls"], args.budget,
                                                 ea["usd"] + eb["usd"]))
        if est_c is None:
            print("arm (c) not planned: %d Claude Code auto-memory file(s) in %s"
                  % (len(list(memory_dir.glob("*.md"))), memory_dir))
            return 0
        print("arm (c): %s as it stood at %s: %d of today's %d file(s) present (%s, from %d transcript "
              "record(s)); by type %s"
              % (memory_dir, snap_info["at_iso"], snap_info["present"], snap_info["today"],
                 snap_info["status"], snap_info["events"], snap_info["types"]))
        if snap_info["approximate"]:
            print("         approximate, today's text used: %s" % ", ".join(snap_info["approximate"]))
        print("         calls: <= %d (one extraction; project pages need no model) + labels; (a)+(c) with "
              "arm (a)'s %s harvested pages %s: <= %d; together <= %d of a %d budget"
              % (est_c["c"], sum(est_c["backfill_types"].values()),
                 "(assumed, arm (a) not run yet)" if est_c["backfill_assumed"] else est_c["backfill_types"],
                 est_c["ac"], est_c["calls"], args.budget_c))
        return 0

    if est_c is not None and est_c["calls"] > args.budget_c:
        raise SystemExit("error: arms (c) and (a)+(c) may need %d calls, over the %d budget"
                         % (est_c["calls"], args.budget_c))
    if "a" in run_ab and est["a"]["calls"] > args.budget:
        raise SystemExit("error: arm (a) alone may need %d calls, over the %d budget"
                         % (est["a"]["calls"], args.budget))
    agent = corpus_agent(args.corpus)
    calls = progress.get("calls") or {}
    meter = Meter(args.budget, spent=int(calls.get("total", 0)), usd=float(calls.get("usd", 0.0)))
    meter.by_model = dict(calls.get("by_model") or {})
    progress["meta"] = {**meta, "corpus": args.corpus.name, "sessions": len(sessions),
                        "prompts": sum(len(s.prompts) for s in sessions),
                        "install_after": install_after, "agent": agent, "rater": args.rater,
                        "sids": [s.sid for s in sessions],
                        "estimate": est}

    calls_c = progress.get("calls_c") or {}
    phase_c = {"on": False, "calls": int(calls_c.get("total", 0)), "usd": float(calls_c.get("usd", 0.0)),
               "at": 0, "usd_at": 0.0}

    def save() -> None:
        progress["calls"] = {"total": meter.calls, "usd": round(meter.usd, 4),
                             "by_model": meter.by_model, "budget": args.budget}
        if phase_c["on"]:
            progress["calls_c"] = {"total": phase_c["calls"] + meter.calls - phase_c["at"],
                                   "usd": round(phase_c["usd"] + meter.usd - phase_c["usd_at"], 4),
                                   "budget": args.budget_c}
        _write(progress_path, progress)
        _write(labels_path, all_labels)

    mirror_root = work / "memory-snapshot"
    if run_c and snap is not None:
        write_snapshot(snap, current, mirror_root / args.corpus.name / "memory", at)
        progress["snapshot"] = snap_info
        progress["snapshot_files"] = {n: st for n, (_t, st) in sorted(snap.items())}
        save()

    before = fingerprint(real_vault)
    with tempfile.TemporaryDirectory(prefix="mnemo-day-one-") as scratch, meter.installed():
        ctx = {"work": work, "corpus": args.corpus, "agent": agent, "cwd": scratch,
               "history": history, "future": future, "sessions": sessions, "plan_b": plan,
               "prompts": args.prompts, "meter": meter, "max_sessions": args.max_sessions,
               "cap_emissions": int((cfg.get("reflex") or {}).get("maxEmissionsPerSession", 10)),
               "mirror_root": mirror_root,
               "label_reserve_a": est["a_label_reserve"] if "a" in run_ab
               and not (progress.get("a") or {}).get("labelled") else 0}
        with mrr.mrl._chdir(scratch):
            if "a" in run_ab:
                run_arm_a(ctx, progress, save)
                with arm_vault(work / "arm-a" / "vault"):
                    label(progress["a"]["units"] + progress["a"]["first_turns"], labels, args.rater, save)
                progress["a"]["labelled"] = True
                ctx["label_reserve_a"] = 0
                save()
            if "b" in run_ab:
                run_arm_b(ctx, progress, save)
                with arm_vault(work / "arm-b" / "vault"):
                    label([u for r in progress["b"]["rows"] for u in r["units"]], labels,
                          args.rater, save)
            if run_c:
                # Its own budget: the meter stops at what arm (c) has left.
                saved_budget = meter.budget
                phase_c.update(on=True, at=meter.calls, usd_at=meter.usd)
                meter.budget = meter.calls + max(0, args.budget_c - phase_c["calls"])
                try:
                    for arm in MIRROR_ARMS:
                        backfill_from = None
                        if arm == "ac":
                            if not (progress.get("a") or {}).get("backfill"):
                                progress["ac"] = {"skipped": "arm (a) has not run; --arm a first"}
                                save()
                                continue
                            progress.get("ac", {}).pop("skipped", None)
                            backfill_from = work / "arm-a" / "vault"
                        state = progress.setdefault(arm, {})
                        vault = work / ("arm-" + arm) / "vault"
                        run_mirror_arm(ctx, state, save, vault, backfill_from)
                        with arm_vault(vault):
                            label(state["units"] + state["first_turns"], labels, args.rater, save)
                    save()
                finally:
                    meter.budget = saved_budget
    after = fingerprint(real_vault)
    diff = changed(before, after)
    progress["meta"]["real_vault"] = {
        "changed": len(diff), "paths": diff[:200],
        "suspects": suspects(diff, [args.corpus.name, agent + "/", "day-one", "mnemo-day-one"])}
    save()
    data = report(progress, labels, work)
    print("\n".join(report_lines(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
