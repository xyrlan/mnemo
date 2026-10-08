"""SessionStart leaves the work no injected block reads to a detached run (#610).

Claude Code holds a session's first prompt until SessionStart returns, and on
a 3,400-rule vault the reflex index rebuild was most of the hook's time. That
index is read by UserPromptSubmit only, and the memory mirror by nothing the
hook injects, so both moved into ``mnemo session-start-deferred``.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.core.reflex import index as reflex_index
from mnemo.hooks import session_start


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    (root / "HOME.md").write_text("# home\n", encoding="utf-8")
    rule = root / "shared" / "feedback" / "run-the-suite.md"
    rule.parent.mkdir(parents=True)
    rule.write_text("---\nname: run-the-suite\ndescription: run it\ntags:\n  - testing\n"
                    "sources:\n  - bots/proj/memory/a.md\nstability: stable\n---\n"
                    "Run the full suite before claiming done.\n", encoding="utf-8")
    return root


def _cfg(vault: Path) -> dict:
    return {"vaultRoot": str(vault), "reflex": {"enabled": True}}


def _run_hook(monkeypatch, vault: Path, cwd: Path, cfg: dict) -> list:
    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: cfg)
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda *a, **k: vault)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "d" * 36, "cwd": str(cwd), "source": "startup"})))
    monkeypatch.setattr("sys.stdout", io.StringIO())
    spawned: list = []
    monkeypatch.setattr(session_start, "_spawn_detached",
                        lambda args, cwd=None: spawned.append(list(args)))
    session_start.main()
    return spawned


def test_the_hook_neither_rebuilds_the_reflex_index_nor_mirrors(monkeypatch, vault, tmp_path):
    reflex_index.write_index(vault, reflex_index.build_index(vault))
    built: list = []
    mirrored: list = []
    monkeypatch.setattr(reflex_index, "build_index", lambda *a, **k: built.append(1) or {})
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda *a, **k: mirrored.append(1))
    spawned = _run_hook(monkeypatch, vault, tmp_path, _cfg(vault))
    assert built == [] and mirrored == []
    assert ["session-start-deferred"] in spawned


def test_a_vault_with_no_reflex_index_gets_one_before_the_first_prompt(monkeypatch, vault,
                                                                       tmp_path):
    assert reflex_index.load_index(vault) is None
    _run_hook(monkeypatch, vault, tmp_path, _cfg(vault))
    index = reflex_index.load_index(vault)
    assert index is not None and "run-the-suite" in index["docs"]


def test_the_deferred_run_mirrors_and_rebuilds_the_reflex_index(monkeypatch, vault):
    mirrored: list = []
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda cfg: mirrored.append(cfg))
    assert session_start.run_deferred(_cfg(vault), vault) == "done"
    assert len(mirrored) == 1
    assert "run-the-suite" in reflex_index.load_index(vault)["docs"]
    # Reflex off: the mirror still runs, the index is not written.
    (vault / ".mnemo" / reflex_index.INDEX_FILENAME).unlink()
    assert session_start.run_deferred({"vaultRoot": str(vault)}, vault) == "done"
    assert len(mirrored) == 2
    assert reflex_index.load_index(vault) is None


def test_one_deferred_run_at_a_time(monkeypatch, vault):
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda cfg: None)
    (vault / session_start._DEFERRED_LOCK).mkdir(parents=True)
    assert session_start.run_deferred(_cfg(vault), vault) == "locked"
    assert reflex_index.load_index(vault) is None


def test_the_deferred_command_is_internal_and_runs_the_deferred_work(monkeypatch, vault):
    from mnemo.cli.parser import COMMANDS, INTERNAL_COMMANDS

    assert "session-start-deferred" in COMMANDS
    assert "session-start-deferred" in INTERNAL_COMMANDS
    seen: list = []
    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: _cfg(vault))
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda *a, **k: vault)
    monkeypatch.setattr(session_start, "run_deferred", lambda cfg, v: seen.append(v) or "done")
    assert COMMANDS["session-start-deferred"](None) == 0
    assert seen == [vault]


def test_the_injected_context_is_the_same_with_the_deferred_work_run_first(monkeypatch, vault,
                                                                           tmp_path):
    """Out of scope for #610 is *what* SessionStart injects. Before it, the
    mirror and the reflex rebuild ran ahead of the injection; a hook whose
    deferred work ran first is that order, and its envelope must not differ
    by a byte from the one the hook now prints without it."""
    import shutil

    briefings = vault / "bots" / "proj" / "briefings" / "sessions"
    briefings.mkdir(parents=True)
    (briefings / "abc.md").write_text(
        "---\nsession_id: abc\ndate: 2026-10-01\nduration_minutes: 10\n---\n"
        "## TL;DR\nShipped the thing.\n", encoding="utf-8")
    cwd = tmp_path / "proj"
    cwd.mkdir()
    outs = []
    for deferred_first in (False, True):
        copy = tmp_path / ("vault-%s" % deferred_first)
        shutil.copytree(vault, copy)
        cfg = {"vaultRoot": str(copy), "reflex": {"enabled": True},
               "injection": {"enabled": True}}
        if deferred_first:
            session_start.run_deferred(cfg, copy)
        monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, _c=cfg, **k: _c)
        monkeypatch.setattr("mnemo.core.paths.vault_root", lambda *a, _v=copy, **k: _v)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
            {"session_id": "e" * 36, "cwd": str(cwd), "source": "startup"})))
        out = io.StringIO()
        monkeypatch.setattr("sys.stdout", out)
        session_start.main()
        outs.append(out.getvalue().replace(str(copy), "<vault>"))
    assert outs[0], "the fixture must inject something to compare"
    assert outs[0] == outs[1]


def test_the_deferred_run_compacts_the_judge_pick_ledger(monkeypatch, vault):
    """#619: the ledger the reflex hook appends to is bounded here, off the
    hot path, and nowhere a prompt waits."""
    from mnemo.core.reflex import picks
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda cfg: None)
    seen: list = []
    monkeypatch.setattr(picks, "compact", lambda v, **k: seen.append(Path(v)) or "under_cap")
    assert session_start.run_deferred(_cfg(vault), vault) == "done"
    assert seen == [vault]


def test_a_failing_compaction_is_logged_and_the_run_goes_on(monkeypatch, vault):
    from mnemo.core.reflex import picks

    def boom(v, **k):
        raise OSError("disk")
    monkeypatch.setattr("mnemo.core.mirror.mirror_all", lambda cfg: None)
    monkeypatch.setattr(picks, "compact", boom)
    assert session_start.run_deferred(_cfg(vault), vault) == "done"
    assert "session_start.judge_picks" in (vault / ".errors.log").read_text(encoding="utf-8")
