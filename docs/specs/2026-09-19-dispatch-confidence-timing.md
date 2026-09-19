# A child's confidence in its issue — measurement and refusal (#383)

**Date:** 2026-09-19
**Script:** `tools/measure_refusal_timing.py` (read-only; no LLM calls, no writes)
**Question:** #383 proposed that a dispatch child rate its confidence in the
issue *before* paying for the exploration, so a low rating comments and stops
instead of building. Its own "Done means" made that conditional: *look at past
dispatches first — of the children that refused, blocked or were re-scoped,
how many showed it within their first N tool uses? If early signal does not
exist in the transcripts, say so and ship nothing.*

**Answer: it does not exist, and the gate is refused.** The doubt that makes a
mnemo refusal worth having is the *product* of the exploration, not a
precondition of it. Nothing is added to the child's prompt. This document and
the script are what shipped.

## Population

Every transcript on disk whose `cwd` parses as a dispatch worktree
(`dispatch.issue_for_cwd`: `<repo>-wt-<issue>` or `<repo>-wt-c-<piece>`),
minus pytest's live trees — the same population `tools/measure_exploration.py`
uses. On 2026-09-19: **182 children, 180 of which ran at least one tool**,
across `mnemo`, `mnemo-desktop`, `clubinho` and `clearframe`.

**33 of them record a refusal, a correction of the issue's premise, or a
re-scope** in their own closing text (22 by a `What I refused` section naming
something, 8 by a contradicted premise, 3 by a re-scope). The classifier reads
what the child wrote, not whether a PR exists: a child that shipped a PR *and*
refused one bullet is in the population, because that bullet is what a
confidence rating would have had to catch.

## When the doubt appears

`doubt_at` is how many tool uses had run when the doubt first appears in the
child's own words. The closing report is written after the last tool use, so
`doubt_at == tools` means "said only at the end".

| | count |
|---|---|
| refusals in the population | 33 |
| doubt stated within the first **3** tool uses | **3** |
| doubt stated within the first **5** | **4** |
| doubt stated within the first **10** | **4** |
| doubt stated before the child's first mutation | 6 |
| doubt stated **only in the closing report** | 20 |
| median tool uses over these children | 44 |
| median tool uses before their first mutation | 17.5 |

### The four early cases are the ones that need no gate

Read by hand, all four are genuine — and three of them are the issue
re-scoping *itself*, read straight out of the child's first command:

- `clubinho-wt-181` @2 — "Items 1–3 already shipped." Tool use 0 was
  `gh issue view 181 --comments`.
- `clubinho-wt-192` @1 — "Comment re-scopes it." Tool use 0 was
  `gh issue view 192 --comments`.
- `mnemo-wt-158` @1 — "Issue re-scoped by comments." Same.
- `mnemo-wt-273` @4 — "the issue claims 'several' but that's the load-bearing
  assumption." The child then spent **37 more tool uses measuring it** before
  it could say anything a maintainer could act on.

The first three cost nothing and are already covered: `_PROMPT` opens with
*"Read the full issue with `gh issue view {issue}` — including its comments,
which often re-scope it."* Those children read the comments, re-scoped, and
built the right thing. A gate at tool use 3 fires on the cases that are
already handled and saves nothing. The fourth is the opposite failure: a gate
there would have stopped the child *before* the measurement that made its
refusal worth reading.

### The other 29 could not have rated anything

The valuable refusals are all measurements, and they are dated where the
measurement finished, not where the issue was read:

- `mnemo-wt-244` @63 of 63 — "Premise half wrong" rests on *100/100 demoted
  feedback pages checked against their source briefings*.
- `mnemo-wt-333` @70 of 70 — the refusal of the carried hill-climber is the
  sweep that produced it.
- `mnemo-wt-274` @100 of 122 — "0 of 153 backticked identifiers" is the
  finding **and** the refusal.
- `mnemo-wt-304` @30 of 30 — "the polluted text never reaches `learn()`"
  needed the call graph read.
- `clubinho-wt-213` @29 of 29 — **the only child in 180 that refused without
  touching the tree at all.** Its argument ends at `PaymentsCard.tsx:363`:
  the renewal link cannot charge because the card is always typed at
  checkout. That fact is 29 tool uses deep. A rating at tool use 3 would have
  said "confident" and been wrong, or "unsure" and thrown the finding away.

**20 of 33 said nothing at all until the report.** Children in auto mode
narrate almost nothing mid-run — `clubinho-wt-213` has exactly one assistant
text block in the whole transcript. Note the direction of that bias:
`doubt_at` is an *upper bound* on when the child could have spoken, so this
measurement can only make early signal look more common than it is. It still
comes out at 4 of 33.

## The other exit: nobody is asked, and nobody answers

#383 also proposed that low confidence "asks instead of building". Over the
180 children, **32 ever received a second human turn**, the earliest after 10
tool uses. What actually arrived:

| kind | count |
|---|---|
| a peer session's message (`Another Claude session sent a message`) | 18 |
| a pasted image | 5 |
| Claude Code's own `Continue from where you left off` | 8 |
| **typed by the maintainer** | **1** |

The single typed turn is an instruction pushed at tool use 75 ("check the
tauri dev log"), not an answer to a question. **Zero of 180 dispatch children
have ever been unblocked by a maintainer answering a question they asked.**
That is `child-completion-has-no-channel` (#357) seen from the child's side:
the ask has no reader. A rating whose low branch is "ask" would route into
that silence.

## Why the gate cannot be built as specified

#383 set two constraints that cannot both hold at tool use ~3:

- *"The rating must be backed by something checkable in the transcript (what
  was read before it was given), or it is another unbacked claim (#306)."*
- *"It must not add a round-trip that blocks a confident child."*

At tool use 3 the only thing a child has read is the issue's own text. A
rating backed by the issue's prose is a rating of the prose, not of the code —
exactly the unbacked claim #306 is about. To back it against the code, the
child must read the code, which is the exploration the rating was meant to
avoid paying for.

The repo has shipped this shape twice and regretted it both times: the reflex
calibrator wrote configs nothing read for six versions
(`reflex-calibration-was-dead-wire`), and #304's stored `answer` had no reader
at all. A confidence field that is almost always "high" is a third.

## What already covers the ground

- **A measured refusal is already licensed**, in the one message the child
  reads as the maintainer's own: *"If the issue proposes a fix that the code
  shows to be wrong, say so and refuse it — a measured refusal is a better
  outcome than a faithful implementation of a bad plan."* 33 of 180 children
  took it.
- **The comments are already read first**, which is where every cheap
  re-scope in the corpus came from.
- **`--read-only` (#371) is the investigate-and-comment posture**, and it is
  chosen at dispatch time by the maintainer — who is the one who actually
  holds doubt about the issue, because they wrote it. That is the right end of
  the wire for this decision, and it is already built.

## Re-running

```sh
PYTHONPATH=src python3 tools/measure_refusal_timing.py           # the counts
PYTHONPATH=src python3 tools/measure_refusal_timing.py --list    # every refusal + the sentence matched
PYTHONPATH=src python3 tools/measure_refusal_timing.py --json    # per-child rows
```

Read `--list` before trusting a count. Both classifiers work from text: of the
13 refusals carrying a mid-run doubt hit, one is wrong (a pluralisation helper
that "already exists", unrelated to the issue) and one is the child correcting
its own earlier reasoning rather than the issue's. Neither is in the `N<=10`
band, so the number the decision rests on survives the read intact.

The corpus grows with every dispatch. If a later run shows early doubt rising
into double digits — particularly children that stated it before their first
mutation and were *right* — this refusal is the thing to revisit.
