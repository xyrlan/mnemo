# mnemo

> The Obsidian that populates itself so your Claude never forgets.

**mnemo** turns every Claude Code session into a self-organizing knowledge
base, then feeds that knowledge back into Claude so it stops forgetting
what you taught it last week.

It runs locally as a hooks-only Python plugin — zero third-party
dependencies, zero network calls, identical on Linux, macOS, and Windows.

## What it does

- **Captures** every session's lifecycle into a Markdown vault at `~/mnemo/`
  (logs, memory mirrors, end-of-session briefings).
- **Extracts** that raw trail into curated rules under
  `shared/{feedback,user,reference,project}/` — your project's brain.
- **Surfaces** the brain back into Claude:
  - a HOME dashboard that regenerates after every extraction
  - an MCP server Claude can query for topics and rules
  - a status line showing the brain's heartbeat
  - automatic injection of the most relevant rule on every prompt
  - hard guardrails on `Bash` and contextual hints on `Edit`/`Write`

The result: Claude consumes in real time the rules you taught it weeks
earlier in a different session, without you having to copy them in.

## Install

One command:

```bash
npx @xyrlan/mnemo install
```

That installs the Python package (via `uv` / `pipx` / `pip --user`,
whichever is available), prompts you to choose **global** (every Claude
Code session) or **project** (this directory only), and wires the hooks,
MCP server, and slash commands.

Non-interactive:

```bash
npx @xyrlan/mnemo install --yes              # global
npx @xyrlan/mnemo install --project --yes    # this directory only
```

To remove:

```bash
npx @xyrlan/mnemo uninstall
```

The vault is always preserved on uninstall.

**Prerequisite:** Python 3.8+ on PATH. Node is already there if you can
run `npx`.

### Via Claude Code (just ask)

If you already have Claude Code open in the repo you want mnemo on, paste
this prompt — Claude will pick `pipx` / `uv` / `pip --user` based on what
you have, install `mnemo-claude`, and run `mnemo init --project`:

> Install mnemo for this project. Use `pipx install mnemo-claude` if
> `pipx` is available, otherwise `uv tool install mnemo-claude`, otherwise
> `python3 -m pip install --user mnemo-claude`. Then run
> `mnemo init --project --yes`. If `mnemo` isn't on PATH after install,
> tell me which directory to add to my shell profile.

For a global install (every Claude Code session, not just this repo),
swap `--project` for nothing and drop the directive.

### Without npm

```bash
pipx install mnemo-claude        # or: uv tool install mnemo-claude
mnemo init                        # global, or:
mnemo init --project              # current directory only
```

## Use it

Once installed, just use Claude Code normally. mnemo runs in the
background:

- session start/end markers, memory mirroring, and briefings happen
  automatically
- extraction runs after sessions end (when there's enough new material)
- on every prompt, mnemo retrieves the single most relevant rule and
  injects it before Claude responds
- when Claude is about to edit a file or run a command that matches one
  of your rules, the rule body is surfaced as context (or hard-blocked,
  if you marked it as a guardrail)

Open the vault any time:

```bash
mnemo open
```

Edit `HOME.md`'s notes section freely — mnemo only manages the dashboard
block at the top.

## Commands

```
mnemo init [--project]    first-run setup (global or scoped to <cwd>)
mnemo status              vault state + hook health
mnemo doctor              full diagnostic with actionable fixes
mnemo extract             run the extraction pipeline manually
mnemo regen-graph-edges   refresh wikilinks for graph viewers (idempotent)
mnemo open                open the vault
mnemo uninstall           remove hooks, MCP server, status line
mnemo help                list commands
```

The same commands are available as slash commands inside Claude Code
(`/init`, `/status`, `/doctor`, `/open`, …).

## Optional: browse the vault in Obsidian

The vault is plain Markdown — any editor with graph support reads it as
is. If you point [Obsidian](https://obsidian.md/) at `~/mnemo/`:

- rules link to the briefings they were extracted from,
- briefings link back to the rules they spawned,
- the **Graph view** renders the rule↔briefing network out of the box.

Run `mnemo regen-graph-edges` once to refresh the wikilink sections on
existing rules and briefings (the extractor emits them automatically for
new ones). The section is bookended by an HTML comment marker so it stays
invisible to mnemo's retrieval — zero impact on Claude's context, zero
impact on BM25F scoring.

For a more readable graph, open Graph view → settings → **Groups** and
add (in order — first match wins):

| Query                              | Suggested color |
|------------------------------------|-----------------|
| `path:shared/feedback`             | green           |
| `path:shared/user`                 | yellow          |
| `path:shared/reference`            | purple          |
| `path:shared/_inbox`               | orange          |
| `path:briefings/sessions`          | blue (hubs)     |
| `path:memory`                      | cyan            |
| `file:HOME`                        | red (dashboards)|
| `path:bots`                        | light gray      |

These groups stay in your local `.obsidian/` folder — Obsidian is **not**
a mnemo dependency, and the vault works identically without it.

## Where things live

```
~/mnemo/                  your vault
├── HOME.md               dashboard at the top, your notes below
├── bots/<repo>/          per-project capture (logs, memory, briefings)
├── shared/               curated rules — the project brain
│   ├── feedback/         preferences and corrections
│   ├── user/             user-profile facts
│   ├── reference/        pointers to external systems
│   └── project/          per-repo project context
└── .mnemo/               internal state (indices, telemetry)

~/.claude/settings.json   hooks + status line composer
~/.claude.json            MCP server registration
~/.claude/commands/       slash commands
```

In `--project` mode, everything lives under `<cwd>/.claude/`,
`<cwd>/.mcp.json`, and `<cwd>/.mnemo/` instead.

## Privacy

100% local. Zero network. No third-party Python dependencies. Every
piece of telemetry (`.mnemo/*.jsonl`) stays on disk — nothing leaves
your machine. Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
