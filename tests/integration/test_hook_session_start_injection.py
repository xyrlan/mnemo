"""Integration tests for v0.5 SessionStart MCP topic injection."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.hooks import session_start


def _write_page(
    vault: Path,
    page_type: str,
    slug: str,
    *,
    tags: list[str],
    sources: list[str],
) -> None:
    target_dir = vault / "shared" / page_type
    target_dir.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    (target_dir / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        f"description: d\n"
        f"type: {page_type}\n"
        f"stability: stable\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        "---\n\nbody\n"
    )


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def _set_config(vault: Path, **overrides):
    cfg_path = vault / "mnemo.config.json"
    base = {"vaultRoot": str(vault)}
    base.update(overrides)
    cfg_path.write_text(json.dumps(base))


def _run_hook(payload: dict, monkeypatch, capsys) -> str:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    rc = session_start.main()
    assert rc == 0
    captured = capsys.readouterr()
    return captured.out


# --- helper functions ---


def test_build_injection_payload_returns_empty_when_no_topics(hook_env: Path):
    text = session_start._build_injection_payload(hook_env)
    assert text == ""


def test_build_injection_payload_lists_topics(hook_env: Path):
    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "package-management"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        hook_env, "user", "u1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )

    text = session_start._build_injection_payload(hook_env)
    assert "git" in text
    assert "package-management" in text
    assert "list_rules_by_topic" in text
    assert "read_mnemo_rule" in text


# --- main() injection wiring ---


def test_session_start_silent_when_injection_disabled(
    hook_env: Path, tmp_path: Path, monkeypatch, capsys,
):
    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _set_config(hook_env)  # injection defaults to false
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)

    out = _run_hook(
        {"session_id": "S1", "cwd": str(repo), "source": "startup"},
        monkeypatch, capsys,
    )
    assert out == ""


def test_session_start_emits_hook_specific_output_when_enabled(
    hook_env: Path, tmp_path: Path, monkeypatch, capsys,
):
    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "package-management"],
        sources=["bots/a/m.md"],
    )
    _set_config(hook_env, injection={"enabled": True})
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)

    out = _run_hook(
        {"session_id": "S2", "cwd": str(repo), "source": "startup"},
        monkeypatch, capsys,
    )
    assert out  # something on stdout
    parsed = json.loads(out)
    assert parsed["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    ctx = parsed["hookSpecificOutput"]["additionalContext"]
    assert "package-management" in ctx
    assert "list_rules_by_topic" in ctx


def test_session_start_silent_when_enabled_but_no_topics(
    hook_env: Path, tmp_path: Path, monkeypatch, capsys,
):
    """No topics in vault → no injection payload, even with flag on."""
    _set_config(hook_env, injection={"enabled": True})
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)

    out = _run_hook(
        {"session_id": "S3", "cwd": str(repo), "source": "startup"},
        monkeypatch, capsys,
    )
    assert out == ""


def test_session_start_injection_failure_is_silent(
    hook_env: Path, tmp_path: Path, monkeypatch, capsys,
):
    """If _build_injection_payload raises, hook still exits 0 with empty stdout."""
    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _set_config(hook_env, injection={"enabled": True})

    def boom(_):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(session_start, "_build_injection_payload", boom)

    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)

    out = _run_hook(
        {"session_id": "S4", "cwd": str(repo), "source": "startup"},
        monkeypatch, capsys,
    )
    assert out == ""  # silent failure


def test_session_start_with_injection_still_runs_session_save(
    hook_env: Path, tmp_path: Path, monkeypatch, capsys,
):
    """Existing session.save + log behavior must be preserved when injection is on."""
    from mnemo.core import session

    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _set_config(hook_env, injection={"enabled": True})
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)

    out = _run_hook(
        {"session_id": "S5", "cwd": str(repo), "source": "startup"},
        monkeypatch, capsys,
    )
    assert out  # injection emitted
    cached = session.load("S5")
    assert cached is not None
    assert cached["name"] == "myrepo"
