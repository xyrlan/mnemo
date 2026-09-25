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
by check as #426 reads them. Review: a PR comment, or a review that comments
or requests changes, written after the child last stopped, by an ``OWNER``,
``MEMBER`` or ``COLLABORATOR`` — a stranger's comment on a public repository
must not be able to spend the maintainer's account. Conflict: GitHub's
``mergeable`` says ``CONFLICTING``.

**Bounds.** At most ``dispatch.followPR.attempts`` wakes per PR (2), inside
``dispatch.followPR.hours`` from the child's first stop (24). A PR still red,
still conflicting or still unanswered when either runs out goes back to the
parent's notice. Each PR is looked at every :data:`POLL_SECONDS`, not every
tick: two ``gh`` calls a PR, so a round of 36 PRs costs ~860 calls an hour
against GitHub's 5,000.

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
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mnemo.core import locks
from mnemo.core.sessions import report_card
from mnemo.core.sessions import wake as wake_mod

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


def activity(url: str, *, run: report_card.Runner = report_card._run
             ) -> Optional[Dict[str, Any]]:
    """What ``gh`` says about *url* beyond the card: ``mergeable`` and the
    comments and reviews, or ``None`` when it could not be asked."""
    code, out, _ = run(
        ["gh", "pr", "view", url, "--json", "mergeable,comments,reviews"], None,
    )
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


def events(card: report_card.Card, data: Optional[Dict[str, Any]], *,
           after: float) -> List[str]:
    """Which of ``wake.PR_EVENTS`` *card* and *data* show, in its order.

    Red only once the checks have settled, so a woken child sees every
    failure at once rather than the first of several.
    """
    found: List[str] = []
    counts = card.checks
    if counts and counts.get("fail") and report_card.settled(counts):
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
    action: str  # "wait", "wake" or "close"
    reason: str = ""
    events: List[str] = field(default_factory=list)


def in_childs_hands(entry: Dict[str, Any]) -> bool:
    """Woken, and not stopped since: nothing to look at until it stops."""
    woken = entry.get("woken_at")
    return woken is not None and woken >= (entry.get("ended_at") or 0)


def window_over(entry: Dict[str, Any], s: Dict[str, Any], now: float) -> bool:
    return now - float(entry.get("since") or now) >= s["hours"] * 3600


def decide(entry: Dict[str, Any], card: report_card.Card,
           data: Optional[Dict[str, Any]], session: Any, *, now: float,
           s: Dict[str, Any]) -> Decision:
    """What to do about one followed child. Pure: every fact is passed in.

    The order is the argument. A PR that is finished needs nothing whatever
    else is true. A PR with nothing outstanding is waited on. Only a PR that
    needs something is checked against everything that forbids acting, and
    the bounds come last, so a hand-back names the most specific reason.
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

    found = events(card, data, after=float(entry.get("ended_at") or 0))
    if not found:
        if window_over(entry, s, now):
            return Decision("close", "window")
        return Decision("wait")

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


def render_stopped(short_id: str, entry: Dict[str, Any], reason: str,
                   found: Sequence[str], *, s: Dict[str, Any], why: str = "") -> str:
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    said = _REASONS.get(reason, reason).format(
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
) -> PassReport:
    """Look at every followed PR that is due, and act on what needs it.

    Never raises past one child: this runs in a detached process nobody reads,
    and one PR ``gh`` cannot read must not cost the others their look.
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
                            report=report, announce=announce)
                wakes += 1 if woke else 0
            except Exception as exc:  # noqa: BLE001
                report.failed.append(f"{short_id} — {exc}")
    return report


def _one(cfg, vault_root: Path, short_id: str, entry: Dict[str, Any], session: Any,
         *, s, now, run, wake_fn, tell, report: PassReport, announce: bool) -> bool:
    card = report_card.gather(short_id, cwd=entry.get("cwd"), run=run)
    if card.pr is not None:
        entry["pr"] = card.pr.url
        entry["pr_number"] = card.pr.number
    data = None
    if card.pr is not None and card.pr.state == "OPEN":
        data = activity(card.pr.url, run=run)
    decision = decide(entry, card, data, session, now=now, s=s)

    def polled(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["polled_at"] = now
            e["pr"], e["pr_number"] = entry.get("pr"), entry.get("pr_number")
            e["last_events"] = list(decision.events)

    _update(vault_root, polled)

    if decision.action == "close":
        _close(vault_root, short_id, entry, decision.reason, decision.events,
               s=s, now=now, tell=tell, report=report)
        return False
    if decision.action != "wake":
        return False

    number = card.pr.number if card.pr else 0
    try:
        why = wake_fn(entry.get("session_id") or "", cwd=entry.get("cwd") or "",
                      pr=number, events=decision.events)
    except Exception as exc:  # noqa: BLE001
        why = str(exc) or type(exc).__name__
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

    def checked(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["checked_at"] = now
            e["pr"], e["pr_number"] = entry.get("pr"), entry.get("pr_number")

    _update(vault_root, checked)
    if card.pr is not None and card.pr.state in ("MERGED", "CLOSED"):
        _close(vault_root, short_id, entry, card.pr.state.lower(), [],
               s=s, now=now, tell=tell, report=report)


def _close(vault_root: Path, short_id: str, entry: Dict[str, Any], reason: str,
           found: Sequence[str], *, s, now: float, tell, report: PassReport,
           why: str = "") -> None:
    def change(d: Dict[str, Any]) -> None:
        e = d.get("children", {}).get(short_id)
        if isinstance(e, dict):
            e["closed"], e["closed_at"] = reason, now
            e["last_events"] = list(found)

    _update(vault_root, change)
    report.closed.append((short_id, reason))
    if reason in LOUD and found:
        tell(vault_root, entry.get("parent"),
             render_stopped(short_id, entry, reason, found, s=s, why=why))


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
    from mnemo.core.sessions import grants, parents

    short_id = (session_id or "")[:8]
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
