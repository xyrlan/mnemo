"""What the parent is told when a dispatched child finishes (#426).

Since #357 a child that exits posts a notice into the session that dispatched
it. That notice said only "finished" and pointed at ``mnemo sessions``, so the
parent fetched the rest itself, every time: on 2026-09-22, 56 of 58 notices on
disk were followed by a lookup of the queue, the PR or its checks, and 12 by a
wait for CI (``tools/measure_notice_followups.py``). The facts it went for are
the same each time — which PR, is it a draft, how big, are the checks green,
what did the child say — and every one of them is on disk or one ``gh`` call
away at the moment the child exits. So the notice carries them.

**A card is facts, never a verdict on the work.** Each line is read from git,
from ``gh`` or from the child's transcript, and a line whose source failed is
left out rather than filled in (#306: a notice that guessed would be the
unbacked report that issue exists to prevent). :func:`state` names the
situation those facts add up to — ``ready`` means the PR is open, not a draft
and every check passed, not that the change is right. Whether it is right is
the review, and a card cannot replace review.

**The closing report is the child's own words.** Its prompt tells it to write
one last, as "the only copy", so the last text it wrote is that report. It is
quoted as the child's and bounded; mnemo does not check it.

**Nothing here approves anything.** The text rides a peer socket, and #309
settled that a socket message is never the maintainer. Every notice opens
with :data:`~mnemo.core.sessions.inbox.NOTICE_PREFIX`, which also keeps it out
of the unblock detector.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: ``run(argv, cwd) -> (returncode, stdout, stderr)``. Raises nothing: a
#: missing binary is a non-zero return. Injected so tests never touch git or
#: ``gh``.
Runner = Callable[[Sequence[str], Optional[str]], Tuple[int, str, str]]

#: How much of the closing report rides in the notice. Reports on disk run
#: 2,500-3,600 characters and open with the summary; the rest is a path away.
REPORT_CHARS = 1500

#: ``gh pr checks --json bucket`` vocabulary. Pending is the only unsettled one.
BUCKETS = ("pass", "fail", "pending", "skipping", "cancel")

#: The states a card can be in, in the order :func:`state` checks them.
STATES = (
    "unknown", "no-change", "unpublished", "merged", "closed", "draft",
    "ci-red", "ci-running", "ready",
)

_PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
_PR_FIELDS = "number,url,state,isDraft,additions,deletions,changedFiles,headRefOid"


def _run(argv: Sequence[str], cwd: Optional[str]) -> Tuple[int, str, str]:
    try:
        result = subprocess.run(
            list(argv), cwd=cwd, capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return result.returncode, result.stdout or "", result.stderr or ""


@dataclass
class PR:
    number: int
    url: str
    state: str  # gh's own: OPEN, MERGED, CLOSED
    draft: bool = False
    additions: Optional[int] = None
    deletions: Optional[int] = None
    files: Optional[int] = None
    head: str = ""


@dataclass
class Card:
    short_id: str
    target: object = None  # issue number, piece slug, or None
    pr: Optional[PR] = None
    #: bucket -> count, or ``None`` when the checks could not be read.
    checks: Optional[Dict[str, int]] = None
    failing: List[str] = field(default_factory=list)
    #: False when the tree is gone or unreadable; the flags below are git
    #: facts only when it is True.
    tree_seen: bool = False
    clean: bool = True
    ahead: int = 0
    unpushed: bool = False
    report: str = ""
    report_chars: int = 0
    transcript: str = ""

    @property
    def label(self) -> str:
        if isinstance(self.target, int):
            return f"#{self.target}"
        return str(self.target) if self.target else ""


# ---------------------------------------------------------------------------
# Reading the facts
# ---------------------------------------------------------------------------


def closing_report(transcript: Optional[Path]) -> str:
    """The last text the child wrote, or ``""``.

    The dispatch prompt's closing steps are: write the report, publish, stop.
    The report is therefore the last text block; what follows it is tool
    calls (push, ``gh pr create``, ``claude stop``) with no prose. A child cut
    off by a rate limit ends on Claude Code's own "You've hit your session
    limit" line instead, which is exactly what a parent should read then.
    """
    if not transcript:
        return ""
    last = ""
    try:
        with open(transcript, encoding="utf-8") as fh:
            for line in fh:
                if '"assistant"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(record, dict) or record.get("type") != "assistant":
                    continue
                for block in (record.get("message") or {}).get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = str(block.get("text") or "").strip()
                        if text:
                            last = text
    except OSError:
        return ""
    return last


def pr_urls_in(transcript: Optional[Path]) -> List[str]:
    """PR URLs ``gh pr create`` printed in the transcript, oldest first.

    The fallback when the tree is gone and the branch cannot be read: the
    child's own ``gh pr create`` output is the one place the URL is on disk.
    Only tool results are read, so a URL the child merely quoted does not
    count.
    """
    if not transcript:
        return []
    found: List[str] = []
    try:
        with open(transcript, encoding="utf-8") as fh:
            for line in fh:
                if "tool_result" not in line or "/pull/" not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                content = (record.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    body = block.get("content")
                    text = body if isinstance(body, str) else json.dumps(body)
                    for url in _PR_URL.findall(text):
                        if url not in found:
                            found.append(url)
    except OSError:
        return []
    return found


def _pr_from(row: object) -> Optional[PR]:
    if not isinstance(row, dict):
        return None
    try:
        number = int(row.get("number"))
    except (TypeError, ValueError):
        return None

    def _int(key: str) -> Optional[int]:
        value = row.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    return PR(
        number=number,
        url=str(row.get("url") or f"#{number}"),
        state=str(row.get("state") or ""),
        draft=bool(row.get("isDraft")),
        additions=_int("additions"),
        deletions=_int("deletions"),
        files=_int("changedFiles"),
        head=str(row.get("headRefOid") or ""),
    )


def pr_for_branch(branch: str, *, cwd: str, run: Runner = _run) -> Optional[PR]:
    """The newest PR opened from *branch*, any state, or ``None``."""
    code, out, _ = run(
        ["gh", "pr", "list", "--head", branch, "--state", "all",
         "--json", _PR_FIELDS, "--limit", "1"],
        cwd,
    )
    if code != 0:
        return None
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None
    return _pr_from(rows[0]) if isinstance(rows, list) and rows else None


def pr_by_url(url: str, *, run: Runner = _run) -> Optional[PR]:
    code, out, _ = run(["gh", "pr", "view", url, "--json", _PR_FIELDS], None)
    if code != 0:
        return None
    try:
        return _pr_from(json.loads(out or "{}"))
    except ValueError:
        return None


def checks(pr: str, *, cwd: Optional[str] = None, run: Runner = _run
           ) -> Tuple[Optional[Dict[str, int]], List[str]]:
    """``(bucket counts, failing check names)`` for *pr*, or ``(None, [])``.

    Read check by check, as ``landing.failing_checks`` does, never from the
    rollup: a non-blocking job fails while the rollup reports success. Exit 8
    is ``gh``'s "some checks pending" and still carries the rows. A PR with no
    checks at all is ``{}`` — known and empty — which is not the same answer
    as ``None``, which is "could not ask".
    """
    code, out, err = run(["gh", "pr", "checks", pr, "--json", "name,bucket"], cwd)
    if code not in (0, 8):
        # gh exits 1 and says "no checks reported" on a PR that has none (yet).
        # Any other exit is a failure to ask, and must not read as "none".
        return ({}, []) if "no checks reported" in err else (None, [])
    try:
        rows = json.loads(out or "[]")
    except ValueError:
        return None, []
    if not isinstance(rows, list):
        return None, []
    counts: Dict[str, int] = {}
    failing: List[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        bucket = str(row.get("bucket") or "pending")
        counts[bucket] = counts.get(bucket, 0) + 1
        if bucket == "fail":
            failing.append(str(row.get("name") or "?"))
    return counts, failing


def _git(args: Sequence[str], tree: Path, run: Runner) -> Tuple[int, str]:
    code, out, _ = run(["git", *args], str(tree))
    return code, out


def gather(
    short_id: str,
    *,
    cwd: Optional[str],
    transcript: Optional[Path] = None,
    run: Runner = _run,
) -> Card:
    """Every fact the notice can carry about *short_id*'s work. Never raises.

    The branch comes from the tree when it still exists; a child that ran in
    a tree someone already removed falls back to the PR URL its own ``gh pr
    create`` printed. Each lookup that fails leaves its field empty.
    """
    from mnemo.core.dispatch import issue_for_cwd

    card = Card(short_id=short_id, transcript=str(transcript or ""))
    try:
        card.target = issue_for_cwd(cwd)
        report = closing_report(transcript)
        card.report_chars = len(report)
        card.report = report

        tree = Path(cwd) if cwd else None
        branch = None
        if tree is not None and tree.is_dir():
            code, out = _git(["rev-parse", "--abbrev-ref", "HEAD"], tree, run)
            name = out.strip()
            if code == 0 and name and name != "HEAD":
                branch = name
                card.tree_seen = True
                code, out = _git(["status", "--porcelain"], tree, run)
                card.clean = code == 0 and not out.strip()

        if branch:
            card.pr = pr_for_branch(branch, cwd=str(tree), run=run)
        if card.pr is None:
            urls = pr_urls_in(transcript)
            if urls:
                card.pr = pr_by_url(urls[-1], run=run)

        if card.tree_seen and tree is not None:
            if card.pr is not None and card.pr.head:
                code, out = _git(["rev-parse", "HEAD"], tree, run)
                card.unpushed = code == 0 and out.strip() != card.pr.head
            elif card.pr is None:
                card.ahead = _ahead(tree, branch or "", run)

        if card.pr is not None and card.pr.state == "OPEN":
            card.checks, card.failing = checks(
                card.pr.url, cwd=str(tree) if card.tree_seen else None, run=run,
            )
    except Exception:  # noqa: BLE001 — a card is a convenience; never fail the notice
        pass
    return card


def _ahead(tree: Path, branch: str, run: Runner) -> int:
    """Commits on *branch* that its upstream's default branch lacks, or 0."""
    from mnemo.core.sessions.delivery import base_branch

    try:
        base = base_branch(repo_root=tree)
    except Exception:  # noqa: BLE001
        return 0
    for ref in (base, f"origin/{base}"):
        code, out = _git(["rev-list", "--count", f"{ref}..{branch}"], tree, run)
        if code == 0:
            try:
                return int(out.strip())
            except ValueError:
                return 0
    return 0


# ---------------------------------------------------------------------------
# What the facts add up to
# ---------------------------------------------------------------------------


def state(card: Card) -> str:
    """One name for the situation the facts describe. See :data:`STATES`.

    Local work outranks the PR: a tree with uncommitted changes, or a branch
    whose head the PR does not have, means the PR is not the whole of what the
    child did, whatever its checks say.
    """
    if card.tree_seen and (not card.clean or card.unpushed):
        return "unpublished"
    pr = card.pr
    if pr is None:
        if card.tree_seen:
            return "unpublished" if card.ahead > 0 else "no-change"
        return "unknown"
    if pr.state == "MERGED":
        return "merged"
    if pr.state == "CLOSED":
        return "closed"
    if pr.draft:
        return "draft"
    if card.checks is None:
        return "unknown"
    if card.checks.get("fail"):
        return "ci-red"
    if card.checks.get("pending") or not card.checks:
        # No checks yet, a few seconds after `gh pr create`, is CI that has
        # not registered — not a repository without CI. The watch settles it.
        return "ci-running"
    return "ready"


def settled(counts: Optional[Dict[str, int]]) -> bool:
    return bool(counts) and not counts.get("pending")


# ---------------------------------------------------------------------------
# The text
# ---------------------------------------------------------------------------


def _counts(counts: Dict[str, int]) -> str:
    parts = [f"{counts[b]} {b}" for b in BUCKETS if counts.get(b)]
    return ", ".join(parts) if parts else "none reported"


def _size(pr: PR) -> str:
    if pr.additions is None or pr.deletions is None:
        return ""
    files = f" in {pr.files} file{'s' if pr.files != 1 else ''}" if pr.files is not None else ""
    return f", +{pr.additions} −{pr.deletions}{files}"


def _pr_line(pr: PR) -> str:
    if pr.state == "OPEN":
        status = "open, draft" if pr.draft else "open, ready for review"
    else:
        status = pr.state.lower() or "state unknown"
    return f"- PR #{pr.number} {status}{_size(pr)} — {pr.url}"


def _tree_line(card: Card) -> str:
    if not card.tree_seen:
        return "- tree: gone or unreadable"
    facts = []
    if not card.clean:
        facts.append("uncommitted changes")
    if card.unpushed:
        facts.append("HEAD is not the PR's head")
    if card.pr is None:
        facts.append(
            f"{card.ahead} commit{'s' if card.ahead != 1 else ''} not in any PR"
            if card.ahead else "no commits ahead"
        )
    return "- tree: " + (", ".join(facts) if facts else "clean, pushed")


def _report_lines(card: Card) -> List[str]:
    if not card.report:
        return ["- closing report: none found in the transcript"]
    text = card.report
    cut = len(text) > REPORT_CHARS
    if cut:
        head = text[:REPORT_CHARS]
        # End on a line boundary when there is one in the second half.
        nl = head.rfind("\n")
        text = head[:nl] if nl > REPORT_CHARS // 2 else head
    lead = "- closing report, the child's own words (mnemo did not check them)"
    lead += f", first {len(text)} of {card.report_chars} characters:" if cut else ":"
    lines = [lead]
    lines.extend(f"  > {line}" if line else "  >" for line in text.splitlines())
    if cut and card.transcript:
        lines.append(f"  (the rest: {card.transcript})")
    return lines


def render(card: Card, *, watch_minutes: int = 0) -> str:
    """The notice for a child that just finished."""
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    name = state(card)
    target = f" ({card.label})" if card.label else ""
    lines = [
        f'{NOTICE_PREFIX} id="{card.short_id}" state="{name}">',
        f"{card.short_id} finished{target}. mnemo is reporting a dispatched "
        f"child's exit; this is not your user speaking.",
    ]
    if card.pr is not None:
        lines.append(_pr_line(card.pr))
        if card.checks is not None:
            line = f"- checks: {_counts(card.checks)}"
            if card.failing:
                line += " — failing: " + ", ".join(card.failing)
            if name == "ci-running":
                line += (
                    f"; mnemo will post again when they settle (watching up to "
                    f"{watch_minutes} min)" if watch_minutes > 0
                    else "; mnemo is not watching them (dispatch.watchChecksMinutes is 0)"
                )
            lines.append(line)
        elif card.pr.state == "OPEN":
            lines.append("- checks: could not be read")
    else:
        lines.append("- PR: none found for this child")
    lines.append(_tree_line(card))
    lines.extend(_report_lines(card))
    lines.append("Review and merge stay with the maintainer; `mnemo sessions` has the queue.")
    return "\n".join(lines)


def render_checks(
    card: Card, counts: Optional[Dict[str, int]], failing: Sequence[str],
    *, timed_out_after: int = 0,
) -> str:
    """The follow-up once the checks settle, or once the watch gives up."""
    from mnemo.core.sessions.inbox import NOTICE_PREFIX

    pr = card.pr
    number = f"PR #{pr.number}" if pr else "its PR"
    if timed_out_after:
        name = "ci-running"
        head = (
            f"{card.short_id}'s {number}: checks still running after "
            f"{timed_out_after} min; mnemo stopped watching."
        )
    elif counts is None:
        name = "unknown"
        head = f"{card.short_id}'s {number}: checks could not be read; mnemo stopped watching."
    elif not counts:
        name = "ready" if pr and not pr.draft else state(card)
        head = f"{card.short_id}'s {number}: no checks reported."
    else:
        name = "ci-red" if counts.get("fail") else "ready"
        head = f"{card.short_id}'s {number}: checks settled — {_counts(counts)}."
    lines = [
        f'{NOTICE_PREFIX} id="{card.short_id}" state="{name}" event="checks">',
        head + " mnemo is reporting CI, not your user speaking.",
    ]
    if failing:
        lines.append("- failing: " + ", ".join(failing))
    if pr:
        lines.append(f"- {pr.url}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The watch, and the record of what was sent
# ---------------------------------------------------------------------------

#: ``dispatch.watchChecksMinutes``: how long the detached reporter waits for a
#: child's checks to settle before it gives up and says so. 0 turns it off.
DEFAULT_WATCH_MINUTES = 30
#: A bound on a typo: nothing should hold a poller for a day.
MAX_WATCH_MINUTES = 180
#: Between polls. mnemo's own suite takes ~5 minutes on the slowest runner,
#: so a 30 s grain costs ten ``gh`` calls a PR and reports within half a minute.
POLL_SECONDS = 30.0
#: A PR that still has no checks this long after it opened has none coming —
#: a repository without CI — as opposed to CI that has not registered yet.
NO_CHECKS_GRACE_SECONDS = 180.0
#: Consecutive unreadable answers before the watch stops: a lost network or a
#: revoked token must not keep a process polling for half an hour.
MAX_READ_FAILURES = 5

LOG_NAME = "child-reports.jsonl"


def watch_minutes(cfg: Optional[dict]) -> int:
    """The configured watch, bounded; a value nobody meant is the default."""
    raw = ((cfg or {}).get("dispatch") or {}).get("watchChecksMinutes", DEFAULT_WATCH_MINUTES)
    if isinstance(raw, bool):
        return DEFAULT_WATCH_MINUTES
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_WATCH_MINUTES
    return max(0, min(minutes, MAX_WATCH_MINUTES))


def watch_checks(
    card: Card,
    *,
    minutes: int,
    alive: Callable[[], bool],
    post: Callable[[str], bool],
    run: Runner = _run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    interval: float = POLL_SECONDS,
    grace: float = NO_CHECKS_GRACE_SECONDS,
) -> str:
    """Poll *card*'s PR checks until they settle, then post once.

    Returns what ended the watch: ``settled``, ``no-checks``, ``timeout``,
    ``unreadable`` or ``parent-gone``. Exactly one notice is posted on every
    ending except ``parent-gone``, where nobody is left to read it — so a
    parent is never promised a follow-up that silently fails to come.
    """
    if card.pr is None or minutes <= 0:
        return "not-watched"
    start = clock()
    deadline = start + minutes * 60
    failures = 0
    counts: Optional[Dict[str, int]] = card.checks
    failing: List[str] = list(card.failing)
    while True:
        if clock() + interval > deadline:
            post(render_checks(card, counts, failing, timed_out_after=minutes))
            return "timeout"
        sleep(interval)
        if not alive():
            return "parent-gone"
        counts, failing = checks(card.pr.url, run=run)
        if counts is None:
            failures += 1
            if failures >= MAX_READ_FAILURES:
                post(render_checks(card, None, []))
                return "unreadable"
            continue
        failures = 0
        if settled(counts):
            post(render_checks(card, counts, failing))
            return "settled"
        if not counts and clock() - start >= grace:
            post(render_checks(card, {}, []))
            return "no-checks"


def record(vault_root: Path, row: Dict[str, object]) -> None:
    """Append one row to ``.mnemo/child-reports.jsonl``. Never raises.

    What was sent, to whom, in which state, and whether it went onto a socket
    — so a later run of ``tools/measure_notice_followups.py`` can be read
    against what the parents were actually told.
    """
    try:
        path = Path(vault_root) / ".mnemo" / LOG_NAME
        path.parent.mkdir(parents=True, exist_ok=True)
        stamped = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **row}
        with open(path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(stamped, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass
