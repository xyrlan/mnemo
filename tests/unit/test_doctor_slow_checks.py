"""The two doctor rows #571 made fast print what they printed before.

``universal_promotion`` stopped scoring pairs that cannot reach the threshold,
and ``rediscovered_procedures`` stopped re-reading transcripts that did not
change. Each row's output on a fixture vault is compared with the output of
the code it replaced: the all-pairs scan, and a scan with no cache.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands.doctor_checks.procedures import _doctor_check_rediscovered_procedures
from mnemo.cli.commands.doctor_checks.rules import _doctor_check_universal_promotion
from mnemo.core import procedures as P
from mnemo.core import universal_candidates as U

from tests.unit.test_procedures import _child, _repo
from tests.unit.test_universal_candidates import _all_pairs_reference, _random_index


def _row(check, vault: Path, capsys) -> str:
    check(vault)
    return capsys.readouterr().out


# --- universal promotion ----------------------------------------------------


def test_universal_row_prints_what_the_all_pairs_scan_printed(
    tmp_vault: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    from mnemo.core import rule_activation

    index = _random_index(3, n=200)
    index["universal"] = {"slugs": ["u-1"], "topics": ["git"]}
    monkeypatch.setattr(rule_activation, "load_index", lambda vault: index)

    pruned = _row(_doctor_check_universal_promotion, tmp_vault, capsys)
    monkeypatch.setattr(U, "find_universal_candidates", _all_pairs_reference)
    every_pair = _row(_doctor_check_universal_promotion, tmp_vault, capsys)

    assert "cross-project promotion candidate(s)" in pruned
    assert pruned == every_pair


# --- rediscovered procedures ------------------------------------------------


@pytest.fixture
def children(tmp_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Three children of ``app`` that rediscovered ``SDKROOT``, in ``~/.claude/projects``."""
    from mnemo.core import agent

    projects = tmp_home / ".claude" / "projects"
    app = _repo(tmp_path, "app")
    for n in (1, 2, 3):
        _child(projects, app, f"app-wt-{n}", ["cargo test", "SDKROOT=/sdk cargo test"])
    monkeypatch.setattr(agent, "resolve_canonical_agent",
                        lambda cwd: agent.AgentInfo(name="app", repo_root=str(app), has_git=True))
    return projects


def _uncached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The row as it ran before #571: every transcript read on every run."""
    real = P.scan
    monkeypatch.setattr(P, "scan", lambda *a, transcript_cache=None, **k: real(*a, **k))


def test_procedures_row_prints_what_the_uncached_scan_printed(
    tmp_vault: Path, children: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    cold = _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys)
    assert (tmp_vault / P.TRANSCRIPT_CACHE_REL).is_file()

    real_records = P._records
    monkeypatch.setattr(P, "_records", lambda path: pytest.fail(f"re-read {path}"))
    warm = _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys)
    monkeypatch.setattr(P, "_records", real_records)

    _uncached(monkeypatch)
    before = _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys)

    assert "Rediscovered procedures: 1 procedure" in before
    assert cold == warm == before


def test_procedures_row_sees_a_new_child_through_the_cache(
    tmp_vault: Path, tmp_path: Path, children: Path, monkeypatch: pytest.MonkeyPatch, capsys,
) -> None:
    _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys)
    app = tmp_path / "repos" / "app"
    _child(children, app, "app-wt-4", ["make", "CC=clang make"])
    _child(children, app, "app-wt-5", ["make", "CC=clang make"])

    cached = _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys)
    _uncached(monkeypatch)
    assert cached == _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys)
    assert "Rediscovered procedures: 2 procedures" in cached


def test_deleting_the_cache_changes_nothing(
    tmp_vault: Path, children: Path, capsys,
) -> None:
    first = _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys)
    (tmp_vault / P.TRANSCRIPT_CACHE_REL).unlink()
    assert _row(_doctor_check_rediscovered_procedures, tmp_vault, capsys) == first
    entries = json.loads((tmp_vault / P.TRANSCRIPT_CACHE_REL).read_text(encoding="utf-8"))
    assert len(entries["files"]) == 3
