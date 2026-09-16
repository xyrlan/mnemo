# The child closes its own loop — report, publish, stop

**Date:** 2026-09-16
**Status:** DESIGN — approved in brainstorming, not yet planned or built.
**Issue:** (none yet)

## Problem

A dispatched child does not end. Nothing stops it, so `SessionEnd` never
fires, so no briefing is written — and mnemo is a memory system whose most
used workflow feeds it almost nothing.

Measured on this machine 2026-09-16, crossing
`<vault>/.mnemo/dispatch-parents.jsonl` against the briefings on disk in
`bots/mnemo/briefings/sessions/`:

| | |
|---|---|
| dispatch children ever recorded | **63** |
| children that wrote a briefing | **6** |
| children that did not | **57** |

Of the 24 job directories still on disk, **16 are `done`**, 4 `blocked`,
3 `stopped`. Sixteen children finished their work and are still in the air.

The cause is not configuration. `child_profile.write_profile` copies mnemo's
hooks into `<tree>/.mnemo-child-profile/settings.json`, and a live check
(`mnemo_hooks()` on this machine) returns `SessionStart`, `UserPromptSubmit`,
`PreToolUse` **and `SessionEnd`**. The child is fully equipped to write a
briefing. It is simply never stopped.

`_stop_finished` states the mechanism outright
(`src/mnemo/cli/commands/deliver.py:165-195`): *"Nothing else stops it. The
daemon retires a finished child after 8 h idle, and until then it holds
~300-400 MB (three measured 2026-09-15); worse, only a stopped child fires
`SessionEnd` (#247), so a child left to the daemon never writes its
briefing."*

So the only path that produces a briefing today is the maintainer typing
`mnemo deliver <id>`, and six of 63 children got one.

### Why the obvious fixes are wrong

The gap invites a supervisor, and every shape of supervisor puts the parent
in charge of the child's death:

- **A sweep inside `mnemo sessions`** would turn a read-only report into one
  that writes to children. `sessions` never writes to a child, by design
  (`cli/commands/sessions.py`); the queue is a reader.
- **A new parent-side command** (`mnemo reap`) keeps the separation but
  depends on the maintainer remembering — which is exactly the failure being
  fixed, with a shorter name. It also needs the parent to be told about it,
  so the parent becomes responsible for the child again.
- **A sweep in the parent's `session_end`** cascades: the parent frequently
  ends before its children, and a hook that runs `claude stop` across a fan of
  eighteen is the shape that produced the hook storm of 2026-09-15.

All three contradict the thing that makes dispatch work: the child is
autonomous, and the parent is told not to watch it (`AGENT_NOTE`,
`cli/commands/dispatch.py:33-38`).

## Goal

The child ends itself. Its last act is to report and stop, so `SessionEnd`
fires inside its own worktree and its briefing is written.

Non-goals:

- **No merge autonomy.** `may: merge` stays refused at parse time
  (`grants.parse`); `land` keeps the merge gate and its rehearsal, and gains
  the CI check described below.
- **No parent-side supervisor.** No new sweeping command, no polling, no
  scheduler.
- **No worktree removal by the child.** `SessionEnd` resolves the child's
  `cwd`; removal stays a later, separate decision.

## The rule

One sentence, no exceptions:

> The child ends with **report + stop**, always.
> Before that, if `git` says there is work, it publishes the PR.

The PR is conditional on work existing. The report and the stop are
conditional on nothing.

This makes the refusing child a first-class outcome rather than an exception.
A child that judges the task wrong and declines has no commits, so it
publishes nothing — but it reports and stops like every other child. That
briefing is the *most* valuable one in the system: a child that implemented
has a diff telling the story, while a refusal has no diff at all, so the
briefing is the only copy of the reasoning.

## Architecture

Nothing new in the parent. No new command, no new process. The change lives in
the **opening prompt** — the same channel `--may` already travels, and the only
message a child reads as `origin.kind == "human"` typed by the maintainer.

```
child finishes its work
  │
  ├─ writes its final report            → lands in the transcript
  │
  ├─ git: branch && clean && ahead > 0 ?
  │     yes → push + gh pr create
  │     no  → publishes nothing (refusal, or nothing to deliver)
  │
  └─ claude stop ${CLAUDE_CODE_SESSION_ID:0:8}
        → SessionEnd fires
        → the hook reads the transcript, report included
        → briefing written
        → process dies, 300-400 MB released
```

### Why that order

Three constraints the code already documents:

1. **Report before stop.** The report becomes an `assistant` message in the
   transcript before the turn closes; `SessionEnd` then reads a complete
   transcript. There is no race, and nothing is lost by stopping after the
   report — today's loss is the opposite case, where the report is written and
   never becomes a briefing.
2. **Stop inside the worktree.** `SessionEnd` resolves the child's `cwd`
   (#247), so the stop must precede any tree removal. A child stopping itself
   is safe by construction: the tree exists while it is standing in it.
3. **PR before stop.** A dead session opens no PR.

### The child's own id

`CLAUDE_CODE_SESSION_ID` is exported into every command a session's Bash tool
runs (`core/sessions/parents.py:7`), and job directories are named by the
first eight characters of that UUID — verified on this machine: the env var
reads `d449c670-3b1c-4413-a089-953993aec5aa` and `~/.claude/jobs` entries are
8 characters wide. So the child derives its own short id as
`${CLAUDE_CODE_SESSION_ID:0:8}`, which is what `claude stop <id>` takes.

`claude stop` keeps the conversation (`claude stop --help`: *"Its conversation
is kept; resume it later with `claude attach <id>`"*). Stopping is not
discarding.

## `git` decides whether there is work

Not the child's own judgement. `delivery.Readiness.ready` is already exactly
the predicate — `bool(branch) and clean and ahead > 0`
(`core/sessions/delivery.py:157-160`) — and it is what `deliver` refuses on
today.

The divergent case is the child that believes it finished but committed
nothing, or committed and believes it refused. Under this design the state of
the tree is the truth and the child's belief is not consulted.

This is `grep-is-a-proxy-run-the-function` applied to the end of life: the
declared intent is a proxy, `git` is the signal.

## `--may pr` becomes the strong default

Today `NO_GRANT` is the default and reads, literally, *"Do not merge or push
without asking."* Under this design `pr` is what a child gets when nothing is
said.

`may: none` remains available in a contract piece for the rare case, and
`may: merge` remains refused. What changes is the silent default.

The invariant that falls, stated plainly: **the maintainer no longer sees the
diff before it reaches the remote.** Two reasons that is acceptable here:

- Every grant recorded in `<vault>/.mnemo/dispatch-grants.jsonl` is already
  `push` or `push,pr`; none is empty. This matches practice rather than
  changing it.
- A PR is reversible — close it, delete the branch. Merge, the irreversible
  step, is untouched and still gated by `land`.

## `deliver` changes role, and keeps two jobs the child cannot do

`deliver` does not lose its purpose. Reading `_deliver_one`
(`cli/commands/deliver.py:102-162`), it does four things, and the autonomous
child only covers the first:

| what `deliver` does | covered by the child |
|---|---|
| push + `gh pr create` | **yes** — now the main path |
| `close_on_merge` — add the missing `Closes #N` trailer | no |
| `_stop_finished` — stop a `done` child left behind | no — this is the net |
| `--review` — list what is deliverable | no |

The trailer matters specifically because of `--may pr`: a child that opens its
own PR may leave off `Closes #N`, and without it the issue does not close on
merge. `deliver` already handles that case and counts it as delivered (#317).

So `deliver` moves from being the path to being the repair: absent from the
routine, present in the repo. Running it against a child that already
delivered and stopped is close to a no-op — it finds the open PR, adds the
trailer if missing, sees the session is already `stopped` and skips it.
Idempotent, which is the sign the repair path does not fight the main one.

### Change: stopping stops depending on delivering

Today `_stop_finished` runs only *after* a successful delivery. With
autonomous children the interesting case becomes the child that is `done`,
did not deliver, and did not stop — the sixteen currently in the air.

Two adjustments, both inside `deliver`:

1. **`--review` reports the state.** A tree whose session is `done` but not
   `stopped` is shown as such, so the queue says what is holding memory.
2. **A `done` child can be stopped without delivering.** Since a briefing does
   not depend on a PR, the stop must not either. The existing safety stays:
   only `state == "done"` is stopped; `blocked` and working children are left
   alone with a line saying so.

The reason this is safe is the same one that makes the whole design work — the
briefing is made from the session, not from the delivery.

## CI runs after the child is gone

The child publishes a PR and stops within seconds. CI takes minutes. So the
child is never alive to see its own result, and this design does not pretend
otherwise.

Measured on this repository 2026-09-16 (`gh run list`): median CI **6.4 min**,
p90 around 12 min; of 200 runs since 2026-09-12, **17 red (8%)**. So roughly
one PR in twelve lands red, which is often enough to design for and rare
enough not to reshape the whole flow around.

The child's prompt already requires a green suite before publishing (*"Once
the full test suite passes"*), which is why the number is 8% and not higher.
What it cannot cover is the gap between the child's machine and the CI matrix
— another interpreter version, another operating system. Those failures are
invisible to a local run by construction.

**The red PR is caught at `land`, not by the child.** Two alternatives were
rejected:

- **The child waits for CI** (`gh pr checks --watch`, fix, then stop). It
  closes the loop, but holds the session idle for 6–12 minutes burning the
  300–400 MB this design exists to release, and a watch can outlast the turn.
  It trades the memory problem for the same memory problem.
- **The red PR is simply the maintainer's to notice.** Cheap and honest, but
  it hands 8% of deliveries back as unannounced manual work, discovered at
  merge time.

`land` is the right owner because it already is: it rehearses every piece in a
throwaway worktree, re-checks signatures in the merged tree, and runs the
suite before merging anything. The gate that catches this exists. What it adds
is reading the PR's CI state alongside the rehearsal it already performs.

A stopped child holding a red PR does no damage — the damage would be merging
it, and merging is exactly where `land` stands.

### Read the checks, not the rollup

When `land` reads CI state it must read the individual checks rather than the
aggregate conclusion. A repository may mark a job non-blocking, and such a job
fails while the run's conclusion and the PR's rollup both report success. A
gate that reads the aggregate would be reading a proxy of the thing it is
gating on — the failure mode `grep-is-a-proxy-run-the-function` records, here
in front of an irreversible step.

## Risks

**The rule lives in the prompt.** It is markdown a model obeys, not code that
forces it. A child that finishes and simply stops acting never reaches the last
instruction, and that child ends up exactly where the sixteen are today: no
worse, but not fixed.

This is why the auto-stop is the main path and not the only one. `deliver`
remains the net, and with the change above it becomes a wider one.

**A child could stop before publishing.** Ordering it after the `git` check
mitigates this, but a model that stops early leaves work in a tree with a dead
session. The work is recoverable — the worktree and branch survive, and
`deliver` still delivers them — but the session is not resumable for further
questions.

## Testing

- `ready()` false (no commits) → the child publishes nothing, still reports,
  still stops
- `ready()` true → publishes, reports, stops
- the report is present in the transcript before the stop, and appears in the
  resulting briefing
- a stopped child fires `SessionEnd` with a resolvable `cwd`
- `may: merge` is still refused at parse time
- `deliver --review` shows a `done`-but-not-`stopped` session
- `deliver` stops a `done` child that has nothing to deliver
- `deliver` against an already-delivered, already-stopped child is a no-op
- `land` refuses a piece whose PR has a failing check, including when the
  rollup conclusion reads success

## Open questions

None. The measurements are on this machine, the mechanism is verified against
the installed binary, and the shape is approved.
