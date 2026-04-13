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


@dataclass
class ExtractionState:
    last_run: str | None
    entries: dict[str, StateEntry] = field(default_factory=dict)
    schema_version: int = 2


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


def scan(vault_root: Path, state: ExtractionState) -> ScanResult:
    by_type: dict[str, list[MemoryFile]] = {t: [] for t in _VALID_TYPES}

    bots_root = vault_root / "bots"
    if bots_root.is_dir():
        for agent_dir in sorted(bots_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            memory_dir = agent_dir / "memory"
            if not memory_dir.is_dir():
                continue
            for md in sorted(memory_dir.glob("*.md")):
                if md.name == "MEMORY.md":
                    continue
                try:
                    mf = _read_memory_file(md, agent=agent_dir.name)
                except OSError:
                    continue
                by_type[mf.type].append(mf)

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
