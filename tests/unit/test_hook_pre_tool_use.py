"""Unit tests for mnemo.hooks.pre_tool_use.

Pattern: feed JSON via a StringIO stdin mock, capture stdout with capsys,
invoke main(), assert envelope + exit code.

Project name derivation: resolve_agent walks up from payload["cwd"] looking
for a .git directory. We create a fake git root inside tmp_vault/bots/<name>/
so the project name is predictable (<name> sanitized).
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.core.rule_activation import build_index, write_index


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_enforce_rule(
    vault: Path,
    filename: str,
    *,
    project: str = "mnemo",
    deny_pattern: str = "git commit.*Co-Authored-By",
    reason: str = "No co-authored-by in commits",
) -> Path:
    """Write a minimal enforce rule under shared/feedback/ for *project*."""
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    content = (
        "---\n"
        f"name: {filename.replace('.md', '')}\n"
        "stability: stable\n"
        "tags:\n"
        "  - auto-promoted\n"
        "sources:\n"
        f"  - bots/{project}/memory/{filename}\n"
        "enforce:\n"
        "  tool: Bash\n"
        f"  deny_pattern: \"{deny_pattern}\"\n"
        f"  reason: \"{reason}\"\n"
        "---\n\n"
        "**Why:** This enforces a policy.\n"
    )
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def _write_enrich_rule(
    vault: Path,
    filename: str,
    *,
    project: str = "mnemo",
    path_glob: str = "src/foo/user-modal.tsx",
    tools: str = "Edit",
    body: str = "Always add a11y attributes to modal components.",
) -> Path:
    """Write a minimal enrich rule under shared/feedback/ for *project*."""
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    slug = filename.replace(".md", "")
    content = (
        "---\n"
        f"name: {slug}\n"
        f"slug: {slug}\n"
        "stability: stable\n"
        "tags:\n"
        "  - auto-promoted\n"
        "sources:\n"
        f"  - bots/{project}/memory/{filename}\n"
        "activates_on:\n"
        f"  tools: [{tools}]\n"
        f"  path_globs: [\"{path_glob}\"]\n"
        "---\n\n"
        f"{body}\n"
    )
    path = target_dir / filename
    path.write_text(content, encoding="utf-8")
    return path


def _make_git_project(vault: Path, project: str) -> Path:
    """Create vault/bots/<project>/ with a .git dir so resolve_agent returns it."""
    project_dir = vault / "bots" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".git").mkdir()
    return project_dir


def _cfg(vault: Path, *, enf: bool = False, enr: bool = False) -> dict:
    return {
        "vaultRoot": str(vault),
        "enforcement": {"enabled": enf},
        "enrichment": {"enabled": enr},
    }


def _run_hook(monkeypatch, payload: dict, cfg: dict) -> tuple[int, str]:
    """Inject stdin + config and call main(). Returns (exit_code, stdout)."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)

    from mnemo.hooks.pre_tool_use import main
    from io import StringIO

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = main()
    return rc, buf.getvalue()


# ---------------------------------------------------------------------------
# Test 1: deny envelope on matching Bash command
# ---------------------------------------------------------------------------


def test_hook_denies_matching_bash_command_emits_deny_envelope(
    tmp_vault: Path, monkeypatch
):
    project = "mnemo"
    _write_enforce_rule(
        tmp_vault, "no-coauthored.md",
        project=project,
        deny_pattern="git commit.*Co-Authored-By",
        reason="No co-authored-by in commits",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m 'fix' --Co-Authored-By: me"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    assert rc == 0
    assert out, "Expected deny envelope on stdout"
    data = json.loads(out)
    hook_out = data["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert hook_out["permissionDecision"] == "deny"
    # reason starts with the rule's reason text; may include path + fix hint suffix
    assert hook_out["permissionDecisionReason"].startswith("No co-authored-by in commits")


# ---------------------------------------------------------------------------
# Test 2: enrich envelope on matching Edit
# ---------------------------------------------------------------------------


def test_hook_enriches_matching_edit_emits_context_envelope(
    tmp_vault: Path, monkeypatch
):
    project = "mnemo"
    _write_enrich_rule(
        tmp_vault, "modal-a11y.md",
        project=project,
        path_glob="src/foo/user-modal.tsx",
        tools="Edit",
        body="Always add a11y attributes to modal components.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    # Claude Code sends an absolute file_path; the glob is repo-relative.
    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(project_dir / "src" / "foo" / "user-modal.tsx")},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert out, "Expected enrich envelope on stdout"
    data = json.loads(out)
    hook_out = data["hookSpecificOutput"]
    assert hook_out["hookEventName"] == "PreToolUse"
    assert "additionalContext" in hook_out
    ctx = hook_out["additionalContext"]
    assert "modal-a11y" in ctx
    assert "a11y attributes" in ctx


# ---------------------------------------------------------------------------
# Test 3: tool outside v1 set → silent
# ---------------------------------------------------------------------------


def test_hook_ignores_tool_outside_v1_set(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    _write_enforce_rule(tmp_vault, "some-rule.md", project=project)
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Grep",
        "tool_input": {"file_path": "src/foo.py"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True, enr=True))

    assert rc == 0
    assert out == ""


# ---------------------------------------------------------------------------
# Test 4: both flags disabled → short-circuit, load_index NOT called
# ---------------------------------------------------------------------------


def test_hook_short_circuits_when_both_flags_disabled(
    tmp_vault: Path, monkeypatch
):
    project = "mnemo"
    _write_enforce_rule(tmp_vault, "some-rule.md", project=project)
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    load_index_called = []

    def _fake_load_index(vault):
        load_index_called.append(True)
        from mnemo.core.rule_activation import load_index as real_load
        return real_load(vault)

    monkeypatch.setattr("mnemo.core.rule_activation.load_index", _fake_load_index)

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m msg"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=False, enr=False))

    assert rc == 0
    assert out == ""
    assert not load_index_called, "load_index should NOT be called when both flags are disabled"


# ---------------------------------------------------------------------------
# Test 5: missing index → fail open
# ---------------------------------------------------------------------------


def test_hook_fails_open_on_missing_index(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    project_dir = _make_git_project(tmp_vault, project)
    # Do NOT write any index — the .mnemo dir doesn't exist

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push --force"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    assert rc == 0
    assert out == ""


# ---------------------------------------------------------------------------
# Test 6: corrupt index → fail open
# ---------------------------------------------------------------------------


def test_hook_fails_open_on_corrupt_index(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    project_dir = _make_git_project(tmp_vault, project)
    mnemo_dir = tmp_vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "rule-activation-index.json").write_text("NOT VALID JSON }{", encoding="utf-8")

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git push --force"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    assert rc == 0
    assert out == ""


# ---------------------------------------------------------------------------
# Test 7: internal exception → fail open + logs to .errors.log
# ---------------------------------------------------------------------------


def test_hook_fails_open_on_internal_exception(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    _write_enforce_rule(tmp_vault, "some-rule.md", project=project)
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    def _boom(index, proj, cmd):
        raise RuntimeError("synthetic failure in match_bash_enforce")

    monkeypatch.setattr("mnemo.core.rule_activation.match_bash_enforce", _boom)

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m msg"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    assert rc == 0
    assert out == ""

    # Error must have been logged
    errors_log = tmp_vault / ".errors.log"
    assert errors_log.exists(), ".errors.log should have been written"
    log_text = errors_log.read_text(encoding="utf-8")
    assert "pre_tool_use" in log_text


# ---------------------------------------------------------------------------
# Test 8: circuit breaker respected → load_index NOT called
# ---------------------------------------------------------------------------


def test_hook_respects_circuit_breaker(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    project_dir = _make_git_project(tmp_vault, project)

    # Saturate the error budget (>10 strikes in the last hour; a strike is a
    # distinct (where, kind) per minute, #314)
    from mnemo.core import errors as err_mod
    for i in range(11):
        try:
            raise ValueError(f"err{i}")
        except ValueError as e:
            err_mod.log_error(tmp_vault, f"test{i}", e)

    load_index_called = []

    def _fake_load_index(vault):
        load_index_called.append(True)
        from mnemo.core.rule_activation import load_index as real_load
        return real_load(vault)

    monkeypatch.setattr("mnemo.core.rule_activation.load_index", _fake_load_index)

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m msg"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    assert rc == 0
    assert out == ""
    assert not load_index_called, "load_index should NOT be called when circuit breaker is open"


# ---------------------------------------------------------------------------
# Test 9: enforcement takes precedence — Bash never triggers enrich path
# ---------------------------------------------------------------------------


def test_hook_enforcement_takes_precedence_over_enrichment(
    tmp_vault: Path, monkeypatch
):
    """On a Bash call, deny fires and enrich path is never taken."""
    project = "mnemo"
    _write_enforce_rule(
        tmp_vault, "no-coauthored.md",
        project=project,
        deny_pattern="git commit.*Co-Authored-By",
        reason="No co-authored-by",
    )
    # Also write an enrich rule (shouldn't matter for Bash, but verifies no bleed)
    _write_enrich_rule(
        tmp_vault, "modal-a11y.md",
        project=project,
        path_glob="**/*.tsx",
        tools="Edit",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m 'x' Co-Authored-By: me"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True, enr=True))

    assert rc == 0
    assert out, "Expected deny envelope on stdout"
    data = json.loads(out)
    hook_out = data["hookSpecificOutput"]
    assert hook_out["permissionDecision"] == "deny"
    assert "additionalContext" not in hook_out


# ---------------------------------------------------------------------------
# Test 10: logs denial to denial-log.jsonl
# ---------------------------------------------------------------------------


def test_hook_logs_denial_to_jsonl(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    _write_enforce_rule(
        tmp_vault, "no-force-push.md",
        project=project,
        deny_pattern="git push.*--force",
        reason="No force pushes",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    command = "git push --force origin main"
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(project_dir),
    }
    _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    denial_log = tmp_vault / ".mnemo" / "denial-log.jsonl"
    assert denial_log.exists(), "denial-log.jsonl should exist"
    lines = denial_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["slug"] == "no-force-push"
    assert entry["project"] == project
    assert entry["reason"] == "No force pushes"
    assert entry["tool"] == "Bash"
    assert "force" in entry["command"]
    assert "timestamp" in entry


# ---------------------------------------------------------------------------
# Test 11: logs enrichment to enrichment-log.jsonl
# ---------------------------------------------------------------------------


def test_hook_logs_enrichment_to_jsonl(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    _write_enrich_rule(
        tmp_vault, "modal-a11y.md",
        project=project,
        path_glob="**/components/dialog-modal.tsx",
        tools="Edit",
        body="Add a11y to modals.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(project_dir / "src" / "components" / "dialog-modal.tsx")},
        "cwd": str(project_dir),
    }
    _run_hook(monkeypatch, payload, _cfg(tmp_vault, enr=True))

    enrich_log = tmp_vault / ".mnemo" / "enrichment-log.jsonl"
    assert enrich_log.exists(), "enrichment-log.jsonl should exist"
    lines = enrich_log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["project"] == project
    assert "modal-a11y" in entry["hit_slugs"]
    assert "timestamp" in entry


# ---------------------------------------------------------------------------
# Test 12: Edit with missing file_path → silent
# ---------------------------------------------------------------------------


def test_hook_ignores_edit_when_file_path_missing(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    _write_enrich_rule(tmp_vault, "modal-a11y.md", project=project)
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Edit",
        "tool_input": {},  # no file_path
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert out == ""


# ---------------------------------------------------------------------------
# Test 13: malformed stdin → exit 0, no output
# ---------------------------------------------------------------------------


def test_hook_returns_zero_on_malformed_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("NOT JSON }{"))

    from mnemo.hooks.pre_tool_use import main
    from io import StringIO

    buf = StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    rc = main()

    assert rc == 0
    assert buf.getvalue() == ""


# ---------------------------------------------------------------------------
# Bonus: Write and MultiEdit also trigger enrich
# ---------------------------------------------------------------------------


def test_hook_enriches_write_tool(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    _write_enrich_rule(
        tmp_vault, "ts-config.md",
        project=project,
        path_glob="src/utils/helper.ts",
        tools="Write",
        body="TypeScript config rule.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(project_dir / "src" / "utils" / "helper.ts")},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert out
    data = json.loads(out)
    assert "additionalContext" in data["hookSpecificOutput"]


def test_hook_enriches_multiedit_tool(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    _write_enrich_rule(
        tmp_vault, "ts-config-multi.md",
        project=project,
        path_glob="src/utils/types.ts",
        tools="MultiEdit",
        body="TypeScript multi-edit rule.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": str(project_dir / "src" / "utils" / "types.ts")},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert out
    data = json.loads(out)
    assert "additionalContext" in data["hookSpecificOutput"]


def test_hook_no_output_when_no_rules_match(tmp_vault: Path, monkeypatch):
    """Bash command that doesn't match any deny rule → silent."""
    project = "mnemo"
    _write_enforce_rule(
        tmp_vault, "no-force-push.md",
        project=project,
        deny_pattern="git push.*--force",
        reason="No force pushes",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git status"},  # doesn't match
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(tmp_vault, enf=True))

    assert rc == 0
    assert out == ""


# ---------------------------------------------------------------------------
# #271: a file's notes surface when a session opens it, once per session
# ---------------------------------------------------------------------------


def _file_note(tmp_vault: Path, *, glob: str = "src/core/agent.py", tools: str = "Edit") -> Path:
    _write_enrich_rule(
        tmp_vault, "agent-resolvers.md",
        project="mnemo",
        path_glob=glob,
        tools=tools,
        body="resolve_agent names a worktree; resolve_canonical_agent names the repo.",
    )
    project_dir = _make_git_project(tmp_vault, "mnemo")
    write_index(tmp_vault, build_index(tmp_vault))
    return project_dir


def _payload(tool: str, file_path: str, cwd: Path, sid: str = "sid-1") -> dict:
    return {
        "tool_name": tool,
        "tool_input": {"file_path": file_path},
        "cwd": str(cwd),
        "session_id": sid,
    }


def test_read_of_an_absolute_path_surfaces_the_files_note(tmp_vault: Path, monkeypatch):
    project_dir = _file_note(tmp_vault, tools="Edit")
    target = str(project_dir / "src" / "core" / "agent.py")

    rc, out = _run_hook(monkeypatch, _payload("Read", target, project_dir), _cfg(tmp_vault, enr=True))

    assert rc == 0
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "agent-resolvers" in ctx
    assert "resolve_canonical_agent" in ctx


def test_note_is_shown_once_per_session_not_once_per_day(tmp_vault: Path, monkeypatch):
    """A sibling session still sees the note the first session already got."""
    project_dir = _file_note(tmp_vault)
    target = str(project_dir / "src" / "core" / "agent.py")
    cfg = _cfg(tmp_vault, enr=True)

    _, first = _run_hook(monkeypatch, _payload("Read", target, project_dir, "sid-a"), cfg)
    _, again = _run_hook(monkeypatch, _payload("Edit", target, project_dir, "sid-a"), cfg)
    _, sibling = _run_hook(monkeypatch, _payload("Read", target, project_dir, "sid-b"), cfg)

    assert "agent-resolvers" in first
    assert again == ""
    assert "agent-resolvers" in sibling


def test_area_glob_stays_silent_on_a_file_inside_it(tmp_vault: Path, monkeypatch):
    project_dir = _file_note(tmp_vault, glob="src/core/**")
    target = str(project_dir / "src" / "core" / "agent.py")

    rc, out = _run_hook(monkeypatch, _payload("Edit", target, project_dir), _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert out == ""


def test_path_is_relative_to_the_worktree_root(tmp_vault: Path, monkeypatch, tmp_path: Path):
    """In a worktree `.git` is a file; the glob is relative to that tree's root."""
    project_dir = _file_note(tmp_vault)
    worktree = tmp_path / "mnemo-wt-271"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: /nowhere/.git/worktrees/wt\n", encoding="utf-8")
    target = str(worktree / "src" / "core" / "agent.py")

    # cwd stays the main checkout so the project resolves to "mnemo".
    rc, out = _run_hook(monkeypatch, _payload("Read", target, project_dir), _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert "agent-resolvers" in out


def test_symlinked_prefix_still_matches(tmp_vault: Path, monkeypatch, tmp_path: Path):
    project_dir = _file_note(tmp_vault)
    link = tmp_path / "linked-checkout"
    try:
        link.symlink_to(project_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    target = str(link / "src" / "core" / "agent.py")

    rc, out = _run_hook(monkeypatch, _payload("Read", target, project_dir), _cfg(tmp_vault, enr=True))

    assert rc == 0
    assert "agent-resolvers" in out


def test_file_outside_any_repo_is_silent(tmp_vault: Path, monkeypatch, tmp_path: Path):
    project_dir = _file_note(tmp_vault, glob="notes.md")
    outside = tmp_path / "loose"
    outside.mkdir()

    rc, out = _run_hook(
        monkeypatch, _payload("Read", str(outside / "notes.md"), project_dir), _cfg(tmp_vault, enr=True),
    )

    assert rc == 0
    assert out == ""


def test_hits_are_trimmed_to_the_room_left_under_the_session_cap(tmp_vault: Path, monkeypatch):
    from mnemo.core.mcp import session_state

    for name in ("a-note.md", "b-note.md", "c-note.md"):
        _write_enrich_rule(tmp_vault, name, project="mnemo", path_glob="prisma/schema.prisma")
    project_dir = _make_git_project(tmp_vault, "mnemo")
    write_index(tmp_vault, build_index(tmp_vault))
    session_state.record_enrichment(tmp_vault, sid="sid-cap", slugs=["x", "y"], now_ts=1)
    cfg = {**_cfg(tmp_vault, enr=True), "enrichment": {"enabled": True, "maxEmissionsPerSession": 3}}
    target = str(project_dir / "prisma" / "schema.prisma")

    _, out = _run_hook(monkeypatch, _payload("Read", target, project_dir, "sid-cap"), cfg)

    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert ctx.count("mnemo rule [[") == 1
    assert session_state.read_emission_counts(tmp_vault, "sid-cap")["enrich_count"] == 3
