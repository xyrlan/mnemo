"""#418: every path that writes a ``shared/`` page or a briefing redacts secrets.

Values are synthetic; the Google key is assembled at runtime.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import briefing as briefing_mod
from mnemo.core import llm as llm_mod
from mnemo.core.extract import evidence, promote, scanner
from mnemo.core.extract import _redact_quote
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.filters import parse_frontmatter
from mnemo.core.share import from_portable, to_portable, to_vault_page

GOOGLE_KEY = "AIza" + "Sy" + "B7xQ-k_9" * 4 + "a"
PASSWORD = "Tr0ub4dor&3"


# --- project pages from mirrored auto-memory ---------------------------------


def _memory_file(vault: Path, body: str) -> scanner.MemoryFile:
    mem = vault / "bots" / "clubinho" / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    path = mem / "project_onboarding_accounts.md"
    path.write_text(
        "---\nname: Onboarding accounts\ndescription: test logins\ntype: project\n---\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return scanner._read_memory_file(path, agent="clubinho")


def test_project_page_drops_the_password_and_keeps_the_login(tmp_vault: Path):
    body = f"Admin: `qa.admin@acme-corp.io` / `{PASSWORD}`\nMaps key {GOOGLE_KEY}"
    f = _memory_file(tmp_vault, body)
    promote.promote_projects([f], scanner.ExtractionState(last_run=None, entries={}), tmp_vault)
    page = (tmp_vault / "shared" / "project" / "clubinho__onboarding-accounts.md").read_text(encoding="utf-8")
    assert PASSWORD not in page and GOOGLE_KEY not in page
    assert "qa.admin@acme-corp.io" in page
    assert page.count("[redacted]") == 2


def test_the_source_memory_file_is_never_rewritten(tmp_vault: Path):
    """It is the user's own auto-memory; only the derived page is redacted."""
    f = _memory_file(tmp_vault, f"Password: `{PASSWORD}`")
    before = f.path.read_bytes()
    promote.promote_projects([f], scanner.ExtractionState(last_run=None, entries={}), tmp_vault)
    assert f.path.read_bytes() == before


def test_a_redacted_project_page_is_not_mistaken_for_a_user_edit(tmp_vault: Path):
    """``written_hash`` is of the redacted page, so the next run overwrites it
    rather than filing a ``.proposed`` sibling."""
    state = scanner.ExtractionState(last_run=None, entries={})
    promote.promote_projects([_memory_file(tmp_vault, f"Password: `{PASSWORD}` v1")], state, tmp_vault)
    result = promote.promote_projects([_memory_file(tmp_vault, f"Password: `{PASSWORD}` v2")], state, tmp_vault)
    assert result.overwrite_safe == ["project/clubinho__onboarding-accounts"]
    assert result.sibling_proposed == []


# --- briefings ---------------------------------------------------------------


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: root)
    return root


def _session(tmp_path: Path, password_turn: str) -> Path:
    events = [
        {"type": "user", "timestamp": "2026-09-01T10:00:00.000Z",
         "message": {"role": "user", "content": "set up the staging login"}},
        {"type": "assistant", "timestamp": "2026-09-01T10:01:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Write", "input": {"file_path": "seed.py"}}]}},
        {"type": "user", "timestamp": "2026-09-01T10:02:00.000Z",
         "message": {"role": "user", "content": password_turn}},
    ]
    path = tmp_path / "sess.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return path


def _llm_says(monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: llm_mod.LLMResponse(
        text=body, total_cost_usd=0.0, input_tokens=1, output_tokens=1,
        api_key_source="none", raw={}))


def test_briefing_body_is_redacted_after_its_corrections_verify(vault, tmp_path, monkeypatch):
    turn = f"no — the staging login is maria.souza@gmail.com / senha: {PASSWORD} always"
    _llm_says(monkeypatch, (
        f"## TL;DR\nSeeded staging. Key {GOOGLE_KEY}.\n\n"
        "## Corrections\n"
        f'- "the staging login is maria.souza@gmail.com / senha: {PASSWORD} always" → use it\n'
    ))
    out = briefing_mod.generate_session_briefing(_session(tmp_path, turn), "proj", {"extraction": {}})
    text = out.read_text(encoding="utf-8")
    assert PASSWORD not in text and GOOGLE_KEY not in text
    # The correction verified against the raw turn and was kept, redacted.
    assert "corrections: 1\n" in text
    assert "maria.souza@gmail.com / senha: [redacted] always" in text


# --- the evidence gate and the quote a page stores ---------------------------


BRIEFING_TMPL = """---
type: briefing
agent: proj
session_id: s1
---

# Briefing — proj — s1

## Corrections
- "{quote}" → use the staging admin account for every seed run
"""
SRC = "bots/proj/briefings/sessions/s1.md"
RAW_QUOTE = f"always seed staging as qa.admin@acme-corp.io with password `{PASSWORD}` before running the suite"


def _gate_vault(tmp_path: Path, quote_in_briefing: str) -> Path:
    root = tmp_path / "vault"
    b = root / SRC
    b.parent.mkdir(parents=True)
    b.write_text(BRIEFING_TMPL.format(quote=quote_in_briefing), encoding="utf-8")
    return root


def _page(quote: str) -> ExtractedPage:
    return ExtractedPage(
        slug="seed-staging-admin", type="feedback", name="Seed as staging admin",
        description="d", body="b", source_files=[SRC], source_hash="h",
        evidence={"quote": quote, "source": SRC},
    )


@pytest.mark.parametrize("in_briefing,in_quote", [
    (RAW_QUOTE, RAW_QUOTE),                            # a briefing from before #418
    (RAW_QUOTE.replace(PASSWORD, "[redacted]"),        # a briefing written since
     RAW_QUOTE.replace(PASSWORD, "[redacted]")),
    (RAW_QUOTE.replace(PASSWORD, "[redacted]"), RAW_QUOTE),  # old quote, new briefing
    (RAW_QUOTE, RAW_QUOTE.replace(PASSWORD, "[redacted]")),  # redacted quote, old briefing
])
def test_a_quote_verifies_whichever_side_was_redacted(tmp_path, in_briefing, in_quote):
    root = _gate_vault(tmp_path, in_briefing)
    p = evidence.verify_page(_page(in_quote), root)
    assert p.confidence == "verified" and not p.unverified_feedback


def test_the_stored_quote_loses_its_secret_but_keeps_its_address():
    ev, n = _redact_quote({"quote": RAW_QUOTE, "source": SRC})
    assert n == 1
    assert PASSWORD not in ev["quote"] and "qa.admin@acme-corp.io" in ev["quote"]
    assert ev["source"] == SRC


def test_redact_quote_leaves_a_clean_quote_as_the_same_object():
    ev = {"quote": "never retry on 4xx", "source": SRC}
    assert _redact_quote(ev) == (ev, 0) and _redact_quote(ev)[0] is ev
    assert _redact_quote(None) == (None, 0)


# --- imports from a team tree ------------------------------------------------


PORTABLE_SRC = f"""---
name: Staging admin login
slug: staging-admin-login
description: 'password `{PASSWORD}`'
type: reference
confidence: verified
sources:
  - bots/p/briefings/sessions/s1.md
evidence:
  quote: "log in as qa@acme-corp.io / {PASSWORD} on staging please"
  source: bots/p/briefings/sessions/s1.md
tags:
  - auth
---

Admin is `qa@acme-corp.io` / `{PASSWORD}`; maps key {GOOGLE_KEY}.
"""


def test_an_imported_page_is_redacted():
    rule = from_portable(to_portable(PORTABLE_SRC, vault="v", project="theirs", today="2026-09-22"))
    assert rule is not None and PASSWORD in rule.body  # the tree carried it
    page = to_vault_page(rule, project="mine", today="2026-09-22")
    assert PASSWORD not in page and GOOGLE_KEY not in page
    assert "qa@acme-corp.io" in page
    fm = parse_frontmatter(page)
    assert "[redacted]" in fm["evidence"]["quote"] and "[redacted]" in fm["description"]


# --- autopilot rule stubs ----------------------------------------------------


def test_eos_rule_stub_is_redacted(tmp_vault: Path):
    from mnemo.autopilot.proposer import eos_extractor as eos

    cand = eos.RuleCandidate(
        slug_hint="always-login-staging",
        title="Always log in to staging",
        description=f"Ran `psql` with DB_PASSWORD={PASSWORD} and key {GOOGLE_KEY}",
        confidence=0.9,
    )
    eos._write_rule_stub(tmp_vault, cand, "s1")
    text = (tmp_vault / "shared" / "_inbox" / "reference" / "always-login-staging.md").read_text(encoding="utf-8")
    assert PASSWORD not in text and GOOGLE_KEY not in text


# --- end to end: the extraction loop writes the redacted quote ---------------


def test_extraction_writes_a_verified_page_whose_quote_has_no_secret(
    populated_vault: Path, monkeypatch: pytest.MonkeyPatch,
):
    from mnemo.core.extract import run_extraction

    b = populated_vault / SRC
    b.parent.mkdir(parents=True)
    b.write_text(BRIEFING_TMPL.format(quote=RAW_QUOTE), encoding="utf-8")  # pre-#418
    page = {
        "slug": "seed-staging-admin", "name": "Seed as staging admin", "description": "d",
        "type": "feedback", "body": "Seed staging as the admin account.",
        "source_files": [SRC], "evidence": {"quote": RAW_QUOTE, "source": SRC},
    }
    text = json.dumps({"pages": [page]})
    responses = [llm_mod.LLMResponse(text=text, total_cost_usd=0.0, input_tokens=1,
                                     output_tokens=1, api_key_source="none", raw={"result": text})]
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: responses.pop(0))

    summary = run_extraction({
        "vaultRoot": str(populated_vault),
        "extraction": {"model": "claude-haiku-4-5", "chunkSize": 10, "hintThreshold": 5,
                       "preferAPI": False, "subprocessTimeout": 60, "costSoftCap": None},
    })

    written = (populated_vault / "shared" / "feedback" / "seed-staging-admin.md").read_text(encoding="utf-8")
    fm = parse_frontmatter(written)
    assert fm["confidence"] == "verified"
    assert PASSWORD not in written
    # The address stays in the quote (it still matches the briefing); only the
    # password went. The body's own e-mail pass is unchanged.
    assert "qa.admin@acme-corp.io" in fm["evidence"]["quote"]
    assert summary.redactions >= 1
