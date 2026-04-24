"""Tests for enriched deny envelope: rule path + disable hint.

Uses the same _run_hook / tmp_vault fixture pattern as the sibling
test_hook_pre_tool_use.py — that convention is authoritative.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

from mnemo.core.rule_activation import build_index, write_index


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_hook_pre_tool_use.py)
# ---------------------------------------------------------------------------


def _make_git_project(vault: Path, project: str) -> Path:
    project_dir = vault / "bots" / project
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".git").mkdir(exist_ok=True)
    return project_dir


def _cfg(vault: Path, *, enf: bool = True, enr: bool = False) -> dict:
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
# Test: deny envelope contains rule path + disable hint
# ---------------------------------------------------------------------------


def test_deny_envelope_contains_rule_path_and_hint(monkeypatch, tmp_vault: Path):
    """When a Bash command is denied, the reason includes rule path + fix hint."""
    project = "demo"
    vault = tmp_vault

    # Write the rule under shared/feedback/
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    rule_path = target_dir / "no-curl-example-com.md"
    rule_path.write_text(
        "---\n"
        "name: Block curl example.com\n"
        "description: blocks curl to example.com\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/demo/memory/foo.md\n"
        "tags:\n"
        "  - demo\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  deny_pattern: 'curl .*example\\.com'\n"
        "  reason: 'no external fetch'\n"
        "---\n"
        "Body.\n"
    )

    project_dir = _make_git_project(vault, project)
    write_index(vault, build_index(vault))

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "curl https://example.com"},
        "cwd": str(project_dir),
    }
    rc, out = _run_hook(monkeypatch, payload, _cfg(vault))

    assert rc == 0
    assert out, "hook should have emitted a deny envelope"
    envelope = json.loads(out)
    reason = envelope["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no external fetch" in reason
    assert "no-curl-example-com.md" in reason
    assert "mnemo disable-rule" in reason or "edit the file" in reason.lower()
