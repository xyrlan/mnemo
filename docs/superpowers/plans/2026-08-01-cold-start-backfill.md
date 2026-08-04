# Cold-Start Backfill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate a new user's vault from Claude Code session transcripts that already sit on disk, so mnemo injects useful rules on day one instead of after weeks of accrual.

**Architecture:** One new package, `mnemo.core.backfill`, synthesises memory files from old `~/.claude/projects/*/*.jsonl` transcripts and writes them to `bots/<repo>/memory/` — the exact seam the existing extraction pipeline already reads from. Everything downstream (extract → inbox → promote → index → reflex) is untouched except for one routing gate that keeps backfilled material out of `shared/` until reviewed. `harvest.py` mirrors `core/briefing.py` line for line: same LLM plumbing, same config keys, same zero-mutation skip.

**Tech Stack:** Python 3.8+, stdlib only (no third-party deps — this is a hard project constraint). pytest for tests. LLM access via `mnemo.core.llm.call`, which shells out to the `claude` CLI.

**Spec:** `docs/superpowers/specs/2026-08-01-cold-start-backfill-design.md`

---

## File Structure

| File | Responsibility | New? |
|---|---|---|
| `src/mnemo/core/backfill/__init__.py` | package marker + public re-exports | create |
| `src/mnemo/core/backfill/ledger.py` | `.mnemo/backfill-state.json` — what's done, what failed | create |
| `src/mnemo/core/backfill/discover.py` | `~/.claude/projects/` → (repo, transcript) pairs, mtime-ranked | create |
| `src/mnemo/core/backfill/harvest.py` | one transcript → N memory files | create |
| `src/mnemo/core/extract/prompts/templates/harvest.py` | `HARVEST_SYSTEM_PROMPT` | create |
| `src/mnemo/core/extract/prompts/render.py` | add `build_harvest_prompt` | modify |
| `src/mnemo/core/extract/prompts/__init__.py` | re-export both | modify |
| `src/mnemo/core/config.py` | `backfill` block in `DEFAULTS` | modify |
| `src/mnemo/core/extract/inbox/types.py` | `ExtractedPage.origin_backfill` | modify |
| `src/mnemo/core/extract/inbox/paths.py` | route backfill-origin pages to `_inbox` | modify |
| `src/mnemo/core/extract/__init__.py` | thread origin flag from `MemoryFile` → `ExtractedPage` | modify |
| `src/mnemo/cli/commands/backfill.py` | `mnemo backfill` | create |
| `src/mnemo/cli/parser.py` | subparser wiring | modify |
| `src/mnemo/cli/commands/__init__.py` | import the new command module | modify |
| `src/mnemo/hooks/session_start.py` | detached first-run spawn | modify |
| `docs/configuration.md` | regenerate config table | modify |

`ledger` and `discover` are pure filesystem functions with no LLM dependency, so they test without any stubbing. `harvest` is the only unit needing a stubbed `llm.call`. That split is deliberate — keep it.

**Read before starting:** `src/mnemo/core/briefing.py`. `harvest.py` is its sibling and should look like it.

---

## Task 1: Config defaults

**Files:**
- Modify: `src/mnemo/core/config.py` (the `DEFAULTS` dict)
- Test: `tests/unit/test_config_backfill_defaults.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_config_backfill_defaults.py`:

```python
"""backfill config block ships with safe defaults."""
from __future__ import annotations

from mnemo.core.config import DEFAULTS, load_config


def test_defaults_have_backfill_block():
    assert "backfill" in DEFAULTS
    bf = DEFAULTS["backfill"]
    assert bf["enabled"] is True
    assert bf["installCap"] == 20
    assert bf["minFileMutations"] == 1
    assert bf["autoOnFirstSession"] is True


def test_load_config_merges_backfill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    cfg = load_config()
    assert cfg["backfill"]["installCap"] == 20
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_config_backfill_defaults.py -v`
Expected: FAIL with `KeyError: 'backfill'` (or `assert "backfill" in DEFAULTS`).

- [ ] **Step 3: Add the block to `DEFAULTS`**

In `src/mnemo/core/config.py`, add to the `DEFAULTS` dict, immediately after the `"briefings"` entry so related capture-time knobs sit together:

```python
    "backfill": {
        "enabled": True,
        "installCap": 20,
        "minFileMutations": 1,
        "autoOnFirstSession": True,
    },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_config_backfill_defaults.py tests/unit/test_config.py -v`
Expected: PASS. `test_config.py` is included because it asserts on the shape of `DEFAULTS`; if it has a key-count or exact-dict assertion, update that assertion to include `backfill`.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/config.py tests/unit/test_config_backfill_defaults.py
git commit -m "feat(backfill): add backfill config defaults"
```

---

## Task 2: Ledger

The ledger answers one question: has this transcript already been harvested, and if it failed, how many times? Keyed by session id; the file hash lets a transcript that grew on disk be re-harvested.

**Files:**
- Create: `src/mnemo/core/backfill/__init__.py`
- Create: `src/mnemo/core/backfill/ledger.py`
- Test: `tests/unit/test_backfill_ledger.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_backfill_ledger.py`:

```python
"""Ledger: idempotency, resume, re-harvest on change, 3-strike skip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core.backfill import ledger


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _transcript(tmp_path: Path, name: str, text: str) -> Path:
    p = tmp_path / f"{name}.jsonl"
    p.write_text(text, encoding="utf-8")
    return p


def test_unseen_transcript_should_harvest(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    assert ledger.should_harvest(led, t) is True


def test_marking_done_makes_it_skip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)
    assert ledger.should_harvest(led, t) is False


def test_survives_a_save_load_roundtrip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)
    ledger.save(vault, led)

    reloaded = ledger.load(vault)
    assert ledger.should_harvest(reloaded, t) is False


def test_changed_transcript_is_reharvested(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    ledger.mark_done(led, t, produced=3)

    t.write_text('{"type":"user"}\n{"type":"assistant"}\n', encoding="utf-8")
    assert ledger.should_harvest(led, t) is True


def test_three_failures_permanently_skip(vault, tmp_path):
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    for _ in range(2):
        ledger.mark_failed(led, t, "boom")
    assert ledger.should_harvest(led, t) is True

    ledger.mark_failed(led, t, "boom")
    assert ledger.should_harvest(led, t) is False


def test_corrupt_ledger_file_starts_clean(vault, tmp_path):
    (vault / ".mnemo" / "backfill-state.json").write_text("{not json", encoding="utf-8")
    t = _transcript(tmp_path, "sess-a", '{"type":"user"}\n')
    led = ledger.load(vault)
    assert ledger.should_harvest(led, t) is True


def test_save_writes_schema_version(vault, tmp_path):
    led = ledger.load(vault)
    ledger.save(vault, led)
    data = json.loads((vault / ".mnemo" / "backfill-state.json").read_text(encoding="utf-8"))
    assert data["schemaVersion"] == 1
    assert "sessions" in data
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_backfill_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.core.backfill'`.

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/backfill/__init__.py`:

```python
"""Cold-start backfill: synthesise memory files from historical transcripts.

New vaults inject nothing until enough sessions accrue to extract from. This
package converts the Claude Code session transcripts already on disk at
``~/.claude/projects/<slug>/*.jsonl`` into ``bots/<repo>/memory/*.md`` files —
the seam the existing extraction pipeline already reads from.

See ``docs/superpowers/specs/2026-08-01-cold-start-backfill-design.md``.
"""
```

Create `src/mnemo/core/backfill/ledger.py`:

```python
"""Durable record of which transcripts have been harvested.

Lives at ``<vault>/.mnemo/backfill-state.json``. Keyed by session id (the
transcript filename stem) with the file's content hash, so:

- a rerun is a no-op,
- an interrupted sweep resumes where it stopped,
- a transcript that grew on disk is harvested again,
- a transcript that fails three times is skipped for good.

Pure filesystem + hashing. No LLM dependency, so it tests without stubbing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_ATTEMPTS = 3
_STATE_NAME = "backfill-state.json"


def state_path(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo" / _STATE_NAME


def transcript_hash(path: Path) -> str:
    """Content hash of a transcript, or ``""`` when unreadable."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(65536), b""):
                h.update(block)
    except OSError:
        return ""
    return "sha256:" + h.hexdigest()


def load(vault_root: Path) -> dict[str, Any]:
    """Read the ledger. A missing or corrupt file yields a clean ledger."""
    path = state_path(vault_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return {"schemaVersion": SCHEMA_VERSION, "sessions": {}, "installRunDone": False}
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    data.setdefault("installRunDone", False)
    return data


def save(vault_root: Path, led: dict[str, Any]) -> None:
    """Atomically write the ledger."""
    path = state_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(led, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _entry(led: dict[str, Any], path: Path) -> dict[str, Any] | None:
    entry = led.get("sessions", {}).get(Path(path).stem)
    return entry if isinstance(entry, dict) else None


def should_harvest(led: dict[str, Any], path: Path) -> bool:
    """True when this transcript still needs work."""
    entry = _entry(led, path)
    if entry is None:
        return True
    if entry.get("hash") != transcript_hash(path):
        return True  # transcript changed on disk
    if entry.get("status") == "done":
        return False
    return int(entry.get("attempts") or 0) < MAX_ATTEMPTS


def mark_done(led: dict[str, Any], path: Path, *, produced: int) -> None:
    led.setdefault("sessions", {})[Path(path).stem] = {
        "status": "done",
        "hash": transcript_hash(path),
        "produced": int(produced),
        "attempts": 0,
    }


def mark_failed(led: dict[str, Any], path: Path, reason: str) -> None:
    key = Path(path).stem
    sessions = led.setdefault("sessions", {})
    prior = sessions.get(key) if isinstance(sessions.get(key), dict) else {}
    attempts = int(prior.get("attempts") or 0) + 1
    sessions[key] = {
        "status": "failed",
        "hash": transcript_hash(path),
        "attempts": attempts,
        "lastError": str(reason)[:200],
    }
```

Note: `mark_done`'s `produced` is keyword-only in the implementation but the test calls it as `produced=3` — consistent.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_backfill_ledger.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/backfill/ tests/unit/test_backfill_ledger.py
git commit -m "feat(backfill): add harvest ledger with resume and 3-strike skip"
```

---

## Task 3: Discovery

Maps Claude Code's dash-encoded project directories back to repos and ranks transcripts by recency.

Claude Code stores transcripts at `~/.claude/projects/<dash-encoded-cwd>/<session-id>.jsonl`, where the cwd has every path separator replaced by a dash — see `_resolve_session_jsonl_path` in `src/mnemo/hooks/session_end.py:120` for the existing decoder. Discovery runs the inverse: decode the directory name into a cwd, then resolve that cwd to a canonical agent name with `agent.resolve_canonical_agent`, which already follows worktree `.git` pointers so a worktree and its main checkout collapse to one repo.

**Files:**
- Create: `src/mnemo/core/backfill/discover.py`
- Test: `tests/unit/test_backfill_discover.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_backfill_discover.py`:

```python
"""Discovery: decode project dirs, rank by mtime, filter by project."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core.backfill import discover


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    root = home / ".claude" / "projects"
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return root


def _session(project_dir: Path, name: str, mtime: float) -> Path:
    project_dir.mkdir(parents=True, exist_ok=True)
    p = project_dir / f"{name}.jsonl"
    p.write_text('{"type":"user"}\n', encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def test_decodes_dashed_dir_back_to_cwd():
    encoded = "-Users-xyrlan-github-meunu"
    assert discover.decode_project_dir(encoding=encoded) == "/Users/xyrlan/github/meunu"


def test_finds_transcripts_and_ranks_newest_first(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "old", 1000.0)
    _session(d, "new", 2000.0)

    found = discover.find_transcripts()
    names = [t.path.stem for t in found]
    assert names == ["new", "old"]


def test_limit_takes_the_newest(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "a", 1000.0)
    _session(d, "b", 2000.0)
    _session(d, "c", 3000.0)

    found = discover.find_transcripts(limit=2)
    assert [t.path.stem for t in found] == ["c", "b"]


def test_project_filter_excludes_other_repos(projects_root, monkeypatch):
    monkeypatch.setattr(discover, "_agent_for_cwd", lambda cwd: Path(cwd).name)
    _session(projects_root / "-tmp-alpha", "a1", 1000.0)
    _session(projects_root / "-tmp-beta", "b1", 2000.0)

    found = discover.find_transcripts(project="alpha")
    assert [t.path.stem for t in found] == ["a1"]
    assert all(t.agent == "alpha" for t in found)


def test_missing_projects_dir_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "nowhere"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "nowhere"))
    assert discover.find_transcripts() == []


def test_non_jsonl_files_are_ignored(projects_root):
    d = projects_root / "-tmp-repo-alpha"
    _session(d, "real", 1000.0)
    d.joinpath("notes.txt").write_text("hi", encoding="utf-8")

    found = discover.find_transcripts()
    assert [t.path.stem for t in found] == ["real"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_backfill_discover.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.core.backfill.discover'`.

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/backfill/discover.py`:

```python
"""Locate historical Claude Code transcripts and map them back to repos.

Claude Code writes ``~/.claude/projects/<dash-encoded-cwd>/<session-id>.jsonl``,
encoding the cwd by replacing every path separator with a dash. This module
runs that decode in reverse and resolves each cwd to the canonical mnemo agent
name, so a worktree and its main checkout land in the same bucket.

Pure filesystem. No LLM dependency.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Transcript:
    path: Path
    agent: str
    cwd: str
    mtime: float


def projects_root() -> Path:
    return Path(os.path.expanduser("~")) / ".claude" / "projects"


def decode_project_dir(*, encoding: str) -> str:
    """Turn ``-Users-me-github-repo`` back into ``/Users/me/github/repo``.

    Lossy by nature — a directory whose real name contains a dash is
    indistinguishable from a separator. That is acceptable here: the decoded
    cwd is only used to derive an agent name, and a cwd that no longer exists
    falls back to a name derived from the encoded string itself.
    """
    return "/" + "/".join(part for part in encoding.split("-") if part)


def _agent_for_cwd(cwd: str) -> str:
    """Canonical mnemo agent name for a cwd. Seam for tests."""
    from mnemo.core import agent as agent_mod

    return agent_mod.resolve_canonical_agent(cwd).name


def _fallback_agent(encoding: str) -> str:
    parts = [p for p in encoding.split("-") if p]
    return parts[-1] if parts else "unknown"


def find_transcripts(
    *,
    project: str | None = None,
    limit: int | None = None,
) -> list[Transcript]:
    """Return transcripts newest-first, optionally filtered and capped.

    ``project`` matches the resolved agent name exactly. ``limit`` is applied
    *after* sorting and filtering, so it always yields the most recent N of
    whatever the filter selected.
    """
    root = projects_root()
    if not root.is_dir():
        return []

    out: list[Transcript] = []
    for project_dir in sorted(root.iterdir()):
        if not project_dir.is_dir():
            continue
        cwd = decode_project_dir(encoding=project_dir.name)
        if Path(cwd).is_dir():
            try:
                agent = _agent_for_cwd(cwd)
            except Exception:
                agent = _fallback_agent(project_dir.name)
        else:
            agent = _fallback_agent(project_dir.name)

        if project is not None and agent != project:
            continue

        for jsonl in project_dir.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
            except OSError:
                continue
            out.append(Transcript(path=jsonl, agent=agent, cwd=cwd, mtime=mtime))

    out.sort(key=lambda t: t.mtime, reverse=True)
    if limit is not None:
        out = out[: max(0, int(limit))]
    return out
```

Note on `test_project_filter_excludes_other_repos`: it monkeypatches `_agent_for_cwd` because `/tmp/alpha` is not a real git repo. The other tests do not patch it, so they exercise the `_fallback_agent` path — that is intentional coverage of both branches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_backfill_discover.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/backfill/discover.py tests/unit/test_backfill_discover.py
git commit -m "feat(backfill): discover historical transcripts per repo"
```

---

## Task 4: Harvest prompt

**Files:**
- Create: `src/mnemo/core/extract/prompts/templates/harvest.py`
- Modify: `src/mnemo/core/extract/prompts/render.py`
- Modify: `src/mnemo/core/extract/prompts/__init__.py`
- Test: `tests/unit/test_harvest_prompt.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_harvest_prompt.py`:

```python
"""Harvest prompt shape: importable, constrains types, embeds the transcript."""
from __future__ import annotations

from mnemo.core.extract import prompts


def test_system_prompt_is_exported_and_nonempty():
    assert isinstance(prompts.HARVEST_SYSTEM_PROMPT, str)
    assert len(prompts.HARVEST_SYSTEM_PROMPT) > 200


def test_system_prompt_names_every_valid_type():
    text = prompts.HARVEST_SYSTEM_PROMPT
    for t in ("feedback", "user", "reference", "project"):
        assert t in text


def test_system_prompt_demands_json_pages_array():
    text = prompts.HARVEST_SYSTEM_PROMPT
    assert '"pages"' in text
    assert "JSON" in text


def test_user_prompt_wraps_the_transcript():
    out = prompts.build_harvest_prompt("USER: do the thing")
    assert "USER: do the thing" in out
    assert "=== TRANSCRIPT ===" in out
    assert "=== END TRANSCRIPT ===" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_harvest_prompt.py -v`
Expected: FAIL with `AttributeError: module 'mnemo.core.extract.prompts' has no attribute 'HARVEST_SYSTEM_PROMPT'`.

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/extract/prompts/templates/harvest.py`:

```python
"""System prompt for the cold-start backfill harvest call.

Harvest sits between briefing and consolidation: like briefing it reads a raw
Claude Code jsonl transcript, but like consolidation it emits structured pages.
The pages it emits are *memory files* (``bots/<repo>/memory/*.md``), not rules —
the existing extraction pipeline turns those into rules afterwards.

The prompt is deliberately conservative. Backfill reconstructs intent from an
old transcript rather than observing it live, so recall is worth trading for
precision: it is told to emit nothing rather than guess.
"""
from __future__ import annotations


HARVEST_SYSTEM_PROMPT = (
    "You are reading one archived Claude Code session transcript and writing "
    "down the durable lessons it contains, so a future session can benefit "
    "from them without re-reading the transcript.\n\n"
    "Emit ONLY durable, reusable knowledge. A lesson is durable when it would "
    "still be true and useful in a different session next month. Skip anything "
    "that is merely episodic: what was done, in what order, which files were "
    "opened, what the task was.\n\n"
    "Each page has a type:\n"
    "- feedback — a correction or preference the user expressed about how to "
    "work ('never use any', 'always run the linter before committing')\n"
    "- user — a durable fact about who the user is: role, stack, expertise, "
    "working style\n"
    "- reference — a pointer to an external system, or hard-won operational "
    "knowledge about one (an API's quirks, a deploy procedure, a gotcha that "
    "cost a build cycle)\n"
    "- project — context about this specific repository that is not derivable "
    "from reading its code: architectural intent, constraints, decisions and "
    "the reasoning behind them\n\n"
    "Precision beats recall. This transcript is being read long after the fact, "
    "so you are reconstructing intent rather than observing it. If a lesson is "
    "ambiguous, or you would be guessing at the user's reasoning, omit it. "
    "Returning zero pages is a correct and common answer.\n\n"
    "Never invent a lesson to fill space. Never restate what the code already "
    "says. Never emit a page whose body is a summary of the session.\n\n"
    "Respond with a single JSON object, no prose and no code fences:\n"
    "{\n"
    '  "pages": [\n'
    "    {\n"
    '      "slug": "kebab-case-identifier",\n'
    '      "type": "feedback|user|reference|project",\n'
    '      "name": "Short human-readable title",\n'
    '      "description": "One line stating what this page holds",\n'
    '      "body": "Markdown. State the lesson and why it holds."\n'
    "    }\n"
    "  ]\n"
    "}\n"
    'An empty list — {"pages": []} — is valid and expected for sessions that '
    "taught nothing durable."
)
```

In `src/mnemo/core/extract/prompts/render.py`, add the builder next to `build_briefing_prompt` (it ends around line 124), and add the re-export anchor beside the existing briefing one at line 21:

```python
from mnemo.core.extract.prompts.templates.harvest import HARVEST_SYSTEM_PROMPT  # noqa: F401  (re-export anchor for the package shim)
```

```python
def build_harvest_prompt(transcript: str) -> str:
    """Render a harvest prompt from a pre-flattened transcript string.

    Mirrors :func:`build_briefing_prompt` — same transcript delimiters — but
    asks for structured memory pages instead of a handoff narrative.
    """
    return (
        "Task: extract durable memory pages from the following archived "
        "Claude Code session transcript. Follow the JSON schema from the "
        "system prompt exactly. Output JSON only, no prose, no code fences.\n\n"
        "=== TRANSCRIPT ===\n"
        f"{transcript}\n"
        "=== END TRANSCRIPT ===\n"
    )
```

In `src/mnemo/core/extract/prompts/__init__.py`, add `build_harvest_prompt` to the existing `from ...render import (...)` block (keep the list alphabetical — it goes after `build_feedback_prompt`), and add a new re-export block after the briefing one:

```python
from mnemo.core.extract.prompts.templates.harvest import (  # noqa: F401
    HARVEST_SYSTEM_PROMPT,
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_harvest_prompt.py tests/unit/test_extract_prompts.py -v`
Expected: PASS. `test_extract_prompts.py` is included to confirm the shim edits broke nothing.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/extract/prompts/ tests/unit/test_harvest_prompt.py
git commit -m "feat(backfill): add harvest system prompt and builder"
```

---

## Task 5: Harvest

The core unit. Read `src/mnemo/core/briefing.py` first — this is its sibling and reuses its helpers directly.

**Files:**
- Create: `src/mnemo/core/backfill/harvest.py`
- Test: `tests/unit/test_backfill_harvest.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_backfill_harvest.py`:

```python
"""Harvest: transcript in, origin-stamped memory files out."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import llm
from mnemo.core.backfill import harvest
from mnemo.core.extract.scanner import parse_frontmatter


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    root = tmp_path / "vault"
    (root / "bots").mkdir(parents=True)
    return {
        "vaultRoot": str(root),
        "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60},
        "backfill": {"minFileMutations": 1},
    }


def _write_transcript(tmp_path: Path, *, mutations: int) -> Path:
    events = [{
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "no, use pathlib"}]},
    }]
    for i in range(mutations):
        events.append({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Edit", "id": f"t{i}", "input": {}}],
            },
        })
    p = tmp_path / "sess-1.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return p


def _stub_llm(monkeypatch, pages: list[dict]):
    def fake_call(prompt, *, system, model="claude-haiku-4-5", timeout=60):
        return llm.LLMResponse(
            text=json.dumps({"pages": pages}),
            total_cost_usd=0.0,
            input_tokens=10,
            output_tokens=10,
            api_key_source="subscription",
            raw={},
        )

    monkeypatch.setattr(harvest.llm, "call", fake_call)


def test_writes_a_memory_file_per_page(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [{
        "slug": "use-pathlib",
        "type": "feedback",
        "name": "Use pathlib",
        "description": "Prefer pathlib over os.path",
        "body": "User corrected os.path usage; pathlib is the house style.",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)

    assert len(written) == 1
    assert written[0].name == "use-pathlib.md"
    assert written[0].parent == Path(cfg["vaultRoot"]) / "bots" / "alpha" / "memory"


def test_stamps_origin_backfill_in_frontmatter(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [{
        "slug": "use-pathlib",
        "type": "feedback",
        "name": "Use pathlib",
        "description": "Prefer pathlib",
        "body": "House style.",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)
    fm = parse_frontmatter(written[0].read_text(encoding="utf-8"))

    assert fm["metadata"]["origin"] == "backfill"
    assert fm["metadata"]["type"] == "feedback"
    assert fm["metadata"]["node_type"] == "memory"
    assert fm["metadata"]["originSessionId"] == "sess-1"


def test_zero_mutation_session_is_skipped_without_calling_llm(cfg, tmp_path, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("LLM must not be called for a zero-mutation session")

    monkeypatch.setattr(harvest.llm, "call", explode)
    t = _write_transcript(tmp_path, mutations=0)

    assert harvest.harvest_session(t, "alpha", cfg) == []


def test_invalid_page_type_is_dropped(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [
        {"slug": "good", "type": "feedback", "name": "G", "description": "d", "body": "b"},
        {"slug": "bad", "type": "nonsense", "name": "B", "description": "d", "body": "b"},
    ])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)
    assert [p.name for p in written] == ["good.md"]


def test_empty_body_page_is_dropped(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [
        {"slug": "hollow", "type": "feedback", "name": "H", "description": "d", "body": "   "},
    ])
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []


def test_empty_pages_array_writes_nothing(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [])
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []


def test_existing_memory_file_is_not_overwritten(cfg, tmp_path, monkeypatch):
    memory_dir = Path(cfg["vaultRoot"]) / "bots" / "alpha" / "memory"
    memory_dir.mkdir(parents=True)
    existing = memory_dir / "use-pathlib.md"
    existing.write_text("LIVE CONTENT", encoding="utf-8")

    _stub_llm(monkeypatch, [{
        "slug": "use-pathlib", "type": "feedback",
        "name": "Use pathlib", "description": "d", "body": "b",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []
    assert existing.read_text(encoding="utf-8") == "LIVE CONTENT"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_backfill_harvest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.core.backfill.harvest'`.

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/backfill/harvest.py`:

```python
"""Turn one archived transcript into memory files.

Sibling of :mod:`mnemo.core.briefing` — same shape, same helpers, same config
keys. Where briefing produces one narrative markdown file, harvest produces N
structured memory pages under ``bots/<agent>/memory/``.

Every page written carries ``metadata.origin: backfill`` so the extraction
pipeline can hold reconstructed material to a higher bar than live-authored
memory (see ``extract/inbox/paths.py``).

Never overwrites an existing memory file. Live-authored memory always wins: it
was written by the session that learned the lesson, not reconstructed after.
"""
from __future__ import annotations

import time as _time
from pathlib import Path

from mnemo.core import llm, paths
from mnemo.core.briefing import (
    _atomic_write,
    _count_file_mutations,
    _load_jsonl_events,
)
from mnemo.core.extract import prompts
from mnemo.core.extract.scanner import _normalize_slug
from mnemo.core.transcript import flatten_transcript_events

VALID_TYPES = frozenset({"feedback", "user", "reference", "project"})


def _render_memory_file(
    *,
    slug: str,
    page_type: str,
    name: str,
    description: str,
    body: str,
    session_id: str,
) -> str:
    """Render a memory file matching the shape live capture writes."""
    safe_desc = str(description).replace('"', "'").strip()
    return (
        "---\n"
        f"name: {slug}\n"
        f'description: "{safe_desc}"\n'
        "metadata:\n"
        "  node_type: memory\n"
        f"  type: {page_type}\n"
        f"  originSessionId: {session_id}\n"
        "  origin: backfill\n"
        "---\n\n"
        f"# {name}\n\n"
        f"{body.strip()}\n"
    )


def harvest_session(jsonl_path: Path, agent: str, cfg: dict) -> list[Path]:
    """Harvest one transcript into memory files. Returns the paths written.

    Returns an empty list when the session is below the mutation threshold,
    when the LLM emitted no usable pages, or when every page collided with an
    existing memory file. Raises on LLM or I/O failure — callers that want
    fire-and-forget semantics wrap this in a try/except, as the CLI does.
    """
    events = _load_jsonl_events(jsonl_path)

    backfill_cfg = cfg.get("backfill") or {}
    min_mutations = int(backfill_cfg.get("minFileMutations", 1))
    if _count_file_mutations(events) < min_mutations:
        return []

    extraction_cfg = cfg.get("extraction") or {}
    model = extraction_cfg.get("model") or "claude-haiku-4-5"
    timeout = int(extraction_cfg.get("subprocessTimeout") or 60)

    transcript = flatten_transcript_events(events)
    t0 = _time.perf_counter()
    response = llm.call(
        prompts.build_harvest_prompt(transcript),
        system=prompts.HARVEST_SYSTEM_PROMPT,
        model=model,
        timeout=timeout,
    )
    elapsed_ms = (_time.perf_counter() - t0) * 1000

    try:
        from mnemo.core.mcp import access_log as _al

        _al.record_llm_call(
            vault_root=paths.vault_root(cfg),
            response=response,
            purpose="backfill:harvest",
            model=model,
            project=agent,
            agent=agent,
            elapsed_ms=elapsed_ms,
        )
    except Exception:
        pass  # telemetry must never break a harvest

    payload = llm._parse_llm_json(response.text or "")
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list):
        return []

    session_id = jsonl_path.stem
    memory_dir = paths.memory_dir(cfg, agent)
    written: list[Path] = []

    for rp in raw_pages:
        if not isinstance(rp, dict):
            continue
        slug = _normalize_slug(str(rp.get("slug") or ""))
        if not slug:
            continue
        page_type = str(rp.get("type") or "").strip().lower()
        if page_type not in VALID_TYPES:
            continue
        body = str(rp.get("body") or "")
        if not body.strip():
            continue

        target = memory_dir / f"{slug}.md"
        if target.exists():
            continue  # live-authored memory wins

        content = _render_memory_file(
            slug=slug,
            page_type=page_type,
            name=str(rp.get("name") or slug),
            description=str(rp.get("description") or ""),
            body=body,
            session_id=session_id,
        )
        _atomic_write(target, content)
        written.append(target)

    return written
```

Check `_atomic_write` in `src/mnemo/core/briefing.py:89` — if it does not `mkdir(parents=True)` on the target's parent, add `memory_dir.mkdir(parents=True, exist_ok=True)` immediately before the page loop in `harvest_session`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_backfill_harvest.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/backfill/harvest.py tests/unit/test_backfill_harvest.py
git commit -m "feat(backfill): harvest memory files from archived transcripts"
```

---

## Task 6: Origin gate

Backfilled material must not auto-promote into `shared/`. Today `_target_path_for_page` routes on source count alone: one source → `shared/<type>/`, many → `shared/_inbox/<type>/`. Add origin as a second reason to stage.

`paths.py` is deliberately I/O-free, so the origin decision is computed upstream and carried on the page as a flag.

**Files:**
- Modify: `src/mnemo/core/extract/inbox/types.py` (`ExtractedPage`)
- Modify: `src/mnemo/core/extract/inbox/paths.py:28-40` (`_target_path_for_page`)
- Modify: `src/mnemo/core/extract/__init__.py:170` (`_parse_pages_from_response`) and its call site at line 387
- Test: `tests/unit/test_extract_backfill_gate.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_extract_backfill_gate.py`:

```python
"""Backfill-origin pages stage in _inbox instead of auto-promoting."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.extract import _parse_pages_from_response
from mnemo.core.extract.inbox.paths import _target_path_for_page
from mnemo.core.extract.inbox.types import ExtractedPage


def _page(**kw) -> ExtractedPage:
    base = dict(
        slug="s", type="feedback", name="n", description="d",
        body="b", source_files=["bots/a/memory/x.md"], source_hash="sha256:x",
    )
    base.update(kw)
    return ExtractedPage(**base)


def test_single_source_live_page_auto_promotes(tmp_path):
    target = _target_path_for_page(_page(), tmp_path)
    assert target == tmp_path / "shared" / "feedback" / "s.md"


def test_single_source_backfill_page_stages_in_inbox(tmp_path):
    target = _target_path_for_page(_page(origin_backfill=True), tmp_path)
    assert target == tmp_path / "shared" / "_inbox" / "feedback" / "s.md"


def test_multi_source_still_stages_regardless_of_origin(tmp_path):
    page = _page(source_files=["bots/a/memory/x.md", "bots/b/memory/y.md"])
    assert _target_path_for_page(page, tmp_path) == tmp_path / "shared" / "_inbox" / "feedback" / "s.md"


def test_origin_backfill_defaults_false():
    assert _page().origin_backfill is False


def test_parser_flags_page_whose_sources_are_all_backfill():
    text = json.dumps({"pages": [{
        "slug": "s", "type": "feedback", "name": "n", "description": "d",
        "body": "b", "source_files": ["bots/a/memory/x.md"],
    }]})
    pages = _parse_pages_from_response(
        text, "feedback", backfill_sources=frozenset({"bots/a/memory/x.md"}),
    )
    assert pages[0].origin_backfill is True


def test_parser_leaves_mixed_origin_page_unflagged():
    text = json.dumps({"pages": [{
        "slug": "s", "type": "feedback", "name": "n", "description": "d",
        "body": "b", "source_files": ["bots/a/memory/x.md", "bots/a/memory/live.md"],
    }]})
    pages = _parse_pages_from_response(
        text, "feedback", backfill_sources=frozenset({"bots/a/memory/x.md"}),
    )
    assert pages[0].origin_backfill is False


def test_parser_without_backfill_sources_flags_nothing():
    text = json.dumps({"pages": [{
        "slug": "s", "type": "feedback", "name": "n", "description": "d",
        "body": "b", "source_files": ["bots/a/memory/x.md"],
    }]})
    pages = _parse_pages_from_response(text, "feedback")
    assert pages[0].origin_backfill is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_extract_backfill_gate.py -v`
Expected: FAIL with `TypeError: __init__() got an unexpected keyword argument 'origin_backfill'`.

- [ ] **Step 3: Write the implementation**

In `src/mnemo/core/extract/inbox/types.py`, add the field to `ExtractedPage` after `activates_on`:

```python
    # True when every source memory file carried metadata.origin: backfill.
    # Such pages are reconstructed from archived transcripts rather than
    # observed live, so they always stage in _inbox for review — see
    # inbox/paths.py::_target_path_for_page.
    origin_backfill: bool = False
```

In `src/mnemo/core/extract/inbox/paths.py`, replace the body of `_target_path_for_page`:

```python
def _target_path_for_page(page: ExtractedPage, vault_root: Path) -> Path:
    """Return the filesystem target for a page.

    Single-source pages go directly to the sacred dir (auto-promote).
    Multi-source pages stage in _inbox/ for review.
    Backfill-origin pages always stage, whatever their source count: they are
    reconstructed from archived transcripts, so a human confirms before they
    reach the sacred dir.

    Routes through ``_promoted_path`` / ``_inbox_path`` so the shared
    ``shared/<type>/<slug>.md`` shape lives in exactly one place.
    """
    if getattr(page, "origin_backfill", False):
        return _inbox_path(vault_root, page)
    if len(page.source_files) == 1:
        return _promoted_path(vault_root, page)
    return _inbox_path(vault_root, page)
```

In `src/mnemo/core/extract/__init__.py`, change the signature at line 170 and the construction at line 196:

```python
def _parse_pages_from_response(
    text: str,
    default_type: str,
    *,
    backfill_sources: frozenset[str] = frozenset(),
) -> list[inbox.ExtractedPage]:
```

Then, right after the existing `activates_on = _sanitize_llm_activates_on(...)` line, add:

```python
        origin_backfill = bool(backfill_sources) and all(
            s in backfill_sources for s in source_files
        )
```

and add `origin_backfill=origin_backfill,` to the `inbox.ExtractedPage(...)` call.

At the call site (line 387), build the set from the chunk's `MemoryFile` frontmatter. The loop variable `chunk` is a `list[scanner.MemoryFile]` and each has a parsed `frontmatter` dict. Replace:

```python
                pages = _parse_pages_from_response(response.text, type_name)
```

with:

```python
                chunk_backfill = frozenset(
                    source_paths.vault_relative_source(mf.path, vault_root)
                    for mf in chunk
                    if str(mf.frontmatter.get("origin") or "") == "backfill"
                )
                pages = _parse_pages_from_response(
                    response.text, type_name, backfill_sources=chunk_backfill,
                )
```

`source_paths` is already imported at the top of `extract/__init__.py`.

**Why `mf.frontmatter.get("origin")` and not `.get("metadata").get("origin")`.** Found during Task 5. `scanner.parse_frontmatter` is a **flat** `key: value` line reader — it splits every frontmatter line on the first `:` with no YAML and no nesting. A nested block therefore flattens: `metadata:` becomes `fm["metadata"] == ""`, and the indented `origin: backfill` becomes `fm["origin"] == "backfill"`. The nested lookup evaluates to `""` for every page, so the gate would silently never fire and every backfilled page would auto-promote into `shared/` — precisely the failure this gate exists to prevent.

Verify it yourself before implementing:

```
python3 -c "
from mnemo.core.extract.scanner import parse_frontmatter
fm, _ = parse_frontmatter(open('<a harvested memory file>').read())
print(repr(fm.get('metadata')), repr(fm.get('origin')))"
```

Two consequences for this task's tests:

1. `parse_frontmatter` returns a **tuple** `(fm, body)`, not a dict. Unpack it.
2. At least one test must reach the gate through a **real harvested file** — `harvest_session(...)` → `scanner._read_memory_file(...)` → `_parse_pages_from_response`. Tests that hand-build `ExtractedPage(origin_backfill=True)` verify the routing but cannot catch a broken frontmatter read, which is exactly how this bug survived plan review.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_extract_backfill_gate.py tests/unit/test_extract_inbox.py tests/unit/test_extract_orchestrator.py -v`
Expected: PASS. The two existing suites confirm the routing change did not disturb live extraction.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/extract/ tests/unit/test_extract_backfill_gate.py
git commit -m "feat(backfill): stage backfill-origin pages in _inbox, never auto-promote"
```

---

## Task 6b: Close the universal-promotion bypass

Task 6's gate is not the only door into `shared/`. **Universal promotion** moves a staged `_inbox` page into the sacred dir with no human review once its merged sources span `scoping.universalThreshold` distinct projects (default 2). Neither entry point consults `origin_backfill`:

- `extract/inbox/apply.py::_is_universal_promotion` — the apply-time branch
- `extract/__init__.py::_reconcile_universal_promotions` — the end-of-extract reconciler, which runs on *every* extract and rebuilds pages from on-disk `_inbox` files

Reproduced during Task 6: two harvested `origin: backfill` memory files under different agents, one page citing both → the gate correctly routes to `_inbox`, then universal promotion immediately writes `shared/feedback/<slug>.md`.

This matters specifically for backfill. A first sweep harvests many sessions across many projects at once, so cross-project slug collisions are *more* likely than in normal live extraction.

**Why the apply-time predicate alone is not enough.** Fixing only `_is_universal_promotion` leaves the page at `status="inbox"` with cross-project sources, so the reconciler promotes it on the next run anyway. The flag has to survive into the staged file so the reconciler can see it.

**Files:**
- Modify: `src/mnemo/core/extract/inbox/rendering.py` (`_render_page`)
- Modify: `src/mnemo/core/extract/inbox/sources.py` (the merge that rebuilds a page)
- Modify: `src/mnemo/core/extract/inbox/branches/universal_promotion.py` or `apply.py` (`_is_universal_promotion`)
- Modify: `src/mnemo/core/extract/__init__.py` (`_reconcile_universal_promotions`)
- Test: `tests/unit/test_extract_backfill_universal_gate.py`

- [ ] **Step 1: Write the failing test**

Start from the reproduction, which is already a working failing test. Create `tests/unit/test_extract_backfill_universal_gate.py`:

```python
"""A backfill page must not reach shared/ via universal promotion."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core import llm as llm_mod
from mnemo.core.extract import run_extraction


def _resp(pages):
    return llm_mod.LLMResponse(
        text=json.dumps({"pages": pages}), total_cost_usd=0.0,
        input_tokens=1, output_tokens=1, api_key_source="subscription", raw={},
    )


def _vault(tmp_path: Path, *, origin: str | None) -> Path:
    root = tmp_path / "vault"
    (root / "shared").mkdir(parents=True)
    (root / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(root)}))
    stamp = f"metadata:\n  origin: {origin}\n" if origin else ""
    for agent in ("alpha", "beta"):
        d = root / "bots" / agent / "memory"
        d.mkdir(parents=True)
        (d / "prefer-pathlib.md").write_text(
            f"---\nname: Prefer pathlib\ntype: feedback\n{stamp}---\n\nUse pathlib.\n",
            encoding="utf-8",
        )
    return root


def _cfg(root: Path) -> dict:
    return {"vaultRoot": str(root), "extraction": {
        "model": "m", "chunkSize": 10, "hintThreshold": 5,
        "preferAPI": False, "subprocessTimeout": 60, "costSoftCap": None}}


def _stub(monkeypatch):
    monkeypatch.setattr(llm_mod, "call", lambda *a, **k: _resp([{
        "slug": "prefer-pathlib", "type": "feedback", "name": "Prefer pathlib",
        "description": "d", "body": "Use pathlib.",
        "source_files": ["bots/alpha/memory/prefer-pathlib.md",
                         "bots/beta/memory/prefer-pathlib.md"],
    }]))


def test_backfill_page_never_reaches_sacred_dir(tmp_path, monkeypatch):
    root = _vault(tmp_path, origin="backfill")
    _stub(monkeypatch)
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists()
    assert (root / "shared" / "_inbox" / "feedback" / "prefer-pathlib.md").exists()


def test_backfill_page_still_blocked_on_a_second_extract(tmp_path, monkeypatch):
    """The reconciler runs on every extract — one pass proves nothing."""
    root = _vault(tmp_path, origin="backfill")
    _stub(monkeypatch)
    run_extraction(_cfg(root))
    run_extraction(_cfg(root))

    assert not (root / "shared" / "feedback" / "prefer-pathlib.md").exists()


def test_live_cross_project_page_still_universally_promotes(tmp_path, monkeypatch):
    """Control: the gate must not break universal promotion for live memory."""
    root = _vault(tmp_path, origin=None)
    _stub(monkeypatch)
    run_extraction(_cfg(root))
    run_extraction(_cfg(root))

    assert (root / "shared" / "feedback" / "prefer-pathlib.md").exists()
```

- [ ] **Step 2: Run to verify the first two fail**

Run: `python -m pytest tests/unit/test_extract_backfill_universal_gate.py -v`
Expected: the two backfill tests FAIL (page found in `shared/feedback/`), the live control test PASSES. If the control test fails, stop — the fixture does not reproduce normal promotion and the whole test is invalid.

- [ ] **Step 3: Carry the flag into the staged file**

In `rendering.py::_render_page`, emit a top-level `origin: backfill` key when `page.origin_backfill` is set. **Top-level, not nested** — `parse_frontmatter` is flat, so a nested block would read back as `""` (the same bug corrected in `32f8a5e`).

- [ ] **Step 4: Preserve the flag when sources merge**

`inbox/sources.py` rebuilds an `ExtractedPage` when merging source lists and copies fields explicitly — it will silently drop `origin_backfill`. Add it to the reconstruction.

- [ ] **Step 5: Teach both promotion paths to respect it**

`_is_universal_promotion` returns False for a backfill-origin page. In `_reconcile_universal_promotions`, the rebuilt page reads `origin_backfill=str(fm.get("origin") or "") == "backfill"` from the staged file's frontmatter, so the reconciler sees what Step 3 wrote.

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/unit/test_extract_backfill_universal_gate.py tests/unit/test_extract_inbox_universal_promotion.py tests/unit/test_extract_backfill_gate.py -v`
Expected: PASS. The existing universal-promotion suite is the guard that live behavior is unchanged.

Then the full suite: `python -m pytest tests/unit -q`

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/core/extract/ tests/unit/test_extract_backfill_universal_gate.py
git commit -m "fix(backfill): universal promotion must not bypass the origin gate"
```

---

## Task 6c: Project-type bypass, and the doctor advisory it breaks

Two loose ends found during Task 6b, both blocking a user-visible backfill.

### Part 1 — project-type files never touch the gate

`extract/promote.py` is a separate pipeline: project-type memory files are promoted **1:1 straight into `shared/project/<agent>__<slug>.md`** with no LLM, no clustering, no `_inbox`, and no origin check. Its module docstring says so outright. Reproduced in Task 6b: a harvested file stamped `metadata.origin: backfill` with `type: project` lands unreviewed in `shared/project/` on the next extract.

This is the largest hole of the six, because project-type is the *most* common thing backfill produces — the maintainer's own vault runs 90 project rules against 15 feedback.

**Design decision: stage them in `shared/_inbox/project/`. Do not make harvest refuse to emit `type: project`.** Refusing the type would gut the feature's most valuable category to dodge a routing bug, and every other type already stages. Staging is the consistent answer.

**Files:**
- Modify: `src/mnemo/core/extract/promote.py` (`_target_path`, `_render_project_page`)
- Test: `tests/unit/test_extract_promote_backfill.py`

- [ ] **Step 1: Write the failing test**

The `MemoryFile` objects `promote_projects` receives already carry a parsed `.frontmatter` dict, and `origin` sits at the top level of it (flat parser — same rule as everywhere else in this feature).

Test both directions:
- a project memory file stamped `origin: backfill` lands in `shared/_inbox/project/`, **not** `shared/project/`
- an unstamped project file still lands in `shared/project/` exactly as today
- the staged file's own frontmatter carries `origin: backfill` forward, so a later reader can still tell

Model the vault fixture on `tests/unit/test_extract_promote.py`.

- [ ] **Step 2: Verify it fails**

Run: `python -m pytest tests/unit/test_extract_promote_backfill.py -v`
Expected: the backfill test fails (file found in `shared/project/`); the unstamped control passes. If the control fails, stop — the fixture doesn't reproduce normal promotion.

- [ ] **Step 3: Route on origin**

In `_target_path`, return `vault_root / "shared" / "_inbox" / "project" / f"{_project_slug(file)}.md"` when `file.frontmatter.get("origin") == "backfill"`. Have `_render_project_page` emit a top-level `origin: backfill` line so the staged file stays self-describing.

Check whether `promote_projects`'s state bookkeeping (the `key = f"project/{_project_slug(file)}"` entry and its `status`) needs to distinguish staged from promoted. Report what you conclude rather than guessing.

- [ ] **Step 4: Verify**

Run: `python -m pytest tests/unit/test_extract_promote_backfill.py tests/unit/test_extract_promote.py -v`, then `python -m pytest tests/unit -q`.

### Part 2 — the doctor advisory now cries wolf

`cli/commands/doctor_checks/rules.py::_doctor_check_unpromoted_universal_candidates` warns about `status="inbox"` entries that cross the universal threshold, advising the user to "run `mnemo extract` to reconcile". Its docstring states the assumption directly: *"The reconciler clears the backlog on every extract; if this warning fires, the user is reading doctor between extracts."*

Task 6b makes that assumption false. Backfill pages now sit at `status="inbox"` **permanently and by design**, so the warning fires forever and its advice never works. Advisory-only, no correctness impact — but a permanent false alarm with useless advice trains users to ignore doctor.

- [ ] **Step 5: Separate the two populations**

Backfill-staged entries are not a backlog; they are the feature working. Exclude them from the existing warning and surface them separately as a neutral status line — e.g. `N backfill rules staged in _inbox awaiting review`, with the actual review command rather than `mnemo extract`.

Update the docstring — it currently documents an invariant that no longer holds.

- [ ] **Step 6: Test and commit**

Add a test that a vault whose only threshold-crossing entries are backfill-origin produces **no** backlog warning, and that a genuine live backlog still does.

```bash
git add src/mnemo/core/extract/promote.py src/mnemo/cli/commands/doctor_checks/rules.py tests/unit/
git commit -m "fix(backfill): stage project-type pages and stop the doctor false alarm"
```

---

## Task 7: CLI command

**Files:**
- Create: `src/mnemo/cli/commands/backfill.py`
- Modify: `src/mnemo/cli/parser.py`
- Modify: `src/mnemo/cli/commands/__init__.py`
- Test: `tests/unit/test_cli_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_cli_backfill.py`:

```python
"""backfill CLI: dry-run spends nothing, caps are honoured, ledger advances."""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from mnemo.core.backfill import discover, ledger
from mnemo.cli.commands import backfill as cmd


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60},
        "backfill": {"enabled": True, "installCap": 2, "minFileMutations": 1,
                     "autoOnFirstSession": True},
    }
    monkeypatch.setattr(cmd.cfg_mod, "load_config", lambda: cfg)

    made: list[discover.Transcript] = []
    for i, name in enumerate(["s1", "s2", "s3"]):
        p = tmp_path / f"{name}.jsonl"
        p.write_text('{"type":"user"}\n', encoding="utf-8")
        made.append(discover.Transcript(path=p, agent="alpha", cwd="/tmp/alpha", mtime=float(i)))
    made.sort(key=lambda t: t.mtime, reverse=True)
    monkeypatch.setattr(
        cmd.discover, "find_transcripts",
        lambda **kw: made[: kw["limit"]] if kw.get("limit") else made,
    )
    return cfg, vault, made


def _args(**kw) -> argparse.Namespace:
    base = dict(all=False, dry_run=False, project=None, limit=None,
                install_run=False, yes=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_dry_run_writes_nothing_and_calls_no_llm(env, monkeypatch, capsys):
    cfg, vault, _ = env

    def explode(*a, **k):
        raise AssertionError("dry run must not harvest")

    monkeypatch.setattr(cmd.harvest, "harvest_session", explode)

    assert cmd.cmd_backfill(_args(dry_run=True, all=True)) == 0
    assert not ledger.state_path(vault).exists()
    assert "3" in capsys.readouterr().out


def test_harvests_and_records_in_ledger(env, monkeypatch):
    cfg, vault, made = env
    calls: list[Path] = []

    def fake(jsonl_path, agent, config):
        calls.append(jsonl_path)
        return [Path("written.md")]

    monkeypatch.setattr(cmd.harvest, "harvest_session", fake)

    assert cmd.cmd_backfill(_args(all=True)) == 0
    assert len(calls) == 3

    led = ledger.load(vault)
    assert ledger.should_harvest(led, made[0].path) is False


def test_install_run_respects_install_cap(env, monkeypatch):
    cfg, vault, _ = env
    calls: list[Path] = []
    monkeypatch.setattr(
        cmd.harvest, "harvest_session",
        lambda p, a, c: calls.append(p) or [],
    )

    assert cmd.cmd_backfill(_args(install_run=True)) == 0
    assert len(calls) == 2  # installCap


def test_second_run_skips_already_done_sessions(env, monkeypatch):
    cfg, vault, _ = env
    calls: list[Path] = []
    monkeypatch.setattr(
        cmd.harvest, "harvest_session",
        lambda p, a, c: calls.append(p) or [],
    )

    cmd.cmd_backfill(_args(all=True))
    first = len(calls)
    cmd.cmd_backfill(_args(all=True))
    assert len(calls) == first  # nothing re-harvested


def test_one_failing_session_does_not_abort_the_run(env, monkeypatch):
    cfg, vault, made = env
    seen: list[Path] = []

    def flaky(jsonl_path, agent, config):
        seen.append(jsonl_path)
        if jsonl_path == made[0].path:
            raise RuntimeError("boom")
        return []

    monkeypatch.setattr(cmd.harvest, "harvest_session", flaky)

    assert cmd.cmd_backfill(_args(all=True)) == 0
    assert len(seen) == 3

    led = ledger.load(vault)
    assert led["sessions"][made[0].path.stem]["status"] == "failed"


def test_disabled_config_is_a_no_op(env, monkeypatch):
    cfg, vault, _ = env
    cfg["backfill"]["enabled"] = False
    monkeypatch.setattr(
        cmd.harvest, "harvest_session",
        lambda p, a, c: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert cmd.cmd_backfill(_args(all=True)) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_cli_backfill.py -v`
Expected: FAIL with `ImportError: cannot import name 'backfill' from 'mnemo.cli.commands'`.

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/cli/commands/backfill.py`:

```python
"""``mnemo backfill`` — populate a vault from archived session transcripts.

Two entry shapes:

- ``--install-run``: the capped, current-repo-only sweep that ``session_start``
  spawns once after install. Non-interactive by construction.
- everything else: an explicit sweep the user asks for, which prints an
  estimate and asks before spending.

Per-session failures are recorded and stepped over — one malformed transcript
must never abort a sweep.
"""
from __future__ import annotations

import argparse
import os

from mnemo.cli.parser import command
from mnemo.core import config as cfg_mod
from mnemo.core import errors as err_mod
from mnemo.core import paths
from mnemo.core.backfill import discover, harvest, ledger


def _select(args: argparse.Namespace, cfg: dict) -> list:
    """Which transcripts this invocation should consider, newest first."""
    backfill_cfg = cfg.get("backfill") or {}
    if args.install_run:
        from mnemo.core import agent as agent_mod

        project = agent_mod.resolve_canonical_agent(os.getcwd()).name
        limit = int(backfill_cfg.get("installCap", 20))
        return discover.find_transcripts(project=project, limit=limit)

    project = args.project
    if project is None and not args.all:
        from mnemo.core import agent as agent_mod

        project = agent_mod.resolve_canonical_agent(os.getcwd()).name
    limit = int(args.limit) if args.limit else None
    return discover.find_transcripts(project=project, limit=limit)


@command("backfill")
def cmd_backfill(args: argparse.Namespace) -> int:
    cfg = cfg_mod.load_config()
    backfill_cfg = cfg.get("backfill") or {}
    if not backfill_cfg.get("enabled", True):
        print("backfill: disabled in config (backfill.enabled = false)")
        return 0

    vault_root = paths.vault_root(cfg)
    candidates = _select(args, cfg)
    led = ledger.load(vault_root)
    todo = [t for t in candidates if ledger.should_harvest(led, t.path)]

    if not todo:
        print("backfill: nothing to do — every transcript is already harvested.")
        return 0

    projects = sorted({t.agent for t in todo})
    print(
        f"backfill: {len(todo)} session(s) across {len(projects)} project(s): "
        f"{', '.join(projects)}"
    )
    print(f"          ~{_estimate_input_tokens(todo):,} input tokens, "
          f"{len(todo)} LLM call(s) via your existing claude CLI.")

    if args.dry_run:
        for t in todo:
            print(f"          would harvest {t.agent}/{t.path.name}")
        print("backfill: dry run — nothing written.")
        return 0

    if not args.install_run and not args.yes:
        try:
            reply = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("backfill: cancelled.")
            return 0

    produced = 0
    harvested = 0
    for t in todo:
        try:
            written = harvest.harvest_session(t.path, t.agent, cfg)
        except Exception as exc:  # one bad transcript must not end the sweep
            ledger.mark_failed(led, t.path, str(exc))
            err_mod.log_error(vault_root, "backfill.harvest", exc)
            ledger.save(vault_root, led)
            if _is_rate_limited(exc):
                print("backfill: rate limited — stopping. Rerun to resume.")
                break
            continue
        ledger.mark_done(led, t.path, produced=len(written))
        ledger.save(vault_root, led)
        harvested += 1
        produced += len(written)

    print(f"backfill: harvested {harvested} session(s), wrote {produced} memory file(s).")
    if produced:
        print("          run `mnemo extract` to turn them into rules.")
    return 0


def _estimate_input_tokens(transcripts: list) -> int:
    """Rough input-token estimate for a sweep.

    Transcript bytes are a usable proxy: flattening drops jsonl scaffolding but
    keeps the text, and ~4 bytes per token is the standard approximation. This
    only has to be good enough for a user to judge whether to proceed, so it
    deliberately avoids tokenizing anything.
    """
    total_bytes = 0
    for t in transcripts:
        try:
            total_bytes += t.path.stat().st_size
        except OSError:
            continue
    return total_bytes // 4


def _is_rate_limited(exc: Exception) -> bool:
    from mnemo.core import llm

    return isinstance(exc, llm.LLMSubprocessError) and llm._is_rate_limit(str(exc))
```

In `src/mnemo/cli/commands/__init__.py`, add the import alongside the others so the `@command` decorator registers at import time:

```python
from mnemo.cli.commands import backfill  # noqa: F401
```

In `src/mnemo/cli/parser.py`, add the subparser inside `_build_parser` next to the other command definitions:

```python
    bf = sub.add_parser("backfill", help="populate the vault from past session transcripts")
    bf.add_argument("--all", action="store_true", help="every project, not just this repo")
    bf.add_argument("--dry-run", action="store_true", help="show what would be harvested, write nothing")
    bf.add_argument("--project", type=str, default=None, help="limit to one project by name")
    bf.add_argument("--limit", type=int, default=None, help="cap the number of sessions")
    bf.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    bf.add_argument("--install-run", action="store_true", help=argparse.SUPPRESS)
```

`argparse` converts `--dry-run` to `args.dry_run` and `--install-run` to `args.install_run`, matching the code above.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_cli_backfill.py tests/unit/test_cli_dispatch.py -v`
Expected: PASS. `test_cli_dispatch.py` verifies the registry; if it asserts an exact command list, add `backfill` to it.

- [ ] **Step 5: Verify the command is reachable**

Run: `python -m mnemo backfill --dry-run --limit 1`
Expected: either a candidate listing or "nothing to do" — no traceback.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/cli/ tests/unit/test_cli_backfill.py
git commit -m "feat(backfill): add mnemo backfill command"
```

---

## Task 8: First-run hook

The hook must return immediately. Harvest makes one LLM call per session; running it inline would stall session start for minutes. Spawn detached, exactly as `session_end` does for briefings (`_spawn_detached_briefing`, `src/mnemo/hooks/session_end.py:139`).

**Files:**
- Modify: `src/mnemo/hooks/session_start.py`
- Test: `tests/unit/test_hook_session_start_backfill.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_hook_session_start_backfill.py`:

```python
"""session_start fires the install backfill exactly once, detached."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.backfill import ledger
from mnemo.hooks import session_start


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / ".mnemo").mkdir(parents=True)
    return root


def _cfg(vault: Path, **over) -> dict:
    bf = {"enabled": True, "installCap": 20, "minFileMutations": 1,
          "autoOnFirstSession": True}
    bf.update(over)
    return {"vaultRoot": str(vault), "backfill": bf}


def test_spawns_once_and_marks_the_ledger(vault, monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda: spawned.append(["x"]))

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault)
    assert len(spawned) == 1
    assert ledger.load(vault)["installRunDone"] is True


def test_second_session_does_not_spawn_again(vault, monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda: spawned.append(["x"]))

    session_start._maybe_schedule_install_backfill(_cfg(vault), vault)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault)
    assert len(spawned) == 1


def test_disabled_auto_flag_never_spawns(vault, monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda: spawned.append(["x"]))

    session_start._maybe_schedule_install_backfill(
        _cfg(vault, autoOnFirstSession=False), vault,
    )
    assert spawned == []


def test_disabled_backfill_never_spawns(vault, monkeypatch):
    spawned: list[list[str]] = []
    monkeypatch.setattr(session_start, "_spawn_detached_backfill", lambda: spawned.append(["x"]))

    session_start._maybe_schedule_install_backfill(_cfg(vault, enabled=False), vault)
    assert spawned == []


def test_a_spawn_failure_is_swallowed(vault, monkeypatch):
    def boom():
        raise OSError("no fork for you")

    monkeypatch.setattr(session_start, "_spawn_detached_backfill", boom)
    session_start._maybe_schedule_install_backfill(_cfg(vault), vault)  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_hook_session_start_backfill.py -v`
Expected: FAIL with `AttributeError: module 'mnemo.hooks.session_start' has no attribute '_spawn_detached_backfill'`.

- [ ] **Step 3: Write the implementation**

In `src/mnemo/hooks/session_start.py`, add both functions at module level, next to the other helpers (before `main`). Model `_spawn_detached_backfill` on `session_end._spawn_detached_briefing` — open that function and copy its detach flags exactly, since they differ by platform:

```python
def _spawn_detached_backfill() -> None:
    """Fire-and-forget background install backfill via subprocess.Popen.

    Invokes `mnemo backfill --install-run`. Detach semantics match
    session_end's briefing spawn — see hooks/session_end.py:139.
    """
    import subprocess

    from mnemo._selfexec import self_argv

    argv = self_argv("backfill", "--install-run")
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(argv, **kwargs)


def _maybe_schedule_install_backfill(cfg: dict, vault_root) -> None:
    """Spawn the one-time install backfill, at most once per vault.

    The ledger marker is written *before* the spawn so a crash mid-harvest
    cannot cause the sweep to restart on every subsequent session. Resuming
    is the explicit `mnemo backfill` command's job, not the hook's.
    """
    try:
        from mnemo.core.backfill import ledger as _ledger

        backfill_cfg = cfg.get("backfill") or {}
        if not backfill_cfg.get("enabled", True):
            return
        if not backfill_cfg.get("autoOnFirstSession", True):
            return

        led = _ledger.load(vault_root)
        if led.get("installRunDone"):
            return
        led["installRunDone"] = True
        _ledger.save(vault_root, led)

        _spawn_detached_backfill()
    except Exception as exc:
        try:
            from mnemo.core import errors as _e

            _e.log_error(vault_root, "session_start.backfill", exc)
        except Exception:
            pass
```

Then call it from `main()`. Place the call in the same `try` block that already runs the post-scaffold work (near the `rule_activation` / `reflex_index` block around line 250), after scaffolding is guaranteed to have run:

```python
            _maybe_schedule_install_backfill(cfg, vault_root)
```

Confirm the local variable holding the vault root at that point — it is `vault_root` in `main`'s scope; if it is named differently there, use that name.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_hook_session_start_backfill.py tests/unit/test_session_start_injection.py -v`
Expected: PASS. If `test_session_start_injection.py` does not exist under that name, run the whole hook suite instead: `python -m pytest tests/unit -k session_start -v`.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/session_start.py tests/unit/test_hook_session_start_backfill.py
git commit -m "feat(backfill): spawn the capped install backfill on first session"
```

---

## Task 9: End-to-end test

One test that walks a real transcript all the way to a staged rule, proving the seam holds. Marked opt-in like the existing e2e tests.

**Files:**
- Create: `tests/e2e/test_backfill_pipeline.py`
- Test: itself

- [ ] **Step 1: Check how existing e2e tests opt in**

Run: `python -m pytest tests/e2e -v` and read `tests/e2e/` — the two existing tests are skipped by default. Reuse the same marker or env guard they use; do not invent a new mechanism.

- [ ] **Step 2: Write the test**

Create `tests/e2e/test_backfill_pipeline.py` (replace `SKIP_GUARD` with whatever the existing e2e tests use):

```python
"""End-to-end: archived transcript → memory file → staged rule in _inbox."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.core import llm
from mnemo.core.backfill import harvest
from mnemo.core.extract import _parse_pages_from_response, source_paths
from mnemo.core.extract.inbox.paths import _target_path_for_page


def test_transcript_becomes_a_staged_rule(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    cfg = {
        "vaultRoot": str(vault),
        "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60},
        "backfill": {"minFileMutations": 1},
    }

    events = [
        {"type": "user", "message": {"role": "user",
         "content": [{"type": "text", "text": "never use any in this codebase"}]}},
        {"type": "assistant", "message": {"role": "assistant",
         "content": [{"type": "tool_use", "name": "Edit", "id": "t0", "input": {}}]}},
    ]
    transcript = tmp_path / "sess-e2e.jsonl"
    transcript.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")

    monkeypatch.setattr(harvest.llm, "call", lambda *a, **k: llm.LLMResponse(
        text=json.dumps({"pages": [{
            "slug": "no-any-types", "type": "feedback", "name": "No any",
            "description": "Strict types are the house rule",
            "body": "The codebase has strict types; `any` is never acceptable.",
        }]}),
        total_cost_usd=0.0, input_tokens=1, output_tokens=1,
        api_key_source="subscription", raw={},
    ))

    written = harvest.harvest_session(transcript, "alpha", cfg)
    assert len(written) == 1

    # The memory file carries the origin stamp the extraction gate reads.
    text = written[0].read_text(encoding="utf-8")
    assert "origin: backfill" in text

    # A page extracted from that source stages in _inbox, not shared/.
    rel = source_paths.vault_relative_source(written[0], vault)
    pages = _parse_pages_from_response(
        json.dumps({"pages": [{
            "slug": "no-any-types", "type": "feedback", "name": "No any",
            "description": "d", "body": "b", "source_files": [rel],
        }]}),
        "feedback",
        backfill_sources=frozenset({rel}),
    )
    target = _target_path_for_page(pages[0], vault)
    assert target == vault / "shared" / "_inbox" / "feedback" / "no-any-types.md"
```

Drop the unused `scan` import if the real scanner entry point is named differently — it is not needed by the assertions.

- [ ] **Step 3: Run it**

Run: `python -m pytest tests/e2e/test_backfill_pipeline.py -v` (with whatever env var the e2e suite requires)
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_backfill_pipeline.py
git commit -m "test(backfill): end-to-end transcript to staged rule"
```

---

## Task 10: Docs

**Files:**
- Modify: `docs/configuration.md`
- Modify: `README.md`
- Modify: `docs/getting-started.md`
- Test: `tests/unit/test_docs_accuracy.py` (existing — must keep passing)

- [ ] **Step 1: Run the docs accuracy suite to see what it demands**

Run: `python -m pytest tests/unit/test_docs_accuracy.py -v`
Expected: FAIL — it checks documented config keys against `DEFAULTS`, and `backfill.*` is now undocumented.

- [ ] **Step 2: Add the config rows**

In `docs/configuration.md`, add a `backfill` section. Use **fully qualified keys** — every row must be copyable into `mnemo.config.json` as-is. That convention is enforced by the test suite.

| Key | Default | What it does |
|---|---|---|
| `backfill.enabled` | `true` | Master switch for all backfill. |
| `backfill.installCap` | `20` | Max sessions the automatic first-run sweep harvests. |
| `backfill.minFileMutations` | `1` | Skip sessions with fewer file edits than this — no LLM call is made. |
| `backfill.autoOnFirstSession` | `true` | Run the capped sweep once, on the first session after install. |

- [ ] **Step 3: Document the command**

In `README.md`, extend the "Check it worked" section — after install, mnemo now backfills the current repo automatically, so a new user sees rules immediately. State plainly that it makes one LLM call per session through their existing `claude` CLI, and that `mnemo backfill --all` covers their other projects.

In `docs/getting-started.md`, add `mnemo backfill` to the command list with its flags: `--all`, `--dry-run`, `--project`, `--limit`, `--yes`.

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest tests/ -q`
Expected: PASS — 1690+ tests, 2 skipped (the opt-in e2e ones), zero regressions.

- [ ] **Step 5: Commit**

```bash
git add docs/ README.md
git commit -m "docs(backfill): document the backfill command and config"
```

---

## Task 11: Real-data validation

Not a code task. The spec's next workstream depends on the numbers this produces, so do not skip it.

- [ ] **Step 1: Dry-run against the real vault**

Run: `python -m mnemo backfill --all --dry-run`
Expected: a session count in the hundreds and a project list matching `~/.claude/projects/`. No writes.

- [ ] **Step 2: Harvest one project and inspect**

Run: `python -m mnemo backfill --project mnemo --limit 5 --yes`

Then read every memory file it produced. Judge each: is this a durable lesson, or session narration the prompt was supposed to reject?

- [ ] **Step 3: Record the numbers**

Write down: sessions harvested, memory files produced, files-per-session, and the share you judged worth keeping. That keep-rate compared against the vault's existing 87% archive rate is the entry evidence for the extraction-precision workstream.

- [ ] **Step 4: Decide on the salience filter**

The spec deferred salience pre-filtering pending real cost data. With per-session token cost now measurable via `mnemo telemetry`, decide whether the `--all` sweep needs it. Record the decision in the next spec, not here.

---

## Notes for the implementer

**Two hard project constraints, both easy to violate by habit:**

1. **No third-party Python dependencies.** stdlib only. This is a shipped promise in the README's privacy section, not a preference.
2. **No network calls.** The `claude` CLI subprocess is the sole exception and it already exists — do not add another.

**Cross-platform.** Windows is a supported, tested platform. Use `pathlib`, never assume `/`, and mirror the platform branching in `session_end._spawn_detached_briefing` for any process spawning.

**Where things live:** `src/mnemo/core/` is the library, `src/mnemo/cli/commands/` is one file per command, `src/mnemo/hooks/` is the four Claude Code event handlers. Tests mirror that split under `tests/unit/`.
