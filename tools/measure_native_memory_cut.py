"""Memories Claude Code's native loading cuts off, and how often the reflex delivered them (#574)

Usage:
    PYTHONPATH=src python3 tools/measure_native_memory_cut.py [--vault ~/mnemo]
        [--projects ~/.claude/projects] [--since 2026-09-28T23:09:00Z]
        [--anonymize] [--json]

Read-only: no LLM calls, no writes. It changes nothing mnemo injects.

**Native loading.** Claude Code keeps a project's auto-memory in
``~/.claude/projects/<project>/memory/``: an index, ``MEMORY.md``, and one
topic file per memory. Its docs (code.claude.com/docs/en/memory, read
2026-10-07): "The first 200 lines of ``MEMORY.md``, or the first 25KB,
whichever comes first, are loaded at the start of every conversation";
topic files are not loaded at startup, Claude reads them on demand. The 25KB
is 25,000 *characters*, cut at a whole line (:data:`LINE_LIMIT`,
:data:`CHAR_LIMIT`, :func:`loaded_lines`): of the loads recorded in the
transcripts this tool reads (:func:`observed_cuts`), the 46 cut before line
201 on 2026-10-07 held 24,789-24,990 characters each and up to 25,776 bytes,
which no byte limit of 25KB allows. So a memory
whose index line sits past the cut, or that no index line links, never
reaches a session unless the model goes looking for it.

**Per project** (every memory directory holding a file): the index's lines,
characters and loaded lines; the topic files linked only from lines past the
cut; the topic files no index line links (:func:`native_index`). A project
whose transcripts all ran in a temp or job-scratch directory
(:func:`mnemo.core.hook_guard.is_throwaway`) is counted on one line, not
listed.

**What the reflex delivered.** Rows of ``reflex-log.jsonl`` (and its rotated
generation) written since :data:`LIVE`, when the reflex started injecting
whole rule bodies (#543), plus the rows #545's check keeps in its archive
(``full-body-fresh/reflex-rows.jsonl``) once the log has rotated them away.
Each emitted slug is one injection. The mapping back to an auto-memory file is
the one mnemo itself records, never a loose name match:

1. the slug's entry in ``reflex-index.json`` gives its page (``path``);
2. the page's ``sources:`` frontmatter lists the files it was built from,
   vault-relative (:func:`mnemo.core.extract.source_paths.vault_relative_source`);
3. a source ``bots/<agent>/memory/<file>.md`` is that agent's auto-memory
   file, mirrored by :func:`mnemo.core.mirror.mirror_all` from every
   ``~/.claude/projects/<dir>/memory/`` whose
   :func:`mnemo.core.mirror._agent_from_project_dir` is ``<agent>``.

A source under ``bots/<agent>/briefings/`` is a session briefing, not a
memory; a rule whose sources are all briefings (or none) maps to no memory.
Nor does a file mnemo's backfill reconstructed from old transcripts into
the same ``bots/<agent>/memory/`` (:mod:`mnemo.core.backfill.harvest`): it
carries ``origin: backfill`` (:func:`mnemo.core.backfill.origin.is_backfill_frontmatter`)
and was never in Claude Code's directory, so a rule built only from such
files is counted apart (``backfill memory only``), not as cut off.

**Could native loading have shown it?** Read per session, from the session
itself: Claude Code writes what it loaded into the transcript, an
``instructions`` attachment whose ``files`` hold ``MEMORY.md``'s loaded text
(``type: AutoMem``) with the cut warning after it. The injection is judged
against the last such record written before it (:func:`session_reading`):

- ``shown`` — a source file is linked from the loaded text;
- ``written this session`` — not linked, but the session wrote a source
  file itself (``Write``/``Edit`` on ``…/projects/<dir>/memory/<file>``)
  before the injection, so it was in front of the model already;
- ``other project`` — no source is in an auto-memory directory this session
  loaded, so its native loading never looks there;
- ``not shown`` — the session loaded this project's index and no line of it
  links a source;
- ``nothing loaded`` — the transcript holds no ``AutoMem`` record;
- ``no transcript`` — the session's transcript is gone.

Native loading shows an index *line*, never a memory's body; ``shown``
counts the line. Every injection not ``shown`` is also placed by today's
index (:func:`today_state`): ``past the cut``, ``unindexed``, ``before the
cut`` (the index changed since), ``gone`` (no such file now) — today's index
is all there is for the lines a session did not load.

Sessions are split into human and background: a background job is a
transcript whose first main-thread user record has ``sessionKind: "bg"``.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnemo.core import mirror  # noqa: E402
from mnemo.core.backfill.origin import is_backfill_frontmatter  # noqa: E402
from mnemo.core.filters import parse_frontmatter  # noqa: E402
from mnemo.core.hook_guard import is_throwaway  # noqa: E402
from mnemo.core.log_utils import iter_rotated_rows  # noqa: E402

#: When the full-body reflex went live on the maintainer's machine (#543,
#: ``measure_full_body_fresh.LIVE``).
LIVE = "2026-09-28T23:09:00Z"
#: Claude Code's load limits for ``MEMORY.md``: 200 lines or 25KB, whichever
#: comes first; the 25KB measured as characters (see the module docstring).
LINE_LIMIT = 200
CHAR_LIMIT = 25_000
INDEX = "MEMORY.md"
#: #545's archive of the reflex rows the log has since rotated away.
ARCHIVE = Path("full-body-fresh") / "reflex-rows.jsonl"
#: The line Claude Code appends under a cut index.
_WARNING = "> WARNING: MEMORY.md is "
_LINK = re.compile(r"\]\(([^)\s]+)\)")
_MEMORY_SOURCE = re.compile(r"^bots/([^/]+)/memory/([^/]+\.md)$")

HUMAN, BG, UNKNOWN = "human", "bg", "unknown"
KINDS = (HUMAN, BG, UNKNOWN)
SHOWN, WRITTEN, OTHER, NOT_SHOWN, NOTHING, NO_TRANSCRIPT = (
    "shown", "written this session", "other project", "not shown", "nothing loaded", "no transcript")
READINGS = (SHOWN, WRITTEN, NOT_SHOWN, OTHER, NOTHING, NO_TRANSCRIPT)
#: Readings in which native loading could not have shown the memory.
UNSEEN = (NOT_SHOWN, OTHER, NOTHING)
BEFORE, PAST, UNINDEXED, GONE = "before the cut", "past the cut", "unindexed", "gone"
TODAY = (BEFORE, PAST, UNINDEXED, GONE)
NO_INDEX_ENTRY, NO_PAGE, NO_MEMORY = "not in reflex index", "page missing", "no memory source"
BACKFILL_ONLY, SOURCE_MISSING = "backfill memory only", "source file missing"

AgentOf = Callable[[str], str]


# --- the native index ----------------------------------------------------------------------

def loaded_lines(text: str, *, line_limit: int = LINE_LIMIT, char_limit: int = CHAR_LIMIT) -> int:
    """How many whole lines of an index Claude Code loads: the most lines,
    at most ``line_limit``, whose text joined by newlines is at most
    ``char_limit`` characters."""
    lines = text.splitlines()
    size = -1
    for n, line in enumerate(lines[:line_limit], 1):
        size += len(line) + 1
        if size > char_limit:
            return n - 1
    return min(len(lines), line_limit)


def _target(link: str) -> Optional[str]:
    """A link's topic-file name, relative to the memory directory; None for
    a URL, an anchor or a file outside the directory."""
    link = link.split("#", 1)[0]
    if not link or "://" in link or link.startswith(("/", "mailto:")):
        return None
    norm = posixpath.normpath(link)
    if norm.startswith("../") or norm == "..":
        return None
    return norm


def index_links(text: str) -> List[Tuple[int, str]]:
    """``(line number, file)`` for every link in an index, 1-based. Claude
    Code's own cut warning is not part of the index."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        if line.startswith(_WARNING):
            continue
        for raw in _LINK.findall(line):
            name = _target(raw)
            if name:
                out.append((n, name))
    return out


def linked_names(text: str) -> Set[str]:
    return {name for _, name in index_links(text)}


def topic_files(memory_dir: Path) -> Set[str]:
    """The memory directory's topic files: its ``*.md`` but the index, the
    ones mnemo's scanner promotes."""
    try:
        return {p.name for p in memory_dir.glob("*.md") if p.is_file() and p.name != INDEX}
    except OSError:
        return set()


def native_index(memory_dir: Path) -> Dict[str, Any]:
    """One memory directory as native loading sees it today."""
    files = topic_files(memory_dir)
    try:
        text = (memory_dir / INDEX).read_text(encoding="utf-8", errors="replace")
        has_index = True
    except OSError:
        text, has_index = "", False
    cut = loaded_lines(text)
    before: Set[str] = set()
    after: Set[str] = set()
    for n, name in index_links(text):
        (before if n <= cut else after).add(name)
    linked = before | after
    lines = text.splitlines()
    return {
        "has_index": has_index,
        "lines": len(lines),
        "chars": len("\n".join(lines)),
        "bytes": len(text.encode("utf-8")),
        "loaded_lines": cut,
        "cut": cut < len(lines),
        "files": sorted(files),
        "before": sorted(before & files),
        "past": sorted((after - before) & files),
        "unindexed": sorted(files - linked),
        "broken": sorted(linked - files),
    }


def today_state(name: str, indexes: Sequence[Dict[str, Any]]) -> str:
    """Where a topic file sits in today's indexes of its agent: the best
    placement across them."""
    states = set()
    for ix in indexes:
        if name in ix["before"]:
            states.add(BEFORE)
        elif name in ix["past"]:
            states.add(PAST)
        elif name in ix["unindexed"]:
            states.add(UNINDEXED)
    for state in (BEFORE, PAST, UNINDEXED):
        if state in states:
            return state
    return GONE


# --- the vault's mapping -------------------------------------------------------------------

def memory_sources(page_text: str) -> List[Tuple[str, str]]:
    """``(agent, file)`` for each auto-memory file a page's ``sources:`` lists."""
    raw = parse_frontmatter(page_text).get("sources") or []
    if isinstance(raw, str):
        raw = [raw]
    out = []
    for src in raw:
        m = _MEMORY_SOURCE.match(str(src).strip().replace("\\", "/"))
        if m and m.group(2) != INDEX:
            out.append((m.group(1), m.group(2)))
    return out


def source_origin(vault: Path, agent: str, name: str) -> str:
    """Whether the vault's copy of a memory source is Claude Code's auto-memory
    (``auto``), a file mnemo's backfill reconstructed from old transcripts
    into the same directory (``backfill``), or gone (``missing``)."""
    try:
        text = (vault / "bots" / agent / "memory" / name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "missing"
    return "backfill" if is_backfill_frontmatter(parse_frontmatter(text)) else "auto"


def rule_sources(slug: str, docs: Dict[str, Any], vault: Path,
                 cache: Dict[str, Tuple[str, List[Tuple[str, str]]]]) -> Tuple[str, List[Tuple[str, str]]]:
    """A slug's auto-memory sources, or why it has none: ``(reason, sources)``
    with ``reason`` empty when it maps."""
    if slug in cache:
        return cache[slug]
    doc = docs.get(slug)
    if not isinstance(doc, dict) or not doc.get("path"):
        got: Tuple[str, List[Tuple[str, str]]] = (NO_INDEX_ENTRY, [])
    else:
        try:
            text = (vault / str(doc["path"])).read_text(encoding="utf-8", errors="replace")
        except OSError:
            got = (NO_PAGE, [])
        else:
            found = memory_sources(text)
            origins = {src: source_origin(vault, *src) for src in found}
            auto = [src for src in found if origins[src] == "auto"]
            if auto:
                got = ("", auto)
            elif not found:
                got = (NO_MEMORY, [])
            elif "backfill" in origins.values():
                got = (BACKFILL_ONLY, [])
            else:
                got = (SOURCE_MISSING, [])
    cache[slug] = got
    return got


# --- the reflex log ------------------------------------------------------------------------

def _epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except ValueError:
        return None


def _row_key(row: Dict[str, Any]) -> str:
    return json.dumps([row.get("session_id"), row.get("prompt_hash"), row.get("ts"), row.get("emitted")])


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def injecting_rows(rows: Iterable[Dict[str, Any]], since: str = LIVE) -> List[Dict[str, Any]]:
    """Rows that emitted a rule at or after ``since``, each once, oldest first."""
    start = _epoch(since)
    seen: Set[str] = set()
    out = []
    for row in rows:
        if not isinstance(row, dict) or not row.get("emitted") or not row.get("session_id"):
            continue
        at = _epoch(row.get("ts"))
        if at is None or (start is not None and at < start):
            continue
        key = _row_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return sorted(out, key=lambda r: _epoch(r.get("ts")) or 0.0)


def load_rows(vault: Path, since: str = LIVE) -> List[Dict[str, Any]]:
    mnemo_dir = vault / ".mnemo"
    rows = list(iter_rotated_rows(mnemo_dir / "reflex-log.jsonl")) + _read_jsonl(mnemo_dir / ARCHIVE)
    return injecting_rows(rows, since)


# --- the transcripts -----------------------------------------------------------------------

def _automem_files(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    att = record.get("attachment")
    if not isinstance(att, dict):
        return []
    files = att.get("files")
    if not isinstance(files, list):
        return []
    return [f for f in files if isinstance(f, dict) and f.get("type") == "AutoMem"
            and isinstance(f.get("content"), str)]


#: The tools a session writes a memory file with.
_WRITERS = frozenset({"Write", "Edit", "MultiEdit"})


def _memory_file(path: Any) -> Optional[Tuple[str, str]]:
    """``(project dir, file)`` for a path ``.../projects/<dir>/memory/<file>``."""
    parts = str(path or "").replace("\\", "/").split("/")
    if len(parts) >= 4 and parts[-2] == "memory" and parts[-4] == "projects":
        return parts[-3], parts[-1]
    return None


def read_session(path: Path) -> Dict[str, Any]:
    """A transcript's kind, every ``AutoMem`` load it recorded and every
    memory file it wrote itself: ``{"kind", "loads": [{"at", "dir",
    "content"}], "writes": [{"at", "dir", "name"}]}``."""
    kind = None
    loads: List[Dict[str, Any]] = []
    writes: List[Dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if (kind is not None and "AutoMem" not in line
                        and not ("/memory/" in line and "tool_use" in line)):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict):
                    continue
                if (kind is None and record.get("type") == "user"
                        and not record.get("isSidechain")):
                    kind = BG if record.get("sessionKind") == "bg" else HUMAN
                for f in _automem_files(record):
                    # .../projects/<dir>/memory/MEMORY.md, written on any OS
                    parts = str(f.get("path") or "").replace("\\", "/").split("/")
                    loads.append({
                        "at": _epoch(record.get("timestamp")),
                        "dir": parts[-3] if len(parts) >= 3 else "",
                        "content": f["content"],
                    })
                if record.get("type") == "assistant":
                    content = (record.get("message") or {}).get("content")
                    for block in content if isinstance(content, list) else []:
                        if (isinstance(block, dict) and block.get("type") == "tool_use"
                                and block.get("name") in _WRITERS):
                            target = _memory_file((block.get("input") or {}).get("file_path"))
                            if target:
                                writes.append({"at": _epoch(record.get("timestamp")),
                                               "dir": target[0], "name": target[1]})
    except OSError:
        pass
    return {"kind": kind or UNKNOWN, "loads": loads, "writes": writes}


def loads_at(loads: Sequence[Dict[str, Any]], at: Optional[float]) -> List[Dict[str, Any]]:
    """The ``AutoMem`` records in force at ``at``: those of the last load
    written by then, or the first load when none was."""
    if not loads:
        return []
    stamps = sorted({ld["at"] for ld in loads if ld["at"] is not None})
    pick = None
    for stamp in stamps:
        if at is None or stamp <= at:
            pick = stamp
    if pick is None:
        pick = stamps[0] if stamps else None
    return [ld for ld in loads if ld["at"] == pick]


def session_reading(sources: Sequence[Tuple[str, str]], session: Optional[Dict[str, Any]],
                    at: Optional[float], agent_of: AgentOf) -> str:
    """Whether a session had a rule's memory files in front of it at ``at``:
    linked from the index it loaded, or written by the session itself."""
    if session is None:
        return NO_TRANSCRIPT
    current = loads_at(session["loads"], at)
    shown: Set[Tuple[str, str]] = set()
    agents: Set[str] = set()
    for ld in current:
        agent = agent_of(ld["dir"])
        agents.add(agent)
        shown |= {(agent, name) for name in linked_names(ld["content"])}
    if any(src in shown for src in sources):
        return SHOWN
    written = {(agent_of(w["dir"]), w["name"]) for w in session.get("writes", ())
               if at is None or w["at"] is None or w["at"] <= at}
    if any(src in written for src in sources):
        return WRITTEN
    if not current:
        return NOTHING
    if not any(agent in agents for agent, _ in sources):
        return OTHER
    return NOT_SHOWN


def observed_cuts(sessions: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """What the recorded loads say about the limit: how many were cut, at
    which line, and the size of the loaded text of those cut before the line
    limit. Every such load at most :data:`CHAR_LIMIT` characters, with some
    over 25,600 bytes, is a character limit and not a byte one."""
    cut_at: Counter = Counter()
    chars: List[int] = []
    sizes: List[int] = []
    total = 0
    for s in sessions:
        for ld in s["loads"]:
            total += 1
            text = ld["content"]
            warn = text.find(_WARNING)
            if warn < 0:
                continue
            m = re.search(r"starting at line (\d+)", text[warn:])
            if not m:
                continue
            line = int(m.group(1))
            cut_at[line] += 1
            if line <= LINE_LIMIT:
                body = "\n".join(text[:warn].rstrip("\n").splitlines())
                chars.append(len(body))
                sizes.append(len(body.encode("utf-8")))
    return {"loads": total, "cut": sum(cut_at.values()),
            "cut_at_line": {str(k): v for k, v in sorted(cut_at.items())},
            "char_cuts": len(chars),
            "char_cut_chars": [min(chars), max(chars)] if chars else None,
            "char_cut_max_bytes": max(sizes) if sizes else None}


# --- the report ----------------------------------------------------------------------------

def _first_cwd(project_dir: Path) -> Optional[str]:
    for path in sorted(project_dir.glob("*.jsonl")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for _, line in zip(range(50), fh):
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict) and record.get("cwd"):
                        return str(record["cwd"])
        except OSError:
            continue
    return None


def scan_projects(projects: Path, agent_of: AgentOf) -> List[Dict[str, Any]]:
    """Every project directory whose ``memory/`` holds a file."""
    out = []
    try:
        dirs = sorted(p for p in projects.iterdir() if p.is_dir())
    except OSError:
        return out
    for d in dirs:
        mem = d / "memory"
        if not mem.is_dir() or not mirror._has_files(mem):
            continue
        cwd = _first_cwd(d)
        out.append({"dir": d.name, "agent": agent_of(d.name),
                    "throwaway": bool(cwd) and is_throwaway(cwd), **native_index(mem)})
    out.sort(key=lambda p: (p["throwaway"], -p["lines"], -len(p["files"]), p["dir"]))
    return out


def gather(vault: Path, projects: Path, *, since: str = LIVE,
           agent_of: Optional[AgentOf] = None) -> Dict[str, Any]:
    agent_of = agent_of or _cached(mirror._agent_from_project_dir)
    native = scan_projects(projects, agent_of)
    by_agent: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for p in native:
        by_agent[p["agent"]].append(p)

    rows = load_rows(vault, since)
    try:
        docs = json.loads((vault / ".mnemo" / "reflex-index.json").read_text(encoding="utf-8")).get("docs") or {}
    except (OSError, ValueError, AttributeError):
        docs = {}
    transcripts = {p.stem: p for p in projects.glob("*/*.jsonl")}
    sessions: Dict[str, Optional[Dict[str, Any]]] = {}
    cache: Dict[str, Tuple[str, List[Tuple[str, str]]]] = {}
    injections = []
    for row in rows:
        sid = str(row["session_id"])
        if sid not in sessions:
            path = transcripts.get(sid)
            sessions[sid] = read_session(path) if path is not None else None
        session = sessions[sid]
        at = _epoch(row.get("ts"))
        for slug in row["emitted"]:
            slug = str(slug)
            reason, sources = rule_sources(slug, docs, vault, cache)
            inj: Dict[str, Any] = {
                "session_id": sid, "ts": row.get("ts"), "project": row.get("project"),
                "kind": session["kind"] if session else UNKNOWN, "slug": slug,
                "sources": ["/".join(s) for s in sources], "unmapped": reason,
            }
            if not reason:
                inj["reading"] = session_reading(sources, session, at, agent_of)
                if inj["reading"] != SHOWN:
                    states = [today_state(name, by_agent.get(agent, [])) for agent, name in sources]
                    inj["today"] = next((s for s in TODAY if s in states), GONE)
            injections.append(inj)
    return {
        "since": since,
        "limits": {"lines": LINE_LIMIT, "chars": CHAR_LIMIT},
        "native": native,
        "observed": observed_cuts(s for s in sessions.values() if s),
        "rows": len(rows),
        "sessions": {k: sum(1 for s in sessions.values() if (s["kind"] if s else UNKNOWN) == k)
                     for k in KINDS},
        "injections": injections,
    }


def _cached(fn: AgentOf) -> AgentOf:
    memo: Dict[str, str] = {}

    def wrapped(name: str) -> str:
        if name not in memo:
            memo[name] = fn(name)
        return memo[name]
    return wrapped


def summarize(injections: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Per kind and in all: injections and distinct rules, mapped or not,
    each reading, and today's placement of the unseen."""
    out: Dict[str, Any] = {}
    for kind in ("all",) + KINDS:
        part = [i for i in injections if kind == "all" or i["kind"] == kind]
        mapped = [i for i in part if not i["unmapped"]]
        unseen = [i for i in mapped if i.get("reading") in UNSEEN]
        out[kind] = {
            "sessions": len({i["session_id"] for i in part}),
            "injections": len(part),
            "rules": len({i["slug"] for i in part}),
            "mapped": len(mapped),
            "mapped_rules": len({i["slug"] for i in mapped}),
            "unmapped": dict(Counter(i["unmapped"] for i in part if i["unmapped"])),
            "readings": {r: sum(1 for i in mapped if i["reading"] == r) for r in READINGS},
            "unseen": len(unseen),
            "unseen_rules": len({i["slug"] for i in unseen}),
            "unseen_sessions": len({i["session_id"] for i in unseen}),
            "unseen_today": {s: sum(1 for i in unseen if i.get("today") == s) for s in TODAY},
            "not_shown_today": {s: sum(1 for i in mapped if i["reading"] == NOT_SHOWN and i.get("today") == s)
                                for s in TODAY},
        }
    return out


def labels(native: Sequence[Dict[str, Any]], anonymize: bool) -> Dict[str, str]:
    """A name per project directory: its own, or ``project N`` in report order."""
    return {p["dir"]: (f"project {n}" if anonymize else p["dir"])
            for n, p in enumerate((p for p in native if not p["throwaway"]), 1)}


def _pct(part: int, whole: int) -> str:
    return f"{100.0 * part / whole:.1f}%" if whole else "-"


def render(report: Dict[str, Any], *, anonymize: bool = False) -> str:
    native = report["native"]
    names = labels(native, anonymize)
    out = [f"Claude Code loads the first {report['limits']['lines']} lines of MEMORY.md "
           f"or {report['limits']['chars']:,} characters, whichever comes first.", "",
           "Native auto-memory, today:"]
    out.append(f"  {'project':<28} {'lines':>5} {'loaded':>6} {'chars':>7} {'files':>5} "
               f"{'past cut':>8} {'unindexed':>9} {'broken':>6}")
    for p in native:
        if p["throwaway"]:
            continue
        loaded = p["loaded_lines"] if p["has_index"] else "-"
        out.append(f"  {names[p['dir']][:28]:<28} {p['lines']:>5} {loaded!s:>6} {p['chars']:>7} "
                   f"{len(p['files']):>5} {len(p['past']):>8} {len(p['unindexed']):>9} {len(p['broken']):>6}")
    junk = [p for p in native if p["throwaway"]]
    if junk:
        out.append(f"  + {len(junk)} temp/job-scratch directories: {sum(len(p['files']) for p in junk)} files, "
                   f"{sum(1 for p in junk if p['cut'])} cut")
    real = [p for p in native if not p["throwaway"]]
    out.append(f"  {sum(1 for p in real if p['cut'])} of {len(real)} indexes cut; "
               f"{sum(len(p['past']) for p in real)} of {sum(len(p['files']) for p in real)} topic files "
               f"past the cut, {sum(len(p['unindexed']) for p in real)} unindexed")

    obs = report["observed"]
    out += ["", f"Loads recorded in the measured sessions' transcripts: {obs['loads']}, "
                f"{obs['cut']} cut (by the line they cut at: {obs['cut_at_line'] or '-'})"]
    if obs["char_cuts"]:
        lo, hi = obs["char_cut_chars"]
        out.append(f"  {obs['char_cuts']} cut before line {report['limits']['lines'] + 1}: loaded text "
                   f"{lo:,}-{hi:,} characters, up to {obs['char_cut_max_bytes']:,} bytes")

    s = summarize(report["injections"])
    out += ["", f"Reflex injections since {report['since']}: {report['rows']} rows, "
                f"sessions human {report['sessions'][HUMAN]}, bg {report['sessions'][BG]}, "
                f"no transcript {report['sessions'][UNKNOWN]}"]
    for kind in ("all", HUMAN, BG, UNKNOWN):
        k = s[kind]
        if not k["injections"]:
            continue
        out += ["", f"  {kind}: {k['injections']} injections of {k['rules']} rules in {k['sessions']} sessions",
                f"    from an auto-memory file: {k['mapped']} ({_pct(k['mapped'], k['injections'])}), "
                f"{k['mapped_rules']} rules; not: {k['unmapped'] or '-'}"]
        for r in READINGS:
            if k["readings"][r]:
                out.append(f"      {r:<21} {k['readings'][r]:>5} ({_pct(k['readings'][r], k['mapped'])})")
        out.append(f"    native loading could not have shown: {k['unseen']} "
                   f"({_pct(k['unseen'], k['mapped'])} of mapped), {k['unseen_rules']} rules, "
                   f"{k['unseen_sessions']} sessions")
        out.append("      by today's index: " + ", ".join(f"{st} {n}" for st, n in k["unseen_today"].items()))
        out.append("      'not shown' alone: " + ", ".join(f"{st} {n}" for st, n in k["not_shown_today"].items()))
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--vault", default=None, help="the vault (default: mnemo's configured one)")
    ap.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument("--since", default=LIVE)
    ap.add_argument("--anonymize", action="store_true", help="name projects 'project 1..n'")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.vault:
        vault = Path(os.path.expanduser(args.vault))
    else:
        from mnemo.core import config, paths
        vault = paths.vault_root(config.load_config())
    report = gather(vault, Path(args.projects), since=args.since)
    if args.json:
        report["summary"] = summarize(report["injections"])
        if args.anonymize:
            # Counts only: directory, agent, file and slug names can each
            # name a private project.
            names = labels(report["native"], True)
            report["native"] = [
                {k: (len(v) if isinstance(v, list) else v) for k, v in p.items() if k != "agent"}
                for p in (dict(p, dir=names.get(p["dir"], "temp")) for p in report["native"])]
            del report["injections"]
        print(json.dumps(report, indent=1, ensure_ascii=False))
    else:
        print(render(report, anonymize=args.anonymize))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
