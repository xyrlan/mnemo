# mnemo

> Claude Code forgets your corrections. mnemo doesn't.

Monday, in your app repo, you tell Claude: *"never use npm in this repo, always
yarn."* You run `mnemo learn` — or you just end the session and let it happen.
Thursday, a new session, you ask Claude to add a dependency: that rule is
injected before Claude answers. One or two short lines, about 150 tokens, and
only when it clearly applies.

What gets injected is a 300-character preview plus a pointer, under a
`reflex context:` header: `• [[use-yarn-not-npm]]: Use yarn, never npm …`. mnemo
also registers an MCP server so Claude can call `read_mnemo_rule` for the full
text when the preview is not enough; the plugin sets it up, and `/mnemo:doctor`
tells you if it is not connected.

A rule that recurs in two different repos is promoted to universal and follows
you everywhere.

Measured on the maintainer's vault, 2026-09-02:

```
reflex: injected on 90 of 1041 prompts (8.7%)
recall: primacy@5 41.7% over 72 cases (mnemo recall, 2026-09-01)
```

`reflex` is the per-prompt recall; `primacy@5` means the rule that session
actually needed was in the top five. On topics with more than 20 rules,
query-aware ranking lifted primacy@5 from 16% to 31% (530 evaluations). Your
own numbers: `mnemo status`.

## How it compares

**CLAUDE.md** — you write it and prune it by hand, and it is loaded whole,
every session, whether or not any of it is relevant to what you're doing.

**Claude Code auto memory** — Claude writes it for you, but only the head of
the index loads every session (200 lines as of 2026-09) and the rest is
silently dropped. Nothing is ranked against the prompt in front of you.

**mnemo** — learns from your corrections and keeps a verifiable quote of what
you actually said. Rules it can't verify stay staged for your review instead of
entering the vault. Then it injects at most two rules per prompt — usually
one — chosen by BM25F against the prompt text. No database, no daemon, no
per-prompt LLM call.

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
sees this — run `mnemo migrate-plugin` to clear the old install. Your vault is
untouched.

</details>

## Check it worked

The five-minute loop is written out in
[docs/getting-started.md](docs/getting-started.md#five-minutes): correct Claude
in your own words, run `mnemo learn`, read the output, type your next prompt on
that subject.

Step 3 is the whole feature:

```
read: ~/.claude/projects/-Users-you-github-app/3f2a….jsonl
briefing: bots/app/briefings/sessions/3f2a….md (1 correction(s))
learned: use-yarn-not-npm — Use yarn, never npm (evidence: "never use npm in this repo, always yarn")
next prompt about this will surface it — check with `mnemo why`
```

The `evidence:` quote is your own sentence, carried back to you. A rule with a
quote is a rule mnemo can prove you asked for.

Per-prompt recall (the *reflex*) is silent by design — it injects a rule only
when one clearly beats the rest. To see the decisions themselves:

```
/mnemo:why
```

```
09:41:24  injected  mnemo-1.0-roadmap (6.84)
          ahead of  recall-degrades-with-topic-size (3.45)

09:30:07  silent    recall-degrades-with-topic-size led at 4.21 but needed 5.78
                    (1.50 x the runner-up's 3.85) to be clearly ahead
                    recall-degrades-with-topic-size  4.21
                    mnemo-1.0-roadmap                3.85
```

Silence with a reason is the difference between "my vault has nothing useful"
and "my thresholds are a little too tight".

## Day one

A fresh vault has nothing to inject, and mnemo does not go rummaging through
your history uninvited. Claude Code keeps every past session on disk, so your
first session in a repo with harvestable transcripts prints one line: how many
sessions are there, and roughly what reading them would cost.

Nothing runs until you say so:

```bash
mnemo backfill --dry-run    # exactly what it would read, and what that costs
mnemo backfill              # this repo
mnemo backfill --all        # every project
```

Backfilled pages are reconstructed rather than observed, so every rule that
comes out of them lands in `shared/_inbox/` for you to read — **backfilled
material is never auto-promoted into `shared/`**. `/mnemo:doctor` lists what's
waiting. Details in
[docs/getting-started.md](docs/getting-started.md#backfill).

## Commands

```
/mnemo:status   vault state + hook health
/mnemo:why      why per-prompt recall fired, or didn't, on your last prompts
/mnemo:doctor   full diagnostic with actionable fixes
/mnemo:learn    learn from this session now
/mnemo:help     list commands
```

Everything else is a CLI subcommand (`mnemo help --all`): `mnemo open`,
`mnemo fix`, `mnemo statusline`, `mnemo backfill`, `mnemo reclassify`,
`mnemo autopilot`, `mnemo disable-rule`, and the rest.

Want the live heartbeat in your status line (`mnemo · 9 topics · 7↓ today`)?
It's opt-in, because plugins can't set a status line:

```bash
mnemo statusline --install
```

Uninstall with `/plugin uninstall mnemo`. The vault is always preserved.

## Autopilot

Between sessions, mnemo keeps its own brain in shape — rebuilding indices,
sweeping dead rules, and calibrating how often rules get injected against your
own hit/miss log. Nothing runs on the prompt path, and all of it is local.

It opens GitHub issues or pull requests only if you set
`autopilot.network.enabled` to `true`.

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

100% local. No network calls unless you turn `autopilot.network.enabled` on.
No third-party Python dependencies. Every piece of telemetry
(`.mnemo/*.jsonl`) stays on disk.

LLM calls go through the `claude` CLI you already have — one per session for
the briefing, and one per ten new files at extraction time — never on the
prompt path. Logs are capped at 1 MB; briefings accumulate, one file per
session. The one
other outbound call is the plugin downloading its binary from GitHub Releases
on first use (checksum-verified). Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
