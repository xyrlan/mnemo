"""``tools/measure_sync.py`` (#644) over synthetic logs, and once over a real pair.

Parity, push lag and conflicts each have their arithmetic pinned on rows
built here; the parity digest is shown unchanged when only the vault's
location changes and changed when a briefing does; and one briefing whose
push waited for a later SessionStart is counted as such. The last tests run
the collectors on a vault initialised against a bare repository under
``tmp_path``: no network, no real vault, no real git config.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from mnemo.core import sync
from mnemo.core.log_utils import iter_rotated_rows

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_sync.py"
_spec = importlib.util.spec_from_file_location("measure_sync", _TOOL)
ms = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ms)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

PAGE = (
    "---\nname: Use the staging hub\ndescription: which hub the deploy script reads\n"
    "type: feedback\nsources:\n  - bots/app/memory/hub.md\ntags:\n  - deploy\n---\n"
    "Deploys read the staging hub first.\n"
)


def _briefing_text(sid: str, body: str) -> str:
    return (f"---\ntype: briefing\nagent: app\nsession_id: {sid}\ndate: 2026-10-13\n"
            f"duration_minutes: 5\ncorrections: 0\n---\n\n# Briefing — app — {sid}\n\n{body}\n")


# --- push lag over synthetic rows ---------------------------------------------


def _row(lag, trigger):
    return {"path": "bots/app/briefings/sessions/x.md", "lagSeconds": lag, "trigger": trigger}


def test_push_lag_judges_the_awake_pushes_and_counts_the_ones_that_waited():
    rows = [_row(10, "session_end"), _row(30, "session_end"), _row(20, "cli"),
            _row(4000, "session_start")]
    report = ms.push_lag(rows)
    # Nearest-rank: p50 of four is the third, as judge_stats.percentile has it.
    assert report["all"] == {"n": 4, "p50": 30, "p95": 4000, "max": 4000}
    assert report["awake"] == {"n": 3, "p50": 20, "p95": 30, "max": 30}
    assert report["judged"] == report["awake"]
    assert report["waitedForSessionStart"] == 1
    assert report["triggerUnknown"] == 0
    assert report["withinBar"] is True
    # The same four with no triggers at all: unknown, taken as awake, and the
    # 4,000 s push fails the bar instead of being explained away.
    plain = ms.push_lag([_row(r["lagSeconds"], None) for r in rows])
    assert plain["triggerUnknown"] == 4 and plain["waitedForSessionStart"] == 0
    assert plain["judged"]["n"] == 4 and plain["withinBar"] is False
    assert "no trigger" in plain["judgedOver"]
    empty = ms.push_lag([])
    assert empty["judged"]["p95"] is None and empty["withinBar"] is False


def test_push_trigger_finds_the_push_row_by_the_commit_it_sent():
    log = [
        {"op": "push", "outcome": "ok", "at": "2026-10-13T10:00:00Z", "trigger": "session_end", "commit": "aaa"},
        {"op": "push", "outcome": "ok", "at": "2026-10-13T10:00:00Z", "trigger": "session_start", "commit": "bbb"},
        {"op": "push", "outcome": "refused", "at": "2026-10-13T10:00:01Z", "trigger": "cli", "commit": "ccc"},
        {"op": "init", "outcome": "ok", "at": "2026-10-13T09:00:00Z", "commit": "000"},
        {"op": "push", "outcome": "ok", "at": "2026-10-13T10:05:00Z", "commit": "ddd"},
    ]
    assert ms.push_trigger(log, "bbb") == "session_start"
    assert ms.push_trigger(log, "aaa") == "session_end"
    assert ms.push_trigger(log, "ccc") is None  # refused: not the row that sent it
    assert ms.push_trigger(log, "000") is None  # init is not a push row
    assert ms.push_trigger(log, "ddd") is None  # no trigger recorded
    assert ms.push_trigger(log, "eee") is None


# --- conflicts over synthetic rows --------------------------------------------


def test_conflicts_report_sums_the_log_and_counts_what_is_on_disk():
    log = [{"op": "pull", "outcome": "ok", "conflicts": 2}, {"op": "push", "outcome": "ok", "conflicts": 1},
           {"op": "push", "outcome": "refused"}, {"op": "pull", "outcome": "unreachable"}]
    state = {"conflictsResolved": 3, "losersKept": 2}
    clean = ms.conflicts(log, state, {"unmerged": 0, "marked": []})
    assert (clean["resolved"], clean["resolvedInLog"], clean["losersKept"], clean["unresolved"]) == (3, 3, 2, 0)
    dirty = ms.conflicts(log, state, {"unmerged": 1, "marked": ["shared/feedback/a.md"]})
    assert dirty["unresolved"] == 2 and dirty["marked"] == ["shared/feedback/a.md"]


def test_marker_scan_wants_all_three_lines_in_order():
    assert ms.has_markers(b"x\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> origin/main\n")
    assert not ms.has_markers(b"Title\n=======\n\nbody with >>>>>>> inside a line\n")
    assert not ms.has_markers(b">>>>>>> b\n=======\n<<<<<<< a\n")  # out of order
    assert not ms.has_markers(b"text <<<<<<< not at line start\n=======\n>>>>>>> x\n")


# --- parity -------------------------------------------------------------------


def test_normalise_replaces_the_vault_however_spelled_and_the_home(tmp_path):
    vault = tmp_path / "mnemo"
    text = (f"full text is at {vault}{os.sep}bots{os.sep}app{os.sep}briefings{os.sep}s.md] "
            f"and {vault.as_posix()}/x and {os.path.expanduser('~')}/y")
    out = ms.normalise(text, vault)
    assert out == "full text is at <vault>/bots/app/briefings/s.md] and <vault>/x and ~/y"
    assert ms.digest(out) == ms.digest(out) and ms.digest(out) != ms.digest(out + " ")


@pytest.fixture
def last_mode_config(tmp_path, monkeypatch) -> Path:
    config = tmp_path / "mnemo.config.json"
    config.write_text(json.dumps({"briefings": {"sessionStart": "last"}}), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config))
    return config


def _vault_for_parity(root: Path) -> Path:
    vault = root / "mnemo"
    page = vault / "shared" / "feedback" / "use-the-staging-hub.md"
    page.parent.mkdir(parents=True)
    page.write_text(PAGE, encoding="utf-8")
    briefing = vault / "bots" / "app" / "briefings" / "sessions" / "s1.md"
    briefing.parent.mkdir(parents=True)
    # Far past the envelope limit, so the block is trimmed and names its file:
    # the one place a machine path enters what SessionStart injects.
    briefing.write_text(_briefing_text("s1", "line of a long briefing\n" * 2500), encoding="utf-8")
    return vault


def test_parity_digest_is_unchanged_when_only_the_vault_moves(tmp_path, last_mode_config):
    here = _vault_for_parity(tmp_path / "here")
    raw = ms.injected_text(here, "app")
    assert raw["briefingMode"] == "last"
    # The trimmed block names its file, with the vault as the label, so the
    # cut does not move with the path's length.
    assert "deploy" in raw["text"] and str(here) not in raw["text"]
    assert "<vault>/bots/app/briefings/sessions/s1.md]" in raw["text"]
    first = ms.parity(here, "app")
    assert first["digest"].startswith("sha256:") and first["commit"] is None  # sync off: no commit

    elsewhere = tmp_path / "elsewhere" / "deeper"
    shutil.copytree(str(here), str(elsewhere / "mnemo"))
    moved = ms.parity(elsewhere / "mnemo", "app")
    assert moved["digest"] == first["digest"]
    assert moved["bytes"] == first["bytes"]

    # And a briefing that differs is a digest that differs.
    (elsewhere / "mnemo" / "bots" / "app" / "briefings" / "sessions" / "s1.md").write_text(
        _briefing_text("s1", "a different line\n" * 2500), encoding="utf-8")
    assert ms.parity(elsewhere / "mnemo", "app")["digest"] != first["digest"]


def test_parity_leaves_no_briefing_log_row(tmp_path, last_mode_config):
    vault = _vault_for_parity(tmp_path / "here")
    ms.parity(vault, "app")
    assert not (vault / ".mnemo" / "briefing-log.jsonl").exists()
    assert (vault / ".mnemo" / "rule-activation-index.json").exists()  # rebuilt, as SessionStart does


# --- on a real pair --------------------------------------------------------------


@pytest.fixture
def git_env(monkeypatch):
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        monkeypatch.delenv(name, raising=False)


def _primary(tmp_path: Path, monkeypatch) -> Path:
    hub = tmp_path / "hub.git"
    subprocess.run(["git", "init", "--bare", "-q", str(hub)], check=True)
    vault = _vault_for_parity(tmp_path / "mac")
    config = vault / "mnemo.config.json"
    config.write_text(json.dumps({"vaultRoot": str(vault), "sync": {"machineId": "mac"},
                                  "briefings": {"sessionStart": "last"}}), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(config))
    assert sync.init(vault, str(hub), machine="mac", config_path=config).ok
    return vault


def _write_briefing(vault: Path, sid: str, *, age_seconds: float) -> Path:
    path = vault / "bots" / "app" / "briefings" / "sessions" / f"{sid}.md"
    path.write_text(_briefing_text(sid, "short"), encoding="utf-8")
    then = time.time() - age_seconds
    os.utime(str(path), (then, then))
    return path


@needs_git
def test_push_lag_from_the_write_to_the_hub_and_the_push_that_waited(tmp_path, monkeypatch, git_env):
    vault = _primary(tmp_path, monkeypatch)
    config = vault / "mnemo.config.json"
    # The worker pushed this one 100 s after it was written...
    _write_briefing(vault, "s2", age_seconds=100)
    assert sync.push(vault, machine="mac", config_path=config, trigger="session_end").outcome == "ok"
    # ...and this one waited for the next SessionStart, 1,000 s later.
    _write_briefing(vault, "s3", age_seconds=1000)
    assert sync.push(vault, machine="mac", config_path=config, trigger="session_start").outcome == "ok"

    rows = list(iter_rotated_rows(vault / ".mnemo" / "sync-log.jsonl"))
    pushes = [r for r in rows if r["op"] == "push"]
    assert [r["trigger"] for r in pushes] == ["session_end", "session_start"]
    assert all(r["files"] == 1 and r["conflicts"] == 0 and len(r["commit"]) == 40 for r in pushes)

    commits = ms.briefing_commits(vault)
    assert [c["paths"] for c in commits] == [["bots/app/briefings/sessions/s3.md"],
                                             ["bots/app/briefings/sessions/s2.md"],
                                             ["bots/app/briefings/sessions/s1.md"]]
    assert {c["machine"] for c in commits} == {"mac"}

    report = ms.measure(str(vault), str(tmp_path / "projects"), project="app", machine="mac")
    lag = report["pushLag"]
    by_path = {r["path"]: r for r in report["pushLagRows"]}
    assert abs(by_path["bots/app/briefings/sessions/s2.md"]["lagSeconds"] - 100) < 5
    assert abs(by_path["bots/app/briefings/sessions/s3.md"]["lagSeconds"] - 1000) < 5
    # s1 went out with `init`, whose row is not a push row: trigger unknown.
    assert lag["waitedForSessionStart"] == 1 and lag["triggerUnknown"] == 1
    assert lag["awake"]["n"] == 1 and lag["all"]["n"] == 3
    assert abs(lag["judged"]["p95"] - 100) < 5 and lag["withinBar"] is True
    assert report["conflicts"]["unresolved"] == 0
    verdicts = {b["bar"].split(" ")[0]: b["verdict"] for b in report["bars"]}
    assert verdicts["push"] == "pass" and verdicts["0"] == "pass"
    assert verdicts["parity"].startswith("unknown") and verdicts["SessionStart"] == "unknown"
    assert report["parity"]["commit"] == commits[0]["commit"] and report["parity"]["pending"] == 0


@needs_git
def test_a_file_with_markers_is_an_unresolved_conflict_and_fails_the_bar(tmp_path, monkeypatch, git_env):
    vault = _primary(tmp_path, monkeypatch)
    (vault / "shared" / "feedback" / "torn.md").write_bytes(
        b"---\nname: torn\n---\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> origin/main\n")
    on_disk = ms.unresolved_on_disk(vault)
    assert on_disk == {"unmerged": 0, "marked": ["shared/feedback/torn.md"]}
    report = ms.measure(str(vault), str(tmp_path / "projects"), project="app", machine="mac")
    assert report["conflicts"]["unresolved"] == 1
    assert [b["verdict"] for b in report["bars"] if b["bar"].startswith("0 conflicts")] == ["fail"]


@needs_git
def test_main_prints_a_report_with_provenance_and_the_other_digest_judges_parity(
        tmp_path, monkeypatch, git_env, capsys):
    vault = _primary(tmp_path, monkeypatch)
    projects = tmp_path / "projects"
    projects.mkdir()
    argv = ["--vault", str(vault), "--projects", str(projects), "--project", "app"]
    assert ms.main(argv + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["provenance"]["tool"] == "measure_sync"
    assert data["parity"]["digest"].startswith("sha256:")
    assert len(data["bars"]) == 4

    assert ms.main(argv + ["--other-digest", data["parity"]["digest"]]) == 0
    text = capsys.readouterr().out
    assert "PASS     parity identical" in text
    assert text.rstrip().splitlines()[-1].startswith("provenance: {")
    assert ms.main(argv + ["--other-digest", "sha256:0000"]) == 0
    assert "FAIL     parity identical" in capsys.readouterr().out
