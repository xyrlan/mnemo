"""Which auto-memories does Claude Code's own loading cut off, and how often did mnemo's reflex deliver them? (#574)

Usage:
    PYTHONPATH=src python3 tools/measure_native_memory_cut.py [--since ISO] [--json] [--names]
        [--projects-root DIR] [--vault DIR]

Claude Code loads only the head of a project's auto-memory index,
``~/.claude/projects/<project>/memory/MEMORY.md``. Its docs
(code.claude.com/docs/en/memory, read 2026-10-07): "The first 200 lines of
``MEMORY.md``, or the first 25KB, whichever comes first, are loaded at the
start of every conversation." Topic files are never loaded at start; a
model reads them only if it goes looking, and it only knows to look for the
ones whose index line it was shown.

**The limit, measured.** "25KB" is 25,000 characters of whole lines
(:data:`LIMIT_CHARS`), not 25,600 bytes. On 2026-10-07 this repo's index
(252 lines, 34,766 bytes) loaded 198 lines: 24,885 characters but 25,693
bytes; the 199th line would have made 25,010 characters. Every run checks
the model against every transcript's recorded load (the *model check*
line): a load whose lines are still the head of today's file, with the same
line count, should cut where :func:`loaded_count` says.

**What each session loaded.** A transcript records what Claude Code loaded
at start: an ``instructions`` attachment whose ``files`` hold one
``type: "AutoMem"`` entry, with the ``MEMORY.md`` path and its loaded lines,
followed by Claude Code's ``> WARNING: MEMORY.md is …`` paragraph when it
cut. So whether a session's index showed a memory is read from that
session's own record, not from today's file.

**The mapping, from rule to memory file.** ``core/mirror.py`` copies each
``~/.claude/projects/<dir>/memory/`` into ``<vault>/bots/<agent>/memory/``,
with ``agent = mirror._agent_from_project_dir(dir)``. Extraction records
where a page came from in its frontmatter ``sources:``, as vault-relative
paths (a few older ones absolute under the vault): an auto-memory origin is
``bots/<agent>/memory/<file>.md``. A reflex row's ``emitted`` slugs are
looked up the way the reflex index does (``shared/{feedback,user,reference,
project}/*.md``, ``derive_rule_slug``), and each ``sources:`` entry of that
shape is a memory source ``(agent, file)``. No name is matched loosely.

**Each injected rule, in the session it was injected into**, takes the best
of its memory sources, in this order:

- ``shown`` — the file is linked from an index line the session loaded;
- ``past_cut`` — the session's index was cut and today's ``MEMORY.md``
  links the file past the cut;
- ``unindexed`` — the file is in the session's memory directory and its
  index does not link it (when the session loaded the whole index this is
  exact; when it was cut, read from today's file);
- ``removed`` — the page's source is this project's memory, but the file
  is not in the directory the session loaded today: renamed, merged or
  deleted from the native directory (the mirror never deletes its copy), or
  kept in another directory of the same project. Its index line was not
  among the loaded lines either way; whether it was cut or unlinked at the
  time is unknown;
- ``other_project`` — every memory source is another project's: the
  session loaded a different directory, which never listed the file.

``past_cut + unindexed + removed + other_project`` is what native loading
could not have shown. That sum is read from each session's own record;
only the split between its parts leans on today's files. A rule with no memory source is ``not_memory`` (its sources are
session briefings), ``no_source`` (no ``sources:`` at all) or ``no_page``
(the slug has no page today). A session whose transcript is gone, or that
holds no ``AutoMem`` record, is ``unknown`` and counted apart.

**Rows** are ``reflex-log.jsonl``, its rotated ``.1`` and #545's archive
(``full-body-fresh/reflex-rows.jsonl``), merged, since :data:`SINCE`: when
the reflex started injecting whole rule bodies (#543). A unit is one
emitted slug in one row; distinct ``(session, slug)`` pairs are reported
beside it. A background job is a transcript whose first main-thread user
record has ``sessionKind: "bg"``; every other session is human.

Read-only: nothing is written, nothing mnemo injects changes, no model is
called. Project names are printed as "project 1…n" unless ``--names``.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

#: When the reflex started writing whole rule bodies (#543, ``measure_full_body_fresh.LIVE``).
SINCE = "2026-09-28T23:09:00Z"
#: Claude Code's load limits for ``MEMORY.md``: whole lines, whichever comes first.
LIMIT_LINES = 200
LIMIT_CHARS = 25_000
INDEX = "MEMORY.md"
PAGE_TYPES = ("feedback", "user", "reference", "project")
LOGS = ("reflex-log.jsonl.1", "reflex-log.jsonl")
ARCHIVE = Path("full-body-fresh") / "reflex-rows.jsonl"

SHOWN, PAST_CUT, UNINDEXED = "shown", "past_cut", "unindexed"
REMOVED, OTHER_PROJECT = "removed", "other_project"
NOT_MEMORY, NO_SOURCE, NO_PAGE, UNKNOWN = "not_memory", "no_source", "no_page", "unknown"
#: Best first: a rule with several memory sources takes the first that holds.
PRECEDENCE = (SHOWN, PAST_CUT, UNINDEXED, REMOVED, OTHER_PROJECT)
HIDDEN = PRECEDENCE[1:]
STATUSES = PRECEDENCE + (UNKNOWN, NOT_MEMORY, NO_SOURCE, NO_PAGE)
HUMAN, BG = "human", "bg"
WRITE_TOOLS = ("Write", "Edit", "MultiEdit")

WARNING = "\n\n> WARNING: MEMORY.md is "
_LINK = re.compile(r"\]\(\s*<?([^)<>\s]+)>?(?:\s+\"[^\"]*\")?\s*\)")
_MEMORY_SOURCE = re.compile(r"^bots/([^/]+)/memory/(.+\.md)$")


# --- the index -----------------------------------------------------------------------------

def _chars(text: str) -> int:
    """Length as Claude Code counts it: UTF-16 code units."""
    return len(text.encode("utf-16-le")) // 2


def split_lines(text: str) -> List[str]:
    """The file's lines as an editor numbers them (a final newline ends the last)."""
    if not text:
        return []
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return lines


def loaded_count(lines: Sequence[str], max_lines: int = LIMIT_LINES, max_chars: int = LIMIT_CHARS) -> int:
    """How many whole lines Claude Code loads: at most ``max_lines``, and no
    more than fit in ``max_chars`` characters, newlines between them counted."""
    used = 0
    for n, line in enumerate(lines[:max_lines]):
        used += _chars(line) + (1 if n else 0)
        if used > max_chars:
            return n
    return min(len(lines), max_lines)


def links(lines: Iterable[str]) -> List[str]:
    """Relative ``.md`` targets of the markdown links on ``lines``, normalized, in order."""
    out: List[str] = []
    for line in lines:
        for target in _LINK.findall(line):
            target = target.split("#", 1)[0]
            if not target.endswith(".md") or "://" in target or target.startswith(("/", "~")):
                continue
            norm = posixpath.normpath(target)
            if not norm.startswith("..") and norm not in out:
                out.append(norm)
    return out


def memory_files(memdir: Path) -> List[str]:
    """Every topic file under ``memdir``, relative, posix; the index itself excluded."""
    out = []
    for path in sorted(memdir.rglob("*.md")):
        rel = path.relative_to(memdir).as_posix()
        if rel != INDEX and path.is_file():
            out.append(rel)
    return out


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def project_cut(memdir: Path) -> Dict[str, Any]:
    """One auto-memory directory as Claude Code would load it today."""
    text = _read(memdir / INDEX)
    lines = split_lines(text or "")
    loaded = loaded_count(lines)
    shown = links(lines[:loaded])
    indexed = links(lines)
    files = memory_files(memdir)
    present = set(files)
    past = [rel for rel in indexed if rel not in shown]
    return {
        "has_index": text is not None,
        "lines": len(lines),
        "chars": _chars(text or ""),
        "bytes": len((text or "").encode("utf-8")),
        "loaded_lines": loaded,
        "cut_at": loaded + 1 if loaded < len(lines) else None,
        "cut_by": (None if loaded >= len(lines) else "lines" if loaded == LIMIT_LINES else "chars"),
        "entries": len(indexed),
        "entries_past_cut": len(past),
        "files_past_cut": len([rel for rel in past if rel in present]),
        "files": len(files),
        "unindexed": len([rel for rel in files if rel not in set(indexed)]),
        "dead_links": len([rel for rel in indexed if rel not in present]),
    }


# --- the transcripts -----------------------------------------------------------------------

def loaded_index(content: str) -> Tuple[List[str], Optional[int]]:
    """A recorded ``AutoMem`` entry's loaded lines, and the file's line count
    when Claude Code's cut warning names it (``None`` when it did not cut)."""
    at = content.find(WARNING)
    if at < 0:
        return split_lines(content), None
    m = re.match(r"(\d+) lines", content[at + len(WARNING):])
    return split_lines(content[:at]), (int(m.group(1)) if m else -1)


def _records(path: Path) -> Iterator[Dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def read_session(path: Path) -> Dict[str, Any]:
    """What a transcript says about its start: kind, and the index it loaded.

    Reads until the first main-thread user record and the first ``AutoMem``
    record are both seen; Claude Code writes the load before the prompt, so
    a session that has none by its first reply is read no further.
    """
    kind: Optional[str] = None
    memory: Optional[Dict[str, Any]] = None
    for record in _records(path):
        attachment = record.get("attachment")
        if memory is None and isinstance(attachment, dict) and attachment.get("type") == "instructions":
            for entry in attachment.get("files") or []:
                if isinstance(entry, dict) and entry.get("type") == "AutoMem" \
                        and str(entry.get("path") or "").endswith(INDEX):
                    lines, total = loaded_index(str(entry.get("content") or ""))
                    memory = {"memdir": str(Path(entry["path"]).parent), "lines": lines, "total": total}
                    break
        if record.get("isSidechain"):
            continue
        if kind is None and record.get("type") == "user":
            kind = BG if record.get("sessionKind") == "bg" else HUMAN
        if kind is not None and (memory is not None or record.get("type") == "assistant"):
            break
    return {"kind": kind or HUMAN, "memory": memory}


def memory_writes(path: Path) -> Dict[str, str]:
    """``…/memory/<file>`` paths this session wrote or edited → first time."""
    out: Dict[str, str] = {}
    for record in _records(path):
        content = (record.get("message") or {}).get("content") if record.get("type") == "assistant" else None
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict) or block.get("type") != "tool_use" \
                    or block.get("name") not in WRITE_TOOLS:
                continue
            target = str((block.get("input") or {}).get("file_path") or "").replace("\\", "/")
            if "/memory/" in target:
                out.setdefault(target, str(record.get("timestamp") or ""))
    return out


def transcripts(projects_root: Path) -> Dict[str, Path]:
    """Session id → main transcript, across every project directory."""
    out: Dict[str, Path] = {}
    for path in projects_root.glob("*/*.jsonl"):
        out.setdefault(path.stem, path)
    return out


# --- the vault -----------------------------------------------------------------------------

def vault_sources(vault: Path) -> Dict[str, List[str]]:
    """Slug → its page's ``sources:``, for every page the reflex index walks."""
    from mnemo.core.filters import derive_rule_slug, parse_frontmatter

    out: Dict[str, List[str]] = {}
    for page_type in PAGE_TYPES:
        type_dir = vault / "shared" / page_type
        if not type_dir.is_dir():
            continue
        for path in sorted(type_dir.glob("*.md")):
            text = _read(path)
            if text is None:
                continue
            fm = parse_frontmatter(text)
            raw = fm.get("sources") or []
            if isinstance(raw, str):
                raw = [raw]
            out.setdefault(derive_rule_slug(fm, path.stem), [s for s in raw if isinstance(s, str)])
    return out


def memory_sources(sources: Sequence[str], vault: Path) -> List[Tuple[str, str]]:
    """The ``(agent, file)`` auto-memory origins among a page's ``sources:``."""
    root = vault.as_posix().rstrip("/") + "/"
    out: List[Tuple[str, str]] = []
    for source in sources:
        s = source.replace("\\", "/")
        if s.startswith(root):
            s = s[len(root):]
        m = _MEMORY_SOURCE.match(s)
        if m and m.group(2) != INDEX and (m.group(1), m.group(2)) not in out:
            out.append((m.group(1), m.group(2)))
    return out


# --- the reflex ----------------------------------------------------------------------------

def read_rows(path: Path) -> List[Dict[str, Any]]:
    return [r for r in _records(path)] if path.is_file() else []


def emitting_rows(vault: Path, since: str = SINCE) -> List[Dict[str, Any]]:
    """Every row that injected a rule since ``since``: both log generations
    and #545's archive, each row once, oldest first."""
    seen = set()
    out = []
    for path in [vault / ".mnemo" / ARCHIVE] + [vault / ".mnemo" / name for name in LOGS]:
        for row in read_rows(path):
            if not row.get("emitted") or str(row.get("ts") or "") < since:
                continue
            key = json.dumps([row.get("session_id"), row.get("prompt_hash"), row.get("ts"), row.get("emitted")])
            if key not in seen:
                seen.add(key)
                out.append(row)
    return sorted(out, key=lambda r: str(r.get("ts") or ""))


def classify(sources: Optional[Sequence[str]], session: Optional[Dict[str, Any]], vault: Path,
             agent_of: Callable[[str], str]) -> str:
    """One injected rule's status in the session it was injected into."""
    if sources is None:
        return NO_PAGE
    if not sources:
        return NO_SOURCE
    mem = memory_sources(sources, vault)
    if not mem:
        return NOT_MEMORY
    memory = (session or {}).get("memory")
    if memory is None:
        return UNKNOWN
    memdir = Path(memory["memdir"])
    load_agent = agent_of(memdir.parent.name)
    shown = set(links(memory["lines"]))
    cut = memory["total"] is not None
    today: Optional[set] = None
    found = []
    for agent, rel in mem:
        if agent != load_agent:
            found.append(OTHER_PROJECT)
        elif rel in shown:
            found.append(SHOWN)
        elif cut:
            if today is None:
                today = set(links(split_lines(_read(memdir / INDEX) or "")))
            found.append(PAST_CUT if rel in today else UNINDEXED if (memdir / rel).is_file() else REMOVED)
        else:
            found.append(UNINDEXED if (memdir / rel).is_file() else REMOVED)
    return next(s for s in PRECEDENCE if s in found)


def self_written(sources: Sequence[str], session: Dict[str, Any], vault: Path, ts: str) -> bool:
    """Whether the session itself wrote one of the rule's memory files before
    the row: then the model had it in context, whatever the index showed."""
    writes = session.get("writes") or {}
    names = {"/memory/" + rel for _agent, rel in memory_sources(sources, vault)}
    return any(when <= ts and any(target.endswith(n) for n in names) for target, when in writes.items())


def model_check(session: Dict[str, Any]) -> Optional[Tuple[bool, str]]:
    """Whether :func:`loaded_count` cuts today's file where this session's
    load did, and which limit cut it (``lines``, ``chars`` or ``none``);
    ``None`` when today's file is no longer the one it loaded."""
    memory = session.get("memory")
    if memory is None:
        return None
    lines = split_lines(_read(Path(memory["memdir"]) / INDEX) or "")
    got = memory["lines"]
    total = memory["total"] if memory["total"] is not None else len(got)
    if len(lines) != total or lines[:len(got)] != got:
        return None
    by = "none" if len(got) == len(lines) else "lines" if len(got) == LIMIT_LINES else "chars"
    return loaded_count(lines) == len(got), by


def model_tally(checks: Iterable[Optional[Tuple[bool, str]]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"agree": 0, "disagree": 0, "not_comparable": 0,
                           "agree_by": {"lines": 0, "chars": 0, "none": 0}}
    for check in checks:
        if check is None:
            out["not_comparable"] += 1
        elif check[0]:
            out["agree"] += 1
            out["agree_by"][check[1]] += 1
        else:
            out["disagree"] += 1
    return out


def _empty() -> Dict[str, Any]:
    return {"rows": 0, "sessions": 0, "units": 0, "pairs": 0,
            "units_by": {s: 0 for s in STATUSES}, "pairs_by": {s: 0 for s in STATUSES},
            "units_hidden_self_written": 0, "pairs_hidden_self_written": 0}


def reflex_tally(rows: Sequence[Dict[str, Any]], pages: Dict[str, List[str]], sessions: Dict[str, Dict[str, Any]],
                 vault: Path, agent_of: Callable[[str], str]) -> Dict[str, Any]:
    """Units and ``(session, slug)`` pairs by status, human and bg apart."""
    out = {HUMAN: _empty(), BG: _empty()}
    seen_sessions: Dict[str, set] = {HUMAN: set(), BG: set()}
    pairs: Dict[Tuple[str, str], Tuple[str, str, bool]] = {}
    for row in rows:
        sid = str(row.get("session_id") or "")
        session = sessions.get(sid)
        kind = (session or {}).get("kind", HUMAN)
        bucket = out[kind]
        bucket["rows"] += 1
        seen_sessions[kind].add(sid)
        for slug in row.get("emitted") or []:
            status = classify(pages.get(slug), session, vault, agent_of)
            own = status in HIDDEN and self_written(pages[slug], session or {}, vault, str(row.get("ts") or ""))
            bucket["units"] += 1
            bucket["units_by"][status] += 1
            bucket["units_hidden_self_written"] += own
            pairs.setdefault((sid, slug), (kind, status, own))
    for kind, status, own in pairs.values():
        out[kind]["pairs"] += 1
        out[kind]["pairs_by"][status] += 1
        out[kind]["pairs_hidden_self_written"] += own
    for kind in out:
        out[kind]["sessions"] = len(seen_sessions[kind])
        out[kind]["no_transcript"] = len([s for s in seen_sessions[kind] if s not in sessions])
    return out


# --- the run -------------------------------------------------------------------------------

def memory_dirs(projects_root: Path) -> List[Path]:
    """Every auto-memory directory holding a file."""
    out = []
    for memdir in sorted(projects_root.glob("*/memory")):
        if memdir.is_dir() and any(p.is_file() for p in memdir.rglob("*")):
            out.append(memdir)
    return out


def measure(projects_root: Path, vault: Path, since: str = SINCE,
            agent_of: Optional[Callable[[str], str]] = None) -> Dict[str, Any]:
    if agent_of is None:
        from mnemo.core import mirror
        agent_of = mirror._agent_from_project_dir
    cache: Dict[str, str] = {}

    def agent(name: str) -> str:
        if name not in cache:
            cache[name] = agent_of(name)
        return cache[name]

    projects = []
    for memdir in memory_dirs(projects_root):
        row = project_cut(memdir)
        row["name"] = memdir.parent.name
        projects.append(row)
    projects.sort(key=lambda r: (-r["lines"], -r["files"], r["name"]))
    for n, row in enumerate(projects, 1):
        row["alias"] = "project %d" % n

    rows = emitting_rows(vault, since)
    paths = transcripts(projects_root)
    wanted = {str(r.get("session_id") or "") for r in rows}
    sessions: Dict[str, Dict[str, Any]] = {}
    checks = []
    for sid, path in paths.items():
        session = read_session(path)
        checks.append(model_check(session))
        if sid in wanted:
            session["writes"] = memory_writes(path)
            sessions[sid] = session
    return {
        "since": since,
        "limit": {"lines": LIMIT_LINES, "chars": LIMIT_CHARS},
        "projects": projects,
        "model_check": model_tally(checks),
        "reflex": reflex_tally(rows, vault_sources(vault), sessions, vault, agent),
    }


def _pct(n: int, d: int) -> str:
    return "%d/%d (%.1f%%)" % (n, d, 100.0 * n / d) if d else "%d/0" % n


def report_lines(data: Dict[str, Any], names: bool = False) -> List[str]:
    out = ["Native auto-memory cut (#574): Claude Code loads the first %d lines or %d characters of MEMORY.md"
           % (data["limit"]["lines"], data["limit"]["chars"]), ""]
    out.append("%-12s %6s %7s %7s %7s %8s %9s %6s %10s" % (
        "project", "lines", "chars", "loaded", "cut at", "entries", "past cut", "files", "unindexed"))
    for p in data["projects"]:
        out.append("%-12s %6d %7d %7d %7s %8d %9d %6d %10d" % (
            p["name"] if names else p["alias"], p["lines"], p["chars"], p["loaded_lines"],
            p["cut_at"] or "-", p["entries"], p["entries_past_cut"], p["files"], p["unindexed"]))
    cut = [p for p in data["projects"] if p["cut_at"]]
    out.append("")
    out.append("%d of %d auto-memory directories are cut; %d index entries past the cut, %d files never indexed."
               % (len(cut), len(data["projects"]), sum(p["entries_past_cut"] for p in data["projects"]),
                  sum(p["unindexed"] for p in data["projects"])))
    mc = data["model_check"]
    out.append("Model check, every recorded load whose file is unchanged today: %d agree "
               "(cut by lines %d, by chars %d, whole %d), %d disagree; %d not comparable."
               % (mc["agree"], mc["agree_by"]["lines"], mc["agree_by"]["chars"], mc["agree_by"]["none"],
                  mc["disagree"], mc["not_comparable"]))
    out.append("")
    out.append("Reflex since %s:" % data["since"])
    for kind in (HUMAN, BG):
        r = data["reflex"][kind]
        for unit, total, key in (("units", "units", "units_by"), ("(session, slug) pairs", "pairs", "pairs_by")):
            by = r[key]
            mem = sum(by[s] for s in PRECEDENCE + (UNKNOWN,))
            known = sum(by[s] for s in PRECEDENCE)
            hidden = sum(by[s] for s in HIDDEN)
            if total == "units":
                out.append("  %s: %d sessions (%d without a transcript), %d rows, %d injected units"
                           % (kind, r["sessions"], r["no_transcript"], r["rows"], r["units"]))
            own = r[total + "_hidden_self_written"]
            out.append("    %s: from an auto-memory file %s; of those with a load record %d, native could not show %s"
                       % (unit, _pct(mem, r[total]), known, _pct(hidden, known)))
            out.append("      of which the session had written the file itself %d; hidden and not its own %s"
                       % (own, _pct(hidden - own, known)))
            out.append("      " + ", ".join("%s %d" % (s, by[s]) for s in STATUSES))
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--since", default=SINCE, help="reflex rows from this ISO instant (default %(default)s)")
    ap.add_argument("--projects-root", type=Path, default=Path(os.path.expanduser("~/.claude/projects")))
    ap.add_argument("--vault", type=Path, default=None, help="default: the configured vault")
    ap.add_argument("--json", action="store_true", help="the report as data")
    ap.add_argument("--names", action="store_true", help="print project directory names, not 'project N'")
    args = ap.parse_args(argv)
    vault = args.vault
    if vault is None:
        from mnemo.core import config, paths
        vault = paths.vault_root(config.load_config())
    data = measure(args.projects_root, Path(vault), args.since)
    if args.json:
        if not args.names:
            for p in data["projects"]:
                p["name"] = p["alias"]
        print(json.dumps(data, indent=2))
    else:
        print("\n".join(report_lines(data, names=args.names)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
