"""Put a finished child back on its own PR when the PR changes (#436).

A dispatched child ends itself once it has opened its PR. Whatever happens to
that PR afterwards — CI goes red, a reviewer comments, the base moves and the
PR conflicts — had nobody on it: #426 made the parent *hear* about red CI, and
the fix was still typed by hand. On 2026-09-22, 4 of 46 mnemo child PRs and
5 of 35 mnemo-desktop ones carried commits made after the child's last turn
(``tools/measure_post_done_commits.py``; its per-PR list names the same mnemo
four — #356, #390, #398, #407 — as the scratch count the issue cites), and
roughly half of those were CI or environment failures.

**What acts, and why it is the child.** The child is woken in its own session
— ``claude --bg --resume <full id>``, the channel #393 measured — with a
constant nudge naming the event (:func:`wake.pr_nudge`). Waking it rather than
spawning a successor is what keeps the issue's first limit without a line of
code to enforce it: ``--resume`` restores every saved option, and the opening
prompt, which is the only thing that ever said what the child may publish, is
still its opening prompt. A child dispatched with ``--may none`` wakes into a
conversation that says "do not push", so it publishes nothing now either. And
the child has what a successor would have to be told: the issue, its own
reasoning, the tree it built. That is the "without the maintainer re-typing
context" the issue asks for.

Only a child granted ``push`` is followed at all. Its fix reaches the PR only
through a push; a child that may not push would fix the tree and stop to ask,
which is a spent session for a question ``mnemo deliver`` already answers.

**What it will not touch** — each of these closes the follow and hands the PR
back to the parent's notice rather than waking anything:

- a branch whose head on GitHub is not the tree's ``HEAD``. Somebody else
  pushed — the maintainer fixing it by hand, most likely — or the child left
  commits it never pushed. Either way the tree is not the PR, and the
  maintainer's commit is never under a woken child's feet. The nudge repeats
  it (no force-push, no rebase of a pushed branch), and the child's opening
  prompt already forbade force-pushing.
- a tree that is gone or has uncommitted changes.
- a session that is not finished in the roster. A running one would be forked
  by ``--resume``; a blocked one — the account limit included — is
  :mod:`mnemo.core.sessions.rewake`'s and ``mnemo resume``'s, and this module
  never races them: it waits, and if the window closes first it says so.

**Which events.** Red: checks settled with at least one failing, read check
by check as #426 reads them — and at least one of those failing for a reason
the child could act on (#575). A check GitHub never started (its annotation
says the account's Actions payments failed or its spending limit is spent) or
one a rerun on the same commit passed is not the child's: on 2026-09-30 two
children were woken to "fix" checks that had never run, and could only stop
again. Those are read as :mod:`mnemo.core.sessions.check_runs` reads them —
the same reading ``tools/measure_dispatch_outcomes.py`` gives — and a PR red
only by checks that never ran is told to the parent once per head commit, so
a person can fix the billing; the follow stays open, so a rerun that then
fails for real still wakes the child. Review: a PR comment, or a review that comments
or requests changes, written after the child last stopped, by an ``OWNER``,
``MEMBER`` or ``COLLABORATOR`` — a stranger's comment on a public repository
must not be able to spend the maintainer's account. Conflict: GitHub's
``mergeable`` says ``CONFLICTING``.

**Bounds.** At most ``dispatch.followPR.attempts`` wakes per PR (2), inside
``dispatch.followPR.hours`` from the child's first stop (24). A PR still red,
still conflicting or still unanswered when either runs out goes back to the
parent's notice. Each PR is looked at every :data:`POLL_SECONDS`, not every
tick: two ``gh`` calls a PR, so a round of 36 PRs costs ~860 calls an hour
against GitHub's 5,000. A PR whose checks settled red costs one more call for
its check runs and at most :data:`MAX_NOTE_READS` annotation reads a poll; a
run's reading is kept in the ledger, so no run is read twice. When any of
those calls fails, the failure counts as the child's, which is the behaviour
before #575.

**What stops a storm (#329, #330).** The same three things that bound
``rewake``: one watcher per vault behind its own lock (every SessionEnd may
try to start one; only the first survives), one pass at a time behind a
second lock, and at most :data:`MAX_WAKES_PER_PASS` wakes a pass. The watcher
runs ``git`` and ``gh`` and, per wake, one ``claude --bg --resume`` — never
``claude --print`` — and the hook that starts it is off under
``MNEMO_HOOKS_OFF``. It is started through ``session_start._spawn_detached``,
the one chokepoint the test suite stubs (#408).

**How the maintainer knows.** A notice to the parent on every wake and on
every hand-back, a line in the repo's day log, and the ledger
(:data:`LEDGER_NAME`). The woken child's own stop then posts its report card,
as any child's does.

**A child dispatched on Grok** is followed the same way. Its key in the
ledger is ``grok.paths.short_id`` (the last eight digits of its id; the
first eight are a timestamp two children share), its wake is a user turn
through its own leader (:mod:`.grok_verbs`), and the nudge tells it to end
its last turn with its report rather than to stop itself. A leader that
cannot be reached leaves the follow as it was, to be tried on the next poll.

**The merge (#638).** A child ends with a green PR, and the PR then waited
for a person to type "merge": over the 100 PRs merged here up to 2026-10-11,
83 came from a child's branch and the median wait from the last check
completing to the merge was 11 minutes, 71 hours in all
(``tools/measure_merge_wait.py``). With ``dispatch.autoMerge.repos`` naming
the PR's repository, the watcher merges a followed child's PR itself, on the
poll that finds nothing outstanding and the PR ready (:func:`merge_ready`):
open, not a draft, ``MERGEABLE``, at least one check reported and every one
of them passed, read check by check. mnemo merges; the child still does not —
``merge`` is still refused as a grant (:mod:`.grants`) and the child's prompt
still says "Do not merge".

*The switch* is a list of ``owner/name`` in mnemo's config, empty by default.
It is read from the config file again on every pass, so turning it off does
not wait for a day-old watcher, and never from a file inside a tree dispatch
made (:func:`merge_on`): the watcher's cwd, and so its config, can be a
child's worktree, and a branch could carry that file. Nothing on the command
line of ``mnemo dispatch``, on the ledger or in a child's session names it.

*It merges what it read* (:func:`merge_pr`). The PR is read once more right
before the merge and must still be open, mergeable and at the head the checks
were read for; ``gh pr merge`` then gets ``--match-head-commit`` with that
head, the method ``mnemo land`` defaults to, and never ``--admin`` or
``--auto``. A refusal — branch protection, a race, ``gh`` failing — goes to
the parent's notice in ``gh``'s own words, and that head is not tried again.
After a merge the PR is read again, and "merged" is said only when GitHub
says ``MERGED``.

*What it never merges* (:func:`merge_hold`): a twin's PR, or any PR while
another open PR targets the same issue (two answers to one question are the
maintainer's to choose between — #556's pilot closed three green PRs); a
contract piece, whose order ``mnemo land`` owns; a PR whose head is not the
tree's ``HEAD``, or whose tree is dirty or gone; a PR with a trusted comment
or asking review the child has not answered, or with changes still requested;
a child that is running or blocked; and a PR with no checks at all, so a
repository without CI gets no automatic merge. A skipped or cancelled check
did not pass: a check that never ran is not green. Each hold is told to the
parent once per head, and the follow stays open.

*What it is not* is a merge queue. Two PRs each green on their own can break
together, and nothing here tests the combination: the base's own CI run after
the merge is where that shows.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mnemo.core import locks
from mnemo.core.sessions import check_runs, report_card
from mnemo.core.sessions import wake as wake_mod
from mnemo.core.sessions.grok_verbs import NotReached

#: ``dispatch.followPR`` defaults. See the module docstring for each.
DEFAULT_ATTEMPTS = 2
DEFAULT_HOURS = 24
#: Bounds on a typo, as ``report_card.MAX_WATCH_MINUTES`` is.
MAX_ATTEMPTS = 5
MAX_HOURS = 72

#: How often one PR is looked at. CI on mnemo's slowest runner is ~5 minutes,
#: so a red build is acted on within one more poll of settling.
POLL_SECONDS = 300.0
#: How often the watcher wakes to see which PRs are due.
TICK_SECONDS = 60.0
#: Wakes one pass may make. A wake starts a whole session; a round of PRs that
#: all went red on one base change must not start all of them in one minute.
MAX_WAKES_PER_PASS = 3

#: Annotation reads one poll may make for one PR. A lapsed bill fails every
#: job of a matrix; the rest are read on the next polls (each run once).
MAX_NOTE_READS = 6

LEDGER_NAME = "pr-follow.json"
LOCK_NAME = "pr-follow.lock"
WATCH_LOCK_NAME = "pr-follow-watch.lock"
LEDGER_LOCK_NAME = "pr-follow-ledger.lock"
#: A pass is a few ``gh`` calls a PR and at most three wakes.
LOCK_STALE_SECONDS = 10 * 60
#: The watcher re-stamps its lock every tick.
WATCH_LOCK_STALE_SECONDS = 15 * 60
#: A watcher retires after a day, as ``rewake``'s does, so it is never much
#: older than the mnemo installed; the next session start or end restarts it.
WATCH_LIFETIME_SECONDS = 24 * 3600
SCHEMA_VERSION = 1

#: GitHub's ``authorAssociation`` values whose words may wake a child.
TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
#: Review states that ask something of the author. An approval does not.
REVIEW_STATES = frozenset({"COMMENTED", "CHANGES_REQUESTED"})

#: Why a follow ended. The first group hands the PR back to the parent when
#: something was still outstanding; the second never has anything to hand.
LOUD = frozenset({
    "attempts", "window", "branch-moved", "tree-dirty", "tree-gone",
    "session-gone", "wake-failed",
})
QUIET = frozenset({"merged", "closed", "no-change", "no-pr"})

_REASONS = {
    "attempts": "it was woken {attempts} time(s), the most `dispatch.followPR.attempts` allows",
    "window": "`dispatch.followPR.hours` ({hours} h) ran out",
    "branch-moved": "the PR's head is not the child's HEAD — somebody else pushed, "
                    "or the child left commits unpushed — so mnemo will not wake it onto that branch",
    "tree-dirty": "the child's worktree has uncommitted changes",
    "tree-gone": "the child's worktree is gone",
    "session-gone": "the child's session is no longer in the roster",
    "wake-failed": "`claude --bg --resume` failed: {why}",
}

#: The reasons whose words name a Claude command, as said of a Grok child.
_GROK_REASONS = {
    "wake-failed": "the turn sent through Grok's leader failed: {why}",
}

#: How the watcher merges: what ``mnemo land`` defaults to
#: (``landing.merge_prs``), pinned to it by a test.
MERGE_METHOD = "squash"

#: Why a ready PR was not merged. Every one but the last three is told to the
#: parent, once per head; ``tried`` was told when ``gh`` answered,
#: ``siblings-unread`` is asked again on the next poll, and ``review`` is the
#: wake's to tell.
_HOLDS = {
    "twin": "it is a twin's PR, and which twin lands is the maintainer's to choose",
    "piece": "it is not an issue child's PR — a contract piece lands through "
             "`mnemo land`, which owns the order",
    "siblings": "another open PR targets the same issue ({siblings})",
    "tree-gone": "the child's worktree is gone, so mnemo cannot tell the PR is what the child built",
    "branch-moved": "the PR's head is not the child's HEAD — somebody else pushed, "
                    "or the child left commits unpushed",
    "tree-dirty": "the child's worktree has uncommitted changes",
    "child-working": "the child is still running or blocked",
    "changes-requested": "a review still requests changes",
    "tried": "", "siblings-unread": "", "review": "",
}

#: A branch dispatch named for an issue, twins included (``dispatch.branch_name``).
_ISSUE_BRANCH = re.compile(r"^fix/issue-(\d+)(?:-[0-9a-f]{6})?$")

#: How much of ``gh``'s refusal rides in a notice.
REFUSAL_CHARS = 600

_EVENT_WORDS = {
    "ci-red": "checks failing",
    "review": "review comments since it stopped",
    "conflict": "conflicts with its base",
}


def settings(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``dispatch.followPR``, bounded; a value nobody meant is the default."""
    raw = (((cfg or {}).get("dispatch") or {}).get("followPR") or {})
    if not isinstance(raw, dict):
        raw = {}

    def _int(key: str, default: int, top: int) -> int:
        value = raw.get(key, default)
        if isinstance(value, bool):
            return default
        try:
            return max(0, min(int(value), top))
        except (TypeError, ValueError):
            return default

    enabled = raw.get("enabled", True)
    return {
        "enabled": enabled if isinstance(enabled, bool) else True,
        "attempts": _int("attempts", DEFAULT_ATTEMPTS, MAX_ATTEMPTS),
        "hours": _int("hours", DEFAULT_HOURS, MAX_HOURS),
    }


def enabled(cfg: Optional[Dict[str, Any]]) -> bool:
    s = settings(cfg)
    return bool(s["enabled"] and s["attempts"] > 0 and s["hours"] > 0)


def merge_repos(cfg: Optional[Dict[str, Any]]) -> frozenset:
    """``dispatch.autoMerge.repos`` as lowercase ``owner/name``; anything that
    is not a list of such names is no repository at all."""
    raw = ((cfg or {}).get("dispatch") or {})
    raw = raw.get("autoMerge") if isinstance(raw, dict) else None
    repos = raw.get("repos") if isinstance(raw, dict) else None
    if not isinstance(repos, list):
        return frozenset()
    return frozenset(r.strip().lower() for r in repos
                     if isinstance(r, str) and re.match(r"^[\w.-]+/[\w.-]+$", r.strip()))


def _in_a_dispatched_tree(path: Path, tree: Optional[str]) -> bool:
    """Whether *path* lies inside *tree* or inside any tree dispatch named."""
    from mnemo.core.dispatch import issue_for_cwd

    try:
        here = Path(os.path.abspath(str(path)))
        if tree:
            there = Path(os.path.abspath(tree))
            if os.path.normcase(str(there)) in {
                    os.path.normcase(str(p)) for p in (here, *here.parents)}:
                return True
        return any(issue_for_cwd(p) is not None for p in here.parents)
    except (OSError, ValueError):
        return True


def merge_on(cfg: Optional[Dict[str, Any]], repo: Optional[str], *,
             config_path: Optional[Path] = None, tree: Optional[str] = None) -> bool:
    """Whether the maintainer turned the merge on for *repo* (``owner/name``).

    *config_path* is the file *cfg* was read from. One inside the child's
    *tree*, or inside any tree dispatch made, is a file a branch can carry,
    and it turns nothing on.
    """
    if not repo or repo.lower() not in merge_repos(cfg):
        return False
    return config_path is None or not _in_a_dispatched_tree(Path(config_path), tree)


def will_merge(cfg: Optional[Dict[str, Any]], *, vault_root: Path, short_id: str,
               cwd: Optional[str], url: str,
               config_path: Optional[Path] = None) -> bool:
    """Whether the watcher would merge this child's PR once it is ready: what
    the report card's last line says. Never raises.

    True for an issue child that is followed (granted ``push``) in a repository
    the switch names. It promises nothing about this PR's checks or reviews.
    """
    try:
        from mnemo.core.dispatch import issue_for_cwd, twin_tag_for_cwd
        from mnemo.core.sessions import grants

        if not enabled(cfg) or not merge_on(cfg, check_runs.repo_of(url),
                                            config_path=config_path, tree=cwd):
            return False
        if not isinstance(issue_for_cwd(cwd), int) or twin_tag_for_cwd(cwd):
            return False
        return "push" in grants.read(vault_root).get(short_id, ())
    except Exception:  # noqa: BLE001 — a line of a notice
        return False


#: How long the queue keeps saying mnemo merged a child's PR.
QUEUE_MERGED_SECONDS = 24 * 3600


def queue_lines(cfg: Optional[Dict[str, Any]], *, vault_root: Path,
                short_ids: Optional[Sequence[str]] = None,
                config_path: Optional[Path] = None,
                now: Optional[float] = None) -> List[str]:
    """The queue's footer about the merge: which children's PRs mnemo will
    merge once ready, and which it merged in the last day. Never raises.

    Read from the ledger alone — no ``gh`` call on the queue's path. Nothing
    is said while the switch names no repository of an open follow and mnemo
    merged nothing, so a maintainer who never turned it on sees no new line.
    A merge a person made is not listed: the queue's PR column already says
    ``MERGED``, and this line is how the two are told apart.
    """
    try:
        moment = time.time() if now is None else now
        wanted = None if short_ids is None else set(short_ids)
        children = load_ledger(vault_root).get("children") or {}
        will, made = [], []
        for sid, e in sorted(children.items()):
            if not isinstance(e, dict) or (wanted is not None and sid not in wanted):
                continue
            label = f"{sid} (PR #{e['pr_number']})" if e.get("pr_number") else sid
            if e.get("closed"):
                if (e.get("merged_by") == "mnemo"
                        and moment - float(e.get("closed_at") or 0) < QUEUE_MERGED_SECONDS):
                    made.append(label)
            elif e.get("pr") and will_merge(cfg, vault_root=vault_root, short_id=sid,
                                            cwd=e.get("cwd"), url=str(e["pr"]),
                                            config_path=config_path):
                will.append(label)
        lines = []
        if will:
            lines.append("  mnemo merges when ready (dispatch.autoMerge): " + ", ".join(will))
        if made:
            lines.append("  merged by mnemo: " + ", ".join(made))
        return lines
    except Exception:  # noqa: BLE001 — a footer
        return []


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------


def ledger_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / LEDGER_NAME


def load_ledger(vault_root: Path) -> Dict[str, Any]:
    """The ledger, or an empty one. Never raises."""
    try:
        data = json.loads(ledger_path(vault_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"schema_version": SCHEMA_VERSION, "children": {}}
    if not isinstance(data, dict) or not isinstance(data.get("children"), dict):
        return {"schema_version": SCHEMA_VERSION, "children": {}}
    return data


def _update(vault_root: Path, change: Callable[[Dict[str, Any]], None],
            *, sleep: Callable[[float], None] = time.sleep) -> bool:
    """Read, *change* and write the ledger under its own short lock.

    The hook registering a stop and the watcher recording a wake write the
    same file; without the lock one of them loses its line, and a lost stop
    leaves a child reading as "still working" until the window closes.
    """
    lock = Path(vault_root) / ".mnemo" / LEDGER_LOCK_NAME
    for _ in range(40):
        with locks.try_lock(lock, stale_after=30.0) as held:
            if held:
                data = load_ledger(vault_root)
                change(data)
                data["schema_version"] = SCHEMA_VERSION
                path = ledger_path(vault_root)
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
                os.replace(tmp, path)
                return True
        sleep(0.05)
    return False


def open_entries(ledger: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        sid: entry for sid, entry in (ledger.get("children") or {}).items()
        if isinstance(entry, dict) and not entry.get("closed")
    }


def register(vault_root: Path, *, short_id: str, session_id: str, cwd: str,
             parent: Optional[str], now: Optional[float] = None) -> bool:
    """Note that *short_id* just stopped. Never raises.

    A first stop opens a follow; a later one — the child stopping again after
    a wake — only moves ``ended_at``, which is what hands the PR back to the
    watcher. A closed follow stays closed: the bounds are per PR, and a
    maintainer who wants more wakes the child by hand.
    """
    moment = time.time() if now is None else now

    def change(data: Dict[str, Any]) -> None:
        children = data.setdefault("children", {})
        entry = children.get(short_id)
        if isinstance(entry, dict):
            if not entry.get("closed"):
                entry["ended_at"] = moment
            return
        children[short_id] = {
            "session_id": session_id, "cwd": cwd, "parent": parent,
            "since": moment, "ended_at": moment, "woken_at": None,
            "polled_at": None, "attempts": [], "pr": None, "last_events": [],
            "closed": None, "closed_at": None,
        }

    try:
        return _update(vault_root, change)
    except Exception:  # noqa: BLE001 — a hook's convenience
        return False


# ---------------------------------------------------------------------------
# reading the PR
# ---------------------------------------------------------------------------


def _epoch(value: Any) -> Optional[float]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def activity(url: str, *, run: report_card.Runner = report_card._run,
             review_decision: bool = False) -> Optional[Dict[str, Any]]:
    """What ``gh`` says about *url* beyond the card: ``mergeable`` and the
    comments and reviews, or ``None`` when it could not be asked. With
    *review_decision*, GitHub's standing verdict on the reviews too, which
    only the merge reads (#638)."""
    fields = "mergeable,comments,reviews" + (",reviewDecision" if review_decision else "")
    code, out, _ = run(["gh", "pr", "view", url, "--json", fields], None)
    if code != 0:
        return None
    try:
        data = json.loads(out or "{}")
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def trusted_remarks(data: Optional[Dict[str, Any]], *, after: float) -> int:
    """Comments and asking reviews written after *after* by :data:`TRUSTED`."""
    if not data:
        return 0
    count = 0
    for row in data.get("comments") or []:
        if not isinstance(row, dict) or row.get("authorAssociation") not in TRUSTED:
            continue
        stamp = _epoch(row.get("createdAt"))
        if stamp is not None and stamp > after:
            count += 1
    for row in data.get("reviews") or []:
        if not isinstance(row, dict) or row.get("authorAssociation") not in TRUSTED:
            continue
        if row.get("state") not in REVIEW_STATES:
            continue
        stamp = _epoch(row.get("submittedAt"))
        if stamp is not None and stamp > after:
            count += 1
    return count


def settled_red(card: report_card.Card) -> bool:
    counts = card.checks
    return bool(counts and counts.get("fail") and report_card.settled(counts))


#: What :func:`failure_kinds` calls a failing check. Only ``real`` is the
#: child's; ``unread`` is one the poll's bound left for the next poll.
REAL, NOT_RUN, RERUN_PASSED, UNREAD = "real", "not-run", "rerun-passed", "unread"


def failure_kinds(card: report_card.Card, known: Optional[Dict[str, str]] = None, *,
                  run: report_card.Runner = report_card._run,
                  max_reads: int = MAX_NOTE_READS,
                  ) -> Tuple[Optional[Dict[str, str]], Dict[str, str]]:
    """``(kind by failing check name, kind by check-run id)`` for *card*'s PR.

    The first is ``None`` when the check runs could not be read — every
    failure is then the child's, as before #575. *known* is the second answer
    of an earlier poll: a completed run's annotations do not change, so a run
    read once is never read again. Reading stops at the first ``real`` one,
    which settles the question, and after *max_reads* annotation reads, which
    leaves the rest ``unread``. A failed annotation read is ``real`` and is not
    kept, so the next poll asks again.
    """
    known = dict(known or {})
    pr = card.pr
    repo = check_runs.repo_of(pr.url) if pr is not None else None
    if pr is None or not pr.head or not repo or not card.failing:
        return None, known
    runs = check_runs.commit_runs(repo, pr.head, run=run)
    if runs is None:
        return None, known
    kept: Dict[str, str] = {}
    by_run: Dict[str, str] = {}
    reads = 0
    for r in [r for r in runs if check_runs.failed(r)]:
        rid = str(r.get("id"))
        if check_runs.rerun_passed(r, runs):
            kind = RERUN_PASSED
        elif rid in known:
            kind = kept[rid] = known[rid]
        elif REAL in by_run.values() or reads >= max_reads:
            kind = UNREAD
        else:
            reads += 1
            notes = check_runs.annotations(repo, r.get("id"), run=run)
            if notes is None:
                kind = REAL
            else:
                kind = kept[rid] = NOT_RUN if check_runs.never_started(notes) else REAL
        by_run[rid] = kind
    names: Dict[str, str] = {}
    for name in card.failing:
        mine = {by_run[str(r.get("id"))] for r in runs
                if r.get("name") == name and str(r.get("id")) in by_run}
        # A failing check with no failed check run — a commit status, say —
        # has nothing to excuse it.
        for kind in (REAL, UNREAD, NOT_RUN, RERUN_PASSED):
            if kind in mine or (kind == REAL and not mine):
                names[name] = kind
                break
    return names, kept


def events(card: report_card.Card, data: Optional[Dict[str, Any]], *,
           after: float, kinds: Optional[Dict[str, str]] = None) -> List[str]:
    """Which of ``wake.PR_EVENTS`` *card* and *data* show, in its order.

    Red only once the checks have settled, so a woken child sees every
    failure at once rather than the first of several; and, given *kinds*
    (:func:`failure_kinds`), only when a failing check is ``real``. Without
    *kinds* every failure is real.
    """
    found: List[str] = []
    if settled_red(card) and (
            kinds is None or not card.failing
            or any(kinds.get(name, REAL) == REAL for name in card.failing)):
        found.append("ci-red")
    if trusted_remarks(data, after=after):
        found.append("review")
    if data and data.get("mergeable") == "CONFLICTING":
        found.append("conflict")
    return found


# ---------------------------------------------------------------------------
# the decision
# ---------------------------------------------------------------------------


@dataclass
class Decision:
    action: str  # "wait", "wake", "merge" or "close"
    #: Why a follow closed or, on a ``wait``, why a ready PR was not merged
    #: (a key of :data:`_HOLDS`).
    reason: str = ""
    events: List[str] = field(default_factory=list)


@dataclass
class MergeFacts:
    """What the merge reads that the card does not carry (#638)."""

    #: The maintainer's switch, for this PR's repository.
    on: bool = False
    #: The child is a twin (``dispatch.twin_tag_for_cwd``).
    twin: bool = False
    #: Other open PRs that target the same issue; ``None`` when not read.
    siblings: Optional[List[int]] = None


def merge_ready(card: report_card.Card, data: Optional[Dict[str, Any]]) -> bool:
    """The report card's ``ready``, held to what a merge needs. Pure.

    Open, not a draft, ``MERGEABLE`` on the read made this poll — ``UNKNOWN``
    is GitHub still computing, and an activity read that failed is no answer —
    and at least one check reported with every one of them in ``pass``. Any
    other bucket is a check that did not pass: failing, pending, cancelled, or
    skipped, which never ran.
    """
    pr = card.pr
    if pr is None or pr.state != "OPEN" or pr.draft:
        return False
    if not data or data.get("mergeable") != "MERGEABLE":
        return False
    counts = card.checks
    return bool(counts) and counts.get("pass", 0) > 0 and set(counts) == {"pass"}


def merge_hold(entry: Dict[str, Any], card: report_card.Card,
               data: Optional[Dict[str, Any]], session: Any,
               facts: MergeFacts) -> str:
    """Why a ready PR is not merged — a key of :data:`_HOLDS` — or ``""``. Pure.

    The order is cheapest fact first, and ``siblings`` last because it is the
    one that costs a ``gh`` call: :func:`_one` asks it only when nothing else
    holds.
    """
    pr = card.pr
    if facts.twin:
        return "twin"
    if not isinstance(card.target, int) or isinstance(card.target, bool):
        return "piece"
    if not card.tree_seen:
        return "tree-gone"
    if card.unpushed:
        return "branch-moved"
    if not card.clean:
        return "tree-dirty"
    if session is not None and (not getattr(session, "is_done", False)
                                or getattr(session, "is_blocked", False)):
        return "child-working"
    if trusted_remarks(data, after=float(entry.get("ended_at") or 0)):
        return "review"
    if (data or {}).get("reviewDecision") == "CHANGES_REQUESTED":
        return "changes-requested"
    tried = entry.get("merge")
    if isinstance(tried, dict) and pr is not None and tried.get("head") == pr.head:
        return "tried"
    if facts.siblings is None:
        return "siblings-unread"
    if facts.siblings:
        return "siblings"
    return ""


def in_childs_hands(entry: Dict[str, Any]) -> bool:
    """Woken, and not stopped since: nothing to look at until it stops."""
    woken = entry.get("woken_at")
    return woken is not None and woken >= (entry.get("ended_at") or 0)


def woken_since(vault_root: Path, short_id: str, *, pr: Optional[int],
                since: float) -> bool:
    """True when the follow woke *short_id* for *pr* after *since*. Never raises.

    A checks watch started at the child's stop asks this (#623): once the
    child is woken to change its PR, the watch's verdict is about a PR that is
    being rewritten, and the child's next stop reports instead.
    """
    try:
        entry = (load_ledger(vault_root).get("children") or {}).get(short_id)
        if not isinstance(entry, dict):
            return False
        woken = entry.get("woken_at")
        if not isinstance(woken, (int, float)) or woken <= since:
            return False
        number = entry.get("pr_number")
        return pr is None or number is None or number == pr
    except Exception:  # noqa: BLE001
        return False


def window_over(entry: Dict[str, Any], s: Dict[str, Any], now: float) -> bool:
    return now - float(entry.get("since") or now) >= s["hours"] * 3600


def decide(entry: Dict[str, Any], card: report_card.Card,
           data: Optional[Dict[str, Any]], session: Any, *, now: float,
           s: Dict[str, Any], kinds: Optional[Dict[str, str]] = None,
           merge: Optional[MergeFacts] = None) -> Decision:
    """What to do about one followed child. Pure: every fact is passed in.

    The order is the argument. A PR that is finished needs nothing whatever
    else is true. A PR with nothing outstanding is waited on — or, with the
    maintainer's switch on (*merge*), merged when it is ready and nothing
    holds it (#638). Only a PR that needs something is checked against
    everything that forbids acting, and the bounds come last, so a hand-back
    names the most specific reason.
    """
    pr = card.pr
    if pr is None:
        if card.tree_seen and card.ahead == 0 and card.clean:
            return Decision("close", "no-change")
        if not card.tree_seen or window_over(entry, s, now):
            return Decision("close", "no-pr")
        return Decision("wait")
    if pr.state == "MERGED":
        return Decision("close", "merged")
    if pr.state == "CLOSED":
        return Decision("close", "closed")

    found = events(card, data, after=float(entry.get("ended_at") or 0), kinds=kinds)
    if not found:
        hold = ""
        if merge is not None and merge.on and merge_ready(card, data):
            hold = merge_hold(entry, card, data, session, merge)
            if not hold:
                return Decision("merge")
        if window_over(entry, s, now):
            return Decision("close", "window")
        return Decision("wait", hold)

    if session is None:
        return Decision("close", "session-gone", found)
    if not getattr(session, "is_done", False) or getattr(session, "is_blocked", False):
        # Running, or blocked — the account limit is `mnemo resume`'s.
        if window_over(entry, s, now):
            return Decision("close", "window", found)
        return Decision("wait", "", found)
    if not card.tree_seen:
        return Decision("close", "tree-gone", found)
    if card.unpushed:
        return Decision("close", "branch-moved", found)
    if not card.clean:
        return Decision("close", "tree-dirty", found)
    if len(entry.get("attempts") or []) >= s["attempts"]:
        return Decision("close", "attempts", found)
    if window_over(entry, s, now):
        return Decision("close", "window", found)
    return Decision("wake", "", found)


# ---------------------------------------------------------------------------
# the merge
# ---------------------------------------------------------------------------


def open_siblings(repo: str, number: int, target: int, *,
                  run: report_card.Runner = report_card._run) -> Optional[List[int]]:
    """Other open PRs in *repo* that target the issue PR *number* does.

    The issue is *target* — the one dispatch named the tree for — and whatever
    the PR itself says it closes. Another PR targets it when GitHub lists it
    as closing one of those, or its branch is one dispatch names for them
    (``fix/issue-<n>``, a twin's included). ``None`` when the list could not
    be read or does not hold PR *number*: not knowing is not "none".
    """
    code, out, _ = run(
        ["gh", "pr", "list", "-R", repo, "--state", "open", "--limit", "200",
         "--json", "number,headRefName,closingIssuesReferences"], None)
    if code != 0:
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None

    def closes(row: Dict[str, Any]) -> set:
        found = set()
        for ref in row.get("closingIssuesReferences") or []:
            if not isinstance(ref, dict) or not isinstance(ref.get("number"), int):
                continue
            home = ref.get("repository") or {}
            owner = (home.get("owner") or {}).get("login") if isinstance(home, dict) else None
            name = home.get("name") if isinstance(home, dict) else None
            if owner and name and f"{owner}/{name}".lower() != repo.lower():
                continue
            found.add(ref["number"])
        match = _ISSUE_BRANCH.match(str(row.get("headRefName") or ""))
        if match:
            found.add(int(match.group(1)))
        return found

    rows = [r for r in rows if isinstance(r, dict)]
    mine = [r for r in rows if r.get("number") == number]
    if not mine:
        return None
    issues = {target} | closes(mine[0])
    return sorted(r["number"] for r in rows
                  if isinstance(r.get("number"), int) and r["number"] != number
                  and closes(r) & issues)


@dataclass
class Merged:
    #: ``merged`` (GitHub says ``MERGED``), ``unconfirmed`` (``gh`` accepted
    #: it and GitHub does not show it merged), ``refused`` (``gh`` said no) or
    #: ``moved`` (the PR was not what was judged; ``gh pr merge`` never ran).
    outcome: str
    #: ``gh``'s own words on a refusal; what changed on ``moved``.
    detail: str = ""
    #: The PR's state on the read after the merge, ``""`` when unreadable.
    state: str = ""


def merge_pr(pr: report_card.PR, *, run: report_card.Runner = report_card._run,
             method: str = MERGE_METHOD) -> Merged:
    """Merge *pr* at the head it was judged at, and read what GitHub says.

    Three ``gh`` calls. The first reads the PR again: the checks were read
    between that read and the one the card made, so a head that is the same
    on both is the head they were read for. The merge is pinned to it with
    ``--match-head-commit``, so a push that lands in between is refused by
    GitHub rather than merged unread. Never ``--admin``: a protection that
    refuses is a rule the maintainer set. Never ``--auto``: that would merge
    some later head.
    """
    fresh = report_card.pr_by_url(pr.url, run=run)
    if fresh is None:
        return Merged("moved", "the PR could not be read again")
    if fresh.state != "OPEN" or fresh.draft or fresh.mergeable != "MERGEABLE":
        return Merged("moved", f"it reads {fresh.state or '?'}"
                               f"{', draft' if fresh.draft else ''}, {fresh.mergeable or '?'}")
    if not pr.head or fresh.head != pr.head:
        return Merged("moved", "its head changed")
    code, out, err = run(
        ["gh", "pr", "merge", pr.url, f"--{method}", "--match-head-commit", pr.head], None)
    if code != 0:
        words = (err or "").strip() or (out or "").strip() or "gh pr merge failed"
        return Merged("refused", words[:REFUSAL_CHARS])
    after = report_card.pr_by_url(pr.url, run=run)
    state_now = after.state if after is not None else ""
    return Merged("merged" if state_now == "MERGED" else "unconfirmed", state=state_now)


# ---------------------------------------------------------------------------
# the text
# ---------------------------------------------------------------------------


def _pr_label(entry: Dict[str, Any]) -> str:
    number = entry.get("pr_number")
    return f"PR #{number}" if number else "its PR"


def render_woke(short_id: str, entry: Dict[str, Any], found: Sequence[str],
                *, attempt: int, s: Dict[str, Any]) -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    what = "; ".join(_EVENT_WORDS[e] for e in found if e in _EVENT_WORDS)
    lines = [
        f'{NOTICE_PREFIX} id="{short_id}" state="{",".join(found)}" event="follow-woke">',
        f"mnemo woke {short_id} to work on {_pr_label(entry)}: {what}. Attempt "
        f"{attempt} of {s['attempts']}. mnemo is reporting, not your user speaking; "
        "the child posts its report card when it stops again.",
    ]
    if entry.get("pr"):
        lines.append(f"- {entry['pr']}")
    return "\n".join(lines)


def not_run_only(card: report_card.Card, kinds: Optional[Dict[str, str]]) -> List[str]:
    """The checks that never ran, when nothing else failing is the child's:
    what the parent must hear about, because only a person can fix it."""
    if not kinds or not settled_red(card):
        return []
    if any(kinds.get(name, REAL) in (REAL, UNREAD) for name in card.failing):
        return []
    return [name for name in card.failing if kinds.get(name) == NOT_RUN]


def render_not_run(short_id: str, entry: Dict[str, Any], names: Sequence[str],
                   *, s: Dict[str, Any]) -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    lines = [
        f'{NOTICE_PREFIX} id="{short_id}" state="ci-not-run" event="follow-not-run">',
        f"mnemo did not wake {short_id} for {_pr_label(entry)}: its failing checks "
        f"never ran ({shown}). GitHub did not start the jobs — the annotation says "
        "recent account payments failed or the spending limit needs to be "
        "increased (Actions billing) — so no code was tested and there is nothing "
        "for the child to fix. Fix the billing and rerun the checks (`gh run "
        f"rerun`); mnemo keeps following the PR for up to {s['hours']} h and wakes "
        "the child if they then fail for real. mnemo is reporting, not your user "
        "speaking.",
    ]
    if entry.get("pr"):
        lines.append(f"- {entry['pr']}")
    return "\n".join(lines)


def _head(entry: Dict[str, Any]) -> str:
    return str((entry.get("merge") or {}).get("head") or entry.get("pr_head") or "")[:7] or "?"


def render_merged(short_id: str, entry: Dict[str, Any]) -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    tried = entry.get("merge") or {}
    lines = [
        f'{NOTICE_PREFIX} id="{short_id}" state="merged" event="follow-merged">',
        f"mnemo merged {short_id}'s {_pr_label(entry)} ({tried.get('method') or MERGE_METHOD}, "
        f"head {_head(entry)}): it was open, mergeable and every reported check had "
        "passed (`dispatch.autoMerge`). GitHub reports it MERGED. Nobody reviewed it "
        "on mnemo's behalf, and the base's own CI run is what tests it together "
        "with whatever else merged. mnemo is reporting, not your user speaking.",
    ]
    if entry.get("pr"):
        lines.append(f"- {entry['pr']}")
    return "\n".join(lines)


def render_merge_unconfirmed(short_id: str, entry: Dict[str, Any], state_now: str) -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    lines = [
        f'{NOTICE_PREFIX} id="{short_id}" state="unknown" event="follow-merge-unconfirmed">',
        f"`gh pr merge` accepted {short_id}'s {_pr_label(entry)} at head {_head(entry)}, "
        f"but GitHub {('reports it ' + state_now) if state_now else 'could not be read'} "
        "afterwards — a merge queue, most likely. mnemo does not call it merged, will "
        "not ask again for this head, and says so if a later poll finds it merged. "
        "mnemo is reporting, not your user speaking.",
    ]
    if entry.get("pr"):
        lines.append(f"- {entry['pr']}")
    return "\n".join(lines)


def render_merge_refused(short_id: str, entry: Dict[str, Any], words: str) -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    lines = [
        f'{NOTICE_PREFIX} id="{short_id}" state="ready" event="follow-merge-refused">',
        f"mnemo tried to merge {short_id}'s {_pr_label(entry)} at head {_head(entry)} "
        "(`dispatch.autoMerge`) and `gh pr merge` refused. mnemo never passes "
        "`--admin` and will not try this head again: the merge is back with you. "
        "mnemo is reporting, not your user speaking. gh said:",
    ]
    lines.extend(f"  > {line}" for line in (words or "?").splitlines())
    if entry.get("pr"):
        lines.append(f"- {entry['pr']}")
    return "\n".join(lines)


def render_merge_held(short_id: str, entry: Dict[str, Any], hold: str,
                      siblings: Sequence[int] = ()) -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    said = _HOLDS.get(hold, hold).format(
        siblings=", ".join(f"#{n}" for n in siblings) or "?")
    lines = [
        f'{NOTICE_PREFIX} id="{short_id}" state="ready" event="follow-merge-held">',
        f"mnemo did not merge {short_id}'s {_pr_label(entry)}, though it is open, "
        f"mergeable and green (`dispatch.autoMerge`): {said}. The merge is with "
        "you; mnemo keeps following the PR. mnemo is reporting, not your user speaking.",
    ]
    if entry.get("pr"):
        lines.append(f"- {entry['pr']}")
    return "\n".join(lines)


def render_stopped(short_id: str, entry: Dict[str, Any], reason: str,
                   found: Sequence[str], *, s: Dict[str, Any], why: str = "",
                   agent: str = "claude") -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    words = _GROK_REASONS.get(reason) if agent == "grok" else None
    said = (words or _REASONS.get(reason, reason)).format(
        attempts=len(entry.get("attempts") or []), hours=s["hours"], why=why or "?",
    )
    what = "; ".join(_EVENT_WORDS[e] for e in found if e in _EVENT_WORDS)
    lines = [
        f'{NOTICE_PREFIX} id="{short_id}" state="{",".join(found) or "unknown"}" '
        f'event="follow-stopped">',
        f"mnemo stopped following {short_id}'s {_pr_label(entry)}: {said}. Still "
        f"outstanding: {what or 'unknown'}. It is back with you; mnemo is "
        "reporting, not your user speaking.",
    ]
    if entry.get("pr"):
        lines.append(f"- {entry['pr']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# the pass
# ---------------------------------------------------------------------------


@dataclass
class PassReport:
    woken: List[str] = field(default_factory=list)
    #: Children whose PR this pass merged, and those ``gh`` refused (#638).
    merged: List[str] = field(default_factory=list)
    refused: List[str] = field(default_factory=list)
    closed: List[Tuple[str, str]] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
    polled: int = 0
    remaining: int = 0
    locked: bool = False
    disabled: bool = False


def _read_sessions() -> List[Any]:
    from mnemo.core.sessions.jobs import read_sessions

    return list(read_sessions())


def _notify(vault_root: Path, parent: Optional[str], text: str) -> bool:
    if not parent:
        return False
    from mnemo.core.sessions import inbox

    return inbox.notify(vault_root, parent, text)


def _announce(cfg, entry: Dict[str, Any], text: str) -> None:
    """One line in the day log of the repo the child worked in."""
    try:
        from mnemo.core import agent as agent_mod
        from mnemo.core import log_writer

        name = agent_mod.resolve_canonical_agent(entry.get("cwd") or ".").name
        log_writer.append_line(name, text, cfg or {})
    except Exception:
        pass


def sweep(
    cfg: Optional[Dict[str, Any]],
    *,
    vault_root: Path,
    now: Optional[float] = None,
    run: report_card.Runner = report_card._run,
    wake_fn: Optional[Callable[..., Optional[str]]] = None,
    reader: Optional[Callable[[], List[Any]]] = None,
    notify: Optional[Callable[[Path, Optional[str], str], bool]] = None,
    announce: bool = True,
    poll_seconds: float = POLL_SECONDS,
    config_path: Optional[Path] = None,
) -> PassReport:
    """Look at every followed PR that is due, and act on what needs it.

    Never raises past one child: this runs in a detached process nobody reads,
    and one PR ``gh`` cannot read must not cost the others their look.

    *config_path* is the file *cfg* came from. Given it, the merge switch is
    read from that file again on every pass and refused when the file lies in
    a tree dispatch made (:func:`merge_on`); without it the switch is *cfg*'s.
    """
    report = PassReport()
    if not enabled(cfg):
        report.disabled = True
        return report
    s = settings(cfg)
    moment = time.time() if now is None else now
    wake_fn = wake_fn or wake_mod.wake_for_pr
    tell = notify or _notify
    read = reader or _read_sessions

    def _due() -> Dict[str, Dict[str, Any]]:
        return {
            sid: e for sid, e in open_entries(load_ledger(vault_root)).items()
            if (in_childs_hands(e) and window_over(e, s, moment))
            or moment - float(e.get("checked_at" if in_childs_hands(e) else "polled_at")
                              or 0) >= poll_seconds
        }

    if not _due():
        return report

    switch = cfg
    if config_path is not None:
        try:
            from mnemo.core import config as config_mod

            switch = config_mod.load_config(Path(config_path))
        except Exception:  # noqa: BLE001 — an unreadable switch is an off one
            switch = {}

    def merging(repo: Optional[str], tree: Optional[str]) -> bool:
        return merge_on(switch, repo, config_path=config_path, tree=tree)

    with locks.try_lock(Path(vault_root) / ".mnemo" / LOCK_NAME,
                        stale_after=LOCK_STALE_SECONDS) as held:
        if not held:
            report.locked = True
            return report
        # Re-read inside the lock: the pass that held it may have woken these
        # very children, and the ledger is how it said so.
        due = _due()
        try:
            roster = {getattr(x, "short_id", None): x for x in read()}
        except Exception:
            roster = {}
        wakes = 0
        for short_id, entry in sorted(due.items()):
            try:
                if in_childs_hands(entry):
                    # Nothing to wake while it works, but the PR can still
                    # merge under it, and the window closes on what the PR
                    # shows now, not on what it showed at the wake (#500).
                    report.polled += 1
                    if window_over(entry, s, moment):
                        # No ``merging``: a woken child is about to push.
                        _one(cfg, vault_root, short_id, entry, roster.get(short_id),
                             s=s, now=moment, run=run, wake_fn=wake_fn, tell=tell,
                             report=report, announce=announce)
                    else:
                        _check_in_hands(vault_root, short_id, entry, now=moment,
                                        run=run, s=s, tell=tell, report=report)
                    continue
                if wakes >= MAX_WAKES_PER_PASS:
                    report.remaining += 1
                    continue
                report.polled += 1
                woke = _one(cfg, vault_root, short_id, entry, roster.get(short_id),
                            s=s, now=moment, run=run, wake_fn=wake_fn, tell=tell,
                            report=report, announce=announce, merging=merging)
                wakes += 1 if woke else 0
            except Exception as exc:  # noqa: BLE001
                report.failed.append(f"{short_id} — {exc}")
    return report


def _one(cfg, vault_root: Path, short_id: str, entry: Dict[str, Any], session: Any,
         *, s, now, run, wake_fn, tell, report: PassReport, announce: bool,
         merging: Optional[Callable[[Optional[str], Optional[str]], bool]] = None) -> bool:
    card = report_card.gather(short_id, cwd=entry.get("cwd"), run=run)
    if card.pr is not None:
        entry["pr"] = card.pr.url
        entry["pr_number"] = card.pr.number
        entry["pr_head"] = card.pr.head
    data = None
    kinds: Optional[Dict[str, str]] = None
    facts: Optional[MergeFacts] = None
    known = entry.get("check_kinds") if isinstance(entry.get("check_kinds"), dict) else {}
    if card.pr is not None and card.pr.state == "OPEN":
        repo = check_runs.repo_of(card.pr.url)
        if merging is not None and merging(repo, entry.get("cwd")):
            from mnemo.core.dispatch import twin_tag_for_cwd

            facts = MergeFacts(on=True, twin=bool(twin_tag_for_cwd(entry.get("cwd"))))
        data = activity(card.pr.url, run=run, review_decision=facts is not None)
        if settled_red(card):
            kinds, known = failure_kinds(card, known, run=run)
        if (facts is not None and repo and merge_ready(card, data)
                and not events(card, data, after=float(entry.get("ended_at") or 0), kinds=kinds)
                and not merge_hold(entry, card, data, session, replace(facts, siblings=[]))):
            # The one fact that costs a call, asked only when it decides.
            facts.siblings = open_siblings(repo, card.pr.number, card.target, run=run)
    decision = decide(entry, card, data, session, now=now, s=s, kinds=kinds, merge=facts)
    never_ran = not_run_only(card, kinds)
    head = card.pr.head if card.pr is not None else ""
    tell_not_run = bool(never_ran) and entry.get("not_run_head") != head
    hold = decision.reason if decision.action == "wait" else ""
    tell_held = bool(_HOLDS.get(hold)) and entry.get("held") != [head, hold]

    def polled(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["polled_at"] = now
            e["pr"], e["pr_number"] = entry.get("pr"), entry.get("pr_number")
            e["last_events"] = list(decision.events)
            e["check_kinds"] = known
            if tell_not_run:
                e["not_run_head"] = head
            if tell_held:
                e["held"] = [head, hold]

    _update(vault_root, polled)
    if tell_held:
        tell(vault_root, entry.get("parent"),
             render_merge_held(short_id, entry, hold, (facts.siblings if facts else None) or ()))
        if announce:
            _announce(cfg, entry, f"⏸ mnemo did not merge {_pr_label(entry)} for "
                                  f"{short_id} ({hold})")
    if tell_not_run:
        tell(vault_root, entry.get("parent"), render_not_run(short_id, entry, never_ran, s=s))
        if announce:
            _announce(cfg, entry,
                      f"⛔ mnemo did not wake {short_id} for {_pr_label(entry)}: "
                      f"its failing checks never ran (Actions billing)")

    if decision.action == "close":
        _close(vault_root, short_id, entry, decision.reason, decision.events,
               s=s, now=now, tell=tell, report=report, cfg=cfg, announce=announce)
        return False
    if decision.action == "merge":
        _merge(cfg, vault_root, short_id, entry, card, s=s, now=now, run=run,
               tell=tell, report=report, announce=announce)
        return False
    if decision.action != "wake":
        return False

    number = card.pr.number if card.pr else 0
    try:
        why = wake_fn(entry.get("session_id") or "", cwd=entry.get("cwd") or "",
                      pr=number, events=decision.events)
    except Exception as exc:  # noqa: BLE001
        why = str(exc) or type(exc).__name__
    if isinstance(why, NotReached):
        # A Grok child whose leader could not be reached never saw the
        # nudge: the follow stays open, as it was, and the next poll tries
        # again — until the window closes it, which tells the parent.
        report.failed.append(f"{short_id} — {why}")
        return False
    if why:
        report.failed.append(f"{short_id} — {why}")
        _close(vault_root, short_id, entry, "wake-failed", decision.events,
               s=s, now=now, tell=tell, report=report, why=why)
        return False

    attempt = len(entry.get("attempts") or []) + 1

    def woke(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["woken_at"] = now
            e.setdefault("attempts", []).append(
                {"at": now, "events": list(decision.events)})

    _update(vault_root, woke)
    report.woken.append(short_id)
    tell(vault_root, entry.get("parent"),
         render_woke(short_id, entry, decision.events, attempt=attempt, s=s))
    if announce:
        _announce(cfg, entry,
                  f"🔧 mnemo woke {short_id} for {_pr_label(entry)} "
                  f"({', '.join(decision.events)}; attempt {attempt}/{s['attempts']})")
    return True


def _merge(cfg, vault_root: Path, short_id: str, entry: Dict[str, Any],
           card: report_card.Card, *, s, now: float, run, tell, report: PassReport,
           announce: bool) -> None:
    """Merge the PR :func:`decide` found ready, and say what GitHub said."""
    pr = card.pr
    result = merge_pr(pr, run=run)
    if result.outcome == "moved":
        return  # Not what was judged: the next poll judges what it is now.
    tried = {"at": now, "head": pr.head, "method": MERGE_METHOD,
             "outcome": result.outcome, "detail": result.detail, "state": result.state}
    entry["merge"] = tried

    def wrote(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["merge"] = tried

    _update(vault_root, wrote)
    if result.outcome == "merged":
        report.merged.append(short_id)
        _close(vault_root, short_id, entry, "merged", [], s=s, now=now, tell=tell,
               report=report, cfg=cfg, announce=announce)
        return
    if result.outcome == "refused":
        report.refused.append(short_id)
        text = render_merge_refused(short_id, entry, result.detail)
        line = f"⛔ gh refused mnemo's merge of {_pr_label(entry)} for {short_id}"
    else:
        text = render_merge_unconfirmed(short_id, entry, result.state)
        line = (f"❔ gh accepted mnemo's merge of {_pr_label(entry)} for {short_id}; "
                f"GitHub does not show it merged")
    tell(vault_root, entry.get("parent"), text)
    if announce:
        _announce(cfg, entry, line)


def _check_in_hands(vault_root: Path, short_id: str, entry: Dict[str, Any], *,
                    now: float, run, s, tell, report: PassReport) -> None:
    """A woken child's PR, looked at only for the end it may reach meanwhile.

    Merged or closed ends the follow quietly. Anything else waits for the
    child's stop or the window: this never wakes, and it leaves
    ``polled_at`` and ``last_events`` to the look that can.
    """
    card = report_card.gather(short_id, cwd=entry.get("cwd"), run=run)
    if card.pr is not None:
        entry["pr"], entry["pr_number"] = card.pr.url, card.pr.number
        entry["pr_head"] = card.pr.head

    def checked(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["checked_at"] = now
            e["pr"], e["pr_number"] = entry.get("pr"), entry.get("pr_number")

    _update(vault_root, checked)
    if card.pr is not None and card.pr.state in ("MERGED", "CLOSED"):
        _close(vault_root, short_id, entry, card.pr.state.lower(), [],
               s=s, now=now, tell=tell, report=report)


def merged_by(entry: Dict[str, Any]) -> str:
    """Who merged a PR the follow saw merged: ``mnemo`` when ``gh`` accepted
    the watcher's own merge of the head that merged, ``other`` — a person,
    ``mnemo land``, anything else — when it did not."""
    tried = entry.get("merge")
    if (isinstance(tried, dict) and tried.get("outcome") in ("merged", "unconfirmed")
            and tried.get("head") and tried.get("head") == entry.get("pr_head")):
        return "mnemo"
    return "other"


def _close(vault_root: Path, short_id: str, entry: Dict[str, Any], reason: str,
           found: Sequence[str], *, s, now: float, tell, report: PassReport,
           why: str = "", cfg=None, announce: bool = False) -> None:
    who = merged_by(entry) if reason == "merged" else None

    def change(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["closed"], e["closed_at"] = reason, now
            e["last_events"] = list(found)
            if who:
                e["merged_by"] = who

    _update(vault_root, change)
    report.closed.append((short_id, reason))
    if who == "mnemo":
        tell(vault_root, entry.get("parent"), render_merged(short_id, entry))
        if announce:
            _announce(cfg, entry, f"🔀 mnemo merged {_pr_label(entry)} for {short_id} "
                                  f"({MERGE_METHOD}, head {_head(entry)})")
    if reason in LOUD and found:
        from mnemo.core.sessions import grok_verbs

        grok = grok_verbs.by_short_id(short_id, vault_root=vault_root) is not None
        tell(vault_root, entry.get("parent"),
             render_stopped(short_id, entry, reason, found, s=s, why=why,
                            agent="grok" if grok else "claude"))


# ---------------------------------------------------------------------------
# the watcher
# ---------------------------------------------------------------------------


def _lock(vault_root: Path, name: str) -> Path:
    return Path(vault_root) / ".mnemo" / name


def watcher_running(vault_root: Path, *, now: Optional[float] = None) -> bool:
    """A ``stat``, so a hook can skip spawning a watcher that would exit."""
    moment = time.time() if now is None else now
    try:
        age = moment - _lock(vault_root, WATCH_LOCK_NAME).stat().st_mtime
    except OSError:
        return False
    return age < WATCH_LOCK_STALE_SECONDS


def watch(
    cfg: Optional[Dict[str, Any]],
    *,
    vault_root: Path,
    clock: Optional[Callable[[], float]] = None,
    sleeper: Optional[Callable[[float], None]] = None,
    tick: float = TICK_SECONDS,
    lifetime: float = WATCH_LIFETIME_SECONDS,
    **sweep_kwargs: Any,
) -> str:
    """Run :func:`sweep` every *tick* until no follow is open. Returns why it
    stopped: ``idle``, ``retired``, ``locked`` or ``disabled``."""
    if not enabled(cfg):
        return "disabled"
    now = clock or time.time
    rest = sleeper or time.sleep
    born = now()
    lock = _lock(vault_root, WATCH_LOCK_NAME)
    with locks.try_lock(lock, stale_after=WATCH_LOCK_STALE_SECONDS) as held:
        if not held:
            return "locked"
        while True:
            try:
                os.utime(lock, None)
            except OSError:
                pass
            moment = now()
            sweep(cfg, vault_root=vault_root, now=moment, **sweep_kwargs)
            if not open_entries(load_ledger(vault_root)):
                return "idle"
            if moment - born >= lifetime:
                return "retired"
            rest(tick)


# ---------------------------------------------------------------------------
# the hook triggers
# ---------------------------------------------------------------------------


def _spawn_watcher() -> None:
    """``mnemo pr-follow``, detached, through the hooks' one chokepoint —
    outside any child's tree, which it would otherwise pin for a day (#506)."""
    from mnemo.hooks.session_start import _spawn_detached, watcher_cwd

    _spawn_detached(["pr-follow"], cwd=watcher_cwd())


def on_session_end(cfg: Optional[Dict[str, Any]], *, vault_root: Path,
                   session_id: str, cwd: str,
                   spawn: Optional[Callable[[], None]] = None,
                   now: Optional[float] = None) -> str:
    """Follow this child's PR if it is a dispatched child that may push.

    Returns what it decided, as a word: ``off``, ``not-a-child``,
    ``no-push``, ``closed`` (already followed to the end), ``running`` (a
    watcher holds its lock) or ``spawned``. Costs two small file reads for
    every session that is not a dispatched child.
    """
    if not enabled(cfg):
        return "off"
    from mnemo.core.sessions import grants, grok_verbs, parents

    # A Grok child is keyed by its id's last eight digits (its first eight
    # are a timestamp two children share); the dispatch ledger says which.
    short_id = grok_verbs.short_id_for(session_id or "", vault_root=vault_root)
    if not short_id or not wake_mod.is_full_session_id(session_id):
        return "not-a-child"
    parent = parents.read(vault_root).get(short_id)
    if not parent:
        return "not-a-child"
    if "push" not in grants.read(vault_root).get(short_id, ()):
        return "no-push"
    register(vault_root, short_id=short_id, session_id=session_id, cwd=cwd,
             parent=parent, now=now)
    entry = (load_ledger(vault_root).get("children") or {}).get(short_id) or {}
    if entry.get("closed"):
        return "closed"
    if watcher_running(vault_root, now=now):
        return "running"
    (spawn or _spawn_watcher)()
    return "spawned"


def on_session_start(cfg: Optional[Dict[str, Any]], *, vault_root: Path,
                     spawn: Optional[Callable[[], None]] = None) -> str:
    """Restart a watcher that died (a reboot, a kill) while follows are open.

    ``off``, ``nothing``, ``running`` or ``spawned``. A file read and a
    ``stat`` on every session start; never a ``gh`` call.
    """
    if not enabled(cfg):
        return "off"
    if not open_entries(load_ledger(vault_root)):
        return "nothing"
    if watcher_running(vault_root):
        return "running"
    (spawn or _spawn_watcher)()
    return "spawned"
