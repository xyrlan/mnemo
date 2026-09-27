# Contract dispatch — parallel work from a feature, not from an issue tracker

**Date:** 2026-09-12
**Status:** DESIGN — approved in brainstorming, not yet planned or built.
**Issue:** (none yet)

## Problem

`mnemo dispatch` today takes GitHub issue numbers and nothing else. The CLI
signature is `dispatch ISSUE [ISSUE ...]` (`src/mnemo/cli/parser.py:87`), every
child is fed the body of `gh issue view N` (`src/mnemo/core/dispatch.py:169`),
and the worktree path `<repo>-wt-<N>` *is* the issue↔child mapping — there is no
sidecar file (`dispatch.py:83`).

That works when the unit of work is an issue. It does not work for the case this
design targets: a brainstorming session produces a feature, the feature has
several parts that could be built at once, and none of those parts is an issue.
Nobody files four issues to build one feature.

Filing them anyway is the workaround available today, and it is a bad one. It
inflates the tracker with entries that exist only to satisfy an argument parser,
and it puts the decomposition in a place the implementation never reads back.

### The decomposition is the actual problem

The missing piece is not an input format. It is that **nothing decides where the
feature splits**. Splitting is entirely caller-side today: `dispatch_all` is a
plain loop over the numbers you typed (`dispatch.py:315`), with no planner, no
fan-out heuristic, no dependency ordering.

A split is only real when each part can be built without reading the interior of
the others. When that is false and the work is dispatched anyway, the children
edit the same files and the cost returns as merge conflicts — after paying for N
sessions. A wrong split is worse than no split, because it looks organised.

This is the trap in reasoning by analogy with how a large engineering
organisation parallelises. Such an organisation does not parallelise *because*
it has a backend team and a frontend team; it has those teams because the
boundary in the code already existed — an API on one side, a client on the
other, a contract between them. The team is a consequence of the boundary, never
its cause. A decomposition that cuts by area ("one child for the backend, one
for the frontend, one for the tests") invents a boundary that the code does not
have, and every child lands in the same files.

## Goal

Let a session that has just designed a feature produce a reviewable
**contract** — the split, plus the boundary between the parts — and let
`mnemo dispatch` consume that contract directly.

Non-goals, explicitly:

- **No child-to-child communication.** If the contract is written correctly the
  children have nothing to negotiate; the negotiation happened at the cut.
  Children talking to each other is a symptom of a bad cut, not a feature.
- **No scheduling.** No ordering, no concurrency cap, no dependency graph
  execution. `dispatch_all` stays a dumb loop.
- **No automatic merge.**
- **No replacement of the superpowers skills.** Nothing in `brainstorming` or
  `writing-plans` is modified or forked.

## Constraint: plan mode persists nothing

Verified against the Claude Code documentation, because the design initially
assumed a plan document would be available to read:

- Built-in plan mode writes **no file**. The plan exists only as a message in the
  conversation.
- There is **no `ExitPlanMode` hook**, and no plan-approval event. The hook
  events are `SessionStart`/`SessionEnd`, `UserPromptSubmit`, `Stop`/
  `StopFailure`, `PreToolUse`/`PostToolUse`.
- The transcript JSONL under `~/.claude/projects/` contains the plan only as an
  ordinary assistant message, in an internal format that changes between
  releases.

Two consequences, both load-bearing.

**The skill must not read a plan file**, because in the plain plan-mode case
there is none. Its input is the conversation it is already in — whatever the
origin: a superpowers brainstorm, plain plan mode, or the maintainer describing
the feature out loud.

**The skill must not parse transcript JSONL** to recover a plan. The format is
internal and version-fragile; building on it would be reading a proxy rather
than the signal.

This is a simplification, not a limitation. The contract file — not the plan —
is what dispatch needs, and the skill is the step that produces it.

## Architecture

Three parts, one direction of dependency, no cycles.

```
conversation ──▶ skill ──▶ contract file ──▶ dispatch ──▶ N children
                           (human reviews)
```

**Skill `mnemo:decomposing-for-dispatch`** — markdown, no code. Reads the current
conversation. Writes a contract file. It never invokes dispatch.

**Contract file** — `docs/mnemo/contracts/YYYY-MM-DD-<feature>.md`. The only
durable artifact. Reviewed by the maintainer before anything is spawned.

**`mnemo dispatch --contract <path>`** — reads the file, creates one worktree per
piece, injects that piece's section into the child's prompt.

Dispatch never learns that the skill exists; it reads markdown. The skill never
calls dispatch; it writes and stops. **The maintainer is the joint between
them.**

That joint is the existing invariant preserved, not revoked:
`src/mnemo/cli/commands/dispatch.py:3` — *"Human-only… Spawning work is a
decision, and a decision belongs to the maintainer."* A model may now propose a
decomposition, which is cheap and reversible. Creating worktrees and spending
tokens still requires a human.

The session-queue invariant is likewise untouched. `read_sessions()` stays out of
hooks and MCP (`src/mnemo/cli/commands/sessions.py:7`, spec
`2026-09-12-live-session-queue-design.md:191`). This design gives a session no
ability to enumerate its peers.

### Seam

`dispatch_issue` already accepts an injectable fetcher — `fetch: Fetcher =
fetch_issue` (`dispatch.py:293`). A fetcher that reads a contract section rather
than shelling out to `gh` is the whole integration. The issue path continues to
work unchanged.

## The contract file

Fixed format, because dispatch parses it. Frontmatter plus one `##` section per
piece.

```markdown
---
feature: contract-dispatch
created: 2026-09-12
verdict: parallel        # parallel | sequential
---

## parser
- **files:** src/mnemo/core/contracts.py, tests/unit/test_contracts.py
- **exposes:** `parse_contract(path) -> Contract`, `Contract.pieces: list[Piece]`
- **consumes:** nothing

## dispatch-seam
- **files:** src/mnemo/core/dispatch.py, src/mnemo/cli/parser.py
- **consumes:** `parse_contract` from `parser`
- **exposes:** `mnemo dispatch --contract <path>`
```

Four fields per piece:

| field | meaning |
|---|---|
| *(heading)* | the slug — addresses the worktree and the branch |
| `files` | the hard boundary; the child does not edit outside it |
| `exposes` | the literal signature this piece must deliver |
| `consumes` | a signature this piece may assume exists, plus its owning piece |

`verdict: sequential` is a valid and expected outcome. It means *do not
dispatch*; `dispatch --contract` exits with an error asking for review rather
than spawning anything.

### Forward references resolve by signature, not by ordering

A `consumes` entry names something that does not exist yet when the child
starts. The child of `dispatch-seam` needs `parse_contract` before `parser` has
finished writing it.

**The child writes against the signature.** The other piece's `exposes` line *is*
the specification; the child stubs locally if it needs to, and the merge
resolves. This is why `exposes` must be a literal signature and not a prose
description — the signature is the entire contract, and a description cannot be
written against.

The alternative — dispatch waits for producers before spawning consumers — was
considered and rejected. It turns dispatch into a scheduler, requires
orchestration that does not exist (`dispatch_all` is a loop, `dispatch.py:315`),
and converts the parallelism into a pipeline, which removes the gain that
motivated the work.

## The skill

Deliberately small. Roughly 60–80 lines; materially more than that means it has
become prose.

Three reasons brevity is a design decision and not laziness:

1. It is read by a model whose context is already full at the end of a
   brainstorm. Rule 40 of 40 does not get followed.
2. What prevents a bad cut is a test that fails, not a paragraph that warns.
3. Issue #184 in this repo was exactly this failure: an elaborate instruction the
   model could not satisfy, where ignoring it was the *correct* answer. A rule
   the model cannot satisfy becomes noise, not a guardrail.

### Contents

**One test, applied to every pair of pieces:**

> Can piece A be written and tested without reading the interior of B?

If no, they are not two pieces. A cut by area almost always fails this test on
its own, which is why the skill needs no rule naming and forbidding that cut.

**The required output per piece:** slug, `files`, `exposes`, `consumes`.

**The boundary-versus-approach distinction.** "Do not touch `X`; consume
`Y.parse()`" is a boundary and belongs in the contract. "Use a regex to parse it"
is an approach and is forbidden. `build_prompt` deliberately takes no `approach`
parameter, with the rationale recorded at `dispatch.py:140`; the child dispatched
for issue #187 refused a prescribed approach and was right to. If the skill blurs
this line it reintroduces precisely the bug that rationale exists to prevent.

**`sequential` as a first-class result.** The skill must be able to conclude that
the work does not parallelise, and that must not read as failure. A skill that
can only say yes will always find a cut, and a skill that always finds a cut
produces only bad merges.

### Deliberately excluded

Size heuristics ("a good piece touches 3–5 files"), a target piece count,
execution ordering, effort estimates, a taxonomy of piece types. Each is the
model asserting a precision it does not have.

## Changes to dispatch

In order of risk.

**1. Addressing — `int | str`.** Today an `int` is required (`dispatch.py:83`,
`:99`, `:103`).

| | issue | contract piece |
|---|---|---|
| worktree | `repo-wt-193` | `repo-wt-c-parser` |
| branch | `fix/issue-193` | `feat/<feature>/parser` |

**The `c-` prefix is load-bearing, not decoration.** The existing regex
`_WT_RE = re.compile(r"-wt-(\d+)/?$")` (`dispatch.py:52`) carries a comment
stating that it is anchored on both ends *"so `mnemo-wt-feature` — a hand-made
worktree that is not a dispatch — is not mistaken for one"*, and `issue_for_cwd`
repeats the guarantee: it returns `None` for any path this module did not name,
"so a false positive cannot mislabel an unrelated session."

So `\d+` is not an implementation detail. It is the mechanism that separates a
dispatched worktree from one a human made by hand, and a naive widening to
`(.+?)` would delete that guard silently — every directory ending in `-wt-<word>`
would start being reported as a dispatch child in the session queue.

The regex therefore widens to a closed alternation rather than a wildcard:

```python
_WT_RE = re.compile(r"-wt-(\d+|c-[a-z0-9-]+)/?$")
```

It stays anchored, still refuses `mnemo-wt-feature`, and returns an `int` when
the capture is all digits and the slug `str` when it begins with `c-`. Piece
slugs are constrained to `[a-z0-9-]+` at parse time so that the contract file can
never name a piece the addressing scheme cannot express.

**It has two call sites, and the second is easy to miss.** Besides the inverse
lookup in `issue_for_cwd` (`dispatch.py:112`), `worktree_path` uses
`_WT_RE.sub("", root.name)` (`dispatch.py:95`) to strip an existing suffix so
that dispatching from inside a child does not nest worktrees. Widening the
pattern changes what counts as a strippable suffix, so the nesting guard must be
re-tested against slug-named worktrees, not only against the new lookup.

This is the change with the largest blast radius: every caller that assumes an
`int` must tolerate a string — notably `Session.label`
(`src/mnemo/core/sessions/jobs.py:78`), which interpolates it into the queue
display.

**2. Contract parsing.** New `src/mnemo/core/contracts.py` exposing
`parse_contract(path) -> Contract`. Dispatch gains a fetcher returning a piece
rather than an `Issue`. Both shapes satisfy what `build_prompt` consumes.

**3. Child prompt.** `build_prompt` gains a contract path that injects: the slug,
`files` as a hard boundary, `exposes` as what the child must deliver, `consumes`
as signatures it may assume exist. **No `approach` parameter is added.**

**4. CLI.** `mnemo dispatch --contract <path>`, mutually exclusive with
positional issue numbers. `--dry-run` continues to apply.

## Error handling

- **Invalid contract** — `consumes` naming a piece that does not exist or the
  consuming piece itself, missing field, duplicate slug, unaddressable slug,
  unparseable frontmatter. Rejected **before any worktree is created**; the
  whole file validates first.

  **What validation deliberately does not check:** that the owner piece
  actually `exposes` the consumed signature. The two lines are written by hand
  and differ cosmetically — a space, a backtick, a renamed argument — so string
  equality would refuse well-formed contracts over formatting. This repo has
  already paid for that shape once (#184: an instruction the model could not
  satisfy, where ignoring it was the correct answer), and a rule that fires on
  formatting gets ignored, which is worse than no rule. Whether the boundary
  was real is answered by the measurement below — did the children collide? —
  not by comparing two strings beforehand.
- **`verdict: sequential`** — rejected with a message pointing at the file.
- **Worktree already exists** — already refused today (`dispatch.py:203`);
  unchanged. Paths are never reused.
- **Failure partway through fan-out** — per-child rollback already exists
  (`remove_worktree`, `dispatch.py:239`), including on `KeyboardInterrupt`.
  Children already spawned stay up; the report says which succeeded.

  **Correction found while building this:** that rollback was incomplete, on
  the issue path as much as the new contract one. It removed the directory but
  left the branch, so retrying the same target died on `fatal: a branch named
  '...' already exists` — a *different* failure from the one rolled back, and
  one no amount of retrying clears. The existing test asserted only that the
  directory was gone, which is why the leak survived: the assertion with teeth
  is that the **retry succeeds**. `remove_worktree` now takes an opt-in
  `branch=` and deletes it with `git branch -d`.

  Opt-in rather than derived from the target, because only the caller knows
  whether the branch was ours: `ensure_worktree`'s own failure path must not
  pass it, since the usual reason `git worktree add` refuses is that the branch
  already existed — someone else's work, which a rollback must never delete.
  And `-d`, never `-D`: `-d` refuses a branch holding unmerged commits, which
  moments after `worktree add` it cannot have; if it somehow does, keeping it
  is the right outcome.

## Testing

- `core/contracts.py` — parse, validation, orphan `consumes`, duplicate slug,
  `verdict: sequential`. Pure, no I/O.
- Addressing — slug and int through `worktree_path`, `branch_name`,
  `issue_for_cwd`; round-trip; both kinds coexisting in one repo.
- The nesting guard — `worktree_path` called from inside a slug-named worktree
  must strip the existing suffix rather than nest, the same way it already does
  for `-wt-<int>`.
- The false-positive guard — a hand-made `repo-wt-feature` (no `c-` prefix, not
  all digits) still yields `None` from `issue_for_cwd` after the widening. This
  test encodes the reason the regex is an alternation and not a wildcard.
- Prompt — a contract injects the boundary and does **not** inject an approach.
- `tests/conftest.py:69` already monkeypatches `spawn_child` globally, so no test
  spawns a real child.

The skill has no automated test. It is markdown. The first real feature
dispatched through it is the test.

## Open question, to be answered by measurement

**Does the independence test actually discriminate?** It is sound in principle,
but nothing yet shows whether it rejects bad cuts in this repo or waves
everything through.

The contract file is committed, which makes this measurable rather than a matter
of faith: after the first few dispatched features, compare the pieces a contract
declared independent against the files the children actually touched and the
conflicts the merges actually produced. A piece that edited outside its `files`
boundary, or two pieces that collided, is a cut the test should have rejected.

Until that measurement exists, the test is a hypothesis.
