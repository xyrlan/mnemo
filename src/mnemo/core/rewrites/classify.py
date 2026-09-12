"""Diff each staged ``.proposed.md`` against its live rule and label it.

Frontmatter is excluded from the body diff on purpose: every proposal restamps
``extraction_run`` and ``promoted_at``, so including frontmatter would make all
35 look like rewrites. The interesting question is what happens to the prose.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from mnemo.core.filters import INBOX_DIR, is_proposed_sibling
from mnemo.core.rewrites.types import Kind, Rewrite

PROPOSED_SUFFIX = ".proposed.md"


def _split_body(text: str) -> str:
    """Return everything after the closing frontmatter ``---``."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def _live_for(proposal: Path, vault_root: Path) -> Path:
    """``shared/_inbox/<type>/<slug>.proposed.md`` → ``shared/<type>/<slug>.md``.

    Assumes the real layout: exactly one directory level under ``_inbox``, whose
    name is the page type. The caller walks with ``rglob``, so a proposal nested
    deeper would resolve its type to the wrong directory name and find no live
    file — it is then skipped rather than mis-paired. If sub-namespaced inbox
    folders ever land, this is the function that has to learn about them.
    """
    page_type = proposal.parent.name
    slug = proposal.name[: -len(PROPOSED_SUFFIX)]
    return vault_root / "shared" / page_type / f"{slug}.md"


def _classify_bodies(live_body: str, proposal_body: str) -> tuple[Kind, float, int, int]:
    live = live_body.splitlines()
    prop = proposal_body.splitlines()
    matcher = difflib.SequenceMatcher(None, live, prop, autojunk=False)
    opcodes = matcher.get_opcodes()

    changed = {tag for tag, *_ in opcodes if tag != "equal"}
    inserted = sum(j2 - j1 for tag, _i1, _i2, j1, j2 in opcodes if tag in ("insert", "replace"))
    dropped = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in opcodes if tag in ("delete", "replace"))

    live_nonblank = len([ln for ln in live if ln.strip()])
    kept_nonblank = sum(
        len([ln for ln in live[i1:i2] if ln.strip()])
        for tag, i1, i2, _j1, _j2 in opcodes
        if tag == "equal"
    )
    # An empty live body keeps 1.0 vacuously: there is no live prose to lose, so
    # "no live content dropped" holds. That routes a proposal against an empty
    # or stub live rule to ``insert_only`` rather than ``full_rewrite``, which is
    # the safe direction — merging cannot discard what was never there.
    keep_ratio = kept_nonblank / live_nonblank if live_nonblank else 1.0

    if changed <= {"insert"}:
        kind: Kind = "insert_only"
    elif keep_ratio == 0.0:
        kind = "full_rewrite"
    else:
        kind = "mixed"
    return kind, keep_ratio, inserted, dropped


def classify(vault_root: Path) -> list[Rewrite]:
    """Every staged rewrite under ``shared/_inbox/`` that has a live counterpart.

    A proposal with no live rule is skipped: there is nothing to merge into, and
    such a file belongs to the plain-staged-page path, not here.
    """
    vault_root = Path(vault_root)
    inbox = vault_root / "shared" / INBOX_DIR
    if not inbox.is_dir():
        return []

    out: list[Rewrite] = []
    for proposal in sorted(inbox.rglob("*.md")):
        if not is_proposed_sibling(proposal):
            continue
        if not proposal.name.endswith(PROPOSED_SUFFIX):
            # ``.update-proposed.md`` has a different provenance (an _inbox
            # target that vanished while the promoted file survived) and no
            # reliable live counterpart. Left for a follow-up.
            continue
        live = _live_for(proposal, vault_root)
        if not live.is_file():
            continue
        # Deliberately unguarded. An earlier draft swallowed OSError and
        # ``continue``d, which made an unreadable proposal vanish from the
        # plan with no signal at all — nothing then distinguished "no
        # rewrites are staged" from "every one of them failed to read." This
        # command exists to make an invisible backlog visible, so a loud
        # crash beats a silent gap. An unreadable file among the staged set
        # is exceptional, not routine.
        live_text = live.read_text(encoding="utf-8", errors="replace")
        prop_text = proposal.read_text(encoding="utf-8", errors="replace")
        kind, keep_ratio, inserted, dropped = _classify_bodies(
            _split_body(live_text), _split_body(prop_text)
        )
        page_type = proposal.parent.name
        slug = proposal.name[: -len(PROPOSED_SUFFIX)]
        out.append(Rewrite(
            proposal=proposal,
            live=live,
            key=f"{page_type}/{slug}",
            kind=kind,
            keep_ratio=keep_ratio,
            inserted_lines=inserted,
            dropped_lines=dropped,
        ))
    return out
