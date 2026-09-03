# Getting started with mnemo

The deeper tour. For the pitch and the one-step install, read the
[README](../README.md) first — this page assumes you've seen it and want the
details.

## Install

### Option A — the plugin (recommended)

Inside Claude Code:

```
/plugin marketplace add xyrlan/mnemo
/plugin install mnemo@mnemo-marketplace
```

Nothing else is required: no terminal, no Python, no Node. mnemo ships as a
self-contained binary, and the plugin fetches the build for your platform on
first use into its own data directory, verifying it against a published
SHA-256 before installing it.

The download happens during your first `SessionStart` and takes a few seconds.
If it fails — offline, unsupported platform — mnemo stays quiet and does
nothing rather than interrupting the session; the next session retries.

### Option B — npm

Puts `mnemo` on your `$PATH` and wires everything into your own Claude Code
config rather than the plugin's.

```bash
npx @xyrlan/mnemo install              # prompts for global or project scope
npx @xyrlan/mnemo install --yes        # global, no prompts
npx @xyrlan/mnemo install --project --yes
npx @xyrlan/mnemo uninstall
```

It installs the Python package via whichever of `uv` / `pipx` / `pip --user`
you have. `uv` is preferred and needs nothing else — it brings its own Python.
The other two need Python 3.8+ already on `PATH`.

### Option C — pipx / uv directly

For dotfile-managed setups and CI:

```bash
pipx install mnemo-claude        # or: uv tool install mnemo-claude
mnemo init                       # global
mnemo init --project             # or: scoped to the current directory
```

`python -m mnemo` and the installed `mnemo` console script are equivalent.

### Migrating from B or C to the plugin

Installing the plugin on top of an existing `mnemo init` leaves **both** sets
of hooks live, so every session gets doubled capture, injection, and
enforcement. mnemo detects this and says so once at session start.

```bash
mnemo migrate-plugin
```

That strips the hooks the old install wrote, leaving a timestamped backup
beside each settings file. The plugin's own hooks are unaffected, and your
vault is never touched.

## Five minutes

The shortest path from "installed" to "it remembered something I said". Four
steps, one of them typing.

**1. Correct Claude, in your own words.** In any repo, in a normal session,
say the thing you'd say anyway:

```
never use npm in this repo, always yarn
```

No special syntax. mnemo is looking for you telling Claude to stop, change,
prefer, or never/always do something — phrased however you'd phrase it.

**2. Run `mnemo learn`** (or `/mnemo:learn` inside Claude Code). This is the
same briefing-and-extraction the `SessionEnd` hook runs on its own, except in
the foreground, on this session, right now — you don't have to end the session
and wait out the debounce to see whether the correction landed.

**3. Read the output.** It is the whole feature:

```
read: ~/.claude/projects/-Users-you-github-app/3f2a….jsonl
briefing: bots/app/briefings/sessions/3f2a….md (1 correction(s))
learned: use-yarn-not-npm — Use yarn, never npm (evidence: "never use npm in this repo, always yarn")
next prompt about this will surface it — check with `mnemo why`
```

Line by line: the transcript it read, the briefing it wrote and how many
corrections verified against that transcript, and then one `learned:` line per
rule that reached the vault — carrying **your own sentence** back to you as the
evidence. A rule with a quote is a rule mnemo can prove you asked for. If some
pages were held back you'll also see `staged for review: N
(shared/_inbox/reference/)`; if nothing was learned you get a hint saying so
rather than silence.

**4. Type your next prompt about packages.** The rule is already live. It
arrives on the `UserPromptSubmit` hook under the reflex's own
`reflex context:` header, and `/mnemo:why` shows the arithmetic — which rules
were scored, what they scored, and why the winner beat the threshold (or why
nothing fired).

That's the loop. Everything else in this document is that loop with more
knobs.

### What `mnemo learn` does not do

- **It doesn't extract your whole vault.** Stage 2 is scoped with `only=` to
  the briefing stage 1 just wrote. Any other dirty pages — other projects'
  backlogs — wait for the normal end-of-session run rather than being swept
  into LLM calls you didn't ask for.
- **It never opens a PR and never touches the network.** No `gh`, no issues,
  no self-fix branch. The only outbound calls are the LLM calls extraction
  already makes through your existing `claude` CLI.
- **It won't run while another extraction holds the lock.** If the
  `SessionEnd` hook's pass (or another `mnemo learn`) is already running, this
  one stops and says so: *another extraction is already running — it will pick
  up this session's briefing; run `mnemo learn` again in a minute to see what
  it learned.* The condition is benign — that running pass sweeps every dirty
  file, this briefing included.

### GIF storyboard

For the maintainer recording the README asset. Three frames, about 20 seconds,
no narration and no cuts mid-frame — the point is that a reader who never
scrolls past the image still understands the loop.

| # | Frame | Roughly |
|---|---|---|
| 1 | The correction. A normal Claude Code session; the user types `never use npm in this repo, always yarn` and Claude answers normally. Nothing mnemo-shaped happens on screen. | 6s |
| 2 | `mnemo learn`. The four output lines land, with the `learned:` line — and its `evidence:` quote — on screen long enough to read. | 8s |
| 3 | The next prompt. A fresh prompt about installing a package; the injected rule is visible in the context mnemo added, and Claude reaches for yarn. | 6s |

Record at a readable terminal size, don't speed it up, and let frame 2 sit —
the quote is the thing people need time to notice.

## What `mnemo init` actually does

Only relevant for options B and C — the plugin declares all of this itself.

It's idempotent; running it twice is safe. On first run it will:

1. Preflight: Python version, writable vault root, `~/.claude/` accessible.
2. Scaffold the vault tree at `~/mnemo/` (or `--vault-root <path>`).
3. Inject **four** hooks into `~/.claude/settings.json` (with a backup):
   `SessionStart`, `UserPromptSubmit`, `PreToolUse` (matching
   `Bash|Edit|Write|MultiEdit`), and `SessionEnd`.
4. Register the stdio MCP server in `~/.claude.json`.
5. Wire the additive status line composer, preserving any `statusLine` you
   already had.
6. Mirror existing Claude Code memories from `~/.claude/projects/*/memory/`
   into `bots/<repo>/memory/`.

In `--project` mode, everything lands under `<cwd>/.claude/`, `<cwd>/.mcp.json`,
and `<cwd>/.mnemo/` instead, and both are added to `.gitignore`.

Re-running `mnemo init` reconciles drift without clobbering your edits.

## Your first session

Just use Claude Code normally.

Your per-session trail lands at `~/mnemo/bots/<repo-name>/logs/YYYY-MM-DD.md`,
with `🟢` at session start and `🔴` at session end. Anything Claude saved to
its own memory directory is mirrored into `~/mnemo/bots/<repo-name>/memory/`.

Confirm it's live:

```
/mnemo:status
```

Under the plugin, hook health reads `Hooks (plugin): 4/4`. Under an npm or
pipx install it names the settings file instead, because that's where the
hooks live.

## Backfill

A brand-new vault knows nothing, so mnemo has nothing to inject for the first
few weeks — which is when most people give up on it. Backfill fixes that from
history you already have: Claude Code stores every session it has ever run at
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, and mnemo can read them.

### What happens on your first session

On the first session after install, mnemo spawns a background sweep of **the
repo you're sitting in** — newest sessions first, capped at
`backfill.installCap` (20). Each session costs one call to the `claude` CLI you
already have, using `extraction.model` (Haiku by default) and retried once if
it times out. Sessions that touched fewer than `backfill.minFileMutations`
files are skipped without a call.

What comes back is written into `bots/<repo>/memory/` — the same place live
capture writes — and the next extraction turns it into rules.

That is the whole automatic budget: it runs **once per vault**, never for your
other projects, and never again. Everything beyond it is something you type.

The calls go through your existing `claude` CLI on whatever authentication it
already uses. On a Pro/Max subscription that means no per-token charge; on
API-key auth it is billed like any other Haiku call.

To never let it run, before you upgrade or install:

```json
{ "backfill": { "autoOnFirstSession": false } }
```

### Backfilled pages are always staged for review

This is a guarantee, not a default. A backfilled page is the model's
*reconstruction* of a session that ended weeks ago — not something mnemo
watched happen. So every page it produces is stamped `origin: backfill`, and
every rule extracted from one lands in `shared/_inbox/<type>/`. **Nothing of
backfill origin is ever auto-promoted into `shared/`**, whatever its source
count, and the stamp survives across extraction runs.

Review them the way you'd review a pull request:

```bash
mnemo doctor        # lists what's staged and waiting
```

Read each file under `shared/_inbox/`. Move the keepers into the matching
directory (`shared/_inbox/project/foo.md` → `shared/project/foo.md`) and delete
the rest. Only then do they take part in injection.

Backfill also never overwrites an existing memory file — a page you or a live
session wrote always wins over a reconstruction of it.

### Running it yourself

```bash
mnemo backfill                      # this repo, everything not yet harvested
mnemo backfill --all                # every project on the machine
mnemo backfill --project mnemo      # one project by name
mnemo backfill --limit 10           # the 10 most recent of the selection
mnemo backfill --dry-run            # list what it would harvest, write nothing
mnemo backfill --yes                # skip the confirmation prompt
mnemo backfill --retry-failed       # un-retire transcripts that failed 3 times
```

It prints the session count, the projects involved and a rough input-token
estimate, then asks before spending anything. The estimate is measured on the
flattened text actually sent to the model, not on the bytes on disk, but it's
still an estimate: it counts sessions that the mutation threshold may skip
without a call, and it says nothing about output tokens.

**Which sessions get picked:**

- `--project NAME` and `--all` answer the same question, so passing both is an
  error rather than a silent win for one of them. `NAME` is the project name
  mnemo derives from the repo directory — the same name you see under `bots/`
  — not a path. Worktrees collapse into their main checkout.
- With neither flag, the selection is the current repo.
- `--limit` applies to whatever the above selected, newest first.
- `--limit 0` selects nothing, deliberately.

**What it survives:**

- A transcript that fails is recorded and stepped over; the sweep continues.
- Progress is written after every session, so interrupting with `Ctrl-C` and
  rerunning resumes where it stopped. Already-harvested sessions are skipped.
- Three failures retire a transcript for good, until `mnemo backfill
  --retry-failed` clears it.
- A failure of the *machine* rather than of a transcript — no `claude` CLI,
  expired auth, a rate limit — stops the sweep immediately and holds nothing
  against the transcripts it never reached.

Exit codes: `0` done, `1` finished with some sessions failed, `2` aborted on an
environment failure, `130` interrupted. Answering `n` at the prompt, or having
nothing to do, is a normal `0`.

`--dry-run` writes nothing at all — no memory files, no LLM calls, no bookkeeping
— and that holds even beside `--retry-failed`, which under a dry run reports
how many entries it *would* clear and previews the sweep as if it had.

## The loop

Everything below is on by default. To disable a piece, set it to `false` in
`~/mnemo/mnemo.config.json` — see [configuration.md](configuration.md) for the
full key reference.

### Extraction

At every `SessionEnd`, the hook checks whether there are at least
`extraction.auto.minNewMemories` new files since the last run and whether
`minIntervalMinutes` have passed. If so it spawns a detached background
extraction and returns in under 100ms — your session exits normally while
extraction runs.

Extraction shells out to the `claude` CLI you already have. Each run typically
makes 3 calls (one per cluster type) and costs a few cents on API-key auth, or
$0 on a Claude subscription.

**Output splits by source count:**

- **Single-source** pages (one source file, no clustering judgment needed) go
  straight to `shared/<type>/<slug>.md`, tagged `auto-promoted`. mnemo treats
  these as its own and rewrites them when the source changes — as long as you
  haven't edited them.
- **Multi-source** clusters (cross-agent merges, where the model made an
  editorial call) land in `shared/_inbox/<type>/<slug>.md` tagged
  `needs-review`. Review before promoting.

**Promoting is a plain move.** `mv shared/_inbox/feedback/x.md
shared/feedback/x.md` and you're done — the rule goes live at your next
session, when the activation index is rebuilt. Leave the `needs-review` tag
alone if you like; **location** is what decides whether a rule is a draft, not
the tag. (Before v0.18 the tag also hid the page, which quietly made promotion
a no-op. See [troubleshooting.md](troubleshooting.md).)

**Your edits win.** If you edit an auto-promoted page and its source later
changes, the new output is written as `shared/_inbox/<type>/<slug>.proposed.md`
rather than overwriting your file.

### Briefings

At every `SessionEnd`, mnemo writes a per-session briefing into
`bots/<repo>/briefings/sessions/`. Briefings are the dense input that feeds the
next extraction — the difference between capturing ~1 file/day and capturing
every meaningful decision.

### Injection

At `SessionStart`, mnemo emits a compact topic list into Claude's
`additionalContext`, telling it to call the MCP tools when a task matches a
known topic. Topics are filtered to the current project by default.

The MCP tools — `list_rules_by_topic`, `read_mnemo_rule`, `get_mnemo_topics` —
are always available; this flag only controls whether Claude is *told about*
them at session start. All three default to `scope="project"`; pass
`scope="vault"` for cross-project lookups.

### Reflex

On every prompt, mnemo runs BM25F retrieval over its rule index and injects
the single most relevant rule inline — only when it clears a triple gate on
term overlap, relative score gap, and an absolute floor. The floor scales
with the vault's size, so the first rule you learn can fire on the next
prompt instead of waiting for the vault to grow. Fail-open: any error means
the prompt passes through untouched.

### Enforcement and enrichment

At `PreToolUse`, a `Bash` command matching a rule you marked as a guardrail is
blocked outright. An `Edit`/`Write` whose path matches a rule's `activates_on`
gets that rule's body surfaced as context.

## Taking your rules with you

```bash
mnemo export                      # → .claude/rules/mnemo.md
mnemo export --target claude-md   # managed block inside CLAUDE.md instead
mnemo export --host cursor        # → .cursor/rules/mnemo.mdc
mnemo export --host codex         # managed block inside AGENTS.md
mnemo export --dry-run            # print the block, touch nothing
mnemo export --limit 10           # keep the ten most-sourced rules
mnemo export --remove             # delete the file / strip the block
```

What goes in: `feedback` and `user` rules attributed to this repo, plus
universal ones, most-sourced first. `reference` pages stay out unless you
pass `--all-types`. Each rule carries the sentence you said as
`> you said: "…"`. User-profile pages (`type: user`) are included too and
can carry names or emails — export tells you when one is in the block; pass
`--types feedback` to leave them out. When the block would load more than
about 4,000 tokens on every prompt, export says so on stderr and suggests a
`--limit`.

The block sits between `<!-- mnemo:start` and `<!-- mnemo:end -->`; anything
outside the markers in CLAUDE.md or AGENTS.md is never touched, and export
refuses to write if it finds a half-deleted or duplicated block. Re-running
regenerates the block; nothing flows back from the file into the vault.

Once a rule is in `.claude/rules/mnemo.md`, the reflex still ranks it but
does not inject it again — Claude Code is already loading it — and
`mnemo why` lists it as `exported`. `mnemo status` shows
`Export: N rules → … (up to date)` or how many rules differ from the vault
since you last exported.

### Cursor and Codex

```bash
mnemo init --host cursor          # ~/.cursor/mcp.json + .cursor/rules/mnemo.mdc
mnemo init --host cursor --project   # <repo>/.cursor/mcp.json instead of the global file
mnemo init --host codex           # runs `codex mcp add mnemo …` + AGENTS.md block
mnemo uninstall --host cursor     # removes only that MCP registration
```

What you get in those tools is the two halves that do not need a hook: the
MCP tools (`list_rules_by_topic`, `read_mnemo_rule`) and the rules file
`mnemo export` writes, loaded by the tool itself. What you do not get is
learning — mnemo reads Claude Code transcripts, not Cursor's or Codex's — so
correct Claude in Claude Code, run `mnemo learn`, then
`mnemo export --host cursor` (or `codex`) to refresh the file.

Codex has no per-project MCP config, so `--project` is refused there. If the
`codex` binary is not on your PATH, `init` prints the `[mcp_servers.mnemo]`
table to paste into `~/.codex/config.toml`. `mnemo status` lists the hosts it
finds registered; `mnemo doctor` checks that each registration points at a
command that still exists.

## Observing and debugging

```bash
mnemo status    # vault state, hook health, last auto-run, currently-running state
mnemo doctor    # full diagnostic: statusLine drift, stale locks, recent failures
mnemo extract   # manual extraction (also rebuilds the HOME dashboard)
mnemo fix       # reset the extraction circuit breaker after repeated failures
```

`mnemo status` and `mnemo doctor` also have slash forms under the plugin
(`/mnemo:status`, `/mnemo:doctor`); `open`, `fix` and `extract` are CLI-only.

Detailed errors land in `~/mnemo/.errors.log` under `where=extract.bg.*`. If
`mnemo doctor` warns about `statusLine` drift, you hand-edited
`~/.claude/settings.json` after `mnemo init` — re-run it to reconcile.

Manual extraction flags:

```bash
mnemo extract --dry-run   # show what would run without calling the LLM
mnemo extract --force     # reprocess entries previously dismissed or promoted
```

## Working on mnemo itself

The repo is also the plugin, so it ships a `.mcp.json`. Under a plugin
install Claude Code sets `CLAUDE_PLUGIN_ROOT` and the entry resolves to the
plugin's own `bin/launch`. Opened as a project that variable is unset, so the
entry falls back to `./bin/launch` (relative to the directory you launched
`claude` from — start it at the repo root) and the launcher runs your
editable install (`pip install -e .`) as `python3 -m mnemo` instead of
fetching a release binary. It recognises a dev tree by the gitignored
`src/mnemo_claude.egg-info` that `pip install -e .` leaves behind, or by
`MNEMO_DEV=1`; set `MNEMO_PYTHON` to pick the interpreter. The version it
reports comes from that egg-info, so re-run `pip install -e .` after a
version bump.

The entry is `bash bin/launch`, not `bin/mnemo.cmd`, because Claude Code
spawns stdio MCP servers directly (no shell) and the polyglot `.cmd` has no
shebang. Nested defaults such as `${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR}}`
are not expanded by Claude Code, hence the plain `.` fallback.

If you would rather keep using your global `mnemo` registration inside the
repo, add `"disabledMcpjsonServers": ["mnemo"]` to
`.claude/settings.local.json`. The user-vs-project "conflicting scopes"
notice from `claude mcp list` is expected while both exist.

## Uninstalling

Plugin: `/plugin uninstall mnemo`.

npm: `npx @xyrlan/mnemo uninstall` (also removes the Python package).

pipx/uv: `mnemo uninstall`, then `pipx uninstall mnemo-claude`.

All of them remove hooks, the MCP registration, and the status line composer.
**Your vault is never deleted** — `rm -rf ~/mnemo` is a separate, conscious
step.
