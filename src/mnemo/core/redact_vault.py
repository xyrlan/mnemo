"""Secrets already on disk in the vault's derived text (#418).

Redaction at write time (:mod:`mnemo.core.redact`) protects what mnemo writes
from now on. Pages and briefings written before a pattern existed are never
revisited by any writer, so this module walks them:

* ``shared/**/*.md`` — live, staged, proposed and archived rule pages;
* ``bots/*/briefings/**/*.md`` — session briefings.

Never ``bots/*/memory/``: that is the mirror of the user's own Claude Code
auto-memory, which mnemo copies and must not rewrite; the next promotion of a
project page redacts its derived copy.

:func:`scan` reports *where* and *what kind*, never the value — the report is
printed to a terminal and may be pasted into an issue. :func:`apply` rewrites
the files in place under the extraction lock, and moves the extraction
state's ``written_hash`` along with any page it rewrites, so the next run
still sees an untouched page (not a user edit to be preserved as a
``.proposed`` sibling).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, List

from mnemo.core import locks
from mnemo.core.redact import find, redact_secrets


@dataclass(frozen=True)
class Hit:
    """One secret: the vault-relative file, the 1-based line, the kind."""

    path: str
    line: int
    kind: str


@dataclass
class Report:
    files_scanned: int = 0
    hits: List[Hit] = field(default_factory=list)
    rewritten: List[str] = field(default_factory=list)

    @property
    def files(self) -> List[str]:
        return sorted({h.path for h in self.hits})


class VaultBusy(RuntimeError):
    """An extraction holds the vault; rewriting under it would race its writes."""


def _targets(vault_root: Path) -> Iterator[Path]:
    shared = vault_root / "shared"
    if shared.is_dir():
        yield from sorted(p for p in shared.rglob("*.md") if p.is_file())
    bots = vault_root / "bots"
    if bots.is_dir():
        yield from sorted(
            p for p in bots.glob("*/briefings/**/*.md") if p.is_file()
        )


def _read(path: Path) -> str:
    return path.read_bytes().decode("utf-8", errors="replace")


def scan(vault_root: Path) -> Report:
    """Every secret :func:`redact_secrets` would replace, by file and line."""
    vault_root = Path(vault_root)
    report = Report()
    # String slicing, not ``Path.relative_to``: every target is under the
    # root by construction, and relative_to was a third of the walk's time.
    cut = len(str(vault_root)) + 1
    for path in _targets(vault_root):
        try:
            text = _read(path)
        except OSError:
            continue
        report.files_scanned += 1
        rel = str(path)[cut:].replace("\\", "/")
        for f in find(text):
            report.hits.append(Hit(rel, text.count("\n", 0, f.start) + 1, f.kind))
    return report


def apply(vault_root: Path) -> Report:
    """Redact every file :func:`scan` flags, in place. Returns the scan, with
    ``rewritten`` listing the files actually changed."""
    from mnemo.core.extract.inbox.io import atomic_write, content_hash
    from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state

    vault_root = Path(vault_root)
    lock_path = vault_root / ".mnemo" / "extract.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with locks.try_lock(lock_path) as held:
        if not held:
            raise VaultBusy("an extraction is in progress (lock held); try again later")
        report = scan(vault_root)
        if not report.hits:
            return report
        state_path = vault_root / ".mnemo" / "extraction-state.json"
        state = load_state(state_path) if state_path.exists() else None
        by_hash = {}
        if state is not None:
            for entry in state.entries.values():
                if entry.written_hash:
                    by_hash.setdefault(entry.written_hash, []).append(entry)
        moved = False
        for rel in report.files:
            path = vault_root / rel
            raw = path.read_bytes()
            text, n = redact_secrets(raw.decode("utf-8", errors="replace"))
            if not n:
                continue
            atomic_write(path, text)
            report.rewritten.append(rel)
            new_hash = content_hash(text)
            for entry in by_hash.get(content_hash(raw), ()):
                entry.written_hash = new_hash
                moved = True
        if moved and state is not None:
            atomic_write_state(state, state_path)
    return report
