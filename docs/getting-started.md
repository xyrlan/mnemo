# Getting started with mnemo

This is the deeper tour. For the 30-second pitch and the default install
flow, read the [README](../README.md) first — this page assumes you've
seen it and want the details.

## Install

### Option A — Claude Code plugin marketplace (recommended)

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
/mnemo init
```

### Option B — manual / dotfiles

For dotfile-managed setups or when you want `mnemo` on your `$PATH`
without the Claude Code plugin wrapper:

```bash
git clone https://github.com/xyrlan/mnemo ~/.mnemo-repo
cd ~/.mnemo-repo
pip install -e .
python -m mnemo init --yes --vault-root ~/mnemo
```

`python -m mnemo` and the installed `mnemo` console script are
equivalent. Both work without any Claude Code plugin being installed —
mnemo's hooks-and-MCP integration is wired by `mnemo init` directly into
`~/.claude/settings.json` and `~/.claude.json`, regardless of how you
installed the package itself.

### What `mnemo init` actually does

It's idempotent — running it twice is safe. On first run it will:

1. Preflight: Python version, writable vault root, `~/.claude/` accessible.
2. Scaffold the vault tree at `~/mnemo/` (or `--vault-root <path>`).
3. Inject `SessionStart` and `SessionEnd` hooks into `~/.claude/settings.json` (with backup).
4. Register the stdio MCP server (`mnemo-mcp`) in `~/.claude.json`.
5. Wire the additive status line composer, preserving any existing `statusLine` you already had.
6. Mirror existing Claude Code memories from `~/.claude/projects/*/memory/` into `bots/<repo>/memory/`.

Re-running `mnemo init` reconciles any drift from those steps without
clobbering your edits.

## First real session

After `mnemo init`, just use Claude Code like you normally would. You'll
see the heartbeat in your status line:

```
mnemo mcp · 9 topics · 7↓ today
```

Open the vault whenever you want to browse what's accumulated:

```
/mnemo open
```

Your per-session trail lives at
`~/mnemo/bots/<repo-name>/logs/YYYY-MM-DD.md`, with `🟢` markers at
session start and `🔴` at session end. Anything Claude saved to its own
memory directory during the session is mirrored into
`~/mnemo/bots/<repo-name>/memory/` automatically.

## Turning on the loop (opt-in flags)

The three features below ship **disabled by default** — v0.5 ships dark,
you dogfood, then you flip. Edit `~/mnemo/mnemo.config.json`:

```json
{
  "extraction": {
    "auto": {
      "enabled": true,
      "minNewMemories": 1,
      "minIntervalMinutes": 60
    }
  },
  "briefings": { "enabled": true },
  "injection": { "enabled": true }
}
```

### `extraction.auto.enabled`

At every `SessionEnd`, the hook checks: are there at least
`minNewMemories` new files in `bots/*/memory/` since the last run, and
have at least `minIntervalMinutes` passed? If yes, it spawns a detached
`mnemo extract --background` subprocess and returns in under 100ms. Your
Claude Code session exits normally while extraction runs asynchronously.

**Output split by source count:**

- **Single-source** pages (one source file, no clustering judgment
  needed) write directly to `shared/<type>/<slug>.md` with
  `tags: [auto-promoted]` and a `last_sync` frontmatter key. mnemo treats
  these as its own territory and rewrites them when the source changes —
  as long as you haven't edited them.
- **Multi-source** clusters (cross-agent merges, where the LLM made an
  editorial decision) land in `shared/_inbox/<type>/<slug>.md` with
  `tags: [needs-review]`. Review before promoting.

**Your edits are protected by content-addressing.** If you edit an
auto-promoted page and the source later changes, the new LLM output is
written as `shared/_inbox/<type>/<slug>.proposed.md` instead of
overwriting your canonical file.

### `briefings.enabled`

At every `SessionEnd`, generate a per-session briefing into
`bots/<repo>/briefings/sessions/`. Briefings are the dense input that
feeds the next extraction run — the difference between mnemo capturing
~1 file/day and capturing every meaningful decision.

### `injection.enabled`

At every `SessionStart`, emit the ~120-token topic list into Claude's
`additionalContext`, telling it to call the MCP tools
(`list_rules_by_topic`, `read_mnemo_rule`, `get_mnemo_topics`) before
writing code when the task matches a known topic. Topics are filtered
to the current project by default.

The MCP tools themselves are always available after `mnemo init` — this
flag only controls whether Claude is *told about* them at session start.
All three tools default to `scope="project"` (rules owned by the current
repo only); pass `scope="vault"` for cross-project lookups.

## Observing and debugging

```bash
mnemo status    # vault state, hook health, last auto-run summary, currently-running state
mnemo doctor    # full diagnostic: statusLine drift, stale locks, recent background failures, legacy dirs
mnemo extract   # manual extraction (also rebuilds the HOME dashboard)
mnemo fix       # reset the extraction circuit breaker after repeated failures
```

Detailed errors land in `~/mnemo/.errors.log` under `where=extract.bg.*`
for background failures. If `mnemo doctor` warns about `statusLine`
drift, it means you hand-edited `~/.claude/settings.json` after
`mnemo init` — re-run `mnemo init` to reconcile.

### Manual extraction flags

```bash
mnemo extract --dry-run   # show what would run without calling the LLM
mnemo extract --force     # reprocess entries previously dismissed or promoted
```

Each run typically makes 3 LLM calls (one per cluster type) and costs a
few cents on API-key auth or $0 on a Claude subscription. The command
prints a token-count summary on completion.

## Uninstalling

```
/mnemo uninstall
```

Removes hooks, the MCP server registration, and the status line
composer. Your vault is **never** deleted — if you really want it gone,
`rm -rf ~/mnemo` is a separate, conscious step.
