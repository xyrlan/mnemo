"""`mnemo sync` end to end (#642): two temp vaults and a bare repository as the hub.

No network, no real vault, no real git config: the hub is a bare repository
under ``tmp_path``, every vault is under ``tmp_path``, and git reads neither
the developer's global config nor the system one. Paths are compared as
``Path`` objects and file contents as bytes.

Under ``tests/unit/`` although it runs git, because that is the directory
CI's Windows job runs, and Windows is where a line ending or a path
separator would break this.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List

import pytest

from mnemo.core import locks, sync

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

PAGE = (
    "---\n"
    "name: Use the staging hub\n"
    "description: which hub the deploy script reads\n"
    "type: feedback\n"
    "sources:\n"
    "  - bots/app/memory/hub.md\n"
    "tags:\n"
    "  - deploy\n"
    "---\n"
    "Deploys read the staging hub first.\n"
)
# Built at run time so no secret shape sits in the repository's own text.
TOKEN = "ghp_" + "a1B2c3D4e5" * 4


@pytest.fixture(autouse=True)
def _git_reads_no_real_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Git sees no global or system config, and no identity from the shell."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                 "GIT_COMMITTER_EMAIL", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


class Box:
    """One machine: a vault, a machine id and its own config file."""

    def __init__(self, root: Path, machine: str) -> None:
        self.vault = root / machine / "mnemo"
        self.machine = machine
        self.config = self.vault / "mnemo.config.json"

    def init(self, hub) -> "sync.Outcome":
        return sync.init(self.vault, str(hub), machine=self.machine, config_path=self.config)

    def join(self, hub) -> "sync.Outcome":
        return sync.join(self.vault, str(hub), machine=self.machine, config_path=self.config)

    def pull(self) -> "sync.Outcome":
        return sync.pull(self.vault, machine=self.machine)

    def push(self) -> "sync.Outcome":
        return sync.push(self.vault, machine=self.machine, config_path=self.config)

    def status(self) -> dict:
        return sync.status(self.vault, machine=self.machine)

    def write(self, rel: str, data: bytes) -> Path:
        path = self.vault.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read(self, rel: str) -> bytes:
        return self.vault.joinpath(*rel.split("/")).read_bytes()


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return out.stdout.decode("utf-8")


def _hub_files(hub: Path) -> List[str]:
    return sorted(_git(hub, "ls-tree", "-r", "--name-only", "main").splitlines())


def _snapshot(vault: Path) -> Dict[Path, bytes]:
    """Every file of the vault but git's own directory and sync's local bookkeeping."""
    skip = {Path(".mnemo") / "sync-state.json", Path(".mnemo") / "sync-log.jsonl"}
    out: Dict[Path, bytes] = {}
    for path in sorted(vault.rglob("*")):
        rel = path.relative_to(vault)
        if rel.parts[0] == ".git" or rel in skip or not path.is_file():
            continue
        out[rel] = path.read_bytes()
    return out


def _unmerged(vault: Path) -> str:
    return _git(vault, "ls-files", "-u")


# The remote as both machines name it: relative to the vault, so a whole
# ``<root>/{hub.git,mac/mnemo,win/mnemo}`` tree can be copied and still work.
REMOTE = "../../hub.git"


def _bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    return path


def _primary(root: Path) -> Box:
    """The primary: a vault with one of everything that travels."""
    box = Box(root, "mac")
    box.write("shared/feedback/use-the-staging-hub.md", PAGE.encode("utf-8"))
    box.write("shared/_inbox/feedback/staged.md", b"---\nname: staged\n---\nstaged\n")
    box.write("bots/app/briefings/sessions/s1.md", b"# briefing one\n")
    box.write("bots/app/memory/MEMORY.md", b"- a note\n")
    box.write(".mnemo/extraction-state.json", b'{"entries": {}}\n')
    box.write("HOME.md", b"# home\n")
    box.config.write_text(json.dumps({"vaultRoot": str(box.vault)}), encoding="utf-8")
    return box


@pytest.fixture
def hub(tmp_path: Path, request: pytest.FixtureRequest) -> Path:
    """The bare repository at ``tmp_path/hub.git``: the copied one when the
    test also asks for ``pair``, an empty one otherwise."""
    if "pair" in request.fixturenames:
        request.getfixturevalue("pair")
        return tmp_path / "hub.git"
    return _bare(tmp_path / "hub.git")


@pytest.fixture
def mac(tmp_path: Path) -> Box:
    return _primary(tmp_path)


@pytest.fixture
def win(tmp_path: Path) -> Box:
    return Box(tmp_path, "win")


_JOINED: Dict[str, Path] = {}


@pytest.fixture
def pair(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory):
    """A primary and a satellite already in sync through ``tmp_path/hub.git``.

    ``init`` and ``join`` are run for real once, by the first test that asks
    (others cover them directly); every test gets its own copy of the result.
    Each git call costs a process, which on CI's Windows runner is most of
    what these tests cost.
    """
    if "root" not in _JOINED:
        root = tmp_path_factory.mktemp("sync-joined")
        _bare(root / "hub.git")
        assert _primary(root).init(REMOTE).ok
        assert Box(root, "win").join(REMOTE).ok
        _JOINED["root"] = root
    for name in ("hub.git", "mac", "win"):
        shutil.copytree(str(_JOINED["root"] / name), str(tmp_path / name))
    mac, win = Box(tmp_path, "mac"), Box(tmp_path, "win")
    for box in (mac, win):  # the one absolute path the copy carried along
        raw = json.loads(box.config.read_text(encoding="utf-8"))
        raw["vaultRoot"] = str(box.vault)
        box.config.write_text(json.dumps(raw), encoding="utf-8")
    return mac, win


# --- off by default --------------------------------------------------------


def test_a_vault_never_initialised_is_off_and_nothing_touches_it(mac: Box):
    before = _snapshot(mac.vault)
    assert mac.status()["enabled"] is False
    assert mac.pull().outcome == "off"
    assert mac.push().outcome == "off"
    assert not (mac.vault / ".git").exists()
    assert _snapshot(mac.vault) == before


# --- init, join, and the two directions ------------------------------------


def test_init_then_join_then_each_side_reaches_the_other(mac: Box, win: Box, hub: Path):
    assert mac.init(hub).ok
    joined = win.join(hub)
    assert joined.ok, joined.message
    for rel in ("shared/feedback/use-the-staging-hub.md", "shared/_inbox/feedback/staged.md",
                "bots/app/briefings/sessions/s1.md", "bots/app/memory/MEMORY.md",
                ".mnemo/extraction-state.json", "HOME.md"):
        assert win.read(rel) == mac.read(rel), rel
    assert mac.status()["role"] == "primary"
    assert win.status()["role"] == "satellite"
    assert win.status()["primary"] == "mac"

    # A briefing written on the satellite reaches the primary.
    win.write("bots/app/briefings/sessions/s2.md", b"# briefing two, from the satellite\n")
    assert win.push().outcome == "ok"
    assert mac.pull().outcome == "ok"
    assert mac.read("bots/app/briefings/sessions/s2.md") == b"# briefing two, from the satellite\n"

    # A page changed on the primary reaches the satellite.
    changed = PAGE.replace("staging hub first", "production hub last").encode("utf-8")
    mac.write("shared/feedback/use-the-staging-hub.md", changed)
    assert mac.push().outcome == "ok"
    assert win.pull().outcome == "ok"
    assert win.read("shared/feedback/use-the-staging-hub.md") == changed


def test_init_twice_is_safe_and_sync_commits_need_no_git_identity(mac: Box, hub: Path):
    assert mac.init(hub).ok
    first = _git(hub, "rev-parse", "main")
    again = mac.init(hub)
    assert again.ok, again.message
    assert _git(hub, "rev-parse", "main") == first
    assert _git(hub, "log", "-1", "--format=%an", "main").strip() == "mnemo sync"


def test_init_refuses_a_hub_that_already_holds_another_vault(mac: Box, win: Box, hub: Path, tmp_path: Path):
    assert mac.init(hub).ok
    other = Box(tmp_path, "other")
    other.write("shared/feedback/x.md", b"x\n")
    out = other.init(hub)
    assert not out.ok
    assert "join" in out.message
    assert not (other.vault / ".mnemo" / "sync" / "primary.json").exists()
    assert not (other.vault / ".git").exists()   # the repository it made is taken back
    assert other.status()["enabled"] is False


def test_the_hub_moving_first_is_pulled_and_the_push_retried(pair):
    mac, win = pair
    win.write("bots/app/briefings/sessions/s2.md", b"# two\n")
    assert win.push().outcome == "ok"
    mac.write("shared/feedback/another.md", b"---\nname: another\n---\nbody\n")
    out = mac.push()
    assert out.outcome == "ok", out.message
    assert mac.read("bots/app/briefings/sessions/s2.md") == b"# two\n"
    assert win.pull().outcome == "ok"
    assert win.read("shared/feedback/another.md") == b"---\nname: another\n---\nbody\n"


def test_a_page_removed_on_the_primary_is_removed_on_the_satellite(pair):
    mac, win = pair
    (mac.vault / "shared" / "_inbox" / "feedback" / "staged.md").unlink()
    assert mac.push().outcome == "ok"
    assert win.pull().outcome == "ok"
    assert not (win.vault / "shared" / "_inbox" / "feedback" / "staged.md").exists()
    assert win.status()["pendingFiles"] == 0


def test_a_commit_a_killed_push_left_behind_is_pushed_once(pair, hub: Path):
    """The branch steps back onto what the hub has; the work tree is the truth."""
    mac, win = pair
    mac.write("bots/app/briefings/sessions/s7.md", b"# seven\n")
    # What a push killed between its commit and its send leaves: a local
    # commit the hub never saw.
    _git(mac.vault, "add", "-f", "bots/app/briefings/sessions/s7.md")
    _git(mac.vault, "-c", "user.name=k", "-c", "user.email=k@example.com", "commit", "-q", "-m", "killed")
    assert mac.status()["ahead"] == 1
    win.write("bots/app/briefings/sessions/s8.md", b"# eight\n")
    assert win.push().outcome == "ok"

    assert mac.push().outcome == "ok"
    assert mac.status()["ahead"] == 0 and mac.status()["pendingFiles"] == 0
    assert "killed" not in _git(hub, "log", "--format=%s", "main")
    assert win.pull().outcome == "ok"
    assert win.read("bots/app/briefings/sessions/s7.md") == b"# seven\n"
    assert mac.read("bots/app/briefings/sessions/s8.md") == b"# eight\n"


def test_nothing_to_push_is_not_an_error(pair):
    mac, _win = pair
    out = mac.push()
    assert out.ok and out.outcome == "nothing"


# --- conflicts -------------------------------------------------------------


def _losers(box: Box) -> List[Path]:
    root = box.vault / "shared" / "_archive" / "sync-losers"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.is_dir() else []


@pytest.mark.parametrize("first", ["satellite", "primary"])
def test_the_same_page_edited_on_both_sides_ends_with_the_primarys(pair, hub: Path, first: str):
    mac, win = pair
    rel = "shared/feedback/use-the-staging-hub.md"
    primarys = PAGE.replace("first", "PRIMARY").encode("utf-8")
    satellites = PAGE.replace("first", "SATELLITE").encode("utf-8")
    mac.write(rel, primarys)
    win.write(rel, satellites)

    if first == "satellite":
        assert win.push().outcome == "ok"
        assert mac.push().outcome == "ok"   # pulls, keeps its own, publishes it
        assert win.pull().outcome == "ok"
    else:
        assert mac.push().outcome == "ok"
        assert win.push().ok                # pulls, loses, has nothing left to publish
        assert mac.pull().ok

    assert mac.read(rel) == primarys
    assert win.read(rel) == satellites.replace(b"SATELLITE", b"PRIMARY")
    assert _git(hub, "show", "main:" + rel).encode("utf-8") == primarys
    for box in (mac, win):
        assert _unmerged(box.vault) == ""
        assert b"<<<<<<<" not in box.read(rel)
    # The loser is kept on the machine that lost, outside the tracked set.
    kept = _losers(win)
    assert [p.read_bytes() for p in kept] == [satellites]
    assert kept[0].relative_to(win.vault).parts[-3:] == ("shared", "feedback", "use-the-staging-hub.md")
    assert _losers(mac) == []
    assert win.status()["conflictsResolved"] == 1
    assert not any("sync-losers" in name for name in _hub_files(hub))


# --- an unreachable hub ----------------------------------------------------


@pytest.mark.parametrize("op", ["pull", "push"])
def test_an_unreachable_hub_leaves_the_vault_as_it_was(pair, tmp_path: Path, op: str):
    _mac, win = pair
    win.write("bots/app/briefings/sessions/s3.md", b"# three\n")
    _git(win.vault, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    before = _snapshot(win.vault)
    head = _git(win.vault, "rev-parse", "HEAD")
    out = getattr(win, op)()
    assert out.outcome == "unreachable"
    assert not out.ok
    assert "reach" in out.message
    assert _snapshot(win.vault) == before
    assert _git(win.vault, "rev-parse", "HEAD") == head
    assert win.status()["lastError"]["op"] == op


def test_join_from_an_unreachable_hub_leaves_an_existing_vault_alone(win: Box, tmp_path: Path):
    win.write("shared/feedback/mine.md", b"mine\n")
    before = _snapshot(win.vault)
    out = win.join(tmp_path / "gone.git")
    assert not out.ok
    assert _snapshot(win.vault) == before
    assert [p.name for p in win.vault.parent.iterdir()] == ["mnemo"]


# --- the secret gate -------------------------------------------------------


def test_a_push_carrying_a_secret_shape_is_refused_until_it_is_gone(pair, hub: Path):
    mac, _win = pair
    rel = "bots/app/memory/deploy.md"
    mac.write(rel, ("the token is " + TOKEN + "\n").encode("utf-8"))
    before = _git(hub, "rev-parse", "main")

    out = mac.push()
    assert out.outcome == "refused" and not out.ok
    assert rel in out.message and "GitHub token" in out.message
    assert TOKEN not in out.message
    # Where to fix it: the mirror would copy the original back.
    assert "Claude Code's own memory" in out.message
    assert _git(hub, "rev-parse", "main") == before
    status = mac.status()
    assert status["refused"] == [{"path": rel, "kind": "GitHub token"}]
    assert status["pushesRefused"] == 1
    assert TOKEN not in json.dumps(status)
    # Nothing the gate refused is left in the local history either.
    assert TOKEN not in _git(mac.vault, "log", "--all", "-p")

    mac.write(rel, b"the token lives in the keychain\n")
    assert mac.push().outcome == "ok"
    assert mac.status()["refused"] == []
    assert _git(hub, "show", "main:" + rel) == "the token lives in the keychain\n"
    assert TOKEN not in _git(hub, "log", "--all", "-p")


# --- the tracked set -------------------------------------------------------


NEVER_TRACKED = [
    "shared/_archive/feedback/old.md",
    ".mnemo/reflex-index.json",
    ".mnemo/judge-picks.jsonl",
    "bots/app/logs/2026-10-13.md",
    "bots/app/working/scratch.md",
    ".obsidian/app.json",
    "mnemo.config.json.bak.20260915T233638",
    "HOME.md.bak",
    ".errors.log",
    ".errors.log.20260915T094852",
    "rater-check/pairs.json",
    "README.md",
    # Agent directories left by test runs and probes.
    "bots/bg-0p3r5wj-T-pytest-of-you-pytest-12/briefings/sessions/a.md",
    "bots/claude-jobs-93f2-tmp-probe/memory/MEMORY.md",
    "bots/claude-tmp-model-probe-DKBc/briefings/sessions/b.md",
    "bots/501--Users-you-github-app-1a2f/briefings/sessions/c.md",
    "bots/private-pytest-of-you-test-run0/memory/MEMORY.md",
    "bots/my-scratchpad-repo/briefings/sessions/d.md",
]


def test_one_file_of_each_never_tracked_kind_is_planted_and_none_is_committed(mac: Box, hub: Path):
    for rel in NEVER_TRACKED:
        mac.write(rel, b"planted\n")
    assert mac.init(hub).ok
    assert _hub_files(hub) == sorted([
        ".mnemo/extraction-state.json",
        ".mnemo/sync/config-seed.json",
        ".mnemo/sync/format.json",
        ".mnemo/sync/primary.json",
        "HOME.md",
        "bots/app/briefings/sessions/s1.md",
        "bots/app/memory/MEMORY.md",
        "shared/_inbox/feedback/staged.md",
        "shared/feedback/use-the-staging-hub.md",
    ])
    # Nothing is deleted from the vault, and the config stays where it was.
    for rel in NEVER_TRACKED + ["mnemo.config.json"]:
        assert mac.vault.joinpath(*rel.split("/")).is_file(), rel


def test_a_hub_carrying_a_path_outside_the_tracked_set_cannot_write_it(pair, hub: Path, tmp_path: Path):
    """A pull only ever writes tracked paths: the hub cannot replace a config."""
    mac, win = pair
    rogue = tmp_path / "rogue"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(hub), str(rogue)], check=True)
    (rogue / "mnemo.config.json").write_text('{"vaultRoot": "/elsewhere"}', encoding="utf-8")
    _git(rogue, "add", "-f", "mnemo.config.json")
    _git(rogue, "-c", "user.name=r", "-c", "user.email=r@example.com", "commit", "-q", "-m", "rogue")
    _git(rogue, "push", "-q", "origin", "main")
    config_before = win.config.read_bytes()
    assert win.pull().ok
    assert win.config.read_bytes() == config_before


# --- bytes -----------------------------------------------------------------


def test_a_page_with_crlf_comes_back_byte_for_byte(mac: Box, win: Box, hub: Path,
                                                 tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # A machine whose git converts line endings, which is Git for Windows'
    # default: without sync's own settings this is where the bytes change.
    hostile = tmp_path / "gitconfig"
    hostile.write_text("[core]\n\tautocrlf = true\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    crlf = b"---\r\nname: crlf page\r\n---\r\nline one\r\nline two\r\n"
    mixed = b"lf line\ncrlf line\r\nbare cr\rend"
    mac.write("shared/feedback/crlf.md", crlf)
    mac.write("shared/feedback/mixed.md", mixed)
    assert mac.init(hub).ok
    assert win.join(hub).ok
    assert win.read("shared/feedback/crlf.md") == crlf
    assert win.read("shared/feedback/mixed.md") == mixed
    assert win.read("shared/feedback/use-the-staging-hub.md") == PAGE.encode("utf-8")
    # And the other way, through a pull.
    win.write("bots/app/briefings/sessions/crlf.md", crlf)
    assert win.push().outcome == "ok"
    assert mac.pull().outcome == "ok"
    assert mac.read("bots/app/briefings/sessions/crlf.md") == crlf
    assert mac.read("shared/feedback/crlf.md") == crlf


# --- join ------------------------------------------------------------------


def test_join_over_an_existing_vault_moves_it_aside(mac: Box, win: Box, hub: Path):
    assert mac.init(hub).ok
    win.write("shared/feedback/only-here.md", b"written before the join\n")
    out = win.join(hub)
    assert out.ok, out.message
    aside = [p for p in win.vault.parent.iterdir() if p.name.startswith("mnemo.pre-sync-")]
    assert len(aside) == 1
    assert (aside[0] / "shared" / "feedback" / "only-here.md").read_bytes() == b"written before the join\n"
    assert str(aside[0]) in out.message
    assert not (win.vault / "shared" / "feedback" / "only-here.md").exists()
    assert win.read("HOME.md") == mac.read("HOME.md")


def test_join_twice_does_not_move_the_joined_vault_aside(pair, hub: Path):
    _mac, win = pair
    out = win.join(REMOTE)
    assert out.ok
    assert [p.name for p in win.vault.parent.iterdir()] == ["mnemo"]


def test_join_seeds_the_config_once_without_paths_or_secrets(mac: Box, win: Box, hub: Path):
    mac.config.write_text(json.dumps({
        "vaultRoot": str(mac.vault),
        "reflex": {"enabled": False, "judge": {"keyEnv": "TYPESAFE_API_KEY"}},
        "extraction": {"model": "claude-haiku-4-5"},
        "sync": {"machineId": "mac"},
        "tools": {"editor": "/Users/you/bin/edit", "drive": "C:\\Users\\you\\x", "home": "~/x"},
        "service": {"apiToken": TOKEN, "note": "the key is " + TOKEN},
    }), encoding="utf-8")
    assert mac.init(hub).ok
    assert win.join(hub).ok
    seeded = json.loads(win.config.read_text(encoding="utf-8"))
    assert Path(seeded.pop("vaultRoot")) == win.vault
    assert seeded == {
        "reflex": {"enabled": False, "judge": {"keyEnv": "TYPESAFE_API_KEY"}},
        "extraction": {"model": "claude-haiku-4-5"},
        "sync": {"machineId": "win"},
    }
    # Written once: after that the two configs are independent.
    raw = json.loads(mac.config.read_text(encoding="utf-8"))
    raw["extraction"]["model"] = "changed-on-the-primary"
    mac.config.write_text(json.dumps(raw), encoding="utf-8")
    assert mac.push().outcome == "ok"
    assert win.pull().outcome == "ok"
    assert json.loads(win.config.read_text(encoding="utf-8"))["extraction"]["model"] == "claude-haiku-4-5"


# --- the reflex index ------------------------------------------------------


def test_a_pull_that_changed_shared_rebuilds_the_reflex_index(pair):
    from mnemo.core.reflex import index as reflex_index

    def indexed(box: Box) -> set:
        return {doc["path"] for doc in reflex_index.load_index(box.vault)["docs"].values()}

    mac, win = pair
    assert indexed(win) == {"shared/feedback/use-the-staging-hub.md"}   # join built it
    mac.write("shared/feedback/pin-the-runner.md", PAGE.replace("Use the staging hub", "Pin the runner").encode())
    assert mac.push().outcome == "ok"
    assert win.pull().outcome == "ok"
    assert indexed(win) == {"shared/feedback/use-the-staging-hub.md", "shared/feedback/pin-the-runner.md"}


# --- format version --------------------------------------------------------


def test_an_unknown_hub_format_neither_pulls_nor_pushes(pair, hub: Path, tmp_path: Path):
    mac, win = pair
    newer = tmp_path / "newer"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(hub), str(newer)], check=True)
    (newer / ".mnemo" / "sync" / "format.json").write_text('{"version": 99}', encoding="utf-8")
    (newer / "HOME.md").write_text("# from the future\n", encoding="utf-8")
    _git(newer, "add", "-A")
    _git(newer, "-c", "user.name=n", "-c", "user.email=n@example.com", "commit", "-q", "-m", "format 99")
    _git(newer, "push", "-q", "origin", "main")
    tip = _git(hub, "rev-parse", "main")

    win.write("bots/app/briefings/sessions/s9.md", b"# nine\n")
    before = _snapshot(win.vault)
    for op in (win.pull, win.push):
        out = op()
        assert out.outcome == "format" and not out.ok
        assert "99" in out.message
    assert _snapshot(win.vault) == before
    assert _git(hub, "rev-parse", "main") == tip
    status = win.status()
    assert status["hubFormatVersion"] == 99
    assert status["formatSupported"] is False


# --- two runs at once ------------------------------------------------------


@pytest.mark.parametrize("op", ["pull", "push"])
def test_a_second_run_while_the_first_holds_the_lock_is_busy(pair, op: str):
    _mac, win = pair
    win.write("bots/app/briefings/sessions/s4.md", b"# four\n")
    before = _snapshot(win.vault)
    index = (win.vault / ".git" / "index").read_bytes()
    with locks.try_lock(sync.lock_path(win.vault)) as held:
        assert held
        out = getattr(win, op)()
    assert out.outcome == "busy" and out.ok
    assert _snapshot(win.vault) == before
    assert (win.vault / ".git" / "index").read_bytes() == index


# --- status and the log ----------------------------------------------------


def test_status_and_the_local_log(mac: Box, win: Box, hub: Path):
    assert mac.init(hub).ok
    assert win.join(hub).ok
    win.write("bots/app/briefings/sessions/s5.md", b"# five\n")
    assert win.status()["pendingFiles"] == 1
    assert win.push().outcome == "ok"
    mac.write("shared/feedback/later.md", b"---\nname: later\n---\nlater\n")
    status = mac.status()
    assert status["enabled"] is True
    assert Path(status["remote"]) == hub
    assert (status["role"], status["machineId"], status["primary"]) == ("primary", "mac", "mac")
    assert status["pendingFiles"] == 1
    assert status["lastPush"] is not None and status["lastPushAgoSeconds"] >= 0
    assert status["lastError"] is None
    assert status["formatSupported"] is True
    assert mac.pull().outcome == "ok"
    assert mac.status()["lastPull"] is not None
    assert win.status()["behind"] == 0

    rows = [json.loads(line) for line in
            (mac.vault / ".mnemo" / "sync-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(r["op"], r["outcome"]) for r in rows] == [("init", "ok"), ("pull", "ok")]
    for row in rows:
        assert row["machine"] == "mac" and row["durationMs"] >= 0 and row["at"].endswith("Z")
    # The log and the state are local: neither reaches the hub.
    assert not any(name.startswith(".mnemo/sync-") for name in _hub_files(hub))


def test_status_counts_what_the_hub_has_that_this_machine_does_not(pair):
    mac, win = pair
    mac.write("shared/feedback/later.md", b"---\nname: later\n---\nlater\n")
    assert mac.push().outcome == "ok"
    assert win.status()["behind"] == 0          # status never reaches the hub itself
    _git(win.vault, "fetch", "-q", "origin")
    assert win.status()["behind"] == 1
