---
feature: channels
created: 2026-09-15
verdict: parallel
---

The cross-session channels round. Spec:
`docs/superpowers/specs/2026-09-15-cross-session-channels.md` — read it first;
every number a piece needs to reproduce is there with its source file.

Four pieces fix a measured defect, three measure a channel that fires at or
near zero and propose what to do about it. The three investigation pieces must
land a measurement before they propose anything, and are free to propose
"remove" or "this is correct as designed" — a piece that assumes the answer has
failed its brief.

Boundaries hold across all seven: `core/briefing.py` belongs to
`briefing-telemetry` alone, `core/briefing_select.py` and
`hooks/session_start.py` to `briefing-query` alone,
`core/sessions/unblocks.py` to `unblocks-retire` alone, and
`core/dispatch.py` + `core/child_profile.py` to `child-briefing` alone.
Run the suite from the worktree with `PYTHONPATH=src` or it tests the main
checkout.

## unblocks-retire

- **files:** src/mnemo/core/sessions/unblocks.py, tests/unit/test_unblocks_retire.py
- **exposes:** `ConsumeReport.retired`
- **consumes:** nothing

Issue: the 27 markers in `.mnemo/session-queue.json` all carry `extracted=0`
and retry on every pass. 26 of 27 name a deleted worktree in `cwd`, so
`resolve_canonical_agent(cwd)` yields a project (`mnemo-wt-200`) that owns no
transcripts; `learn()` returns an error, and `consume()` counts every error as
`failed` (`unblocks.py:119-122`). The retirement branch at `unblocks.py:104-110`
only fires when `session_id` or `cwd` is *missing*, which these have, so the
list grows without bound — the unbounded retry #195 exists to prevent.

Distinguish *permanently* unresolvable from transiently failed. A marker whose
transcript can never be found again is retired and counted; a transient error
(a read that failed, a vault momentarily locked) still retries. The line
between the two is yours to draw and to defend in the PR body — name what you
keyed on and why it cannot misfire on a live session whose transcript exists.

Measure before and after against the real `~/mnemo` vault, not fixtures: report
how many of the 27 retire, how many still retry, and why each survivor did. The
errors the consumer currently swallows into its report should be observable
somewhere a human will find them.

## briefing-telemetry

- **files:** src/mnemo/core/briefing.py, src/mnemo/core/mcp/access_log.py, tests/unit/test_briefing_telemetry_perfile.py
- **exposes:** `record_briefing_read(vault_root, record) -> None`
- **consumes:** nothing

Issue: `pick_latest_briefing` (`core/briefing.py:275-307`) has no logging at
all, and `record_session_start_inject`
(`hooks/session_start.py:770-775`) stores only the boolean
`included_briefing`. 2168 of 2385 injections carried a briefing and there is no
way to tell which of the 368 on disk it was. The channel that fires 90.9% of
the time is the one channel mnemo cannot measure per item.

Make per-briefing consumption recoverable from disk. Fields, storage and
whether this extends the access log or opens a new one are yours to decide —
`mcp-access-log.jsonl` is already the home of `session_start.inject`, which
argues one way; a briefing read is not an MCP call, which argues the other.
Keep it cheap: this runs on every session start and must never fail one.

Do not touch `hooks/session_start.py` — it belongs to `briefing-query`, which
wires your recorder into the hook when it lands. Your job ends at a function
that piece can call.

Report, from the real vault, how many distinct briefings the existing 2168
injections could possibly have drawn from, and what the new record would have
captured over the last 7 days had it been in place.

## briefing-query

- **files:** src/mnemo/core/briefing_select.py, tests/unit/test_briefing_select.py, src/mnemo/hooks/session_start.py
- **exposes:** `briefing_select.pick(vault_root, agent_name, *, query) -> BriefingRecord | None`
- **consumes:** `record_briefing_read(vault_root, record) -> None` from `briefing-telemetry`

Issue: the briefing is the only injector in mnemo with no relevance step. It
takes the newest and pastes the body verbatim, at a median 7133 bytes
(~1783 tokens) — 95% of the whole envelope. The recall harness measures queried
primacy@5 at **72%** (n=25) against unqueried at **20%** (n=65)
(`.mnemo/recall-report.json`), and the briefing is 100% unqueried by
construction. Observed live on 2026-09-15: a session opened on product strategy
received 1783 tokens about OSC 7 and Higgsfield credits.

What a query even is at `SessionStart` is the first thing to settle and the
part most likely to sink this: the hook fires *before* the user's first prompt
on a cold start, so there may be no query at all. Resume and `--continue` are
different. Establish what is actually available at each entry point before
designing anything, and say so in the PR body — if the honest answer is that no
query exists at the moment the briefing is chosen, then the piece's finding is
that this cannot work as framed, and that is a valid outcome. Do not invent a
query the hook does not have.

If a query is available, selecting among the last N briefings, or selecting
sections within one, are both open; so is leaving the default untouched when
confidence is low. mnemo already has BM25 and a floor in
`core/reflex/decide.py` — reuse rather than grow a second ranker.

Measure against the real vault and report honestly: primacy of the chosen
briefing versus today's newest-wins, on whatever case set you can build from
`~/.claude/projects`. A result showing no improvement is publishable and
expected to be published.

You are the sole owner of `hooks/session_start.py` this round. Confine your
edit to the block that builds `briefing_block` (currently lines ~128-150) plus
the call that records the read, and leave the rest of that 799-line file alone.
`briefing-telemetry` lands first; call its recorder rather than writing a
second one.

## child-briefing

- **files:** src/mnemo/core/dispatch.py, src/mnemo/core/child_profile.py, tests/unit/test_child_briefing.py
- **exposes:** nothing
- **consumes:** nothing

Issue: all 20 `bots/mnemo-wt-*` namespaces hold `briefings=0`, and **0 of 2385
inject events carry a `-wt-` project name**. Children re-acquire the vault
through their own SessionStart, but no per-child handoff document is ever
written, and no briefing reaches them.

**Measure and decide before writing code.** This may be correct: `#225`'s
`resolve_canonical_agent` scoping deliberately lands a child's writes under the
canonical project so children see the parent's 78 topics rather than an empty
worktree namespace. A briefing written under `mnemo-wt-200` would die with the
worktree — which is exactly why `dispatch-parents.jsonl` lives in the vault and
not the tree. So the honest possible outcomes are: children should receive the
canonical project's briefing; children should write one that lands canonically;
both; or neither, and the current behaviour is right and should be documented
and tested so it is not "fixed" by a later session.

Whatever you conclude, the evidence is the deliverable: how many children ran,
what each could see at start, and what survived their worktree. Note that 9
rules in `learned.jsonl` *did* come from child sessions
(`mnemo-wt-158`, `-187`, `-195`, `-196`, `-c-label-column`), so the
child → vault path demonstrably works; this piece is about the briefing path
specifically.

Do not change `resolve_canonical_agent` or anything under `core/sessions/` —
that is #225's settled scoping and another piece's territory.

## investigate-shared

- **files:** docs/superpowers/specs/2026-09-15-shared-layer-status.md
- **exposes:** nothing
- **consumes:** nothing

`.mnemo-shared` has **never run**: no such tree exists in any repo under
`/Users/xyrlan/github`, and `.mnemo/share/imports.json` is absent. Both
`publish` and `import_rules` are registered
(`cli/commands/__init__.py:23,33`), so the #245 landing gap is closed and the
commands are reachable.

Find out why it never ran. Candidates worth separating: it needs a second
human and there is only one; it needs a step the docs never tell anyone to
take; it works and was simply never invoked; it breaks on first contact. Run
it end to end against a scratch repo and report what actually happens.

Deliver a spec documenting the finding and recommending one of: fix (naming
what), remove (naming the cost), or keep and document (naming where). Write
no production code — the recommendation is the deliverable, and the maintainer
decides.

## investigate-enrichment

- **files:** docs/superpowers/specs/2026-09-15-enrichment-status.md
- **exposes:** nothing
- **consumes:** nothing

Path enrichment has fired **2 times ever** (`.mnemo/enrichment-log.jsonl`, both
2026-09-14, project `clubinho`) while 655 rules carry `path_globs` and
`enrichment.enabled=true`. The log begins after the #271 fix, so 2 is the
post-fix total, not a historical artifact.

#271 fixed absolute `file_path` versus repo-relative globs. Determine whether 2
is the correct post-fix number for this vault's rules and this user's file
traffic, or whether something still does not match. Reproduce a real Read
against a real file that a real rule's glob should cover, and report what the
matcher actually saw — a grep proves a string is absent, never a behaviour.

Deliver a spec: fix (naming what), remove (naming the cost), or keep and
document (naming where). No production code.

## investigate-socket

- **files:** docs/superpowers/specs/2026-09-15-socket-reply-status.md
- **exposes:** nothing
- **consumes:** nothing

The inbox socket (`/tmp/cc-socks/<pid>.sock`) is the only live, non-vault
session → session path, and neither mnemo nor mnemo-desktop persists anything
about it. A reply reaches a running child; the child's receipt exists only
inside its own transcript; the count of replies ever sent is **unmeasurable**.
10 sockets were live on 2026-09-15.

Establish what is knowable. Is a sent reply recoverable after the fact from the
child's transcript, and by what marker? Would recording the send side be cheap,
and does it belong to mnemo or to mnemo-desktop (the desktop owns
`mission_reply`, `mission.rs:1353`)? Is the one-way shape a limitation of
Claude Code or of mnemo's use of it?

Deliver a spec with the finding and a recommendation. No production code.
