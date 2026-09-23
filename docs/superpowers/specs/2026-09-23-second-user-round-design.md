# The round before 2.0: a vault that drains itself, and a second user from zero

**Date:** 2026-09-23
**Status:** draft; the maintainer approved the direction, the open questions below are theirs
**Related:** #429 (inbox expiry), #417/#425 (reference gate judge), #432 (demotions judged), #155 (desktop onboarding), #439 (closed: the child-level A/B will not run)

## Why this round, and not another round of tools

Of the four things a 2.0 was held to on 2026-09-22, one is answered and one has
not moved:

| | Status 2026-09-23 |
|---|---|
| The vault changes the work | Answered at rule level: an on-point injected rule lifts follow rate +31.1 pp [+19.7, +42.6] (#434). The judge at `injectAt` 0.4 is now the shipped path to the prompt (#461). |
| Issue to merge without leaving the app | Barely moved. Today's merges still went through a terminal. |
| CLI and desktop are one product | Moved a little (#452, #168). Unsigned builds and #155 remain. |
| Someone other than the maintainer uses it | Not moved. |

Every number that says mnemo works was measured on one vault: the maintainer's,
with ~1,900 live pages. Two things stand between that and a product: the intake
that feeds the vault still waits on one person, and nobody has started from an
empty vault. This round addresses both. Round 20 polish, #158, #401 and the
Codex/Cursor adapters wait.

## Front A: the inbox drains itself

### What is in it (measured 2026-09-23)

`mnemo inbox --stats`: **181 staged, oldest 20 days**. Last 7 days: 48 offered at
session start, **0 promoted**, 101 dropped. The 101 were the 09-22 audit's bulk
act, not organic decisions. `--stats` counts the 181 plain staged pages. There
are another 61 `.proposed.md` files next to them, which belong to the
`mnemo rewrites` flow. By staging reason (frontmatter of every `.md` under
`shared/_inbox/`):

| Reason | Pages | Fate today |
|---|---|---|
| Reference gate `generic` / `narrative` (G/N) | 97 | Expire after 14 days, undoable (#429) |
| Demoted from feedback, gate says `system` / `technique` (S/T) | 68 | **Wait for a human indefinitely** |
| Plain feedback, no stamp | 9 | Wait for a human (oldest, 21 days) |
| Plain reference, no gate stamp (2 of them demoted) | 7 | Wait for a human |
| *`.proposed.md` rewrites (`proposals/` 30, `project/` 12, `reference/` 11, `feedback/` 4, `rejected-*` 4)* | *61* | *Wait for a human; not counted by `--stats`* |

The S/T row is the backlog summaries have been calling "the inbox". Its fate
does not match the policy for the same verdict elsewhere. A reference page the
extractor emits directly and the gate calls S/T **goes live** (#425). A page
the extractor first called feedback, whose quote was not found in the user's
turns (the evidence gate, `extract/evidence.py`), was demoted to reference. If
the gate then calls it S/T, it **stays staged**. The judge gives both the same
verdict, and they end up in different places. #432 judged these pages and
stamped them, but promoting them was deliberately left out (on 2026-09-22,
"provenance unverified").

### A1. Demoted pages judged S/T go live as reference, if a blind sample holds

The provenance worry is about the *feedback* claim ("the user said this"). A
demoted page promoted as `reference`, with `demoted_from: feedback` kept and no
`verified` confidence, makes no such claim. It is exactly what a directly
emitted S/T reference page already is.

**Measure before building.** Take a uniform sample of 40 of the 68 and have it
blind-labelled good/junk by the #425 rubric, with two raters as in the 09-22
audit. **Declare the bar now: promote automatically only if ≥ 85% of the
sample is good under both raters.** For comparison, the gate's S/T verdicts on
the audit's held-out test let 1 of 31 junk pages through while keeping 38 of 41
good ones. Demoted pages may be a harder population. That is the reason to
sample. If the bar fails, A1 stops and the finding says why.

If it passes: promotion is automatic at extraction time for new pages, and one
`mnemo inbox --promote-judged` sweep handles the existing ones. Every promotion
is recorded in `inbox-offers.jsonl` with its reason, so it can be undone with
`--restore` and drain stays measurable.

### A2. What the rewrite proposals are, before deciding anything about them

61 `.proposed.md` files are proposed rewrites of live pages or new project
pages, up to 11 days old, four of them in leftover `rejected-*` directories
from 2026-09-12. Nobody has characterised them. **A read-only child** reads them and
answers:
- which command produced each;
- what fraction would change a live page's meaning, and what fraction are
  timestamp or wording churn;
- whether the churn has one cause (compare #376, run-id collisions).

No design for them until that answer exists.

### Success for front A

For 14 days after A1 ships, **no page the judge has stamped waits for a human.**
The only staged pages are rewrite proposals, and their count is known. This is
measured with `mnemo inbox --stats` plus a per-reason count added to it.

## Front B: a second user, from an empty vault

The second user is **not named yet**. The clubinho repository's owner does not
program, so nobody on that repository besides the maintainer is a candidate.
The first thing that has to be known does not depend on who it is: **what a
developer gets from mnemo on day one, and how long the vault takes to start
carrying anything.** This is unmeasured. Two sources exist in the code, and
neither has run on an empty vault in a real HOME:

1. **Their own history.** After install, SessionStart spawns a one-time, capped
   `mnemo backfill --install-run` over the current repo's past transcripts
   (`hooks/session_start.py:329`). Someone who already uses Claude Code starts
   with whatever that cap harvests.
2. **Their own sessions from then on.** Someone new to Claude Code starts
   empty, and the vault grows one SessionEnd extraction at a time. The reflex
   floor scales with vault size (`floorReferenceDocs`, the 09-03 cold-start
   fix), so a small vault can inject, but how often it does has never been
   measured.

Team rules (`mnemo publish` / `import`, #245) are a third source, but only for
a repository with a second developer. Nothing is published this round (the
maintainer's call, 2026-09-23).

### B1. Time to first value, replayed

A child builds `tools/measure_day_one.py`. It takes an isolated `HOME`, runs
`mnemo init --yes`, and uses the maintainer's own clubinho transcripts as
someone else's history. Two arms:

- **(a) With history.** Run the install backfill exactly as it runs after
  install (same cap), then replay the next 50 real prompts.
- **(b) Without history.** Feed the sessions in chronological order, as if they
  happened one at a time after install, and record after each session: live
  pages, whether the next SessionStart briefing carries anything, and the
  reflex's emit rate and on-point rate (the #411 rubric) on that session's
  prompts. The judge is off, because a new user has no key.

The headline number is the session at which (b) first injects an on-point
rule, and what (a) carries on its first session. Report the model calls each
arm cost. Unit tests over synthetic transcripts, per repo practice.

If both arms are near-silent for the first days, that finding matters most in
this round, and B2 waits for a fix to it.

### B2. Install from zero

Until a second developer is named, the maintainer does the install
themselves, on a clean account or machine. The Windows machine #155 is already
waiting on is the obvious one. The run follows only the README and the desktop
onboarding, with no vault and no mnemo on `PATH`. Known pieces:
- desktop #155: onboarding finds or installs `claude` and `mnemo`. It shipped in
  0.1.2 and was never tried on a clean machine;
- a signed build for that platform: Windows SmartScreen reputation, or macOS
  notarization;
- one README path that starts from nothing.

Every step that needs knowledge not in the README or the app is a bug in B2.
When a second developer exists, they repeat the same run on their own machine,
and the maintainer does not type on it.

### Success for front B

- **Clean install:** from nothing to a first Claude Code session with mnemo
  hooks active, following only the README and the app, with the terminal never
  opened for anything the app did not ask for.
- **Day one:** B1 names the session at which a new vault first carries an
  on-point rule, and the number is small enough to state in the README.
- **A second developer**, when there is one: their first real session starts
  with a briefing, gets at least one on-point rule injected within their first
  ten prompts (counted in their own vault's logs), and after a week they are
  still using it without being reminded.

## Order

1. A1's blind sample, A2 (read-only) and B1 run in parallel: all three are
   measurements.
2. A1 builds only if its bar holds.
3. B2's clean install starts when B1 shows day one carries something.

## Open questions (the maintainer's)

1. **Who is the second developer?** Not needed to start. Needed for the last
   success line of front B.
2. **A1's bar.** 85% good under both raters, declared before sampling. It
   stands unless the maintainer changes it before the sample is labelled.

## Non-goals

- A1 does not promote anything as `feedback` or `verified`. The evidence gate
  is untouched.
- No accounts, no relay, no live presence (team layer 3 waits for demand).
- No new recall ranking work (#158) and no Jev pool widening in this round.
