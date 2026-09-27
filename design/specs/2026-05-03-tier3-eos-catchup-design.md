# Tier 3 EoS scheduler fallback — design

**Status:** approved 2026-05-03
**Branch:** `feat/tier3-eos-catchup`
**Supersedes:** memory note `project_tier3_eos_scheduler_fallback_pending.md`

## Problem

Tier 3 end-of-session rule extraction runs only from `mnemo.hooks.session_end`. When a Claude Code session crashes, is killed, or ends without graceful shutdown, the SessionEnd hook never fires and the session's git/denial signals are lost. v0.14.0 shipped this gap on purpose; this spec closes it.

## Approach

Approach **(b)** from the brainstorm: a daily time-windowed sweep that runs inside the existing hook-driven scheduler. No new event log, no orphan-detection state machine. Idempotency comes from the proposer's existing dedup against vault slugs.

Catchup grouping is per *cwd* (resolved to canonical project), iterating every cached session in `mnemo.core.session` that lacks an `analyzed_at` marker and started within the catchup window. This catches **cross-project crashes** — a session that crashed on project A is still recovered the next time *any* project opens a Claude Code session.

## Architecture

### New scheduler operation

In `mnemo.autopilot.core.scheduler.run_due_jobs`, register:

| Name | Interval | Mode | Runner |
|---|---|---|---|
| `tier3.eos-catchup` | 1 day | inline | `_eos_catchup_inline(vault_root)` |

Slot between tier0 and tier1 blocks. Inherits the existing `is_active()` kill-switch gate. Add the same entry to `status_summary` operations list.

### Session lifecycle changes

**`mnemo.core.session`** — add two functions:

```python
def mark_analyzed(session_id: str) -> None:
    """Stamp `analyzed_at` (ISO-8601 UTC) into the cache file. No-op if absent."""

def iter_unanalyzed(max_age_seconds: float = 26 * 3600) -> list[dict]:
    """Return cache entries with `started_at` within window AND no `analyzed_at`.
    
    Each dict includes the session_id (added under that key) plus the original
    cached fields (`name`, `agent`, `started_at`, `cwd_at_start`, ...).
    Malformed/unreadable files are skipped silently.
    """
```

`mark_analyzed` reads → mutates → atomic-replaces (same pattern as `save`). The marker survives on disk until `cleanup_stale` wipes the file, so the same crashed session is never re-analyzed within its retention window.

**`mnemo.hooks.session_start`** — bump cleanup retention from 24h to 48h:

```python
session.cleanup_stale(max_age_seconds=48 * 3600)
```

This buffers the 26h catchup window so a session that crashes ~25h before the next SessionStart still has its cache file intact when catchup reads.

**`mnemo.hooks.session_end`** — after `_maybe_schedule_propose` returns (success or swallowed failure), call `session.mark_analyzed(sid)`. Do **not** clear the cache; leave the file for diagnostics + cleanup_stale.

Important: `mark_analyzed` is called whether or not the proposer actually wrote a candidate. Reaching SessionEnd means we *had a chance* to analyze — even if kill-switch was off, that's the user's intent and the catchup should respect it. (Trade-off: a session that ends with kill-switch off, then user enables autopilot before the next start, won't be retro-analyzed. That's acceptable; "off means off".)

### Catchup operation

```python
def _eos_catchup_inline(vault_root: Path) -> None:
    from mnemo.core import session as session_mod
    from mnemo.core import agent as agent_mod
    from mnemo.core import errors as err_mod
    from mnemo.autopilot.proposer.eos_extractor import analyze_session

    by_cwd: dict[str, list[dict]] = {}
    for entry in session_mod.iter_unanalyzed(max_age_seconds=26 * 3600):
        cwd = entry.get("cwd_at_start")
        if not cwd or not Path(cwd).exists():
            continue
        by_cwd.setdefault(cwd, []).append(entry)

    for cwd, entries in by_cwd.items():
        earliest_iso = min(e["started_at"] for e in entries)
        try:
            project = agent_mod.resolve_canonical_agent(cwd).name
            analyze_session(
                session_id=f"catchup-{entries[0]['session_id']}",
                project=project,
                vault_root=vault_root,
                cwd=Path(cwd),
                session_start_iso=earliest_iso,
            )
            for e in entries:
                session_mod.mark_analyzed(e["session_id"])
        except Exception as exc:
            err_mod.log_error(vault_root, "autopilot.tier3.catchup", exc)
```

Per-cwd error isolation: a failure on project A does not block project B, and unanalyzed entries for the failing cwd remain unmarked → retried tomorrow (bounded by the 26h window before they age out).

The synthetic `session_id="catchup-<original-sid>"` keeps the proposer's per-session log entries traceable back to the crashed session without colliding with any future real session_id.

## Failure modes

| Scenario | Behavior |
|---|---|
| No unanalyzed sessions | One directory scan, returns immediately |
| `cwd_at_start` deleted or moved | Entry skipped, no error logged (legitimate cleanup) |
| `analyze_session` raises | Swallowed via `errors.log_error`; `analyzed_at` not stamped → retried next day until aged out |
| Same crash discovered after 48h | Never — `cleanup_stale` wiped the file |
| Two cwds resolve to same canonical project | Each runs `analyze_session` independently with its own window; proposer dedup handles overlap |
| `mark_analyzed` race with concurrent SessionEnd of the same sid | Cannot happen — sid is unique per Claude session, can't be both ending now and a stale crash |

## Testing

**`tests/core/test_session.py`** (extend existing):
- `mark_analyzed` writes ISO-8601 `analyzed_at`, preserves other fields, no-op on missing file
- `iter_unanalyzed` filters by age (returns recent, omits stale), filters by marker (returns unmarked, omits marked), tolerates malformed JSON files
- `iter_unanalyzed` includes `session_id` in each returned dict

**`tests/hooks/test_session_end.py`** (extend):
- `mark_analyzed` called after a successful EoS proposer run
- `mark_analyzed` called even when kill-switch is off (intent-based, not outcome-based)
- `mark_analyzed` failure does not propagate (swallowed)

**`tests/hooks/test_session_start.py`** (extend):
- `cleanup_stale` called with `max_age_seconds=48*3600`

**`tests/autopilot/core/test_scheduler.py`** (extend):
- `tier3.eos-catchup` appears in registry with interval=1, mode=inline
- Fires when `should_run` returns True; skipped when False
- Appears in `status_summary` with correct interval

**`tests/autopilot/proposer/test_eos_catchup.py`** (new):
- Two unanalyzed cache files in two distinct cwds → `analyze_session` called twice, each with its own `session_start_iso`
- Three unanalyzed cache files in same cwd → `analyze_session` called once with earliest start; all three get marked
- One file with `cwd_at_start` pointing to nonexistent path → skipped, others proceed
- `analyze_session` raises on cwd A → cwd B still runs, A's entries remain unmarked
- All entries get `analyzed_at` stamped on success

## Out of scope

- Recovering crashed sessions older than 48h
- Diagnostic CLI (`mnemo autopilot crashed-sessions` or similar)
- Cross-machine recovery (cache lives in OS tmpdir, machine-local)
- Promoting `analyzed_at` to a richer event log (Start/End/Crash transitions)

## Files touched

- `src/mnemo/core/session.py` — `mark_analyzed`, `iter_unanalyzed`
- `src/mnemo/hooks/session_start.py` — `cleanup_stale(48*3600)`
- `src/mnemo/hooks/session_end.py` — call `mark_analyzed` after propose
- `src/mnemo/autopilot/core/scheduler.py` — register `tier3.eos-catchup` in `run_due_jobs` + `status_summary`
- Tests as listed above

No new modules, no new config keys, no schema bumps.
