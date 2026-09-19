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

`docs/assets/loop.gif` is recorded by `tools/demo/loop.tape` against the real
`claude` CLI in a throwaway repo (`tools/demo/README.md` has the procedure and
the log of runs). Four frames, under a minute, no cuts mid-frame (the
`/exit` between frames is not recorded):

| # | On screen | Hold |
|---|-----------|------|
| 1 | `claude` in the demo repo; the user types `never use npm in this repo, always yarn`; Claude acknowledges. | 3s |
| 2 | `/exit`, then `mnemo learn`: the `learned:` line, with `evidence: "never use npm in this repo, always yarn"` — your own sentence, carried back. | 6s |
| 3 | `claude` again; the user types `add the lodash package to the dependencies`; Claude runs `yarn add lodash`, not `npm install`. | 3s |
| 4 | `/exit`, then `mnemo why`: the top entry reads `injected  <slug>`. | 5s |

Frame 4 exists because the injection itself is invisible in the TUI
(`UserPromptSubmit` context is not rendered); what a viewer can see is
Claude's behaviour and the receipt. Frames 2 and 4 are the proof and hold
longest. A run where Claude does not reach for yarn is discarded and logged,
never edited.

## What `mnemo init` actually does

Only relevant for options B and C — the plugin declares all of this itself.

It's idempotent; running it twice is safe. On first run it will:

1. Preflight: Python version, writable vault root, `~/.claude/` accessible.
2. Scaffold the vault tree at `~/mnemo/` (or `--vault-root <path>`).
3. Inject **four** hooks into `~/.claude/settings.json` (with a backup):
   `SessionStart`, `UserPromptSubmit`, `PreToolUse` (matching
   `Bash|Read|Edit|Write|MultiEdit`), and `SessionEnd`.
4. Register the stdio MCP server in `~/.claude.json`.
5. Wire the additive status line composer, preserving any `statusLine` you
   already had.
6. Write the slash commands to `~/.claude/commands/` and the skills to
   `~/.claude/skills/` — the same `/mnemo:*` menu and the same
   `decomposing-for-dispatch` and `mnemo-loop` skills the plugin ships by
   convention. `mnemo doctor` reports a skill that is missing or that Claude
   Code would not index.
7. Mirror existing Claude Code memories from `~/.claude/projects/*/memory/`
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

## Where things live

```
~/mnemo/                  your vault
├── HOME.md               dashboard at the top, your notes below
├── bots/<repo>/          per-project capture (logs, memory, briefings)
├── shared/               curated rules — the project brain
│   ├── feedback/         preferences and corrections
│   ├── user/             user-profile facts
│   ├── reference/        pointers to external systems
│   ├── project/          per-repo project context
│   ├── _inbox/           staged for your review: backfilled pages, proposed rewrites
│   └── _archive/         originals kept by reclassify; never read
└── .mnemo/               internal state (indices, telemetry)
```

Edit `HOME.md`'s notes section freely — mnemo only manages the dashboard block
at the top.

## Backfill

A brand-new vault knows nothing, so mnemo has nothing to inject for the first
few weeks — which is when most people give up on it. Backfill fixes that from
history you already have: Claude Code stores every session it has ever run at
`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`, and mnemo can read them.

### What happens on your first session

Nothing is swept unless you ask. On the first session in a repo that has
harvestable transcripts, mnemo prints one line: how many past sessions are
there and, at most, what reading them would cost in Haiku calls. That line is
the invitation to run `mnemo backfill` yourself (see [Running it
yourself](#running-it-yourself) below); it is shown once per repo and never
spends a call.

If you would rather have the sweep run on its own, opt in before the first
session:

```json
{ "backfill": { "autoOnFirstSession": true } }
```

With that set, the first session after install spawns a background sweep of
**the repo you're sitting in** — newest sessions first, capped at
`backfill.installCap` (20). Each session costs one call to the `claude` CLI you
already have, using `extraction.model` (Haiku by default) and retried once if
it times out. Sessions that touched fewer than `backfill.minFileMutations`
files are skipped without a call.

What comes back is written into `bots/<repo>/memory/` — the same place live
capture writes — and the next extraction turns it into rules, staged for your
review as described next. While those staged rules are the only thing in the
vault, every session start says so — one line naming how many are waiting in
`shared/_inbox/` and what to do with them — and stops saying so the moment you
move or delete them, or any rule goes live.

That is the whole automatic budget: it runs **once per vault**, never for your
other projects, and never again. Everything beyond it is something you type.

The calls go through your existing `claude` CLI on whatever authentication it
already uses. On a Pro/Max subscription that means no per-token charge; on
API-key auth it is billed like any other Haiku call.

### Backfilled pages are always staged for review

This is a guarantee, not a default. A backfilled page is the model's
*reconstruction* of a session that ended weeks ago — not something mnemo
watched happen. So every page it produces is stamped `origin: backfill`, and
every rule extracted from one lands in `shared/_inbox/<type>/`. **Nothing of
backfill origin is ever auto-promoted into `shared/`**, whatever its source
count, and the stamp survives across extraction runs.

Review them the way you'd review a pull request:

```bash
mnemo inbox                      # what's staged for this project, oldest first
mnemo inbox --show <key>         # read one before deciding
mnemo inbox --promote <key>      # keep it: into shared/<type>/, live at once
mnemo inbox --drop <key>         # throw it away: archived, out of the queue
```

Only a promoted page takes part in injection. You do not have to remember to
run this: when pages are staged for the project you just opened, mnemo names
the oldest of them at session start, with the command beside each one.

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

**Promoting is a move, and `mnemo inbox --promote <key>` is the move.** It
puts the page in `shared/<type>/`, rebuilds the indexes so the rule is live
now rather than at your next session, and tells the extractor it was promoted
— which a hand `mv` does not, leaving the next extraction to stage an update
proposal for a source that never changed. The plain `mv` still works. Leave
the `needs-review` tag alone if you like; **location** is what decides whether
a rule is a draft, not the tag. (Before v0.18 the tag also hid the page, which quietly made promotion
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
the most relevant rule inline — plus the runner-up when it is nearly as good —
only when it clears an absolute score floor and a term-overlap check. (A
relative-gap gate that demanded one clear winner is still configurable, but
off by default: a near-tie means two rules apply, #332.) The floor scales
with the vault's size, so the first rule you learn can fire on the next
prompt instead of waiting for the vault to grow. Fail-open: any error means
the prompt passes through untouched.

### Enforcement and enrichment

At `PreToolUse`, a `Bash` command matching a rule you marked as a guardrail is
blocked outright. When a `Read`, `Edit` or `Write` touches a file a rule's
`activates_on.path_globs` names — a repo-relative path such as
`prisma/schema.prisma` or `**/screens/HomeScreen.tsx` — that rule's body is
surfaced as context, once per rule per session. Globs that name an area
(`src/app/**`, `**/*.ts`) do not fire here: an area is what the prompt-time
reflex is for, and a note on every `.ts` file would be noise.

## Autopilot

Between sessions, mnemo keeps its own brain in shape — rebuilding indices,
sweeping dead rules, and calibrating how often rules get injected against your
own hit/miss log. Nothing runs on the prompt path, and all of it is local.

It opens GitHub issues or pull requests only if you set
`autopilot.network.enabled` to `true` — see
[configuration.md](configuration.md#autopilot--what-may-leave-the-machine).

Control it with `mnemo autopilot {status,pause,off,on}`.

## Taking your rules with you

```bash
mnemo export                      # → .claude/rules/mnemo.md
mnemo export --target claude-md   # managed block inside CLAUDE.md instead
mnemo export --host cursor        # → .cursor/rules/mnemo.mdc
mnemo export --host codex         # managed block inside AGENTS.md
mnemo export --dry-run            # print the block, touch nothing
mnemo export --limit 10           # keep the ten most-sourced rules
mnemo export --full               # whole rule bodies, not just the lead sentence
mnemo export --remove             # delete the file / strip the block
```

What goes in: `feedback` and `user` rules attributed to this repo, plus
universal ones, most-sourced first. `reference` pages stay out unless you
pass `--all-types`. Each rule is written as its lead sentence — the line
before the `**Why:**` / `**How to apply:**` sections — plus the sentence you
said as `> you said: "…"`; the block opens with a note pointing the tool at
the `read_mnemo_rule` MCP tool for the rest. That keeps a real project's
block under 3,000 tokens where the whole bodies ran to 6,000–7,000, and the
block rides on every prompt. Pass `--full` for the whole bodies. User-profile
pages (`type: user`) are included too and can carry names or emails — export
tells you when one is in the block; pass `--types feedback` to leave them
out. When the block would load more than about 4,000 tokens on every prompt,
export says so on stderr and suggests a `--limit` (or dropping `--full`).

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
mnemo why       # the reflex's last decisions: what fired, what stayed silent, and why
mnemo replay    # replay your transcripts: how often an earlier session's rule would have fired
mnemo doctor    # full diagnostic: statusLine drift, stale locks, recent failures
mnemo extract   # manual extraction (also rebuilds the HOME dashboard)
mnemo fix       # reset the extraction circuit breaker after repeated failures
```

`mnemo status` and `mnemo doctor` also have slash forms under the plugin
(`/mnemo:status`, `/mnemo:doctor`); `open`, `fix` and `extract` are CLI-only.

`mnemo why` (or `/mnemo:why`) prints the reflex's last decisions with their
arithmetic — what was scored, what won, and what a silent one needed and did
not reach:

```
09:41:24  injected  mnemo-1.0-roadmap (6.84)
          ahead of  recall-degrades-with-topic-size (3.45)

09:30:07  silent    recall-degrades-with-topic-size led at 1.21, under the 2.00 floor
                    — nothing scored well enough to be worth saying
                    recall-degrades-with-topic-size  1.21
                    mnemo-1.0-roadmap                0.85
```

Detailed errors land in `~/mnemo/.errors.log` under `where=extract.bg.*`. If
`mnemo doctor` warns about `statusLine` drift, you hand-edited
`~/.claude/settings.json` after `mnemo init` — re-run it to reconcile.

Manual extraction flags:

```bash
mnemo extract --dry-run   # show what would run without calling the LLM
mnemo extract --force     # reprocess entries previously dismissed or promoted
```

## The dispatch loop

Once the vault holds something, the other half of mnemo is spending it: a
session fans out into background children, a queue says which child needs
you, a delivery pushes each finished one and opens its PR, and a landing
merges a contract's pieces in the order their signatures require. The
children run under the same hooks the parent does, and a worktree resolves to
the repo it was cut from, so a child sees the repo's rules exactly as you do:
what you taught mnemo in the parent is already injected into the children.

Four commands, in order: `dispatch` → `sessions` → `deliver` → `land`. Only
`dispatch` has a slash form. The other three are CLI-only, because the queue
is designed never to reach a session's context
([why](#it-never-reaches-claude)).

### Issue form

```bash
mnemo dispatch 197 198          # or /mnemo:dispatch 197 198, inside a session
mnemo dispatch 197 --dry-run    # the worktree and branch per child; nothing spawned
```

Each issue gets one background `claude` session in a fresh git worktree at
`<repo>-wt-<issue>` beside the repo, on branch `fix/issue-<issue>`:

```
#197  a41c8e2f  /Users/you/github/app-wt-197
#198  13b6f4f3  /Users/you/github/app-wt-198

  queue:  mnemo sessions
  attach: claude attach a41c8e2f
```

Run from inside a session (`/mnemo:dispatch`, or the model calling `mnemo
dispatch` itself), the output ends with a note addressed to that session: the
children are detached, nothing tells it when they block or finish, so it
reports the ids and stops instead of promising to watch them. The queue is
yours.

That note corrects one seam. The rest of the loop — which verbs a session may
run itself, which are yours, what a socket message from another session is
worth, and that the SessionStart briefing is the last session and not a task —
is in the `mnemo-loop` skill, which any session loads on demand ("how does
mnemo's loop work for me?"). It is a skill and not injected context precisely
so it costs nothing on the sessions that never dispatch anything.

The child's opening prompt is the issue body, the worktree, and three scope
limits: no merge or push without asking, no files outside what the issue
needs, the full suite before claiming done. It carries no preferred solution,
deliberately — a prompt that prescribes an approach can override a correct
refusal, and the one time a child here was handed one it refused and was right
(#187). Write the issue, not the instructions.

A worktree that already exists is refused rather than reused, because it may
hold another session's uncommitted work; anything dispatch created is removed
if a later step fails, so a bad issue number never strands the others. The
queue labels each child by its issue, recovered from the worktree path.

### Contract form

A feature is not issues yet, and nobody files four issues to build one. Ask
any session to "decompose this for dispatch": the `decomposing-for-dispatch`
skill (shipped by the plugin and by `mnemo init` alike) reads the conversation
and writes a **contract** to `docs/superpowers/contracts/<date>-<feature>.md` —
the pieces, the files each may change, the literal signatures each `exposes`,
and the ones it `consumes` from other pieces. Then it stops; dispatching is
your decision.

```bash
mnemo dispatch --contract --example                        # the format, commented and parseable
mnemo dispatch --contract docs/superpowers/contracts/f.md --dry-run
mnemo dispatch --contract docs/superpowers/contracts/f.md
```

```
label-column  feat/queue-followups/label-column  /Users/you/github/app-wt-c-label-column
sessions-docs  feat/queue-followups/sessions-docs  /Users/you/github/app-wt-c-sessions-docs
```

One child per piece, at `<repo>-wt-c-<slug>` on `feat/<feature>/<slug>`. A
piece's prompt is its boundary — the files it may touch, the signatures it
must deliver, the signatures it may assume exist — and nothing about how. A
`consumes` may name something that does not exist yet, because the piece
delivering it is being written at the same moment: the child writes against
the signature and the merge resolves it. That is why `exposes` must be a
literal signature, and why dispatch is a flat fan-out and not a scheduler.

`verdict: sequential` is a real answer — the work does not divide — and it is
refused rather than dispatched. So is a contract naming an unknown piece, a
duplicate or unaddressable slug, a piece with no files, or a piece consuming
from itself, all before any worktree exists.

### Choosing the model

A child runs on whatever `~/.claude/settings.json` resolves to unless you say
otherwise. That is one price for every kind of work: the child that designs a
decomposition and the child that sweeps an encoding fix across 44 call sites
cost the same.

```bash
mnemo dispatch 255 246 --model haiku     # both children, one model
mnemo dispatch 244 --model opus          # this one needs the judgement
```

The flag takes what `claude --model` takes — an alias (`haiku`, `sonnet`,
`opus`) or a full id. mnemo does not keep a list of valid models: Claude Code
owns that vocabulary, and an unknown id fails in the child's own startup where
the error names the real one.

A contract piece may carry its own, which wins over the flag — the contract
was written and reviewed knowing what each piece is, and a blanket from the
command line should not silently re-price it:

```markdown
## storage

- **files:** src/app/storage.py, tests/unit/test_storage.py
- **exposes:** `load(key) -> Record | None`
- **model:** haiku
```

`--dry-run` prints what each child would get, per piece, before anything is
spent. Afterwards `mnemo session <short_id>` names the model a child is on,
and `mnemo sessions` ends with one line naming the models in play across the
whole queue — a footer rather than a column, because the same id repeated on
every row would cost 20 characters of width to say nothing.

### Choosing the effort

`--effort` sets how hard every child reasons, independently of the model:

```bash
mnemo dispatch 255 --effort low             # mechanical sweep
mnemo dispatch 244 --model opus --effort max
```

Unlike models, the levels are a closed list — `low`, `medium`, `high`,
`xhigh`, `max` — and mnemo checks it before anything is spawned. That is on
purpose: Claude Code does not fail on an unknown level, it warns and runs the
default, so a typo would quietly give you a child on the effort you did not
ask for. Omit the flag and no `--effort` is sent at all; the child runs at its
default.

A contract piece may name its own with `- **effort:** high`, which wins over
the flag exactly as `model:` does. `mnemo session <short_id>` shows
`esforço <level>` for a child that was given one, `mnemo sessions` adds an
`esforço:` footer once any child in the queue has one, and
`mnemo sessions --json` carries an `effort` field. `null` there means the
child runs at the default, not that the value could not be read: Claude Code
records an effort only when one was passed.

mnemo does not choose the effort for you. Whether effort changes what a child
delivers has not been measured yet, and a wrong heuristic here costs money on
every child.

### Saying up front what a child may publish

Ungranted, a child that finishes stops to ask "may I push / open a PR?", and
an answer sent later through `SendMessage` or mnemo-desktop cannot approve it:
Claude Code frames every such message as another session's, never as yours.
The one message a child reads as yours is the prompt `mnemo dispatch` wrote,
so that is where the permission goes:

```bash
mnemo dispatch 255 --may push      # push its branch once the suite passes
mnemo dispatch 246 --may pr        # push, and open the pull request itself
mnemo dispatch 244 --may none      # publish nothing; `mnemo deliver` does it
```

`pr` implies `push`. The child is told it may do exactly that once its full
suite passes, on its own branch, never force-pushing — and not to ask again.
`merge` is refused: merging stays with `mnemo land` and review.

`--may` defaults to `pr`. Use `--may none` for work that should not become a
branch at all — an exploratory spike, or anything you want to read before it
is published. That restores the prompt dispatch always sent, word for word,
and `mnemo deliver` publishes instead. The default is the command line's
alone: every library entry point still grants nothing when it is told nothing.

A contract piece may carry its own `- **may:** pr`, which wins over the flag
the way `model:` does; `- **may:** none` withholds from that piece what the
flag gave the rest. `--dry-run` prints each child's grant, `mnemo sessions`
ends with a `publicam sem perguntar:` line naming the running children that
hold one, and `mnemo sessions --json` carries it on every row as `may`
(`["push", "pr"]`, or `[]`).

### How a child ends

A child ends itself: it writes its closing report, asks `git` whether there
is anything to publish — a clean tree on its own branch, at least one commit
ahead of the base — publishes it when there is, and then stops. The order is
the point. The report reaches the transcript before the stop, and the stop
matters beyond tidiness: only a stopped session fires `SessionEnd`, and that
is where the child's briefing is written. A child left running holds a few
hundred megabytes and leaves no memory behind.

The grant decides only how it publishes. With `--may pr` it pushes and opens
the pull request; with `--may push` it pushes and leaves the PR to you; with
`--may none` it says in its report that there is something to deliver and
leaves it for `mnemo deliver`. The report and the stop do not depend on the
grant — a child that refused the task has nothing to publish and its
reasoning is the most valuable briefing in the system, because no diff
carries it.

If a child finishes without stopping itself, `mnemo deliver --stop-done`
stops every finished child in this repo's dispatch worktrees, whether or not
it delivered anything:

```bash
mnemo deliver --stop-done
```

It publishes nothing and approves nothing, so it is not an `--all` in
disguise; naming ids or `--review` alongside it is refused, because each of
those is a different command.

### What a child already knows about your repo

A child is handed the issue, a worktree and its scope limits — never an
approach. But there is a third thing it needs and the issue never says: how
work is *run* here. Run the suite how? Where do changelog entries go? That is
not an approach, it is a boundary, and a child that has to work it out pays
for it every time.

mnemo adds no file format for this, because Claude Code already has one.
**Put it in the repo's `CLAUDE.md`.** A child opens in a worktree of your
repo, so it gets that file exactly as you do, lean profile and all.

The difference that makes is measurable, and
`tools/measure_child_procedures.py` counts it over every dispatch child on
disk (182 of them on 2026-09-19, across four real repos — plus a
rehearsal fixture and a nested tree the table leaves out):

| repo | channel | what happened |
| --- | --- | --- |
| clubinho | `CLAUDE.md`, 20/20 children | the flag it states was on **24 of 24** suite runs |
| clubinho | nothing states the heap size | **13 of 14** children ran out of it |
| mnemo | no `CLAUDE.md` at the time | 12 children rediscovered `PYTHONPATH=src` mid-run, **2 never did** |
| mnemo-desktop | no `CLAUDE.md`, no memory | **33 of 35** children missed the build's environment; 10 hit `Text file busy` |

The first two rows are the same repo, the same children, often the *same
command*: `npm test -- --runInBand` carries the flag `CLAUDE.md` names and
omits the one it does not. Only the channel differs.

The failure is quiet, which is why it is worth the file. A bare `pytest` in a
mnemo worktree imports the *main* checkout's `src` and prints `83 passed` —
a green suite that proves nothing about the change under test. Nothing in the
transcript looks wrong, so no child goes looking for a skill or a rule to
correct it.

Keep it to boundaries and keep it short. Every session that opens in the repo
pays for every line, and anything that tells a child *how to solve* the issue
re-creates the failure `mnemo dispatch` exists to avoid: the #187 child was
handed an approach, refused it, and was right.

### Watching, and answering

`mnemo sessions` is the queue; the [next section](#watching-background-sessions)
reads it line by line. The short version: blocked children first, then the
running ones with what each is doing, then the finished ones with their PRs,
then the abandoned. `claude attach <short_id>` gets you inside a blocked one.

What you say to a blocked child is a correction, and mnemo learns from it the
way it learns from any other. When the session-end sweep finds a child that
was blocked and then answered, it records a marker; the `SessionEnd` hook
runs the consumer in the background, and you can run it by hand to see the
result:

```bash
mnemo sessions --consume-unblocks
```

```
consumed: 1 unblocked session(s)
learned: hint-owner-is-the-queue — The attach hint belongs to the queue, not the session
```

It ignores `--all` and the current directory, since an unblocked session is
worth learning from wherever it ran. A marker whose transcript cannot be
resolved is reported as `skipped:`; a transient failure is `deferred:` and
retried on the next pass.

### Delivering

```bash
mnemo deliver --review        # every dispatch worktree, and whether it is deliverable
mnemo deliver 7c1e            # by short id, issue number or piece slug
mnemo deliver 205 206         # several — each approved by being named
mnemo deliver --stop-done     # stop every finished child, delivered or not
```

```
READY (1)
  #205  fix/issue-205
      3 commits ahead of master, 4 files changed, 118 insertions(+), 9 deletions(-)

NOT READY (2)
  #206  fix/issue-206
      uncommitted changes — commit them or discard them first
  #207  fix/issue-207
      no commits ahead of master — nothing to deliver

FINISHED, NOT STOPPED (1)
  #207  9f2ab410
      no briefing until stopped: mnemo deliver --stop-done

  deliver: mnemo deliver 205
```

The third group is the one children usually land in when they finish with
nothing to publish: finished, still running, and — until they are stopped —
with no briefing written. It is printed across both of the groups above it,
because the child with nothing to deliver is the one most worth stopping.

`deliver` pushes the child's branch and opens its pull request with
`gh pr create --fill` — the commits are the description — and appends
`Closes #N` for an issue child so the merge closes the issue. Its one
invariant is that naming an id **is** the approval: there is no `--all`, and
each refusal (not ahead of `master`, a dirty tree, a PR that already exists)
is printed against the name you typed. A PR that is already open — one a
child granted `--may pr` opened itself — counts as delivered: it is printed,
given the `Closes #N` trailer if it lacks one, and the finished child is
stopped. A delivered child prints its PR:

```
#205: https://github.com/you/app/pull/199
```

### Landing a contract

A contract's pieces were written against each other's signatures, and the
merge is where those assumptions become real. `mnemo land <contract>` is
read-only: every piece in **landing order** — a stable topological sort by
`consumes`, owners before consumers, ties in contract order — with its PR and
state, the ref that carries it, and whether each `exposes` is actually defined
in the piece's files on that ref: `✓` present, `✗` missing, `?` for a
signature that names no identifier, such as a CLI shape.

```bash
mnemo land docs/superpowers/contracts/2026-09-13-dispatch-last-metre.md
```

```
contract dispatch-last-metre (3 pieces, in landing order)
  1. delivery            feat/dispatch-last-metre/delivery  [origin/feat/dispatch-last-metre/delivery]
       PR: https://github.com/xyrlan/mnemo/pull/219 (MERGED)
       exposes  ✓ `ready(worktree, *, repo_root) -> Readiness`
       exposes  ✓ `pr_for(branch, *, repo_root) -> str | None`
  2. contract-discovery  feat/dispatch-last-metre/contract-discovery  [origin/feat/dispatch-last-metre/contract-discovery]
       PR: https://github.com/xyrlan/mnemo/pull/220 (MERGED)
  3. watch-modes         feat/dispatch-last-metre/watch-modes  [origin/feat/dispatch-last-metre/watch-modes]
       PR: https://github.com/xyrlan/mnemo/pull/221 (MERGED)

  all landed — nothing to do
```

Presence is a name check, not a string check: `consumes` and `exposes` are
hand-written and differ cosmetically, and a name survives a renamed argument
while still catching a function that was never written. A merged piece whose
branch is gone is checked on `master`. A cycle has no landing order and is
refused by name.

A piece that would otherwise land is checked against its pull request's CI,
and a failing check refuses it — `✗ CI red on <check>` against that piece,
and the piece named under `CANNOT LAND`. The verdict is read check by check
(`gh pr checks --json name,bucket`), not from the run's conclusion or the
PR's rollup: a repository may mark a job non-blocking, and such a job fails
while both aggregates still report success. Pending is not failure, and an
unreadable answer — no `gh`, no checks on the PR — refuses nothing: the gate
stands on evidence, never on its absence.

```bash
mnemo land <contract> --merge                       # rehearse, then gh pr merge in order
mnemo land <contract> --merge --suite "npm test"    # default: python -m pytest -q
mnemo land <contract> --merge --method merge        # squash (default), merge, rebase
```

`--merge` runs in two phases, and the irreversible one starts only after the
reversible one passed in full. First a **rehearsal** in a throwaway worktree
under the system temp dir: each open piece is merged in order, its `exposes`
are checked in the merged tree, every `consumes` is checked against the
owner's files there — the first moment the consumed signature either exists
or does not — and the suite runs. The first conflict, missing name or red
suite stops it with the piece and the step named, and nothing anywhere has
changed. Only then are the PRs merged with `gh pr merge`, in the same order,
stopping at the first refusal; a rerun skips whatever `gh` reports as merged.

A verb of its own rather than a mode of `deliver`, because `deliver`'s
invariant is per-child approval by name and a landing is inherently every
piece of the contract — that approval already happened when each piece was
delivered. Not a scheduler: dispatch stays a flat fan-out, and `land` runs
after it has landed.

## Watching background sessions

Claude Code can run sessions in the background, and once there are six of them
the bottleneck stops being the machine and becomes you: every one of them may
or may not be waiting for an answer, and the only way to find out used to be
attaching to each in turn.

Most of them get there through `mnemo dispatch`; the [dispatch loop](#the-dispatch-loop)
above is the whole sequence. This section is the queue itself.

```bash
mnemo sessions
```

```
WAITING ON YOU (1)
  a41c8e2f  #196 queue liveness                   4m  Which of the two should own the hint?

WORKING (2)
  13b6f4f3  #207 sessions docs                 Read docs/getting-started.md (+31)    9k
  3a14bdd9  #206 label column                  Bash pytest -q (+12) ↻                7k

DONE (1)
  7c1e9a04  #205 unblock consumer              #199                                 22k

  attach: claude attach a41c8e2f
```

Four buckets, and the order is the point. **WAITING ON YOU** is a session blocked
on a human, and it comes first because it is the only bucket where nothing
happens until you act. **WORKING** is running, **DONE** has finished
(the column shows the PRs it opened), and **ABANDONED** asked for a human and
then died before getting one — listed rather than hidden, because whether a
dead session still matters is your call, not mnemo's. A session only lands
there when its process is *provably* gone; when mnemo cannot tell, the session
stays in the waiting bucket, on the grounds that a question wrongly written off
is worse than one listed twice.

Within the waiting bucket, newest first. That looks backwards until you know
what the timestamp means: a blocked session's clock stops when its process
stops writing, so the *stalest* entry is the likeliest corpse rather than the
most urgent question. The freshest one is the one actually waiting on you, so
it gets the top line and the `attach:` hint. The abandoned bucket sorts the
other way — oldest first — because there the stalest really is the one to
clear, and it gets its own `remove:` hint.

### What the activity column tells you

A session sitting at "working" for twenty minutes reads identically whether it
is making progress, stuck on one thing, or going in circles — and those call
for three different responses. So the working bucket shows the last tool it
used, what it used it on, `(+N)` for how many tool uses came before it in the
window just read, and `↻` when the previous tool use had the same tool and the
same target:

- **rising count, changing target** — progress, leave it alone
- **frozen count** — stalled
- **rising count, unchanged target, `↻`** — a loop, which without the mark
  reads exactly like progress

The column is budgeted, and when it runs out of room it cuts the *target*
rather than the `(+N)` and `↻` that carry the signal.

The label beside it is budgeted the same way, and for the same reason. Where it
has to cut, it keeps the head — the issue number, or the contract piece — and
trims the inferred title after it, because the identifier is what you are
tracking across a dispatch and the title is the expendable half.

Only the working bucket gets it. A blocked session's claim on you is the
question it is asking, and burying that under a tool name would invert the
ordering the queue exists to provide.

### Looking closer at one session

When a queue line looks wrong, the next question is *wrong how*:

```bash
mnemo session 3a14
mnemo session 3a14 --limit 40    # default is 15
```

That prints the session's recent actions oldest-first with timestamps, so a
loop becomes visible as a shape rather than a single mark. A unique prefix of
the short id is enough; an ambiguous prefix refuses rather than guessing, on
the grounds that showing you the wrong session's actions is worse than asking
for another character.

The `↻` here is deliberately broader than the queue's: the queue marks a
back-to-back repeat, while this view marks any tool and target that comes back
anywhere in the window you are looking at. A grep run four times with other
work in between is a loop too, and it is invisible to a consecutive-repeat
test.

There is no `--follow`. You look, you decide, and `claude attach <short_id>`
is how you get inside — that is Claude Code's job, not mnemo's.

### What a child spent before its first edit

A dispatched child starts out knowing the vault, and still pays to find its way
around a repo the parent already knew. The DONE rows end with that price, and
a line under the table sums it across the finished children:

```
DONE (2)
  934f353f  #247 dispatch orphan namespace     #262                  64k  32u/+107k
  aafd88e7  c-format format share-rules piece  #259                  33k  9u/+41k

  before first edit (u/+tokens): 41 uses, +148k across 2 done sessions
```

`32u/+107k` is 32 tool uses before the child first changed its working tree,
and 107k tokens of context added to the window in that time — on top of the
first turn's baseline (system prompt, skills, hooks, the task, whatever reflex
injected), which `mnemo session <short_id>` prints alongside how many reflex
rules the opening prompt carried. `≥` in front means the child never edited,
so the count is a floor.

"Changed its tree" is not "called `Edit`": a child that writes with
`cat >> tests/…` or a Python heredoc has edited, and a `Write` to a memory note
outside the repo has not. The Bash half reads command text, so it can be wrong;
`tools/measure_exploration.py --list` prints the edit it picked for every
dispatch transcript on disk, and splits the numbers by how many reflex rules
each child got — correlation over what happened, not a controlled test.

### What fills a child's context

A working or finished row ends with `Bash 46%` when one tool's results fill at
least 40% of that session's context. MCP tools count per server
(`mcp:claude-in-chrome`). Every other row prints nothing extra. Dispatched
children lean on Bash, with a median around 31%, so a lower bar would put a
suffix on most rows. `mnemo sessions --json` carries the whole breakdown as
`context_breakdown: {tool: tokens}`.

These numbers will not match `/context`'s "Read results using …" line, which
is not wrong in the same way. `/context` sizes each block at its JSON length / 4
and shows it as a share of the whole window. That counts a screenshot's base64
as text: one session read "Read 207%" while its whole context was 818k. mnemo
counts result text at 2.25 characters per token, measured from how much the
context really grew. An image counts at its pixels / 750, and each turn's total
is capped at that turn's real growth. A tool's call (the content a `Write`
sends) is output, not a result, and is not counted.

### Flags

```bash
mnemo sessions --all               # every repo, not just the one you're in
mnemo sessions --stale             # also list finished children whose worktree is gone
mnemo sessions --watch             # redraw every 2s until Ctrl-C
mnemo sessions --json              # machine-readable; --watch is ignored
mnemo sessions --consume-unblocks  # learn from sessions answered while blocked
```

`--watch` clears the screen only on a terminal; redirected to a file it appends
each redraw instead of writing escape codes into your log.

A finished child whose worktree was removed (its PR merged, the dispatcher
cleaned up) is hidden from the queue and from `--json`: Claude Code keeps its
job record until `claude rm`, so without this they pile up under DONE for
days. A footer counts what was hidden; `--stale` lists them again, and
`mnemo doctor` prints the `claude rm` line that clears them. A blocked session
is never hidden, whatever happened to its tree.

#### Writing a script against `--json`

Branch on the derived booleans, never on the raw `state` string:

```bash
mnemo sessions --all --json | jq -r '.[] | select(.is_waiting) | .short_id'
```

| Field | Means |
|---|---|
| `is_waiting` | **Blocked and its process is still alive** — a real claim on you. This is the one you want. |
| `is_blocked` | Blocked on disk, alive or not. |
| `is_abandoned` | Blocked, but the process is provably gone. Nothing to answer. |
| `is_done` | Finished, by process phase (`done` *or* `stopped`). |
| `is_stale` | Finished, and its `cwd` no longer exists. Only ever `true` under `--stale`. |

The raw fields are still there, and the two enumerations are worth stating
because guessing at them is what breaks scripts:

- `state` — the process phase: `working`, `blocked`, `done`, `stopped`
- `tempo` — whether a human is needed: `active`, `idle`, `blocked`

They **overlap**: `state` has its own `blocked`, so the axes are not
orthogonal and a filter written against `state` can look right and be wrong.
`is_waiting` in particular is not a one-liner — it is an interaction between
`tempo` and liveness — so read it rather than rebuilding it.

`parent_session` is the full session id of whoever ran `mnemo dispatch` for
this child, or `null` when it was run from a plain terminal or the child was
not dispatched at all. It still answers after the child's worktree has been
removed, so summing children's `tokens` onto their parent works for finished
children too:

```bash
mnemo sessions --all --json | jq 'map(select(.parent_session)) | group_by(.parent_session)
  | map({parent: .[0].parent_session, tokens: (map(.tokens // 0) | add)})'
```

`--consume-unblocks` is the one that is not about looking. When you answer a
blocked session, that moment is a correction worth learning from, and mnemo
records a marker for it; this redeems the markers and prints what it learned.
The marker comes from the session's transcript, not from catching `tempo`
mid-flip: every `mnemo sessions` and every `SessionEnd` reads each background
session's transcript forward from where the last one stopped, and any answer
it finds there is recorded however long ago it landed. The `SessionEnd` hook
then runs the consumer for you in the background — running it by hand is for
when you want to see the result. It ignores `--all` and the current directory,
because an unblocked session is worth learning from wherever it ran.

### It never reaches Claude

Both commands are human-only by design: no hook and no MCP tool exposes either
one, and nothing about the queue is written into any session's context. The
parent session's context is the scarce resource this whole feature exists to
protect — a queue that spent it to describe itself would be defeating its own
purpose. What Claude Code does surface is the count, in the status line
(`N esperando`), if you installed it.

## Working on mnemo itself

`CLAUDE.md` at the repo root is the short version: how to run the suite from
a worktree, the Python floor CI still builds, where changelog entries go. It
is what a dispatched child reads too — see [what a child already knows about
your repo](#what-a-child-already-knows-about-your-repo).

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
