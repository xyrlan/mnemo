"""What each dispatched child delivered, and each plain ``claude --bg`` job (#556).

Usage:
    PYTHONPATH=src python3 tools/measure_dispatch_outcomes.py [--projects ~/.claude/projects]
        [--jobs ~/.claude/jobs] [--vault ~/mnemo] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
        [--cache FILE] [--no-gh] [--list] [--json]

Read-only: no LLM calls, no writes except ``--cache``. It reads GitHub: per
PR one ``gh pr view`` and at most two ``gh api …/check-runs`` calls, which
``--cache`` keeps between runs.

The zero-token baseline for the question #556 asks — what does ``mnemo
dispatch`` deliver that Claude Code's own background agents do not — read
off what is already on disk, for both kinds of job.

**The population** is every transcript under ``~/.claude/projects`` whose
first main-thread user record says ``sessionKind: "bg"``: Claude Code writes
that on every ``claude --bg`` session, so a job Claude Code has since pruned
from ``~/.claude/jobs`` is still counted. Each is one of two arms:

- **dispatch** — its short id is in ``dispatch-parents.jsonl``, or its cwd has
  a shape only ``mnemo dispatch`` makes (:data:`mnemo.core.dispatch._WT_RE`:
  ``-wt-<n>``, ``-wt-c-<piece>``, ``-wt-<n>-<twin tag>``). Split by that
  shape into issue, piece and twin children;
- **plain** — every other ``--bg`` session. A plain job whose cwd is a temp or
  job-scratch directory (:func:`mnemo.core.hook_guard.is_throwaway`) is a
  probe; the rest are split by whether they opened a PR.

**Per job**, all read without a model:

- *PR*: the URL the job's own ``gh pr create`` printed (as
  ``measure_post_done_commits`` reads it: never a URL some other command
  printed); failing that the ``pr-follow.json`` entry, then the PR in the
  job's ``state.json`` ``children`` when there is exactly one. The source is
  kept per row;
- *merged*, and the hours from the PR's creation to its merge (``gh``);
- *commits after the child*: each commit on the PR classified by author date
  against the child's first stop and its last turn
  (``measure_post_done_commits.classify``). ``after_child`` is work somebody
  else did once nothing of the child's was running;
- *CI at hand-off*: the check runs on the PR commit that was its head when the
  child first stopped — the last commit authored by then — read from GitHub:
  ``green``, ``red``, ``pending`` or ``none``. And the same for the PR's last
  commit, *CI at the end*. What the parent was told at the first report is
  kept beside it (``child-reports.jsonl``'s first ``finished`` state). A red
  commit is split by :func:`failure_kind`: ``flaky`` (a rerun on the same
  commit passed, or a matrix job failed where its same-OS sibling passed, in
  tests the PR never touched), ``not-run`` (GitHub never started the job — a
  private repo whose Actions billing lapsed) or ``red``;
- *woken*: ``<mnemo-pr-follow`` turns in the transcript, and whether any
  ``pr-follow.json`` attempt was for ``ci-red``. A child red at hand-off is
  **fixed by the child** when it committed after its first stop, nobody
  committed after its last turn, and its last commit is green; **fixed by
  someone else** when somebody committed after its last turn and the end is
  green;
- *rate-limit resumes*: ``<mnemo-resume`` turns;
- *human turns*: turns :func:`mnemo.core.sessions.detector.is_human_turn`
  counts after the job's first reply, plus answered ``AskUserQuestion`` calls
  — how :func:`mnemo.core.twins.conditions_of` reads a twin;
- *notice*: for a dispatched child with a parent, whether a ``finished`` row
  in ``child-reports.jsonl`` answers its first stop (``told``), after more
  than ``child_notices.GRACE_SECONDS`` (``late``), only a ``spawned`` row
  (``lost``: the reporter died, #460), or nothing (``silent``). A child
  whose job reads ``done`` (it never stopped) is ``not-stopped``: no notice
  is due, by design. A stop before the log's first row is ``before-log``;
- *tokens*: ``state.json`` ``tokens`` where the job is still on disk, the
  transcript's output tokens summed per message id where it is not — #439
  measured the two equal (median ratio 1.0004 over 109 children);
- *mnemo reached it*: mnemo hook attachments and ``mcp__mnemo__`` calls in the
  transcript. For a plain job this is the leak check: nonzero means it was
  not plain;
- *follow-up* (``--followups``): a revert, or a later PR naming this one and
  changing its files, within :data:`FOLLOWUP_DAYS` of the merge.

**Hands-off**, #556's primary outcome for one job: a PR whose last commit's
checks pass (green, or red only by flaky failures), no commit by anyone after
the job's last turn, and no human input.

**The two-arm study.** ``--pilot <pairs.jsonl>`` reads a ledger of issue
pairs — one ``mnemo dispatch`` child and one plain ``claude --bg`` job on the
same issue from the same commit — and prints both arms side by side, what
reached each (:func:`mnemo.core.twins.conditions_of`), the paired hands-off
table and :func:`readout`, the study's bar, fixed before it runs.
``--size RATE`` prints the pairs it needs.
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import measure_post_done_commits as post_done  # noqa: E402

from mnemo.core import dispatch  # noqa: E402
from mnemo.core.hook_guard import is_throwaway  # noqa: E402
from mnemo.core.sessions import child_notices, detector, report_card  # noqa: E402

DISPATCH = "dispatch"
PLAIN = "plain"

#: A pr-follow wake and a rate-limit wake, as the child sees them.
FOLLOW = post_done.FOLLOW
RESUME = "<mnemo-resume"

#: Check-run conclusions that pass, and the ones that fail.
_PASS = frozenset({"success", "neutral", "skipped"})
_FAIL = frozenset({"failure", "cancelled", "timed_out", "action_required",
                   "startup_failure", "stale"})

#: Days after a merge in which a revert or a follow-up fix counts against the
#: PR — #556's secondary outcome, fixed before the pilot's numbers were read.
FOLLOWUP_DAYS = 7

#: ``state.json`` states of a job that will not change its work again.
FINISHED = frozenset({"stopped", "done"})

#: A last commit whose checks count as passing for the hands-off outcome: a
#: flaky failure is the suite's, not the job's.
PASSING = frozenset({"green", "flaky", "non-blocking"})

_PR = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")

Runner = Callable[[Sequence[str], Optional[str]], Tuple[int, str, str]]


def _epoch(value: Any) -> Optional[float]:
    return post_done._epoch(value)


def _text(record: Dict[str, Any]) -> str:
    return post_done._user_text(record)


# ---------------------------------------------------------------------------
# one transcript
# ---------------------------------------------------------------------------


def read_transcript(path: str) -> Optional[Dict[str, Any]]:
    """Everything one job's transcript says, in one pass; ``None`` when it
    has no main-thread user record."""
    first: Optional[Dict[str, Any]] = None
    stamps: List[float] = []
    first_stop: Optional[float] = None
    wakes = resumes = human = answered = hooks = mcp = 0
    replied = False
    asked: set = set()
    creating: set = set()
    urls: List[str] = []
    tokens: Dict[str, int] = {}
    branch = ""
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("isSidechain"):
                    continue
                stamp = _epoch(record.get("timestamp"))
                kind = record.get("type")
                if record.get("gitBranch") and record.get("gitBranch") != "HEAD":
                    branch = str(record["gitBranch"])
                attachment = record.get("attachment")
                if isinstance(attachment, dict) and attachment.get("hookEvent"):
                    said = " ".join(str(attachment.get(k) or "") for k in
                                    ("stdout", "content", "command", "hookName"))
                    if "mnemo" in said:
                        hooks += 1
                content = (record.get("message") or {}).get("content")
                blocks = [b for b in content if isinstance(b, dict)] \
                    if isinstance(content, list) else []
                if kind == "user":
                    if first is None:
                        first = record
                    text = _text(record).lstrip()
                    if text.startswith(FOLLOW):
                        wakes += 1
                        if first_stop is None:
                            first_stop = stamps[-1] if stamps else stamp
                    elif text.startswith(RESUME):
                        resumes += 1
                    if replied and detector.is_human_turn(record):
                        human += 1
                    for block in blocks:
                        if (block.get("type") == "tool_result"
                                and block.get("tool_use_id") in asked
                                and not block.get("is_error")):
                            answered += 1
                elif kind == "assistant":
                    replied = True
                    message = record.get("message") or {}
                    usage = message.get("usage") or {}
                    if message.get("id") and isinstance(usage.get("output_tokens"), int):
                        # A message is written once per content block, each
                        # carrying the same usage: count it once.
                        tokens[message["id"]] = usage["output_tokens"]
                    for block in blocks:
                        if block.get("type") != "tool_use":
                            continue
                        name = str(block.get("name") or "")
                        if name == "AskUserQuestion":
                            asked.add(block.get("id"))
                        elif name.startswith("mcp__mnemo__"):
                            mcp += 1
                post_done._created(record, creating, urls)
                if stamp is not None:
                    stamps.append(stamp)
    except OSError:
        return None
    if first is None or not stamps:
        return None
    last = max(stamps)
    session_id = str(first.get("sessionId") or Path(path).stem)
    return {
        "transcript": path,
        "session_id": session_id,
        "short_id": session_id[:8],
        "bg": first.get("sessionKind") == "bg",
        "cwd": str(first.get("cwd") or ""),
        "branch": branch,
        "started": min(stamps),
        "first_stop": first_stop if first_stop is not None else last,
        "last_turn": last,
        "prompt": _text(first)[:200],
        "created_prs": list(dict.fromkeys(urls)),
        "wakes": wakes,
        "resumes": resumes,
        "human_turns": human,
        "answered_questions": answered,
        "transcript_tokens": sum(tokens.values()),
        "mnemo_hooks": hooks,
        "mnemo_mcp_calls": mcp,
    }


# ---------------------------------------------------------------------------
# the arms
# ---------------------------------------------------------------------------


def arm_of(row: Dict[str, Any], parents: Dict[str, str]) -> Tuple[str, str]:
    """``(arm, kind)`` for one ``--bg`` job.

    A temp or job-scratch cwd is a probe whatever its shape: the suite's
    ``live_claude`` tests dispatch real children into ``…/live-wt-1``.
    """
    match = dispatch._WT_RE.search(row["cwd"])
    if is_throwaway(row["cwd"]):
        return (DISPATCH if row["short_id"] in parents or match else PLAIN), "probe"
    if row["short_id"] in parents or match:
        if not match:
            return DISPATCH, "other"
        captured = match.group(1)
        if dispatch._TWIN_RE.match(captured):
            return DISPATCH, "twin"
        if captured.startswith("c-"):
            return DISPATCH, "piece"
        # #371's read-only posture: it investigates and comments, and opens
        # no PR by design.
        return DISPATCH, "read-only" if row["prompt"].startswith("Investigate issue #") else "issue"
    return PLAIN, "task" if row.get("pr") else "no-pr"


def pick_pr(row: Dict[str, Any], follow: Optional[Dict[str, Any]],
            state: Optional[Dict[str, Any]], *,
            run: Optional[Runner] = None) -> Tuple[Optional[str], Optional[str]]:
    """The job's PR and where it was read from.

    Last, with *run*, the PR GitHub has for the child's branch: until
    2026-09-16 a child's PR was opened by ``mnemo deliver`` in the parent, so
    its URL is in no transcript of the child's.
    """
    if row["created_prs"]:
        return row["created_prs"][-1], "created"
    if isinstance(follow, dict) and _PR.match(str(follow.get("pr") or "")):
        return str(follow["pr"]), "pr-follow"
    found = [c.get("href") for c in (state or {}).get("children") or []
             if isinstance(c, dict) and c.get("kind") == "pr" and _PR.match(str(c.get("href") or ""))]
    if len(found) == 1:
        return str(found[0]), "state"
    if run is not None and row.get("branch") and row["branch"] not in ("master", "main"):
        url = pr_for_branch(row, run=run)
        if url:
            return url, "branch"
    return None, None


def repo_for(cwd: str, *, run: Runner) -> Optional[str]:
    """``owner/repo`` of the checkout *cwd* is a tree of: the tree itself while
    it exists, the checkout its name was made from once it is gone."""
    candidates = [cwd]
    stripped = dispatch._WT_RE.sub("", cwd.rstrip("/"))
    if stripped != cwd.rstrip("/"):
        candidates.append(stripped)
    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        code, out, _ = run(["git", "-C", directory, "remote", "get-url", "origin"], None)
        match = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", (out or "").strip())
        if code == 0 and match:
            return match.group(1)
    return None


def pr_for_branch(row: Dict[str, Any], *, run: Runner) -> Optional[str]:
    """The first PR on the child's branch opened after the child started."""
    repo = repo_for(row["cwd"], run=run)
    if not repo:
        return None
    code, out, _ = run(["gh", "pr", "list", "--repo", repo, "--head", row["branch"],
                        "--state", "all", "--json", "url,createdAt"], None)
    if code != 0:
        return None
    try:
        found = json.loads(out or "[]") or []
    except ValueError:
        return None
    later = sorted((_epoch(p.get("createdAt")), str(p.get("url") or "")) for p in found
                   if isinstance(p, dict) and _epoch(p.get("createdAt")) is not None
                   and _epoch(p.get("createdAt")) >= row["started"])
    return later[0][1] if later else None


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


def pr_facts(url: str, *, run: Runner) -> Optional[Dict[str, Any]]:
    """State, merge time and commits ``(authored, oid)`` of *url*, or ``None``."""
    code, out, _ = run(["gh", "pr", "view", url, "--json",
                        "state,createdAt,mergedAt,commits"], None)
    if code != 0:
        return None
    try:
        data = json.loads(out or "{}") or {}
    except ValueError:
        return None
    commits = []
    for c in data.get("commits") or []:
        if not isinstance(c, dict):
            continue
        at = _epoch(c.get("authoredDate"))
        if at is not None:
            commits.append((at, str(c.get("oid") or "")))
    return {
        "state": data.get("state"),
        "created": _epoch(data.get("createdAt")),
        "merged": _epoch(data.get("mergedAt")),
        "commits": commits,
    }


def check_state(repo: str, oid: str, *, run: Runner) -> Optional[str]:
    """``green``/``red``/``pending``/``none`` for the check runs on *oid*."""
    if not oid:
        return None
    code, out, _ = run(["gh", "api", f"repos/{repo}/commits/{oid}/check-runs?per_page=100",
                        "--jq", "[.check_runs[] | [.status, .conclusion]]"], None)
    if code != 0:
        return None
    try:
        runs = json.loads(out or "[]") or []
    except ValueError:
        return None
    return verdict(runs)


def verdict(runs: Iterable[Sequence[Any]]) -> str:
    """One word for a commit's check runs, each ``[status, conclusion]``."""
    runs = list(runs)
    if not runs:
        return "none"
    if any(str(c or "") in _FAIL for _, c in runs):
        return "red"
    if any(s != "completed" for s, _ in runs):
        return "pending"
    return "green" if all(str(c or "") in _PASS for _, c in runs) else "red"


#: What a check run's annotation says when GitHub never started the job
#: (a private repo whose Actions billing lapsed): no code was tested.
_NOT_STARTED = re.compile(r"not started|spending limit|payments have failed", re.I)
#: A failing test in a pytest log: ``FAILED tests/unit/test_x.py::test_y - …``.
_FAILED_TEST = re.compile(r"\bFAILED ([\w./-]+\.py)::")


def changed_files(url: str, *, run: Runner) -> Optional[set]:
    """The files the PR at *url* changes. The same call :func:`followup` makes,
    so a cache answers it once for both."""
    code, out, _ = run(["gh", "pr", "view", url, "--json", "mergedAt,files,baseRefName"], None)
    if code != 0:
        return None
    try:
        data = json.loads(out or "{}") or {}
    except ValueError:
        return None
    return {f.get("path") for f in data.get("files") or [] if isinstance(f, dict)}


def failure_kind(failed: Dict[str, Any], runs: Sequence[Dict[str, Any]], *, repo: str,
                 changed: Optional[set], run: Runner) -> Tuple[str, str]:
    """``(kind, why)`` for one failed check run on a commit.

    - ``rerun-passed``: a run of the same check on the same commit succeeded —
      the one verdict about flakiness GitHub itself gives;
    - ``non-blocking``: the workflow run it belongs to concluded ``success``
      with this job failing — the repo marked the job ``continue-on-error``
      (mnemo's experimental Windows job), so its own CI does not count it;
    - ``not-run``: its annotation says the job was never started;
    - ``flaky``: a matrix job (``<os> / <version>``) whose sibling on the same
      OS in the same workflow run passed, and every test its log names as
      failing lives in a file the PR does not change: the same code passed
      the same test next door, and the change never touched the test;
    - ``real``: anything else, including a failure whose log names no test.
    """
    name = str(failed.get("name") or "")
    for other in runs:
        if other is not failed and other.get("name") == name and other.get("conclusion") == "success":
            return "rerun-passed", "a rerun on the same commit passed"
    if failed.get("suite"):
        code, out, _ = run(["gh", "api", f"repos/{repo}/check-suites/{failed.get('suite')}",
                            "--jq", ".conclusion"], None)
        if code == 0 and (out or "").strip() == "success":
            return "non-blocking", "its workflow passed with it failing (continue-on-error)"
    code, out, _ = run(["gh", "api", f"repos/{repo}/check-runs/{failed.get('id')}/annotations",
                        "--jq", "[.[].message]"], None)
    try:
        notes = json.loads(out or "[]") if code == 0 else []
    except ValueError:
        notes = []
    if any(_NOT_STARTED.search(str(n)) for n in notes or []):
        return "not-run", "the job was never started"
    prefix = name.split(" / ")[0] if " / " in name else None
    if not prefix:
        return "real", "not a matrix job"
    siblings = [r for r in runs if r is not failed and r.get("suite") == failed.get("suite")
                and str(r.get("name") or "").split(" / ")[0] == prefix]
    if not any(r.get("conclusion") == "success" for r in siblings):
        return "real", f"no other {prefix} job passed"
    code, out, _ = run(["gh", "run", "view", "--job", str(failed.get("id")), "-R", repo,
                        "--log-failed"], None)
    tests = set(_FAILED_TEST.findall(out or "")) if code == 0 else set()
    if not tests:
        return "real", "the log names no failing test"
    if changed is None or tests & changed:
        return "real", "a failing test is in a file the PR changes"
    return "flaky", f"{', '.join(sorted(tests))} failed only here; untouched by the PR"


def classify_red(repo: str, oid: str, *, changed: Optional[set],
                 run: Runner) -> Tuple[str, List[Dict[str, str]]]:
    """A red commit as ``red``, ``not-run``, ``non-blocking`` or ``flaky``,
    with each failed run's kind: ``red`` when any failure is ``real``;
    ``not-run`` when one was never started; ``non-blocking`` when every one
    is; ``flaky`` otherwise (each is flaky, rerun-passed or non-blocking)."""
    code, out, _ = run(["gh", "api", f"repos/{repo}/commits/{oid}/check-runs?per_page=100",
                        "--jq", "[.check_runs[] | {id, name, status, conclusion, "
                                "suite: .check_suite.id}]"], None)
    try:
        runs = json.loads(out or "[]") if code == 0 else []
    except ValueError:
        runs = []
    failures = []
    for r in runs if isinstance(runs, list) else []:
        if str(r.get("conclusion") or "") in _FAIL:
            kind, why = failure_kind(r, runs, repo=repo, changed=changed, run=run)
            failures.append({"check": str(r.get("name") or ""), "kind": kind, "why": why})
    kinds = {f["kind"] for f in failures}
    if not failures or "real" in kinds:
        return "red", failures
    if "not-run" in kinds:
        return "not-run", failures
    if kinds == {"non-blocking"}:
        return "non-blocking", failures
    return "flaky", failures


def head_at(commits: Sequence[Tuple[float, str]], at: float) -> Optional[str]:
    """The last commit authored by *at*: the PR's head when the child stopped."""
    oid = None
    for stamp, sha in commits:
        if stamp <= at + post_done.SLACK_SECONDS:
            oid = sha
    return oid


def cached(run: Runner, path: Optional[str]) -> Runner:
    """*run* with its answers kept in *path* between runs; ``None`` keeps nothing."""
    if not path:
        return run
    try:
        with open(path, encoding="utf-8") as fh:
            store = json.load(fh)
    except (OSError, ValueError):
        store = {}

    def _run(argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
        key = json.dumps(list(argv))
        if key in store:
            code, out = store[key]
            return code, out, ""
        code, out, err = run(argv, cwd)
        if code == 0:
            store[key] = [code, out]
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(store, fh)
        return code, out, err

    return _run


# ---------------------------------------------------------------------------
# the parent's notice
# ---------------------------------------------------------------------------


def notice(row: Dict[str, Any], reports: Sequence[Dict[str, Any]], *,
           log_start: Optional[float], has_parent: bool,
           state: Optional[Dict[str, Any]]) -> Optional[str]:
    """How the parent heard of this child's first stop; ``None`` when no
    notice is owed (no parent)."""
    if not has_parent:
        return None
    if state is not None and state.get("state") == "done":
        return "not-stopped"
    stop = row["first_stop"]
    if log_start is None or stop < log_start:
        return "before-log"
    after = [r for r in reports if r.get("_at") is not None
             and r["_at"] >= stop - child_notices.SLACK_SECONDS]
    finished = [r["_at"] for r in after if r.get("event") == "finished"]
    if finished:
        return "late" if min(finished) - stop > child_notices.GRACE_SECONDS else "told"
    if any(r.get("event") == "spawned" for r in after):
        return "lost"
    return "silent"


def first_report_state(row: Dict[str, Any], reports: Sequence[Dict[str, Any]]) -> Optional[str]:
    """What the first report after the child's first stop said of its checks."""
    for r in sorted((r for r in reports if r.get("_at") is not None),
                    key=lambda r: r["_at"]):
        if r.get("event") == "finished" and r["_at"] >= row["first_stop"] - child_notices.SLACK_SECONDS:
            return str(r.get("state") or "?")
    return None


# ---------------------------------------------------------------------------
# reading it all
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def load_reports(vault: Path) -> Tuple[Dict[str, List[Dict[str, Any]]], Optional[float]]:
    """``short_id -> rows`` of ``child-reports.jsonl``, and its first row's time."""
    by_id: Dict[str, List[Dict[str, Any]]] = {}
    start: Optional[float] = None
    for row in _read_jsonl(vault / ".mnemo" / report_card.LOG_NAME):
        at = report_card._stamp(row)
        if at is not None and (start is None or at < start):
            start = at
        sid = row.get("short_id")
        if isinstance(sid, str):
            by_id.setdefault(sid, []).append({**row, "_at": at})
    return by_id, start


def load_parents(vault: Path) -> Dict[str, str]:
    return {str(r["short_id"]): str(r.get("parent_session") or "")
            for r in _read_jsonl(vault / ".mnemo" / "dispatch-parents.jsonl")
            if r.get("short_id")}


def load_follow(vault: Path) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads((vault / ".mnemo" / "pr-follow.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    children = data.get("children") if isinstance(data, dict) else None
    return children if isinstance(children, dict) else {}


def load_states(jobs: str) -> Dict[str, Dict[str, Any]]:
    out = {}
    for path in glob.glob(os.path.join(jobs, "*", "state.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            out[os.path.basename(os.path.dirname(path))] = data
    return out


def gather(projects: str, *, jobs: str, vault: Path,
           since: Optional[float] = None, until: Optional[float] = None,
           run: Optional[Runner] = report_card._run,
           only: Optional[Iterable[str]] = None,
           followup_days: Optional[int] = None,
           now: Optional[float] = None) -> List[Dict[str, Any]]:
    """One row per ``--bg`` job. *run* ``None`` reads nothing from GitHub.

    *only* limits the read to these short ids (a transcript is named after its
    session id). *followup_days* adds :func:`followup` to every merged PR.
    """
    wanted = set(only) if only is not None else None
    parents = load_parents(vault)
    reports, log_start = load_reports(vault)
    follow = load_follow(vault)
    states = load_states(jobs)
    rows = []
    for path in sorted(glob.glob(os.path.join(projects, "*", "*.jsonl"))):
        if wanted is not None and Path(path).stem[:8] not in wanted:
            continue
        row = read_transcript(path)
        if row is None or not row["bg"]:
            continue
        if since is not None and row["started"] < since:
            continue
        if until is not None and row["started"] >= until:
            continue
        sid = row["short_id"]
        state = states.get(sid)
        if state is not None and state.get("state") not in FINISHED:
            continue  # still running: nothing it delivered is final yet
        row["pr"], row["pr_source"] = pick_pr(row, follow.get(sid), state, run=run)
        row["arm"], row["kind"] = arm_of(row, parents)
        row["job_on_disk"] = state is not None
        tokens = (state or {}).get("tokens")
        row["tokens"] = tokens if isinstance(tokens, int) and tokens > 0 else row["transcript_tokens"]
        row["tokens_source"] = "state" if row["tokens"] == tokens else "transcript"
        attempts = (follow.get(sid) or {}).get("attempts") or []
        row["woken_red"] = any("ci-red" in (a.get("events") or []) for a in attempts
                               if isinstance(a, dict))
        mine = reports.get(sid, [])
        row["notice"] = notice(row, mine, log_start=log_start,
                               has_parent=bool(parents.get(sid)), state=state)
        row["first_report"] = first_report_state(row, mine)
        row["repo"] = post_done.repo_of(row["pr"]) if row["pr"] else None
        rows.append(row)
    # Two children naming one PR (a re-dispatch of the same issue, a branch
    # reused) count once, as the one that ran last — the PR is its outcome.
    last: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        if row["pr"] and (row["pr"] not in last or row["last_turn"] > last[row["pr"]]["last_turn"]):
            last[row["pr"]] = row
    for row in rows:
        if row["pr"] and last[row["pr"]] is not row:
            row["pr"], row["pr_source"] = None, "superseded"
        row["gh"] = outcome(row, run=run) if row["pr"] and run is not None else None
        if row["gh"] is not None and followup_days is not None:
            row["gh"]["followup"] = followup(row["pr"], days=followup_days, run=run,
                                             now=now)
    return rows


def _refers_to(text: str, number: int, repo: str) -> bool:
    """Whether *text* names PR *number*: ``#n`` or its URL."""
    return bool(re.search(r"(?<![\w/])#%d\b|github\.com/%s/pull/%d\b"
                          % (number, re.escape(repo), number), text or ""))


def followup(url: str, *, days: int = FOLLOWUP_DAYS, run: Runner,
             now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """A revert of the merged PR at *url*, or a follow-up fix, within *days*.

    ``state`` is ``found`` with what was found, ``none``, ``not-due`` while
    the window is still open, or ``not-merged``. What counts, all readable on
    GitHub without judgement:

    - a PR merged within *days* after this one that names it (``#n`` or its
      URL, in its title or body) and changes at least one file this one
      changed — a fix that says what it fixes;
    - a commit on the base branch within the window whose message starts
      ``Revert`` and names the PR — a revert pushed without a PR.

    A fix that names neither is not seen; a PR that names this one only to
    build on it, and touches its files, is counted. Both are the same for
    both arms of a pair.
    """
    match = _PR.match(url)
    if not match:
        return None
    repo, number = match.group(1), int(match.group(2))
    code, out, _ = run(["gh", "pr", "view", url, "--json",
                        "mergedAt,files,baseRefName"], None)
    if code != 0:
        return None
    try:
        data = json.loads(out or "{}") or {}
    except ValueError:
        return None
    merged = _epoch(data.get("mergedAt"))
    if merged is None:
        return {"state": "not-merged", "by": []}
    end = merged + days * 86400
    if (now if now is not None else datetime.now(timezone.utc).timestamp()) < end:
        return {"state": "not-due", "by": [], "due": end}
    files = {f.get("path") for f in data.get("files") or [] if isinstance(f, dict)}
    day = lambda t: datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")  # noqa: E731
    found: List[str] = []
    code, out, _ = run(["gh", "pr", "list", "--repo", repo, "--state", "merged", "--limit", "100",
                        "--search", f"{number} merged:{day(merged)}..{day(end)}",
                        "--json", "number,title,body,mergedAt,files"], None)
    try:
        later = json.loads(out or "[]") if code == 0 else []
    except ValueError:
        later = []
    for pr in later if isinstance(later, list) else []:
        at = _epoch(pr.get("mergedAt"))
        if (pr.get("number") == number or at is None or not merged < at <= end
                or not _refers_to(f"{pr.get('title')}\n{pr.get('body')}", number, repo)):
            continue
        touched = {f.get("path") for f in pr.get("files") or [] if isinstance(f, dict)}
        if touched & files:
            found.append(f"#{pr.get('number')}")
    code, out, _ = run(["gh", "api", f"repos/{repo}/commits?sha={data.get('baseRefName') or 'HEAD'}"
                        f"&since={day(merged)}T00:00:00Z&until={day(end)}T23:59:59Z&per_page=100",
                        "--jq", "[.[] | {sha: .sha, message: .commit.message, "
                                "date: .commit.author.date}]"], None)
    try:
        commits = json.loads(out or "[]") if code == 0 else []
    except ValueError:
        commits = []
    for c in commits if isinstance(commits, list) else []:
        message = str(c.get("message") or "")
        at = _epoch(c.get("date"))
        if (message.startswith("Revert") and at is not None and merged < at <= end
                and _refers_to(message, number, repo)):
            found.append(str(c.get("sha") or "")[:8])
    return {"state": "found" if found else "none", "by": found}


def outcome(row: Dict[str, Any], *, run: Runner) -> Optional[Dict[str, Any]]:
    """What GitHub says became of the job's PR."""
    facts = pr_facts(row["pr"], run=run)
    if facts is None:
        return None
    stamps = [at for at, _ in facts["commits"]]
    counts = post_done.classify(row, stamps)
    handoff = head_at(facts["commits"], row["first_stop"])
    last = facts["commits"][-1][1] if facts["commits"] else None
    ci_handoff = check_state(row["repo"], handoff, run=run) if handoff else None
    ci_end = (ci_handoff if last == handoff
              else check_state(row["repo"], last, run=run) if last else None)
    failures: Dict[str, List[Dict[str, str]]] = {}
    changed: Optional[set] = None
    if "red" in (ci_handoff, ci_end):
        changed = changed_files(row["pr"], run=run)
    if ci_handoff == "red" and handoff:
        ci_handoff, failures["handoff"] = classify_red(row["repo"], handoff, changed=changed, run=run)
    if ci_end == "red" and last:
        ci_end, failures["end"] = classify_red(row["repo"], last, changed=changed, run=run)
    merged = facts["merged"] is not None
    hours = ((facts["merged"] - facts["created"]) / 3600.0
             if merged and facts["created"] is not None else None)
    fixed = None
    if ci_handoff == "red":
        if ci_end in PASSING and counts["by_child_later"] and not counts["after_child"]:
            fixed = "child"
        elif ci_end in PASSING and counts["after_child"]:
            fixed = "someone-else"
        elif ci_end in PASSING:
            fixed = "rerun"
        else:
            fixed = "no"
    return {
        "state": facts["state"],
        "merged": merged,
        "hours_to_merge": hours,
        "commits": counts,
        "ci_handoff": ci_handoff,
        "ci_end": ci_end,
        "red_fixed_by": fixed,
        "failures": failures,
    }


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%})" if n else "0/0"


def _median(values: Sequence[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def hands_off(row: Dict[str, Any]) -> bool:
    """#556's primary outcome for one job: it opened a PR whose last commit's
    checks pass (green, or red only by a :func:`failure_kind` flaky failure), nobody else committed to it after the job's last turn,
    and no person reached the job after its first reply."""
    g = row.get("gh")
    return bool(row.get("pr") and g and g["ci_end"] in PASSING
                and not g["commits"]["after_child"]
                and not (row["human_turns"] or row["answered_questions"]))


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """The per-group numbers for *rows* (one arm and kind, or any subset)."""
    n = len(rows)
    with_pr = [r for r in rows if r.get("pr")]
    read = [r for r in with_pr if r.get("gh")]
    merged = [r for r in read if r["gh"]["merged"]]
    clean = [r for r in merged if not r["gh"]["commits"]["after_child"]]
    ci = {}
    for key in ("ci_handoff", "ci_end"):
        tally: Dict[str, int] = {}
        for r in read:
            word = r["gh"][key] or "unread"
            tally[word] = tally.get(word, 0) + 1
        ci[key] = tally
    fixed: Dict[str, int] = {}
    for r in read:
        by = r["gh"]["red_fixed_by"]
        if by:
            fixed[by] = fixed.get(by, 0) + 1
    notices: Dict[str, int] = {}
    for r in rows:
        if r.get("notice"):
            notices[r["notice"]] = notices.get(r["notice"], 0) + 1
    first_reports: Dict[str, int] = {}
    for r in with_pr:
        if r.get("first_report"):
            first_reports[r["first_report"]] = first_reports.get(r["first_report"], 0) + 1
    tokens = sorted(r["tokens"] for r in rows if r.get("tokens"))
    wall = [(r["first_stop"] - r["started"]) / 60.0 for r in rows]
    return {
        "jobs": n,
        "with_pr": len(with_pr),
        "pr_read": len(read),
        "merged": len(merged),
        "merged_no_commit_after_child": len(clean),
        "hands_off": sum(1 for r in rows if hands_off(r)),
        "hours_to_merge_median": _median([r["gh"]["hours_to_merge"] for r in merged
                                          if r["gh"]["hours_to_merge"] is not None]),
        "ci": ci,
        "red_fixed_by": fixed,
        "first_report": first_reports,
        "woken_for_pr": sum(1 for r in rows if r["wakes"]),
        "woken_red": sum(1 for r in rows if r.get("woken_red")),
        "resumed": sum(1 for r in rows if r["resumes"]),
        "with_human_input": sum(1 for r in rows if r["human_turns"] or r["answered_questions"]),
        "human_turns": sum(r["human_turns"] + r["answered_questions"] for r in rows),
        "notices": notices,
        "tokens_median": _median(tokens),
        "tokens_p75": tokens[max(0, math.ceil(0.75 * len(tokens)) - 1)] if tokens else None,
        "wall_minutes_median": _median(wall),
        "reached_by_mnemo": sum(1 for r in rows if r["mnemo_hooks"] or r["mnemo_mcp_calls"]),
    }


def measure(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(f"{r['arm']}/{r['kind']}", []).append(r)
    return {"groups": {k: summarize(v) for k, v in sorted(groups.items())},
            "dispatch": summarize([r for r in rows if r["arm"] == DISPATCH]),
            "plain": summarize([r for r in rows if r["arm"] == PLAIN])}


def _tally(d: Dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1])) or "—"


def _num(value: Optional[float], fmt: str = "{:.1f}") -> str:
    return "—" if value is None else fmt.format(value)


def format_group(name: str, s: Dict[str, Any]) -> List[str]:
    lines = [f"{name}: {s['jobs']} job(s)"]
    if not s["jobs"]:
        return lines
    lines.append(f"  PR opened: {_pct(s['with_pr'], s['jobs'])}; read with gh: {s['pr_read']}")
    if s["pr_read"]:
        lines.append(f"  merged: {_pct(s['merged'], s['pr_read'])}; merged with no commit after "
                     f"the child's last turn: {_pct(s['merged_no_commit_after_child'], s['merged'])}; "
                     f"median hours PR→merge {_num(s['hours_to_merge_median'])}")
        lines.append(f"  CI at hand-off: {_tally(s['ci']['ci_handoff'])}")
        lines.append(f"  CI at the end: {_tally(s['ci']['ci_end'])}")
        lines.append(f"  red at hand-off, then green by: {_tally(s['red_fixed_by'])}")
        lines.append(f"  hands-off (PR, last commit passes, nobody else's commit, no human "
                     f"input): {_pct(s['hands_off'], s['jobs'])}")
    if s["first_report"]:
        lines.append(f"  first report told the parent: {_tally(s['first_report'])}")
    lines.append(f"  woken by pr-follow: {s['woken_for_pr']} (for ci-red: {s['woken_red']}); "
                 f"rate-limit resumes: {s['resumed']}")
    lines.append(f"  human input after the first reply: {_pct(s['with_human_input'], s['jobs'])} "
                 f"({s['human_turns']} turn(s) or answers)")
    if s["notices"]:
        lines.append(f"  parent notice for the first stop: {_tally(s['notices'])}")
    lines.append(f"  output tokens: median {_num(s['tokens_median'], '{:,.0f}')}, "
                 f"p75 {_num(s['tokens_p75'], '{:,.0f}')}; minutes to first stop: "
                 f"median {_num(s['wall_minutes_median'])}")
    lines.append(f"  reached by mnemo (hooks or mcp__mnemo__): {_pct(s['reached_by_mnemo'], s['jobs'])}")
    return lines


def format_report(report: Dict[str, Any], rows: Sequence[Dict[str, Any]] = (), *,
                  listing: bool = False) -> str:
    lines: List[str] = []
    for name, s in report["groups"].items():
        lines += format_group(name, s)
    lines.append("")
    lines += format_group("dispatch (all)", report["dispatch"])
    lines += format_group("plain (all)", report["plain"])
    if listing:
        lines.append("")
        for r in sorted(rows, key=lambda r: r["started"]):
            g = r.get("gh") or {}
            when = datetime.fromtimestamp(r["started"], tz=timezone.utc)
            lines.append(
                f"{when:%Y-%m-%d %H:%M}  {r['short_id']}  {r['arm']}/{r['kind']:<6} "
                f"pr {r['pr'] or '—'} ({r['pr_source'] or '—'})  "
                f"merged {g.get('merged', '—')}  ci {g.get('ci_handoff') or '—'}→{g.get('ci_end') or '—'}  "
                f"after-child {((g.get('commits') or {}).get('after_child', '—'))}  "
                f"wakes {r['wakes']}  human {r['human_turns'] + r['answered_questions']}  "
                f"notice {r['notice'] or '—'}  tokens {r['tokens']}"
            )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# sizing the two-arm study
# ---------------------------------------------------------------------------

#: The differences in hands-off rate (dispatch minus plain) the study is sized for.
DIFFERENCES = (0.25, 0.20, 0.15, 0.10)


def mcnemar_pairs(p_dispatch: float, p_plain: float, *, alpha: float = 0.05,
                  power: float = 0.80) -> Optional[int]:
    """Issue pairs for a two-sided McNemar test to see *p_dispatch* against
    *p_plain* (Connor 1987), taking the two arms of one issue as independent.

    Independence is the conservative end: an issue both arms fail, or both
    pass, is a concordant pair and costs power only through the discordant
    ones, and a shared difficulty makes discordance rarer, not commoner.
    ``None`` when the rates are equal.
    """
    from statistics import NormalDist

    delta = p_dispatch - p_plain
    if abs(delta) < 1e-12:
        return None
    discordant = p_dispatch * (1 - p_plain) + p_plain * (1 - p_dispatch)
    z = NormalDist()
    za, zb = z.inv_cdf(1 - alpha / 2), z.inv_cdf(power)
    n = (za * math.sqrt(discordant) + zb * math.sqrt(discordant - delta * delta)) ** 2 / delta ** 2
    return math.ceil(n)


def sizing(p_dispatch: float, *, excluded: float = 0.0) -> List[Dict[str, Any]]:
    """Pairs to run for each of :data:`DIFFERENCES` below *p_dispatch*,
    *excluded* being the share of pairs expected to be left out (a leak)."""
    out = []
    for d in DIFFERENCES:
        p_plain = p_dispatch - d
        if p_plain < 0:
            continue
        clean = mcnemar_pairs(p_dispatch, p_plain)
        out.append({"difference": d, "p_plain": p_plain, "clean_pairs": clean,
                    "pairs_to_run": (math.ceil(clean / (1 - excluded)) if clean else None)})
    return out


#: The full study's bar (#556), fixed before any pair of it runs. The
#: difference is dispatch's hands-off rate minus plain's over clean pairs.
BAR_POSITIVE = 0.10
BAR_NULL = 0.10
ALPHA = 0.05


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p: the binomial test of *b* against *c*
    discordant pairs at one half."""
    n = b + c
    if not n:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def readout(table: Dict[str, int], *, planned: Optional[int] = None) -> Dict[str, Any]:
    """The full study's verdict from its paired table (:func:`pilot`'s).

    - **dispatch delivers**: exact McNemar p < 0.05, dispatch ahead, and the
      difference at least :data:`BAR_POSITIVE`;
    - **plain delivers more**: p < 0.05 with plain ahead;
    - **null**: the 95% interval of the difference lies inside
      ±:data:`BAR_NULL`;
    - **inconclusive**: anything else. It is reported as "not detectable at
      this size", and the study is not extended to chase it.

    Before *planned* pairs the verdict is ``running``: the study is read once,
    at its size, never peeked at into a stop.

    The interval is Agresti and Min's (2005) for a paired difference of
    proportions: Wald's, ``d ± 1.96 · sqrt(b + c - (b - c)² / n) / n``,
    after adding one half to each of the four cells. Plain Wald has zero
    width when no pair is discordant, and would call one concordant pair a
    null.
    """
    b, c = table.get("dispatch-only", 0), table.get("plain-only", 0)
    n = b + c + table.get("both", 0) + table.get("neither", 0)
    if not n:
        return {"pairs": 0, "verdict": "no pairs"}
    diff = (b - c) / n
    b2, c2, n2 = b + 0.5, c + 0.5, n + 2
    centre = (b2 - c2) / n2
    half = 1.959964 * math.sqrt(max(0.0, b2 + c2 - (b2 - c2) ** 2 / n2)) / n2
    p = mcnemar_exact(b, c)
    out = {"pairs": n, "difference": diff, "low": centre - half, "high": centre + half, "p": p}
    if planned is not None and n < planned:
        return {**out, "verdict": f"running ({n} of {planned})"}
    if p < ALPHA and diff >= BAR_POSITIVE:
        word = "dispatch delivers"
    elif p < ALPHA and diff < 0:
        word = "plain delivers more"
    elif -BAR_NULL < centre - half and centre + half < BAR_NULL:
        word = "null"
    else:
        word = "inconclusive"
    return {**out, "verdict": word}


def format_sizing(p_dispatch: float, *, excluded: float) -> str:
    lines = [f"pairs for a two-sided McNemar test, alpha 0.05, power 0.8, dispatch "
             f"hands-off rate {p_dispatch:.0%}, {excluded:.0%} of pairs left out for a leak:"]
    for row in sizing(p_dispatch, excluded=excluded):
        lines.append(f"  plain {row['p_plain']:.0%} (−{row['difference'] * 100:.0f} pp): "
                     f"{row['clean_pairs']} clean pairs → {row['pairs_to_run']} to run")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# the pilot
# ---------------------------------------------------------------------------


def load_pairs(path: Path) -> List[Dict[str, Any]]:
    """The pilot's pair ledger: one row per issue, ``dispatch`` and ``plain``
    naming each arm's short id."""
    return [r for r in _read_jsonl(path) if r.get("dispatch") and r.get("plain")]


def conditions(row: Dict[str, Any], other: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """What reached one arm besides its prompt (#453's channels), read the way
    ``mnemo twins show`` reads a twin; the sibling is the other arm's tree,
    branch and id."""
    from mnemo.core import twins

    names = []
    if other:
        names = [other["short_id"], os.path.basename(other["cwd"].rstrip("/"))]
        if other.get("branch"):
            names.append(other["branch"])
    return twins.conditions_of(Path(row["transcript"]), sibling=[n for n in names if n])


def pilot(pairs: Sequence[Dict[str, Any]], rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Each pair's two arms side by side, and the paired hands-off table."""
    by_id = {r["short_id"]: r for r in rows}
    out = []
    table = {"both": 0, "dispatch-only": 0, "plain-only": 0, "neither": 0, "unfinished": 0}
    for pair in pairs:
        d, p = by_id.get(pair["dispatch"]), by_id.get(pair["plain"])
        arms = {}
        for name, row, other in (("dispatch", d, p), ("plain", p, d)):
            if row is None:
                arms[name] = None
                continue
            g = row.get("gh") or {}
            arms[name] = {
                "short_id": row["short_id"], "pr": row["pr"], "hands_off": hands_off(row),
                "ci_handoff": g.get("ci_handoff"), "ci_end": g.get("ci_end"),
                "after_child": (g.get("commits") or {}).get("after_child"),
                "merged": g.get("merged"), "followup": (g.get("followup") or {}).get("state"),
                "wakes": row["wakes"], "human": row["human_turns"] + row["answered_questions"],
                "tokens": row["tokens"],
                "minutes": round((row["first_stop"] - row["started"]) / 60.0, 1),
                "mnemo_reached": row["mnemo_hooks"] + row["mnemo_mcp_calls"],
                "conditions": conditions(row, other),
            }
        if arms["dispatch"] is None or arms["plain"] is None:
            table["unfinished"] += 1
        else:
            a, b = arms["dispatch"]["hands_off"], arms["plain"]["hands_off"]
            table["both" if a and b else "dispatch-only" if a else "plain-only" if b else "neither"] += 1
        out.append({"issue": pair.get("issue"), "repo": pair.get("repo"), **arms})
    return {"pairs": out, "table": table}


def format_pilot(result: Dict[str, Any], *, planned: Optional[int] = None) -> str:
    lines = []
    for pair in result["pairs"]:
        lines.append(f"#{pair['issue']} ({pair['repo']})")
        for name in ("dispatch", "plain"):
            a = pair[name]
            if a is None:
                lines.append(f"  {name:<8} not finished")
                continue
            from mnemo.core import twins

            seen = twins.describe(a["conditions"]) if a["conditions"] is not None else "transcript not read"
            lines.append(
                f"  {name:<8} {a['short_id']}  pr {a['pr'] or '—'}  hands-off {'yes' if a['hands_off'] else 'no'}  "
                f"ci {a['ci_handoff'] or '—'}→{a['ci_end'] or '—'}  after-child {a['after_child']}  "
                f"merged {a['merged']}  follow-up {a['followup'] or '—'}  wakes {a['wakes']}  "
                f"human {a['human']}  tokens {a['tokens']:,}  min {a['minutes']}  "
                f"mnemo {a['mnemo_reached']}" + (f"  [{seen}]" if seen else ""))
    t = result["table"]
    lines.append(f"hands-off: both {t['both']}, dispatch only {t['dispatch-only']}, "
                 f"plain only {t['plain-only']}, neither {t['neither']}, unfinished {t['unfinished']}")
    r = readout(t, planned=planned)
    if r["pairs"]:
        lines.append(f"readout (the full study's bar): difference {r['difference']:+.2f} "
                     f"[{r['low']:+.2f}, {r['high']:+.2f}], exact McNemar p {r['p']:.3f} "
                     f"over {r['pairs']} pair(s) → {r['verdict']}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# private names
# ---------------------------------------------------------------------------


def load_aliases(path: Path) -> List[Tuple[str, str]]:
    """``name<TAB>alias`` rows, longest name first; ``[]`` without the file."""
    out = []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return out
    for line in text.splitlines():
        name, _, alias = line.partition("\t")
        if name.strip():
            out.append((name.strip(), alias.strip() or "a-private-repo"))
    return sorted(out, key=lambda na: -len(na[0]))


def redact(text: str, aliases: Sequence[Tuple[str, str]]) -> str:
    """Each private name to its alias, and the home directory to ``~``."""
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "~")
    for name, alias in aliases:
        text = re.sub(r"(?i)(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), alias, text)
    return text


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--jobs", default=os.path.expanduser("~/.claude/jobs"))
    parser.add_argument("--vault", default=os.path.expanduser("~/mnemo"))
    parser.add_argument("--since", help="jobs started on or after this date (YYYY-MM-DD)")
    parser.add_argument("--until", help="jobs started before this date (YYYY-MM-DD)")
    parser.add_argument("--cache", help="keep gh answers in this JSON file between runs")
    parser.add_argument("--no-gh", action="store_true", help="read nothing from GitHub")
    parser.add_argument("--list", action="store_true", help="one line per job")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--size", type=float, metavar="RATE",
                        help="print the two-arm study's size for this dispatch hands-off "
                             "rate (0-1) and exit; reads nothing")
    parser.add_argument("--excluded", type=float, default=0.0,
                        help="with --size: the share of pairs expected to be left out")
    parser.add_argument("--pilot", metavar="PAIRS",
                        help="report the pairs in this ledger (JSON lines with issue, repo, "
                             "dispatch and plain short ids) instead of every job")
    parser.add_argument("--followups", type=int, nargs="?", const=FOLLOWUP_DAYS, metavar="DAYS",
                        help=f"also look for a revert or follow-up fix within DAYS of each "
                             f"merge (default {FOLLOWUP_DAYS})")
    parser.add_argument("--planned", type=int, metavar="N",
                        help="with --pilot: the study's pre-registered size; before N pairs "
                             "the readout says running")
    args = parser.parse_args(argv)
    if args.size is not None:
        sys.stdout.write(format_sizing(args.size, excluded=args.excluded))
        return 0
    since = _epoch(f"{args.since}T00:00:00Z") if args.since else None
    until = _epoch(f"{args.until}T00:00:00Z") if args.until else None
    vault = Path(args.vault).expanduser()
    run = None if args.no_gh else cached(report_card._run, args.cache)
    pairs = load_pairs(Path(args.pilot).expanduser()) if args.pilot else None
    only = ([p[k] for p in pairs for k in ("dispatch", "plain")] if pairs is not None else None)
    rows = gather(args.projects, jobs=args.jobs, vault=vault, since=since, until=until, run=run,
                  only=only, followup_days=args.followups)
    aliases = load_aliases(vault / ".mnemo" / "private-names.tsv")
    if pairs is not None:
        result = pilot(pairs, rows)
        text = (json.dumps(result, indent=2, default=str) if args.json else format_pilot(result, planned=args.planned))
        sys.stdout.write(redact(text, aliases) + ("\n" if args.json else ""))
        return 0
    report = measure(rows)
    if args.json:
        text = json.dumps({"report": report, "jobs": rows}, indent=2, default=str)
    else:
        text = format_report(report, rows, listing=args.list)
    sys.stdout.write(redact(text, aliases) + ("\n" if args.json else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
