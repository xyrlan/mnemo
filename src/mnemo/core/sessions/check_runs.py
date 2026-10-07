"""Why a failed check failed, as far as GitHub itself says (#575).

Two readings that need no log and no judgement, shared by ``pr-follow`` and
``tools/measure_dispatch_outcomes.py`` (#561) so the two never disagree:

- **rerun-passed** — another run of the same check on the same commit
  succeeded. GitHub's own verdict that the failure was not the commit's.
- **not-run** — the check run's annotation says the job was never started. A
  private repository whose Actions billing lapsed fails every job in about two
  seconds with no step run, and the only trace of why is the annotation "The
  job was not started because recent account payments have failed or your
  spending limit needs to be increased." No code was tested.

Every lookup goes through an injected runner and answers ``None`` when it could
not ask, so a caller can tell "GitHub said no" from "GitHub was not reached".
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Runner = Callable[[Sequence[str], Optional[str]], Tuple[int, str, str]]

#: Check-run conclusions that fail.
FAIL = frozenset({"failure", "cancelled", "timed_out", "action_required",
                  "startup_failure", "stale"})

#: What a check run's annotation says when GitHub never started the job. Its
#: phrases, not bare "not started": a failing job's annotations carry its own
#: output, and a test that says "server not started" ran.
NOT_STARTED = re.compile(r"job was not started|account payments have failed|"
                         r"spending limit needs to be increased", re.I)

_REPO = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/\d+")


def repo_of(url: str) -> Optional[str]:
    """``owner/name`` of a PR URL, or ``None``."""
    m = _REPO.match(url or "")
    return m.group(1) if m else None


def commit_runs(repo: str, oid: str, *, run: Runner) -> Optional[List[Dict[str, Any]]]:
    """Every check run on *oid* as ``{id, name, status, conclusion, suite}``,
    or ``None`` when ``gh`` could not be asked."""
    code, out, _ = run(["gh", "api", f"repos/{repo}/commits/{oid}/check-runs?per_page=100",
                        "--jq", "[.check_runs[] | {id, name, status, conclusion, "
                                "suite: .check_suite.id}]"], None)
    if code != 0:
        return None
    try:
        runs = json.loads(out or "[]")
    except ValueError:
        return None
    return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else None


def failed(r: Dict[str, Any]) -> bool:
    return str(r.get("conclusion") or "") in FAIL


def rerun_passed(failed_run: Dict[str, Any], runs: Sequence[Dict[str, Any]]) -> bool:
    """Whether another run of *failed_run*'s check on the same commit succeeded."""
    name = failed_run.get("name")
    return any(other is not failed_run and other.get("name") == name
               and other.get("conclusion") == "success" for other in runs)


def annotations(repo: str, run_id: Any, *, run: Runner) -> Optional[List[str]]:
    """The messages of check run *run_id*'s annotations, or ``None`` when
    ``gh`` could not be asked."""
    code, out, _ = run(["gh", "api", f"repos/{repo}/check-runs/{run_id}/annotations",
                        "--jq", "[.[].message]"], None)
    if code != 0:
        return None
    try:
        notes = json.loads(out or "[]")
    except ValueError:
        return None
    return [str(n) for n in notes] if isinstance(notes, list) else None


def never_started(notes: Optional[Sequence[str]]) -> bool:
    """Whether *notes* say GitHub never started the job."""
    return any(NOT_STARTED.search(str(n)) for n in notes or [])
