# Enforce Safety Rails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a single LLM-extracted briefing line from becoming a session-wide hard-block on developer tools by adding schema validation, safe auto-promotion, and discoverable block messages around `enforce:` rules.

**Architecture:** Five independent changes across four subsystems (schema validation in `rule_activation/parsing.py`, safe promotion in `extract/inbox/rendering.py`, richer block messages in `hooks/pre_tool_use.py`, a new CLI audit command, and extractor prompt tweak). Each change is behind its own task and has its own tests; they compose but do not depend on each other except where explicitly noted.

**Tech Stack:** Python 3.11+, pytest, argparse, mnemo's existing rule-activation index + extraction pipeline.

---

## Background (from the 2026-04-23 dogfood incident)

Running `git push -u origin feat/menu-import-a1-anthropic-sdk` on the Meunu project was hard-blocked by the mnemo PreToolUse hook. Trace:

1. Extractor consumed `bots/Meunu/briefings/sessions/445b14a6-…md`, which mentioned "retargetear PRs stacked antes de mergear/push".
2. LLM emitted `shared/feedback/stacked-prs-retarget-on-merge.md` with `enforce: {tool: Bash, deny_command: git push, reason: ...}`.
3. Rule was auto-promoted from `_inbox/` → `shared/feedback/` without human review (tag `auto-promoted`, `last_sync` same-day).
4. `deny_command: git push` is a blunt prefix match — fires on **every** `git push`, not only in a stacked-PR context.
5. Hook output surfaced only the `reason` string. User had no pointer to the rule file.

Four independent failures in the pipeline:

- **R1 (extractor over-reaches):** extractor has no heuristic for "can the hook actually verify this?" and emits `enforce:` for context-dependent rules.
- **R2 (`deny_command` prefix is too coarse):** schema allows bare prefix without requiring a qualifying `deny_pattern` or context predicate.
- **R3 (auto-promotion carries `enforce:` unreviewed):** promoter copies frontmatter verbatim. `enforce:` is security-sensitive; it should require a human gate.
- **R4 (block messages omit provenance):** user can't see which file to edit.

Fixes below are labelled C1-C5 matching the original spec and implemented in tasks T1-T8.

---

## File Structure

**Files created:**
- `src/mnemo/cli/commands/list_enforced.py` — new CLI command (Task 6)
- `tests/core/rule_activation/test_parsing_require_qualifier.py` — schema validator test (Task 1)
- `tests/unit/test_extract_inbox_strip_enforce.py` — promoter strip test (Task 3)
- `tests/unit/test_hook_pre_tool_use_block_message.py` — block message test (Task 4)
- `tests/cli/test_list_enforced.py` — CLI test (Task 6)
- `tests/unit/test_extract_prompts_enforce_guidance.py` — extractor prompt test (Task 7)

**Files modified:**
- `src/mnemo/core/rule_activation/parsing.py` — reject bare `deny_command` (Task 1)
- `src/mnemo/core/rule_activation/index.py` — store rule `path` in entry (Task 2)
- `src/mnemo/core/extract/inbox/rendering.py` — strip `enforce:` on auto-promote (Task 3)
- `src/mnemo/hooks/pre_tool_use.py` — enrich deny envelope with path + hint (Task 4)
- `src/mnemo/core/rule_activation/matching.py` — add `path` to `EnforceHit` (Task 4)
- `src/mnemo/cli/parser.py` — register `list-enforced` subcommand (Task 6)
- `src/mnemo/cli/commands/__init__.py` — import new command (Task 6)
- `src/mnemo/core/extract/prompts/templates/system_feedback.py` — tighten `enforce:` guidance (Task 7)
- `pyproject.toml` — version bump 0.8.0 → 0.11.0 (Task 8)
- `CHANGELOG.md` — changelog entry (Task 8)
- `src/mnemo/cli/commands/doctor_checks/rules.py` — surface malformed-bare-deny-command count (Task 1)

---

## Task 1: Schema validator rejects bare `deny_command`

**Why first:** C2 is load-time enforcement. Everything else assumes rules either load or are flagged; this gates the rest.

**Files:**
- Modify: `src/mnemo/core/rule_activation/parsing.py:85-166`
- Modify: `src/mnemo/cli/commands/doctor_checks/rules.py`
- Test: `tests/core/rule_activation/test_parsing_require_qualifier.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/rule_activation/test_parsing_require_qualifier.py`:

```python
from mnemo.core.rule_activation.parsing import parse_enforce_block


def test_bare_deny_command_without_pattern_rejected():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_command": "git push",
            "reason": "Don't push",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert parsed is None
    assert err is not None
    assert "qualifier" in err.lower() or "deny_pattern" in err


def test_deny_command_with_pattern_accepted():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_command": "git commit",
            "deny_pattern": "Co-Authored-By",
            "reason": "No coauthor trailers",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert err is None
    assert parsed is not None
    assert parsed["deny_commands"] == ["git commit"]
    assert parsed["deny_patterns"] == ["Co-Authored-By"]


def test_deny_pattern_alone_still_accepted():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_pattern": "rm -rf /",
            "reason": "Don't wipe root",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert err is None
    assert parsed is not None


def test_bare_deny_command_list_also_rejected():
    fm = {
        "enforce": {
            "tool": "Bash",
            "deny_command": ["git push", "git commit"],
            "reason": "nope",
        }
    }
    parsed, err = parse_enforce_block(fm)
    assert parsed is None
    assert err is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/core/rule_activation/test_parsing_require_qualifier.py -v`
Expected: `test_bare_deny_command_without_pattern_rejected` and `test_bare_deny_command_list_also_rejected` FAIL (currently accepted by `_parse_enforce`).

- [ ] **Step 3: Confirm `parse_enforce_block` is the public name**

Run: `grep -n "parse_enforce_block\|parse_enforce" src/mnemo/core/rule_activation/__init__.py src/mnemo/core/rule_activation/parsing.py | head`
Expected: public symbol is `parse_enforce_block` (re-exported from `__init__`). If test imports fail, adjust the import or add a re-export shim.

- [ ] **Step 4: Add the validator**

Edit `src/mnemo/core/rule_activation/parsing.py`, in `_parse_enforce`, right after the "Must have at least one pattern or command" block (~line 152) and before the reason validation:

```python
    # --- qualifier requirement (C2 safety rail, 2026-04-23) ---
    # Bare `deny_command` is a coarse prefix match — `git push` blocks all
    # pushes regardless of context. Force every enforce block that uses
    # deny_command to also supply a deny_pattern qualifier. deny_pattern
    # alone is fine (regex is already specific).
    if validated_commands and not validated_patterns:
        return None, (
            "enforce: deny_command requires a qualifier — "
            "add a deny_pattern regex that narrows the match, "
            "or drop the enforce block entirely"
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/core/rule_activation/test_parsing_require_qualifier.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 6: Run full rule_activation test suite**

Run: `PYTHONPATH=$(pwd)/src pytest tests/core/rule_activation/ -v`
Expected: all existing tests still pass. If any existing fixture uses bare `deny_command`, update it to add `deny_pattern` or document the exception.

- [ ] **Step 7: Doctor surfaces bare-deny-command rules**

Edit `src/mnemo/cli/commands/doctor_checks/rules.py` — find the existing malformed-rule loop and add a dedicated count for rules rejected by the new validator. Look for the "malformed" list iteration; rules with the new error string will already appear there, but add a summary line:

```python
bare_deny = [m for m in malformed if "deny_command requires a qualifier" in m.get("error", "")]
if bare_deny:
    print(f"  ⚠ {len(bare_deny)} rule(s) have bare `deny_command` without `deny_pattern` — "
          f"add a regex qualifier or remove the enforce block. Paths:")
    for m in bare_deny[:5]:
        print(f"    • {m['path']}")
    if len(bare_deny) > 5:
        print(f"    … {len(bare_deny) - 5} more")
```

- [ ] **Step 8: Verify doctor output on a vault with a bare rule**

Create `/tmp/vault-bare-test/shared/feedback/bad.md` with a bare `deny_command`. Run `MNEMO_VAULT=/tmp/vault-bare-test mnemo doctor` and confirm the new warning appears. Delete the fixture after.

- [ ] **Step 9: Commit**

```bash
git add src/mnemo/core/rule_activation/parsing.py \
        src/mnemo/cli/commands/doctor_checks/rules.py \
        tests/core/rule_activation/test_parsing_require_qualifier.py
git commit -m "feat(rule-activation): reject bare deny_command without deny_pattern qualifier"
```

---

## Task 2: Store rule source path in index entry

**Why:** Task 4 (block message) needs the rule's absolute path. The index currently stores `file_stem` but not the full path. Add `path` so downstream consumers (hook, list-enforced CLI) can surface it without reopening the vault.

**Files:**
- Modify: `src/mnemo/core/rule_activation/index.py:202-214`
- Test: extend `tests/core/rule_activation/test_projects_from_frontmatter.py` or add a new test file.

- [ ] **Step 1: Write the failing test**

Add to a new file `tests/core/rule_activation/test_entry_has_path.py`:

```python
from pathlib import Path
from mnemo.core.rule_activation.index import build_index


def test_entry_stores_rule_path(tmp_path: Path):
    vault = tmp_path
    (vault / "shared" / "feedback").mkdir(parents=True)
    rule = vault / "shared" / "feedback" / "example.md"
    rule.write_text(
        "---\n"
        "name: Example\n"
        "description: Example rule\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/demo/memory/foo.md\n"
        "tags:\n"
        "  - demo\n"
        "---\n"
        "Body.\n"
    )
    index = build_index(vault)
    # At least one rule entry exists and carries the file path.
    rules = index.get("rules", {})
    assert rules, "expected at least one rule in the index"
    any_entry = next(iter(rules.values()))
    assert "path" in any_entry
    assert any_entry["path"].endswith("shared/feedback/example.md")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/core/rule_activation/test_entry_has_path.py -v`
Expected: FAIL — `"path" in any_entry` is False.

- [ ] **Step 3: Add `path` to entry**

Edit `src/mnemo/core/rule_activation/index.py`, in `_build_entry` (around line 202):

```python
    entry = {
        "type": page_type,
        "name": fm.get("name", slug),
        "file_stem": md_path.stem,
        "path": str(md_path),           # NEW — C4 uses this in block messages
        "topic_tags": topic_tags_list,
        # … rest unchanged …
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/core/rule_activation/test_entry_has_path.py -v`
Expected: PASS.

- [ ] **Step 5: Run full rule_activation suite + any index snapshot tests**

Run: `PYTHONPATH=$(pwd)/src pytest tests/core/rule_activation/ tests/unit/ -k "index or rule_activation" -v`
Expected: all pass. If a snapshot test compares entry shape, update the snapshot (new additive key).

- [ ] **Step 6: Bump INDEX_VERSION if required**

Run: `grep -n "INDEX_VERSION" src/mnemo/core/rule_activation/index.py src/mnemo/core/reflex/index.py`
If the schema version is used for cache invalidation, bump it (e.g. 3 → 4) so existing `rule-activation-index.json` gets rebuilt on next `mnemo fix`. If no version constant exists, skip this step.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/rule_activation/index.py \
        tests/core/rule_activation/test_entry_has_path.py
git commit -m "feat(rule-activation): store rule source path in index entry"
```

---

## Task 3: Auto-promotion strips `enforce:` blocks

**Why:** R3 — promotion from `_inbox/` to `shared/` should not silently carry `enforce:` into production. `auto_promoted=True` is the exact boundary where "LLM-authored" becomes "runtime-active"; strip the enforcement and flag the page so the user can review.

**Files:**
- Modify: `src/mnemo/core/extract/inbox/rendering.py:80-124`
- Test: `tests/unit/test_extract_inbox_strip_enforce.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_extract_inbox_strip_enforce.py`:

```python
from mnemo.core.extract.inbox.rendering import _render_page
from mnemo.core.extract.inbox.types import ExtractedPage


def _make_page_with_enforce() -> ExtractedPage:
    return ExtractedPage(
        name="Retarget stacked PRs",
        description="Retarget child PRs",
        type="feedback",
        body="Never push without retargeting.",
        source_files=["bots/demo/briefings/sessions/abc.md"],
        tags=["git"],
        stability="stable",
        enforce={
            "tool": "Bash",
            "deny_command": "git push",
            "reason": "Check retarget before push",
        },
        activates_on=None,
    )


def test_auto_promoted_render_strips_enforce():
    page = _make_page_with_enforce()
    out = _render_page(page, run_id="2026-04-23T12:00:00", auto_promoted=True)
    assert "enforce:" not in out, "auto-promoted page must not carry enforce block"
    assert "promoted_without_enforce: true" in out
    assert "review" in out.lower()


def test_manual_render_preserves_enforce():
    page = _make_page_with_enforce()
    out = _render_page(page, run_id="2026-04-23T12:00:00", auto_promoted=False)
    assert "enforce:" in out
    assert "promoted_without_enforce" not in out


def test_auto_promoted_page_without_enforce_unchanged():
    page = _make_page_with_enforce()
    page = page.__class__(**{**page.__dict__, "enforce": None})
    out = _render_page(page, run_id="2026-04-23T12:00:00", auto_promoted=True)
    assert "promoted_without_enforce" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_extract_inbox_strip_enforce.py -v`
Expected: `test_auto_promoted_render_strips_enforce` FAILS — current code renders the enforce block.

If the `ExtractedPage` constructor signature in the test doesn't match the real dataclass, open `src/mnemo/core/extract/inbox/types.py`, check the required fields, and adjust the test to match.

- [ ] **Step 3: Modify `_render_page`**

Edit `src/mnemo/core/extract/inbox/rendering.py:80-124`. Replace the enforce-rendering section:

```python
    # --- enforce block ---
    # Safety rail (C3, 2026-04-23): auto-promoted pages are LLM-authored
    # and unreviewed. Stripping the enforce block prevents a single briefing
    # line from becoming a session-wide hard-block. Manual promotion paths
    # (auto_promoted=False) still honor the enforce block.
    enforce_block = ""
    enforce_stripped = False
    if isinstance(page.enforce, dict) and page.enforce:
        if auto_promoted:
            enforce_stripped = True
        else:
            enforce_block = _render_nested_block("enforce", page.enforce)
```

In the `extras` computation (line ~82-87), append the flag and the review note when stripped:

```python
    if auto_promoted:
        extras = f"last_sync: {run_id}\n"
        if enforce_stripped:
            extras += "promoted_without_enforce: true\n"
        system_marker = "auto-promoted"
    else:
        extras = ""
        system_marker = "needs-review"
```

And in the body composition (end of `_render_page`), prepend a review note when stripped:

```python
    body_prefix = ""
    if enforce_stripped:
        body_prefix = (
            "> _mnemo auto-promoter stripped an `enforce:` block from this rule._\n"
            "> _Review the pattern and re-add manually if safe. "
            "See docs/superpowers/plans/2026-04-23-enforce-safety-rails.md._\n\n"
        )
    return (
        "---\n"
        # …existing frontmatter…
        "---\n\n"
        f"{body_prefix}{page.body}\n"
    )
```

Note: the variable `enforce_stripped` is computed before `extras`, so reorder the function body so the enforce branch runs first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_extract_inbox_strip_enforce.py -v`
Expected: all 3 PASS.

- [ ] **Step 5: Run full extraction test suite**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_extract_inbox.py tests/unit/test_extract_inbox_v0_3.py tests/unit/test_extract_promote.py -v`
Expected: all pass. If an integration snapshot changed (e.g. `assert expected_frontmatter in rendered`), update it — the added `promoted_without_enforce:` key and body prefix are expected.

- [ ] **Step 6: Add doctor warning for stripped rules**

Edit `src/mnemo/cli/commands/doctor_checks/rules.py` and add a check for rules whose frontmatter contains `promoted_without_enforce: true`:

```python
# After the existing rule iteration:
stripped_paths = []
for md in (vault_root / "shared").rglob("*.md"):
    try:
        text = md.read_text()
        if "promoted_without_enforce: true" in text.split("---", 2)[1]:
            stripped_paths.append(md)
    except (IndexError, OSError):
        continue

if stripped_paths:
    print(f"  ⚠ {len(stripped_paths)} auto-promoted rule(s) had enforce block stripped — "
          f"review and re-add manually if the pattern is safe:")
    for p in stripped_paths[:5]:
        print(f"    • {p.relative_to(vault_root)}")
```

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/extract/inbox/rendering.py \
        src/mnemo/cli/commands/doctor_checks/rules.py \
        tests/unit/test_extract_inbox_strip_enforce.py
git commit -m "feat(extract): strip enforce block from auto-promoted pages, surface in doctor"
```

---

## Task 4: Hook block message includes rule path + disable hint

**Why:** R4 — users can't find the offending rule from the current deny envelope. Include path, slug, and a one-line remediation.

**Files:**
- Modify: `src/mnemo/core/rule_activation/matching.py:23-28,119-130`
- Modify: `src/mnemo/hooks/pre_tool_use.py:68-131`
- Test: `tests/unit/test_hook_pre_tool_use_block_message.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_hook_pre_tool_use_block_message.py`:

```python
import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.hooks import pre_tool_use


def test_deny_envelope_contains_rule_path_and_hint(monkeypatch, tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / ".mnemo").mkdir()
    (vault / "shared" / "feedback").mkdir(parents=True)
    rule_path = vault / "shared" / "feedback" / "no-curl-example-com.md"
    rule_path.write_text(
        "---\n"
        "name: Block curl example.com\n"
        "description: blocks curl to example.com\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/demo/memory/foo.md\n"
        "tags:\n"
        "  - demo\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  deny_pattern: 'curl .*example\\.com'\n"
        "  reason: 'no external fetch'\n"
        "---\n"
        "Body.\n"
    )

    monkeypatch.setenv("MNEMO_VAULT", str(vault))
    # minimal config enabling enforcement
    (vault / "mnemo.config.json").write_text(json.dumps({
        "enforcement": {"enabled": True},
        "enrichment": {"enabled": False},
    }))

    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "curl https://example.com"},
        "cwd": str(tmp_path),
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    pre_tool_use.main()

    out = capsys.readouterr().out
    assert out, "hook should have emitted a deny envelope"
    envelope = json.loads(out)
    reason = envelope["hookSpecificOutput"]["permissionDecisionReason"]
    assert "no external fetch" in reason
    assert "no-curl-example-com.md" in reason
    assert "mnemo disable-rule" in reason or "edit the file" in reason.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_hook_pre_tool_use_block_message.py -v`
Expected: FAIL — current reason is bare.

If setup is tricky (config loading, etc.), consult `tests/unit/test_hook_pre_tool_use.py` for the established fixture pattern and mirror it.

- [ ] **Step 3: Add `path` to `EnforceHit`**

Edit `src/mnemo/core/rule_activation/matching.py:23-28`:

```python
@dataclass(frozen=True)
class EnforceHit:
    slug: str
    project: str
    reason: str
    path: str = ""   # NEW — filled by match_bash_enforce when available
```

- [ ] **Step 4: Populate `path` in `match_bash_enforce`**

Edit `src/mnemo/core/rule_activation/matching.py:119-130`. At both `EnforceHit(...)` construction sites:

```python
                    return EnforceHit(
                        slug=slug,
                        project=project,
                        reason=enforce.get("reason", slug),
                        path=rule.get("path", ""),
                    )
```

- [ ] **Step 5: Enrich the deny envelope in the hook**

Edit `src/mnemo/hooks/pre_tool_use.py:68-70,120-131`. Replace the block:

```python
        if enf_enabled and tool_name == _ENFORCE_TOOL:
            command = tool_input.get("command") or ""
            hit = ra.match_bash_enforce(index, project, command)
            if hit is not None:
                _emit_deny(hit)
                ra.log_denial(vault, hit, tool_input)
                return 0
```

And replace `_emit_deny`:

```python
def _emit_deny(hit) -> None:
    try:
        lines = [hit.reason]
        if getattr(hit, "path", ""):
            lines.append(f"Rule: {hit.path}")
            lines.append(f"Fix: edit the file to remove or narrow the enforce block, "
                         f"or run `mnemo disable-rule {hit.slug}`.")
        reason = "\n".join(lines)
        sys.stdout.write(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
        }))
        sys.stdout.flush()
    except Exception:
        pass
```

- [ ] **Step 6: Run test to verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_hook_pre_tool_use_block_message.py -v`
Expected: PASS.

- [ ] **Step 7: Run all hook tests**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_hook_pre_tool_use.py tests/integration/test_hook_pre_tool_use_reflex_e2e.py tests/integration/test_hooks_never_raise.py -v`
Expected: all pass. If any test asserts the exact `permissionDecisionReason` string, it needs updating to account for the new "Rule: …\nFix: …" suffix.

- [ ] **Step 8: Commit**

```bash
git add src/mnemo/core/rule_activation/matching.py \
        src/mnemo/hooks/pre_tool_use.py \
        tests/unit/test_hook_pre_tool_use_block_message.py
git commit -m "feat(hooks): include rule path + disable hint in deny envelope"
```

---

## Task 5: `mnemo disable-rule <slug>` CLI

**Why:** The block message in Task 4 suggests `mnemo disable-rule <slug>`. Make it real. Flips `runtime: false` in the rule's frontmatter without touching its body.

**Files:**
- Create: `src/mnemo/cli/commands/disable_rule.py`
- Modify: `src/mnemo/cli/parser.py`
- Modify: `src/mnemo/cli/commands/__init__.py`
- Test: `tests/cli/test_disable_rule.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_disable_rule.py`:

```python
from pathlib import Path

from mnemo.cli.commands import disable_rule as dr


def test_disable_rule_sets_runtime_false(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    rule = vault / "shared" / "feedback" / "example-rule.md"
    rule.write_text(
        "---\n"
        "name: Example rule\n"
        "description: example\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/demo/memory/foo.md\n"
        "tags:\n"
        "  - demo\n"
        "---\n"
        "Body line 1.\nBody line 2.\n"
    )
    rc = dr.run_disable_rule(vault, slug="example-rule")
    assert rc == 0
    text = rule.read_text()
    assert "runtime: false" in text.split("---", 2)[1]
    assert "Body line 1." in text   # body untouched


def test_disable_rule_unknown_slug_errors(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    (vault / "shared").mkdir()
    rc = dr.run_disable_rule(vault, slug="does-not-exist")
    assert rc != 0
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "not found" in out.lower()


def test_disable_rule_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    rule = vault / "shared" / "feedback" / "x.md"
    rule.write_text(
        "---\nname: X\ndescription: x\ntype: feedback\n"
        "sources:\n  - bots/a/memory/b.md\n"
        "tags:\n  - t\n"
        "runtime: false\n"
        "---\nBody\n"
    )
    rc = dr.run_disable_rule(vault, slug="x")
    assert rc == 0
    # runtime: false appears exactly once
    assert rule.read_text().count("runtime: false") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/cli/test_disable_rule.py -v`
Expected: FAIL — module and function don't exist.

- [ ] **Step 3: Implement the command**

Create `src/mnemo/cli/commands/disable_rule.py`:

```python
"""`mnemo disable-rule <slug>` — flip runtime: false on a rule's frontmatter."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mnemo.cli.parser import register
from mnemo.core import config, paths
from mnemo.core.filters import derive_rule_slug, parse_frontmatter


def _find_rule_file(vault_root: Path, slug: str) -> Path | None:
    for md in (vault_root / "shared").rglob("*.md"):
        try:
            text = md.read_text()
        except OSError:
            continue
        fm, _body = parse_frontmatter(text)
        if fm is None:
            continue
        if derive_rule_slug(fm, md.stem) == slug:
            return md
    return None


def run_disable_rule(vault_root: Path, *, slug: str) -> int:
    md = _find_rule_file(vault_root, slug)
    if md is None:
        print(f"error: rule not found for slug {slug!r}", file=sys.stderr)
        return 2
    text = md.read_text()
    if not text.startswith("---\n"):
        print(f"error: {md} has no frontmatter", file=sys.stderr)
        return 2
    end = text.find("\n---\n", 4)
    if end == -1:
        print(f"error: {md} frontmatter not closed", file=sys.stderr)
        return 2
    fm_block = text[4:end]
    body = text[end + 5 :]
    if "\nruntime: false" in "\n" + fm_block or fm_block.startswith("runtime: false"):
        print(f"already disabled: {md.relative_to(vault_root)}")
        return 0
    # Remove any existing `runtime: true` line; then append `runtime: false`.
    fm_lines = [ln for ln in fm_block.splitlines() if ln.strip() != "runtime: true"]
    fm_lines.append("runtime: false")
    new_text = "---\n" + "\n".join(fm_lines) + "\n---\n" + body
    md.write_text(new_text)
    print(f"disabled: {md.relative_to(vault_root)}")
    return 0


@register("disable-rule")
def _cmd(ns: argparse.Namespace) -> int:
    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    return run_disable_rule(vault, slug=ns.slug)
```

- [ ] **Step 4: Register the subcommand**

Edit `src/mnemo/cli/parser.py`. Find the `_build_parser` function and add, near other `sub.add_parser` calls:

```python
    disable = sub.add_parser("disable-rule", help="set runtime: false on a rule's frontmatter by slug")
    disable.add_argument("slug", help="rule slug (from the block message or `mnemo list-enforced`)")
```

Edit `src/mnemo/cli/commands/__init__.py` to import the new module so `@register` fires at import time:

```python
from mnemo.cli.commands import disable_rule as _disable_rule   # noqa: F401
```

If `__init__.py` follows a pattern like "import all commands", place the import alongside siblings.

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/cli/test_disable_rule.py -v`
Expected: all 3 PASS.

- [ ] **Step 6: Smoke test end-to-end**

Run: `PYTHONPATH=$(pwd)/src python -m mnemo disable-rule --help`
Expected: help text for the new command. Then on a disposable vault, run `mnemo disable-rule some-slug` and confirm the `.md` file gained `runtime: false`.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/commands/disable_rule.py \
        src/mnemo/cli/parser.py \
        src/mnemo/cli/commands/__init__.py \
        tests/cli/test_disable_rule.py
git commit -m "feat(cli): add mnemo disable-rule <slug> to flip runtime: false"
```

---

## Task 6: `mnemo list-enforced` CLI

**Why:** Users need a one-shot audit of all rules that can block tool calls. Currently the only way is `grep -r "^enforce:" ~/mnemo/`.

**Files:**
- Create: `src/mnemo/cli/commands/list_enforced.py`
- Modify: `src/mnemo/cli/parser.py`
- Modify: `src/mnemo/cli/commands/__init__.py`
- Test: `tests/cli/test_list_enforced.py`

- [ ] **Step 1: Write the failing test**

Create `tests/cli/test_list_enforced.py`:

```python
from pathlib import Path

from mnemo.cli.commands import list_enforced as le


def _make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "shared" / "feedback").mkdir(parents=True)
    (vault / "shared" / "project").mkdir(parents=True)
    (vault / "shared" / "feedback" / "blocks-curl.md").write_text(
        "---\n"
        "name: Block curl example.com\n"
        "description: x\n"
        "type: feedback\n"
        "sources:\n"
        "  - bots/a/memory/b.md\n"
        "tags:\n"
        "  - demo\n"
        "enforce:\n"
        "  tool: Bash\n"
        "  deny_pattern: 'curl .*example\\.com'\n"
        "  reason: 'blocked'\n"
        "---\n"
        "Body.\n"
    )
    (vault / "shared" / "feedback" / "plain-rule.md").write_text(
        "---\nname: plain\ndescription: x\ntype: feedback\n"
        "sources:\n  - bots/a/memory/b.md\ntags:\n  - demo\n---\nBody\n"
    )
    return vault


def test_list_enforced_prints_rules(tmp_path: Path, capsys):
    vault = _make_vault(tmp_path)
    rc = le.run_list_enforced(vault)
    assert rc == 0
    out = capsys.readouterr().out
    assert "blocks-curl.md" in out
    assert "curl .*example" in out
    assert "plain-rule.md" not in out


def test_list_enforced_empty_vault(tmp_path: Path, capsys):
    vault = tmp_path / "vault"
    (vault / "shared").mkdir(parents=True)
    rc = le.run_list_enforced(vault)
    assert rc == 0
    out = capsys.readouterr().out
    assert "no enforce" in out.lower() or out.strip() == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/cli/test_list_enforced.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement the command**

Create `src/mnemo/cli/commands/list_enforced.py`:

```python
"""`mnemo list-enforced` — audit rules that can hard-block tool calls."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.parser import register
from mnemo.core import config, paths
from mnemo.core.filters import parse_frontmatter


def _iter_enforced(vault_root: Path):
    for md in sorted((vault_root / "shared").rglob("*.md")):
        try:
            text = md.read_text()
        except OSError:
            continue
        fm, _body = parse_frontmatter(text)
        if not isinstance(fm, dict):
            continue
        enforce = fm.get("enforce")
        if isinstance(enforce, dict) and enforce:
            yield md, fm, enforce


def run_list_enforced(vault_root: Path) -> int:
    any_rule = False
    for md, fm, enforce in _iter_enforced(vault_root):
        any_rule = True
        rel = md.relative_to(vault_root)
        tool = enforce.get("tool", "?")
        dp = enforce.get("deny_pattern") or enforce.get("deny_patterns")
        dc = enforce.get("deny_command") or enforce.get("deny_commands")
        reason = enforce.get("reason", "")
        print(f"{rel}")
        print(f"  tool: {tool}")
        if dp:
            print(f"  deny_pattern: {dp}")
        if dc:
            print(f"  deny_command: {dc}")
        if reason:
            print(f"  reason: {reason}")
        print()
    if not any_rule:
        print("no enforce blocks found")
    return 0


@register("list-enforced")
def _cmd(_ns: argparse.Namespace) -> int:
    cfg = config.load_config()
    vault = paths.vault_root(cfg)
    return run_list_enforced(vault)
```

- [ ] **Step 4: Register the subcommand**

Edit `src/mnemo/cli/parser.py`:

```python
    sub.add_parser("list-enforced", help="audit rules with enforce blocks (can block tool calls)")
```

Edit `src/mnemo/cli/commands/__init__.py`:

```python
from mnemo.cli.commands import list_enforced as _list_enforced  # noqa: F401
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/cli/test_list_enforced.py -v`
Expected: both PASS.

- [ ] **Step 6: Smoke test**

Run: `PYTHONPATH=$(pwd)/src python -m mnemo list-enforced`
Expected: lists all enforce rules in the actual vault, with path + trigger + reason. Useful sanity check before committing.

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/cli/commands/list_enforced.py \
        src/mnemo/cli/parser.py \
        src/mnemo/cli/commands/__init__.py \
        tests/cli/test_list_enforced.py
git commit -m "feat(cli): add mnemo list-enforced for auditing deny rules"
```

---

## Task 7: Tighten extractor prompt — default to `enrich`, not `enforce`

**Why:** R1 — the LLM has latitude to emit `enforce:` for any command in backticks. Narrow the guidance so it only emits `enforce:` when the briefing contains explicit "block this" language.

**Files:**
- Modify: `src/mnemo/core/extract/prompts/templates/system_feedback.py:50-67`
- Test: `tests/unit/test_extract_prompts_enforce_guidance.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_extract_prompts_enforce_guidance.py`:

```python
from mnemo.core.extract.prompts.templates.system_feedback import SYSTEM_FEEDBACK


def test_prompt_names_the_blocking_intent_requirement():
    # The prompt MUST tell the LLM that enforce: requires the source briefing
    # to use explicit blocking language (never allow / always refuse / hook
    # should block). Mentioning a command in backticks is NOT enough.
    assert "explicit" in SYSTEM_FEEDBACK.lower()
    assert any(kw in SYSTEM_FEEDBACK.lower() for kw in ("never allow", "always refuse", "hook should"))


def test_prompt_requires_deny_pattern_qualifier():
    # The prompt MUST require deny_pattern when deny_command is used, to
    # match the schema validator from Task 1.
    assert "deny_pattern" in SYSTEM_FEEDBACK
    assert "qualifier" in SYSTEM_FEEDBACK.lower() or "narrow" in SYSTEM_FEEDBACK.lower()


def test_prompt_defaults_to_omission():
    # "When in doubt, omit" (or equivalent) must be preserved.
    assert "omit" in SYSTEM_FEEDBACK.lower()
```

- [ ] **Step 2: Check the current prompt symbol name**

Run: `grep -n "^SYSTEM_FEEDBACK\|^_SYSTEM\|^SYSTEM =\|= (" src/mnemo/core/extract/prompts/templates/system_feedback.py | head`
Expected: the module-level string binding. If the name differs, adjust the test import.

- [ ] **Step 3: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_extract_prompts_enforce_guidance.py -v`
Expected: `test_prompt_names_the_blocking_intent_requirement` and `test_prompt_requires_deny_pattern_qualifier` FAIL.

- [ ] **Step 4: Rewrite the enforce guidance**

Edit `src/mnemo/core/extract/prompts/templates/system_feedback.py:50-67`. Replace the block:

```python
    "## Optional activation metadata\n\n"
    "Default to emitting NO `enforce` block. A missing enforce block is always "
    "safe; a wrong one blocks real work. Emit `enforce` ONLY when the rule "
    "body contains explicit blocking intent — phrases like 'never allow', "
    "'always refuse', 'the hook should block', 'hard-fail the tool'. The mere "
    "presence of a command in backticks (e.g. `git push`) is NOT sufficient; "
    "most such mentions are advisory, and the hook has no way to verify the "
    "surrounding context (is this a stacked PR? is force enabled?). When in "
    "doubt, omit.\n\n"
    "If you do emit `enforce`, use this exact shape: "
    "`{\"tool\": \"Bash\", \"deny_pattern\": \"<regex>\", \"reason\": \"<short>\"}`. "
    "A bare `deny_command` is rejected by the schema validator — it must be "
    "paired with a `deny_pattern` regex that narrows the match. Example for "
    "\"never commit with Co-Authored-By trailers\": "
    "`{\"tool\": \"Bash\", \"deny_command\": [\"git commit\"], "
    "\"deny_pattern\": \"Co-Authored-By\", \"reason\": \"no coauthor trailers\"}`. "
    "The pattern is the qualifier that makes the block specific and verifiable.\n\n"
    "Separately, if the rule is advisory about code in specific files (e.g. "
    # … keep the existing activates_on guidance as-is …
```

- [ ] **Step 5: Run test to verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_extract_prompts_enforce_guidance.py -v`
Expected: all 3 PASS.

- [ ] **Step 6: Update few-shot examples if they show bare deny_command**

Run: `grep -n "deny_command" src/mnemo/core/extract/prompts/templates/few_shot_feedback.py src/mnemo/core/extract/prompts/templates/schema.py`
If any few-shot example emits `deny_command` without a paired `deny_pattern`, update the example to include one (the schema example at `schema.py:23` should mention the qualifier rule).

- [ ] **Step 7: Run full extraction test suite**

Run: `PYTHONPATH=$(pwd)/src pytest tests/unit/test_extract_inbox.py tests/unit/test_extract_inbox_v0_3.py tests/unit/test_extract_promote.py tests/unit/test_extract_prompts_enforce_guidance.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/mnemo/core/extract/prompts/templates/system_feedback.py \
        src/mnemo/core/extract/prompts/templates/few_shot_feedback.py \
        src/mnemo/core/extract/prompts/templates/schema.py \
        tests/unit/test_extract_prompts_enforce_guidance.py
git commit -m "feat(extract): tighten enforce guidance, require deny_pattern qualifier"
```

---

## Task 8: Version bump + changelog

**Why:** Schema-breaking change (Task 1 rejects previously-valid rules). Minor bump signals the behavior shift.

**Files:**
- Modify: `pyproject.toml:7`
- Modify: `CHANGELOG.md` (create if absent)

- [ ] **Step 1: Bump version**

Edit `pyproject.toml`:

```toml
version = "0.11.0"
```

(From whatever the current `version = "..."` is — run `grep '^version' pyproject.toml` first to confirm the starting point.)

- [ ] **Step 2: Add changelog entry**

If `CHANGELOG.md` exists, prepend:

```markdown
## 0.11.0 — 2026-04-23

### Breaking

- **Rule schema:** `enforce.deny_command` now requires a paired `deny_pattern` regex.
  Bare `deny_command: "git push"` is rejected at index load. Run `mnemo doctor`
  to surface affected rules; fix by adding a `deny_pattern` that narrows the match,
  or remove the enforce block.

### Added

- `mnemo list-enforced` — audit all rules that can block tool calls.
- `mnemo disable-rule <slug>` — flip `runtime: false` on a rule without
  touching its body.
- PreToolUse hook deny envelope now includes rule path and disable hint.

### Changed

- Auto-promoted pages have their `enforce:` block stripped (flagged with
  `promoted_without_enforce: true` + body notice). Manual promotion and
  hand-authored rules are unaffected.
- Extractor prompt tightened: `enforce:` emission now requires explicit
  blocking intent in the source briefing, not just a command in backticks.

### Migration

Rules with bare `deny_command` will fail to load. After upgrade, run
`mnemo fix && mnemo doctor` — the "rules" check lists every offender by path.
Fix options, per rule:

1. Add a `deny_pattern` that narrows the block (preferred).
2. Remove the `enforce:` block — the rule stays advisory.
3. Run `mnemo disable-rule <slug>` to set `runtime: false`.
```

If `CHANGELOG.md` does not exist, create it with the above entry as the first section.

- [ ] **Step 3: Run the full test suite once more**

Run: `PYTHONPATH=$(pwd)/src pytest -q`
Expected: all pass. If any test reads the version string, it may need updating.

- [ ] **Step 4: Sanity-check the dogfood vault**

Run: `PYTHONPATH=$(pwd)/src python -m mnemo fix && PYTHONPATH=$(pwd)/src python -m mnemo doctor`
Expected: the real vault either loads clean, or doctor surfaces any rules that need attention. Fix the incident rule (`shared/feedback/stacked-prs-retarget-on-merge.md`) was already patched manually on 2026-04-23; doctor may find others.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "release: v0.11.0 enforce safety rails"
```

---

## Execution order & dependencies

Tasks 1 and 2 are independent. Task 4 depends on Task 2 (needs `path` in entry). Task 5 is referenced by Task 4's block message (the hint mentions `mnemo disable-rule`) but they can be developed in parallel — the hint text is free-form.

Recommended order: **T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8**.

If dispatching subagents in parallel via `superpowers:subagent-driven-development`, safe parallel batches:

- Batch A: T1 + T3 + T7 (parsing / rendering / prompt — no overlap)
- Batch B: T2 (infra for T4)
- Batch C: T4 + T5 + T6 (hook + both CLIs — touch disjoint files except `parser.py` and `commands/__init__.py`; serialize those two edits or hand them to the same agent)
- Batch D: T8 (release)

---

## Self-review

**Spec coverage:**
- C1 (extractor default `enrich`) → T7 ✓
- C2 (schema requires qualifier) → T1 ✓
- C3 (auto-promotion strips enforce) → T3 ✓
- C4 (block message w/ path + hint) → T4 (+ T2 infra, + T5 provides `disable-rule` backing) ✓
- C5 (`list-enforced` CLI) → T6 ✓
- C6 (`disable-rule`, marked optional in spec) → T5 ✓ (promoted to full task since the block message hints at it)

**Placeholder scan:** no "TBD", "similar to", or bare "implement X" steps; every code change has code; every test has the assertion text; every command has expected behavior.

**Type consistency:** `EnforceHit.path` is added in T4 and used in T4's hook code. `ExtractedPage.enforce` shape is consistent across T3 (uses dict). `parse_enforce_block` is the public symbol per T1's import check step. `run_disable_rule(vault_root, *, slug)` and `run_list_enforced(vault_root)` signatures match between their test files and implementations.

---

**Plan complete and saved to `docs/superpowers/plans/2026-04-23-enforce-safety-rails.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — fresh subagent per task + two-stage review, fast iteration, isolated context per step.

**2. Inline Execution** — execute tasks in this session with checkpoints for review.

**Which approach?**
