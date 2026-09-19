# mnemo

> Claude Code forgets your corrections. mnemo doesn't — and the sessions it
> fans out for you start out knowing them.

![The five-minute loop: correct Claude, run mnemo learn, the next prompt already knows](docs/assets/loop.gif)

Monday, in your app repo, you tell Claude: *"never use npm in this repo, always
yarn."* You run `mnemo learn` — or you just end the session and let it happen.
Thursday, a new session, you ask Claude to add a dependency: that rule is
injected before Claude answers. One or two short lines, about 150 tokens, and
only when it clearly applies.

What gets injected is a 300-character preview plus a pointer, under a
`reflex context:` header: `• [[use-yarn-not-npm]]: Use yarn, never npm …`.
Claude can call `read_mnemo_rule` over MCP for the full text; `/mnemo:doctor`
tells you if that server is not connected. A rule that recurs in two different
repos is promoted to universal and follows you everywhere.

## The whole loop

Friday there are three issues that could be built at once:

```bash
mnemo dispatch 197 198 199    # or /mnemo:dispatch 197 198 199, from inside a session
```

Each gets a background Claude Code session in its own worktree and branch,
handed the issue body and scope limits — never a preferred solution, so a
child can refuse a wrong one. The children run under the same hooks you do,
and a worktree resolves to the repo it was cut from, so every rule the vault
holds for that repo — *use yarn, never npm* included — is injected into them
exactly as it is into you.

Not every issue wants a patch. Some want an answer first:

```bash
mnemo dispatch 361 --read-only
```

That child investigates and posts its finding as a comment on the issue
instead of building. Its file-editing tools are closed at spawn — Edit, Write
and NotebookEdit, its own subagents included — so habit cannot turn an
investigation into a branch. It can still write through the shell if it sets
out to, which is why it keeps a worktree of its own: the restriction is there
to prevent drift, not to contain a child that means to build. It publishes
nothing, so `--may` is refused alongside it.

`mnemo sessions` is the queue. Blocked on a human comes first, because nothing
happens there until you act:

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

Attach, answer the question, detach. That answer is a correction, and mnemo
treats it as one: the `SessionEnd` hook runs `mnemo sessions
--consume-unblocks`, which learns from what you told the blocked child the way
`mnemo learn` does, so the next child to hit the same fork already has your
answer. A child ends itself: it reports, opens its own pull request when git
says there is something to publish, and stops — and only a stopped child fires
`SessionEnd`, which is where its briefing is written. Dispatch it with
`--may none` and it publishes nothing; then it is delivered by name —
`mnemo deliver 7c1e` pushes its branch and opens the pull request,
`Closes #205` included — and naming it is the approval; no flag approves them
all.

When the unit of work is a feature rather than issues, ask any session to
"decompose this for dispatch": the `decomposing-for-dispatch` skill writes a
contract — the pieces, the files each may touch, the signatures each `exposes`
and `consumes` — and `mnemo dispatch --contract <path>` spawns one child per
piece. `mnemo land <contract>` is the last metre: every piece in landing
order, owners before consumers, and whether each promised signature actually
exists on its branch:

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

`--merge` rehearses the sequence in a throwaway worktree — merge, check the
signatures, run the suite, next piece — and only then runs `gh pr merge` in
the same order. Neither the queue nor dispatch ever reaches Claude on its own:
no hook and no MCP tool exposes them, because a parent's context is the thing
they exist to save. Both forms, every flag: [docs/getting-started.md](docs/getting-started.md#the-dispatch-loop).

## What is measured

On the maintainer's vault, 2026-09-09:

```
reflex: injected on 88 of 1131 prompts (7.8%)
```

`reflex` is the per-prompt recall. When Claude asks for rules by topic
instead, query-aware ranking lifted the share of cases with the needed rule
in the top five from 16% to 31% on topics with more than 20 rules (530
evaluations).

Your own numbers, with a baseline: `mnemo replay` runs every prompt you typed
through the hook's own decision and sorts every rule that would have fired by
*when the vault learned it*. The maintainer's vault, 2026-09-14:

```
prompts replayed          2107   (219 sessions, 2026-08-03 → 2026-09-14)
rules in the vault        1830   (78 cite a correction you typed: 18 verified by the evidence gate today, 60 label only)

  reflex would have fired               230   prompts   10.9%
  ├─ rule from an EARLIER session      116   prompts   5.5%  (95% CI 4.6–6.6%)   ← the vault's contribution
  │    citing your own words              0   prompts   0.0%  (95% CI 0.0–0.2%)   verified by the evidence gate today
  │    label only, gate can't check       3   prompts   a `verified` from mnemo reclassify; its briefing has no Corrections to check against
  ├─ rule from this SAME session         24   prompts   hindsight — the vault could not have helped
  └─ rule not learned yet                90   prompts   today's vault fires, but the rule postdates the prompt

not measured: whether an injected rule changed the answer; tokens, session length, or time saved; what CLAUDE.md or auto-memory would have covered instead.
```

The earlier-session line is the only one the vault can take credit for; a
naive replay would claim all three. A rate only once there are enough prompts.
"Your own words" is the strongest claim on the page, so it is held to the
evidence gate as it stands today: the quote must sit in the `## Corrections`
of a briefing the rule was built from. A `confidence: verified` the gate
cannot re-check — the 2026-09 `mnemo reclassify` labels cite briefings
written before that section existed — is printed on its own line and never
quoted as the number.

## How it compares

**CLAUDE.md** — you write it and prune it by hand, and it is loaded whole,
every session, whether or not any of it is relevant to what you're doing.

**Claude Code auto memory** — Claude writes it for you, but only the head of
the index loads every session (200 lines as of 2026-09) and the rest is
silently dropped. Nothing is ranked against the prompt in front of you.

**`claude --bg` by hand** — Claude Code runs a session in the background for
you, and then you have six of them: in whatever directory each was started,
with no view of which one is waiting for a human, and nothing that pushes a
finished one or merges them in the order their dependencies require.

**mnemo** — learns from your corrections and keeps a verifiable quote of what
you actually said. Rules it can't verify stay staged for your review instead of
entering the vault. Then it injects at most two rules per prompt — usually
one — chosen by BM25F against the prompt text. No database, no daemon, no
per-prompt LLM call. Dispatch gives every child a worktree and a branch, the
queue puts the blocked ones first, each child inherits the vault, `deliver` and
`land` finish the job, and what you answer at a blocked child goes back in.

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

Both need Python 3.8+ (uv brings its own). Details, including what `mnemo
init` writes, how to undo it, and what to do if the plugin lands on top of an
older install, in [docs/getting-started.md](docs/getting-started.md).

</details>

## Check it worked

Correct Claude in your own words, run `mnemo learn`, read the output, type
your next prompt on that subject ([the five minutes](docs/getting-started.md#five-minutes)).
Step 3 is the whole feature:

```
read: ~/.claude/projects/-Users-you-github-app/3f2a….jsonl
briefing: bots/app/briefings/sessions/3f2a….md (1 correction(s))
learned: use-yarn-not-npm — Use yarn, never npm (evidence: "never use npm in this repo, always yarn")
next prompt about this will surface it — check with `mnemo why`
```

The `evidence:` quote is your own sentence, carried back to you. A rule with a
quote is a rule mnemo can prove you asked for.

Per-prompt recall is silent by design — it injects a rule only when one
clearly beats the rest — and `/mnemo:why` shows each decision with its
arithmetic. Silence with a reason is the difference between "my vault has
nothing useful" and "my thresholds are a little too tight".

## Day one

A fresh vault has nothing to inject, and mnemo does not go rummaging through
your history uninvited. Your first session in a repo with harvestable
transcripts prints one line — how many, and roughly what reading them costs —
and nothing runs until you say so:

```bash
mnemo backfill --dry-run    # exactly what it would read, and what that costs
mnemo backfill              # this repo
mnemo backfill --all        # every project
```

Backfilled pages are reconstructed rather than observed, so every rule from
them lands in `shared/_inbox/` for you to read — **never auto-promoted into
`shared/`**. `mnemo inbox` lists what's waiting and takes the decision in one
command — `--promote <key>` to keep a page, `--drop <key>` to throw it away —
and mnemo names the oldest of them at session start rather than waiting for you
to ask ([details](docs/getting-started.md#backfill)).

## Commands

```
/mnemo:status   vault state + hook health
/mnemo:why      why per-prompt recall fired, or didn't, on your last prompts
/mnemo:doctor   full diagnostic with actionable fixes
/mnemo:learn    learn from this session now
/mnemo:dispatch spawn a background child per issue, or per piece of a contract
/mnemo:help     list commands

mnemo inbox     pages extraction staged for review: promote one, or drop it
mnemo sessions  the queue: blocked first, then what each running child is doing
mnemo deliver   push a finished child's branch and open its PR
mnemo land      a dispatched contract's pieces in landing order; --merge lands them
mnemo replay    your transcripts against your vault: what would have come back, and from when
mnemo stale     live rules citing a file this repo no longer has, and where it moved to
```

Your rules are yours. `mnemo export` writes the ones for the repo you're in to
`.claude/rules/mnemo.md` — a plain file Claude Code loads by itself, one lead
sentence and your own quote per rule (`--full` for whole bodies) — so a
teammate without mnemo gets them too, and leaving mnemo costs you nothing.
`mnemo init --host cursor` (or `codex`) registers the MCP server there and
`mnemo export --host cursor` writes the rules where that tool looks.

Rules can also travel between vaults, through the repo. `mnemo publish`
writes the rules for the repo you're in to `.mnemo-shared/<type>/<slug>.md`
— your quote kept, your vault paths dropped, an opaque vault id as
provenance and never a name or an email — for you to commit like any other
file. A teammate runs `mnemo import`: every rule is staged in their
`shared/_inbox/` for review, never promoted, marked as another
contributor's words rather than their own, and a rerun brings only what
changed. `mnemo status` says when the published tree is behind your vault.

Everything else is a CLI subcommand (`mnemo help --all`): `mnemo open`,
`mnemo fix`, `mnemo backfill`, `mnemo reclassify`, `mnemo autopilot`, and the
rest. The status line (`mnemo · 9 topics · 7↓ today`, plus `N esperando` while
children wait on you) is opt-in, since plugins can't set one: `mnemo statusline --install`.

Uninstall with `/plugin uninstall mnemo`. The vault is always preserved.

## Docs

- [Getting started](docs/getting-started.md) — the deeper tour: every install
  path, [where things live](docs/getting-started.md#where-things-live), the
  [dispatch loop](docs/getting-started.md#the-dispatch-loop) end to end, the [autopilot](docs/getting-started.md#autopilot)
- [Configuration](docs/configuration.md) — every knob in `mnemo.config.json`
- [Troubleshooting](docs/troubleshooting.md) — when something looks wrong
- [Obsidian](docs/obsidian.md) — optional: browse the vault as a graph

## Privacy

100% local. No network calls unless you turn `autopilot.network.enabled` on.
No third-party Python dependencies. Every piece of telemetry
(`.mnemo/*.jsonl`) stays on disk. LLM calls go through the `claude` CLI you
already have — one per session for the briefing, one per ten new files at
extraction time — never on the prompt path. Logs are capped at 1 MB;
briefings accumulate, one file per session. The one other outbound call is
the plugin downloading its binary from GitHub Releases on first use
(checksum-verified). Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
