"""The learned ledger: what extraction promoted, and what each project still owes an announcement.

Extraction appends one line per freshly-promoted page; SessionStart reads the
lines a project has not been told about yet, prints them, and marks them
announced. Two files under the vault's ``.mnemo/``:

* ``learned.jsonl`` — the append-only log, one JSON object per promoted page.
* ``announced.json`` — ``{project: last_seen_seq}``, the high-water mark per
  project.

Ordering runs on ``seq``, a monotonically increasing integer stamped at record
time, not on ``ts``: every entry of one ``record`` call shares a timestamp to
the microsecond on a fast filesystem, and a timestamp marker would then either
re-announce that whole batch forever or swallow all but the last of it. ``seq``
is derived as (the largest seq either file has seen) + 1, so it needs no side
file and survives rotation — rotation keeps the tail, and the tail holds the
largest seqs. The markers are consulted too, because they outlive a
``learned.jsonl`` that a user deletes or truncates by hand.

Every reader here is called from a hook, so ``pending``, ``pending_count`` and
``mark_announced`` swallow I/O and parse failures and return an empty answer;
only ``record`` (called from extraction, which already wraps it) may raise.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from mnemo.core.atomic import atomic_write_bytes

LEDGER_REL = ".mnemo/learned.jsonl"
MARKERS_REL = ".mnemo/announced.json"
#: Rotate at 1 MB, keeping the newest half of the lines. Same shape as
#: ``errors._rotate_if_needed``, but rewriting rather than renaming: the tail
#: is the part a pending() query can still reach, so a rolled-off prefix is
#: dead weight, not history worth a sidecar file.
MAX_BYTES = 1_048_576


def _ledger_path(vault_root: Path) -> Path:
    return Path(vault_root) / LEDGER_REL


def _markers_path(vault_root: Path) -> Path:
    return Path(vault_root) / MARKERS_REL


def _read(vault_root: Path) -> list[dict]:
    """Parse the ledger, skipping anything unreadable. Never raises.

    A line missing ``seq`` (hand-written, or from a pre-``seq`` ledger) is
    assigned ``previous_seq + 1``, which keeps such a file monotonic and
    orderable without a migration. The line *index* would not: a file whose
    real seqs already run past its line count (rotation keeps the tail, so
    line 0 can hold seq 3000) would give every hand-written line a seq far
    below the ones around it, sorting it to the front and re-announcing it
    under every live marker.
    """
    try:
        raw = _ledger_path(vault_root).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[dict] = []
    prev_seq = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(entry, dict) or not entry.get("slug"):
            continue
        seq = entry.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            entry["seq"] = prev_seq + 1
        prev_seq = entry["seq"]
        projects = entry.get("projects")
        if not isinstance(projects, list):
            entry["projects"] = []
        out.append(entry)
    out.sort(key=lambda e: e["seq"])
    return out


def _max_seq(entries: list[dict]) -> int:
    return max((e["seq"] for e in entries), default=0)


def _read_markers(vault_root: Path) -> dict:
    try:
        data = json.loads(_markers_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, int) and not isinstance(v, bool)}


def _rotate_if_needed(vault_root: Path) -> None:
    path = _ledger_path(vault_root)
    try:
        if path.stat().st_size <= MAX_BYTES:
            return
    except OSError:
        return
    entries = _read(vault_root)
    if not entries:
        return
    keep = entries[len(entries) // 2:]
    body = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in keep)
    atomic_write_bytes(path, body.encode("utf-8"))


def _ends_without_newline(path: Path) -> bool:
    """True when the file exists, is non-empty, and its last byte is not ``\\n``.

    A previous append that died mid-write leaves a torn last line. Appending
    straight onto it would glue the next entry to the fragment, and the parser
    would then drop *both*: the fragment is not valid JSON, and the entry that
    was welded to it is no longer on a line of its own.
    """
    try:
        size = path.stat().st_size
        if size <= 0:
            return False
        with open(path, "rb") as fh:
            fh.seek(-1, 2)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def record(vault_root: Path, *, run_id: str, entries: list[dict]) -> None:
    """Append one line per entry. Called from extraction, which logs failures."""
    if not entries:
        return
    path = _ledger_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The ledger is not the only record of how far seqs have run: a hand-deleted
    # or truncated learned.jsonl leaves announced.json holding markers above
    # every seq we would then hand out, and the new entries pend for nobody,
    # forever. Take the base from whichever of the two is further along.
    markers = _read_markers(vault_root)
    base = max([_max_seq(_read(vault_root)), 0, *markers.values()])
    ts = datetime.now().isoformat(timespec="seconds")
    lines = []
    for i, entry in enumerate(entries, start=1):
        projects = entry.get("projects") or []
        lines.append(json.dumps({
            "seq": base + i,
            "ts": ts,
            "run_id": run_id,
            "slug": entry.get("slug"),
            "type": entry.get("type"),
            "name": entry.get("name"),
            "projects": list(projects),
            "confidence": entry.get("confidence"),
            "quote": entry.get("quote"),
        }, ensure_ascii=False))
    body = "\n".join(lines) + "\n"
    if _ends_without_newline(path):
        body = "\n" + body
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(body)
    _rotate_if_needed(vault_root)


def max_seq(vault_root: Path) -> int:
    """The largest seq the ledger holds, or 0 when it is empty. Never raises.

    ``core.learn`` takes this before a run and diffs against it after, which
    is how a synchronous ``mnemo learn`` reports *what this session taught*
    rather than the whole announcement backlog.
    """
    try:
        return _max_seq(_read(vault_root))
    except Exception:  # noqa: BLE001 — mirrors the fail-silent readers above
        return 0


def _pending_entries(
    vault_root: Path, project: str, universal_threshold: int = 2
) -> list[dict]:
    try:
        entries = _read(vault_root)
        if not entries:
            return []
        floor = _read_markers(vault_root).get(project, 0)
        out = []
        for e in entries:
            if e["seq"] <= floor:
                continue
            projects = e["projects"]
            # A rule with at least ``universal_threshold`` projects is
            # universal: it applies everywhere, so it pends for every project,
            # not just its sources'.
            if project in projects or len(projects) >= universal_threshold:
                out.append(e)
        return out
    except Exception:  # noqa: BLE001 — runs inside a hook
        return []


def pending(
    vault_root: Path,
    project: str,
    *,
    limit: int | None = None,
    universal_threshold: int = 2,
) -> list[dict]:
    """Entries this project has not been shown yet, oldest first. Never raises.

    ``universal_threshold`` mirrors ``scoping.universalThreshold``, but is
    passed in rather than read here: every caller is on a hook path, and the
    hook already holds the loaded config.
    """
    out = _pending_entries(vault_root, project, universal_threshold)
    if limit is not None:
        out = out[:limit]
    return out


def pending_count(
    vault_root: Path, project: str, *, universal_threshold: int = 2
) -> int:
    """How many entries ``pending`` would return unlimited. Never raises."""
    return len(_pending_entries(vault_root, project, universal_threshold))


def recent(
    vault_root: Path,
    project: str | None,
    *,
    limit: int = 10,
    universal_threshold: int = 2,
) -> list[dict]:
    """The newest entries relevant to ``project``, newest first. Never raises.

    Same project/universal filter as ``pending``, but blind to the announced
    markers: this is the "what have I learned lately" view, and an entry the
    session-start block already showed is exactly what a user comes here to
    look up again. ``project=None`` means the whole vault, for the case where
    ``status`` cannot resolve a project name.
    """
    try:
        entries = _read(vault_root)
        if not entries:
            return []
        out = []
        for e in entries:
            if project is None:
                out.append(e)
                continue
            projects = e["projects"]
            if project in projects or len(projects) >= universal_threshold:
                out.append(e)
        out.reverse()
        if limit is not None:
            out = out[:limit]
        return out
    except Exception:  # noqa: BLE001 — mirrors the fail-silent readers above
        return []


def mark_announced(vault_root: Path, project: str) -> None:
    """Advance this project's high-water mark to the whole ledger. Never raises."""
    try:
        top = _max_seq(_read(vault_root))
        markers = _read_markers(vault_root)
        markers[project] = top
        path = _markers_path(vault_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(path, (json.dumps(markers, indent=2) + "\n").encode("utf-8"))
    except Exception:  # noqa: BLE001 — runs inside a hook
        return None
