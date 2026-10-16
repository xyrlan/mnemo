"""``mnemo init|uninstall --host grok`` and ``HOSTS["grok"]``.

No ``grok`` binary runs here: ``fake_grok`` stands in for it, answering
``grok inspect --json`` from a dict shaped like the real output (recorded on
grok 1.0.60, 2026-10-16) and recording every other call.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from mnemo import cli
from mnemo._selfexec import self_argv
from mnemo.install import grok_hooks
from tests.unit._export_fixtures import write_rule

PY = "/usr/bin/python3"


def inspect_json(*, hooks: Optional[list] = None, mcp: Optional[list] = None) -> Dict[str, Any]:
    return {"grokVersion": "1.0.60", "hooks": hooks or [], "mcpServers": mcp or []}


def claude_hooks(source: str = "/home/u/.claude", status: Optional[str] = "enabled") -> list:
    out = []
    for event, module in (("session_start", "session_start"), ("user_prompt_submit", "user_prompt_submit"),
                          ("pre_tool_use", "pre_tool_use"), ("session_end", "session_end")):
        hook = {"event": event, "hookType": "command", "target": f"{PY} -m mnemo.hooks.{module}",
                "source": {"type": "user", "path": source}, "matcher": None, "vendor": "claude"}
        if status is not None:
            hook["compatibilityStatus"] = status
        out.append(hook)
    # a hook that is not mnemo's must never count
    out.append({"event": "stop", "hookType": "command", "target": "'/x/mnemo-desktop-hook' Stop || true",
                "source": {"type": "user", "path": source}, "matcher": None, "vendor": "claude"})
    return out


def claude_mcp(path: str = "/home/u/.claude.json", status: str = "enabled") -> list:
    return [{"name": "mnemo", "transport": "stdio", "target": PY,
             "source": {"type": "claudeJson", "path": path}, "compatibilityStatus": status, "vendor": "claude"}]


class FakeGrok:
    def __init__(self) -> None:
        self.inspect = inspect_json()
        self.inspect_fails = False
        self.add_fails: Optional[str] = None
        self.calls: List[Dict[str, Any]] = []

    def run(self, argv, **kw):
        self.calls.append({"argv": list(argv), "env": kw.get("env") or {}, "cwd": kw.get("cwd")})
        if argv[:2] == ["grok", "inspect"]:
            if self.inspect_fails:
                return subprocess.CompletedProcess(argv, 1, stdout="", stderr="boom")
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(self.inspect), stderr="")
        if argv[:3] == ["grok", "mcp", "add"] and self.add_fails:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=self.add_fails)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    def mcp_calls(self, verb: str) -> List[List[str]]:
        return [c["argv"] for c in self.calls if c["argv"][:3] == ["grok", "mcp", verb]]

    def inspect_cwds(self) -> List[Path]:
        return [Path(c["cwd"]) for c in self.calls if c["argv"][:2] == ["grok", "inspect"]]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    for name in ("GROK_HOME", "MNEMO_AGENT", "GROK_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fake_grok(monkeypatch: pytest.MonkeyPatch) -> FakeGrok:
    fake = FakeGrok()
    monkeypatch.setattr("mnemo.hosts.grok.shutil.which",
                        lambda name: "/usr/local/bin/grok" if name == "grok" else None)
    monkeypatch.setattr("mnemo.hosts.grok.subprocess.run", fake.run)
    return fake


@pytest.fixture
def no_grok(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("mnemo.hosts.grok.shutil.which", lambda name: None)

    def boom(*a, **k):  # pragma: no cover - would be a bug
        raise AssertionError("grok must not run when it is not on PATH")

    monkeypatch.setattr("mnemo.hosts.grok.subprocess.run", boom)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def hook_file(tmp_home: Path) -> Path:
    return tmp_home / ".grok" / "hooks" / "mnemo.json"


def init_grok(tmp_home: Path, *extra: str) -> int:
    return cli.main(["init", "--host", "grok", "--yes", "--vault-root", str(tmp_home / "v"),
                     "--no-mirror", *extra])


# --- the registry entry -------------------------------------------------------


def test_registry_has_grok_after_the_others():
    from mnemo.hosts import HOSTS, get_host

    assert list(HOSTS) == ["claude", "cursor", "codex", "grok"]
    assert get_host("grok").name == "grok"


# --- MCP registration -----------------------------------------------------------


def test_register_adds_through_grok_mcp_add_for_the_account(tmp_home: Path, tmp_path: Path, fake_grok):
    from mnemo.hosts import get_host

    r = get_host("grok").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "grok-cli" and r.note is None
    assert Path(r.path) == tmp_home / ".grok" / "config.toml"
    assert fake_grok.mcp_calls("add") == [
        ["grok", "mcp", "add", "mnemo", "-s", "user", "--", *self_argv("mcp-server")]]
    add = [c for c in fake_grok.calls if c["argv"][:3] == ["grok", "mcp", "add"]][0]
    assert Path(add["env"]["GROK_HOME"]) == tmp_home / ".grok"


def test_register_honours_grok_home(tmp_home: Path, tmp_path: Path, fake_grok, monkeypatch):
    from mnemo.hosts import get_host

    other = tmp_path / "acct"
    monkeypatch.setenv("GROK_HOME", str(other))
    r = get_host("grok").register_mcp(project=False, cwd=tmp_path)
    assert Path(r.path) == other / "config.toml"
    add = [c for c in fake_grok.calls if c["argv"][:3] == ["grok", "mcp", "add"]][0]
    assert Path(add["env"]["GROK_HOME"]) == other


def test_register_project_scope_writes_the_projects_config(tmp_home: Path, tmp_path: Path, fake_grok):
    from mnemo.hosts import get_host

    fake_grok.inspect = inspect_json(mcp=claude_mcp())  # visible globally: still added to the project
    r = get_host("grok").register_mcp(project=True, cwd=tmp_path)
    assert r.method == "grok-cli" and Path(r.path) == tmp_path / ".grok" / "config.toml"
    assert fake_grok.mcp_calls("add")[0][3:6] == ["mnemo", "-s", "project"]
    add = [c for c in fake_grok.calls if c["argv"][:3] == ["grok", "mcp", "add"]][0]
    assert Path(add["cwd"]) == tmp_path


def test_register_adds_nothing_when_grok_already_reads_it_from_claude(tmp_home: Path, tmp_path: Path, fake_grok):
    from mnemo.hosts import get_host

    claude_json = str(tmp_home / ".claude.json")
    fake_grok.inspect = inspect_json(mcp=claude_mcp(claude_json))
    r = get_host("grok").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "inherited" and r.path == claude_json
    assert fake_grok.mcp_calls("add") == []
    # asked from the home directory, where Grok loads no project files
    assert fake_grok.inspect_cwds() == [tmp_home]


def test_a_server_grok_lists_as_disabled_is_not_inherited(tmp_home: Path, tmp_path: Path, fake_grok):
    from mnemo.hosts import get_host

    fake_grok.inspect = inspect_json(mcp=claude_mcp(status="disabled"))
    assert get_host("grok").register_mcp(project=False, cwd=tmp_path).method == "grok-cli"


def test_register_without_the_binary_prints_the_toml_to_paste(tmp_home: Path, tmp_path: Path, no_grok):
    from mnemo.hosts import get_host

    r = get_host("grok").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "snippet" and "`grok` is not on PATH" in r.note
    assert "[mcp_servers.mnemo]" in r.note and str(tmp_home / ".grok" / "config.toml") in r.note


def test_register_without_the_binary_still_sees_claudes_registration(tmp_home: Path, tmp_path: Path, no_grok):
    from mnemo.hosts import get_host
    from mnemo.install import settings as inj

    inj.inject_mcp_servers(tmp_home / ".claude.json")
    r = get_host("grok").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "inherited" and Path(r.path) == tmp_home / ".claude.json"


def test_register_failure_falls_back_to_the_snippet(tmp_home: Path, tmp_path: Path, fake_grok):
    from mnemo.hosts import get_host

    fake_grok.add_fails = "config.toml is read-only"
    r = get_host("grok").register_mcp(project=False, cwd=tmp_path)
    assert r.method == "snippet" and "config.toml is read-only" in r.note


def test_inspect_failure_does_not_block_registering(tmp_home: Path, tmp_path: Path, fake_grok):
    from mnemo.hosts import get_host

    fake_grok.inspect_fails = True
    assert get_host("grok").register_mcp(project=False, cwd=tmp_path).method == "grok-cli"


# --- describe / status ------------------------------------------------------------


GROK_WRITTEN = (  # the shape `grok mcp add` writes (measured)
    '[mcp_servers.mnemo]\ncommand = "{cmd}"\nargs = [\n    "-m",\n    "mnemo",\n    "mcp-server",\n]\nenabled = true\n')


def test_describe_reads_what_grok_wrote(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    cfg = tmp_home / ".grok" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(GROK_WRITTEN.format(cmd=Path(PY).as_posix()) + '\n[other]\ncommand = "nope"\n', encoding="utf-8")
    s = get_host("grok").describe(project=False, cwd=tmp_path)
    assert s.registered and Path(s.path) == cfg
    assert s.command_ok == Path(PY).exists()


def test_describe_flags_a_dead_command(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    cfg = tmp_home / ".grok" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(GROK_WRITTEN.format(cmd="/gone/python3"), encoding="utf-8")
    s = get_host("grok").describe(project=False, cwd=tmp_path)
    assert s.registered and not s.command_ok and "command not found" in s.detail


def test_describe_is_unregistered_without_a_table_or_a_file(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import get_host

    assert not get_host("grok").describe(project=False, cwd=tmp_path).registered
    cfg = tmp_home / ".grok" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text('[mcp_servers.other]\ncommand = "x"\n', encoding="utf-8")
    assert not get_host("grok").describe(project=False, cwd=tmp_path).registered


def test_a_claude_inherited_server_is_not_reported_as_groks_own(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import registered_hosts
    from mnemo.install import settings as inj

    inj.inject_mcp_servers(tmp_home / ".claude.json")
    assert [s.name for s in registered_hosts(tmp_path)] == []


def test_registered_hosts_lists_a_groks_own_registration(tmp_home: Path, tmp_path: Path):
    from mnemo.hosts import registered_hosts

    cfg = tmp_home / ".grok" / "config.toml"
    cfg.parent.mkdir()
    cfg.write_text(GROK_WRITTEN.format(cmd=Path(PY).as_posix()), encoding="utf-8")
    assert [(s.name, Path(s.path)) for s in registered_hosts(tmp_path)] == [("grok", cfg)]


# --- mnemo init --host grok -------------------------------------------------------


def test_init_grok_only_machine_wires_mcp_and_hooks_and_exports(tmp_home: Path, repo: Path, fake_grok, capsys):
    write_rule(tmp_home / "v", slug="use-yarn-not-npm", projects=("app",))
    assert init_grok(tmp_home) == 0
    out = capsys.readouterr().out
    assert len(fake_grok.mcp_calls("add")) == 1
    data = json.loads(hook_file(tmp_home).read_text(encoding="utf-8"))
    assert data == grok_hooks.render()
    assert (repo / ".grok" / "rules" / "mnemo.md").exists()
    assert "exported 1 rule" in out and "Writing mnemo's hooks" in out
    assert "grok --trust" in out
    # nothing of Claude's was written
    assert not (tmp_home / ".claude" / "settings.json").exists()
    assert not (tmp_home / ".claude.json").exists()


def test_init_grok_is_idempotent(tmp_home: Path, repo: Path, fake_grok):
    assert init_grok(tmp_home) == 0
    first = hook_file(tmp_home).read_bytes()
    assert init_grok(tmp_home) == 0
    assert hook_file(tmp_home).read_bytes() == first


def test_init_grok_adds_nothing_when_grok_reads_claudes_install(tmp_home: Path, repo: Path, fake_grok, capsys):
    fake_grok.inspect = inspect_json(hooks=claude_hooks(str(tmp_home / ".claude")),
                                     mcp=claude_mcp(str(tmp_home / ".claude.json")))
    assert init_grok(tmp_home) == 0
    out = capsys.readouterr().out
    assert fake_grok.mcp_calls("add") == []
    assert not hook_file(tmp_home).exists()
    assert "Grok already loads mnemo's hooks" in out and "each hook runs once" in out
    assert "Grok already reads mnemo's MCP server" in out
    assert (tmp_home / "v" / "mnemo.config.json").exists() or (tmp_home / "v").is_dir()


def test_init_grok_ignores_claude_hooks_grok_has_switched_off(tmp_home: Path, repo: Path, fake_grok):
    fake_grok.inspect = inspect_json(hooks=claude_hooks(status="disabled"))
    assert init_grok(tmp_home) == 0
    assert hook_file(tmp_home).exists()


def test_init_grok_removes_its_own_file_once_claude_covers_the_hooks(tmp_home: Path, repo: Path, fake_grok, capsys):
    assert init_grok(tmp_home) == 0  # Grok-only first
    assert hook_file(tmp_home).exists()
    # a hook file of ours is listed too, from the hooks directory; only Claude's makes it a duplicate
    ours = [dict(h, source={"type": "user", "path": str(hook_file(tmp_home).parent)}, vendor=None)
            for h in claude_hooks()[:4]]
    fake_grok.inspect = inspect_json(hooks=ours + claude_hooks(str(tmp_home / ".claude")))
    capsys.readouterr()
    assert init_grok(tmp_home) == 0
    assert not hook_file(tmp_home).exists()
    assert "Removed" in capsys.readouterr().out


def test_init_grok_refreshes_its_own_file_when_only_it_provides_the_hooks(tmp_home: Path, repo: Path, fake_grok):
    assert init_grok(tmp_home) == 0
    ours = [dict(h, source={"type": "user", "path": str(hook_file(tmp_home).parent)})
            for h in claude_hooks()[:4]]
    fake_grok.inspect = inspect_json(hooks=ours)
    hook_file(tmp_home).write_text("{}", encoding="utf-8")
    assert init_grok(tmp_home) == 0
    assert json.loads(hook_file(tmp_home).read_text(encoding="utf-8")) == grok_hooks.render()


def test_init_grok_without_the_binary_reads_claudes_settings_directly(tmp_home: Path, repo: Path, no_grok, capsys):
    from mnemo.install import settings as inj

    assert init_grok(tmp_home) == 0                      # nothing wired: a file, and a snippet to paste
    out = capsys.readouterr().out
    assert hook_file(tmp_home).exists() and "[mcp_servers.mnemo]" in out
    hook_file(tmp_home).unlink()
    inj.inject_hooks(tmp_home / ".claude" / "settings.json")
    assert init_grok(tmp_home) == 0                      # Claude wired: the file stays away
    assert not hook_file(tmp_home).exists()


def test_init_grok_project_scope(tmp_home: Path, repo: Path, fake_grok, capsys):
    assert init_grok(tmp_home, "--project") == 0
    out = capsys.readouterr().out
    assert json.loads((repo / ".grok" / "hooks" / "mnemo.json").read_text(encoding="utf-8")) == grok_hooks.render()
    assert not hook_file(tmp_home).exists()
    assert fake_grok.mcp_calls("add")[0][3:6] == ["mnemo", "-s", "project"]
    assert fake_grok.inspect_cwds()[-1] == repo
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".grok/hooks/mnemo.json" in gitignore and ".mnemo/" in gitignore
    assert "trusts" in out


def test_init_grok_global_asks_grok_from_the_home_directory(tmp_home: Path, repo: Path, fake_grok):
    assert init_grok(tmp_home) == 0
    assert set(fake_grok.inspect_cwds()) == {tmp_home}


def test_init_grok_honours_grok_home(tmp_home: Path, tmp_path: Path, repo: Path, fake_grok, monkeypatch):
    acct = tmp_path / ".grok-work"
    monkeypatch.setenv("GROK_HOME", str(acct))
    assert init_grok(tmp_home) == 0
    assert (acct / "hooks" / "mnemo.json").exists()
    assert not hook_file(tmp_home).exists()


def test_init_grok_hook_file_failure_is_an_error_not_a_traceback(tmp_home: Path, repo: Path, fake_grok, capsys):
    hook_file(tmp_home).parent.mkdir(parents=True)
    hook_file(tmp_home).write_text("[1]", encoding="utf-8")
    assert init_grok(tmp_home) == 1
    assert "not a JSON object" in capsys.readouterr().err


def test_init_grok_hooks_only_refreshes_the_file_and_nothing_else(tmp_home: Path, repo: Path, fake_grok, capsys):
    assert init_grok(tmp_home, "--hooks-only") == 1      # nothing to refresh yet
    assert "no mnemo hooks" in capsys.readouterr().err and not hook_file(tmp_home).exists()
    assert init_grok(tmp_home) == 0
    hook_file(tmp_home).write_text('{"hooks": {"SessionStart": [{"hooks": [{"type": "command", '
                                   '"command": "python3 -m mnemo.hooks.session_start"}]}]}}', encoding="utf-8")
    fake_grok.calls.clear()
    assert init_grok(tmp_home, "--hooks-only") == 0
    assert json.loads(hook_file(tmp_home).read_text(encoding="utf-8")) == grok_hooks.render()
    assert fake_grok.calls == []


def test_hooks_only_stays_refused_for_the_hosts_without_hooks(tmp_home: Path, repo: Path, capsys):
    assert cli.main(["init", "--host", "cursor", "--hooks-only", "--yes"]) == 2
    assert "other hosts have none" in capsys.readouterr().err


# --- mnemo uninstall --host grok --------------------------------------------------


def test_uninstall_grok_removes_its_config_entry_and_hook_file_only(tmp_home: Path, repo: Path, fake_grok, capsys):
    from mnemo.install import settings as inj

    inj.inject_hooks(tmp_home / ".claude" / "settings.json")
    inj.inject_mcp_servers(tmp_home / ".claude.json")
    claude_before = [(tmp_home / ".claude" / "settings.json").read_bytes(), (tmp_home / ".claude.json").read_bytes()]
    fake_grok.inspect = inspect_json()  # Claude hooks switched off for Grok, say
    assert init_grok(tmp_home) == 0
    assert hook_file(tmp_home).exists()
    capsys.readouterr()

    assert cli.main(["uninstall", "--host", "grok", "--yes"]) == 0
    out = capsys.readouterr().out
    assert not hook_file(tmp_home).exists()
    assert "Grok hooks removed" in out and "MCP registration removed for grok" in out
    assert fake_grok.mcp_calls("remove") == [["grok", "mcp", "remove", "mnemo", "-s", "user"]]
    # Claude's own install is not Grok's to remove
    assert [(tmp_home / ".claude" / "settings.json").read_bytes(), (tmp_home / ".claude.json").read_bytes()] == claude_before


def test_uninstall_grok_project_scope(tmp_home: Path, repo: Path, fake_grok):
    assert init_grok(tmp_home, "--project") == 0
    assert cli.main(["uninstall", "--host", "grok", "--project", "--yes"]) == 0
    assert not (repo / ".grok" / "hooks" / "mnemo.json").exists()
    assert fake_grok.mcp_calls("remove") == [["grok", "mcp", "remove", "mnemo", "-s", "project"]]


def test_uninstall_grok_without_the_binary_is_quiet(tmp_home: Path, repo: Path, no_grok):
    assert cli.main(["uninstall", "--host", "grok", "--yes"]) == 0


# --- Claude stays what it is -------------------------------------------------------


def test_claude_init_never_touches_grok(tmp_home: Path, repo: Path, monkeypatch):
    def boom(*a, **k):  # pragma: no cover - would be a bug
        raise AssertionError("a Claude init must not start grok")

    monkeypatch.setattr("mnemo.hosts.grok.subprocess.run", boom)
    monkeypatch.setattr("mnemo.hosts.grok.shutil.which", lambda name: "/usr/local/bin/grok")
    assert cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"]) == 0
    assert not (tmp_home / ".grok").exists()
    settings = json.loads((tmp_home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert set(settings["hooks"]) == {"SessionStart", "UserPromptSubmit", "PreToolUse", "SessionEnd"}
    assert "mnemo" in json.loads((tmp_home / ".claude.json").read_text(encoding="utf-8"))["mcpServers"]
    assert cli.main(["uninstall", "--yes"]) == 0


def test_claude_hooks_json_is_what_settings_writes(tmp_home: Path):
    """The Grok file adds no hook definition of its own: it is settings.json's table."""
    from mnemo.install import settings as inj

    target = tmp_home / ".claude" / "settings.json"
    inj.inject_hooks(target)
    written = json.loads(target.read_text(encoding="utf-8"))["hooks"]
    assert written == grok_hooks.render()["hooks"]
