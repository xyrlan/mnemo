"""#228: migrating a briefing must carry every citation of it along.

The move itself was always correct; what it left behind was a vault full of
references to a path that no longer existed. These tests pin the four
populations that cite a briefing path — rule frontmatter (in three spellings),
``_inbox`` drafts, the extraction state, and the project-derived indexes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from mnemo.cli.commands import migrate_worktree_briefings as cmd_mod
from mnemo.core.migrations import briefing_citations as citations

_UUID = "ba5d51e7-be91-484a-8191-8b6c5cf3e306"
_OLD = f"bots/myproj-feature-x/briefings/sessions/{_UUID}.md"
_NEW = f"bots/myproj/briefings/sessions/{_UUID}.md"


def _rule_text(source: str) -> str:
    """A rule page citing *source* in all three spellings mnemo writes."""
    return (
        "---\n"
        "name: 'A rule'\n"
        "slug: a-rule\n"
        "type: feedback\n"
        "stability: stable\n"
        "sources:\n"
        f"  - {source}\n"
        "tags:\n"
        "  - testing\n"
        "evidence:\n"
        f"  source: 'briefing: {source} — user turns, turn 1'\n"
        "---\n"
        "\n"
        "Body text long enough to clear the doctor body floor, which is fifty.\n"
        "\n"
        "## Sources\n"
        f"- [[{source[:-3]}]]\n"
    )


def _make_vault(tmp_path: Path) -> tuple[Path, Path]:
    """A vault + repo wired like a real worktree. Returns (vault, main_repo)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    wt_gitdir = main / ".git" / "worktrees" / "feature-x"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    worktree = tmp_path / "myproj-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n", encoding="utf-8")

    orphan = vault / "bots" / "myproj-feature-x" / "briefings" / "sessions"
    orphan.mkdir(parents=True)
    (orphan / f"{_UUID}.md").write_text("# orphan briefing\n", encoding="utf-8")

    live = vault / "shared" / "feedback"
    live.mkdir(parents=True)
    (live / "a-rule.md").write_text(_rule_text(_OLD), encoding="utf-8")

    # A draft: doctor cannot see these, so nothing but the migration reports them.
    draft = vault / "shared" / "_inbox" / "feedback"
    draft.mkdir(parents=True)
    (draft / "b-rule.md").write_text(_rule_text(_OLD), encoding="utf-8")

    state = {
        "schema_version": 2,
        "last_run": "2026-09-13T00:00:00",
        "entries": {
            "feedback/a-rule": {
                "source_files": [_OLD],
                "source_hash": "sha256:abc",
                "written_hash": "sha256:stale",
                "written_at": "2026-09-13T00:00:00",
                "status": "auto_promoted",
            }
        },
    }
    mnemo_dir = vault / ".mnemo"
    mnemo_dir.mkdir()
    (mnemo_dir / "extraction-state.json").write_text(json.dumps(state), encoding="utf-8")
    return vault, main


def _run(vault: Path, main: Path, monkeypatch, *, dry_run: bool) -> None:
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=dry_run, repos=[str(main)])
    assert cmd_mod.cmd_migrate_worktree_briefings(args) == 0


def test_rewrites_all_three_citation_spellings(tmp_path: Path, monkeypatch) -> None:
    """sources:, evidence.source and the [[wikilink]] all move together."""
    vault, main = _make_vault(tmp_path)
    _run(vault, main, monkeypatch, dry_run=False)

    text = (vault / "shared" / "feedback" / "a-rule.md").read_text(encoding="utf-8")
    assert _OLD not in text
    assert f"  - {_NEW}\n" in text
    assert f"source: 'briefing: {_NEW}" in text
    assert f"- [[{_NEW[:-3]}]]" in text


def test_rewrites_inbox_drafts(tmp_path: Path, monkeypatch) -> None:
    """_inbox drafts are invisible to doctor — the migration must still fix them."""
    vault, main = _make_vault(tmp_path)
    _run(vault, main, monkeypatch, dry_run=False)

    text = (vault / "shared" / "_inbox" / "feedback" / "b-rule.md").read_text(encoding="utf-8")
    assert _OLD not in text
    assert _NEW in text


def test_repoints_extraction_state_source_files(tmp_path: Path, monkeypatch) -> None:
    """dedup compares source sets for exact equality; a stale list defeats it."""
    vault, main = _make_vault(tmp_path)
    _run(vault, main, monkeypatch, dry_run=False)

    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8"))
    assert state["entries"]["feedback/a-rule"]["source_files"] == [_NEW]


def test_advances_written_hash_for_pages_it_rewrote(tmp_path: Path, monkeypatch) -> None:
    """A bulk rewriter owns its bytes; a stale hash reads as a hand edit."""
    from mnemo.core.extract.inbox.io import content_hash

    vault, main = _make_vault(tmp_path)
    _run(vault, main, monkeypatch, dry_run=False)

    page = vault / "shared" / "feedback" / "a-rule.md"
    state = json.loads((vault / ".mnemo" / "extraction-state.json").read_text(encoding="utf-8"))
    recorded = state["entries"]["feedback/a-rule"]["written_hash"]
    assert recorded != "sha256:stale"
    assert recorded == content_hash(page.read_text(encoding="utf-8"))


def test_index_files_the_rule_under_the_canonical_project(tmp_path: Path, monkeypatch) -> None:
    """The real consequence: a stale -wt- source hides the rule from its project.

    ``projects_for_rule`` reads the project out of the ``bots/<name>/`` segment,
    so before the rewrite this rule is filed under ``myproj-feature-x`` — a
    project nothing ever queries.
    """
    vault, main = _make_vault(tmp_path)
    _run(vault, main, monkeypatch, dry_run=False)

    index = json.loads(
        (vault / ".mnemo" / "rule-activation-index.json").read_text(encoding="utf-8")
    )
    rules = index.get("rules", {})
    entry = rules.get("feedback/a-rule") or next(iter(rules.values()))
    assert "myproj" in entry["projects"]
    assert "myproj-feature-x" not in entry["projects"]


def test_dry_run_reports_blast_radius_and_writes_nothing(tmp_path: Path, monkeypatch, capsys) -> None:
    """The issue's minimum bar: say what will break before anything moves."""
    vault, main = _make_vault(tmp_path)
    before = (vault / "shared" / "feedback" / "a-rule.md").read_text(encoding="utf-8")

    _run(vault, main, monkeypatch, dry_run=True)

    out = capsys.readouterr().out
    assert "would move" in out
    assert "citation" in out
    assert "a-rule.md" in out
    assert "extraction-state" in out
    assert "would rebuild" in out
    # Nothing moved, nothing rewritten.
    assert (vault / "bots" / "myproj-feature-x" / "briefings" / "sessions" / f"{_UUID}.md").exists()
    assert (vault / "shared" / "feedback" / "a-rule.md").read_text(encoding="utf-8") == before
    assert not (vault / ".mnemo" / "rule-activation-index.json").exists()


def test_unrelated_citations_are_left_alone(tmp_path: Path, monkeypatch) -> None:
    """Only paths that actually moved are rewritten — no prose collateral."""
    vault, main = _make_vault(tmp_path)
    other = vault / "shared" / "reference"
    other.mkdir(parents=True)
    prose = (
        "---\n"
        "name: 'Another'\n"
        "slug: another\n"
        "type: reference\n"
        "sources:\n"
        "  - bots/other/briefings/sessions/zzz.md\n"
        "tags:\n"
        "  - testing\n"
        "---\n"
        "\n"
        "Strip any trailing `-wt-N` suffix from the repo root before appending.\n"
    )
    (other / "another.md").write_text(prose, encoding="utf-8")

    _run(vault, main, monkeypatch, dry_run=False)

    assert (other / "another.md").read_text(encoding="utf-8") == prose


def test_rewrite_is_a_noop_when_nothing_cites_the_briefing(tmp_path: Path, monkeypatch) -> None:
    """A vault with no citations still migrates cleanly."""
    vault, main = _make_vault(tmp_path)
    for p in (vault / "shared").rglob("*.md"):
        p.unlink()

    _run(vault, main, monkeypatch, dry_run=False)
    assert (vault / "bots" / "myproj" / "briefings" / "sessions" / f"{_UUID}.md").exists()


def test_build_path_map_ignores_moves_outside_the_vault(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    moves = [(tmp_path / "elsewhere" / "a.md", vault / "bots" / "p" / "a.md")]
    assert citations.build_path_map(moves, vault) == {}
