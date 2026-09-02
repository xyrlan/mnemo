"""The first session offers a backfill instead of silently running one.

Task 1 turned ``backfill.autoOnFirstSession`` off by default: a fresh install
no longer spends ~20 Haiku calls on the user's account before they have agreed
to anything. That trade is only honest if the user is *told* the history is
there — otherwise the feature they installed mnemo for is simply invisible.

So: one line, once per project, appended to the injection envelope.

- Once per project, tracked by ``firstRunNoticeShown`` in the ledger (a dict
  keyed by canonical project name — a second repo sharing this vault gets its
  own invitation). A legacy ``True`` bool from before this was per-project
  means shown everywhere.
- Never when a real sweep already finished (``installRunDone``) — there is
  nothing left to invite them to.
- Never when there are zero transcripts, and in that case the flag stays
  *unset*, so a repo that accumulates history later still gets its invitation.
  Burning the one-shot on an empty repo would silence the feature forever on
  exactly the machines where it takes longest to become useful.
- Never at the cost of the session: any failure is logged and swallowed.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.core.backfill import ledger
from mnemo.hooks import session_start


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _cfg(**over) -> dict:
    bf = {"enabled": True, "autoOnFirstSession": False}
    bf.update(over.pop("backfill", {}))
    cfg = {"backfill": bf, "injection": {"enabled": True}}
    cfg.update(over)
    return cfg


class _FakeTranscript:
    """Stand-in for discover.Transcript — the notice only counts them."""


def _patch_transcripts(monkeypatch, n: int) -> None:
    from mnemo.core.backfill import discover

    monkeypatch.setattr(
        discover, "find_transcripts",
        lambda **_kw: [_FakeTranscript() for _ in range(n)],
    )


# --- the notice itself -----------------------------------------------------

def test_first_run_offers_the_backfill_and_says_what_it_costs(vault, monkeypatch):
    _patch_transcripts(monkeypatch, 3)

    notice = session_start._first_run_notice(vault, _cfg(), "theproject")

    assert notice == (
        "[mnemo] first run: 3 past session(s) for this repo can be learned "
        "with `mnemo backfill` (opt-in, up to 3 Haiku calls; "
        "`mnemo backfill --dry-run` shows the exact cost)."
    )
    assert ledger.load(vault)["firstRunNoticeShown"] == {"theproject": True}


def test_the_call_estimate_is_capped_at_installcap(vault, monkeypatch):
    """backfill only ever spends up to installCap calls on an install run."""
    _patch_transcripts(monkeypatch, 50)

    notice = session_start._first_run_notice(
        vault, _cfg(backfill={"installCap": 20}), "theproject"
    )

    assert notice == (
        "[mnemo] first run: 50 past session(s) for this repo can be learned "
        "with `mnemo backfill` (opt-in, up to 20 Haiku calls; "
        "`mnemo backfill --dry-run` shows the exact cost)."
    )


def test_the_notice_is_shown_only_once_per_project(vault, monkeypatch):
    _patch_transcripts(monkeypatch, 3)

    assert session_start._first_run_notice(vault, _cfg(), "theproject")
    assert session_start._first_run_notice(vault, _cfg(), "theproject") == ""


def test_a_second_project_in_the_same_vault_gets_its_own_notice(vault, monkeypatch):
    """'for this repo' is per project — a second repo has its own history."""
    _patch_transcripts(monkeypatch, 3)

    assert session_start._first_run_notice(vault, _cfg(), "projectone")
    second = session_start._first_run_notice(vault, _cfg(), "projecttwo")

    assert second == (
        "[mnemo] first run: 3 past session(s) for this repo can be learned "
        "with `mnemo backfill` (opt-in, up to 3 Haiku calls; "
        "`mnemo backfill --dry-run` shows the exact cost)."
    )
    assert ledger.load(vault)["firstRunNoticeShown"] == {
        "projectone": True,
        "projecttwo": True,
    }


def test_a_completed_sweep_is_not_invited_to_run_again(vault, monkeypatch):
    """``installRunDone`` means the history is already in the vault."""
    _patch_transcripts(monkeypatch, 3)
    ledger.mark_install_run_done(vault)

    assert session_start._first_run_notice(vault, _cfg(), "theproject") == ""


def test_backfill_disabled_says_nothing(vault, monkeypatch):
    _patch_transcripts(monkeypatch, 3)

    cfg = _cfg(backfill={"enabled": False})
    assert session_start._first_run_notice(vault, cfg, "theproject") == ""
    assert ledger.load(vault)["firstRunNoticeShown"] == {}


def test_a_repo_with_no_history_keeps_its_invitation_for_later(vault, monkeypatch):
    """Zero transcripts is not the user's one chance being spent.

    A brand-new repo has nothing to backfill *yet*. Marking the notice shown
    here would mean the invitation never appears once history accumulates —
    silencing the feature exactly where it takes longest to pay off.
    """
    _patch_transcripts(monkeypatch, 0)

    assert session_start._first_run_notice(vault, _cfg(), "theproject") == ""
    assert ledger.load(vault)["firstRunNoticeShown"] == {}


def test_legacy_true_bool_suppresses_the_notice_for_every_project(vault, monkeypatch):
    """Pre-migration vaults stored a bare bool; it must mean shown everywhere."""
    _patch_transcripts(monkeypatch, 3)
    led = ledger.load(vault)
    led["firstRunNoticeShown"] = True
    ledger.save(vault, led)

    assert session_start._first_run_notice(vault, _cfg(), "projectone") == ""
    assert session_start._first_run_notice(vault, _cfg(), "projecttwo") == ""
    assert ledger.load(vault)["firstRunNoticeShown"] is True


def test_legacy_false_bool_is_upgraded_to_a_dict_on_first_mark(vault, monkeypatch):
    """A ledger written before this was per-project starts as ``False``."""
    _patch_transcripts(monkeypatch, 3)
    led = ledger.load(vault)
    led["firstRunNoticeShown"] = False
    ledger.save(vault, led)

    assert session_start._first_run_notice(vault, _cfg(), "theproject")

    assert ledger.load(vault)["firstRunNoticeShown"] == {"theproject": True}


def test_a_broken_discovery_is_logged_and_never_reaches_the_session(vault, monkeypatch):
    from mnemo.core import errors as errors_mod
    from mnemo.core.backfill import discover

    def _boom(**_kw):
        raise RuntimeError("projects dir exploded")

    monkeypatch.setattr(discover, "find_transcripts", _boom)
    logged: list[tuple] = []
    monkeypatch.setattr(
        errors_mod, "log_error",
        lambda root, where, exc: logged.append((root, where, exc)),
    )

    assert session_start._first_run_notice(vault, _cfg(), "theproject") == ""
    assert [w for _r, w, _e in logged] == ["session_start.first_run_notice"]


# --- wired into the hook ---------------------------------------------------

def test_the_notice_alone_is_a_valid_envelope(monkeypatch, tmp_path, tmp_home, capsys):
    """An empty vault has no topics and no briefing — the notice must still ship.

    ``_build_injection_payload`` returns "" for a vault with nothing in it, and
    ``main`` only emits when the payload is non-empty. If the notice were not
    allowed to stand on its own, the one message that exists to reach a
    brand-new install would be the one message a brand-new install never sees.
    """
    repo = tmp_path / "theproject"
    repo.mkdir()
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)

    _patch_transcripts(monkeypatch, 4)
    spawned: list[dict] = []
    monkeypatch.setattr(
        session_start, "_spawn_detached_backfill", lambda **kw: spawned.append(kw)
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
    assert envelope["hookSpecificOutput"]["additionalContext"] == (
        "[mnemo] first run: 4 past session(s) for this repo can be learned "
        "with `mnemo backfill` (opt-in, up to 4 Haiku calls; "
        "`mnemo backfill --dry-run` shows the exact cost)."
    )
    assert spawned == [], "the notice replaces the automatic sweep, it does not join it"
