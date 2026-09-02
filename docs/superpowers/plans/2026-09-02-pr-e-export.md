# PR E — `mnemo export` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `mnemo export` writes the current project's learned rules into a managed rules file that Claude Code, Cursor or Codex loads on its own, with a manifest so `mnemo status` can say when the file is stale and the reflex never injects a rule the file already carries.

**Architecture:** A new `mnemo.core.export` package with four focused modules — `select` (which rules), `render` (one host-independent markdown block), `writers` (whole-file vs managed-block targets), `manifest` (what was exported, per project, under the vault's `.mnemo/`) — plus a thin orchestrator and a CLI command. Two small touches outside the package: the `UserPromptSubmit` hook subtracts exported slugs from its candidates and records them, and `mnemo why` / `mnemo status` show that.

**Tech Stack:** Python 3.8+ (no `tomllib`, no new deps), pytest, existing helpers: `filters.iter_shared_pages` / `is_consumer_visible` / `derive_rule_slug` / `parse_frontmatter`, `reclassify_types.split_frontmatter`, `text_utils.strip_graph_section`, `rule_activation.projects_for_rule` / `is_universal`, `atomic.atomic_write_bytes`, `agent.resolve_canonical_agent`.

**Spec:** `docs/superpowers/specs/2026-09-02-distribution-design.md` § 1.

**Process rules (from the corrections-layer work):** one writer subagent at a time; TDD per task; run `git status` before dispatching; reviewers measure on the real vault (`~/mnemo`) for Task 8, never on fixtures alone. Full suite: `python3 -m pytest tests/unit -q` (2000+ tests, ~2 min). Use `/usr/local/bin/python3 -m mnemo …` to run the dev CLI; the `mnemo` on PATH is stale.

---

## File structure

| Path | Responsibility |
|------|----------------|
| `src/mnemo/core/export/__init__.py` | `run_export()` orchestrator + `ExportReport` |
| `src/mnemo/core/export/select.py` | `ExportRule` dataclass, `select_rules()` — which pages qualify, in what order |
| `src/mnemo/core/export/render.py` | markers, `render_entry()`, `render_block()`, `entry_hash()`, `estimated_tokens()` |
| `src/mnemo/core/export/writers.py` | `Target`, `target_for()`, pure `replace_block()` / `strip_block()`, `write_target()`, `remove_target()` |
| `src/mnemo/core/export/manifest.py` | manifest path/read/write/delete, `exported_slugs_for()`, `staleness()` |
| `src/mnemo/cli/commands/export.py` | `@command("export")` — argument surface and printing only |
| `src/mnemo/cli/parser.py` | `export` subparser |
| `src/mnemo/cli/commands/__init__.py` | import `export` so the decorator runs |
| `src/mnemo/hooks/user_prompt_submit.py` | subtract exported slugs; log `exported` |
| `src/mnemo/core/reflex/receipts.py` | print `exported` lines; explain `all_exported` |
| `src/mnemo/cli/commands/status.py` | `_print_export_status()` |
| `tests/unit/_export_fixtures.py` | `write_rule()` helper shared by the export tests |
| `tests/unit/test_export_select.py`, `test_export_render.py`, `test_export_writers.py`, `test_export_manifest.py`, `test_cli_export.py`, `test_reflex_export_skip.py`, `test_cli_status_export.py` | one test module per unit |
| `README.md`, `docs/getting-started.md`, `CHANGELOG.md` | docs |

---

### Task 1: Selection — `core/export/select.py`

**Files:**
- Create: `src/mnemo/core/export/__init__.py` (empty for now)
- Create: `src/mnemo/core/export/select.py`
- Create: `tests/unit/_export_fixtures.py`
- Test: `tests/unit/test_export_select.py`

- [ ] **Step 1: Write the shared fixture helper**

```python
# tests/unit/_export_fixtures.py
"""Write rule pages in the exact shape ``_render_page`` produces, for export tests."""
from __future__ import annotations

from pathlib import Path


def write_rule(
    vault: Path,
    *,
    page_type: str = "feedback",
    slug: str,
    name: str | None = None,
    projects: tuple[str, ...] = ("app",),
    body: str = "Do the thing.\n",
    quote: str | None = None,
    stability: str = "stable",
    inbox: bool = False,
    graph_section: bool = True,
) -> Path:
    """One page under ``shared/<type>/`` (or ``shared/_inbox/<type>/``).

    ``projects`` becomes one ``sources:`` briefing per project, which is how
    ``projects_for_rule`` attributes a page; two projects make it universal at
    the default threshold of 2.
    """
    where = vault / "shared" / ("_inbox" if inbox else "") / page_type
    where.mkdir(parents=True, exist_ok=True)
    sources = "\n".join(
        f"  - bots/{p}/briefings/sessions/{slug}-{i}.md" for i, p in enumerate(projects)
    )
    lines = [
        "---",
        f"name: '{name or slug.replace('-', ' ').capitalize()}'",
        f"slug: {slug}",
        f"description: 'about {slug}'",
        f"type: {page_type}",
        f"stability: {stability}",
        "sources:",
        sources,
        "tags:",
        "  - auto-promoted",
    ]
    if quote is not None:
        lines += ["evidence:", f"  quote: '{quote}'", "  source: 'briefing: x — user turns, turn 1'"]
    lines.append("---")
    text = "\n".join(lines) + "\n\n" + body
    if graph_section:
        text += "\n<!-- mnemo:graph-section -->\n## Sources\n- [[bots/app/briefings/sessions/x]]\n"
    path = where / f"{slug}.md"
    path.write_text(text, encoding="utf-8")
    return path
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_export_select.py
from __future__ import annotations

from pathlib import Path

from tests.unit._export_fixtures import write_rule


def test_selects_project_rules_and_universal_only(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="mine", projects=("app",))
    write_rule(tmp_vault, slug="theirs", projects=("other",))
    write_rule(tmp_vault, slug="everywhere", projects=("other", "third"))

    rules = select_rules(tmp_vault, project="app")
    assert [r.slug for r in rules] == ["everywhere", "mine"]
    assert rules[0].universal is True and rules[1].universal is False


def test_default_types_are_feedback_and_user(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="fb", page_type="feedback")
    write_rule(tmp_vault, slug="us", page_type="user")
    write_rule(tmp_vault, slug="ref", page_type="reference")

    assert {r.slug for r in select_rules(tmp_vault, project="app")} == {"fb", "us"}
    assert {r.slug for r in select_rules(tmp_vault, project="app", types=("reference",))} == {"ref"}


def test_inbox_archive_and_evolving_are_skipped(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="staged", inbox=True)
    write_rule(tmp_vault, slug="flux", stability="evolving")
    archived = tmp_vault / "shared" / "_archive" / "reclassify-x" / "originals" / "feedback"
    archived.mkdir(parents=True)
    (archived / "old.md").write_text("---\nname: old\nslug: old\ntype: feedback\nsources:\n  - bots/app/b.md\n---\nx\n")
    write_rule(tmp_vault, slug="live")

    assert [r.slug for r in select_rules(tmp_vault, project="app")] == ["live"]


def test_order_universal_then_source_count_then_slug(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="b-one", projects=("app",))
    write_rule(tmp_vault, slug="a-one", projects=("app",))
    write_rule(tmp_vault, slug="z-two", projects=("app", "app"))          # 2 sources, 1 project
    write_rule(tmp_vault, slug="uni", projects=("app", "other"))          # universal

    assert [r.slug for r in select_rules(tmp_vault, project="app")] == ["uni", "z-two", "a-one", "b-one"]


def test_limit_truncates_after_ordering(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(tmp_vault, slug="b", projects=("app",))
    write_rule(tmp_vault, slug="uni", projects=("app", "other"))
    write_rule(tmp_vault, slug="a", projects=("app",))

    assert [r.slug for r in select_rules(tmp_vault, project="app", limit=2)] == ["uni", "a"]


def test_rule_carries_name_body_quote_and_counts(tmp_vault: Path):
    from mnemo.core.export.select import select_rules

    write_rule(
        tmp_vault, slug="use-yarn-not-npm", name="Use yarn, never npm",
        body="Use yarn in this repo.\n\n**Why:** lockfile.\n",
        quote="never use npm in this repo, always yarn",
    )
    (rule,) = select_rules(tmp_vault, project="app")
    assert rule.name == "Use yarn, never npm"
    assert rule.body == "Use yarn in this repo.\n\n**Why:** lockfile.\n"      # graph section gone
    assert rule.quote == "never use npm in this repo, always yarn"
    assert rule.source_count == 1 and rule.page_type == "feedback"


def test_missing_vault_yields_nothing(tmp_path: Path):
    from mnemo.core.export.select import select_rules

    assert select_rules(tmp_path / "nope", project="app") == []
```

- [ ] **Step 3: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_export_select.py -q`
Expected: `ModuleNotFoundError: No module named 'mnemo.core.export'`

- [ ] **Step 4: Implement**

```python
# src/mnemo/core/export/__init__.py
"""``mnemo export`` — the project's learned rules as a file another tool loads.

See docs/superpowers/specs/2026-09-02-distribution-design.md § 1.
"""
```

```python
# src/mnemo/core/export/select.py
"""Which rule pages an export carries, and in what order.

The selection is the reflex's project scope — pages attributed to the
current project plus universal ones — restricted to the types a user can
act on (``feedback``, ``user`` by default). ``_inbox``, ``_archive`` and
``stability: evolving`` pages never qualify: the same visibility rule the
MCP tools apply (``filters.is_consumer_visible``).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from mnemo.core.filters import derive_rule_slug, is_consumer_visible, iter_shared_pages
from mnemo.core.reclassify_types import split_frontmatter
from mnemo.core.rule_activation import is_universal, projects_for_rule
from mnemo.core.text_utils import strip_graph_section

DEFAULT_TYPES: tuple[str, ...] = ("feedback", "user")


@dataclass(frozen=True)
class ExportRule:
    slug: str
    name: str
    body: str
    quote: Optional[str]
    universal: bool
    source_count: int
    page_type: str


def select_rules(
    vault_root: Path,
    *,
    project: str,
    types: Sequence[str] = DEFAULT_TYPES,
    universal_threshold: int = 2,
    limit: Optional[int] = None,
) -> list[ExportRule]:
    """Rules scoped to *project*, universal first, then most-sourced, then slug."""
    vault_root = Path(vault_root)
    shared = vault_root / "shared"
    wanted = set(types)
    out: list[ExportRule] = []
    for md in iter_shared_pages(vault_root, include_inbox=False):
        rel = md.relative_to(shared).parts
        if len(rel) != 2 or rel[0] not in wanted:
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = split_frontmatter(text)
        if not fm or not is_consumer_visible(md, fm, vault_root):
            continue
        sources = [s for s in (fm.get("sources") or []) if isinstance(s, str)]
        projects = projects_for_rule(sources, frontmatter=fm)
        universal = is_universal(projects, universal_threshold)
        if project not in projects and not universal:
            continue
        evidence = fm.get("evidence")
        quote = evidence.get("quote") if isinstance(evidence, dict) else None
        slug = derive_rule_slug(fm, md.stem)
        out.append(ExportRule(
            slug=slug,
            name=str(fm.get("name") or slug),
            body=strip_graph_section(body).strip() + "\n",
            quote=str(quote).strip() if quote else None,
            universal=universal,
            source_count=len(sources),
            page_type=rel[0],
        ))
    out.sort(key=lambda r: (not r.universal, -r.source_count, r.slug))
    if limit is not None:
        out = out[: max(0, int(limit))]
    return out
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_export_select.py -q`
Expected: `7 passed`

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/export tests/unit/_export_fixtures.py tests/unit/test_export_select.py
git commit -m "feat(export): select the project's exportable rules"
```

---

### Task 2: Rendering — `core/export/render.py`

**Files:**
- Create: `src/mnemo/core/export/render.py`
- Test: `tests/unit/test_export_render.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_export_render.py
from __future__ import annotations

from mnemo.core.export.select import ExportRule


def _rule(slug="use-yarn-not-npm", **kw):
    base = dict(
        slug=slug, name="Use yarn, never npm", body="Use yarn in this repo.\n",
        quote="never use npm in this repo, always yarn", universal=False,
        source_count=1, page_type="feedback",
    )
    base.update(kw)
    return ExportRule(**base)


def test_entry_has_heading_body_and_quote():
    from mnemo.core.export.render import render_entry

    text = render_entry(_rule())
    assert text.startswith("### Use yarn, never npm  `use-yarn-not-npm`\n")
    assert "Use yarn in this repo.\n" in text
    assert text.rstrip().endswith('> you said: "never use npm in this repo, always yarn"')


def test_entry_without_quote_has_no_you_said_line():
    from mnemo.core.export.render import render_entry

    assert "you said" not in render_entry(_rule(quote=None))


def test_universal_marker_after_slug():
    from mnemo.core.export.render import render_entry

    assert render_entry(_rule(universal=True)).startswith(
        "### Use yarn, never npm  `use-yarn-not-npm` (universal)\n"
    )


def test_block_wraps_entries_in_markers_with_counts():
    from mnemo.core.export.render import END_MARKER, START_MARKER, render_block

    block = render_block([_rule(universal=True), _rule(slug="two", universal=False)],
                         project="app", today="2026-09-02")
    lines = block.splitlines()
    assert lines[0].startswith(START_MARKER)
    assert "2026-09-02" in lines[0] and "edit the vault, not this block" in lines[0]
    assert lines[1] == "## Rules mnemo learned from you (project: app, 2 rules, 1 universal)"
    assert lines[-1] == END_MARKER
    assert block.count("### ") == 2


def test_entry_hash_is_stable_and_content_sensitive():
    from mnemo.core.export.render import entry_hash

    assert entry_hash(_rule()) == entry_hash(_rule())
    assert entry_hash(_rule()) != entry_hash(_rule(body="Other.\n"))
    assert len(entry_hash(_rule())) == 64


def test_estimated_tokens_is_chars_over_four():
    from mnemo.core.export.render import TOKEN_WARN, estimated_tokens

    assert estimated_tokens("a" * 400) == 100
    assert TOKEN_WARN == 4000
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_export_render.py -q`
Expected: `ImportError: cannot import name 'render_entry'`

- [ ] **Step 3: Implement**

```python
# src/mnemo/core/export/render.py
"""The exported block: one markdown shape for every host.

Everything host-specific (a Cursor frontmatter, where the block sits in a
CLAUDE.md) belongs to :mod:`writers`. This module only turns rules into
text and hashes that text so the manifest can tell when the vault moved on.
"""
from __future__ import annotations

import hashlib
from typing import Sequence

from mnemo.core.export.select import ExportRule

START_MARKER = "<!-- mnemo:start"
END_MARKER = "<!-- mnemo:end -->"
TOKEN_WARN = 4000


def render_entry(rule: ExportRule) -> str:
    head = f"### {rule.name}  `{rule.slug}`"
    if rule.universal:
        head += " (universal)"
    parts = [head, rule.body.rstrip()]
    if rule.quote:
        parts.append(f'> you said: "{rule.quote}"')
    return "\n".join(parts) + "\n"


def render_block(rules: Sequence[ExportRule], *, project: str, today: str) -> str:
    n_uni = sum(1 for r in rules if r.universal)
    header = (
        f"{START_MARKER} — generated by `mnemo export` on {today}; "
        "edit the vault, not this block -->"
    )
    title = (
        f"## Rules mnemo learned from you (project: {project}, "
        f"{len(rules)} rule{'s' if len(rules) != 1 else ''}, {n_uni} universal)"
    )
    body = "\n".join(render_entry(r) for r in rules)
    return "\n".join([header, title, "", body.rstrip(), END_MARKER]) + "\n"


def entry_hash(rule: ExportRule) -> str:
    return hashlib.sha256(render_entry(rule).encode("utf-8")).hexdigest()


def estimated_tokens(text: str) -> int:
    return len(text) // 4
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_export_render.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/export/render.py tests/unit/test_export_render.py
git commit -m "feat(export): render the managed rules block"
```

---

### Task 3: Writers — `core/export/writers.py`

**Files:**
- Create: `src/mnemo/core/export/writers.py`
- Test: `tests/unit/test_export_writers.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_export_writers.py
from __future__ import annotations

from pathlib import Path

import pytest

BLOCK = "<!-- mnemo:start — x -->\n## Rules\n\n### A  `a`\nbody\n<!-- mnemo:end -->\n"
BLOCK2 = "<!-- mnemo:start — y -->\n## Rules\n\n### B  `b`\nbody2\n<!-- mnemo:end -->\n"


def test_target_for_each_host(tmp_path: Path):
    from mnemo.core.export.writers import target_for

    assert target_for("claude", "auto", tmp_path).path == tmp_path / ".claude" / "rules" / "mnemo.md"
    assert target_for("claude", "auto", tmp_path).kind == "whole"
    t = target_for("claude", "claude-md", tmp_path)
    assert t.path == tmp_path / "CLAUDE.md" and t.kind == "block"
    c = target_for("cursor", "auto", tmp_path)
    assert c.path == tmp_path / ".cursor" / "rules" / "mnemo.mdc" and c.kind == "whole"
    assert c.prelude == "---\ndescription: Rules mnemo learned from you\nalwaysApply: true\n---\n"
    x = target_for("codex", "auto", tmp_path)
    assert x.path == tmp_path / "AGENTS.md" and x.kind == "block"


@pytest.mark.parametrize("host,target", [("cursor", "claude-md"), ("claude", "agents-md"), ("codex", "rules")])
def test_target_for_rejects_mismatched_pairs(tmp_path: Path, host, target):
    from mnemo.core.export.writers import TargetError, target_for

    with pytest.raises(TargetError):
        target_for(host, target, tmp_path)


def test_replace_block_appends_when_no_markers():
    from mnemo.core.export.writers import replace_block

    assert replace_block("# My project\n\nnotes\n", BLOCK) == "# My project\n\nnotes\n\n" + BLOCK
    assert replace_block("", BLOCK) == BLOCK


def test_replace_block_swaps_between_markers_and_keeps_the_rest():
    from mnemo.core.export.writers import replace_block

    text = "before\n\n" + BLOCK + "\nafter\n"
    assert replace_block(text, BLOCK2) == "before\n\n" + BLOCK2 + "\nafter\n"


def test_replace_block_refuses_a_single_marker():
    from mnemo.core.export.writers import MarkerError, replace_block

    with pytest.raises(MarkerError):
        replace_block("x\n<!-- mnemo:start — x -->\nno end\n", BLOCK)
    with pytest.raises(MarkerError):
        replace_block("x\n<!-- mnemo:end -->\n", BLOCK)


def test_strip_block_removes_it_or_returns_none():
    from mnemo.core.export.writers import strip_block

    assert strip_block("before\n\n" + BLOCK + "\nafter\n") == "before\n\nafter\n"
    assert strip_block("no block here\n") is None


def test_write_whole_target_writes_prelude_plus_block(tmp_path: Path):
    from mnemo.core.export.writers import target_for, write_target

    t = target_for("cursor", "auto", tmp_path)
    write_target(t, BLOCK)
    assert t.path.read_text(encoding="utf-8") == t.prelude + BLOCK


def test_write_block_target_round_trips_and_remove(tmp_path: Path):
    from mnemo.core.export.writers import remove_target, target_for, write_target

    t = target_for("codex", "auto", tmp_path)
    t.path.write_text("# Agents\n", encoding="utf-8")
    write_target(t, BLOCK)
    write_target(t, BLOCK2)
    assert t.path.read_text(encoding="utf-8") == "# Agents\n\n" + BLOCK2
    assert remove_target(t) is True
    assert t.path.read_text(encoding="utf-8") == "# Agents\n"
    assert remove_target(t) is False           # nothing left to strip


def test_remove_whole_target_deletes_file(tmp_path: Path):
    from mnemo.core.export.writers import remove_target, target_for, write_target

    t = target_for("claude", "auto", tmp_path)
    write_target(t, BLOCK)
    assert remove_target(t) is True and not t.path.exists()
    assert remove_target(t) is False
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_export_writers.py -q`
Expected: `ImportError: cannot import name 'target_for'`

- [ ] **Step 3: Implement**

```python
# src/mnemo/core/export/writers.py
"""Where an export lands and how the file is touched.

Two kinds of target. *Whole*: the file is nothing but the block (plus a
host prelude, e.g. Cursor's mandatory frontmatter) and is rewritten
wholesale. *Block*: the file belongs to the user (CLAUDE.md, AGENTS.md) and
only the text between the markers is ours — replaced if both markers exist,
appended if neither, refused if exactly one, so a half-deleted block never
silently swallows the user's own text.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.export.render import END_MARKER, START_MARKER


class TargetError(ValueError):
    """Host/target pair that does not exist."""


class MarkerError(ValueError):
    """Exactly one of the two markers is present; refusing to guess."""


@dataclass(frozen=True)
class Target:
    host: str
    name: str          # rules | claude-md | agents-md
    kind: str          # whole | block
    path: Path
    prelude: str = ""


_CURSOR_PRELUDE = "---\ndescription: Rules mnemo learned from you\nalwaysApply: true\n---\n"

_TARGETS = {
    ("claude", "rules"): ("whole", Path(".claude") / "rules" / "mnemo.md", ""),
    ("claude", "claude-md"): ("block", Path("CLAUDE.md"), ""),
    ("cursor", "rules"): ("whole", Path(".cursor") / "rules" / "mnemo.mdc", _CURSOR_PRELUDE),
    ("codex", "agents-md"): ("block", Path("AGENTS.md"), ""),
}
_AUTO = {"claude": "rules", "cursor": "rules", "codex": "agents-md"}


def target_for(host: str, target: str, cwd: Path) -> Target:
    if host not in _AUTO:
        raise TargetError(f"unknown host {host!r}")
    name = _AUTO[host] if target == "auto" else target
    spec = _TARGETS.get((host, name))
    if spec is None:
        raise TargetError(f"--target {name} is not a {host} target")
    kind, rel, prelude = spec
    return Target(host=host, name=name, kind=kind, path=Path(cwd) / rel, prelude=prelude)


def _span(text: str) -> tuple[Optional[int], Optional[int]]:
    """(start offset, offset just past the end marker's line) or Nones."""
    s = text.find(START_MARKER)
    e = text.find(END_MARKER)
    if s == -1 and e == -1:
        return None, None
    if s == -1 or e == -1 or e < s:
        raise MarkerError("found one mnemo marker but not the other; fix the file by hand")
    end = e + len(END_MARKER)
    if text[end:end + 1] == "\n":
        end += 1
    return s, end


def replace_block(text: str, block: str) -> str:
    s, e = _span(text)
    if s is None:
        if not text:
            return block
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    return text[:s] + block + text[e:]


def strip_block(text: str) -> Optional[str]:
    s, e = _span(text)
    if s is None:
        return None
    before, after = text[:s], text[e:]
    if after.startswith("\n"):
        after = after[1:]
    return before + after


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_target(target: Target, block: str) -> None:
    if target.kind == "whole":
        data = target.prelude + block
    else:
        data = replace_block(_read(target.path), block)
    atomic_write_bytes(target.path, data.encode("utf-8"))


def remove_target(target: Target) -> bool:
    """True when something was removed."""
    if target.kind == "whole":
        if not target.path.exists():
            return False
        target.path.unlink()
        return True
    if not target.path.exists():
        return False
    stripped = strip_block(_read(target.path))
    if stripped is None:
        return False
    atomic_write_bytes(target.path, stripped.encode("utf-8"))
    return True
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_export_writers.py -q`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/export/writers.py tests/unit/test_export_writers.py
git commit -m "feat(export): whole-file and managed-block writers"
```

---

### Task 4: Manifest — `core/export/manifest.py`

**Files:**
- Create: `src/mnemo/core/export/manifest.py`
- Test: `tests/unit/test_export_manifest.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_export_manifest.py
from __future__ import annotations

import json
from pathlib import Path


def test_write_and_read_round_trip(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(
        tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
        path=".claude/rules/mnemo.md", rules={"a": "h1", "b": "h2"},
    )
    p = tmp_vault / ".mnemo" / "export" / "app.json"
    assert p.exists()
    data = M.read_manifest(tmp_vault, "app")
    assert data["host"] == "claude" and data["rules"] == {"a": "h1", "b": "h2"}
    assert data["cwd"] == "/r/app" and "exported_at" in data
    assert M.read_manifest(tmp_vault, "other") is None


def test_corrupt_manifest_reads_as_none(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    p = tmp_vault / ".mnemo" / "export" / "app.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    assert M.read_manifest(tmp_vault, "app") is None


def test_delete_manifest(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
                     path=".claude/rules/mnemo.md", rules={})
    assert M.delete_manifest(tmp_vault, "app") is True
    assert M.delete_manifest(tmp_vault, "app") is False


def test_exported_slugs_only_for_claude_loaded_targets_in_that_repo(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
                     path=".claude/rules/mnemo.md", rules={"a": "h", "b": "h"})
    assert M.exported_slugs_for(tmp_vault, "app", repo_root="/r/app") == {"a", "b"}
    assert M.exported_slugs_for(tmp_vault, "app", repo_root="/elsewhere/app") == set()
    assert M.exported_slugs_for(tmp_vault, "nope", repo_root="/r/app") == set()

    M.write_manifest(tmp_vault, "cur", host="cursor", target="rules", cwd="/r/cur",
                     path=".cursor/rules/mnemo.mdc", rules={"c": "h"})
    assert M.exported_slugs_for(tmp_vault, "cur", repo_root="/r/cur") == set()


def test_staleness_counts_changed_added_and_removed(tmp_vault: Path):
    from mnemo.core.export import manifest as M

    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/r/app",
                     path=".claude/rules/mnemo.md", rules={"a": "h1", "b": "h2"})
    # a unchanged, b changed, c new, and nothing for a removed 'd'
    assert M.staleness(tmp_vault, "app", current={"a": "h1", "b": "X", "c": "h3"}) == (2, 2)
    assert M.staleness(tmp_vault, "app", current={"a": "h1", "b": "h2"}) == (2, 0)
    assert M.staleness(tmp_vault, "app", current={"a": "h1"}) == (2, 1)
    assert M.staleness(tmp_vault, "none", current={}) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_export_manifest.py -q`
Expected: `ImportError: cannot import name 'manifest'`

- [ ] **Step 3: Implement**

```python
# src/mnemo/core/export/manifest.py
"""What ``mnemo export`` last wrote for a project.

Lives under the vault's ``.mnemo/export/<project>.json`` — next to the
learned ledger and the migration markers, outside every repo — so the only
thing an export leaves in a repo is the rules file itself.

Two readers: ``mnemo status`` (is the file stale?) and the UserPromptSubmit
hook (which slugs is Claude Code already loading, so the reflex must not
inject them again).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from mnemo.core.atomic import atomic_write_bytes

MANIFEST_DIR_REL = ".mnemo/export"

# Targets Claude Code loads by itself; injecting these again is a repeat.
_CLAUDE_LOADED = {("claude", "rules"), ("claude", "claude-md")}


def manifest_path(vault_root: Path, project: str) -> Path:
    return Path(vault_root) / MANIFEST_DIR_REL / f"{project}.json"


def write_manifest(
    vault_root: Path, project: str, *, host: str, target: str, cwd: str,
    path: str, rules: dict[str, str],
) -> None:
    data = {
        "host": host,
        "target": target,
        "cwd": cwd,
        "path": path,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules": dict(rules),
    }
    atomic_write_bytes(manifest_path(vault_root, project),
                       (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def read_manifest(vault_root: Path, project: str) -> Optional[dict]:
    p = manifest_path(vault_root, project)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rules"), dict) else None


def delete_manifest(vault_root: Path, project: str) -> bool:
    p = manifest_path(vault_root, project)
    if not p.exists():
        return False
    p.unlink()
    return True


def exported_slugs_for(vault_root: Path, project: str, *, repo_root: str) -> set[str]:
    """Slugs the host is already loading for this repo, else empty."""
    data = read_manifest(vault_root, project)
    if not data:
        return set()
    if (data.get("host"), data.get("target")) not in _CLAUDE_LOADED:
        return set()
    if str(data.get("cwd") or "") != str(repo_root):
        return set()
    return {s for s in data["rules"] if isinstance(s, str)}


def staleness(vault_root: Path, project: str, *, current: dict[str, str]) -> Optional[tuple[int, int]]:
    """(rules in the file, rules that differ from the vault now) or None."""
    data = read_manifest(vault_root, project)
    if not data:
        return None
    exported = data["rules"]
    changed = sum(1 for s, h in exported.items() if current.get(s) != h)
    added = sum(1 for s in current if s not in exported)
    return len(exported), changed + added
```

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_export_manifest.py -q`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/export/manifest.py tests/unit/test_export_manifest.py
git commit -m "feat(export): per-project manifest under the vault"
```

---

### Task 5: Orchestrator + CLI — `run_export()` and `mnemo export`

**Files:**
- Modify: `src/mnemo/core/export/__init__.py`
- Create: `src/mnemo/cli/commands/export.py`
- Modify: `src/mnemo/cli/parser.py` (after the `learn` subparser, ~line 262)
- Modify: `src/mnemo/cli/commands/__init__.py`
- Test: `tests/unit/test_cli_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cli_export.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli
from tests.unit._export_fixtures import write_rule


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repo named ``app`` as the cwd, so the project resolves to ``app``."""
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


@pytest.fixture
def vault(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(cli, "_resolve_vault", lambda: tmp_vault)
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    return tmp_vault


def test_export_is_registered_with_its_flags():
    from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, _build_parser
    import mnemo.cli.commands  # noqa: F401

    assert "export" in COMMANDS and "export" not in ADVANCED_COMMANDS
    ns = _build_parser().parse_args(["export", "--host", "cursor", "--limit", "3", "--dry-run"])
    assert ns.host == "cursor" and ns.limit == 3 and ns.dry_run is True
    ns = _build_parser().parse_args(["export"])
    assert ns.host == "claude" and ns.target == "auto" and ns.types == "feedback,user"


def test_dry_run_prints_block_and_writes_nothing(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    assert cli.main(["export", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "### Use yarn  `use-yarn-not-npm`" in out
    assert "would write 1 rule" in out
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_export_writes_file_manifest_and_summary(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="use-yarn-not-npm", name="Use yarn", quote="always yarn")
    write_rule(vault, slug="uni", projects=("x", "y"))
    assert cli.main(["export"]) == 0
    out = capsys.readouterr().out
    assert "exported 2 rules (1 universal) → .claude/rules/mnemo.md" in out
    text = (repo / ".claude" / "rules" / "mnemo.md").read_text(encoding="utf-8")
    assert text.startswith("<!-- mnemo:start") and '> you said: "always yarn"' in text
    data = json.loads((vault / ".mnemo" / "export" / "app.json").read_text())
    assert data["cwd"] == str(repo.resolve()) and set(data["rules"]) == {"use-yarn-not-npm", "uni"}
    assert data["path"] == ".claude/rules/mnemo.md"


def test_no_rules_says_so_and_writes_nothing(repo: Path, vault: Path, capsys):
    assert cli.main(["export"]) == 0
    out = capsys.readouterr().out
    assert "no rules to export for app" in out and "mnemo learn" in out
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()


def test_cursor_and_codex_targets(repo: Path, vault: Path):
    write_rule(vault, slug="r")
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    assert cli.main(["export", "--host", "cursor"]) == 0
    assert (repo / ".cursor" / "rules" / "mnemo.mdc").read_text().startswith("---\ndescription:")
    assert cli.main(["export", "--host", "codex"]) == 0
    agents = (repo / "AGENTS.md").read_text()
    assert agents.startswith("# Agents\n") and "<!-- mnemo:end -->" in agents


def test_mismatched_target_errors(repo: Path, vault: Path, capsys):
    assert cli.main(["export", "--host", "cursor", "--target", "claude-md"]) == 2
    assert "not a cursor target" in capsys.readouterr().err


def test_single_marker_refuses_without_writing(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    (repo / "CLAUDE.md").write_text("x\n<!-- mnemo:start — old -->\nhalf\n", encoding="utf-8")
    assert cli.main(["export", "--target", "claude-md"]) == 1
    assert "one mnemo marker" in capsys.readouterr().err
    assert (repo / "CLAUDE.md").read_text() == "x\n<!-- mnemo:start — old -->\nhalf\n"


def test_remove_strips_file_and_manifest(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="r")
    cli.main(["export"])
    assert cli.main(["export", "--remove"]) == 0
    assert "removed .claude/rules/mnemo.md" in capsys.readouterr().out
    assert not (repo / ".claude" / "rules" / "mnemo.md").exists()
    assert not (vault / ".mnemo" / "export" / "app.json").exists()


def test_token_warning_names_limit(repo: Path, vault: Path, capsys):
    for i in range(40):
        write_rule(vault, slug=f"r{i:02d}", body=("word " * 120) + "\n")
    cli.main(["export", "--dry-run"])
    err = capsys.readouterr().err
    assert "tokens" in err and "--limit" in err


def test_all_types_includes_reference_and_always_warns(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="ref", page_type="reference")
    assert cli.main(["export", "--all-types", "--dry-run"]) == 0
    captured = capsys.readouterr()
    assert "`ref`" in captured.out and "--limit" in captured.err


def test_project_override(repo: Path, vault: Path, capsys):
    write_rule(vault, slug="theirs", projects=("other",))
    cli.main(["export", "--project", "other", "--dry-run"])
    assert "`theirs`" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_cli_export.py -q`
Expected: first test fails with `assert "export" in COMMANDS`

- [ ] **Step 3: Implement the orchestrator**

```python
# src/mnemo/core/export/__init__.py
"""``mnemo export`` — the project's learned rules as a file another tool loads.

See docs/superpowers/specs/2026-09-02-distribution-design.md § 1. The CLI
in :mod:`mnemo.cli.commands.export` only prints; every decision is here so
``init --host`` (PR F) and ``status`` can reuse it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from mnemo.core.export import manifest as manifest_mod
from mnemo.core.export.render import TOKEN_WARN, entry_hash, estimated_tokens, render_block
from mnemo.core.export.select import DEFAULT_TYPES, ExportRule, select_rules
from mnemo.core.export.writers import (  # noqa: F401  — re-exported for callers
    MarkerError, Target, TargetError, remove_target, target_for, write_target,
)

ALL_TYPES: tuple[str, ...] = ("feedback", "user", "reference", "project")


@dataclass
class ExportReport:
    project: str
    target: Target
    rules: list[ExportRule] = field(default_factory=list)
    block: str = ""
    tokens: int = 0
    wrote: bool = False
    removed: bool = False
    warning: Optional[str] = None

    @property
    def universal(self) -> int:
        return sum(1 for r in self.rules if r.universal)


def current_hashes(vault_root: Path, *, project: str, types: Sequence[str] = DEFAULT_TYPES,
                   universal_threshold: int = 2) -> dict[str, str]:
    """slug → entry hash for what an export would write right now (status uses this)."""
    return {r.slug: entry_hash(r) for r in select_rules(
        vault_root, project=project, types=types, universal_threshold=universal_threshold)}


def run_export(
    vault_root: Path,
    *,
    project: str,
    repo_root: Path,
    host: str = "claude",
    target: str = "auto",
    types: Sequence[str] = DEFAULT_TYPES,
    universal_threshold: int = 2,
    limit: Optional[int] = None,
    dry_run: bool = False,
    remove: bool = False,
    force_warning: bool = False,
    today: Optional[str] = None,
) -> ExportReport:
    """Select, render, write (or remove). Raises TargetError / MarkerError."""
    vault_root = Path(vault_root)
    tgt = target_for(host, target, Path(repo_root))
    report = ExportReport(project=project, target=tgt)

    if remove:
        report.removed = remove_target(tgt)
        manifest_mod.delete_manifest(vault_root, project)
        return report

    report.rules = select_rules(vault_root, project=project, types=types,
                                universal_threshold=universal_threshold, limit=limit)
    if not report.rules:
        return report
    report.block = render_block(report.rules, project=project,
                                today=today or date.today().isoformat())
    report.tokens = estimated_tokens(report.block)
    if force_warning or report.tokens > TOKEN_WARN:
        report.warning = (
            f"about {report.tokens} tokens will load on every prompt — "
            "consider --limit N to keep the most-sourced rules only"
        )
    if dry_run:
        return report

    write_target(tgt, report.block)
    manifest_mod.write_manifest(
        vault_root, project, host=host, target=tgt.name, cwd=str(Path(repo_root).resolve()),
        path=str(tgt.path.relative_to(Path(repo_root)).as_posix()),
        rules={r.slug: entry_hash(r) for r in report.rules},
    )
    report.wrote = True
    return report
```

- [ ] **Step 4: Implement the CLI command**

```python
# src/mnemo/cli/commands/export.py
"""``mnemo export`` — write this project's rules where another tool will read them.

Prints one line per outcome and nothing else; the reasoning lives in
:mod:`mnemo.core.export`.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mnemo.cli.parser import command


@command("export")
def cmd_export(args: argparse.Namespace) -> int:
    from mnemo import cli  # late binding, as every other command does
    from mnemo.core import config as cfg_mod
    from mnemo.core.agent import resolve_canonical_agent
    from mnemo.core import export as export_mod

    vault = cli._resolve_vault()
    agent = resolve_canonical_agent(os.getcwd())
    project = getattr(args, "project", None) or agent.name
    repo_root = Path(agent.repo_root)
    all_types = bool(getattr(args, "all_types", False))
    types = export_mod.ALL_TYPES if all_types else tuple(
        t.strip() for t in str(getattr(args, "types", "feedback,user")).split(",") if t.strip()
    )
    try:
        threshold = int((cfg_mod.load_config().get("scoping") or {}).get("universalThreshold", 2))
    except Exception:  # noqa: BLE001
        threshold = 2

    try:
        report = export_mod.run_export(
            vault, project=project, repo_root=repo_root,
            host=getattr(args, "host", "claude"), target=getattr(args, "target", "auto"),
            types=types, universal_threshold=threshold,
            limit=getattr(args, "limit", None),
            dry_run=bool(getattr(args, "dry_run", False)),
            remove=bool(getattr(args, "remove", False)),
            force_warning=all_types,
        )
    except export_mod.TargetError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except export_mod.MarkerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rel = report.target.path.relative_to(repo_root).as_posix()
    if bool(getattr(args, "remove", False)):
        print(f"removed {rel}" if report.removed else f"nothing to remove at {rel}")
        return 0
    if not report.rules:
        print(f"no rules to export for {project} — correct Claude, run `mnemo learn`, then export")
        return 0
    if report.warning:
        print(f"warning: {report.warning}", file=sys.stderr)
    n = len(report.rules)
    plural = "rule" if n == 1 else "rules"
    if not report.wrote:
        print(report.block, end="")
        print(f"would write {n} {plural} ({report.universal} universal) → {rel}")
        return 0
    print(f"exported {n} {plural} ({report.universal} universal) → {rel}")
    print("re-run after new rules; `mnemo status` says when it is stale")
    return 0
```

- [ ] **Step 5: Register the subparser and the import**

In `src/mnemo/cli/parser.py`, directly after the `learn` subparser block (the `learn.add_argument("--dry-run", …)` call), add:

```python
    export = sub.add_parser(
        "export",
        help="write this project's rules to a file Claude Code / Cursor / Codex loads",
    )
    export.add_argument("--host", choices=["claude", "cursor", "codex"], default="claude",
                        help="which tool will read the file (default: claude)")
    export.add_argument("--target", choices=["auto", "rules", "claude-md", "agents-md"], default="auto",
                        help="rules file (default per host), or a managed block in CLAUDE.md / AGENTS.md")
    export.add_argument("--project", default=None, help="export another project's rules instead of the cwd's")
    export.add_argument("--types", default="feedback,user", help="page types to include (default: feedback,user)")
    export.add_argument("--all-types", action="store_true", help="include reference and project pages too")
    export.add_argument("--limit", type=int, default=None, metavar="N", help="keep only the first N after ordering")
    export.add_argument("--dry-run", action="store_true", help="print the block, write nothing")
    export.add_argument("--remove", action="store_true", help="delete the exported file / block and its manifest")
```

In `src/mnemo/cli/commands/__init__.py`, add `export,` to the import list between `doctor,` and `extract,`.

- [ ] **Step 6: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_cli_export.py -q`
Expected: `11 passed`

Then the surface tests that pin the command list: `python3 -m pytest tests/unit/test_cli_dispatch.py tests/unit/test_cli_help.py -q 2>/dev/null || python3 -m pytest tests/unit -q -k "dispatch or help"`
Expected: pass (if a test pins the exact command set, add `"export"` to it and note this in the commit body).

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/export/__init__.py src/mnemo/cli/commands/export.py src/mnemo/cli/parser.py src/mnemo/cli/commands/__init__.py tests/unit/test_cli_export.py
git commit -m "feat(export): mnemo export command"
```

---

### Task 6: Reflex skips exported slugs; `mnemo why` shows them

**Files:**
- Modify: `src/mnemo/hooks/user_prompt_submit.py` (candidate step, ~line 66; `_log_silence` / `_log_emission`)
- Modify: `src/mnemo/core/reflex/receipts.py` (`_format_emission`, `_format_silence`, `_explain`)
- Test: `tests/unit/test_reflex_export_skip.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_reflex_export_skip.py
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest


def _run_hook(monkeypatch, vault: Path, repo: Path, prompt: str) -> tuple[str, list[dict]]:
    from mnemo.hooks import user_prompt_submit as hook

    cfg = {"vaultRoot": str(vault), "reflex": {"enabled": True,
           "thresholds": {"minQueryTokens": 1, "termOverlapMin": 1, "relativeGap": 1.0, "absoluteFloor": 0.0}}}
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": "s1", "cwd": str(repo), "prompt": prompt})))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    assert hook.main() == 0
    log = vault / ".mnemo" / "reflex-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines()] if log.exists() else []
    return out.getvalue(), entries


# The fixture's target rule is about mocking Prisma with jest-mock-extended;
# this prompt clears the default gates against it (see tests/conftest.py
# ``synthetic_index`` — it takes the vault only and returns nothing).
PROMPT = "mock the prisma client in tests with jest-mock-extended"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    return root


def test_exported_slug_is_not_injected_and_is_recorded(tmp_vault: Path, repo: Path, synthetic_index, monkeypatch):
    from mnemo.core.export import manifest as M

    synthetic_index(tmp_vault)          # seeds `use-prisma-mock`, universal, + noise rules
    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd=str(repo.resolve()),
                     path=".claude/rules/mnemo.md", rules={"use-prisma-mock": "h"})

    out, entries = _run_hook(monkeypatch, tmp_vault, repo, PROMPT)
    assert out == ""                                   # nothing injected
    assert entries[-1]["silence_reason"] == "all_exported"
    assert entries[-1]["exported"] == ["use-prisma-mock"]


def test_other_repo_still_gets_injection(tmp_vault: Path, repo: Path, synthetic_index, monkeypatch):
    from mnemo.core.export import manifest as M

    synthetic_index(tmp_vault)
    M.write_manifest(tmp_vault, "app", host="claude", target="rules", cwd="/somewhere/else",
                     path=".claude/rules/mnemo.md", rules={"use-prisma-mock": "h"})

    out, entries = _run_hook(monkeypatch, tmp_vault, repo, PROMPT)
    assert "use-prisma-mock" in out
    assert "exported" not in entries[-1]


def test_why_prints_exported_lines():
    from mnemo.core.reflex import receipts

    emission = {"ts": "2026-09-02T09:41:24Z", "emitted": ["b"], "scores": [3.0],
                "silence_reason": None, "candidates": [["b", 3.0]], "exported": ["a"]}
    silence = {"ts": "2026-09-02T09:42:00Z", "emitted": [], "scores": [],
               "silence_reason": "all_exported", "exported": ["a", "c"]}
    text = receipts.format_human([emission, silence])
    assert "injected  b (3.00)" in text
    assert "exported  a (already in your rules file)" in text
    assert "silent    every matching rule is already in your rules file (a, c)" in text
```

`synthetic_index` (tests/conftest.py ~277) seeds `use-prisma-mock` as a universal rule plus five noise rules, so any project matches and the default gates pass for `PROMPT`. If the second test still gets silence, print `entries[-1]` — the fix is the prompt wording, never the fixture.

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_reflex_export_skip.py -q`
Expected: 3 failures (`out` contains the injection; `exported` key missing; `mnemo why` text lacks the lines)

- [ ] **Step 3: Implement the hook change**

In `src/mnemo/hooks/user_prompt_submit.py`:

Replace

```python
        cwd = payload.get("cwd") or str(Path.cwd())
        project = resolve_canonical_agent(cwd).name
```

with

```python
        cwd = payload.get("cwd") or str(Path.cwd())
        agent = resolve_canonical_agent(cwd)
        project = agent.name
```

Replace the candidates step

```python
        candidates = _candidates_for_project(index, project)
        if not candidates:
            _log_silence(vault, sid, project, prompt_raw, reason="index_missing")
            return 0
```

with

```python
        candidates = _candidates_for_project(index, project)
        # Rules already exported into this repo's rules file are loaded by
        # Claude Code itself; injecting them again is a repeat.
        from mnemo.core.export.manifest import exported_slugs_for
        exported = sorted(exported_slugs_for(vault, project, repo_root=agent.repo_root)
                          & set(candidates))
        if exported:
            candidates = [s for s in candidates if s not in exported]
        if not candidates:
            reason = "all_exported" if exported else "index_missing"
            _log_silence(vault, sid, project, prompt_raw, reason=reason, exported=exported)
            return 0
```

Add `exported: list | None = None` as a keyword parameter to both `_log_silence` and `_log_emission`, and in each, after the `thresholds` handling:

```python
    if exported:
        entry["exported"] = exported
```

Pass `exported=exported` in every `_log_silence(...)` / `_log_emission(...)` call that comes after the candidates step (the `below_min_tokens` and first `index_missing` calls run before `exported` exists — leave them).

- [ ] **Step 4: Implement the receipts change**

In `src/mnemo/core/reflex/receipts.py`:

Add a helper after `_candidates`:

```python
def _exported_line(when: str, entry: dict) -> list[str]:
    exported = [str(s) for s in (entry.get("exported") or [])]
    if not exported:
        return []
    return [f"{' ' * len(when)}  exported  {', '.join(exported)} (already in your rules file)"]
```

At the end of `_format_emission`, before `return lines`: `lines.extend(_exported_line(when, entry))`.
At the end of `_format_silence`, before `return lines`: if `reason != "all_exported"`, `lines.extend(_exported_line(when, entry))`.

In `_explain`, after the `session_cap_reached` branch:

```python
    if reason == "all_exported":
        names = ", ".join(str(s) for s in (entry.get("exported") or []))
        return (f"every matching rule is already in your rules file ({names})", False)
```

- [ ] **Step 5: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_reflex_export_skip.py tests/unit -q -k "reflex or receipts or why"`
Expected: all pass, no regressions in the existing receipts/why tests.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/hooks/user_prompt_submit.py src/mnemo/core/reflex/receipts.py tests/unit/test_reflex_export_skip.py
git commit -m "feat(export): reflex skips exported slugs; mnemo why shows them"
```

---

### Task 7: `mnemo status` export line

**Files:**
- Modify: `src/mnemo/cli/commands/status.py` (add `_print_export_status`, call it right after `_print_learned_status` in `cmd_status`)
- Test: `tests/unit/test_cli_status_export.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cli_status_export.py
from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit._export_fixtures import write_rule


def _status_out(capsys) -> str:
    from mnemo.cli.commands import status as status_cmd
    return capsys.readouterr().out


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "app"
    (root / ".git").mkdir(parents=True)
    monkeypatch.chdir(root)
    return root


def test_no_manifest_prints_nothing(tmp_vault: Path, repo: Path, capsys):
    from mnemo.cli.commands.status import _print_export_status

    _print_export_status(tmp_vault)
    assert capsys.readouterr().out == ""


def test_up_to_date_and_stale(tmp_vault: Path, repo: Path, capsys, monkeypatch):
    from mnemo.core import export as export_mod
    from mnemo.cli.commands.status import _print_export_status

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"vaultRoot": str(tmp_vault)})
    write_rule(tmp_vault, slug="a")
    export_mod.run_export(tmp_vault, project="app", repo_root=repo)

    _print_export_status(tmp_vault)
    assert capsys.readouterr().out == "\nExport: 1 rule → .claude/rules/mnemo.md (up to date)\n"

    write_rule(tmp_vault, slug="a", body="changed\n")
    write_rule(tmp_vault, slug="b")
    _print_export_status(tmp_vault)
    assert capsys.readouterr().out == (
        "\nExport: 1 rule → .claude/rules/mnemo.md (2 changed in vault since, run mnemo export)\n"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `python3 -m pytest tests/unit/test_cli_status_export.py -q`
Expected: `ImportError: cannot import name '_print_export_status'`

- [ ] **Step 3: Implement**

In `src/mnemo/cli/commands/status.py`, after `_print_learned_status`:

```python
def _print_export_status(vault: Path) -> None:
    """One line when this project has an exported rules file: where, and whether
    the vault has moved on since. Silent when nothing was exported."""
    from mnemo.core import config as cfg_mod
    from mnemo.core import export as export_mod
    from mnemo.core.export import manifest as manifest_mod

    project = _current_project()
    if not project:
        return
    data = manifest_mod.read_manifest(vault, project)
    if not data:
        return
    try:
        threshold = int((cfg_mod.load_config().get("scoping") or {}).get("universalThreshold", 2))
    except Exception:  # noqa: BLE001
        threshold = 2
    current = export_mod.current_hashes(vault, project=project, universal_threshold=threshold)
    stale = manifest_mod.staleness(vault, project, current=current)
    if stale is None:
        return
    total, changed = stale
    noun = "rule" if total == 1 else "rules"
    state = "up to date" if changed == 0 else f"{changed} changed in vault since, run mnemo export"
    print(f"\nExport: {total} {noun} → {data.get('path')} ({state})")
```

In `cmd_status`, find the call to `_print_learned_status(vault)` and add `_print_export_status(vault)` on the next line.

- [ ] **Step 4: Run to verify pass**

Run: `python3 -m pytest tests/unit/test_cli_status_export.py tests/unit/test_cli_status_doctor.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/cli/commands/status.py tests/unit/test_cli_status_export.py
git commit -m "feat(export): status reports the exported file and its staleness"
```

---

### Task 8: Docs, changelog, real-vault check

**Files:**
- Modify: `README.md` (Commands section, ~line 150)
- Modify: `docs/getting-started.md` (new section before "## Observing and debugging", ~line 357)
- Modify: `CHANGELOG.md` (new `[Unreleased]` above `[1.2.0]`)

- [ ] **Step 1: README**

In the "Commands" section, after the slash-command code block and before the "Everything else is a CLI subcommand" paragraph, add:

```markdown
Your rules are yours. `mnemo export` writes the ones for the repo you're in
to `.claude/rules/mnemo.md` — a plain file Claude Code loads by itself, with
your own quote under each rule — so a teammate without mnemo gets them too,
and leaving mnemo costs you nothing. `--host cursor` and `--host codex` write
the same rules where those tools look. Re-run after learning more;
`mnemo status` says when the file is behind the vault.
```

- [ ] **Step 2: getting-started**

Insert before `## Observing and debugging`:

```markdown
## Taking your rules with you

```
mnemo export                 # → .claude/rules/mnemo.md
mnemo export --target claude-md   # managed block inside CLAUDE.md instead
mnemo export --host cursor   # → .cursor/rules/mnemo.mdc
mnemo export --host codex    # managed block inside AGENTS.md
mnemo export --dry-run       # print the block, touch nothing
mnemo export --remove        # delete the file / strip the block
```

What goes in: `feedback` and `user` rules attributed to this repo, plus
universal ones, most-sourced first. `reference` pages stay out unless you
pass `--all-types`; `--limit N` keeps the top N. Each rule carries the
sentence you said as `> you said: "…"`.

The block sits between `<!-- mnemo:start` and `<!-- mnemo:end -->`; anything
outside the markers in CLAUDE.md or AGENTS.md is never touched. Re-running
regenerates the block and nothing flows back from the file into the vault.
Once a rule is in `.claude/rules/mnemo.md`, the reflex stops injecting it —
Claude Code is already loading it — and `mnemo why` lists it as `exported`.
`mnemo status` shows `Export: N rules → … (up to date)` or how many rules
changed in the vault since.
```

- [ ] **Step 3: CHANGELOG**

Above `## [1.2.0] — 2026-09-02`:

```markdown
## [Unreleased]

### Added

- **`mnemo export`.** Writes the current repo's learned rules — `feedback`
  and `user` pages attributed to it, plus universal ones — to a file the
  host loads on its own: `.claude/rules/mnemo.md` (default), a managed block
  in `CLAUDE.md` (`--target claude-md`), `.cursor/rules/mnemo.mdc`
  (`--host cursor`) or a managed block in `AGENTS.md` (`--host codex`).
  Each rule carries the user's own quote. `--dry-run`, `--limit`,
  `--all-types`, `--remove`. A per-project manifest under the vault's
  `.mnemo/export/` lets `mnemo status` report staleness and lets the reflex
  skip rules Claude Code is already loading (`mnemo why` marks them
  `exported`).
```

- [ ] **Step 4: Real-vault check (reviewer step, read-only)**

```bash
cd ~/github/clubinho 2>/dev/null || cd "$(ls -d ~/github/* | head -1)"
/usr/local/bin/python3 -m mnemo export --dry-run | head -60
/usr/local/bin/python3 -m mnemo export --dry-run | tail -3
```

Expected: a block of the project's feedback rules with `> you said:` lines under the verified ones, the final line `would write N rules (M universal) → .claude/rules/mnemo.md`, nothing written (`git status` in that repo unchanged, no `~/mnemo/.mnemo/export/` created). Note N and whether the text reads as rules rather than narrative in the PR description.

- [ ] **Step 5: Full suite + commit**

Run: `python3 -m pytest tests/unit -q`
Expected: all pass (2 known env-dependent skips/flakes are documented in memory; nothing new).

```bash
git add README.md docs/getting-started.md CHANGELOG.md
git commit -m "docs(export): README, getting-started, changelog"
```

- [ ] **Step 6: Open the PR**

Branch `feat/pr-e-export` from `master`; PR title `feat: mnemo export — managed rules file for Claude Code, Cursor, Codex`. Body: link the spec § 1, the real-vault dry-run numbers from Step 4, and the acceptance list (dry-run writes nothing; single marker refuses; reflex skip; status line). Wait for CI 14/14 before requesting review.

---

## Self-review against the spec

- Selection (types, project ∪ universal, inbox/archive excluded, ordering, limit, token warning): Tasks 1, 5. ✔
- Rendering (markers, heading with slug, universal tag, graph section stripped, `you said`): Task 2. ✔
- Targets table, managed-block semantics, one-marker refusal, atomic writes, `--remove`: Task 3, 5. ✔
- Manifest under `<vault>/.mnemo/export/<project>.json` with `cwd`, `path`, hashes: Task 4. ✔
- Staleness line in `status`: Task 7. ✔
- No double injection + `mnemo why` `exported`: Task 6. ✔
- "Not a sync / not a dump" — `--all-types` always warns: Task 5 (`force_warning=all_types`). ✔
- Real-vault review step: Task 8. ✔
- Names used consistently: `select_rules`, `ExportRule`, `render_entry/render_block/entry_hash/estimated_tokens/TOKEN_WARN/START_MARKER/END_MARKER`, `Target/target_for/replace_block/strip_block/write_target/remove_target/TargetError/MarkerError`, `write_manifest/read_manifest/delete_manifest/exported_slugs_for/staleness`, `run_export/ExportReport/current_hashes/ALL_TYPES`, `_print_export_status`.
