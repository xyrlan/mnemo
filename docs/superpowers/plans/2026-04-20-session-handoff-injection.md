# Session Handoff Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Inject the most recent briefing for the canonical project agent into the SessionStart `mnemo://v1` envelope, and add per-call LLM token instrumentation so `mnemo telemetry` can report real cost.

**Architecture:** Adds `agent.resolve_canonical_agent` (follows `.git` worktree pointers) used by both the briefing **writer** (SessionEnd) and **reader** (SessionStart) so all worktrees of a repo share one briefing pool. SessionStart hook gains a new section appended to the existing envelope. `llm.call()` already returns `input_tokens`/`output_tokens`; new `access_log.record_llm_call` helper persists them to the same `mcp-access-log.jsonl` consumed by `mnemo telemetry`.

**Tech Stack:** Python 3.13 stdlib only (mnemo's standing constraint), pytest, existing JSONL telemetry conventions.

---

## File Structure

**Create:**
- `tests/unit/test_resolve_canonical_agent.py` — worktree/main/no-git resolution tests
- `tests/unit/test_briefing_picker.py` — most-recent-by-date selection
- `tests/unit/test_session_start_briefing_injection.py` — envelope composition
- `tests/unit/test_llm_telemetry.py` — `record_llm_call` writes correct JSON
- `tests/unit/test_telemetry_cost.py` — aggregator handles new tool entries + cost calc
- `tests/unit/test_migrate_worktree_briefings.py` — migration command
- `src/mnemo/core/pricing.py` — per-model USD-per-MTok table
- `src/mnemo/cli/commands/migrate_worktree_briefings.py` — one-shot migration CLI

**Modify:**
- `src/mnemo/core/agent.py` — add `resolve_canonical_agent`
- `src/mnemo/core/briefing.py` — add `pick_latest_briefing` + use canonical agent in writer
- `src/mnemo/hooks/session_end.py` — switch briefing writer to canonical agent
- `src/mnemo/hooks/session_start.py` — append briefing section + envelope-size logging
- `src/mnemo/core/mcp/access_log.py` — add `record_llm_call` and `record_session_start_inject` helpers
- `src/mnemo/core/mcp/access_log_summary.py` — accept new tool entries; aggregate by purpose; cost calc
- `src/mnemo/cli/commands/telemetry.py` — render cost section
- `src/mnemo/core/briefing.py:generate_session_briefing` — log LLM call after success
- `src/mnemo/core/extract/__init__.py` — log LLM call after each consolidation invocation
- `README.md`, `CHANGELOG.md` — feature documentation

---

## Task 1: `resolve_canonical_agent` — worktree-aware agent resolution

**Files:**
- Create: `tests/unit/test_resolve_canonical_agent.py`
- Modify: `src/mnemo/core/agent.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_resolve_canonical_agent.py
"""Worktree-aware canonical agent resolution."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core import agent


def test_canonical_agent_main_repo(tmp_path: Path) -> None:
    """A normal repo (.git is a directory) resolves to its own basename."""
    repo = tmp_path / "myproject"
    repo.mkdir()
    (repo / ".git").mkdir()
    info = agent.resolve_canonical_agent(str(repo))
    assert info.name == "myproject"
    assert info.repo_root == str(repo.resolve())
    assert info.has_git is True


def test_canonical_agent_worktree_resolves_to_main(tmp_path: Path) -> None:
    """A worktree (.git is a file with `gitdir:` pointer) resolves to the main repo's basename."""
    main_repo = tmp_path / "myproject"
    main_repo.mkdir()
    git_dir = main_repo / ".git"
    git_dir.mkdir()
    worktrees_dir = git_dir / "worktrees" / "feature-x"
    worktrees_dir.mkdir(parents=True)
    (worktrees_dir / "commondir").write_text("../..\n")  # points back to git_dir

    worktree = tmp_path / "myproject-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {worktrees_dir}\n")

    info = agent.resolve_canonical_agent(str(worktree))
    assert info.name == "myproject"
    assert info.repo_root == str(main_repo.resolve())


def test_canonical_agent_no_git_falls_back(tmp_path: Path) -> None:
    """When no .git is found, falls back to resolve_agent (basename of cwd)."""
    plain = tmp_path / "plainfolder"
    plain.mkdir()
    info = agent.resolve_canonical_agent(str(plain))
    assert info.name == "plainfolder"
    assert info.has_git is False


def test_canonical_agent_malformed_git_file_falls_back(tmp_path: Path) -> None:
    """A `.git` file with no parseable `gitdir:` line degrades to current basename."""
    repo = tmp_path / "weird"
    repo.mkdir()
    (repo / ".git").write_text("not a real gitdir pointer\n")
    info = agent.resolve_canonical_agent(str(repo))
    assert info.name == "weird"
    assert info.has_git is True


def test_canonical_agent_missing_commondir_falls_back(tmp_path: Path) -> None:
    """When .git points to a worktree dir without `commondir`, fall back to current basename."""
    repo = tmp_path / "broken"
    repo.mkdir()
    fake_target = tmp_path / "fake-gitdir"
    fake_target.mkdir()
    (repo / ".git").write_text(f"gitdir: {fake_target}\n")
    info = agent.resolve_canonical_agent(str(repo))
    assert info.name == "broken"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_resolve_canonical_agent.py -v
```

Expected: 5 FAIL with `AttributeError: module 'mnemo.core.agent' has no attribute 'resolve_canonical_agent'`.

- [ ] **Step 3: Implement `resolve_canonical_agent`**

Add to `src/mnemo/core/agent.py` (after the existing `resolve_agent`):

```python
def _read_gitdir_pointer(git_file: Path) -> Path | None:
    """Parse a `.git` file's `gitdir: <path>` line. Returns the resolved path or None."""
    try:
        text = git_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("gitdir:"):
            target = line[len("gitdir:"):].strip()
            if not target:
                return None
            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = (git_file.parent / target_path).resolve()
            return target_path
    return None


def _resolve_common_dir(worktree_gitdir: Path) -> Path | None:
    """Given a worktree's gitdir (e.g. .git/worktrees/feature-x), return the main repo root.

    Reads `<worktree_gitdir>/commondir` (relative path back to the main .git dir),
    then returns its parent (which is the main repo root). Returns None on any failure.
    """
    commondir_file = worktree_gitdir / "commondir"
    if not commondir_file.is_file():
        return None
    try:
        rel = commondir_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not rel:
        return None
    common_git_dir = (worktree_gitdir / rel).resolve()
    # common_git_dir is the main repo's `.git` directory; its parent is the repo root.
    if common_git_dir.name == ".git":
        return common_git_dir.parent
    # Defensive: some setups point directly at the repo root.
    return common_git_dir


def resolve_canonical_agent(cwd: str) -> AgentInfo:
    """Like `resolve_agent`, but follows `.git` worktree pointers to the main repo.

    For a worktree at `~/proj-feature-x` whose `.git` file points back to
    `~/proj/.git/worktrees/feature-x`, returns AgentInfo(name="proj", ...).

    Falls back to `resolve_agent(cwd)` for: missing `.git`, malformed `.git` file,
    missing `commondir`, or any I/O error during resolution.
    """
    start = Path(cwd) if cwd else Path.cwd()
    git_root = _find_git_root(start)
    if git_root is None:
        return resolve_agent(cwd)
    git_marker = git_root / ".git"
    # Main repo: .git is a directory. Already canonical.
    if git_marker.is_dir():
        return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root.resolve()), has_git=True)
    # Worktree: .git is a file pointing to the worktree's gitdir under the main repo.
    if git_marker.is_file():
        worktree_gitdir = _read_gitdir_pointer(git_marker)
        if worktree_gitdir is None:
            return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root.resolve()), has_git=True)
        canonical_root = _resolve_common_dir(worktree_gitdir)
        if canonical_root is None:
            return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root.resolve()), has_git=True)
        return AgentInfo(name=_sanitize(canonical_root.name), repo_root=str(canonical_root), has_git=True)
    # Should not happen — _find_git_root only returns dirs whose `.git` exists.
    return resolve_agent(cwd)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_resolve_canonical_agent.py -v
```

Expected: 5 PASS.

- [ ] **Step 5: Run full suite to verify no regression**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green (the plugin_manifest tests are pre-existing failures from the deleted `.claude-plugin/` files, unrelated to this work).

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/agent.py tests/unit/test_resolve_canonical_agent.py
git commit -m "feat(agent): resolve_canonical_agent follows worktree .git pointer

Adds a new resolver that returns the main repo's basename even when called
from a worktree, by parsing .git's gitdir pointer + commondir. Falls back
to resolve_agent on any failure. Foundation for shared briefing pool
across worktrees."
```

---

## Task 2: `pick_latest_briefing` — most-recent briefing picker

**Files:**
- Create: `tests/unit/test_briefing_picker.py`
- Modify: `src/mnemo/core/briefing.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_briefing_picker.py
"""Briefing picker — selects the most recent briefing for an agent."""
from __future__ import annotations

import os
from pathlib import Path

from mnemo.core import briefing


def _write_briefing(dir_path: Path, session_id: str, *, date: str, body: str = "body") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    p = dir_path / f"{session_id}.md"
    p.write_text(
        "---\n"
        "type: briefing\n"
        f"agent: testagent\n"
        f"session_id: {session_id}\n"
        f"date: {date}\n"
        "duration_minutes: 30\n"
        "---\n\n"
        f"# Briefing — testagent — {session_id}\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return p


def test_picker_returns_none_when_no_briefings(tmp_path: Path) -> None:
    vault = tmp_path
    result = briefing.pick_latest_briefing(vault, agent_name="ghost")
    assert result is None


def test_picker_returns_only_briefing(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    p = _write_briefing(sessions_dir, "abc123", date="2026-04-19")
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.path == p
    assert result.frontmatter["session_id"] == "abc123"
    assert result.body.startswith("# Briefing — testagent — abc123")


def test_picker_selects_most_recent_date(tmp_path: Path) -> None:
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    _write_briefing(sessions_dir, "old", date="2026-04-10")
    _write_briefing(sessions_dir, "newer", date="2026-04-19")
    _write_briefing(sessions_dir, "middle", date="2026-04-15")
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.frontmatter["session_id"] == "newer"


def test_picker_breaks_tie_by_session_id(tmp_path: Path) -> None:
    """When two briefings share the same date, prefer the higher (lexicographic) session_id."""
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    _write_briefing(sessions_dir, "aaaa", date="2026-04-19")
    _write_briefing(sessions_dir, "zzzz", date="2026-04-19")
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.frontmatter["session_id"] == "zzzz"


def test_picker_falls_back_to_mtime_when_date_missing(tmp_path: Path) -> None:
    """A briefing without a parseable date is ranked by file mtime."""
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True)
    older = sessions_dir / "older.md"
    older.write_text("# no frontmatter\nbody\n", encoding="utf-8")
    os.utime(older, (1000, 1000))
    newer = sessions_dir / "newer.md"
    newer.write_text("# no frontmatter\nbody\n", encoding="utf-8")
    os.utime(newer, (2000, 2000))
    result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
    assert result is not None
    assert result.path == newer


def test_picker_skips_unreadable_briefings(tmp_path: Path) -> None:
    """A briefing that raises on read does not crash the picker."""
    sessions_dir = tmp_path / "bots" / "myagent" / "briefings" / "sessions"
    _write_briefing(sessions_dir, "good", date="2026-04-15")
    bad = sessions_dir / "bad.md"
    bad.write_text("---\ndate: 2026-04-19\n---\nbody\n", encoding="utf-8")
    bad.chmod(0o000)
    try:
        result = briefing.pick_latest_briefing(tmp_path, agent_name="myagent")
        assert result is not None
        assert result.frontmatter["session_id"] == "good"
    finally:
        bad.chmod(0o644)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_briefing_picker.py -v
```

Expected: all 6 FAIL with `AttributeError: module 'mnemo.core.briefing' has no attribute 'pick_latest_briefing'`.

- [ ] **Step 3: Implement `pick_latest_briefing`**

Add to `src/mnemo/core/briefing.py` (at end of file, before any `if __name__` block):

```python
from dataclasses import dataclass
import re

_FRONTMATTER_RE = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)


@dataclass(frozen=True)
class BriefingRecord:
    path: Path
    frontmatter: dict
    body: str


def _parse_briefing_file(path: Path) -> BriefingRecord | None:
    """Read and parse a briefing markdown file. Returns None on any I/O or parse error."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm: dict = {}
    body = text
    m = _FRONTMATTER_RE.match(text)
    if m:
        for line in m.group(1).splitlines():
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip()
        body = text[m.end():]
    return BriefingRecord(path=path, frontmatter=fm, body=body)


def pick_latest_briefing(vault_root: Path, agent_name: str) -> BriefingRecord | None:
    """Return the most recent briefing for ``agent_name``, or None if there are none.

    Ordering: frontmatter ``date`` (ISO YYYY-MM-DD) descending, tie-break by
    ``session_id`` lexicographic descending. Files without a parseable date
    fall back to file mtime — they sort below any dated briefing.
    """
    sessions_dir = vault_root / "bots" / agent_name / "briefings" / "sessions"
    if not sessions_dir.is_dir():
        return None

    records: list[tuple[tuple, BriefingRecord]] = []
    for md in sessions_dir.glob("*.md"):
        rec = _parse_briefing_file(md)
        if rec is None:
            continue
        date = rec.frontmatter.get("date", "")
        session_id = rec.frontmatter.get("session_id", md.stem)
        # Sort key: (has_date, date, session_id, mtime). has_date=1 outranks 0.
        if date:
            key = (1, date, session_id, 0.0)
        else:
            try:
                mtime = md.stat().st_mtime
            except OSError:
                mtime = 0.0
            key = (0, "", "", mtime)
        records.append((key, rec))

    if not records:
        return None
    records.sort(key=lambda kv: kv[0], reverse=True)
    return records[0][1]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_briefing_picker.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/briefing.py tests/unit/test_briefing_picker.py
git commit -m "feat(briefing): pick_latest_briefing selects most recent for an agent

Returns a BriefingRecord (path + parsed frontmatter + body) for the most
recent briefing under bots/<agent>/briefings/sessions/, ordered by
frontmatter date desc with session_id tiebreak, falling back to mtime
for briefings without a parseable date."
```

---

## Task 3: Switch briefing writer to canonical agent

**Files:**
- Modify: `src/mnemo/hooks/session_end.py:_maybe_schedule_briefing`
- Modify: `src/mnemo/core/briefing.py:generate_session_briefing` (use canonical agent path)
- Test: `tests/unit/test_session_end_briefing_canonical.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_session_end_briefing_canonical.py
"""SessionEnd writes briefings under the canonical agent dir, even from worktrees."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.hooks import session_end


def _make_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """Create a `proj` main repo + `proj-feature-x` worktree. Return (main, worktree)."""
    main = tmp_path / "proj"
    main.mkdir()
    git_dir = main / ".git"
    git_dir.mkdir()
    wt_gitdir = git_dir / "worktrees" / "feature-x"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")

    worktree = tmp_path / "proj-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n")
    return main, worktree


def test_briefing_path_uses_canonical_agent_from_worktree(tmp_path: Path) -> None:
    """When SessionEnd fires from a worktree, briefing goes under the main repo's agent dir."""
    main, worktree = _make_worktree(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"vaultRoot": str(vault), "briefings": {"enabled": True}}

    captured: dict[str, object] = {}

    def fake_spawn(jsonl_path: Path, agent: str) -> None:
        captured["jsonl_path"] = jsonl_path
        captured["agent"] = agent

    with patch.object(session_end, "_spawn_detached_briefing", fake_spawn):
        # Mock the transcript path lookup to return a fake existing path.
        with patch.object(session_end, "_resolve_session_jsonl_path", return_value=tmp_path / "fake.jsonl"):
            (tmp_path / "fake.jsonl").write_text("{}\n")
            session_end._maybe_schedule_briefing(
                cfg,
                vault,
                agent_name="ignored-old-resolution",
                session_id="abc123",
                cwd=str(worktree),
            )

    assert captured["agent"] == "proj", (
        f"expected canonical agent 'proj', got {captured['agent']!r}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_session_end_briefing_canonical.py -v
```

Expected: FAIL because `_maybe_schedule_briefing` currently passes `agent_name` straight through (the parameter named `ignored-old-resolution` in the test).

- [ ] **Step 3: Modify `_maybe_schedule_briefing` to resolve canonical agent from cwd**

Edit `src/mnemo/hooks/session_end.py:_maybe_schedule_briefing`. Replace its body so it calls `agent.resolve_canonical_agent(cwd)` instead of using the passed `agent_name` for the spawned briefing. Keep the `agent_name` parameter for backwards compatibility with the existing log-writer call elsewhere, but ignore it for the briefing path.

```python
def _maybe_schedule_briefing(
    cfg: dict,
    vault_root,
    agent_name: str,
    *,
    session_id: str,
    cwd: str,
) -> None:
    """Spawn a detached per-session briefing when briefings.enabled=True.

    The briefing's storage agent is the **canonical** agent for the cwd
    (resolves through worktree .git pointers). This unifies briefings from
    main + worktree sessions of the same repo into one pool. The
    ``agent_name`` parameter is kept for signature compatibility and is no
    longer used for briefing path resolution.
    """
    try:
        from mnemo.core import agent as agent_mod
        from mnemo.core import errors as err_mod

        briefings_cfg = cfg.get("briefings") or {}
        if not bool(briefings_cfg.get("enabled", False)):
            return

        jsonl_path = _resolve_session_jsonl_path(session_id, cwd)
        if jsonl_path is None:
            return

        canonical = agent_mod.resolve_canonical_agent(cwd).name

        try:
            _spawn_detached_briefing(jsonl_path, canonical)
        except OSError as exc:
            err_mod.log_error(vault_root, "session_end.briefing.popen", exc)
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_end.briefing", exc)
        except Exception:
            pass
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_session_end_briefing_canonical.py -v
```

Expected: PASS.

- [ ] **Step 5: Run full suite to verify no regression**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/hooks/session_end.py tests/unit/test_session_end_briefing_canonical.py
git commit -m "feat(session_end): briefing writer uses canonical agent

When SessionEnd fires from a worktree, the spawned briefing is written
under bots/<canonical>/briefings/sessions/ rather than the worktree's
own agent dir. Worktrees and the main repo now share one briefing pool."
```

---

## Task 4: SessionStart briefing injection

**Files:**
- Create: `tests/unit/test_session_start_briefing_injection.py`
- Modify: `src/mnemo/hooks/session_start.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_session_start_briefing_injection.py
"""SessionStart appends a [last-briefing ...] section when a briefing exists."""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from mnemo.hooks import session_start


def _write_briefing(vault: Path, agent: str, session_id: str, *, date: str, body: str) -> Path:
    sessions_dir = vault / "bots" / agent / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    p = sessions_dir / f"{session_id}.md"
    p.write_text(
        "---\n"
        "type: briefing\n"
        f"agent: {agent}\n"
        f"session_id: {session_id}\n"
        f"date: {date}\n"
        "duration_minutes: 42\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return p


def test_envelope_includes_briefing_section_when_present(tmp_path: Path) -> None:
    """The injection envelope ends with a [last-briefing ...] block."""
    vault = tmp_path
    _write_briefing(vault, "myproj", "abc123", date="2026-04-19", body="Stopped at line 42")
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    assert "[last-briefing session=abc123 date=2026-04-19 duration_minutes=42]" in payload
    assert "Stopped at line 42" in payload
    assert "[/last-briefing]" in payload
    # Briefing block is the last section.
    assert payload.rstrip().endswith("[/last-briefing]")


def test_envelope_omits_briefing_section_when_no_briefing(tmp_path: Path) -> None:
    """No briefing → no [last-briefing] block."""
    vault = tmp_path
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    assert "[last-briefing" not in payload


def test_envelope_omits_briefing_section_when_disabled(tmp_path: Path) -> None:
    """inject_briefing=False suppresses the section even when a briefing exists."""
    vault = tmp_path
    _write_briefing(vault, "myproj", "abc", date="2026-04-19", body="anything")
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=False,
    )
    assert "[last-briefing" not in payload


def test_envelope_briefing_picked_for_canonical_agent(tmp_path: Path) -> None:
    """The picker reads briefings from the canonical agent dir, not the worktree's."""
    vault = tmp_path
    _write_briefing(vault, "myproj", "real", date="2026-04-19", body="canonical-body")
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    assert "canonical-body" in payload
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_session_start_briefing_injection.py -v
```

Expected: 4 FAIL — `_build_injection_payload` does not accept `inject_briefing`.

- [ ] **Step 3: Extend `_build_injection_payload` to accept `inject_briefing` and append the section**

In `src/mnemo/hooks/session_start.py`:

```python
def _build_injection_payload(
    vault_root: Path,
    current_project: str | None = None,
    inject_briefing: bool = False,
) -> str:
    """Return a structured ``mnemo://v1`` envelope, or '' when there's nothing to inject.

    [keep the existing docstring text]

    When ``inject_briefing`` is True, append a ``[last-briefing ...]`` section
    with the body of the most recent briefing for ``current_project``. The
    block is omitted entirely when no briefing exists or when reading fails.
    """
    # ... existing body up to the topic-list rendering, unchanged ...

    if not local_topics and not universal_topics:
        # Existing early return — but if a briefing is available, we still want
        # to inject it. Build the rest of the lines and decide at the end.
        topic_lines: list[str] = []
    else:
        topic_lines = lines  # use the list built so far

    # NEW: append briefing section
    briefing_block = ""
    if inject_briefing and current_project:
        try:
            from mnemo.core import briefing as briefing_mod
            rec = briefing_mod.pick_latest_briefing(vault_root, current_project)
            if rec is not None:
                fm = rec.frontmatter
                framing = (
                    f"[last-briefing session={fm.get('session_id', rec.path.stem)} "
                    f"date={fm.get('date', '')} "
                    f"duration_minutes={fm.get('duration_minutes', '0')}]"
                ).replace("  ", " ")
                briefing_block = (
                    "\n\n"
                    + framing
                    + "\n"
                    + rec.body.rstrip()
                    + "\n[/last-briefing]"
                )
        except Exception:
            briefing_block = ""

    if not topic_lines and not briefing_block:
        return ""
    return "\n".join(topic_lines) + briefing_block
```

(Refactor note: the existing function early-returns `""` when no topics exist. After this change, that early return is replaced by the conditional at the end — verify the line numbers carefully against the current file.)

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_session_start_briefing_injection.py -v
```

Expected: 4 PASS.

- [ ] **Step 5: Run full suite to verify no regression**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green. Pay special attention to existing `test_session_start*.py` tests — they may need updating if they call `_build_injection_payload` with positional args.

- [ ] **Step 6: Wire `inject_briefing` flag from config in `main()`**

Edit the `main()` function in `session_start.py`. Find the block:

```python
        if cfg.get("injection", {}).get("enabled", False):
            try:
                payload_text = _build_injection_payload(vault, current_project=ainfo.name)
```

Change `ainfo.name` to use the canonical agent (so worktree sessions also pick up the canonical pool's briefing) and wire the new flag:

```python
        if cfg.get("injection", {}).get("enabled", False):
            try:
                canonical_name = agent.resolve_canonical_agent(cwd).name
                inject_briefing = bool(cfg.get("briefings", {}).get("injectLastOnSessionStart", True))
                payload_text = _build_injection_payload(
                    vault,
                    current_project=canonical_name,
                    inject_briefing=inject_briefing,
                )
                if payload_text:
                    _emit_injection(payload_text)
            except Exception as e:
                errors.log_error(vault, "session_start.injection", e)
```

- [ ] **Step 7: Re-run full suite**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/mnemo/hooks/session_start.py tests/unit/test_session_start_briefing_injection.py
git commit -m "feat(session_start): inject [last-briefing] section into envelope

Extends _build_injection_payload with inject_briefing param. When true and
the canonical project has a briefing on disk, appends a verbatim block
with framing line. Wired from briefings.injectLastOnSessionStart config
(default true). Uses canonical agent so worktrees pick up the main pool."
```

---

## Task 5: `record_llm_call` telemetry helper

**Files:**
- Create: `tests/unit/test_llm_telemetry.py`
- Modify: `src/mnemo/core/mcp/access_log.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_llm_telemetry.py
"""record_llm_call writes a structured telemetry entry."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.llm import LLMResponse
from mnemo.core.mcp import access_log


def _read_log(vault: Path) -> list[dict]:
    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def test_record_llm_call_writes_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    response = LLMResponse(
        text="hello",
        total_cost_usd=0.001,
        input_tokens=1234,
        output_tokens=56,
        api_key_source="oauth",
        raw={},
    )
    access_log.record_llm_call(
        tmp_path,
        response,
        purpose="briefing",
        model="claude-haiku-4-5",
        project="myproj",
        agent="myproj",
        elapsed_ms=2345.6,
    )
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "llm.call"
    assert e["purpose"] == "briefing"
    assert e["model"] == "claude-haiku-4-5"
    assert e["project"] == "myproj"
    assert e["agent"] == "myproj"
    assert e["usage"] == {"input_tokens": 1234, "output_tokens": 56}
    assert e["elapsed_ms"] == 2345.6
    assert "timestamp" in e and e["timestamp"].endswith("Z")
    assert e["result_count"] == 1  # so access_log_summary.is_well_formed accepts it


def test_record_llm_call_handles_missing_usage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    response = LLMResponse(
        text="hi",
        total_cost_usd=None,
        input_tokens=None,
        output_tokens=None,
        api_key_source=None,
        raw={},
    )
    access_log.record_llm_call(
        tmp_path,
        response,
        purpose="consolidation:feedback",
        model="claude-haiku-4-5",
        project=None,
        agent="myagent",
        elapsed_ms=100.0,
    )
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["usage"] == {"input_tokens": 0, "output_tokens": 0}


def test_record_session_start_inject_writes_entry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    access_log.record_session_start_inject(
        tmp_path,
        envelope_bytes=4321,
        included_briefing=True,
        project="myproj",
        agent="myproj",
    )
    entries = _read_log(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["tool"] == "session_start.inject"
    assert e["envelope_bytes"] == 4321
    assert e["included_briefing"] is True
    assert e["project"] == "myproj"
    assert e["result_count"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_llm_telemetry.py -v
```

Expected: 3 FAIL — `record_llm_call` and `record_session_start_inject` do not exist.

- [ ] **Step 3: Implement helpers in `src/mnemo/core/mcp/access_log.py`**

Append to the end of `access_log.py`:

```python
from datetime import datetime, timezone

from mnemo.core.llm import LLMResponse


def _utc_iso_z() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_llm_call(
    vault_root: Path,
    response: LLMResponse,
    *,
    purpose: str,
    model: str,
    project: str | None,
    agent: str,
    elapsed_ms: float,
) -> None:
    """Append an `llm.call` entry to mcp-access-log.jsonl. Never raises."""
    entry = {
        "timestamp": _utc_iso_z(),
        "tool": "llm.call",
        "purpose": purpose,
        "model": model,
        "project": project,
        "agent": agent,
        "usage": {
            "input_tokens": int(response.input_tokens or 0),
            "output_tokens": int(response.output_tokens or 0),
        },
        "elapsed_ms": float(elapsed_ms),
        "result_count": 1,
    }
    record(vault_root, entry)


def record_session_start_inject(
    vault_root: Path,
    *,
    envelope_bytes: int,
    included_briefing: bool,
    project: str | None,
    agent: str,
) -> None:
    """Append a `session_start.inject` entry. Never raises."""
    entry = {
        "timestamp": _utc_iso_z(),
        "tool": "session_start.inject",
        "envelope_bytes": int(envelope_bytes),
        "included_briefing": bool(included_briefing),
        "project": project,
        "agent": agent,
        "result_count": 1,
    }
    record(vault_root, entry)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_llm_telemetry.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/mcp/access_log.py tests/unit/test_llm_telemetry.py
git commit -m "feat(telemetry): record_llm_call + record_session_start_inject

Adds two structured-entry helpers on top of access_log.record so callers
of llm.call() can persist input/output token counts and the SessionStart
hook can persist envelope size. Both tagged with result_count=1 so the
existing access_log_summary aggregator accepts them."
```

---

## Task 6: Wire `record_llm_call` into briefing + extraction call sites

**Files:**
- Modify: `src/mnemo/core/briefing.py:generate_session_briefing`
- Modify: `src/mnemo/core/extract/__init__.py` (every `llm.call` invocation)

- [ ] **Step 1: Write integration test for briefing telemetry**

```python
# tests/unit/test_briefing_logs_telemetry.py
"""generate_session_briefing logs an llm.call entry on success."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.core import briefing
from mnemo.core.llm import LLMResponse


def _fake_jsonl(path: Path) -> Path:
    path.write_text(json.dumps({
        "type": "user",
        "timestamp": "2026-04-20T12:00:00Z",
        "message": {
            "content": [{"type": "tool_use", "name": "Edit"}],
        },
    }) + "\n", encoding="utf-8")
    return path


def test_briefing_writes_telemetry_entry(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    cfg = {"vaultRoot": str(vault), "extraction": {"model": "claude-haiku-4-5"}}
    jsonl = _fake_jsonl(tmp_path / "session.jsonl")

    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )

    fake_response = LLMResponse(
        text="briefing body",
        total_cost_usd=0.001,
        input_tokens=500,
        output_tokens=100,
        api_key_source=None,
        raw={},
    )
    with patch("mnemo.core.briefing.llm.call", return_value=fake_response):
        out = briefing.generate_session_briefing(jsonl, agent="myagent", cfg=cfg)
    assert out is not None

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    llm_entries = [e for e in entries if e.get("tool") == "llm.call"]
    assert len(llm_entries) == 1
    e = llm_entries[0]
    assert e["purpose"] == "briefing"
    assert e["agent"] == "myagent"
    assert e["usage"]["input_tokens"] == 500
    assert e["usage"]["output_tokens"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_briefing_logs_telemetry.py -v
```

Expected: FAIL because briefing does not log telemetry.

- [ ] **Step 3: Modify `briefing.generate_session_briefing` to log telemetry**

In `src/mnemo/core/briefing.py`, find the `llm.call(...)` invocation and wrap it with timing + post-log:

```python
import time as _time
# ...
    transcript = flatten_transcript_events(events)
    prompt_text = prompts.build_briefing_prompt(transcript)
    t0 = _time.perf_counter()
    response = llm.call(
        prompt_text,
        system=prompts.BRIEFING_SYSTEM_PROMPT,
        model=model,
        timeout=timeout,
    )
    elapsed_ms = (_time.perf_counter() - t0) * 1000
    try:
        from mnemo.core.mcp import access_log as _al
        _al.record_llm_call(
            vault_root=paths.vault_root(cfg),
            response=response,
            purpose="briefing",
            model=model,
            project=agent,  # briefing's project == its agent name
            agent=agent,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        pass  # telemetry must never break the briefing
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_briefing_logs_telemetry.py -v
```

Expected: PASS.

- [ ] **Step 5: Wire `record_llm_call` into the extraction call site**

There is exactly one `llm.call` invocation in `src/mnemo/core/extract/__init__.py` at line 350, inside a `for chunk in prompts.chunks_for(files, chunk_size)` loop, inside a `for type_name, builder, system_prompt in type_plan` loop. Wrap it with timing + post-log:

```python
import time as _time
# (add at top of file if not already imported)

# Inside the inner for-chunk loop, replace the existing call block:
            prompt_text = builder(chunk, vault_root=vault_root)
            t0 = _time.perf_counter()
            try:
                response = llm.call(
                    prompt_text,
                    system=system_prompt,
                    model=model,
                    timeout=timeout,
                )
            except (llm.LLMSubprocessError, llm.LLMParseError) as exc:
                errors.log_error(vault_root, "extract.chunk", exc)
                summary.failed_chunks += 1
                continue
            elapsed_ms = (_time.perf_counter() - t0) * 1000
            try:
                from mnemo.core.mcp import access_log as _al
                _al.record_llm_call(
                    vault_root=vault_root,
                    response=response,
                    purpose=f"consolidation:{type_name}",
                    model=model,
                    project=None,  # extraction is vault-wide, not per-project
                    agent="(extraction)",
                    elapsed_ms=elapsed_ms,
                )
            except Exception:
                pass
```

The existing `summary.llm_calls += 1` / `summary.total_cost_usd` / etc. block right after the call is unchanged — keep both bookkeeping paths (the in-memory summary is for the extraction's return value; the access-log entry is for cross-run telemetry).

- [ ] **Step 6: Add an integration test for the extraction call site**

```python
# tests/unit/test_extract_logs_telemetry.py
"""extract.run_once writes one llm.call entry per consolidation chunk."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo.core import extract as extract_mod
from mnemo.core.llm import LLMResponse


def _seed_memory_file(vault: Path, agent: str, slug: str, body: str = "x") -> Path:
    d = vault / "bots" / agent / "memory"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"feedback_{slug}.md"
    p.write_text(
        f"---\ntype: feedback\nname: {slug}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return p


def test_extract_logs_llm_call_per_chunk(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "shared" / "_inbox" / "feedback").mkdir(parents=True)
    (vault / "shared" / "feedback").mkdir(parents=True)
    _seed_memory_file(vault, "myagent", "rule-one", body="some feedback content")

    cfg = {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "subprocessTimeout": 60,
            "chunkSize": 5,
        },
    }

    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: cfg)

    fake_response = LLMResponse(
        text='[{"slug": "rule-one", "name": "Rule One", "body": "x", "tags": ["a"]}]',
        total_cost_usd=0.001,
        input_tokens=2000,
        output_tokens=200,
        api_key_source="none",
        raw={},
    )
    with patch("mnemo.core.extract.llm.call", return_value=fake_response):
        # Use whichever public entry point invokes the consolidation loop in
        # extract/__init__.py. As of v0.9 this is `run_once(vault, cfg)`. If
        # the API has shifted, swap accordingly — the assertion below is the
        # contract being tested.
        extract_mod.run_once(vault, cfg)

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    consolidation = [e for e in entries if e.get("tool") == "llm.call"
                     and e.get("purpose", "").startswith("consolidation:")]
    assert len(consolidation) >= 1
    e = consolidation[0]
    assert e["model"] == "claude-haiku-4-5"
    assert e["usage"]["input_tokens"] == 2000
    assert e["usage"]["output_tokens"] == 200
```

Note: confirm `extract_mod.run_once` is the public entry — if not, search `src/mnemo/core/extract/__init__.py` for the function annotated with the "Phase 2+" cluster loop (around line 333) and call that directly.

- [ ] **Step 7: Run full suite**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/mnemo/core/briefing.py src/mnemo/core/extract/__init__.py tests/unit/test_briefing_logs_telemetry.py tests/unit/test_extract_logs_telemetry.py
git commit -m "feat(telemetry): log every llm.call() with usage + elapsed

Wraps llm.call call sites in briefing.py and extract/__init__.py with
timing + record_llm_call. Failures in telemetry never propagate. Lays
the data path for mnemo telemetry to compute real cost."
```

---

## Task 7: Pricing table + telemetry cost aggregation

**Files:**
- Create: `src/mnemo/core/pricing.py`
- Create: `tests/unit/test_pricing.py`
- Create: `tests/unit/test_telemetry_cost.py`
- Modify: `src/mnemo/core/mcp/access_log_summary.py`
- Modify: `src/mnemo/cli/commands/telemetry.py`

- [ ] **Step 1: Write pricing table tests**

```python
# tests/unit/test_pricing.py
"""Per-model USD-per-MTok lookup."""
from __future__ import annotations

import pytest

from mnemo.core import pricing


def test_known_model_prices() -> None:
    assert pricing.estimate_usd("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0) == pytest.approx(1.0)
    assert pricing.estimate_usd("claude-haiku-4-5", input_tokens=0, output_tokens=1_000_000) == pytest.approx(5.0)
    assert pricing.estimate_usd("claude-opus-4-7", input_tokens=1_000_000, output_tokens=1_000_000) > 0


def test_unknown_model_returns_none() -> None:
    assert pricing.estimate_usd("future-model-x", input_tokens=100, output_tokens=100) is None


def test_zero_tokens_zero_cost() -> None:
    assert pricing.estimate_usd("claude-haiku-4-5", input_tokens=0, output_tokens=0) == 0.0
```

- [ ] **Step 2: Run pricing tests to verify they fail**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_pricing.py -v
```

Expected: 3 FAIL — module does not exist.

- [ ] **Step 3: Implement `src/mnemo/core/pricing.py`**

```python
"""Per-model token pricing — USD per million tokens.

Keep this table in sync with Anthropic's published pricing. mnemo is
stdlib-only and does not fetch prices at runtime — bump the table when
prices change."""
from __future__ import annotations

# (input_per_mtok_usd, output_per_mtok_usd)
_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":      (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6":     (3.0, 15.0),
    "claude-opus-4-7":       (15.0, 75.0),
}


def estimate_usd(model: str, *, input_tokens: int, output_tokens: int) -> float | None:
    """Return USD cost for ``input_tokens`` + ``output_tokens`` at ``model``'s rate.

    Returns None for unknown models. Returns 0.0 for zero tokens regardless of model.
    """
    if input_tokens == 0 and output_tokens == 0:
        return 0.0
    rates = _PRICES.get(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    return (input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate


def known_models() -> tuple[str, ...]:
    return tuple(_PRICES.keys())
```

- [ ] **Step 4: Run pricing tests to verify they pass**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_pricing.py -v
```

Expected: 3 PASS.

- [ ] **Step 5: Write telemetry-cost aggregation tests**

```python
# tests/unit/test_telemetry_cost.py
"""access_log_summary aggregates llm.call entries by purpose + estimates cost."""
from __future__ import annotations

from mnemo.core.mcp import access_log_summary


def _llm_entry(purpose: str, model: str, in_tok: int, out_tok: int) -> dict:
    return {
        "tool": "llm.call",
        "purpose": purpose,
        "model": model,
        "project": "myproj",
        "agent": "myagent",
        "usage": {"input_tokens": in_tok, "output_tokens": out_tok},
        "elapsed_ms": 1000.0,
        "result_count": 1,
    }


def test_summary_buckets_llm_calls_by_purpose() -> None:
    entries = [
        _llm_entry("briefing", "claude-haiku-4-5", 10_000, 1_000),
        _llm_entry("briefing", "claude-haiku-4-5", 20_000, 2_000),
        _llm_entry("consolidation:feedback", "claude-haiku-4-5", 50_000, 5_000),
    ]
    summary = access_log_summary.summarize(entries)
    cost = summary["llm_cost"]
    assert cost["by_purpose"]["briefing"]["input_tokens"] == 30_000
    assert cost["by_purpose"]["briefing"]["output_tokens"] == 3_000
    assert cost["by_purpose"]["briefing"]["calls"] == 2
    assert cost["by_purpose"]["consolidation:feedback"]["calls"] == 1
    assert cost["total_input_tokens"] == 80_000
    assert cost["total_output_tokens"] == 8_000
    assert cost["estimated_usd"] > 0


def test_summary_includes_session_start_inject() -> None:
    entries = [
        {
            "tool": "session_start.inject",
            "envelope_bytes": 1234,
            "included_briefing": True,
            "project": "myproj",
            "agent": "myagent",
            "result_count": 1,
        },
        {
            "tool": "session_start.inject",
            "envelope_bytes": 567,
            "included_briefing": False,
            "project": "myproj",
            "agent": "myagent",
            "result_count": 1,
        },
    ]
    summary = access_log_summary.summarize(entries)
    inj = summary["injection_stats"]
    assert inj["total_sessions"] == 2
    assert inj["sessions_with_briefing"] == 1
    assert inj["total_envelope_bytes"] == 1234 + 567


def test_summary_unknown_model_estimated_cost_excluded() -> None:
    """Entries with unknown models contribute tokens but not USD."""
    entries = [_llm_entry("briefing", "future-model-z", 1_000_000, 1_000_000)]
    summary = access_log_summary.summarize(entries)
    cost = summary["llm_cost"]
    assert cost["total_input_tokens"] == 1_000_000
    assert cost["estimated_usd"] == 0.0
    assert "future-model-z" in cost["unknown_models"]
```

- [ ] **Step 6: Run aggregation tests to verify they fail**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_telemetry_cost.py -v
```

Expected: 3 FAIL — `summary["llm_cost"]` and `summary["injection_stats"]` keys do not exist.

- [ ] **Step 7: Extend `access_log_summary.summarize` to compute cost + injection stats**

In `src/mnemo/core/mcp/access_log_summary.py`, modify `summarize`:

```python
from mnemo.core import pricing as _pricing


def summarize(entries: list[dict]) -> dict:
    """[existing docstring]

    Adds two new top-level keys for v0.10:
    - ``llm_cost``: aggregated input/output tokens by purpose + estimated USD.
    - ``injection_stats``: SessionStart envelope size + briefing-inclusion rate.
    """
    by_tool: dict[str, int] = {}
    by_project: dict[str, dict[str, int | float]] = {}
    total = 0
    zero_hits = 0

    # NEW
    cost_by_purpose: dict[str, dict] = {}
    cost_total_in = 0
    cost_total_out = 0
    cost_total_usd = 0.0
    unknown_models: set[str] = set()
    inj_total = 0
    inj_with_briefing = 0
    inj_total_bytes = 0

    for entry in entries:
        if not _is_well_formed(entry):
            continue
        total += 1

        tool = entry["tool"]
        by_tool[tool] = by_tool.get(tool, 0) + 1

        is_zero = int(entry["result_count"]) == 0
        if is_zero:
            zero_hits += 1

        project = entry.get("project") or _NULL_PROJECT_BUCKET
        bucket = by_project.setdefault(project, {"calls": 0, "zero_hit": 0, "zero_hit_rate": 0.0})
        bucket["calls"] = int(bucket["calls"]) + 1
        if is_zero:
            bucket["zero_hit"] = int(bucket["zero_hit"]) + 1

        if tool == "llm.call":
            usage = entry.get("usage") or {}
            in_tok = int(usage.get("input_tokens", 0))
            out_tok = int(usage.get("output_tokens", 0))
            purpose = entry.get("purpose", "(unknown)")
            model = entry.get("model", "")
            p_bucket = cost_by_purpose.setdefault(purpose, {
                "calls": 0, "input_tokens": 0, "output_tokens": 0, "estimated_usd": 0.0,
            })
            p_bucket["calls"] += 1
            p_bucket["input_tokens"] += in_tok
            p_bucket["output_tokens"] += out_tok
            cost_total_in += in_tok
            cost_total_out += out_tok
            usd = _pricing.estimate_usd(model, input_tokens=in_tok, output_tokens=out_tok)
            if usd is None:
                unknown_models.add(model)
            else:
                p_bucket["estimated_usd"] = round(p_bucket["estimated_usd"] + usd, 6)
                cost_total_usd = round(cost_total_usd + usd, 6)

        elif tool == "session_start.inject":
            inj_total += 1
            inj_total_bytes += int(entry.get("envelope_bytes", 0))
            if bool(entry.get("included_briefing", False)):
                inj_with_briefing += 1

    for bucket in by_project.values():
        calls = int(bucket["calls"])
        zh = int(bucket["zero_hit"])
        bucket["zero_hit_rate"] = round(zh / calls, 4) if calls else 0.0

    zero_hit_rate = round(zero_hits / total, 4) if total else 0.0

    return {
        "total_calls": total,
        "zero_hit_calls": zero_hits,
        "zero_hit_rate": zero_hit_rate,
        "by_tool": by_tool,
        "by_project": by_project,
        "llm_cost": {
            "total_input_tokens": cost_total_in,
            "total_output_tokens": cost_total_out,
            "estimated_usd": cost_total_usd,
            "by_purpose": cost_by_purpose,
            "unknown_models": sorted(unknown_models),
        },
        "injection_stats": {
            "total_sessions": inj_total,
            "sessions_with_briefing": inj_with_briefing,
            "total_envelope_bytes": inj_total_bytes,
        },
    }
```

- [ ] **Step 8: Update `format_human` to render cost + injection blocks**

In the same file, extend `format_human`:

```python
def format_human(summary: dict) -> str:
    """Render summary as a plain-text report."""
    total = summary["total_calls"]
    lines = [f"Total calls: {total}"]
    if total == 0:
        lines.append("(no entries — access log is empty)")
        return "\n".join(lines)

    zh = summary["zero_hit_calls"]
    zh_rate = summary["zero_hit_rate"]
    lines.append(f"Zero-hit calls: {zh} ({zh_rate:.1%})")

    lines.append("")
    lines.append("By tool:")
    for tool, count in sorted(summary["by_tool"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {tool}: {count}")

    lines.append("")
    lines.append("By project:")
    for project, bucket in sorted(summary["by_project"].items(), key=lambda kv: -int(kv[1]["calls"])):
        calls = bucket["calls"]
        proj_zh = bucket["zero_hit"]
        proj_rate = bucket["zero_hit_rate"]
        lines.append(f"  {project}: {calls} calls, {proj_zh} zero-hit ({proj_rate:.1%})")

    cost = summary.get("llm_cost") or {}
    if cost.get("total_input_tokens", 0) or cost.get("total_output_tokens", 0):
        lines.append("")
        lines.append("LLM cost (input/output tokens, est. USD):")
        for purpose, p in sorted(cost.get("by_purpose", {}).items()):
            lines.append(
                f"  {purpose}: {p['calls']} calls, "
                f"in={p['input_tokens']:,} out={p['output_tokens']:,} "
                f"≈ ${p['estimated_usd']:.4f}"
            )
        lines.append(
            f"  TOTAL: in={cost['total_input_tokens']:,} "
            f"out={cost['total_output_tokens']:,} "
            f"≈ ${cost['estimated_usd']:.4f}"
        )
        if cost.get("unknown_models"):
            lines.append(
                f"  (cost not estimated for unknown models: {', '.join(cost['unknown_models'])})"
            )

    inj = summary.get("injection_stats") or {}
    if inj.get("total_sessions", 0):
        lines.append("")
        lines.append("SessionStart injection:")
        lines.append(
            f"  {inj['total_sessions']} sessions, "
            f"{inj['sessions_with_briefing']} with briefing, "
            f"avg envelope ≈ {inj['total_envelope_bytes'] // max(inj['total_sessions'], 1)} bytes"
        )

    return "\n".join(lines)
```

- [ ] **Step 9: Run tests to verify they pass**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_telemetry_cost.py tests/unit/test_pricing.py -v
```

Expected: 6 PASS.

- [ ] **Step 10: Run full suite**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green.

- [ ] **Step 11: Commit**

```bash
git add src/mnemo/core/pricing.py src/mnemo/core/mcp/access_log_summary.py tests/unit/test_pricing.py tests/unit/test_telemetry_cost.py
git commit -m "feat(telemetry): aggregate llm.call cost + injection stats

access_log_summary now extracts llm.call entries into a per-purpose
cost block (input/output tokens, estimated USD via pricing.py table)
and session_start.inject entries into an injection_stats block.
mnemo telemetry --json exposes both; format_human renders them."
```

---

## Task 8: SessionStart inject telemetry hook

**Files:**
- Modify: `src/mnemo/hooks/session_start.py`
- Test: `tests/unit/test_session_start_telemetry.py`

- [ ] **Step 1: Write failing test**

```python
# tests/unit/test_session_start_telemetry.py
"""SessionStart logs an envelope-size telemetry entry."""
from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from mnemo.hooks import session_start


def test_session_start_records_inject_entry(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setattr(
        "mnemo.core.mcp.access_log._load_telemetry_config",
        lambda: (True, 1_048_576),
    )

    sessions_dir = vault / "bots" / "myproj" / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "abc.md").write_text(
        "---\nsession_id: abc\ndate: 2026-04-19\nduration_minutes: 10\n---\nbody\n",
        encoding="utf-8",
    )

    out = io.StringIO()
    payload = session_start._build_injection_payload(
        vault, current_project="myproj", inject_briefing=True,
    )
    session_start._emit_injection(payload, out=out)
    # Real telemetry write happens inside main() — exercise the helper directly.
    from mnemo.core.mcp import access_log
    access_log.record_session_start_inject(
        vault,
        envelope_bytes=len(payload.encode("utf-8")),
        included_briefing=True,
        project="myproj",
        agent="myproj",
    )

    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    entries = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    inj = [e for e in entries if e["tool"] == "session_start.inject"]
    assert len(inj) == 1
    assert inj[0]["included_briefing"] is True
    assert inj[0]["envelope_bytes"] > 0
```

- [ ] **Step 2: Run test to verify the helper works (already covered by Task 5)**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_session_start_telemetry.py -v
```

Expected: PASS (we already implemented `record_session_start_inject` in Task 5; this test verifies it integrates with `_emit_injection`).

- [ ] **Step 3: Wire telemetry call into `main()`**

In `src/mnemo/hooks/session_start.py:main`, replace the existing `_emit_injection(payload_text)` block to also call telemetry:

```python
        if cfg.get("injection", {}).get("enabled", False):
            try:
                canonical_name = agent.resolve_canonical_agent(cwd).name
                inject_briefing = bool(cfg.get("briefings", {}).get("injectLastOnSessionStart", True))
                payload_text = _build_injection_payload(
                    vault,
                    current_project=canonical_name,
                    inject_briefing=inject_briefing,
                )
                if payload_text:
                    _emit_injection(payload_text)
                    try:
                        from mnemo.core.mcp import access_log as _al
                        _al.record_session_start_inject(
                            vault,
                            envelope_bytes=len(payload_text.encode("utf-8")),
                            included_briefing=("[last-briefing" in payload_text),
                            project=canonical_name,
                            agent=canonical_name,
                        )
                    except Exception as exc:
                        errors.log_error(vault, "session_start.inject_telemetry", exc)
            except Exception as e:
                errors.log_error(vault, "session_start.injection", e)
```

- [ ] **Step 4: Run full suite**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/session_start.py tests/unit/test_session_start_telemetry.py
git commit -m "feat(session_start): log envelope size + briefing inclusion

After emitting the SessionStart additionalContext, persist a
session_start.inject access-log entry so mnemo telemetry can report
the always-on cost of injection vs the per-call cost of LLM work."
```

---

## Task 9: `mnemo migrate-worktree-briefings` CLI

**Files:**
- Create: `src/mnemo/cli/commands/migrate_worktree_briefings.py`
- Create: `tests/unit/test_migrate_worktree_briefings.py`
- Modify: `src/mnemo/cli/parser.py` (if commands are registered explicitly; otherwise auto-discovery suffices — verify by looking at neighbours)

- [ ] **Step 1: Inspect command auto-registration mechanism**

```bash
grep -n "command(" src/mnemo/cli/commands/telemetry.py src/mnemo/cli/commands/__init__.py | head -10
```

The `@command("telemetry")` decorator should auto-register. Confirm by reading `src/mnemo/cli/parser.py`.

- [ ] **Step 2: Write failing tests**

```python
# tests/unit/test_migrate_worktree_briefings.py
"""mnemo migrate-worktree-briefings moves orphan briefings to canonical agent dir."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli.commands import migrate_worktree_briefings as cmd_mod


def _make_repo_with_worktree_briefings(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Returns (vault, main_repo, worktree)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    wt_gitdir = main / ".git" / "worktrees" / "feature-x"
    wt_gitdir.mkdir(parents=True)
    (wt_gitdir / "commondir").write_text("../..\n")
    worktree = tmp_path / "myproj-feature-x"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {wt_gitdir}\n")

    # Orphan briefing under the worktree's old agent dir.
    orphan_dir = vault / "bots" / "myproj-feature-x" / "briefings" / "sessions"
    orphan_dir.mkdir(parents=True)
    (orphan_dir / "session-1.md").write_text("orphan briefing", encoding="utf-8")

    return vault, main, worktree


def test_migrate_dry_run_lists_moves_only(tmp_path: Path, capsys, monkeypatch) -> None:
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=True, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "would move" in out
    assert "session-1.md" in out
    # No move actually happened.
    assert (vault / "bots" / "myproj-feature-x" / "briefings" / "sessions" / "session-1.md").exists()
    assert not (vault / "bots" / "myproj" / "briefings" / "sessions" / "session-1.md").exists()


def test_migrate_moves_orphans(tmp_path: Path, monkeypatch) -> None:
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    moved = vault / "bots" / "myproj" / "briefings" / "sessions" / "session-1.md"
    assert moved.exists()
    assert moved.read_text() == "orphan briefing"
    # Source removed.
    assert not (vault / "bots" / "myproj-feature-x" / "briefings" / "sessions" / "session-1.md").exists()


def test_migrate_skips_collisions(tmp_path: Path, capsys, monkeypatch) -> None:
    """If a target file with the same name already exists in canonical dir, skip + warn."""
    vault, main, _wt = _make_repo_with_worktree_briefings(tmp_path)
    canonical_dir = vault / "bots" / "myproj" / "briefings" / "sessions"
    canonical_dir.mkdir(parents=True)
    (canonical_dir / "session-1.md").write_text("existing canonical", encoding="utf-8")

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "collision" in out.lower() or "skipped" in out.lower()
    # Original orphan stays in place.
    assert (vault / "bots" / "myproj-feature-x" / "briefings" / "sessions" / "session-1.md").exists()
    # Canonical file untouched.
    assert (canonical_dir / "session-1.md").read_text() == "existing canonical"


def test_migrate_noop_when_nothing_to_move(tmp_path: Path, capsys, monkeypatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    main = tmp_path / "myproj"
    main.mkdir()
    (main / ".git").mkdir()
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    args = argparse.Namespace(dry_run=False, repos=[str(main)])
    rc = cmd_mod.cmd_migrate_worktree_briefings(args)
    assert rc == 0
    out = capsys.readouterr().out
    assert "nothing to migrate" in out.lower()
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_migrate_worktree_briefings.py -v
```

Expected: 4 FAIL — module does not exist.

- [ ] **Step 4: Implement the command**

```python
# src/mnemo/cli/commands/migrate_worktree_briefings.py
"""``mnemo migrate-worktree-briefings`` — relocate orphan worktree briefings.

When the canonical-agent change shipped (v0.10), pre-existing briefings
written under ``bots/<worktree-name>/briefings/sessions/`` are no longer
discoverable by the new SessionStart injection (which reads the
canonical agent dir). This one-shot command finds those orphan dirs and
moves their contents into the canonical agent's dir.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.core import agent as agent_mod


@command("migrate-worktree-briefings")
def cmd_migrate_worktree_briefings(args: argparse.Namespace) -> int:
    """Move orphan worktree briefings into the canonical agent's pool."""
    from mnemo import cli
    vault = cli._resolve_vault()

    repos: list[str] = list(getattr(args, "repos", []) or [])
    dry_run: bool = bool(getattr(args, "dry_run", False))

    if not repos:
        print("(usage) mnemo migrate-worktree-briefings --repos /path/to/repo [/path/to/another ...]")
        return 0

    moves: list[tuple[Path, Path]] = []
    collisions: list[Path] = []

    bots_root = vault / "bots"
    if not bots_root.is_dir():
        print("nothing to migrate (no bots/ dir in vault)")
        return 0

    for repo_path in repos:
        repo_p = Path(repo_path)
        canonical = agent_mod.resolve_canonical_agent(str(repo_p)).name
        # Find any agent dir whose name resolves to the same canonical (i.e. its own
        # .git is a worktree pointing to this repo's main .git).
        for agent_dir in sorted(bots_root.iterdir()):
            if not agent_dir.is_dir() or agent_dir.name == canonical:
                continue
            sessions = agent_dir / "briefings" / "sessions"
            if not sessions.is_dir():
                continue
            # Heuristic: name-prefix match. We can't always cwd-resolve a vault dir
            # back to a worktree because the worktree may have been deleted.
            if not agent_dir.name.startswith(canonical + "-"):
                continue
            target_dir = bots_root / canonical / "briefings" / "sessions"
            for src in sorted(sessions.glob("*.md")):
                target = target_dir / src.name
                if target.exists():
                    collisions.append(src)
                else:
                    moves.append((src, target))

    if not moves and not collisions:
        print("nothing to migrate")
        return 0

    for src, target in moves:
        if dry_run:
            print(f"would move {src} -> {target}")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(target))
            print(f"moved {src.name} -> {target.parent}")

    for src in collisions:
        print(f"collision: skipped {src} (target with same name already exists)")

    return 0
```

- [ ] **Step 5: Register the parser flags**

`src/mnemo/cli/parser.py` registers every subcommand explicitly inside `_build_parser()` via direct `sub.add_parser(...)` calls (the `@command` decorator only wires the *handler*, not the args). Edit `_build_parser` to append, just before the `sub.add_parser("help", ...)` line:

```python
    migrate = sub.add_parser(
        "migrate-worktree-briefings",
        help="move orphan worktree briefings to the canonical agent dir",
    )
    migrate.add_argument(
        "--repos", nargs="+", default=[],
        help="canonical repo paths whose worktree briefings should be relocated",
    )
    migrate.add_argument(
        "--dry-run", action="store_true",
        help="list planned moves without performing them",
    )
```

Also: `src/mnemo/cli/commands/__init__.py` triggers imports of every command module so the `@command` decorator runs at import time. Add an import for the new module so its handler is registered:

```bash
grep -n "import" src/mnemo/cli/commands/__init__.py
```

If the file imports modules by name, append:
```python
from mnemo.cli.commands import migrate_worktree_briefings  # noqa: F401
```

If it uses a glob/auto-import pattern, no edit is needed — verify by re-running `mnemo migrate-worktree-briefings --help` after the file is created.

- [ ] **Step 6: Run tests to verify they pass**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest tests/unit/test_migrate_worktree_briefings.py -v
```

Expected: 4 PASS.

- [ ] **Step 7: Smoke-test via CLI**

```bash
PYTHONPATH=$(pwd)/src python3 -m mnemo migrate-worktree-briefings --help
```

Expected: usage text including `--repos` and `--dry-run`.

- [ ] **Step 8: Run full suite**

```bash
PYTHONPATH=$(pwd)/src python3 -m pytest -q --ignore=tests/unit/test_plugin_manifest.py
```

Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/mnemo/cli/commands/migrate_worktree_briefings.py src/mnemo/cli/parser.py tests/unit/test_migrate_worktree_briefings.py
git commit -m "feat(cli): mnemo migrate-worktree-briefings

One-shot relocation tool for vaults that have orphan worktree briefing
dirs (bots/<repo>-<wt>/briefings/sessions/) leftover from before the
canonical-agent change. --dry-run lists planned moves; collisions are
reported and skipped."
```

---

## Task 10: Documentation update

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add a section to README**

Find the section that documents the SessionStart injection envelope. Add a paragraph:

> ### Last-briefing handoff (v0.10+)
>
> When `briefings.injectLastOnSessionStart` is true (default), every new Claude
> Code session in a project whose canonical agent has at least one briefing
> on disk gets a `[last-briefing session=… date=… duration_minutes=…] … [/last-briefing]`
> block appended to the SessionStart injection envelope. Worktrees of the same
> repo share one briefing pool, resolved via `.git` worktree pointers.
>
> If you have orphan worktree briefings from before this change, run
> `mnemo migrate-worktree-briefings --repos /path/to/repo --dry-run` to preview
> moves, then drop `--dry-run` to apply.

Add another section about telemetry:

> ### Cost telemetry (v0.10+)
>
> Every `llm.call()` (briefing + extraction consolidations) writes a
> `tool: "llm.call"` entry into `.mnemo/mcp-access-log.jsonl` with
> `usage.input_tokens` / `usage.output_tokens`. Every SessionStart writes a
> `tool: "session_start.inject"` entry with `envelope_bytes`. `mnemo telemetry`
> aggregates both into per-purpose token totals + estimated USD (using a
> hard-coded pricing table at `src/mnemo/core/pricing.py` — bump the table
> when Anthropic prices change).

- [ ] **Step 2: Add a CHANGELOG entry**

Prepend to `CHANGELOG.md`:

```markdown
## [Unreleased] — v0.10.0

### Added
- **Session handoff injection.** SessionStart now appends the most recent briefing's body (under `[last-briefing …]`) to the `mnemo://v1` envelope when `briefings.injectLastOnSessionStart` is true (default). Claude wakes up with the previous session's handoff context already in scope.
- **Worktree-aware canonical agent.** New `agent.resolve_canonical_agent` follows `.git` worktree pointers to the main repo, so all worktrees of a repo share a single briefing pool. Briefing writer (SessionEnd) now uses canonical naming.
- **`mnemo migrate-worktree-briefings`** — one-shot CLI to relocate orphan worktree briefings written before the canonical-agent change.
- **Cost telemetry.** `llm.call()` invocations and SessionStart injections both write structured entries to `mcp-access-log.jsonl`. `mnemo telemetry` now reports per-purpose token totals + estimated USD via a hard-coded pricing table.

### Changed
- `_build_injection_payload` accepts `inject_briefing: bool` parameter (default `False` for backwards compat with direct callers; SessionStart hook passes `True` by default via config).
- `access_log_summary.summarize` returns two new top-level keys: `llm_cost`, `injection_stats`. Existing keys unchanged.
```

- [ ] **Step 3: Commit**

```bash
git add README.md CHANGELOG.md
git commit -m "docs(v0.10): session handoff injection + cost telemetry"
```

---

## Self-Review Checklist

Before declaring this plan complete, the implementer should verify:

1. **Spec coverage.** Walk through each section of `docs/superpowers/specs/2026-04-20-session-handoff-injection-design.md`. Every requirement maps to at least one task above:
   - Project resolution (canonical agent) → Tasks 1, 3
   - Briefing selection → Task 2
   - Injection envelope → Task 4
   - Race condition (best-effort) → covered by Task 4 (no special code, just absence of waiting)
   - Token instrumentation → Tasks 5, 6, 7
   - Migration → Task 9
   - Config → Task 4 (Step 6) + Task 8 (Step 3)
   - Testing → tests in every task
   - Documentation → Task 10

2. **No regressions.** After every task, the full suite (excluding the pre-existing `test_plugin_manifest.py` failures) must still pass.

3. **No secret leakage.** Telemetry entries log token counts but never log prompt or response text — verify this in the `record_llm_call` shape.

4. **Backwards compatibility.** Existing `mnemo telemetry` consumers continue to read `total_calls`, `by_tool`, `by_project` unchanged. The new `llm_cost` and `injection_stats` keys are additive.

5. **Stdlib-only constraint.** No new dependencies added — verify `pyproject.toml` is untouched.
