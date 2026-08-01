"""Doctor check — the first-run backfill that nobody watched.

``session_start`` launches ``mnemo backfill --install-run`` detached, with
stdout and stderr on ``DEVNULL`` because the hook's stdout carries the
injection envelope. Everything that child says about itself is therefore
discarded, including the sentence explaining why it gave up. The mechanism
that keeps a failed run *retryable* lives in ``ledger`` and ``cmd_backfill``;
this is the part that lets a human ever find out it happened.

Three fingerprints, all meaning "the automatic backfill did not do its job",
and all read entirely from inside the vault:

- a spawn lock older than the TTL — a sweep started and its process died
  without ever reaching the ``finally`` that releases it;
- errors logged by the backfill while the completion marker is still unset —
  the shape of an environmental abort (``claude`` CLI missing, expired auth,
  rate limit), which releases the lock and records nothing per-session;
- a sweep that *completed* having produced nothing while recording failures.
  Attributable failures — a transcript that times out, one per session, up to
  ``installCap`` of them — are each stepped over rather than aborting, so the
  run ends at ``_EXIT_SOME_FAILED``, marks itself done, and is by every other
  measure a finished sweep. It is also a vault that got nothing, and the
  remedy (``--retry-failed``) exists but is named nowhere the user will look.

Deliberately *not* "is there unharvested history?". Answering that means
scanning ``~/.claude/projects``, which makes the check depend on the machine
rather than the vault, and reports a brand-new install whose first session has
simply not ended yet. Evidence of a failure is the thing worth reporting; the
mere absence of a success is not.

Silence on any error, and silence while a sweep is legitimately in flight.
"""
from __future__ import annotations

import json
from pathlib import Path

# Where the two silent-failure paths write. `backfill.harvest` is logged by
# `cmd_backfill` before it aborts; `session_start.backfill` is logged when the
# spawn itself never got off the ground.
_FAILURE_SITES = ("backfill.harvest", "session_start.backfill")


def _doctor_check_install_backfill(vault: Path) -> bool:
    """Return True when there is nothing to report."""
    try:
        from mnemo.core import config as cfg_mod
        from mnemo.core.backfill import ledger

        if not (cfg_mod.load_config().get("backfill") or {}).get("enabled", True):
            return True

        age = ledger.spawn_lock_age(vault)
        if age is not None and age >= ledger.SPAWN_LOCK_TTL_SECONDS:
            print(f"  ⚠ A first-run backfill started {int(age // 3600)}h ago and "
                  "never finished")
            print("       → its process was killed; nothing is running now")
            print("       → `mnemo backfill` resumes it (harvested sessions are "
                  "skipped)")
            return False

        led = ledger.load(vault)
        if led.get("installRunDone"):
            return _report_barren_sweep(led)
        if age is not None:
            return True  # a sweep is in flight right now — say nothing

        failures = _logged_backfill_failures(vault)
        if not failures:
            return True

        noun = "time" if failures == 1 else "times"
        print(f"  ⚠ The automatic first-run backfill has failed {failures} {noun} "
              "and never completed")
        print("       → nothing was harvested and the child's output was discarded")
        print("       → `mnemo backfill` runs it in the foreground and prints why")
        return False
    except Exception:
        return True


def _report_barren_sweep(led: dict) -> bool:
    """A finished sweep that harvested nothing and failed doing it.

    The zero-produced half alone is not a fault: a sweep of quiet sessions, or
    of a machine with no history at all, correctly produces nothing and must
    stay silent — that is the fresh-install case this check exists not to
    nag about. It is the *combination* with recorded failures that means the
    user has an empty vault and an unread reason for it.
    """
    sessions = led.get("sessions")
    if not isinstance(sessions, dict):
        return True  # hand-edited ledger; the empty case falls out below

    failed = 0
    produced = 0
    for entry in sessions.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("status") == "done":
            produced += int(entry.get("produced") or 0)
        else:
            failed += 1

    if produced or not failed:
        return True

    noun = "session" if failed == 1 else "sessions"
    print(f"  ⚠ The first-run backfill finished having harvested nothing — "
          f"{failed} {noun} failed")
    print("       → `mnemo backfill --retry-failed` clears them for another attempt")
    print("       → `mnemo backfill` then reports what went wrong, in the foreground")
    return False


def _logged_backfill_failures(vault: Path) -> int:
    from mnemo.core import errors as err_mod

    path = Path(vault) / err_mod.ERROR_LOG_NAME
    count = 0
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    if json.loads(raw.decode("utf-8")).get("where") in _FAILURE_SITES:
                        count += 1
                except Exception:
                    continue
    except OSError:
        return 0
    return count
