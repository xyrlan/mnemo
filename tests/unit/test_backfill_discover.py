"""Discovery: decode project dirs, rank by mtime, filter by project."""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

import pytest

from mnemo.core.backfill import discover


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    root = home / ".claude" / "projects"
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return root


def _session(project_dir: Path, name: str, mtime: float) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / f"{name}.jsonl"
    p.write_text('{"type":"user"}\n', encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def _encode_cwd(cwd: str) -> str:
    """Mirror session_end.py's `_resolve_session_jsonl_path` encoder exactly."""
    encoded = cwd.replace(os.sep, "-")
    if os.altsep:
        encoded = encoded.replace(os.altsep, "-")
    return encoded


def test_decodes_dashed_dir_back_to_cwd():
    encoded = "-Users-xyrlan-github-meunu"
    assert discover.decode_project_dir(encoding=encoded) == "/Users/xyrlan/github/meunu"


def test_decodes_windows_drive_letter_without_leading_slash():
    # session_end.py's encoder replaces os.sep/os.altsep with "-" but leaves
    # the drive letter's colon untouched, so "C:\Users\me\repo" -> this.
    encoded = "C:-Users-me-repo"
    assert discover.decode_project_dir(encoding=encoded) == "C:/Users/me/repo"


def test_finds_transcripts_and_ranks_newest_first(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "old", 1000.0)
    _session(d, "new", 2000.0)

    found = discover.find_transcripts()
    names = [t.path.stem for t in found]
    assert names == ["new", "old"]


def test_limit_takes_the_newest(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "a", 1000.0)
    _session(d, "b", 2000.0)
    _session(d, "c", 3000.0)

    found = discover.find_transcripts(limit=2)
    assert [t.path.stem for t in found] == ["c", "b"]


def test_project_filter_excludes_other_repos(projects_root, monkeypatch):
    monkeypatch.setattr(discover, "_agent_for_cwd", lambda cwd: Path(cwd).name)
    _session(projects_root / "-tmp-alpha", "a1", 1000.0)
    _session(projects_root / "-tmp-beta", "b1", 2000.0)

    found = discover.find_transcripts(project="alpha")
    assert [t.path.stem for t in found] == ["a1"]
    assert all(t.agent == "alpha" for t in found)


def test_resolves_real_repo_through_agent_module(projects_root):
    # Exercise the real `_agent_for_cwd` -> `resolve_canonical_agent` path
    # against an actual matching directory, so the integration (including
    # the `except Exception` wrapper around it) is genuinely covered rather
    # than always falling into `_fallback_agent` or a full monkeypatch.
    #
    # decode_project_dir is a lossy, naive dash-split: it only round-trips
    # exactly when no path segment contains a literal dash. pytest's own
    # tmp_path base commonly does (e.g. "pytest-of-<user>/pytest-3"), so the
    # repo is built under a dedicated, dash-free temp dir via tempfile
    # instead of tmp_path, keeping the round trip honest rather than
    # coincidentally broken by the test harness.
    if os.name == "nt":
        pytest.skip(
            "the round trip cannot be staged on Windows: a real cwd there starts "
            "with a drive letter, so the encoded directory name contains a colon "
            "— which NTFS does not allow in a filename. How Claude Code actually "
            "names those directories on Windows is unverified, and guessing it "
            "into decode_project_dir would bake the guess into production."
        )

    base = Path(tempfile.mkdtemp(prefix="mnemodiscovertest"))
    try:
        repo = base / "realrepo"
        (repo / ".git").mkdir(parents=True)

        encoded = _encode_cwd(str(repo))
        _session(projects_root / encoded, "s1", 1000.0)

        found = discover.find_transcripts()

        assert [t.path.stem for t in found] == ["s1"]
        assert found[0].agent == "realrepo"
        assert found[0].cwd == str(repo)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_missing_projects_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nowhere"))
    assert discover.find_transcripts() == []


def test_non_jsonl_files_are_ignored(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "real", 1000.0)
    d.joinpath("notes.txt").write_text("hi", encoding="utf-8")

    found = discover.find_transcripts()
    assert [t.path.stem for t in found] == ["real"]


# --- the cwd a transcript records (#301) -----------------------------------
#
# Claude Code's directory name cannot be decoded: `repo-wt-200` and
# `repo/wt/200` encode identically. Every dispatch child works in a dashed
# `<repo>-wt-<n>` tree, so on the real vault the decode found 49 of 121
# `mnemo` transcripts under agents like `200`, and `learn` — which names the
# project from the cwd an unblock marker recorded — matched none of the 27
# pending markers. These tests stage the dashes the old ones avoided.


def _recorded(project_dir: Path, name: str, cwd: str, mtime: float = 1000.0) -> Path:
    """A transcript shaped like Claude Code's: the cwd rides a later event."""
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / f"{name}.jsonl"
    events = [
        {"type": "queue-operation", "operation": "enqueue"},
        {"type": "user", "cwd": cwd, "sessionId": name,
         "message": {"role": "user", "content": "hi"}},
    ]
    p.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def _encode_like_claude(cwd: str) -> str:
    """Claude Code's real encoder: every non-alphanumeric becomes a dash."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def _worktree(repo: Path, tree: Path) -> None:
    """A git worktree on disk: `.git` file -> gitdir -> commondir -> repo."""
    gitdir = repo / ".git" / "worktrees" / tree.name
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    tree.mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")


def test_a_dashed_worktree_resolves_to_its_repo(projects_root, tmp_path):
    repo = tmp_path / "gh" / "repo"
    (repo / ".git").mkdir(parents=True)
    tree = tmp_path / "gh" / "repo-wt-c-briefing-query"
    _worktree(repo, tree)
    _recorded(projects_root / _encode_like_claude(str(tree)), "s1", str(tree))

    # The decode on its own still tears the name apart — that is the bug.
    assert not Path(discover.decode_project_dir(
        encoding=_encode_like_claude(str(tree)))).exists()

    [t] = discover.find_transcripts()
    assert t.cwd == str(tree)
    assert Path(t.cwd).is_dir()
    assert t.agent == "repo"
    assert [x.path.stem for x in discover.find_transcripts(project="repo")] == ["s1"]


def test_a_deleted_dispatch_worktree_still_files_under_its_repo(projects_root, tmp_path):
    repo = tmp_path / "gh" / "repo"
    (repo / ".git").mkdir(parents=True)
    gone = tmp_path / "gh" / "repo-wt-200"
    _recorded(projects_root / _encode_like_claude(str(gone)), "s1", str(gone))

    [t] = discover.find_transcripts()
    assert t.agent == "repo"
    assert discover.agent_for_cwd(str(gone)) == "repo"


def test_a_gone_directory_dispatch_did_not_name_is_not_folded(projects_root, tmp_path):
    """`repo-old` and a hand-made `repo-wt-feature` are their own scopes: only
    the two shapes dispatch writes carry the repo in their name."""
    repo = tmp_path / "gh" / "repo"
    (repo / ".git").mkdir(parents=True)
    for name in ("repo-old", "repo-wt-feature"):
        gone = tmp_path / "gh" / name
        _recorded(projects_root / _encode_like_claude(str(gone)), name, str(gone))

    agents = {t.path.stem: t.agent for t in discover.find_transcripts()}
    assert agents == {"repo-old": "repo-old", "repo-wt-feature": "repo-wt-feature"}


def test_a_dispatch_worktree_whose_repo_is_gone_too_keeps_its_own_name(projects_root, tmp_path):
    gone = tmp_path / "gh" / "repo-wt-200"
    _recorded(projects_root / _encode_like_claude(str(gone)), "s1", str(gone))

    [t] = discover.find_transcripts()
    assert t.agent == "repo-wt-200"


def test_a_transcript_recording_no_cwd_falls_back_to_the_decode(projects_root):
    _session(projects_root / "-tmp-repo-alpha", "s1", 1000.0)

    [t] = discover.find_transcripts()
    assert t.cwd == "/tmp/repo/alpha"
    assert t.agent == "alpha"
