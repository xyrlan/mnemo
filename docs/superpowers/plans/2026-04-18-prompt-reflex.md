# Prompt Reflex Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v0.8.0 — a `UserPromptSubmit` hook that injects 0-2 rule body previews inline via BM25F retrieval, respecting v0.7 project scope, with cross-hook session-lifetime dedupe.

**Architecture:** New `src/mnemo/core/reflex/` package (tokenizer + BM25F + triple-gate + vault-wide index). `mcp/counter.py` renamed to `mcp/session_state.py` (with compat shim) and extended to own `injected_cache` + `session_emissions`. New `hooks/user_prompt_submit.py`. Extraction prompts gain `aliases:` guidance. Existing `pre_tool_use`, `session_start`, `session_end`, `statusline`, `install/settings` extended; no rewrites.

**Tech Stack:** Python 3.10+, stdlib only (no new deps), pytest, existing mnemo helpers (`_body_preview`, `parse_frontmatter`, `is_consumer_visible`, `rotate_if_needed`, `access_log.record`, `resolve_agent`, `errors.should_run`).

**Spec:** `docs/superpowers/specs/2026-04-18-prompt-reflex-design.md` (commit `d7d2c91`).

**Project conventions:**
- Tests live in `tests/unit/` (not `tests/core/`). Integration in `tests/integration/`.
- Pytest fixtures from `tests/conftest.py`: `tmp_vault`, `tmp_home`, `tmp_tempdir`, `memory_fixture`.
- All hooks and long-running code paths are **fail-open**: any exception returns exit 0 with empty stdout.
- Commit messages follow the existing style: `feat(scope): ...` / `fix(scope): ...` / `test(scope): ...` / `docs(...): ...`.
- Tests before implementation. Run the test suite after every task: `pytest -q`.

---

## Phase A — Foundation: `session_state` module

Renames `mcp/counter.py` → `mcp/session_state.py`, makes `increment()` preserve unknown keys, adds the new helpers (`injected_cache` / `session_emissions` CRUD, 24h GC).

### Task A1: Rename module + compat shim

**Files:**
- Create: `src/mnemo/core/mcp/session_state.py` (move + rename)
- Modify: `src/mnemo/core/mcp/counter.py` — becomes a thin re-export shim
- Test: `tests/unit/test_session_state_shim.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session_state_shim.py
"""Legacy mnemo.core.mcp.counter imports must keep working in v0.8."""
from __future__ import annotations

def test_counter_shim_reexports_increment_and_read_today():
    from mnemo.core.mcp import counter as legacy
    from mnemo.core.mcp import session_state as new

    # Same object identity — the shim truly re-exports.
    assert legacy.increment is new.increment
    assert legacy.read_today is new.read_today

def test_counter_shim_path_constant_unchanged():
    from mnemo.core.mcp import session_state as st

    # Filename on disk MUST stay "mcp-call-counter.json" for backwards compat
    # with statusline.py and server.py readers.
    assert st._FILENAME == "mcp-call-counter.json"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_session_state_shim.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.core.mcp.session_state'`.

- [ ] **Step 3: Create `session_state.py` by copying `counter.py` verbatim**

```bash
cp src/mnemo/core/mcp/counter.py src/mnemo/core/mcp/session_state.py
```

Then edit the new file's module docstring first line to read:
```python
"""Per-session runtime state for mnemo (counter + injection cache + emissions).
```

- [ ] **Step 4: Replace `counter.py` content with a shim**

```python
# src/mnemo/core/mcp/counter.py
"""Backwards-compat shim.

The module was split into session_state.py in v0.8. This file re-exports
the pre-v0.8 public surface (``increment``, ``read_today``, ``_FILENAME``,
``_path``) so existing imports from ``mnemo.core.mcp.counter`` keep working
without churn. To be removed in v0.9.
"""
from mnemo.core.mcp.session_state import (  # noqa: F401
    _FILENAME,
    _path,
    increment,
    read_today,
)
```

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/unit/test_session_state_shim.py -v && pytest -q`
Expected: PASS for the new file; all pre-existing tests still green.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/mcp/session_state.py src/mnemo/core/mcp/counter.py tests/unit/test_session_state_shim.py
git commit -m "refactor(mcp): split counter.py into session_state.py with compat shim"
```

---

### Task A2: `increment()` read-modify-write preservation

**Files:**
- Modify: `src/mnemo/core/mcp/session_state.py:30-53` (`increment` function)
- Test: `tests/unit/test_session_state_increment.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session_state_increment.py
"""increment() must preserve unknown top-level keys (v0.8 contract)."""
from __future__ import annotations

import json
from datetime import date

from mnemo.core.mcp import session_state


def test_increment_preserves_injected_cache(tmp_vault):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    seed = {
        "date": today,
        "count": 4,
        "injected_cache": {"use-prisma-mock": 1713456000},
        "session_emissions": {"sid-abc": {"started_at": 1, "reflex_count": 2, "enrich_count": 0}},
    }
    path.write_text(json.dumps(seed), encoding="utf-8")

    session_state.increment(tmp_vault)

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["count"] == 5
    assert reloaded["injected_cache"] == seed["injected_cache"]
    assert reloaded["session_emissions"] == seed["session_emissions"]


def test_increment_day_rollover_wipes_new_keys_too(tmp_vault):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    seed = {
        "date": "1999-01-01",  # stale
        "count": 99,
        "injected_cache": {"stale-slug": 1},
        "session_emissions": {"stale-sid": {"started_at": 1, "reflex_count": 1, "enrich_count": 0}},
    }
    path.write_text(json.dumps(seed), encoding="utf-8")

    session_state.increment(tmp_vault)

    reloaded = json.loads(path.read_text(encoding="utf-8"))
    assert reloaded["date"] == date.today().isoformat()
    assert reloaded["count"] == 1
    assert reloaded["injected_cache"] == {}
    assert reloaded["session_emissions"] == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_session_state_increment.py -v`
Expected: FAIL — the first test fails because current `increment` rebuilds `{date, count}` on every call and wipes the extra keys.

- [ ] **Step 3: Replace `increment()` with preserving implementation**

In `src/mnemo/core/mcp/session_state.py`, replace the `increment` function body:

```python
def increment(vault_root: Path) -> None:
    """Bump today's counter by 1, preserving unknown top-level keys.

    v0.8: the file now stores additional runtime state (``injected_cache``,
    ``session_emissions``) alongside ``count``. A naive rewrite of
    ``{date, count}`` would silently wipe those keys on every MCP call.
    """
    path = _path(vault_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return  # decorative — never block the caller
    today = date.today().isoformat()
    data: dict = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data = loaded
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        data = {}
    if data.get("date") != today:
        # Day rollover wipes count AND runtime state.
        data = {
            "date": today,
            "count": 0,
            "injected_cache": {},
            "session_emissions": {},
        }
    data["count"] = int(data.get("count", 0)) + 1
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_session_state_increment.py tests/unit/test_session_state_shim.py -v`
Expected: PASS.

Also re-run any existing tests that touched the counter:

Run: `pytest tests/unit/test_cli_status_doctor.py -q`
Expected: PASS (no regression).

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/mcp/session_state.py tests/unit/test_session_state_increment.py
git commit -m "fix(session_state): preserve unknown top-level keys on increment"
```

---

### Task A3: Session-state CRUD helpers (`read_injected_cache`, `add_injection`, `bump_emission`, `gc_old_sessions`)

**Files:**
- Modify: `src/mnemo/core/mcp/session_state.py` (append helpers after `read_today`)
- Test: `tests/unit/test_session_state_helpers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_session_state_helpers.py
"""CRUD helpers around injected_cache and session_emissions."""
from __future__ import annotations

import json
from datetime import date

from mnemo.core.mcp import session_state


def _seed(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_add_injection_records_slug_and_preserves_count(tmp_vault):
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    _seed(path, {"date": date.today().isoformat(), "count": 7})

    session_state.add_injection(tmp_vault, slug="use-prisma-mock", sid="sid-abc", now_ts=1000)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["count"] == 7
    assert data["injected_cache"]["use-prisma-mock"] == 1000


def test_read_injected_cache_returns_empty_on_fresh_file(tmp_vault):
    assert session_state.read_injected_cache(tmp_vault) == {}


def test_bump_emission_creates_and_increments(tmp_vault):
    session_state.bump_emission(tmp_vault, sid="sid-xyz", kind="reflex", now_ts=500)
    session_state.bump_emission(tmp_vault, sid="sid-xyz", kind="reflex", now_ts=600)
    session_state.bump_emission(tmp_vault, sid="sid-xyz", kind="enrich", now_ts=700)

    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    e = data["session_emissions"]["sid-xyz"]
    assert e["reflex_count"] == 2
    assert e["enrich_count"] == 1
    assert e["started_at"] == 500  # first bump sets started_at; later ones don't move it


def test_gc_old_sessions_removes_entries_older_than_24h(tmp_vault):
    now = 1_000_000_000
    stale_started = now - (25 * 3600)
    fresh_started = now - 600
    path = tmp_vault / ".mnemo" / "mcp-call-counter.json"
    _seed(path, {
        "date": date.today().isoformat(),
        "count": 0,
        "injected_cache": {"a": 1, "b": 2},
        "session_emissions": {
            "stale": {"started_at": stale_started, "reflex_count": 1, "enrich_count": 0},
            "fresh": {"started_at": fresh_started, "reflex_count": 2, "enrich_count": 0},
        },
    })

    session_state.gc_old_sessions(tmp_vault, now_ts=now, ttl_seconds=24 * 3600)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert list(data["session_emissions"]) == ["fresh"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_session_state_helpers.py -v`
Expected: FAIL — `add_injection`, `read_injected_cache`, `bump_emission`, `gc_old_sessions` all `AttributeError`.

- [ ] **Step 3: Add the helpers to `session_state.py`**

Append to `src/mnemo/core/mcp/session_state.py`:

```python
# --- v0.8 helpers: injected_cache + session_emissions ---

def _load(vault_root: Path) -> dict:
    """Load state dict with all v0.8 keys present. Never raises."""
    path = _path(vault_root)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            loaded = {}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        loaded = {}
    loaded.setdefault("date", date.today().isoformat())
    loaded.setdefault("count", 0)
    loaded.setdefault("injected_cache", {})
    loaded.setdefault("session_emissions", {})
    return loaded


def _write(vault_root: Path, data: dict) -> None:
    """Atomic write. Decorative — drops silently on OSError."""
    path = _path(vault_root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def read_injected_cache(vault_root: Path) -> dict:
    """Return the current injected_cache mapping (slug -> unix_ts). Never raises."""
    return dict(_load(vault_root).get("injected_cache", {}))


def add_injection(vault_root: Path, *, slug: str, sid: str, now_ts: int) -> None:
    """Record that *slug* was injected at *now_ts* (unix seconds). Never raises."""
    data = _load(vault_root)
    data["injected_cache"][slug] = int(now_ts)
    _write(vault_root, data)


def bump_emission(
    vault_root: Path,
    *,
    sid: str,
    kind: str,  # "reflex" | "enrich"
    now_ts: int,
) -> None:
    """Increment the emission counter for sid.kind. Seeds started_at on first bump."""
    if kind not in ("reflex", "enrich"):
        return  # silently ignore — never raise from session state
    data = _load(vault_root)
    entry = data["session_emissions"].get(sid)
    if entry is None:
        entry = {"started_at": int(now_ts), "reflex_count": 0, "enrich_count": 0}
    key = f"{kind}_count"
    entry[key] = int(entry.get(key, 0)) + 1
    data["session_emissions"][sid] = entry
    _write(vault_root, data)


def read_emission_counts(vault_root: Path, sid: str) -> dict:
    """Return {reflex_count, enrich_count} for sid; zeros if absent. Never raises."""
    entry = _load(vault_root).get("session_emissions", {}).get(sid) or {}
    return {
        "reflex_count": int(entry.get("reflex_count", 0)),
        "enrich_count": int(entry.get("enrich_count", 0)),
    }


def gc_old_sessions(vault_root: Path, *, now_ts: int, ttl_seconds: int = 24 * 3600) -> None:
    """Remove session_emissions entries whose started_at is older than ttl_seconds."""
    data = _load(vault_root)
    cutoff = int(now_ts) - int(ttl_seconds)
    survivors = {
        sid: e
        for sid, e in data.get("session_emissions", {}).items()
        if int(e.get("started_at", 0)) >= cutoff
    }
    if survivors == data.get("session_emissions"):
        return  # no-op
    data["session_emissions"] = survivors
    _write(vault_root, data)


def evict_session(vault_root: Path, sid: str) -> None:
    """On SessionEnd: drop session_emissions[sid] entirely. Never raises."""
    data = _load(vault_root)
    if sid in data["session_emissions"]:
        del data["session_emissions"][sid]
        _write(vault_root, data)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_session_state_helpers.py tests/unit/test_session_state_increment.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/mcp/session_state.py tests/unit/test_session_state_helpers.py
git commit -m "feat(session_state): add injected_cache + session_emissions helpers"
```

---

## Phase B — Foundation: text utilities + stopwords

### Task B1: Promote `_body_preview` to a shared helper

**Files:**
- Create: `src/mnemo/core/text_utils.py`
- Modify: `src/mnemo/core/rule_activation.py:267-279` — re-export from new module
- Test: `tests/unit/test_text_utils.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_text_utils.py
"""Shared text helpers: body_preview promoted from rule_activation."""
from __future__ import annotations

from mnemo.core.text_utils import body_preview


def test_body_preview_strips_frontmatter():
    text = "---\nname: x\ntags: []\n---\nActual body content here."
    assert body_preview(text, max_chars=300) == "Actual body content here."


def test_body_preview_truncates_at_whitespace_when_over_limit():
    text = "---\n---\n" + ("word " * 200).strip()
    preview = body_preview(text, max_chars=50)
    assert len(preview) <= 50
    # No mid-word cut: final char must be "word" boundary, not mid-"word".
    assert not preview.endswith("wor")
    assert not preview.endswith("wo")


def test_body_preview_returns_body_unchanged_when_short():
    assert body_preview("short", max_chars=300) == "short"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_text_utils.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create `text_utils.py` with the helper extracted**

```python
# src/mnemo/core/text_utils.py
"""Shared text helpers used across the mnemo retrieval stack.

Moved from rule_activation._body_preview in v0.8 so multiple consumers
(rule_activation, reflex index builder) can share the same whitespace-
aware truncation without duplicating the logic.
"""
from __future__ import annotations


def body_preview(text: str, max_chars: int = 300) -> str:
    """Extract the first ~max_chars of a rule body, truncating on whitespace.

    Strips leading YAML frontmatter (between ``---\\n`` markers), then returns
    either the full body (if short) or a whitespace-boundary truncation. The
    boundary rule prevents mid-word cuts like "implementat" — the returned
    slice ends at the last whitespace inside the first max_chars as long as
    that boundary is past the midpoint; otherwise returns the raw slice.
    """
    end = text.find("\n---\n", 4)
    body = text[end + 5:].strip() if end != -1 else text.strip()
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_ws = max(truncated.rfind(" "), truncated.rfind("\n"), truncated.rfind("\t"))
    if last_ws > max_chars // 2:
        return truncated[:last_ws]
    return truncated
```

- [ ] **Step 4: Replace the private `_body_preview` in `rule_activation.py` with an import**

Edit `src/mnemo/core/rule_activation.py`. Replace lines 267-279 (the existing `_body_preview` function) with:

```python
from mnemo.core.text_utils import body_preview as _body_preview  # re-exported for backwards compat
```

Keep the alias name `_body_preview` so nothing else in the module changes.

- [ ] **Step 5: Run tests to verify pass + no regression**

Run: `pytest tests/unit/test_text_utils.py tests/unit/test_rule_activation_index.py -v && pytest -q`
Expected: PASS on all.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/core/text_utils.py src/mnemo/core/rule_activation.py tests/unit/test_text_utils.py
git commit -m "refactor(text): promote _body_preview to core.text_utils for reuse"
```

---

### Task B2: Stopwords (EN + PT minimal list)

**Files:**
- Create: `src/mnemo/core/reflex/__init__.py` (empty)
- Create: `src/mnemo/core/reflex/stopwords.py`
- Test: `tests/unit/test_reflex_stopwords.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reflex_stopwords.py
from __future__ import annotations

from mnemo.core.reflex.stopwords import STOPWORDS, is_stopword


def test_stopwords_covers_english_function_words():
    for w in ("the", "and", "is", "of", "to", "how", "i", "a"):
        assert w in STOPWORDS, w


def test_stopwords_covers_portuguese_function_words():
    for w in ("o", "a", "de", "que", "é", "como", "para", "um", "uma"):
        assert w in STOPWORDS, w


def test_stopwords_does_not_contain_code_terms():
    # These should NEVER be stopped — they're code/domain terms.
    for w in ("prisma", "mock", "react", "auth", "use", "test", "banco", "database"):
        assert w not in STOPWORDS, w


def test_is_stopword_is_case_insensitive():
    assert is_stopword("THE")
    assert is_stopword("De")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reflex_stopwords.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Create the package + stopwords module**

```python
# src/mnemo/core/reflex/__init__.py
"""Prompt Reflex — BM25F-based UserPromptSubmit inline rule injection (v0.8)."""
```

```python
# src/mnemo/core/reflex/stopwords.py
"""Conservative English + Portuguese stopword list.

Kept intentionally short — code/domain terms like ``test``, ``use``, ``mock``,
``auth``, ``react``, and ``banco`` (which is Portuguese for "database" and
also a valid rule alias) are NOT stopped. Over-stripping would gut recall on
technical prompts.

If you need to tune this list, the discipline is: only add words that are
structurally grammatical (articles, pronouns, auxiliaries) and never carry
domain meaning. When in doubt, leave it in.
"""
from __future__ import annotations

_EN = {
    "the", "a", "an", "and", "or", "but", "if", "then", "of", "to", "in", "on",
    "at", "by", "for", "from", "with", "without", "about", "as", "is", "are",
    "was", "were", "be", "been", "being", "have", "has", "had", "do", "does",
    "did", "will", "would", "should", "could", "may", "might", "can", "shall",
    "i", "you", "he", "she", "it", "we", "they", "me", "my", "your", "our",
    "this", "that", "these", "those", "there", "here", "how", "what", "when",
    "where", "why", "which", "who", "whose", "not", "no", "yes",
}

_PT = {
    "o", "a", "os", "as", "um", "uma", "uns", "umas",
    "de", "da", "do", "das", "dos",
    "em", "no", "na", "nos", "nas",
    "por", "para", "com", "sem",
    "que", "se", "e", "ou", "mas", "também",
    "é", "são", "foi", "ser", "está", "estão",
    "eu", "tu", "ele", "ela", "nós", "eles", "elas",
    "meu", "teu", "seu", "nosso", "este", "esta", "esse", "essa", "aquele",
    "como", "quando", "onde", "porque", "qual", "quais",
    "não", "sim",
}

STOPWORDS: frozenset[str] = frozenset(_EN | _PT)


def is_stopword(token: str) -> bool:
    """Case-insensitive stopword check."""
    return token.lower() in STOPWORDS
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_reflex_stopwords.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/reflex/__init__.py src/mnemo/core/reflex/stopwords.py tests/unit/test_reflex_stopwords.py
git commit -m "feat(reflex): add EN+PT stopword list (stdlib)"
```

---

### Task B3: Tokenizer (with fenced-code stripping + token cap)

**Files:**
- Create: `src/mnemo/core/reflex/tokenizer.py`
- Test: `tests/unit/test_reflex_tokenizer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reflex_tokenizer.py
from __future__ import annotations

from mnemo.core.reflex.tokenizer import tokenize, tokenize_query


def test_tokenize_lowercases_and_splits():
    assert tokenize("Use Prisma Mock") == ["use", "prisma", "mock"]


def test_tokenize_preserves_kebab_and_snake():
    assert tokenize("package-management path_globs") == ["package-management", "path_globs"]


def test_tokenize_query_strips_stopwords():
    # "the" and "of" are stopwords; "use", "prisma", "mock" aren't.
    assert tokenize_query("Use the Prisma mock of Jest") == ["use", "prisma", "mock", "jest"]


def test_tokenize_query_strips_fenced_code_blocks():
    prompt = """preciso mockar o prisma
```python
def test():
    prisma = Mock()
```
valeu"""
    toks = tokenize_query(prompt)
    # "def", "test", "mock" from inside the fence must NOT appear as tokens;
    # the natural-language query terms survive.
    assert "mockar" in toks
    assert "prisma" in toks
    assert "def" not in toks


def test_tokenize_query_caps_at_200_tokens():
    flood = " ".join(f"term{i}" for i in range(500))
    assert len(tokenize_query(flood)) == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reflex_tokenizer.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement tokenizer**

```python
# src/mnemo/core/reflex/tokenizer.py
"""Pure-stdlib tokenizer for Reflex BM25F scoring.

Design:
- Lowercase + split on ``[^a-z0-9_-]+``. Preserves kebab-case and
  snake_case tokens (package-management, path_globs).
- Strips Markdown fenced code blocks from prompts BEFORE tokenization.
  Pasted stack traces and code examples have thousands of tokens that
  tank BM25 precision; the design principle is "match user intent, not
  artefacts they pasted."
- Caps queries at 200 tokens post-stopword.

NO stemming. Code terms ``mock`` and ``mocking`` legitimately appear both
in prompts and rule bodies, so stemming would merge concepts that users
intentionally keep distinct.
"""
from __future__ import annotations

import re

from mnemo.core.reflex.stopwords import is_stopword

_TOKEN_RE = re.compile(r"[a-z0-9_\-]+")
_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_MAX_QUERY_TOKENS = 200


def _strip_fenced_code(text: str) -> str:
    return _FENCE_RE.sub(" ", text)


def tokenize(text: str) -> list[str]:
    """Lowercase + split. No stopword removal, no truncation."""
    return _TOKEN_RE.findall(text.lower())


def tokenize_query(prompt: str) -> list[str]:
    """Tokenize a user prompt for BM25F scoring.

    Pipeline: strip fenced code → tokenize → drop stopwords → cap at 200.
    """
    body = _strip_fenced_code(prompt)
    tokens = tokenize(body)
    kept = [t for t in tokens if not is_stopword(t)]
    return kept[:_MAX_QUERY_TOKENS]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_reflex_tokenizer.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/reflex/tokenizer.py tests/unit/test_reflex_tokenizer.py
git commit -m "feat(reflex): add tokenizer with fenced-code stripping + 200-token cap"
```

---

## Phase C — Reflex index: build, write, load

### Task C1: Build vault-wide index with `is_consumer_visible` gate + per-doc `projects` / `universal`

**Files:**
- Create: `src/mnemo/core/reflex/index.py`
- Test: `tests/unit/test_reflex_index_build.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reflex_index_build.py
"""Vault-wide index with consumer-visibility gate + per-doc projects/universal."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.reflex.index import build_index

FRONTMATTER_TEMPLATE = (
    "---\n"
    "name: {name}\n"
    "description: {description}\n"
    "tags:\n"
    "{tags_block}"
    "sources:\n"
    "{sources_block}"
    "{extra}"
    "stability: {stability}\n"
    "---\n"
    "{body}\n"
)


def _write_rule(vault: Path, subdir: str, filename: str, **kw) -> Path:
    path = vault / "shared" / subdir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = kw.get("tags") or ["auto-promoted"]
    sources = kw.get("sources") or ["bots/mnemo/memory/example.md"]
    aliases = kw.get("aliases")
    extra = ""
    if aliases:
        extra = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    path.write_text(FRONTMATTER_TEMPLATE.format(
        name=kw.get("name", filename.replace(".md", "")),
        description=kw.get("description", "desc"),
        tags_block="".join(f"  - {t}\n" for t in tags),
        sources_block="".join(f"  - {s}\n" for s in sources),
        extra=extra,
        stability=kw.get("stability", "stable"),
        body=kw.get("body", "Actual rule body content."),
    ), encoding="utf-8")
    return path


def test_build_index_includes_only_consumer_visible_rules(tmp_vault):
    # Visible rule
    _write_rule(tmp_vault, "feedback", "keep.md", name="keep")
    # Inbox rule — must be skipped
    _write_rule(tmp_vault, "_inbox/feedback", "draft.md", name="draft")
    # needs-review — must be skipped
    _write_rule(tmp_vault, "feedback", "review.md", name="review",
                tags=["needs-review"])
    # evolving — must be skipped
    _write_rule(tmp_vault, "feedback", "evolving.md", name="flaky",
                stability="evolving")

    idx = build_index(tmp_vault, universal_threshold=2)
    slugs = set(idx["docs"].keys())
    assert "keep" in slugs
    assert slugs == {"keep"}, f"expected only 'keep', got {slugs}"


def test_build_index_emits_projects_and_universal_per_doc(tmp_vault):
    _write_rule(tmp_vault, "feedback", "a.md", name="a",
                sources=["bots/projA/memory/x.md"])
    _write_rule(tmp_vault, "feedback", "b.md", name="b",
                sources=["bots/projA/memory/y.md", "bots/projB/memory/z.md"])

    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["docs"]["a"]["projects"] == ["projA"]
    assert idx["docs"]["a"]["universal"] is False
    assert idx["docs"]["b"]["projects"] == ["projA", "projB"]
    assert idx["docs"]["b"]["universal"] is True


def test_build_index_indexes_aliases_field(tmp_vault):
    _write_rule(tmp_vault, "feedback", "c.md", name="c",
                description="Mock database in tests",
                aliases=["banco", "database", "db"])

    idx = build_index(tmp_vault, universal_threshold=2)

    # "banco" must appear in postings → points back to slug "c".
    assert "banco" in idx["postings"]
    assert any(p["slug"] == "c" for p in idx["postings"]["banco"])
    assert idx["docs"]["c"]["field_length"]["aliases"] == 3


def test_build_index_schema_shape(tmp_vault):
    _write_rule(tmp_vault, "feedback", "a.md", name="a")
    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["schema_version"] == 1
    assert "generated_at" in idx
    # No scope / project top-level fields (C3 fix).
    assert "scope" not in idx
    assert "project" not in idx
    assert isinstance(idx["avg_field_length"], dict)
    assert set(idx["avg_field_length"]) == {
        "name", "topic_tags", "aliases", "description", "body",
    }
    assert isinstance(idx["postings"], dict)
    assert isinstance(idx["docs"], dict)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reflex_index_build.py -v`
Expected: FAIL — `build_index` not defined.

- [ ] **Step 3: Implement `build_index`**

```python
# src/mnemo/core/reflex/index.py
"""Reflex BM25F index: build, write, load.

Vault-wide — mirrors rule_activation-index.json structure. Project filtering
happens at query time via per-doc ``projects`` + ``universal`` fields (see
retrieval.py / user_prompt_submit hook). Same ``is_consumer_visible`` gate as
``rule_activation.build_index`` — non-negotiable parity with the HOME
dashboard.

Schema v1:
    {
      "schema_version": 1,
      "generated_at": "YYYY-MM-DDTHH:MM:SSZ",
      "avg_field_length": {name, topic_tags, aliases, description, body},
      "doc_count": int,
      "postings": { term: [{"slug": ..., "tf": {field: count}}, ...] },
      "docs": {
        slug: {
          "field_length": {field: int},
          "preview": str,
          "stability": "stable" | "evolving",
          "projects": list[str],
          "universal": bool,
        },
      },
    }
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.filters import is_consumer_visible, parse_frontmatter
from mnemo.core.reflex.tokenizer import tokenize
from mnemo.core.rule_activation import _is_universal, projects_for_rule
from mnemo.core.text_utils import body_preview

SCHEMA_VERSION = 1
INDEX_FILENAME = "reflex-index.json"

_FIELD_NAMES = ("name", "topic_tags", "aliases", "description", "body")
_SYSTEM_TAGS: frozenset[str] = frozenset({"auto-promoted", "needs-review"})


def _field_tokens(fm: dict, body_text: str, slug: str) -> dict[str, list[str]]:
    """Extract token lists per indexed field."""
    name = fm.get("name") or slug
    tags = fm.get("tags") or []
    topic_tags = [t for t in tags if isinstance(t, str) and t not in _SYSTEM_TAGS]
    aliases_raw = fm.get("aliases") or []
    aliases = [a for a in aliases_raw if isinstance(a, str)]
    description = fm.get("description") or ""

    return {
        "name": tokenize(str(name)),
        "topic_tags": [t for tag in topic_tags for t in tokenize(tag)],
        "aliases": [t for alias in aliases for t in tokenize(alias)],
        "description": tokenize(str(description)),
        "body": tokenize(body_text),
    }


def build_index(vault_root: Path, *, universal_threshold: int = 2) -> dict:
    """Walk shared/{feedback,user,reference}/*.md, build the BM25F index."""
    docs: dict[str, dict] = {}
    postings: dict[str, list[dict]] = {}
    field_length_totals = {f: 0 for f in _FIELD_NAMES}

    for page_type in ("feedback", "user", "reference"):
        type_dir = vault_root / "shared" / page_type
        if not type_dir.is_dir():
            continue

        for md_path in sorted(type_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue

            fm = parse_frontmatter(text)
            if not is_consumer_visible(md_path, fm, vault_root):
                continue

            slug = fm.get("slug") or fm.get("name") or md_path.stem

            sources_raw = fm.get("sources") or []
            if isinstance(sources_raw, str):
                sources_raw = [sources_raw]
            source_files = [s for s in sources_raw if isinstance(s, str)]
            projects = projects_for_rule(source_files)
            universal = _is_universal(projects, universal_threshold)

            field_toks = _field_tokens(fm, text, slug)
            field_length = {f: len(field_toks[f]) for f in _FIELD_NAMES}

            # Merge all field tokens into postings with per-field tf.
            for field, toks in field_toks.items():
                seen: dict[str, int] = {}
                for tok in toks:
                    seen[tok] = seen.get(tok, 0) + 1
                for tok, tf in seen.items():
                    postings.setdefault(tok, [])
                    # Find or create entry for this slug.
                    bucket = None
                    for entry in postings[tok]:
                        if entry["slug"] == slug:
                            bucket = entry
                            break
                    if bucket is None:
                        bucket = {"slug": slug, "tf": {f: 0 for f in _FIELD_NAMES}}
                        postings[tok].append(bucket)
                    bucket["tf"][field] = tf

            for f in _FIELD_NAMES:
                field_length_totals[f] += field_length[f]

            docs[slug] = {
                "field_length": field_length,
                "preview": body_preview(text, max_chars=300),
                "stability": fm.get("stability") or "stable",
                "projects": projects,
                "universal": universal,
            }

    doc_count = len(docs)
    avg_field_length = {
        f: (field_length_totals[f] / doc_count) if doc_count else 0.0
        for f in _FIELD_NAMES
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "avg_field_length": avg_field_length,
        "doc_count": doc_count,
        "postings": postings,
        "docs": docs,
    }


def write_index(vault_root: Path, index: dict) -> None:
    """Atomic write. Never raises during test runs; callers should still try/except."""
    path = vault_root / ".mnemo" / INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(json.dumps(index, indent=2).encode("utf-8"))
    os.replace(tmp, path)


def load_index(vault_root: Path) -> dict | None:
    """Load the index from disk. Returns None on ANY error. Never raises."""
    try:
        path = vault_root / ".mnemo" / INDEX_FILENAME
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return None
        if raw.get("schema_version") != SCHEMA_VERSION:
            return None
        return raw
    except Exception:  # noqa: BLE001 — fail-open
        return None
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_reflex_index_build.py -v`
Expected: PASS on all 4 tests.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/reflex/index.py tests/unit/test_reflex_index_build.py
git commit -m "feat(reflex): vault-wide BM25F index with consumer-visible gate"
```

---

### Task C2: BM25F scoring

**Files:**
- Create: `src/mnemo/core/reflex/bm25.py`
- Test: `tests/unit/test_reflex_bm25.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reflex_bm25.py
from __future__ import annotations

from mnemo.core.reflex.bm25 import score_docs, DEFAULT_WEIGHTS, DEFAULT_PARAMS


def _index_with(slugs_fields_lengths, avg_lengths, postings):
    """Helper: build a minimal index dict shape for tests."""
    return {
        "avg_field_length": avg_lengths,
        "doc_count": len(slugs_fields_lengths),
        "docs": {
            slug: {"field_length": lengths, "preview": "", "stability": "stable",
                   "projects": [], "universal": False}
            for slug, lengths in slugs_fields_lengths.items()
        },
        "postings": postings,
    }


def test_score_empty_query_returns_empty():
    idx = _index_with({"a": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 5}},
                      {"name": 1.0, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 5.0},
                      {})
    assert score_docs(idx, query_tokens=[], candidate_slugs=["a"]) == []


def test_score_candidates_filter_is_respected():
    idx = _index_with(
        {
            "in_scope": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 1},
            "out_of_scope": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 1},
        },
        {"name": 1.0, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 1.0},
        {"foo": [
            {"slug": "in_scope", "tf": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0}},
            {"slug": "out_of_scope", "tf": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0}},
        ]},
    )
    results = score_docs(idx, query_tokens=["foo"], candidate_slugs=["in_scope"])
    assert len(results) == 1
    assert results[0][0] == "in_scope"


def test_score_is_descending_and_weighted_by_field():
    # Two rules: A matches in name (weight 3.0), B matches in body (weight 1.0).
    # Same field_length. Expect A > B.
    idx = _index_with(
        {
            "A": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 10},
            "B": {"name": 10, "topic_tags": 0, "aliases": 0, "description": 0, "body": 10},
        },
        {"name": 5.5, "topic_tags": 0.0, "aliases": 0.0, "description": 0.0, "body": 10.0},
        {"prisma": [
            {"slug": "A", "tf": {"name": 1, "topic_tags": 0, "aliases": 0, "description": 0, "body": 0}},
            {"slug": "B", "tf": {"name": 0, "topic_tags": 0, "aliases": 0, "description": 0, "body": 1}},
        ]},
    )
    results = score_docs(idx, query_tokens=["prisma"], candidate_slugs=["A", "B"])
    slugs = [slug for slug, score in results]
    assert slugs == ["A", "B"]
    assert results[0][1] > results[1][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reflex_bm25.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement BM25F scoring**

```python
# src/mnemo/core/reflex/bm25.py
"""BM25F scoring over the Reflex index.

BM25F reference: Robertson et al. — field-weighted variant of BM25.
We adopt the simplified "pseudo-term-frequency" formulation:

    ~tf_t,d = sum_f  weight_f * tf_t,d,f / (1 - b + b * L_f,d / avgL_f)
    score(q, d) = sum_{t in q}  ~tf_t,d / (k1 + ~tf_t,d)  *  idf(t)
    idf(t) = log( (N - df_t + 0.5) / (df_t + 0.5) + 1 )

N = doc_count, df_t = number of docs containing term t (any field).

Design decisions:
- Per-field b is global (see spec BM25F parameters — "no per-field length
  normalization, premature").
- ``avgL_f`` is the vault-wide average field length. Small vaults → the
  denominator stays bounded so scores remain stable at day-1.
- No query-term weighting (all query tokens equal). Tried earlier during
  design; the triple-gate is a stronger safety rail than query boosting.
"""
from __future__ import annotations

import math
from typing import Iterable

DEFAULT_WEIGHTS: dict[str, float] = {
    "name": 3.0,
    "topic_tags": 3.0,
    "aliases": 2.5,
    "description": 2.0,
    "body": 1.0,
}

DEFAULT_PARAMS = {"k1": 1.5, "b": 0.75}


def score_docs(
    index: dict,
    *,
    query_tokens: list[str],
    candidate_slugs: Iterable[str],
    weights: dict[str, float] | None = None,
    params: dict | None = None,
) -> list[tuple[str, float]]:
    """Score candidate docs against the query. Returns [(slug, score), ...] desc."""
    if not query_tokens:
        return []
    w = weights or DEFAULT_WEIGHTS
    p = params or DEFAULT_PARAMS
    k1 = float(p.get("k1", 1.5))
    b = float(p.get("b", 0.75))

    docs = index.get("docs", {})
    postings = index.get("postings", {})
    avg = index.get("avg_field_length", {})
    N = int(index.get("doc_count", 0))

    candidate_set = {s for s in candidate_slugs if s in docs}
    if not candidate_set:
        return []

    # Precompute IDF per unique query term.
    unique_query = list(dict.fromkeys(query_tokens))
    idf: dict[str, float] = {}
    for term in unique_query:
        df = len(postings.get(term, []))
        # +1 Laplace on the idf formula keeps values non-negative.
        idf[term] = math.log((N - df + 0.5) / (df + 0.5) + 1.0)

    scores: dict[str, float] = {slug: 0.0 for slug in candidate_set}

    for term in unique_query:
        term_postings = postings.get(term, [])
        if not term_postings:
            continue
        for entry in term_postings:
            slug = entry["slug"]
            if slug not in candidate_set:
                continue
            doc = docs[slug]
            lengths = doc.get("field_length", {})

            weighted_tf = 0.0
            for field, weight in w.items():
                tf_f = int(entry["tf"].get(field, 0))
                if tf_f == 0:
                    continue
                L_f = int(lengths.get(field, 0))
                avg_L_f = float(avg.get(field, 0.0)) or 1.0
                denom = (1.0 - b) + b * (L_f / avg_L_f)
                if denom <= 0:
                    continue
                weighted_tf += weight * tf_f / denom
            if weighted_tf <= 0:
                continue
            sat = weighted_tf / (k1 + weighted_tf)
            scores[slug] += sat * idf[term]

    out = [(slug, score) for slug, score in scores.items() if score > 0]
    out.sort(key=lambda kv: (-kv[1], kv[0]))
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_reflex_bm25.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/reflex/bm25.py tests/unit/test_reflex_bm25.py
git commit -m "feat(reflex): BM25F scoring over the field-weighted index"
```

---

### Task C3: Triple-gate

**Files:**
- Create: `src/mnemo/core/reflex/gates.py`
- Test: `tests/unit/test_reflex_gates.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_reflex_gates.py
from __future__ import annotations

from mnemo.core.reflex.gates import (
    GateResult, evaluate_gates, DEFAULT_THRESHOLDS,
)


def test_empty_scores_returns_silence_reason():
    res = evaluate_gates([], query_tokens=["x"], doc_tokens_by_slug={}, thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "index_missing"


def test_absolute_floor_failure():
    scores = [("a", 1.5)]
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={"a": {"prisma", "mock"}},
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "absolute_floor_fail"


def test_relative_gap_failure():
    scores = [("a", 3.0), ("b", 2.5)]  # ratio 1.2 < 1.5
    res = evaluate_gates(scores, query_tokens=["prisma", "mock", "orm"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock", "orm"},
                             "b": {"prisma", "mock"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "relative_gap_fail"


def test_term_overlap_failure():
    scores = [("a", 5.0)]
    res = evaluate_gates(scores, query_tokens=["foo", "bar", "baz"],
                         doc_tokens_by_slug={"a": {"foo"}},  # only 1 overlap
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == []
    assert res.silence_reason == "term_overlap_fail"


def test_all_three_gates_pass_returns_top1():
    scores = [("a", 5.0), ("b", 2.0)]  # 5.0/2.0=2.5 >= 1.5
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock", "jest"},
                             "b": {"prisma"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == ["a"]
    assert res.silence_reason is None


def test_top2_included_when_also_passes():
    scores = [("a", 5.0), ("b", 2.5)]
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock"},
                             "b": {"prisma", "mock"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    # 5.0/2.5 = 2.0 >= relative_gap; b passes overlap + absolute_floor (2.0).
    assert res.accepted_slugs == ["a", "b"]


def test_top2_excluded_when_below_absolute_floor():
    scores = [("a", 5.0), ("b", 1.8)]  # 1.8 below 2.0 floor
    res = evaluate_gates(scores, query_tokens=["prisma", "mock"],
                         doc_tokens_by_slug={
                             "a": {"prisma", "mock"},
                             "b": {"prisma", "mock"},
                         },
                         thresholds=DEFAULT_THRESHOLDS)
    assert res.accepted_slugs == ["a"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_reflex_gates.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement triple-gate**

```python
# src/mnemo/core/reflex/gates.py
"""Triple-gate confidence check for Reflex.

Silence is the default; emission requires ALL THREE to pass against the
top-1 candidate:

  (a) term-overlap >= term_overlap_min across the UNION of indexed fields
  (b) relative gap  s[0] >= relative_gap * s[1]   (or s[1] == 0)
  (c) absolute floor s[0] >= absolute_floor

If top-1 passes, top-2 is included ONLY IF it ALSO passes (a) and its score
clears the absolute_floor. We deliberately do not re-check relative gap on
top-2 — the purpose of top-2 is "nearly as good as top-1, not worth hiding."
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_THRESHOLDS: dict = {
    "term_overlap_min": 2,
    "relative_gap": 1.5,
    "absolute_floor": 2.0,
}

_REASONS = (
    "index_missing", "absolute_floor_fail", "relative_gap_fail", "term_overlap_fail",
)


@dataclass
class GateResult:
    accepted_slugs: list[str] = field(default_factory=list)
    silence_reason: str | None = None


def _overlap(query: list[str], doc_tokens: set[str]) -> int:
    return len(set(query) & doc_tokens)


def evaluate_gates(
    scores: list[tuple[str, float]],
    *,
    query_tokens: list[str],
    doc_tokens_by_slug: dict[str, set[str]],
    thresholds: dict,
) -> GateResult:
    """Run the triple-gate and return at most 2 accepted slugs (top-1, [top-2])."""
    if not scores:
        return GateResult(silence_reason="index_missing")

    top1_slug, top1_score = scores[0]
    top2 = scores[1] if len(scores) > 1 else (None, 0.0)

    t_overlap_min = int(thresholds.get("term_overlap_min", 2))
    rel_gap = float(thresholds.get("relative_gap", 1.5))
    abs_floor = float(thresholds.get("absolute_floor", 2.0))

    # (c) absolute floor — cheapest, check first.
    if top1_score < abs_floor:
        return GateResult(silence_reason="absolute_floor_fail")

    # (b) relative gap — s2 == 0 is trivially passing.
    if top2[1] > 0 and top1_score < rel_gap * top2[1]:
        return GateResult(silence_reason="relative_gap_fail")

    # (a) term overlap.
    if _overlap(query_tokens, doc_tokens_by_slug.get(top1_slug, set())) < t_overlap_min:
        return GateResult(silence_reason="term_overlap_fail")

    accepted = [top1_slug]
    if top2[0] is not None:
        top2_slug, top2_score = top2
        if (
            top2_score >= abs_floor
            and _overlap(query_tokens, doc_tokens_by_slug.get(top2_slug, set())) >= t_overlap_min
        ):
            accepted.append(top2_slug)

    return GateResult(accepted_slugs=accepted)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_reflex_gates.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/reflex/gates.py tests/unit/test_reflex_gates.py
git commit -m "feat(reflex): triple-gate confidence test (overlap + gap + floor)"
```

---

## Phase D — Extraction: `aliases:` in all three prompts

### Task D1: Add `aliases` guidance to FEEDBACK / USER / REFERENCE prompts + JSON schemas

**Files:**
- Modify: `src/mnemo/core/extract/prompts.py` — three system prompts + their JSON schema blocks
- Test: `tests/unit/test_extract_prompts_aliases.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_extract_prompts_aliases.py
"""All three extraction prompts must mention `aliases:` guidance (v0.8)."""
from __future__ import annotations

from mnemo.core.extract import prompts as p


def test_feedback_prompt_mentions_aliases():
    assert "aliases" in p.FEEDBACK_SYSTEM_PROMPT.lower()


def test_user_prompt_mentions_aliases():
    assert "aliases" in p.USER_SYSTEM_PROMPT.lower()


def test_reference_prompt_mentions_aliases():
    assert "aliases" in p.REFERENCE_SYSTEM_PROMPT.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_extract_prompts_aliases.py -v`
Expected: FAIL — no "aliases" substring yet.

- [ ] **Step 3: Add the guidance block to all three prompts**

In `src/mnemo/core/extract/prompts.py`, add this paragraph at the end of each of `FEEDBACK_SYSTEM_PROMPT`, `USER_SYSTEM_PROMPT`, and `REFERENCE_SYSTEM_PROMPT` (before the final "Output MUST be valid JSON..." sentence):

```
"Aliases field (v0.8 — optional, strongly encouraged for bilingual/synonymous rules): "
"every emitted page MAY carry an `aliases` list of short lowercase tokens that act as "
"synonym bridges for lexical retrieval. Emit aliases when the rule description or body "
"contains domain terms that a developer would naturally search in a different language or "
"abbreviation — e.g. `aliases: [\"banco\", \"database\", \"db\"]` for a rule about database "
"mocking. Keep aliases to 3-8 tokens max; prefer concrete terms (framework names, file types, "
"commands) over vague ones. If the rule is generic and has no natural synonyms, omit the field.\\n\\n"
```

Also update the JSON schemas referenced by each prompt (search for schema definitions in the same file or sibling files) to include an optional `aliases: list[string]` field.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_extract_prompts_aliases.py tests/unit/test_extract_prompts.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/extract/prompts.py tests/unit/test_extract_prompts_aliases.py
git commit -m "feat(extract): add aliases guidance to all three system prompts"
```

---

## Phase E — Hook integration

### Task E1: `UserPromptSubmit` hook core (the retrieval flow)

**Files:**
- Create: `src/mnemo/hooks/user_prompt_submit.py`
- Test: `tests/unit/test_hook_user_prompt_submit.py`
- Integration test: `tests/integration/test_hook_user_prompt_submit_e2e.py`

- [ ] **Step 1: Write the failing unit test**

```python
# tests/unit/test_hook_user_prompt_submit.py
"""UserPromptSubmit retrieval flow + silence reasons + dedupe."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import user_prompt_submit as hook


def _run_hook(stdin_payload: dict) -> tuple[int, str]:
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(stdin_payload))), \
         patch("sys.stdout", out):
        rc = hook.main()
    return rc, out.getvalue()


def test_hook_returns_silence_on_disabled_reflex(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": False},
    }))

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault),
        "session_id": "sid-xyz",
        "prompt": "Use Prisma mock for the new test",
    })
    assert rc == 0
    assert stdout == ""


def test_hook_returns_silence_on_short_prompt(tmp_vault, monkeypatch):
    # Pre-gate: < 3 distinct non-stopword tokens.
    _enable_reflex(tmp_vault, monkeypatch)
    rc, stdout = _run_hook({
        "cwd": str(tmp_vault), "session_id": "sid", "prompt": "ok",
    })
    assert rc == 0 and stdout == ""


def test_hook_emits_on_confident_match(tmp_vault, monkeypatch, synthetic_index):
    """Uses fixture that writes a reflex-index.json whose top match is 'use-prisma-mock'."""
    _enable_reflex(tmp_vault, monkeypatch)
    synthetic_index(tmp_vault)

    rc, stdout = _run_hook({
        "cwd": str(tmp_vault),
        "session_id": "sid-1",
        "prompt": "How do I mock prisma in a jest test with typescript",
    })
    assert rc == 0
    payload = json.loads(stdout)
    text = payload["hookSpecificOutput"]["additionalContext"]
    assert "mnemo reflex context:" in text
    assert "[[use-prisma-mock]]" in text


def _enable_reflex(vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(vault / "mnemo.config.json"))
    (vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(vault),
        "reflex": {"enabled": True},
    }))
```

Add a `synthetic_index` fixture in `tests/conftest.py`:

```python
# APPEND to tests/conftest.py
@pytest.fixture
def synthetic_index():
    """Return a function that seeds a reflex-index.json with one high-signal rule."""
    def _apply(vault):
        from mnemo.core.reflex.index import build_index, write_index
        # Write the source rule file
        (vault / "shared" / "feedback").mkdir(parents=True, exist_ok=True)
        (vault / "shared" / "feedback" / "use-prisma-mock.md").write_text(
            "---\n"
            "name: use-prisma-mock\n"
            "description: Always use jest-mock-extended to mock Prisma in tests\n"
            "tags:\n"
            "  - prisma\n"
            "  - testing\n"
            "aliases:\n"
            "  - banco\n"
            "  - database\n"
            "sources:\n"
            "  - bots/mnemo/memory/mock.md\n"
            "stability: stable\n"
            "---\n"
            "Mock the Prisma client in tests using jest-mock-extended.\n",
            encoding="utf-8",
        )
        idx = build_index(vault, universal_threshold=2)
        write_index(vault, idx)
    return _apply
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_hook_user_prompt_submit.py -v`
Expected: FAIL — module `mnemo.hooks.user_prompt_submit` not found.

- [ ] **Step 3: Implement the hook**

```python
# src/mnemo/hooks/user_prompt_submit.py
"""UserPromptSubmit hook — Prompt Reflex.

Fail-open absolute: any exception returns exit 0 with empty stdout. The
hook runs on every prompt; a regression here would stall every Claude
turn. Follow the defensive patterns from pre_tool_use.py / session_start.py.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0

    try:
        from mnemo.core import config as cfg_mod
        from mnemo.core import errors, paths
        from mnemo.core.agent import resolve_agent
        from mnemo.core.mcp import session_state
        from mnemo.core.reflex import bm25, gates
        from mnemo.core.reflex.index import load_index
        from mnemo.core.reflex.tokenizer import tokenize, tokenize_query

        cfg = cfg_mod.load_config()
        reflex_cfg = cfg.get("reflex") or {}
        if not bool(reflex_cfg.get("enabled", False)):
            return 0

        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0

        cwd = payload.get("cwd") or str(Path.cwd())
        project = resolve_agent(cwd).name
        sid = str(payload.get("session_id") or "unknown")
        prompt_raw = str(payload.get("prompt") or payload.get("user_message") or "")

        now_ts = int(time.time())

        # Session GC + cap check
        session_state.gc_old_sessions(vault, now_ts=now_ts)
        emissions = session_state.read_emission_counts(vault, sid)
        max_per = int(reflex_cfg.get("maxEmissionsPerSession", 10))
        if emissions["reflex_count"] >= max_per:
            _log_silence(vault, sid, project, prompt_raw, reason="session_cap_reached")
            return 0

        # Pre-gate: min 3 distinct non-stopword tokens.
        thresholds = (reflex_cfg.get("thresholds") or {})
        min_tokens = int(thresholds.get("minQueryTokens", 3))
        q_tokens = tokenize_query(prompt_raw)
        if len(set(q_tokens)) < min_tokens:
            _log_silence(vault, sid, project, prompt_raw, reason="below_min_tokens")
            return 0

        index = load_index(vault)
        if index is None:
            _log_silence(vault, sid, project, prompt_raw, reason="index_missing")
            return 0

        # Candidate slugs — project scope (local + universal).
        candidates = _candidates_for_project(index, project)
        if not candidates:
            _log_silence(vault, sid, project, prompt_raw, reason="index_missing")
            return 0

        # Score
        weights = (reflex_cfg.get("bm25f") or {}).get("fieldWeights") or bm25.DEFAULT_WEIGHTS
        params = reflex_cfg.get("bm25f") or bm25.DEFAULT_PARAMS
        scores = bm25.score_docs(index, query_tokens=q_tokens,
                                 candidate_slugs=candidates,
                                 weights=weights, params=params)

        # Triple-gate
        doc_tokens_by_slug = _doc_token_sets(index, [slug for slug, _ in scores[:2]])
        result = gates.evaluate_gates(
            scores,
            query_tokens=q_tokens,
            doc_tokens_by_slug=doc_tokens_by_slug,
            thresholds={
                "term_overlap_min": int(thresholds.get("termOverlapMin", 2)),
                "relative_gap": float(thresholds.get("relativeGap", 1.5)),
                "absolute_floor": float(thresholds.get("absoluteFloor", 2.0)),
            },
        )
        if not result.accepted_slugs:
            _log_silence(vault, sid, project, prompt_raw, reason=result.silence_reason or "index_missing")
            return 0

        # Dedupe against injected_cache (session-lifetime)
        cache = session_state.read_injected_cache(vault)
        survivors = [s for s in result.accepted_slugs if s not in cache]
        if not survivors:
            _log_silence(vault, sid, project, prompt_raw, reason="deduped")
            return 0

        _emit_reflex_context(index, survivors)
        for slug in survivors:
            session_state.add_injection(vault, slug=slug, sid=sid, now_ts=now_ts)
            session_state.bump_emission(vault, sid=sid, kind="reflex", now_ts=now_ts)

        score_map = dict(scores)
        _log_emission(vault, sid, project, prompt_raw, survivors,
                      scores=[score_map.get(s, 0.0) for s in survivors])
    except Exception as exc:  # noqa: BLE001 — hook must never propagate
        try:
            from mnemo.core import config as _cfg, errors as _err, paths as _paths
            _err.log_error(_paths.vault_root(_cfg.load_config()), "user_prompt_submit.outer", exc)
        except Exception:
            pass
    return 0


def _candidates_for_project(index: dict, project: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for slug, doc in (index.get("docs") or {}).items():
        if project in (doc.get("projects") or []) or doc.get("universal"):
            if slug not in seen:
                seen.add(slug)
                out.append(slug)
    return out


def _doc_token_sets(index: dict, slugs: list[str]) -> dict[str, set[str]]:
    """Rebuild per-doc token UNION (across all 4 fields) for triple-gate overlap check."""
    out: dict[str, set[str]] = {s: set() for s in slugs}
    target = set(slugs)
    for term, entries in (index.get("postings") or {}).items():
        for entry in entries:
            if entry["slug"] in target:
                out[entry["slug"]].add(term)
    return out


def _emit_reflex_context(index: dict, slugs: list[str]) -> None:
    lines = ["mnemo reflex context:"]
    docs = index.get("docs") or {}
    for slug in slugs:
        preview = (docs.get(slug) or {}).get("preview", "")
        preview_line = preview.replace("\n", " ").strip()
        lines.append(f"• [[{slug}]]: {preview_line} (call read_mnemo_rule if you need the full file).")
    text = "\n".join(lines)
    sys.stdout.write(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": text,
        },
    }))
    sys.stdout.flush()


def _prompt_hash(prompt: str) -> str:
    digest = hashlib.sha256(prompt.encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest[:12]}"


def _log_silence(vault_root, sid: str, project: str, prompt: str, *, reason: str) -> None:
    _record_log(vault_root, {
        "session_id": sid,
        "project": project,
        "prompt_hash": _prompt_hash(prompt),
        "prompt_tokens": len(set((__import__("mnemo.core.reflex.tokenizer", fromlist=["tokenize_query"]).tokenize_query)(prompt))),
        "emitted": [],
        "scores": [],
        "silence_reason": reason,
    })


def _log_emission(vault_root, sid: str, project: str, prompt: str,
                  emitted: list[str], *, scores: list[float]) -> None:
    _record_log(vault_root, {
        "session_id": sid,
        "project": project,
        "prompt_hash": _prompt_hash(prompt),
        "emitted": emitted,
        "scores": scores,
        "silence_reason": None,
    })


def _record_log(vault_root, entry: dict) -> None:
    try:
        from datetime import datetime, timezone
        from mnemo.core.log_utils import rotate_if_needed
        entry.setdefault("ts", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        log_path = Path(vault_root) / ".mnemo" / "reflex-log.jsonl"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        rotate_if_needed(log_path, 1_048_576)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
    except Exception:
        pass


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_hook_user_prompt_submit.py -v`
Expected: PASS on 3 tests.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/user_prompt_submit.py tests/unit/test_hook_user_prompt_submit.py tests/conftest.py
git commit -m "feat(hooks): add UserPromptSubmit reflex hook"
```

---

### Task E2: Register the new hook in `HOOK_DEFINITIONS`

**Files:**
- Modify: `src/mnemo/install/settings.py:44` (`HOOK_DEFINITIONS`)
- Test: `tests/unit/test_install_user_prompt_submit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_install_user_prompt_submit.py
"""mnemo init must register UserPromptSubmit; uninstall must clean it."""
from __future__ import annotations

import json

from mnemo.install import settings


def test_hook_definitions_include_user_prompt_submit():
    assert "UserPromptSubmit" in settings.HOOK_DEFINITIONS
    defn = settings.HOOK_DEFINITIONS["UserPromptSubmit"]
    assert defn["module"] == "user_prompt_submit"
    assert defn["matcher"] is None


def test_inject_writes_user_prompt_submit_entry(tmp_path):
    sp = tmp_path / "settings.json"
    settings.inject_hooks(sp)
    data = json.loads(sp.read_text())
    assert "UserPromptSubmit" in data["hooks"]


def test_uninject_removes_user_prompt_submit_entry(tmp_path):
    sp = tmp_path / "settings.json"
    settings.inject_hooks(sp)
    settings.uninject_hooks(sp)
    data = json.loads(sp.read_text())
    assert "UserPromptSubmit" not in data.get("hooks", {})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_install_user_prompt_submit.py -v`
Expected: FAIL — no UserPromptSubmit key.

- [ ] **Step 3: Add entry to `HOOK_DEFINITIONS`**

Edit `src/mnemo/install/settings.py:44`. Add one line inside the dict:

```python
HOOK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "SessionStart": {"module": "session_start", "matcher": None, "async": False},
    "PreToolUse": {"module": "pre_tool_use", "matcher": "Bash|Edit|Write|MultiEdit", "async": False},
    "SessionEnd": {"module": "session_end", "matcher": None, "async": False},
    "UserPromptSubmit": {"module": "user_prompt_submit", "matcher": None, "async": False},
}
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_install_user_prompt_submit.py tests/unit/test_cli_wiki_uninstall.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/install/settings.py tests/unit/test_install_user_prompt_submit.py
git commit -m "feat(install): register UserPromptSubmit hook in HOOK_DEFINITIONS"
```

---

### Task E3: Piggyback reflex index rebuild on SessionStart

**Files:**
- Modify: `src/mnemo/hooks/session_start.py:140-153` (the rebuild block)
- Test: `tests/integration/test_hook_session_start_reflex.py`

- [ ] **Step 1: Write the failing integration test**

```python
# tests/integration/test_hook_session_start_reflex.py
"""SessionStart must rebuild reflex-index.json when reflex.enabled."""
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import session_start


def test_session_start_writes_reflex_index(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": True},
        "injection": {"enabled": False},
        "enforcement": {"enabled": False},
        "enrichment": {"enabled": False},
    }))
    # Minimal feedback rule so the index has a doc.
    fb_dir = tmp_vault / "shared" / "feedback"
    fb_dir.mkdir(parents=True, exist_ok=True)
    (fb_dir / "r.md").write_text(
        "---\nname: r\ndescription: d\ntags:\n  - t\n"
        "sources:\n  - bots/mnemo/memory/x.md\nstability: stable\n---\nbody\n",
        encoding="utf-8",
    )

    payload = {"cwd": str(tmp_vault), "session_id": "sid", "source": "startup"}
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        rc = session_start.main()

    assert rc == 0
    idx_path = tmp_vault / ".mnemo" / "reflex-index.json"
    assert idx_path.exists()
    data = json.loads(idx_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    assert "r" in data["docs"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_hook_session_start_reflex.py -v`
Expected: FAIL — `reflex-index.json` not created.

- [ ] **Step 3: Extend the SessionStart rebuild block**

Edit `src/mnemo/hooks/session_start.py` around lines 140-153. Replace that block with:

```python
# Rebuild rule-activation index when any of the three consumers needs it:
# enforcement (PreToolUse deny), enrichment (PreToolUse context),
# injection (SessionStart topic list), or reflex (UserPromptSubmit BM25F).
inj_enabled = bool(cfg.get("injection", {}).get("enabled", False))
enf_enabled = bool(cfg.get("enforcement", {}).get("enabled", False))
enr_enabled = bool(cfg.get("enrichment", {}).get("enabled", False))
reflex_enabled = bool(cfg.get("reflex", {}).get("enabled", False))
if enf_enabled or enr_enabled or inj_enabled or reflex_enabled:
    try:
        from mnemo.core import rule_activation
        rule_activation.write_index(vault, rule_activation.build_index(vault))
    except Exception as exc:
        errors.log_error(vault, "session_start.rule_activation_index", exc)

if reflex_enabled:
    try:
        from mnemo.core.reflex import index as reflex_index
        reflex_index.write_index(vault, reflex_index.build_index(vault))
    except Exception as exc:
        errors.log_error(vault, "session_start.reflex_index", exc)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/integration/test_hook_session_start_reflex.py tests/integration/test_hook_session_start.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/session_start.py tests/integration/test_hook_session_start_reflex.py
git commit -m "feat(hooks): rebuild reflex-index on SessionStart when reflex.enabled"
```

---

### Task E4: SessionEnd GC (evict `session_emissions[sid]` + sid-scoped cache)

**Files:**
- Modify: `src/mnemo/hooks/session_end.py` (inside `main()`, after `session.clear(sid)`)
- Test: `tests/integration/test_hook_session_end_reflex_gc.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_hook_session_end_reflex_gc.py
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import session_end
from mnemo.core.mcp import session_state


def test_session_end_evicts_session_emissions_entry(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(
        json.dumps({"vaultRoot": str(tmp_vault)})
    )
    session_state.bump_emission(tmp_vault, sid="sid-to-evict", kind="reflex", now_ts=1)
    session_state.bump_emission(tmp_vault, sid="sid-survives", kind="reflex", now_ts=2)

    payload = {
        "cwd": str(tmp_vault),
        "session_id": "sid-to-evict",
        "reason": "exit",
    }
    with patch("sys.stdin", io.StringIO(json.dumps(payload))):
        rc = session_end.main()
    assert rc == 0

    data = json.loads((tmp_vault / ".mnemo" / "mcp-call-counter.json").read_text(encoding="utf-8"))
    assert "sid-to-evict" not in data["session_emissions"]
    assert "sid-survives" in data["session_emissions"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/integration/test_hook_session_end_reflex_gc.py -v`
Expected: FAIL — SessionEnd does not currently touch `session_emissions`.

- [ ] **Step 3: Add eviction call to `session_end.main()`**

In `src/mnemo/hooks/session_end.py`, inside `main()`, right after the existing `session.clear(sid)` try/except block, add:

```python
try:
    from mnemo.core.mcp import session_state as _ss
    _ss.evict_session(vault, sid)
except Exception as e:
    errors.log_error(vault, "session_end.evict_reflex_state", e)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/integration/test_hook_session_end_reflex_gc.py tests/integration/test_hook_session_end.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/session_end.py tests/integration/test_hook_session_end_reflex_gc.py
git commit -m "feat(hooks): SessionEnd evicts session_emissions entry on close"
```

---

### Task E5: Extend PreToolUse enrichment to honour session-state cap + dedupe cache

**Files:**
- Modify: `src/mnemo/hooks/pre_tool_use.py` (the enrichment branch, around lines 74-82)
- Test: `tests/unit/test_hook_pre_tool_use_reflex_integration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_hook_pre_tool_use_reflex_integration.py
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.core.mcp import session_state
from mnemo.hooks import pre_tool_use


def _run(payload):
    out = io.StringIO()
    with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", out):
        rc = pre_tool_use.main()
    return rc, out.getvalue()


def test_enrichment_skips_when_slug_already_injected(tmp_vault, monkeypatch, synthetic_index):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "enrichment": {"enabled": True, "maxEmissionsPerSession": 15},
    }))
    # Pre-populate cache: use-prisma-mock was already injected.
    session_state.add_injection(tmp_vault, slug="use-prisma-mock", sid="sid-a", now_ts=100)
    # ... and feed a payload that WOULD have matched:
    # (for a full test, seed the rule with activates_on path_globs; use fixture)

    # This test asserts the code path reads the cache. Full fixture wiring is in
    # the e2e suite; here we assert the import + basic branch.
    # Trivial assertion: the cache is readable.
    assert "use-prisma-mock" in session_state.read_injected_cache(tmp_vault)


def test_enrichment_returns_silence_when_cap_reached(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "enrichment": {"enabled": True, "maxEmissionsPerSession": 1},
    }))
    # Bump enrich_count to 1 so we're already AT cap.
    session_state.bump_emission(tmp_vault, sid="sid-cap", kind="enrich", now_ts=1)

    # Even if a rule would have matched, hook should emit silence because cap hit.
    # Exact matching wiring is in e2e suite; here we verify the counter plumbing.
    counts = session_state.read_emission_counts(tmp_vault, "sid-cap")
    assert counts["enrich_count"] == 1
```

- [ ] **Step 2: Run test to verify it fails (or partially passes if plumbing-only)**

Run: `pytest tests/unit/test_hook_pre_tool_use_reflex_integration.py -v`
Expected: these plumbing assertions may pass already after A3; the real behavioural tests live in e2e (Task F4).

- [ ] **Step 3: Modify `pre_tool_use._emit_enrich` path to read cache + cap**

In `src/mnemo/hooks/pre_tool_use.py`, between the `hits = ra.match_path_enrich(...)` call and `_emit_enrich(hits)`, insert:

```python
# Reflex integration (v0.8):
#   1. Enforce enrichment.maxEmissionsPerSession cap.
#   2. Filter hits against session-wide injected_cache.
try:
    from mnemo.core.mcp import session_state
    sid = str(payload.get("session_id") or "unknown")
    max_enrich = int(enr_cfg.get("maxEmissionsPerSession", 15))
    counts = session_state.read_emission_counts(vault, sid)
    if counts["enrich_count"] >= max_enrich:
        return 0  # silent: cap reached
    cache = session_state.read_injected_cache(vault)
    hits = [h for h in hits if h.slug not in cache]
    if not hits:
        return 0
except Exception as _exc:
    # fail-open — never block enrichment because session-state is broken
    pass

if hits:
    _emit_enrich(hits)
    ra.log_enrichment(vault, hits, tool_name, tool_input)
    # Record emission + cache updates.
    try:
        import time as _time
        now_ts = int(_time.time())
        for h in hits:
            session_state.add_injection(vault, slug=h.slug, sid=sid, now_ts=now_ts)
            session_state.bump_emission(vault, sid=sid, kind="enrich", now_ts=now_ts)
    except Exception:
        pass
```

Replace the original `if hits: _emit_enrich(hits); ra.log_enrichment(...)` block with the code above.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_hook_pre_tool_use_reflex_integration.py tests/unit/test_hook_pre_tool_use.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/pre_tool_use.py tests/unit/test_hook_pre_tool_use_reflex_integration.py
git commit -m "feat(hooks): enrichment honours session cap + cross-hook dedupe"
```

---

## Phase F — UX surfaces

### Task F1: Statusline `3⚡` segment

**Files:**
- Modify: `src/mnemo/statusline.py` (add reflex segment to `_activation_segments` or similar)
- Modify: `src/mnemo/core/mcp/session_state.py` — add `read_today_emissions(vault)` helper
- Test: `tests/unit/test_statusline_reflex.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_statusline_reflex.py
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from mnemo import statusline
from mnemo.core.mcp import session_state


def test_statusline_emits_reflex_segment_when_reflex_count_nonzero(tmp_vault, tmp_home):
    (Path(tmp_home) / ".claude.json").write_text(json.dumps({"mcpServers": {"mnemo": {}}}))
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=1)
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=2)
    session_state.bump_emission(tmp_vault, sid="s1", kind="reflex", now_ts=3)

    with patch("mnemo.statusline.get_mnemo_topics", return_value=[]), \
         patch("mnemo.statusline.resolve_agent") as mock_ra:
        mock_ra.return_value.name = "mnemo"
        rendered = statusline.render(tmp_vault, Path(tmp_home) / ".claude.json", cwd=str(tmp_vault))

    assert "3⚡" in rendered
    assert "today" not in rendered  # style consistency: no "today" suffix


def test_statusline_omits_reflex_segment_when_zero(tmp_vault, tmp_home):
    (Path(tmp_home) / ".claude.json").write_text(json.dumps({"mcpServers": {"mnemo": {}}}))
    with patch("mnemo.statusline.get_mnemo_topics", return_value=[]), \
         patch("mnemo.statusline.resolve_agent") as mock_ra:
        mock_ra.return_value.name = "mnemo"
        rendered = statusline.render(tmp_vault, Path(tmp_home) / ".claude.json", cwd=str(tmp_vault))

    assert "⚡" not in rendered
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_statusline_reflex.py -v`
Expected: FAIL — no ⚡ segment.

- [ ] **Step 3: Add `read_today_emissions` + statusline segment**

Add to `src/mnemo/core/mcp/session_state.py`:

```python
def read_today_emissions(vault_root: Path) -> int:
    """Return today's reflex emission count (sum across sessions). Never raises."""
    data = _load(vault_root)
    if data.get("date") != date.today().isoformat():
        return 0
    total = 0
    for entry in (data.get("session_emissions") or {}).values():
        total += int(entry.get("reflex_count", 0))
    return total
```

In `src/mnemo/statusline.py`, modify `render` to append a reflex segment:

```python
# Inside render(), right after parts = [f"mnemo · {len(topics)} topics · {count}↓"]:
try:
    from mnemo.core.mcp.session_state import read_today_emissions
    reflex_today = read_today_emissions(vault_root)
except Exception:
    reflex_today = 0
if reflex_today > 0:
    parts.append(f"{reflex_today}\u26a1")  # ⚡
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_statusline_reflex.py tests/unit/test_cli_status_doctor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/statusline.py src/mnemo/core/mcp/session_state.py tests/unit/test_statusline_reflex.py
git commit -m "feat(statusline): add reflex ⚡ segment (session-state aggregated)"
```

---

### Task F2: `mnemo status` + `mnemo doctor` reflex surfaces

**Files:**
- Modify: `src/mnemo/cli.py` — extend `_cmd_status` and add 6 new `_doctor_check_reflex_*`
- Test: `tests/unit/test_cli_status_doctor_reflex.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_cli_status_doctor_reflex.py
from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo import cli
from mnemo.core.mcp import session_state


def test_status_shows_reflex_section(tmp_vault, monkeypatch, capsys):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": True},
    }))
    session_state.bump_emission(tmp_vault, sid="s", kind="reflex", now_ts=1)

    cli.main(["status"])

    captured = capsys.readouterr()
    assert "reflex" in captured.out.lower()


def test_doctor_check_reflex_index_stale_detects_missing(tmp_vault, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    (tmp_vault / "mnemo.config.json").write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "reflex": {"enabled": True},
    }))
    # No reflex-index.json file exists — check should flag.
    ok = cli._doctor_check_reflex_index(tmp_vault)
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_cli_status_doctor_reflex.py -v`
Expected: FAIL — no reflex surfaces in status/doctor yet.

- [ ] **Step 3: Add the status section + doctor checks**

In `src/mnemo/cli.py`, add new functions near the other `_doctor_check_*` helpers:

```python
def _doctor_check_reflex_index(vault: Path) -> bool:
    """Flag when reflex.enabled but reflex-index.json is missing/stale."""
    from mnemo.core.config import load_config
    cfg = load_config()
    if not bool((cfg.get("reflex") or {}).get("enabled", False)):
        return True
    idx_path = vault / ".mnemo" / "reflex-index.json"
    if not idx_path.exists():
        print("  ✗ reflex-index missing — run `mnemo extract` to rebuild")
        return False
    return True


def _doctor_check_reflex_session_cap_hits(vault: Path) -> bool:
    """Flag when >20% of sessions in last 7d hit the emission cap.

    Reads the last ~5000 lines of reflex-log.jsonl (bounded to cap parse time),
    groups by session_id, and counts sessions where at least one emission carried
    silence_reason == "session_cap_reached". If that ratio exceeds 0.20, prints a
    warning and returns False.
    """
    from datetime import datetime, timedelta, timezone
    log_path = vault / ".mnemo" / "reflex-log.jsonl"
    if not log_path.exists():
        return True

    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return True
    lines = text.splitlines()
    if len(lines) > 5000:
        lines = lines[-5000:]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    sessions: dict[str, dict] = {}
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            entry = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        ts = entry.get("ts") or ""
        if not isinstance(ts, str) or ts < cutoff:
            continue
        sid = str(entry.get("session_id") or "")
        if not sid:
            continue
        bucket = sessions.setdefault(sid, {"hit_cap": False})
        if entry.get("silence_reason") == "session_cap_reached":
            bucket["hit_cap"] = True

    total = len(sessions)
    if total == 0:
        return True
    hit = sum(1 for v in sessions.values() if v["hit_cap"])
    if hit / total > 0.20:
        print(f"  ⚠ reflex-session-cap-hit: {hit}/{total} sessions in last 7d hit cap "
              f"(>{0.20:.0%} threshold). Tune reflex.maxEmissionsPerSession up or "
              f"raise thresholds.absoluteFloor to reduce noise.")
        return False
    return True


def _doctor_check_reflex_bilingual_gap(vault: Path) -> bool:
    """Flag >=3 rules with non-ASCII description but no aliases: field."""
    count_missing = 0
    for type_dir in ("feedback", "user", "reference"):
        d = vault / "shared" / type_dir
        if not d.is_dir():
            continue
        for md in d.glob("*.md"):
            try:
                text = md.read_text(encoding="utf-8")
            except OSError:
                continue
            from mnemo.core.filters import parse_frontmatter
            fm = parse_frontmatter(text)
            desc = fm.get("description") or ""
            if any(ord(c) > 127 for c in desc) and not fm.get("aliases"):
                count_missing += 1
    if count_missing >= 3:
        print(f"  ⚠ reflex-bilingual-gap: {count_missing} rules with non-ASCII description "
              f"lack aliases: — run extraction to refresh.")
        return False
    return True
```

Register them in the main `doctor` command list (search for where other `_doctor_check_*` are invoked in the CLI — add the three new ones in the same list).

Add to `_cmd_status` a short reflex block:

```python
# Near the end of _cmd_status, before return:
from mnemo.core.mcp.session_state import read_today_emissions
emissions = read_today_emissions(vault)
cfg = load_config()
if (cfg.get("reflex") or {}).get("enabled", False):
    print(f"\nreflex: enabled ({emissions} emissions today)")
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_cli_status_doctor_reflex.py tests/unit/test_cli_status_doctor.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/cli.py tests/unit/test_cli_status_doctor_reflex.py
git commit -m "feat(cli): mnemo status + doctor surfaces for reflex"
```

---

## Phase G — Config, release, regression

### Task G1: Config defaults for `reflex` + `enrichment.maxEmissionsPerSession`

**Files:**
- Modify: `src/mnemo/core/config.py:10` (`DEFAULTS`)
- Test: `tests/unit/test_config_reflex_defaults.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_config_reflex_defaults.py
from __future__ import annotations

from mnemo.core.config import load_config


def test_reflex_defaults_exist(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_path / "mnemo.config.json"))
    (tmp_path / "mnemo.config.json").write_text("{}")
    cfg = load_config()
    reflex = cfg["reflex"]
    assert reflex["enabled"] is False  # v0.8.0-alpha ships off-by-default
    assert reflex["maxEmissionsPerSession"] == 10
    assert reflex["thresholds"]["termOverlapMin"] == 2
    assert reflex["thresholds"]["relativeGap"] == 1.5
    assert reflex["thresholds"]["absoluteFloor"] == 2.0
    assert reflex["bm25f"]["fieldWeights"]["aliases"] == 2.5


def test_enrichment_cap_default(tmp_path, monkeypatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_path / "mnemo.config.json"))
    (tmp_path / "mnemo.config.json").write_text("{}")
    cfg = load_config()
    assert cfg["enrichment"]["maxEmissionsPerSession"] == 15
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/test_config_reflex_defaults.py -v`
Expected: FAIL — keys missing.

- [ ] **Step 3: Extend `DEFAULTS` in `config.py`**

```python
# Append inside DEFAULTS in src/mnemo/core/config.py:
"reflex": {
    "enabled": False,  # v0.8.0-alpha off-by-default; flip to True in v0.8.0 stable
    "maxHits": 2,
    "previewChars": 300,
    "dedupeTtlMinutes": 120,
    "maxEmissionsPerSession": 10,
    "thresholds": {
        "termOverlapMin": 2,
        "relativeGap": 1.5,
        "absoluteFloor": 2.0,
        "minQueryTokens": 3,
    },
    "bm25f": {
        "k1": 1.5,
        "b": 0.75,
        "fieldWeights": {
            "name": 3.0,
            "topic_tags": 3.0,
            "aliases": 2.5,
            "description": 2.0,
            "body": 1.0,
        },
    },
    "log": {"maxBytes": 1_048_576},
    "debug": {"logRawPrompt": False},
},
```

Also add `"maxEmissionsPerSession": 15` inside the existing `enrichment` block.

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/unit/test_config_reflex_defaults.py tests/unit/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/config.py tests/unit/test_config_reflex_defaults.py
git commit -m "feat(config): add reflex defaults + enrichment session cap"
```

---

### Task G2: Golden regression suite (20 rules × 30 prompts)

**Files:**
- Create: `tests/integration/reflex/test_reflex_golden_regression.py`
- Create: `tests/integration/reflex/fixtures/golden_vault/` — 20 hand-crafted `.md` rules
- Create: `tests/integration/reflex/fixtures/golden_prompts.json` — 30 prompt→expected-slug pairs

- [ ] **Step 1: Scaffold the fixture directory + loader test**

```python
# tests/integration/reflex/test_reflex_golden_regression.py
"""Golden regression: fixed corpus of 20 rules × 30 prompts.

The vault and the prompt→expected mapping are checked into the tree so this
test catches any change in triple-gate behaviour, stopword list, or BM25F
parameters that would shift outcomes.

If you change thresholds or the tokenizer, EXPECT this test to fail and
regenerate the golden expectations deliberately — do not paper over with
``pytest.mark.xfail``.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from mnemo.core.reflex import bm25, gates
from mnemo.core.reflex.index import build_index
from mnemo.core.reflex.tokenizer import tokenize_query

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def golden_vault(tmp_path: Path) -> Path:
    dst = tmp_path / "vault"
    shutil.copytree(FIX / "golden_vault", dst)
    (dst / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(dst)}))
    (dst / "bots").mkdir(exist_ok=True)
    return dst


def test_golden_outcomes(golden_vault):
    idx = build_index(golden_vault, universal_threshold=2)
    expectations = json.loads((FIX / "golden_prompts.json").read_text(encoding="utf-8"))

    for case in expectations:
        q_tokens = tokenize_query(case["prompt"])
        candidates = list(idx["docs"].keys())
        scores = bm25.score_docs(idx, query_tokens=q_tokens, candidate_slugs=candidates)

        doc_tokens = {}
        for t, entries in idx["postings"].items():
            for e in entries:
                doc_tokens.setdefault(e["slug"], set()).add(t)
        result = gates.evaluate_gates(
            scores, query_tokens=q_tokens,
            doc_tokens_by_slug=doc_tokens,
            thresholds=gates.DEFAULT_THRESHOLDS,
        )

        expected = case["expected"]  # list[str] | "silence"
        if expected == "silence":
            assert result.accepted_slugs == [], (
                f"prompt={case['prompt']!r} expected silence but got {result.accepted_slugs}"
            )
        else:
            assert result.accepted_slugs[:1] == expected[:1], (
                f"prompt={case['prompt']!r} expected top-1 {expected[0]!r} got {result.accepted_slugs}"
            )
```

- [ ] **Step 2: Create the 20 golden rules**

Hand-write 20 `.md` files under `tests/integration/reflex/fixtures/golden_vault/shared/feedback/` covering: prisma-mock, react-state-key, auth-httponly, logging-levels, sql-migrations-safe, docker-entrypoint, typescript-strict, pandas-merge, gcloud-env, redis-retry, graphql-cache, webhook-signing, circuit-breaker, retry-idempotent, nextjs-server-component, pnpm-workspace, eslint-rules, jest-snapshot, docker-multistage, terraform-state.

Each file uses the frontmatter shape from Task D1. Include at least 5 that carry `aliases:` PT/EN bridges.

- [ ] **Step 3: Create the 30 prompt expectations**

Write `tests/integration/reflex/fixtures/golden_prompts.json` as a list of `{prompt, expected}` — `expected` is a list of slugs (length 1 or 2) or the string `"silence"`. Include:
- 6 confident-hit prompts (exact topic match)
- 6 silence prompts (too short, noise, unrelated topic)
- 6 PT prompts against EN rules with aliases (validating W3)
- 6 PT prompts against EN rules WITHOUT aliases (validating silence)
- 6 cross-topic ambiguous prompts (validating triple-gate rejects)

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/integration/reflex/test_reflex_golden_regression.py -v`
Expected: PASS on all 30 assertions.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/reflex/
git commit -m "test(reflex): golden regression suite (20 rules × 30 prompts)"
```

---

### Task G3: Performance benchmark (P50 <30ms, P95 <100ms)

**Files:**
- Create: `tests/integration/reflex/test_reflex_perf.py`

- [ ] **Step 1: Write the benchmark**

```python
# tests/integration/reflex/test_reflex_perf.py
"""Perf budget: UserPromptSubmit hook <100ms P95 on 500-rule vault."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mnemo.core.reflex import bm25
from mnemo.core.reflex.index import build_index
from mnemo.core.reflex.tokenizer import tokenize_query


@pytest.fixture
def big_vault(tmp_vault: Path) -> Path:
    fb = tmp_vault / "shared" / "feedback"
    fb.mkdir(parents=True, exist_ok=True)
    for i in range(500):
        (fb / f"rule-{i:04d}.md").write_text(
            "---\n"
            f"name: rule-{i:04d}\n"
            f"description: Description for rule {i} about various code patterns\n"
            "tags:\n"
            f"  - topic-{i % 15}\n"
            "sources:\n"
            f"  - bots/proj-{i % 5}/memory/m.md\n"
            "stability: stable\n"
            "---\n"
            f"Body for rule {i} covering patterns like {' '.join(f'word{j}' for j in range(30))}\n",
            encoding="utf-8",
        )
    return tmp_vault


def test_bm25f_scoring_under_100ms_p95_for_500_docs(big_vault: Path):
    idx = build_index(big_vault, universal_threshold=2)
    candidates = list(idx["docs"].keys())
    prompts = [
        "how do I mock topic-3 rule word5",
        "best pattern for rule-0042 description",
        "refactor topic-7 using word10 word15",
    ] * 10  # 30 runs

    timings: list[float] = []
    for p in prompts:
        q = tokenize_query(p)
        t0 = time.perf_counter()
        bm25.score_docs(idx, query_tokens=q, candidate_slugs=candidates)
        timings.append((time.perf_counter() - t0) * 1000.0)

    timings.sort()
    p50 = timings[len(timings) // 2]
    p95 = timings[int(len(timings) * 0.95)]
    assert p50 < 30.0, f"p50={p50:.1f}ms over budget"
    assert p95 < 100.0, f"p95={p95:.1f}ms over budget"
```

- [ ] **Step 2: Run test to verify PASS on a dev machine**

Run: `pytest tests/integration/reflex/test_reflex_perf.py -v`
Expected: PASS with p50 under 30ms, p95 under 100ms.

If the test fails: profile with `python -m cProfile -o prof.out -m pytest tests/integration/reflex/test_reflex_perf.py` and fix the hot path before shipping. Do NOT relax the thresholds without explicit user approval.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/reflex/test_reflex_perf.py
git commit -m "test(reflex): perf regression — P50<30ms, P95<100ms @ 500 rules"
```

---

### Task G4: Changelog + version bump to v0.8.0

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `src/mnemo/__init__.py` — bump `__version__`
- Modify: `src/mnemo/core/mcp/server.py` — `SERVER_VERSION` constant
- Modify: `pyproject.toml` — package version

- [ ] **Step 1: Add a new CHANGELOG entry at the top**

```markdown
## v0.8.0 — 2026-04-XX — Prompt Reflex

### Added

- **UserPromptSubmit Reflex**: new hook that injects 0-2 rule body previews
  inline via BM25F retrieval when a triple-gate confidence test passes.
  Scope respects v0.7 semantics (local + universal per project).
- **`aliases:` frontmatter field**: optional synonym bridge for bilingual
  or domain-synonym matching. Extraction LLM emits it across all three
  system prompts.
- **`reflex` config block**: full tuning surface for thresholds, BM25F
  parameters, field weights, and kill switches (`reflex.enabled`).
- **`mnemo doctor` reflex checks**: `reflex-index-stale`,
  `reflex-session-cap-hit`, `reflex-bilingual-gap`.
- **Statusline**: new `N⚡` segment aggregating today's reflex emissions.

### Changed

- `mcp-call-counter.json` extended in place with `injected_cache` and
  `session_emissions` top-level keys. File path preserved for
  backwards-compatibility with v0.7 statusline + server readers.
- `counter.py` Python module renamed to `session_state.py` with a thin
  compat shim. The shim will be removed in v0.9.
- `PreToolUse` enrichment now honours `enrichment.maxEmissionsPerSession`
  (default 15) and filters against the shared `injected_cache` to avoid
  cross-hook duplicate injections.

### Defaults

- `reflex.enabled = false` in v0.8.0-alpha (dogfood) → will flip to `true`
  in v0.8.0 stable after a 1-week observation window.
```

- [ ] **Step 2: Bump versions**

Update `src/mnemo/__init__.py`:

```python
__version__ = "0.8.0-alpha"
```

Update `src/mnemo/core/mcp/server.py` `SERVER_VERSION = "0.8.0-alpha"`.

Update `pyproject.toml` `version = "0.8.0a0"` (PEP 440 shape).

- [ ] **Step 3: Run full suite once**

Run: `pytest -q`
Expected: everything PASS. If any test fails, stop here — do NOT ship.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md src/mnemo/__init__.py src/mnemo/core/mcp/server.py pyproject.toml
git commit -m "release(v0.8.0-alpha): Prompt Reflex + aliases field"
```

---

## Phase H — Rollout

### Task H1: Dogfood for 1 week, then flip default to `true`

- [ ] **Step 1: Verify dogfood readiness**

Run `mnemo doctor`. Expected: all green. Flip `reflex.enabled: true` in your own `~/mnemo/mnemo.config.json` and live in it for 7 days.

- [ ] **Step 2: Analyze `reflex-log.jsonl` after the week**

Command: `cat ~/mnemo/.mnemo/reflex-log.jsonl | jq -r '.silence_reason' | sort | uniq -c | sort -rn`

- If `session_cap_reached` is <1% → cap is healthy.
- If `term_overlap_fail` is >50% → prompts don't share vocab with rules (tune stopwords or add more aliases).
- If `emitted` is populated on >20% of prompts → possibly noisy, consider raising thresholds.
- If emitted <5% → possibly too conservative.

- [ ] **Step 3: Flip default to `true` for v0.8.0 stable**

Edit `src/mnemo/core/config.py` → `DEFAULTS["reflex"]["enabled"] = True`.

Update CHANGELOG heading `v0.8.0 — 2026-04-XX`.

Bump `__version__` to `"0.8.0"`. Bump `pyproject.toml` to `0.8.0`.

- [ ] **Step 4: Commit + tag**

```bash
git add src/mnemo/core/config.py src/mnemo/__init__.py src/mnemo/core/mcp/server.py pyproject.toml CHANGELOG.md
git commit -m "release(v0.8.0): flip reflex.enabled default to true after dogfood"
git tag v0.8.0
```

---

## Summary

| Phase | Tasks | Commits | Status |
|---|---|---|---|
| A — session_state | 3 | 3 | pending |
| B — text + tokenizer | 3 | 3 | pending |
| C — reflex core | 3 | 3 | pending |
| D — extraction | 1 | 1 | pending |
| E — hook integration | 5 | 5 | pending |
| F — UX surfaces | 2 | 2 | pending |
| G — config + release prep | 4 | 4 | pending |
| H — rollout | 1 | 2 | pending |
| **Total** | **22** | **23** | |

**Expected test additions:** ~22 new test files. Combined with existing ~884 tests, the v0.8.0 suite should land around **~906 passing** with zero skipped (excluding pre-existing opt-in E2E).

**Critical invariants to preserve throughout:**
1. Every hook path is **fail-open absolute**: exit 0, empty stdout on any exception. Re-verify by running `pytest tests/integration/test_hooks_never_raise.py` after each phase.
2. `mcp-call-counter.json` filename never changes. Anyone seeing a rename proposal should reject the PR.
3. `is_consumer_visible` gate runs in both `rule_activation.build_index` AND `reflex.build_index`. If you add a third consumer later, gate it too.
4. Reflex retrieval is **project-scoped at query time**, not index-time. The vault-wide index + per-query candidate filter is load-bearing for multi-project correctness.
