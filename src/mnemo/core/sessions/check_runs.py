"""Why a failed check failed, read from what GitHub shows (#575).

``gh pr checks`` says a check failed; it does not say whether anything was
tested. Two failures are nobody's code:

- **never ran.** A repository whose Actions billing lapsed fails every job in
  about two seconds with no steps, and the check run's one annotation says
  "The job was not started because recent account payments have failed or
  your spending limit needs to be increased." A woken child can only stop
  again; a person has to settle the bill.
- **a rerun passed.** Another run of the same check on the same commit
  succeeded — the one verdict about flakiness GitHub itself gives.

``tools/measure_dispatch_outcomes.py`` (#561) reads failures the same way
(its ``failure_kind``); both predicates live here so the two never drift.
:mod:`mnemo.core.sessions.pr_follow` asks :func:`read` before it calls a PR
red. Whatever cannot be read counts as a real failure: a call that fails
leaves the wake exactly as it was before any of this.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from mnemo.core.sessions import report_card

#: Check-run conclusions that fail.
FAIL = frozenset({"failure", "cancelled", "timed_out", "action_required",
                  "startup_failure", "stale"})
#: What a check run's annotation says when GitHub never started the job.
NOT_STARTED = re.compile(r"not started|spending limit|payments have failed", re.I)

_PR_URL = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/\d+")

REAL = "real"
NOT_RUN = "not-run"
RERUN_PASSED = "rerun-passed"


def rerun_passed(failed: Dict[str, Any], runs: Iterable[Dict[str, Any]]) -> bool:
    """Another run of *failed*'s check, on the same commit, succeeded."""
    name = failed.get("name")
    return any(other is not failed and other.get("name") == name
               and other.get("conclusion") == "success" for other in runs)


def never_started(messages: Iterable[Any]) -> bool:
    """Any of a check run's annotation *messages* says the job never started."""
    return any(NOT_STARTED.search(str(m)) for m in messages or [])


def repo_of(url: str) -> Optional[str]:
    """``owner/name`` from a PR URL."""
    match = _PR_URL.match(url or "")
    return match.group(1) if match else None


def head_runs(repo: str, oid: str, *, run: report_card.Runner = report_card._run
              ) -> Optional[List[Dict[str, Any]]]:
    """Every check run on *oid*, as GitHub returns them, or ``None``."""
    code, out, _ = run(["gh", "api", f"repos/{repo}/commits/{oid}/check-runs?per_page=100"],
                       None)
    if code != 0:
        return None
    try:
        data = json.loads(out or "{}")
    except ValueError:
        return None
    rows = data.get("check_runs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return None
    return [r for r in rows if isinstance(r, dict)]


def annotations(repo: str, run_id: Any, *, run: report_card.Runner = report_card._run
                ) -> Optional[List[str]]:
    """The messages of check run *run_id*'s annotations, or ``None``."""
    code, out, _ = run(["gh", "api", f"repos/{repo}/check-runs/{run_id}/annotations"], None)
    if code != 0:
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    return [str(r.get("message") or "") for r in rows if isinstance(r, dict)]


@dataclass
class Budget:
    """Annotation reads one poll may still make."""

    left: int

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


@dataclass
class Reading:
    """The failing checks of one PR, by why they failed.

    ``kinds`` maps a check run's id to what its annotations said — ``not-run``
    or ``real`` — so a later poll never reads the same run twice: a completed
    run's annotations do not change.
    """

    real: List[str] = field(default_factory=list)
    not_run: List[str] = field(default_factory=list)
    rerun_passed: List[str] = field(default_factory=list)
    #: Failing checks the budget ran out before.
    unread: List[str] = field(default_factory=list)
    kinds: Dict[str, str] = field(default_factory=dict)

    @property
    def excused(self) -> bool:
        """Every failure is one no child can fix, and all were read."""
        return not self.real and not self.unread


def read(url: str, oid: str, failing: Sequence[str], *, budget: Budget,
         known: Optional[Dict[str, str]] = None,
         run: report_card.Runner = report_card._run) -> Optional[Reading]:
    """Why each of *failing* (check names off ``gh pr checks``) failed on
    *oid*, or ``None`` when the check runs could not be read.

    One call for the commit's check runs, then one per failed run not already
    in *known*, while *budget* lasts. It stops at the first real failure:
    that one wakes the child whatever the others are.
    """
    repo = repo_of(url)
    if not repo or not oid:
        return None
    runs = head_runs(repo, oid, run=run)
    if runs is None:
        return None
    known = dict(known or {})
    reading = Reading()
    for name in dict.fromkeys(failing):
        failed = [r for r in runs if r.get("name") == name
                  and str(r.get("conclusion") or "") in FAIL]
        if not failed:
            # A commit status, or a run GitHub no longer lists: unreadable.
            reading.real.append(name)
            return reading
        if rerun_passed(failed[0], runs):
            reading.rerun_passed.append(name)
            continue
        kinds = []
        for r in failed:
            key = str(r.get("id"))
            kind = known.get(key)
            if kind is None:
                if not budget.take():
                    kinds.append(None)
                    continue
                notes = annotations(repo, r.get("id"), run=run)
                if notes is None:
                    # Could not ask: today's answer, and ask again next poll.
                    kinds.append(REAL)
                    continue
                kind = NOT_RUN if never_started(notes) else REAL
            reading.kinds[key] = kind
            kinds.append(kind)
        if REAL in kinds:
            reading.real.append(name)
            return reading
        if None in kinds:
            reading.unread.append(name)
        else:
            reading.not_run.append(name)
    return reading
