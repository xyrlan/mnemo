from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core.grok import paths


@pytest.fixture
def tmp_path(short_tmp_path: Path) -> Path:
    """Short on Windows: these tests nest a percent-encoded cwd under it (#729)."""
    return short_tmp_path


def test_home_is_grok_home_env_when_set(tmp_path):
    assert paths.grok_home({"GROK_HOME": str(tmp_path)}) == tmp_path


def test_home_defaults_to_dot_grok(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.grok_home({}) == tmp_path / ".grok"


def test_homes_lists_each_account_once(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    for name in (".grok", ".grok-work", ".grok-free"):
        (tmp_path / name).mkdir()
    (tmp_path / ".grok-not-a-dir").write_text("", encoding="utf-8")
    homes = paths.grok_homes({"GROK_HOME": str(tmp_path / ".grok")})
    assert homes == [tmp_path / ".grok", tmp_path / ".grok-free", tmp_path / ".grok-work"]


def test_encode_matches_grok_dirname_seen_live():
    # The live probe's session directory name, 2026-10-16.
    cwd = "/private/tmp/claude-501/p/scratchpad/grok-probe-repo"
    assert paths.encode_cwd(cwd) == "%2Fprivate%2Ftmp%2Fclaude-501%2Fp%2Fscratchpad%2Fgrok-probe-repo"


def test_long_cwd_has_no_computable_name():
    assert paths.encode_cwd("/" + "a" * 300) is None


def test_session_dir_short_form(tmp_path):
    cwd = str(tmp_path / "repo")
    d = tmp_path / "home" / "sessions" / paths.encode_cwd(cwd) / "01a144a5-7369-7b90-a7b7-41d16a299a5a"
    d.mkdir(parents=True)
    found = paths.session_dir(cwd, "01a144a5-7369-7b90-a7b7-41d16a299a5a", tmp_path / "home")
    assert found == d
    assert paths.chat_history_path(found) == d / "chat_history.jsonl"


def test_session_dir_long_form_reads_cwd_file(tmp_path):
    cwd = "/" + "deep/" * 70 + "repo"
    parent = tmp_path / "home" / "sessions" / "repo-0123456789abcdef"
    (parent / "sid-1").mkdir(parents=True)
    (parent / ".cwd").write_text(cwd, encoding="utf-8")
    assert paths.session_dir(cwd, "sid-1", tmp_path / "home") == parent / "sid-1"
    assert paths.decode_cwd_dir(parent) == cwd


def test_find_session_dir_without_cwd(tmp_path):
    d = tmp_path / "home" / "sessions" / "x" / "sid-9"
    d.mkdir(parents=True)
    assert paths.find_session_dir("sid-9", tmp_path / "home") == d
    assert paths.find_session_dir("nope", tmp_path / "home") is None


def test_decode_short_form(tmp_path):
    cwd = str(tmp_path / "repo")
    d = tmp_path / paths.encode_cwd(cwd)
    d.mkdir()
    assert paths.decode_cwd_dir(d) == cwd


def test_short_id_is_last_eight_hex_digits():
    # Two ids from one probe minute share their leading timestamp digits.
    a = "01a14492-e20e-7381-a095-cdd17069b3f7"
    b = "01a144a5-7369-7b90-a7b7-41d16a299a5a"
    assert paths.short_id(a) == "7069b3f7"
    assert paths.short_id(b) == "6a299a5a"
    assert paths.short_id(a) != paths.short_id(b)


def test_leader_socket_env_wins(tmp_path):
    assert paths.leader_socket(tmp_path, {"GROK_LEADER_SOCKET": "/tmp/s.sock"}) == "/tmp/s.sock"
    assert Path(paths.leader_socket(tmp_path, {})) == tmp_path / "leader.sock"


def test_homes_dedupes_symlinked_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / ".grok").mkdir()
    link = tmp_path / "alias"
    try:
        os.symlink(tmp_path / ".grok", link)
    except (OSError, NotImplementedError):
        return
    assert paths.grok_homes({"GROK_HOME": str(link)}) == [link]
