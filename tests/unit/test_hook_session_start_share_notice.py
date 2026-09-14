"""Session start says when the repo publishes rules this vault has not imported,
and no "you said" surface ever attributes an imported rule to the reader.

The notice follows the backfill invitation's per-project discipline, keyed
by a digest of what is pending: a tree that grows invites again, one the
user read and left alone stays quiet, and nothing is once-ever (#229).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from tests.unit._share_stub import ensure_format, publish_rule

fmt = ensure_format()

from mnemo.core.share import imports as imp  # noqa: E402
from mnemo.hooks import session_start  # noqa: E402


@pytest.fixture
def vault(tmp_vault: Path) -> Path:
    (tmp_vault / ".mnemo").mkdir(exist_ok=True)
    return tmp_vault


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    return root


def _notice(vault: Path, repo: Path, project: str = "app") -> str:
    return session_start._share_import_notice(vault, {}, project, str(repo))


# --- the notice ------------------------------------------------------------

def test_invites_import_when_the_repo_publishes_unseen_rules(vault, repo):
    tree = repo / fmt.SHARE_DIR
    publish_rule(tree, slug="a", vault="other-vault")
    publish_rule(tree, slug="b", vault="other-vault")

    notice = _notice(vault, repo)

    assert notice.startswith("[mnemo] this repo publishes 2 rule(s) your vault has not imported")
    assert "mnemo import --dry-run" in notice and "`mnemo import`" in notice
    assert fmt.SHARE_DIR in notice


def test_silent_when_the_repo_publishes_nothing(vault, repo):
    assert _notice(vault, repo) == ""
    assert not (vault / ".mnemo" / "share").exists(), "no marker is spent on an empty repo"


def test_silent_once_shown_for_this_pending_set(vault, repo):
    publish_rule(repo / fmt.SHARE_DIR, slug="a", vault="other-vault")

    assert _notice(vault, repo)
    assert _notice(vault, repo) == ""


def test_invites_again_when_the_tree_gains_a_rule(vault, repo):
    tree = repo / fmt.SHARE_DIR
    publish_rule(tree, slug="a", vault="other-vault")
    assert _notice(vault, repo)
    publish_rule(tree, slug="b", vault="other-vault")

    notice = _notice(vault, repo)

    assert "2 rule(s)" in notice


def test_per_project_not_per_vault(vault, repo, tmp_path):
    publish_rule(repo / fmt.SHARE_DIR, slug="a", vault="other-vault")
    other = tmp_path / "other"
    (other / ".git").mkdir(parents=True)
    publish_rule(other / fmt.SHARE_DIR, slug="a", vault="other-vault")

    assert _notice(vault, repo, project="app")
    assert _notice(vault, other, project="other"), "a second repo gets its own invitation"


def test_silent_after_import_and_for_own_publication(vault, repo):
    tree = repo / fmt.SHARE_DIR
    publish_rule(tree, slug="theirs", vault="other-vault")
    publish_rule(tree, slug="mine", vault=fmt.vault_id(vault))
    imp.run_import(vault, share_root=tree, project="app", dry_run=False, today="2026-09-13")

    assert _notice(vault, repo) == ""


def test_never_raises_and_logs_instead(vault, repo, monkeypatch):
    publish_rule(repo / fmt.SHARE_DIR, slug="a", vault="other-vault")

    def boom(*_a, **_k):
        raise RuntimeError("tree exploded")

    monkeypatch.setattr(imp, "pending_hashes", boom)

    assert _notice(vault, repo) == ""
    from mnemo.core import errors
    log = (vault / errors.ERROR_LOG_NAME).read_text(encoding="utf-8")
    assert "session_start.share_import_notice" in log and "tree exploded" in log


def test_the_notice_rides_the_envelope_on_its_own(monkeypatch, tmp_path, tmp_home, capsys):
    """A fresh clone with a fresh vault: no topics, no briefing, no history —
    the invitation to import is the only news, and it must still ship."""
    import io
    import json
    import sys

    from mnemo.core.backfill import discover

    repo = tmp_path / "theproject"
    (repo / ".git").mkdir(parents=True)
    publish_rule(repo / fmt.SHARE_DIR, slug="use-yarn", vault="other-vault")
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)

    monkeypatch.setattr(discover, "find_transcripts", lambda **_kw: [])
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda **kw: None)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False)
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
    context = envelope["hookSpecificOutput"]["additionalContext"]
    assert "[mnemo] this repo publishes 1 rule(s) your vault has not imported" in context
    assert imp.load_ledger(vault)["noticeShown"].get("theproject"), "marked for this project"


# --- "you said" surfaces exclude imported rules ------------------------------

def _entry(**over) -> dict:
    base = dict(slug="theirs", type="feedback", name="Their rule", projects=["app"],
                confidence="verified-elsewhere", quote="never do that")
    base.update(over)
    return base


def test_session_start_learned_block_shows_no_quote_for_verified_elsewhere(vault, monkeypatch):
    from mnemo.core import learned

    monkeypatch.setattr(learned, "pending", lambda *a, **k: [_entry()])
    monkeypatch.setattr(learned, "pending_count", lambda *a, **k: 1)
    monkeypatch.setattr(learned, "mark_announced", lambda *a, **k: None)

    block = session_start._learned_block(vault, {}, "app")

    assert "theirs" in block
    assert "verified from" not in block and "never do that" not in block


def test_session_start_learned_block_still_quotes_a_verified_rule(vault, monkeypatch):
    """The guard is the confidence value, so a real verified rule keeps its quote."""
    from mnemo.core import learned

    monkeypatch.setattr(learned, "pending", lambda *a, **k: [_entry(confidence="verified")])
    monkeypatch.setattr(learned, "pending_count", lambda *a, **k: 1)
    monkeypatch.setattr(learned, "mark_announced", lambda *a, **k: None)

    assert 'verified from: "never do that"' in session_start._learned_block(vault, {}, "app")


def test_learn_command_prints_no_evidence_for_verified_elsewhere(monkeypatch, capsys):
    from mnemo.cli.commands import learn as cmd
    from mnemo.core import learn as learn_mod

    class _Report:
        error = None
        would_read = None
        transcript = None
        briefing = None
        hint = ""
        staged = 0
        learned = [_entry(), _entry(slug="mine", confidence="verified", quote="my words")]

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {})
    monkeypatch.setattr(learn_mod, "learn", lambda *a, **k: _Report())

    assert cmd.cmd_learn(argparse.Namespace(session=None, dry_run=False)) == 0

    out = capsys.readouterr().out
    assert "learned: theirs — Their rule\n" in out
    assert "never do that" not in out
    assert 'learned: mine — Their rule (evidence: "my words")' in out
