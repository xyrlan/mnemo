# A rate-limited child is recoverable, and the queue said "delete it" (#393)

2026-09-19. What was measured, what was built, and which half of the
verification had to be simulated.

## The incident

Six children (#380–#385) were dispatched with the account's five-hour window
nearly spent. Each ran ~8 minutes and stopped on `You've hit your session
limit · resets 2:40am`. Six hours later `mnemo sessions` had all six under
**ABANDONED** with `rate limited — wait and retry`, and offered `remove:
claude rm <id>`. `claude rm` deletes the worktree; five of the six trees held
uncommitted work and none had a commit or a PR.

Woken by hand — `claude respawn <id>` and then a cross-session message — all
six went `blocked` → `working` and all six finished with a PR (#386–#391).
Nothing was abandoned. The queue was right that the process was gone and
wrong about what that meant.

## What the evidence actually says

**Claude Code writes the reason down twice**, and only one of the two is
machine-readable.

`state.json` gets a rendered sentence. The mapping from API error code to
sentence lives in the binary's own switch and nowhere else; transcribed from
2.1.278 on 2026-09-19 into `sessions.stalls.NEEDS_BY_ERROR`, and re-checked
against the installed binary by a live test that needs no spawn (the binary
stores the em dash as `—`, so both forms are searched):

| error code | `needs` |
| --- | --- |
| `authentication_failed` | login required — run /login |
| `oauth_org_not_allowed` | org disabled OAuth — use API key or ask admin |
| `account_on_hold` | account on hold — see detail |
| `cloud_credential_error` | cloud credentials unavailable — check or refresh them |
| `verification_required` | organization verification required — see detail |
| `billing_error` | usage limit reached — check plan |
| `rate_limit` | rate limited — wait and retry |
| `overloaded` | API overloaded — wait and retry |
| `server_error` | API unavailable — retry |
| `invalid_request` | request too large — /compact or trim *or* invalid API request — see detail |
| (absent) | API error — see detail |
| `unknown`, `dlp_request_denied` | API error (`state: failed`) |

The transcript gets the error itself: an `assistant` record with
`isApiErrorMessage: true`, `model: "<synthetic>"`, zero usage, `error`,
`apiErrorStatus`, and — for a 429 — `quotaLimits` carrying `status`,
`rateLimitType` and **`resetsAt`, a unix epoch**. That epoch is the only
machine-readable answer to "when is this free", which is why the classifier
reads the transcript first and falls back to the sentence.

**The population, over the 404 transcripts in `~/.claude/projects`
(2026-09-19): 79 API-error records.**

| error | n |
| --- | --- |
| `authentication_failed` | 23 |
| `rate_limit` | 18 |
| `invalid_request` | 17 |
| `server_error` | 13 |
| `unknown` (400) | 8 |

Of the 18 rate limits, **14 carry `quotaLimits.resetsAt`** — 13 `five_hour`,
1 `seven_day` — and **4 do not**. All 4 of those read *"You've reached your
Fable limit. Run /usage-credits to continue or switch models"*: a per-model
credit cap, not a window, and not something waiting fixes.

So `error == "rate_limit"` is **not** the classifier. The epoch is. A rate
limit without one is filed as needing a person, because what it needs is a
decision about credits.

## What was built

- `core/sessions/stalls.py` — classify a blocked session into `RATE_LIMIT`
  (a clock frees it, and it says when), `TRANSIENT` (the API asked for a
  retry, no clock) or `HUMAN` (a person must act outside the session).
- `core/sessions/wake.py` — `claude --bg --resume <full session id> <NUDGE>`.
- `mnemo resume` — every stalled child in this repo's queue whose reset has
  passed, in one command; `--dry-run`, ids or issue numbers to narrow.
- `render.py` — the STALLED bucket, and the `rm` hint kept away from it.

### Why `--bg --resume` and not `respawn` + a socket message

Measured on 2.1.278 (`claude_cli` assumption `resume-under-own-id`). Two
throwaway `--bg` haiku children were spawned, stopped, and woken:

- The woken session keeps its **short id, job directory, transcript and
  `cwd`**, with the conversation intact.
- **Every saved option is restored**, not just the model. A probe spawned with
  `--disallowedTools Edit Write NotebookEdit --setting-sources project,local
  --strict-mcp-config --mcp-config <f> --settings <f> --model haiku` came back
  carrying all seven in `respawnFlags`. A read-only child therefore does not
  regain `Edit`, and a lean child does not lose mnemo's hooks.
- `SessionStart` fires again on the wake (a marker hook wrote two files for
  one spawn plus one wake).
- **Passing the short id does not fail — it forks.** stdout reads `note:
  started a copy of that conversation as <new id>`, and a second session is
  now running in the child's worktree. That is the accident
  one-worktree-per-child exists to prevent, so `wake()` refuses anything that
  is not a full id and checks the output for the note afterwards.

### The authority question

The issue's boundary: *whatever resumes a child must not re-grant anything;
the opening prompt is the only message a child reads as the maintainer's.*

A `--bg --resume` prompt lands in the transcript as `origin.kind: "human"`,
`promptSource: "typed"` — byte-for-byte the opening prompt's shape. It is
therefore indistinguishable from the maintainer typing, and the boundary is
honoured structurally rather than carefully: `wake()` takes **no message
parameter**. The text is a constant that grants nothing, says so in as many
words, and points back at the opening prompt. There is no argument through
which a caller, a script or a later feature could put words in the
maintainer's mouth; a test pins the signature.

It opens with `<mnemo-resume`, in `detector.SYNTHETIC_PREFIXES`, so the
unblock sweep does not record a wake as the maintainer answering a blocked
child — a wake is precisely the case where nobody answered anything.

### What bounds re-spending the window

The reset does, and it is the only thing that can. No `claude` subcommand,
flag or file reports how much of the window is left (`/usage` is an in-session
slash command; the usage endpoint is not on the CLI), so mnemo cannot see it
and must not pretend to. Past its reset the window is *fresh* — which is why
waking all six at once was safe on 2026-09-19 — and the count of children is
not the thing being bounded: N children do not spend more of a window than
one child doing the same work, they spend it N times faster. `mnemo resume`
prints that rather than implying a budget it cannot read.

"Or none" — a resume that happens by itself — was refused. mnemo has no
daemon on purpose, and the triggers a sweep could ride (`mnemo sessions`, the
`SessionEnd` hook) are read-only. Spending the account's budget as a side
effect of printing a queue is not something a queue should do.

## How faithful the verification is

Two halves, and only one could use a genuinely rate-limited child.

**The classification: real, no simulation.** The six children's transcripts
are on disk. `tests/fixtures/transcripts/stalled_rate_limit_594436f2.jsonl`
is the tail of #380 cut at the record it stopped on, every key and number
verbatim, only free text redacted; `stalled_credit_cap.jsonl` is a real
`rate_limit` with no `quotaLimits`, the contrasting case. The unit tests read
those, not a hand-written record with a `resetsAt` in it — which would have
passed every assertion while saying nothing about what Claude Code writes
(`fixtures-that-lie-about-the-real-shape`).

**The wake: end-to-end against the real CLI, with one field simulated.** A
throwaway `--bg` haiku child was spawned in a scratch repo and stopped, its
`state.json` edited to Claude Code's own blocked shape (`state: blocked,
tempo: blocked, needs: "rate limited — wait and retry"`), and child #380's
**real** rate-limit record appended to its transcript with only the session
ids and `cwd` rewritten. Then, unmodified:

- `mnemo sessions` printed `STALLED (1) … five-hour spent — free since 02:40`
  with `resume: mnemo resume  (1 of 1 free now)` and no `claude rm` hint;
- `mnemo resume` woke it through the real `claude --bg --resume`;
- afterwards the job directory was the same one, **exactly one** job existed
  in that tree (no copy), and the transcript showed the child reading the
  nudge, running `git status`, reporting the tree clean and finishing;
- `detector.is_human_turn` on the woken turn as actually written to disk
  returned `False`.

What is simulated is only *that this child hit the limit*. Its blocked state
is Claude Code's own shape, and the record driving the classification is a
real one. What could not be reproduced is a live 429 on demand — provoking
one means spending the account's window, which is the thing under discussion.

## Where to re-check

- `claude_cli.ASSUMPTIONS`: `resume-under-own-id` and `api-error-block`, both
  verified against 2.1.278 on 2026-09-19. The sentences are re-checked by
  `pytest -m live_claude`; the wake is not — that test would have to spawn and
  wake a real child, which the live suite deliberately does once, for spawn.
- If Claude Code starts resolving a *reason* into `state.json` alongside
  `needs`, the transcript read becomes a fallback rather than the source.
