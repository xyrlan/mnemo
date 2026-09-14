"""``mnemo import``: bring a published rules tree into this vault, staged.

The tree is ``<repo>/.mnemo-shared/<type>/<slug>.md`` (``format.SHARE_DIR``),
written by ``mnemo publish`` in someone else's clone. Every page it carries
was asked for by *someone else*, so nothing here is ever promoted: a rule
lands in ``shared/_inbox/<type>/`` for the same ``mv``-to-promote review
backfill uses, with ``origin: imported`` and ``confidence:
verified-elsewhere`` (or ``inferred``) so no "you said" surface ever
attributes it to the reader. The page text is ``format.to_vault_page``'s;
this module decides only *where* it goes and *whether* it goes at all.

Routing, per rule in the tree (``ImportDecision.action``):

``yours``
    The provenance names this vault's own id: the tree you published is not
    something to import back.
``unchanged``
    The ledger already holds this rule at this ``portable_hash``. Covers a
    page staged earlier, a page the user promoted, and a page the user
    deleted from ``_inbox`` — all three are decisions already made.
``refused``
    The slug exists under a *different* type anywhere consumer-visible
    (#187: which of two same-slug pages is canonical is not a call to make
    unsupervised); or the target file exists and is not an unmodified page
    this command wrote earlier (it is the extractor's, or hand-edited).
``proposed``
    A live ``shared/<type>/<slug>.md`` exists: the page is written as
    ``shared/_inbox/<type>/<slug>.proposed.md``, the sibling ``mnemo
    rewrites`` reviews. The live file is never touched.
``staged``
    Written as ``shared/_inbox/<type>/<slug>.md`` — the default.
``not-a-page``
    ``format.from_portable`` refused the file: reported by path, never staged.
``failed``
    The write raised ``OSError``; the run steps over it and reports.

**The ledger** at ``<vault>/.mnemo/share/imports.json`` is keyed by
``<type>/<slug>``, not by share root: a rule is one thing in this vault
whichever path it arrived from, so the same tree imported from a sibling
clone and then from a path someone sent is a no-op the second time. Each
entry keeps the ``portable_hash`` last staged, the sha256 of the bytes this
command wrote and where it wrote them (so "still ours and unmodified" is a
byte comparison, not a guess), and the root it came from for the record.

**No extraction-state entries — deliberately.** ``branches/auto_promoted.py``
overwrites any live page that has no ``.mnemo/extraction-state.json`` entry
(#248), and an imported rule the user promotes with ``mv`` falls into that
hole like every hand-promoted page does. Writing an entry here would not
fix that: an entry's ``written_hash`` means "the extractor owns this file",
which is the opposite of the truth for an imported page, and would invite
the extractor to reconcile it. #248 is fixed where it lives, not here.
"""
from __future__ import annotations

import hashlib
import importlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.filters import (
    INBOX_DIR,
    derive_rule_slug,
    is_consumer_visible,
    iter_shared_pages,
    parse_frontmatter,
)

SCHEMA_VERSION = 1
LEDGER_REL = ".mnemo/share/imports.json"
PROPOSED_SUFFIX = ".proposed.md"

ACTIONS = ("staged", "proposed", "unchanged", "yours", "refused", "not-a-page", "failed")


def _fmt():
    """The format piece, resolved at call time.

    Lazy so this module imports in a tree where ``format`` has not landed
    yet, and so a test can put a stand-in under the module's name.
    """
    return importlib.import_module("mnemo.core.share.format")


@dataclass(frozen=True)
class ImportDecision:
    """One routing decision for one file in the tree."""

    source: Path
    action: str
    slug: str = ""
    page_type: str = ""
    target: Optional[Path] = None
    reason: str = ""


@dataclass(frozen=True)
class ImportReport:
    share_root: Path
    project: str
    dry_run: bool
    decisions: tuple = field(default_factory=tuple)

    def count(self, action: str) -> int:
        return sum(1 for d in self.decisions if d.action == action)

    @property
    def staged(self) -> int:
        return self.count("staged")

    @property
    def proposed(self) -> int:
        return self.count("proposed")

    @property
    def unchanged(self) -> int:
        return self.count("unchanged")

    @property
    def yours(self) -> int:
        return self.count("yours")

    @property
    def refused(self) -> int:
        return self.count("refused")

    @property
    def not_pages(self) -> int:
        return self.count("not-a-page")

    @property
    def failed(self) -> int:
        return self.count("failed")

    @property
    def wrote(self) -> int:
        return self.staged + self.proposed


# --- ledger ------------------------------------------------------------------

def ledger_path(vault_root: Path) -> Path:
    return Path(vault_root) / LEDGER_REL


def load_ledger(vault_root: Path) -> dict[str, Any]:
    """Read the ledger; a missing or corrupt file yields a clean one."""
    try:
        data = json.loads(ledger_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("rules"), dict):
        return {"schemaVersion": SCHEMA_VERSION, "rules": {}, "noticeShown": {}}
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    if not isinstance(data.get("noticeShown"), dict):
        data["noticeShown"] = {}
    return data


def save_ledger(vault_root: Path, led: dict[str, Any]) -> None:
    atomic_write_bytes(
        ledger_path(vault_root),
        (json.dumps(led, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _key(page_type: str, slug: str) -> str:
    return f"{page_type}/{slug}"


def _bytes_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def notice_shown(led: dict[str, Any], project: str, pending_digest: str) -> bool:
    """True when the session-start invitation for *this* pending set was shown.

    Per project, like backfill's ``firstRunNoticeShown`` — but keyed by a
    digest of what is pending rather than a bare flag, so a tree that gains
    rules after the first invitation invites again, and one the user read
    and chose not to import stays quiet. Never once-ever (#229).
    """
    shown = led.get("noticeShown")
    return isinstance(shown, dict) and shown.get(project) == pending_digest


def mark_notice_shown(vault_root: Path, project: str, pending_digest: str) -> None:
    led = load_ledger(vault_root)
    led["noticeShown"][project] = pending_digest
    save_ledger(vault_root, led)


# --- what is pending (shared with the session-start notice) ------------------

def pending_hashes(vault_root: Path, share_root: Path) -> list[str]:
    """Hashes in the tree the ledger has not seen and that are not this vault's.

    Read-only, sorted, cheap on a small tree. Never raises for an unreadable
    or missing tree: that is "nothing pending".
    """
    fmt = _fmt()
    share_root = Path(share_root)
    if not share_root.is_dir():
        return []
    led = load_ledger(vault_root)
    mine = fmt.vault_id(vault_root)
    out: list[str] = []
    for _path, rule in fmt.iter_portable(share_root):
        if rule is None or rule.vault == mine:
            continue
        entry = led["rules"].get(_key(rule.type, rule.slug))
        if isinstance(entry, dict) and entry.get("hash") == rule.hash:
            continue
        out.append(rule.hash)
    return sorted(out)


def pending_digest(hashes: list[str]) -> str:
    return _bytes_hash("\n".join(sorted(hashes)).encode("utf-8"))


# --- routing -----------------------------------------------------------------

def _visible_types_by_slug(vault_root: Path) -> dict[str, set[str]]:
    """slug → the page types it is live under, consumer-visible only."""
    shared = Path(vault_root) / "shared"
    out: dict[str, set[str]] = {}
    for md in iter_shared_pages(vault_root, include_inbox=False):
        rel = md.relative_to(shared).parts
        if len(rel) != 2:
            continue
        try:
            fm = parse_frontmatter(md.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not is_consumer_visible(md, fm, vault_root):
            continue
        out.setdefault(derive_rule_slug(fm, md.stem), set()).add(rel[0])
        # The file stem is the path other rules of this slug would collide
        # on, whatever the frontmatter says its slug is.
        out.setdefault(md.stem, set()).add(rel[0])
    return out


def _ours_and_unmodified(path: Path, entry: Optional[dict], vault_root: Path) -> bool:
    """The file at *path* is byte-for-byte what this command last wrote there."""
    if not isinstance(entry, dict):
        return False
    try:
        rel = path.relative_to(vault_root).as_posix()
    except ValueError:
        return False
    if entry.get("target") != rel or not entry.get("written"):
        return False
    try:
        return _bytes_hash(path.read_bytes()) == entry["written"]
    except OSError:
        return False


def run_import(
    vault_root: Path,
    *,
    share_root: Path,
    project: str,
    dry_run: bool,
    today: str,
) -> ImportReport:
    """Stage every changed rule in *share_root* into the vault's ``_inbox``.

    *project* is the **local** canonical project name; it is stamped as the
    page's ``projects:`` attribution (the publisher's name is provenance, not
    authority). *today* is the import date written into the page. With
    *dry_run* every decision is still made — including the on-disk checks —
    and nothing is written, the ledger included.
    """
    fmt = _fmt()
    vault_root = Path(vault_root)
    share_root = Path(share_root)
    shared = vault_root / "shared"
    led = load_ledger(vault_root)
    mine = fmt.vault_id(vault_root)
    types_by_slug = _visible_types_by_slug(vault_root)
    decisions: list[ImportDecision] = []
    dirty = False

    for source, rule in fmt.iter_portable(share_root):
        if rule is None:
            decisions.append(ImportDecision(
                source=source, action="not-a-page",
                reason="not a mnemo page (no provenance block)",
            ))
            continue
        slug, page_type = rule.slug, rule.type
        key = _key(page_type, slug)
        entry = led["rules"].get(key)

        if rule.vault == mine:
            decisions.append(ImportDecision(
                source=source, action="yours", slug=slug, page_type=page_type,
                reason="published by this vault",
            ))
            continue
        if isinstance(entry, dict) and entry.get("hash") == rule.hash:
            decisions.append(ImportDecision(
                source=source, action="unchanged", slug=slug, page_type=page_type,
                reason="already imported at this hash",
            ))
            continue
        other_types = sorted(types_by_slug.get(slug, set()) - {page_type})
        if other_types:
            where = ", ".join(f"shared/{t}/{slug}.md" for t in other_types)
            decisions.append(ImportDecision(
                source=source, action="refused", slug=slug, page_type=page_type,
                reason=f"slug already live under another type: {where}",
            ))
            continue

        live = shared / page_type / f"{slug}.md"
        inbox_dir = shared / INBOX_DIR / page_type
        if live.exists():
            target = inbox_dir / f"{slug}{PROPOSED_SUFFIX}"
            action = "proposed"
        else:
            target = inbox_dir / f"{slug}.md"
            action = "staged"

        if target.exists() and not _ours_and_unmodified(target, entry, vault_root):
            rel = target.relative_to(vault_root).as_posix()
            decisions.append(ImportDecision(
                source=source, action="refused", slug=slug, page_type=page_type,
                target=target,
                reason=f"{rel} exists and was not written by mnemo import (or was edited since)",
            ))
            continue

        text = fmt.to_vault_page(rule, project=project, today=today)
        data = text.encode("utf-8")
        if not dry_run:
            try:
                atomic_write_bytes(target, data)
            except OSError as exc:
                decisions.append(ImportDecision(
                    source=source, action="failed", slug=slug, page_type=page_type,
                    target=target, reason=f"{type(exc).__name__}: {exc}",
                ))
                continue
            led["rules"][key] = {
                "hash": rule.hash,
                "vault": rule.vault,
                "from": str(share_root),
                "target": target.relative_to(vault_root).as_posix(),
                "written": _bytes_hash(data),
                "at": today,
            }
            dirty = True
        decisions.append(ImportDecision(
            source=source, action=action, slug=slug, page_type=page_type, target=target,
            reason="live page exists; staged as a rewrite" if action == "proposed" else "",
        ))

    if dirty and not dry_run:
        save_ledger(vault_root, led)
    return ImportReport(
        share_root=share_root, project=project, dry_run=dry_run,
        decisions=tuple(decisions),
    )
