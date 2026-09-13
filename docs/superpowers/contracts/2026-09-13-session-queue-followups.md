---
feature: queue-followups
verdict: parallel
---

Two loose ends left by PR #210 (session activity view). They were deliberately
not fixed there: one is a pre-existing layout bug that touches every bucket of
the queue renderer, and the other is documentation that never existed. Neither
depends on the other, and they share no file.

## label-column

- **files:** src/mnemo/core/sessions/render.py, tests/unit/test_sessions_render.py
- **exposes:** nothing
- **consumes:** nothing

The queue renderer pads the session label to 22 columns with `{s.label:<22}`,
in all four buckets. Real labels do not fit: `jobs.py` caps `label` at 40
characters and the ones a maintainer actually sees run 30–34
(`"#197 dispatch a feature's pieces"` = 32,
`"#203 measure unblock edge coverage"` = 34). An f-string field width pads but
never truncates, so a long label overflows and shoves whatever follows it on
that line.

This predates PR #210 — `git show 3e2851b:src/mnemo/core/sessions/render.py`
has the same unbudgeted field, already shoving `detail`. What changed is that
it became visible: an activity string is longer and more structured than the
`—` that used to sit in that column, so the misalignment now reads as a broken
table rather than as ragged whitespace.

Reproduce it before changing anything, and keep the reproduction as a test.

The same file already solved this problem once, for the activity column:
`_activity` takes a `budget` and cuts its least important part (the target)
while reserving the parts that carry signal (`(+N)`, `↻`). `DETAIL_WIDTH` and
`MIN_TARGET` are the named constants it budgets against. Whatever this piece
does for `label` should be recognisable as the same idea by whoever reads both.

Two things the label is carrying, for judging what must survive a cut:
`Session.label` leads with the issue number when `cwd` yields one (`#197 …`,
recovered via `issue_for_cwd`), then the session's own inferred name. The
number is the identifier a maintainer is tracking across a dispatch; the
inferred title reads well and is the expendable half.

All four buckets use the same field. Whether they should all behave the same
way is a judgement for this piece — the waiting bucket's entire job is to be
readable, and it is the one whose line the `attach:` hint points at.

Verify against real data rather than fixtures alone. Existing test fixtures in
this repo use short names like `child` (5 chars), which is exactly why the
overflow survived until real labels went through the renderer. Nine real
dispatch-child transcripts live in
`~/.claude/projects/-Users-xyrlan-github-mnemo-wt-<issue>/*.jsonl`, and
`read_sessions(root=<tmp>)` will parse a hand-written `state.json` (camelCase
keys: `state`, `tempo`, `name`, `cwd`, `tokens`, `sessionId`, `linkScanPath`,
`updatedAt`) so a full queue can be rendered with real labels and real
activity. Show a before/after of the rendered table in your report.

`render_queue(sessions)` with no activities argument is byte-identical to
v1.4.0 today, asserted by `test_render_without_activities_is_unchanged`. If
your change alters that output, that test will tell you — decide deliberately
whether the guarantee still means what it says, and say what you concluded.

## sessions-docs

- **files:** README.md, docs/getting-started.md, docs/troubleshooting.md
- **exposes:** nothing
- **consumes:** nothing

`mnemo sessions` has no user-facing documentation and never has. Neither does
`mnemo session <short_id>`, added in PR #210. A maintainer running six parallel
children has no written explanation of the one command built to tell them which
child needs attention.

What exists to document (read the code and the CHANGELOG's Unreleased section
rather than trusting this summary):

- `mnemo sessions` — the queue, four buckets, ordering deliberate: waiting
  first and newest-first within it, because a blocked session's `tempo` freezes
  when its process stops writing, so the stalest entry is the likeliest corpse.
  Flags: `--json`, `--watch` (2s redraw), `--all`, `--consume-unblocks`.
- The activity column: last tool, its target, `(+N)` tool uses since, `↻` when
  the same tool and target come back. The distinction it exists to draw is
  three-way — a rising count with a changing target is progress, a frozen count
  is stalled, a rising count on an unchanged target is a loop.
- `mnemo session <short_id>` — that session's recent actions, oldest first,
  with timestamps. `--limit` (default 15). A unique prefix of the short id is
  enough; an ambiguous one refuses rather than guessing. No `--follow`:
  `claude attach` is how you get inside.
- Both are human-only by design: no hook and no MCP tool exposes either,
  because the parent session's context is the scarce resource the feature
  exists to protect.

Match the voice of the surrounding documentation, which explains *why* a thing
behaves as it does rather than listing flags. Where the existing docs are
organised around a task the reader is trying to accomplish, follow that.

Whether this belongs in README, in `docs/getting-started.md`, in a new
`docs/sessions.md`, or spread across them is yours to decide — read what is
there and put it where a reader would look. A new file under `docs/` is within
your boundary if that is the right answer; say why you chose what you chose.

Run the full suite before you finish: `tests/unit/test_release_workflow.py`
asserts on repository files, and at least one test in this repo has historically
asserted on documentation content, so a docs-only change is not automatically
test-neutral.
