# Spec — the cross-session channels, measured (2026-09-15)

Every number below was counted from files on this machine on 2026-09-15.
Sources are named per row; nothing is inferred from fixtures.

## What was measured

| Channel | Fires | Cost / session | Source |
|---|---|---|---|
| Briefing (session → next session) | **2168 / 2385 = 90.9%** | median **7133 B** (~1783 tok) | `.mnemo/mcp-access-log.jsonl` |
| Reflex (vault → prompt) | **133 / 2580 = 5.2%** | ~150 tok | `.mnemo/reflex-log.jsonl{,.1}` |
| MCP pull (`list_rules_by_topic`, `read_mnemo_rule`) | **364 calls** total | — | `mcp-access-log.jsonl` |
| Path enrichment | **2 events ever** | — | `.mnemo/enrichment-log.jsonl` |
| Unblock markers | **27 markers, 0 extracted** | — | `.mnemo/session-queue.json` |
| `.mnemo-shared` team layer | **0 — never ran** | — | no `.mnemo-shared` in any repo |

Two ratios frame the whole round:

- **push/pull = 0.153** (364 pull calls against 2385 pushed injections). The
  model rarely follows the topic menu the envelope offers; nearly all
  vault → session transfer is the unsolicited briefing push.
- The briefing fires **18× more often** than reflex and costs **12× more per
  fire**, yet reflex has `replay`, `recall`, a gate and CIs, while the briefing
  has no harness, no query, and no per-item log.

The engineering effort went to the channel that fires least.

## The four problems this round fixes

### 1. `unblocks` retries forever (the #195 failure, returned)

27 markers, `extracted=0` on all of them. Running the consumer's own
resolution path (`resolve_canonical_agent(cwd)` → `discover.find_transcripts`)
against all 27: **0 resolve, 27 fail**. 26 of 27 `cwd` values point at deleted
worktrees (`/Users/xyrlan/github/mnemo-wt-200`), so the cwd resolves to project
`mnemo-wt-200`, which owns no transcripts. `learn()` returns `error`;
`consume()` counts every error as `failed` (`unblocks.py:119-122`) and only
retires a marker when `session_id` or `cwd` is *missing*
(`unblocks.py:104-110`). These markers have both set, so the retirement branch
never fires and the list grows without bound.

Nothing surfaces in `.errors.log`: the consumer collects errors into its report
instead of logging them.

This is the exact unbounded-retry failure #195 was written to stop.

### 2. The briefing is unmeasurable per item

`pick_latest_briefing` (`core/briefing.py:275-307`) has **zero logging** — no
read counter, no slug, no session id. `record_session_start_inject`
(`hooks/session_start.py:770-775`) stores only the boolean
`included_briefing`. So which of the 368 briefings on disk was read, and how
often, is unrecoverable.

368 briefings exist; **at most 30 are reachable at any moment** (the newest per
project directory). 2168 injections drew from a pool of ≤30 documents. The
other 338 are write-only unless they were briefly the newest.

### 3. The briefing has no query

`pick_latest_briefing` takes the newest and pastes the body verbatim. It is the
only injector in mnemo with no relevance step. The recall harness measures
**queried primacy@5 = 72% (n=25)** against **unqueried = 20% (n=65)**
(`.mnemo/recall-report.json`) — and the briefing is 100% unqueried by
construction.

Observed failure mode in this very session: the injected briefing covered round
6, OSC 7 and Higgsfield credits; the user's question was about product
direction. ~1783 tokens, zero overlap.

### 4. Dispatch children neither receive nor write a briefing

All 20 `bots/mnemo-wt-*` namespaces have `briefings=0`, and **0 of 2385 inject
events carry a `-wt-` project name**. Children re-acquire the vault through
their own SessionStart, but no per-child handoff document is ever produced.

The channel that works 90.9% of the time for the user does not exist for the
children. Whether that is a bug or the correct consequence of
`resolve_canonical_agent` scoping (#225) is the question the piece must answer
*before* writing code — the measurement comes first.

## Three investigations (measure, then propose)

Built, tested, merged — and firing at or near zero in real life. Each piece
measures first and proposes one of: fix, remove, or document as intentional.
None may assume the answer.

- **`.mnemo-shared`**: 0 firings. `publish` and `import_rules` are both
  registered (`cli/commands/__init__.py:23,33` — the #245 landing gap is
  closed), but no `.mnemo-shared` tree exists in any repo and
  `.mnemo/share/imports.json` is absent.
- **Path enrichment**: 2 events ever, both 2026-09-14, project `clubinho`,
  despite 655 rules carrying `path_globs`. The log begins after the #271 fix,
  so 2 is the post-fix total.
- **Socket reply**: neither mnemo nor mnemo-desktop persists a sent reply; the
  child's receipt lives only in its own transcript. Count of replies ever sent
  is unmeasurable.

## Explicitly not in scope

- Whether the vault's *rules* earn their keep. The replay harness already says
  carried 5.57%, correction-backed 0.05%, gate-verified **0.0%**
  (`.mnemo/replay-report.json`). That is a product question, not this round's.
- The CLAUDE.md counterfactual (`replay-report.json` lists it under
  `not_measured`). Worth doing; not here.

## Seams

Three pieces touch briefing code. They are split so no two share a file:

- `core/briefing.py`, `core/mcp/access_log.py` — **telemetry piece only**. It
  exposes the recorder the query piece calls.
- `core/briefing_select.py` (new) — **query piece only**.
- `hooks/session_start.py` — **query piece only**, sole owner, confined to the
  `briefing_block` region plus the recorder call.
- `core/sessions/unblocks.py` — **unblocks piece only**.
- `core/dispatch.py`, `core/child_profile.py` — **children piece only**.

The telemetry piece lands before the query piece consumes its hook; `mnemo land`
orders them.
