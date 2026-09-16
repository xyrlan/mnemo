"""A finished child is stopped even when it delivered nothing.

The briefing is written by ``SessionEnd``, which only a *stopped* child fires
(#247). ``deliver``'s own stop runs after a successful delivery, so a child
that refused its task — and therefore has nothing to publish — was never
stopped and never remembered. That briefing is the most valuable one in the
system: an implementing child leaves a diff telling the story, a refusing one
leaves no diff at all.
"""
import pytest

from mnemo.core.sessions import delivery


class _Session:
    def __init__(self, short_id, state, live=True):
        self.short_id = short_id
        self.state = state
        self.live = live


@pytest.fixture
def tree(tmp_path):
    return tmp_path / "mnemo-wt-211"


def test_stops_a_done_session(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("aaaaaaaa", "done")])
    stopped = []
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: stopped.append(sid) or None)

    result = delivery.stop_done_in(tree)

    assert stopped == ["aaaaaaaa"]
    assert result == [("aaaaaaaa", None)]


def test_leaves_a_blocked_session_alone(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("bbbbbbbb", "blocked")])
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: pytest.fail("blocked must not be stopped"))

    assert delivery.stop_done_in(tree) == []


def test_skips_an_already_stopped_session(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("cccccccc", "stopped")])
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: pytest.fail("already stopped"))

    assert delivery.stop_done_in(tree) == []


def test_skips_a_done_session_with_no_process_left(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("dddddddd", "done", live=False)])
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: pytest.fail("nothing to stop"))

    assert delivery.stop_done_in(tree) == []


def test_reports_a_failed_stop_rather_than_raising(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("eeeeeeee", "done")])
    monkeypatch.setattr(delivery, "stop_session", lambda sid: "no such job")

    assert delivery.stop_done_in(tree) == [("eeeeeeee", "no such job")]


# --- the command that sweeps with it ---------------------------------------


def _args(**kw):
    import argparse

    ns = argparse.Namespace(ids=[], review=False, stop_done=False)
    for key, value in kw.items():
        setattr(ns, key, value)
    return ns


@pytest.fixture
def in_repo(tmp_path, monkeypatch):
    from mnemo.cli.commands import deliver

    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(deliver, "_repo_root", lambda: root)
    return root


def test_stop_done_sweeps_every_dispatch_worktree(in_repo, monkeypatch, capsys):
    """The sweep is over the trees, not over what was delivered."""
    from mnemo.cli.commands import deliver

    trees = [in_repo.parent / "mnemo-wt-211", in_repo.parent / "mnemo-wt-212"]
    monkeypatch.setattr(delivery, "dispatch_worktrees",
                        lambda *, repo_root: trees)
    seen = []
    monkeypatch.setattr(
        delivery, "stop_done_in",
        lambda tree: seen.append(tree) or [(f"id-{tree.name[-3:]}", None)],
    )

    assert deliver.cmd_deliver(_args(stop_done=True)) == 0
    assert seen == trees
    out = capsys.readouterr().out
    assert "mnemo-wt-211: stopped id-211" in out
    assert "mnemo-wt-212: stopped id-212" in out


def test_stop_done_needs_no_id_and_delivers_nothing(in_repo, monkeypatch, capsys):
    """No id is named, and nothing is pushed: this is not a delivery."""
    from mnemo.cli.commands import deliver

    monkeypatch.setattr(delivery, "dispatch_worktrees",
                        lambda *, repo_root: [in_repo.parent / "mnemo-wt-211"])
    monkeypatch.setattr(delivery, "stop_done_in", lambda tree: [])
    monkeypatch.setattr(
        delivery, "push",
        lambda *a, **kw: pytest.fail("--stop-done must never push"))

    assert deliver.cmd_deliver(_args(stop_done=True)) == 0
    assert "nothing finished and still running" in capsys.readouterr().out


def test_stop_done_reports_a_failed_stop(in_repo, monkeypatch, capsys):
    from mnemo.cli.commands import deliver

    monkeypatch.setattr(delivery, "dispatch_worktrees",
                        lambda *, repo_root: [in_repo.parent / "mnemo-wt-211"])
    monkeypatch.setattr(delivery, "stop_done_in",
                        lambda tree: [("ffffffff", "no such job")])

    assert deliver.cmd_deliver(_args(stop_done=True)) == 0
    out = capsys.readouterr().out
    assert "`claude stop ffffffff` failed: no such job" in out


def test_the_parser_accepts_stop_done() -> None:
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["deliver", "--stop-done"])
    assert args.stop_done is True
    assert args.ids == []


def test_stop_done_is_off_unless_asked() -> None:
    from mnemo.cli.parser import _build_parser

    assert _build_parser().parse_args(["deliver", "--review"]).stop_done is False


def test_stop_done_refuses_to_run_alongside_a_delivery(in_repo, monkeypatch, capsys):
    """Each flag means a different command; one must not silently drop."""
    from mnemo.cli.commands import deliver

    monkeypatch.setattr(
        delivery, "dispatch_worktrees",
        lambda *, repo_root: pytest.fail("refused before sweeping"))

    assert deliver.cmd_deliver(_args(stop_done=True, ids=["211"])) == 1
    assert deliver.cmd_deliver(_args(stop_done=True, review=True)) == 1
    assert "run it on its own" in capsys.readouterr().out


def test_review_names_the_children_holding_memory(in_repo, monkeypatch, capsys):
    """A done-but-running child is reported once, over both groups."""
    from mnemo.cli.commands import deliver

    tree = in_repo.parent / "mnemo-wt-211"
    monkeypatch.setattr(delivery, "dispatch_worktrees",
                        lambda *, repo_root: [tree])
    monkeypatch.setattr(delivery, "base_branch", lambda *, repo_root: "master")
    monkeypatch.setattr(
        delivery, "ready",
        lambda t, **kw: delivery.Readiness(worktree=t, target=211,
                                           reason="no commits ahead"))
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("aaaaaaaa", "done")])

    assert deliver.cmd_deliver(_args(review=True)) == 0
    out = capsys.readouterr().out
    assert "TERMINADAS, NÃO PARADAS (1)" in out
    assert "#211  aaaaaaaa" in out
    assert "mnemo deliver --stop-done" in out


def test_review_is_silent_when_nothing_is_holding(in_repo, monkeypatch, capsys):
    from mnemo.cli.commands import deliver

    tree = in_repo.parent / "mnemo-wt-211"
    monkeypatch.setattr(delivery, "dispatch_worktrees",
                        lambda *, repo_root: [tree])
    monkeypatch.setattr(delivery, "base_branch", lambda *, repo_root: "master")
    monkeypatch.setattr(
        delivery, "ready",
        lambda t, **kw: delivery.Readiness(worktree=t, target=211,
                                           reason="no commits ahead"))
    # Already stopped, and one done child the roster proves is gone.
    monkeypatch.setattr(
        delivery, "sessions_in",
        lambda _t: [_Session("bbbbbbbb", "stopped"),
                    _Session("cccccccc", "done", live=False)])

    assert deliver.cmd_deliver(_args(review=True)) == 0
    assert "TERMINADAS" not in capsys.readouterr().out
