"""A page the reference judge held leaves ``shared/_inbox/`` on its own, undoably (#429).

#417 staged every inferred reference page the judge called generic or
narrative, "never dropped". But the queue has no exit anyone takes: 44
session-start offers over 2026-09-19..22 produced 0 decisions, so "staged"
meant "kept forever, invisible to recall". These pin the exit:

- the verdict is written on the page, because a run-only verdict leaves
  nothing for a later sweep to find;
- only that verdict expires a page — an evidence-gate demotion or a
  multi-source staging waits for a human however old it is;
- expiry archives exactly like ``drop`` and says so in the ledger under its
  own name, so ``resolved`` still counts human decisions only;
- ``restore`` undoes an expiry or a drop, and a restored page never expires
  again.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import time
from pathlib import Path

import pytest

from mnemo.core import inbox as I
from mnemo.core import llm as llm_mod
from mnemo.core.extract import reference_gate as rg
from mnemo.core.extract import run_extraction
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import parse_frontmatter


def _extracted(judged=None) -> ExtractedPage:
    return ExtractedPage(
        slug="p", type="reference", name="Name", description="d", body="Body.",
        source_files=["bots/a/memory/x.md"], source_hash="h", judged=judged,
    )


def _page(vault: Path, rel: str, *, age_days: float, extra: str = "") -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {path.stem}\nslug: {path.stem}\ndescription: d\n"
        f"type: {path.parent.name}\n{extra}sources:\n  - bots/demo/x.md\n---\n\nbody\n",
        encoding="utf-8",
    )
    # A minute past the day boundary: ages floor, and Windows' coarse clock can
    # read `now` a tick before the `time.time()` this was computed from.
    ts = time.time() - age_days * 86400 - 60
    os.utime(path, (ts, ts))
    return path


def _held(vault: Path, slug: str, *, age_days: float, label: str = "generic") -> Path:
    return _page(vault, f"shared/_inbox/reference/{slug}.md", age_days=age_days,
                 extra=f"reference_gate: {label}\n")


def _state(vault: Path, *keys: str) -> Path:
    path = vault / ".mnemo" / "extraction-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = {k: {"source_files": ["bots/demo/x.md"], "source_hash": "sh",
                   "written_hash": "wh", "written_at": "r", "last_sync": "r",
                   "status": "inbox"} for k in keys}
    path.write_text(json.dumps({"schema_version": 2, "last_run": "r", "entries": entries}),
                    encoding="utf-8")
    return path


def _status(vault: Path, key: str) -> str:
    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8"))
    return state["entries"][key]["status"]


def _events(vault: Path) -> list[tuple[str, str]]:
    return [(r["event"], r["key"]) for r in I.read_ledger(vault)]


# --- the verdict is written on the page -------------------------------------


@pytest.mark.parametrize("judged,label", [("G", "generic"), ("N", "narrative")])
def test_a_held_verdict_is_written_on_the_staged_page(judged, label):
    fm, _ = parse_frontmatter(_render_page(_extracted(judged), run_id="r"))

    assert fm["reference_gate"] == label
    assert rg.is_held_frontmatter(fm)


@pytest.mark.parametrize("judged,label", [("T", "technique"), ("S", "system")])
def test_a_kept_verdict_on_a_staged_page_says_so_and_does_not_expire(judged, label):
    """#432: a staged T/S page (a demotion, a multi-source page) carries the
    judge's word too, and that word is "keep": not expirable."""
    fm, _ = parse_frontmatter(_render_page(_extracted(judged), run_id="r"))

    assert fm["reference_gate"] == label
    assert not rg.is_held_frontmatter(fm)


@pytest.mark.parametrize("judged", ["", None])
def test_no_stamp_for_a_page_the_judge_never_answered(judged):
    """``""`` is a judge that failed: nobody judged that page, so nothing may
    expire it on the judge's word."""
    fm, _ = parse_frontmatter(_render_page(_extracted(judged), run_id="r"))

    assert "reference_gate" not in fm
    assert not rg.is_held_frontmatter(fm)


def test_a_live_rewrite_carries_no_stamp():
    """A page already live is never held (#417), and a re-emission rewrites it
    live — the judge's word on it is not this gate's to record there."""
    text = _render_page(_extracted("G"), run_id="r", auto_promoted=True)

    assert "reference_gate" not in parse_frontmatter(text)[0]


def _extraction_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "shared").mkdir(parents=True)
    d = root / "bots" / "agent" / "memory"
    d.mkdir(parents=True)
    (d / "one.md").write_text(
        "---\nname: one\ntype: reference\ndescription: d\n---\n\nbody one\n", encoding="utf-8",
    )
    return root


def _cfg(root: Path, **inbox) -> dict:
    return {
        "vaultRoot": str(root),
        "extraction": {"model": "m", "chunkSize": 10, "subprocessTimeout": 60,
                       "referenceGate": {"enabled": True, "model": "judge-model"}},
        "inbox": inbox,
    }


def _stub_llm(monkeypatch, verdict: str) -> None:
    pages = [{"slug": "dependency-injection", "type": "reference", "name": "DI is good",
              "description": "d", "body": "Inject dependencies.",
              "source_files": ["bots/agent/memory/one.md"]}]

    def call(prompt, *, system, model, timeout):
        payload = ({"verdicts": [{"i": 1, "cat": verdict}]} if system == rg.SYSTEM_PROMPT
                   else {"pages": pages})
        return llm_mod.LLMResponse(text=json.dumps(payload), total_cost_usd=0.0,
                                   input_tokens=1, output_tokens=1,
                                   api_key_source="none", raw={})

    monkeypatch.setattr(llm_mod, "call", call)


def test_extraction_stages_a_generic_page_with_its_verdict_on_it(tmp_path, monkeypatch):
    root = _extraction_vault(tmp_path)
    _stub_llm(monkeypatch, "G")

    run_extraction(_cfg(root))

    staged = root / "shared" / "_inbox" / "reference" / "dependency-injection.md"
    fm, _ = parse_frontmatter(staged.read_text(encoding="utf-8"))
    assert fm["reference_gate"] == "generic"
    assert [p.key for p in I.staged_pages(root) if p.gate_held] == ["reference/dependency-injection"]


# --- what expires -----------------------------------------------------------


def test_a_held_page_expires_after_its_days_like_a_drop(tmp_vault: Path):
    _held(tmp_vault, "old", age_days=15)
    _state(tmp_vault, "reference/old")

    results = I.expire_held(tmp_vault, days=14)

    assert [r.ok for r in results] == [True]
    assert not (tmp_vault / "shared" / "_inbox" / "reference" / "old.md").exists()
    archived = results[0].moved_to
    assert archived.parent.parent.name.startswith(I.EXPIRED_ARCHIVE_PREFIX)
    assert "reference_gate: generic" in archived.read_text(encoding="utf-8")
    # `dismissed`, as a drop writes: the extractor will not stage it again.
    assert _status(tmp_vault, "reference/old") == "dismissed"
    assert _events(tmp_vault) == [(I.EXPIRED, "reference/old")]


def test_a_held_page_younger_than_its_days_stays(tmp_vault: Path):
    _held(tmp_vault, "young", age_days=13)

    assert I.expire_held(tmp_vault, days=14) == []
    assert (tmp_vault / "shared" / "_inbox" / "reference" / "young.md").exists()


def test_a_page_staged_for_any_other_reason_waits_for_a_human(tmp_vault: Path):
    """An unstamped page — a demotion from before #432, a multi-source page —
    nobody judged: the judge's word is what licenses an expiry."""
    _page(tmp_vault, "shared/_inbox/reference/demoted.md", age_days=60,
          extra="demoted_from: feedback\n")
    _page(tmp_vault, "shared/_inbox/feedback/multi.md", age_days=60)

    assert I.expire_held(tmp_vault, days=14) == []
    assert len(I.staged_pages(tmp_vault)) == 2


def test_a_generic_demotion_expires_and_a_kept_one_waits(tmp_vault: Path):
    """#432: the stamp, not the reason it staged, decides."""
    for slug, label in (("generic", "generic"), ("technique", "technique"),
                        ("system", "system")):
        _page(tmp_vault, f"shared/_inbox/reference/{slug}.md", age_days=60,
              extra=f"demoted_from: feedback\nreference_gate: {label}\n")

    results = I.expire_held(tmp_vault, days=14)

    assert [r.moved_to.stem for r in results] == ["generic"]
    assert sorted(p.slug for p in I.staged_pages(tmp_vault)) == ["system", "technique"]


def test_zero_days_turns_expiry_off(tmp_vault: Path):
    _held(tmp_vault, "old", age_days=400)

    assert I.expire_held(tmp_vault, days=0) == []
    assert len(I.staged_pages(tmp_vault)) == 1


def test_a_narrative_page_expires_too(tmp_vault: Path):
    _held(tmp_vault, "story", age_days=20, label="narrative")

    assert [r.ok for r in I.expire_held(tmp_vault, days=14)] == [True]


def test_expiry_is_not_a_human_decision_in_the_stats(tmp_vault: Path):
    _held(tmp_vault, "old", age_days=15)
    I.expire_held(tmp_vault, days=14)

    stats = I.stats(tmp_vault)

    assert stats["expired"] == 1
    assert stats["resolved"] == 0 and stats["dropped"] == 0


# --- restore ----------------------------------------------------------------


def test_restore_puts_an_expired_page_back_and_it_never_expires_again(tmp_vault: Path):
    held = _held(tmp_vault, "old", age_days=15)
    before = held.read_bytes()
    _state(tmp_vault, "reference/old")
    I.expire_held(tmp_vault, days=14)

    result = I.restore(tmp_vault, "reference/old")

    assert result.ok, result.message
    assert held.read_bytes() == before
    assert _status(tmp_vault, "reference/old") == "inbox"
    assert _events(tmp_vault)[-1] == (I.RESTORED, "reference/old")
    # Still older than the window, and still stamped: a human pulled it back.
    assert I.expire_held(tmp_vault, days=14) == []
    assert held.exists()


def test_restore_undoes_a_drop_too(tmp_vault: Path):
    _page(tmp_vault, "shared/_inbox/reference/kept.md", age_days=1)
    (page,) = I.staged_pages(tmp_vault)
    I.drop(tmp_vault, page)

    assert I.restore(tmp_vault, "reference/kept").ok
    assert [p.key for p in I.staged_pages(tmp_vault)] == ["reference/kept"]


def test_restore_takes_the_newest_archived_copy(tmp_vault: Path):
    for stamp, body in (("20260901T000000", "older"), ("20260920T000000", "newer")):
        d = tmp_vault / "shared" / "_archive" / f"{I.DROPPED_ARCHIVE_PREFIX}{stamp}" / "reference"
        d.mkdir(parents=True)
        (d / "twice.md").write_text(f"---\nname: twice\n---\n\n{body}\n", encoding="utf-8")

    assert I.restore(tmp_vault, "reference/twice").ok
    text = (tmp_vault / "shared" / "_inbox" / "reference" / "twice.md").read_text(encoding="utf-8")
    assert "newer" in text


def test_restore_refuses_to_overwrite_a_page_that_is_back(tmp_vault: Path):
    _held(tmp_vault, "old", age_days=15)
    I.expire_held(tmp_vault, days=14)
    _page(tmp_vault, "shared/reference/old.md", age_days=0)

    result = I.restore(tmp_vault, "reference/old")

    assert not result.ok and "already" in result.message
    assert not (tmp_vault / "shared" / "_inbox" / "reference" / "old.md").exists()


def test_restore_of_a_key_never_archived_fails_loudly(tmp_vault: Path):
    result = I.restore(tmp_vault, "reference/nothing")

    assert not result.ok and "reference/nothing" in result.message
    assert _events(tmp_vault) == []


# --- wired into extraction and the CLI --------------------------------------


def test_an_extraction_run_expires_the_held_pages_it_finds(tmp_path, monkeypatch):
    root = _extraction_vault(tmp_path)
    _held(root, "old", age_days=15)
    _stub_llm(monkeypatch, "T")

    summary = run_extraction(_cfg(root))

    assert summary.reference_expired == 1
    assert not (root / "shared" / "_inbox" / "reference" / "old.md").exists()


def test_the_window_is_read_from_config(tmp_path, monkeypatch):
    root = _extraction_vault(tmp_path)
    _held(root, "old", age_days=15)
    _stub_llm(monkeypatch, "T")

    summary = run_extraction(_cfg(root, heldExpiryDays=30))

    assert summary.reference_expired == 0
    assert (root / "shared" / "_inbox" / "reference" / "old.md").exists()


def test_a_dry_run_expires_nothing(tmp_path, monkeypatch):
    root = _extraction_vault(tmp_path)
    _held(root, "old", age_days=15)
    _stub_llm(monkeypatch, "T")

    run_extraction(_cfg(root), dry_run=True)

    assert (root / "shared" / "_inbox" / "reference" / "old.md").exists()


def test_the_default_window_is_fourteen_days():
    from mnemo.core.config import DEFAULTS

    assert DEFAULTS["inbox"]["heldExpiryDays"] == 14
    assert I.held_expiry_days({}) == 14
    assert I.held_expiry_days({"inbox": {"heldExpiryDays": "junk"}}) == 14


def _cli(vault: Path, monkeypatch, **flags) -> tuple[int, str]:
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    from mnemo.cli.parser import COMMANDS

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    defaults = {"all": False, "show": None, "promote": None, "drop": None,
                "restore": None, "stats": False}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = COMMANDS["inbox"](argparse.Namespace(**{**defaults, **flags}))
    return rc, buf.getvalue()


def test_restore_through_the_cli(tmp_vault: Path, monkeypatch):
    from mnemo.cli.parser import _build_parser

    assert _build_parser().parse_args(["inbox", "--restore", "reference/old"]).restore == "reference/old"
    _held(tmp_vault, "old", age_days=15)
    I.expire_held(tmp_vault, days=14)

    rc, out = _cli(tmp_vault, monkeypatch, restore="reference/old")

    assert rc == 0 and "restored reference/old" in out
    rc, out = _cli(tmp_vault, monkeypatch, restore="reference/old")
    assert rc == 1


def test_stats_through_the_cli_counts_what_expired(tmp_vault: Path, monkeypatch):
    _held(tmp_vault, "old", age_days=15)
    I.expire_held(tmp_vault, days=14)

    rc, out = _cli(tmp_vault, monkeypatch, stats=True)

    assert rc == 0 and "expired" in out


def test_mnemo_extract_reports_what_expired(tmp_vault: Path, tmp_home, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.core.extract import ExtractionSummary

    summary = ExtractionSummary()
    summary.reference_expired = 3
    monkeypatch.setattr("mnemo.core.extract.run_extraction", lambda cfg, **_kw: summary)
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({"vaultRoot": str(tmp_vault)}), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))

    assert cli.main(["extract"]) == 0
    assert "reference expired: 3" in capsys.readouterr().out
