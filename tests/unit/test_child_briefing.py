"""What a dispatched child reads and writes of the briefing: the canonical project's.

The channels round (``docs/contracts/channels.md``, ``child-briefing``) started
from two counts: all 20 ``bots/mnemo-wt-*`` namespaces hold no briefing, and
0 of 2385 ``session_start.inject`` events carry a ``-wt-`` project. They read
like "no briefing reaches a child". Measured against the children's own
transcripts (2026-09-15, 92 dispatch children across mnemo, clubinho and
mnemo-desktop), that reading is wrong, and both counts are the design working:

- **Read.** 71 children started on a ``[last-briefing …]`` block: 53 of 53 in
  mnemo, 6 of 6 in clubinho, 12 of 28 in mnemo-desktop. The 16 desktop children
  that got none all started before that project had *any* briefing on disk
  (its first was written by a child at 13:46Z; the last unbriefed child started
  at 11:37Z). The other 5 of the 92 saw the circuit breaker's
  ``[mnemo] paused`` line instead of an envelope. Every child that could have
  received a briefing did — the canonical project's, which is why no inject
  event names a worktree.
- **Write.** 26 children left a briefing, and all 26 are under the canonical
  namespace; 0 died with a tree. 16 of them still say ``agent: mnemo-wt-*`` in
  their frontmatter — written under the worktree's name before #225/#247 and
  moved by ``mnemo migrate-worktree-briefings`` — and the rest were written
  canonically. The ``-wt-`` namespaces that remain hold only day logs from
  2026-09-12..14.

So a child already receives the canonical briefing and already writes one
that lands canonically. These tests pin that round trip so it is not "fixed"
later by giving children a namespace of their own — which is the orphan shape
#225 and #247 removed, and would die with the worktree the way
``dispatch-parents.jsonl`` was moved into the vault to avoid (#288).

They drive the real hooks from a real ``git worktree`` named the way
``mnemo dispatch`` names one, not a hand-built ``.git`` file, because the
shape of the tree is the whole question.
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mnemo.core import child_profile, dispatch
from mnemo.hooks import session_end, session_start

CANONICAL_SID = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
CHILD_SID = "6eb4479d-50a7-4392-a066-4de475519229"


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """A real git repo with one commit, named like the project it stands for."""
    root = tmp_path / "mnemo"
    root.mkdir()

    def git(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=root, check=True,
            capture_output=True, text=True,
            env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
                 "PATH": __import__("os").environ.get("PATH", ""), "HOME": str(tmp_path)},
        )

    git("init", "-b", "master")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    git("add", "README.md")
    git("commit", "-m", "init")
    return root


@pytest.fixture()
def child_tree(repo: Path) -> Path:
    """The tree ``mnemo dispatch`` gives issue #200's child: ``mnemo-wt-200``."""
    tree = dispatch.ensure_worktree(200, repo_root=repo)
    assert tree.name == "mnemo-wt-200"
    return tree


@pytest.fixture()
def vault(tmp_vault: Path, tmp_tempdir: Path, monkeypatch) -> Path:
    """A vault with injection, briefings and session logging on, as the real one has."""
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "injection": {"enabled": True, "telemetry": {"enabled": True}},
        "briefings": {"enabled": True, "injectLastOnSessionStart": True},
        "capture": {"sessionStartEnd": True},
    }), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))

    sessions = tmp_vault / "bots" / "mnemo" / "briefings" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / f"{CANONICAL_SID}.md").write_text(
        "---\n"
        "type: briefing\n"
        "agent: mnemo\n"
        f"session_id: {CANONICAL_SID}\n"
        "date: 2026-09-15\n"
        "duration_minutes: 42\n"
        "---\n\n"
        "# Briefing\n\nThe maintainer stopped at the channels contract.\n",
        encoding="utf-8",
    )
    return tmp_vault


def _run(hook, payload: dict, monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    return capsys.readouterr().out


def _wt_namespaces(vault: Path) -> list[str]:
    return sorted(p.name for p in (vault / "bots").iterdir() if "-wt-" in p.name)


# --- read: the child starts on the canonical project's briefing -------------


def test_a_child_starts_on_the_canonical_projects_briefing(
    vault: Path, child_tree: Path, monkeypatch, capsys
) -> None:
    out = _run(session_start, {
        "session_id": CHILD_SID, "cwd": str(child_tree), "source": "startup",
    }, monkeypatch, capsys)

    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert f"[last-briefing session={CANONICAL_SID}" in context
    assert "stopped at the channels contract" in context


def test_the_inject_event_names_the_canonical_project_not_the_tree(
    vault: Path, child_tree: Path, monkeypatch, capsys
) -> None:
    """Why 0 of 2385 real inject events carry ``-wt-``: it is the canonical name, by design."""
    _run(session_start, {
        "session_id": CHILD_SID, "cwd": str(child_tree), "source": "startup",
    }, monkeypatch, capsys)

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    injects = [e for e in events if e.get("tool") == "session_start.inject"]
    assert [(e["project"], e["included_briefing"]) for e in injects] == [("mnemo", True)]


# --- write: the child's briefing lands canonically and outlives the tree ----


def test_a_childs_briefing_is_filed_under_the_canonical_project_after_its_tree_is_gone(
    vault: Path, repo: Path, child_tree: Path, monkeypatch, capsys
) -> None:
    """The real lifecycle order: start in the tree, tree removed, then SessionEnd.

    ``mnemo dispatch``'s cleanup removes a worktree before its child is stopped
    (#247), so the name SessionEnd files the briefing under must come from what
    SessionStart cached while the tree existed — never from the dead cwd.
    """
    _run(session_start, {
        "session_id": CHILD_SID, "cwd": str(child_tree), "source": "startup",
    }, monkeypatch, capsys)

    dispatch.remove_worktree(child_tree, repo_root=repo)
    assert not child_tree.exists()

    spawned: list[str] = []
    transcript = vault.parent / "child.jsonl"
    transcript.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(session_end, "_spawn_detached_briefing", lambda _jsonl, name: spawned.append(name))
    monkeypatch.setattr(session_end, "_resolve_session_jsonl_path", lambda _sid, _cwd: transcript)

    _run(session_end, {
        "session_id": CHILD_SID, "cwd": str(child_tree), "reason": "other",
    }, monkeypatch, capsys)

    assert spawned == ["mnemo"]


def test_a_childs_whole_round_trip_creates_no_worktree_namespace(
    vault: Path, repo: Path, child_tree: Path, monkeypatch, capsys
) -> None:
    """No ``bots/<repo>-wt-*/`` — not for the briefing, not for the day's log lines.

    The 20 such namespaces in the real vault are day logs from 2026-09-12..14,
    before #225/#247. A child that creates one now is the regression.
    """
    _run(session_start, {
        "session_id": CHILD_SID, "cwd": str(child_tree), "source": "startup",
    }, monkeypatch, capsys)
    dispatch.remove_worktree(child_tree, repo_root=repo)
    monkeypatch.setattr(session_end, "_resolve_session_jsonl_path", lambda _sid, _cwd: None)
    _run(session_end, {
        "session_id": CHILD_SID, "cwd": str(child_tree), "reason": "other",
    }, monkeypatch, capsys)

    assert _wt_namespaces(vault) == []
    assert (vault / "bots" / "mnemo" / "logs").is_dir()


# --- the lean profile keeps both ends of the channel ------------------------


def test_the_lean_profile_hands_back_both_ends_of_the_briefing(
    tmp_home: Path, tmp_path: Path, monkeypatch
) -> None:
    """SessionStart reads the briefing and SessionEnd writes it; a child needs both.

    Installed by the real installer rather than a hand-written settings file:
    the fixture in ``test_child_profile.py`` carries SessionStart and
    PreToolUse only, so it could not notice a hand-back that dropped the end
    that writes the child's briefing.
    """
    from mnemo.install.settings import inject_hooks

    monkeypatch.delenv("MNEMO_DISPATCH_FULL_PROFILE", raising=False)
    inject_hooks(child_profile.user_settings_path())

    tree = tmp_path / "mnemo-wt-200"
    tree.mkdir()
    written = child_profile.write_profile(tree)
    hooks = json.loads(written["settings"].read_text(encoding="utf-8"))["hooks"]

    assert {"SessionStart", "SessionEnd"} <= set(hooks)
    args = child_profile.lean_args(tree)
    assert args[args.index("--settings") + 1] == str(written["settings"])
