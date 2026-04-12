from __future__ import annotations

from pathlib import Path

from mnemo.core import agent


def test_resolves_to_git_root_basename(tmp_path: Path):
    repo = tmp_path / "myproject"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    info = agent.resolve_agent(str(sub))
    assert info.name == "myproject"
    assert info.repo_root == str(repo)
    assert info.has_git is True


def test_resolves_to_basename_when_no_git(tmp_path: Path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    info = agent.resolve_agent(str(folder))
    assert info.name == "scratch"
    assert info.repo_root == str(folder)
    assert info.has_git is False


def test_root_directory_fallback_name(tmp_path: Path, monkeypatch):
    # If walking up reaches filesystem root, name should not be empty.
    # Simulate by passing "/" — the basename of "/" is "" so we expect "root".
    info = agent.resolve_agent("/")
    assert info.name == "root"
    assert info.has_git is False


def test_sanitizes_unsafe_chars(tmp_path: Path):
    folder = tmp_path / "weird name with spaces"
    folder.mkdir()
    info = agent.resolve_agent(str(folder))
    # spaces collapsed to dashes, no path-traversal
    assert "/" not in info.name
    assert info.name == "weird-name-with-spaces"
