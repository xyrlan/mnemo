# Unblock edge coverage — measurement (#176)

**Date:** 2026-09-12
**Script:** `tools/measure_unblock_edges.py` (read-only; no LLM calls, no writes)
**Question:** how often is a `blocked -> active` edge missed *entirely*?

## Why this is the only question left

#176 was re-scoped twice.

- **Option 1 landed** (PR #191). `session_end` now really calls
  `detector.sweep`. Before that the sweep had exactly one caller
  (`cli/commands/sessions.py`), so on a machine where nobody typed
  `mnemo sessions` the detector never ran at all.
- **The consumer landed** (PR #199, #195), and its measurement removed most of
  the case for **option 3** (`linkScanPath`/`linkScanOffset` diffing). The
  consumer runs `core.learn`, which re-reads the transcript from disk, so
  redeeming a marker minutes later sees *more* of the session than racing the
  ~10s edge. Not racing was option 3's entire advantage.
- **Option 2** (statusline sweep) was ruled out for latency and is not
  considered here.

A marker redeemed late is therefore fine. A marker that was **never written**
is not: nothing downstream can recover what was never recorded. That is the
narrow question this measures.

## Method

Ground truth for "an edge happened" cannot come from:

- the detector's own state file — that is the thing under test;
- `timeline.jsonl` — it records `state` transitions and never mentions
  `tempo` (confirmed again here: 14 rows across the 4 live job dirs, all
  `state=working`, no `tempo` key at all);
- `atis-latch` records in the transcript — `atis` is an opaque token.

It comes from the **transcripts**. A background session sits at
`tempo=blocked` while it waits on the maintainer and flips to `active` when
their reply lands. So every genuine human prompt that is not the session's
opening task is one `blocked -> active` edge, and the assistant turn before it
dates the moment the session went blocked. Synthetic `type: user` records
(task notifications, slash-command expansions, skill preambles, interrupt
markers, tool results) are excluded — none is a human unblocking anything.

Scope is `sessionKind: bg` only. The detector reads `~/.claude/jobs`, which
only background sessions populate, so a foreground session could never produce
a marker and including it would measure a gap the feature does not claim.

Triggers that exist today, against which the edges are checked:

| trigger | dated by |
| --- | --- |
| `session_end` hook | mtime of the per-session briefing under `bots/*/briefings/sessions/` |
| `mnemo sessions` typed by hand | **leaves no trace** |

An edge is *caught* only if a trigger fires inside its window: after the flip
to `active` and before the flip back, measured at ~10s on a real dispatch. A
sweep during the blocked stretch records `last_tempo=blocked` — it *arms* the
detector, it does not record the edge.

Because hand-typed `mnemo sessions` leaves no record, the `session_end`-only
rate is reported as a **floor**.

## Results

Corpus: 209 real transcripts, 31 background sessions, 337 recorded
`session_end` firings.

```
real blocked->active edges:     124   (across 18 sessions)
caught by session_end:            0
missed entirely:                124
caught rate (floor):            0.0%
```

(123 edges / 17 sessions when first run; a real edge landed mid-analysis — see
"the one marker in history" below — and the script picked it up, which is a
small live check that the reconstruction tracks reality.)

### The 0 is not an artefact of the 10s window

| window | caught | share |
| --- | --- | --- |
| 10s (real) | 0 | 0.0% |
| 60s | 0 | 0.0% |
| 300s | 1 | 0.8% |
| 900s | 5 | 4.1% |
| 3600s | 6 | 4.9% |
| 86400s | 22 | 17.9% |

Even at a **full day** of grace — 8,640× the real window — 82% of edges still
see no `session_end` firing. The nearest `session_end` to an edge has a median
distance of **~12 days**; the closest one ever recorded is 43s, still outside
the window. Only 2 of the 7 days carrying edges had any `session_end` firing
at all.

### Root cause: the trigger structurally excludes itself

`session_end` fires when a session *stops* — strictly after every edge that
session produced. **A session can never catch its own edges.** Catching one
requires an *unrelated* session to end inside that particular 10-second
window. All 123/123 edges are in this position.

This is a structural property of the trigger, not a frequency that better luck
or a wider window would improve. `hooks/session_end.py:_maybe_sweep_sessions`
predicted it in its own docstring:

> This is a second trigger, not a fix for the cadence: the hook fires when
> *this* session ends, and an edge that opens and closes inside another
> session's lifetime is still missed.

### Independent corroboration from live production state

`~/mnemo/.mnemo/session-queue.json`, with PR #191's trigger and PR #199's
consumer both in place, holds **1 marker across its entire history** against
124 reconstructed edges. One tracked session (`331745e2`) alone has 19 real
edges and recorded none. Two independent methods — transcript reconstruction
and the detector's own live state — agree.

### The one marker in history, and what caught it

That single marker was written **during this measurement** (`9293fe7b`,
`22:49:08Z`, the #200 child answered via `SendMessage`). It is worth being
precise about which trigger caught it, because it looks at first like a
counterexample:

- no briefing was written anywhere in that window, so **`session_end` did not
  fire** and did not catch it;
- a *different* Claude session was running a hand-rolled
  `mnemo sessions --all --json` poll loop on a 3-second sleep, started
  `19:43:35` local and still alive when the edge landed `19:49:08`.

So the only unblock ever recorded was caught by an **ad-hoc polling loop an
agent happened to build for an unrelated purpose** — not by either shipped
trigger. It confirms the detector works correctly when something samples it
often enough, and confirms that nothing in the shipped product does.

## Verdict

**The current triggers are not sufficient in practice.** The measured floor is
0/124, the ceiling under absurdly generous timing is 18%, the detector has
written exactly 1 marker in its lifetime, and that one was caught by an
accidental polling loop rather than by either shipped trigger. The cause is
structural rather than statistical: the only automatic trigger cannot observe
the session it fires for.

This does **not** revive option 3. Option 3's justification was not racing the
edge, and #199 already established that the consumer does not race — it
re-reads the transcript from disk. What the measurement shows is narrower and
different: the problem is not *when* the edge is read, it is that **`tempo` is
sampled at all**. A sampled signal needs the sampler to run while the signal is
up; the edge is up for ~10s, and no trigger is tied to the event.

The honest framing for whatever comes next is therefore: the recording
mechanism is the wrong *shape*, not the wrong cadence. Two facts pin this down
and they point the same way:

- a 3-second poll **did** catch an edge, so sampling works — if you sample
  continuously, which means a daemon, which the detector's docstring explicitly
  rules out ("No daemon");
- every shipped trigger is tied to an event **uncorrelated with the edge**
  (a session ending, a human typing a command), and no amount of tuning makes
  an uncorrelated sampler reliable.

So the choice is not "tighter cadence vs. option 3". It is: either something
polls continuously (rejected by design), or the edge is derived from a record
that **outlives it** instead of being sampled while it is up. `linkScanOffset`
is the latter — not because it avoids racing (#199 settled that racing does not
matter), but because it is a *bookmark* rather than a *sample*, and a bookmark
can be read at any time afterwards.

That is a design question and deliberately not decided here. Note only that it
resurrects option 3's *mechanism* for a completely different reason than option
3's original *justification*, which this measurement leaves dead.

## Reproducing

```sh
PYTHONPATH=src python3 tools/measure_unblock_edges.py
PYTHONPATH=src python3 tools/measure_unblock_edges.py --json
```

Numbers move as transcripts accumulate; the structural claim does not.
