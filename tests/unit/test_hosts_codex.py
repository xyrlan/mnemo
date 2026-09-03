from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def fake_codex(monkeypatch: pytest.MonkeyPatch):
    """Pretend ``codex`` is on PATH and record what we would have run."""
    calls: list = []

    def fake_run(argv, **kw):
        assert kw.get("timeout") == 30
        assert kw.get("capture_output") is True
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("mnemo.hosts.codex.shutil.which", lambda name: "/usr/local/bin/codex" if name == "codex" else None)
    monkeypatch.setattr("mnemo.hosts.codex.subprocess.run", fake_run)
    return calls


def test_codex_registers_through_the_cli(tmp_home: Path, tmp_path: Path, fake_codex):
    from mnemo.hosts import get_host
    from mnemo._selfexec import self_argv

    r = get_host("codex").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "codex-cli" and r.note is None
    assert fake_codex == [["codex", "mcp", "add", "mnemo", "--", *self_argv("mcp-server")]]
    assert Path(r.path) == tmp_home / ".codex" / "config.toml"


def test_codex_unregisters_through_the_cli(tmp_home: Path, tmp_path: Path, fake_codex):
    from mnemo.hosts import get_host

    get_host("codex").unregister_mcp(project=False, cwd=tmp_path)
    assert fake_codex == [["codex", "mcp", "remove", "mnemo"]]


def test_codex_without_binary_prints_a_toml_snippet(tmp_home: Path, tmp_path: Path, monkeypatch):
    from mnemo.hosts import get_host
    from mnemo._selfexec import self_argv
    from mnemo.hosts.codex import _toml_escape

    monkeypatch.setattr("mnemo.hosts.codex.shutil.which", lambda name: None)
    r = get_host("codex").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "snippet"
    assert "[mcp_servers.mnemo]" in r.note
    assert f'command = "{_toml_escape(self_argv("mcp-server")[0])}"' in r.note
    assert "args = [" in r.note


def test_codex_cli_failure_falls_back_to_snippet(tmp_home: Path, tmp_path: Path, monkeypatch):
    from mnemo.hosts import get_host

    monkeypatch.setattr("mnemo.hosts.codex.shutil.which", lambda name: "/usr/local/bin/codex")

    def failing_run(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")

    monkeypatch.setattr("mnemo.hosts.codex.subprocess.run", failing_run)
    r = get_host("codex").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "snippet" and "boom" in r.note and "[mcp_servers.mnemo]" in r.note


def test_codex_project_scope_is_refused(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host
    from mnemo.hosts.codex import CodexScopeError

    with pytest.raises(CodexScopeError):
        get_host("codex").register_mcp(project=True, cwd=tmp_path)


def test_codex_unregister_project_scope_is_refused(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host
    from mnemo.hosts.codex import CodexScopeError

    with pytest.raises(CodexScopeError):
        get_host("codex").unregister_mcp(project=True, cwd=tmp_path)


def test_codex_describe_reads_config_toml(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    assert host.describe(project=False, cwd=tmp_path).registered is False
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('model = "x"\n\n[mcp_servers.mnemo]\ncommand = "/nonexistent/python"\nargs = ["-m", "mnemo", "mcp-server"]\n', encoding="utf-8")
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True and s.command_ok is False and "nonexistent" in s.detail
    assert host.export_target(tmp_path).path == tmp_path / "AGENTS.md"


def test_codex_describe_ignores_a_later_table(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '[mcp_servers.mnemo]\ncommand = "/nonexistent/python"\n\n'
        '[mcp_servers.other]\ncommand = "/bin/sh"\n',
        encoding="utf-8",
    )
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True
    assert "nonexistent" in s.detail
    assert "/bin/sh" not in s.detail


def test_codex_toml_snippet_renders_exact_text(monkeypatch: pytest.MonkeyPatch):
    from mnemo.hosts import codex

    monkeypatch.setattr("mnemo.hosts.codex.self_argv", lambda *a: ["/py", "-m", "mnemo", "mcp-server"])
    assert codex.toml_snippet() == '[mcp_servers.mnemo]\ncommand = "/py"\nargs = ["-m", "mnemo", "mcp-server"]\n'


def test_codex_describe_tolerates_comment_indent_and_indented_command(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        '  [mcp_servers.mnemo]  # added by codex\n'
        '  command = "/nonexistent/python"\n',
        encoding="utf-8",
    )
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True and s.command_ok is False and "nonexistent" in s.detail


def test_codex_describe_accepts_quoted_key(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers."mnemo"]\ncommand = "/nonexistent/python"\n', encoding="utf-8")
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True and "nonexistent" in s.detail


def test_codex_describe_accepts_literal_string_command(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("[mcp_servers.mnemo]\ncommand = 'literal'\n", encoding="utf-8")
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True and "literal" in s.detail


def test_codex_describe_unescapes_basic_string_command(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.mnemo]\ncommand = "C:\\\\bin\\\\py.exe"\n', encoding="utf-8")
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True
    assert r"C:\bin\py.exe" in s.detail
    assert r"C:\\bin\\py.exe" not in s.detail


def test_codex_describe_tolerates_crlf_line_endings(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_bytes(b'[mcp_servers.mnemo]\r\ncommand = "/nonexistent/python"\r\n')
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True


def test_codex_describe_unescapes_embedded_quote(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    host = get_host("codex")
    cfg = tmp_home / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.mnemo]\ncommand = "a\\"b"\n', encoding="utf-8")
    s = host.describe(project=False, cwd=tmp_path)
    assert s.registered is True
    assert 'a"b' in s.detail


def test_run_returns_message_when_subprocess_raises_oserror(monkeypatch: pytest.MonkeyPatch):
    from mnemo.hosts import codex

    def raising_run(argv, **kw):
        raise OSError("no such file")

    monkeypatch.setattr("mnemo.hosts.codex.subprocess.run", raising_run)
    err = codex._run(["codex", "mcp", "add", "mnemo"])
    assert err is not None and "no such file" in err
