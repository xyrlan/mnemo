# Recall Fix — Name-Dedup + projects[] Inference Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover recall primacy@5 back toward the v0.7 baseline (75%) by eliminating cross-slug duplicate rule files and ensuring per-project attribution survives LLM-generated slug drift.

**Architecture:** The regression has two causes: (1) the rule-activation-index derives `projects[]` only from `sources[]` paths under `bots/<project>/...` — frontmatter `project:` is ignored — so cluster pages can already carry the right info but still end up with `projects=[]` if we later broaden source paths; (2) LLM-generated slugs diverge across runs for the same logical rule (`name:` identical), and the existing `dedupe_by_slug` keys on slug, so name-identical files accumulate in `shared/<type>/`. Fix (1) with a name-keyed consolidation CLI + guardrail; fix (2) with a name-keyed dedup at promotion time.

**Tech Stack:** Python 3.11+, pytest, PyYAML (already used for frontmatter), mnemo CLI framework at `src/mnemo/cli/`.

**Scope:** Plan covers Steps 1 (dedup CLI), 2 (projects[] inference hardening), 3 (promotion-time name dedup), 4 (doctor guardrail). Validation via `mnemo recall` after Steps 1+2. If primacy@5 does not recover, halt and re-diagnose before Steps 3+4.

**Out of scope:** BM25F weight tuning, reflex bilingual-gap fix, schema bumps. Filed in follow-ups if recall still misses after Steps 1+2.

---

## File Structure

**Created:**
- `src/mnemo/core/dedup_rules.py` — pure logic: group shared/*.md by `name:`, pick canonical, merge metadata, return plan
- `src/mnemo/cli/commands/dedup_rules.py` — CLI `mnemo dedup-rules` (dry-run default, `--apply`)
- `src/mnemo/cli/commands/doctor_checks/duplicate_names.py` — doctor check for >1 file sharing `name:`
- `tests/core/test_dedup_rules.py`
- `tests/cli/test_dedup_rules_cli.py`
- `tests/core/rule_activation/test_projects_from_frontmatter.py`
- `tests/core/extract/test_promote_name_dedup.py`
- `tests/cli/doctor_checks/test_duplicate_names.py`

**Modified:**
- `src/mnemo/core/rule_activation/index.py:54-65, 137-147` — extend `projects_for_rule(source_files, *, frontmatter=None)` to honor frontmatter `project:`/`projects:` when `sources[]` yields nothing; update the call site in `_build_rule_entry`
- `src/mnemo/core/extract/inbox/dedup.py` — add `dedupe_by_name(pages)` (reuses `normalize_name` from `mnemo.core.dedup_rules`), export from `inbox/__init__.py`
- `src/mnemo/core/extract/__init__.py:394` — chain `dedupe_by_name(dedupe_by_slug(...))` in the promotion pipeline
- `src/mnemo/cli/parser.py:77` — add `sub.add_parser("dedup-rules", ...)` with `--apply` flag; eager-import `mnemo.cli.commands.dedup_rules` so `@command` registers at boot
- `src/mnemo/cli/commands/doctor.py` — register the new `duplicate_names` check (mirror the v0.10 orphan-worktree-briefing check pattern)

---

## Task 1: `projects_for_rule` honors frontmatter `project:` fallback

**Files:**
- Modify: `src/mnemo/core/rule_activation/index.py:54-65, 105-200`
- Test: `tests/core/rule_activation/test_projects_from_frontmatter.py`

**Design note (W1):** Extend the existing `projects_for_rule` with an optional `frontmatter` kwarg instead of introducing a second resolver. Existing callers work unchanged (kwarg default is `None`), and the call site in `_build_rule_entry` already has `fm` in scope — no redundant dict wrapping.

- [ ] **Step 1: Write failing test for sources-based path (regression guard)**

Create `tests/core/rule_activation/test_projects_from_frontmatter.py`:

```python
"""projects_for_rule must derive projects from frontmatter when sources[] is empty
or non-bots; existing sources-path behavior must stay intact."""
from __future__ import annotations

from mnemo.core.rule_activation.index import projects_for_rule


def test_sources_under_bots_still_wins():
    assert projects_for_rule(["bots/mnemo/briefings/sessions/x.md"]) == ["mnemo"]


def test_existing_callers_without_frontmatter_kwarg_unchanged():
    # Regression guard: the positional-only legacy signature must keep working.
    assert projects_for_rule([]) == []
    assert projects_for_rule(["bots/mnemo/x.md"]) == ["mnemo"]


def test_frontmatter_project_used_when_sources_empty():
    assert projects_for_rule([], frontmatter={"project": "mnemo"}) == ["mnemo"]


def test_frontmatter_projects_list_used_when_sources_empty():
    assert projects_for_rule([], frontmatter={"projects": ["mnemo", "Meunu"]}) == ["Meunu", "mnemo"]


def test_frontmatter_ignored_when_sources_yield_bots_paths():
    assert projects_for_rule(["bots/mnemo/x.md"], frontmatter={"project": "wrong"}) == ["mnemo"]


def test_non_bots_sources_fall_back_to_frontmatter():
    assert projects_for_rule(["shared/feedback/x.md"], frontmatter={"project": "mnemo"}) == ["mnemo"]


def test_empty_everything_returns_empty():
    assert projects_for_rule([], frontmatter={}) == []
    assert projects_for_rule([], frontmatter=None) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/rule_activation/test_projects_from_frontmatter.py -v`
Expected: FAIL — frontmatter fallback tests fail because the current `projects_for_rule` rejects the `frontmatter` kwarg.

- [ ] **Step 3: Extend `projects_for_rule` with the frontmatter fallback**

In `src/mnemo/core/rule_activation/index.py`, replace the existing function:

```python
def projects_for_rule(
    source_files: list[str],
    *,
    frontmatter: dict | None = None,
) -> list[str]:
    """From a list of source_files, return sorted unique project names.

    Expects paths like ``bots/<project-name>/...``. Paths not under ``bots/<name>/``
    are ignored. When no bots/ paths are present and *frontmatter* supplies a
    ``project`` (str) or ``projects`` (list[str]) key, that is used as the
    fallback so LLM-generated cluster pages with explicit project attribution
    survive the index build even before they accumulate bots/ sources.
    """
    projects: set[str] = set()
    for sf in source_files:
        parts = Path(sf).parts
        if len(parts) >= 2 and parts[0] == "bots":
            projects.add(parts[1])
    if projects:
        return sorted(projects)
    fm = frontmatter or {}
    raw = fm.get("projects")
    if isinstance(raw, list):
        return sorted({p for p in raw if isinstance(p, str) and p})
    single = fm.get("project")
    if isinstance(single, str) and single:
        return [single]
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/rule_activation/test_projects_from_frontmatter.py -v`
Expected: 8 PASS.

- [ ] **Step 5: Wire the frontmatter kwarg into `_build_rule_entry`**

In `src/mnemo/core/rule_activation/index.py` around line 146, replace:

```python
    projects = projects_for_rule(source_files)
```

with:

```python
    projects = projects_for_rule(source_files, frontmatter=fm)
```

- [ ] **Step 6: Run full rule_activation tests + verify recall improves**

Run: `pytest tests/core/rule_activation/ -v`
Expected: all green (existing tests still pass because bots/ paths take precedence).

Run: `mnemo recall 2>&1 | head -5`
Expected: primacy@5 rate improves vs the pre-task baseline of 26.67% (exact target depends on how many misses map to projects=[] entries with a frontmatter project). Do not gate on a specific number here — just capture the before/after.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/rule_activation/index.py tests/core/rule_activation/test_projects_from_frontmatter.py
git commit -m "feat(rule-activation): infer projects[] from frontmatter when sources lack bots/ paths"
```

---

## Task 2: Name-keyed dedup CLI — pure logic

**Files:**
- Create: `src/mnemo/core/dedup_rules.py`
- Test: `tests/core/test_dedup_rules.py`

**Design notes:**
- **W3 (canonical selection):** canonical = `max(len(sources[]))`, ties broken by most recent `extracted_at`. Mirrors `dedupe_by_slug`'s "most-evidence" heuristic rather than pure recency, so a stale-recent entry with 1 source doesn't win over an older one with 3 sources.
- **W4 (project union):** union frontmatter `project`/`projects` values across the whole group (canonical + duplicates) before writing canonical — otherwise project attribution carried only by a losing duplicate is lost after merge.
- **W2 (byte-identity):** only the `sources:` and `projects:` frontmatter blocks are rewritten. Other keys (`name`, `description`, `extracted_at`, `stability`, `tags`, etc.) are preserved byte-for-byte in their original quoting style. Body is preserved byte-for-byte.

- [ ] **Step 1: Write failing tests**

Create `tests/core/test_dedup_rules.py`:

```python
"""Name-keyed rule dedup: plan a merge for shared/*.md files sharing a `name:`."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.dedup_rules import DedupPlan, plan_dedup


def _write(
    p: Path,
    name: str,
    *,
    sources: list[str],
    extracted_at: str,
    body: str = "body",
    frontmatter_project: str | None = None,
) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    sources_yaml = "\n".join(f"  - {s}" for s in sources) if sources else "[]"
    project_line = f"project: {frontmatter_project}\n" if frontmatter_project else ""
    p.write_text(
        f"---\nname: {name!r}\ndescription: 'd'\ntype: feedback\n"
        f"extracted_at: {extracted_at}\nstability: stable\n"
        f"{project_line}"
        f"sources:\n{sources_yaml if sources else ''}".replace("\n[]", " []") +
        (f"\ntags:\n  - git\n---\n{body}\n" if True else ""),
        encoding="utf-8",
    )
    # Fallback if the inline format got mangled — rewrite cleanly:
    lines = ["---", f"name: {name!r}", "description: 'd'", "type: feedback",
             f"extracted_at: {extracted_at}", "stability: stable"]
    if frontmatter_project:
        lines.append(f"project: {frontmatter_project}")
    if sources:
        lines.append("sources:")
        lines.extend(f"  - {s}" for s in sources)
    else:
        lines.append("sources: []")
    lines.extend(["tags:", "  - git", "---", body, ""])
    p.write_text("\n".join(lines), encoding="utf-8")


def test_no_duplicates_is_empty_plan(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "Rule A", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "Rule B", sources=["bots/y/b.md"], extracted_at="2026-04-19T11:00:00")
    plan = plan_dedup(tmp_path)
    assert plan.groups == []


def test_canonical_is_most_sources_then_recency(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    # b.md has the most sources (2) — must win even though c.md is more recent with 1 source.
    _write(shared / "a.md", "Dup", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "Dup", sources=["bots/x/b.md", "bots/x/b2.md"], extracted_at="2026-04-20T10:00:00")
    _write(shared / "c.md", "Dup", sources=["bots/y/c.md"], extracted_at="2026-04-21T10:00:00")
    plan = plan_dedup(tmp_path)
    assert len(plan.groups) == 1
    g = plan.groups[0]
    assert g.canonical.name == "b.md"
    assert sorted(p.name for p in g.duplicates) == ["a.md", "c.md"]
    assert sorted(g.merged_sources) == ["bots/x/a.md", "bots/x/b.md", "bots/x/b2.md", "bots/y/c.md"]
    assert sorted(g.merged_projects) == ["x", "y"]


def test_canonical_tiebreak_on_recency(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    # Equal source counts → newer extracted_at wins.
    _write(shared / "a.md", "Dup", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "Dup", sources=["bots/y/b.md"], extracted_at="2026-04-20T10:00:00")
    plan = plan_dedup(tmp_path)
    assert plan.groups[0].canonical.name == "b.md"


def test_name_match_is_case_and_whitespace_insensitive(tmp_path):
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "  Stacked PRs  ", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    _write(shared / "b.md", "stacked prs",     sources=["bots/y/b.md"], extracted_at="2026-04-19T11:00:00")
    plan = plan_dedup(tmp_path)
    assert len(plan.groups) == 1


def test_types_do_not_cross(tmp_path):
    (tmp_path / "shared" / "feedback").mkdir(parents=True)
    (tmp_path / "shared" / "project").mkdir(parents=True)
    _write(tmp_path / "shared" / "feedback" / "a.md", "X", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00")
    (tmp_path / "shared" / "project" / "a.md").write_text(
        "---\nname: 'X'\ndescription: 'd'\ntype: project\nextracted_at: 2026-04-19T10:00:00\n"
        "stability: stable\nsources:\n  - bots/y/p.md\ntags: []\n---\nbody\n",
        encoding="utf-8",
    )
    plan = plan_dedup(tmp_path)
    assert plan.groups == []


def test_apply_preserves_frontmatter_byte_identity_for_unchanged_keys(tmp_path):
    """W2: only sources:/projects: blocks may change; name, description,
    extracted_at and body must survive merge byte-for-byte."""
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "Dup", sources=["bots/x/a.md"], extracted_at="2026-04-19T10:00:00", body="old body")
    _write(shared / "b.md", "Dup", sources=["bots/y/b1.md", "bots/y/b2.md"], extracted_at="2026-04-20T10:00:00", body="newer body")

    canonical_before = (shared / "b.md").read_text(encoding="utf-8")
    plan_dedup(tmp_path).apply()
    canonical_after = (shared / "b.md").read_text(encoding="utf-8")

    assert "name: 'Dup'" in canonical_after                          # quoting preserved
    assert "description: 'd'" in canonical_after
    assert "extracted_at: 2026-04-20T10:00:00" in canonical_after
    assert "newer body" in canonical_after                           # body preserved
    assert "bots/x/a.md" in canonical_after                          # merged in
    assert "bots/y/b1.md" in canonical_after and "bots/y/b2.md" in canonical_after
    assert not (shared / "a.md").exists()


def test_apply_unions_frontmatter_project_across_group(tmp_path):
    """W4: if only a duplicate carries frontmatter project:, the merged
    canonical must inherit it (else Task 1's benefit is lost on merge)."""
    shared = tmp_path / "shared" / "feedback"
    _write(shared / "a.md", "Dup", sources=[], extracted_at="2026-04-19T10:00:00",
           frontmatter_project="mnemo")
    _write(shared / "b.md", "Dup", sources=[], extracted_at="2026-04-20T10:00:00")  # canonical (newer, equal sources)

    plan_dedup(tmp_path).apply()
    canonical = (shared / "b.md").read_text(encoding="utf-8")
    assert "project: mnemo" in canonical or "projects:" in canonical
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/core/test_dedup_rules.py -v`
Expected: FAIL — `mnemo.core.dedup_rules` does not exist.

- [ ] **Step 3: Implement the module**

Create `src/mnemo/core/dedup_rules.py`:

```python
"""Name-keyed dedup for already-promoted shared/<type>/*.md rule files.

LLM-generated slugs drift across extraction runs for the same logical rule,
so files with identical ``name:`` accumulate in ``shared/<type>/``. This
module plans a merge: pick canonical by ``max(len(sources[]))`` (ties → newer
``extracted_at``), union ``sources[]`` + frontmatter project attribution,
recompute ``projects[]``, delete the rest. Only the ``sources:`` and
``projects:`` frontmatter blocks are rewritten — every other key and the
body are preserved byte-for-byte (W2).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mnemo.core.extract.scanner import parse_frontmatter
from mnemo.core.rule_activation.index import projects_for_rule


def normalize_name(raw: str) -> str:
    """Case + whitespace normalization used by plan_dedup and the doctor check."""
    return " ".join(raw.strip().lower().split())


@dataclass
class DedupGroup:
    canonical: Path
    duplicates: list[Path]
    merged_sources: list[str] = field(default_factory=list)
    merged_projects: list[str] = field(default_factory=list)
    merged_fm_projects: list[str] = field(default_factory=list)  # W4: unioned fm project(s)


@dataclass
class DedupPlan:
    vault_root: Path
    groups: list[DedupGroup]

    def apply(self) -> None:
        for g in self.groups:
            _merge_group_inplace(g)


def _fm_projects(fm: dict) -> list[str]:
    raw = fm.get("projects")
    if isinstance(raw, list):
        return [p for p in raw if isinstance(p, str) and p]
    single = fm.get("project")
    if isinstance(single, str) and single:
        return [single]
    return []


def plan_dedup(vault_root: Path) -> DedupPlan:
    shared = vault_root / "shared"
    if not shared.is_dir():
        return DedupPlan(vault_root=vault_root, groups=[])

    buckets: dict[tuple[str, str], list[tuple[Path, dict]]] = {}
    for type_dir in sorted(p for p in shared.iterdir() if p.is_dir()):
        for md in sorted(type_dir.glob("*.md")):
            try:
                fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = fm.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = (type_dir.name, normalize_name(name))
            buckets.setdefault(key, []).append((md, fm))

    groups: list[DedupGroup] = []
    for entries in buckets.values():
        if len(entries) < 2:
            continue
        # W3: canonical = most sources; tie-break = newer extracted_at.
        entries.sort(
            key=lambda e: (
                len(e[1].get("sources") or []),
                str(e[1].get("extracted_at") or ""),
            ),
            reverse=True,
        )
        canonical_path, _ = entries[0]
        duplicate_paths = [p for p, _ in entries[1:]]

        merged_sources: list[str] = []
        merged_fm_projects: list[str] = []
        for _p, fm in entries:
            for s in (fm.get("sources") or []):
                if isinstance(s, str) and s not in merged_sources:
                    merged_sources.append(s)
            for proj in _fm_projects(fm):
                if proj not in merged_fm_projects:
                    merged_fm_projects.append(proj)

        merged_projects = projects_for_rule(
            merged_sources,
            frontmatter={"projects": merged_fm_projects} if merged_fm_projects else None,
        )
        groups.append(DedupGroup(
            canonical=canonical_path,
            duplicates=duplicate_paths,
            merged_sources=merged_sources,
            merged_projects=merged_projects,
            merged_fm_projects=merged_fm_projects,
        ))
    return DedupPlan(vault_root=vault_root, groups=groups)


# ---------------------------------------------------------------------------
# W2: surgical frontmatter rewrite — replace only sources:/projects: blocks,
# leave every other line untouched so diffs stay quiet and quoting survives.
# ---------------------------------------------------------------------------

_FM_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


def _rewrite_block(fm_text: str, key: str, lines: list[str]) -> str:
    """Replace an existing `key:` block (`key: [...]` or `key:\n  - a\n  - b`)
    with the given rendered lines. If the key is absent, append at the end
    of the frontmatter. The new block is always emitted in list form."""
    block_re = re.compile(
        rf"(?ms)^{re.escape(key)}:[ \t]*(\[\])?\s*(?:\n(?:[ \t]+-.*(?:\n|$))+)?",
    )
    new_block = f"{key}: []" if not lines else f"{key}:\n" + "\n".join(f"  - {v}" for v in lines)
    if block_re.search(fm_text):
        return block_re.sub(lambda _m: new_block + "\n", fm_text, count=1).rstrip() + "\n"
    # append — ensure single trailing newline
    return fm_text.rstrip() + "\n" + new_block + "\n"


def _merge_group_inplace(g: DedupGroup) -> None:
    text = g.canonical.read_text(encoding="utf-8")
    m = _FM_RE.match(text)
    if not m:
        return  # canonical has no frontmatter; skip rather than corrupt
    fm_text, rest = m.group(1), m.group(2)

    fm_text = _rewrite_block(fm_text, "sources", g.merged_sources)
    if g.merged_fm_projects:
        fm_text = _rewrite_block(fm_text, "projects", g.merged_fm_projects)

    new_text = "---\n" + fm_text.rstrip() + "\n---\n" + rest
    g.canonical.write_text(new_text, encoding="utf-8")
    for d in g.duplicates:
        d.unlink()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/core/test_dedup_rules.py -v`
Expected: 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/dedup_rules.py tests/core/test_dedup_rules.py
git commit -m "feat(dedup): plan_dedup groups shared rules by normalized name"
```

---

## Task 3: `mnemo dedup-rules` CLI (dry-run default)

**Files:**
- Create: `src/mnemo/cli/commands/dedup_rules.py`
- Test: `tests/cli/test_dedup_rules_cli.py`

- [ ] **Step 1: Write failing CLI test**

Create `tests/cli/test_dedup_rules_cli.py`:

```python
"""`mnemo dedup-rules` — dry-run default, prints plan; `--apply` writes."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo import cli as cli_mod
from mnemo.cli.commands import dedup_rules as cmd_mod  # noqa: F401 (register)


def _seed(vault: Path) -> None:
    d = vault / "shared" / "feedback"
    d.mkdir(parents=True)
    for slug, ts in [("a", "2026-04-19T10:00:00"), ("b", "2026-04-20T10:00:00")]:
        (d / f"{slug}.md").write_text(
            f"---\nname: 'Dup'\ndescription: 'd'\ntype: feedback\n"
            f"extracted_at: {ts}\nstability: stable\n"
            f"sources:\n  - bots/x/{slug}.md\ntags: []\n---\nbody\n",
            encoding="utf-8",
        )


def test_dry_run_reports_without_touching(tmp_path, capsys, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(cli_mod, "_resolve_vault", lambda: tmp_path)
    import argparse
    rc = cmd_mod.cmd_dedup_rules(argparse.Namespace(apply=False))
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 group" in out and "Dup" in out and "a.md" in out
    assert (tmp_path / "shared" / "feedback" / "a.md").exists()


def test_apply_merges(tmp_path, monkeypatch):
    _seed(tmp_path)
    monkeypatch.setattr(cli_mod, "_resolve_vault", lambda: tmp_path)
    import argparse
    rc = cmd_mod.cmd_dedup_rules(argparse.Namespace(apply=True))
    assert rc == 0
    assert not (tmp_path / "shared" / "feedback" / "a.md").exists()
    assert (tmp_path / "shared" / "feedback" / "b.md").exists()


def test_no_duplicates_is_clean_exit(tmp_path, capsys, monkeypatch):
    (tmp_path / "shared" / "feedback").mkdir(parents=True)
    monkeypatch.setattr(cli_mod, "_resolve_vault", lambda: tmp_path)
    import argparse
    rc = cmd_mod.cmd_dedup_rules(argparse.Namespace(apply=False))
    assert rc == 0
    assert "no duplicates" in capsys.readouterr().out.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/test_dedup_rules_cli.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the CLI**

**C1 fix:** the `command` decorator in `src/mnemo/cli/parser.py:18` is `def command(name: str)` — single positional arg. Do NOT pass `help=`. **C2 fix:** subparser args are registered inline in `_build_parser()` in parser.py, not via any per-module `register_args`. **S2:** no `register_args` function exists in this plan — parser.py is the only place subparsers get their flags.

Create `src/mnemo/cli/commands/dedup_rules.py`:

```python
"""`mnemo dedup-rules` — consolidate shared/*.md files sharing the same `name:`.

Dry-run by default (like `migrate-worktree-briefings`). Use `--apply` to
execute the plan: canonical = most sources[] (tie → newer extracted_at);
duplicates deleted; sources[] + frontmatter project(s) unioned on canonical.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("dedup-rules")
def cmd_dedup_rules(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core.dedup_rules import plan_dedup
    from mnemo.core.extract.scanner import parse_frontmatter

    vault = cli._resolve_vault()
    plan = plan_dedup(vault)

    if not plan.groups:
        print("no duplicates found")
        return 0

    print(f"{len(plan.groups)} group(s) with duplicate names:\n")
    for g in plan.groups:
        canon_fm, _ = parse_frontmatter(g.canonical.read_text(encoding="utf-8"))
        name = canon_fm.get("name", "")
        canon_rel = g.canonical.relative_to(vault)
        dup_rels = ", ".join(p.name for p in g.duplicates)
        print(f"  '{name}'")
        print(f"    keep:   {canon_rel}")
        print(f"    delete: {dup_rels}")
        print(f"    merged_sources: {len(g.merged_sources)}  projects: {g.merged_projects}")

    if not getattr(args, "apply", False):
        print("\n(dry-run — pass --apply to execute)")
        return 0

    plan.apply()
    print("\napplied.")
    return 0
```

- [ ] **Step 3b: Register the subparser in `parser.py` (C2)**

Edit `src/mnemo/cli/parser.py` — after the `migrate.add_argument("--dry-run", ...)` block (line 77) and before `sub.add_parser("help", ...)` (line 78), insert:

```python
    dedup = sub.add_parser(
        "dedup-rules",
        help="merge shared rule files that share the same name (dry-run default)",
    )
    dedup.add_argument(
        "--apply", action="store_true",
        help="execute the plan (default: dry-run)",
    )
```

Also add the import so the `@command` decorator fires at CLI boot. Find the other `from mnemo.cli.commands import ...` block in parser.py (or wherever command modules are eagerly imported) and append `dedup_rules`.

- [ ] **Step 3c: Verify the help string advertises the command**

Run: `mnemo --help 2>&1 | grep dedup-rules`
Expected: one line listing the command under positional arguments.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/cli/test_dedup_rules_cli.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Smoke-test against the live vault (dry-run)**

Run: `mnemo dedup-rules`
Expected: lists ≥2 groups including "Stacked PRs don't auto-retarget when base merges" (3 files) and "Parallel agents need worktree isolation" (2 files). No files touched.

- [ ] **Step 6: Apply against the live vault**

Run: `mnemo dedup-rules --apply`
Then: `mnemo doctor 2>&1 | grep -E 'recall|orphan|source'`
Expected: the 4 "source path does not resolve" warnings drop (merged canonical keeps surviving source path); duplicate rules collapse.

Run: `mnemo recall 2>&1 | tail -5`
Expected: primacy@5 recovers toward 50%+ (exact target depends on Task 1 effects).

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/commands/dedup_rules.py tests/cli/test_dedup_rules_cli.py
git commit -m "feat(cli): mnemo dedup-rules consolidates shared rules sharing a name"
```

---

## CHECKPOINT — Validate recall

- [ ] **Compare recall before/after Tasks 1+2+3**

Capture pre-task baseline from `.mnemo/recall-report.json` at plan-start time:
- primacy@5 = 26.67% / MRR 0.1311 over 15 cases
- The 5 diagnosed `projects=[]`-attributable misses (from the recall-report rank inspection run before writing this plan): `Prefer auto-population over manual curation in mnemo`, `Shared filter specification as single source of truth`, `Test helpers must differentiate name, slug, and filename`, `Stacked PRs don't auto-retarget when base merges`, `Schema bumps that change public IDs must migrate dependent telemetry`.

After Task 3 `--apply`:

Run: `mnemo recall 2>&1 | tail -20`

**Gate (S3):** at least **3 of those 5 specific misses** must now hit @10 (prefer @5) in the new `recall-report.json`. That is the diagnosis-driven gate — a generic "primacy@5 ≥ 50%" would let unrelated improvements mask the real failure mode or, conversely, fail the checkpoint if a different slice of cases shifts.

**If gate met:** proceed to Task 4 (prevention) + Task 5 (guardrail).
**If gate not met:** halt. The remaining misses are not explained by duplicate files + projects[] attribution. Re-diagnose by inspecting `.mnemo/recall-report.json` rank vs. result_count for each of the 5 and decide whether to tune BM25F field weights or revisit reflex bilingual aliases. Do NOT ship Tasks 4/5 speculatively.

---

## Task 4: Dedup by name at promotion time (prevention)

**Files:**
- Modify: `src/mnemo/core/extract/inbox/dedup.py:19-60`
- Test: `tests/core/extract/test_promote_name_dedup.py`

**C3 fix:** `ExtractedPage` (`src/mnemo/core/extract/inbox/types.py:18-33`) has required field `source_hash: str`. All `ExtractedPage(...)` fixture calls below include it — otherwise construction raises `TypeError` before any test assertion.

- [ ] **Step 1: Write failing test**

Create `tests/core/extract/test_promote_name_dedup.py`:

```python
"""ExtractedPage pages sharing the same `name` must merge even if slugs differ."""
from __future__ import annotations

import dataclasses

from mnemo.core.extract.inbox.dedup import dedupe_by_slug, dedupe_by_name
from mnemo.core.extract.inbox.types import ExtractedPage


def _page(slug: str, name: str, sources: list[str], *, type_: str = "feedback") -> ExtractedPage:
    return ExtractedPage(
        slug=slug,
        type=type_,
        name=name,
        description="d",
        body="body",
        source_files=sources,
        source_hash="h",          # C3: required field
        tags=["git"],
    )


def test_same_name_different_slug_merges():
    pages = [
        _page("stacked-prs-dont-auto-retarget-when-base-merges", "Stacked PRs don't auto-retarget when base merges", ["bots/mnemo/a.md"]),
        _page("stacked-pr-base-retarget-on-merge",               "Stacked PRs don't auto-retarget when base merges", ["bots/mnemo/b.md"]),
    ]
    merged = dedupe_by_name(pages)
    assert len(merged) == 1
    assert sorted(merged[0].source_files) == ["bots/mnemo/a.md", "bots/mnemo/b.md"]


def test_different_names_preserved():
    pages = [
        _page("a", "Rule A", ["bots/x/a.md"]),
        _page("b", "Rule B", ["bots/x/b.md"]),
    ]
    assert len(dedupe_by_name(pages)) == 2


def test_name_match_normalized():
    pages = [
        _page("a", "  Stacked PRs  ", ["bots/x/a.md"]),
        _page("b", "stacked prs",     ["bots/x/b.md"]),
    ]
    assert len(dedupe_by_name(pages)) == 1


def test_types_do_not_cross():
    p1 = _page("a", "Same", ["bots/x/a.md"], type_="feedback")
    p2 = _page("b", "Same", ["bots/y/b.md"], type_="project")
    assert len(dedupe_by_name([p1, p2])) == 2


def test_idempotent_when_slug_and_name_both_unique():
    """W5: pipeline composes dedupe_by_slug → dedupe_by_name. When all slugs
    differ and names are also unique the second pass is a no-op."""
    pages = [
        _page("a", "Rule A", ["bots/x/a.md"]),
        _page("b", "Rule B", ["bots/y/b.md"]),
    ]
    once  = dedupe_by_slug(pages)
    twice = dedupe_by_name(once)
    assert len(twice) == len(once) == 2
    assert {p.slug for p in twice} == {"a", "b"}


def test_pipeline_merges_slug_group_then_name_group():
    """W5 composition: two slug-identical pages (merged first) plus a third
    with a different slug but same name — final result is 1 page with all 3
    sources unioned."""
    pages = [
        _page("dup-slug", "Stacked PRs", ["bots/x/a.md"]),
        _page("dup-slug", "Stacked PRs", ["bots/x/b.md"]),            # same slug
        _page("other-slug", "Stacked PRs", ["bots/y/c.md"]),          # diff slug, same name
    ]
    merged = dedupe_by_name(dedupe_by_slug(pages))
    assert len(merged) == 1
    assert sorted(merged[0].source_files) == ["bots/x/a.md", "bots/x/b.md", "bots/y/c.md"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/extract/test_promote_name_dedup.py -v`
Expected: FAIL — `dedupe_by_name` not defined.

- [ ] **Step 3: Implement `dedupe_by_name`**

In `src/mnemo/core/extract/inbox/dedup.py`, add after `dedupe_by_slug`. Reuse `normalize_name` from `mnemo.core.dedup_rules` rather than defining a second copy:

```python
from mnemo.core.dedup_rules import normalize_name


def dedupe_by_name(pages: list[ExtractedPage]) -> list[ExtractedPage]:
    """Merge pages with identical normalized ``name`` within the same ``type``.

    Canonical is the page with the most source_files; ties broken by original
    order. source_files, tags unioned (same logic as :func:`dedupe_by_slug`).
    Runs AFTER dedupe_by_slug so slug-identical groups are already merged.
    """
    groups: dict[tuple[str, str], list[ExtractedPage]] = {}
    for p in pages:
        key = (p.type, normalize_name(p.name))
        groups.setdefault(key, []).append(p)

    merged: list[ExtractedPage] = []
    for items in groups.values():
        if len(items) == 1:
            merged.append(items[0])
            continue
        chosen = max(items, key=lambda p: len(p.source_files))
        all_sources: list[str] = []
        for p in items:
            for sf in p.source_files:
                if sf not in all_sources:
                    all_sources.append(sf)
        all_tags: list[str] = []
        for p in [chosen] + [x for x in items if x is not chosen]:
            for t in getattr(p, "tags", None) or []:
                if t not in all_tags:
                    all_tags.append(t)
        import dataclasses
        merged.append(dataclasses.replace(chosen, source_files=all_sources, tags=all_tags))
    return merged
```

- [ ] **Step 4: Wire into the promotion pipeline**

Find the call site of `dedupe_by_slug` in `src/mnemo/core/extract/__init__.py` (around line 394). Replace:

```python
            deduped = inbox.dedupe_by_slug(all_pages)
```

with:

```python
            deduped = inbox.dedupe_by_name(inbox.dedupe_by_slug(all_pages))
```

Export `dedupe_by_name` from `src/mnemo/core/extract/inbox/__init__.py` (add to the import block and `__all__`).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/core/extract/ -v`
Expected: all green including new tests.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/extract/inbox/dedup.py src/mnemo/core/extract/inbox/__init__.py src/mnemo/core/extract/__init__.py tests/core/extract/test_promote_name_dedup.py
git commit -m "feat(extract): dedupe_by_name prevents slug-drift duplicates at promotion"
```

---

## Task 5: Doctor guardrail — warn on duplicate `name:` in shared/

**Files:**
- Create: `src/mnemo/cli/commands/doctor_checks/duplicate_names.py`
- Modify: `src/mnemo/cli/commands/doctor.py` (register the check)
- Test: `tests/cli/doctor_checks/test_duplicate_names.py`

- [ ] **Step 1: Write failing test**

Create `tests/cli/doctor_checks/test_duplicate_names.py`:

```python
"""Doctor check: warn when >1 file in shared/<type>/ shares a normalized name."""
from __future__ import annotations

from pathlib import Path

from mnemo.cli.commands.doctor_checks.duplicate_names import check_duplicate_names


def _write(p: Path, name: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"---\nname: {name!r}\ndescription: 'd'\ntype: feedback\n"
        f"extracted_at: 2026-04-22T10:00:00\nstability: stable\nsources: []\ntags: []\n---\nbody\n",
        encoding="utf-8",
    )


def test_no_warnings_when_unique(tmp_path):
    _write(tmp_path / "shared" / "feedback" / "a.md", "A")
    _write(tmp_path / "shared" / "feedback" / "b.md", "B")
    assert check_duplicate_names(tmp_path) == []


def test_warns_on_duplicate_name(tmp_path):
    _write(tmp_path / "shared" / "feedback" / "a.md", "Dup")
    _write(tmp_path / "shared" / "feedback" / "b.md", "Dup")
    warns = check_duplicate_names(tmp_path)
    assert len(warns) == 1
    assert "Dup" in warns[0] and "a.md" in warns[0] and "b.md" in warns[0]
    assert "mnemo dedup-rules" in warns[0]


def test_types_do_not_cross(tmp_path):
    _write(tmp_path / "shared" / "feedback" / "a.md", "Dup")
    _write(tmp_path / "shared" / "project" / "b.md", "Dup")
    assert check_duplicate_names(tmp_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/cli/doctor_checks/test_duplicate_names.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the check**

Create `src/mnemo/cli/commands/doctor_checks/duplicate_names.py`. Reuses `normalize_name` from `mnemo.core.dedup_rules` (DRY with Tasks 2 and 4). **S1:** keep the original name alongside the path when bucketing so we never re-read the file to recover it.

```python
"""Doctor check: duplicate `name:` across files in shared/<type>/."""
from __future__ import annotations

from pathlib import Path

from mnemo.core.dedup_rules import normalize_name
from mnemo.core.extract.scanner import parse_frontmatter


def check_duplicate_names(vault_root: Path) -> list[str]:
    """Return one warning per name that appears in >1 file within the same type dir."""
    shared = vault_root / "shared"
    if not shared.is_dir():
        return []

    # bucket → list[(path, original_name)]
    groups: dict[tuple[str, str], list[tuple[Path, str]]] = {}
    for type_dir in sorted(p for p in shared.iterdir() if p.is_dir()):
        for md in sorted(type_dir.glob("*.md")):
            try:
                fm, _ = parse_frontmatter(md.read_text(encoding="utf-8"))
            except Exception:
                continue
            name = fm.get("name")
            if isinstance(name, str) and name.strip():
                groups.setdefault((type_dir.name, normalize_name(name)), []).append((md, name))

    warns: list[str] = []
    for (type_name, _norm), entries in sorted(groups.items()):
        if len(entries) < 2:
            continue
        original = entries[0][1]
        files = ", ".join(p.name for p, _ in entries)
        warns.append(
            f"Rule name {original!r} is used by {len(entries)} files in shared/{type_name}/: "
            f"{files} — run `mnemo dedup-rules` to consolidate."
        )
    return warns
```

- [ ] **Step 4: Register the check in `doctor.py`**

Read `src/mnemo/cli/commands/doctor.py` to find how other checks are registered (e.g. the orphan-worktree-briefing check from the v0.10 ship). Mirror that pattern: import `check_duplicate_names`, call it with the vault root, print each warning with the standard `  ⚠` prefix.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/cli/doctor_checks/test_duplicate_names.py tests/cli/ -k doctor -v`
Expected: all green.

- [ ] **Step 6: Smoke-test**

Run: `mnemo doctor 2>&1 | grep -i 'rule name'`
Expected: after Task 3 `--apply` ran, zero warnings. If Task 3 was not yet applied live, the expected 2+ warnings appear.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/commands/doctor_checks/duplicate_names.py src/mnemo/cli/commands/doctor.py tests/cli/doctor_checks/test_duplicate_names.py
git commit -m "feat(doctor): warn on duplicate rule name: across shared/<type>/"
```

---

## Final validation

- [ ] **Run full test suite**

Run: `pytest -q`
Expected: all tests pass; new count = baseline (1064) + new tests.

- [ ] **Re-measure recall on the live vault**

Run: `mnemo recall 2>&1 | tail -10`
Expected: primacy@5 substantially recovered vs. 26.67% baseline. Capture the new number for the PR description.

- [ ] **Open PR**

Title: `fix(recall): consolidate duplicate rules + infer projects[] from frontmatter`
Body must include: before/after `mnemo recall` numbers, list of merged groups from `mnemo dedup-rules --apply` output, doctor warnings delta.
