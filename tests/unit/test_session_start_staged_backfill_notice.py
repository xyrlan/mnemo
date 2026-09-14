"""SessionStart says so when reconstructed rules are waiting and nothing is live.

The first-run sweep writes into ``shared/_inbox/`` by design — backfilled
pages are reconstructions, never auto-promoted — but the reflex injects
nothing from ``_inbox``, so on a fresh vault the sweep's whole output is
invisible from inside a session. The one surface that listed it was
``mnemo doctor``, which a new user does not know to run. From their chair a
sweep that staged twenty rules and a sweep that did nothing look the same
(#234).

This notice is the difference. Its shape is set by the two things that went
wrong before:

- **stateless**: read from the vault on every session start, no marker. A
  once-ever marker failed in #229 (spent before the condition it guards
  arose). This one repeats while the condition holds and stops on its own
  the moment the user acts, because acting — moving or deleting the staged
  pages, or any rule going live — is what clears the condition;
- **narrow**: only while the vault has *no live rule at all*. That is the
  population the issue measured for — staged reconstructions and an empty
  ``shared/`` — and the only state in which silence reads as "mnemo did
  nothing". Once anything injects, the user knows mnemo works and ``doctor``
  still lists the rest;
- **read-only**: it never promotes. The staging boundary is the feature.

Fail-silent, like everything else on the session-start path.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.hooks import session_start

NOTICE_3 = (
    "[mnemo] 3 rule(s) reconstructed from your past sessions are staged in "
    "shared/_inbox/ and nothing is live yet — read each one, move the keepers "
    "to shared/<same type>/, delete the rest (`mnemo doctor` lists them)."
)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _page(slug: str, page_type: str, *, stamp: str | None = "top") -> str:
    """A staged page. ``stamp`` is where the origin lives: top-level, nested, or absent."""
    fm = [f"name: {slug.title()}", f"slug: {slug}", f"type: {page_type}"]
    if stamp == "top":
        fm.append("origin: backfill")
    elif stamp == "nested":
        fm += ["metadata:", "  origin: backfill"]
    return "---\n" + "\n".join(fm) + "\n---\n\nBody of " + slug + ".\n"


def _stage(vault: Path, page_type: str, slug: str, *, stamp="top", name=None) -> Path:
    d = vault / "shared" / "_inbox" / page_type
    d.mkdir(parents=True, exist_ok=True)
    p = d / (name or f"{slug}.md")
    p.write_text(_page(slug, page_type, stamp=stamp), encoding="utf-8")
    return p


def _live(vault: Path, page_type: str, slug: str) -> Path:
    d = vault / "shared" / page_type
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{slug}.md"
    p.write_text(_page(slug, page_type, stamp=None), encoding="utf-8")
    return p


# --- the notice itself -----------------------------------------------------

def test_staged_reconstructions_in_an_empty_vault_are_announced(vault):
    _stage(vault, "feedback", "use-yarn")
    _stage(vault, "feedback", "pin-node")
    _stage(vault, "project", "mnemo-uses-pytest")

    assert session_start._staged_backfill_notice(vault) == NOTICE_3


def test_a_vault_with_any_live_rule_says_nothing(vault):
    _stage(vault, "feedback", "use-yarn")
    _live(vault, "reference", "some-live-rule")

    assert session_start._staged_backfill_notice(vault) == ""


def test_pages_staged_by_live_extraction_are_not_counted(vault):
    """``_inbox`` also holds live-extraction staging (needs-review, demotions).

    Those are a different feature with their own review path; counting them
    here would announce a sweep that never ran.
    """
    _stage(vault, "feedback", "needs-review-only", stamp=None)

    assert session_start._staged_backfill_notice(vault) == ""

    _stage(vault, "feedback", "reconstructed")
    assert session_start._staged_backfill_notice(vault).startswith("[mnemo] 1 rule(s) ")


def test_the_nested_stamp_counts_too(vault):
    _stage(vault, "reference", "a", stamp="nested")

    assert session_start._staged_backfill_notice(vault).startswith("[mnemo] 1 rule(s) ")


def test_a_staged_rewrite_is_not_a_rule(vault):
    _stage(vault, "feedback", "x", name="x.proposed.md")

    assert session_start._staged_backfill_notice(vault) == ""


def test_a_stray_rewrite_in_a_live_dir_is_not_a_live_rule(vault):
    """A ``.proposed.md`` beside where a rule would be is a draft, not a rule."""
    _stage(vault, "feedback", "use-yarn")
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True)
    (d / "x.proposed.md").write_text(_page("x", "feedback", stamp=None), encoding="utf-8")

    assert session_start._staged_backfill_notice(vault).startswith("[mnemo] 1 rule(s) ")


def test_archived_pages_do_not_count_as_live(vault):
    _stage(vault, "feedback", "use-yarn")
    d = vault / "shared" / "_archive" / "reclassify-r1" / "originals" / "feedback"
    d.mkdir(parents=True)
    (d / "old.md").write_text(_page("old", "feedback", stamp=None), encoding="utf-8")

    assert session_start._staged_backfill_notice(vault).startswith("[mnemo] 1 rule(s) ")


def test_a_malformed_staged_page_is_skipped_not_fatal(vault):
    _stage(vault, "feedback", "good")
    (vault / "shared" / "_inbox" / "feedback" / "bad.md").write_bytes(
        b"---\nname: [unclosed\n\xff\xfe---\n"
    )

    assert session_start._staged_backfill_notice(vault).startswith("[mnemo] 1 rule(s) ")


def test_it_repeats_while_the_condition_holds_and_writes_nothing(vault):
    _stage(vault, "feedback", "use-yarn")
    before = sorted(p.name for p in (vault / ".mnemo").iterdir())

    first = session_start._staged_backfill_notice(vault)
    second = session_start._staged_backfill_notice(vault)

    assert first and first == second, "no once-ever marker: it repeats while true"
    assert sorted(p.name for p in (vault / ".mnemo").iterdir()) == before


def test_acting_on_the_inbox_clears_it(vault):
    staged = _stage(vault, "feedback", "use-yarn")
    assert session_start._staged_backfill_notice(vault)

    # The review move the notice asks for: keeper goes live.
    (vault / "shared" / "feedback").mkdir(parents=True)
    staged.rename(vault / "shared" / "feedback" / "use-yarn.md")

    assert session_start._staged_backfill_notice(vault) == ""


def test_a_vault_with_no_shared_dir_is_silent(vault):
    assert session_start._staged_backfill_notice(vault) == ""


def test_a_broken_walk_is_logged_and_never_reaches_the_session(vault, monkeypatch):
    from mnemo.core import errors as errors_mod
    from mnemo.core import filters

    _stage(vault, "feedback", "use-yarn")

    def _boom(*_a, **_k):
        raise RuntimeError("shared/ exploded")

    monkeypatch.setattr(filters, "iter_shared_pages", _boom)
    logged: list[tuple] = []
    monkeypatch.setattr(
        errors_mod, "log_error",
        lambda root, where, exc: logged.append((root, where, exc)),
    )

    assert session_start._staged_backfill_notice(vault) == ""
    assert [w for _r, w, _e in logged] == ["session_start.staged_backfill_notice"]


# --- wired into the hook ---------------------------------------------------

def test_the_notice_rides_the_envelope_on_its_own(monkeypatch, tmp_path, tmp_home, capsys):
    """The vault this notice exists for is empty: no topics, no briefing, no
    first-run invitation (the sweep already ran). It must still ship."""
    from mnemo.core.backfill import discover

    repo = tmp_path / "theproject"
    repo.mkdir()
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    _stage(vault, "feedback", "use-yarn")
    _stage(vault, "feedback", "pin-node")
    _stage(vault, "project", "mnemo-uses-pytest")

    monkeypatch.setattr(discover, "find_transcripts", lambda **_kw: [])
    monkeypatch.setattr(
        session_start, "_spawn_detached_backfill", lambda **kw: None
    )
    monkeypatch.setattr(
        "mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False
    )
    config_path = vault / ".mnemo" / "mnemo.config.json"
    config_path.write_text(json.dumps({
        "vaultRoot": str(vault),
        "injection": {"enabled": True},
        "backfill": {"enabled": True, "autoOnFirstSession": False},
        "capture": {"sessionStartEnd": False},
    }), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config_path))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "s1", "cwd": str(repo), "source": "startup"}
    )))
    monkeypatch.chdir(tmp_path)

    assert session_start.main() == 0

    envelope = json.loads(capsys.readouterr().out)
    assert envelope["hookSpecificOutput"]["additionalContext"] == NOTICE_3
