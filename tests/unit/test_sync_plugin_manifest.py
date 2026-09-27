import json
import os
import sys
from pathlib import Path

import pytest

from tools import sync_plugin_manifest


def _plugin_dir(tmp_path: Path) -> Path:
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "name": "mnemo", "version": "0.0.0", "description": "x",
        "commands": [],
    }), encoding="utf-8")
    (manifest.parent / "marketplace.json").write_text(json.dumps({
        "name": "mnemo-marketplace",
        "plugins": [{"name": "mnemo", "source": "github:xyrlan/mnemo", "version": "0.4.0"}],
    }), encoding="utf-8")
    (manifest.parent / "icon.svg").write_text("<svg/>", encoding="utf-8")
    # The rest of what the plugin ships, so plugin/ can be built from it.
    for rel in ("hooks/hooks.json", "bin/launch", "bin/mnemo.cmd", ".mcp.json", "LICENSE"):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"root copy of {rel}\n", encoding="utf-8")
    (tmp_path / "bin" / "launch").chmod(0o755)
    return manifest.parent


def test_sync_bumps_the_manifest_version(tmp_path: Path):
    manifest = _plugin_dir(tmp_path) / "plugin.json"

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.12.0")

    assert json.loads(manifest.read_text(encoding="utf-8"))["version"] == "0.12.0"


def test_sync_drops_the_legacy_commands_array(tmp_path: Path):
    """Claude Code reads commands/ ; the array only invited drift.

    Every entry in it also invoked `python3 -m mnemo`, which a plugin install
    has no way to run.
    """
    manifest = _plugin_dir(tmp_path) / "plugin.json"

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    assert "commands" not in json.loads(manifest.read_text(encoding="utf-8"))


def test_sync_generates_the_plugin_command_files(tmp_path: Path):
    _plugin_dir(tmp_path)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    commands = tmp_path / "commands"
    names = {p.stem for p in commands.glob("*.md")}
    # Exactly the plugin surface — the generated directory IS the slash menu,
    # so an extra file here is an extra entry a user has to read past.
    # init/uninstall have no meaning under a plugin: it declares its own hooks
    # and MCP server, and `/plugin uninstall mnemo` is the uninstall.
    assert names == {"status", "why", "doctor", "learn", "dispatch", "help"}


def test_generated_commands_go_through_the_launcher(tmp_path: Path):
    """Never a resolved path — the plugin is built once, installed everywhere."""
    _plugin_dir(tmp_path)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    body = (tmp_path / "commands" / "status.md").read_text(encoding="utf-8")
    assert '!`"${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd" status`' in body
    assert "python3" not in body


def test_sync_removes_a_command_file_that_no_longer_exists(tmp_path: Path):
    _plugin_dir(tmp_path)
    commands = tmp_path / "commands"
    commands.mkdir()
    (commands / "renamed-away.md").write_text("stale", encoding="utf-8")

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    assert not (commands / "renamed-away.md").exists()


def test_sync_also_bumps_the_marketplace_listing(tmp_path: Path):
    """marketplace.json drifted 12 minors behind because nothing synced it."""
    marketplace = _plugin_dir(tmp_path) / "marketplace.json"

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    entry = json.loads(marketplace.read_text(encoding="utf-8"))["plugins"][0]
    assert entry["version"] == "0.16.0"
    assert entry["source"] == "github:xyrlan/mnemo"  # untouched


def test_sync_fails_loudly_when_the_marketplace_lacks_an_mnemo_entry(tmp_path: Path):
    marketplace = _plugin_dir(tmp_path) / "marketplace.json"
    marketplace.write_text(json.dumps({"name": "mnemo-marketplace", "plugins": []}), encoding="utf-8")

    with pytest.raises(SystemExit, match="mnemo"):
        sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")


def test_sync_generates_the_plugin_skill_files(tmp_path: Path):
    """Claude Code loads a plugin's skills from skills/<name>/SKILL.md by
    convention, so the packaged copy has to be mirrored there (#233)."""
    from mnemo.install.settings import SKILLS, read_skill

    _plugin_dir(tmp_path)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    for name in SKILLS:
        copy = tmp_path / "skills" / name / "SKILL.md"
        assert copy.read_text(encoding="utf-8") == read_skill(name)
        assert copy.read_text(encoding="utf-8").startswith("---\nname: " + name)


def test_sync_removes_a_skill_that_left_the_package(tmp_path: Path):
    _plugin_dir(tmp_path)
    stale = tmp_path / "skills" / "renamed-away"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("stale", encoding="utf-8")

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    assert not stale.exists()


def _files(root: Path) -> set:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def test_sync_builds_the_plugin_subfolder_from_the_root_copies(tmp_path: Path):
    """plugin/ is what the plugin ships and nothing else (#514)."""
    _plugin_dir(tmp_path)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    sub = tmp_path / "plugin"
    shipped = _files(sub) - {"README.md"}
    assert ".claude-plugin/marketplace.json" not in shipped, "the marketplace still installs the root"
    assert {".claude-plugin/plugin.json", ".claude-plugin/icon.svg", "hooks/hooks.json",
            "bin/launch", "bin/mnemo.cmd", ".mcp.json", "LICENSE",
            "commands/status.md"} <= shipped
    assert {f for f in shipped if f.startswith("skills/")}, "skills ship too"
    for rel in shipped:
        assert (sub / rel).read_bytes() == (tmp_path / rel).read_bytes(), rel
    assert json.loads((sub / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"] == "0.16.0"


def test_the_subfolder_launcher_stays_executable(tmp_path: Path):
    _plugin_dir(tmp_path)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    assert os.access(tmp_path / "plugin" / "bin" / "launch", os.X_OK) or sys.platform == "win32"


def test_sync_drops_what_the_subfolder_no_longer_ships(tmp_path: Path):
    _plugin_dir(tmp_path)
    stale = tmp_path / "plugin" / "commands" / "renamed-away.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("stale", encoding="utf-8")
    (tmp_path / "plugin" / "old" / "dir").mkdir(parents=True)

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")

    assert not stale.exists()
    assert not (tmp_path / "plugin" / "old").exists()


def test_sync_refuses_a_subfolder_missing_a_shipped_file(tmp_path: Path):
    _plugin_dir(tmp_path)
    (tmp_path / ".mcp.json").unlink()

    with pytest.raises(SystemExit, match=r"\.mcp\.json"):
        sync_plugin_manifest.sync(repo_root=tmp_path, version="0.16.0")


def test_the_subfolder_readme_links_only_absolutely():
    """A relative link in plugin/README.md points into a folder with no docs."""
    import re
    readme = sync_plugin_manifest.SUBFOLDER_README
    targets = re.findall(r"\]\(([^)]+)\)", readme)
    assert targets
    for target in targets:
        assert target.startswith("https://") or target == "LICENSE", target
    assert "## Privacy" in readme
    for switch in ("autopilot.network.enabled", "recall.rerank.provider", "reflex.judge.provider"):
        assert switch in readme
    assert "/mnemo:why" in readme


def test_plugin_commands_allow_only_the_line_they_inject():
    """`allowed-tools: Bash` is flagged as broad by the plugin directory (#514).

    The narrowed rule was checked live with `claude --plugin-dir`: this form
    runs the injected line, and a rule naming another subcommand blocks it.
    """
    from mnemo.install.settings import PLUGIN_COMMANDS, render_plugin_command

    root = '"${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd"'
    for name, spec in PLUGIN_COMMANDS.items():
        body = render_plugin_command(spec)
        sub = " ".join(spec["args"])
        rule = f"Bash({root} {sub}:*)" if spec.get("arguments") else f"Bash({root} {sub})"
        assert f"\nallowed-tools: {rule}\n" in body, name
        assert "\nallowed-tools: Bash\n" not in body, name
