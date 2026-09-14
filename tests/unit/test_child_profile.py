"""The lean profile a dispatched child starts with (#270).

What is verified here is the *shape* of the decision — which flags go on the
command line, and that mnemo's own hooks and MCP server survive dropping the
user profile. The token saving those flags buy is not assertable from a unit
test (it depends on what the maintainer has installed); it was measured
against real ``--bg`` children and is recorded in
:mod:`mnemo.core.child_profile` and in the ``lean-child-profile`` assumption,
with ``pytest -m live_claude`` as the check that the flags still do what the
measurement assumed.

The trap under test is the middle row of that table: the flags that drop the
noise drop mnemo with it, because mnemo installs itself into exactly the
user-level files being dropped. Most of these tests exist to keep the
hand-back from silently regressing into an empty file.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mnemo.core import child_profile, dispatch

# A user settings.json shaped like a real one: mnemo's four hooks mixed in
# among a maintainer's own, one of which (PreToolUse) carries a matcher.
USER_SETTINGS = {
    "model": "opus[1m]",
    "enabledPlugins": {"superpowers@superpowers-dev": True, "vercel@official": True},
    "statusLine": {"type": "command", "command": "bash /somewhere/statusline.sh"},
    "hooks": {
        "SessionStart": [
            {"hooks": [{"type": "command", "command": "/usr/bin/python3 -m mnemo.hooks.session_start"}]},
            {"hooks": [{"type": "command", "command": "gk ai hook run"}]},
        ],
        "PreToolUse": [
            {
                "matcher": "Bash|Edit|Write|MultiEdit",
                "hooks": [{"type": "command", "command": "/usr/bin/python3 -m mnemo.hooks.pre_tool_use"}],
            },
        ],
        "Notification": [
            {"hooks": [{"type": "command", "command": "/opt/unrelated/notify.sh"}]},
        ],
    },
}

USER_CLAUDE_JSON = {
    "numStartups": 400,
    "mcpServers": {
        "mnemo": {"command": "/usr/bin/python3", "args": ["-m", "mnemo", "mcp-server"]},
        "sentry": {"command": "npx", "args": ["-y", "sentry-mcp"]},
        "google-ads": {"command": "uvx", "args": ["google-ads-mcp"]},
    },
}


@pytest.fixture()
def installed(monkeypatch, tmp_path: Path) -> Path:
    """A HOME with mnemo installed the way ``mnemo init`` installs it."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps(USER_SETTINGS), encoding="utf-8")
    (home / ".claude.json").write_text(
        json.dumps(USER_CLAUDE_JSON), encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("MNEMO_DISPATCH_FULL_PROFILE", raising=False)
    return home


# --- what is kept ----------------------------------------------------------


def test_mnemo_hooks_are_lifted_out_of_the_users_settings(installed) -> None:
    """Only mnemo's hooks come across — not the maintainer's own."""
    hooks = child_profile.mnemo_hooks()

    assert set(hooks) == {"SessionStart", "PreToolUse"}
    commands = [
        hook["command"]
        for entries in hooks.values() for entry in entries for hook in entry["hooks"]
    ]
    assert all("mnemo.hooks." in command for command in commands)
    # The unrelated ones stayed behind, which is the entire point of #270.
    assert not any("gk ai hook" in c or "notify.sh" in c for c in commands)
    assert "Notification" not in hooks


def test_a_hooks_matcher_travels_with_it(installed) -> None:
    """``PreToolUse`` is installed with a matcher; re-registered without one it
    would fire on every tool instead of on four."""
    entry = child_profile.mnemo_hooks()["PreToolUse"][0]
    assert entry["matcher"] == "Bash|Edit|Write|MultiEdit"


def test_only_the_mnemo_mcp_server_is_carried_over(installed) -> None:
    """The vault is the point; the maintainer's other servers are the cost."""
    servers = child_profile.mnemo_mcp_servers()

    assert set(servers) == {"mnemo"}
    assert servers["mnemo"]["args"] == ["-m", "mnemo", "mcp-server"]


def test_the_mcp_entry_is_copied_from_the_installation_not_rebuilt(installed) -> None:
    """It points at the interpreter that ran ``mnemo init`` — the one that can
    import mnemo. Rebuilding it from the dispatching process would be right
    only while the two happen to be the same, and silently wrong in a venv."""
    (installed / ".claude.json").write_text(json.dumps({
        "mcpServers": {"mnemo": {"command": "/venv/bin/python", "args": ["-m", "mnemo", "mcp-server"]}}
    }), encoding="utf-8")

    assert child_profile.mnemo_mcp_servers()["mnemo"]["command"] == "/venv/bin/python"


# --- the argv --------------------------------------------------------------


def test_lean_args_drop_the_user_profile_and_hand_mnemo_back(installed, tmp_path) -> None:
    tree = tmp_path / "wt"
    tree.mkdir()

    args = child_profile.lean_args(tree)

    # Dropped: the user's settings file, and every MCP server but ours.
    assert args[:3] == ["--setting-sources", "project,local", "--strict-mcp-config"]
    # Handed back: both halves of mnemo.
    assert "--mcp-config" in args and "--settings" in args

    written = json.loads(Path(args[args.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert set(written["hooks"]) == {"SessionStart", "PreToolUse"}
    served = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    assert set(served["mcpServers"]) == {"mnemo"}


def test_the_repos_own_settings_still_load(installed, tmp_path) -> None:
    """``project,local`` and not the empty string: a child working in a repo
    obeys that repo's settings. Only the *user* profile is dropped."""
    sources = child_profile.lean_args(tmp_path)[1]
    assert "project" in sources and "local" in sources
    assert "user" not in sources


def test_the_profile_directory_ignores_itself(installed, tmp_path) -> None:
    """Children run ``git add -A``. Without this the child commits its own
    launch configuration into the branch it was dispatched to write."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "master"], cwd=repo, check=True)

    child_profile.lean_args(repo)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

    staged = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                            capture_output=True, text=True, check=True)
    assert staged.stdout.strip() == ""


# --- mnemo not installed ---------------------------------------------------


def test_a_child_is_still_dispatchable_when_mnemo_is_not_installed(monkeypatch, tmp_path) -> None:
    """Nothing to hand back is not a reason to refuse the dispatch — it is a
    reason to say what the child will be missing."""
    home = tmp_path / "bare"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    tree = tmp_path / "wt"
    tree.mkdir()
    args = child_profile.lean_args(tree)

    assert args == list(child_profile.LEAN_FLAGS)  # no empty files pointed at
    gaps = child_profile.missing_pieces()
    assert len(gaps) == 2
    assert any("MCP" in g for g in gaps) and any("briefing" in g for g in gaps)
    assert all("mnemo init" in g for g in gaps)


def test_unreadable_config_is_not_fatal(monkeypatch, tmp_path) -> None:
    """A corrupt settings.json would otherwise take the whole dispatch down."""
    home = tmp_path / "broken"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    (home / ".claude.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    assert child_profile.mnemo_hooks() == {}
    assert child_profile.mnemo_mcp_servers() == {}


# --- the opt-out -----------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("yes", True), ("on", True),
    ("", False), ("0", False), ("false", False), ("no", False), ("off", False),
])
def test_the_environment_opt_out(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("MNEMO_DISPATCH_FULL_PROFILE", value)
    assert child_profile.env_opts_out() is expected


def test_opt_out_is_absent_by_default(monkeypatch) -> None:
    monkeypatch.delenv("MNEMO_DISPATCH_FULL_PROFILE", raising=False)
    assert child_profile.env_opts_out() is False


# --- the spawn -------------------------------------------------------------

REAL_BG_STDOUT = (
    "backgrounded · \x1b[36ma1b2c3d4\x1b[39m\n"
    "  claude agents             list sessions\n"
)


def _spy(monkeypatch, seen: dict):
    def fake_run(args, **kwargs):
        seen["args"] = args
        return subprocess.CompletedProcess(args, 0, stdout=REAL_BG_STDOUT, stderr="")
    monkeypatch.setattr(dispatch.subprocess, "run", fake_run)


@pytest.mark.real_spawn
def test_spawn_is_lean_by_default(installed, tmp_path, monkeypatch) -> None:
    seen: dict = {}
    _spy(monkeypatch, seen)

    dispatch.spawn_child("do the thing", cwd=tmp_path)

    args = seen["args"]
    assert "--setting-sources" in args and "--strict-mcp-config" in args
    # The prompt stays positional and last — the flags go before it, so the
    # #211/#197 shape of the command is unchanged.
    assert args[-1] == "do the thing"
    assert args[0] == "claude" and args[1] == "--bg"


@pytest.mark.real_spawn
def test_spawn_full_profile_adds_no_flags(installed, tmp_path, monkeypatch) -> None:
    seen: dict = {}
    _spy(monkeypatch, seen)

    dispatch.spawn_child("x", cwd=tmp_path, lean=False)

    assert seen["args"] == ["claude", "--bg", "x"]


@pytest.mark.real_spawn
def test_the_environment_opt_out_reaches_the_spawn(installed, tmp_path, monkeypatch) -> None:
    """The escape hatch has to work without threading a flag through every
    caller — that is what makes it usable for a one-off dispatch."""
    seen: dict = {}
    _spy(monkeypatch, seen)
    monkeypatch.setenv("MNEMO_DISPATCH_FULL_PROFILE", "1")

    dispatch.spawn_child("x", cwd=tmp_path)

    assert seen["args"] == ["claude", "--bg", "x"]


def test_lean_travels_from_dispatch_all_to_the_spawn(installed, tmp_path, monkeypatch) -> None:
    """The flag is only useful if it survives the orchestration layer."""
    seen: dict = {}

    def fake_spawn(prompt, *, cwd, model=None, lean=True):
        seen["lean"] = lean
        return "a1b2c3d4"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    monkeypatch.setattr(dispatch, "ensure_worktree", lambda *a, **k: tmp_path)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)

    dispatch.dispatch_all([1], repo_root=tmp_path,
                          fetch=lambda i, **k: dispatch.Issue(i, "t", "b"), lean=False)
    assert seen["lean"] is False

    dispatch.dispatch_all([2], repo_root=tmp_path,
                          fetch=lambda i, **k: dispatch.Issue(i, "t", "b"))
    assert seen["lean"] is True


# --- the assumption --------------------------------------------------------


def test_the_flags_are_stated_as_an_assumption() -> None:
    """A CLI behaviour mnemo depends on is data in ``claude_cli``, so the live
    test can check it against the installed binary rather than a fixture."""
    from mnemo.core import claude_cli

    claim = claude_cli.assumption("lean-child-profile").claim
    for flag in ("--setting-sources", "--strict-mcp-config", "--mcp-config", "--settings"):
        assert flag in claim


# --- the report ------------------------------------------------------------


def test_the_report_names_the_profile_and_the_opt_out(installed, capsys, tmp_path) -> None:
    """A child missing a plugin it needed is confusing to debug from outside;
    one line naming the profile is what makes the cause findable."""
    import argparse

    from mnemo.cli.commands import dispatch as dispatch_cmd

    started = [dispatch.Dispatched(issue=1, worktree=tmp_path, short_id="a1b2c3d4")]
    dispatch_cmd._report(started, lean=True)

    out = capsys.readouterr().out
    assert "profile: lean" in out
    assert "--full-profile" in out

    dispatch_cmd._report(started, lean=False)
    assert "full user profile" in capsys.readouterr().out


def test_mnemo_not_installed_is_not_labelled_WARNING(monkeypatch, capsys, tmp_path) -> None:
    """``WARNING`` in this report means a ``claude`` CLI assumption broke
    (#235). "mnemo is not installed" is a fact about the machine, not about
    the spawn, and sharing the label would make one read as the other."""
    from mnemo.cli.commands import dispatch as dispatch_cmd

    home = tmp_path / "bare"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    dispatch_cmd._report(
        [dispatch.Dispatched(issue=1, worktree=tmp_path, short_id="a1b2c3d4")],
        lean=True,
    )

    out = capsys.readouterr().out
    assert "incomplete:" in out and "mnemo init" in out
    assert "WARNING" not in out


def test_the_report_tells_the_truth_when_the_environment_opts_out(
    installed, capsys, tmp_path, monkeypatch
) -> None:
    """The report used to read ``profile: lean`` for a child that
    ``MNEMO_DISPATCH_FULL_PROFILE=1`` had just started on the full profile —
    the one thing the line exists to be right about."""
    from mnemo.cli.commands import dispatch as dispatch_cmd

    monkeypatch.setenv("MNEMO_DISPATCH_FULL_PROFILE", "1")

    # lean=True is what the *flag* asked for; the environment overrides it.
    dispatch_cmd._report(
        [dispatch.Dispatched(issue=1, worktree=tmp_path, short_id="a1b2c3d4")],
        lean=True,
    )

    out = capsys.readouterr().out
    assert "profile: lean" not in out
    assert "full user profile" in out
    assert "MNEMO_DISPATCH_FULL_PROFILE" in out


def test_is_lean_is_the_single_answer(monkeypatch) -> None:
    """Both opt-outs run through one function, so the spawn and the report
    cannot disagree about what the child got."""
    monkeypatch.delenv("MNEMO_DISPATCH_FULL_PROFILE", raising=False)
    assert child_profile.is_lean(True) is True
    assert child_profile.is_lean(False) is False

    monkeypatch.setenv("MNEMO_DISPATCH_FULL_PROFILE", "1")
    assert child_profile.is_lean(True) is False   # the environment wins
    assert child_profile.is_lean(False) is False
