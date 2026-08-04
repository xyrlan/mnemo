"""A backfill page must not reach shared/ via universal promotion.

Task 6 gated the *routing* door (``_target_path_for_page``). Universal
promotion is a second door into the sacred dir: once a staged page's merged
sources span ``scoping.universalThreshold`` distinct projects it is moved
into ``shared/<type>/`` with no human review. Two entry points do that — the
apply-time predicate and the end-of-extract reconciler — and both must
consult the origin stamp, which means the stamp has to survive into the
staged file on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core import llm as llm_mod
from mnemo.core.extract import run_extraction


def _resp(pages):
    return llm_mod.LLMResponse(
        text=json.dumps({"pages": pages}), total_cost_usd=0.0,
        input_tokens=1, output_tokens=1, api_key_source="subscription", raw={},
    )


def _memory_file(root: Path, agent: str, *, origin: str | None) -> None:
    stamp = f"metadata:\n  origin: {origin}\n" if origin else ""
    d = root / "bots" / agent / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / "prefer-pathlib.md").write_text(
        f"---\nname: Prefer pathlib\ntype: feedback\n{stamp}---\n\nUse pathlib.\n",
        encoding="utf-8",
    )


def _vault(tmp_path: Path, *, origin: str | None) -> Path:
    root = tmp_path / "vault"
    (root / "shared").mkdir(parents=True)
    (root / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(root)}))
    for agent in ("alpha", "beta"):
        _memory_file(root, agent, origin=origin)
    return root


def _cfg(root: Path) -> dict:
    return {"vaultRoot": str(root), "extraction": {
        "model": "m", "chunkSize": 10, "hintThreshold": 5,
        "preferAPI": False, "subprocessTimeout": 60, "costSoftCap": None}}


def _stub(monkeypatch):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp([{
        "slug": "prefer-pathlib", "type": "feedback", "name": "Prefer pathlib",
        "description": "d", "body": "Use pathlib.",
        "source_files": ["bots/alpha/memory/prefer-pathlib.md",
                         "bots/beta/memory/prefer-pathlib.md"],
    }]))


def test_backfill_page_never_reaches_sacred_dir(tmp_path, monkeypatch):
    root = _vault(tmp_path, origin="backfill")
    _stub(monkeypatch)
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists()
    assert (root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md").exists()


def test_backfill_page_still_blocked_on_a_second_extract(tmp_path, monkeypatch):
    """The reconciler runs on every extract — one pass proves nothing."""
    root = _vault(tmp_path, origin="backfill")
    _stub(monkeypatch)
    run_extraction(_cfg(root))
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists()


def test_mixed_origin_page_never_reaches_sacred_dir(tmp_path, monkeypatch):
    """One backfill source + one live source is still partly reconstructed.

    This is the case the ``all`` -> ``any`` change in
    ``_parse_pages_from_response`` exists for: under ``all`` the page carries
    ``origin_backfill=False``, spans two projects, and walks straight past
    both promotion gates.
    """
    root = tmp_path / "vault"
    (root / "shared").mkdir(parents=True)
    (root / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(root)}))
    _memory_file(root, "alpha", origin="backfill")
    _memory_file(root, "beta", origin=None)
    _stub(monkeypatch)
    run_extraction(_cfg(root))
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists()
    assert (root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md").exists()


# --- The flag must survive every page reconstruction ------------------------
#
# Three helpers rebuild an ExtractedPage field-by-field. Any of them that
# forgets ``origin_backfill`` silently reopens the gate.


def _page(**kw):
    from mnemo.core.extract.inbox.types import ExtractedPage
    base = dict(
        slug="s", type="feedback", name="n", description="d", body="b",
        source_files=["bots/a/memory/x.md"], source_hash="sha256:x",
    )
    base.update(kw)
    return ExtractedPage(**base)


def test_dedupe_by_slug_keeps_the_backfill_flag(tmp_path):
    """Cross-chunk merge of the same slug must not launder the stamp.

    Reachable: two chunks emitting one slug off the same single source merge
    to a one-source page, which routes straight to shared/ if the flag is lost.
    """
    from mnemo.core.extract.inbox.dedup import dedupe_by_slug
    from mnemo.core.extract.inbox.paths import _target_path_for_page

    merged = dedupe_by_slug([_page(origin_backfill=True), _page(body="b2")])

    assert len(merged) == 1
    assert merged[0].origin_backfill is True
    assert _target_path_for_page(merged[0], tmp_path) == (
        tmp_path / "shared" / "_inbox" / "feedback" / "s.md"
    )


def test_union_with_prior_sources_keeps_the_backfill_flag():
    from mnemo.core.extract.inbox.sources import union_with_prior_sources
    from mnemo.core.extract.scanner import StateEntry

    entry = StateEntry(
        source_files=["bots/b/memory/y.md"], source_hash="sha256:y",
        written_hash="", written_at="", status="inbox",
    )
    merged = union_with_prior_sources(_page(origin_backfill=True), entry)

    assert merged.source_files == ["bots/b/memory/y.md", "bots/a/memory/x.md"]
    assert merged.origin_backfill is True


def test_rendered_backfill_page_round_trips_through_parse_frontmatter():
    """Top-level key, not nested — and pinned at the text level.

    ``scanner.parse_frontmatter`` is flat: it lifts nested keys to the top
    level, so it alone cannot tell ``origin: backfill`` from
    ``metadata:\\n  origin: backfill``. The doctor advisory reads this same
    staged file with the nesting-aware ``filters.parse_frontmatter``, which
    can, so the writer's spelling has to be asserted directly.
    """
    from mnemo.core.extract.inbox.rendering import _render_page
    from mnemo.core.extract.scanner import parse_frontmatter
    from mnemo.core.filters import parse_frontmatter as filters_parse

    text = _render_page(_page(origin_backfill=True), run_id="r")
    assert "\norigin: backfill\n" in text, "stamp must be written top-level"
    assert filters_parse(text).get("origin") == "backfill"
    fm, _ = parse_frontmatter(text)
    assert fm.get("origin") == "backfill"

    live_text = _render_page(_page(), run_id="r")
    fm_live, _ = parse_frontmatter(live_text)
    assert fm_live.get("origin") is None
    assert filters_parse(live_text).get("origin") is None


def test_live_cross_project_page_still_universally_promotes(tmp_path, monkeypatch):
    """Control: the gate must not break universal promotion for live memory."""
    root = _vault(tmp_path, origin=None)
    _stub(monkeypatch)
    run_extraction(_cfg(root))
    run_extraction(_cfg(root))

    assert (root / "shared" / "feedback" / "prefer-pathlib.md").exists()
