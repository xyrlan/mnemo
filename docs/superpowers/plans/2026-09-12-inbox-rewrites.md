# `_inbox` Rewrites Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Drain the 35 staged `.proposed.md` rewrites in `shared/_inbox/` by classifying them, auto-merging the provably-lossless ones, and reconciling `written_hash` so the extractor stops re-proposing the same rewrite forever.

**Architecture:** Three pure-ish core modules (`classify`, `merge`, `apply`) under `src/mnemo/core/rewrites/`, plus one CLI command `mnemo rewrites`. `classify` diffs each proposal against its live rule with `difflib` and labels it `insert_only` / `mixed` / `full_rewrite`. `merge` rebuilds frontmatter with per-key policies (union `sources[]`, proposal wins on `description`, live wins on `activates_on`) and takes the proposal body. `apply` archives pristine originals to `shared/_archive/rewrites-<run_id>/` with a `manifest.json` for `undo`, writes the merged rule, deletes the `.proposed.md`, and — the step that stops the regeneration loop — sets `entry.written_hash = content_hash(merged)` in `.mnemo/extraction-state.json`.

**Tech Stack:** Python 3.11+, stdlib only (`difflib`, `dataclasses`, `pathlib`, `re`, `json`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-12-inbox-rewrites-design.md`

**Baseline to hold:** `PYTHONPATH=src python3 -m pytest -q` → 2594 passed, 2 skipped, 8 deselected (measured 2026-09-12).

**Decisions already made (do not re-litigate):**
- The 6 `full_rewrite` proposals do NOT auto-apply. `--apply-safe` covers `insert_only` only.
- No interactive per-rule TUI. Printed plan + `--show` / `--accept` / `--reject` flags, mirroring `dedup-rules` / `reclassify`.
- Vault-wide `written_hash` drift from the slug-stamp migration is out of scope (separate issue, Task 9).

---

## File Structure

| Path | Responsibility |
|---|---|
| `src/mnemo/core/rewrites/__init__.py` | Package marker; re-exports `classify`, `plan`, `apply`, `undo` for the CLI. |
| `src/mnemo/core/rewrites/types.py` | `Rewrite`, `ApplyPlan`, `ApplyReport` dataclasses. Separate module so `classify` and `apply` can both import them without a cycle (same reason `reclassify_types.py` exists). |
| `src/mnemo/core/rewrites/classify.py` | Read proposal + live, diff bodies, label `kind`, compute `keep_ratio`. No writes. |
| `src/mnemo/core/rewrites/merge.py` | Build merged page text: frontmatter per-key policy + proposal body. No I/O. |
| `src/mnemo/core/rewrites/apply.py` | Archive, write, delete proposal, reconcile ledger, `undo`. All the I/O. |
| `src/mnemo/cli/commands/rewrites.py` | `@command("rewrites")` — print plan, `--apply-safe`, `--show`, `--accept`, `--reject`, `--undo`. |
| `tests/unit/test_rewrites_classify.py` | Classification boundaries. |
| `tests/unit/test_rewrites_merge.py` | Frontmatter policy + body preservation + idempotence. |
| `tests/unit/test_rewrites_apply.py` | Ledger reconciliation (the anti-regeneration test), archive/undo, guard. |
| `tests/unit/test_cli_rewrites.py` | Registration, flag parsing, printed output. |

Modified: `src/mnemo/cli/parser.py` (subparser + `ADVANCED_COMMANDS`), `src/mnemo/cli/commands/__init__.py` (import for `@command` registration), `CHANGELOG.md`.

---

## Task 1: Types

**Files:**
- Create: `src/mnemo/core/rewrites/__init__.py`
- Create: `src/mnemo/core/rewrites/types.py`
- Test: `tests/unit/test_rewrites_classify.py` (first test lands in Task 2; this task has no test of its own — it is pure data declaration with no behavior)

- [ ] **Step 1: Create the package marker**

```python
# src/mnemo/core/rewrites/__init__.py
"""Staged `.proposed.md` rewrite reconciliation (#159).

``shared/_inbox/`` accumulates ``.proposed.md`` rewrites of live rules. They are
excluded from every consumer surface by ``filters.is_consumer_visible``, so recall
serves the un-updated live rule instead — and the extractor re-proposes the same
rewrite on every run because accept was a manual ``mv`` that never reconciled
``entry.written_hash``.

This package classifies each rewrite, merges the safe ones, and advances the
ledger so an accepted rule stops re-proposing.
"""
from __future__ import annotations
```

- [ ] **Step 2: Write the dataclasses**

```python
# src/mnemo/core/rewrites/types.py
"""Dataclasses shared by the rewrites halves.

``classify`` (planning) and ``apply`` (execution) both need these, and importing
either from the other would be circular — same split as ``reclassify_types.py``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

#: Body-diff classification of a staged rewrite.
#:
#: ``insert_only`` — every non-equal opcode is an insert, so the proposal body is
#: a superset of the live body and merging drops nothing. Auto-mergeable.
#: ``full_rewrite`` — the proposal preserves none of the live body. The live rule
#: is superseded (measured: `MARKETPLACE_ENABLED` reads false when it is true).
#: ``mixed`` — anything between. Reworded prose interleaved with new facts.
Kind = Literal["insert_only", "mixed", "full_rewrite"]

Action = Literal["merge", "replace", "skip"]


@dataclass(frozen=True)
class Rewrite:
    proposal: Path
    live: Path
    #: ``"<type>/<slug>"`` — the ``.mnemo/extraction-state.json`` entry key.
    key: str
    kind: Kind
    #: Fraction of live non-blank body lines the proposal preserves, 0.0–1.0.
    keep_ratio: float
    inserted_lines: int
    dropped_lines: int


@dataclass
class ApplyPlan:
    run_id: str
    entries: list[tuple[Rewrite, Action]] = field(default_factory=list)


@dataclass
class ApplyReport:
    merged: int = 0
    replaced: int = 0
    archive_dir: Optional[Path] = None
    notes: list = field(default_factory=list)
    #: Rewrites that could not be applied: ``[{"key", "reason"}, ...]``.
    #: A no-op apply must never be silent, so the CLI prints these.
    #:
    #: There is deliberately no ``skipped_count`` beside this list. An earlier
    #: draft had both, and they disagreed by construction: only the
    #: "skipped by plan" branch bumped the counter, while a read failure
    #: appended here without touching it. One fact, one field — callers use
    #: ``len(report.skipped)``.
    skipped: list = field(default_factory=list)
```

- [ ] **Step 3: Verify the module imports**

Run: `PYTHONPATH=src python3 -c "from mnemo.core.rewrites.types import Rewrite, ApplyPlan, ApplyReport; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add src/mnemo/core/rewrites/__init__.py src/mnemo/core/rewrites/types.py
git commit -m "feat(rewrites): dataclasses for staged rewrite reconciliation (#159)"
```

---

## Task 2: Classify — insert-only detection

**Files:**
- Create: `src/mnemo/core/rewrites/classify.py`
- Test: `tests/unit/test_rewrites_classify.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rewrites_classify.py
"""Classification of staged ``.proposed.md`` rewrites (#159).

The real vault's 35 proposals split 11 / 18 / 6 across insert_only / mixed /
full_rewrite. One accept semantic cannot serve all three: a plain ``mv``
discards live content in the 11, and a plain append makes the 6 assert both
``MARKETPLACE_ENABLED = false`` and ``= true``.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.rewrites import classify as C


def _write(p: Path, fm: str, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")
    return p


def _pair(vault: Path, slug: str, live_body: str, prop_body: str, *, page_type: str = "project"):
    fm = f"name: n\nslug: {slug}\ntype: {page_type}\nsources:\n  - bots/a/memory/{slug}.md"
    _write(vault / "shared" / page_type / f"{slug}.md", fm, live_body)
    _write(vault / "shared" / "_inbox" / page_type / f"{slug}.proposed.md", fm, prop_body)


def test_appended_lines_classify_as_insert_only(tmp_vault: Path):
    _pair(
        tmp_vault,
        "a__x",
        "line one\nline two\n",
        "line one\nline two\nline three\n",
    )

    rewrites = C.classify(tmp_vault)

    assert len(rewrites) == 1
    r = rewrites[0]
    assert r.kind == "insert_only"
    assert r.keep_ratio == 1.0
    assert r.inserted_lines == 1
    assert r.dropped_lines == 0
    assert r.key == "project/a__x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_classify.py::test_appended_lines_classify_as_insert_only -v`
Expected: FAIL — `ImportError: cannot import name 'classify' from 'mnemo.core.rewrites'`

(The test does `from mnemo.core.rewrites import classify as C`. Importing a *name
from a package* that does not yet exist raises `ImportError: cannot import name`,
not `ModuleNotFoundError` — the latter is what `import mnemo.core.rewrites.classify`
would raise. If you see the `ImportError`, TDD is working correctly.)

- [ ] **Step 3: Write minimal implementation**

```python
# src/mnemo/core/rewrites/classify.py
"""Diff each staged ``.proposed.md`` against its live rule and label it.

Frontmatter is excluded from the body diff on purpose: every proposal restamps
``extraction_run`` and ``promoted_at``, so including frontmatter would make all
35 look like rewrites. The interesting question is what happens to the prose.
"""
from __future__ import annotations

import difflib
from pathlib import Path

from mnemo.core.filters import INBOX_DIR, is_proposed_sibling
from mnemo.core.rewrites.types import Kind, Rewrite

PROPOSED_SUFFIX = ".proposed.md"


def _split_body(text: str) -> str:
    """Return everything after the closing frontmatter ``---``."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


def _live_for(proposal: Path, vault_root: Path) -> Path:
    """``shared/_inbox/<type>/<slug>.proposed.md`` → ``shared/<type>/<slug>.md``."""
    page_type = proposal.parent.name
    slug = proposal.name[: -len(PROPOSED_SUFFIX)]
    return vault_root / "shared" / page_type / f"{slug}.md"


def _classify_bodies(live_body: str, proposal_body: str) -> tuple[Kind, float, int, int]:
    live = live_body.splitlines()
    prop = proposal_body.splitlines()
    matcher = difflib.SequenceMatcher(None, live, prop, autojunk=False)
    opcodes = matcher.get_opcodes()

    changed = {tag for tag, *_ in opcodes if tag != "equal"}
    inserted = sum(j2 - j1 for tag, _i1, _i2, j1, j2 in opcodes if tag in ("insert", "replace"))
    dropped = sum(i2 - i1 for tag, i1, i2, _j1, _j2 in opcodes if tag in ("delete", "replace"))

    live_nonblank = len([ln for ln in live if ln.strip()])
    kept_nonblank = sum(
        len([ln for ln in live[i1:i2] if ln.strip()])
        for tag, i1, i2, _j1, _j2 in opcodes
        if tag == "equal"
    )
    keep_ratio = kept_nonblank / live_nonblank if live_nonblank else 1.0

    if changed <= {"insert"}:
        kind: Kind = "insert_only"
    elif keep_ratio == 0.0:
        kind = "full_rewrite"
    else:
        kind = "mixed"
    return kind, keep_ratio, inserted, dropped


def classify(vault_root: Path) -> list[Rewrite]:
    """Every staged rewrite under ``shared/_inbox/`` that has a live counterpart.

    A proposal with no live rule is skipped: there is nothing to merge into, and
    such a file belongs to the plain-staged-page path, not here.
    """
    vault_root = Path(vault_root)
    inbox = vault_root / "shared" / INBOX_DIR
    if not inbox.is_dir():
        return []

    out: list[Rewrite] = []
    for proposal in sorted(inbox.rglob("*.md")):
        if not is_proposed_sibling(proposal):
            continue
        if not proposal.name.endswith(PROPOSED_SUFFIX):
            # ``.update-proposed.md`` has a different provenance (an _inbox
            # target that vanished while the promoted file survived) and no
            # reliable live counterpart. Left for a follow-up.
            continue
        live = _live_for(proposal, vault_root)
        if not live.is_file():
            continue
        # Deliberately unguarded — see the note in the shipped module. Swallowing
        # OSError here makes an unreadable proposal vanish from the plan with no
        # signal, which is the one failure this command must not have.
        live_text = live.read_text(encoding="utf-8", errors="replace")
        prop_text = proposal.read_text(encoding="utf-8", errors="replace")
        kind, keep_ratio, inserted, dropped = _classify_bodies(
            _split_body(live_text), _split_body(prop_text)
        )
        page_type = proposal.parent.name
        slug = proposal.name[: -len(PROPOSED_SUFFIX)]
        out.append(Rewrite(
            proposal=proposal,
            live=live,
            key=f"{page_type}/{slug}",
            kind=kind,
            keep_ratio=keep_ratio,
            inserted_lines=inserted,
            dropped_lines=dropped,
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_classify.py::test_appended_lines_classify_as_insert_only -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/rewrites/classify.py tests/unit/test_rewrites_classify.py
git commit -m "feat(rewrites): classify insert-only staged rewrites (#159)"
```

---

## Task 3: Classify — mixed, full_rewrite, and exclusions

**Files:**
- Modify: `tests/unit/test_rewrites_classify.py` (append tests)
- Modify: `src/mnemo/core/rewrites/classify.py` only if a test fails

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_rewrites_classify.py`:

```python
def test_replaced_middle_line_classifies_as_mixed(tmp_vault: Path):
    _pair(
        tmp_vault,
        "a__y",
        "keep one\nOLD middle\nkeep two\n",
        "keep one\nNEW middle\nkeep two\n",
    )

    r = C.classify(tmp_vault)[0]

    assert r.kind == "mixed"
    assert 0.0 < r.keep_ratio < 1.0
    # A ``replace`` region counts on BOTH sides: one line left, one arrived.
    # Pinned so a later change to the opcode accounting cannot quietly turn
    # "1 line changed" into "+1" or "-1" alone.
    assert r.dropped_lines == 1
    assert r.inserted_lines == 1


def test_identical_body_is_insert_only_with_nothing_inserted(tmp_vault: Path):
    """A no-op proposal is safe to merge — and must report that it adds nothing.

    ``insert_only`` is ``changed <= {"insert"}``, a subset test, so an empty
    opcode-change set qualifies. That is correct (merging a no-op loses
    nothing), but it means ``--apply-safe`` will sweep such a proposal up, so
    the counts it reports have to be honest. Theoretical on the real vault
    today: all 35 staged rewrites differ substantively.
    """
    _pair(tmp_vault, "a__same", "one\ntwo\n", "one\ntwo\n")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "insert_only"
    assert r.keep_ratio == 1.0
    assert r.inserted_lines == 0
    assert r.dropped_lines == 0


def test_disjoint_body_classifies_as_full_rewrite(tmp_vault: Path):
    _pair(
        tmp_vault,
        "a__z",
        "MARKETPLACE_ENABLED = false\naba Loja removida\n",
        "MARKETPLACE_ENABLED = true\nliberada geral\n",
    )

    r = C.classify(tmp_vault)[0]

    assert r.kind == "full_rewrite"
    assert r.keep_ratio == 0.0


def test_proposal_without_a_live_rule_is_excluded(tmp_vault: Path):
    fm = "name: n\nslug: orphan\ntype: project"
    _write(tmp_vault / "shared" / "_inbox" / "project" / "orphan.proposed.md", fm, "body\n")

    assert C.classify(tmp_vault) == []


def test_update_proposed_suffix_is_excluded(tmp_vault: Path):
    fm = "name: n\nslug: a__u\ntype: project"
    _write(tmp_vault / "shared" / "project" / "a__u.md", fm, "body\n")
    _write(tmp_vault / "shared" / "_inbox" / "project" / "a__u.update-proposed.md", fm, "body2\n")

    assert C.classify(tmp_vault) == []


def test_blank_line_only_delta_is_insert_only_with_no_inserted_content(tmp_vault: Path):
    _pair(tmp_vault, "a__b", "one\ntwo\n", "one\n\ntwo\n")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "insert_only"
    assert r.keep_ratio == 1.0
    assert r.dropped_lines == 0


def test_classification_covers_every_page_type_under_inbox(tmp_vault: Path):
    _pair(tmp_vault, "ref-rule", "a\n", "a\nb\n", page_type="reference")
    _pair(tmp_vault, "fb-rule", "a\n", "a\nb\n", page_type="feedback")

    keys = {r.key for r in C.classify(tmp_vault)}

    assert keys == {"reference/ref-rule", "feedback/fb-rule"}
```

- [ ] **Step 2: Run the tests**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_classify.py -v`
Expected: all 7 PASS. The Task 2 implementation already covers these paths; if any fails, fix `classify.py` — do not weaken the test.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_rewrites_classify.py
git commit -m "test(rewrites): cover mixed, full_rewrite, and excluded proposals (#159)"
```

---

## Task 4: Merge — frontmatter policy

**Files:**
- Create: `src/mnemo/core/rewrites/merge.py`
- Test: `tests/unit/test_rewrites_merge.py`

Per-key policy from the spec (measured on the real vault; counts are `<in the 11 insert_only>` · `<across all 35>`):

| Key | Differs | Policy |
|---|---|---|
| `sources[]` | 11/11 · 33/35 | normalize both sides through `vault_relative_source`, then union (live order first) |
| `description` | 5/11 · 22/35 | proposal wins |
| `name` | 0/11 · 3/35 | proposal wins |
| `promoted_at`, `extraction_run`, `extracted_at`, `last_sync` | 11/11 · 35/35 | proposal wins (then `apply` overwrites `written_at`/`last_sync`) |
| `tags` | 0/11 · 5/35 | union of `filters.topic_tags` from both sides; keep the **live** page's `MANAGED_TAGS` marker |
| `confidence`, `demoted_from`, `activates_on` | 0/11 · ≤2/35 | live wins |

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rewrites_merge.py
"""Frontmatter merge policy for staged rewrites (#159).

Policies are not arbitrary. Measured on the real vault: ``sources[]`` deltas in
the auto-merge set are purely the ``/Users/xyrlan/mnemo/`` absolute-path prefix
(#163, fixed v1.3.3) — but 3 of 11 have live=2 → prop=1, so proposal-wins drops
a source. Live ``description`` values are factually stale ("bloqueia assinante em
dia" vs "RESOLVIDO 2026-08-11"). And ``tdd-red-green-per-feature`` flips
``auto-promoted`` → ``needs-review``, which would re-mark a reviewed rule as a
draft.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.filters import parse_frontmatter
from mnemo.core.rewrites import merge as M


def test_sources_are_normalized_and_unioned(tmp_vault: Path):
    live = (
        "---\nname: n\nslug: s\ntype: project\n"
        "sources:\n  - bots/a/memory/s.md\n  - bots/a/memory/extra.md\n---\n\nbody\n"
    )
    proposal = (
        "---\nname: n\nslug: s\ntype: project\n"
        f"sources:\n  - {tmp_vault}/bots/a/memory/s.md\n---\n\nbody\nmore\n"
    )

    out = M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    assert parse_frontmatter(out)["sources"] == [
        "bots/a/memory/s.md",
        "bots/a/memory/extra.md",
    ]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_merge.py::test_sources_are_normalized_and_unioned -v`
Expected: FAIL — `ImportError: cannot import name 'merge' from 'mnemo.core.rewrites'`

(`from mnemo.core.rewrites import merge as M` imports a name from a package, so the
error is `ImportError: cannot import name`, not `ModuleNotFoundError`. That is the
correct RED state.)

- [ ] **Step 3: Write minimal implementation**

```python
# src/mnemo/core/rewrites/merge.py
"""Build the merged page text for an accepted staged rewrite.

Two named entry points rather than one with a flag, so the call site states which
semantic it intends — the thing that was previously implicit in a manual ``mv``.

Frontmatter is rebuilt key-by-key from a measured policy (see the plan's Task 4
table), not taken wholesale from either side. Taking the proposal's frontmatter
wholesale drops live ``sources[]`` entries and copies a ``needs-review`` marker
onto a rule a human already reviewed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from mnemo.core.extract.source_paths import vault_relative_source
from mnemo.core.filters import MANAGED_TAGS, parse_frontmatter, topic_tags

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.DOTALL)

#: Keys the proposal is authoritative for.
_PROPOSAL_WINS = (
    "name",
    "description",
    "promoted_at",
    "extraction_run",
    "extracted_at",
    "last_sync",
    "stability",
)

#: Keys the live page is authoritative for. ``activates_on`` drives rule
#: activation and ``demoted_from`` records a reclassify decision; neither is the
#: extractor's to revise from a session transcript.
_LIVE_WINS = ("confidence", "demoted_from", "activates_on", "enforce")


def _split(text: str) -> tuple[str, str]:
    """``(frontmatter_text, body)``. Frontmatter text excludes the ``---`` fences."""
    m = _FM_RE.match(text)
    if not m:
        return "", text
    return m.group(1), m.group(2)


def _rewrite_block(fm_text: str, key: str, lines: list[str]) -> str:
    """Replace a ``key:`` block with rendered list lines, or append if absent.

    Same surgical approach as ``dedup_rules._rewrite_block``: only the named
    block is touched so every other key keeps its quoting byte-for-byte.
    """
    block_re = re.compile(
        rf"(?m)^{re.escape(key)}:[ \t]*(?:\[\])?[ \t]*\n(?:[ \t]+-[^\n]*\n?)*",
    )
    new_block = f"{key}: []" if not lines else f"{key}:\n" + "\n".join(f"  - {v}" for v in lines)
    if block_re.search(fm_text):
        return block_re.sub(lambda _m: new_block + "\n", fm_text, count=1).rstrip() + "\n"
    return fm_text.rstrip() + "\n" + new_block + "\n"


def _set_scalar(fm_text: str, key: str, value: Any) -> str:
    """Replace a scalar ``key: value`` line, or append it when absent."""
    rendered = f"{key}: {value}"
    line_re = re.compile(rf"(?m)^{re.escape(key)}:[ \t]*[^\n]*$")
    if line_re.search(fm_text):
        return line_re.sub(lambda _m: rendered, fm_text, count=1)
    return fm_text.rstrip() + "\n" + rendered + "\n"


def _merged_sources(live_fm: dict, prop_fm: dict, vault_root: Path) -> list[str]:
    out: list[str] = []
    for fm in (live_fm, prop_fm):
        raw = fm.get("sources") or []
        if isinstance(raw, str):
            raw = [raw]
        for s in raw:
            if not isinstance(s, str):
                continue
            norm = vault_relative_source(s, vault_root)
            if norm not in out:
                out.append(norm)
    return out


def _merged_tags(live_fm: dict, prop_fm: dict) -> list[str]:
    """Live managed marker + union of topic tags (live order first).

    The proposal's topic tags are usually better (`frontend-gotchas`: live
    ``workflow, testing`` → proposal ``react, testing, ui, css``), but its
    managed marker is not: a staged page always carries ``needs-review``, and
    copying that onto a live rule re-marks a reviewed page as a draft.
    """
    live_managed = [t for t in (live_fm.get("tags") or []) if t in MANAGED_TAGS]
    out = list(live_managed)
    for t in topic_tags(live_fm) + topic_tags(prop_fm):
        if t not in out:
            out.append(t)
    return out


def _build(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    live_fm_text, _live_body = _split(live_text)
    _prop_fm_text, prop_body = _split(proposal_text)
    live_fm = parse_frontmatter(live_text)
    prop_fm = parse_frontmatter(proposal_text)

    fm_text = live_fm_text
    for key in _PROPOSAL_WINS:
        if key in prop_fm and not isinstance(prop_fm[key], list):
            fm_text = _set_scalar(fm_text, key, prop_fm[key])
    # _LIVE_WINS needs no action: fm_text starts as the live frontmatter.
    fm_text = _rewrite_block(fm_text, "sources", _merged_sources(live_fm, prop_fm, vault_root))
    fm_text = _rewrite_block(fm_text, "tags", _merged_tags(live_fm, prop_fm))

    return "---\n" + fm_text.rstrip() + "\n---\n" + prop_body


def merge_insert_only(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    """Merge an ``insert_only`` rewrite.

    The proposal body is a superset of the live body (every non-equal opcode is
    an insert), so taking it preserves every live line in order — verified by
    ``classify``, and re-asserted in this module's tests.
    """
    return _build(live_text, proposal_text, vault_root=vault_root)


def replace_wholesale(live_text: str, proposal_text: str, *, vault_root: Path) -> str:
    """Take the proposal body for a ``mixed`` or ``full_rewrite`` acceptance.

    Body handling is identical to :func:`merge_insert_only`; the separate name is
    the point. Here the caller is knowingly discarding live prose, which is why
    ``apply`` archives the pristine original before writing.
    """
    return _build(live_text, proposal_text, vault_root=vault_root)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_merge.py::test_sources_are_normalized_and_unioned -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/rewrites/merge.py tests/unit/test_rewrites_merge.py
git commit -m "feat(rewrites): merge frontmatter with per-key policy (#159)"
```

---

## Task 5: Merge — remaining policy tests

**Files:**
- Modify: `tests/unit/test_rewrites_merge.py`
- Modify: `src/mnemo/core/rewrites/merge.py` only if a test fails

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_rewrites_merge.py`:

```python
def _pair_text(live_fm: str, prop_fm: str, live_body: str = "a\n", prop_body: str = "a\nb\n"):
    return (
        f"---\n{live_fm}\n---\n\n{live_body}",
        f"---\n{prop_fm}\n---\n\n{prop_body}",
    )


def test_description_comes_from_the_proposal(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project\ndescription: only 2 of 5 events — bloqueia assinante",
        "name: n\nslug: s\ntype: project\ndescription: RESOLVIDO 2026-08-11 (issue #285)",
    )

    out = M.merge_insert_only(live, prop, vault_root=tmp_vault)

    assert parse_frontmatter(out)["description"] == "RESOLVIDO 2026-08-11 (issue #285)"


def test_activation_keys_come_from_the_live_rule(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project\nactivates_on: git commit\nconfidence: explicit",
        "name: n\nslug: s\ntype: project\nactivates_on: npm test\nconfidence: inferred",
    )

    fm = parse_frontmatter(M.merge_insert_only(live, prop, vault_root=tmp_vault))

    assert fm["activates_on"] == "git commit"
    assert fm["confidence"] == "explicit"


def test_managed_tag_marker_is_never_copied_from_the_proposal(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: reference\ntags:\n  - auto-promoted\n  - testing\n  - tdd",
        "name: n\nslug: s\ntype: reference\ntags:\n  - needs-review\n  - testing\n  - process",
    )

    tags = parse_frontmatter(M.merge_insert_only(live, prop, vault_root=tmp_vault))["tags"]

    assert "auto-promoted" in tags
    assert "needs-review" not in tags
    # Topic tags from both sides survive.
    assert {"testing", "tdd", "process"} <= set(tags)


def test_insert_only_merge_preserves_every_live_body_line(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project",
        "name: n\nslug: s\ntype: project",
        live_body="first\nsecond\n",
        prop_body="first\nsecond\nthird\n",
    )

    out = M.merge_insert_only(live, prop, vault_root=tmp_vault)

    for line in ("first", "second", "third"):
        assert line in out


def test_merge_is_idempotent(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project\nsources:\n  - bots/a/memory/s.md",
        "name: n\nslug: s\ntype: project\nsources:\n  - bots/a/memory/s.md",
    )

    once = M.merge_insert_only(live, prop, vault_root=tmp_vault)
    twice = M.merge_insert_only(once, prop, vault_root=tmp_vault)

    assert once == twice


def test_replace_wholesale_takes_the_proposal_body(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project",
        "name: n\nslug: s\ntype: project",
        live_body="MARKETPLACE_ENABLED = false\n",
        prop_body="MARKETPLACE_ENABLED = true\n",
    )

    out = M.replace_wholesale(live, prop, vault_root=tmp_vault)

    assert "MARKETPLACE_ENABLED = true" in out
    assert "MARKETPLACE_ENABLED = false" not in out
```

- [ ] **Step 2: Run the tests**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_merge.py -v`
Expected: all 7 PASS. If `test_managed_tag_marker_is_never_copied_from_the_proposal` fails, the bug is in `_merged_tags` — `MANAGED_TAGS` must be read from the live side only.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_rewrites_merge.py
git commit -m "test(rewrites): pin the frontmatter merge policy (#159)"
```

---

## Task 6: Apply — ledger reconciliation and archive

This is the task that fixes the bug. Everything before it is preparation.

**Files:**
- Create: `src/mnemo/core/rewrites/apply.py`
- Test: `tests/unit/test_rewrites_apply.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rewrites_apply.py
"""Applying staged rewrites, and the ledger reconciliation that stops the loop (#159).

Four of five staging sites compare ``content_hash(live)`` against
``entry.written_hash`` and stage a ``.proposed.md`` when they differ. Promotion
was a manual ``mv`` that never advanced ``written_hash``, so every run re-derived
the same rewrite — verified 35/35 drifted on the real vault, ``written_at``
spanning 2026-05 to 2026-09. If ``apply`` does not advance the hash, the merged
result is itself a "user edit" and re-proposes on the next run.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.rewrites import apply as A
from mnemo.core.rewrites import classify as C


def _seed(vault: Path, slug: str = "a__x", *, page_type: str = "project") -> None:
    fm = f"name: n\nslug: {slug}\ntype: {page_type}\nsources:\n  - bots/a/memory/{slug}.md"
    live = vault / "shared" / page_type / f"{slug}.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(f"---\n{fm}\n---\n\nline one\n", encoding="utf-8")

    prop = vault / "shared" / "_inbox" / page_type / f"{slug}.proposed.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text(f"---\n{fm}\n---\n\nline one\nline two\n", encoding="utf-8")

    state = {
        "schema_version": 2,
        "last_run": "2026-09-01T00:00:00",
        "entries": {
            f"{page_type}/{slug}": {
                "source_files": [f"bots/a/memory/{slug}.md"],
                "source_hash": "sha256:aaa",
                # Stale on purpose: this mismatch is what stages a rewrite.
                "written_hash": "sha256:stale",
                "written_at": "2026-05-28T00:00:00",
                "status": "direct",
                "last_sync": "2026-05-28T00:00:00",
            }
        },
    }
    state_path = vault / ".mnemo" / "extraction-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state), encoding="utf-8")


def test_apply_reconciles_written_hash_so_the_rewrite_stops_regenerating(tmp_vault: Path):
    _seed(tmp_vault)
    plan = A.plan(tmp_vault, include={"project/a__x"})

    report = A.apply(plan, tmp_vault)

    assert report.merged == 1
    live = tmp_vault / "shared" / "project" / "a__x.md"
    state = json.loads((tmp_vault / ".mnemo" / "extraction-state.json").read_text())
    entry = state["entries"]["project/a__x"]
    assert entry["written_hash"] == content_hash(live)
    # The proposal is gone, so a second classify finds nothing to re-propose.
    assert C.classify(tmp_vault) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_apply.py::test_apply_reconciles_written_hash_so_the_rewrite_stops_regenerating -v`
Expected: FAIL — `ImportError: cannot import name 'apply' from 'mnemo.core.rewrites'`

(`from mnemo.core.rewrites import apply as A` imports a name from a package, so the
error is `ImportError: cannot import name`, not `ModuleNotFoundError`. That is the
correct RED state.)

- [ ] **Step 3: Write minimal implementation**

```python
# src/mnemo/core/rewrites/apply.py
"""Execute a rewrite plan: archive, write, delete the proposal, advance the ledger.

The vault is not a git repository and only 1 of the real vault's 35 proposals has
any archive coverage, so there is no rollback path but the one this module
creates. Shape mirrors ``reclassify_apply``: pristine ``originals/`` plus a
``manifest.json`` that :func:`undo` replays.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.rewrites import merge as M
from mnemo.core.rewrites.classify import classify
from mnemo.core.rewrites.types import ApplyPlan, ApplyReport, Rewrite

_ACTION_FOR_KIND = {
    "insert_only": "merge",
    "mixed": "replace",
    "full_rewrite": "replace",
}


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def plan(vault_root: Path, *, include: set[str]) -> ApplyPlan:
    """Build a plan covering exactly the rewrites whose ``key`` is in *include*."""
    entries: list[tuple[Rewrite, str]] = []
    for r in classify(vault_root):
        if r.key in include:
            entries.append((r, _ACTION_FOR_KIND[r.kind]))
    return ApplyPlan(run_id=_run_id(), entries=entries)


def apply(plan_obj: ApplyPlan, vault_root: Path) -> ApplyReport:
    """Execute *plan_obj*, keeping byte-exact originals for :func:`undo`."""
    vault_root = Path(vault_root)
    report = ApplyReport()
    if not plan_obj.entries:
        return report

    arch = vault_root / "shared" / "_archive" / f"rewrites-{plan_obj.run_id}"
    # Re-applying a run would copy already-merged files over the pristine
    # originals and overwrite the manifest, silently destroying undo. Guard on
    # the manifest, not the directory — same as reclassify_apply.
    if (arch / "manifest.json").exists():
        raise RuntimeError(f"run {plan_obj.run_id} already applied; undo it first")
    originals = arch / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    report.archive_dir = arch

    state_path = vault_root / ".mnemo" / "extraction-state.json"
    state: dict = {"entries": {}}
    state_backup: str | None = None
    if state_path.exists():
        backup = originals / "extraction-state.json"
        shutil.copy2(state_path, backup)
        state_backup = str(backup.relative_to(vault_root))
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {"entries": {}}
    state.setdefault("entries", {})

    moves: list[dict] = []
    for rewrite, action in plan_obj.entries:
        if action == "skip":
            report.skipped.append({"key": rewrite.key, "reason": "skipped by plan"})
            continue
        try:
            live_text = rewrite.live.read_text(encoding="utf-8")
            prop_text = rewrite.proposal.read_text(encoding="utf-8")
        except OSError as exc:
            report.skipped.append({"key": rewrite.key, "reason": f"read: {exc}"})
            continue

        # Pristine original first — nothing is overwritten before it is archived.
        shutil.copy2(rewrite.live, originals / f"{rewrite.live.stem}.md")

        builder = M.merge_insert_only if action == "merge" else M.replace_wholesale
        merged = builder(live_text, prop_text, vault_root=vault_root)
        atomic_write(rewrite.live, merged)
        rewrite.proposal.unlink()

        # THE FIX: advance written_hash to what is now on disk. Without this the
        # merged file reads as a user edit and the next run re-proposes it.
        entry = state["entries"].get(rewrite.key)
        if entry is not None:
            entry["written_hash"] = content_hash(rewrite.live)
            entry["written_at"] = plan_obj.run_id
            entry["last_sync"] = plan_obj.run_id
        else:
            report.notes.append(f"{rewrite.key}: no state entry; hash not reconciled")

        moves.append({
            "key": rewrite.key,
            "slug": rewrite.live.stem,
            "kind": rewrite.kind,
            "action": action,
            "live_path": str(rewrite.live.relative_to(vault_root)),
            "proposal_path": str(rewrite.proposal.relative_to(vault_root)),
        })
        if action == "merge":
            report.merged += 1
        else:
            report.replaced += 1

    (arch / "manifest.json").write_text(
        json.dumps({
            "run_id": plan_obj.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "moves": moves,
            "skipped": report.skipped,
            "state_backup": state_backup,
        }, indent=2),
        encoding="utf-8",
    )

    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(json.dumps(state, indent=2).encode("utf-8"))
    tmp.replace(state_path)
    return report


def undo(vault_root: Path, run_id: str) -> int:
    """Restore every file *run_id* touched, byte for byte. Returns files restored."""
    vault_root = Path(vault_root)
    arch = vault_root / "shared" / "_archive" / f"rewrites-{run_id}"
    manifest_path = arch / "manifest.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    originals = arch / "originals"
    restored = 0

    for move in manifest.get("moves") or []:
        src = originals / f"{move.get('slug')}.md"
        dest = vault_root / str(move.get("live_path") or "")
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            restored += 1

    backup_rel = manifest.get("state_backup")
    if backup_rel:
        backup = vault_root / str(backup_rel)
        if backup.exists():
            state_path = vault_root / ".mnemo" / "extraction-state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_bytes(backup.read_bytes())
            restored += 1
    return restored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_apply.py::test_apply_reconciles_written_hash_so_the_rewrite_stops_regenerating -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/rewrites/apply.py tests/unit/test_rewrites_apply.py
git commit -m "feat(rewrites): reconcile written_hash on accept so rewrites stop regenerating (#159)"
```

---

## Task 7: Apply — archive, undo, and the re-apply guard

**Files:**
- Modify: `tests/unit/test_rewrites_apply.py`
- Modify: `src/mnemo/core/rewrites/apply.py` only if a test fails

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_rewrites_apply.py`:

```python
import pytest


def test_originals_hold_pristine_live_bytes_and_manifest_shape(tmp_vault: Path):
    _seed(tmp_vault)
    before = (tmp_vault / "shared" / "project" / "a__x.md").read_bytes()
    plan = A.plan(tmp_vault, include={"project/a__x"})

    report = A.apply(plan, tmp_vault)

    original = report.archive_dir / "originals" / "a__x.md"
    assert original.read_bytes() == before

    manifest = json.loads((report.archive_dir / "manifest.json").read_text())
    assert manifest["run_id"] == plan.run_id
    assert manifest["state_backup"].endswith("extraction-state.json")
    move = manifest["moves"][0]
    assert move["key"] == "project/a__x"
    assert move["kind"] == "insert_only"
    assert move["action"] == "merge"
    assert move["live_path"] == "shared/project/a__x.md"


def test_undo_restores_bytes_and_state_exactly(tmp_vault: Path):
    _seed(tmp_vault)
    live = tmp_vault / "shared" / "project" / "a__x.md"
    state_path = tmp_vault / ".mnemo" / "extraction-state.json"
    live_before = live.read_bytes()
    state_before = state_path.read_bytes()
    plan = A.plan(tmp_vault, include={"project/a__x"})
    A.apply(plan, tmp_vault)
    assert live.read_bytes() != live_before

    restored = A.undo(tmp_vault, plan.run_id)

    assert restored == 2  # the rule + the state file
    assert live.read_bytes() == live_before
    assert json.loads(state_path.read_bytes()) == json.loads(state_before)


def test_undo_of_an_unknown_run_restores_nothing(tmp_vault: Path):
    _seed(tmp_vault)

    assert A.undo(tmp_vault, "20260101T000000") == 0


def test_reapplying_the_same_run_id_is_refused(tmp_vault: Path):
    _seed(tmp_vault)
    plan = A.plan(tmp_vault, include={"project/a__x"})
    A.apply(plan, tmp_vault)

    _seed(tmp_vault)  # re-stage so there is something to apply
    with pytest.raises(RuntimeError, match="already applied"):
        A.apply(plan, tmp_vault)


def test_proposal_is_removed_on_success(tmp_vault: Path):
    _seed(tmp_vault)
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    plan = A.plan(tmp_vault, include={"project/a__x"})

    A.apply(plan, tmp_vault)

    assert not prop.exists()


def test_full_rewrite_uses_replace_and_is_counted_separately(tmp_vault: Path):
    fm = "name: n\nslug: a__z\ntype: project\nsources:\n  - bots/a/memory/a__z.md"
    live = tmp_vault / "shared" / "project" / "a__z.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(f"---\n{fm}\n---\n\nMARKETPLACE_ENABLED = false\n", encoding="utf-8")
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__z.proposed.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text(f"---\n{fm}\n---\n\nMARKETPLACE_ENABLED = true\n", encoding="utf-8")
    state_path = tmp_vault / ".mnemo" / "extraction-state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"schema_version": 2, "entries": {}}), encoding="utf-8")

    plan = A.plan(tmp_vault, include={"project/a__z"})
    report = A.apply(plan, tmp_vault)

    assert report.replaced == 1 and report.merged == 0
    assert "MARKETPLACE_ENABLED = true" in live.read_text(encoding="utf-8")
    # No state entry existed, so the missing reconciliation is reported, not silent.
    assert any("hash not reconciled" in n for n in report.notes)


def test_empty_plan_writes_no_archive(tmp_vault: Path):
    _seed(tmp_vault)
    plan = A.plan(tmp_vault, include=set())

    report = A.apply(plan, tmp_vault)

    assert report.merged == 0
    assert report.archive_dir is None
    assert not (tmp_vault / "shared" / "_archive").exists()
```

- [ ] **Step 2: Run the tests**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_rewrites_apply.py -v`
Expected: all 8 PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_rewrites_apply.py
git commit -m "test(rewrites): archive, undo, and re-apply guard (#159)"
```

---

## Task 8: CLI — `mnemo rewrites`

**Files:**
- Create: `src/mnemo/cli/commands/rewrites.py`
- Modify: `src/mnemo/cli/parser.py` (add subparser after the `dedup-rules` block at ~line 229; add `"rewrites"` to `ADVANCED_COMMANDS` at ~line 20)
- Modify: `src/mnemo/cli/commands/__init__.py` (add `rewrites` to the import tuple)
- Test: `tests/unit/test_cli_rewrites.py`

The name is `rewrites`, not `proposals`: `mnemo autopilot proposals {list,review}` already exists and reads a different thing (`.mnemo/proposals/*.json`, `kind: rule_candidate` from `tier0.miss_collector`).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_rewrites.py
"""``mnemo rewrites`` — review surface for staged ``.proposed.md`` rewrites (#159)."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, _build_parser


def test_rewrites_registered_as_advanced_command():
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    assert "rewrites" in COMMANDS and "rewrites" in ADVANCED_COMMANDS
    ns = _build_parser().parse_args(["rewrites", "--apply-safe"])
    assert ns.command == "rewrites" and ns.apply_safe is True
    ns = _build_parser().parse_args(["rewrites", "--show", "project/a__x"])
    assert ns.show == "project/a__x"
    ns = _build_parser().parse_args(["rewrites", "--undo", "20260912T000000"])
    assert ns.undo == "20260912T000000"


def _seed(vault: Path) -> None:
    fm = "name: n\nslug: a__x\ntype: project\nsources:\n  - bots/a/memory/a__x.md"
    live = vault / "shared" / "project" / "a__x.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_text(f"---\n{fm}\n---\n\nline one\n", encoding="utf-8")
    prop = vault / "shared" / "_inbox" / "project" / "a__x.proposed.md"
    prop.parent.mkdir(parents=True, exist_ok=True)
    prop.write_text(f"---\n{fm}\n---\n\nline one\nline two\n", encoding="utf-8")


def test_dry_run_lists_safe_and_undecided_without_writing(tmp_vault: Path, monkeypatch, capsys):
    from mnemo import cli
    from mnemo.cli.commands import rewrites as cmd

    _seed(tmp_vault)
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    args = argparse.Namespace(
        command="rewrites", apply_safe=False, show=None, accept=None, reject=None, undo=None
    )

    assert cmd.cmd_rewrites(args) == 0

    out = capsys.readouterr().out
    assert "1 staged rewrite" in out
    assert "safe to merge (1)" in out
    assert "--apply-safe" in out
    # Nothing was written.
    assert (tmp_vault / "shared" / "_inbox" / "project" / "a__x.proposed.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_cli_rewrites.py -v`
Expected: FAIL — `assert "rewrites" in COMMANDS` fails (module does not exist yet)

- [ ] **Step 3: Write the command**

```python
# src/mnemo/cli/commands/rewrites.py
"""`mnemo rewrites` — review and accept staged ``.proposed.md`` rewrites (#159).

    mnemo rewrites                     # plan: what is staged, and what is safe
    mnemo rewrites --apply-safe        # merge every insert-only rewrite
    mnemo rewrites --show KEY          # full diff for one rewrite
    mnemo rewrites --accept KEY        # accept one mixed / full-rewrite
    mnemo rewrites --reject KEY        # delete one proposal, keep the live rule
    mnemo rewrites --undo RUN_ID       # restore every file an apply touched

``--apply-safe`` is deliberately not ``--apply``: the staged set includes
rewrites where the live rule is superseded outright, and an unqualified apply
over those is the mistake this command exists to prevent.
"""
from __future__ import annotations

import argparse
import difflib

from mnemo.cli.parser import command


def _print_plan(rewrites: list) -> None:
    n = len(rewrites)
    word = "rewrite" if n == 1 else "rewrites"
    print(f"{n} staged {word} in shared/_inbox/\n")

    safe = [r for r in rewrites if r.kind == "insert_only"]
    undecided = [r for r in rewrites if r.kind != "insert_only"]

    if safe:
        print(f"  safe to merge ({len(safe)}) — insert-only, no live content dropped")
        for r in safe:
            print(f"    {r.key:<55} +{r.inserted_lines} lines")
        print()
    if undecided:
        print(f"  needs a decision ({len(undecided)})")
        for r in undecided:
            kind = "full rewrite" if r.kind == "full_rewrite" else "mixed"
            flag = "  ⚠ live rule fully superseded" if r.kind == "full_rewrite" else ""
            # Deliberately prints keep_ratio, NOT inserted_lines/dropped_lines.
            # A ``replace`` region counts on both sides, so one changed line
            # reads as "+1 -1" and looks like two lines of churn. The safe
            # bucket above is insert-only by construction (no replace opcodes),
            # so its ``+N`` is honest. If a future ``--show`` surfaces these
            # counts for mixed/full_rewrite, label them "changed", not "+/-".
            print(f"    {r.key:<45} {kind:<13} keeps {r.keep_ratio:.0%}{flag}")
        print()
    print("(dry-run — `mnemo rewrites --apply-safe` merges the safe set; "
          "`--show KEY` prints one diff)")


@command("rewrites")
def cmd_rewrites(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core.rewrites import apply as A
    from mnemo.core.rewrites.classify import classify

    vault = cli._resolve_vault()

    if getattr(args, "undo", None):
        restored = A.undo(vault, args.undo)
        if not restored:
            print(f"no rewrites run {args.undo} found (nothing restored)")
            return 1
        print(f"restored {restored} file(s) from rewrites-{args.undo}")
        return 0

    rewrites = classify(vault)
    if not rewrites:
        print("no staged rewrites in shared/_inbox/")
        return 0
    by_key = {r.key: r for r in rewrites}

    if getattr(args, "show", None):
        r = by_key.get(args.show)
        if r is None:
            print(f"no staged rewrite for {args.show}")
            return 1
        live = r.live.read_text(encoding="utf-8").splitlines(keepends=True)
        prop = r.proposal.read_text(encoding="utf-8").splitlines(keepends=True)
        print(f"{r.key} — {r.kind}, keeps {r.keep_ratio:.0%} of live lines\n")
        for line in difflib.unified_diff(
            live, prop, fromfile=str(r.live.name), tofile=str(r.proposal.name)
        ):
            print(line, end="")
        return 0

    if getattr(args, "reject", None):
        r = by_key.get(args.reject)
        if r is None:
            print(f"no staged rewrite for {args.reject}")
            return 1
        r.proposal.unlink()
        print(f"rejected {r.key}; live rule untouched")
        print("note: the extractor will re-propose this until the live rule or its "
              "source changes — reject is not a permanent decision")
        return 0

    if getattr(args, "accept", None):
        r = by_key.get(args.accept)
        if r is None:
            print(f"no staged rewrite for {args.accept}")
            return 1
        include = {r.key}
    elif getattr(args, "apply_safe", False):
        include = {r.key for r in rewrites if r.kind == "insert_only"}
        if not include:
            print("no insert-only rewrites to merge")
            return 0
    else:
        _print_plan(rewrites)
        return 0

    plan = A.plan(vault, include=include)
    try:
        report = A.apply(plan, vault)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    for note in report.notes:
        print(f"  note: {note}")
    for item in report.skipped:
        print(f"  skipped: {item.get('key')} · {item.get('reason')}")
    print(f"merged {report.merged} · replaced {report.replaced} · "
          f"skipped {len(report.skipped)}")
    print(f"undo with: mnemo rewrites --undo {plan.run_id}")
    return 0
```

- [ ] **Step 4: Wire the parser**

In `src/mnemo/cli/parser.py`, add `"rewrites"` to the `ADVANCED_COMMANDS` frozenset (~line 20), then add this block immediately after the `dedup` block (~line 232):

```python
    rewrites_p = sub.add_parser(
        "rewrites",
        help="review and accept staged _inbox rewrites of live rules (dry-run default)",
    )
    rewrites_p.add_argument(
        "--apply-safe", action="store_true",
        help="merge every insert-only rewrite (nothing live is dropped)",
    )
    rewrites_p.add_argument(
        "--show", metavar="KEY",
        help="print the full diff for one rewrite (e.g. project/clubinho__sprints-github)",
    )
    rewrites_p.add_argument(
        "--accept", metavar="KEY",
        help="accept one mixed or full rewrite, taking the proposal's body",
    )
    rewrites_p.add_argument(
        "--reject", metavar="KEY",
        help="delete one staged proposal, leaving the live rule untouched",
    )
    rewrites_p.add_argument(
        "--undo", metavar="RUN_ID",
        help="restore every file a previous apply touched, byte for byte",
    )
```

- [ ] **Step 5: Register the module**

In `src/mnemo/cli/commands/__init__.py`, add `rewrites,` to the import tuple, alphabetically between `regen_graph_edges,` and `statusline,`:

```python
    regen_graph_edges,
    rewrites,
    statusline,
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src python3 -m pytest tests/unit/test_cli_rewrites.py -v`
Expected: both PASS

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/commands/rewrites.py src/mnemo/cli/parser.py \
        src/mnemo/cli/commands/__init__.py tests/unit/test_cli_rewrites.py
git commit -m "feat(cli): mnemo rewrites — review surface for staged _inbox rewrites (#159)"
```

---

## Task 9: Full suite, CHANGELOG, and the follow-up issue

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Run the whole suite**

Run: `PYTHONPATH=src python3 -m pytest -q`
Expected: `2594 + <new tests> passed, 2 skipped, 8 deselected`. No pre-existing test may fail. If one does, fix the cause — the merge touches `shared/` walkers that other tests assert on.

- [ ] **Step 2: Verify the dry run against the real vault (read-only)**

Run: `PYTHONPATH=src python3 -m mnemo rewrites`
Expected: `35 staged rewrites in shared/_inbox/`, with `safe to merge (11)` and `needs a decision (24)`. The 6 `full rewrite` rows carry the `⚠ live rule fully superseded` flag. Nothing is written — confirm with `ls /Users/xyrlan/mnemo/shared/_inbox/*/*.proposed.md | wc -l` → still 35.

- [ ] **Step 3: Add the CHANGELOG entry**

Under `## [Unreleased]`, add an `### Added` section above the existing `### Changed`:

```markdown
### Added

- **`mnemo rewrites` accepts the `_inbox` backlog, and accepting now sticks.**
  The extractor stages a rewrite of a hand-edited rule as a `.proposed.md`
  sibling, and promotion was a manual `mv` that never advanced
  `written_hash` — so every later run compared the live file against a stale
  hash, concluded "user edited", and re-proposed the same rewrite over the
  unread draft. All 35 staged rewrites on the real vault had drifted this
  way, some since May. Meanwhile `shared/_inbox/` is excluded from every
  consumer surface, so recall served the un-updated rule: one said
  `MARKETPLACE_ENABLED = false` when it had been `true` since 24/08, another
  reported a Stripe webhook gap closed on 2026-08-11 as still open.

  The new command classifies each rewrite by what it does to the live body —
  insert-only (11 of 35), mixed (18), or a full rewrite of a superseded rule
  (6) — merges the insert-only set with `--apply-safe`, and reconciles
  `written_hash` so an accepted rule stops re-proposing. Frontmatter is
  merged per key rather than taken wholesale: `sources[]` is normalized and
  unioned (proposal-wins dropped a source in 3 of 11 cases), `description`
  comes from the proposal (live ones were factually stale), and a staged
  page's `needs-review` marker is never copied onto a reviewed rule. Every
  apply archives pristine originals to `shared/_archive/rewrites-<run_id>/`
  with `mnemo rewrites --undo <run_id>`, because the vault is not a git
  repository.
```

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs(changelog): mnemo rewrites and written_hash reconciliation (#159)"
```

- [ ] **Step 5: File the follow-up issue for the vault-wide drift**

The slug-stamp migration (`core/migrations/slugs.py:112`) rewrites every page with
`atomic_write_bytes` and never advances `written_hash`. On the real vault that left
1,638 state entries drifted (`.mnemo/slugs-stamped.v1`, 2026-09-02 20:23; 1234/1234
touched pages carry `slug:`). It is inert today only because those rules' sources are
no longer scanned, so they never reach a staging branch.

Run:

```bash
gh issue create \
  --title "Bulk migrations rewrite rule files without reconciling written_hash" \
  --body "$(cat <<'BODY'
`core/migrations/slugs.py:112` stamps a `slug:` into every live page via
`atomic_write_bytes` and never touches `entry.written_hash`. On the real vault
that left 1,638 of 1,762 state entries hash-drifted (marker
`.mnemo/slugs-stamped.v1` dated 2026-09-02 20:23; 1234 live pages share that
mtime and 1234/1234 carry `slug:`).

Consequence: every one of those rules reads as "user edited" to
`extract/inbox/branches/*`, which is the condition that stages a `.proposed.md`.
It is inert *today* only because those rules' sources are no longer scanned —
`scanner.scan()` walks source files, and a rule whose memory file is gone never
becomes dirty. Any future run that re-scans one of those sources will stage a
rewrite for a rule nobody edited.

Fix belongs at the migration site: a bulk rewriter that owns the new bytes should
advance `written_hash` to `content_hash(new_text)` for the entry it just rewrote,
the way `reclassify_apply.py:247` already does.

Found while implementing #159, which fixes the same class of bug at the accept
path. Out of scope there on purpose.
BODY
)"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| `core/rewrites/classify.py` + `Rewrite` | 1, 2, 3 |
| `core/rewrites/merge.py`, both entry points | 4, 5 |
| Frontmatter merge rules table (all 6 rows) | 4 (impl), 5 (tests) |
| `core/rewrites/apply.py` — archive, manifest, ledger, undo | 6, 7 |
| Manifest-existence guard | 7 |
| `cli/commands/rewrites.py` + `ADVANCED_COMMANDS` | 8 |
| Testing section (all 15 listed cases) | 2, 3, 5, 7 |
| Out-of-scope: vault-wide drift → separate issue | 9 |

Spec test list vs plan: classify's 5 cases → Tasks 2–3 (6 tests, one extra for page types). Merge's 4 cases → Tasks 4–5 (7 tests). Apply's 5 cases → Tasks 6–7 (9 tests). Every spec case has a task.

**Placeholder scan:** no TBD/TODO. Every code step carries complete code. Every test step carries the assertion. Every run step names the command and the expected result.

**Type consistency:** `Rewrite(proposal, live, key, kind, keep_ratio, inserted_lines, dropped_lines)` — declared Task 1, constructed Task 2, read in Tasks 6–8. `ApplyPlan(run_id, entries)` and `ApplyReport(merged, replaced, archive_dir, notes, skipped)` — declared Task 1, used Tasks 6–8. `skipped` is the only representation of a skip; `len(report.skipped)` is the count, so the two cannot drift. `merge_insert_only` / `replace_wholesale` — declared Task 4, dispatched in Task 6's `_ACTION_FOR_KIND`. CLI flag `--apply-safe` → `args.apply_safe` (argparse dash-to-underscore) in both Task 8's parser block and its handler. `A.plan(vault, include=...)` / `A.apply(plan, vault)` / `A.undo(vault, run_id)` — same argument order at every call site.

One gap found and closed while reviewing: Task 7's `test_full_rewrite_uses_replace_and_is_counted_separately` seeds an empty `entries` dict, which exercises the `report.notes` branch for a missing state entry — a path the spec mentions but did not test.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-09-12-inbox-rewrites.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
