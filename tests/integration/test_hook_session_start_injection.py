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
        "---\n\nbody\n", 
    encoding="utf-8")


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def _set_config(vault: Path, **overrides):
    cfg_path = vault / "mnemo.config.json"
    base = {"vaultRoot": str(vault)}
    base.update(overrides)
    cfg_path.write_text(json.dumps(base), encoding="utf-8")


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
    _set_config(hook_env, injection={"enabled": False})
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
    # Two sources → universal rule; shows up in envelope for any project
    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "package-management"],
        sources=["bots/a/m.md", "bots/b/m.md"],
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
    assert "mnemo://v1" in ctx
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

    # Two sources → universal rule; shows up in envelope for any project
    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md", "bots/b/m.md"],
    )
    _set_config(hook_env, injection={"enabled": True})
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)

    out = _run_hook(
        {"session_id": "S5", "cwd": str(repo), "source": "startup"},
        monkeypatch, capsys,
    )
    assert out  # injection emitted
    parsed = json.loads(out)
    assert "mnemo://v1" in parsed["hookSpecificOutput"]["additionalContext"]
    cached = session.load("S5")
    assert cached is not None
    assert cached["name"] == "myrepo"


def test_session_start_context_carries_no_skill_text(
    hook_env: Path, tmp_path: Path, monkeypatch, capsys,
):
    """Skills load on demand; none of their text rides on every session (#327).

    The whole case for `mnemo-loop` being a skill is that it costs nothing
    until a session needs it. Asserted on the hook's real stdout, which is the
    only place the cost could appear.
    """
    from mnemo.install.settings import SKILLS, read_skill

    _write_page(
        hook_env, "feedback", "f1",
        tags=["auto-promoted", "package-management"],
        sources=["bots/a/m.md", "bots/b/m.md"],
    )
    _set_config(hook_env, injection={"enabled": True})
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)

    out = _run_hook(
        {"session_id": "S9", "cwd": str(repo), "source": "startup"},
        monkeypatch, capsys,
    )
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    for name in SKILLS:
        assert name not in ctx
        for line in read_skill(name).splitlines():
            stripped = line.strip("#- *`").strip()
            if len(stripped) > 30:
                assert stripped not in ctx, f"{name}: {stripped[:40]!r}"
