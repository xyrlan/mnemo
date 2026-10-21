"""Roles and hooks with sync on (#643): a satellite writes only its briefings,
SessionStart pulls before it injects, the workers push after they write.

Two temp vaults and a bare repository as the hub, as in
``test_sync_two_vaults.py``; the detached workers run inline. No network, no
real vault, no real git config.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from mnemo.core import llm as llm_mod
from mnemo.core import sync
from mnemo.core.sync import engine, roles
from mnemo.core.sync import hooks as sync_hooks

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

PAGE = (
    "---\nname: Use the staging hub\ndescription: which hub the deploy script reads\n"
    "type: feedback\nsources:\n  - bots/app/memory/hub.md\ntags:\n  - deploy\n---\n"
    "Deploys read the staging hub first.\n"
)
REFERENCE_PAGES = 20
BRIEFING_BODY = "## TL;DR\nWired the retry helper.\n\n## What I did\n- edited retry.py\n"


@pytest.fixture(autouse=True)
def _git_reads_no_real_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME",
                 "GIT_COMMITTER_EMAIL", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _fake_llm(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Every briefing and extraction call is answered locally."""
    calls: dict = {"n": 0}

    def fake_call(prompt, *, system, model, timeout):
        calls["n"] += 1
        return llm_mod.LLMResponse(text=BRIEFING_BODY, total_cost_usd=0.0, input_tokens=1,
                                   output_tokens=1, api_key_source="none", raw={})

    monkeypatch.setattr(llm_mod, "call", fake_call)
    return calls


class Box:
    """One machine: a vault, a machine id and its own config file."""

    def __init__(self, root: Path, machine: str) -> None:
        self.root = root
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

    def write(self, rel: str, data: bytes) -> Path:
        path = self.vault.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read(self, rel: str) -> bytes:
        return self.vault.joinpath(*rel.split("/")).read_bytes()

    def has(self, rel: str) -> bool:
        return self.vault.joinpath(*rel.split("/")).is_file()

    def cfg(self) -> dict:
        from mnemo.core import config as cfg_mod

        return cfg_mod.load_config(self.config)

    def set_config(self, **sections) -> None:
        raw = json.loads(self.config.read_text(encoding="utf-8"))
        for key, value in sections.items():
            raw[key] = value
        self.config.write_text(json.dumps(raw), encoding="utf-8")

    def tracked(self) -> List[str]:
        from mnemo.core.sync import tracked

        return sorted(tracked.walk(self.vault))


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=str(cwd), check=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out.stdout.decode("utf-8")


def _hub_files(hub: Path) -> List[str]:
    return sorted(_git(hub, "ls-tree", "-r", "--name-only", "main").splitlines())


def _hub_blob(hub: Path, rel: str) -> bytes:
    out = subprocess.run(["git", "cat-file", "-p", f"main:{rel}"], cwd=str(hub), check=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return out.stdout


def _bare(path: Path) -> Path:
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    return path


HOOK_CONFIG = {
    "injection": {"enabled": True},
    "briefings": {"enabled": True, "injectLastOnSessionStart": True},
    "reflex": {"enabled": True, "judge": {"provider": "none"}},
    "extraction": {"auto": {"enabled": True, "minNewMemories": 1, "minIntervalMinutes": 60}},
    "install": {"autoRepairHooks": False},
    "backfill": {"enabled": True, "autoOnFirstSession": True},
}


def _primary(root: Path) -> Box:
    box = Box(root, "mac")
    box.write("shared/feedback/use-the-staging-hub.md", PAGE.encode("utf-8"))
    for i in range(REFERENCE_PAGES):
        box.write(f"shared/reference/page-{i:02d}.md",
                  f"---\nname: page {i}\ntype: reference\n---\nreference page {i}\n".encode("utf-8"))
    box.write("shared/_inbox/feedback/staged.md", b"---\nname: staged\n---\nstaged\n")
    box.write("bots/app/briefings/sessions/s1.md", b"---\ntype: briefing\nmachine: mac\n---\n# one\n")
    box.write("bots/app/memory/MEMORY.md", b"- a note\n")
    box.write(".mnemo/extraction-state.json", b'{"schema_version": 2, "entries": {}}\n')
    box.write("HOME.md", b"# home\n")
    box.config.write_text(json.dumps({"vaultRoot": str(box.vault), **HOOK_CONFIG}),
                          encoding="utf-8")
    return box


_JOINED: Dict[str, Path] = {}


@pytest.fixture
def pair(tmp_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Tuple[Box, Box, Path]:
    """A primary ``mac`` and a satellite ``win`` in sync through ``hub.git``,
    built once and copied per test."""
    if "root" not in _JOINED:
        root = tmp_path_factory.mktemp("sync-hooks-joined")
        _bare(root / "hub.git")
        assert _primary(root).init("../../hub.git").ok
        assert Box(root, "win").join("../../hub.git").ok
        _JOINED["root"] = root
    for name in ("hub.git", "mac", "win"):
        shutil.copytree(str(_JOINED["root"] / name), str(tmp_path / name))
    mac, win = Box(tmp_path, "mac"), Box(tmp_path, "win")
    for box in (mac, win):
        raw = json.loads(box.config.read_text(encoding="utf-8"))
        raw["vaultRoot"] = str(box.vault)
        raw.update(HOOK_CONFIG)
        box.config.write_text(json.dumps(raw), encoding="utf-8")
    return mac, win, tmp_path / "hub.git"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A git repository named ``app``: the agent both vaults already know."""
    repo = tmp_path / "app"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _use(monkeypatch: pytest.MonkeyPatch, box: Box) -> None:
    """Every hook and worker in this test reads this machine's config."""
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(box.config))


def _inline_pull(monkeypatch: pytest.MonkeyPatch, box: Box) -> List[sync.Outcome]:
    """Run SessionStart's pull worker inline instead of as a process."""
    seen: List[sync.Outcome] = []

    def fake_worker(vault, cwd):
        seen.append(sync_hooks.run_hook_pull(box.cfg(), vault))
        return None

    monkeypatch.setattr(sync_hooks, "_start_pull_worker", fake_worker)
    return seen


def _inline_briefing(monkeypatch: pytest.MonkeyPatch) -> List[Tuple[Path, str]]:
    """Run SessionEnd's briefing worker inline: the real ``mnemo briefing``."""
    from mnemo.cli.commands.briefing import cmd_briefing
    from mnemo.hooks import session_end

    seen: List[Tuple[Path, str]] = []

    def fake_spawn(jsonl_path, agent):
        seen.append((Path(jsonl_path), agent))
        args = argparse.Namespace(jsonl_path=str(jsonl_path), agent=agent, prune=False,
                                  dry_run=False)
        assert cmd_briefing(args) == 0

    monkeypatch.setattr(session_end, "_spawn_detached_briefing", fake_spawn)
    return seen


def _transcript(home: Path, cwd: Path, sid: str) -> Path:
    from mnemo.core.backfill.discover import encode_cwd

    path = home / ".claude" / "projects" / encode_cwd(str(cwd)) / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    events = [
        {"type": "user", "timestamp": "2026-10-13T10:00:00.000Z",
         "message": {"role": "user", "content": "add a retry helper"}},
        {"type": "assistant", "timestamp": "2026-10-13T10:05:00.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Write",
              "input": {"file_path": "retry.py", "content": "# ..."}}]}},
    ]
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return path


def _run(hook, payload: dict, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert hook.main() == 0
    return out.getvalue()


def _run_all_hooks(monkeypatch, box: Box, project: Path, sid: str, home: Path) -> Dict[str, str]:
    from mnemo.hooks import pre_tool_use, session_end, session_start, user_prompt_submit

    _transcript(home, project, sid)
    cwd = str(project)
    return {
        "session_start": _run(session_start, {"session_id": sid, "cwd": cwd, "source": "startup"},
                              monkeypatch),
        "user_prompt_submit": _run(user_prompt_submit, {"session_id": sid, "cwd": cwd,
                                                        "prompt": "deploy to the staging hub"},
                                   monkeypatch),
        "pre_tool_use": _run(pre_tool_use, {"session_id": sid, "cwd": cwd, "tool_name": "Read",
                                            "tool_input": {"file_path": str(project / "x.py")}},
                             monkeypatch),
        "session_end": _run(session_end, {"session_id": sid, "cwd": cwd, "reason": "exit"},
                            monkeypatch),
    }


def _errors(vault: Path) -> List[dict]:
    path = vault / ".errors.log"
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


# --- item 1: sync off changes nothing --------------------------------------


def _snapshot(vault: Path) -> Dict[Path, Optional[bytes]]:
    """Every file in the vault; contents for those not telemetry or a day log."""
    out: Dict[Path, Optional[bytes]] = {}
    for path in sorted(vault.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(vault)
        stable = (rel.parts[0] != ".mnemo" and "logs" not in rel.parts
                  and rel.name not in (".errors.log", "mnemo.config.json"))  # the config names its own vault
        out[rel] = path.read_bytes() if stable else None
    return out


def _plain_vault(root: Path, name: str) -> Box:
    box = _primary(root / name)
    return box


def test_sync_off_hooks_output_and_files_are_what_they_were(tmp_path, tmp_home, monkeypatch):
    """Both hooks run twice on identical vaults: once with every #643 entry
    point replaced by a no-op (the code before this change), once as shipped.
    The envelopes and the files written are the same, and the briefing
    carries no ``machine:`` line."""
    _inline_briefing(monkeypatch)
    monkeypatch.setattr("mnemo.hooks.session_end._spawn_detached_extraction", lambda: None)
    results = []
    for name, unchanged in (("before", True), ("after", False)):
        box = _plain_vault(tmp_path, name)
        project = tmp_path / name / "app"  # its own transcripts: the runs must not see each other's
        project.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        _use(monkeypatch, box)
        with monkeypatch.context() as m:
            if unchanged:
                m.setattr(sync_hooks, "pull_at_session_start", lambda *a, **k: "off")
                m.setattr(sync_hooks, "push_after_write", lambda *a, **k: None)
                m.setattr(roles, "is_satellite", lambda *a, **k: False)
                m.setattr(roles, "briefing_machine", lambda *a, **k: "")
            outputs = _run_all_hooks(m, box, project, "sid-same", tmp_home)
        results.append((outputs, _snapshot(box.vault)))
        # The first-run notice counts transcripts under the home; each run
        # must see only its own.
        shutil.rmtree(str(tmp_home / ".claude" / "projects"))
    (out_before, files_before), (out_after, files_after) = results
    import difflib
    for key in out_before:
        diff = "\n".join(difflib.unified_diff(out_before[key].replace("\\n", "\n").splitlines(),
                                              out_after[key].replace("\\n", "\n").splitlines(), lineterm=""))
        assert out_after[key] == out_before[key], f"{key}:\n{diff}"
    assert sorted(files_after) == sorted(files_before)
    assert files_after == files_before
    briefing = box.vault / "bots" / "app" / "briefings" / "sessions" / "sid-same.md"
    assert briefing.is_file()
    assert "machine:" not in briefing.read_text(encoding="utf-8")
    assert not (box.vault / ".git").exists()


def test_sync_off_every_role_answer_is_off(tmp_path):
    box = _primary(tmp_path)
    assert roles.role(box.vault) == roles.OFF
    assert roles.is_satellite(box.vault) is False
    assert roles.is_primary(box.vault) is False
    assert roles.briefing_machine(box.cfg(), box.vault) == ""
    assert sync_hooks.pull_at_session_start(box.cfg(), box.vault, cwd=str(tmp_path)) == sync_hooks.OFF
    assert sync_hooks.push_after_write(box.cfg(), box.vault, where="x") is None


# --- item 2: a satellite's automatic paths write only briefings -------------


def test_satellite_four_hooks_add_one_briefing_and_nothing_else(pair, project, tmp_home, monkeypatch):
    mac, win, hub = pair
    _use(monkeypatch, win)
    _inline_pull(monkeypatch, win)
    _inline_briefing(monkeypatch)
    extractions: list = []
    monkeypatch.setattr("mnemo.hooks.session_end._spawn_detached_extraction",
                        lambda: extractions.append(1))
    autopilot: list = []
    monkeypatch.setattr("mnemo.autopilot.core.scheduler.run_due_jobs",
                        lambda **kw: autopilot.append(kw))
    hub_before, tracked_before = _hub_files(hub), win.tracked()
    assert roles.role(win.vault, win.cfg()) == roles.SATELLITE

    outputs = _run_all_hooks(monkeypatch, win, project, "sat-session", tmp_home)

    briefing = "bots/app/briefings/sessions/sat-session.md"
    assert sorted(set(win.tracked()) - set(tracked_before)) == [briefing]
    assert set(tracked_before) <= set(win.tracked())
    assert sorted(set(_hub_files(hub)) - set(hub_before)) == [briefing]
    for rel in tracked_before:
        assert _hub_blob(hub, rel) == win.read(rel), rel
    assert "mnemo://v1" in outputs["session_start"]
    assert extractions == [] and autopilot == []
    assert b"machine: win\n" in win.read(briefing)
    assert _errors(win.vault) == []


def test_satellite_hooks_do_not_mirror_or_prune_or_propose(pair, project, tmp_home, monkeypatch):
    mac, win, hub = pair
    _use(monkeypatch, win)
    _inline_pull(monkeypatch, win)
    calls: Dict[str, int] = {}

    def count(name):
        def _f(*a, **k):
            calls[name] = calls.get(name, 0) + 1
        return _f

    monkeypatch.setattr("mnemo.core.mirror.mirror_all", count("mirror"))
    monkeypatch.setattr("mnemo.hooks.session_start._maybe_prune_briefings", count("prune"))
    monkeypatch.setattr("mnemo.hooks.session_start._maybe_schedule_install_backfill",
                        count("backfill"))
    monkeypatch.setattr("mnemo.hooks.session_end._maybe_schedule_propose", count("propose"))
    monkeypatch.setattr("mnemo.hooks.session_end._maybe_schedule_extraction", count("extract"))
    monkeypatch.setattr("mnemo.core.migrations.slugs.stamp_slugs", count("slugs"))
    _run_all_hooks(monkeypatch, win, project, "sat-2", tmp_home)
    assert calls == {}

    # The same hooks on the primary call every one of them.
    _use(monkeypatch, mac)
    _inline_pull(monkeypatch, mac)
    _run_all_hooks(monkeypatch, mac, project, "pri-2", tmp_home)
    assert set(calls) == {"mirror", "prune", "backfill", "propose", "extract", "slugs"}


def _tracked_unchanged(box: Box, before: Dict[str, bytes]) -> None:
    after = {rel: box.read(rel) for rel in box.tracked()}
    assert after == before


def _tracked_now(box: Box) -> Dict[str, bytes]:
    return {rel: box.read(rel) for rel in box.tracked()}


def test_writer_mirror_writes_nothing_on_a_satellite_and_writes_on_the_primary(pair, tmp_home, monkeypatch):
    from mnemo.core import mirror

    mac, win, _hub = pair
    memory = tmp_home / ".claude" / "projects" / "-Users-you-app" / "memory"
    memory.mkdir(parents=True)
    # Bytes, not text: `write_text` would put "\r\n" on disk on Windows, and
    # the mirror copies what is on disk.
    (memory / "MEMORY.md").write_bytes(b"- mirrored\n")
    monkeypatch.setattr(mirror, "_agent_from_project_dir", lambda name: "app")

    before = _tracked_now(win)
    mirror.mirror_all(win.cfg())
    _tracked_unchanged(win, before)

    mirror.mirror_all(mac.cfg())
    assert mac.read("bots/app/memory/MEMORY.md") == b"- mirrored\n"


def test_writer_prune_deletes_nothing_on_a_satellite_and_deletes_on_the_primary(pair):
    from mnemo.core import briefing as briefing_mod

    mac, win, _hub = pair
    old = time.time() - 400 * 86400
    for box in (mac, win):
        for i in range(3):
            path = box.write(f"bots/app/briefings/sessions/old-{i}.md", b"# old\n")
            os.utime(str(path), (old, old))
    cfg = {"briefings": {"retentionDays": 30, "keepPerAgent": 1}}
    rep = briefing_mod.prune(win.vault, cfg)
    assert len(rep.deleted) == 3 and all(p.is_file() for p in rep.deleted)
    rep = briefing_mod.prune(mac.vault, cfg)
    assert len(rep.deleted) == 3 and not any(p.is_file() for p in rep.deleted)


def test_writer_extraction_is_refused_on_a_satellite_and_runs_on_the_primary(pair):
    from mnemo.core import extract as extract_mod

    mac, win, _hub = pair
    before = _tracked_now(win)
    with pytest.raises(extract_mod.SatelliteRefused):
        extract_mod.run_extraction(win.cfg())
    _tracked_unchanged(win, before)
    assert not (win.vault / ".mnemo" / "extract.lock").exists()

    summary = extract_mod.run_extraction(mac.cfg())
    state = json.loads(mac.read(".mnemo/extraction-state.json"))
    assert state.get("last_run") and summary.mode == "manual"


def test_writer_extraction_background_logs_the_refusal_under_its_own_name(pair, monkeypatch):
    from mnemo.cli.commands import extract as extract_cmd

    mac, win, _hub = pair
    _use(monkeypatch, win)
    args = argparse.Namespace(background=True, dry_run=False, force=False)
    assert extract_cmd.cmd_extract(args) == 2
    assert [e["where"] for e in _errors(win.vault)] == ["extract.bg.satellite"]


def test_writer_inbox_apply_and_expiry_write_nothing_on_a_satellite(pair, monkeypatch):
    from mnemo.core import inbox as inbox_queue
    from mnemo.core.extract import inbox
    from mnemo.core.extract.inbox.types import ExtractedPage

    mac, win, _hub = pair
    page = ExtractedPage(slug="new-rule", type="feedback", name="New rule", description="d",
                         body="body", source_files=["bots/app/memory/MEMORY.md"],
                         source_hash="sha256:0")
    state_path = win.vault / ".mnemo" / "extraction-state.json"
    for box in (win, mac):
        state = inbox.load_state(box.vault / ".mnemo" / "extraction-state.json")
        result = inbox.apply_pages([page], state, box.vault, run_id="r1")
        if box is win:
            assert result.written_fresh == [] and not win.has("shared/_inbox/feedback/new-rule.md")
        else:
            assert result.written_fresh or result.auto_promoted
    assert state_path.is_file()

    old = time.time() - 60 * 86400
    for box in (win, mac):
        path = box.write("shared/_inbox/reference/held.md",
                         b"---\nname: held\ntype: reference\nreference_gate:\n  verdict: held\n---\nx\n")
        os.utime(str(path), (old, old))
    assert inbox_queue.expire_held(win.vault, days=14) == []
    assert win.has("shared/_inbox/reference/held.md")


def test_writer_migrations_rewrite_nothing_on_a_satellite(pair):
    from mnemo.core.migrations import proposed, slugs

    mac, win, _hub = pair
    for box in (win, mac):
        box.write("shared/feedback/legacy.md", b"---\nname: legacy\ntype: feedback\n---\nx\n")
        box.write("shared/feedback/legacy.proposed.md", b"---\nname: legacy\n---\nproposal\n")
    before = _tracked_now(win)
    rep = slugs.stamp_slugs(win.vault)
    assert rep.stamped >= 1  # counted, as a dry run counts
    rep2 = proposed.relocate_proposed(win.vault)
    assert rep2.moved == 1
    _tracked_unchanged(win, before)

    slugs.stamp_slugs(mac.vault)
    proposed.relocate_proposed(mac.vault)
    assert b"slug: legacy" in mac.read("shared/feedback/legacy.md")
    assert mac.has("shared/_inbox/feedback/legacy.proposed.md")


def test_writer_install_backfill_never_runs_on_a_satellite(pair, project, monkeypatch):
    from mnemo.cli.commands import backfill as backfill_cmd
    from mnemo.core.backfill import ledger
    from mnemo.hooks import session_start

    mac, win, _hub = pair
    ran: list = []
    monkeypatch.setattr(backfill_cmd, "_run_backfill", lambda args: ran.append(args) or 0)
    _use(monkeypatch, win)
    assert backfill_cmd.cmd_backfill(argparse.Namespace(install_run=True)) == 2
    assert ran == [] and ledger.load(win.vault).get("installRunDone") is not True

    spawned: list = []
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda **kw: spawned.append(kw))
    with monkeypatch.context() as m:
        m.setattr(roles, "is_satellite", lambda *a, **k: False)
        session_start._maybe_schedule_install_backfill(mac.cfg(), mac.vault, cwd=str(project))
    assert len(spawned) == 1


def test_writer_dashboard_leaves_home_md_alone_on_a_satellite(pair):
    from mnemo.core import dashboard

    mac, win, _hub = pair
    dashboard.update_home_md(win.cfg())
    assert win.read("HOME.md") == b"# home\n"
    dashboard.update_home_md(mac.cfg())
    assert mac.read("HOME.md") != b"# home\n"


def test_writer_auto_retire_stands_down_on_a_satellite(pair, monkeypatch):
    from mnemo.core.friction import retire

    mac, win, _hub = pair
    cfg = {"friction": {"autoRetire": True}}
    applied: list = []
    monkeypatch.setattr(retire, "_plan", lambda vault, records: [])
    monkeypatch.setattr(retire, "apply_retirements", lambda vault, plans: applied.append(vault) or retire.RetireRun())
    run = retire.auto_retire(win.vault, [], cfg=cfg)
    assert "satellite" in (run.skipped or "") and applied == []
    retire.auto_retire(mac.vault, [], cfg=cfg)
    assert applied == [mac.vault]


def test_a_typed_change_to_a_live_page_on_a_satellite_still_works_and_travels(pair):
    mac, win, hub = pair
    changed = PAGE.replace("staging hub first", "production hub last").encode("utf-8")
    win.write("shared/feedback/use-the-staging-hub.md", changed)
    out = win.push()
    assert out.outcome == "ok", out.message
    assert _hub_blob(hub, "shared/feedback/use-the-staging-hub.md") == changed
    assert mac.pull().ok
    assert mac.read("shared/feedback/use-the-staging-hub.md") == changed


# --- item 3: a briefing says where it was written --------------------------


def test_briefing_frontmatter_carries_the_machine_id_once_sync_is_on(pair, tmp_home, project):
    from mnemo.core import briefing as briefing_mod
    from mnemo.core.extract.scanner import parse_frontmatter

    mac, win, _hub = pair
    for box in (win, mac):
        jsonl = _transcript(tmp_home, project, f"stamp-{box.machine}")
        path = briefing_mod.generate_session_briefing(jsonl, "app", box.cfg())
        fm, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
        assert fm["machine"] == box.machine
        assert roles.machine_in_briefing(path.read_bytes()) == box.machine


# --- item 4: SessionStart pulls before it builds what it injects -----------


class _Worker:
    def __init__(self, finish: Optional[float] = None, body=None) -> None:
        self.finish = finish
        self.body = body
        self.waited: List[float] = []

    def wait(self, timeout=None):
        self.waited.append(timeout)
        if self.body is not None:
            self.body()
        if self.finish is not None and self.finish > (timeout or 0):
            raise subprocess.TimeoutExpired("mnemo", timeout)
        return 0


def test_session_start_injects_what_the_pull_just_brought(pair, project, tmp_home, monkeypatch):
    """The primary pushes a briefing and a rule; the satellite's next session
    start pulls both and the envelope it injects lists the briefing."""
    from mnemo.hooks import session_start

    mac, win, hub = pair
    mac.write("bots/app/briefings/sessions/fresh.md",
              b"---\ntype: briefing\nagent: app\nsession_id: fresh\ndate: 2026-10-13\n"
              b"machine: mac\n---\n# Briefing\n\n## TL;DR\nThe fresh briefing from the mac.\n")
    mac.write("shared/feedback/new-rule.md",
              b"---\nname: new rule\ndescription: a rule the mac learned\ntype: feedback\n"
              b"tags:\n  - deploy\n---\nAlways deploy on Tuesdays.\n")
    assert mac.push().ok
    _use(monkeypatch, win)
    seen = _inline_pull(monkeypatch, win)
    out = _run(session_start, {"session_id": "s-pull", "cwd": str(project), "source": "startup"},
               monkeypatch)
    assert len(seen) == 1 and seen[0].outcome == "ok" and seen[0].detail["files"] == 2
    assert "The fresh briefing from the mac" in out
    assert win.has("shared/feedback/new-rule.md")


def test_pull_waits_at_most_the_budget_and_lets_a_slow_pull_finish_on_its_own(pair, monkeypatch):
    mac, win, _hub = pair
    worker = _Worker(finish=60.0)
    monkeypatch.setattr(sync_hooks, "_start_pull_worker", lambda vault, cwd: worker)
    assert sync_hooks.pull_at_session_start(win.cfg(), win.vault, cwd=str(win.root)) == sync_hooks.DETACHED
    # One wait, bounded by the budget: proven by what the worker was asked,
    # not by a wall clock that read 0.62 s of a 1.0 s bound under load (#750).
    assert worker.waited == [sync_hooks.PULL_BUDGET_SECONDS] == [3.0]
    quick = _Worker(finish=0.1)
    monkeypatch.setattr(sync_hooks, "_start_pull_worker", lambda vault, cwd: quick)
    (win.vault / sync_hooks.PULL_STAMP).unlink()
    assert sync_hooks.pull_at_session_start(win.cfg(), win.vault, cwd=str(win.root)) == sync_hooks.DONE


def test_pull_runs_at_most_once_per_five_minutes_per_vault(pair, monkeypatch):
    mac, win, _hub = pair
    starts: list = []
    monkeypatch.setattr(sync_hooks, "_start_pull_worker", lambda vault, cwd: starts.append(vault) or _Worker())
    cfg = win.cfg()
    # A resume storm: five session starts inside a second pull once.
    results = [sync_hooks.pull_at_session_start(cfg, win.vault, cwd=str(win.root)) for _ in range(5)]
    assert results == [sync_hooks.DONE] + [sync_hooks.RECENT] * 4
    assert len(starts) == 1
    stamp = win.vault / sync_hooks.PULL_STAMP
    old = time.time() - sync_hooks.PULL_INTERVAL_SECONDS - 1
    os.utime(str(stamp), (old, old))
    assert sync_hooks.pull_at_session_start(cfg, win.vault, cwd=str(win.root)) == sync_hooks.DONE
    assert len(starts) == 2
    # The primary's vault is throttled on its own.
    assert sync_hooks.pull_at_session_start(mac.cfg(), mac.vault, cwd=str(mac.root)) == sync_hooks.DONE


def test_a_dispatched_child_does_not_pull(pair, tmp_path, monkeypatch):
    mac, win, _hub = pair
    starts: list = []
    monkeypatch.setattr(sync_hooks, "_start_pull_worker", lambda vault, cwd: starts.append(vault) or _Worker())
    child = tmp_path / "app-wt-643"
    child.mkdir()
    assert sync_hooks.pull_at_session_start(win.cfg(), win.vault, cwd=str(child)) == sync_hooks.CHILD
    assert starts == []
    assert sync_hooks.pull_at_session_start(win.cfg(), win.vault, cwd=str(tmp_path)) == sync_hooks.DONE


def test_an_unreachable_hub_leaves_the_vault_as_it_is_and_logs_no_error(pair, monkeypatch):
    mac, win, hub = pair
    # Pointed at a hub that is not there. Removing the real one fails on
    # Windows: git's object files are read-only there.
    _git(win.vault, "remote", "set-url", "origin", str(hub.parent / "gone.git"))
    before = _tracked_now(win)
    out = sync_hooks.run_hook_pull(win.cfg(), win.vault)
    assert out.outcome == "unreachable"
    _tracked_unchanged(win, before)
    assert _errors(win.vault) == []
    assert sync_hooks.push_after_write(win.cfg(), win.vault, where="x").outcome == "unreachable"
    assert _errors(win.vault) == []


def test_the_worker_command_runs_the_pull(pair, monkeypatch):
    from mnemo.cli.commands.sync import cmd_sync

    mac, win, hub = pair
    mac.write("bots/app/briefings/sessions/from-mac.md", b"---\ntype: briefing\nmachine: mac\n---\n# m\n")
    assert mac.push().ok
    _use(monkeypatch, win)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: win.vault)
    assert cmd_sync(argparse.Namespace(sync_action="hook-pull")) == 0
    assert win.has("bots/app/briefings/sessions/from-mac.md")
    rows = [json.loads(ln) for ln in (win.vault / ".mnemo" / "sync-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["op"] == "pull" and rows[-1]["trigger"] == "session_start"


# --- item 5: a pull that changed shared/ rebuilds the reflex index ---------


def test_a_pull_that_changed_shared_rebuilds_the_reflex_index_in_the_worker(pair):
    from mnemo.core.reflex import index as reflex_index

    mac, win, _hub = pair
    reflex_index.write_index(win.vault, reflex_index.build_index(win.vault))
    assert "tuesday-rule" not in json.dumps(reflex_index.load_index(win.vault))
    mac.write("shared/feedback/tuesday-rule.md",
              b"---\nname: tuesday-rule\ndescription: deploy on tuesdays\ntype: feedback\n"
              b"tags:\n  - deploy\n---\nDeploy on Tuesdays.\n")
    assert mac.push().ok
    out = sync_hooks.run_hook_pull(win.cfg(), win.vault)
    assert out.detail["sharedChanged"] is True
    assert "tuesday-rule" in json.dumps(reflex_index.load_index(win.vault))


# --- item 6: the primary extracts what a pull brought ----------------------


def test_on_the_primary_a_pull_that_brought_briefings_schedules_extraction(pair, monkeypatch):
    mac, win, _hub = pair
    spawned: list = []
    monkeypatch.setattr("mnemo.hooks.session_end._spawn_detached_extraction", lambda: spawned.append(1))
    # Nothing new: nothing scheduled.
    out = sync_hooks.run_hook_pull(mac.cfg(), mac.vault)
    assert out.detail["briefings"] == 0 and spawned == []
    # A page alone is not a briefing.
    win.write("shared/feedback/typed.md", b"---\nname: typed\ntype: feedback\n---\nx\n")
    assert win.push().ok
    out = sync_hooks.run_hook_pull(mac.cfg(), mac.vault)
    assert out.detail["briefings"] == 0 and spawned == []
    # A briefing from the satellite: scheduled, once, behind the debounce.
    win.write("bots/app/briefings/sessions/from-win.md", b"---\ntype: briefing\nmachine: win\n---\n# w\n")
    assert win.push().ok
    out = sync_hooks.run_hook_pull(mac.cfg(), mac.vault)
    assert out.detail["briefings"] == 1 and spawned == [1]
    # The same debounce SessionEnd uses: a run a minute ago holds it back.
    win.write("bots/app/briefings/sessions/from-win-2.md", b"---\ntype: briefing\nmachine: win\n---\n# w2\n")
    assert win.push().ok
    from datetime import datetime

    mac.write(".mnemo/extraction-state.json", json.dumps(
        {"schema_version": 2, "last_run": datetime.now().isoformat(timespec="seconds"),
         "entries": {}}).encode("utf-8"))
    out = sync_hooks.run_hook_pull(mac.cfg(), mac.vault)
    assert out.detail["briefings"] == 1 and spawned == [1]
    # And the lock SessionEnd checks.
    (mac.vault / ".mnemo" / "extract.lock").mkdir()
    mac.write(".mnemo/extraction-state.json", b'{"schema_version": 2, "entries": {}}\n')
    win.write("bots/app/briefings/sessions/from-win-3.md", b"---\ntype: briefing\nmachine: win\n---\n# w3\n")
    assert win.push().ok
    assert sync_hooks.run_hook_pull(mac.cfg(), mac.vault).detail["briefings"] == 1
    assert spawned == [1]


def test_on_the_primary_a_push_whose_own_pull_brought_briefings_schedules_extraction(pair, monkeypatch):
    mac, win, _hub = pair
    spawned: list = []
    monkeypatch.setattr("mnemo.hooks.session_end._spawn_detached_extraction", lambda: spawned.append(1))
    win.write("bots/app/briefings/sessions/from-win.md", b"---\ntype: briefing\nmachine: win\n---\n# w\n")
    assert win.push().ok
    mac.write("shared/feedback/edited.md", b"---\nname: edited\ntype: feedback\n---\nx\n")
    out = sync_hooks.push_after_write(mac.cfg(), mac.vault, where="x", trigger="session_end")
    assert out.outcome == "ok" and out.detail["briefings"] == 1
    assert spawned == [1]


def test_on_a_satellite_a_pull_that_brought_briefings_schedules_nothing(pair, monkeypatch):
    mac, win, _hub = pair
    spawned: list = []
    monkeypatch.setattr("mnemo.hooks.session_end._spawn_detached_extraction", lambda: spawned.append(1))
    mac.write("bots/app/briefings/sessions/m2.md", b"---\ntype: briefing\nmachine: mac\n---\n# m2\n")
    assert mac.push().ok
    assert sync_hooks.run_hook_pull(win.cfg(), win.vault).detail["briefings"] == 1
    assert spawned == []


# --- item 7: push after writing --------------------------------------------


def test_the_briefing_worker_pushes_what_it_wrote(pair, project, tmp_home, monkeypatch):
    mac, win, hub = pair
    _use(monkeypatch, win)
    seen = _inline_briefing(monkeypatch)
    from mnemo.hooks import session_end

    jsonl = _transcript(tmp_home, project, "w-sid")
    session_end._spawn_detached_briefing(jsonl, "app")
    assert seen == [(jsonl, "app")]
    assert "bots/app/briefings/sessions/w-sid.md" in _hub_files(hub)
    assert mac.pull().ok and b"machine: win" in mac.read("bots/app/briefings/sessions/w-sid.md")
    rows = [json.loads(ln) for ln in (win.vault / ".mnemo" / "sync-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["op"] == "push" and rows[-1]["trigger"] == "session_end"


def test_the_extraction_pushes_when_it_finishes(pair):
    from mnemo.core import extract as extract_mod

    mac, win, hub = pair
    extract_mod.run_extraction(mac.cfg())
    state = json.loads(_hub_blob(hub, ".mnemo/extraction-state.json"))
    assert state.get("last_run")
    assert state == json.loads(mac.read(".mnemo/extraction-state.json"))


def test_session_start_deferred_work_pushes_what_an_earlier_run_left(pair, monkeypatch):
    from mnemo.hooks import session_start

    mac, win, hub = pair
    _use(monkeypatch, win)
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda cfg: None)
    win.write("bots/app/briefings/sessions/left-behind.md",
              b"---\ntype: briefing\nmachine: win\n---\n# written while offline\n")
    assert "bots/app/briefings/sessions/left-behind.md" not in _hub_files(hub)
    assert session_start.run_deferred(win.cfg(), win.vault) == "done"
    assert "bots/app/briefings/sessions/left-behind.md" in _hub_files(hub)
    rows = [json.loads(ln) for ln in (win.vault / ".mnemo" / "sync-log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["op"] == "push" and rows[-1]["trigger"] == "session_start"


# --- item 8: a sync failure never fails a hook -----------------------------


def test_a_failing_pull_is_logged_under_the_hook_name_and_the_hook_still_injects(pair, project, monkeypatch):
    from mnemo.hooks import session_start

    mac, win, _hub = pair
    _use(monkeypatch, win)

    def boom(vault, cwd):
        raise OSError("git went away")

    monkeypatch.setattr(sync_hooks, "_start_pull_worker", boom)
    pull = sync_hooks.pull_at_session_start
    outcomes = []
    monkeypatch.setattr(sync_hooks, "pull_at_session_start",
                        lambda *a, **kw: outcomes.append(pull(*a, **kw)) or outcomes[-1])
    out = _run(session_start, {"session_id": "s-fail", "cwd": str(project), "source": "startup"},
               monkeypatch)
    # The failed pull took the ERROR return, which waits on nothing. Timing the
    # whole hook instead failed at 7.2 s under pytest-xdist load (#750).
    assert outcomes == [sync_hooks.ERROR]
    assert "mnemo://v1" in out
    assert [e["where"] for e in _errors(win.vault)] == ["session_start.sync_pull"]
    # The breaker treats it as any other session_start error: counted, not excluded.
    from mnemo.core import errors

    assert errors.recent_summary(win.vault)[1] == [("session_start.sync_pull", 1)]


def test_a_failing_push_never_fails_the_briefing_worker(pair, project, tmp_home, monkeypatch):
    from mnemo.cli.commands.briefing import cmd_briefing

    mac, win, _hub = pair
    _use(monkeypatch, win)
    monkeypatch.setattr(sync, "push", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no git")))
    jsonl = _transcript(tmp_home, project, "w-fail")
    assert cmd_briefing(argparse.Namespace(jsonl_path=str(jsonl), agent="app", prune=False,
                                           dry_run=False)) == 0
    assert win.has("bots/app/briefings/sessions/w-fail.md")
    assert [e["where"] for e in _errors(win.vault)] == ["briefing.sync_push"]


# --- item 9: MNEMO_HOOKS_OFF stops sync from hooks as well -----------------


def test_hooks_off_stops_the_pull_and_the_push(pair, monkeypatch):
    mac, win, hub = pair
    monkeypatch.setenv("MNEMO_HOOKS_OFF", "1")
    starts: list = []
    monkeypatch.setattr(sync_hooks, "_start_pull_worker", lambda vault, cwd: starts.append(1) or _Worker())
    assert sync_hooks.pull_at_session_start(win.cfg(), win.vault, cwd=str(win.root)) == sync_hooks.HOOKS_OFF
    assert starts == []
    win.write("bots/app/briefings/sessions/held.md", b"---\ntype: briefing\nmachine: win\n---\n# h\n")
    assert sync_hooks.push_after_write(win.cfg(), win.vault, where="x") is None
    assert sync_hooks.run_hook_pull(win.cfg(), win.vault) is None
    assert "bots/app/briefings/sessions/held.md" not in _hub_files(hub)


# --- item 10: a satellite cannot take anything away from the primary ------


def test_a_satellite_that_lost_a_directory_of_pages_takes_nothing_from_the_hub(pair):
    """The measured case of 2026-10-13: ``shared/reference`` removed on the
    satellite, one new briefing, a push. Before this change the push carried
    20 deletions and the primary's next pull removed every page."""
    mac, win, hub = pair
    pages = [f"shared/reference/page-{i:02d}.md" for i in range(REFERENCE_PAGES)]
    shutil.rmtree(str(win.vault / "shared" / "reference"))
    win.write("bots/app/briefings/sessions/after-loss.md", b"---\ntype: briefing\nmachine: win\n---\n# a\n")
    out = win.push()
    assert out.outcome == "ok", out.message
    assert out.message == "pushed 1 file"
    hub_files = _hub_files(hub)
    assert all(rel in hub_files for rel in pages)
    assert "bots/app/briefings/sessions/after-loss.md" in hub_files
    # The primary's pull brings the briefing and loses no page.
    pulled = mac.pull()
    assert pulled.ok and pulled.detail["files"] == 1
    assert all(mac.has(rel) for rel in pages)
    assert mac.has("bots/app/briefings/sessions/after-loss.md")
    # And the satellite has its pages back — the push's own pull already did it.
    assert all(win.has(rel) for rel in pages)
    assert out.detail["withheld"] == 0  # restored, not withheld: nothing to hold back


def test_a_satellite_with_its_pages_gone_gets_them_back_at_its_next_pull(pair):
    mac, win, hub = pair
    pages = [f"shared/reference/page-{i:02d}.md" for i in range(REFERENCE_PAGES)]
    shutil.rmtree(str(win.vault / "shared" / "reference"))
    win.write("HOME.md", b"# edited on the satellite\n")
    win.write(".mnemo/extraction-state.json", b'{"schema_version": 2, "entries": {"x": 1}}\n')
    win.write("bots/app/memory/MEMORY.md", b"- changed on the satellite\n")
    win.write("shared/_inbox/feedback/staged.md", b"---\nname: staged\n---\nchanged on the satellite\n")
    hub_before = _hub_files(hub)
    out = win.pull()
    assert out.ok and out.detail["restored"] == REFERENCE_PAGES + 4, out.message
    assert all(win.has(rel) for rel in pages)
    assert win.read("HOME.md") == b"# home\n"
    assert win.read(".mnemo/extraction-state.json") == mac.read(".mnemo/extraction-state.json")
    assert win.read("bots/app/memory/MEMORY.md") == b"- a note\n"
    assert win.read("shared/_inbox/feedback/staged.md") == b"---\nname: staged\n---\nstaged\n"
    assert _hub_files(hub) == hub_before  # a pull never touches the hub


def test_a_satellite_never_publishes_the_primary_only_files_or_a_deletion(pair):
    mac, win, hub = pair
    before = {rel: _hub_blob(hub, rel) for rel in _hub_files(hub)}
    win.write("HOME.md", b"# edited on the satellite\n")
    win.write(".mnemo/extraction-state.json", b'{"schema_version": 2, "entries": {"x": 1}}\n')
    win.write("bots/app/memory/MEMORY.md", b"- changed on the satellite\n")
    win.write("bots/app/memory/new-note.md", b"- added on the satellite\n")
    win.write("shared/_inbox/feedback/staged.md", b"---\nname: staged\n---\nchanged\n")
    win.write("shared/_inbox/feedback/added.md", b"---\nname: added\n---\nadded\n")
    win.write(".mnemo/sync/primary.json", b'{"machine": "win"}\n')
    (win.vault / "bots" / "app" / "briefings" / "sessions" / "s1.md").unlink()
    # A briefing another machine stamped, changed here.
    win.write("bots/app/briefings/sessions/theirs.md", b"---\ntype: briefing\nmachine: mac\n---\n# theirs\n")
    win.write("bots/app/briefings/sessions/mine.md", b"---\ntype: briefing\nmachine: win\n---\n# mine\n")
    out = win.push()
    assert out.outcome == "ok", out.message
    after = {rel: _hub_blob(hub, rel) for rel in _hub_files(hub)}
    assert set(after) - set(before) == {"bots/app/briefings/sessions/mine.md"}
    for rel, data in before.items():
        assert after[rel] == data, rel
    assert out.detail["withheld"] == 3  # new-note, added, theirs: additions in refused places
    # The primary is still the primary everywhere.
    assert roles.role(win.vault, win.cfg()) == roles.SATELLITE
    assert win.read(".mnemo/sync/primary.json") == mac.read(".mnemo/sync/primary.json")


def test_a_commit_made_as_a_satellite_that_deletes_pages_on_the_hub_changes_nothing_on_the_primary(pair, tmp_path):
    """An older or damaged satellite pushes a deletion straight to the hub."""
    mac, win, hub = pair
    pages = [f"shared/reference/page-{i:02d}.md" for i in range(REFERENCE_PAGES)]
    rogue = tmp_path / "rogue"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(hub), str(rogue)], check=True)
    _git(rogue, "rm", "-q", "-r", "shared/reference")
    (rogue / "HOME.md").write_text("# rewritten by a satellite\n", encoding="utf-8")
    (rogue / ".mnemo" / "extraction-state.json").write_text('{"entries": {"rogue": 1}}\n', encoding="utf-8")
    (rogue / "bots" / "app" / "briefings" / "sessions" / "rogue.md").write_text("# rogue briefing\n", encoding="utf-8")
    _git(rogue, "add", "-A")
    _git(rogue, "-c", "user.name=r", "-c", "user.email=r@example.com", "commit", "-q",
         "-m", "mnemo sync: win (23 files)\n\nSync-Machine: win")
    _git(rogue, "push", "-q", "origin", "main")
    assert not any(rel in _hub_files(hub) for rel in pages)

    out = mac.pull()
    assert out.ok, out.message
    assert out.detail["refused"] == REFERENCE_PAGES + 2
    assert out.detail["files"] == 1
    assert all(mac.has(rel) for rel in pages)
    assert mac.read("HOME.md") == b"# home\n"
    assert mac.read(".mnemo/extraction-state.json") == b'{"schema_version": 2, "entries": {}}\n'
    assert mac.has("bots/app/briefings/sessions/rogue.md")
    # The primary's next push puts the hub right.
    assert mac.push().outcome == "ok"
    hub_files = _hub_files(hub)
    assert all(rel in hub_files for rel in pages)
    assert _hub_blob(hub, "HOME.md") == b"# home\n"
    # A satellite that pulls now has every page again.
    assert win.pull().ok and all(win.has(rel) for rel in pages)


def test_a_satellite_still_takes_the_primary_deletions(pair):
    mac, win, _hub = pair
    (mac.vault / "shared" / "reference" / "page-00.md").unlink()
    assert mac.push().ok
    assert win.pull().ok
    assert not win.has("shared/reference/page-00.md")


# --- the pieces --------------------------------------------------------------


def test_satellite_may_publish_is_the_whole_rule():
    own = b"---\ntype: briefing\nmachine: win\n---\n# b\n"
    theirs = b"---\ntype: briefing\nmachine: mac\n---\n# b\n"
    bare = b"# a briefing with no stamp\n"
    assert roles.satellite_may_publish("bots/app/briefings/sessions/x.md", own, "win")
    assert roles.satellite_may_publish("bots/app/briefings/sessions/x.md", bare, "win")
    assert not roles.satellite_may_publish("bots/app/briefings/sessions/x.md", theirs, "win")
    assert not roles.satellite_may_publish("bots/app/briefings/sessions/x.md", None, "win")
    assert roles.satellite_may_publish("shared/feedback/x.md", b"x", "win")
    assert not roles.satellite_may_publish("shared/feedback/x.md", None, "win")
    for rel in ("shared/_inbox/feedback/x.md", "HOME.md", ".mnemo/extraction-state.json",
                "bots/app/memory/MEMORY.md", ".mnemo/sync/primary.json", ".mnemo/sync/format.json",
                "mnemo.config.json", "shared/_archive/x.md"):
        assert not roles.satellite_may_publish(rel, b"x", "win"), rel
    assert roles.primary_refuses("shared/feedback/x.md", deleted=True)
    assert not roles.primary_refuses("shared/feedback/x.md", deleted=False)
    assert not roles.primary_refuses("bots/app/briefings/sessions/x.md", deleted=False)
    assert roles.primary_refuses("shared/_inbox/feedback/x.md", deleted=False)
    assert roles.primary_refuses(".mnemo/sync/primary.json", deleted=False)


def test_machine_in_briefing_reads_only_the_frontmatter():
    assert roles.machine_in_briefing(b"---\na: 1\nmachine:  win \n---\nmachine: mac\n") == "win"
    assert roles.machine_in_briefing(b"# no frontmatter\nmachine: win\n") == ""
    assert roles.machine_in_briefing(b"") == ""


def test_the_role_survives_a_config_without_the_machine_id(pair, monkeypatch):
    """The id is recorded in the local state at init/join; a config that
    lost it, or a hostname that changed, does not change the role."""
    mac, win, _hub = pair
    for box in (mac, win):
        box.set_config(sync={})
    monkeypatch.setattr("socket.gethostname", lambda: "some-other-host")
    assert roles.role(mac.vault, mac.cfg()) == roles.PRIMARY
    assert roles.role(win.vault, win.cfg()) == roles.SATELLITE
    assert roles.machine_of(win.vault) == "win"


def test_spawn_waitable_can_be_waited_on_and_outlives_the_wait():
    from mnemo import _detach

    proc = _detach.spawn_waitable([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            proc.wait(timeout=0.2)
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_measure_tool_can_make_every_timed_start_pull(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "measure_hook_durations",
        Path(__file__).resolve().parents[2] / "tools" / "measure_hook_durations.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    sync_hooks.stamp_pull(tmp_path)
    assert (tmp_path / sync_hooks.PULL_STAMP).exists()
    tool.reset_sync_throttle(str(tmp_path))
    assert not (tmp_path / sync_hooks.PULL_STAMP).exists()
    assert sync_hooks.pull_is_due(tmp_path)


def _filetime_floats(ticks: int):
    """``(time.time(), st_mtime)`` as CPython 3.11 builds them on Windows
    from one ``GetSystemTimeAsFileTime`` value (pytime.c, posixmodule.c)."""
    ns = (ticks - 116444736000000000) * 100
    sec, nsec = divmod(ns, 10 ** 9)
    return ns / 1e9, sec + 1e-9 * nsec


def test_one_windows_tick_can_put_the_stamp_ahead_of_the_clock():
    # The mechanism behind #684, on any OS: the same tick read both ways.
    base = 116444736000000000 + 1_792_000_000 * 10 ** 7
    ahead = [m - t for t, m in (_filetime_floats(base + i * 156_250) for i in range(2000)) if m > t]
    assert ahead, "no tick rounds the mtime ahead: the #684 mechanism is gone"
    assert max(ahead) < sync_hooks.CLOCK_SLACK_SECONDS


def test_a_stamp_rounded_ahead_of_the_clock_is_not_due(tmp_path):
    sync_hooks.stamp_pull(tmp_path)
    mtime = (tmp_path / sync_hooks.PULL_STAMP).stat().st_mtime
    assert not sync_hooks.pull_is_due(tmp_path, now=mtime - 2.384185791015625e-07)
    assert not sync_hooks.pull_is_due(tmp_path, now=mtime - sync_hooks.CLOCK_SLACK_SECONDS)


def test_a_clock_set_back_makes_a_pull_due(tmp_path):
    sync_hooks.stamp_pull(tmp_path)
    mtime = (tmp_path / sync_hooks.PULL_STAMP).stat().st_mtime
    assert sync_hooks.pull_is_due(tmp_path, now=mtime - sync_hooks.CLOCK_SLACK_SECONDS - 1)
    assert sync_hooks.pull_is_due(tmp_path, now=mtime - 3600)
