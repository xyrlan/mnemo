# `npx mnemo install` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace mnemo's three-paradigm install flow (pip + /plugin marketplace add + /plugin install) with a single command — `npx mnemo install` — backed by a thin npm wrapper that bootstraps Python via `uv`/`pipx`/`pip --user`, prompts for global vs project scope, and runs `mnemo init` (which now also registers slash commands directly into `~/.claude/settings.json`).

**Architecture:** Two coupled changes ship together. (1) Python-side: `mnemo init` gains `inject_slash_commands` so the `/plugin marketplace` dance becomes optional. (2) npm wrapper: `npm/` package (~150 LOC Node, zero runtime deps) detects Python, picks the best installer, installs `mnemo` from PyPI, and invokes `mnemo init`. Plugin manifest stays as an alternative entry point.

**Tech Stack:** Node 18+ (built-in `node:test`, `child_process`, `readline`); Python 3.8+ stdlib (no new deps); GitHub Actions for `release.yml`; PyPI + npm registries.

**Spec:** `docs/superpowers/specs/2026-04-25-npx-install-bootstrap-design.md`

---

## Phase 0 — PyPI prerequisite (P1-P5)

PyPI publish is a hard prerequisite per the spec. The npm wrapper bootstrap calls `pipx install mnemo` which only works if mnemo is on PyPI. Phase 0 is net-new release infrastructure — no `release.yml` exists today.

### Task P1: Bump `pyproject.toml` version to 0.12.0

**Files:**
- Modify: `pyproject.toml:8`

- [ ] **Step 1: Read current version**

Run: `grep '^version' pyproject.toml`
Expected: `version = "0.11.0"`

- [ ] **Step 2: Bump to 0.12.0**

```toml
# pyproject.toml line 8
version = "0.12.0"
```

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "release: bump to 0.12.0 (catches up to merged v0.12 work)"
```

### Task P2: Bump `.claude-plugin/plugin.json` version to 0.12.0

**Files:**
- Modify: `.claude-plugin/plugin.json:3`

- [ ] **Step 1: Edit version field**

```json
{
  "name": "mnemo",
  "version": "0.12.0",
  ...
}
```

- [ ] **Step 2: Commit**

```bash
git add .claude-plugin/plugin.json
git commit -m "chore(plugin): sync manifest version to 0.12.0"
```

### Task P3: Create `tools/` directory and `tools/sync_npm_version.py`

**Files:**
- Create: `tools/sync_npm_version.py`
- Create: `tools/__init__.py` (empty — keeps `tools/` discoverable but not a package)

Note: `tools/` is net-new per spec. Convention: standalone Python scripts invoked by name.

- [ ] **Step 1: Write failing test**

Create: `tests/unit/test_sync_npm_version.py`

```python
import json
from pathlib import Path

import pytest

from tools import sync_npm_version


def test_sync_npm_version_reads_pyproject_and_writes_npm_package_json(tmp_path: Path):
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "mnemo"\nversion = "0.12.0"\n')
    npm_dir = tmp_path / "npm"
    npm_dir.mkdir()
    npm_pkg = npm_dir / "package.json"
    npm_pkg.write_text(json.dumps({"name": "mnemo", "version": "0.0.0"}))

    sync_npm_version.sync(repo_root=tmp_path)

    data = json.loads(npm_pkg.read_text())
    assert data["version"] == "0.12.0"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `PYTHONPATH=$(pwd) pytest tests/unit/test_sync_npm_version.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.sync_npm_version'`

- [ ] **Step 3: Implement `tools/sync_npm_version.py`**

```python
"""Sync npm/package.json version from pyproject.toml.

Single source of truth for mnemo version is pyproject.toml. The npm
wrapper (`npm/package.json`) is regenerated from it before each
`npm publish` so PyPI and npm versions stay aligned.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _read_pyproject_version(pyproject_path: Path) -> str:
    text = pyproject_path.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise SystemExit(f"Could not find version in {pyproject_path}")
    return m.group(1)


def sync(repo_root: Path) -> str:
    pyproject = repo_root / "pyproject.toml"
    npm_pkg = repo_root / "npm" / "package.json"
    version = _read_pyproject_version(pyproject)
    data = json.loads(npm_pkg.read_text())
    data["version"] = version
    npm_pkg.write_text(json.dumps(data, indent=2) + "\n")
    return version


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    version = sync(repo_root)
    print(f"npm/package.json version → {version}")
```

Create empty `tools/__init__.py`:

```python
```

- [ ] **Step 4: Run test, verify it passes**

Run: `PYTHONPATH=$(pwd) pytest tests/unit/test_sync_npm_version.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tools/__init__.py tools/sync_npm_version.py tests/unit/test_sync_npm_version.py
git commit -m "tools: sync_npm_version reads pyproject as source of truth"
```

### Task P4: Create `.github/workflows/release.yml` with `publish-pypi` job

**Files:**
- Create: `.github/workflows/release.yml`

Note: `release.yml` is net-new. CI today is only `ci.yml`.

- [ ] **Step 1: Create workflow**

```yaml
name: Release

on:
  push:
    tags: ["v*"]

jobs:
  publish-pypi:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write  # for trusted publishing (optional alt to API token)
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Verify tag matches pyproject.toml version
        run: |
          TAG_VERSION="${GITHUB_REF_NAME#v}"
          PYPROJECT_VERSION=$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2)
          if [ "$TAG_VERSION" != "$PYPROJECT_VERSION" ]; then
            echo "Tag $GITHUB_REF_NAME does not match pyproject.toml version $PYPROJECT_VERSION"
            exit 1
          fi

      - name: Install build tooling
        run: python -m pip install --upgrade build

      - name: Build sdist + wheel
        run: python -m build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        with:
          password: ${{ secrets.PYPI_API_TOKEN }}
          packages-dir: dist/
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci: add release.yml with publish-pypi job triggered by v* tags"
```

### Task P5: Tag v0.12.0 and verify PyPI publish

**Files:** none (tag + manual verification)

- [ ] **Step 1: Confirm `PYPI_API_TOKEN` secret exists in repo settings**

Manual: Visit `https://github.com/xyrlan/mnemo/settings/secrets/actions`. If absent, generate a project-scoped token at `https://pypi.org/manage/account/token/` (project = mnemo, after first publish; for very first publish, use a user-wide token, then narrow it).

- [ ] **Step 2: For first publish only, register the project on PyPI**

Manual (one-time): `python -m build && twine upload --repository pypi dist/*` from local machine using a user-wide token. PyPI requires manual project creation on first upload.

- [ ] **Step 3: Tag v0.12.0**

```bash
git tag v0.12.0
git push origin v0.12.0
```

- [ ] **Step 4: Watch the release workflow**

Manual: Visit `https://github.com/xyrlan/mnemo/actions` and verify `publish-pypi` job succeeds. Check `https://pypi.org/project/mnemo/` shows v0.12.0.

- [ ] **Step 5: Verify pipx install works end-to-end**

In a clean Linux environment (Docker `ubuntu:22.04` or similar):

```bash
apt-get update && apt-get install -y python3-pip python3-venv pipx
pipx install mnemo
mnemo --version
# Expected: 0.12.0
```

If this fails, **Phase 1+ are blocked** — fix the publish before proceeding.

---

## Phase 1 — Python-side slash command registration

`mnemo init` learns to write slash commands into `~/.claude/settings.json` (or `<cwd>/.claude/settings.json` for `--project`). Two coupled pieces: (a) the channel-discovery spike, (b) the `inject_slash_commands` function following the existing `inject_*` pattern.

### Task 1: Channel discovery spike

**Files:** none (research only)

Goal: confirm which file Claude Code reads user-defined slash commands from. Spec accepts two candidates: `~/.claude/settings.json > customCommands` (preferred) or `~/.claude/commands/<name>.json`. Fallback if neither: leave the `/plugin install` instruction.

- [ ] **Step 1: Read Claude Code docs**

Use WebFetch on `https://docs.claude.com/en/docs/claude-code/slash-commands` (or current canonical URL). Look for "user-defined commands", "custom commands", "settings.json customCommands", or any mention of `~/.claude/commands/`.

- [ ] **Step 2: Inspect a fresh `/plugin install` artifact**

Manual: in a clean `~/.claude/`, run `/plugin marketplace add xyrlan/mnemo && /plugin install mnemo@mnemo-marketplace`. Then:

```bash
find ~/.claude -newer /tmp/before-marker -type f 2>/dev/null
```

Note any new files. The directory and filename pattern reveals the channel.

- [ ] **Step 3: Record findings in `docs/superpowers/specs/2026-04-25-npx-install-bootstrap-design.md`**

Edit the "Slash command registration" section to replace "(channel to confirm at implementation)" with the confirmed channel and exact file/key location. If no channel was found, mark it explicitly: `Channel: not found — fallback path engaged.`

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-04-25-npx-install-bootstrap-design.md
git commit -m "spec: confirm slash-command registration channel via spike"
```

### Task 2: `SLASH_COMMANDS` constant + `inject_slash_commands` (TDD)

**Files:**
- Modify: `src/mnemo/install/settings.py` (append after `_do_uninject_statusline`)
- Test: `tests/integration/test_inject_slash_commands.py` (new)

Note: implementation depends on Task 1 outcome. The function below assumes channel #1 (`settings.json > customCommands`). If the spike picked channel #2, adapt the function body to write `~/.claude/commands/<name>.json` files (one per command). The constant + tests stay the same.

- [ ] **Step 1: Write failing test**

Create `tests/integration/test_inject_slash_commands.py`:

```python
import json
from pathlib import Path

import pytest

from mnemo.install import settings as inj


def test_inject_slash_commands_writes_all_nine_commands(tmp_path: Path):
    target = tmp_path / "settings.json"
    inj.inject_slash_commands(target)
    data = json.loads(target.read_text())
    cmds = data.get("customCommands") or {}
    expected = {
        "init", "init-project", "status", "doctor",
        "open", "fix", "uninstall", "uninstall-project", "help",
    }
    assert expected.issubset(set(cmds.keys()))


def test_inject_slash_commands_idempotent(tmp_path: Path):
    target = tmp_path / "settings.json"
    inj.inject_slash_commands(target)
    inj.inject_slash_commands(target)
    data = json.loads(target.read_text())
    cmds = data.get("customCommands") or {}
    # No duplicates: each name appears once
    assert len(cmds) == 9


def test_uninject_slash_commands_strips_only_mnemo(tmp_path: Path):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "customCommands": {
            "other-cmd": {"command": "echo hi"},
        },
    }))
    inj.inject_slash_commands(target)
    inj.uninject_slash_commands(target)
    data = json.loads(target.read_text())
    cmds = data.get("customCommands") or {}
    assert "other-cmd" in cmds
    assert "init" not in cmds
```

- [ ] **Step 2: Run test, verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/integration/test_inject_slash_commands.py -v`
Expected: FAIL with `AttributeError: module 'mnemo.install.settings' has no attribute 'inject_slash_commands'`

- [ ] **Step 3: Implement constant + function**

Append to `src/mnemo/install/settings.py`:

```python
# --- v0.13: slash command registration (replaces /plugin install dance) ---


SLASH_COMMAND_TAG = "-m mnemo "  # substring of every slash command we emit; distinct from MNEMO_TAG


SLASH_COMMANDS: dict[str, dict[str, str]] = {
    "init":              {"description": "first-run setup (global)",
                          "command": "python3 -m mnemo init"},
    "init-project":      {"description": "first-run setup scoped to <cwd> (v0.12+)",
                          "command": "python3 -m mnemo init --project"},
    "status":            {"description": "vault state + hook health",
                          "command": "python3 -m mnemo status"},
    "doctor":            {"description": "full diagnostic",
                          "command": "python3 -m mnemo doctor"},
    "open":              {"description": "open vault in Obsidian",
                          "command": "python3 -m mnemo open"},
    "fix":               {"description": "reset circuit breaker",
                          "command": "python3 -m mnemo fix"},
    "uninstall":         {"description": "remove hooks (global; keeps vault)",
                          "command": "python3 -m mnemo uninstall"},
    "uninstall-project": {"description": "remove hooks (project-scoped; keeps vault)",
                          "command": "python3 -m mnemo uninstall --project"},
    "help":              {"description": "list commands",
                          "command": "python3 -m mnemo help"},
}


def inject_slash_commands(settings_path: Path) -> None:
    """Register mnemo slash commands in settings.json. Idempotent."""
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_inject_slash_commands(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_inject_slash_commands(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    cmds = data.setdefault("customCommands", {})
    # Strip stale mnemo entries first (handles renames between versions)
    for name in list(cmds.keys()):
        if SLASH_COMMAND_TAG in (cmds.get(name) or {}).get("command", ""):
            del cmds[name]
    # Re-register current set
    for name, spec in SLASH_COMMANDS.items():
        cmds[name] = dict(spec)
    settings_path.write_text(json.dumps(data, indent=2))


def uninject_slash_commands(settings_path: Path) -> None:
    """Remove mnemo slash commands; preserve any third-party customCommands."""
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_uninject_slash_commands(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_uninject_slash_commands(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    cmds = data.get("customCommands", {})
    for name in list(cmds.keys()):
        if SLASH_COMMAND_TAG in (cmds.get(name) or {}).get("command", ""):
            del cmds[name]
    if not cmds:
        data.pop("customCommands", None)
    settings_path.write_text(json.dumps(data, indent=2))
```

- [ ] **Step 4: Run test, verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/integration/test_inject_slash_commands.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/install/settings.py tests/integration/test_inject_slash_commands.py
git commit -m "feat(install): inject_slash_commands writes all 9 commands to settings.json"
```

### Task 3: Wire `inject_slash_commands` into `cmd_init` and `cmd_uninstall` (TDD)

**Files:**
- Modify: `src/mnemo/cli/commands/init.py:170-180` (after `inject_statusline` call)
- Modify: `src/mnemo/cli/commands/misc.py:cmd_uninstall` (after `uninject_statusline`)
- Test: `tests/integration/test_cli_init.py` (append)

- [ ] **Step 1: Write failing test**

Append to `tests/integration/test_cli_init.py`:

```python
def test_init_registers_slash_commands(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    data = json.loads((tmp_home / ".claude" / "settings.json").read_text())
    cmds = data.get("customCommands") or {}
    assert "init-project" in cmds
    assert cmds["init-project"]["command"] == "python3 -m mnemo init --project"


def test_uninstall_strips_slash_commands(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    cli.main(["uninstall", "--yes"])
    data = json.loads((tmp_home / ".claude" / "settings.json").read_text())
    cmds = data.get("customCommands") or {}
    assert "init-project" not in cmds
    assert "init" not in cmds
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/integration/test_cli_init.py::test_init_registers_slash_commands tests/integration/test_cli_init.py::test_uninstall_strips_slash_commands -v`
Expected: FAIL with `assert 'init-project' in {}` (or similar — customCommands key absent).

- [ ] **Step 3: Wire into `cmd_init`**

In `src/mnemo/cli/commands/init.py`, locate the block after the `inject_statusline` call (around line 170-180) and add:

```python
    # 5d. Register slash commands (replaces the /plugin install dance)
    say(f"Registering slash commands in {target_settings}…")
    try:
        inj.inject_slash_commands(target_settings)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
```

(Insert after the existing `inject_statusline` block, before the `if project: _ensure_gitignore(cwd)` line.)

- [ ] **Step 4: Wire into `cmd_uninstall`**

In `src/mnemo/cli/commands/misc.py`, in `cmd_uninstall`, after `inj.uninject_statusline(...)` add:

```python
        inj.uninject_slash_commands(settings_path)
```

So the try block becomes (existing + new line):

```python
        vault = cli._resolve_vault()
        inj.uninject_statusline(settings_path, vault)
        inj.uninject_slash_commands(settings_path)
        inj.uninject_hooks(settings_path)
        inj.uninject_mcp_servers(mcp_path)
```

- [ ] **Step 5: Run tests, verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/integration/test_cli_init.py -v`
Expected: PASS (20 tests = 18 baseline + 2 new)

- [ ] **Step 6: Run full Python suite**

Run: `PYTHONPATH=$(pwd)/src pytest -q`
Expected: 1115 passed (1113 baseline + 2 new), 2 skipped

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/commands/init.py src/mnemo/cli/commands/misc.py tests/integration/test_cli_init.py
git commit -m "feat(init): register slash commands during init; uninstall strips them"
```

### Task 4: `tools/sync_plugin_manifest.py` — generate `.claude-plugin/plugin.json` from `SLASH_COMMANDS`

**Files:**
- Create: `tools/sync_plugin_manifest.py`
- Test: `tests/unit/test_sync_plugin_manifest.py`

Goal: keep the plugin manifest (alternative entry point) in sync with the SLASH_COMMANDS source-of-truth so we don't drift between npx-install and /plugin-marketplace paths.

- [ ] **Step 1: Write failing test**

```python
import json
from pathlib import Path

import pytest

from tools import sync_plugin_manifest


def test_sync_plugin_manifest_uses_slash_commands(tmp_path: Path, monkeypatch):
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({
        "name": "mnemo", "version": "0.0.0", "description": "x",
        "commands": [],
    }))

    sync_plugin_manifest.sync(repo_root=tmp_path, version="0.12.0")

    data = json.loads(manifest.read_text())
    names = [c["name"] for c in data["commands"]]
    assert "init-project" in names
    assert "uninstall-project" in names
    assert data["version"] == "0.12.0"
```

- [ ] **Step 2: Run test, verify it fails**

Run: `PYTHONPATH=$(pwd):$(pwd)/src pytest tests/unit/test_sync_plugin_manifest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.sync_plugin_manifest'`

- [ ] **Step 3: Implement**

```python
"""Regenerate .claude-plugin/plugin.json from mnemo.install.settings.SLASH_COMMANDS.

Plugin manifest is the alternative entry point for users who install via
/plugin marketplace. SLASH_COMMANDS in install/settings.py is the source
of truth for what commands mnemo exposes; this script keeps the manifest
aligned so the two install paths produce the same surface.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def sync(repo_root: Path, version: str) -> None:
    sys.path.insert(0, str(repo_root / "src"))
    from mnemo.install.settings import SLASH_COMMANDS

    manifest_path = repo_root / ".claude-plugin" / "plugin.json"
    data = json.loads(manifest_path.read_text())
    data["version"] = version
    data["commands"] = [
        {"name": name, "description": spec["description"], "command": spec["command"]}
        for name, spec in SLASH_COMMANDS.items()
    ]
    manifest_path.write_text(json.dumps(data, indent=2) + "\n")


if __name__ == "__main__":
    repo_root = Path(__file__).resolve().parent.parent
    import re
    pyproject_text = (repo_root / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject_text, re.MULTILINE)
    version = m.group(1) if m else "0.0.0"
    sync(repo_root, version)
    print(f".claude-plugin/plugin.json regenerated (version {version})")
```

- [ ] **Step 4: Run test, verify it passes**

Run: `PYTHONPATH=$(pwd):$(pwd)/src pytest tests/unit/test_sync_plugin_manifest.py -v`
Expected: PASS

- [ ] **Step 5: Run script against real manifest, verify diff**

Run: `python3 tools/sync_plugin_manifest.py && git diff .claude-plugin/plugin.json`
Expected: No diff (manifest already in sync after PR #56). If diff exists, review and stage.

- [ ] **Step 6: Commit**

```bash
git add tools/sync_plugin_manifest.py tests/unit/test_sync_plugin_manifest.py .claude-plugin/plugin.json
git commit -m "tools: sync_plugin_manifest regenerates plugin.json from SLASH_COMMANDS"
```

---

## Phase 2 — npm wrapper

The wrapper is ~150 LOC across 5 small files. Each task creates one file with TDD via `node:test`.

### Task 5: `npm/package.json` skeleton + `bin/mnemo.js` dispatcher

**Files:**
- Create: `npm/package.json`
- Create: `npm/bin/mnemo.js`
- Create: `npm/README.md`
- Create: `npm/.npmignore`

- [ ] **Step 1: Verify name availability**

Run: `npm view mnemo 2>&1 | head -5`
Expected: either `npm ERR! 404` (name free → use `mnemo`) or version listing (taken → use `@xyrlan/mnemo`).

Record decision below; the rest of the task assumes `mnemo`. If scoped, replace `"name": "mnemo"` with `"name": "@xyrlan/mnemo"` and add `"publishConfig": { "access": "public" }`.

- [ ] **Step 2: Create `npm/package.json`**

```json
{
  "name": "mnemo",
  "version": "0.12.0",
  "description": "One-command installer for mnemo (the Obsidian that populates itself).",
  "bin": { "mnemo": "bin/mnemo.js" },
  "engines": { "node": ">=18" },
  "license": "MIT",
  "repository": {
    "type": "git",
    "url": "git+https://github.com/xyrlan/mnemo.git"
  },
  "homepage": "https://github.com/xyrlan/mnemo",
  "scripts": {
    "test": "node --test test/"
  },
  "files": ["bin/", "lib/", "README.md"]
}
```

- [ ] **Step 3: Create `npm/bin/mnemo.js`**

```javascript
#!/usr/bin/env node
"use strict";

const { runInstall } = require("../lib/runInstall");
const { runUninstall } = require("../lib/runUninstall");

function printHelp() {
  process.stdout.write([
    "Usage: npx mnemo <command> [flags]",
    "",
    "Commands:",
    "  install      Install mnemo (Python) + register hooks/slash commands",
    "  uninstall    Remove mnemo from this scope (vault preserved)",
    "  help         Show this message",
    "",
    "Install flags:",
    "  --global             Install globally (default)",
    "  --project, --local   Install only in the current directory",
    "  --vault-root <path>  Override vault location",
    "  --upgrade            Force upgrade if already installed",
    "  --yes, -y            Non-interactive (default: global)",
    "  --quiet              Suppress informational output",
    "",
    "Uninstall flags:",
    "  --scope global|project|both",
    "  --yes, -y",
    "  --quiet",
    "",
  ].join("\n"));
}

async function main() {
  const argv = process.argv.slice(2);
  const cmd = argv[0];
  const rest = argv.slice(1);
  switch (cmd) {
    case "install":
      process.exit(await runInstall(rest));
    case "uninstall":
      process.exit(await runUninstall(rest));
    case undefined:
    case "help":
    case "--help":
    case "-h":
      printHelp();
      process.exit(0);
    default:
      process.stderr.write(`unknown command: ${cmd}\n`);
      printHelp();
      process.exit(2);
  }
}

main().catch((err) => {
  process.stderr.write(`fatal: ${err && err.message ? err.message : err}\n`);
  process.exit(1);
});
```

- [ ] **Step 4: Create `npm/README.md`**

```markdown
# mnemo (npm wrapper)

One-command installer for [mnemo](https://github.com/xyrlan/mnemo).

```
npx mnemo install              # global, default
npx mnemo install --project    # only in <cwd>
npx mnemo uninstall
```

This package is a thin Node bootstrap. The actual mnemo runtime is a Python
package installed automatically via `uv`, `pipx`, or `pip --user` (whichever
is available). Python 3.8+ is required.

See the main project README for usage details.
```

- [ ] **Step 5: Create `npm/.npmignore`**

```
test/
.github/
*.log
```

- [ ] **Step 6: Make bin executable**

```bash
chmod +x npm/bin/mnemo.js
```

- [ ] **Step 7: Commit**

```bash
git add npm/
git commit -m "feat(npm): scaffold wrapper package with bin dispatcher"
```

### Task 6: `lib/detect.js` — Python + installer detection (TDD)

**Files:**
- Create: `npm/lib/detect.js`
- Create: `npm/test/detect.test.js`

- [ ] **Step 1: Write failing tests**

Create `npm/test/detect.test.js`:

```javascript
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parsePythonVersion, pickInstaller, isPep668 } = require("../lib/detect");

test("parsePythonVersion accepts CPython 3.11", () => {
  assert.deepEqual(parsePythonVersion("Python 3.11.4"), { major: 3, minor: 11, patch: 4 });
});

test("parsePythonVersion rejects below 3.8", () => {
  const v = parsePythonVersion("Python 3.7.16");
  assert.equal(v.major, 3);
  assert.equal(v.minor, 7);
});

test("parsePythonVersion returns null for garbage", () => {
  assert.equal(parsePythonVersion("not a version"), null);
});

test("pickInstaller prefers uv when present", () => {
  const fakeProbe = (cmd) => cmd === "uv" || cmd === "pipx" || cmd === "pip";
  assert.equal(pickInstaller(fakeProbe), "uv");
});

test("pickInstaller falls back to pipx when uv missing", () => {
  const fakeProbe = (cmd) => cmd === "pipx" || cmd === "pip";
  assert.equal(pickInstaller(fakeProbe), "pipx");
});

test("pickInstaller falls back to pip-user when only pip", () => {
  const fakeProbe = (cmd) => cmd === "pip";
  assert.equal(pickInstaller(fakeProbe), "pip-user");
});

test("pickInstaller returns null when nothing available", () => {
  const fakeProbe = () => false;
  assert.equal(pickInstaller(fakeProbe), null);
});

test("isPep668 detects externally-managed-environment", () => {
  const stderr = "error: externally-managed-environment\n× This environment is externally managed";
  assert.equal(isPep668(stderr), true);
});

test("isPep668 returns false on normal pip output", () => {
  assert.equal(isPep668("Successfully installed foo-1.0"), false);
});
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `cd npm && node --test test/detect.test.js`
Expected: FAIL with `Cannot find module '../lib/detect'`

- [ ] **Step 3: Implement `npm/lib/detect.js`**

```javascript
"use strict";

const { execSync } = require("node:child_process");


function parsePythonVersion(stdout) {
  const m = /Python\s+(\d+)\.(\d+)(?:\.(\d+))?/.exec(stdout || "");
  if (!m) return null;
  return {
    major: parseInt(m[1], 10),
    minor: parseInt(m[2], 10),
    patch: m[3] ? parseInt(m[3], 10) : 0,
  };
}


function probe(cmd) {
  // Returns true if `<cmd> --version` exits 0.
  try {
    execSync(`${cmd} --version`, { stdio: "ignore" });
    return true;
  } catch (_e) {
    return false;
  }
}


function pickInstaller(probeFn = probe) {
  if (probeFn("uv")) return "uv";
  if (probeFn("pipx")) return "pipx";
  if (probeFn("pip") || probeFn("pip3") || probeFn("python3 -m pip")) return "pip-user";
  return null;
}


function detectPython() {
  // Returns parsed version or null if Python 3.8+ not found.
  for (const cmd of ["python3", "python"]) {
    try {
      const out = execSync(`${cmd} --version`, { encoding: "utf8" });
      const v = parsePythonVersion(out);
      if (v && (v.major > 3 || (v.major === 3 && v.minor >= 8))) {
        return { version: v, command: cmd };
      }
    } catch (_e) {
      continue;
    }
  }
  return null;
}


function isPep668(stderr) {
  return /externally-managed-environment/i.test(stderr || "");
}


function pep668InstallHint() {
  // Platform-specific command to install pipx outside pip.
  if (process.platform === "darwin") return "brew install pipx";
  // Linux: try to detect Debian/Ubuntu vs Fedora/RHEL.
  try {
    const osr = require("node:fs").readFileSync("/etc/os-release", "utf8");
    if (/ID=(?:debian|ubuntu)/i.test(osr)) return "sudo apt install pipx";
    if (/ID=(?:fedora|rhel|centos)/i.test(osr)) return "sudo dnf install pipx";
  } catch (_e) { /* ignore */ }
  return "install pipx via your system package manager";
}


module.exports = { parsePythonVersion, probe, pickInstaller, detectPython, isPep668, pep668InstallHint };
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `cd npm && node --test test/detect.test.js`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add npm/lib/detect.js npm/test/detect.test.js
git commit -m "feat(npm): detect.js — Python version + installer cascade + PEP 668"
```

### Task 7: `lib/bootstrap.js` — install/upgrade with idempotency (TDD)

**Files:**
- Create: `npm/lib/bootstrap.js`
- Create: `npm/test/bootstrap.test.js`

- [ ] **Step 1: Write failing tests**

```javascript
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { buildInstallCmd, buildUpgradeCmd, isAlreadyInstalled } = require("../lib/bootstrap");

const SPEC = "mnemo>=0.12,<0.13";

test("buildInstallCmd uses uv tool install for uv", () => {
  assert.equal(buildInstallCmd("uv", SPEC), `uv tool install '${SPEC}'`);
});

test("buildInstallCmd uses pipx install for pipx", () => {
  assert.equal(buildInstallCmd("pipx", SPEC), `pipx install '${SPEC}'`);
});

test("buildInstallCmd uses pip --user for pip-user", () => {
  assert.equal(buildInstallCmd("pip-user", SPEC), `python3 -m pip install --user '${SPEC}'`);
});

test("buildUpgradeCmd uses pipx upgrade", () => {
  assert.equal(buildUpgradeCmd("pipx"), "pipx upgrade mnemo");
});

test("buildUpgradeCmd uses uv tool upgrade", () => {
  assert.equal(buildUpgradeCmd("uv"), "uv tool upgrade mnemo");
});

test("buildUpgradeCmd uses pip install --user --upgrade for pip-user", () => {
  assert.equal(buildUpgradeCmd("pip-user"), "python3 -m pip install --user --upgrade mnemo");
});

test("isAlreadyInstalled returns true when probe finds mnemo on path", () => {
  const probeFn = (cmd) => cmd === "mnemo --version";
  assert.equal(isAlreadyInstalled(probeFn), true);
});

test("isAlreadyInstalled returns false when probe misses", () => {
  const probeFn = () => false;
  assert.equal(isAlreadyInstalled(probeFn), false);
});
```

- [ ] **Step 2: Run tests, verify fail**

Run: `cd npm && node --test test/bootstrap.test.js`
Expected: FAIL with `Cannot find module '../lib/bootstrap'`

- [ ] **Step 3: Implement `npm/lib/bootstrap.js`**

```javascript
"use strict";

const { execSync, spawnSync } = require("node:child_process");
const { probe } = require("./detect");


const PIN_SPEC = "mnemo>=0.12,<0.13";


function buildInstallCmd(installer, spec = PIN_SPEC) {
  switch (installer) {
    case "uv":       return `uv tool install '${spec}'`;
    case "pipx":     return `pipx install '${spec}'`;
    case "pip-user": return `python3 -m pip install --user '${spec}'`;
    default: throw new Error(`unknown installer: ${installer}`);
  }
}


function buildUpgradeCmd(installer) {
  switch (installer) {
    case "uv":       return "uv tool upgrade mnemo";
    case "pipx":     return "pipx upgrade mnemo";
    case "pip-user": return "python3 -m pip install --user --upgrade mnemo";
    default: throw new Error(`unknown installer: ${installer}`);
  }
}


function isAlreadyInstalled(probeFn = probe) {
  return probeFn("mnemo --version");
}


function runShell(cmd, { quiet = false } = {}) {
  // Streams stdout/stderr inherited unless quiet. Returns exit code.
  const result = spawnSync("sh", ["-c", cmd], { stdio: quiet ? "ignore" : "inherit" });
  return result.status === null ? 1 : result.status;
}


function verifyOnPath() {
  // True if `mnemo --version` runs successfully.
  try {
    execSync("mnemo --version", { stdio: "ignore" });
    return true;
  } catch (_e) {
    return false;
  }
}


function pathFixHint(installer) {
  if (installer === "pipx") return "Run `pipx ensurepath` and reopen your shell.";
  if (installer === "uv")   return "Run `uv tool update-shell` and reopen your shell.";
  if (installer === "pip-user") {
    if (process.platform === "win32") return "Add %APPDATA%\\Python\\Scripts to PATH.";
    return "Add ~/.local/bin to PATH (e.g. via your shell profile).";
  }
  return "Re-open your shell to refresh PATH.";
}


module.exports = {
  PIN_SPEC,
  buildInstallCmd,
  buildUpgradeCmd,
  isAlreadyInstalled,
  runShell,
  verifyOnPath,
  pathFixHint,
};
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd npm && node --test test/bootstrap.test.js`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add npm/lib/bootstrap.js npm/test/bootstrap.test.js
git commit -m "feat(npm): bootstrap.js — install/upgrade builders + PATH verification"
```

### Task 8: `lib/prompt.js` — scope prompt (TDD)

**Files:**
- Create: `npm/lib/prompt.js`
- Create: `npm/test/prompt.test.js`

- [ ] **Step 1: Write failing tests**

```javascript
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const { Readable, Writable } = require("node:stream");

const { promptScope } = require("../lib/prompt");

function fakeStreams(input) {
  const stdin = Readable.from([input]);
  const chunks = [];
  const stdout = new Writable({
    write(chunk, _enc, cb) { chunks.push(chunk.toString()); cb(); },
  });
  return { stdin, stdout, chunks };
}

test("promptScope defaults to global on empty input", async () => {
  const { stdin, stdout } = fakeStreams("\n");
  const choice = await promptScope({ stdin, stdout });
  assert.equal(choice, "global");
});

test("promptScope returns project on '2'", async () => {
  const { stdin, stdout } = fakeStreams("2\n");
  const choice = await promptScope({ stdin, stdout });
  assert.equal(choice, "project");
});

test("promptScope returns global on '1'", async () => {
  const { stdin, stdout } = fakeStreams("1\n");
  const choice = await promptScope({ stdin, stdout });
  assert.equal(choice, "global");
});
```

- [ ] **Step 2: Run tests, verify fail**

Run: `cd npm && node --test test/prompt.test.js`
Expected: FAIL with `Cannot find module '../lib/prompt'`

- [ ] **Step 3: Implement `npm/lib/prompt.js`**

```javascript
"use strict";

const readline = require("node:readline");


function promptScope({ stdin = process.stdin, stdout = process.stdout } = {}) {
  const rl = readline.createInterface({ input: stdin, output: stdout, terminal: false });
  const question = [
    "",
    "Where should mnemo install hooks?",
    "  [1] Global  — every Claude Code session (recommended)",
    "  [2] Project — only in this directory",
    "choice [1]: ",
  ].join("\n");
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      const trimmed = (answer || "").trim();
      if (trimmed === "2") return resolve("project");
      return resolve("global");
    });
  });
}


module.exports = { promptScope };
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd npm && node --test test/prompt.test.js`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add npm/lib/prompt.js npm/test/prompt.test.js
git commit -m "feat(npm): prompt.js — readline scope picker, defaults to global"
```

### Task 9: `lib/runMnemo.js` — invoke `mnemo init` / `mnemo uninstall`

**Files:**
- Create: `npm/lib/runMnemo.js`

- [ ] **Step 1: Implement (no separate test — exercised by integration via runShell)**

```javascript
"use strict";

const { spawnSync } = require("node:child_process");


function buildInitArgs({ scope, vaultRoot, quiet, yes = true }) {
  const args = ["init"];
  if (scope === "project") args.push("--project");
  if (yes) args.push("--yes");
  if (quiet) args.push("--quiet");
  if (vaultRoot) { args.push("--vault-root", vaultRoot); }
  return args;
}


function buildUninstallArgs({ scope, quiet, yes = true }) {
  const args = ["uninstall"];
  if (scope === "project") args.push("--project");
  if (yes) args.push("--yes");
  if (quiet) args.push("--quiet");
  return args;
}


function runMnemo(args, { quiet = false } = {}) {
  const result = spawnSync("mnemo", args, { stdio: quiet ? "ignore" : "inherit" });
  return result.status === null ? 1 : result.status;
}


module.exports = { buildInitArgs, buildUninstallArgs, runMnemo };
```

- [ ] **Step 2: Commit**

```bash
git add npm/lib/runMnemo.js
git commit -m "feat(npm): runMnemo.js — argv builders + mnemo CLI invocation"
```

### Task 10: `lib/runInstall.js` — wire the install flow

**Files:**
- Create: `npm/lib/runInstall.js`
- Create: `npm/lib/messages.js`

- [ ] **Step 1: Create `npm/lib/messages.js`**

```javascript
"use strict";

// ANSI escapes (raw, no chalk).
const C = {
  green:  "\x1b[32m",
  yellow: "\x1b[33m",
  red:    "\x1b[31m",
  dim:    "\x1b[2m",
  reset:  "\x1b[0m",
};

function ok(msg)    { process.stdout.write(`${C.green}✓${C.reset} ${msg}\n`); }
function warn(msg)  { process.stdout.write(`${C.yellow}⚠${C.reset} ${msg}\n`); }
function err(msg)   { process.stderr.write(`${C.red}✗${C.reset} ${msg}\n`); }
function info(msg)  { process.stdout.write(`${C.dim}↻${C.reset} ${msg}\n`); }
function plain(msg) { process.stdout.write(`${msg}\n`); }

module.exports = { ok, warn, err, info, plain };
```

- [ ] **Step 2: Create `npm/lib/runInstall.js`**

```javascript
"use strict";

const { detectPython, pickInstaller, pep668InstallHint } = require("./detect");
const {
  PIN_SPEC,
  buildInstallCmd,
  buildUpgradeCmd,
  isAlreadyInstalled,
  runShell,
  verifyOnPath,
  pathFixHint,
} = require("./bootstrap");
const { promptScope } = require("./prompt");
const { buildInitArgs, runMnemo } = require("./runMnemo");
const m = require("./messages");


function parseFlags(argv) {
  const flags = { scope: null, vaultRoot: null, upgrade: false, yes: false, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--global")              flags.scope = "global";
    else if (a === "--project" || a === "--local") flags.scope = "project";
    else if (a === "--upgrade")        flags.upgrade = true;
    else if (a === "--yes" || a === "-y") flags.yes = true;
    else if (a === "--quiet")          flags.quiet = true;
    else if (a === "--vault-root")     { flags.vaultRoot = argv[++i]; }
  }
  return flags;
}


async function runInstall(argv) {
  const flags = parseFlags(argv);

  // 1. Python detect
  const py = detectPython();
  if (!py) {
    m.err("Python 3.8+ not found.");
    m.plain("  → Install Python 3.8 or newer (https://www.python.org/downloads/) and retry.");
    return 1;
  }
  if (!flags.quiet) m.ok(`Python ${py.version.major}.${py.version.minor} detected`);

  // 2. Installer detect
  const installer = pickInstaller();
  if (!installer) {
    m.err("No Python installer (uv, pipx, or pip) found on PATH.");
    m.plain(`  → ${pep668InstallHint()}`);
    return 1;
  }
  if (!flags.quiet) m.ok(`installer: ${installer}`);

  // 3. Install / upgrade / skip
  const installed = isAlreadyInstalled();
  if (installed && !flags.upgrade) {
    if (!flags.quiet) m.ok("mnemo already installed. Skipping installer step. (use --upgrade to force)");
  } else {
    const cmd = installed ? buildUpgradeCmd(installer) : buildInstallCmd(installer, PIN_SPEC);
    if (!flags.quiet) m.info(`Running: ${cmd}`);
    const status = runShell(cmd, { quiet: flags.quiet });
    if (status !== 0) {
      m.err(`Installer command failed (exit ${status}).`);
      return status;
    }
    if (!verifyOnPath()) {
      m.err("`mnemo --version` not reachable on PATH after install.");
      m.plain(`  → ${pathFixHint(installer)}`);
      return 2;
    }
    if (!flags.quiet) m.ok("mnemo on PATH");
  }

  // 4. Resolve scope
  let scope = flags.scope;
  if (!scope) {
    if (flags.yes) scope = "global";
    else scope = await promptScope();
  }

  // 5. Run mnemo init
  const args = buildInitArgs({ scope, vaultRoot: flags.vaultRoot, quiet: flags.quiet, yes: true });
  const status = runMnemo(args, { quiet: flags.quiet });
  if (status !== 0) return status;

  // 6. Final message
  if (!flags.quiet) {
    if (scope === "project") {
      m.plain(`\nDone. Launch \`claude\` in ${process.cwd()} to activate the local hooks.`);
    } else {
      m.plain("\nDone. Open Claude Code anywhere; mnemo is active.");
    }
  }
  return 0;
}


module.exports = { runInstall, parseFlags };
```

- [ ] **Step 3: Add unit test for `parseFlags`**

Append to `npm/test/bootstrap.test.js` (or create `npm/test/runInstall.test.js`):

```javascript
const { parseFlags } = require("../lib/runInstall");

test("parseFlags reads --project, --vault-root, --upgrade, --yes", () => {
  const f = parseFlags(["--project", "--vault-root", "/tmp/v", "--upgrade", "--yes"]);
  assert.equal(f.scope, "project");
  assert.equal(f.vaultRoot, "/tmp/v");
  assert.equal(f.upgrade, true);
  assert.equal(f.yes, true);
});

test("parseFlags accepts --local as alias for --project", () => {
  const f = parseFlags(["--local"]);
  assert.equal(f.scope, "project");
});
```

Place in `npm/test/runInstall.test.js`:

```javascript
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parseFlags } = require("../lib/runInstall");

test("parseFlags reads --project, --vault-root, --upgrade, --yes", () => {
  const f = parseFlags(["--project", "--vault-root", "/tmp/v", "--upgrade", "--yes"]);
  assert.equal(f.scope, "project");
  assert.equal(f.vaultRoot, "/tmp/v");
  assert.equal(f.upgrade, true);
  assert.equal(f.yes, true);
});

test("parseFlags accepts --local as alias for --project", () => {
  const f = parseFlags(["--local"]);
  assert.equal(f.scope, "project");
});
```

- [ ] **Step 4: Run all npm tests**

Run: `cd npm && npm test`
Expected: PASS — 8 detect + 8 bootstrap + 3 prompt + 2 runInstall = 21 tests

- [ ] **Step 5: Commit**

```bash
git add npm/lib/runInstall.js npm/lib/messages.js npm/test/runInstall.test.js
git commit -m "feat(npm): runInstall — wires detect/bootstrap/prompt/runMnemo into one flow"
```

### Task 11: `lib/runUninstall.js` — uninstall flow with `--scope`

**Files:**
- Create: `npm/lib/runUninstall.js`

- [ ] **Step 1: Write tests**

Create `npm/test/runUninstall.test.js`:

```javascript
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");

const { parseUninstallFlags, resolveUninstallScope } = require("../lib/runUninstall");

test("parseUninstallFlags reads --scope global", () => {
  const f = parseUninstallFlags(["--scope", "global", "--yes"]);
  assert.equal(f.scope, "global");
  assert.equal(f.yes, true);
});

test("parseUninstallFlags reads --scope both", () => {
  const f = parseUninstallFlags(["--scope", "both"]);
  assert.equal(f.scope, "both");
});

test("resolveUninstallScope auto-picks project when only project present", () => {
  const r = resolveUninstallScope(null, { project: true, global: false }, false);
  assert.equal(r, "project");
});

test("resolveUninstallScope auto-picks global when only global present", () => {
  const r = resolveUninstallScope(null, { project: false, global: true }, false);
  assert.equal(r, "global");
});

test("resolveUninstallScope errors on both-present + --yes + no flag", () => {
  assert.throws(() => resolveUninstallScope(null, { project: true, global: true }, true));
});

test("resolveUninstallScope respects explicit flag over detection", () => {
  const r = resolveUninstallScope("global", { project: true, global: true }, true);
  assert.equal(r, "global");
});
```

- [ ] **Step 2: Run, verify fail**

Run: `cd npm && node --test test/runUninstall.test.js`
Expected: FAIL with `Cannot find module '../lib/runUninstall'`

- [ ] **Step 3: Implement `npm/lib/runUninstall.js`**

```javascript
"use strict";

const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const { pickInstaller } = require("./detect");
const { runShell } = require("./bootstrap");
const { buildUninstallArgs, runMnemo } = require("./runMnemo");
const { promptScope } = require("./prompt");
const m = require("./messages");


function parseUninstallFlags(argv) {
  const f = { scope: null, yes: false, quiet: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--scope") f.scope = argv[++i];
    else if (a === "--yes" || a === "-y") f.yes = true;
    else if (a === "--quiet") f.quiet = true;
  }
  return f;
}


function _hasMnemoInSettings(settingsPath) {
  if (!fs.existsSync(settingsPath)) return false;
  try {
    const data = JSON.parse(fs.readFileSync(settingsPath, "utf8"));
    const hooks = (data && data.hooks) || {};
    for (const ev of Object.keys(hooks)) {
      const entries = hooks[ev] || [];
      for (const e of entries) {
        for (const h of (e.hooks || [])) {
          if (typeof h.command === "string" && h.command.includes("mnemo.hooks.")) return true;
        }
      }
    }
    return false;
  } catch (_e) {
    return false;
  }
}


function detectScopes() {
  return {
    project: _hasMnemoInSettings(path.join(process.cwd(), ".claude", "settings.json")),
    global:  _hasMnemoInSettings(path.join(os.homedir(), ".claude", "settings.json")),
  };
}


function resolveUninstallScope(flag, present, nonInteractive) {
  if (flag) {
    if (!["project", "global", "both"].includes(flag)) {
      throw new Error(`invalid --scope: ${flag}`);
    }
    return flag;
  }
  const both = present.project && present.global;
  if (both && nonInteractive) {
    throw new Error("both project and global installs detected; pass --scope explicitly");
  }
  if (present.project && !present.global) return "project";
  if (present.global && !present.project) return "global";
  return null; // ambiguous → caller prompts
}


async function _promptUninstallScope() {
  // Reuse promptScope's interface but with a third option ("both").
  const readline = require("node:readline");
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  const question = [
    "",
    "Both project and global mnemo installs detected.",
    "  [1] project (this directory)",
    "  [2] global",
    "  [3] both",
    "choice [1]: ",
  ].join("\n");
  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      const t = (answer || "").trim();
      if (t === "2") return resolve("global");
      if (t === "3") return resolve("both");
      return resolve("project");
    });
  });
}


async function runUninstall(argv) {
  const flags = parseUninstallFlags(argv);
  const present = detectScopes();
  if (!present.project && !present.global) {
    m.warn("No mnemo install detected (no hooks in project or global settings).");
    return 0;
  }
  let scope;
  try {
    scope = resolveUninstallScope(flags.scope, present, flags.yes) || await _promptUninstallScope();
  } catch (e) {
    m.err(e.message);
    return 2;
  }

  const scopes = scope === "both" ? ["project", "global"] : [scope];
  for (const s of scopes) {
    const status = runMnemo(buildUninstallArgs({ scope: s, quiet: flags.quiet, yes: true }), { quiet: flags.quiet });
    if (status !== 0) return status;
  }

  // Remove the Python package
  const installer = pickInstaller();
  if (installer) {
    const cmd = installer === "uv" ? "uv tool uninstall mnemo"
              : installer === "pipx" ? "pipx uninstall mnemo"
              : "python3 -m pip uninstall -y --user mnemo";
    if (!flags.quiet) m.info(`Running: ${cmd}`);
    runShell(cmd, { quiet: flags.quiet }); // best-effort; ignore exit
  }
  if (!flags.quiet) m.plain("\nDone. Vault preserved.");
  return 0;
}


module.exports = { runUninstall, parseUninstallFlags, resolveUninstallScope, detectScopes };
```

- [ ] **Step 4: Run tests, verify pass**

Run: `cd npm && node --test`
Expected: PASS — 21 prior + 6 new = 27 tests

- [ ] **Step 5: Commit**

```bash
git add npm/lib/runUninstall.js npm/test/runUninstall.test.js
git commit -m "feat(npm): runUninstall — --scope handling, both-detect, package removal"
```

---

## Phase 3 — Release infra + docs

### Task 12: Add `publish-npm` job to `release.yml`

**Files:**
- Modify: `.github/workflows/release.yml`

- [ ] **Step 1: Append `publish-npm` job**

```yaml
  publish-npm:
    name: Publish to npm
    runs-on: ubuntu-latest
    needs: publish-pypi
    steps:
      - uses: actions/checkout@v4

      - name: Set up Node 20
        uses: actions/setup-node@v4
        with:
          node-version: "20"
          registry-url: "https://registry.npmjs.org"

      - name: Sync npm package version from pyproject.toml
        run: python3 tools/sync_npm_version.py

      - name: Install (no-op for zero-dep package, runs npm test)
        working-directory: npm
        run: npm test

      - name: Publish to npm
        working-directory: npm
        run: npm publish --access public
        env:
          NODE_AUTH_TOKEN: ${{ secrets.NPM_TOKEN }}
```

- [ ] **Step 2: Confirm `NPM_TOKEN` secret**

Manual: visit `https://github.com/xyrlan/mnemo/settings/secrets/actions` and ensure `NPM_TOKEN` exists. Generate at `https://www.npmjs.com/settings/<user>/tokens` if missing.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): add publish-npm job, gated on publish-pypi"
```

### Task 13: Rewrite `README.md` Install section

**Files:**
- Modify: `README.md` (Install section, currently lines 13-50)

- [ ] **Step 1: Replace Install block**

Replace the lines from `## Install` (line 13) **up to but not including** `### Installation scope: global vs project (v0.12+)` (currently at line 36 after PR #54). Keep the `### Installation scope` subsection and everything after it untouched. The replacement:

```markdown
## Install

One command:

```bash
npx mnemo install
```

That installs the Python package (via `uv`/`pipx`/`pip --user`, whichever is available), prompts you to choose **global** (every Claude Code session) or **project** (this directory only), wires the hooks + MCP server + slash commands, and you're done.

Non-interactive variants:

```bash
npx mnemo install --yes              # global, no prompts
npx mnemo install --project --yes    # project-scoped, no prompts
```

`npx mnemo uninstall [--scope global|project|both]` reverses everything; the vault is preserved.

**Prerequisites:** Python 3.8+ on your machine. Node is already there if you run `npx`. mnemo's npm wrapper is zero-dep and ~150 LOC — it's a thin bootstrap, not the runtime.

### Alternative install paths

If you'd rather skip npm entirely:

```bash
pipx install mnemo                                      # or: uv tool install mnemo
mnemo init                                              # global
mnemo init --project                                    # project-only
```

If you'd rather wire the slash commands via Claude Code's marketplace:

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
```

(The marketplace path requires `pipx install mnemo` first — `/plugin install` registers slash commands but does not install the Python runtime.)

```

(The `### Installation scope: global vs project (v0.12+)` subsection that follows is preserved verbatim — no changes.)

- [ ] **Step 2: Update Commands section to mention `npx mnemo install` / `uninstall`**

Locate the Commands code block (around line 440) and add at the top:

```
npx mnemo install           one-command setup (recommended)
npx mnemo uninstall         remove mnemo (vault preserved)
mnemo init                  first-run setup if installed via pip directly
mnemo init --project        scope the install to <cwd> only (v0.12+)
...
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs(readme): npx mnemo install becomes the recommended install path"
```

### Task 14: First v0.13.0 release

**Files:** none (tag + verify)

- [ ] **Step 1: Bump pyproject.toml to 0.13.0**

```toml
version = "0.13.0"
```

- [ ] **Step 2: Sync npm and plugin manifest versions**

```bash
python3 tools/sync_npm_version.py
python3 tools/sync_plugin_manifest.py
```

- [ ] **Step 3: Run full test suite**

```bash
PYTHONPATH=$(pwd)/src pytest -q
cd npm && npm test
```

Expected: Python 1115 passing; npm 27 passing.

- [ ] **Step 4: Commit version bumps**

```bash
git add pyproject.toml npm/package.json .claude-plugin/plugin.json
git commit -m "release: bump to 0.13.0 (npx mnemo install)"
```

- [ ] **Step 5: Tag and push**

```bash
git tag v0.13.0
git push origin master v0.13.0
```

- [ ] **Step 6: Verify both jobs ran**

Manual: watch `https://github.com/xyrlan/mnemo/actions` — `publish-pypi` and `publish-npm` should both succeed. Then:

```bash
# Fresh shell
npx mnemo@latest install --yes --quiet
mnemo --version
# Expected: 0.13.0
```

- [ ] **Step 7: Smoke test the project flow**

```bash
mkdir /tmp/proj-smoke && cd /tmp/proj-smoke
npx mnemo install --project --yes --quiet
test -f .claude/settings.json && test -f .mcp.json && test -d .mnemo && echo OK
```

Expected: `OK`.

- [ ] **Step 8: Final commit (if any cleanup)**

If smoke surfaced bugs, fix in a follow-up branch. Otherwise, this task is complete.

---

## Out of scope (deferred, per spec)

- Telemetry on install success/failure
- Auto-installing Python when absent
- `npx mnemo upgrade` standalone command
- Migration tooling for users on the old 3-step install path (re-running `npx mnemo install` upgrades them in place — idempotent)
