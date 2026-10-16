"""Every verb mnemo applies to a child, on a child spawned on Grok (grok-verbs).

The wave-2 contract (``design/contracts/2026-10-16-grok-host-wave2.md``):
wake, the automatic rewake, pr-follow's nudge, stop, ``deliver``,
``tree-sweep`` and ``resume --agent`` work on a Grok child on the CLI it was
spawned on, never the current one, and a Claude child's verbs stay byte for
byte what they were.

No ``grok`` runs here. The leader is ``_fake_grok_leader.py`` (the shapes
grok 1.0.60 answered live), and the dispatch ledger is a stand-in module
installed as ``mnemo.core.sessions.child_agents``, holding the
``ChildAgent`` fields the grok-spawn piece records.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from mnemo import cli
from mnemo.core.grok import acp, paths
from mnemo.core.sessions import (
    delivery, grok_verbs, pr_follow, rewake, tree_sweep, wake,
)
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.stalls import Kind, Stall

FAKE = Path(__file__).with_name("_fake_grok_leader.py")

#: Two Grok ids dispatched together: UUIDv7, so the first eight digits match.
GROK_ID = "01a144cc-77f5-7ea3-9060-06013224200f"
GROK_TWIN = "01a144cc-9b1e-7c40-8d1a-5f0e2b7c11aa"
GROK_SHORT = paths.short_id(GROK_ID)
CLAUDE_ID = "594436f2-a282-4a1c-b04a-08441296047e"
CLAUDE_SHORT = CLAUDE_ID[:8]
PARENT = "0ff9d810-e54d-41f3-a045-b0ccff6c5186"
T0 = 1_790_000_000.0


@dataclass(frozen=True)
class ChildAgent:
    """The record grok-spawn writes at spawn (its contract's fields)."""

    short_id: str
    agent: str
    session_id: str
    home: Optional[str]
    cwd: str


@pytest.fixture(autouse=True)
def _state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # mnemo's own Grok state (the yolo ledger) under tmp: HOME patching is a
    # no-op on Windows.
    monkeypatch.setattr(acp, "state_dir", lambda: tmp_path / "grok-state")


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    monkeypatch.setattr(grok_verbs, "_vault", lambda: root)
    return root


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    path = tmp_path / "app-wt-7"
    path.mkdir()
    return path


@pytest.fixture
def home(tmp_path: Path) -> Path:
    h = tmp_path / "grok-account"
    h.mkdir()
    (h / "auth.json").write_text("{}", encoding="utf-8")
    return h


@pytest.fixture
def ledger(vault: Path, monkeypatch: pytest.MonkeyPatch):
    """The dispatch ledger: ``ledger.add(ChildAgent)`` records a child."""
    rows: Dict[str, ChildAgent] = {}
    asked: List[Any] = []

    def read(vault_root):
        asked.append(vault_root)
        return dict(rows)

    module = types.ModuleType("mnemo.core.sessions.child_agents")
    module.read = read  # type: ignore[attr-defined]
    module.ChildAgent = ChildAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mnemo.core.sessions.child_agents", module)

    class Ledger:
        vault_roots = asked

        @staticmethod
        def add(child: ChildAgent) -> ChildAgent:
            rows[child.short_id] = child
            return child

    return Ledger


@pytest.fixture
def leader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The fake leader; ``leader.session(id, cwd=…)`` seeds one."""
    state_path = tmp_path / "fake-state.json"
    state_path.write_text(json.dumps({"sessions": {}, "env_keys": ["GROK_HOME"]}),
                          encoding="utf-8")
    monkeypatch.setenv("FAKE_GROK_STATE", str(state_path))
    monkeypatch.setattr(acp, "_grok_argv", lambda: [sys.executable, str(FAKE)])

    class Leader:
        @staticmethod
        def state() -> dict:
            return json.loads(state_path.read_text(encoding="utf-8"))

        @staticmethod
        def set(**kw) -> None:
            s = Leader.state()
            s.update(kw)
            state_path.write_text(json.dumps(s), encoding="utf-8")

        @staticmethod
        def session(sid: str, *, cwd: str, **kw) -> None:
            s = Leader.state()
            row = {"cwd": cwd, "resident": True, "activity": "idle", "yolo": True}
            row.update(kw)
            s["sessions"][sid] = row
            state_path.write_text(json.dumps(s), encoding="utf-8")

        @staticmethod
        def prompts(sid: str) -> List[str]:
            return Leader.state()["sessions"].get(sid, {}).get("prompts", [])

        @staticmethod
        def requests(method: str) -> List[dict]:
            return [m for m in Leader.state().get("log", []) if m.get("method") == method]

    return Leader


@pytest.fixture
def no_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Any ``claude`` subprocess fails the test: a Grok child never reaches one."""
    real = subprocess.run

    def run(args, *a, **kw):
        if list(args)[:1] == ["claude"]:
            pytest.fail(f"a Grok child reached claude: {args!r}")
        return real(args, *a, **kw)

    monkeypatch.setattr(subprocess, "run", run)


def _grok_child(ledger, home: Path, tree: Path, sid: str = GROK_ID) -> ChildAgent:
    return ledger.add(ChildAgent(short_id=paths.short_id(sid), agent="grok",
                                 session_id=sid, home=str(home), cwd=str(tree)))


def _claude_run(monkeypatch: pytest.MonkeyPatch, *, stdout: str = "") -> List[tuple]:
    seen: List[tuple] = []

    def run(args, **kw):
        seen.append((list(args), kw.get("cwd")))
        return subprocess.CompletedProcess(args, 0, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)
    return seen


# ---------------------------------------------------------------------------
# which child is a Grok child: the ledger, never the environment
# ---------------------------------------------------------------------------


def test_the_ledger_is_read_from_the_vault(ledger, vault, home, tree) -> None:
    _grok_child(ledger, home, tree)
    assert grok_verbs.by_session_id(GROK_ID).session_id == GROK_ID
    assert ledger.vault_roots == [vault]


def test_a_grok_child_is_keyed_by_its_last_eight_digits(ledger, home, tree) -> None:
    _grok_child(ledger, home, tree)
    _grok_child(ledger, home, tree, GROK_TWIN)
    assert GROK_ID[:8] == GROK_TWIN[:8]
    assert grok_verbs.by_short_id(GROK_SHORT).session_id == GROK_ID
    assert grok_verbs.by_short_id(paths.short_id(GROK_TWIN)).session_id == GROK_TWIN
    assert grok_verbs.short_id_for(GROK_ID) == GROK_SHORT
    assert grok_verbs.short_id_for(CLAUDE_ID) == CLAUDE_SHORT


def test_a_full_id_must_match_the_recorded_one(ledger, home, tree) -> None:
    """A Claude id ending in a Grok child's key is still a Claude child."""
    _grok_child(ledger, home, tree)
    lookalike = "594436f2-a282-4a1c-b04a-0844" + GROK_SHORT
    assert paths.short_id(lookalike) == GROK_SHORT
    assert grok_verbs.by_session_id(lookalike) is None


def test_a_claude_line_in_the_ledger_routes_nothing(ledger, home, tree) -> None:
    ledger.add(ChildAgent(short_id=CLAUDE_SHORT, agent="claude", session_id=CLAUDE_ID,
                          home=None, cwd=str(tree)))
    assert grok_verbs.by_short_id(CLAUDE_SHORT) is None
    assert grok_verbs.by_session_id(CLAUDE_ID) is None


def test_no_ledger_module_means_no_grok_child(vault, monkeypatch) -> None:
    # `None` in sys.modules makes the import raise ImportError, as a mnemo
    # without grok-spawn's module would.
    monkeypatch.setitem(sys.modules, "mnemo.core.sessions.child_agents", None)
    assert grok_verbs.by_session_id(GROK_ID) is None
    assert grok_verbs.short_id_for(GROK_ID) == GROK_ID[:8]


def test_the_current_account_does_not_make_a_claude_child_grok(
        ledger, monkeypatch, tree) -> None:
    monkeypatch.setenv("MNEMO_AGENT", "grok")
    monkeypatch.setenv("GROK_SESSION_ID", GROK_ID)
    seen = _claude_run(monkeypatch)
    assert wake.wake(CLAUDE_ID, cwd=str(tree)) is None
    assert seen[0][0][:4] == ["claude", "--bg", "--resume", CLAUDE_ID]


# ---------------------------------------------------------------------------
# wake: a user turn through the leader of the child's own home
# ---------------------------------------------------------------------------


def test_wake_sends_the_nudge_through_the_leader(ledger, leader, home, tree, no_claude) -> None:
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree))
    assert wake.wake(GROK_ID, cwd=str(tree)) is None
    assert leader.prompts(GROK_ID) == [wake.NUDGE]


def test_wake_reaches_the_home_the_child_was_spawned_in(
        ledger, leader, home, tree, tmp_path, monkeypatch, no_claude) -> None:
    other = tmp_path / "selected-now"
    other.mkdir()
    monkeypatch.setenv("GROK_HOME", str(other))
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree))
    assert wake.wake(GROK_ID, cwd=str(tree)) is None
    assert {b["GROK_HOME"] for b in leader.state()["bridges"]} == {str(home)}


def test_wake_loads_a_dormant_child_first(ledger, leader, home, tree, no_claude) -> None:
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree), resident=False, activity="dormant")
    assert wake.wake(GROK_ID, cwd=str(tree)) is None
    assert [r["params"]["sessionId"] for r in leader.requests("session/load")] == [GROK_ID]
    assert leader.prompts(GROK_ID) == [wake.NUDGE]


def test_a_child_with_no_home_recorded_is_woken_under_grok_s_default(
        ledger, monkeypatch, tree, tmp_path) -> None:
    ledger.add(ChildAgent(short_id=GROK_SHORT, agent="grok", session_id=GROK_ID,
                          home=None, cwd=str(tree)))
    monkeypatch.setenv("GROK_HOME", str(tmp_path / "selected-now"))
    homes = []

    class Leader:
        def __init__(self, h):
            homes.append(h)

        def prompt(self, *a, **k):
            return None

    monkeypatch.setattr(acp, "Leader", Leader)
    assert wake.wake(GROK_ID, cwd=str(tree)) is None
    assert homes == [paths.grok_home({})]


def test_an_unreachable_leader_is_not_reached(ledger, tmp_path, tree, leader, no_claude) -> None:
    logged_out = tmp_path / "logged-out"
    logged_out.mkdir()
    _grok_child(ledger, logged_out, tree)
    why = wake.wake(GROK_ID, cwd=str(tree))
    assert isinstance(why, grok_verbs.NotReached)
    assert "grok login" in why


def test_a_turn_taken_before_the_limit_answers_is_a_wake(
        ledger, leader, home, tree, no_claude) -> None:
    """The leader starts the turn, then answers ``Rate limited``: a wake that
    does not wait has already returned. As with ``claude --bg --resume``, the
    turn was taken, and a child that stalls again reads as a new stall."""
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree))
    leader.set(rate_limited=True)
    assert wake.wake(GROK_ID, cwd=str(tree)) is None


def test_a_limit_refused_before_the_turn_is_not_reached(ledger, home, tree, monkeypatch) -> None:
    _grok_child(ledger, home, tree)

    class Leader:
        def __init__(self, h):
            pass

        def prompt(self, *a, **k):
            raise acp.RateLimited("session/prompt", {"message": "Rate limited"})

    monkeypatch.setattr(acp, "Leader", Leader)
    why = wake.wake(GROK_ID, cwd=str(tree))
    assert isinstance(why, grok_verbs.NotReached)
    assert "rate limited" in why


def test_a_session_the_home_does_not_have_is_a_plain_failure(
        ledger, leader, home, tree, no_claude) -> None:
    _grok_child(ledger, home, tree)
    why = wake.wake(GROK_ID, cwd=str(tree))
    assert why and not isinstance(why, grok_verbs.NotReached)
    assert GROK_ID in why


def test_wake_never_raises(ledger, home, tree, monkeypatch) -> None:
    _grok_child(ledger, home, tree)

    class Leader:
        def __init__(self, h):
            pass

        def prompt(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(acp, "Leader", Leader)
    assert wake.wake(GROK_ID, cwd=str(tree)) == "boom"


def test_claude_wake_is_unchanged_with_grok_children_recorded(
        ledger, home, tree, monkeypatch) -> None:
    _grok_child(ledger, home, tree)
    seen = _claude_run(monkeypatch)
    assert wake.wake(CLAUDE_ID, cwd="/w") is None
    assert seen == [(["claude", "--bg", "--resume", CLAUDE_ID, wake.NUDGE], "/w")]


def test_a_short_id_is_still_refused_before_anything_runs(ledger, home, tree, monkeypatch) -> None:
    _grok_child(ledger, home, tree)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("ran"))
    monkeypatch.setattr(acp, "Leader", lambda *a, **k: pytest.fail("leader"))
    assert "copy" in wake.wake(GROK_SHORT, cwd=str(tree))


# ---------------------------------------------------------------------------
# the PR nudge: Grok's ending, Claude's unchanged
# ---------------------------------------------------------------------------

#: The Claude nudge's ending, as master had it.
_CLAUDE_FINISH = ("Then finish as your opening prompt says: the closing report, "
                  "publishing only\nwhat it granted, and stopping yourself last.\n"
                  "</mnemo-pr-follow>")


def test_the_claude_pr_nudge_still_ends_by_stopping_itself() -> None:
    text = wake.pr_nudge(12, ["ci-red"])
    assert text == wake.pr_nudge(12, ["ci-red"], agent="claude")
    assert text.endswith(_CLAUDE_FINISH)


def test_the_grok_pr_nudge_ends_with_the_report_and_names_no_claude_command() -> None:
    text = wake.pr_nudge(12, ["ci-red", "review"], agent="grok")
    assert text.endswith("end your last turn with your closing report.\n</mnemo-pr-follow>")
    assert "stopping yourself" not in text and "claude" not in text.lower()
    assert text[: -len("</mnemo-pr-follow>")].startswith(
        wake.pr_nudge(12, ["ci-red", "review"]).split("Then finish")[0])


def test_an_unknown_agent_has_no_pr_nudge() -> None:
    with pytest.raises(ValueError):
        wake.pr_nudge(12, ["ci-red"], agent="codex")


def test_wake_for_pr_sends_the_grok_nudge_through_the_leader(
        ledger, leader, home, tree, no_claude) -> None:
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree))
    assert wake.wake_for_pr(GROK_ID, cwd=str(tree), pr=12, events=["ci-red"]) is None
    assert leader.prompts(GROK_ID) == [wake.pr_nudge(12, ["ci-red"], agent="grok")]


# ---------------------------------------------------------------------------
# stop: close the session in its leader
# ---------------------------------------------------------------------------


def test_stop_closes_a_resident_child(ledger, leader, home, tree, no_claude) -> None:
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree))
    assert delivery.stop_session(GROK_SHORT) is None
    assert [r["params"]["sessionId"] for r in leader.requests("_x.ai/session/close")] == [GROK_ID]
    assert leader.state()["sessions"][GROK_ID]["activity"] == "dormant"


def test_stop_of_a_dormant_child_has_nothing_left_to_end(
        ledger, leader, home, tree, no_claude) -> None:
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree), resident=False, activity="dormant")
    assert delivery.stop_session(GROK_SHORT) is None


def test_stop_with_the_leader_down_is_not_reached(ledger, tmp_path, tree, leader) -> None:
    gone = tmp_path / "no-such-home"
    _grok_child(ledger, gone, tree)
    why = delivery.stop_session(GROK_SHORT)
    assert isinstance(why, grok_verbs.NotReached)
    assert str(gone) in why


def test_another_outcome_is_reported(ledger, home, tree, monkeypatch) -> None:
    _grok_child(ledger, home, tree)

    class Leader:
        def __init__(self, h):
            pass

        def close(self, sid):
            return "superseded"

    monkeypatch.setattr(acp, "Leader", Leader)
    assert "superseded" in delivery.stop_session(GROK_SHORT)


def test_claude_stop_is_unchanged_with_grok_children_recorded(
        ledger, home, tree, monkeypatch) -> None:
    _grok_child(ledger, home, tree)
    seen = _claude_run(monkeypatch)
    assert delivery.stop_session(CLAUDE_SHORT) is None
    assert seen == [(["claude", "stop", CLAUDE_SHORT], None)]


def test_the_stop_command_names_each_cli(ledger, home, tree) -> None:
    _grok_child(ledger, home, tree)
    assert delivery.stop_command(CLAUDE_SHORT) == f"claude stop {CLAUDE_SHORT}"
    assert delivery.stop_command(GROK_SHORT) == f"mnemo grok close {GROK_SHORT} --home {home}"


def test_the_commands_omit_the_default_home(ledger, tree) -> None:
    child = ledger.add(ChildAgent(short_id=GROK_SHORT, agent="grok", session_id=GROK_ID,
                                  home=None, cwd=str(tree)))
    assert grok_verbs.close_command(child) == f"mnemo grok close {GROK_SHORT}"
    assert grok_verbs.attach_command(child) == f"grok --cwd {tree} -r {GROK_ID}"


# ---------------------------------------------------------------------------
# deliver --stop-done [--agent]
# ---------------------------------------------------------------------------


def _session(short_id: str, agent: str, *, state: str = "done", live: Optional[bool] = True,
             cwd: str = "", session_id: Optional[str] = None, **kw) -> Session:
    return Session(short_id=short_id, state=state, live=live, cwd=cwd, agent=agent,
                   session_id=session_id, **kw)


def test_stop_done_in_keeps_only_the_named_cli(monkeypatch, tree) -> None:
    rows = [_session(CLAUDE_SHORT, "claude"), _session(GROK_SHORT, "grok")]
    monkeypatch.setattr(delivery, "sessions_in", lambda _t: rows)
    stopped: List[str] = []
    monkeypatch.setattr(delivery, "stop_session", lambda sid: stopped.append(sid) or None)
    assert delivery.stop_done_in(tree, agent="grok") == [(GROK_SHORT, None)]
    assert delivery.stop_done_in(tree, agent="claude") == [(CLAUDE_SHORT, None)]
    assert delivery.stop_done_in(tree) == [(CLAUDE_SHORT, None), (GROK_SHORT, None)]
    assert stopped == [GROK_SHORT, CLAUDE_SHORT, CLAUDE_SHORT, GROK_SHORT]


def test_deliver_stop_done_closes_grok_children_and_says_how_on_failure(
        ledger, home, tree, monkeypatch, capsys) -> None:
    from mnemo.cli.commands import deliver

    _grok_child(ledger, home, tree)
    monkeypatch.setattr(deliver, "_repo_root", lambda: tree.parent)
    monkeypatch.setattr(delivery, "dispatch_worktrees", lambda **k: [tree])
    monkeypatch.setattr(delivery, "sessions_in", lambda _t: [
        _session(CLAUDE_SHORT, "claude"), _session(GROK_SHORT, "grok")])
    closed: List[str] = []

    class Leader:
        def __init__(self, h):
            assert Path(h) == home

        def close(self, sid):
            closed.append(sid)
            raise acp.GrokUnavailable("leader down")

    monkeypatch.setattr(acp, "Leader", Leader)
    assert cli.main(["deliver", "--stop-done", "--agent", "grok"]) == 0
    out = capsys.readouterr().out
    assert closed == [GROK_ID]
    assert f"`mnemo grok close {GROK_SHORT} --home {home}` failed: leader down" in out
    assert CLAUDE_SHORT not in out


def test_deliver_refuses_agent_without_stop_done(monkeypatch, tree, capsys) -> None:
    from mnemo.cli.commands import deliver

    monkeypatch.setattr(deliver, "_repo_root", lambda: tree.parent)
    assert cli.main(["deliver", "211", "--agent", "grok"]) == 1
    assert "--stop-done" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# resume --agent
# ---------------------------------------------------------------------------

FREE = Stall(kind=Kind.RATE_LIMIT, error="rate_limit", resets_at=int(T0), limit="five_hour")
ASKS = Stall(kind=Kind.HUMAN, error="permission", resets_at=None, limit=None)


@pytest.fixture
def stalled(monkeypatch, tmp_path, tree):
    from mnemo.core.sessions import jobs as jobs_mod
    from mnemo.core.sessions import stalls as stalls_mod

    rows = [
        _session(CLAUDE_SHORT, "claude", state="blocked", tempo="blocked", live=False,
                 cwd=str(tree), session_id=CLAUDE_ID, needs="rate limited"),
        _session(GROK_SHORT, "grok", state="blocked", tempo="blocked", live=False,
                 cwd=str(tree), session_id=GROK_ID, needs="rate limited"),
    ]
    stalls = {CLAUDE_SHORT: FREE, GROK_SHORT: FREE}
    monkeypatch.setattr(jobs_mod, "read_sessions", lambda *a, **k: list(rows))
    monkeypatch.setattr(stalls_mod, "stalls_for", lambda sessions, **k: dict(stalls))
    woken: List[str] = []
    monkeypatch.setattr(wake, "wake", lambda sid, **k: woken.append(sid) or None)
    monkeypatch.chdir(tmp_path)
    return {"woken": woken, "stalls": stalls}


def test_resume_agent_wakes_only_that_cli_s_children(stalled, capsys) -> None:
    assert cli.main(["resume", "--all", "--agent", "grok"]) == 0
    assert stalled["woken"] == [GROK_ID]
    assert cli.main(["resume", "--all", "--agent", "claude"]) == 0
    assert stalled["woken"] == [GROK_ID, CLAUDE_ID]


def test_resume_without_agent_wakes_both(stalled) -> None:
    assert cli.main(["resume", "--all"]) == 0
    assert sorted(stalled["woken"]) == sorted([CLAUDE_ID, GROK_ID])


def test_a_grok_child_that_needs_a_person_names_the_grok_command(
        stalled, ledger, home, tree, capsys) -> None:
    _grok_child(ledger, home, tree)
    stalled["stalls"].update({CLAUDE_SHORT: ASKS, GROK_SHORT: ASKS})
    assert cli.main(["resume", "--all"]) == 0
    out = capsys.readouterr().out
    assert f"(GROK_HOME={home} grok --cwd {tree} -r {GROK_ID})" in out
    assert f"(claude attach {CLAUDE_SHORT})" in out
    assert stalled["woken"] == []


def test_resume_watch_refuses_agent(capsys) -> None:
    assert cli.main(["resume", "--watch", "--agent", "grok"]) == 1
    assert "--agent does not narrow it" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# the automatic rewake
# ---------------------------------------------------------------------------


def _stalled_grok(tree: Path) -> Session:
    return _session(GROK_SHORT, "grok", state="blocked", tempo="blocked", live=False,
                    cwd=str(tree), session_id=GROK_ID)


def test_rewake_wakes_a_grok_child_through_its_leader(
        ledger, leader, home, tree, vault, no_claude) -> None:
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree), resident=False, activity="dormant")
    report = rewake.sweep({}, vault_root=vault, sessions=[_stalled_grok(tree)],
                          stalls={GROK_SHORT: FREE}, now=T0 + 60, announce=False)
    assert [w.split()[0] for w in report.woken] == [GROK_SHORT]
    assert leader.prompts(GROK_ID) == [wake.NUDGE]
    assert rewake.load_ledger(vault)["wakes"][GROK_ID]["short_id"] == GROK_SHORT


def test_rewake_retries_an_unreachable_leader_and_records_no_wake(
        ledger, tree, vault, tmp_path) -> None:
    _grok_child(ledger, tmp_path / "no-such-home", tree)
    report = rewake.sweep({}, vault_root=vault, sessions=[_stalled_grok(tree)],
                          stalls={GROK_SHORT: FREE}, now=T0 + 60, announce=False)
    assert len(report.failed) == 1
    data = rewake.load_ledger(vault)
    assert data["wakes"] == {}
    assert data["attempts"][GROK_ID]["outcome"] == "retrying"


def test_not_reached_is_never_permanent(ledger, tree, vault) -> None:
    why = grok_verbs.NotReached("resume-under-own-id appears in the leader's words")
    report = rewake.sweep({}, vault_root=vault, sessions=[_stalled_grok(tree)],
                          stalls={GROK_SHORT: FREE}, now=T0 + 60, announce=False,
                          wake_fn=lambda sid, **k: why)
    assert report.failed
    assert rewake.load_ledger(vault)["attempts"][GROK_ID]["outcome"] == "retrying"


# ---------------------------------------------------------------------------
# pr-follow
# ---------------------------------------------------------------------------


def _dispatched(vault: Path, short_id: str) -> None:
    mnemo = vault / ".mnemo"
    with (mnemo / "dispatch-parents.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"short_id": short_id, "parent_session": PARENT}) + "\n")
    with (mnemo / "dispatch-grants.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"short_id": short_id, "may": ["push", "pr"]}) + "\n")


def test_a_grok_child_is_followed_under_its_last_eight_digits(
        ledger, home, tree, vault) -> None:
    _grok_child(ledger, home, tree)
    _dispatched(vault, GROK_SHORT)
    said = pr_follow.on_session_end({}, vault_root=vault, session_id=GROK_ID,
                                    cwd=str(tree), spawn=lambda: None, now=T0)
    assert said == "spawned"
    children = pr_follow.load_ledger(vault)["children"]
    assert list(children) == [GROK_SHORT]
    assert children[GROK_SHORT]["session_id"] == GROK_ID


def test_two_grok_children_dispatched_together_get_two_follows(
        ledger, home, tree, vault) -> None:
    for sid in (GROK_ID, GROK_TWIN):
        _grok_child(ledger, home, tree, sid)
        _dispatched(vault, paths.short_id(sid))
        pr_follow.on_session_end({}, vault_root=vault, session_id=sid, cwd=str(tree),
                                 spawn=lambda: None, now=T0)
    assert sorted(pr_follow.load_ledger(vault)["children"]) == sorted(
        [GROK_SHORT, paths.short_id(GROK_TWIN)])


def test_a_claude_child_is_still_followed_under_its_first_eight(ledger, tree, vault) -> None:
    _dispatched(vault, CLAUDE_SHORT)
    pr_follow.on_session_end({}, vault_root=vault, session_id=CLAUDE_ID, cwd=str(tree),
                             spawn=lambda: None, now=T0)
    assert list(pr_follow.load_ledger(vault)["children"]) == [CLAUDE_SHORT]


class _Fake:
    def __init__(self, table) -> None:
        self.table = table

    def __call__(self, argv, cwd):
        for prefix, answer in self.table.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return answer
        return 1, "", "unexpected call"


def _red_pr() -> _Fake:
    row = {"number": 12, "url": "https://github.com/o/r/pull/12", "state": "OPEN",
           "isDraft": False, "additions": 1, "deletions": 1, "changedFiles": 1,
           "headRefOid": "abc123"}
    return _Fake({
        ("git", "rev-parse", "--abbrev-ref", "HEAD"): (0, "fix/issue-7\n", ""),
        ("git", "status", "--porcelain"): (0, "", ""),
        ("git", "rev-parse", "HEAD"): (0, "abc123\n", ""),
        ("git", "rev-list"): (0, "0\n", ""),
        ("gh", "pr", "list"): (0, json.dumps([row]), ""),
        ("gh", "pr", "checks"): (0, json.dumps([{"name": "job", "bucket": "fail"}]), ""),
        ("gh", "pr", "view"): (0, json.dumps({"mergeable": "MERGEABLE", "comments": [],
                                              "reviews": []}), ""),
        ("gh", "run"): (1, "", "no"),
        ("gh", "api"): (1, "", "no"),
    })


def _follow_grok(vault: Path, tree: Path) -> None:
    assert pr_follow.register(vault, short_id=GROK_SHORT, session_id=GROK_ID,
                              cwd=str(tree), parent=PARENT, now=T0)


def _pr_sweep(vault: Path, *, wake_fn=None, told=None):
    row = _session(GROK_SHORT, "grok", state="done", live=False, cwd="",
                   session_id=GROK_ID)
    return pr_follow.sweep({}, vault_root=vault, now=T0 + 400, run=_red_pr(),
                           wake_fn=wake_fn, reader=lambda: [row],
                           notify=told or (lambda *a: True), announce=False)


def test_pr_follow_wakes_a_grok_child_with_the_grok_nudge(
        ledger, leader, home, tree, vault, no_claude) -> None:
    _grok_child(ledger, home, tree)
    leader.session(GROK_ID, cwd=str(tree), resident=False, activity="dormant")
    _follow_grok(vault, tree)
    report = _pr_sweep(vault)
    assert report.woken == [GROK_SHORT]
    assert leader.prompts(GROK_ID) == [wake.pr_nudge(12, ["ci-red"], agent="grok")]


def test_an_unreachable_leader_leaves_the_follow_as_it_was(ledger, tree, vault, tmp_path) -> None:
    _grok_child(ledger, tmp_path / "no-such-home", tree)
    _follow_grok(vault, tree)
    sent: List[tuple] = []
    report = _pr_sweep(vault, told=lambda v, p, t: sent.append((p, t)) or True)
    assert len(report.failed) == 1 and report.closed == []
    entry = pr_follow.load_ledger(vault)["children"][GROK_SHORT]
    assert entry["closed"] is None and entry["attempts"] == [] and entry["woken_at"] is None
    assert sent == []


def test_a_refused_grok_wake_hands_back_in_grok_s_words(ledger, home, tree, vault) -> None:
    _grok_child(ledger, home, tree)
    _follow_grok(vault, tree)
    sent: List[tuple] = []
    report = _pr_sweep(vault, wake_fn=lambda sid, **k: "no Grok session",
                       told=lambda v, p, t: sent.append((p, t)) or True)
    assert report.closed == [(GROK_SHORT, "wake-failed")]
    (parent, text), = sent
    assert parent == PARENT
    assert "the turn sent through Grok's leader failed: no Grok session" in text
    assert "claude --bg --resume" not in text


def test_a_refused_claude_wake_still_names_claude(tree, vault) -> None:
    text = pr_follow.render_stopped(CLAUDE_SHORT, {"pr": None}, "wake-failed", ["ci-red"],
                                    s={"hours": 24}, why="exit 1")
    assert "`claude --bg --resume` failed: exit 1" in text


# ---------------------------------------------------------------------------
# tree-sweep: when a Grok child is gone
# ---------------------------------------------------------------------------

_LONG_AGO = "2026-09-01T00:00:00Z"
_NOW = 1_800_000_000.0


@pytest.mark.parametrize("state, live, gone", [
    ("done", False, True),       # finished, unloaded by its leader
    ("stopped", False, True),
    ("done", True, False),       # finished, still resident
    ("done", None, False),       # the leader could not be asked
    ("working", False, False),
])
def test_a_grok_child_is_gone_once_finished_and_not_resident(tree, state, live, gone) -> None:
    row = _session(GROK_SHORT, "grok", state=state, live=live, cwd=str(tree),
                   updated_at=_LONG_AGO)
    assert tree_sweep.child_gone(tree, [row], now=_NOW) is gone


def test_a_claude_child_still_needs_stopped(tree) -> None:
    row = _session(CLAUDE_SHORT, "claude", state="done", live=False, cwd=str(tree),
                   updated_at=_LONG_AGO)
    assert tree_sweep.child_gone(tree, [row], now=_NOW) is False


def test_a_grok_child_finished_moments_ago_keeps_its_tree(tree) -> None:
    from datetime import datetime, timezone

    just_now = datetime.fromtimestamp(_NOW - 60, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = _session(GROK_SHORT, "grok", state="done", live=False, cwd=str(tree),
                   updated_at=just_now)
    assert tree_sweep.child_gone(tree, [row], now=_NOW) is False
