"""The rules file that carries a project's briefing to its next Grok session.

What Grok does with the file was measured on grok 1.0.60 (2026-10-16):
``.grok/rules/*.md`` in the session's tree lands in its first user record
with the frontmatter stripped, ``info/exclude`` does not stop it loading,
an untrusted folder drops it in a headless session, and
``prompt_context.json`` lists it (or not) before the SessionStart hooks run.
These tests pin mnemo's half: where the file goes, what it holds, and that
it never reaches a commit, another project or a home rules dir.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mnemo.core.grok import briefing_file


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@example.com",
         "-c", "commit.gpgsign=false"] + list(args),
        capture_output=True, text=True, encoding="utf-8", check=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    _git(root, "add", "a.txt")
    _git(root, "commit", "-q", "-m", "a")
    return root


def test_the_file_is_written_where_grok_reads_rules_and_never_committed(repo: Path) -> None:
    path = briefing_file.write(repo, "mnemo://v1 project=proj\nbody", "proj")
    assert path == repo / ".grok" / "rules" / "mnemo-briefing.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n") and briefing_file.OWNER_LINE in text
    body = text.split("---\n", 2)[2]  # what Grok hands the model
    assert body == "[mnemo:briefing via=rules-file project=proj]\nmnemo://v1 project=proj\nbody\n"
    assert _git(repo, "status", "--porcelain") == ""
    _git(repo, "add", "-A")
    assert _git(repo, "diff", "--cached", "--name-only") == ""
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(briefing_file.EXCLUDE_LINE) == 1
    briefing_file.write(repo, "second", "proj")
    exclude = (repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.splitlines().count(briefing_file.EXCLUDE_LINE) == 1


def test_every_worktree_gets_the_file_and_the_repo_stays_clean(repo: Path, tmp_path: Path) -> None:
    tree = tmp_path / "proj-wt-1"
    _git(repo, "worktree", "add", "-q", str(tree))
    found = briefing_file.trees(tree)
    assert {Path(p).resolve() for p in found} == {repo.resolve(), tree.resolve()}
    written = briefing_file.refresh(tmp_path, {}, tree, "proj", text="the block")
    assert len(written) == 2
    for where in (repo, tree):
        assert "the block" in briefing_file.rule_path(where).read_text(encoding="utf-8")
        assert _git(where, "status", "--porcelain") == ""
    # A tree that holds the file is still removable (tree-sweep relies on it).
    _git(repo, "worktree", "remove", str(tree))
    assert not tree.exists()


def test_nothing_is_written_outside_a_git_repository(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert briefing_file.trees(plain) == []
    assert briefing_file.refresh(tmp_path, {}, plain, "plain", text="block") == []
    assert not (plain / ".grok").exists()


def test_a_home_or_grok_home_tree_is_refused(repo: Path, monkeypatch) -> None:
    """There ``.grok/rules`` is a home rules dir, loaded by every project."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: repo))
    assert briefing_file.write(repo, "block", "proj") is None
    assert not (repo / ".grok").exists()


def test_empty_text_removes_mnemos_file_and_only_mnemos(repo: Path) -> None:
    briefing_file.write(repo, "block", "proj")
    assert briefing_file.write(repo, "", "proj") is None
    assert not briefing_file.rule_path(repo).exists()
    foreign = briefing_file.rule_path(repo)
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text("someone else's rule\n", encoding="utf-8")
    assert briefing_file.write(repo, "", "proj") is None
    assert briefing_file.write(repo, "block", "proj") is None
    assert foreign.read_text(encoding="utf-8") == "someone else's rule\n"


def test_render_follows_injection_and_never_logs_a_read(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    folder = vault / "bots" / "proj" / "briefings" / "sessions"
    folder.mkdir(parents=True)
    (folder / "s1.md").write_text(
        "---\ntype: briefing\nagent: proj\nsession_id: s1\ndate: 2026-10-15\n"
        "duration_minutes: 3\n---\n\n## TL;DR\nCodeword BRIEFOWL.\n", encoding="utf-8")
    assert briefing_file.render(vault, {}, "proj") == ""
    on = {"vaultRoot": str(vault), "injection": {"enabled": True}}
    text = briefing_file.render(vault, on, "proj")
    assert "BRIEFOWL" in text and "[recent-briefings" in text
    assert briefing_file.render(vault, on, "other-project") == ""
    assert not list(vault.rglob("briefing-log*"))


def _session(tmp_path: Path, files) -> Path:
    folder = tmp_path / "session"
    folder.mkdir(parents=True)
    (folder / "prompt_context.json").write_text(json.dumps({"agents_md_files": files}),
                                               encoding="utf-8")
    return folder


def test_loaded_reads_what_grok_put_in_prompt_context(tmp_path: Path, repo: Path) -> None:
    # The shape grok 1.0.60 wrote for the probe session that loaded the file.
    entry = {"file_name": "mnemo-briefing.md", "source": "project", "content": "x",
             "file_path": str(briefing_file.rule_path(repo))}
    assert briefing_file.loaded(_session(tmp_path / "a", [entry])) is True
    other = dict(entry, file_path=str(repo / "CLAUDE.md"), file_name="CLAUDE.md")
    assert briefing_file.loaded(_session(tmp_path / "b", [other])) is False
    assert briefing_file.loaded(_session(tmp_path / "c", [])) is False  # untrusted folder
    assert briefing_file.loaded(tmp_path / "missing") is None


def test_session_dir_finds_the_session_under_the_hooks_home(tmp_path: Path, monkeypatch) -> None:
    from mnemo.core.grok import paths

    home = tmp_path / "grok-home"
    sid = "01a144cd-aa48-7271-b671-114727e068a2"
    folder = home / "sessions" / paths.encode_cwd(str(tmp_path / "proj")) / sid
    folder.mkdir(parents=True)
    assert briefing_file.session_dir(sid, str(tmp_path / "proj"), env={"GROK_HOME": str(home)}) == folder
    assert briefing_file.session_dir("nope", str(tmp_path / "proj"), env={"GROK_HOME": str(home)}) is None
