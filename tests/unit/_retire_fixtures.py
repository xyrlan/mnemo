"""A small vault where one correction contradicts one rule.

Shared by ``test_friction_retire.py`` and ``test_retired_surfaces.py``. The
contradicted rule and its successor are the ``merge-requires-admin`` pair the
design was measured on: near-identical vocabulary, opposite instruction.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.friction import ledger
from mnemo.core.friction.ledger import FrictionRecord

PROJECT = "mnemo"
SID = "e7fb983c-0000-4000-8000-000000000001"
BRIEFING = f"bots/{PROJECT}/briefings/sessions/{SID}.md"
QUOTE = "nao precisa de --admin, roda o merge normal e ve se passa"

OLD = "merge-requires-admin"
NEW = "merge-requires-admin-corrected"


def write_rule(vault: Path, slug: str, *, body: str, tags=("git", "github"),
               sources=(BRIEFING,), evidence_quote: str | None = None,
               extra: str = "", page_type: str = "feedback") -> Path:
    d = vault / "shared" / page_type
    d.mkdir(parents=True, exist_ok=True)
    src = "".join(f"  - {s}\n" for s in sources)
    tag = "".join(f"  - {t}\n" for t in tags)
    ev = ""
    if evidence_quote is not None:
        ev = f"evidence:\n  quote: '{evidence_quote}'\n  source: {BRIEFING}\n"
    path = d / f"{slug}.md"
    path.write_text(
        f"---\nname: {slug}\ndescription: how to merge a pull request on master\n"
        f"type: {page_type}\nstability: stable\ntags:\n{tag}sources:\n{src}"
        f"{ev}{extra}---\n{body}\n",
        encoding="utf-8",
    )
    return path


def seed(vault: Path) -> dict[str, Path]:
    """The contradicted rule, its replacement, and noise; ledger not written."""
    briefing = vault / BRIEFING
    briefing.parent.mkdir(parents=True, exist_ok=True)
    briefing.write_text("# briefing\n", encoding="utf-8")
    pages = {
        OLD: write_rule(
            vault, OLD,
            body="Merging a pull request on master requires the admin flag: "
                 "run gh pr merge with --admin because code owner approval blocks it.",
        ),
        NEW: write_rule(
            vault, NEW,
            body="Merging a pull request on master does not require the admin flag: "
                 "run gh pr merge normally and check that it passes.",
            evidence_quote=QUOTE,
        ),
    }
    for i, (slug, body, tag) in enumerate([
        ("use-yarn", "Prefer yarn over npm for installing packages.", "yarn"),
        ("small-commits", "Write small atomic commits with clear messages.", "git"),
        ("pep8", "Follow PEP8 and black formatting in python code.", "python"),
    ]):
        pages[slug] = write_rule(vault, slug, body=body, tags=(tag,),
                                 sources=(f"bots/{PROJECT}/memory/n{i}.md",))
    return pages


def record(vault: Path, *, quote: str = QUOTE, contradicts=(OLD,),
           link_basis: str = ledger.LINK_EXTRACTOR, session_id: str = SID,
           ts: str = "2026-09-16T02:50:04Z") -> FrictionRecord:
    """Append a linked record to the ledger and return it with its id."""
    rec = FrictionRecord(
        ts=ts, session_id=session_id, project=PROJECT, quote=quote,
        rule_text="Run gh pr merge without --admin.", briefing=BRIEFING,
        contradicts=list(contradicts), link_basis=link_basis,
    )
    rec_id = ledger.record_id(rec)
    written = ledger.record(vault, rec)
    assert written == rec_id, "ledger refused the fixture row; is telemetry on?"
    from dataclasses import replace

    return replace(rec, id=rec_id)
