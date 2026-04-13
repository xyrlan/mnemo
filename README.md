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

## Auto-brain mode (v0.3)

v0.3 can run extraction in the background after Claude Code sessions, so
single-source pages auto-promote into your canonical `shared/<type>/` layer
without any manual step. Multi-source clusters (cross-agent merges) still
stage in `shared/_inbox/<type>/` for review.

Opt-in via `~/mnemo/mnemo.config.json`:

```json
{
  "extraction": {
    "auto": {
      "enabled": true,
      "minNewMemories": 5,
      "minIntervalMinutes": 60
    }
  }
}
```

Once enabled, a SessionEnd with ≥5 new memories and ≥60min since the last
run spawns a detached background extraction. The hook returns in <100ms
and the extraction runs asynchronously. Check progress with `mnemo status`
or diagnose problems with `mnemo doctor`. Your manual edits to auto-promoted
files are protected by content-addressing: a conflict produces a
`.proposed.md` sibling in `_inbox/` rather than overwriting your work.

## Privacy

100% local. Zero telemetry. Zero network. No third-party packages. Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
