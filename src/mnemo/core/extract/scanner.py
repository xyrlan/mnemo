"""Walk bots/*/memory/*.md, group by type, diff against state."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

_VALID_TYPES = {"feedback", "user", "reference", "project"}
_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_SLUG_CLEAN_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class MemoryFile:
    path: Path
    agent: str
    type: str
    slug: str
    frontmatter: dict
    body: str
    source_hash: str


@dataclass
class StateEntry:
    source_files: list[str]
    source_hash: str
    written_hash: str
    written_at: str
    status: str  # "inbox" | "promoted" | "dismissed" | "direct" | "auto_promoted"
    last_sync: str = ""

    def mark_written(
        self,
        *,
        run_id: str,
        new_hash: str,
        source_files: list[str],
        source_hash: str,
        status: str | None = None,
    ) -> None:
        """Refresh a state entry to reflect a fresh write of its target page.

        D4 consolidation (v0.9 PR I): replaces 5 duplicate "fresh write"
        blocks in ``extract/inbox/branches/auto_promoted.py`` and
        ``extract/inbox/branches/inbox_flow.py`` (originally inlined at
        ``inbox.py:481-487``, ``494-500``, ``510-516``, ``588-593``, and
        ``608-614``). Always advances ``written_at`` and ``last_sync`` to
        ``run_id``; the optional ``status`` argument is applied only when
        provided so blocks that don't change status (e.g. inbox→inbox
        overwrite_safe) need not pass it.

        ``extract/promote.py``'s mutation sites have a divergent shape
        (``status="direct"`` + no ``last_sync`` update) and are
        intentionally NOT migrated to this helper to preserve v0.8
        behavior for direct-promotion entries.
        """
        self.source_files = list(source_files)
        self.source_hash = source_hash
        self.written_hash = new_hash
        self.written_at = run_id
        self.last_sync = run_id
        if status is not None:
            self.status = status


def _default_schema_version() -> int:
    """Return the canonical state-file schema version.

    D5 consolidation (v0.9 PR I): defers to ``inbox.state_io.SCHEMA_VERSION``
    so the on-disk schema version lives in exactly one place. A
    function-level import side-steps the circular dependency that a
    module-level import would create (state_io imports ExtractionState
    from this module).
    """
    from mnemo.core.extract.inbox.state_io import SCHEMA_VERSION
    return SCHEMA_VERSION


@dataclass
class ExtractionState:
    last_run: str | None
    entries: dict[str, StateEntry] = field(default_factory=dict)
    schema_version: int = field(default_factory=_default_schema_version)


@dataclass(frozen=True)
class ScanResult:
    by_type: dict[str, list[MemoryFile]]
    unchanged_slugs: set[str]
    dirty_files: list[MemoryFile]


def _normalize_slug(stem: str) -> str:
    slug = _SLUG_CLEAN_RE.sub("-", stem.lower()).strip("-")
    return slug[:60].rstrip("-") or "untitled"


def _stem_without_type_prefix(stem: str) -> str:
    for prefix in ("feedback_", "user_", "reference_", "project_"):
        if stem.startswith(prefix):
            return stem[len(prefix):]
    return stem


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    fm_block = m.group(1)
    body = text[m.end():]
    fm: dict = {}
    for line in fm_block.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm, body


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Public alias for _parse_frontmatter.

    Promoted in v0.10 so non-extraction modules (briefing picker, future
    consumers) can reuse the same parser. The leading-underscore form is
    retained as an alias for in-package callers and to avoid touching
    every existing call site.
    """
    return _parse_frontmatter(text)


def _read_memory_file(path: Path, agent: str) -> MemoryFile:
    raw = path.read_bytes()
    source_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    fm, body = _parse_frontmatter(text)
    declared_type = fm.get("type", "").strip()
    if declared_type not in _VALID_TYPES:
        declared_type = "feedback"
    slug = _normalize_slug(_stem_without_type_prefix(path.stem))
    return MemoryFile(
        path=path,
        agent=agent,
        type=declared_type,
        slug=slug,
        frontmatter=fm,
        body=body,
        source_hash=source_hash,
    )


def _read_briefing_file(path: Path, agent: str) -> MemoryFile:
    """Read a briefing file and route it through the feedback cluster.

    Briefings carry `type: briefing` in their frontmatter, but v0.3.1 routes
    them through the existing feedback extraction path so their Decisions
    made / Dead ends sections get mined into Tier 2 pages. The slug is the
    session id (the filename stem) so multiple briefings do not collide.
    """
    raw = path.read_bytes()
    source_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    fm, body = _parse_frontmatter(text)
    slug = _normalize_slug(f"briefing-{path.stem}")
    return MemoryFile(
        path=path,
        agent=agent,
        type="feedback",
        slug=slug,
        frontmatter=fm,
        body=body,
        source_hash=source_hash,
    )


def scan(vault_root: Path, state: ExtractionState) -> ScanResult:
    by_type: dict[str, list[MemoryFile]] = {t: [] for t in _VALID_TYPES}

    bots_root = vault_root / "bots"
    if bots_root.is_dir():
        for agent_dir in sorted(bots_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            memory_dir = agent_dir / "memory"
            if memory_dir.is_dir():
                for md in sorted(memory_dir.glob("*.md")):
                    if md.name == "MEMORY.md":
                        continue
                    try:
                        mf = _read_memory_file(md, agent=agent_dir.name)
                    except OSError:
                        continue
                    by_type[mf.type].append(mf)

            briefings_dir = agent_dir / "briefings" / "sessions"
            if briefings_dir.is_dir():
                for md in sorted(briefings_dir.glob("*.md")):
                    try:
                        mf = _read_briefing_file(md, agent=agent_dir.name)
                    except OSError:
                        continue
                    by_type["feedback"].append(mf)

    dirty: list[MemoryFile] = []
    unchanged: set[str] = set()
    for type_name, files in by_type.items():
        for f in files:
            key = f"{type_name}/{f.slug}"
            entry = state.entries.get(key)
            if entry is not None and entry.source_hash == f.source_hash:
                unchanged.add(key)
            else:
                dirty.append(f)

    return ScanResult(by_type=by_type, unchanged_slugs=unchanged, dirty_files=dirty)
