# tests/integration/test_hook_session_end.py
from __future__ import annotations

import io
import json
import os
import shutil
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import agent, session
from mnemo.hooks import session_end


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_session_end_logs_and_clears_cache(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S1", {"name": "myrepo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S1", "reason": "exit"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text(encoding="utf-8")
    assert "🔴 session ended (exit)" in log
    assert session.load("S1") is None


def test_session_end_falls_back_when_cache_missing(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "r3"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({"session_id": "missing", "reason": "compact", "cwd": str(repo)})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    log = (hook_env / "bots" / "r3" / "logs" / f"{date.today().isoformat()}.md").read_text(encoding="utf-8")
    assert "🔴 session ended (compact)" in log


def _make_worktree(tmp_path: Path, *, repo_name: str) -> Path:
    """Build a main repo + one worktree that resolves canonically to *repo_name*."""
    main_repo = tmp_path / repo_name
    main_repo.mkdir()
    git_dir = main_repo / ".git"
    git_dir.mkdir()
    wt_dir = git_dir / "worktrees" / "feature-x"
    wt_dir.mkdir(parents=True)
    (wt_dir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / f"{repo_name}-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_dir}\n", encoding="utf-8")
    return worktree


def test_session_end_cache_miss_logs_under_canonical_project(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#225: the cache-miss fallback must not open an orphan worktree namespace.

    Pre-fix this wrote `bots/wtrepo-feature-x/logs/`, one orphan dir per
    dispatched worktree, while injection used the canonical name.
    """
    worktree = _make_worktree(tmp_path, repo_name="wtrepo")
    payload = json.dumps(
        {"session_id": "wt-miss", "reason": "exit", "cwd": str(worktree)}
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    today = f"{date.today().isoformat()}.md"
    assert (hook_env / "bots" / "wtrepo" / "logs" / today).exists()
    assert not (hook_env / "bots" / "wtrepo-feature-x").exists()


def test_session_end_briefing_keeps_the_cached_name_after_the_tree_is_removed(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#247: the briefing must not re-resolve the agent from a cwd that is gone.

    The sequence on 2026-09-13 (dispatcher transcript ``24d4dc06``): PR merged
    21:40, ``git worktree remove --force ../mnemo-wt-236`` 21:41,
    ``claude stop 23b62795`` 21:42. SessionEnd then fired with
    ``cwd=~/github/mnemo-wt-236`` and nothing on disk at that path. The log
    line used the name session_start had cached (``mnemo``); the briefing
    re-resolved from the dead cwd, found no ``.git`` above it, and got the
    basename back (``mnemo-wt-236``) — one orphan namespace per stopped child.
    """
    worktree = _make_worktree(tmp_path, repo_name="wtrepo")
    # What session_start records, while the tree still exists.
    session.save("wt-gone", asdict(agent.resolve_canonical_agent(str(worktree))))
    # The transcript lives at the path Claude Code derives from the cwd string,
    # and outlives the tree.
    encoded = str(worktree).replace(os.sep, "-")
    jsonl = Path.home() / ".claude" / "projects" / encoded / "wt-gone.jsonl"
    jsonl.parent.mkdir(parents=True)
    jsonl.write_text("{}\n", encoding="utf-8")
    (hook_env / "mnemo.config.json").write_text(
        json.dumps({"vaultRoot": str(hook_env), "briefings": {"enabled": True}}),
        encoding="utf-8",
    )
    shutil.rmtree(worktree)  # `git worktree remove --force` leaves nothing behind

    spawned: dict[str, str] = {}
    monkeypatch.setattr(
        session_end, "_spawn_detached_briefing",
        lambda _jsonl, agent_name: spawned.update(agent=agent_name),
    )
    payload = json.dumps(
        {"session_id": "wt-gone", "reason": "other", "cwd": str(worktree)}
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert session_end.main() == 0

    today = f"{date.today().isoformat()}.md"
    assert (hook_env / "bots" / "wtrepo" / "logs" / today).exists()
    assert spawned["agent"] == "wtrepo"
    assert not (hook_env / "bots" / "wtrepo-feature-x").exists()


# --- #357: the child tells the session that dispatched it --------------

def _dispatched_child(hook_env: Path, *, parent: str) -> str:
    """Record *parent* as the dispatcher of a child and give it an address."""
    from mnemo.core.sessions import inbox, parents

    child = "c0da0f55-1111-2222-3333-444455556666"
    parents.log_path(hook_env).parent.mkdir(parents=True, exist_ok=True)
    parents.log_path(hook_env).write_text(
        json.dumps({"short_id": child[:8], "parent_session": parent}) + "\n",
        encoding="utf-8",
    )
    inbox.record(hook_env, {
        "session_id": parent, "socket": "/tmp/x.sock", "token": None,
        "pid": 1, "pid_start": "now",
    })
    session.save(child, {"name": "myrepo", "repo_root": "/x", "has_git": True})
    return child


def test_session_end_hands_the_report_card_to_a_detached_reporter(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    """#426: the hook starts `mnemo child-report` for a live parent and posts
    nothing itself — the card's `gh` calls must not hold SessionEnd open."""
    from mnemo.core.sessions import inbox

    parent = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
    child = _dispatched_child(hook_env, parent=parent)
    spawned = []
    monkeypatch.setattr(inbox, "is_live", lambda a: True)
    monkeypatch.setattr(inbox, "post", lambda *a, **k: pytest.fail("the reporter posts, not the hook"))
    monkeypatch.setattr(
        session_end, "_spawn_detached_child_report",
        lambda short_id, **kw: spawned.append((short_id, kw)),
    )
    payload = {
        "session_id": child, "reason": "other", "cwd": "/x/app-wt-7",
        "transcript_path": "/t/c0da0f55.jsonl",
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert session_end.main() == 0

    assert spawned == [(child[:8], {
        "parent": parent, "cwd": "/x/app-wt-7", "transcript": "/t/c0da0f55.jsonl",
    })]


def test_session_end_falls_back_to_the_one_line_notice_when_the_reporter_cannot_start(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A child with a recorded parent still gets a notice delivered on exit."""
    from mnemo.core.sessions import inbox

    child = _dispatched_child(hook_env, parent="0ff9d810-e54d-41f3-a045-b0ccff6c5186")
    sent = []
    monkeypatch.setattr(inbox, "is_live", lambda a: True)
    monkeypatch.setattr(inbox, "post", lambda a, t: sent.append(t) or True)

    def _cannot_start(*a, **k):
        raise OSError("no such executable")

    monkeypatch.setattr(session_end, "_spawn_detached_child_report", _cannot_start)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": child, "reason": "exit"})),
    )
    assert session_end.main() == 0

    assert len(sent) == 1
    assert child[:8] in sent[0]
    # It must announce itself as mnemo, not as the user (#309).
    assert sent[0].startswith(inbox.NOTICE_PREFIX)
    assert "not your user speaking" in sent[0]


def test_session_end_starts_no_reporter_for_a_parent_that_has_exited(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    from mnemo.core.sessions import inbox

    child = _dispatched_child(hook_env, parent="0ff9d810-e54d-41f3-a045-b0ccff6c5186")
    monkeypatch.setattr(inbox, "is_live", lambda a: False)
    monkeypatch.setattr(
        session_end, "_spawn_detached_child_report",
        lambda *a, **k: pytest.fail("nobody is left to read it"),
    )
    monkeypatch.setattr(inbox, "post", lambda *a, **k: pytest.fail("must not send"))
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": child, "reason": "exit"})),
    )
    assert session_end.main() == 0


def _dead_pid() -> int:
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _report_rows(vault: Path):
    from mnemo.core.sessions import report_card

    path = vault / ".mnemo" / report_card.LOG_NAME
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]


def test_session_end_reports_to_a_live_parent_behind_a_newer_dead_row(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    """#454, the path that failed on 2026-09-22, with nothing in inbox patched:
    the parent is live in one process (here, this one) while a second process
    that resumed its id wrote a newer row and exited. Three children of that
    parent took the "parent has exited" return and posted nothing."""
    from mnemo.core.sessions import inbox

    parent = "a6158015-1b6f-4801-a446-ecbc63a9826c"
    child = _dispatched_child(hook_env, parent=parent)
    live_sock = hook_env / "75451.sock"
    live_sock.write_text("", encoding="utf-8")
    inbox.record(hook_env, {
        "session_id": parent, "socket": str(live_sock), "token": None,
        "pid": os.getpid(), "pid_start": inbox.pid_start(os.getpid()),
    })
    inbox.record(hook_env, {
        "session_id": parent, "socket": str(hook_env / "28611.sock"), "token": None,
        "pid": _dead_pid(), "pid_start": "Tue Sep 22 19:32:46 2026",
    })
    spawned = []
    monkeypatch.setattr(
        session_end, "_spawn_detached_child_report",
        lambda short_id, **kw: spawned.append(kw["parent"]) or 4242,
    )
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": child, "reason": "exit"})),
    )
    assert session_end.main() == 0

    assert spawned == [parent]
    assert [(r["event"], r.get("pid")) for r in _report_rows(hook_env)] == [("spawned", 4242)]


def test_session_end_says_so_when_the_parent_is_gone(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    """#454: no reporter for a dead parent, but a row with the reason and an
    errors.log entry — and that entry must not count toward the hook breaker."""
    from mnemo.core import errors
    from mnemo.core.sessions import inbox, report_card

    parent = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
    child = _dispatched_child(hook_env, parent=parent)  # pid 1, start "now": not live
    monkeypatch.setattr(
        session_end, "_spawn_detached_child_report",
        lambda *a, **k: pytest.fail("nobody is left to read it"),
    )
    monkeypatch.setattr(inbox, "post", lambda *a, **k: pytest.fail("must not send"))
    for _ in range(3):
        monkeypatch.setattr(
            sys, "stdin", io.StringIO(json.dumps({"session_id": child, "reason": "exit"})),
        )
        assert session_end.main() == 0

    rows = _report_rows(hook_env)
    assert len(rows) == 3
    assert all(r["delivered"] is False and r["reason"].startswith("parent not live") for r in rows)
    logged = (hook_env / ".errors.log").read_text(encoding="utf-8")
    assert logged.count(report_card.UNDELIVERED_WHERE) == 3
    assert errors.recent_strikes(hook_env) == 0


def test_session_end_logs_the_one_line_fallback(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    from mnemo.core.sessions import inbox

    child = _dispatched_child(hook_env, parent="0ff9d810-e54d-41f3-a045-b0ccff6c5186")
    monkeypatch.setattr(inbox, "is_live", lambda a: True)
    monkeypatch.setattr(inbox, "post", lambda a, t: False)

    def _cannot_start(*a, **k):
        raise OSError("no such executable")

    monkeypatch.setattr(session_end, "_spawn_detached_child_report", _cannot_start)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": child, "reason": "exit"})),
    )
    assert session_end.main() == 0

    [row] = _report_rows(hook_env)
    assert row["delivered"] is False
    assert "reporter did not start (OSError: no such executable)" in row["reason"]
    assert "socket write failed" in row["reason"]


def test_session_end_says_nothing_for_a_session_nobody_dispatched(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    from mnemo.core.sessions import inbox

    monkeypatch.setattr(inbox, "post", lambda *a, **k: pytest.fail("must not send"))
    session.save("S9", {"name": "myrepo", "repo_root": "/x", "has_git": True})
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": "S9", "reason": "exit"})),
    )
    assert session_end.main() == 0


def test_notify_parent_is_switchable_off(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    from mnemo.core.sessions import inbox, parents

    child = "c0da0f55-1111-2222-3333-444455556666"
    parents.log_path(hook_env).parent.mkdir(parents=True, exist_ok=True)
    parents.log_path(hook_env).write_text(
        json.dumps({"short_id": child[:8], "parent_session": "p"}) + "\n", encoding="utf-8",
    )
    (hook_env / "mnemo.config.json").write_text(
        json.dumps({"vaultRoot": str(hook_env), "dispatch": {"notifyParent": False}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(inbox, "post", lambda *a, **k: pytest.fail("must not send"))
    session.save(child, {"name": "myrepo", "repo_root": "/x", "has_git": True})
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": child, "reason": "exit"})),
    )
    assert session_end.main() == 0


def test_session_end_follows_the_pr_of_a_child_that_may_push(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    """#436: the stop is on the ledger the watcher reads; the watcher itself
    goes through `session_start._spawn_detached`, which the suite stubs."""
    from mnemo.core.sessions import grants, inbox, pr_follow
    from mnemo.hooks import session_start

    parent = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
    child = _dispatched_child(hook_env, parent=parent)
    grants.record(child[:8], ("push", "pr"), vault_root=hook_env)
    monkeypatch.setattr(inbox, "is_live", lambda a: False)
    spawned = []
    monkeypatch.setattr(session_start, "_spawn_detached",
                        lambda args, cwd=None: spawned.append(args))
    payload = {"session_id": child, "reason": "other", "cwd": "/x/app-wt-7"}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert session_end.main() == 0

    entry = pr_follow.load_ledger(hook_env)["children"][child[:8]]
    assert entry["session_id"] == child and entry["parent"] == parent
    assert entry["cwd"] == "/x/app-wt-7" and not entry["closed"]
    assert spawned == [["pr-follow"]]


def test_session_end_does_not_follow_a_child_granted_nothing(
    hook_env: Path, monkeypatch: pytest.MonkeyPatch,
):
    """#436: `--may none` publishes nothing, so nothing wakes it to publish."""
    from mnemo.core.sessions import inbox, pr_follow

    child = _dispatched_child(hook_env, parent="0ff9d810-e54d-41f3-a045-b0ccff6c5186")
    monkeypatch.setattr(inbox, "is_live", lambda a: False)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"session_id": child, "reason": "exit"})),
    )
    assert session_end.main() == 0
    assert pr_follow.load_ledger(hook_env)["children"] == {}
