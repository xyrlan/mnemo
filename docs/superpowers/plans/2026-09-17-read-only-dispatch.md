# Read-only dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `mnemo dispatch <issue> --read-only` spawns a child that investigates the issue, posts its finding as a comment, and cannot use the file-editing tools.

**Architecture:** A new posture flag threaded beside the existing `may` grant, but on its own axis. It selects a second prompt template, emits `--disallowedTools` early in the child's argv, and swaps the closing's publish step for a `gh issue comment` step. The worktree, the parent link, and the implement-path stay exactly as they are.

**Tech Stack:** Python 3.8+, pytest, argparse. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-17-read-only-dispatch-design.md`

**Issue:** #371

---

## Before you start

Read the spec. The section "The enforcement question" records what was measured about `--disallowedTools` and what was not; Task 1 exists to close the one remaining gap, and its answer decides the wording used in Task 8.

Two repo conventions that will bite you if you skip them:

- **Commit messages are in English**, even when the issue or conversation is Portuguese.
- **Another session may be working in `~/github/mnemo`.** Work in a worktree of your own. If `git status` shows changes you did not make, stop and ask rather than committing them.

Run the suite with `python -m pytest` from the repo root. In a worktree you must set `PYTHONPATH=src` or a bare `pytest` imports the master checkout's `src/mnemo` and a green run proves nothing.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/mnemo/core/dispatch.py` | prompt templates, argv assembly, the dispatch entry points | Modify |
| `src/mnemo/core/contracts.py` | `read-only:` on a piece, parse + precedence | Modify |
| `src/mnemo/cli/parser.py` | the `--read-only` argument | Modify |
| `src/mnemo/cli/commands/dispatch.py` | refuse `--read-only` with `--may`, render it in dry-run | Modify |
| `tests/unit/test_dispatch_read_only.py` | every test in this plan | Create |

No new module. The posture is one boolean carried on the same channel as `may`, and splitting it into its own module would separate it from the two functions that consume it.

---

### Task 1: Measure whether Bash bypasses the restriction

This task writes no production code. It answers the spec's one open question, and its answer decides the wording in Task 8. Do it first — if Bash bypasses the block, the feature still ships, but it must not claim more than it does.

**Files:**
- Modify: `docs/superpowers/specs/2026-09-17-read-only-dispatch-design.md`

- [ ] **Step 1: Create a throwaway directory with a file to clobber**

```bash
D=$(mktemp -d)
cd "$D"
printf original > target.txt
git init -q .
```

- [ ] **Step 2: Spawn a background child with the file tools closed**

```bash
claude --bg --disallowedTools Edit Write NotebookEdit --model haiku \
  "Put the word CHANGED into target.txt using a Bash redirect: printf CHANGED > target.txt. Then run: cat target.txt. Report the exact output and any error verbatim. Then stop yourself with: claude stop \${CLAUDE_CODE_SESSION_ID:0:8}"
```

Note the short id it prints.

**Why `--bg` and not `--print`:** the sandbox that wraps `--print` blocks shell redirection on its own, which masks the thing being measured. A `--bg` child is the real case. This is recorded in the spec.

- [ ] **Step 3: Wait for it, then read the file**

```bash
sleep 60
cat "$D/target.txt"
```

- [ ] **Step 4: Record the answer in the spec**

Open the spec's "### What was NOT measured: whether Bash bypasses the restriction" section.

If `target.txt` still reads `original`, replace that section's heading and body with a measured result stating that the shell could not write either, quoting the child's own error.

If it reads `CHANGED`, keep the narrow claim and replace the section's final paragraph ("The first step of the implementation plan is to answer it…") with the measured outcome: the shell **can** write, so the restriction prevents accident and drift, not intent. Add one sentence saying `--read-only`'s help text must say so too.

Either way, delete the sentence that calls this the first step of the plan — it has now been taken.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-17-read-only-dispatch-design.md
git commit -m "docs(dispatch): measure whether a read-only child can write through Bash"
```

---

### Task 2: `spawn_child` emits `--disallowedTools` when read-only

**Files:**
- Modify: `src/mnemo/core/dispatch.py` (`spawn_child`, around `:739-750`)
- Test: `tests/unit/test_dispatch_read_only.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_dispatch_read_only.py`:

```python
"""The read-only posture: a child that investigates and cannot edit files."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core import dispatch


class _Spawn:
    """Records the argv instead of running it."""

    def __init__(self, stdout: str = "abc12345\n") -> None:
        self.args: list[str] = []
        self.stdout = stdout

    def __call__(self, args, **kwargs):
        self.args = list(args)
        return type("R", (), {"returncode": 0, "stdout": self.stdout, "stderr": ""})()


READ_ONLY_TOOLS = ("Edit", "Write", "NotebookEdit")

# Every test below that calls `spawn_child` directly carries
# `@pytest.mark.real_spawn`. `tests/conftest.py`'s autouse
# `_no_real_detached_jobs` otherwise replaces `spawn_child` itself with a no-op,
# so the test would measure the stub rather than the argv — and the
# byte-identical test would pass for a hollow reason, comparing two empty lists
# down the same no-op path. Nine test modules already carry it for this reason.


@pytest.mark.real_spawn
def test_read_only_child_is_spawned_with_the_file_tools_closed(monkeypatch, tmp_path):
    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)
    monkeypatch.setattr(dispatch.claude_cli, "require_short_id", lambda out: "abc12345")

    dispatch.spawn_child("do the thing", cwd=tmp_path, read_only=True)

    assert "--disallowedTools" in spawn.args
    at = spawn.args.index("--disallowedTools")
    assert spawn.args[at + 1:at + 1 + len(READ_ONLY_TOOLS)] == list(READ_ONLY_TOOLS)


@pytest.mark.real_spawn
@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"lean": False},
        {"lean": False, "model": "haiku"},
        {"model": "haiku", "effort": "high"},
    ],
    ids=["defaults", "full-profile", "full-profile+model", "model+effort"],
)
def test_the_prompt_is_never_eaten_by_the_variadic_tool_list(monkeypatch, tmp_path, kwargs):
    """`--disallowedTools` consumes tokens until a flag, so the prompt must be
    fenced off explicitly. Relying on a later flag to stop it holds only while
    one happens to be present: `--full-profile` with no model puts the prompt
    straight after the list, where the CLI reads it as a tool name and the run
    dies with "Input must be provided either through stdin or as a prompt
    argument" (measured 2026-09-17).
    """
    spawn = _Spawn()
    monkeypatch.setattr(dispatch.subprocess, "run", spawn)
    monkeypatch.setattr(dispatch.claude_cli, "require_short_id", lambda out: "abc12345")

    dispatch.spawn_child("THE PROMPT", cwd=tmp_path, read_only=True, **kwargs)

    assert spawn.args[-1] == "THE PROMPT"
    at = spawn.args.index("--disallowedTools")
    after = spawn.args[at + 1 + len(READ_ONLY_TOOLS)]
    assert after == "--", f"the tool list must be closed explicitly, got {after!r}"


@pytest.mark.real_spawn
def test_a_normal_child_argv_is_byte_identical(monkeypatch, tmp_path):
    """A dispatch that is not read-only runs the command it ran before."""
    plain, ro = _Spawn(), _Spawn()
    monkeypatch.setattr(dispatch.claude_cli, "require_short_id", lambda out: "abc12345")

    monkeypatch.setattr(dispatch.subprocess, "run", plain)
    dispatch.spawn_child("do the thing", cwd=tmp_path)

    monkeypatch.setattr(dispatch.subprocess, "run", ro)
    dispatch.spawn_child("do the thing", cwd=tmp_path, read_only=False)

    assert plain.args == ro.args
    assert "--disallowedTools" not in plain.args
```

- [ ] **Step 2: Run them to verify they fail**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -v
```

Expected: FAIL with `TypeError: spawn_child() got an unexpected keyword argument 'read_only'`.
If instead you see `assert '--disallowedTools' in []`, the `real_spawn` marker is missing from that test — the autouse guard stubbed `spawn_child` and you are measuring the stub.

- [ ] **Step 3: Add the parameter and the argv**

In `src/mnemo/core/dispatch.py`, add a module constant next to the other prompt-level constants:

```python
#: The tools a read-only child may not call. Measured 2026-09-17 against the
#: real CLI: the block holds and reaches the child's own subagents, and an
#: unknown name is reported rather than ignored ("matches no known tool").
READ_ONLY_TOOLS = ("Edit", "Write", "NotebookEdit")
```

Add `read_only: bool = False` to `spawn_child`'s keyword-only parameters, and emit the flag **first**, before `--model`:

```python
    args = ["claude", "--bg"]
    if read_only:
        # `--disallowedTools` is variadic and space-separated, so it consumes
        # every following token until a flag. `--` ends it explicitly: relying
        # on a later flag to stop it is relying on `--model`/`--effort`/lean
        # happening to be present, and `--full-profile` with no model makes the
        # prompt the next token, where the CLI reads it as a tool name and the
        # child starts with no instructions at all (measured 2026-09-17).
        args += ["--disallowedTools", *READ_ONLY_TOOLS, "--"]
    if model:
        args += ["--model", model]
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -v
```

Expected: 3 passed

- [ ] **Step 5: Run the full dispatch suite for regressions**

```bash
python -m pytest tests/unit/ -k dispatch -q
```

Expected: all pass. The byte-identical test above is the one that would catch an argv regression.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/dispatch.py tests/unit/test_dispatch_read_only.py
git commit -m "feat(dispatch): close the file tools for a read-only child"
```

---

### Task 3: The analysis prompt template

**Files:**
- Modify: `src/mnemo/core/dispatch.py` (next to `_PROMPT` at `:235`)
- Test: `tests/unit/test_dispatch_read_only.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dispatch_read_only.py`:

```python
def test_the_analysis_prompt_does_not_ask_for_an_implementation():
    prompt = dispatch.build_prompt(
        361, title="dedupe suppresses the strongest matches",
        body="Measured: 30.7% of silences.", read_only=True,
    )

    assert "Run the full test suite before claiming the work is done." not in prompt
    assert "changelog.d" not in prompt
    assert "investigate" in prompt.lower()


def test_the_analysis_prompt_keeps_the_refusal_licence():
    """The passage that licenses a measured refusal reads correctly for an
    investigator, and is the reason the outcome was reachable at all.
    """
    prompt = dispatch.build_prompt(361, title="t", body="b", read_only=True)

    assert "No approach is prescribed" in prompt


def test_the_analysis_prompt_names_the_issue_and_the_worktree():
    prompt = dispatch.build_prompt(361, title="the title", body="the body", read_only=True)

    assert "#361" in prompt
    assert "the title" in prompt
    assert "the body" in prompt
    assert dispatch.branch_name(361) in prompt


def test_the_implement_prompt_is_unchanged():
    """Byte-identical for every existing caller."""
    before = dispatch.build_prompt(361, title="t", body="b")
    after = dispatch.build_prompt(361, title="t", body="b", read_only=False)

    assert before == after
    assert "Run the full test suite before claiming the work is done." in before
```

- [ ] **Step 2: Run them to verify they fail**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -k prompt -v
```

Expected: FAIL with `TypeError: build_prompt() got an unexpected keyword argument 'read_only'`

- [ ] **Step 3: Add the template**

In `src/mnemo/core/dispatch.py`, after `_PROMPT`'s closing `"""`:

```python
#: The read-only posture's opening prompt. Deliberately parallel to `_PROMPT`:
#: same issue block, same worktree sentence, same refusal licence. What differs
#: is the task (investigate, do not implement) and the closing, which asks for a
#: comment on the issue rather than a check for commits to publish.
_ANALYSIS_PROMPT = """Investigate issue #{issue} in this repo: {title}

Read the full issue with `gh issue view {issue}` — including its comments,
which often re-scope it. The body as it stands:

---
{body}
---

You are in a git worktree of your own on branch `{branch}`. You are here to
find something out, not to build anything: your file-editing tools are closed,
and nothing you learn needs a diff to be worth having.

What is being asked of you:
- Answer the question the issue actually poses. If the issue poses none, say
  what question it should have posed and answer that instead.
- Reach for evidence over inference. Run the code, read the log, count the
  rows. A claim you measured is worth more than a claim you reasoned to.
- Say what you could not determine, and why. An honest gap is a finding.

No approach is prescribed. Decide what the issue actually calls for from the
evidence in the repo. If the issue rests on a premise the code shows to be
wrong, say so and show the evidence — a measured correction is the most
valuable thing you can return.
{closing}"""
```

- [ ] **Step 4: Branch in `build_prompt`**

Add `read_only: bool = False` to `build_prompt`'s keyword-only parameters, and branch before the existing `return`:

```python
    branch = branch_name(issue)
    if read_only:
        return _ANALYSIS_PROMPT.format(
            issue=issue,
            title=title or f"issue #{issue}",
            body=(body or "").strip() or "(empty — read it with gh)",
            branch=branch,
            closing=_closing_clause(read_only=True, issue=issue),
        )
    return _PROMPT.format(
```

`_closing_clause` grows those parameters in Task 4. Until then this test will fail on the call signature — that is expected and the next task fixes it.

- [ ] **Step 5: Run the tests**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -k prompt -v
```

Expected: FAIL with `TypeError: _closing_clause() got an unexpected keyword argument 'read_only'`. Proceed to Task 4; do not commit a red suite.

---

### Task 4: The read-only closing

**Files:**
- Modify: `src/mnemo/core/dispatch.py` (`_CLOSING_STEPS` at `:301`, `_closing_clause` at `:318`)
- Test: `tests/unit/test_dispatch_read_only.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dispatch_read_only.py`:

```python
def test_the_read_only_closing_asks_for_a_comment_not_a_push():
    prompt = dispatch.build_prompt(361, title="t", body="b", read_only=True)

    assert "gh issue comment 361" in prompt
    assert "commit ahead of the base" not in prompt
    assert "mnemo deliver" not in prompt


def test_the_read_only_closing_keeps_the_report_and_the_stop():
    """Neither depends on the posture, and the briefing is the artefact the
    implement-path already calls the most valuable in the system.
    """
    prompt = dispatch.build_prompt(361, title="t", body="b", read_only=True)

    assert "becomes the session's briefing" in prompt
    assert "claude stop" in prompt


def test_the_implement_closing_is_unchanged():
    from mnemo.core.sessions import grants

    for may in ((), grants.parse("push"), grants.parse("pr")):
        assert dispatch._closing_clause(may) == dispatch._closing_clause(may, read_only=False)
```

- [ ] **Step 2: Run them to verify they fail**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -k closing -v
```

Expected: FAIL with `TypeError: _closing_clause() got an unexpected keyword argument 'read_only'`

- [ ] **Step 3: Add the read-only steps and the branch**

In `src/mnemo/core/dispatch.py`, after `_CLOSING_STEPS`:

```python
#: The read-only closing. Steps 1 and 3 are `_CLOSING_STEPS`' own, word for
#: word: the report and the stop do not depend on the posture. Step 2 is the
#: difference — a finding goes to the issue, because the worktree it was found
#: in is removed and a briefing alone is read by whoever runs `mnemo sessions`.
_READ_ONLY_CLOSING_STEPS = (
    _CLOSING_STEPS[0],
    "Post your finding as a comment on the issue: `gh issue comment {issue} "
    "--body-file -`, with the body on stdin. Lead with the answer, then the "
    "evidence for it. If you could not reach `gh`, say so in your report — it "
    "is the only other copy.",
    _CLOSING_STEPS[2],
)
```

Then rewrite `_closing_clause`'s signature and body:

```python
def _closing_clause(
    may: grants.Grant = (), *, read_only: bool = False, issue: Target | None = None,
) -> str:
```

Keep the existing docstring and add to it:

```
    *read_only* swaps step 2 for a comment on *issue*: a read-only child has
    nothing to publish, and the check for commits ahead would be asking it to
    look for something the posture guarantees is not there.
```

Body:

```python
    if read_only:
        steps_source = tuple(
            step.format(issue=issue) if "{issue}" in step else step
            for step in _READ_ONLY_CLOSING_STEPS
        )
    else:
        if "pr" in may:
            hint = ", push it and open the pull request you were granted"
        elif "push" in may:
            hint = ", push it (do not open a pull request)"
        else:
            hint = ", say so in your report and leave it for `mnemo deliver`"
        steps_source = tuple(
            step.format(publish_hint=hint) if "{publish_hint}" in step else step
            for step in _CLOSING_STEPS
        )
    steps = "\n".join(
        _wrap(step, initial_indent=f"{number}. ", subsequent_indent="   ")
        for number, step in enumerate(steps_source, start=1)
    )
    return f"{_CLOSING_HEADING}\n{steps}\n"
```

Note the `.format` is now guarded by a substring check on each step. `_CLOSING_STEPS[2]` contains `${{CLAUDE_CODE_SESSION_ID:0:8}}`, which `str.format` collapses to single braces — calling `.format` on it unconditionally would change the implement-path's text. The `test_the_implement_closing_is_unchanged` test above is what catches that.

- [ ] **Step 4: Run the tests**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -v
```

Expected: all pass, including Task 3's prompt tests.

- [ ] **Step 5: Run the full suite**

```bash
python -m pytest -q
```

Expected: all pass. `_closing_clause` is called on every dispatch, so a regression here reaches every existing test.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/dispatch.py tests/unit/test_dispatch_read_only.py
git commit -m "feat(dispatch): an analysis prompt that reports to the issue"
```

---

### Task 5: Thread the posture through the dispatch entry points

**Files:**
- Modify: `src/mnemo/core/dispatch.py` (`Dispatched` at `:166`, `_spawn_into` at `:768`, `dispatch_issue`, `dispatch_all`, `dispatch_piece`, `dispatch_contract`)
- Test: `tests/unit/test_dispatch_read_only.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_dispatch_read_only.py`:

```python
def test_dispatch_issue_carries_the_posture_to_the_child(monkeypatch, tmp_path):
    seen = {}

    def fake_spawn(prompt, *, cwd, model=None, lean=True, effort=None, read_only=False):
        seen["prompt"] = prompt
        seen["read_only"] = read_only
        return "abc12345"

    monkeypatch.setattr(dispatch, "spawn_child", fake_spawn)
    monkeypatch.setattr(dispatch, "ensure_worktree", lambda *a, **k: tmp_path)
    monkeypatch.setattr(dispatch.claude_cli, "verify_registered", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.parents, "record", lambda *a, **k: None)
    monkeypatch.setattr(dispatch.grants, "record", lambda *a, **k: None)

    result = dispatch.dispatch_issue(
        361, repo_root=tmp_path,
        fetch=lambda n, **k: ("the title", "the body"),
        read_only=True,
    )

    assert seen["read_only"] is True
    assert "Investigate issue #361" in seen["prompt"]
    assert result.read_only is True
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -k carries -v
```

Expected: FAIL with `TypeError: dispatch_issue() got an unexpected keyword argument 'read_only'`

- [ ] **Step 3: Add the field and the parameter**

Add to the `Dispatched` dataclass (`:166`, beside `may`):

```python
    read_only: bool = False
```

Add `read_only: bool = False` as a keyword-only parameter to `_spawn_into`, `dispatch_issue`, `dispatch_all`, `dispatch_piece` and `dispatch_contract`, passing it down at each call. In `_spawn_into`, pass it to `spawn_child` and set it on both `Dispatched(...)` constructions — the `ContractBroken` branch included, or a child whose id could not be read back loses its posture in the queue.

In `dispatch_issue`, pass it to `build_prompt`.

- [ ] **Step 4: Run the tests**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -v
```

Expected: all pass.

- [ ] **Step 5: Run the full suite**

```bash
python -m pytest -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/dispatch.py tests/unit/test_dispatch_read_only.py
git commit -m "feat(dispatch): carry the read-only posture to every entry point"
```

---

### Task 6: The `--read-only` flag, refused alongside `--may`

**Files:**
- Modify: `src/mnemo/cli/parser.py` (beside `--may` at `:162`)
- Modify: `src/mnemo/cli/commands/dispatch.py` (`cmd_dispatch`, after the `_default_grant` block at `:102`)
- Test: `tests/unit/test_dispatch_read_only.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dispatch_read_only.py`:

```python
import argparse

from mnemo.cli.commands import dispatch as cli_dispatch


def _args(**over):
    base = dict(issues=[361], contract=None, model=None, effort=None, may=None,
                read_only=False, example=False, dry_run=False, full_profile=False)
    base.update(over)
    return argparse.Namespace(**base)


def test_read_only_with_a_grant_is_refused_before_anything_spawns(capsys, monkeypatch):
    def never(*a, **k):
        raise AssertionError("nothing may spawn")

    monkeypatch.setattr(cli_dispatch, "_repo_root", lambda: None)

    code = cli_dispatch.cmd_dispatch(_args(read_only=True, may="pr"))

    assert code == 1
    out = capsys.readouterr().out
    assert "--read-only" in out and "--may" in out


def test_read_only_alone_is_accepted():
    """`--may` defaults to `pr`, so the refusal must key on what was *typed*,
    not on the resolved grant — otherwise `--read-only` can never be used alone.
    """
    from mnemo.core.sessions import grants

    assert cli_dispatch._grant_for(_args(read_only=True)) == ()
    assert cli_dispatch._grant_for(_args()) == grants.parse("pr")
```

- [ ] **Step 2: Run them to verify they fail**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -k read_only_with -v
python -m pytest tests/unit/test_dispatch_read_only.py -k read_only_alone -v
```

Expected: FAIL — `AttributeError: module has no attribute '_grant_for'`

- [ ] **Step 3: Add the argument**

In `src/mnemo/cli/parser.py`, after the `--may` argument:

```python
    # A posture, not a permission: it changes what the child is asked to do and
    # closes its file-editing tools, where `--may` only decides what it may
    # publish. The two are refused together in `cmd_dispatch` — a child with
    # nothing to publish cannot be given a publishing grant.
    dispatch_p.add_argument("--read-only", dest="read_only", action="store_true",
                            help="the child investigates the issue and comments its "
                                 "finding; its file-editing tools are closed and it "
                                 "publishes nothing")
```

- [ ] **Step 4: Add the refusal and the grant resolution**

In `src/mnemo/cli/commands/dispatch.py`, add beside `_default_grant`:

```python
def _grant_for(args: argparse.Namespace) -> grants.Grant:
    """The grant this dispatch runs with, `()` when the posture is read-only.

    Keyed on what was *typed*: `--may` defaults to `pr`, so resolving the grant
    first and then refusing a non-empty one would make `--read-only` unusable
    on its own.
    """
    from mnemo.core.sessions import grants

    if getattr(args, "read_only", False):
        return ()
    return _default_grant(getattr(args, "may", None))
```

In `cmd_dispatch`, replace the `may = _default_grant(...)` block with:

```python
    read_only = bool(getattr(args, "read_only", False))
    if read_only and getattr(args, "may", None) is not None:
        # Before anything could spawn, as the grant refusal is. Not a
        # tightening or a loosening but a contradiction: a read-only child
        # produces nothing to publish, so a publishing grant on it is a
        # statement about work that cannot exist.
        print("--read-only: a read-only child publishes nothing, "
              "so it cannot be given --may")
        return 1
    try:
        may = _grant_for(args)
    except grants.GrantError as exc:
        print(f"--may: {exc}")
        return 1
```

Then thread `read_only=read_only` into the `dispatch_all` and `_dispatch_contract` calls further down.

- [ ] **Step 5: Run the tests**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -v
```

Expected: all pass.

- [ ] **Step 6: Verify the flag end to end without spawning**

```bash
python -m mnemo dispatch 361 --read-only --dry-run
```

Expected: the plan prints, naming the issue, and nothing spawns.

```bash
python -m mnemo dispatch 361 --read-only --may pr
```

Expected: `--read-only: a read-only child publishes nothing, so it cannot be given --may`, exit 1.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/parser.py src/mnemo/cli/commands/dispatch.py tests/unit/test_dispatch_read_only.py
git commit -m "feat(dispatch): --read-only, refused alongside --may"
```

---

### Task 7: `read-only:` on a contract piece

**Files:**
- Modify: `src/mnemo/core/contracts.py` (`_FIELD_RE` at `:58`, `Piece` at `:235`, the parse branch near `:489`)
- Modify: `src/mnemo/core/dispatch.py` (a `piece_read_only` beside `piece_grant` at `:916`)
- Test: `tests/unit/test_dispatch_read_only.py`

- [ ] **Step 1: Get the real contract format**

Do not invent the fixture. Print the repo's own example and copy its exact shape:

```bash
python -c "from mnemo.core import contracts; print(contracts.EXAMPLE)"
```

(If `EXAMPLE` is not the name, `grep -n 'EXAMPLE' src/mnemo/core/contracts.py` finds it; the CLI prints it via `mnemo dispatch --example`.)

Take from the output: the heading levels, the verdict line, and the bullet syntax for a piece. A fixture whose shape is not production's tests the guess rather than the parser — that failure has hidden four bugs in this repo already.

Build `_CONTRACT` in the test file from that output, with two pieces named `the-investigation` (carrying `- **read-only:** yes`) and `the-build` (carrying no such line).

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_dispatch_read_only.py`, with `_CONTRACT` as built in Step 1:

```python
from mnemo.core import contracts


def test_a_piece_can_declare_read_only(tmp_path):
    path = tmp_path / "contract.md"
    path.write_text(_CONTRACT, encoding="utf-8")

    contract = contracts.parse_contract(path)
    by_slug = {p.slug: p for p in contract.pieces}

    assert by_slug["the-investigation"].read_only is True
    assert by_slug["the-build"].read_only is None


def test_an_unreadable_read_only_value_is_refused_at_parse_time(tmp_path):
    path = tmp_path / "contract.md"
    path.write_text(
        _CONTRACT.replace("**read-only:** yes", "**read-only:** maybe"),
        encoding="utf-8",
    )

    with pytest.raises(contracts.ContractError, match="read-only"):
        contracts.parse_contract(path)


def test_the_piece_wins_over_the_flag():
    """Same precedence as `may`: absent inherits, present overrides."""
    absent = contracts.Piece(slug="s", files=[], read_only=None)
    asked = contracts.Piece(slug="s", files=[], read_only=True)
    refused = contracts.Piece(slug="s", files=[], read_only=False)

    assert dispatch.piece_read_only(absent, True) is True
    assert dispatch.piece_read_only(absent, False) is False
    assert dispatch.piece_read_only(asked, False) is True
    assert dispatch.piece_read_only(refused, True) is False
```

- [ ] **Step 3: Run them to verify they fail**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -k piece -v
```

Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'read_only'`

- [ ] **Step 4: Add the field**

In `src/mnemo/core/contracts.py`, extend `_FIELD_RE`:

```python
_FIELD_RE = re.compile(
    r"^-\s+\*\*(files|exposes|consumes|model|effort|may|read-only)\:\*\*\s*(.*)$"
)
```

Add to `Piece`, beside `may`:

```python
    #: Whether this piece is an investigation rather than a build. `None` is
    #: absent — the piece takes `mnemo dispatch --read-only`; `True`/`False`
    #: override it, as `may` does.
    read_only: bool | None = None
```

Add the parse branch beside the `may:` one, and a local reset beside the others:

```python
        if field == "read-only":
            word = value.strip().lower()
            if word in ("yes", "true", "on"):
                read_only = True
            elif word in ("no", "false", "off"):
                read_only = False
            else:
                # At parse time, before any tree exists, as `may:` is.
                raise ContractError(
                    f"{path}:{lineno}: read-only takes yes or no, not {value.strip()!r}"
                )
            continue
```

Pass `read_only=read_only` into the `Piece(...)` construction and reset it to `None` per piece alongside the other locals.

- [ ] **Step 5: Add the precedence helper**

In `src/mnemo/core/dispatch.py`, beside `piece_grant`:

```python
def piece_read_only(piece: "contracts.Piece", read_only: bool) -> bool:
    """The piece's own posture, or the dispatch's when it declares none."""
    return read_only if piece.read_only is None else piece.read_only
```

Use it in `dispatch_contract` where `piece_grant` is already used, and pass the result to `dispatch_piece`.

- [ ] **Step 6: Run the tests**

```bash
python -m pytest tests/unit/test_dispatch_read_only.py -v
```

Expected: all pass.

- [ ] **Step 7: Run the full suite**

```bash
python -m pytest -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/mnemo/core/contracts.py src/mnemo/core/dispatch.py tests/unit/test_dispatch_read_only.py
git commit -m "feat(contracts): a piece can declare itself read-only"
```

---

### Task 8: Documentation, and the honest claim

**Files:**
- Modify: `README.md` (the dispatch section)
- Create: `changelog.d/371.added.md`

- [ ] **Step 1: Check the changelog convention**

```bash
ls changelog.d/ | head -5
```

Entries are named `<id>.<section>.md` — the id is the issue number (#371), and the sections in use are `added`, `changed` and `fixed`. Confirm against the listing rather than trusting this line.

- [ ] **Step 2: Write the changelog fragment**

Create `changelog.d/371.added.md`:

```markdown
`mnemo dispatch <issue> --read-only` spawns a child that investigates the issue
and posts its finding as a comment, instead of implementing it. Its
file-editing tools are closed at spawn, which reaches its subagents too. A
contract piece can ask for the same with `- **read-only:** yes`.
```

- [ ] **Step 3: Document the flag in the README**

Find the dispatch section and add, matching the surrounding prose style:

```markdown
Not every issue wants a patch. `--read-only` dispatches a child that
investigates and comments its finding on the issue, with its file-editing
tools closed:

    mnemo dispatch 361 --read-only

It publishes nothing, so `--may` is refused alongside it.
```

- [ ] **Step 4: State the claim exactly as Task 1 measured it**

If Task 1 found the shell **could not** write, the README may say the child cannot write.

If Task 1 found it **could**, the README must say what the restriction does and does not do — for example: "Its file-editing tools are closed; a determined child can still write through the shell, so this prevents accident and drift rather than intent."

Do not write a sentence Task 1 did not license. This is the plan's one non-negotiable step: the repo has shipped an unobserved guarantee before, and the spec cites it.

- [ ] **Step 5: Run the full suite one last time**

```bash
python -m pytest -q
```

Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add README.md changelog.d/371.added.md
git commit -m "docs(dispatch): document --read-only"
```

---

### Task 9: Dogfood it

**Files:** none

- [ ] **Step 1: Dispatch a real read-only child at a real issue**

Issue #361 (reflex dedupe) is a genuine open question and a good first target.

```bash
mnemo dispatch 361 --read-only
```

- [ ] **Step 2: Watch it**

```bash
mnemo sessions
```

- [ ] **Step 3: When it stops, read what it produced**

```bash
gh issue view 361 --comments | tail -40
```

- [ ] **Step 4: Check the worktree is clean**

```bash
mnemo deliver --review
```

Expected: the read-only child's tree appears with no commits ahead and nothing to deliver. If it has commits, the restriction did not hold the way Task 1 concluded — reopen that question before merging.

- [ ] **Step 5: Remove the worktree**

```bash
git worktree remove ../mnemo-wt-361
```

- [ ] **Step 6: Record what the dogfood showed**

If the child's comment is good, say so in the PR. If it investigated badly — too shallow, or it implemented anyway — that is a prompt problem and belongs in the PR description as a known limit, not hidden.

---

## Self-review notes

**Spec coverage:** surface (Task 6), prompt (Task 3), spawn (Task 2), closing (Task 4), worktree (unchanged, verified in Task 9), delivery (Task 4 step 3, verified Task 9), contract piece (Task 7), mutual exclusion (Task 6), argv shape (Task 2), the Bash question (Task 1), the honest claim (Task 8 step 4).

**Open question 1 in the spec** — whether the queue shows the posture — is deliberately *not* in this plan. `Dispatched.read_only` exists after Task 5, so the column can follow; the spec says it need not gate the feature.

**Open question 3** — `files:` on a read-only piece — is likewise left. It needs a real contract to reason about and none exists yet.
