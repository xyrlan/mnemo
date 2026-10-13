"""PreToolUse sends an auto-memory write to the file's real path (#637).

A session started with ``CLAUDE_CONFIG_DIR=~/.claude-<slug>`` whose
``projects`` entry links to ``~/.claude/projects`` is told to keep its memory
under the account dir, and Claude Code prompts for every write spelled that
way. The hook names the same file by its real path and nothing else: a Write
that creates the file is rewritten, an edit of one that exists is denied with
the real path in the reason (Claude Code refuses a rewritten edit as not read
yet). One group of tests per limit the issue sets. Every directory is built in
``tmp_path``; the link is a real one, so a test skips where the platform
refuses it.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from mnemo.core import memory_path


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """``(account, real_projects)``: an account dir whose ``projects`` is a
    link into a default-style dir, the layout the desktop app's accounts get."""
    default = tmp_path / "home" / ".claude"
    real_projects = default / "projects"
    (real_projects / "-proj" / "memory").mkdir(parents=True)
    account = tmp_path / "home" / ".claude-account"
    account.mkdir()
    try:
        os.symlink(str(real_projects), str(account / "projects"), target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip("creating a symlink needs a privilege this platform withholds: %r" % exc)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    monkeypatch.delenv("MNEMO_HOOKS_OFF", raising=False)
    # Symlinks in tmp_path's own prefix (/var -> /private/var on macOS) are
    # not the link under test: hand back the projects dir as realpath names it.
    return account, Path(os.path.realpath(str(real_projects)))


def _run(monkeypatch, tool_input, *, tool="Write", cfg=None, cwd=""):
    payload = {"tool_name": tool, "tool_input": tool_input, "cwd": cwd,
               "session_id": "s1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    if cfg is not None:
        monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    from mnemo.hooks.pre_tool_use import main

    assert main() == 0
    return buf.getvalue()


def _untouched(monkeypatch, asked) -> bool:
    """Neither a rewrite nor a redirect, for a creating Write and for an Edit."""
    write = _run(monkeypatch, {"file_path": str(asked), "content": "x"})
    edit = _run(monkeypatch, {"file_path": str(asked), "old_string": "a", "new_string": "b"},
                tool="Edit")
    return write == "" and edit == ""


def _memory(real_projects: Path, name: str = "MEMORY.md", text: str = "- a\n") -> Path:
    path = real_projects / "-proj" / "memory" / name
    path.write_text(text, encoding="utf-8")
    return path


# --- limit 1: the same file under its other name, nothing else ----------------

@pytest.mark.parametrize("name", ["MEMORY.md", "feedback-topic.md"])
def test_account_dir_memory_write_is_rewritten_to_its_real_path(dirs, monkeypatch, name):
    account, real_projects = dirs
    asked = account / "projects" / "-proj" / "memory" / name
    out = _run(monkeypatch, {"file_path": str(asked), "content": "- a rule\n"})
    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["hookEventName"] == "PreToolUse"
    updated = hook["updatedInput"]
    assert Path(updated.pop("file_path")) == real_projects / "-proj" / "memory" / name
    assert updated == {"content": "- a rule\n"}


@pytest.mark.parametrize("tool,tool_input", [
    ("Edit", {"old_string": "a", "new_string": "b", "replace_all": True}),
    ("MultiEdit", {"edits": [{"old_string": "a", "new_string": "b"}]}),
    ("Write", {"content": "- b\n"}),
])
def test_a_change_to_a_file_that_exists_is_sent_to_its_real_path(
        dirs, monkeypatch, tool, tool_input):
    """Not rewritten: Claude Code checks "was it read" on the path the model
    asked for and again on the rewritten one, and no Read satisfies both."""
    account, real_projects = dirs
    real = _memory(real_projects)
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    out = _run(monkeypatch, dict(tool_input, file_path=str(asked)), tool=tool)
    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    assert "updatedInput" not in hook
    assert str(real) in hook["permissionDecisionReason"]
    assert str(asked) in hook["permissionDecisionReason"]
    assert real.read_text(encoding="utf-8") == "- a\n"


def test_the_redirect_names_only_the_two_spellings_of_one_file(dirs):
    account, real_projects = dirs
    asked = str(account / "projects" / "-proj" / "memory" / "MEMORY.md")
    real = memory_path.real_memory_path(asked, str(account))
    reason = memory_path.redirect_reason(asked, real)
    assert Path(real) == real_projects / "-proj" / "memory" / "MEMORY.md"
    assert reason.count(real) == 1 and reason.count(asked) == 1


def test_a_file_in_a_memory_subdirectory_is_the_same_file_too(dirs):
    account, real_projects = dirs
    asked = account / "projects" / "-proj" / "memory" / "topics" / "a.md"
    got = memory_path.real_memory_path(str(asked), str(account))
    assert Path(got) == real_projects / "-proj" / "memory" / "topics" / "a.md"


# --- limit 2: only auto-memory markdown ---------------------------------------

def test_the_default_dir_rewrites_nothing(dirs, monkeypatch):
    _account, real_projects = dirs
    asked = real_projects / "-proj" / "memory" / "MEMORY.md"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(real_projects.parent))
    assert _untouched(monkeypatch, asked)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR")
    assert _untouched(monkeypatch, asked)


def test_an_account_dir_with_projects_of_its_own_rewrites_nothing(tmp_path, monkeypatch):
    account = tmp_path / ".claude-account"
    (account / "projects" / "-proj" / "memory").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    assert _untouched(monkeypatch, asked)


@pytest.mark.parametrize("tail", [
    ("-proj", "memory", "notes.txt"),          # not markdown
    ("-proj", "memory", "state.json"),
    ("-proj", "notes.md"),                     # markdown, not in memory/
    ("-proj", "abc.jsonl"),                    # the transcript itself
    ("-proj", "src", "memory", "a.md"),        # merely contains /memory/
    ("memory", "a.md"),                        # no project above it
    ("-proj", "memory"),                       # the directory, not a file
])
def test_anything_but_memory_markdown_is_left_alone(dirs, monkeypatch, tail):
    account, _real = dirs
    asked = account.joinpath("projects", *tail)
    assert _untouched(monkeypatch, asked)


def test_a_memory_path_outside_the_config_dir_is_left_alone(dirs, tmp_path, monkeypatch):
    _account, real_projects = dirs
    other = tmp_path / "elsewhere"
    other.mkdir()
    os.symlink(str(real_projects), str(other / "projects"), target_is_directory=True)
    asked = other / "projects" / "-proj" / "memory" / "MEMORY.md"
    assert _untouched(monkeypatch, asked)


def test_dot_dot_out_of_the_memory_dir_is_left_alone(dirs, monkeypatch):
    account, _real = dirs
    asked = str(account / "projects" / "-proj" / "memory") + os.sep + os.path.join("..", "x.md")
    assert _untouched(monkeypatch, asked)


def test_a_link_below_projects_is_not_followed(dirs, tmp_path, monkeypatch):
    """Only the link that makes the two spellings differ is resolved: a memory
    file that is itself a link elsewhere is somebody else's file."""
    account, real_projects = dirs
    secret = tmp_path / "secret.md"
    secret.write_text("x", encoding="utf-8")
    os.symlink(str(secret), str(real_projects / "-proj" / "memory" / "linked.md"))
    asked = account / "projects" / "-proj" / "memory" / "linked.md"
    assert _untouched(monkeypatch, asked)
    assert secret.read_text(encoding="utf-8") == "x"


def test_a_read_is_left_alone(dirs, monkeypatch):
    """Claude Code reads the account spelling without asking, and a rewritten
    Read would not make the Edit after it pass (see the redirect above)."""
    account, real_projects = dirs
    _memory(real_projects)
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    assert _run(monkeypatch, {"file_path": str(asked)}, tool="Read") == ""


@pytest.mark.parametrize("tool,tool_input", [
    ("Bash", {"command": "cat MEMORY.md", "file_path": "PLACEHOLDER"}),
    ("NotebookEdit", {"notebook_path": "PLACEHOLDER", "file_path": "PLACEHOLDER"}),
    ("Grep", {"pattern": "x", "path": "PLACEHOLDER", "file_path": "PLACEHOLDER"}),
])
def test_no_other_tool_is_touched(dirs, monkeypatch, tool, tool_input):
    account, _real = dirs
    asked = str(account / "projects" / "-proj" / "memory" / "MEMORY.md")
    tool_input = {k: (asked if v == "PLACEHOLDER" else v) for k, v in tool_input.items()}
    assert _run(monkeypatch, tool_input, tool=tool) == ""


# --- limit 3: the hook does not become what approves the write ----------------

def test_the_rewrite_carries_no_permission_decision(dirs, monkeypatch):
    account, _real = dirs
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    hook = json.loads(_run(monkeypatch, {"file_path": str(asked), "content": "x"}))[
        "hookSpecificOutput"]
    assert sorted(hook) == ["hookEventName", "updatedInput"]


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "Read"])
@pytest.mark.parametrize("exists", [True, False])
def test_no_call_is_ever_allowed_or_asked(dirs, monkeypatch, tool, exists):
    account, real_projects = dirs
    if exists:
        _memory(real_projects)
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    out = _run(monkeypatch, {"file_path": str(asked), "content": "x"}, tool=tool)
    decision = json.loads(out)["hookSpecificOutput"].get("permissionDecision") if out else None
    assert decision in (None, "deny")


def test_a_rule_that_enriches_the_write_shares_the_envelope(dirs, tmp_vault, monkeypatch):
    """One hook prints one envelope: the rewrite must not cost a memory write
    the rule body an ``activates_on`` glob attaches to it, nor the reverse."""
    from mnemo.core.rule_activation import build_index, write_index
    from tests.unit.test_hook_pre_tool_use import _cfg, _write_enrich_rule

    account, real_projects = dirs
    project_dir = tmp_vault / "bots" / "mnemo"
    project_dir.mkdir(parents=True)
    (project_dir / ".git").mkdir()
    # Globs are matched relative to the git root enclosing the file (#271).
    (real_projects / "-proj" / ".git").mkdir()
    _write_enrich_rule(tmp_vault, "memory-style.md", path_glob="memory/MEMORY.md",
                       tools="Write", body="One line per memory.")
    write_index(tmp_vault, build_index(tmp_vault))
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    out = _run(monkeypatch, {"file_path": str(asked), "content": "x"},
               cfg=_cfg(tmp_vault, enr=True), cwd=str(project_dir))
    hook = json.loads(out)["hookSpecificOutput"]
    assert "One line per memory." in hook["additionalContext"]
    assert Path(hook["updatedInput"]["file_path"]) == (
        real_projects / "-proj" / "memory" / "MEMORY.md")
    assert "permissionDecision" not in hook


# --- limit 4: fails open ------------------------------------------------------

def test_a_broken_projects_link_leaves_the_input_alone(tmp_path, monkeypatch):
    account = tmp_path / ".claude-account"
    account.mkdir()
    try:
        os.symlink(str(tmp_path / "gone" / "projects"), str(account / "projects"),
                   target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip("creating a symlink needs a privilege this platform withholds: %r" % exc)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    assert _untouched(monkeypatch, asked)


def test_a_missing_config_dir_leaves_the_input_alone(tmp_path, monkeypatch):
    account = tmp_path / "never-made"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(account))
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    assert _untouched(monkeypatch, asked)


def test_an_error_while_resolving_leaves_the_input_alone(dirs, monkeypatch):
    account, _real = dirs
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"

    def boom(*_a, **_k):
        raise OSError("unreadable link")

    monkeypatch.setattr(memory_path.os.path, "realpath", boom)
    assert _untouched(monkeypatch, asked)


@pytest.mark.parametrize("tool_input", [None, [], {"file_path": 7}, {"file_path": ""}, {}])
def test_a_malformed_input_leaves_the_input_alone(dirs, monkeypatch, tool_input):
    assert _run(monkeypatch, tool_input) == ""


def test_mnemos_own_helper_sessions_are_not_touched(dirs, monkeypatch):
    account, _real = dirs
    monkeypatch.setenv("MNEMO_HOOKS_OFF", "1")
    asked = account / "projects" / "-proj" / "memory" / "MEMORY.md"
    assert _untouched(monkeypatch, asked)


def test_a_session_in_a_temp_dir_is_served_too(dirs, tmp_path, monkeypatch):
    """The throwaway gate (#420) keeps a probe out of the vault; this reads no
    vault, and a probe's memory write stops for the same prompt."""
    account, _real = dirs
    asked = account / "projects" / "-proj" / "memory" / "new-topic.md"
    out = _run(monkeypatch, {"file_path": str(asked), "content": "x"}, cwd=str(tmp_path))
    assert "updatedInput" in json.loads(out)["hookSpecificOutput"]
