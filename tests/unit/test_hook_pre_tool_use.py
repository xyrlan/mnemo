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
    path_glob: str = "**/*modal*.tsx",
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
    assert hook_out["permissionDecisionReason"] == "No co-authored-by in commits"


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
        path_glob="**/*modal*.tsx",
        tools="Edit",
        body="Always add a11y attributes to modal components.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/foo/user-modal.tsx"},
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
        "tool_name": "Read",
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
    (mnemo_dir / "rule-activation-index.json").write_text("NOT VALID JSON }{")

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
    log_text = errors_log.read_text()
    assert "pre_tool_use" in log_text


# ---------------------------------------------------------------------------
# Test 8: circuit breaker respected → load_index NOT called
# ---------------------------------------------------------------------------


def test_hook_respects_circuit_breaker(tmp_vault: Path, monkeypatch):
    project = "mnemo"
    project_dir = _make_git_project(tmp_vault, project)

    # Saturate the error budget (>10 errors in the last hour)
    from mnemo.core import errors as err_mod
    for i in range(11):
        try:
            raise ValueError(f"err{i}")
        except ValueError as e:
            err_mod.log_error(tmp_vault, "test", e)

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
        path_glob="**/*modal*.tsx",
        tools="Edit",
        body="Add a11y to modals.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Edit",
        "tool_input": {"file_path": "src/components/dialog-modal.tsx"},
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
        path_glob="**/*.ts",
        tools="Write",
        body="TypeScript config rule.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "src/utils/helper.ts"},
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
        path_glob="**/*.ts",
        tools="MultiEdit",
        body="TypeScript multi-edit rule.",
    )
    project_dir = _make_git_project(tmp_vault, project)
    write_index(tmp_vault, build_index(tmp_vault))

    payload = {
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": "src/utils/types.ts"},
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
