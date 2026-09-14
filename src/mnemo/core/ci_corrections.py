"""The second source of corrections: a red CI run followed by a green one (#272).

The vault has one correction channel — the user typing one — and `mnemo replay`
says what that yields here: eighteen gate-verified correction-backed rules out
of 1830 pages, none of which ever carried into a later session's prompt.
Meanwhile CI corrected the model four times in the same class inside a fortnight
(#233, #236, #243, #255/#176), each with a literal quote: ``'charmap' codec
can't decode byte 0x90``, ``[WinError 2] The system cannot find the file
specified``, ``assert 312 == 310``. Each became a rule only because the
maintainer wrote one by hand.

A red job is a correction with better evidence than most human ones: the
assertion text is exact, the commit that caused it is known, and the push that
turned it green names the fix.

Why this is its own evidence class
----------------------------------

A quote from a log is not the user's words. :data:`VERIFIED_CI` follows the
pattern :data:`mnemo.core.share.format.VERIFIED_ELSEWHERE` established — every
surface that speaks in the user's voice (``hooks/session_start``,
``cli/commands/learn``, ``cli/commands/status``) compares ``== "verified"``, so
a distinct literal is excluded from all of them with no change at those call
sites.

Why a CI page is a ``reference`` and not ``feedback``
----------------------------------------------------

``extract/evidence.verify_page`` demotes any ``feedback`` page whose quote is
absent from one of its own briefings' ``## Corrections`` **and sets
``evidence=None``**. A CI quote lives in a log, so it can never clear that bar;
routed through the gate as ``feedback`` it would be stripped of the very
evidence this module exists to keep. ``reference`` is the honest slot — real
knowledge nobody typed — and it is the one type ``verify_page`` only ever
promotes out of, never strips. Lowering the human gate was explicitly not on
the table, and nothing here touches it.

Network
-------

Reading a GitHub run leaves the machine, so the ``gh`` path sits behind
``autopilot.network.enabled`` like every other GitHub call
(``autopilot/core/network.py``). :func:`parse_failures` takes text, so the same
extraction runs against a local ``pytest`` red→green pair with no network at
all — the maintainer's own loop.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from mnemo.core.extract.inbox.types import ExtractedPage

#: What a rule's ``confidence`` is when its quote came from a CI log rather
#: than from the user. Every "you said" surface compares ``== "verified"``, so
#: this value excludes the rule from them with no further change — the same
#: mechanism ``share.format.VERIFIED_ELSEWHERE`` uses for imported rules.
VERIFIED_CI = "verified-ci"

#: The ``origin`` axis ``mnemo replay`` splits correction-backed rules along.
ORIGIN_USER = "user"
ORIGIN_CI = "ci"

#: pytest's summary line, with or without the three tab-separated columns a
#: `gh run view --log-failed` line carries in front of it. Anchored at the
#: start of the line on purpose: prose that merely mentions FAILED is not a
#: result.
_FAILURE_RE = re.compile(
    r"^(?:[^\t\n]*\t[^\t\n]*\t(?:\d{4}-\d{2}-\d{2}T[\d:.]+Z)?\s*)?"
    r"(?:FAILED|ERROR)\s+(?P<test>\S+?)(?:\s+-\s+(?P<message>.*))?$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class Failure:
    """One failing test and the assertion text that is its quote."""

    test: str
    message: str
    file: str


@dataclass(frozen=True)
class RunPair:
    """A red run and the green one that followed it on the same branch."""

    red_sha: str
    green_sha: str
    red_run_id: str = ""
    branch: str = ""


def parse_failures(log: str) -> list[Failure]:
    """Every distinct failing test in ``log``, in the order first seen.

    Accepts both shapes: ``gh run view --log-failed`` output (runner, step and
    ISO timestamp in tab-separated columns) and a bare local ``pytest`` run. A
    matrix of runners reports one bug once per runner, so identical failures
    collapse — keyed on the test id, since the same test can report a different
    message per platform and the first is as good a quote as any.

    A log with no summary line yields nothing. Two of the thirteen red branches
    in this repo's history never reached pytest; a run that died at install has
    no assertion to quote, and inventing one is exactly the fabrication this
    evidence class exists to prevent.
    """
    seen: dict[str, Failure] = {}
    for m in _FAILURE_RE.finditer(log or ""):
        test = m.group("test") or ""
        message = (m.group("message") or "").strip()
        if not test or test in seen:
            continue
        seen[test] = Failure(test=test, message=message, file=test.split("::", 1)[0])
    return list(seen.values())


def pair_runs(runs: Sequence[dict]) -> Optional[RunPair]:
    """The last red run before the newest green one, or ``None``.

    A red run with no green successor is not a pair and teaches nothing: until
    a push turns it green nobody knows what the fix is, or whether the test
    itself was wrong. Where a branch went red several times before it went
    green (``fix/issue-255`` did, three times), the fix that matters is the
    diff against the *final* red.
    """
    ordered = sorted(runs or [], key=lambda r: str(r.get("createdAt") or ""))
    green = None
    for run in reversed(ordered):
        if str(run.get("conclusion") or "") == "success":
            green = run
            break
    if green is None:
        return None

    green_at = str(green.get("createdAt") or "")
    red = None
    for run in ordered:
        if str(run.get("conclusion") or "") != "failure":
            continue
        if str(run.get("createdAt") or "") < green_at:
            red = run
    if red is None:
        return None

    return RunPair(
        red_sha=str(red.get("headSha") or ""),
        green_sha=str(green.get("headSha") or ""),
        red_run_id=str(red.get("databaseId") or ""),
        branch=str(red.get("headBranch") or ""),
    )


def origin_of(confidence: str) -> Optional[str]:
    """Which channel corrected the model, for ``mnemo replay``'s origin axis.

    ``None`` for a page no correction backs at all, so a caller can tell the
    three states apart without repeating the literals.
    """
    value = str(confidence or "")
    if value == "verified":
        return ORIGIN_USER
    if value == VERIFIED_CI:
        return ORIGIN_CI
    return None


def _slug_for(failure: Failure) -> str:
    """A stable slug from the test id, so the same failure re-extracts onto
    the same page instead of accumulating near-duplicates."""
    tail = failure.test.split("::")[-1]
    tail = re.sub(r"^test_", "", tail)
    slug = re.sub(r"[^a-z0-9]+", "-", tail.lower()).strip("-")
    return f"ci-{slug}"[:80] if slug else "ci-failure"


def to_page(
    failure: Failure,
    fixed_files: Sequence[str],
    branch: str,
    red_sha: str,
    green_sha: str,
    run_url: str,
) -> ExtractedPage:
    """The rule a red→green pair implies, as a page the vault can stage.

    The quote is the assertion text exactly as the log carried it; the source
    is the run it came from, not a briefing, which is what keeps it out of
    every surface that speaks in the user's voice.
    """
    files = [f for f in (fixed_files or []) if f]
    body = (
        f"`{failure.test}` failed in CI and the next push turned it green.\n\n"
        f"**What CI said:** `{failure.message}`\n\n"
        f"**What the fix touched:** "
        + (", ".join(f"`{f}`" for f in files) if files else "not recorded")
        + "\n\n**Why:** the failure is a correction with an exact quote — the "
        "assertion text above is what the runner printed, not a summary of it. "
        "The fix that turned the job green is the rule this page carries.\n\n"
        "**How to apply:** before changing "
        + (", ".join(f"`{f}`" for f in files) if files else "this area")
        + f", re-read what `{failure.file}` asserts; that test is the one that "
        "caught this class of mistake once already.\n"
    )
    return ExtractedPage(
        slug=_slug_for(failure),
        type="reference",
        name=f"CI: {failure.message[:60]}" if failure.message else f"CI: {failure.test}",
        description=f"{failure.test} went red then green on {branch or 'a branch'}.",
        body=body,
        source_files=[],
        source_hash="",
        tags=["ci", "testing"],
        evidence={
            "quote": failure.message,
            "source": run_url,
            "test": failure.test,
            "red": red_sha,
            "green": green_sha,
        },
        confidence=VERIFIED_CI,
    )


# --- collecting a pair, with and without the network ---------------------------

def rules_from_local_run(
    red_log: str,
    green_log: str,
    fixed_files: Sequence[str],
    branch: str = "",
    red_sha: str = "",
    green_sha: str = "",
) -> list[ExtractedPage]:
    """The no-network path: two local ``pytest`` logs and the files between them.

    Nothing here leaves the machine, so no gate applies. A failure that is still
    present in ``green_log`` taught nothing — the run is not green for it — and
    is dropped, which is what keeps a flaky test from minting a rule every time
    it happens to pass.
    """
    still_failing = {f.test for f in parse_failures(green_log)}
    return [
        to_page(
            failure,
            fixed_files=fixed_files,
            branch=branch,
            red_sha=red_sha,
            green_sha=green_sha,
            run_url=f"local pytest {red_sha or 'red'}→{green_sha or 'green'}",
        )
        for failure in parse_failures(red_log)
        if failure.test not in still_failing
    ]


def rules_from_github(
    branch: str,
    *,
    run_json: Optional[Sequence[dict]] = None,
    fetch_log: Optional[Any] = None,
    fetch_runs: Optional[Any] = None,
    changed_files: Optional[Any] = None,
    cfg: Optional[dict] = None,
) -> list[ExtractedPage]:
    """The networked path: a branch's runs, its last red log, and the fix diff.

    Reading a run leaves the machine, so this refuses to do anything unless
    ``autopilot.network.enabled`` is on — the same switch every other GitHub
    call in the autopilot sits behind. The three ``fetch_*`` seams exist so the
    parsing above can be tested without a network at all; in production they
    default to ``gh``/``git``.
    """
    from mnemo.autopilot.core import network

    if not network.enabled(cfg):
        return []

    runs = run_json if run_json is not None else (fetch_runs or _gh_runs)(branch)
    pair = pair_runs(runs or [])
    if pair is None:
        return []

    log = (fetch_log or _gh_failed_log)(pair.red_run_id)
    failures = parse_failures(log)
    if not failures:
        return []

    files = (changed_files or _git_changed_files)(pair.red_sha, pair.green_sha)
    run_url = f"https://github.com/actions/runs/{pair.red_run_id}"
    return [
        to_page(
            failure,
            fixed_files=files,
            branch=branch or pair.branch,
            red_sha=pair.red_sha,
            green_sha=pair.green_sha,
            run_url=run_url,
        )
        for failure in failures
    ]


def _run(cmd: Sequence[str]) -> str:
    import subprocess

    try:
        result = subprocess.run(
            list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except (FileNotFoundError, OSError):
        return ""
    return result.stdout or ""


def _gh_runs(branch: str) -> list[dict]:
    import json

    raw = _run([
        "gh", "run", "list", "--branch", branch, "--limit", "30",
        "--json", "databaseId,headBranch,headSha,conclusion,createdAt",
    ])
    try:
        return json.loads(raw) if raw.strip() else []
    except ValueError:
        return []


def _gh_failed_log(run_id: str) -> str:
    return _run(["gh", "run", "view", str(run_id), "--log-failed"])


def _git_changed_files(red_sha: str, green_sha: str) -> list[str]:
    if not red_sha or not green_sha:
        return []
    out = _run(["git", "diff", "--name-only", f"{red_sha}..{green_sha}"])
    return [line.strip() for line in out.splitlines() if line.strip()]
