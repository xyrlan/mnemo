# Install review: decide once what mnemo learned from your history

**Date:** 2026-09-24
**Status:** approved by the maintainer; pieces M1, M2 (mnemo) and D1 (mnemo-desktop) dispatched in parallel
**Related:** #467 (day one measured), #471/#474 → #477 → #483 (backfill routing reverted), #485 (a project gate cannot clear the bar at this sample size), #429 (inbox expiry), mnemo-desktop #155 (onboarding)

## The problem

A new user who already uses Claude Code has history mnemo can learn from. The install backfill harvests past sessions of the current repo and extraction turns them into pages: on clubinho, 44 sessions became **56 pages**. Every one of them is staged, because backfill pages are LLM reconstructions (`core/backfill/origin.py`). Nobody drains the inbox (0 promoted in the 7 days measured on 2026-09-23), so on day one the reflex reaches none of them: 0 of 50 prompts (#467).

Letting them go live on their own was tried and reverted. The pages the normal gates would pass were 85.1% good on clubinho and 72.5% on clearframe, against a bar of 85% (#471, #477). A reference gate on the project route scored 86.7% and 83.3%, each one page from the bar, with about 30 pages per corpus (#485). No automatic rule clears the bar with confidence.

## The design

**The user decides once, at install, when they are most curious about what mnemo knows.** This replaces an inbox nobody opens. After the backfill and its first extraction finish, one screen lists what was learned, grouped by type. Every page starts checked. The user unchecks what is wrong or transient, then either keeps the selection or skips.

- **Kept** pages are promoted to `shared/<type>/`, and the reflex reaches them from the next prompt on.
- **Dropped** pages are archived, and extraction does not propose them again (`mnemo inbox --drop` already records `dismissed`).
- **Skipped or undecided** pages stay staged and **expire 14 days after staging**, as the gate-held pages do (#429). `--restore` brings one back.

Between 72.5% and 85.1% of the pages that would go live were good on the two corpora measured (#471, #477), so the work is mostly unchecking a few. Pages start checked because the measured majority is good, and the user's time goes on the minority.

The same review is available in a terminal (`mnemo inbox --review`) and later from the desktop vault screen, not only at install.

## Interface (the contract between the pieces)

All JSON goes to stdout, one document per call, UTF-8. Keys are `<type>/<slug>` as `mnemo inbox` already prints them.

### Listing (M1)

`mnemo inbox --origin backfill [--project P] --json`

```json
{
  "project": "clubinho",
  "origin": "backfill",
  "pages": [
    {
      "key": "project/clubinho__cron-annual-bloqueado-183",
      "type": "project",
      "name": "Annual cron blocked until #183",
      "description": "one line",
      "excerpt": "the first 300 characters of the body, secrets redacted",
      "staged_at": "2026-09-24T10:12:03",
      "expires_at": "2026-10-08T10:12:03"
    }
  ],
  "counts": {"project": 32, "feedback": 9, "reference": 15}
}
```

`--origin` is `backfill`, or `any` (the default). `--json` also works without `--origin`, so the desktop InboxView can stop parsing text later. Keep existing text output byte-identical when `--json` is absent.

### Deciding in batch (M1)

`mnemo inbox --promote KEY [KEY ...] --json` and `mnemo inbox --drop KEY [KEY ...] --json`, or `--keys-stdin` to read one key per line.

```json
{"promoted": ["project/a", "feedback/b"], "failed": [{"key": "reference/c", "error": "not staged"}]}
```

(`dropped` in place of `promoted` for `--drop`.) A failure on one key never stops the others. Every decision is recorded in `.mnemo/inbox-offers.jsonl` with `"via": "review"`, so drain stays measurable.

### Terminal review (M1)

`mnemo inbox --review [--origin backfill] [--project P]`: the same list as a checklist, grouped by type, all checked. The user can toggle by number, keep the selection, drop the rest, or quit and decide nothing. Off a tty it prints the list and exits without deciding.

### Running the backfill for review (M2)

`mnemo backfill --project P --dry-run --json`:

```json
{"project": "clubinho", "sessions": 44, "calls_estimate": 19, "api_price_estimate_usd": 1.2}
```

`mnemo backfill --project P --yes --extract --progress-json` harvests, then runs the first extraction for that project, printing JSON lines as it goes:

```json
{"event": "harvest", "done": 3, "of": 44}
{"event": "extract", "done": 1, "of": 8}
{"event": "done", "staged": 56, "live": 0, "failed": 0}
```

Exit 0 when the sweep finished even if some sessions failed (they are counted in `failed`); exit 2 when it could not run at all. Consent is the caller's job: the desktop shows the dry run's estimate and asks before running.

### Expiry (M2)

Staged pages with the backfill origin that no one decided expire 14 days after staging, through the same mechanism and ledger as #429, and `mnemo inbox --restore` undoes it. `expires_at` in the listing comes from this rule.

## Pieces

| Piece | Repo | Files (expected) | Depends on |
|---|---|---|---|
| **M1** listing, batch decisions, terminal review | mnemo | `cli/commands/inbox.py`, `core/inbox.py`, tests | nothing |
| **M2** backfill for review, progress JSON, expiry of backfill pages | mnemo | `cli/commands/backfill.py`, the #429 expiry code, tests | nothing (M1 only reads `expires_at`, which M2 defines: M1 computes it from the same constant) |
| **D1** "what mnemo learned" screen | mnemo-desktop | a new view under `src/`, a Tauri command to run mnemo, tests over fixtures of the JSON above | the interface above, not M1/M2's code: build against fixtures, then check against the real CLI once M1 and M2 merge |

## D1, the screen

- **Where it appears:** after setup (#155) finishes and a repo is open, when the vault has no install-review decision for that project and `mnemo backfill --dry-run --json` finds sessions to read. Also reachable from the vault screen later ("review what mnemo learned").
- **Consent:** "mnemo can read your last N sessions in <project> and learn from them. About K model calls on your Claude plan." with [Read my history] and [Not now].
- **Progress:** the `--progress-json` events as a bar. The user can leave the screen and it keeps running.
- **Review:** groups (Project facts, Your rules = `feedback`, Technical references = `reference`, others), each with a count and a "keep all/none" toggle. Rows show the name and description, expandable to the excerpt. Everything starts checked. [Keep selected] promotes the checked pages and drops the unchecked ones; [Decide later] leaves everything staged, and it expires in 14 days.
- **Measurement:** append one row per review to `usage.jsonl`: project, pages shown, kept, dropped, seconds from open to decision, skipped or not. It holds no page text.

## Success

- Adopting the review takes minutes: the median time from open to decision is under 3 minutes (from `usage.jsonl`).
- Day one carries what was kept. On the first sessions after a review, the reflex reaches kept pages. Measure with `tools/measure_day_one.py` or live `reflex-log` once real reviews exist: emit rate and on-point with the Jev judge on, next to #479's judge-on numbers.
- The inbox stops accumulating backfill pages: none is older than 14 days.

## Non-goals

- No automatic promotion of backfill pages. #483 stays.
- No change to live capture, extraction gates or the reflex.
- No team or shared review.
