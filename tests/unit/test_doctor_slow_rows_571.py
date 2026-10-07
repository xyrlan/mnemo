"""The two slow doctor rows print the same thing after #571 made them fast.

``universal_promotion`` scored every same-type rule pair; it now scores only
pairs sharing a name token. ``rediscovered_procedures`` re-read every child
transcript; it now keeps what each one read as in ``.mnemo/``. Neither change
may move a byte of what ``mnemo doctor`` prints, and these tests hold each row
on a fixture vault to the output of the code it replaced.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import random
import types
from pathlib import Path

import pytest

from mnemo.cli.commands.doctor_checks import procedures as doctor_procedures
from mnemo.cli.commands.doctor_checks import rules as doctor_rules
from mnemo.core import universal_candidates as U
from mnemo.core.rule_activation.index import INDEX_FILENAME, INDEX_VERSION


def _printed(fn, *args) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(*args)
    return out.getvalue()


def _all_pairs(items, _threshold):
    """What the loop scored before #571: every same-type pair, in order."""
    return [
        (i, j)
        for i in range(len(items))
        for j in range(i + 1, len(items))
        if items[i].type == items[j].type
    ]


# --- universal promotion ---------------------------------------------------

_WORDS = (
    "run migrations before deploy release check branch merge stacked rebase "
    "tests worktree cache index"
).split()


def _synthetic_rules(seed: int, count: int = 160) -> dict:
    """Rules drawn from a small vocabulary, so many pairs clear the bar and
    clusters merge transitively — the case where the visiting order shows."""
    rng = random.Random(seed)
    rules = {}
    for n in range(count):
        name = " ".join(rng.sample(_WORDS, rng.randint(2, 4)))
        rules[f"rule-{n:03d}"] = {
            "type": rng.choice(["feedback", "feedback", "reference"]),
            "name": name,
            "topic_tags": rng.sample(_WORDS, rng.randint(0, 2)),
            "body_preview": " ".join(rng.sample(_WORDS, rng.randint(0, 6))),
            "projects": rng.sample(["repo-a", "repo-b", "repo-c", "repo-d"],
                                   rng.randint(1, 2)),
            "universal": rng.random() < 0.05,
        }
    return rules


def _vault_with_index(tmp_path: Path, rules: dict) -> Path:
    vault = tmp_path / "vault"
    (vault / ".mnemo").mkdir(parents=True)
    (vault / ".mnemo" / INDEX_FILENAME).write_text(json.dumps({
        "schema_version": INDEX_VERSION,
        "rules": rules,
        "universal": {"slugs": [], "topics": []},
    }), encoding="utf-8")
    return vault


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_universal_row_prints_what_scoring_every_pair_printed(
    tmp_path: Path, monkeypatch, seed: int
) -> None:
    vault = _vault_with_index(tmp_path, _synthetic_rules(seed))
    fast = _printed(doctor_rules._doctor_check_universal_promotion, vault)
    monkeypatch.setattr(U, "_candidate_pairs", _all_pairs)
    slow = _printed(doctor_rules._doctor_check_universal_promotion, vault)

    assert "cross-project promotion candidate(s)" in slow  # the fixture bites
    assert fast == slow


@pytest.mark.parametrize("threshold", [0.3, 0.5, 0.5000000001, 0.55, 0.7, 1.0])
def test_every_pair_that_clears_the_bar_is_a_candidate_pair(threshold: float) -> None:
    rules = _synthetic_rules(7, count=120)
    items = [U._Fields(slug, rule) for slug, rule in sorted(rules.items())]
    kept = set(U._candidate_pairs(items, threshold))
    for i, j in _all_pairs(items, threshold):
        if U._similarity(items[i], items[j]) >= threshold:
            assert (i, j) in kept
    assert sorted(kept) == U._candidate_pairs(items, threshold)


def test_a_threshold_no_name_overlap_could_reach_scores_every_pair() -> None:
    items = [U._Fields(slug, rule) for slug, rule in sorted(_synthetic_rules(9, 40).items())]
    assert U._candidate_pairs(items, 0.5) == _all_pairs(items, 0.5)


def test_clusters_match_scoring_every_pair_at_any_threshold(monkeypatch) -> None:
    index = {"rules": _synthetic_rules(11)}
    for threshold in (0.3, 0.45, 0.55, 0.65):
        fast = U.find_universal_candidates(index, threshold=threshold)
        with monkeypatch.context() as m:
            m.setattr(U, "_candidate_pairs", _all_pairs)
            slow = U.find_universal_candidates(index, threshold=threshold)
        assert fast == slow


# --- rediscovered procedures -----------------------------------------------


def _child(projects: Path, repo_root: Path, worktree: str, commands: list) -> Path:
    cwd = str(repo_root.parent / worktree)
    records = [{
        "type": "assistant", "cwd": cwd, "timestamp": "2026-09-19T10:00:00Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": c}}]},
    } for i, c in enumerate(commands)]
    directory = projects / worktree
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{len(list(directory.iterdir()))}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def procedures_home(tmp_path: Path, monkeypatch):
    """A machine whose ``~/.claude/projects`` holds two children of ``app``."""
    home = tmp_path / "home"
    projects = home / ".claude" / "projects"
    repo = tmp_path / "repos" / "app"
    repo.mkdir(parents=True)
    (repo / "CLAUDE.md").write_text("# app\n", encoding="utf-8")
    for n in (1, 2):
        _child(projects, repo, f"app-wt-{n}", ["cargo test", "SDKROOT=/sdk cargo test"])

    real_expanduser = os.path.expanduser
    monkeypatch.setattr(
        os.path, "expanduser",
        lambda p: str(home) + p[1:] if p.startswith("~") else real_expanduser(p))
    monkeypatch.setattr("mnemo.core.agent.resolve_canonical_agent",
                        lambda _cwd: types.SimpleNamespace(name="app"))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda *a, **k: {})
    vault = tmp_path / "vault"
    vault.mkdir()
    return types.SimpleNamespace(vault=vault, projects=projects, repo=repo)


def test_the_procedures_row_prints_the_same_cold_warm_and_uncached(
    procedures_home, monkeypatch
) -> None:
    from mnemo.core import procedures as P

    vault = procedures_home.vault
    cache = P.transcripts_cache_path(vault)
    cold = _printed(doctor_procedures._doctor_check_rediscovered_procedures, vault)
    assert cache.is_file()
    assert cache.parent == vault / ".mnemo"
    warm = _printed(doctor_procedures._doctor_check_rediscovered_procedures, vault)

    real_scan = P.scan
    monkeypatch.setattr(P, "scan", lambda *a, **k: real_scan(*a, **{**k, "cache": None}))
    uncached = _printed(doctor_procedures._doctor_check_rediscovered_procedures, vault)

    assert "Rediscovered procedures: 1 procedure" in cold  # the fixture bites
    assert cold == warm == uncached


def test_the_procedures_row_sees_a_child_that_arrived_after_the_cache(
    procedures_home,
) -> None:
    vault = procedures_home.vault
    first = _printed(doctor_procedures._doctor_check_rediscovered_procedures, vault)
    _child(procedures_home.projects, procedures_home.repo, "app-wt-3",
           ["pnpm test", "CI=1 pnpm test"])
    _child(procedures_home.projects, procedures_home.repo, "app-wt-4",
           ["pnpm test", "CI=1 pnpm test"])
    second = _printed(doctor_procedures._doctor_check_rediscovered_procedures, vault)

    assert "1 procedure " in first
    assert "2 procedures " in second


def test_deleting_the_cache_changes_nothing_the_row_prints(procedures_home) -> None:
    from mnemo.core import procedures as P

    vault = procedures_home.vault
    before = _printed(doctor_procedures._doctor_check_rediscovered_procedures, vault)
    P.transcripts_cache_path(vault).unlink()
    after = _printed(doctor_procedures._doctor_check_rediscovered_procedures, vault)
    assert before == after
