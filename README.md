# mnemo

> The Obsidian that populates itself so your Claude never forgets.

**mnemo** is a Claude Code plugin that automatically captures every session
into a local Obsidian-compatible markdown vault. Hooks-only, stdlib-only,
zero telemetry, runs identically on Linux, macOS, and Windows.

## Install

```
/plugin install mnemo@claude-plugins-official
/mnemo init
```

That's it. Now use Claude Code normally and your vault grows on its own.

## What gets captured

- **Session starts and ends** — `🟢` and `🔴` markers in the daily log
- **Every prompt** — first non-empty line, ≤200 chars
- **Every file Write/Edit** — relative path with create/edit verb
- **Claude memories** — mirrored from `~/.claude/projects/*/memory/`

## Where it goes

`~/mnemo/bots/<repo-name>/logs/YYYY-MM-DD.md`. See [docs/getting-started.md](docs/getting-started.md).

## Privacy

100% local. Zero telemetry. Zero network. No third-party packages. Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
