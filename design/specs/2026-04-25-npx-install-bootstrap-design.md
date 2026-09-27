# `npx mnemo install` — single-command install bootstrap

## Context

Today mnemo's install path has three paradigms with implicit ordering and silent failure modes:

```
pip install git+https://github.com/xyrlan/mnemo.git    # shell, prerequisite
/plugin marketplace add xyrlan/mnemo                    # inside Claude Code
/plugin install mnemo@mnemo-marketplace                 # inside Claude Code
```

Skip the first step (the most common mistake) and the slash commands appear to install successfully but `/mnemo init` errors with `command not found`. The README polish landed in PR #56 mitigates this for readers, but the underlying UX is still bad: three different toolchains, four lines, no atomicity.

This design replaces the install flow with **a single command**: `npx mnemo install`. A thin npm wrapper (~150 LOC Node) detects Python, installs mnemo via the best available Python installer, prompts the user for install scope, and runs `mnemo init` with the right flags. Slash commands register themselves during `mnemo init`, dispensing with the `/plugin` dance.

The plugin manifest at `.claude-plugin/plugin.json` keeps working as an alternative entry point for users who already use the Claude Code marketplace.

## Decisions registered

| Decision | Choice | Rationale |
|---|---|---|
| Wrapper scope | install + uninstall only | Day-to-day commands (`status`, `doctor`, `extract`) keep using the Python CLI; npx-as-proxy adds 200-400ms latency per call and breaks hook-fired commands like statusline-compose. |
| Slash command registration | npm wrapper writes `~/.claude/settings.json` direct (via `mnemo init`) | Eliminates the `/plugin marketplace` dance. Plugin manifest stays as compat. |
| Default install scope | global | Matches existing default. Project install via `--project`. |
| Interactive scope choice | prompt unless `--global` / `--project` / `--yes` | One-line install must still let users opt into project scope without learning a flag. |
| Python bootstrap cascade | `uv` → `pipx` → `pip --user` (first available wins) | Best-in-class isolation when `uv` exists; pipx as the broad standard; pip --user as last resort for stripped-down environments. PEP 668 (Debian/Ubuntu) — fail with platform-specific hint pointing at the package-manager-installed pipx (`apt install python3-pipx` / `brew install pipx`), never `--break-system-packages`. |
| npm package version sync | minor-pinned: npm `0.X.*` installs `mnemo>=0.X,<0.X+1` | Wrapper bug fixes ship without coordinating PyPI; mnemo minor bumps require a paired npm release. |
| npm package name | `mnemo` if free, else `@xyrlan/mnemo` | Verified at implementation time via `npm view mnemo`. |
| PyPI publish dependency | **Hard prerequisite** of this work | Bootstrap calls `pipx install mnemo` — only works if mnemo is on PyPI. v1 does not bootstrap from `git+https://...`. |
| Re-run idempotency | Detect-and-skip default; `--upgrade` to force | `pipx install mnemo` errors if already present. Default behavior detects via `pipx list` (or installer-equivalent), skips Python install, runs only `mnemo init`. `--upgrade` forces `pipx upgrade` / `uv tool upgrade` / `pip install -U`. |
| Uninstall scope when both present | `--scope global\|project\|both` (mirrors install); auto-detect when unambiguous | Cwd-only auto-detect can leave global hooks active and silently surprise the user. Explicit flag covers full cleanup; ambiguous case (both scopes present, no flag) prompts with `[1] project [2] global [3] both`, default `1` (project — matches the cwd context). |

## Pre-implementation tasks

These must complete **before** any npm-wrapper code is written. They unblock the bootstrap path:

1. **Bump `pyproject.toml` version** to `0.12.0` (master shipped v0.12 work via PRs #54-56 without bumping; current value is `0.11.0`).
2. **Cut `v0.12.0` git tag** + GitHub release.
3. **First PyPI publish** of `mnemo==0.12.0`. This is net-new infrastructure (no `release.yml` exists today, only `ci.yml`). Requires:
   - `PYPI_API_TOKEN` repo secret
   - New workflow `.github/workflows/release.yml` with a `publish-pypi` job triggered by `v*` tags (build wheel + sdist via `python -m build`, upload via `pypa/gh-action-pypi-publish@release/v1`)
4. **Verify `pipx install mnemo` works end-to-end** in a clean Linux VM. This is the contract the npm wrapper depends on.
5. **Bump `.claude-plugin/plugin.json` version** from `0.6.0` to `0.12.0` (long-standing drift; spec uses it as source of truth in step 4).

Only after step 4 confirms green does the npm wrapper implementation begin.

## User experience

### Install

```
$ npx mnemo install
✓ Python 3.11 detected
✓ pipx detected
↻ Installing mnemo via pipx…
✓ mnemo 0.12.0 installed (PATH: /home/user/.local/bin/mnemo)

# (re-run on an existing install — idempotent)
$ npx mnemo install
✓ mnemo already installed (0.12.0). Skipping pipx step. (use --upgrade to force)

Where should mnemo install hooks?
  [1] Global  — every Claude Code session (recommended)
  [2] Project — only in this directory
choice [1]: 

✓ Hooks + MCP + statusLine installed in ~/.claude/
✓ Slash commands registered: /mnemo init, /mnemo status, /mnemo doctor, …
  (if the registration channel falls back, this line is replaced by:
   "ℹ Run `/plugin marketplace add xyrlan/mnemo && /plugin install mnemo` for slash commands")

Done. Open Claude Code anywhere; mnemo is active.
```

Project flow ends with `Launch claude in <cwd> to activate the local hooks.`

### Uninstall

```
$ npx mnemo uninstall
✓ Removing mnemo hooks (global) from ~/.claude/settings.json
✓ Uninstalling mnemo via pipx
Vault preserved at ~/mnemo. Delete manually if you want it gone.
```

`npx mnemo uninstall` auto-detects scope from cwd: if `<cwd>/.claude/settings.json` has mnemo hooks, runs `mnemo uninstall --project`; else global.

## Architecture

### npm package layout (`npm/` at repo root)

```
npm/
├── package.json          name, version, bin, engines:{node:>=18}, license, repository
├── README.md             one-pager, links to main README
├── bin/
│   └── mnemo.js          #!/usr/bin/env node — argv parse + dispatch
├── lib/
│   ├── detect.js         Python version detection, installer cascade, PEP 668 detection
│   ├── bootstrap.js      uv|pipx|pip install + idempotency check + verify-on-PATH
│   ├── prompt.js         readline-based scope prompt (no `inquirer`)
│   ├── runMnemo.js       spawn `mnemo init` / `mnemo uninstall` with flags
│   └── messages.js       ANSI raw escapes (no `chalk`)
└── test/
    ├── detect.test.js    mocked execSync per platform
    ├── bootstrap.test.js cascade order, fallback path, idempotency-skip
    └── prompt.test.js    interactive defaults
```

Zero runtime deps. `node:test` for unit tests (requires Node ≥18, pinned via `engines.node`). Windows compatibility: npm auto-generates a `.cmd` shim from the `bin` field — no extra wrapper code needed, but verify in smoke test.

### Install flow (`npx mnemo install`)

1. **Parse flags:** `--global` / `--project` (mutex) · `--yes` / `-y` (default scope = global, no prompts) · `--vault-root <path>` (forwarded to `mnemo init`) · `--upgrade` (force `pipx upgrade` / `uv tool upgrade` even when installed) · `--quiet`.
2. **Detect Python 3.8+:** `python3 --version` → parse. <3.8 or absent → fail with platform-specific install hint, exit 1.
3. **Detect installer (cascade):** `uv --version` → `pipx --version` → `python3 -m pip --version`. First success wins. No pip → fail with `apt install python3-pip` / `brew install python` hint.
4. **Install or upgrade mnemo (idempotent):** First detect existing install (`pipx list | grep mnemo` or `uv tool list | grep mnemo` or `python3 -m pip show mnemo`).
   - **Already installed + no `--upgrade`:** print `✓ mnemo already installed (X.Y.Z). Skipping pipx step.` and skip to step 5.
   - **Already installed + `--upgrade`:** run installer-specific upgrade (`pipx upgrade mnemo` / `uv tool upgrade mnemo` / `pip install --user --upgrade mnemo`).
   - **Not installed:** run `uv tool install mnemo` / `pipx install mnemo` / `python3 -m pip install --user mnemo`.
   Verify `mnemo --version` reachable on PATH after install/upgrade; if not, print PATH-fix hint per platform (`pipx ensurepath`, etc.) and exit 2.
5. **Resolve scope:** `--global` or `--yes` (no scope flag) → global; `--project` → project; else readline prompt with default `[1]` (global).
6. **Run setup:** `mnemo init --yes` (global) or `mnemo init --project --yes` (project). Forward `--vault-root` if present. Exit code mirrors `mnemo init`.
7. **Final message:** scope-appropriate next-step instruction.

### Uninstall flow (`npx mnemo uninstall`)

Flags: `--scope global|project|both` (mirrors install) · `--yes` / `-y` · `--quiet`.

1. **Resolve scope:**
   - `--scope` flag set → use it.
   - No flag, only one scope present (project hooks in cwd OR global hooks in `~/.claude/`) → use that one.
   - No flag, **both** scopes present + interactive → prompt `[1] project [2] global [3] both` (default `1`).
   - No flag, both scopes present + `--yes` → fail with `error: both project and global installs detected; pass --scope explicitly` (exit 2). Auto-defaulting to one in non-interactive mode would silently leave the other behind.
2. **Run `mnemo uninstall --yes`** with the right flag(s):
   - `project` → `mnemo uninstall --project --yes`
   - `global` → `mnemo uninstall --yes`
   - `both` → both calls in sequence (project first; vault path of each preserved).
3. **Detect installer** (uv/pipx/pip in same cascade as install).
4. **Remove the Python package:** `uv tool uninstall mnemo` / `pipx uninstall mnemo` / `python3 -m pip uninstall --user mnemo`. Vault paths printed (one per scope removed).

### Python-side changes (`src/mnemo/install/settings.py`)

- New `inject_slash_commands(target_path: Path) -> None` + `uninject_slash_commands(target_path: Path) -> None`. Same lock + backup + idempotency primitives as `inject_hooks` / `inject_mcp_servers`. Marker: substring `"-m mnemo "` (with trailing space) in the registered command — present in every command we generate, absent from anything else likely (`MNEMO_TAG = "mnemo.hooks."` covers hooks; this is its slash-command sibling, kept distinct so the two strippers don't cross-fire).
- `SLASH_COMMANDS` constant: list of 9 commands (init, init-project, status, doctor, open, fix, uninstall, uninstall-project, help) — single source of truth.
- `cmd_init` calls `inj.inject_slash_commands(target_settings)` after `inj.inject_statusline(...)`. `cmd_uninstall` mirrors with `uninject_slash_commands`.
- `tools/sync_plugin_manifest.py` (new) regenerates `.claude-plugin/plugin.json` from `SLASH_COMMANDS` to keep marketplace path consistent. Pre-commit hook or manual run — not enforced in CI initially.

### Slash command registration (channel confirmed by 2026-04-25 spike)

**Channel: `~/.claude/commands/<name>.md`** — markdown files with YAML frontmatter, one per command, in the user's `commands` directory (or `<cwd>/.claude/commands/` for project scope). Confirmed by Claude Code docs (https://code.claude.com/docs/en/slash-commands): "A file at `.claude/commands/deploy.md` and a skill at `.claude/skills/deploy/SKILL.md` both create `/deploy` and work the same way. Your existing `.claude/commands/` files keep working."

Spike rejected the originally-preferred channel #1 (`~/.claude/settings.json > customCommands`): no such key exists in Claude Code. Adopted channel #2 with the actual documented format (`.md` not `.json`).

**File format:**

```markdown
---
description: first-run setup (global)
allowed-tools: Bash
disable-model-invocation: true
---

!`python3 -m mnemo init`
```

The body uses Claude Code's bash-injection syntax (`` !`<cmd>` ``) so typing `/init` runs `python3 -m mnemo init` in a subshell, captures output, and surfaces it to the user via Claude. `disable-model-invocation: true` keeps Claude from auto-loading these slash entries — they're explicit user actions only.

**`SLASH_COMMAND_TAG`:** files mnemo writes carry an HTML comment marker `<!-- mnemo:slash-command -->` near the top so `uninject_slash_commands` can identify and remove them without touching third-party command files. (Filename alone isn't a reliable tag — third parties may install commands with the same names.)

**`inject_slash_commands(commands_dir: Path)` signature:** takes the `commands/` directory (`~/.claude/commands` or `<cwd>/.claude/commands`), creates it if missing, writes one `.md` per `SLASH_COMMANDS` entry. Idempotent: existing mnemo-tagged files are overwritten; non-mnemo files are left alone.

**Fallback contract:** preserved. If during implementation the bash-injection format proves unreliable (e.g. if Claude Code rejects `disable-model-invocation` for `commands/*.md`), `mnemo init` falls back to printing the `/plugin marketplace` instruction. The npm install does not block on this — slash commands degrade to manual registration, hooks + MCP install regardless.

## Versioning + release

- npm package version stays at `0.X.*` while mnemo PyPI is at `0.X.*`. Wrapper minor bump only when `mnemo init` CLI signature changes.
- npm `package.json` pins peer Python version range: `mnemoPythonRange: ">=0.12,<0.13"` — embedded in `bootstrap.js` as the install spec (`mnemo>=0.12,<0.13`).
- **Release workflow is net-new.** No `release.yml` exists today (only `ci.yml`). This spec creates `.github/workflows/release.yml` with two parallel jobs triggered by `v*` tags:
  - `publish-pypi` — builds wheel + sdist, uploads via `pypa/gh-action-pypi-publish@release/v1`. Requires `PYPI_API_TOKEN` repo secret.
  - `publish-npm` — runs after `publish-pypi` succeeds (job-level `needs:`), uses `npm publish` with `--access public`. Requires `NPM_TOKEN` repo secret.
- Single source of truth for version: `pyproject.toml`. `npm/package.json` version + `bootstrap.js` install spec are regenerated from it by a release script (`tools/sync_npm_version.py`).
- The `tools/` directory is **net-new** — created by this spec to host release helpers. Convention: each script is a standalone Python file invoked by name from CI; no implicit `__init__.py`. Two scripts in this spec: `tools/sync_npm_version.py` (run by `publish-npm` job before `npm publish`) and `tools/sync_plugin_manifest.py` (run as a manual pre-release step or pre-commit hook — not enforced in CI initially).

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Slash command channel undocumented | Fallback to "/plugin install" manual instruction (above). Spec accepts this degradation. |
| `mnemo` not on PATH after pipx install | `bootstrap.js` validates via `mnemo --version` post-install; emits platform-specific PATH-fix hint and exits 2 if missing. |
| PEP 668 strict environments (Debian/Ubuntu newer) | Cascade tries uv/pipx first. `bootstrap.js` detects PEP 668 (parses `pip install --dry-run` for `externally-managed-environment`) and emits the **package-manager-specific pipx install command** for the platform (`apt install python3-pipx` on Debian/Ubuntu 23.04+, `brew install pipx` on macOS, `dnf install pipx` on Fedora). Never invokes `--break-system-packages`. |
| `mnemo` name taken on npm | Implementation checks `npm view mnemo` first; falls back to `@xyrlan/mnemo`. README updated accordingly. |
| Wrapper-PyPI version skew | Minor pinning: npm 0.X.* requires mnemo 0.X.* on PyPI. Major bump = paired release. |
| Install telemetry missing | Out of v1 scope. Add only if real users complain about silent failures. |
| Re-running `npx mnemo install` on existing install | Detect-and-skip default (see install flow step 4). `--upgrade` forces installer-specific upgrade. Idempotent without `--force` thrashing. |
| Coexistence after re-run with different scope | `mnemo init` (v0.12+) already detects global+project coexistence and warns. With `npx mnemo install --project --yes`, the warn is auto-confirmed and the install proceeds — documented as supported. User who wants single-scope re-runs `npx mnemo uninstall --scope <other>` first. |

## Testing

### npm package (`npm/test/`)

- **detect.test.js**: mocked `child_process.execSync` returning fake `python3 --version`, `uv --version`, `pipx --version` outputs. Asserts (a) cascade order respected, (b) version parsing rejects <3.8, (c) absent-Python failure path.
- **bootstrap.test.js**: mocked `execSync` for `pipx install mnemo`. Asserts (a) correct command per installer, (b) version pin respected (`mnemo>=0.12,<0.13`), (c) PATH-verify failure exit code = 2.
- **prompt.test.js**: mocked stdin/stdout streams. Asserts default = global on empty input, valid `2` chooses project, invalid input re-prompts.

### Python (`tests/integration/test_cli_init.py`)

- `test_init_registers_slash_commands` — settings.json after `mnemo init` contains all 9 commands under the slash-commands key (whichever channel ends up correct).
- `test_uninstall_strips_slash_commands` — after `mnemo uninstall`, slash commands are gone but other settings keys (hooks of other plugins, statusLine, etc.) preserved.

### Smoke (manual, release checklist)

- Fresh Ubuntu 22.04 VM, no Python install: `npx mnemo install` should fail with apt install hint.
- Fresh Ubuntu 22.04 VM, Python 3.11 + pipx: `npx mnemo install` succeeds end-to-end; `claude` opens with mnemo active.
- macOS Sonoma, brew-installed Python + uv: cascade picks uv; install completes.
- Re-running `npx mnemo install` is idempotent (no-op if already current; respects `--upgrade`).

## Out of scope (deferred)

- Telemetry on install success/failure
- Auto-installing Python when absent (multi-platform Python install is a separate problem)
- `npx mnemo upgrade` standalone command (re-run `install` covers it)
- Migration script for users who already installed via the old 3-step path (re-running `npx mnemo install` upgrades them in place; idempotent)

## Critical files

- `npm/package.json` (new)
- `npm/bin/mnemo.js` (new)
- `npm/lib/{detect,bootstrap,prompt,runMnemo,messages}.js` (new)
- `npm/test/{detect,bootstrap,prompt}.test.js` (new)
- `src/mnemo/install/settings.py` — `inject_slash_commands` + `SLASH_COMMANDS`
- `src/mnemo/cli/commands/init.py` — call `inject_slash_commands` after `inject_statusline`
- `src/mnemo/cli/commands/misc.py` (uninstall) — mirror with `uninject_slash_commands`
- `tools/sync_npm_version.py` (new) — regenerate `npm/package.json` version from `pyproject.toml`
- `tools/sync_plugin_manifest.py` (new) — regenerate `.claude-plugin/plugin.json` from `SLASH_COMMANDS`
- `tests/integration/test_cli_init.py` — 2 new test cases
- `.github/workflows/release.yml` — add `publish-npm` job
- `README.md` — Install section becomes a one-liner, old paths kept as "alternative"

## Verification (end-to-end)

1. **Smoke (Linux):** Fresh Docker `ubuntu:22.04` + Python 3.11 + pipx. `npx mnemo install --yes` exits 0; `mnemo --version` works; `claude` in any dir loads mnemo hooks; slash commands listed.
2. **Smoke (project):** Same env. `mkdir /tmp/p && cd /tmp/p && npx mnemo install --project --yes`. `<cwd>/.claude/settings.json` + `<cwd>/.mcp.json` + `<cwd>/.mnemo/` exist; `~/.claude/` untouched.
3. **Slash command registration:** After install, opening Claude Code shows `/mnemo init`, `/mnemo doctor`, etc. without running `/plugin install`.
4. **Uninstall round-trip:** `npx mnemo uninstall --yes` cleans hooks + MCP + slash commands + Python package; vault preserved.
5. **Cascade fallback:** Env without uv → installer reports "pipx detected"; without uv+pipx → installer reports "pip --user (PEP 668 may block)".
6. **Automated:** `pytest -q` (Python) — 1115 expected (1113 + 2 new). `npm test` (Node) — all unit tests green.
