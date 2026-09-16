# The Child Closes Its Own Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dispatched child ends itself — report, publish when `git` says there is work, then stop — so `SessionEnd` fires and its briefing is written.

**Architecture:** Four independent changes. (1) A closing clause in the child's opening prompt tells it to report, check `git`, publish, and stop itself. (2) `--may pr` becomes the default when nothing is said. (3) `mnemo deliver` gains `--stop-done`, stopping a finished child that has nothing to deliver, and `--review` shows which children are holding memory. (4) `mnemo land` refuses a piece whose PR has a failing check, reading individual checks rather than the rollup.

**Tech Stack:** Python 3.8+, pytest, `gh` CLI, Claude Code CLI.

**Spec:** `docs/superpowers/specs/2026-09-16-child-closes-its-own-loop-design.md`

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/mnemo/core/dispatch.py` | the child's opening prompt | add `_CLOSING_PROMPT` + `_closing_clause()`; render in both templates |
| `src/mnemo/cli/commands/dispatch.py` | flag → grant | default to `pr` when `--may` is absent |
| `src/mnemo/core/sessions/delivery.py` | git/gh/session actions | add `stop_done_in()` |
| `src/mnemo/cli/commands/deliver.py` | the repair command | add `--stop-done`; show `done`-not-`stopped` in `--review` |
| `src/mnemo/cli/parser.py` | argument surface | register `--stop-done` |
| `src/mnemo/core/landing.py` | the merge gate | add `failing_checks()`; fill `PieceState.reason` |
| `tests/unit/test_dispatch_closing.py` | **new** | closing clause + default grant |
| `tests/unit/test_deliver_stop_done.py` | **new** | `--stop-done` behaviour |
| `tests/unit/test_landing_checks.py` | **new** | the CI gate |

Tasks 1–4 are independent and may be done in any order. Task 5 is documentation and comes last.

---

### Task 1: The closing clause in the child's prompt

The child is told to report, consult `git`, publish when there is work, and stop itself. This is the whole of the spec's main path.

**Files:**
- Modify: `src/mnemo/core/dispatch.py` (add `_CLOSING_PROMPT`, `_closing_clause`, render into `_PROMPT` and `_PIECE_PROMPT`)
- Test: `tests/unit/test_dispatch_closing.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_dispatch_closing.py`:

```python
"""The child is told how to end itself (2026-09-16 design)."""
from mnemo.core import dispatch


def test_closing_clause_names_the_self_stop_command():
    text = dispatch._closing_clause()
    assert "claude stop" in text
    assert "${CLAUDE_CODE_SESSION_ID:0:8}" in text


def test_closing_clause_orders_report_before_stop():
    text = dispatch._closing_clause()
    assert text.index("report") < text.index("claude stop")


def test_closing_clause_defers_to_git_not_to_judgement():
    text = dispatch._closing_clause()
    assert "git" in text
    # The child must not decide "there is nothing to deliver" on its own.
    assert "commit" in text


def test_issue_prompt_carries_the_closing_clause():
    prompt = dispatch.build_prompt(
        211, title="t", body="b", may=("push", "pr"),
    )
    assert "claude stop" in prompt


def test_piece_prompt_carries_the_closing_clause():
    from mnemo.core import contracts

    piece = contracts.Piece(
        slug="parser",
        files=["src/mnemo/core/contracts.py"],
        exposes=["parse_contract(path) -> Contract"],
    )
    prompt = dispatch.build_piece_prompt(
        piece, feature="contract-dispatch", may=("push", "pr"),
    )
    assert "claude stop" in prompt


def test_refusing_child_is_still_told_to_report_and_stop():
    """A child with no grant still ends itself — the stop is unconditional."""
    prompt = dispatch.build_prompt(211, title="t", body="b", may=())
    assert "claude stop" in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_dispatch_closing.py -v`

Expected: FAIL — `AttributeError: module 'mnemo.core.dispatch' has no attribute '_closing_clause'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/dispatch.py`, immediately after the `NO_GRANT` definition (around line 280), add:

```python
#: How every child ends, whatever it decided (2026-09-16 design). Stated in the
#: opening prompt for the same reason the publish grant is: this is the one
#: message the child reads as the maintainer's own, and nothing that arrives
#: later carries the same authority.
#:
#: The order is load-bearing. The report reaches the transcript before the
#: stop, and ``SessionEnd`` — which only a *stopped* child fires (#247) — is
#: what turns that transcript into a briefing. Stopping first would end the
#: session with nothing to write down; not stopping at all is the state this
#: replaces, where the report is written and never becomes memory.
_CLOSING_PROMPT = """
How to finish, whatever you decided:

1. Write your closing report in this session — what you did, what you
   decided and why, what you refused. This is the only copy: it becomes the
   session's briefing.
2. Ask git whether there is work to publish: a clean tree on your own branch
   with at least one commit ahead of the base. If there is{publish_hint}. If
   there is not — you refused the task, or it needed no change — publish
   nothing. Do not decide this from memory; run git and read it.
3. Stop yourself, last: `claude stop ${{CLAUDE_CODE_SESSION_ID:0:8}}`. Your
   conversation is kept. Nothing else stops you, and a session left running
   writes no briefing at all.
"""


def _closing_clause(may: grants.Grant = ()) -> str:
    """The end-of-life instructions every child gets, grant or no grant.

    *may* only decides how step 2 is phrased — whether publishing is something
    this child may do unasked. The report and the stop do not depend on it:
    a child that refused the task has no commits and publishes nothing, but
    its reasoning is the most valuable briefing in the system, because no diff
    carries it.
    """
    if "pr" in may:
        hint = ", push it and open the pull request you were granted"
    elif "push" in may:
        hint = ", push it (do not open a pull request)"
    else:
        hint = ", say so in your report and leave it for `mnemo deliver`"
    return _CLOSING_PROMPT.format(publish_hint=hint)
```

- [ ] **Step 4: Render it into both prompt templates**

In `src/mnemo/core/dispatch.py`, change the `_PROMPT` template's final paragraph so the closing clause follows it. Replace the closing line of `_PROMPT`:

```python
faithful implementation of a bad plan, and reporting that is finishing the
job, not failing it.
"""
```

with:

```python
faithful implementation of a bad plan, and reporting that is finishing the
job, not failing it.
{closing}"""
```

In `build_prompt`, add `closing=_closing_clause(may),` to the `_PROMPT.format(...)` call, after the existing `publish=` argument.

Do the same for the piece path: append `{closing}` to the end of `_PIECE_PROMPT`, and add `closing=_closing_clause(may),` to the `_PIECE_PROMPT.format(...)` call inside `build_piece_prompt`.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_dispatch_closing.py -v`

Expected: PASS (6 tests)

- [ ] **Step 6: Run the existing dispatch tests — prompt text is asserted elsewhere**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_dispatch.py tests/unit/test_dispatch_may.py -v`

Expected: PASS. If a test asserts the prompt's exact ending, update it to expect the closing clause — that assertion is now describing the old contract.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/dispatch.py tests/unit/test_dispatch_closing.py
git commit -m "feat(dispatch): tell the child how to end itself

Report, ask git whether there is work, publish, then stop. Only a
stopped child fires SessionEnd, which is where its briefing is written;
6 of 63 children ever wrote one.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `pr` becomes the default grant

**Files:**
- Modify: `src/mnemo/cli/commands/dispatch.py:70`
- Modify: `src/mnemo/cli/parser.py` (the `--may` help text)
- Test: `tests/unit/test_dispatch_closing.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dispatch_closing.py`:

```python
def test_absent_may_flag_defaults_to_pr():
    """Nothing said means the child publishes (2026-09-16 design)."""
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant(None) == ("push", "pr")


def test_explicit_none_still_withholds():
    """`--may none` is how a maintainer opts out; it must survive the default."""
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant("none") == ()


def test_explicit_push_is_not_upgraded():
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant("push") == ("push",)


def test_a_contract_piece_can_still_withhold_against_the_default():
    """`piece_grant` already resolves precedence; the new default flows
    through it as the flag's value, so `may: none` on a piece must still win."""
    from mnemo.core import contracts, dispatch
    from mnemo.cli.commands import dispatch as cmd

    default = cmd._default_grant(None)
    spike = contracts.Piece(slug="spike", files=["x.py"], may=())
    normal = contracts.Piece(slug="normal", files=["y.py"])

    assert dispatch.piece_grant(spike, default) == ()
    assert dispatch.piece_grant(normal, default) == ("push", "pr")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_dispatch_closing.py -k default -v`

Expected: FAIL — `AttributeError: ... has no attribute '_default_grant'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/cli/commands/dispatch.py`, add above `cmd_dispatch`:

```python
def _default_grant(value: str | None) -> grants.Grant:
    """What a child may publish, defaulting to ``pr`` when nothing was said.

    The default inverted on 2026-09-16. It used to be ``()`` — "Do not merge
    or push without asking" — which left the child finished, unpublished and
    running, and a child that never stops never writes a briefing. Every grant
    ever recorded in the vault was ``push`` or ``push,pr``, so this matches
    what dispatch was already used for rather than changing it.

    ``--may none`` is untouched and still withholds: the opt-out has to
    survive the default, or a spike that should not become a branch has
    nowhere to go.
    """
    if value is None:
        return grants.parse("pr")
    return grants.parse(value)
```

Then change line 70 from:

```python
        may = grants.parse(getattr(args, "may", None))
```

to:

```python
        may = _default_grant(getattr(args, "may", None))
```

- [ ] **Step 4: Update the flag's help text**

In `src/mnemo/cli/parser.py`, replace the `--may` help string with:

```python
                        help="what every child may publish once its suite passes, "
                             "without asking: push, or pr (push + open the PR); "
                             "defaults to pr, `none` withholds; "
                             "a contract piece's own `may:` wins over it")
```

- [ ] **Step 5: Run the tests**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_dispatch_closing.py tests/unit/test_dispatch_may.py -v`

Expected: PASS. A test asserting that an absent `--may` produces `NO_GRANT` is now asserting the old default — update it to expect the `pr` clause.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/cli/commands/dispatch.py src/mnemo/cli/parser.py tests/unit/test_dispatch_closing.py
git commit -m "feat(dispatch): --may defaults to pr

Every grant ever recorded was push or push,pr; the empty default left
children finished, unpublished and running. \`--may none\` still withholds.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: `deliver --stop-done` — the wider net

A `done` child with nothing to deliver is stopped anyway, because the briefing is made from the session and not from the delivery.

**Files:**
- Modify: `src/mnemo/core/sessions/delivery.py` (add `stop_done_in`)
- Modify: `src/mnemo/cli/commands/deliver.py` (`--stop-done`, and `--review` reporting)
- Modify: `src/mnemo/cli/parser.py` (register the flag)
- Test: `tests/unit/test_deliver_stop_done.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_deliver_stop_done.py`:

```python
"""A finished child is stopped even when it delivered nothing."""
from pathlib import Path

import pytest

from mnemo.core.sessions import delivery


class _Session:
    def __init__(self, short_id, state, live=True):
        self.short_id = short_id
        self.state = state
        self.live = live


@pytest.fixture
def tree(tmp_path):
    return tmp_path / "mnemo-wt-211"


def test_stops_a_done_session(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("aaaaaaaa", "done")])
    stopped = []
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: stopped.append(sid) or None)

    result = delivery.stop_done_in(tree)

    assert stopped == ["aaaaaaaa"]
    assert result == [("aaaaaaaa", None)]


def test_leaves_a_blocked_session_alone(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("bbbbbbbb", "blocked")])
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: pytest.fail("blocked must not be stopped"))

    assert delivery.stop_done_in(tree) == []


def test_skips_an_already_stopped_session(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("cccccccc", "stopped")])
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: pytest.fail("already stopped"))

    assert delivery.stop_done_in(tree) == []


def test_skips_a_done_session_with_no_process_left(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("dddddddd", "done", live=False)])
    monkeypatch.setattr(delivery, "stop_session",
                        lambda sid: pytest.fail("nothing to stop"))

    assert delivery.stop_done_in(tree) == []


def test_reports_a_failed_stop_rather_than_raising(monkeypatch, tree):
    monkeypatch.setattr(delivery, "sessions_in",
                        lambda _t: [_Session("eeeeeeee", "done")])
    monkeypatch.setattr(delivery, "stop_session", lambda sid: "no such job")

    assert delivery.stop_done_in(tree) == [("eeeeeeee", "no such job")]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_deliver_stop_done.py -v`

Expected: FAIL — `AttributeError: module 'mnemo.core.sessions.delivery' has no attribute 'stop_done_in'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/sessions/delivery.py`, after `stop_session`, add:

```python
def stop_done_in(worktree: Path | str) -> list[tuple[str, str | None]]:
    """Stop every ``done`` session in *worktree*. Returns ``(short_id, why)``.

    ``why`` is ``None`` when the stop succeeded. Separate from delivering on
    purpose: a briefing is made from the session, not from the PR, so a child
    that refused its task — and therefore has nothing to publish — still has
    to be stopped for its reasoning to survive (2026-09-16 design).

    The same guards as ``deliver``'s own stop: only ``done``. A ``blocked``
    child is waiting for an answer and is not finished; a ``stopped`` one
    already fired ``SessionEnd``; a ``done`` one the roster proves is gone
    (``live is False``) has no process left to end.
    """
    out: list[tuple[str, str | None]] = []
    for session in sessions_in(worktree):
        if session.state != "done":
            continue
        if session.live is False:
            continue
        out.append((session.short_id, stop_session(session.short_id)))
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_deliver_stop_done.py -v`

Expected: PASS (5 tests)

- [ ] **Step 5: Wire the flag into the command**

In `src/mnemo/cli/parser.py`, find the `deliver` subparser and add:

```python
    deliver_p.add_argument("--stop-done", dest="stop_done", action="store_true",
                           help="stop every finished child in this repo's dispatch "
                                "worktrees, delivered or not — a stopped child is "
                                "the one that writes its briefing")
```

In `src/mnemo/cli/commands/deliver.py`, inside `cmd_deliver`, before the existing id-handling, add:

```python
    if getattr(args, "stop_done", False):
        from mnemo.core.sessions import delivery

        stopped = 0
        for tree in delivery.dispatch_worktrees(repo_root=root):
            for short_id, why in delivery.stop_done_in(tree):
                if why is None:
                    print(f"{tree.name}: stopped {short_id}")
                    stopped += 1
                else:
                    print(f"{tree.name}: `claude stop {short_id}` failed: {why}")
        if not stopped:
            print("nothing finished and still running")
        return 0
```

- [ ] **Step 6: Show the holding sessions in `--review`**

`_review` prints two groups, `PRONTAS` and `NÃO PRONTAS`, and a finished child
can be in either — a refusing child is "not ready" (no commits) and is exactly
the one whose briefing matters most. So the report is added once, over all
states, rather than inside either loop.

In `src/mnemo/cli/commands/deliver.py`, inside `_review`, insert this
immediately **before** the final `if ready:` block that prints the
`entregar:` hint:

```python
    # A finished child still running holds a few hundred MB and has written no
    # briefing, whether or not it had anything to deliver. Said once, across
    # both groups: the child with nothing to deliver is the one most worth
    # stopping, because no diff carries what it decided.
    holding = [
        (r.label, s.short_id)
        for r in states
        for s in delivery.sessions_in(r.worktree)
        if s.state == "done" and s.live is not False
    ]
    if holding:
        print(f"TERMINADAS, NÃO PARADAS ({len(holding)})")
        for label, short_id in holding:
            print(f"  {label}  {short_id}")
        print("      sem briefing até parar: mnemo deliver --stop-done")
        print()
```

- [ ] **Step 7: Run the deliver tests**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/ -k deliver -v`

Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/mnemo/core/sessions/delivery.py src/mnemo/cli/commands/deliver.py src/mnemo/cli/parser.py tests/unit/test_deliver_stop_done.py
git commit -m "feat(deliver): --stop-done stops a finished child that delivered nothing

A briefing is made from the session, not from the PR, so stopping must
not depend on delivering. --review now names the children holding memory.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: `land` refuses a piece whose PR has a failing check

**Files:**
- Modify: `src/mnemo/core/landing.py` (add `failing_checks`, fill `PieceState.reason`)
- Test: `tests/unit/test_landing_checks.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_landing_checks.py`:

```python
"""The merge gate reads individual checks, never the rollup (2026-09-16)."""
import json

from mnemo.core import landing


def _fake_gh(payload, *, returncode=0):
    class _R:
        pass

    def run(args, **kwargs):
        r = _R()
        r.returncode = returncode
        r.stdout = json.dumps(payload)
        r.stderr = ""
        return r

    return run


def test_no_failing_checks_when_all_pass(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "ubuntu / py3.12", "bucket": "pass", "state": "SUCCESS"},
        {"name": "lint", "bucket": "pass", "state": "SUCCESS"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_a_failing_check_is_named(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "ubuntu / py3.12", "bucket": "pass", "state": "SUCCESS"},
        {"name": "windows / py3.11", "bucket": "fail", "state": "FAILURE"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == [
        "windows / py3.11"
    ]


def test_a_non_blocking_failure_is_still_a_failure(monkeypatch):
    """The whole point: a job the repo marked non-blocking fails while the
    run's conclusion and the PR's rollup both report success."""
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "required", "bucket": "pass", "state": "SUCCESS"},
        {"name": "optional-job", "bucket": "fail", "state": "FAILURE"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == [
        "optional-job"
    ]


def test_pending_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "slow", "bucket": "pending", "state": "IN_PROGRESS"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_skipped_and_cancelled_are_not_failures(monkeypatch):
    monkeypatch.setattr(landing, "_run_gh", _fake_gh([
        {"name": "a", "bucket": "skipping", "state": "SKIPPED"},
        {"name": "b", "bucket": "cancel", "state": "CANCELLED"},
    ]))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_unreadable_checks_are_not_invented(monkeypatch):
    """gh unavailable, or a PR with no checks at all: report nothing rather
    than blocking a landing on an answer we do not have."""
    monkeypatch.setattr(landing, "_run_gh", _fake_gh({}, returncode=1))

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []


def test_malformed_json_is_not_a_failure(monkeypatch):
    def run(args, **kwargs):
        class _R:
            returncode = 0
            stdout = "not json"
            stderr = ""
        return _R()

    monkeypatch.setattr(landing, "_run_gh", run)

    assert landing.failing_checks("https://github.com/o/r/pull/1") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_landing_checks.py -v`

Expected: FAIL — `AttributeError: module 'mnemo.core.landing' has no attribute 'failing_checks'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/landing.py`, add near the other `gh` helpers:

Note the module's existing convention: `subprocess` is imported inside the
function that uses it (see `_git` at `landing.py:206`), not at module scope.
Follow it.

```python
def _run_gh(args, **kwargs):
    """Indirection so the check reader can be faked in tests."""
    import subprocess

    return subprocess.run(args, capture_output=True, text=True, timeout=60, **kwargs)


def failing_checks(pr: str) -> list[str]:
    """The names of *pr*'s failing checks, read one by one.

    Reads ``gh pr checks --json name,bucket`` rather than the run's conclusion
    or the PR's rollup. A repository may mark a job non-blocking, and such a
    job fails while both aggregates report success — so a gate that trusted
    the aggregate would be reading a proxy of the thing it is gating on,
    immediately before the one irreversible step.

    Only ``bucket == "fail"`` counts. Pending is not failure (the landing is
    simply not ready yet, which the rehearsal will say), and skipped or
    cancelled checks are not results. An unreadable answer — ``gh`` missing, a
    PR with no checks, malformed output — returns ``[]``: this refuses a
    landing on evidence, never on the absence of it.
    """
    import json

    try:
        result = _run_gh(["gh", "pr", "checks", pr, "--json", "name,bucket"])
    except Exception:  # noqa: BLE001 — an unreadable gate must not raise
        return []
    if result.returncode not in (0, 8):  # 8 == checks pending
        return []
    try:
        rows = json.loads(result.stdout or "[]")
    except ValueError:
        return []
    if not isinstance(rows, list):
        return []
    return [
        str(row.get("name") or "?")
        for row in rows
        if isinstance(row, dict) and row.get("bucket") == "fail"
    ]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/test_landing_checks.py -v`

Expected: PASS (7 tests)

- [ ] **Step 5: Wire it into `inspect`**

In `src/mnemo/core/landing.py`, inside `inspect`, where each `PieceState` is built: after the existing reason checks, and only for a piece that has an open PR and no reason yet, add:

```python
        if state.pr and not state.reason and state.pr_state == "OPEN":
            red = failing_checks(state.pr)
            if red:
                state = replace(
                    state,
                    reason=f"CI vermelho em {', '.join(red[:3])}"
                           + (f" (+{len(red) - 3})" if len(red) > 3 else ""),
                )
```

`landing.py` does not currently import `replace`. Add it to the module's
existing `dataclasses` import line (which already brings in `field`), so the
line reads:

```python
from dataclasses import dataclass, field, replace
```

If `inspect` builds each `PieceState` in one constructor call rather than
binding it to a name first, set the reason inline instead of with `replace`:
compute `red = failing_checks(pr)` before constructing, and pass
`reason=...` as part of the same call. Either shape is correct; do not
introduce a mutable `PieceState` — it is `frozen`.

- [ ] **Step 6: Run the landing tests**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest tests/unit/ -k landing -v`

Expected: PASS. A test building a `PieceState` with an open PR may now need its `gh` call faked; patch `landing.failing_checks` to return `[]` in those tests.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/landing.py tests/unit/test_landing_checks.py
git commit -m "feat(land): refuse a piece whose PR has a failing check

The child stops in seconds; CI takes minutes, so nothing else catches a
red PR before the merge. Reads individual checks, not the rollup: a
non-blocking job fails while the aggregate reports success.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Documentation and changelog

**Files:**
- Modify: `docs/getting-started.md` (the dispatch section)
- Modify: `skills/decomposing-for-dispatch/SKILL.md` (the `may:` guidance)
- Create: `changelog.d/348.added.md`, `changelog.d/348.changed.md`

- [ ] **Step 1: Write the changelog fragments**

Create `changelog.d/348.added.md`:

```markdown
- `mnemo deliver --stop-done` stops a finished child that delivered nothing, so its briefing is written.
- `mnemo land` refuses a piece whose pull request has a failing check, reading individual checks rather than the rollup conclusion.
```

Create `changelog.d/348.changed.md`:

```markdown
- A dispatched child now ends itself: it reports, publishes when git says there is work, and stops. Only a stopped child fires `SessionEnd`, which is where its briefing is written.
- `mnemo dispatch --may` defaults to `pr`. Pass `--may none` to withhold.
```

- [ ] **Step 2: Update the getting-started dispatch section**

In `docs/getting-started.md`, in the dispatch section, after the `--may` description, add:

```markdown
A child ends itself: it writes its closing report, asks `git` whether there
is anything to publish, publishes it when there is, and then stops. The stop
matters beyond tidiness — only a stopped session fires `SessionEnd`, and that
is where the child's briefing is written. A child left running holds a few
hundred megabytes and leaves no memory behind.

`--may` defaults to `pr`. Use `--may none` for work that should not become a
branch.

If a child finishes without stopping itself, `mnemo deliver --stop-done`
stops every finished child in the repo's dispatch worktrees, whether or not
it delivered anything.
```

- [ ] **Step 3: Update the decomposition skill**

In `skills/decomposing-for-dispatch/SKILL.md`, where `may:` is described, replace the guidance with:

```markdown
`may:` is optional and defaults to `pr` — the piece's child publishes its own
pull request. Write `may: none` for a piece that should not become a branch
at all, such as an exploratory spike. `may: merge` is refused: landing belongs
to `mnemo land`.
```

- [ ] **Step 4: Run the full suite**

Run: `cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest -q`

Expected: PASS, no regressions against the 4042-passing baseline.

- [ ] **Step 5: Commit**

```bash
git add docs/getting-started.md skills/decomposing-for-dispatch/SKILL.md changelog.d/
git commit -m "docs(dispatch): the child ends itself

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verification

After every task:

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python -m pytest -q
```

`PYTHONPATH=src` is required — a bare `pytest` in a worktree imports
`~/github/mnemo/src`, so a green suite there proves nothing about the branch.

### Live check (after all tasks, manual)

1. Dispatch one real issue with no `--may` flag; confirm the child's prompt
   carries the closing clause and the `pr` grant (`mnemo sessions --json`).
2. Let the child finish. Confirm it opened a PR and stopped itself:
   `mnemo sessions` shows `stopped`, and a briefing exists under
   `<vault>/bots/<project>/briefings/sessions/`.
3. Confirm `mnemo deliver --stop-done` reports "nothing finished and still
   running" once the child stopped itself.
