"""The plugin's bin/launch script.

This is the only thing standing between Claude Code and mnemo for a plugin
install, and it runs as a hook — so its failure modes matter more than its
happy path. A non-zero exit here is not read as "mnemo is unavailable" but as
"your session hit an error", and on PreToolUse it reads as a denial. Every
path must exit 0.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCH = REPO_ROOT / "bin" / "launch"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="bash launcher; Windows goes through mnemo.cmd"
)


@pytest.fixture
def plugin_root(tmp_path: Path) -> Path:
    """A minimal plugin tree: the manifest the launcher reads, plus bin/."""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "mnemo", "version": "9.9.9", "description": "x"})
    )
    (root / "bin").mkdir()
    shutil.copy(LAUNCH, root / "bin" / "launch")
    return root


def run(plugin_root: Path, data_dir: Path, *args: str, timeout: int = 30):
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PLUGIN_DATA": str(data_dir),
        # Keep the test off the network no matter which branch it reaches.
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    return subprocess.run(
        ["bash", str(plugin_root / "bin" / "launch"), *args],
        capture_output=True, text=True, env=env, timeout=timeout, input="{}",
    )


def install_fake_binary(data_dir: Path, version: str = "9.9.9") -> Path:
    """Put a stub where the launcher expects the cached binary."""
    import platform
    machine = platform.machine()
    arch = "arm64" if (sys.platform == "darwin" and machine in ("arm64", "aarch64")) else "x64"
    target = f"{'darwin' if sys.platform == 'darwin' else 'linux'}-{arch}"
    binary = data_dir / "bin" / version / target / "mnemo"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/usr/bin/env bash\necho "STUB ARGS: $*"\nexit 0\n')
    binary.chmod(0o755)
    return binary


@pytest.mark.parametrize("event", ["user_prompt_submit", "pre_tool_use", "session_end"])
def test_hot_path_hooks_never_download(plugin_root: Path, tmp_path: Path, event: str):
    """A cold cache must not stall every prompt behind a network round trip."""
    data = tmp_path / "data"

    result = run(plugin_root, data, "hook", event)

    assert result.returncode == 0
    assert result.stderr == "", "hot-path hooks must stay silent when cold"
    assert not (data / "bin").exists(), "must not have attempted a download"


def test_cold_session_start_attempts_a_download_and_still_exits_zero(
    plugin_root: Path, tmp_path: Path
):
    """SessionStart is the one hook allowed to fetch; version 9.9.9 does not exist."""
    result = run(plugin_root, tmp_path / "data")

    assert result.returncode == 0
    assert "install manually" in result.stderr or "fetching" in result.stderr


def test_execs_the_cached_binary_with_its_arguments(plugin_root: Path, tmp_path: Path):
    data = tmp_path / "data"
    install_fake_binary(data)

    result = run(plugin_root, data, "hook", "user_prompt_submit")

    assert result.returncode == 0
    assert "STUB ARGS: hook user_prompt_submit" in result.stdout


def test_passes_through_non_hook_commands(plugin_root: Path, tmp_path: Path):
    data = tmp_path / "data"
    install_fake_binary(data)

    result = run(plugin_root, data, "status")

    assert "STUB ARGS: status" in result.stdout


def test_a_missing_manifest_is_survivable(plugin_root: Path, tmp_path: Path):
    (plugin_root / ".claude-plugin" / "plugin.json").unlink()

    result = run(plugin_root, tmp_path / "data", "hook", "session_start")

    assert result.returncode == 0
    assert "cannot read the plugin version" in result.stderr


def test_a_malformed_manifest_is_survivable(plugin_root: Path, tmp_path: Path):
    (plugin_root / ".claude-plugin" / "plugin.json").write_text("{ not json")

    result = run(plugin_root, tmp_path / "data", "hook", "session_start")

    assert result.returncode == 0


def test_cache_is_keyed_by_version_so_an_update_refetches(plugin_root: Path, tmp_path: Path):
    """A stale binary must not survive a plugin upgrade."""
    data = tmp_path / "data"
    install_fake_binary(data, version="9.9.9")
    (plugin_root / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": "mnemo", "version": "9.9.10", "description": "x"})
    )

    result = run(plugin_root, data, "hook", "user_prompt_submit")

    assert result.returncode == 0
    assert "STUB ARGS" not in result.stdout, "must not exec the previous version's binary"


def test_refuses_to_install_without_a_checksum_tool(plugin_root: Path, tmp_path: Path):
    """Better no binary than an unverified one off the network."""
    fake_bin = tmp_path / "nobin"
    fake_bin.mkdir()
    # Everything the launcher needs EXCEPT shasum/sha256sum.
    for tool in ("bash", "curl", "tar", "uname", "sed", "awk", "mktemp",
                 "rm", "mkdir", "mv", "head", "dirname", "cat"):
        real = shutil.which(tool)
        if real:
            (fake_bin / tool).symlink_to(real)

    env = {
        **os.environ,
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
        "PATH": str(fake_bin),
    }
    result = subprocess.run(
        ["bash", str(plugin_root / "bin" / "launch"), "hook", "session_start"],
        capture_output=True, text=True, env=env, timeout=30, input="{}",
    )

    assert result.returncode == 0
    assert not (tmp_path / "data" / "bin" / "9.9.9").is_dir() or not list(
        (tmp_path / "data" / "bin" / "9.9.9").rglob("mnemo")
    ), "must not have installed an unverified binary"


def test_source_checkout_runs_python_when_plugin_root_unset(tmp_path: Path):
    """#118: opened as a project (no CLAUDE_PLUGIN_ROOT), the launcher runs the
    editable source tree instead of fetching a release binary."""
    root = tmp_path / "repo"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"version": "9.9.9"}')
    (root / "bin").mkdir()
    shutil.copy(LAUNCH, root / "bin" / "launch")
    (root / "src" / "mnemo").mkdir(parents=True)
    (root / "src" / "mnemo" / "__init__.py").write_text("")
    (root / "src" / "mnemo" / "__main__.py").write_text(
        "import sys; print('SOURCE-TREE', sys.argv[1:])\n"
    )
    (root / "src" / "mnemo_claude.egg-info").mkdir()
    env = {
        **os.environ,
        "CLAUDE_PLUGIN_DATA": str(tmp_path / "data"),
        # Absolute, so the sanitized PATH below cannot hide the interpreter.
        "MNEMO_PYTHON": sys.executable,
        # Keep the test off the network no matter which branch it reaches.
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    }
    env.pop("CLAUDE_PLUGIN_ROOT", None)
    r = subprocess.run(
        ["bash", str(root / "bin" / "launch"), "mcp-server"],
        capture_output=True, text=True, env=env, timeout=30, input="{}",
    )
    assert r.returncode == 0
    assert "SOURCE-TREE ['mcp-server']" in r.stdout
    assert not (tmp_path / "data").exists(), "no download may be attempted"


def test_plugin_root_set_ignores_source_tree(plugin_root: Path, tmp_path: Path):
    """A plugin clone also has src/; only the unset env var opens the dev path."""
    (plugin_root / "src" / "mnemo").mkdir(parents=True)
    (plugin_root / "src" / "mnemo" / "__init__.py").write_text("")
    (plugin_root / "src" / "mnemo" / "__main__.py").write_text(
        "print('SOURCE-TREE')\n"
    )
    (plugin_root / "src" / "mnemo_claude.egg-info").mkdir()
    data = tmp_path / "data"
    install_fake_binary(data)

    r = run(plugin_root, data, "--version")

    assert r.returncode == 0
    assert "SOURCE-TREE" not in r.stdout
    assert "STUB ARGS: --version" in r.stdout
