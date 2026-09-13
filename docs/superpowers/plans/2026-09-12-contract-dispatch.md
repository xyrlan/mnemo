# Contract dispatch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `mnemo dispatch` spawn one background child per *piece of a feature*, read from a reviewed contract file, instead of requiring one GitHub issue per piece.

**Architecture:** A new pure module `core/contracts.py` parses a markdown contract into `Contract`/`Piece` dataclasses and validates it. `core/dispatch.py` widens its addressing from `int` to `int | str` so a piece slug can name a worktree, and gains a contract-shaped prompt. The CLI grows `mnemo dispatch --contract <path>`. A markdown skill (no code) writes the contract file in the first place; the maintainer reviews it before dispatching.

**Tech Stack:** Python 3.11+, stdlib only (`re`, `dataclasses`, `pathlib`). Tests with `pytest`. Frontmatter parsed via the existing `mnemo.core.filters.parse_frontmatter` — never a fresh regex.

**Spec:** `docs/superpowers/specs/2026-09-12-contract-dispatch-design.md`

---

## File Structure

| File | Responsibility |
|---|---|
| `src/mnemo/core/contracts.py` | **Create.** Parse + validate a contract file. Pure: no git, no subprocess, no I/O beyond reading the given path. |
| `tests/unit/test_contracts.py` | **Create.** Parsing, validation, every rejection reason. |
| `src/mnemo/core/dispatch.py` | **Modify.** Widen `_WT_RE`, `worktree_path`, `branch_name`, `issue_for_cwd` to accept a piece. Add `build_piece_prompt`, `dispatch_contract`. |
| `tests/unit/test_dispatch_worktrees.py` | **Modify.** Slug addressing, the nesting guard, the false-positive guard. |
| `tests/unit/test_dispatch_plan.py` | **Modify.** The contract prompt carries a boundary and no approach. |
| `src/mnemo/cli/parser.py` | **Modify.** `--contract` flag, `issues` becomes optional. |
| `src/mnemo/cli/commands/dispatch.py` | **Modify.** Contract branch of `cmd_dispatch`, including `--dry-run`. |
| `tests/unit/test_cli_dispatch.py` | **Modify.** CLI wiring, mutual exclusion, refusals. |
| `skills/decomposing-for-dispatch/SKILL.md` | **Create.** Markdown only, ~60-80 lines. No code, no test. |

Task order is dependency order: the parser is written first because dispatch consumes it, and the CLI last because it consumes both.

---

### Task 1: Contract parsing — the happy path

**Files:**
- Create: `src/mnemo/core/contracts.py`
- Test: `tests/unit/test_contracts.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_contracts.py`:

```python
"""``mnemo dispatch --contract`` — reading the file that names the pieces.

The contract is the only durable artifact of a decomposition. It is parsed
strictly: a file that cannot be read completely is refused before any worktree
exists, because a half-understood contract dispatches children against
boundaries nobody agreed to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core import contracts


VALID = """\
---
feature: contract-dispatch
created: 2026-09-12
verdict: parallel
---

## parser
- **files:** src/mnemo/core/contracts.py, tests/unit/test_contracts.py
- **exposes:** `parse_contract(path) -> Contract`
- **consumes:** nothing

## dispatch-seam
- **files:** src/mnemo/core/dispatch.py
- **consumes:** `parse_contract` from `parser`
- **exposes:** `mnemo dispatch --contract <path>`
"""


def write(tmp_path: Path, text: str) -> Path:
    target = tmp_path / "contract.md"
    target.write_text(text, encoding="utf-8")
    return target


def test_parses_feature_and_verdict(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    assert contract.feature == "contract-dispatch"
    assert contract.verdict == "parallel"


def test_parses_every_piece_in_order(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    assert [p.slug for p in contract.pieces] == ["parser", "dispatch-seam"]


def test_parses_a_piece_fields(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    piece = contract.pieces[0]
    assert piece.files == [
        "src/mnemo/core/contracts.py",
        "tests/unit/test_contracts.py",
    ]
    assert piece.exposes == ["`parse_contract(path) -> Contract`"]
    assert piece.consumes == []


def test_consumes_records_the_owning_piece(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    consumed = contract.pieces[1].consumes
    assert consumed == [("`parse_contract`", "parser")]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_contracts.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'mnemo.core.contracts'`

- [ ] **Step 3: Write the minimal implementation**

Create `src/mnemo/core/contracts.py`:

```python
"""The contract a decomposition produces: the pieces, and the boundary between.

A contract file is markdown with frontmatter and one ``##`` section per piece.
It is the only durable artifact of a decomposition — dispatch reads it, and the
maintainer reviews it before anything is spawned.

Parsing is strict on purpose. The file is written by a model and reviewed by a
human, so a shape this module does not recognise is far more likely to be a
mistake than a dialect worth tolerating. Every refusal happens here, before
:mod:`mnemo.core.dispatch` creates any git state.

Frontmatter is read with :func:`mnemo.core.filters.parse_frontmatter` rather
than a fresh regex: mnemo already has one reader for the shape it writes, and a
second one drifts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mnemo.core.filters import parse_frontmatter

# A slug must survive being a directory name and a branch segment, so it is
# constrained to what ``-wt-c-<slug>`` can express. Enforced at parse time so a
# contract can never name a piece the addressing scheme cannot address.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_FIELD_RE = re.compile(r"^-\s+\*\*(files|exposes|consumes)\:\*\*\s*(.*)$")
_FROM_RE = re.compile(r"^(.*?)\s+from\s+(\S+)$")


class ContractError(ValueError):
    """A contract file could not be read, or read but not trusted.

    Raised before any git state exists, so catching it means nothing was
    created and nothing needs cleaning up.
    """


@dataclass(frozen=True)
class Piece:
    """One unit of parallel work: a boundary, plus what crosses it."""

    slug: str
    files: list[str] = field(default_factory=list)
    exposes: list[str] = field(default_factory=list)
    # (signature, owning piece slug) — the owner is validated, not decorative.
    consumes: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Contract:
    feature: str
    verdict: str
    pieces: list[Piece] = field(default_factory=list)
    path: Path | None = None


def _split_list(value: str) -> list[str]:
    """``a, b , c`` -> ``["a", "b", "c"]``; ``nothing`` and ``` -> ``[]``."""
    cleaned = value.strip()
    if not cleaned or cleaned.lower() in {"nothing", "none", "-"}:
        return []
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def parse_contract(path: Path | str) -> Contract:
    """Read a contract file into a :class:`Contract`, or refuse."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise ContractError(f"cannot read contract {target}: {exc}") from exc

    meta = parse_frontmatter(text)
    feature = str(meta.get("feature") or "").strip()
    verdict = str(meta.get("verdict") or "").strip()

    pieces: list[Piece] = []
    slug: str | None = None
    files: list[str] = []
    exposes: list[str] = []
    consumes: list[tuple[str, str]] = []

    def flush() -> None:
        if slug is not None:
            pieces.append(
                Piece(slug=slug, files=files, exposes=exposes, consumes=consumes)
            )

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            slug = heading.group(1).strip()
            files, exposes, consumes = [], [], []
            continue
        matched = _FIELD_RE.match(line)
        if not matched or slug is None:
            continue
        key, value = matched.group(1), matched.group(2)
        if key == "files":
            files = _split_list(value)
        elif key == "exposes":
            exposes = _split_list(value)
        else:
            for item in _split_list(value):
                owner = _FROM_RE.match(item)
                if owner:
                    consumes.append((owner.group(1).strip(), owner.group(2).strip()))
                else:
                    consumes.append((item, ""))
    flush()

    return Contract(feature=feature, verdict=verdict, pieces=pieces, path=target)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/unit/test_contracts.py -v`

Expected: PASS — 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/contracts.py tests/unit/test_contracts.py
git commit -m "feat(contracts): parse a decomposition contract file"
```

---

### Task 2: Contract validation — every reason to refuse

Parsing succeeding does not mean the contract is safe to dispatch. Validation is separate so that the rejection reason reaches the maintainer intact.

**Files:**
- Modify: `src/mnemo/core/contracts.py`
- Test: `tests/unit/test_contracts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_contracts.py`:

```python
def test_orphan_consumes_is_refused(tmp_path: Path) -> None:
    """A signature nobody exposes means the cut is wrong, not merely untidy."""
    text = VALID.replace("`parse_contract` from `parser`", "`missing` from ghost")
    with pytest.raises(contracts.ContractError, match="ghost"):
        contracts.parse_contract(write(tmp_path, text))


def test_duplicate_slug_is_refused(tmp_path: Path) -> None:
    """Two pieces with one slug would collide on the same worktree path."""
    text = VALID.replace("## dispatch-seam", "## parser")
    with pytest.raises(contracts.ContractError, match="parser"):
        contracts.parse_contract(write(tmp_path, text))


def test_unaddressable_slug_is_refused(tmp_path: Path) -> None:
    """A slug must survive being a directory name and a branch segment."""
    text = VALID.replace("## parser", "## Parser/One")
    with pytest.raises(contracts.ContractError, match="slug"):
        contracts.parse_contract(write(tmp_path, text))


def test_piece_without_files_is_refused(tmp_path: Path) -> None:
    """Without a file boundary there is nothing keeping children apart."""
    text = VALID.replace(
        "- **files:** src/mnemo/core/contracts.py, tests/unit/test_contracts.py\n", ""
    )
    with pytest.raises(contracts.ContractError, match="files"):
        contracts.parse_contract(write(tmp_path, text))


def test_missing_verdict_is_refused(tmp_path: Path) -> None:
    text = VALID.replace("verdict: parallel\n", "")
    with pytest.raises(contracts.ContractError, match="verdict"):
        contracts.parse_contract(write(tmp_path, text))


def test_no_pieces_is_refused(tmp_path: Path) -> None:
    text = VALID.split("## parser")[0]
    with pytest.raises(contracts.ContractError, match="no pieces"):
        contracts.parse_contract(write(tmp_path, text))


def test_sequential_verdict_parses_but_is_not_dispatchable(tmp_path: Path) -> None:
    """``sequential`` is a real answer, not a failure — it parses fine."""
    text = VALID.replace("verdict: parallel", "verdict: sequential")
    contract = contracts.parse_contract(write(tmp_path, text))
    assert contract.verdict == "sequential"
    assert not contract.is_dispatchable
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_contracts.py -v`

Expected: FAIL — the refusal tests fail with `DID NOT RAISE`, and `test_sequential_verdict...` fails with `AttributeError: 'Contract' object has no attribute 'is_dispatchable'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/contracts.py`, add the property to `Contract` (immediately after the `path` field):

```python
    @property
    def is_dispatchable(self) -> bool:
        """``sequential`` is a valid verdict that must not spawn anything."""
        return self.verdict == "parallel"
```

Add the validator above `parse_contract`:

```python
def _validate(contract: Contract) -> None:
    """Refuse a contract that would dispatch children against a bad boundary.

    Every check runs against the whole file before the caller creates any
    worktree, so a contract is either entirely dispatchable or entirely
    refused — never half-spawned.
    """
    if contract.verdict not in {"parallel", "sequential"}:
        raise ContractError(
            f"verdict must be 'parallel' or 'sequential', got {contract.verdict!r}"
        )
    if not contract.pieces:
        raise ContractError("no pieces: a contract names at least one")

    seen: set[str] = set()
    for piece in contract.pieces:
        if not SLUG_RE.match(piece.slug):
            raise ContractError(
                f"slug {piece.slug!r} is not addressable: "
                "use lowercase letters, digits and hyphens"
            )
        if piece.slug in seen:
            raise ContractError(f"duplicate piece slug {piece.slug!r}")
        seen.add(piece.slug)
        if not piece.files:
            raise ContractError(f"piece {piece.slug!r} declares no files boundary")

    for piece in contract.pieces:
        for signature, owner in piece.consumes:
            if owner not in seen:
                raise ContractError(
                    f"piece {piece.slug!r} consumes {signature} from unknown "
                    f"piece {owner!r}"
                )
```

Then call it at the end of `parse_contract`, replacing the bare `return`:

```python
    contract = Contract(
        feature=feature, verdict=verdict, pieces=pieces, path=target
    )
    _validate(contract)
    return contract
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_contracts.py -v`

Expected: PASS — 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/contracts.py tests/unit/test_contracts.py
git commit -m "feat(contracts): refuse a contract that cannot be dispatched safely"
```

---

### Task 3: Widen dispatch addressing to accept a piece

The riskiest change in the plan. `_WT_RE` has **two** call sites and its `\d+` is a deliberate guard — see the spec section *Addressing*.

**Files:**
- Modify: `src/mnemo/core/dispatch.py:52` (`_WT_RE`), `:83` (`worktree_path`), `:99` (`branch_name`), `:103` (`issue_for_cwd`)
- Test: `tests/unit/test_dispatch_worktrees.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dispatch_worktrees.py`:

```python
def test_piece_slug_names_a_worktree(tmp_path: Path) -> None:
    root = tmp_path / "mnemo"
    tree = dispatch.worktree_path("c-parser", repo_root=root)
    assert tree.name == "mnemo-wt-c-parser"
    assert tree.parent == root.parent


def test_issue_number_still_names_a_worktree(tmp_path: Path) -> None:
    root = tmp_path / "mnemo"
    assert dispatch.worktree_path(193, repo_root=root).name == "mnemo-wt-193"


def test_slug_worktree_does_not_nest(tmp_path: Path) -> None:
    """Dispatching from inside a slug-named child must not stack suffixes."""
    root = tmp_path / "mnemo-wt-c-parser"
    tree = dispatch.worktree_path("c-seam", repo_root=root)
    assert tree.name == "mnemo-wt-c-seam"


def test_issue_for_cwd_reads_a_slug_back(tmp_path: Path) -> None:
    assert dispatch.issue_for_cwd("/x/mnemo-wt-c-parser") == "c-parser"


def test_issue_for_cwd_still_reads_an_int_back(tmp_path: Path) -> None:
    assert dispatch.issue_for_cwd("/x/mnemo-wt-193") == 193


def test_hand_made_worktree_is_still_not_a_dispatch(tmp_path: Path) -> None:
    """The guard the ``\\d+`` anchor existed to provide, preserved.

    A directory someone named by hand must not be reported as a dispatch
    child — a false positive mislabels an unrelated session in the queue.
    """
    assert dispatch.issue_for_cwd("/x/mnemo-wt-feature") is None
    assert dispatch.issue_for_cwd("/x/mnemo-wt-My-Branch") is None


def test_piece_branch_is_namespaced_by_feature() -> None:
    name = dispatch.branch_name("c-parser", feature="contract-dispatch")
    assert name == "feat/contract-dispatch/parser"


def test_issue_branch_is_unchanged() -> None:
    assert dispatch.branch_name(193) == "fix/issue-193"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_dispatch_worktrees.py -v`

Expected: FAIL — `test_issue_for_cwd_reads_a_slug_back` returns `None`, and `test_piece_branch_is_namespaced_by_feature` raises `TypeError: branch_name() got an unexpected keyword argument 'feature'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/dispatch.py`, replace `_WT_RE` and its comment (lines 49-52):

```python
# `<anything>-wt-<digits>` for an issue, `<anything>-wt-c-<slug>` for a contract
# piece; optional trailing slash. A closed alternation, never a wildcard:
# anchored on both ends so `mnemo-wt-feature` — a hand-made worktree that is
# not a dispatch — is still not mistaken for one. Widening this to `(.+)`
# would silently delete that guard and mislabel unrelated sessions in the
# queue, which is why the contract form carries the `c-` marker.
_WT_RE = re.compile(r"-wt-(\d+|c-[a-z0-9-]+)/?$")

# What names a child: a GitHub issue number, or a contract piece slug.
Target = int | str
```

Replace `worktree_path`'s signature line (keeping its docstring, extending the
last paragraph):

```python
def worktree_path(target: Target, *, repo_root: Path | str) -> Path:
```

and its body's final line:

```python
    return root.parent / f"{base}{WORKTREE_SUFFIX}{target}"
```

Replace `branch_name` entirely:

```python
def branch_name(target: Target, *, feature: str | None = None) -> str:
    """The branch a child works on.

    An issue keeps ``fix/issue-<n>``, unchanged. A contract piece is namespaced
    under its feature — ``feat/<feature>/<slug>`` — so that the branches of one
    decomposition sort together and a piece slug as ordinary as ``parser`` does
    not collide across features.
    """
    if feature:
        slug = str(target)
        slug = slug[2:] if slug.startswith("c-") else slug
        return f"feat/{feature}/{slug}"
    return f"fix/issue-{target}"
```

Replace `issue_for_cwd`'s return line (line 113):

```python
    if not match:
        return None
    captured = match.group(1)
    return int(captured) if captured.isdigit() else captured
```

and widen its signature and docstring first line:

```python
def issue_for_cwd(cwd: str | Path | None) -> Target | None:
    """What a dispatched worktree encodes — an issue number or a piece slug.
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_dispatch_worktrees.py -v`

Expected: PASS — all tests in the file, old and new

- [ ] **Step 5: Run the full suite — this task touches a shared helper**

Run: `python -m pytest tests/unit -q`

Expected: PASS. If `tests/unit/test_sessions*.py` fails on `Session.label`, that is the widening reaching `src/mnemo/core/sessions/jobs.py:78`; fix it in Task 4 rather than here, and note the failure.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/dispatch.py tests/unit/test_dispatch_worktrees.py
git commit -m "feat(dispatch): address a worktree by piece slug as well as issue"
```

---

### Task 4: Keep the session queue readable with slug-named children

`Session.label` interpolates `issue_for_cwd`'s result into the queue display and was written when that could only be an `int`.

**Files:**
- Modify: `src/mnemo/core/sessions/jobs.py:78-103`
- Test: `tests/unit/test_sessions_jobs.py` (if absent, create it following the fixture style of `tests/unit/test_dispatch_worktrees.py`)

- [ ] **Step 1: Write the failing test**

```python
def test_label_shows_an_issue_number() -> None:
    session = Session(short_id="ab12", cwd="/x/mnemo-wt-193", name="fix the thing")
    assert session.label.startswith("#193 ")


def test_label_shows_a_piece_slug_without_a_hash() -> None:
    """``#c-parser`` reads as an issue that does not exist; the slug alone does not."""
    session = Session(short_id="ab12", cwd="/x/mnemo-wt-c-parser", name="parse it")
    assert session.label.startswith("c-parser ")
    assert "#" not in session.label.split()[0]
```

Import `Session` at the top of the file: `from mnemo.core.sessions.jobs import Session`.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/test_sessions_jobs.py -v`

Expected: FAIL — the second test fails because the label reads `#c-parser parse it`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/sessions/jobs.py`, inside `label`, replace the line that
prefixes the issue with:

```python
        target = issue_for_cwd(self.cwd)
        # `#` means "GitHub issue". A contract piece is not one, so it is shown
        # bare rather than as `#c-parser`, which would read as a missing issue.
        prefix = f"#{target} " if isinstance(target, int) else f"{target} " if target else ""
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_sessions_jobs.py -v`

Expected: PASS — 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/sessions/jobs.py tests/unit/test_sessions_jobs.py
git commit -m "fix(sessions): label a contract piece by slug, not as a fake issue"
```

---

### Task 5: The contract prompt — a boundary, never an approach

**Files:**
- Modify: `src/mnemo/core/dispatch.py` (after `build_prompt`, around `:140-163`)
- Test: `tests/unit/test_dispatch_plan.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dispatch_plan.py`:

```python
from mnemo.core import contracts


PIECE = contracts.Piece(
    slug="parser",
    files=["src/mnemo/core/contracts.py"],
    exposes=["`parse_contract(path) -> Contract`"],
    consumes=[("`spawn_child`", "seam")],
)


def test_prompt_names_the_file_boundary() -> None:
    text = dispatch.build_piece_prompt(PIECE, feature="contract-dispatch")
    assert "src/mnemo/core/contracts.py" in text


def test_prompt_states_what_the_piece_must_deliver() -> None:
    text = dispatch.build_piece_prompt(PIECE, feature="contract-dispatch")
    assert "`parse_contract(path) -> Contract`" in text


def test_prompt_says_a_consumed_signature_may_be_assumed() -> None:
    """The forward reference resolves by signature — the child does not wait."""
    text = dispatch.build_piece_prompt(PIECE, feature="contract-dispatch")
    assert "`spawn_child`" in text
    assert "seam" in text


def test_prompt_carries_the_branch() -> None:
    text = dispatch.build_piece_prompt(PIECE, feature="contract-dispatch")
    assert "feat/contract-dispatch/parser" in text


def test_build_piece_prompt_takes_no_approach() -> None:
    """The #187 refusal, preserved: a prompt cannot prescribe a solution.

    A boundary ("do not touch X") is scope. An approach ("use a regex") is a
    solution, and passing one can override a correct refusal.
    """
    import inspect

    params = inspect.signature(dispatch.build_piece_prompt).parameters
    assert "approach" not in params
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_dispatch_plan.py -v`

Expected: FAIL — `AttributeError: module 'mnemo.core.dispatch' has no attribute 'build_piece_prompt'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/dispatch.py`, after `build_prompt`, add:

```python
_PIECE_PROMPT = """You are building one piece of the feature "{feature}": {slug}

The decomposition was reviewed and agreed before you started. Your piece:

**Files you may change** — this is a hard boundary. Work outside it belongs to
another child working in parallel right now, and editing it causes a conflict
that costs more than the parallelism saved:
{files}

**What your piece must deliver** — other pieces are being written against these
signatures at this moment, so they are not negotiable without saying so:
{exposes}

{consumes}You are on branch `{branch}` in your own worktree.

Nothing about *how* to build this is specified, deliberately. If the contract's
boundary turns out to be wrong — the work does not divide where it says, or a
signature cannot be delivered as written — stop and say so rather than widening
your boundary to make it fit.

Run the full test suite before you finish. Do not merge or push without asking.
"""

_CONSUMES_PROMPT = """**What you may assume exists** — another piece is
delivering these. They may not exist in your worktree yet: write against the
signature, stub locally if you must, and the merge resolves it. Do not wait,
and do not implement them yourself:
{items}

"""


def build_piece_prompt(piece: "contracts.Piece", *, feature: str) -> str:
    """A contract piece's opening prompt: its boundary and its interfaces.

    Like :func:`build_prompt`, this takes no "approach" parameter, and for the
    same reason (see that function's rationale). The distinction the contract
    relies on is thin but real: *"do not touch X, consume ``Y.parse()``"* is a
    **boundary** and belongs here, while *"use a regex to parse it"* is an
    **approach** and must not be expressible. Blurring the two reintroduces the
    #187 failure at N children instead of one.
    """
    consumes = ""
    if piece.consumes:
        items = "\n".join(
            f"- {signature} — from the piece `{owner}`"
            for signature, owner in piece.consumes
        )
        consumes = _CONSUMES_PROMPT.format(items=items)

    return _PIECE_PROMPT.format(
        feature=feature,
        slug=piece.slug,
        files="\n".join(f"- {path}" for path in piece.files),
        exposes="\n".join(f"- {item}" for item in piece.exposes) or "- (nothing)",
        consumes=consumes,
        branch=branch_name(piece.slug, feature=feature),
    )
```

Add the import at the top of the module, under the existing imports:

```python
from mnemo.core import contracts
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_dispatch_plan.py -v`

Expected: PASS — 5 new tests passing alongside the existing ones

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/dispatch.py tests/unit/test_dispatch_plan.py
git commit -m "feat(dispatch): build a child prompt from a contract piece"
```

---

### Task 6: Dispatch a whole contract

**Files:**
- Modify: `src/mnemo/core/dispatch.py` (after `dispatch_all`, around `:315`)
- Test: `tests/unit/test_dispatch_worktrees.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_dispatch_worktrees.py`:

```python
def _contract(tmp_path: Path, verdict: str = "parallel"):
    from mnemo.core import contracts

    return contracts.Contract(
        feature="demo",
        verdict=verdict,
        pieces=[
            contracts.Piece(slug="one", files=["a.py"], exposes=["`f()`"]),
            contracts.Piece(slug="two", files=["b.py"], exposes=["`g()`"]),
        ],
        path=tmp_path / "contract.md",
    )


def test_dispatch_contract_spawns_one_child_per_piece(repo: Path, monkeypatch) -> None:
    spawned: list[Path] = []
    monkeypatch.setattr(
        dispatch, "spawn_child", lambda prompt, *, cwd: spawned.append(cwd) or "id1"
    )
    results = dispatch.dispatch_contract(_contract(repo), repo_root=repo)
    assert [r.issue for r in results] == ["c-one", "c-two"]
    assert [p.name for p in spawned] == ["proj-wt-c-one", "proj-wt-c-two"]


def test_dispatch_contract_refuses_a_sequential_verdict(repo: Path, monkeypatch) -> None:
    """``sequential`` means the work does not divide — spawning would be wrong."""
    monkeypatch.setattr(dispatch, "spawn_child", lambda *a, **k: pytest.fail("spawned"))
    with pytest.raises(dispatch.DispatchError, match="sequential"):
        dispatch.dispatch_contract(_contract(repo, verdict="sequential"), repo_root=repo)


def test_one_failing_piece_does_not_strand_the_others(repo: Path, monkeypatch) -> None:
    calls = {"n": 0}

    def flaky(prompt, *, cwd):
        calls["n"] += 1
        if calls["n"] == 1:
            raise dispatch.DispatchError("boom")
        return "id2"

    monkeypatch.setattr(dispatch, "spawn_child", flaky)
    results = dispatch.dispatch_contract(_contract(repo), repo_root=repo)
    assert results[0].error == "boom"
    assert results[1].short_id == "id2"
    assert not (repo.parent / "proj-wt-c-one").exists()  # rolled back
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_dispatch_worktrees.py -v`

Expected: FAIL — `AttributeError: module 'mnemo.core.dispatch' has no attribute 'dispatch_contract'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/dispatch.py`, after `dispatch_all`:

```python
def dispatch_piece(
    piece: "contracts.Piece", *, feature: str, repo_root: Path | str
) -> Dispatched:
    """Dispatch one contract piece: make its tree, spawn its child.

    The mirror of :func:`dispatch_issue` with the fetch step already done —
    the contract was read and validated in one pass before any of this ran.
    """
    target = f"c-{piece.slug}"
    tree = ensure_worktree(target, repo_root=repo_root)
    try:
        short_id = spawn_child(
            build_piece_prompt(piece, feature=feature), cwd=tree
        )
    except BaseException:
        remove_worktree(tree, repo_root=repo_root)
        raise
    return Dispatched(issue=target, worktree=tree, short_id=short_id)


def dispatch_contract(
    contract: "contracts.Contract", *, repo_root: Path | str
) -> list[Dispatched]:
    """Dispatch every piece of a contract, independently.

    Refuses a ``sequential`` verdict outright: that verdict is the
    decomposition reporting that the work does not divide, and spawning
    children against it produces exactly the merge conflicts the contract
    exists to prevent.
    """
    if not contract.is_dispatchable:
        raise DispatchError(
            f"contract verdict is {contract.verdict!r}, not 'parallel' — "
            "this work does not divide; run it in one session"
        )

    out: list[Dispatched] = []
    for piece in contract.pieces:
        try:
            out.append(
                dispatch_piece(
                    piece, feature=contract.feature, repo_root=repo_root
                )
            )
        except DispatchError as exc:
            out.append(Dispatched(issue=f"c-{piece.slug}", error=str(exc)))
    return out
```

Widen `Dispatched.issue`'s annotation (`dispatch.py:70`) from `int` to `Target`, and its docstring:

```python
@dataclass(frozen=True)
class Dispatched:
    """One child's outcome — by issue number or piece slug. ``error`` is set iff it failed."""

    issue: Target
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_dispatch_worktrees.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/dispatch.py tests/unit/test_dispatch_worktrees.py
git commit -m "feat(dispatch): spawn a child per contract piece"
```

---

### Task 7: Wire `--contract` into the CLI

**Files:**
- Modify: `src/mnemo/cli/parser.py:87-92`
- Modify: `src/mnemo/cli/commands/dispatch.py:38-75`
- Test: `tests/unit/test_cli_dispatch.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_cli_dispatch.py`:

```python
def test_contract_flag_parses() -> None:
    from mnemo.cli.parser import _build_parser

    args = _build_parser().parse_args(["dispatch", "--contract", "c.md"])
    assert args.contract == "c.md"
    assert not args.issues


def test_contract_and_issues_are_refused_together(capsys) -> None:
    """Enforced in the command, not by argparse — see the parser note below."""
    rc = cli.main(["dispatch", "193", "--contract", "c.md"])
    assert rc == 1
    assert "both" in capsys.readouterr().out.lower()


def test_neither_issues_nor_contract_is_refused(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        "mnemo.cli.commands.dispatch._repo_root", lambda: tmp_path
    )
    args = argparse.Namespace(issues=[], contract=None, dry_run=False)
    assert cmd_dispatch(args) == 1
    assert "issue" in capsys.readouterr().out.lower()


def test_dry_run_prints_pieces_without_spawning(monkeypatch, tmp_path, capsys) -> None:
    """The paths are pure functions of the contract, so the plan is checkable."""
    contract = tmp_path / "c.md"
    contract.write_text(VALID_CONTRACT, encoding="utf-8")
    monkeypatch.setattr(
        "mnemo.cli.commands.dispatch._repo_root", lambda: tmp_path / "proj"
    )
    monkeypatch.setattr(
        "mnemo.core.dispatch.spawn_child", lambda *a, **k: pytest.fail("spawned")
    )
    args = argparse.Namespace(issues=[], contract=str(contract), dry_run=True)
    assert cmd_dispatch(args) == 0
    out = capsys.readouterr().out
    assert "proj-wt-c-parser" in out
    assert "feat/contract-dispatch/parser" in out


def test_unreadable_contract_is_reported_not_raised(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.setattr(
        "mnemo.cli.commands.dispatch._repo_root", lambda: tmp_path
    )
    args = argparse.Namespace(
        issues=[], contract=str(tmp_path / "missing.md"), dry_run=False
    )
    assert cmd_dispatch(args) == 1
    assert "contract" in capsys.readouterr().out.lower()
```

Add at the top of the file, alongside the existing imports (`cli` is already
imported there):

```python
import argparse
from mnemo.cli.commands.dispatch import cmd_dispatch

VALID_CONTRACT = """\
---
feature: contract-dispatch
created: 2026-09-12
verdict: parallel
---

## parser
- **files:** src/mnemo/core/contracts.py
- **exposes:** `parse_contract(path) -> Contract`
- **consumes:** nothing
"""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/unit/test_cli_dispatch.py -v`

Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'contract'`

- [ ] **Step 3: Write the implementation**

In `src/mnemo/cli/parser.py`, replace the `dispatch_p` block (lines 87-92):

```python
    dispatch_p = sub.add_parser(
        "dispatch",
        help="spawn a background child per issue, or per piece of a contract")
    dispatch_p.add_argument("issues", nargs="*", type=int, metavar="ISSUE",
                            help="GitHub issue number(s) to dispatch")
    dispatch_p.add_argument("--contract", metavar="PATH",
                            help="dispatch each piece of a decomposition contract")
    dispatch_p.add_argument("--dry-run", dest="dry_run", action="store_true",
                            help="print the worktree and branch per child without spawning")
```

`nargs="*"` replaces `nargs="+"` so `--contract` can stand alone. Mutual
exclusion is enforced in the command rather than by argparse, because the
positional and the flag cannot share an `add_mutually_exclusive_group` when the
positional is variadic.

In `src/mnemo/cli/commands/dispatch.py`, inside `cmd_dispatch`, replace
everything after the `root is None` guard:

```python
    issues = list(getattr(args, "issues", []) or [])
    contract_path = getattr(args, "contract", None)

    if issues and contract_path:
        print("pass issue numbers or --contract, not both")
        return 1
    if not issues and not contract_path:
        print("nothing to dispatch: give an issue number or --contract PATH")
        return 1

    if contract_path:
        return _dispatch_contract(contract_path, root=root, args=args)

    if getattr(args, "dry_run", False):
        for issue in issues:
            tree = core.worktree_path(issue, repo_root=root)
            print(f"#{issue}  {core.branch_name(issue)}  {tree}")
        return 0

    return _report(core.dispatch_all(issues, repo_root=root))
```

Add the two helpers below `cmd_dispatch`:

```python
def _dispatch_contract(path: str, *, root: Path, args: argparse.Namespace) -> int:
    """Read, validate, then dispatch — refusing before any tree is created."""
    from mnemo.core import contracts
    from mnemo.core import dispatch as core

    try:
        contract = contracts.parse_contract(path)
    except contracts.ContractError as exc:
        # A contract that cannot be trusted is a decomposition to redo, not a
        # dispatch to retry, so the message names the file rather than a step.
        print(f"contract unusable: {exc}")
        return 1

    if getattr(args, "dry_run", False):
        for piece in contract.pieces:
            target = f"c-{piece.slug}"
            tree = core.worktree_path(target, repo_root=root)
            branch = core.branch_name(target, feature=contract.feature)
            print(f"{piece.slug}  {branch}  {tree}")
        return 0

    try:
        results = core.dispatch_contract(contract, repo_root=root)
    except core.DispatchError as exc:
        print(str(exc))
        return 1
    return _report(results)


def _report(results: list) -> int:
    """Print every outcome, then the two commands the maintainer needs next."""
    started = [r for r in results if r.error is None]
    failed = [r for r in results if r.error is not None]

    for r in started:
        print(f"{r.issue}  {r.short_id}  {r.worktree}")
    for r in failed:
        # Never silent: a skipped child the maintainer does not see is one
        # they will assume is running.
        print(f"{r.issue}  FAILED: {r.error}")

    if started:
        print()
        print("  queue:  mnemo sessions")
        print(f"  attach: claude attach {started[0].short_id}")

    return 1 if failed else 0
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_dispatch.py -v`

Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest tests/unit -q`

Expected: PASS — all tests, including the CI-config assertions in
`tests/unit/test_release_workflow.py`

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/cli/parser.py src/mnemo/cli/commands/dispatch.py tests/unit/test_cli_dispatch.py
git commit -m "feat(cli): mnemo dispatch --contract"
```

---

### Task 8: The decomposition skill

Markdown only. No code, no automated test — the first real feature dispatched through it is the test.

**Files:**
- Create: `skills/decomposing-for-dispatch/SKILL.md`

This repo ships as a Claude Code plugin (`.claude-plugin/plugin.json`), and a
plugin's skills live at `skills/<name>/SKILL.md` relative to the repo root. That
directory does not exist yet — this task creates it.

- [ ] **Step 1: Create the directory**

Run: `mkdir -p skills/decomposing-for-dispatch`

- [ ] **Step 2: Write the skill**

```markdown
---
name: decomposing-for-dispatch
description: Use after designing a feature, when deciding whether its parts can be built in parallel - produces the contract file that `mnemo dispatch --contract` consumes
---

# Decomposing a feature for dispatch

Turn a designed feature into a **contract**: the pieces, and the boundary
between them. Input is the current conversation — a brainstorm, a plan, or the
maintainer describing the work. Do not go looking for a plan file; there may not
be one.

Write the contract to `docs/mnemo/contracts/YYYY-MM-DD-<feature>.md`. Then stop.
Dispatching is the maintainer's decision, not yours.

## The test

For every pair of candidate pieces, ask:

> Can piece A be written and tested without reading the interior of B?

If no, they are not two pieces. Merge them and ask again.

Two pieces may depend on each other's **signatures** — that is what the contract
records. They may not depend on each other's **internals**.

## `sequential` is a real answer

If the work does not divide, write `verdict: sequential` and say why. This is a
correct outcome, not a failure. A decomposition that always finds a cut produces
only bad merges.

Cutting by area — one piece for the backend, one for the frontend, one for the
tests — almost always fails the test above, because all three land in the same
files. Teams exist because a boundary already does; the boundary does not appear
because you named teams.

## Boundary, not approach

The contract says **where** a piece may work and **what** it must deliver. It
never says **how**.

- Boundary: "only `src/mnemo/core/contracts.py`", "deliver `parse_contract(path) -> Contract`"
- Approach: "use a regex", "subclass `dict`", "cache it"

A child given an approach cannot refuse a wrong one. This has already cost a
real dispatch (#187) and the refusal was correct.

## Format

```markdown
---
feature: <slug>
created: YYYY-MM-DD
verdict: parallel
---

## <piece-slug>
- **files:** path/one.py, path/two.py
- **exposes:** `literal_signature(arg) -> Type`
- **consumes:** `other_signature` from `other-piece`
```

Rules the parser enforces — a contract breaking one is refused:

- Slugs are lowercase letters, digits and hyphens.
- Every piece declares at least one file.
- Every `consumes` names a piece that `exposes` it.
- `exposes` is a literal signature. Other pieces are written against it while
  they wait, so a prose description cannot be delivered against.

## After writing

Tell the maintainer the file is ready for review and show the command:

    mnemo dispatch --contract docs/mnemo/contracts/<file>.md --dry-run

Do not run it.
```

- [ ] **Step 3: Verify the skill is discoverable**

Run: `head -5 skills/decomposing-for-dispatch/SKILL.md`

Expected: the frontmatter, with `name` and `description` present.

The skill is only listed after the plugin is reinstalled or the session
restarts, so do not treat its absence from the current session's skill list as
a failure.

- [ ] **Step 4: Commit**

```bash
git add skills/decomposing-for-dispatch/SKILL.md
git commit -m "feat(skills): decomposing-for-dispatch"
```

---

### Task 9: Documentation and changelog

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md` (the section documenting `mnemo dispatch`, if one exists)

- [ ] **Step 1: Find where dispatch is documented**

Run: `grep -rn "mnemo dispatch" README.md docs/*.md CHANGELOG.md | head`

- [ ] **Step 2: Add the changelog entry**

Under the unreleased heading in `CHANGELOG.md`:

```markdown
### Added

- `mnemo dispatch --contract <path>` — spawn one background child per piece of
  a decomposition contract, instead of requiring one GitHub issue per piece. A
  contract names each piece's file boundary and the signatures it exposes and
  consumes; pieces are addressed as `<repo>-wt-c-<slug>` on
  `feat/<feature>/<slug>`. A `verdict: sequential` contract is refused rather
  than dispatched.
- Skill `decomposing-for-dispatch` — writes the contract file from the current
  conversation. The maintainer reviews it before anything is spawned; no model
  spawns work.
```

- [ ] **Step 3: Update the README section** if `mnemo dispatch` is documented there, adding the `--contract` form alongside the issue form.

- [ ] **Step 4: Run the full suite one last time**

Run: `python -m pytest tests/unit -q`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md
git commit -m "docs: contract dispatch"
```

---

## Verification before claiming completion

Run each and confirm the output before saying the work is done:

```bash
python -m pytest tests/unit -q                    # whole suite green
grep -rn "read_sessions\|render_queue" src/mnemo/hooks/ src/mnemo/core/mcp/
grep -rn "dispatch" src/mnemo/hooks/ src/mnemo/core/mcp/
```

The two greps must return **nothing**. They assert the invariants this design
preserves: no hook and no MCP tool may enumerate sessions or spawn work. A hit
means the implementation revoked an invariant the spec promised to keep.

Then a real end-to-end check, which the unit suite cannot cover because
`conftest.py:69` monkeypatches `spawn_child` globally:

```bash
mnemo dispatch --contract docs/mnemo/contracts/<file>.md --dry-run
```

Expected: one line per piece with its branch and worktree path, and **no
worktree created** — verify with `git worktree list`.

## What this plan does not build

Recorded so a later reader does not assume they were forgotten: no ordering or
scheduling, no concurrency cap, no automatic merge, no child-to-child
communication, no MCP or hook access to dispatch, and no automated test of the
skill. Each is a decision in the spec, not an omission.
