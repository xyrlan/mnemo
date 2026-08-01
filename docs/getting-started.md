# Getting started with mnemo

The deeper tour. For the pitch and the one-step install, read the
[README](../README.md) first — this page assumes you've seen it and want the
details.

## Install

### Option A — the plugin (recommended)

Inside Claude Code:

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
```

Nothing else is required: no terminal, no Python, no Node. mnemo ships as a
self-contained binary, and the plugin fetches the build for your platform on
first use into its own data directory, verifying it against a published
SHA-256 before installing it.

The download happens during your first `SessionStart` and takes a few seconds.
If it fails — offline, unsupported platform — mnemo stays quiet and does
nothing rather than interrupting the session; the next session retries.

### Option B — npm

Puts `mnemo` on your `$PATH` and wires everything into your own Claude Code
config rather than the plugin's.

```bash
npx @xyrlan/mnemo install              # prompts for global or project scope
npx @xyrlan/mnemo install --yes        # global, no prompts
npx @xyrlan/mnemo install --project --yes
npx @xyrlan/mnemo uninstall
```

It installs the Python package via whichever of `uv` / `pipx` / `pip --user`
you have. `uv` is preferred and needs nothing else — it brings its own Python.
The other two need Python 3.8+ already on `PATH`.

### Option C — pipx / uv directly

For dotfile-managed setups and CI:

```bash
pipx install mnemo-claude        # or: uv tool install mnemo-claude
mnemo init                       # global
mnemo init --project             # or: scoped to the current directory
```

`python -m mnemo` and the installed `mnemo` console script are equivalent.

### Migrating from B or C to the plugin

Installing the plugin on top of an existing `mnemo init` leaves **both** sets
of hooks live, so every session gets doubled capture, injection, and
enforcement. mnemo detects this and says so once at session start.

```
/mnemo:migrate
```

That strips the hooks the old install wrote, leaving a timestamped backup
beside each settings file. The plugin's own hooks are unaffected, and your
vault is never touched.

## What `mnemo init` actually does

Only relevant for options B and C — the plugin declares all of this itself.

It's idempotent; running it twice is safe. On first run it will:

1. Preflight: Python version, writable vault root, `~/.claude/` accessible.
2. Scaffold the vault tree at `~/mnemo/` (or `--vault-root <path>`).
3. Inject **four** hooks into `~/.claude/settings.json` (with a backup):
   `SessionStart`, `UserPromptSubmit`, `PreToolUse` (matching
   `Bash|Edit|Write|MultiEdit`), and `SessionEnd`.
4. Register the stdio MCP server in `~/.claude.json`.
5. Wire the additive status line composer, preserving any `statusLine` you
   already had.
6. Mirror existing Claude Code memories from `~/.claude/projects/*/memory/`
   into `bots/<repo>/memory/`.

In `--project` mode, everything lands under `<cwd>/.claude/`, `<cwd>/.mcp.json`,
and `<cwd>/.mnemo/` instead, and both are added to `.gitignore`.

Re-running `mnemo init` reconciles drift without clobbering your edits.

## Your first session

Just use Claude Code normally.

Your per-session trail lands at `~/mnemo/bots/<repo-name>/logs/YYYY-MM-DD.md`,
with `🟢` at session start and `🔴` at session end. Anything Claude saved to
its own memory directory is mirrored into `~/mnemo/bots/<repo-name>/memory/`.

Confirm it's live:

```
/mnemo:status
```

Under the plugin, hook health reads `Hooks (plugin): 4/4`. Under an npm or
pipx install it names the settings file instead, because that's where the
hooks live.

## The loop

Everything below is on by default. To disable a piece, set it to `false` in
`~/mnemo/mnemo.config.json` — see [configuration.md](configuration.md) for the
full key reference.

### Extraction

At every `SessionEnd`, the hook checks whether there are at least
`extraction.auto.minNewMemories` new files since the last run and whether
`minIntervalMinutes` have passed. If so it spawns a detached background
extraction and returns in under 100ms — your session exits normally while
extraction runs.

Extraction shells out to the `claude` CLI you already have. Each run typically
makes 3 calls (one per cluster type) and costs a few cents on API-key auth, or
$0 on a Claude subscription.

**Output splits by source count:**

- **Single-source** pages (one source file, no clustering judgment needed) go
  straight to `shared/<type>/<slug>.md`, tagged `auto-promoted`. mnemo treats
  these as its own and rewrites them when the source changes — as long as you
  haven't edited them.
- **Multi-source** clusters (cross-agent merges, where the model made an
  editorial call) land in `shared/_inbox/<type>/<slug>.md` tagged
  `needs-review`. Review before promoting.

**Your edits win.** If you edit an auto-promoted page and its source later
changes, the new output is written as `shared/_inbox/<type>/<slug>.proposed.md`
rather than overwriting your file.

### Briefings

At every `SessionEnd`, mnemo writes a per-session briefing into
`bots/<repo>/briefings/sessions/`. Briefings are the dense input that feeds the
next extraction — the difference between capturing ~1 file/day and capturing
every meaningful decision.

### Injection

At `SessionStart`, mnemo emits a compact topic list into Claude's
`additionalContext`, telling it to call the MCP tools when a task matches a
known topic. Topics are filtered to the current project by default.

The MCP tools — `list_rules_by_topic`, `read_mnemo_rule`, `get_mnemo_topics` —
are always available; this flag only controls whether Claude is *told about*
them at session start. All three default to `scope="project"`; pass
`scope="vault"` for cross-project lookups.

### Reflex

On every prompt, mnemo runs BM25F retrieval over its rule index and injects
the single most relevant rule inline — only when it clears a triple gate on
term overlap, relative score gap, and an absolute floor. Fail-open: any error
means the prompt passes through untouched.

### Enforcement and enrichment

At `PreToolUse`, a `Bash` command matching a rule you marked as a guardrail is
blocked outright. An `Edit`/`Write` whose path matches a rule's `activates_on`
gets that rule's body surfaced as context.

## Observing and debugging

```bash
mnemo status    # vault state, hook health, last auto-run, currently-running state
mnemo doctor    # full diagnostic: statusLine drift, stale locks, recent failures
mnemo extract   # manual extraction (also rebuilds the HOME dashboard)
mnemo fix       # reset the extraction circuit breaker after repeated failures
```

Under the plugin, use the `/mnemo:` forms of the first four.

Detailed errors land in `~/mnemo/.errors.log` under `where=extract.bg.*`. If
`mnemo doctor` warns about `statusLine` drift, you hand-edited
`~/.claude/settings.json` after `mnemo init` — re-run it to reconcile.

Manual extraction flags:

```bash
mnemo extract --dry-run   # show what would run without calling the LLM
mnemo extract --force     # reprocess entries previously dismissed or promoted
```

## Uninstalling

Plugin: `/plugin uninstall mnemo`.

npm: `npx @xyrlan/mnemo uninstall` (also removes the Python package).

pipx/uv: `mnemo uninstall`, then `pipx uninstall mnemo-claude`.

All of them remove hooks, the MCP registration, and the status line composer.
**Your vault is never deleted** — `rm -rf ~/mnemo` is a separate, conscious
step.
