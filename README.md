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

## Rule activation *(v0.5)*

The three flags above tell Claude *that rules exist* at session start. **Rule
activation** makes mnemo push a rule directly into Claude's turn at the exact
moment it's about to run a tool — not just once per session. Two modes share
the same per-project index and the same `PreToolUse` hook:

- **Enforcement** — when Claude is about to run a `Bash` command that
  matches a `deny_pattern` regex or a `deny_command` prefix from any rule
  owned by the current project, the hook emits `permissionDecision: deny`
  and Claude Code blocks the call with the rule's `reason` string visible to
  the model. Use for hard guardrails: "never commit with Co-Authored-By",
  "never run `drizzle-kit push`".
- **Enrichment** — when Claude is about to run `Edit`, `Write`, or
  `MultiEdit` on a file path that matches one of a rule's `path_globs`, the
  hook emits `additionalContext` containing the rule body preview (up to 3
  matching rules, ordered by source count). Claude sees the text as a
  `<system-reminder>` *before* performing the edit. Use for advisory rules
  with file-level scope: "HeroUI v3 modals use the Drawer slot pattern",
  "React key remount is required for these components".

Both modes are **strictly per-project** — a rule captured while working on
project A never fires while working on project B. Project ownership is
derived from the `sources:` frontmatter field (e.g. `bots/sg-imports/...`
belongs to `sg-imports`). Cross-project rules self-heal via repeated
capture.

### Defaults and kill switch

- **`enforcement.enabled`** defaults to **`true`**. This is safe because
  enforcement is *inert until you own a rule with an `enforce:` block* — a
  fresh vault has zero such rules, so the hook fires but matches nothing.
  Rules gain activation metadata either by hand-editing the frontmatter or
  when the extraction LLM emits it for a high-confidence rule (see "Rule
  frontmatter shape" below).
- **`enrichment.enabled`** defaults to **`false`** because it's slightly
  more invasive — enrichment surfaces context *every* time you edit a
  matching file, not just once per block. Turn it on when you have
  `activates_on:` rules you want visible at edit time.

To disable enforcement (kill switch), add to `~/mnemo/mnemo.config.json`:

```json
{
  "enforcement": { "enabled": false }
}
```

The `PreToolUse` hook is **absolutely fail-open**: any error at any stage
(missing index, corrupt JSON, exception in match logic) returns exit code 0
with empty stdout. The hook can never block Claude Code from running. You
can trust it not to brick your session even if mnemo itself is broken.

### Rule frontmatter shape

Both blocks are optional and live in a feedback page's YAML frontmatter:

```yaml
---
name: no-co-authored-by-in-commits
description: Never add Co-Authored-By trailers in git commits
type: feedback
stability: stable
sources:
  - bots/mnemo/memory/feedback_no_coauthored.md
tags:
  - git
  - workflow
enforce:
  tool: Bash
  deny_pattern: git commit.*Co-Authored-By
  reason: No Co-Authored-By trailers in commits
activates_on:
  tools: [Edit, Write, MultiEdit]
  path_globs:
    - '**/components/modals/**'
    - '**/*modal*.tsx'
---
```

- `enforce.tool` must be `"Bash"` in v0.5 (the only tool v1 supports
  for hard-blocking).
- `enforce.deny_pattern` is a regex compiled with `re.IGNORECASE | re.DOTALL`
  and pre-validated against ReDoS at index build time (a time-budget probe
  rejects pathological patterns like `(a+)+b`).
- `enforce.deny_command` is an alternative to the regex: a list of command
  prefixes; the hook normalizes the command (strips `sudo`, `env FOO=bar`,
  shell inline env) before the match.
- `activates_on.tools` must be a subset of `{Edit, Write, MultiEdit}`.
- `activates_on.path_globs` supports `**` (match any number of path
  segments), single `*` (no slash crossing), and bracket classes with
  `[!...]` negation.

Rules tagged `needs-review` or with `stability: evolving`, and rules still
in `shared/_inbox/`, **never** become activation rules — they're gated
through the same `is_consumer_visible` predicate that the dashboard and
MCP retrieval use.

### Observability

- `mnemo status` — shows per-project rule counts, recent denials, recent
  enrichments, and the last denied command.
- `mnemo doctor` — checks for malformed `enforce:`/`activates_on:` blocks,
  stale activation index, suspicious `deny_pattern` regex, and overly
  broad `path_globs` (`**/*`, `*`).
- `<vault>/.mnemo/denial-log.jsonl` — JSONL stream of every deny, with
  slug, reason, tool, full command (truncated to 500 chars), timestamp.
- `<vault>/.mnemo/enrichment-log.jsonl` — JSONL stream of every enrichment,
  with hit slugs, tool name, file path, timestamp.
- `<vault>/.mnemo/rule-activation-index.json` — the per-project index
  written by `build_index` after every extraction and on session start. If
  you suspect drift, delete it; the next session rebuilds it.

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
