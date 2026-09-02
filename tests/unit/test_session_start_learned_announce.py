"""SessionStart tells you what mnemo learned, and how to take it back.

Extraction promotes rules silently. Until now the only way to discover that
mnemo had written a rule about your work was to go read the vault — so the
rules that were wrong stayed wrong, because nobody knew they existed to
correct. This block is the other half of trusting mnemo with defaults: every
announcement carries its own undo on the same line.

Shape:

- capped at 5 bullets, with a ``(N more — mnemo status)`` tail, because this
  rides on the session-start prompt and must not become a wall,
- a verified rule shows the quote it was learned from (truncated at 80), an
  inferred one shows only its name — the parenthetical is evidence, and
  inventing one for an inferred rule would be a lie,
- announcing marks announced: the same rule never appears twice,
- and every failure is swallowed. This runs on a hook.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.core import learned
from mnemo.hooks import session_start


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _e(slug, projects, confidence="inferred", quote=None, name=None):
    return {"slug": slug, "type": "feedback", "name": name or slug.title(),
            "projects": projects, "confidence": confidence, "quote": quote}


def _seed(vault: Path, n: int = 7) -> None:
    """One verified entry with a quote, then n-1 inferred ones."""
    entries = [_e("use-yarn", ["proj"], "verified", "use yarn not npm", "Use yarn")]
    entries += [_e(f"rule-{i}", ["proj"]) for i in range(1, n)]
    learned.record(vault, run_id="r1", entries=entries)


# --- the block ------------------------------------------------------------

def test_the_block_caps_at_five_and_counts_the_rest(vault):
    _seed(vault, 7)

    block = session_start._learned_block(vault, {}, "proj")

    lines = block.splitlines()
    assert lines[0] == "[mnemo learned since your last session]"
    bullets = [ln for ln in lines if ln.startswith("• ")]
    assert len(bullets) == 5
    assert bullets[0] == (
        "• use-yarn — Use yarn (verified from: \"use yarn not npm\") "
        "· veto: mnemo disable-rule use-yarn"
    )
    assert bullets[1] == "• rule-1 — Rule-1 · veto: mnemo disable-rule rule-1"
    assert lines[-2] == "(2 more — mnemo status)"
    assert lines[-1] == "[/mnemo learned]"


def test_announcing_marks_announced(vault):
    """The point of the marker: the same rule is never announced twice."""
    _seed(vault, 7)

    assert session_start._learned_block(vault, {}, "proj")

    assert learned.pending(vault, "proj") == []
    assert session_start._learned_block(vault, {}, "proj") == ""


def test_exactly_five_pending_has_no_more_line(vault):
    _seed(vault, 5)

    lines = session_start._learned_block(vault, {}, "proj").splitlines()

    assert len([ln for ln in lines if ln.startswith("• ")]) == 5
    assert not [ln for ln in lines if "more —" in ln]
    assert lines[-1] == "[/mnemo learned]"


def test_a_long_quote_is_truncated(vault):
    learned.record(vault, run_id="r1", entries=[
        _e("long", ["proj"], "verified", "x" * 200, "Long"),
    ])

    block = session_start._learned_block(vault, {}, "proj")

    assert '(verified from: "' + "x" * 80 + '…")' in block


def test_nothing_pending_is_an_empty_string(vault):
    assert session_start._learned_block(vault, {}, "proj") == ""


def test_the_configured_universal_threshold_is_honoured(vault):
    """A two-project rule is not universal when scoping.universalThreshold is 3."""
    learned.record(vault, run_id="r1", entries=[_e("two", ["a", "b"])])

    cfg = {"scoping": {"universalThreshold": 3}}
    assert session_start._learned_block(vault, cfg, "zzz") == ""
    assert "two" in session_start._learned_block(vault, {}, "zzz")


def test_a_failure_is_logged_and_never_reaches_the_session(vault, monkeypatch):
    from mnemo.core import errors as errors_mod

    def _boom(*_a, **_kw):
        raise RuntimeError("ledger exploded")

    monkeypatch.setattr(learned, "pending", _boom)
    logged: list[tuple] = []
    monkeypatch.setattr(
        errors_mod, "log_error",
        lambda root, where, exc: logged.append((root, where, exc)),
    )

    assert session_start._learned_block(vault, {}, "proj") == ""
    assert [w for _r, w, _e2 in logged] == ["session_start.learned"]


# --- wired into the hook --------------------------------------------------

def test_the_block_alone_is_a_valid_envelope(monkeypatch, tmp_path, tmp_home, capsys):
    """An empty vault has no topics and no briefing — the block must still ship."""
    repo = tmp_path / "proj"
    repo.mkdir()
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    learned.record(vault, run_id="r1", entries=[
        _e("use-yarn", ["proj"], "verified", "use yarn not npm", "Use yarn"),
    ])

    monkeypatch.setattr(
        "mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False
    )
    monkeypatch.setattr(session_start, "_first_run_notice", lambda *a, **k: "")
    config_path = vault / ".mnemo" / "mnemo.config.json"
    config_path.write_text(json.dumps({
        "vaultRoot": str(vault),
        "injection": {"enabled": True},
        "backfill": {"enabled": False},
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
        "[mnemo learned since your last session]\n"
        "• use-yarn — Use yarn (verified from: \"use yarn not npm\") "
        "· veto: mnemo disable-rule use-yarn\n"
        "[/mnemo learned]"
    )


# --- the veto has to be a command people can find -------------------------

def test_disable_rule_is_a_public_command():
    """A veto advertised on every announcement cannot be hidden from `mnemo help`."""
    import argparse

    from mnemo.cli.commands.misc import cmd_help
    from mnemo.cli.parser import ADVANCED_COMMANDS

    assert "disable-rule" not in ADVANCED_COMMANDS

    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cmd_help(argparse.Namespace(all=False)) == 0

    assert "disable-rule" in buf.getvalue()
