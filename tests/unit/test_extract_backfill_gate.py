"""Backfill-origin pages stage in _inbox instead of auto-promoting."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import llm as llm_mod
from mnemo.core.extract import _parse_pages_from_response, run_extraction
from mnemo.core.extract.inbox.paths import _target_path_for_page
from mnemo.core.extract.inbox.types import ExtractedPage


def _page(**kw) -> ExtractedPage:
    base = dict(
        slug="s", type="reference", name="n", description="d",
        body="b", source_files=["bots/a/memory/x.md"], source_hash="sha256:x",
    )
    base.update(kw)
    return ExtractedPage(**base)


def test_single_source_live_page_auto_promotes(tmp_path):
    target = _target_path_for_page(_page(), tmp_path)
    assert target == tmp_path / "shared" / "reference" / "s.md"


def test_single_source_backfill_page_stages_in_inbox(tmp_path):
    target = _target_path_for_page(_page(origin_backfill=True), tmp_path)
    assert target == tmp_path / "shared" / "_inbox" / "reference" / "s.md"


def test_multi_source_still_stages_regardless_of_origin(tmp_path):
    page = _page(source_files=["bots/a/memory/x.md", "bots/b/memory/y.md"])
    assert _target_path_for_page(page, tmp_path) == tmp_path / "shared" / "_inbox" / "reference" / "s.md"


def test_origin_backfill_defaults_false():
    assert _page().origin_backfill is False


def test_parser_flags_page_whose_sources_are_all_backfill():
    text = json.dumps({"pages": [{
        "slug": "s", "type": "reference", "name": "n", "description": "d",
        "body": "b", "source_files": ["bots/a/memory/x.md"],
    }]})
    pages = _parse_pages_from_response(
        text, "reference", backfill_sources=frozenset({"bots/a/memory/x.md"}),
    )
    assert pages[0].origin_backfill is True


def test_parser_flags_mixed_origin_page():
    """One reconstructed source is enough — see Task 6b.

    This asserted ``is False`` when the gate only governed routing, where
    ``all`` vs ``any`` made no observable difference (multi-source pages stage
    unconditionally). Once the gate also governs universal promotion, ``all``
    lets a page with one live and one backfill source cross two projects and
    land in the sacred dir unreviewed.
    """
    text = json.dumps({"pages": [{
        "slug": "s", "type": "reference", "name": "n", "description": "d",
        "body": "b", "source_files": ["bots/a/memory/x.md", "bots/a/memory/live.md"],
    }]})
    pages = _parse_pages_from_response(
        text, "reference", backfill_sources=frozenset({"bots/a/memory/x.md"}),
    )
    assert pages[0].origin_backfill is True


def test_parser_without_backfill_sources_flags_nothing():
    text = json.dumps({"pages": [{
        "slug": "s", "type": "reference", "name": "n", "description": "d",
        "body": "b", "source_files": ["bots/a/memory/x.md"],
    }]})
    pages = _parse_pages_from_response(text, "reference")
    assert pages[0].origin_backfill is False


# --- End-to-end: a real harvested file, read by the real scanner ------------
#
# The hand-built ``ExtractedPage`` tests above prove the routing but cannot
# prove the orchestrator reads the ``origin: backfill`` stamp correctly.
# ``scanner.parse_frontmatter`` is a flat line reader, so the nested
# ``metadata:`` block a harvested file carries flattens to a top-level
# ``origin`` key. Read it the wrong way (``fm["metadata"]["origin"]``) and the
# gate silently never fires. Only a run over a file harvest actually wrote,
# parsed by the scanner the orchestrator actually uses, catches that.


def _llm_response(pages: list[dict]) -> llm_mod.LLMResponse:
    text = json.dumps({"pages": pages})
    return llm_mod.LLMResponse(
        text=text,
        total_cost_usd=0.0,
        input_tokens=10,
        output_tokens=10,
        api_key_source="subscription",
        raw={"result": text},
    )


def _stub_llm(monkeypatch, pages: list[dict]) -> None:
    monkeypatch.setattr(llm_mod, "call", lambda *a, **kw: _llm_response(pages))


def _write_transcript(path: Path) -> Path:
    events = [
        {"type": "user", "message": {
            "role": "user", "content": [{"type": "text", "text": "no, use pathlib"}]}},
        {"type": "assistant", "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Edit", "id": "t0", "input": {}}]}},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return path


def _cfg(vault_root: Path) -> dict:
    return {
        "vaultRoot": str(vault_root),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "hintThreshold": 5,
            "preferAPI": False,
            "subprocessTimeout": 60,
            "costSoftCap": None,
        },
        "backfill": {"minFileMutations": 1},
    }


@pytest.fixture
def harvested_vault(tmp_vault: Path, tmp_path: Path, monkeypatch) -> Path:
    """A vault whose only memory file was written by a real harvest run."""
    from mnemo.core.backfill import harvest

    cfg = _cfg(tmp_vault)
    _stub_llm(monkeypatch, [{
        "slug": "prefer-pathlib",
        "type": "reference",
        "name": "Prefer pathlib",
        "description": "Path handling",
        "body": "Use pathlib, not os.path.",
    }])
    written = harvest.harvest_session(_write_transcript(tmp_path / "sess-1.jsonl"), "alpha", cfg)
    assert written, "fixture precondition: harvest wrote a memory file"
    return tmp_vault


def test_harvested_source_carries_a_flat_origin_key(harvested_vault: Path):
    """Guards the read the gate depends on: nested metadata flattens."""
    from mnemo.core.extract.scanner import _read_memory_file

    src = harvested_vault / "bots" / "alpha" / "memory" / "prefer-pathlib.md"
    mf = _read_memory_file(src, "alpha")

    assert mf.frontmatter.get("origin") == "backfill"
    # The nested-lookup spelling the gate must NOT use.
    assert mf.frontmatter.get("metadata") == ""


def test_single_source_harvested_file_stages_instead_of_auto_promoting(
    harvested_vault: Path, monkeypatch,
):
    """End-to-end: harvested file in, staged page out. One source, no promotion."""
    _stub_llm(monkeypatch, [{
        "slug": "prefer-pathlib",
        "type": "reference",
        "name": "Prefer pathlib",
        "description": "Path handling",
        "body": "Use pathlib, not os.path.",
        "source_files": ["bots/alpha/memory/prefer-pathlib.md"],
    }])

    summary = run_extraction(_cfg(harvested_vault))

    assert (harvested_vault / "shared" / "_inbox" / "reference" / "prefer-pathlib.md").exists()
    assert not (harvested_vault / "shared" / "reference" / "prefer-pathlib.md").exists()
    assert summary.auto_promoted == 0


def test_single_source_live_file_still_auto_promotes(tmp_vault: Path, monkeypatch):
    """The control: same shape without the stamp promotes as it always has."""
    mem = tmp_vault / "bots" / "alpha" / "memory"
    mem.mkdir(parents=True)
    (mem / "prefer-pathlib.md").write_text(
        "---\nname: Prefer pathlib\ntype: reference\n---\n\nUse pathlib, not os.path.\n",
        encoding="utf-8",
    )
    _stub_llm(monkeypatch, [{
        "slug": "prefer-pathlib",
        "type": "reference",
        "name": "Prefer pathlib",
        "description": "Path handling",
        "body": "Use pathlib, not os.path.",
        "source_files": ["bots/alpha/memory/prefer-pathlib.md"],
    }])

    run_extraction(_cfg(tmp_vault))

    assert (tmp_vault / "shared" / "reference" / "prefer-pathlib.md").exists()
