# Session activity — seeing what a dispatched child is doing

**Date:** 2026-09-13
**Status:** design approved, not implemented
**Issue:** none yet (file after spec review)

## Problem

`mnemo dispatch` is fire-and-forget. You see PRs at the end; you see nothing in
between. `mnemo sessions` answers two questions — *is it alive* (`live`, pid
probe) and *does it need me* (`tempo`) — but not *is it progressing*.

A child sitting at `active` for twenty minutes is indistinguishable between
three situations that call for three different responses:

| Situation | What you should do |
|---|---|
| Making progress | Leave it alone |
| Stuck on one thing | `claude attach` and unstick it |
| Looping | Kill it |

Today all three render identically. That is the gap this design closes.

### What was considered and rejected

Four framings were on the table:

1. **See live activity** (read-only) — chosen.
2. **Read the history after the fact** — falls out of (1) for free; same parser
   without the follow.
3. **Real two-way attach** — rejected. That is Claude Code's job
   (`claude attach <short_id>`), already printed by dispatch
   (`cli/commands/dispatch.py:120-137`). mnemo's contribution is telling you
   *which* session is worth entering, not replacing attach. Also sits on top of
   #196 (queue has no liveness check, zombies own the attach hint).
4. **Persist the dispatch split plan** — rejected as shallow. When dispatching
   by issue, mnemo decides nothing — the maintainer passes issue numbers on
   argv (`cli/commands/dispatch.py:53`). When dispatching by contract, the
   decision lives in a hand-written `.md` and `Contract.path` already records
   it (`contracts.py:85-92`). And a plan is static: it answers "how was this
   cut" once. The complaint is present-continuous. The split becomes visible
   anyway as a side effect of (1), since each line is labelled by issue.

## The asset this design exploits

`state.json` already hands over `linkScanPath` — the absolute path to the
child's `.jsonl` transcript. `jobs.py:133` already parses it into
`Session.link_scan_path`. Nothing in mnemo ever opens it:
`detector.py:110` forwards it into an unblock marker, and `unblocks.py:113`
ignores it and re-resolves via `learn(cwd=, session_id=)`.

The pointer into the live session is already in hand. This design opens it.

The transcripts are append-only (one JSON object per line, by Claude Code's
design), so they are structurally tailable. No incremental reader exists —
every current reader loads the whole file (`learn.py:128`,
`discover.py:104`).

`linkScanOffset` is named in docs as the bookmark mechanism
(`detector.py:37-40`) but never parsed. This design does not revive it; see
"Offset lifetime" below.

## Measurement that drove the design

Measured on disk 2026-09-13: 881 transcripts, 570.3 MB total.

| Population | p50 | p90 | p99 | max |
|---|---|---|---|---|
| All transcripts (n=881) | 327 KB | 1.3 MB | 6.2 MB | 18.1 MB |

Worktree sessions specifically (the closest available proxy for a dispatched
child — a session running a whole task alone, uninterrupted):

| Worktree dir | n | p50 | max |
|---|---|---|---|
| clearframe / cavebot-andares | 4 | 3.3 MB | 7.4 MB |
| clearframe / overlay-vivo | 3 | 1.8 MB | 3.3 MB |
| meunu / fix-select-scroll-bairro | 2 | 2.9 MB | 6.1 MB |
| clearframe / scripts-funcoes-panel | 2 | 990 KB | 1.2 MB |
| clubinho / 129-legado-client | 1 | 3.6 MB | 3.6 MB |
| mnemo / feat-pr-f-hosts | 1 | 1.6 MB | 1.6 MB |

**Proxy caveat, stated plainly:** these are `--claude-worktrees-<branch>`
directories — native Claude Code worktrees. No `mnemo dispatch` child has left
a transcript on this machine yet; the dispatch convention is
`<repo>-wt-<issue>` (`dispatch.py:106-122`) and zero such directories exist
under `~/.claude/projects`. PRs #194/#191/#192 came out of native worktrees.
So the table above is the best available proxy, **not** a measurement of
dispatch children. It is used only to establish an order of magnitude.

**What it settles:** worktree children run 5–10x the global median. Re-reading
every file on every tick would mean ~10 MB of JSON parsed every 2 s for four
children, to render four lines. That rejects the stateless "read it all each
tick" approach on measured cost, not on taste.

## Architecture

Three new units. None reaches inside an existing one.

### `core/activity/tail.py`

Incremental reader. Bytes in, events out. Knows nothing about sessions or
activity.

```
read_tail(path: str, offset: int, window: int = 262144)
    -> tuple[list[dict], int]
```

- `offset == 0` (cold start): `seek(max(0, size - window))`, discard the first
  partial line, parse the rest.
- `offset > 0`: `seek(offset)`, parse forward.
- Returns the offset of the last complete `\n` seen. A trailing line without
  `\n` is discarded and does not advance the offset — the child may be
  mid-write.
- Invalid JSON on a line: drop that line, keep going.

### `core/activity/summarize.py`

Pure distiller. `list[dict]` in, `Activity` out. No I/O.

```
@dataclass
class Activity:
    tool: str | None          # "Edit", "Bash", "Grep"
    target: str | None        # "dispatch.py", "pytest tests/unit"
    since: int                # tool_use blocks seen after the last one
    at: str | None            # timestamp of the last tool_use
    repeated: bool            # last tool+target seen >1x consecutively
```

`summarize(events) -> Activity | None` reads **raw** event dicts and walks
`message.content` for `tool_use` blocks, taking `name` and a short target from
`input`.

**It does not use `flatten_transcript_events`.** That function returns a
flattened `str` (`transcript.py:16-57`), rendering tool uses as
`[tool_use: Bash]` and truncating `tool_result` at 400 chars — it discards the
tool input, so the edited filename and the timestamps are gone. It exists to
build the briefing prompt; it is the wrong input for this. Raw blocks only.

### `core/activity/__init__.py`

Joins the two: given a `Session` and an offset, reads
`session.link_scan_path` and returns `(Activity | None, new_offset)`.
Returns `None` when there is no `link_scan_path`, the file is gone, or nothing
new arrived.

### Changes to existing code

| File | Change | Compatibility |
|---|---|---|
| `core/sessions/render.py` | `render_queue` takes an optional `dict[str, Activity]` and appends a suffix to the line | Called without it, output is byte-identical to today |
| `cli/commands/sessions.py` | `--watch` holds the offset dict in memory across ticks; single invocation passes `{}` | Single invocation reads the tail window, not the whole file |
| `cli/commands/session.py` | **new** — layer 2 | new command |

The boundary that matters: `tail.py` knows nothing of sessions,
`summarize.py` knows nothing of files. Each is testable alone.

## Two layers

### Layer 1 — `mnemo sessions` / `--watch`

```
state.json ──► jobs.load() ──► [Session]              (exists)
                                  │
                                  │ session.link_scan_path   (exists, jobs.py:133)
                                  ▼
                        read_tail(path, offset) ──► (events, new_offset)
                                  │                        │
                                  │                        └──► offsets[short_id]  (memory, --watch only)
                                  ▼
                          summarize(events) ──► Activity
                                  ▼
                      render_queue(sessions, activities)
```

Rendered inside the buckets that already exist (`render.py:94-117`):

```
TRABALHANDO
  #197 dispatch-child     editando dispatch.py (+3)
  #201 sessions-render    pytest: 468 passed · 2min
  #203 measure-edges      grep linkScanOffset (+7)      ← loop
```

`(+N)` on a repeated tool is the loop signal. A timestamp with no counter is
the stalled signal.

### Layer 2 — `mnemo session <short_id>`

Same `read_tail`, offset 0, `summarize` in list mode. Last N actions,
default 15, `--limit` to change.

```
#203 measure-edges   active · live · 18min
/Users/xyrlan/github/mnemo-wt-203

  14:02:11  Grep      linkScanOffset          3 matches
  14:02:19  Read      detector.py:20-60
  14:03:02  Edit      detector.py             +12 −3
  14:03:40  Bash      pytest tests/unit       468 passed
  14:04:15  Grep      linkScanOffset          3 matches   ← repeated
```

No `--follow` in this version. You look, you decide, you go to
`claude attach` if you want in.

If the 256 KB window holds fewer than N actions (a few fat `tool_result`
blocks can do that), layer 2 prints what it has and says how many. It does not
widen the window and re-read — degrading beats parsing 3 MB.

## Offset lifetime

Offsets live **in memory only**, for the life of a `--watch` loop. Nothing is
written to disk.

Rationale: an offset only has value inside a live `--watch`. Between separate
invocations of `mnemo sessions` what you want is current state, not the delta
since yesterday. Keeping it in memory avoids the sidecar-race argument that
`dispatch.py:28-35` makes against per-`short_id` files, and avoids touching
`session-queue.json`, which owns tempo history and unblocks
(`detector.py:85-127`) and should not also be a read cache.

Losing an offset costs one window re-read. That is the whole downside.

## Error handling

Every failure degrades to `None`. Nothing propagates.

| Situation | Behavior |
|---|---|
| `linkScanPath` absent from state.json | `Activity = None`, line renders as today |
| File missing or deleted | `None` |
| Permission denied | `None` |
| Invalid JSON on a line | Drop the line, continue |
| File shrank (`size < offset`) | Reset offset to 0, re-read window |
| Trailing line without `\n` | Discard it, do not advance offset |
| Zero new events | Keep previous `Activity`, refresh only the `· Xmin` |

The shrink case matters: if Claude Code rotates or truncates a transcript, a
stale offset points into the wrong place and would parse garbage.
`size < offset` is the detection.

## Relationship to liveness

Activity and liveness are orthogonal and stay that way.

- `live` (from `liveness.py:43-70`, pid probe via `roster.json`) remains the
  sole authority on *does this process exist*.
- `Activity` answers *is it progressing*.

A zombie can have recent activity in its transcript; a healthy child can have
none (thinking, or writing a long message). This design neither fixes nor
depends on #196.

## Testing

**`tail.py`** — temp file that grows: read, append, read again, assert only the
delta came back. Partial line: write without `\n`, assert the offset held;
complete the line, assert it arrives. Truncation: write, read, truncate,
assert the offset reset. Cold start: file larger than the window, assert the
first partial line is dropped.

**`summarize.py`** — pure, fixed list of dicts. Cases: single tool; tool plus
3 tools after; same tool+target repeated (loop → `repeated=True`);
assistant text with no tool; empty list; malformed event (non-dict, missing
`message`, `content` as `str`).

**`render.py`** — `render_queue` with and without the activities dict. The
without-case must match today's output byte for byte. That is the
backward-compatibility test.

**`cli/commands/session.py`** — nonexistent session; session with no
`link_scan_path`; `--limit` respected; window holding fewer than N actions.

**Integration** — `--watch` across two ticks, asserting the second tick did
not re-read from zero.

## Out of scope

Stated explicitly so the plan does not drift into them:

- `--follow` on layer 2
- Persisting offsets to disk (or reviving `linkScanOffset`)
- A dispatch plan artifact
- Anything about liveness or #196
- A TUI framework (curses/rich/textual) — `--watch` stays
  `print("\033[2J\033[H")` + `sleep(2)` (`cli/commands/sessions.py:85-90`)

## Open question for review

The 256 KB window is reasoned, not measured: it needs to hold ~15 actions, and
transcript bytes are dominated by `tool_result` payloads, few of which exceed
10 KB. It is ~7% of the median worktree child. If layer 2 routinely comes up
short on real dispatch children, the number moves — it is one constant with a
default, not a structural choice.
