"""#418: ``mnemo redact`` finds secrets already on disk and never prints them."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import redact_vault as RV
from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.state_io import atomic_write_state, load_state
from mnemo.core.extract.scanner import ExtractionState, StateEntry

GOOGLE_KEY = "AIza" + "Sy" + "B7xQ-k_9" * 4 + "a"
PASSWORD = "Tr0ub4dor&3"

REFERENCE = f"""---
name: Staging logins
type: reference
---

Admin: `qa.admin@acme-corp.io` / `{PASSWORD}`
"""
BRIEFING = f"""---
type: briefing
---

## TL;DR
Wired maps with key {GOOGLE_KEY}.
"""
MEMORY = f"Password: `{PASSWORD}`\n"


def _seed(vault: Path) -> dict:
    files = {
        "shared/reference/staging-logins.md": REFERENCE,
        "bots/proj/briefings/sessions/s1.md": BRIEFING,
        "bots/proj/memory/project_x.md": MEMORY,  # the user's auto-memory mirror
        "shared/feedback/clean.md": "---\nname: clean\n---\n\nUse yarn.\n",
    }
    for rel, text in files.items():
        p = vault / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return files


def test_scan_reports_file_line_and_kind(tmp_vault: Path):
    _seed(tmp_vault)
    report = RV.scan(tmp_vault)
    assert report.files_scanned == 3  # the memory mirror is not walked
    assert [(h.path, h.line, h.kind) for h in report.hits] == [
        ("shared/reference/staging-logins.md", 6, "credential pair"),
        ("bots/proj/briefings/sessions/s1.md", 6, "Google API key"),
    ]


def test_scan_writes_nothing(tmp_vault: Path):
    files = _seed(tmp_vault)
    RV.scan(tmp_vault)
    for rel, text in files.items():
        assert (tmp_vault / rel).read_text(encoding="utf-8") == text


def test_apply_redacts_pages_and_briefings_but_never_the_memory_mirror(tmp_vault: Path):
    _seed(tmp_vault)
    report = RV.apply(tmp_vault)
    assert sorted(report.rewritten) == [
        "bots/proj/briefings/sessions/s1.md", "shared/reference/staging-logins.md",
    ]
    ref = (tmp_vault / "shared/reference/staging-logins.md").read_text(encoding="utf-8")
    assert PASSWORD not in ref and "qa.admin@acme-corp.io" in ref
    assert GOOGLE_KEY not in (tmp_vault / "bots/proj/briefings/sessions/s1.md").read_text(encoding="utf-8")
    assert (tmp_vault / "bots/proj/memory/project_x.md").read_text(encoding="utf-8") == MEMORY
    assert RV.scan(tmp_vault).hits == []


def test_apply_moves_written_hash_so_the_page_still_reads_as_untouched(tmp_vault: Path):
    _seed(tmp_vault)
    page = tmp_vault / "shared/reference/staging-logins.md"
    state_path = tmp_vault / ".mnemo" / "extraction-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = ExtractionState(last_run=None, entries={
        "reference/staging-logins": StateEntry(
            source_files=["bots/proj/briefings/sessions/s1.md"], source_hash="s",
            written_hash=content_hash(page), written_at="t", status="direct"),
        "reference/hand-edited": StateEntry(
            source_files=[], source_hash="s", written_hash="sha256:other",
            written_at="t", status="direct"),
    })
    atomic_write_state(state, state_path)

    RV.apply(tmp_vault)

    after = load_state(state_path)
    assert after.entries["reference/staging-logins"].written_hash == content_hash(page)
    assert after.entries["reference/hand-edited"].written_hash == "sha256:other"


def test_apply_refuses_while_an_extraction_holds_the_vault(tmp_vault: Path):
    _seed(tmp_vault)
    (tmp_vault / ".mnemo" / "extract.lock").mkdir(parents=True)
    with pytest.raises(RV.VaultBusy):
        RV.apply(tmp_vault)
    assert PASSWORD in (tmp_vault / "shared/reference/staging-logins.md").read_text(encoding="utf-8")


# --- the command -------------------------------------------------------------


def test_cli_report_never_prints_a_value(tmp_vault: Path, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.cli import main

    _seed(tmp_vault)
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    assert main(["redact"]) == 0
    out = capsys.readouterr().out
    assert "shared/reference/staging-logins.md: line 6 (credential pair)" in out
    assert "2 secrets in 2 files (3 scanned)" in out
    assert PASSWORD not in out and GOOGLE_KEY not in out
    assert PASSWORD in (tmp_vault / "shared/reference/staging-logins.md").read_text(encoding="utf-8")


def test_cli_json_and_apply(tmp_vault: Path, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.cli import main

    _seed(tmp_vault)
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    assert main(["redact", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["files"] == 2 and payload["rewritten"] == []
    assert PASSWORD not in json.dumps(payload)

    assert main(["redact", "--apply"]) == 0
    assert "Redacted 2 secrets in 2 files" in capsys.readouterr().out
    assert main(["redact"]) == 0
    assert "No secrets found in 3 pages and briefings." in capsys.readouterr().out
