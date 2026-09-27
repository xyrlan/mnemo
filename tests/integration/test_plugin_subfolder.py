"""plugin/ runs as the plugin, exactly as the repository root does (#514).

The Claude plugin directory takes a plugin from a subfolder, so plugin/ is the
same plugin with nothing else around it. It only earns that if Claude Code can
run it from there: the hooks and the slash commands go through
`${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd`, the MCP server through a git alias that
`cd`s into `$CLAUDE_PLUGIN_ROOT`, and all three end in bin/launch, which reads
the version from the plugin.json beside it. Each entry point is run here the
way Claude Code runs it, once from each root, against the same cached binary.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SUBFOLDER = REPO / "plugin"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="bash launcher; Windows goes through mnemo.cmd"
)


def _version(root: Path) -> str:
    return json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]


def _install_stub(data_dir: Path, version: str) -> Path:
    """A cached 'binary' that reports where it was found and with what."""
    machine = platform.machine()
    arch = "arm64" if (sys.platform == "darwin" and machine in ("arm64", "aarch64")) else "x64"
    target = f"{'darwin' if sys.platform == 'darwin' else 'linux'}-{arch}"
    binary = data_dir / "bin" / version / target / "mnemo"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/usr/bin/env bash\necho "BINARY=$0 ARGS=$*"\n', encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _env(root: Path, data_dir: Path) -> dict:
    return {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(root),
        "CLAUDE_PLUGIN_DATA": str(data_dir),
        # Off the network whatever branch the launcher reaches; git lives here too.
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin",
    }


def _hook_command(root: Path, event: str) -> str:
    """The hooks.json command for ``event``, expanded as Claude Code does."""
    hooks = json.loads((root / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    command = hooks[event][0]["hooks"][0]["command"]
    return command.replace("${CLAUDE_PLUGIN_ROOT}", str(root))


def _slash_command(root: Path, name: str) -> str:
    """The `!`...`` line a plugin command injects, expanded the same way."""
    body = (root / "commands" / f"{name}.md").read_text(encoding="utf-8")
    line = next(l for l in body.splitlines() if l.startswith("!`"))
    return line[2:-1].replace("${CLAUDE_PLUGIN_ROOT}", str(root))


def _mcp(root: Path, env: dict) -> subprocess.CompletedProcess:
    server = json.loads((root / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]["mnemo"]
    # Claude Code spawns it without a shell, from the user's project.
    return subprocess.run(
        [server["command"], *server["args"]], capture_output=True, text=True,
        env=env, cwd=str(Path.home()), timeout=30, stdin=subprocess.DEVNULL,
    )


def _shell(command: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", command], capture_output=True, text=True,
        env=env, cwd=str(Path.home()), timeout=30, input="{}",
    )


def test_the_subfolder_carries_the_roots_version():
    assert _version(SUBFOLDER) == _version(REPO)


@pytest.mark.parametrize("entry", ["hook", "command", "mcp"])
def test_every_entry_point_resolves_the_same_binary_from_either_root(tmp_path: Path, entry: str):
    data = tmp_path / "data"
    binary = _install_stub(data, _version(REPO))

    outputs = {}
    for root in (REPO, SUBFOLDER):
        env = _env(root, data)
        if entry == "hook":
            r = _shell(_hook_command(root, "SessionStart"), env)
        elif entry == "command":
            r = _shell(_slash_command(root, "status"), env)
        else:
            r = _mcp(root, env)
        assert r.returncode == 0, r.stderr
        outputs[root] = r.stdout.strip()

    expected = {
        "hook": "hook session_start",
        "command": "status",
        "mcp": "mcp-server",
    }[entry]
    assert outputs[SUBFOLDER] == f"BINARY={binary} ARGS={expected}"
    assert outputs[SUBFOLDER] == outputs[REPO]


def test_launch_finds_its_root_without_the_env_var(tmp_path: Path):
    """bin/launch falls back to its own location, which in plugin/ is plugin/."""
    data = tmp_path / "data"
    binary = _install_stub(data, _version(SUBFOLDER))
    env = _env(SUBFOLDER, data)
    env.pop("CLAUDE_PLUGIN_ROOT")

    r = subprocess.run(
        ["bash", str(SUBFOLDER / "bin" / "launch"), "status"],
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=30,
    )

    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == f"BINARY={binary} ARGS=status"
