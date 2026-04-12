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
