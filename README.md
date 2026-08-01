# mnemo

> The Obsidian that populates itself so your Claude never forgets.

You correct Claude on Monday. On Thursday, in a different repo, it makes the
same mistake — because the session where you taught it is gone.

**mnemo fixes that.** It watches your Claude Code sessions, distils what you
taught into durable rules, and feeds the relevant one back the next time it
matters. Across sessions. Across projects. Without you copying anything.

```
Monday    you: "don't use `any` in this codebase — we have strict types"
                                    ↓
          mnemo writes shared/feedback/typescript-no-any.md

Thursday  you: "add a helper to parse the config"   (different repo, new session)
                                    ↓
          mnemo injects that rule before Claude answers
```

Four moving parts, all on your machine:

- **Capture** — every session's trail into a Markdown vault at `~/mnemo/`
- **Extract** — that trail into curated rules under `shared/`
- **Inject** — the most relevant rule, on every prompt, before Claude answers
- **Self-maintain** — an autopilot that heals and retunes itself between sessions

Zero third-party dependencies. Zero network calls. Identical on Linux, macOS,
and Windows.

## Install

Inside Claude Code, type:

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
```

That's the whole thing. No terminal, no Python, no Node — mnemo ships as a
self-contained binary that the plugin fetches for your platform on first use.

Restart Claude Code, and it's running.

<details>
<summary>Other ways to install</summary>

**Via npm** — if you'd rather have `mnemo` on your `$PATH`:

```bash
npx @xyrlan/mnemo install              # prompts for global or project scope
npx @xyrlan/mnemo install --yes        # global, no prompts
npx @xyrlan/mnemo install --project --yes
```

**Via pipx / uv** — for dotfile-managed setups and CI:

```bash
pipx install mnemo-claude    # or: uv tool install mnemo-claude
mnemo init                   # global, or `mnemo init --project`
```

Both need Python 3.8+ (uv brings its own). See
[docs/getting-started.md](docs/getting-started.md) for the details, including
what `mnemo init` writes and how to undo it.

**Already installed mnemo the old way?** Installing the plugin on top means
both sets of hooks fire and everything happens twice. mnemo tells you when it
sees this — run `/mnemo:migrate` to clear the old install. Your vault is
untouched.

</details>

## Check it worked

```
/mnemo:status     vault state + hook health
/mnemo:doctor     full diagnostic, with fixes
```

A healthy `status` opens like this:

```
Vault: /Users/you/mnemo  (exists)
Hooks (plugin): 4/4 — declared by the mnemo plugin
Circuit breaker: closed (ok)
```

Then just use Claude Code. mnemo runs in the background: it logs sessions,
writes a briefing at the end of each one, extracts rules when there's enough
new material, and injects the most relevant rule on every prompt.

Want the live heartbeat in your status line (`mnemo · 9 topics · 7↓ today`)?
It's opt-in, because plugins can't set a status line:

```
/mnemo:statusline
```

## Commands

```
/mnemo:status       vault state + hook health
/mnemo:doctor       full diagnostic with actionable fixes
/mnemo:open         open the vault
/mnemo:fix          reset the extraction circuit breaker
/mnemo:statusline   install the optional status line
/mnemo:migrate      remove a pre-plugin install
/mnemo:help         list commands
```

Uninstall with `/plugin uninstall mnemo`. The vault is always preserved.

If you installed via npm or pipx, the same commands are `mnemo <name>` in a
terminal, plus `mnemo init`, `mnemo extract`, and `mnemo autopilot`
(`mnemo help --all` for the full list).

## Autopilot

Between sessions, mnemo keeps its own brain in shape. Work is scheduled in
tiers and rate-limited by a budget — nothing runs on the prompt path. It
repairs rule provenance, grid-searches retrieval weights against your own
hit/miss log, calibrates how often rules get injected, and files a weekly
health digest. On by default, fully local for the mechanical work.

Control it with `mnemo autopilot {status,pause,off,on}`.

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
```

Edit `HOME.md`'s notes section freely — mnemo only manages the dashboard block
at the top.

## Docs

- [Getting started](docs/getting-started.md) — the deeper tour: every install
  path, what gets written where, and how the loop works
- [Configuration](docs/configuration.md) — every knob in `mnemo.config.json`
- [Troubleshooting](docs/troubleshooting.md) — when something looks wrong
- [Obsidian](docs/obsidian.md) — optional: browse the vault as a graph

## Privacy

100% local. Zero network calls in normal operation. No third-party Python
dependencies. Every piece of telemetry (`.mnemo/*.jsonl`) stays on disk.

Two exceptions, both explicit: the plugin downloads its binary from GitHub
Releases on first use (checksum-verified), and rule extraction shells out to
the `claude` CLI you already have — which is what turns raw session logs into
rules. Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
