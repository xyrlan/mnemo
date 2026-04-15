# mnemo

> The Obsidian that populates itself so your Claude never forgets.

**mnemo** is a Claude Code plugin that turns every coding session into a
self-organizing knowledge base — and then feeds that knowledge back into
Claude so it stops forgetting what you taught it last week.

It runs as a hooks-only, stdlib-only Python package. Zero third-party
dependencies, zero telemetry, zero network calls. Identical on Linux,
macOS, and Windows.

## Install

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
/mnemo init
```

`mnemo init` is idempotent and does four things:

1. Scaffolds a vault at `~/mnemo/` (or wherever you point it)
2. Injects two hooks (`SessionStart`, `SessionEnd`) into `~/.claude/settings.json`
3. Registers a stdio MCP server in `~/.claude.json` so Claude Code can call mnemo's tools
4. Wires an additive status line composer (preserves your existing one if you have it)

That's it. Use Claude Code normally — your vault populates itself, the
HOME dashboard regenerates after every extraction, and Claude starts
consulting captured rules on its own.

## How it works — Capture → Present → Inject

mnemo's tagline is one sentence: *"so your Claude never forgets."* That
breaks into three stages, each shipped in a different release, all live
together in v0.5.

### 1. Capture (v0.2 → v0.3.1)

mnemo watches Claude Code's lifecycle hooks and writes a structured trail
into your vault as you work:

- **Session start / end markers** — `🟢` and `🔴` in `bots/<repo>/logs/YYYY-MM-DD.md`
- **Claude memory mirror** — anything Claude saves to `~/.claude/projects/*/memory/`
  is mirrored into `bots/<repo>/memory/` so it lives next to the rest of the trail
- **Per-session briefings** *(opt-in, v0.3.1)* — at session end, an LLM pass
  summarizes the full transcript into a structured handoff document under
  `bots/<repo>/briefings/sessions/`. Briefings are the dense input that
  feeds extraction; they're the difference between mnemo capturing
  ~1 file/day vs. capturing every meaningful decision.

The extraction pipeline (`mnemo extract`, also auto-run after sessions
when enabled) consolidates everything in `bots/` into canonical Tier 2
pages under `shared/{feedback,user,reference,project}/`. Single-source
pages auto-promote into the canonical layer; multi-source clusters
(cross-agent merges) stage in `shared/_inbox/<type>/` for review. Your
manual edits to auto-promoted files are protected by content-addressing —
a conflict produces a `.proposed.md` sibling instead of overwriting your
work.

### 2. Present (v0.4)

Capture without surfacing is just a dump. v0.4 added two consumer
surfaces over the same data:

- **HOME.md dashboard** — a managed block at the top of `HOME.md`
  regenerates after every extraction. Pages are grouped by trust tier
  (cross-agent synthesized first, single-source auto-promoted second) and
  by topic tag, so you can scan the project brain in seconds inside
  Obsidian. Everything below the managed block is yours to edit; mnemo
  never touches it.
- **Dimensional tags** — extraction asks the LLM to tag each page with
  topic kebab-case identifiers (`auth`, `react`, `package-management`),
  using a controlled-vocabulary hint built from your existing vault tags
  to prevent sprawl. Tags become the ontology Claude navigates in v0.5.

### 3. Inject (v0.5)

The loop closes. Claude Code itself reaches into mnemo at the start of
every session, no manual command needed.

- **MCP stdio server** — `mnemo init` registers a long-running JSON-RPC
  server in `~/.claude.json` exposing three read-only tools:
  - `list_rules_by_topic(topic)` — slugs sorted by source count desc
    (multi-agent synthesized rules surface first)
  - `read_mnemo_rule(slug)` — full body + frontmatter
  - `get_mnemo_topics()` — sorted union of all topic tags in the vault
- **SessionStart topic injection** *(opt-in)* — the SessionStart hook
  emits a ~120-token instruction listing the topics in your vault and
  telling Claude to call the MCP tools BEFORE writing code when the task
  matches a known topic. ~120 tokens regardless of vault size: the topic
  list is the only thing pre-loaded; rule bodies are fetched on demand.
- **Filter parity** — both the dashboard and the MCP tools call the same
  `is_consumer_visible` predicate, so evolving and needs-review pages
  never reach Claude.

The result: Claude consumes in real time the rules you taught it weeks
earlier in a different session, without you having to remember to copy
them in.

## Status line

After `mnemo init`, your Claude Code status line shows the brain's heartbeat:

```
mnemo mcp · 9 topics · 7↓ today
```

- `mnemo mcp` — MCP server is registered in `~/.claude.json`
- `9 topics` — topic tags currently known in your vault (live count)
- `7↓ today` — number of times Claude has called a mnemo MCP tool today
  (resets at midnight, atomic write)

The status line is **additive**: if you already had a custom statusLine
in `~/.claude/settings.json`, mnemo wraps it instead of overwriting.
Your original output appears first, then ` · `, then mnemo's segment.
`mnemo uninstall` restores your original cleanly. If you manually edit
settings.json after `mnemo init`, `mnemo doctor` warns about the drift.

## Opt-in flags

Three runtime features default to **off**, matching the conservative
release pattern: ship dark, dogfood, then flip. Enable in `~/mnemo/mnemo.config.json`:

```json
{
  "extraction": {
    "auto": {
      "enabled": true,
      "minNewMemories": 1,
      "minIntervalMinutes": 60
    }
  },
  "briefings": {
    "enabled": true
  },
  "injection": {
    "enabled": true
  }
}
```

- **`extraction.auto.enabled`** — at every session end, if there are at
  least `minNewMemories` new files (default 1) and at least
  `minIntervalMinutes` since the last run (default 60), spawn a detached
  background extraction. The hook returns in <100ms; extraction runs
  asynchronously. Check progress via `mnemo status`, diagnose with `mnemo doctor`.
- **`briefings.enabled`** — at every session end, generate a per-session
  briefing into `bots/<repo>/briefings/sessions/`. Briefings feed the
  next extraction run as dense input.
- **`injection.enabled`** *(v0.5)* — at every session start, emit the MCP
  topic list into Claude's `additionalContext`. The MCP tools are always
  available once `mnemo init` has run; this flag controls only whether
  Claude is *told about* the topics at session start.

## Commands

```
mnemo init       first-run setup (idempotent)
mnemo status     vault state + hook health + auto-brain state
mnemo doctor     full diagnostic with actionable fixes
mnemo extract    manually run the extraction pipeline (also rebuilds the dashboard)
mnemo open       open vault in Obsidian / file manager
mnemo fix        reset circuit breaker
mnemo uninstall  remove hooks, MCP server, status line (vault preserved)
mnemo help       list commands
```

## Where things live

```
~/mnemo/                              your vault
├── HOME.md                           dashboard at the top, your notes below
├── bots/<repo-name>/
│   ├── logs/YYYY-MM-DD.md            session start/end markers
│   ├── memory/                       mirror of Claude memories for this repo
│   └── briefings/sessions/           per-session shift handoffs (opt-in)
├── shared/
│   ├── feedback/                     auto-promoted preference rules
│   ├── user/                         user-profile facts
│   ├── reference/                    pointers to external systems
│   ├── project/                      per-repo project context
│   └── _inbox/<type>/                drafts awaiting review
└── .mnemo/
    ├── extract.lock                  background extraction lock
    ├── last-auto-run.json            background extraction telemetry
    ├── mcp-call-counter.json         daily MCP tool call counter (v0.5)
    └── statusline-original.json      preserved user statusLine (v0.5)

~/.claude/settings.json               hooks + status line composer
~/.claude.json                        MCP server registration
```

See [docs/getting-started.md](docs/getting-started.md) for a deeper tour.

## Privacy

100% local. Zero telemetry. Zero network. No third-party Python packages
(`pyproject.toml` declares `dependencies = []` as a load-bearing
architectural choice). Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
