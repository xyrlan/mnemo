"""Regenerate the .claude-plugin/ manifests from the release version.

Plugin manifest is the alternative entry point for users who install via
/plugin marketplace. SLASH_COMMANDS in install/settings.py is the source
of truth for what commands mnemo exposes; this script keeps the manifest
aligned so the two install paths produce the same surface.

marketplace.json carries its own copy of the version, which is what the
/plugin marketplace UI shows. Nothing used to sync it and it drifted twelve
minors behind, so it is regenerated here too.

plugin/ is the same plugin again, in a folder that holds nothing else, for
the Claude plugin directory (#514). Its validator reads every file under the
plugin root, and at the repository root that is the changelog, the design
notes, the docs and the whole source tree: prose it reads as code, and more
files than it screens. The folder is written here, after the root copies, so
it is those copies byte for byte plus a LICENSE and a README for the listing.
It holds no marketplace file: the marketplace still installs the root.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


PLUGIN_NAME = "mnemo"


def _sync_marketplace(marketplace_path: Path, version: str) -> None:
    data = json.loads(marketplace_path.read_text(encoding="utf-8"))
    entries = [p for p in data.get("plugins", []) if p.get("name") == PLUGIN_NAME]
    if len(entries) != 1:
        raise SystemExit(
            f"Expected exactly one {PLUGIN_NAME!r} entry in {marketplace_path}, "
            f"found {len(entries)}"
        )
    entries[0]["version"] = version
    marketplace_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _sync_plugin_commands(commands_dir: Path) -> None:
    """Regenerate the plugin's commands/ directory from PLUGIN_COMMANDS."""
    from mnemo.install.settings import PLUGIN_COMMANDS, render_plugin_command

    commands_dir.mkdir(parents=True, exist_ok=True)
    expected = {f"{name}.md" for name in PLUGIN_COMMANDS}
    for name, spec in PLUGIN_COMMANDS.items():
        (commands_dir / f"{name}.md").write_text(render_plugin_command(spec), encoding="utf-8")
    # Drop files for commands that no longer exist, so a rename can't leave a
    # stale command behind that still invokes a subcommand we removed.
    for stale in commands_dir.glob("*.md"):
        if stale.name not in expected:
            stale.unlink()


def _sync_plugin_skills(skills_dir: Path) -> None:
    """Regenerate the plugin's skills/ directory from the packaged skills.

    Claude Code loads a plugin's skills from ``skills/<name>/SKILL.md`` at
    the plugin root, and a wheel can only carry files inside the package, so
    the file exists twice. The package copy is the one that gets edited; this
    keeps the plugin's copy identical, as it does for commands/.
    """
    from mnemo.install.settings import SKILLS, read_skill

    skills_dir.mkdir(parents=True, exist_ok=True)
    for name in SKILLS:
        target = skills_dir / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(read_skill(name), encoding="utf-8")
    # A skill that left the package must leave the plugin too, or the plugin
    # keeps offering something `mnemo init` no longer installs.
    for stale in skills_dir.iterdir():
        if stale.is_dir() and stale.name not in SKILLS:
            skill = stale / "SKILL.md"
            if skill.exists():
                skill.unlink()
            if not any(stale.iterdir()):
                stale.rmdir()


# What the plugin ships, relative to the repository root. commands/ and
# skills/ are added from disk, since the sync above has just written them.
SUBFOLDER = "plugin"
SHIPPED_FILES = (
    ".claude-plugin/plugin.json",
    ".claude-plugin/icon.svg",
    "hooks/hooks.json",
    "bin/launch",
    "bin/mnemo.cmd",
    ".mcp.json",
    "LICENSE",
)

GITHUB = "https://github.com/xyrlan/mnemo"

# Links are absolute: the listing shows this file on its own, and a relative
# link from plugin/ would point into a folder that holds none of the docs.
SUBFOLDER_README = f"""\
# mnemo

> mnemo keeps what your Claude Code sessions learned, puts the relevant piece
> in front of the next prompt, and fans out sessions that start out knowing it.

You correct Claude once — *"never use npm in this repo, always yarn"* — and
mnemo turns the correction into a rule in a local Markdown vault. The next
session that touches the subject gets that rule injected before Claude
answers: one or two short lines, only when it clearly applies. A rule that
recurs in two different repos follows you everywhere.

## Install

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
```

The plugin ships a launcher, not a binary: on first use `bin/launch` fetches
the mnemo binary for your platform from the project's GitHub Releases
(checksum-verified) and caches it per version. Restart Claude Code and it is
running.

## Use

- `/mnemo:why` — why a rule was injected on your last prompts, or wasn't.
- `/mnemo:status` — vault state and hook health.
- `/mnemo:doctor` — what is wrong, if something is.
- `/mnemo:learn` — extract rules from your sessions now.
- `/mnemo:help` — everything else.

## Privacy

Local by default. Three switches can make a network call, all off until you
turn them on:

- `autopilot.network.enabled`;
- `recall.rerank.provider`, which sends the query of a `list_rules_by_topic`
  call and the first 800 characters of each rule in that topic to a
  third-party ranking model;
- `reflex.judge.provider`, which sends the first 1,200 characters of the
  prompts you type, plus up to three candidate rules, to the same model. It
  has its own consent, and `mnemo rerank --off` turns it off with the rest.

Nothing else leaves your machine. The vault and every log stay on disk. LLM
calls go through the `claude` CLI you already have, never on the prompt path.
The one other outbound call is the binary download above.
[What is sent, and why]({GITHUB}/blob/master/docs/configuration.md).

## More

- [Full README]({GITHUB}#readme)
- [Getting started]({GITHUB}/blob/master/docs/getting-started.md)
- [Configuration]({GITHUB}/blob/master/docs/configuration.md)
- [Troubleshooting]({GITHUB}/blob/master/docs/troubleshooting.md)
- [Source]({GITHUB})

MIT licensed — see [LICENSE](LICENSE).
"""


def _shipped_files(repo_root: Path) -> "list[str]":
    files = list(SHIPPED_FILES)
    files += sorted(p.relative_to(repo_root).as_posix()
                    for p in (repo_root / "commands").glob("*.md"))
    files += sorted(p.relative_to(repo_root).as_posix()
                    for p in (repo_root / "skills").glob("*/SKILL.md"))
    return files


def _sync_plugin_subfolder(repo_root: Path) -> None:
    """Rebuild plugin/: the shipped files copied from the root, and a README."""
    out = repo_root / SUBFOLDER
    expected = set()
    for rel in _shipped_files(repo_root):
        src = repo_root / rel
        if not src.is_file():
            raise SystemExit(f"{rel} is part of the plugin but missing from {repo_root}")
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        # copy2 keeps the exec bit bin/launch and bin/mnemo.cmd need.
        shutil.copy2(src, dest)
        expected.add(dest)
    readme = out / "README.md"
    # LF on every platform, so a Windows run doesn't read as drift.
    with readme.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(SUBFOLDER_README)
    expected.add(readme)
    # Anything else in plugin/ is something the plugin no longer ships.
    for path in sorted(out.rglob("*"), reverse=True):
        if path.is_file() and path not in expected:
            path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def sync(repo_root: Path, version: str) -> None:
    sys.path.insert(0, str(repo_root / "src"))

    plugin_dir = repo_root / ".claude-plugin"
    manifest_path = plugin_dir / "plugin.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["version"] = version
    # The manifest used to carry a `commands` array. Claude Code discovers
    # commands from the commands/ directory instead, and every entry in that
    # array invoked `python3 -m mnemo`, which a plugin install has no way to
    # run. Generating the directory (below) replaces it.
    data.pop("commands", None)
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    _sync_plugin_commands(repo_root / "commands")
    _sync_plugin_skills(repo_root / "skills")
    _sync_marketplace(plugin_dir / "marketplace.json", version)
    _sync_plugin_subfolder(repo_root)


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    import re
    pyproject_text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    version = m.group(1) if m else "0.0.0"
    sync(repo_root, version)
    print(f".claude-plugin/{{plugin,marketplace}}.json, commands/, skills/ and plugin/ regenerated (version {version})")
