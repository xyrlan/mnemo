"""Test seam for ``mnemo.core.share.format``, the piece ``import`` consumes.

The ``import`` piece of share-rules is written and tested in a worktree where
the ``format`` piece does not exist yet (the contract says so). Two rules keep
these tests honest across the merge:

- **The real module wins.** :func:`ensure_format` installs a stub only when
  ``mnemo.core.share.format`` cannot be imported. After the merge every test
  here runs against the real thing and the stub is dead code.
- **Fixtures are built through the consumed signatures.** A portable page is
  produced by calling ``to_portable`` on a vault page (the shape
  ``_export_fixtures.write_rule`` writes), never by hand-typing the tree's
  frontmatter — the contract's one named risk is a fixture that guesses the
  portable shape and guesses wrong.

The stub implements exactly the eight names the contract exposes, with the
semantics the contract states, and nothing else.
"""
from __future__ import annotations

import hashlib
import importlib
import secrets
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MODULE = "mnemo.core.share.format"


def ensure_format() -> types.ModuleType:
    """Return the real format module, or install and return the stub."""
    try:
        return importlib.import_module(MODULE)
    except ImportError:
        pass
    mod = sys.modules.get(MODULE)
    if mod is not None:
        return mod
    mod = _build_stub()
    sys.modules[MODULE] = mod
    return mod


# --- the stub -----------------------------------------------------------------

def _build_stub() -> types.ModuleType:
    from mnemo.core.filters import parse_frontmatter, topic_tags
    from mnemo.core.reclassify_types import split_frontmatter
    from mnemo.core.text_utils import strip_graph_section

    mod = types.ModuleType(MODULE)
    mod.__stub__ = True  # type: ignore[attr-defined]
    mod.SHARE_DIR = ".mnemo-shared"

    @dataclass(frozen=True)
    class PortableRule:
        slug: str
        type: str
        name: str
        description: str
        body: str
        tags: tuple
        confidence: str
        stability: str
        quote: str | None
        evidence_source: str | None
        vault: str
        project: str
        published_at: str
        source_count: int
        hash: str

    def vault_id(vault_root: Path) -> str:
        p = Path(vault_root) / ".mnemo" / "vault-id"
        try:
            existing = p.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return existing
        p.parent.mkdir(parents=True, exist_ok=True)
        new = secrets.token_hex(16)
        p.write_text(new + "\n", encoding="utf-8")
        return new

    def portable_hash(text: str) -> str:
        return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _q(v: object) -> str:
        s = str(v).replace("'", "''")
        return f"'{s}'"

    def to_portable(text: str, *, vault: str, project: str, today: str) -> str | None:
        fm, body = split_frontmatter(text)
        if not fm or not (fm.get("slug") or fm.get("name")):
            return None
        slug = fm.get("slug") or fm.get("name")
        prov = fm.get("imported") if isinstance(fm.get("imported"), dict) else None
        if prov:
            p_vault, p_project = prov.get("vault", vault), prov.get("project", project)
            p_date, p_count = prov.get("published_at", today), prov.get("source_count", 0)
        else:
            p_vault, p_project, p_date = vault, project, today
            p_count = len([s for s in (fm.get("sources") or []) if isinstance(s, str)])
        lines = [
            "---",
            f"name: {_q(fm.get('name') or slug)}",
            f"slug: {slug}",
            f"description: {_q(fm.get('description') or '')}",
            f"type: {fm.get('type') or 'feedback'}",
            f"stability: {fm.get('stability') or 'stable'}",
            f"confidence: {fm.get('confidence') or 'inferred'}",
            "tags:",
        ]
        lines += [f"  - {t}" for t in topic_tags(fm)]
        ev = fm.get("evidence")
        if isinstance(ev, dict) and ev.get("quote"):
            lines += ["evidence:", f"  quote: {_q(ev['quote'])}",
                      f"  source: {_q(ev.get('source') or '')}"]
        lines += [
            "provenance:",
            f"  vault: {p_vault}",
            f"  project: {p_project}",
            f"  published_at: {p_date}",
            f"  source_count: {p_count}",
            "---",
            "",
        ]
        return "\n".join(lines) + strip_graph_section(body).rstrip() + "\n"

    def from_portable(text: str) -> PortableRule | None:
        fm, body = split_frontmatter(text)
        prov = fm.get("provenance") if fm else None
        if not isinstance(prov, dict) or not fm.get("slug"):
            return None
        ev = fm.get("evidence") if isinstance(fm.get("evidence"), dict) else {}
        return PortableRule(
            slug=str(fm["slug"]),
            type=str(fm.get("type") or "feedback"),
            name=str(fm.get("name") or fm["slug"]),
            description=str(fm.get("description") or ""),
            body=body,
            tags=tuple(topic_tags(fm)),
            confidence=str(fm.get("confidence") or "inferred"),
            stability=str(fm.get("stability") or "stable"),
            quote=str(ev["quote"]) if ev.get("quote") else None,
            evidence_source=str(ev["source"]) if ev.get("source") else None,
            vault=str(prov.get("vault") or ""),
            project=str(prov.get("project") or ""),
            published_at=str(prov.get("published_at") or ""),
            source_count=int(prov.get("source_count") or 0),
            hash=portable_hash(text),
        )

    def to_vault_page(rule: PortableRule, *, project: str, today: str) -> str:
        conf = "verified-elsewhere" if rule.confidence == "verified" else "inferred"
        lines = [
            "---",
            f"name: {_q(rule.name)}",
            f"slug: {rule.slug}",
            f"description: {_q(rule.description)}",
            f"type: {rule.type}",
            f"stability: {rule.stability}",
            f"confidence: {conf}",
            "origin: imported",
            "projects:",
            f"  - {project}",
            "sources: []",
            "tags:",
            "  - needs-review",
        ]
        lines += [f"  - {t}" for t in rule.tags]
        if rule.quote:
            lines += ["evidence:", f"  quote: {_q(rule.quote)}",
                      f"  source: {_q(rule.evidence_source or '')}"]
        lines += [
            "imported:",
            f"  vault: {rule.vault}",
            f"  project: {rule.project}",
            f"  published_at: {rule.published_at}",
            f"  source_count: {rule.source_count}",
            f"  imported_at: {today}",
            "---",
            "",
        ]
        return "\n".join(lines) + rule.body.rstrip() + "\n"

    def iter_portable(share_root: Path) -> list[tuple[Path, Any]]:
        root = Path(share_root)
        out: list[tuple[Path, Any]] = []
        for md in sorted(root.glob("*/*.md")):
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                out.append((md, None))
                continue
            out.append((md, from_portable(text)))
        return out

    def is_imported_frontmatter(fm: Any) -> bool:
        if not isinstance(fm, dict):
            return False
        if str(fm.get("origin") or "") == "imported":
            return True
        meta = fm.get("metadata")
        return isinstance(meta, dict) and str(meta.get("origin") or "") == "imported"

    mod.PortableRule = PortableRule
    mod.vault_id = vault_id
    mod.portable_hash = portable_hash
    mod.to_portable = to_portable
    mod.from_portable = from_portable
    mod.to_vault_page = to_vault_page
    mod.iter_portable = iter_portable
    mod.is_imported_frontmatter = is_imported_frontmatter
    return mod


# --- fixture builders (through the consumed signatures) -----------------------

def publish_rule(
    share_root: Path,
    *,
    slug: str,
    vault: str,
    project: str = "app",
    page_type: str = "feedback",
    name: str | None = None,
    body: str = "Do the thing.\n",
    quote: str | None = "never do the other thing",
    today: str = "2026-09-10",
    scratch: Path | None = None,
) -> Path:
    """Write ``<share_root>/<type>/<slug>.md`` the way ``publish`` would.

    Builds a vault page with ``_export_fixtures.write_rule`` in a scratch
    vault, converts it with the format module's ``to_portable``, and writes
    the result. The portable shape is therefore whatever the format piece
    says it is.
    """
    import tempfile

    from tests.unit._export_fixtures import write_rule

    fmt = ensure_format()
    scratch = scratch or Path(tempfile.mkdtemp(prefix="mnemo-share-scratch-"))
    page = write_rule(
        scratch, slug=slug, page_type=page_type, name=name, body=body, quote=quote,
        projects=(project,),
    )
    text = page.read_text(encoding="utf-8")
    if quote is not None:
        # write_rule stamps no confidence; a page with a quote is a verified one.
        text = text.replace("\nstability: ", "\nconfidence: verified\nstability: ", 1)
    portable = fmt.to_portable(text, vault=vault, project=project, today=today)
    assert portable is not None, "write_rule produced a page to_portable refused"
    target = Path(share_root) / page_type / f"{slug}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(portable, encoding="utf-8")
    return target
