"""Stand-in for ``mnemo.core.share.format`` while the ``format`` piece lands elsewhere.

The share-rules contract (``docs/superpowers/contracts/2026-09-13-share-rules.md``)
splits the feature three ways; ``publish`` consumes five names from ``format``
and is written in a worktree where that module does not exist yet.
:func:`ensure_format_module` installs this stub **only** when the real module
cannot be imported, so after the merge every publish test runs against the
real ``format`` and this file is inert. Keep the stub to the contract's
surface — the names, the keyword arguments, ``None`` for a non-page, a
provenance block a reader can find — and never to a guess about the real
module's byte layout. Tests that rely on this stub must therefore assert
counts, paths and ownership, not text.

``CALLS`` records every ``to_portable`` call so a test can assert the wiring
(``vault=`` is the publishing vault's id, ``project=`` the caller's project)
rather than only that a file appeared.
"""
from __future__ import annotations

import hashlib
import secrets
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

MODULE_NAME = "mnemo.core.share.format"

# Every to_portable call as {"vault": ..., "project": ..., "today": ...}.
CALLS: list[dict[str, Any]] = []


@dataclass(frozen=True)
class PortableRule:
    slug: str
    type: str
    name: str
    description: str
    body: str
    tags: tuple[str, ...]
    confidence: str
    stability: str
    quote: Optional[str]
    evidence_source: Optional[str]
    vault: str
    project: str
    published_at: str
    source_count: int
    hash: str


def _scalar(value: Any) -> str:
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _to_portable(text: str, *, vault: str, project: str, today: str) -> Optional[str]:
    from mnemo.core.filters import parse_frontmatter, topic_tags
    from mnemo.core.reclassify_types import split_frontmatter
    from mnemo.core.text_utils import strip_graph_section

    CALLS.append({"vault": vault, "project": project, "today": today})
    fm = parse_frontmatter(text)
    if not fm or not (fm.get("slug") or fm.get("name")):
        return None
    _, body = split_frontmatter(text)
    slug = fm.get("slug") or fm.get("name")
    imported = fm.get("imported")
    if str(fm.get("origin") or "") == "imported" and isinstance(imported, dict) and imported.get("vault"):
        # The hop rule: an already-imported page keeps its first vault.
        prov_vault, prov_project = str(imported["vault"]), str(imported.get("project") or "")
        prov_date, prov_count = str(imported.get("date") or "")[:10], int(imported.get("source_count") or 0)
    else:
        sources = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
        # The date is the vault's newest timestamp; *today* only when none.
        stamps = [str(fm[k])[:10] for k in ("last_sync", "promoted_at", "extraction_run", "extracted_at")
                  if isinstance(fm.get(k), str) and fm.get(k)]
        prov_vault, prov_project, prov_count = vault, project, len(sources)
        prov_date = max(stamps) if stamps else str(today)[:10]
    lines = [
        "---",
        f"name: {_scalar(fm.get('name') or slug)}",
        f"slug: {slug}",
        f"description: {_scalar(fm.get('description') or '')}",
        f"type: {fm.get('type') or 'feedback'}",
        f"stability: {fm.get('stability') or 'stable'}",
        "tags: [" + ", ".join(topic_tags(fm)) + "]",
        f"confidence: {fm.get('confidence') or 'inferred'}",
    ]
    ev = fm.get("evidence")
    if isinstance(ev, dict):
        lines += ["evidence:", f"  quote: {_scalar(ev.get('quote') or '')}",
                  f"  source: {_scalar(ev.get('source') or '')}"]
    lines += [
        "published:",
        f"  vault: {prov_vault}",
        f"  project: {prov_project}",
        f"  date: {prov_date}",
        f"  source_count: {prov_count}",
        "---",
    ]
    return "\n".join(lines) + "\n\n" + strip_graph_section(body).strip() + "\n"


def _portable_hash(text: str) -> str:
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def _from_portable(text: str) -> Optional[PortableRule]:
    from mnemo.core.filters import parse_frontmatter
    from mnemo.core.reclassify_types import split_frontmatter

    fm = parse_frontmatter(text)
    prov = fm.get("published") if fm else None
    if not isinstance(prov, dict) or not prov.get("vault"):
        return None
    _, body = split_frontmatter(text)
    ev = fm.get("evidence") if isinstance(fm.get("evidence"), dict) else {}
    slug = str(fm.get("slug") or fm.get("name"))
    return PortableRule(
        slug=slug,
        type=str(fm.get("type") or "feedback"),
        name=str(fm.get("name") or slug),
        description=str(fm.get("description") or ""),
        body=body.strip() + "\n",
        tags=tuple(fm.get("tags") or []),
        confidence=str(fm.get("confidence") or "inferred"),
        stability=str(fm.get("stability") or "stable"),
        quote=str(ev["quote"]) if ev.get("quote") else None,
        evidence_source=str(ev["source"]) if ev.get("source") else None,
        vault=str(prov["vault"]),
        project=str(prov.get("project") or ""),
        published_at=str(prov.get("date") or "")[:10],
        source_count=int(prov.get("source_count") or 0),
        hash=_portable_hash(text),
    )


def _iter_portable(share_root: Path) -> list[tuple[Path, Optional[PortableRule]]]:
    share_root = Path(share_root)
    out: list[tuple[Path, Optional[PortableRule]]] = []
    if not share_root.is_dir():
        return out
    for md in sorted(share_root.glob("*/*.md")):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            out.append((md, None))
            continue
        out.append((md, _from_portable(text)))
    return out


def _vault_id(vault_root: Path) -> str:
    p = Path(vault_root) / ".mnemo" / "vault-id"
    try:
        existing = p.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    p.parent.mkdir(parents=True, exist_ok=True)
    vid = secrets.token_hex(16)
    p.write_text(vid + "\n", encoding="utf-8")
    return vid


def ensure_format_module() -> bool:
    """Install the stub unless the real module imports. True when the stub is live."""
    try:
        __import__(MODULE_NAME)
        return False
    except ImportError:
        pass
    if MODULE_NAME in sys.modules:
        return True
    mod = types.ModuleType(MODULE_NAME)
    mod.SHARE_DIR = ".mnemo-shared"  # type: ignore[attr-defined]
    mod.PortableRule = PortableRule  # type: ignore[attr-defined]
    mod.vault_id = _vault_id  # type: ignore[attr-defined]
    mod.to_portable = _to_portable  # type: ignore[attr-defined]
    mod.from_portable = _from_portable  # type: ignore[attr-defined]
    mod.iter_portable = _iter_portable  # type: ignore[attr-defined]
    mod.portable_hash = _portable_hash  # type: ignore[attr-defined]
    sys.modules[MODULE_NAME] = mod
    import mnemo.core.share as pkg  # namespace package: the directory holds publish.py

    setattr(pkg, "format", mod)
    return True
