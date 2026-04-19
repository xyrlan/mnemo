"""Tests for the mnemo statusline renderer + composer."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo import statusline as sl


def _write_page(
    vault: Path,
    page_type: str,
    slug: str,
    *,
    tags: list[str],
    sources: list[str],
) -> None:
    target = vault / "shared" / page_type
    target.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources)
    tags_yaml = "\n".join(f"  - {t}" for t in tags)
    (target / f"{slug}.md").write_text(
        "---\n"
        f"name: {slug}\n"
        f"type: {page_type}\n"
        f"stability: stable\n"
        "sources:\n"
        f"{sources_yaml}\n"
        "tags:\n"
        f"{tags_yaml}\n"
        "---\n\nbody\n"
    )


@pytest.fixture(autouse=True)
def _no_project_resolution(monkeypatch):
    """Statusline tests expect vault-wide topic counts unless overridden."""
    monkeypatch.setattr(
        "mnemo.core.agent.resolve_agent",
        lambda cwd: type("A", (), {"name": None, "repo_root": cwd, "has_git": False})(),
    )


def _write_claude_json_with_mnemo(path: Path) -> None:
    path.write_text(json.dumps({
        "mcpServers": {
            "mnemo": {"command": "python", "args": ["-m", "mnemo", "mcp-server"]},
        },
    }))


# --- render ---


def test_render_returns_empty_when_mcp_not_registered(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    # File doesn't exist
    assert sl.render(tmp_vault, claude_json) == ""


def test_render_returns_empty_when_claude_json_has_no_mcp_section(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({"theme": "dark"}))
    assert sl.render(tmp_vault, claude_json) == ""


def test_render_returns_empty_when_other_servers_but_no_mnemo(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text(json.dumps({
        "mcpServers": {"other": {"command": "node"}},
    }))
    assert sl.render(tmp_vault, claude_json) == ""


def test_render_zero_topics_zero_calls(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)
    assert sl.render(tmp_vault, claude_json) == "mnemo · 0 topics · 0↓"


def test_render_with_topics_and_calls(tmp_vault, tmp_path):
    from mnemo.core.mcp import session_state as counter

    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    _write_page(
        tmp_vault, "feedback", "f2",
        tags=["auto-promoted", "react"],
        sources=["bots/a/m.md"],
    )
    counter.increment(tmp_vault)
    counter.increment(tmp_vault)
    counter.increment(tmp_vault)

    claude_json = tmp_path / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)
    assert sl.render(tmp_vault, claude_json) == "mnemo · 2 topics · 3↓"


def test_render_handles_malformed_claude_json(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text("{not valid")
    assert sl.render(tmp_vault, claude_json) == ""


def test_render_handles_non_dict_claude_json(tmp_vault, tmp_path):
    claude_json = tmp_path / ".claude.json"
    claude_json.write_text("[1, 2, 3]")
    assert sl.render(tmp_vault, claude_json) == ""


# --- write_state / read_state / clear_state ---


def test_write_state_with_original_command(tmp_vault):
    sl.write_state(tmp_vault, {"command": "echo hello", "type": "command"})
    state = sl.read_state(tmp_vault)
    assert state == {"command": "echo hello", "type": "command"}


def test_write_state_with_none_means_no_pre_existing(tmp_vault):
    sl.write_state(tmp_vault, None)
    state = sl.read_state(tmp_vault)
    assert state == {"command": None}


def test_read_state_returns_none_when_missing(tmp_vault):
    assert sl.read_state(tmp_vault) is None


def test_clear_state_removes_file(tmp_vault):
    sl.write_state(tmp_vault, {"command": "echo hi"})
    sl.clear_state(tmp_vault)
    assert sl.read_state(tmp_vault) is None


def test_clear_state_noop_when_missing(tmp_vault):
    sl.clear_state(tmp_vault)  # must not raise


# --- _run_original ---


def test_run_original_returns_stdout(tmp_vault):
    assert sl._run_original("printf 'hello world'") == "hello world"


def test_run_original_strips_trailing_whitespace(tmp_vault):
    assert sl._run_original("printf 'foo\\n'") == "foo"


def test_run_original_returns_empty_when_command_is_none():
    assert sl._run_original(None) == ""


def test_run_original_returns_empty_when_command_is_blank():
    assert sl._run_original("") == ""


def test_run_original_returns_empty_on_subprocess_failure():
    # Command that doesn't exist
    assert sl._run_original("/nonexistent/path/xyzzy") == ""


# --- compose (the additive pipeline) ---


def test_compose_with_no_original_emits_only_mnemo(tmp_vault, monkeypatch, capsys):
    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    sl.write_state(tmp_vault, None)

    claude_json = tmp_vault / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_vault)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(claude_json) if p == "~/.claude.json" else p)

    out = io.StringIO()
    sl.compose(out=out)
    text = out.getvalue()
    assert text == "mnemo · 1 topics · 0↓"


def test_compose_with_original_concatenates(tmp_vault, monkeypatch):
    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    # Use printf, NOT `echo -n` — `echo -n` is non-portable: macOS bash 3 and
    # Windows cmd.exe both treat -n as literal text instead of suppressing
    # the newline. printf is POSIX and available on all CI platforms via
    # /usr/bin/printf (Linux/macOS) or Git for Windows printf.exe.
    sl.write_state(tmp_vault, {"command": "printf 'BATTERY 87'"})

    claude_json = tmp_vault / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_vault)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(claude_json) if p == "~/.claude.json" else p)

    out = io.StringIO()
    sl.compose(out=out)
    text = out.getvalue()
    assert text == "BATTERY 87 · mnemo · 1 topics · 0↓"


def test_compose_with_failing_original_still_emits_mnemo(tmp_vault, monkeypatch):
    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    sl.write_state(tmp_vault, {"command": "/nonexistent/script.sh"})

    claude_json = tmp_vault / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_vault)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(claude_json) if p == "~/.claude.json" else p)

    out = io.StringIO()
    sl.compose(out=out)
    text = out.getvalue()
    assert text == "mnemo · 1 topics · 0↓"


def test_compose_with_no_mnemo_segment_only_emits_original(tmp_vault, monkeypatch):
    """If MCP isn't registered, mnemo segment is empty — but original still runs."""
    sl.write_state(tmp_vault, {"command": "printf 'just original'"})
    # No claude.json → MCP not registered

    claude_json = tmp_vault / "nonexistent.json"

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_vault)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(claude_json) if p == "~/.claude.json" else p)

    out = io.StringIO()
    sl.compose(out=out)
    text = out.getvalue()
    assert text == "just original"


def test_compose_returns_empty_when_config_load_fails(tmp_vault, monkeypatch):
    def boom():
        raise RuntimeError("config gone")
    monkeypatch.setattr("mnemo.core.config.load_config", boom)
    out = io.StringIO()
    rc = sl.compose(out=out)
    assert rc == 0
    assert out.getvalue() == ""


# --- CLI integration ---


def test_cli_statusline_subcommand_exists():
    from mnemo.cli import COMMANDS
    assert "statusline" in COMMANDS
    assert "statusline-compose" in COMMANDS


def test_cli_statusline_invokes_render(tmp_vault, monkeypatch, capsys):
    from mnemo.cli import main

    _write_page(
        tmp_vault, "feedback", "f1",
        tags=["auto-promoted", "git"],
        sources=["bots/a/m.md"],
    )
    claude_json = tmp_vault / ".claude.json"
    _write_claude_json_with_mnemo(claude_json)

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    monkeypatch.setattr("mnemo.core.paths.vault_root", lambda cfg: tmp_vault)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(claude_json) if p == "~/.claude.json" else p)

    rc = main(["statusline"])
    assert rc == 0
    out = capsys.readouterr().out
    assert out == "mnemo · 1 topics · 0↓"


def test_cli_statusline_compose_invokes_compose(monkeypatch):
    from mnemo.cli import main

    called: dict[str, bool] = {}

    def fake_compose(out=None):
        called["yes"] = True
        return 0

    monkeypatch.setattr("mnemo.statusline.compose", fake_compose)
    rc = main(["statusline-compose"])
    assert rc == 0
    assert called.get("yes") is True
