"""``mnemo reverify`` end to end through the CLI: dry run saves a plan and
touches nothing; ``--apply`` executes it; ``--undo`` restores."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.cli.runtime import main
from mnemo.core import reverify as RV
from mnemo.core.reclassify_types import split_frontmatter
from tests.unit.test_reverify import FakeBriefer, Q_KEPT, SID_A, env  # noqa: F401 — fixture reuse


@pytest.fixture
def wired(env, monkeypatch):
    vault, projects = env
    monkeypatch.setattr(cli, "_resolve_vault", lambda: vault)
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    briefer = FakeBriefer([Q_KEPT])
    monkeypatch.setattr(RV, "default_briefer", lambda cfg, **kw: briefer)
    real_run = RV.run
    monkeypatch.setattr(RV, "run", lambda v, **kw: real_run(v, projects_root=projects, **kw))
    return vault, briefer


def test_dry_run_saves_a_plan_and_changes_nothing(wired, capsys):
    vault, briefer = wired
    before = {p: p.read_bytes() for p in (vault / "shared").rglob("*.md")}

    assert main(["reverify"]) == 0
    out = capsys.readouterr().out

    assert re.search(r"verified now\s+1", out) and re.search(r"still fails\s+1", out)
    assert "dry run — nothing changed" in out
    assert briefer.calls == [("alpha", SID_A)]
    plan = json.loads((vault / ".mnemo" / RV.PLAN_NAME).read_text(encoding="utf-8"))
    assert {o["status"] for o in plan["outcomes"]} == {RV.VERIFIED, RV.FAILS, RV.QUOTE_TOO_SHORT, RV.NO_TRANSCRIPT}
    assert {p: p.read_bytes() for p in (vault / "shared").rglob("*.md")} == before


def test_apply_without_a_dry_run_refuses(wired, capsys):
    assert main(["reverify", "--apply"]) == 1
    assert "run `mnemo reverify` first" in capsys.readouterr().out


def test_apply_executes_the_saved_plan_without_the_llm_and_undo_restores(wired, capsys):
    vault, briefer = wired
    from tests.unit.test_reverify import _snapshot

    before = _snapshot(vault)
    assert main(["reverify"]) == 0
    capsys.readouterr()

    assert main(["reverify", "--apply"]) == 0
    out = capsys.readouterr().out
    assert "applied: kept 1, demoted 2" in out
    assert briefer.calls == [("alpha", SID_A)], "apply spends no LLM call"
    run_id = re.search(r"--undo (\S+)", out).group(1)
    fm, _ = split_frontmatter((vault / "shared" / "reference" / "lost-rule.md").read_text(encoding="utf-8"))
    assert fm["demoted_from"] == "feedback"

    assert main(["reverify", "--apply"]) == 1, "the same plan cannot be applied twice"
    assert "already applied" in capsys.readouterr().out

    assert main(["reverify", "--undo", run_id]) == 0
    assert _snapshot(vault) == before
