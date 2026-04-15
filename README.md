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

## MCP injection (v0.5)

v0.5 closes the loop: Claude Code itself reaches into your mnemo brain
without you doing anything. `mnemo init` registers a stdio MCP server in
`~/.claude.json`. When `injection.enabled=true` is set, every new session
gets a one-line instruction listing the topics Claude can query plus the
two tools available — `list_rules_by_topic(topic)` and `read_mnemo_rule(slug)`.

When the current task touches a known topic, Claude pulls the matching rules
out of `shared/<type>/` via MCP and uses them BEFORE writing code. No vector
DB, no embeddings, no manual `mnemo context` invocation — the LLM navigates
your tags as an ontology by zero-shot reasoning.

Opt-in via `~/mnemo/mnemo.config.json`:

```json
{
  "injection": {
    "enabled": true
  }
}
```

Cost is ~120 tokens per session regardless of vault size: the topic list is
the only thing pre-loaded; rule bodies are fetched on demand. Filter parity
with the HOME dashboard is enforced by `core/filters.py` so evolving and
needs-review pages never reach Claude.

## Privacy

100% local. Zero telemetry. Zero network. No third-party packages. Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
