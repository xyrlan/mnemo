# Live session queue — mnemo over Claude Code background agents

**Date:** 2026-09-12
**Status:** DESIGN — validation gate PASSED 2026-09-12 with two corrections to
the design; see *Validation results*.
**Issue:** (none yet)

## Problem

Six issues to resolve. Today there are two options, and both cost too much:

1. **Six sessions in parallel.** The machine handles it fine. The human does
   not — six live contexts to hold, six conversations to poll, no signal about
   which one actually needs an answer right now.
2. **One session at a time.** One context to hold, but the wall-clock cost is
   six times the work in sequence.

The scarce resource is not compute and not context window. It is the
maintainer's attention. The goal is **machine parallelism with serial
attention**: six sessions working, one question at a time reaching the human.

A second, quieter cost sits underneath. A background session starts with an
empty context — none of the corrections the maintainer has already given. Six
naive sessions produce six novice mistakes in parallel, and the cost returns as
six PR reviews. Fan-out without memory makes the fleet dumber, not smarter.

## What already exists

Claude Code 2.1.269 ships the whole session manager. Verified on this machine:

| capability | command |
|---|---|
| start a background session | `claude --bg` (returns a short id; manages its own worktree) |
| list running sessions | `claude agents` (TTY) / `claude agents --json` |
| enter a session interactively | `claude attach <id>` |
| read output without entering | `claude logs <id>` |
| stop / delete | `claude stop <id>` / `claude rm <id>` |
| agent→user channel | `--brief` enables the `SendUserMessage` tool |

`claude attach` is the piece that solves the attention problem: the child holds
the context, the human steps in, answers, steps out. That is serial attention
over parallel work, and it is already built.

**This design does not reimplement any of it.** Anthropic built the engine.

### The state on disk

Each background session writes `~/.claude/jobs/<short-id>/`:

- `state.json` — current state
- `timeline.jsonl` — append-only state transitions
- `tmp/` — session scratch

Two real jobs on this machine, read 2026-09-12. Fields that matter:

```json
{
  "state": "blocked",
  "tempo": "blocked",
  "detail": "feedback analisado; precisa da loja + pedido pra confirmar no BD",
  "needs": "provide store name + order number to check metodoPagamento in database",
  "suggestedReply": "a loja é a domdogpva, pedido 42",
  "tokens": 275532,
  "name": "refactor-complementos-system",
  "intent": "uma pergunta eu nao lembro o que voce tinha falado sobre...",
  "cwd": "/Users/xyrlan/github/meunu",
  "sessionId": "e51f48ec-a41d-4484-8b90-75fecc62bd6c",
  "linkScanPath": "/Users/xyrlan/.claude/projects/-Users-xyrlan-github-meunu/678637f0-....jsonl",
  "updatedAt": "2026-08-19T21:59:28.654Z",
  "children": [{"id": "307", "href": "https://github.com/...", "kind": "pr"}]
}
```

`needs` is literally *what this session needs from you*. Present in 3/3 jobs
observed, including an infrastructure block (`"login required — run /login ·
Login expired"`), not only product questions. `suggestedReply` is present in
1/3 — treat as optional.

**`state` and `tempo` are different axes, and the queue depends on `tempo`.**
`state` is the process phase (`working`, `done`); `tempo` is whether a human is
needed (`blocked`, `active`, `idle`). A session waiting on a question sits at
`state=working, tempo=blocked`. Reading `state` alone misses exactly the
sessions the queue exists to find. See *Correction 1*.

`linkScanPath` points at the session transcript — the same `.jsonl` that
`mnemo learn` already reads. That is the bridge between the queue and the
existing extraction pipeline, and it costs nothing to build.

### What is missing

1. **No aggregated queue.** `claude agents` lists sessions; it does not answer
   "which two of the six are waiting on me right now". Without that, the human
   polls all six — the original pain.
2. **No memory of answers.** Answering a blocked session is the highest-signal
   correction there is: the human is only consulted when it matters. Today that
   answer is extracted like any other transcript line, with no knowledge that
   it resolved a specific `needs`.
3. **Sessions cannot learn from each other.** Session #1 discovers a convention
   at 14:00; session #4, running now, does not know.

This design covers (1) and (2). (3) is roadmap.

## Positioning

> Anthropic built the engine. mnemo is what makes the engine pay: children are
> born knowing, the human is interrupted less, and every interruption becomes
> durable memory.

The product test, stated by the maintainer and adopted here as a gate: **if six
issues with mnemo perform worse than six issues without it, the product is
wrong — not incomplete, wrong.**

## Architecture

One direction of dependency. mnemo reads; Anthropic writes.

```
~/.claude/jobs/*/state.json        (Anthropic writes; mnemo only reads)
            │
            ▼
   [1] queue reader  ──────────►  mnemo sessions / --watch / statusline
            │                      (human surfaces only; zero context tokens)
            ▼
   [2] unblock detector  ───────►  marks transcript for priority extraction
            │
            ▼
   [3] existing extraction (mnemo learn) ──► _inbox proposal ──► vault ──► next child
```

**[1] Queue reader.** Scans `~/.claude/jobs/*/state.json`, sorts blocked-first,
renders. Read-only, no process, no daemon. Tolerant parsing: a missing field
degrades the render, never raises.

**[2] Unblock detector.** Watches the `tempo` field in `state.json` for a
`blocked → active` edge (see *Correction 1* — `tempo`, not `state`, and not via
`timeline.jsonl`). On an edge it records the tuple `(needs, sessionId,
timestamp, linkScanPath, cwd)` and marks that transcript region as carrying a
high-signal correction. It extracts nothing itself — it only records where to
look.

*When it runs:* piggybacked, never as a daemon. The sweep is a cheap tail-read
of files mnemo already has a reason to open, so it rides the existing triggers:
every `mnemo sessions` invocation (including `--watch` ticks) and the
`session_end` hook. The statusline is deliberately *not* a trigger — see the
latency note under Surfaces. This mirrors how autopilot already
schedules itself off hooks rather than holding a process. Worst case the sweep
is late, never lost — `timeline.jsonl` is append-only, so a transition observed
minutes afterwards records identically.

**[3] Extraction.** The existing pipeline, given the marker.

### Where mnemo's own state lives

The detector needs somewhere to record what it saw, and it may not be
`~/.claude/jobs/`. It writes `<vault>/.mnemo/session-queue.json`, alongside the
other mnemo state files (`statusline-original.json` and siblings):

```json
{
  "seen": {
    "<short-id>": {
      "lastTempo": "blocked",
      "lastTimelineOffset": 4821,
      "unblocks": [
        {
          "at": "2026-09-12T11:04:22Z",
          "needs": "usar bcrypt ou argon2 pro hash?",
          "suggestedReply": "argon2, bcrypt é lento pro nosso volume",
          "sessionId": "...",
          "linkScanPath": "/Users/.../678637f0-....jsonl",
          "cwd": "/Users/xyrlan/github/mnemo",
          "extracted": false
        }
      ]
    }
  }
}
```

`lastTempo` is what makes the unblock edge detectable at all — the detector
compares it against the current `tempo` in `state.json` and records the
`blocked → active` transition itself. `lastTimelineOffset` keeps the `state`
history read incremental. `extracted` flips once the pipeline has consumed the
marker, so a transition is never processed twice.

This file is disposable: deleting it costs at most some un-extracted
high-signal markers, never a rule or a proposal.

### Two invariants

1. **The queue never enters model context.** No hook, no MCP tool, no
   injection. Terminal and statusline only. The day "how many sessions are
   blocked" costs a context token in the parent session, the product has lost
   the thing it was built to protect.
2. **mnemo never writes to `~/.claude/jobs/`.** Strictly read-only. The state
   belongs to Claude Code. On schema change mnemo degrades; it never corrupts.

mnemo's own state (what it records) lives in the vault, like everything else.

## The queue reader

`mnemo sessions` — default scope is the current repo (matching `cwd`); `--all`
for every repo.

```
$ mnemo sessions

TE ESPERANDO (2)
  a3f1  auth-refactor          18m   usar bcrypt ou argon2 pro hash?
        ↳ sugerido: "argon2, bcrypt é lento pro nosso volume"
  9c2e  rate-limit-middleware   4m   aprovar mudança de schema em User?

TRABALHANDO (3)
  7b0d  fix-typo-readme               escrevendo testes                 42k
  1e88  bump-actions-v7               rodando CI                       118k
  c204  export-command                lendo src/mnemo/cli/             203k

PRONTAS (1)
  e51f  refactor-complementos         PR #307, #308, #309              276k

  attach: claude attach a3f1
```

The ordering is the message: blocked first, oldest first. The first line
answers "where do I go now", which is what replaces polling.

Render decisions:

- **`needs` is the primary column.** Falls back to `detail`, then to `—` plus
  age (a session stalled a long time without saying why is itself a signal).
- **`suggestedReply` renders indented** when present. Often the human only has
  to confirm, and seeing the suggestion in the queue decides whether to attach
  now or later.
- **Age only on blocked rows.** In `working` it means nothing; in `blocked` it
  is the cost of the human's delay.
- **Tokens only on working/done.** Cost signal, and a marker of a bloated
  session.
- **`children` renders as the PR list** under PRONTAS — that is the delivery.
- **Name** from `name`, falling back to truncated `intent`.

Surfaces, in order of value:

- **`mnemo sessions`** — the command above.
- **`mnemo sessions --json`** — same data, for scripts and tests.
- **`mnemo sessions --watch`** — redraw every 2s; file polling, no daemon.
- **Statusline** — `mnemo · 2 esperando`. Highest value per token spent: the
  human never runs the command, the signal finds them. Reuses the existing
  additive composer.

  The statusline path is latency-critical: the composer runs on every render
  under a 2s timeout, and it already scans the vault to count topics. So it
  reads `state.json` only — never `timeline.jsonl`, and it never runs the
  detector sweep. Counting blocked sessions is a stat + parse per job
  directory, single-digit milliseconds for a realistic fleet. If the job
  directory is large enough to threaten the budget, the count degrades to
  absent rather than slowing the render.

**No push notification** (sound, OS banner). Deliberate: it would turn the
queue from "I check when I can" into "it interrupts me", and interruption is
the cost being reduced.

Deliberately out (YAGNI):

- No navigable TUI — `claude agents` has one, and `attach` already works.
- No answering from the queue — the human attaches; the queue only points.
- No kill/respawn — `claude stop` / `rm` / `respawn` exist.

## Session decisions as proposals

A decision taken inside a child **enters `_inbox` as a proposal, never as a
rule.** The path to the vault is the one that already exists: recurrence, or
the maintainer's confirmation.

Two reasons, both real failure modes:

1. **Local context is not global law.** "use argon2", answered inside the auth
   issue, may be about *that* endpoint, not the repo. Promoting it to an
   injected rule is exactly the failure [[product-audit-2026-09-01]] already
   measured — 40% of the vault being generic aphorisms. Fan-out multiplies that
   risk by six, because there are now six sources of local decisions.
2. **The decision may be wrong.** Child #3 had partial context. Child #5, which
   read a different part of the codebase, may know argon2 does not run on the
   target runtime. Freezing #3's decision propagates the error to five others.

### Mechanism — all of it already exists

| need | existing mnemo machinery |
|---|---|
| "proposed, not in force" state | `shared/_inbox/` (33 items today) |
| promote on N independent sources | `scoping.universalThreshold` (default 2) |
| accumulate sources across runs | `union_with_prior_sources` |
| topic-gated injection within budget | BM25F reflex gate (~150 tokens, ≤2 items) |

"Two sessions reaching the same decision promotes it" is `universalThreshold`
with *session* on the axis where *project* sits today. Not a new mechanism —
the same promotion, a different axis.

### Capture

The detector sees `blocked → working`. It knows `needs` (the question),
`suggestedReply` (what the session proposed), `linkScanPath` (where the human's
answer is written), and `cwd` / `name` (the scope). It marks that region as
high-signal and hands it to extraction.

### Scope travels with the proposal

The proposal carries where it came from: repo, session, issue, and the question
it answered. This is the antidote to the generic aphorism — a proposal reading
*"argon2 for password hashing, answering 'bcrypt or argon2?' in issue #142 of
repo X"* cannot be misread as universal law, because the context travels with
it.

### Visibility between siblings

Child #5 may see #3's proposal, but only through the same BM25F gate that
filters rules today — relevant topic, inside the same ~150-token budget.
Proposals compete *within* the budget; they never add to it. The render states
what it is:

```
mnemo reflex context:
• [proposta, sessão auth-refactor] argon2 pro hashing — contexto: issue #142
```

`[proposta, sessão X]` instead of `[[slug]]`. The child reads it as sourced
data, not as an order. If it knows something that contradicts, the
disagreement is new information — and becomes another proposal, not a fight.

### Disagreement freezes

Two conflicting proposals on the same topic: neither promotes, and the conflict
appears in the queue as a low-priority row:

```
TE ESPERANDO (2)
  a3f1  auth-refactor          18m   usar bcrypt ou argon2?
  ⚖     duas sessões discordam sobre hashing  (auth-refactor / rate-limit)
```

Resolved whenever the human wants. No interruption. If never resolved, nothing
promotes — the default is safe.

**The worst case of this design is that mnemo learns nothing.** No path here
produces a wrong rule injected into six sessions. An unpromoted proposal is
zero noise.

## Failure modes

Everything degrades; nothing breaks.

| failure | behavior |
|---|---|
| `~/.claude/jobs/` missing | "nenhuma sessão em background" |
| `state.json` malformed or partial | skip that session, render the rest |
| field removed (Anthropic schema change) | per-field degrade: `needs`→`detail`→`—` |
| `linkScanPath` dead | detector records it; extraction ignores it later |
| stale / orphaned job | filter by `updatedAt` (>24h outside the listing) |

A `doctor` check reports "N background sessions, schema readable".
[[terminal-hides-packaging-bugs]] is the precedent: a packaging bug only
surfaces when doctor actually exercises the path.

## Testing

- Unit tests over real `state.json` fixtures — three shapes observed
  2026-09-12: one full (`needs` + `suggestedReply` + 48 `children`), one
  infrastructure block (`needs` describing a login failure, no
  `suggestedReply`), one freshly dispatched (`state=working, tempo=blocked`,
  `needs` with an `answer:` prefix, 131 tokens). The third is the common case
  and the one the queue is built for. TDD throughout, per project convention.
- A test that a session at `state=working, tempo=blocked` lands under
  TE ESPERANDO. This is the regression guard for *Correction 1*; a
  `state`-only reader files it under TRABALHANDO and the queue silently
  loses its whole purpose.
- An explicit schema-drift test: a state file with fields missing renders
  without raising.
- **No test may invoke `claude --bg`.** [[test-suite-spawned-real-autopilot-jobs]]
  records the cost of that mistake (46 orphan processes froze the Mac). The
  conftest guard blocks spawning; any test needing a real session sits behind
  an opt-in marker.

## The performance gate

The maintainer's criterion — *"if throughput gets worse, the product has lost
its point"* — as measurable checks:

1. **Parent context: zero tokens.** Testable: no hook and no MCP tool exposes
   the queue.
2. **Child context: today's ceiling.** ~150 tokens, ≤2 items. Proposals compete
   inside the budget, never add to it. Testable.
3. **Interruptions: children with the vault must block *less* than naive
   children.** Measurable — `mnemo sessions --json` counts blocks per session.
4. **No new rule without repeated evidence.** A proposal promotes only on
   recurrence or explicit confirmation.

(3) is the number that decides whether the product makes sense. If children
with mnemo block as often as children without, the thesis is wrong, and that is
knowable early.

## Validation results (2026-09-12)

Ran against a real dispatch on this repo: a background session told to read the
README and then stop and ask a question. Session `9bc3a47a`, plus a second
session `5cc4b99d` created accidentally (see correction 2). Both cleaned up
afterwards; `~/.claude/jobs/` is back to its prior two entries.

**PASS — `needs` is populated, fast, and well-formed.** Within 5 seconds of
dispatch:

```
state            'working'
tempo            'blocked'
needs            'answer: Resumo do resto do README em qual idioma? (Português · Inglês)'
detail           'Showing top of README'
name             'read readme first section'
linkScanPath     '~/.claude/projects/-Users-xyrlan-github-mnemo/9bc3a47a-....jsonl'
```

Better than assumed: `needs` carries an `answer:` prefix naming the kind of
reply expected, and inlines the options. It is a queue row almost verbatim.
`suggestedReply` was absent here — confirming it is optional (now 1/3 observed).

**PASS — plugin hooks fire under `--bg`.** Session `9bc3a47a` wrote two entries
to `<vault>/.mnemo/reflex-log.jsonl`, both evaluated and correctly silenced
(`relative_gap_fail`). The child is born with the vault. No prerequisite work.

### Correction 1 — the blocked signal is `tempo`, not `state`

The design said the detector watches `blocked → working` in `state`. Wrong.
The two fields mean different things:

- **`state`** — process phase: `working`, `done`. Written to `timeline.jsonl`.
- **`tempo`** — whether a human is needed: `blocked`, `active`, `idle`. Observed
  in `state.json` only; the test session's `timeline.jsonl` recorded the
  `working` transition and never mentioned `tempo`.

A session can sit at `state=working, tempo=blocked` indefinitely — exactly the
case the queue exists to surface, and exactly the case a `state`-only reader
would miss.

So: **the queue reads `tempo` from `state.json`** (blocked / active / idle), and
**the detector cannot rely on `timeline.jsonl` for unblock transitions.** It
polls `tempo` in `state.json` across its existing triggers and records the
`blocked → active` edge itself, keeping the last-seen `tempo` per session in
`session-queue.json` alongside `lastTimelineOffset`. `timeline.jsonl` remains
useful for `state` history and for `done`.

### Correction 2 — answering from outside forks the session

`claude --bg --resume <session-id> "answer"` against a *live* session does not
answer it. It prints:

```
note: session 9bc3a47a is already running in the background, so this started a
copy as 5cc4b99d. `claude attach 9bc3a47a` opens the original.
```

The copy ran the answer to completion while the original stayed
`tempo=blocked` with the same `needs`. Scripted answering forks; it does not
unblock.

This is a constraint, not a blocker, and the design already assumed it: the
queue points, the human attaches. But it rules out a tempting future feature —
answering a session from the queue — and it means the detector must tolerate
fork siblings (two jobs, same `intent`, different short ids). Worth a note in
the queue render if fork pairs turn out to be common.

### Not established

The end-to-end memory claim — human answers a blocked session, the answer
becomes a scoped proposal — was **not** validated. The forked copy did finish
and a rule about commit language did land, but in Claude Code's own
`memory/MEMORY.md`, not mnemo's vault: `.mnemo/learned.jsonl` stayed at 143
entries. mnemo extraction runs on `session_end` and may simply not have run
yet, but that is an assumption, not a measurement. **Verify during
implementation** that a `tempo: blocked → active` edge yields an extractable
correction in mnemo's own pipeline.

## Scope

**In:** queue reader (`mnemo sessions`, `--json`, `--watch`), statusline
segment, unblock detector, scoped proposals, promotion by recurrence,
disagreement row in the queue, doctor check.

**Out, named for the roadmap:**

- **Rich dashboard.** `claude agents` in a TTY is already an official screen,
  and it has not been examined. Designing against an unexamined screen is how
  a second, stale screen gets built. Revisit with the right question: *what
  does mnemo know that the official screen does not show* — cross-repo `needs`,
  answer history, vault linkage, cost over time.
- **Dispatch via MCP (`mnemo_dispatch`).** The parent session can already run
  `claude --bg` through Bash. Wrapping that in an MCP tool is convenience, not
  capability. Build it after missing it.
- **Live cross-session memory** (child #1 teaches child #4 in real time).
  Requires real-time rule writing, a quality gate without human review, and
  resolving concurrent writers — [[ws-a-corrections-layer-status]] records
  "one writer subagent at a time" as a known constraint.
- **Conflict coordination** between siblings touching the same files. A
  worktree isolates the checkout, not the intent.
