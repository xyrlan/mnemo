"""Integration tests for Task 6: SessionStart rule-activation index rebuild
and per-project topic filter.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.hooks import session_start
from mnemo.core import rule_activation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _set_config(vault: Path, **overrides):
    cfg_path = vault / "mnemo.config.json"
    base = {"vaultRoot": str(vault)}
    base.update(overrides)
    cfg_path.write_text(json.dumps(base))


def _write_feedback_page(
    vault: Path,
    slug: str,
    *,
    sources: list[str],
    tags: list[str],
    enforce_block: dict | None = None,
    activates_on_block: dict | None = None,
) -> Path:
    """Write a feedback page under shared/feedback/<slug>.md."""
    target_dir = vault / "shared" / "feedback"
    target_dir.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    tags_yaml = "\n".join(f"  - {t}" for t in tags)

    extra_yaml = ""
    if enforce_block:
        extra_yaml += "enforce:\n"
        for k, v in enforce_block.items():
            if isinstance(v, list):
                extra_yaml += f"  {k}:\n"
                for item in v:
                    extra_yaml += f"    - {item}\n"
            else:
                extra_yaml += f"  {k}: {v}\n"
    if activates_on_block:
        extra_yaml += "activates_on:\n"
        for k, v in activates_on_block.items():
            if isinstance(v, list):
                extra_yaml += f"  {k}:\n"
                for item in v:
                    extra_yaml += f"    - {item}\n"
            else:
                extra_yaml += f"  {k}: {v}\n"

    content = (
        "---\n"
        f"name: {slug}\n"
        f"description: test rule {slug}\n"
        "type: feedback\n"
        "stability: stable\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        f"{extra_yaml}"
        "---\n\nRule body.\n"
    )
    path = target_dir / f"{slug}.md"
    path.write_text(content)
    return path


def _make_project_repo(tmp_path: Path, name: str) -> Path:
    """Create a fake git repo dir named *name*."""
    repo = tmp_path / name
    (repo / ".git").mkdir(parents=True)
    return repo


def _run_hook(vault: Path, payload: dict, monkeypatch, capsys) -> tuple[int, str]:
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = session_start.main()
    captured = capsys.readouterr()
    return rc, captured.out


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


# ---------------------------------------------------------------------------
# Test 1: index rebuild when enforcement is enabled
# ---------------------------------------------------------------------------


def test_session_start_rebuilds_index_when_enforcement_enabled(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """With enforcement.enabled=True, session_start writes the activation index."""
    _write_feedback_page(
        hook_env, "no-force-push",
        sources=["bots/project-a/MEMORY.md"],
        tags=["auto-promoted", "git"],
        enforce_block={
            "tool": "Bash",
            "deny_pattern": "git push.*--force",
            "reason": "Never force-push to main.",
        },
    )
    _set_config(hook_env, enforcement={"enabled": True})

    repo = _make_project_repo(tmp_path, "project-a")
    rc, _ = _run_hook(hook_env, {"session_id": "S1", "cwd": str(repo)}, monkeypatch, capsys)

    assert rc == 0
    index_path = hook_env / ".mnemo" / "rule-activation-index.json"
    assert index_path.exists(), "rule-activation-index.json must be written"
    idx = json.loads(index_path.read_text())
    assert idx.get("schema_version") == 1
    assert "project-a" in idx["enforce_by_project"]


# ---------------------------------------------------------------------------
# Test 2: index rebuild when enrichment is enabled
# ---------------------------------------------------------------------------


def test_session_start_rebuilds_index_when_enrichment_enabled(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """With enrichment.enabled=True (only), session_start writes the activation index."""
    _write_feedback_page(
        hook_env, "react-best-practices",
        sources=["bots/project-a/MEMORY.md"],
        tags=["auto-promoted", "react"],
        activates_on_block={
            "tools": ["Edit", "Write"],
            "path_globs": ["**/*.tsx", "**/*.jsx"],
        },
    )
    _set_config(hook_env, enrichment={"enabled": True})

    repo = _make_project_repo(tmp_path, "project-a")
    rc, _ = _run_hook(hook_env, {"session_id": "S2", "cwd": str(repo)}, monkeypatch, capsys)

    assert rc == 0
    index_path = hook_env / ".mnemo" / "rule-activation-index.json"
    assert index_path.exists(), "rule-activation-index.json must be written"
    idx = json.loads(index_path.read_text())
    assert "project-a" in idx["enrich_by_project"]


# ---------------------------------------------------------------------------
# Test 3: no index rebuild when both flags are disabled
# ---------------------------------------------------------------------------


def test_session_start_skips_index_rebuild_when_both_flags_disabled(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """With both flags off, build_index must NOT be called."""
    # v0.5: enforcement defaults to True, so we must explicitly disable it
    # to exercise the "both flags off" path.
    _set_config(
        hook_env,
        enforcement={"enabled": False},
        enrichment={"enabled": False},
    )

    called = []

    def fail_loudly(vault):
        called.append(vault)
        raise AssertionError("build_index should NOT be called when flags are off")

    monkeypatch.setattr(rule_activation, "build_index", fail_loudly)

    repo = _make_project_repo(tmp_path, "myrepo")
    rc, _ = _run_hook(hook_env, {"session_id": "S3", "cwd": str(repo)}, monkeypatch, capsys)

    assert rc == 0
    assert called == [], "build_index was unexpectedly called"


# ---------------------------------------------------------------------------
# Test 4: topics filtered by current project (the regression test)
# ---------------------------------------------------------------------------


def test_session_start_filters_topics_by_project(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """Topics from project-b must NOT appear when the session is under project-a."""
    # project-a: react enrich rule
    _write_feedback_page(
        hook_env, "react-best-practices",
        sources=["bots/project-a/MEMORY.md"],
        tags=["auto-promoted", "react"],
        activates_on_block={
            "tools": ["Edit"],
            "path_globs": ["**/*.tsx"],
        },
    )
    # project-b: database enrich rule
    _write_feedback_page(
        hook_env, "database-schema",
        sources=["bots/project-b/MEMORY.md"],
        tags=["auto-promoted", "database"],
        activates_on_block={
            "tools": ["Edit"],
            "path_globs": ["**/*.sql"],
        },
    )

    # Build the index so the per-project filter can read it
    rule_activation.write_index(hook_env, rule_activation.build_index(hook_env))

    # Enable injection so we get output on stdout
    _set_config(hook_env, injection={"enabled": True})

    repo = _make_project_repo(tmp_path, "project-a")
    rc, out = _run_hook(hook_env, {"session_id": "S4", "cwd": str(repo)}, monkeypatch, capsys)

    assert rc == 0
    assert out, "injection payload expected"
    parsed = json.loads(out)
    ctx = parsed["hookSpecificOutput"]["additionalContext"]
    assert "react" in ctx, "project-a topic 'react' must be in injection"
    assert "database" not in ctx, "project-b topic 'database' must NOT leak into project-a session"


# ---------------------------------------------------------------------------
# Test 5: falls back to vault-wide union when index is absent
# ---------------------------------------------------------------------------


def test_session_start_falls_back_to_vault_union_when_no_index(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """When the index file doesn't exist, injection falls back to vault-wide topics."""
    # Two rules from different projects — both should appear in vault-wide fallback
    _write_feedback_page(
        hook_env, "react-best-practices",
        sources=["bots/project-a/MEMORY.md"],
        tags=["auto-promoted", "react"],
        activates_on_block={
            "tools": ["Edit"],
            "path_globs": ["**/*.tsx"],
        },
    )
    _write_feedback_page(
        hook_env, "database-schema",
        sources=["bots/project-b/MEMORY.md"],
        tags=["auto-promoted", "database"],
        activates_on_block={
            "tools": ["Edit"],
            "path_globs": ["**/*.sql"],
        },
    )

    # v0.5: enforcement defaults to True, which would cause session_start to
    # rebuild the index on hook entry — defeating the "no index" precondition
    # this test relies on. Explicitly disable both activation flags so the
    # index stays absent and the vault-wide fallback path is exercised.
    _set_config(
        hook_env,
        injection={"enabled": True},
        enforcement={"enabled": False},
        enrichment={"enabled": False},
    )

    # Ensure no index exists (must come AFTER _set_config so a stale rebuild
    # from a previous test invocation can't beat the unlink to the punch).
    index_path = hook_env / ".mnemo" / "rule-activation-index.json"
    if index_path.exists():
        index_path.unlink()

    repo = _make_project_repo(tmp_path, "project-a")
    rc, out = _run_hook(hook_env, {"session_id": "S5", "cwd": str(repo)}, monkeypatch, capsys)

    assert rc == 0
    assert out, "injection payload expected"
    parsed = json.loads(out)
    ctx = parsed["hookSpecificOutput"]["additionalContext"]
    # Vault-wide fallback: both topics appear
    assert "react" in ctx
    assert "database" in ctx


# ---------------------------------------------------------------------------
# Test 6: index rebuild failure is logged, not raised
# ---------------------------------------------------------------------------


def test_session_start_index_rebuild_failure_is_logged_not_raised(
    hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
):
    """If build_index raises, session_start exits 0 and logs the error."""
    _set_config(hook_env, enforcement={"enabled": True})

    # Patch build_index to raise — session_start does a lazy import of the
    # module, which resolves to the already-imported module in sys.modules,
    # so the monkeypatch on the module object takes effect.
    def _fail(vault):
        raise RuntimeError("simulated index build failure")

    monkeypatch.setattr(rule_activation, "build_index", _fail)

    repo = _make_project_repo(tmp_path, "myrepo")
    rc, _ = _run_hook(hook_env, {"session_id": "S6", "cwd": str(repo)}, monkeypatch, capsys)

    assert rc == 0

    error_log = hook_env / ".errors.log"
    assert error_log.exists(), ".errors.log must be written"
    log_text = error_log.read_text()
    assert "session_start.rule_activation_index" in log_text
