---
feature: dispatch-last-metre
created: 2026-09-13
verdict: parallel
---

Four follow-ups filed after the 2026-09-13 dispatch round (#215, #216, #217,
#218). Three pieces, because #215 and #217 need the same missing fact — the
join from a child to its branch to its PR — and splitting them would put two
children in one seam.

**The one file all three touch: `src/mnemo/cli/parser.py`.** Each piece adds
its own flags or subparser there, at a different anchor: the `sessions` /
`session_p` block (lines 77-92), the `dispatch_p` block (93-104), and a new
subparser of its own. Add only your own lines, leave every neighbouring line
byte-identical, and the three-way merge resolves. Do not reformat, reorder or
re-wrap anything you did not add — a cosmetic touch to a line another piece
also edits is the one way this collides.

## delivery

- **files:** src/mnemo/core/sessions/delivery.py, src/mnemo/cli/commands/deliver.py, src/mnemo/cli/parser.py, src/mnemo/core/sessions/render.py, tests/unit/test_delivery.py, tests/unit/test_deliver_command.py, tests/unit/test_sessions_render.py
- **exposes:** `ready(worktree, *, repo_root) -> Readiness`, `pr_for(branch, *, repo_root) -> str | None`
- **consumes:** nothing

Closes #215 and #217. Read both issues in full (`gh issue view 215`,
`gh issue view 217`) — they carry the reasoning this summary compresses.

**The problem (#215).** Four children finished their work and every one of them
stopped, unable to deliver it. The maintainer pushed and opened the PR three
times by hand. Two children were stopped by their own permission classifier —
correctly, and both refused to route around it, which is behaviour that must
stay. The cost is not the denial: it is that approving N children costs N
context switches, and the mechanics after the decision are not worth repeating
N times.

**The invariant, and it is not negotiable.** A child must never acquire
permission it was denied, and the maintainer must see the diff before anything
is pushed. A command that pushes unreviewed work is the same failure as a child
that routes around its classifier. The push runs in the parent, under the
maintainer's own credentials, on branches the maintainer named after looking.

**Shape agreed with the maintainer**, two commands rather than a prompt:

- `mnemo deliver --review` — read-only. Every dispatch worktree, its branch,
  whether it is clean, how far ahead of `master` it is, a diffstat, and any PR
  that already exists. Prints and exits.
- `mnemo deliver <short_id> [<short_id>...]` — pushes and opens a PR for
  **exactly** the ids named, and nothing else. Naming an id is the approval.
  There is deliberately no `--all` and no "deliver everything ready": one flag
  approving N children is the failure mode this exists to prevent.

Both must work on a pipe. `mnemo sessions` and `mnemo session` are pipe-safe by
design and this joins them; an interactive y/n prompt would break that and was
rejected for it.

**Readiness comes from git, not from session state** (#217's "read the PR from
git rather than from session state"). A worktree whose tree is clean and whose
branch is ahead of `master` is ready. That fact is authoritative, survives the
child dying, and needs nothing volunteered by Claude Code. A child that is not
clean or not ahead is refused with a reason, never pushed anyway.

Contrast with what exists: `render.py:_prs` filters `Session.children` for
`kind == "pr"`, a list Claude Code writes when it happens to notice a PR and
mnemo never writes at all. That is why #212 showed a PR number and #213 showed
a prose sentence. `gh pr list --head <branch> --json number,url` is the
authoritative join, and `branch_name()` already derives the branch from the
target. Wire that into `_prs` as a fallback so the queue's PRONTAS bucket
stops depending on the child reporting anything.

**Persist nothing.** `dispatch.py:28-35` argues a sidecar keyed by `short_id`
would race the child and need reconciling on prune, and
`tests/unit/test_dispatch_plan.py:18-21` encodes that as a rejected proposal.
That reasoning was about the *plan*; the maintainer's decision is that it
extends to the outcome too. Derive the join on every read. If a `gh` call per
row is too slow to be acceptable, cache within the single invocation — do not
reach for a file on disk.

`issue_for_cwd` is the existing link from a session back to what it was
dispatched for, and `worktree_path` / `branch_name` are its inverses. Use them
rather than re-deriving a path convention.

Judgement calls that are yours: how `--review` finds the worktrees (the queue
already knows every child and its `cwd`; `git worktree list` also knows);
whether a PR body is generated or left to `gh` defaults; what happens when a
named id has a PR already. Decide from the code and say what you concluded.

Run the full suite. Note that `tests/unit/test_sessions_render.py` holds
`test_render_without_activities_is_unchanged`, which pins `render_queue`'s
output — if your `_prs` change moves it, decide deliberately whether that
guarantee still means what it says and say so, rather than editing the test to
match.

## contract-discovery

- **files:** src/mnemo/core/contracts.py, src/mnemo/cli/commands/dispatch.py, src/mnemo/cli/parser.py, skills/decomposing-for-dispatch/SKILL.md, tests/unit/test_contracts.py, tests/unit/test_cli_dispatch.py
- **exposes:** nothing
- **consumes:** nothing

Closes #216. Read it in full (`gh issue view 216`).

**The problem.** The first real `--contract` file was hand-written by reading
`src/mnemo/core/contracts.py` — the frontmatter keys, the `##`-per-piece
structure, the exact bullet syntax. It worked, and it worked because the author
had the parser open. Someone who does not has `--contract PATH` in `--help` and
no way to learn what belongs in `PATH`.

This is a discoverability gap, not a defect. `--contract` works. Nothing here
should change what the parser accepts.

Directions from the issue, in rough order of value — how many you take is your
call, argued from what you find:

- An example the command itself can emit, so the format is learnable from the
  command that consumes it rather than from the source.
- **Make the refusal teach.** `ContractError` already refuses precisely, and
  before any git state exists — `_validate` is a good set of messages that
  assume the reader already knows what a contract is. An unparseable file could
  show the expected shape next to what it found.
- Point `--help` at `skills/decomposing-for-dispatch/SKILL.md`. Cheapest, and
  closes discoverability without touching the format.

**Settle the directory convention, with evidence.** The skill says to write
contracts to `docs/mnemo/contracts/YYYY-MM-DD-<feature>.md` (SKILL.md:16, again
at :85). That directory does not exist in the repo, nothing in `src/` or
`tests/` reads any contract directory, and both real contracts went to
`docs/superpowers/contracts/` instead — including the one that dispatched you.
Verify that yourself rather than trusting this paragraph. Then either make
something read the directory (so `--contract` could take a feature name and not
a path), or stop the skill prescribing a location nothing enforces. A
convention that lives only in prose, in a file nobody was pointed at, is not a
convention. Say which you chose and why.

The parse messages are the product here. Whatever you add, a maintainer who
runs the command wrong should end up knowing more than before they ran it.

Run the full suite before you finish.

## watch-modes

- **files:** src/mnemo/cli/commands/sessions.py, src/mnemo/cli/commands/session.py, src/mnemo/cli/parser.py, tests/unit/test_sessions_command.py, tests/unit/test_session_command.py
- **exposes:** nothing
- **consumes:** nothing

Closes #218. Read it in full (`gh issue view 218`).

**Two problems, both presentation.** From #210's own measurements the cost side
is known: nine real dispatch-child transcripts total 9.3 MB, a cold tick across
them is 7.8 ms and a warm tick 0.011 ms. The bookmark already made the read
nearly free. Nothing here is a performance fix.

1. **The redraw destroys its own history.** `--watch` is
   `"\033[2J\033[H"` plus `time.sleep(2)`: every tick erases the screen and
   reprints. Scrollback is unusable, it cannot be read beside the work it
   describes, and nothing distinguishes a changed row from an unchanged one —
   with four children the interesting event is *one* row moving. A
   line-oriented mode that emits only what changed, appending rather than
   clearing, is readable in a log and in scrollback.

   **The existing redraw stays, and stays the default.** The append-only mode
   is opt-in — the maintainer's decision. Two modes, not a replacement.

2. **No incremental detail view.** `mnemo session <id>` prints once. Watching
   one child means re-running it, and every run re-reads a 256KB cold window
   because the CLI passes `read_tail(..., 0)` every time. `read_tail` already
   returns a new offset and `activities_for` already carries offsets across a
   loop — the plumbing landed in #210 and is unused on this path. A `--follow`
   that carries the bookmark is the smaller half of this piece.

   #210 ruled `--follow` out as a scope call, recorded in the spec. The issue
   revisits that: watching one child's actions accumulate is a thing the
   maintainer actually wanted. Read that spec before you decide the shape.

**Not a TUI.** #210 deliberately ruled out curses, rich and textual, and that
decision stands. The queue's value is being cheap and glanceable; a framework
is a large dependency for a command that prints under 20 lines. Do not add one.

The change signal already exists and is thrown away: `_activity` computes
`(+N)` from `Activity.since` and `↻` from `Activity.repeated`, both derived by
comparing against the previous window, and the renderer then prints a flat
table. Whatever you build, that comparison is available to you.

Also yours to judge: the fixed 2s interval (a twenty-minute dispatch gets 600
redraws of mostly-identical text), and whether the append mode belongs behind a
flag on `--watch` or as its own. Argue it from use, not from taste.

Run the full suite before you finish.
