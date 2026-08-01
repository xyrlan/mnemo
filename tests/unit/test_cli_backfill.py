"""backfill CLI: dry-run spends nothing, caps are honoured, ledger advances."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mnemo.core.backfill import discover, ledger
from mnemo.cli.commands import backfill as cmd


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60},
        "backfill": {"enabled": True, "installCap": 2, "minFileMutations": 1,
                     "autoOnFirstSession": True},
    }
    monkeypatch.setattr(cmd.cfg_mod, "load_config", lambda: cfg)

    made: list[discover.Transcript] = []
    for i, name in enumerate(["s1", "s2", "s3"]):
        p = tmp_path / f"{name}.jsonl"
        p.write_text('{"type":"user"}\n', encoding="utf-8")
        made.append(discover.Transcript(path=p, agent="alpha", cwd="/tmp/alpha", mtime=float(i)))
    made.sort(key=lambda t: t.mtime, reverse=True)
    monkeypatch.setattr(
        cmd.discover, "find_transcripts",
        lambda **kw: made[: kw["limit"]] if kw.get("limit") else made,
    )
    return cfg, vault, made


def _args(**kw) -> argparse.Namespace:
    base = dict(all=False, dry_run=False, project=None, limit=None,
                install_run=False, yes=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_dry_run_writes_nothing_and_calls_no_llm(env, monkeypatch, capsys):
    cfg, vault, _ = env

    def explode(*a, **k):
        raise AssertionError("dry run must not harvest")

    monkeypatch.setattr(cmd.harvest, "harvest_session", explode)

    assert cmd.cmd_backfill(_args(dry_run=True, all=True)) == 0
    assert not ledger.state_path(vault).exists()
    assert "3" in capsys.readouterr().out


def test_harvests_and_records_in_ledger(env, monkeypatch):
    cfg, vault, made = env
    calls: list[Path] = []

    def fake(jsonl_path, agent, config):
        calls.append(jsonl_path)
        return [Path("written.md")]

    monkeypatch.setattr(cmd.harvest, "harvest_session", fake)

    assert cmd.cmd_backfill(_args(all=True)) == 0
    assert len(calls) == 3

    led = ledger.load(vault)
    assert ledger.should_harvest(led, made[0].path) is False


def test_install_run_respects_install_cap(env, monkeypatch):
    cfg, vault, _ = env
    calls: list[Path] = []
    monkeypatch.setattr(
        cmd.harvest, "harvest_session",
        lambda p, a, c: calls.append(p) or [],
    )

    assert cmd.cmd_backfill(_args(install_run=True)) == 0
    assert len(calls) == 2  # installCap


def test_second_run_skips_already_done_sessions(env, monkeypatch):
    cfg, vault, _ = env
    calls: list[Path] = []
    monkeypatch.setattr(
        cmd.harvest, "harvest_session",
        lambda p, a, c: calls.append(p) or [],
    )

    cmd.cmd_backfill(_args(all=True))
    first = len(calls)
    cmd.cmd_backfill(_args(all=True))
    assert len(calls) == first  # nothing re-harvested


def test_one_failing_session_does_not_abort_the_run(env, monkeypatch):
    cfg, vault, made = env
    seen: list[Path] = []

    def flaky(jsonl_path, agent, config):
        seen.append(jsonl_path)
        if jsonl_path == made[0].path:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(cmd.harvest, "harvest_session", flaky)

    assert cmd.cmd_backfill(_args(all=True)) == 0
    assert len(seen) == 3

    led = ledger.load(vault)
    assert led["sessions"][made[0].path.stem]["status"] == "failed"


def test_disabled_config_is_a_no_op(env, monkeypatch):
    cfg, vault, _ = env
    cfg["backfill"]["enabled"] = False
    calls: list[Path] = []
    monkeypatch.setattr(
        cmd.harvest, "harvest_session",
        lambda p, a, c: calls.append(p) or [],
    )
    assert cmd.cmd_backfill(_args(all=True)) == 0
    # Asserted positively rather than by a raising stub: the sweep's
    # per-session ``except Exception`` would swallow the raise and the test
    # would pass even with the enabled check gone.
    assert calls == []
    assert not ledger.state_path(vault).exists()
