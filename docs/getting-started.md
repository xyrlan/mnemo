# Getting started with mnemo

## Install (Claude Code plugin)

```
/plugin install mnemo@claude-plugins-official
/mnemo init
```

`/mnemo init` will:
1. Run preflight checks (Python version, writable vault, settings.json access)
2. Ask where to put your vault (default: `~/mnemo`)
3. Ask permission to modify `~/.claude/settings.json` (with backup)
4. Scaffold the vault directory tree
5. Mirror existing Claude Code memories

## Install (manual / dotfiles)

```bash
git clone https://github.com/xyrlan/mnemo ~/.mnemo-repo
cd ~/.mnemo-repo
pip install -e .
python -m mnemo init --yes --vault-root ~/Documents/brain
```

## Daily flow

Just use Claude Code. Open your vault in Obsidian whenever you want to browse:

```
/mnemo open
```

Your daily log lives at `~/mnemo/bots/<repo-name>/logs/YYYY-MM-DD.md`.

## Promoting notes to the wiki

```
/mnemo promote ~/mnemo/shared/people/alice.md
/mnemo compile
```

## Uninstalling

```
/mnemo uninstall
```

This removes hooks but **never** deletes your vault.

## Extracting canonical pages (v0.2)

After a week or two of real use, your `~/mnemo/bots/*/memory/` will contain
memory files that the Claude Code agents decided were worth remembering. These
are already in a canonical format but scattered across agents.

To consolidate them into `shared/`:

```bash
mnemo extract
```

This runs one `claude --print` subprocess per memory type (reusing your
existing Claude Code authentication — Pro/Max subscription or API key,
whichever is configured). The pipeline:

1. Promotes each `project` memory directly to
   `shared/project/<agent>__<slug>.md` (1:1, no LLM).
2. Clusters `feedback`, `user`, and `reference` memories across agents via an
   LLM call per type, producing consolidated pages in
   `shared/_inbox/<type>/<slug>.md`.

Review the `_inbox/` files, then move the ones you want into their canonical
location (`shared/feedback/`, `shared/user/`, `shared/reference/`). The plugin
never touches files outside `_inbox/` for cluster types.

**Flags:**
- `mnemo extract --dry-run` — show what would run without making any calls.
- `mnemo extract --force` — reprocess entries previously dismissed or promoted.

**Passive hint:** when you close a Claude Code session and there are ≥5 new
memory files since your last extraction, today's log will contain a `🟡` line
reminding you to run the command. This is cosmetic — nothing runs in the
background.

**Cost:** each run typically makes 3 calls (one per cluster type) and costs
a few cents on API-key auth, or $0 on subscription. The command prints a
summary with token counts after completion.
