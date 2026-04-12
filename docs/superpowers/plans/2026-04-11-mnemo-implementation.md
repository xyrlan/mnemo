# mnemo v0.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the mnemo v0.1 Claude Code plugin — a hooks-only, stdlib-only Python tool that captures every Claude Code session into a local Obsidian-compatible vault.

**Architecture:** Four hooks (SessionStart, SessionEnd, UserPromptSubmit, PostToolUse) call thin orchestrators in `src/mnemo/hooks/` which delegate to pure-logic modules in `src/mnemo/core/`. Installation (`/mnemo init`) is driven by `src/mnemo/install/` and `src/mnemo/cli.py`. Concurrency is handled with `os.mkdir`-based locks plus POSIX `O_APPEND` atomicity. No third-party dependencies; cross-platform by construction.

**Tech Stack:** Python 3.8+ stdlib only (`pathlib`, `json`, `subprocess`, `tempfile`, `dataclasses`, `argparse`, `threading` for tests). `pytest` + `coverage` for testing (dev dependencies only). `rsync` is an optional system dependency with a pure-Python fallback.

**Spec:** `docs/specs/2026-04-11-mnemo-design.md` (commit `47ecf1b`).

**Out of scope for this plan:** M7 (beta dogfood) and M8 (launch ops). Those are operational, not implementation.

---

## File map

This plan creates the following file structure (only the **leaves** worth naming explicitly are listed):

```
mnemo/
├── pyproject.toml                          # Task 0
├── .gitignore                              # Task 0
├── README.md                               # Task 31
├── CHANGELOG.md                            # Task 31
├── LICENSE                                 # Task 31
├── .github/workflows/ci.yml                # Task 32
├── .claude-plugin/
│   ├── plugin.json                         # Task 27
│   └── marketplace.json                    # Task 27
├── src/mnemo/
│   ├── __init__.py                         # Task 0
│   ├── __main__.py                         # Task 22
│   ├── cli.py                              # Tasks 22-25
│   ├── hooks/
│   │   ├── __init__.py                     # Task 0
│   │   ├── session_start.py                # Task 13
│   │   ├── session_end.py                  # Task 14
│   │   ├── user_prompt.py                  # Task 15
│   │   └── post_tool_use.py                # Task 16
│   ├── core/
│   │   ├── __init__.py                     # Task 0
│   │   ├── config.py                       # Task 1
│   │   ├── paths.py                        # Task 2
│   │   ├── agent.py                        # Task 3
│   │   ├── locks.py                        # Task 4
│   │   ├── session.py                      # Task 5
│   │   ├── errors.py                       # Task 6
│   │   ├── log_writer.py                   # Tasks 7-8
│   │   ├── mirror.py                       # Tasks 9-10
│   │   └── wiki.py                         # Tasks 11-12
│   ├── install/
│   │   ├── __init__.py                     # Task 0
│   │   ├── preflight.py                    # Task 18
│   │   ├── scaffold.py                     # Task 19
│   │   └── settings.py                     # Tasks 20-21
│   └── templates/
│       ├── HOME.md                         # Task 28
│       ├── README.md                       # Task 28
│       ├── mnemo.config.json               # Task 28
│       └── graph-dark-gold.css             # Task 28
├── tests/
│   ├── __init__.py
│   ├── conftest.py                         # Task 0
│   ├── unit/                               # Tasks 1-21
│   ├── integration/                        # Task 17
│   └── e2e/                                # Tasks 29-30
└── docs/
    ├── getting-started.md                  # Task 31
    ├── configuration.md                    # Task 31
    └── troubleshooting.md                  # Task 31
```

**File size discipline:** Every `core/*.py` should fit in one screen (≲150 lines). Each hook is ≤30 lines. `cli.py` may grow to ~250 lines but is sliced into one function per command. If a file balloons past these targets during implementation, stop and split.

---

## How to run tests

All test commands assume `cd ~/github/mnemo` and that dev deps are installed (`pip install -e ".[dev]"`, see Task 0). If you prefer a venv: `python -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`.

- Run a single test: `pytest tests/unit/test_foo.py::test_bar -v`
- Run a whole module: `pytest tests/unit/test_foo.py -v`
- Run unit suite: `pytest tests/unit/ -v`
- Run with coverage: `pytest --cov=mnemo --cov-report=term-missing tests/`

---

# M0 — Project setup

## Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/mnemo/__init__.py`
- Create: `src/mnemo/core/__init__.py`
- Create: `src/mnemo/hooks/__init__.py`
- Create: `src/mnemo/install/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/integration/__init__.py`
- Create: `tests/e2e/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "mnemo"
version = "0.1.0.dev0"
description = "The Obsidian that populates itself so your Claude never forgets."
readme = "README.md"
requires-python = ">=3.8"
license = { text = "MIT" }
authors = [{ name = "xyrlan" }]
dependencies = []  # stdlib only — this is intentional

[project.optional-dependencies]
dev = [
    "pytest>=7.0",
    "pytest-cov>=4.0",
]

[project.scripts]
mnemo = "mnemo.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
mnemo = ["templates/*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-ra"

[tool.coverage.run]
branch = true
source = ["src/mnemo"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "if __name__ == .__main__.:",
    "raise NotImplementedError",
]
```

- [ ] **Step 2: Create `.gitignore`**

```
__pycache__/
*.py[cod]
*.egg-info/
.pytest_cache/
.coverage
htmlcov/
build/
dist/
.venv/
venv/
.mypy_cache/
.DS_Store
```

- [ ] **Step 3: Create empty package `__init__.py` files**

Each of the following is the literal one-liner:

`src/mnemo/__init__.py`:
```python
__version__ = "0.1.0.dev0"
```

`src/mnemo/core/__init__.py`, `src/mnemo/hooks/__init__.py`, `src/mnemo/install/__init__.py`, `tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/e2e/__init__.py`: empty file.

- [ ] **Step 4: Create `tests/conftest.py` with shared fixtures**

```python
"""Shared pytest fixtures for mnemo tests."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def tmp_vault(tmp_path: Path) -> Path:
    """Create a minimal vault directory tree and return its root."""
    root = tmp_path / "vault"
    (root / "bots").mkdir(parents=True)
    (root / "shared").mkdir()
    (root / "wiki" / "sources").mkdir(parents=True)
    (root / "wiki" / "compiled").mkdir()
    (root / "mnemo.config.json").write_text(json.dumps({"vaultRoot": str(root)}))
    return root


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect HOME to a temp dir so ~/.claude and ~/mnemo are isolated."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows compatibility
    return home


@pytest.fixture
def tmp_tempdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect tempfile.gettempdir() to an isolated dir."""
    td = tmp_path / "tmp"
    td.mkdir()
    monkeypatch.setenv("TMPDIR", str(td))
    monkeypatch.setenv("TEMP", str(td))
    monkeypatch.setenv("TMP", str(td))
    return td
```

- [ ] **Step 5: Install dev dependencies and verify pytest discovers an empty suite**

```bash
pip install -e ".[dev]"
pytest -q
```

Expected: `no tests ran in 0.0Xs` (exit code 5 is fine for empty collection — the goal is to verify install). If you want a clean exit code, add `tests/test_smoke.py` containing `def test_smoke(): assert True` and remove it after the next task.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/ tests/
git commit -m "chore: scaffold project layout and dev tooling"
```

---

# M1 — Core modules

## Task 1: `core/config.py` — config loading with defaults and forward-compat

**Files:**
- Create: `src/mnemo/core/config.py`
- Create: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_config.py
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mnemo.core import config


def test_defaults_when_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("MNEMO_CONFIG_PATH", raising=False)
    cfg = config.load_config(missing_path=tmp_path / "nope.json")
    assert cfg["vaultRoot"]  # always set
    assert cfg["capture"]["sessionStartEnd"] is True
    assert cfg["capture"]["userPrompt"] is True
    assert cfg["capture"]["fileEdits"] is True
    assert cfg["agent"]["strategy"] == "git-root"
    assert cfg["async"]["userPrompt"] is True
    assert cfg["async"]["postToolUse"] is True


def test_user_overrides_defaults(tmp_path: Path):
    p = tmp_path / "mnemo.config.json"
    p.write_text(json.dumps({
        "vaultRoot": "~/somewhere",
        "capture": {"userPrompt": False},
    }))
    cfg = config.load_config(p)
    assert cfg["vaultRoot"] == "~/somewhere"
    assert cfg["capture"]["userPrompt"] is False
    # Other capture keys still get defaults
    assert cfg["capture"]["sessionStartEnd"] is True
    assert cfg["capture"]["fileEdits"] is True


def test_unknown_keys_preserved(tmp_path: Path):
    p = tmp_path / "mnemo.config.json"
    p.write_text(json.dumps({"futureFeatureX": {"enabled": True}}))
    cfg = config.load_config(p)
    assert cfg["futureFeatureX"] == {"enabled": True}


def test_env_var_overrides_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    p = tmp_path / "elsewhere.json"
    p.write_text(json.dumps({"vaultRoot": "/env/vault"}))
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(p))
    cfg = config.load_config()
    assert cfg["vaultRoot"] == "/env/vault"


def test_corrupted_json_returns_defaults(tmp_path: Path):
    p = tmp_path / "mnemo.config.json"
    p.write_text("{not valid json")
    cfg = config.load_config(p)
    # Falls back silently to defaults; never raises
    assert cfg["vaultRoot"]
    assert cfg["capture"]["sessionStartEnd"] is True
```

- [ ] **Step 2: Run tests, expect ImportError / failures**

```bash
pytest tests/unit/test_config.py -v
```
Expected: collection error or `ModuleNotFoundError: No module named 'mnemo.core.config'`.

- [ ] **Step 3: Implement `core/config.py`**

```python
# src/mnemo/core/config.py
"""Config loading with defaults and forward-compat preservation."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "vaultRoot": "~/mnemo",
    "capture": {
        "sessionStartEnd": True,
        "userPrompt": True,
        "fileEdits": True,
    },
    "agent": {
        "strategy": "git-root",
        "overrides": {},
    },
    "async": {
        "userPrompt": True,
        "postToolUse": True,
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def default_config_path() -> Path:
    env = os.environ.get("MNEMO_CONFIG_PATH")
    if env:
        return Path(env)
    return Path(os.path.expanduser("~/mnemo/mnemo.config.json"))


def load_config(path: Path | None = None, missing_path: Path | None = None) -> dict[str, Any]:
    """Return a config dict with all defaults populated.

    `path` overrides the default lookup. `missing_path` is a no-op convenience
    used by tests to assert "no file present" without depending on $HOME.
    """
    cfg_path = path or default_config_path()
    if missing_path is not None and not missing_path.exists():
        cfg_path = missing_path
    try:
        raw = json.loads(cfg_path.read_text())
        if not isinstance(raw, dict):
            raw = {}
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        raw = {}
    return _deep_merge(DEFAULTS, raw)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2))
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_config.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/config.py tests/unit/test_config.py
git commit -m "feat(core): config loader with defaults and forward-compat"
```

---

## Task 2: `core/paths.py` — vault path resolution helpers

**Files:**
- Create: `src/mnemo/core/paths.py`
- Create: `tests/unit/test_paths.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_paths.py
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from mnemo.core import paths


def test_vault_root_expands_tilde(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = {"vaultRoot": "~/mnemo"}
    assert paths.vault_root(cfg) == tmp_path / "mnemo"


def test_vault_root_absolute(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path / "explicit")}
    assert paths.vault_root(cfg) == tmp_path / "explicit"


def test_logs_dir_for_agent(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    assert paths.logs_dir(cfg, "myrepo") == tmp_path / "bots" / "myrepo" / "logs"


def test_memory_dir_for_agent(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    assert paths.memory_dir(cfg, "myrepo") == tmp_path / "bots" / "myrepo" / "memory"


def test_today_log_path(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    today = date.today().isoformat()
    expected = tmp_path / "bots" / "myrepo" / "logs" / f"{today}.md"
    assert paths.today_log(cfg, "myrepo") == expected


def test_errors_log_path(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path)}
    assert paths.errors_log(cfg) == tmp_path / ".errors.log"


def test_ensure_writeable_creates_dir(tmp_path: Path):
    target = tmp_path / "newdir"
    paths.ensure_writeable(target)
    assert target.exists() and target.is_dir()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_paths.py -v
```
Expected: ModuleNotFoundError.

- [ ] **Step 3: Implement `core/paths.py`**

```python
# src/mnemo/core/paths.py
"""Vault path resolution helpers."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any


def vault_root(cfg: dict[str, Any]) -> Path:
    return Path(os.path.expanduser(cfg.get("vaultRoot", "~/mnemo")))


def bots_dir(cfg: dict[str, Any]) -> Path:
    return vault_root(cfg) / "bots"


def agent_dir(cfg: dict[str, Any], agent: str) -> Path:
    return bots_dir(cfg) / agent


def logs_dir(cfg: dict[str, Any], agent: str) -> Path:
    return agent_dir(cfg, agent) / "logs"


def memory_dir(cfg: dict[str, Any], agent: str) -> Path:
    return agent_dir(cfg, agent) / "memory"


def working_dir(cfg: dict[str, Any], agent: str) -> Path:
    return agent_dir(cfg, agent) / "working"


def today_log(cfg: dict[str, Any], agent: str) -> Path:
    return logs_dir(cfg, agent) / f"{date.today().isoformat()}.md"


def errors_log(cfg: dict[str, Any]) -> Path:
    return vault_root(cfg) / ".errors.log"


def ensure_writeable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_paths.py -v
```
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/paths.py tests/unit/test_paths.py
git commit -m "feat(core): vault path resolution helpers"
```

---

## Task 3: `core/agent.py` — git repo detection and agent naming

**Files:**
- Create: `src/mnemo/core/agent.py`
- Create: `tests/unit/test_agent.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_agent.py
from __future__ import annotations

from pathlib import Path

from mnemo.core import agent


def test_resolves_to_git_root_basename(tmp_path: Path):
    repo = tmp_path / "myproject"
    (repo / ".git").mkdir(parents=True)
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    info = agent.resolve_agent(str(sub))
    assert info.name == "myproject"
    assert info.repo_root == str(repo)
    assert info.has_git is True


def test_resolves_to_basename_when_no_git(tmp_path: Path):
    folder = tmp_path / "scratch"
    folder.mkdir()
    info = agent.resolve_agent(str(folder))
    assert info.name == "scratch"
    assert info.repo_root == str(folder)
    assert info.has_git is False


def test_root_directory_fallback_name(tmp_path: Path, monkeypatch):
    # If walking up reaches filesystem root, name should not be empty.
    # Simulate by passing "/" — the basename of "/" is "" so we expect "root".
    info = agent.resolve_agent("/")
    assert info.name == "root"
    assert info.has_git is False


def test_sanitizes_unsafe_chars(tmp_path: Path):
    folder = tmp_path / "weird name with spaces"
    folder.mkdir()
    info = agent.resolve_agent(str(folder))
    # spaces collapsed to dashes, no path-traversal
    assert "/" not in info.name
    assert info.name == "weird-name-with-spaces"
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_agent.py -v
```

- [ ] **Step 3: Implement `core/agent.py`**

```python
# src/mnemo/core/agent.py
"""Git repo detection and agent naming."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentInfo:
    name: str
    repo_root: str
    has_git: bool


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _sanitize(name: str) -> str:
    cleaned = _SAFE_NAME.sub("-", name).strip("-")
    return cleaned or "root"


def _find_git_root(start: Path) -> Path | None:
    cur = start.resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_agent(cwd: str) -> AgentInfo:
    start = Path(cwd) if cwd else Path.cwd()
    git_root = _find_git_root(start)
    if git_root is not None:
        return AgentInfo(name=_sanitize(git_root.name), repo_root=str(git_root), has_git=True)
    base = start.resolve()
    return AgentInfo(name=_sanitize(base.name), repo_root=str(base), has_git=False)
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_agent.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/agent.py tests/unit/test_agent.py
git commit -m "feat(core): git-root agent resolution with name sanitization"
```

---

## Task 4: `core/locks.py` — cross-platform atomic mkdir lock with stale recovery

**Files:**
- Create: `src/mnemo/core/locks.py`
- Create: `tests/unit/test_locks.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_locks.py
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from mnemo.core import locks


def test_lock_acquires_when_free(tmp_path: Path):
    with locks.try_lock(tmp_path / "x.lock") as held:
        assert held is True
        assert (tmp_path / "x.lock").exists()
    # released after context exit
    assert not (tmp_path / "x.lock").exists()


def test_lock_returns_false_when_held(tmp_path: Path):
    lock = tmp_path / "x.lock"
    lock.mkdir()  # simulate live lock
    # Make it fresh so stale-recovery doesn't reclaim
    os.utime(lock, None)
    with locks.try_lock(lock) as held:
        assert held is False
    # We did not own it, so it must still exist
    assert lock.exists()


def test_stale_lock_is_reclaimed(tmp_path: Path):
    lock = tmp_path / "x.lock"
    lock.mkdir()
    # Set mtime to 10 minutes ago
    old = time.time() - 600
    os.utime(lock, (old, old))
    with locks.try_lock(lock, stale_after=60.0) as held:
        assert held is True


def test_nonblocking_does_not_wait(tmp_path: Path):
    lock = tmp_path / "x.lock"
    lock.mkdir()
    os.utime(lock, None)
    start = time.time()
    with locks.try_lock(lock) as held:
        assert held is False
    assert time.time() - start < 0.5
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_locks.py -v
```

- [ ] **Step 3: Implement `core/locks.py`**

```python
# src/mnemo/core/locks.py
"""Cross-platform advisory lock built on os.mkdir atomicity."""
from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from typing import Iterator


@contextlib.contextmanager
def try_lock(lock_dir: Path, stale_after: float = 60.0) -> Iterator[bool]:
    """Non-blocking advisory lock. Yields True if held, False otherwise.

    Uses os.mkdir as the atomic primitive — works identically on POSIX and
    Windows with no OS-specific imports. Reclaims the lock if the directory
    is older than `stale_after` seconds.
    """
    lock_dir = Path(lock_dir)
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    held = False
    try:
        try:
            os.mkdir(lock_dir)
            held = True
        except FileExistsError:
            try:
                age = time.time() - lock_dir.stat().st_mtime
            except OSError:
                age = 0.0
            if age > stale_after:
                try:
                    os.rmdir(lock_dir)
                    os.mkdir(lock_dir)
                    held = True
                except OSError:
                    held = False
        yield held
    finally:
        if held:
            try:
                os.rmdir(lock_dir)
            except OSError:
                pass
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_locks.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/locks.py tests/unit/test_locks.py
git commit -m "feat(core): atomic mkdir lock with stale recovery"
```

---

## Task 5: `core/session.py` — IPC cache between hooks

**Files:**
- Create: `src/mnemo/core/session.py`
- Create: `tests/unit/test_session.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_session.py
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from mnemo.core import session


def test_save_then_load_roundtrip(tmp_tempdir: Path):
    info = {"agent": "foo", "repo_root": "/x", "has_git": True}
    session.save("abc123", info)
    loaded = session.load("abc123")
    assert loaded == info


def test_load_missing_returns_none(tmp_tempdir: Path):
    assert session.load("never-existed") is None


def test_load_corrupted_returns_none_and_deletes(tmp_tempdir: Path):
    session.save("xyz", {"a": 1})
    cache_file = session._cache_file("xyz")
    cache_file.write_text("{not valid json")
    assert session.load("xyz") is None
    assert not cache_file.exists()


def test_clear_removes_file(tmp_tempdir: Path):
    session.save("toclear", {"a": 1})
    session.clear("toclear")
    assert session.load("toclear") is None


def test_cleanup_stale_removes_old_entries(tmp_tempdir: Path):
    session.save("old", {"a": 1})
    session.save("fresh", {"b": 2})
    old_file = session._cache_file("old")
    ancient = time.time() - 100_000  # >24h
    os.utime(old_file, (ancient, ancient))
    session.cleanup_stale(max_age_seconds=86400)
    assert session.load("old") is None
    assert session.load("fresh") == {"b": 2}


def test_cache_dir_under_tempdir(tmp_tempdir: Path):
    assert session._cache_dir().parent == tmp_tempdir or str(session._cache_dir()).startswith(str(tmp_tempdir))
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_session.py -v
```

- [ ] **Step 3: Implement `core/session.py`**

```python
# src/mnemo/core/session.py
"""Per-session IPC cache shared across hooks."""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


def _cache_dir() -> Path:
    return Path(tempfile.gettempdir()) / "mnemo"


def _cache_file(session_id: str) -> Path:
    safe = _SAFE_ID.sub("_", session_id) or "unknown"
    return _cache_dir() / f"session-{safe}.json"


def save(session_id: str, info: dict[str, Any]) -> None:
    _cache_dir().mkdir(parents=True, exist_ok=True)
    target = _cache_file(session_id)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(info))
    os.replace(tmp, target)


def load(session_id: str) -> dict[str, Any] | None:
    target = _cache_file(session_id)
    try:
        return json.loads(target.read_text())
    except FileNotFoundError:
        return None
    except (OSError, json.JSONDecodeError, ValueError):
        try:
            target.unlink()
        except OSError:
            pass
        return None


def clear(session_id: str) -> None:
    try:
        _cache_file(session_id).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def cleanup_stale(max_age_seconds: float = 86400.0) -> None:
    cache_dir = _cache_dir()
    if not cache_dir.exists():
        return
    cutoff = time.time() - max_age_seconds
    for f in cache_dir.glob("session-*.json"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            pass
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_session.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/session.py tests/unit/test_session.py
git commit -m "feat(core): IPC session cache with corruption self-healing"
```

---

## Task 6: `core/errors.py` — error log + circuit breaker

**Files:**
- Create: `src/mnemo/core/errors.py`
- Create: `tests/unit/test_errors.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_errors.py
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from mnemo.core import errors


def test_log_error_writes_jsonl(tmp_vault: Path):
    try:
        raise ValueError("boom")
    except ValueError as e:
        errors.log_error(tmp_vault, "session_start", e)
    log = (tmp_vault / ".errors.log").read_text().strip().splitlines()
    assert len(log) == 1
    entry = json.loads(log[0])
    assert entry["where"] == "session_start"
    assert entry["kind"] == "ValueError"
    assert "boom" in entry["message"]
    assert "timestamp" in entry


def test_log_error_never_raises_when_vault_unwritable(tmp_path: Path):
    # Pointing into a non-existent parent should NOT raise
    bogus = tmp_path / "no" / "such" / "vault"
    try:
        raise RuntimeError("x")
    except RuntimeError as e:
        errors.log_error(bogus, "anywhere", e)  # silent


def test_should_run_true_when_no_log(tmp_vault: Path):
    assert errors.should_run(tmp_vault) is True


def test_should_run_true_under_threshold(tmp_vault: Path):
    for i in range(5):
        try:
            raise ValueError(f"err{i}")
        except ValueError as e:
            errors.log_error(tmp_vault, "test", e)
    assert errors.should_run(tmp_vault) is True


def test_should_run_false_at_threshold(tmp_vault: Path):
    for i in range(11):
        try:
            raise ValueError(f"err{i}")
        except ValueError as e:
            errors.log_error(tmp_vault, "test", e)
    assert errors.should_run(tmp_vault) is False


def test_should_run_ignores_old_errors(tmp_vault: Path):
    log_path = tmp_vault / ".errors.log"
    old = (datetime.now() - timedelta(hours=2)).isoformat()
    lines = [json.dumps({"timestamp": old, "where": "x", "kind": "E", "message": "m"}) for _ in range(20)]
    log_path.write_text("\n".join(lines) + "\n")
    assert errors.should_run(tmp_vault) is True


def test_reset_archives_log(tmp_vault: Path):
    try:
        raise ValueError("x")
    except ValueError as e:
        errors.log_error(tmp_vault, "test", e)
    errors.reset(tmp_vault)
    assert not (tmp_vault / ".errors.log").exists()
    archives = list(tmp_vault.glob(".errors.log.*"))
    assert len(archives) == 1


def test_log_rotation_at_5mb(tmp_vault: Path):
    log_path = tmp_vault / ".errors.log"
    log_path.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
    try:
        raise ValueError("trigger")
    except ValueError as e:
        errors.log_error(tmp_vault, "rotation", e)
    # After rotation, .errors.log only contains the new entry (small)
    assert log_path.stat().st_size < 1024
    assert any(p.name.startswith(".errors.log.") for p in tmp_vault.iterdir())
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_errors.py -v
```

- [ ] **Step 3: Implement `core/errors.py`**

```python
# src/mnemo/core/errors.py
"""Best-effort error logging with circuit breaker."""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

ERROR_LOG_NAME = ".errors.log"
ROTATE_BYTES = 5 * 1024 * 1024
THRESHOLD_PER_HOUR = 10


def _log_path(vault_root: Path) -> Path:
    return Path(vault_root) / ERROR_LOG_NAME


def _rotate_if_needed(log_path: Path) -> None:
    try:
        if log_path.exists() and log_path.stat().st_size > ROTATE_BYTES:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            log_path.rename(log_path.with_name(f"{ERROR_LOG_NAME}.{stamp}"))
    except OSError:
        pass


def log_error(vault_root: Path, where: str, exc: BaseException) -> None:
    """Append a JSON line. Never raises."""
    try:
        log_path = _log_path(vault_root)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _rotate_if_needed(log_path)
        entry = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "where": where,
            "kind": type(exc).__name__,
            "message": str(exc),
            "traceback_summary": traceback.format_exception_only(type(exc), exc)[-1].strip(),
        }
        line = json.dumps(entry) + "\n"
        with open(log_path, "ab", buffering=0) as fh:
            fh.write(line.encode("utf-8"))
    except Exception:
        return  # never propagate


def should_run(vault_root: Path) -> bool:
    """Return False if circuit breaker is open."""
    try:
        log_path = _log_path(vault_root)
        if not log_path.exists():
            return True
        cutoff = datetime.now() - timedelta(hours=1)
        recent = 0
        with open(log_path, "rb") as fh:
            for raw in fh:
                try:
                    entry = json.loads(raw.decode("utf-8"))
                    ts = datetime.fromisoformat(entry["timestamp"])
                    if ts >= cutoff:
                        recent += 1
                except Exception:
                    continue
                if recent > THRESHOLD_PER_HOUR:
                    return False
        return recent <= THRESHOLD_PER_HOUR
    except Exception:
        return True  # fail-open: never block hooks because the breaker is broken


def reset(vault_root: Path) -> None:
    log_path = _log_path(vault_root)
    if not log_path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    try:
        log_path.rename(log_path.with_name(f"{ERROR_LOG_NAME}.{stamp}"))
    except OSError:
        pass
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_errors.py -v
```
Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/errors.py tests/unit/test_errors.py
git commit -m "feat(core): error logger and circuit breaker"
```

---

## Task 7: `core/log_writer.py` — atomic single-syscall append

**Files:**
- Create: `src/mnemo/core/log_writer.py`
- Create: `tests/unit/test_log_writer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_log_writer.py
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import log_writer


def test_appends_creates_file_with_header(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    log_writer.append_line("foo", "🟢 session started", cfg)
    log_path = tmp_vault / "bots" / "foo" / "logs" / f"{date.today().isoformat()}.md"
    content = log_path.read_text()
    assert content.startswith("---\n")
    assert "tags: [log, foo]" in content
    assert "# " in content
    assert re.search(r"- \*\*\d\d:\d\d\*\* — 🟢 session started", content)


def test_subsequent_append_does_not_duplicate_header(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    log_writer.append_line("foo", "first", cfg)
    log_writer.append_line("foo", "second", cfg)
    log_path = tmp_vault / "bots" / "foo" / "logs" / f"{date.today().isoformat()}.md"
    content = log_path.read_text()
    assert content.count("tags: [log, foo]") == 1
    assert "first" in content and "second" in content


def test_truncates_oversize_lines(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    huge = "x" * 5000
    log_writer.append_line("foo", f"💬 {huge}", cfg)
    log_path = tmp_vault / "bots" / "foo" / "logs" / f"{date.today().isoformat()}.md"
    line = [l for l in log_path.read_text().splitlines() if l.startswith("- ")][0]
    assert len(line.encode("utf-8")) <= 3800


def test_creates_parent_dirs(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path / "deeply" / "nested" / "vault")}
    log_writer.append_line("bar", "hello", cfg)
    log_path = tmp_path / "deeply" / "nested" / "vault" / "bots" / "bar" / "logs" / f"{date.today().isoformat()}.md"
    assert log_path.exists()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_log_writer.py -v
```

- [ ] **Step 3: Implement `core/log_writer.py`**

```python
# src/mnemo/core/log_writer.py
"""Atomic single-syscall append to daily log."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

from mnemo.core import paths

MAX_LINE_BYTES = 3800  # safety margin under macOS PIPE_BUF=512 we deliberately exceed; Linux=4096


def _header(agent: str) -> bytes:
    today = date.today().isoformat()
    text = (
        "---\n"
        f"tags: [log, {agent}]\n"
        f"date: {today}\n"
        "---\n"
        f"# {today} — {agent}\n"
        "\n"
    )
    return text.encode("utf-8")


def _format_line(content: str) -> bytes:
    now = datetime.now().strftime("%H:%M")
    line = f"- **{now}** — {content}\n"
    encoded = line.encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        encoded = encoded[: MAX_LINE_BYTES - 5] + b"...\n"
    return encoded


def append_line(agent: str, content: str, cfg: dict[str, Any]) -> None:
    log_path = paths.today_log(cfg, agent)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not log_path.exists()
    payload = _format_line(content)
    if fresh:
        # Header + first line in two writes is fine; concurrency only matters
        # for steady-state appends, and only one writer can win the create.
        with open(log_path, "ab", buffering=0) as fh:
            fh.write(_header(agent))
    with open(log_path, "ab", buffering=0) as fh:
        fh.write(payload)
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_log_writer.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/log_writer.py tests/unit/test_log_writer.py
git commit -m "feat(core): atomic single-syscall log writer with header bootstrap"
```

---

## Task 8: `log_writer` concurrency stress test

**Files:**
- Create: `tests/integration/test_log_writer_concurrent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_log_writer_concurrent.py
"""Critical test from spec § 10.3: 50 threads × 20 lines, no corruption."""
from __future__ import annotations

import re
import threading
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import log_writer


def test_concurrent_log_writes(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    threads = []
    n_threads = 50
    n_lines = 20

    def worker(tid: int) -> None:
        for i in range(n_lines):
            log_writer.append_line("agent", f"thread-{tid}-line-{i:02d}", cfg)

    for tid in range(n_threads):
        threads.append(threading.Thread(target=worker, args=(tid,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    log_path = tmp_vault / "bots" / "agent" / "logs" / f"{date.today().isoformat()}.md"
    content = log_path.read_text()
    expected_lines = n_threads * n_lines
    matches = re.findall(r"thread-(\d+)-line-(\d+)", content)
    assert len(matches) == expected_lines, (
        f"expected {expected_lines} log entries, got {len(matches)}"
    )
    seen = {(int(t), int(l)) for t, l in matches}
    assert len(seen) == expected_lines, "lost or duplicated log entries"
    # Header must appear exactly once.
    assert content.count("tags: [log, agent]") == 1
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/integration/test_log_writer_concurrent.py -v
```

If it passes immediately, great. If it fails on macOS due to PIPE_BUF (~512B) or unexpected interleaving, the implementation in Task 7 needs adjustment — note in Step 3.

- [ ] **Step 3: Verify or harden**

If the test fails on macOS, the cause is that PIPE_BUF on macOS is 512 bytes — well below our 3800-byte cap. Fix: lower `MAX_LINE_BYTES` to 480 in `src/mnemo/core/log_writer.py`. Re-run.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_log_writer_concurrent.py src/mnemo/core/log_writer.py
git commit -m "test(core): 50×20 thread concurrency on log writes"
```

---

# M2 — Mirror + wiki

## Task 9: `core/mirror.py` — rsync path

**Files:**
- Create: `src/mnemo/core/mirror.py`
- Create: `tests/unit/test_mirror.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_mirror.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core import mirror


def _make_claude_project(home: Path, encoded_name: str, files: dict[str, str]) -> Path:
    project_dir = home / ".claude" / "projects" / encoded_name / "memory"
    project_dir.mkdir(parents=True)
    for rel, content in files.items():
        p = project_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return project_dir


def test_extracts_agent_name_from_encoded_dir():
    assert mirror._agent_from_project_dir("-home-user-github-sg-imports") == "sg-imports"
    assert mirror._agent_from_project_dir("-Users-foo-Code-app") == "app"
    assert mirror._agent_from_project_dir("-") == "root"
    assert mirror._agent_from_project_dir("") == "root"


def test_mirror_copies_memory_files(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {
        "feedback.md": "# feedback content",
        "user_role.md": "# user role",
    })
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)
    target_dir = tmp_vault / "bots" / "myrepo" / "memory"
    assert (target_dir / "feedback.md").read_text() == "# feedback content"
    assert (target_dir / "user_role.md").read_text() == "# user role"


def test_mirror_never_deletes_user_notes(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {"a.md": "from claude"})
    cfg = {"vaultRoot": str(tmp_vault)}
    target_dir = tmp_vault / "bots" / "myrepo" / "memory"
    target_dir.mkdir(parents=True)
    (target_dir / "user_note.md").write_text("user wrote this")
    mirror.mirror_all(cfg)
    assert (target_dir / "user_note.md").exists()
    assert (target_dir / "a.md").exists()


def test_mirror_skips_when_no_claude_projects(tmp_home: Path, tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)  # must not raise
    assert (tmp_vault / "bots").exists()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_mirror.py -v
```

- [ ] **Step 3: Implement `core/mirror.py` (rsync path; pure-Python fallback in Task 10)**

```python
# src/mnemo/core/mirror.py
"""Claude → vault sync. rsync preferred, pure-Python fallback."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from mnemo.core import locks, paths


def _claude_projects_root() -> Path:
    return Path(os.path.expanduser("~/.claude/projects"))


def _agent_from_project_dir(name: str) -> str:
    cleaned = name.strip("-")
    if not cleaned:
        return "root"
    return cleaned.rsplit("-", 1)[-1] or "root"


def _has_rsync() -> bool:
    return shutil.which("rsync") is not None


def _rsync_copy(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["rsync", "-a", f"{src}/", f"{dst}/"],
        check=False,
        capture_output=True,
    )


def _python_copy(src: Path, dst: Path) -> None:
    """Pure-Python rsync substitute. Never deletes from dst."""
    dst.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_dir = dst / rel
        target_dir.mkdir(parents=True, exist_ok=True)
        for f in files:
            shutil.copy2(Path(root) / f, target_dir / f)


def mirror_all(cfg: dict[str, Any]) -> None:
    vault = paths.vault_root(cfg)
    paths.bots_dir(cfg).mkdir(parents=True, exist_ok=True)
    projects_root = _claude_projects_root()
    if not projects_root.exists():
        return
    lock_dir = vault / ".mirror.lock"
    with locks.try_lock(lock_dir) as held:
        if not held:
            return
        for project_dir in projects_root.iterdir():
            memory_src = project_dir / "memory"
            if not memory_src.is_dir():
                continue
            agent = _agent_from_project_dir(project_dir.name)
            target = paths.memory_dir(cfg, agent)
            if _has_rsync():
                _rsync_copy(memory_src, target)
            else:
                _python_copy(memory_src, target)
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_mirror.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/mirror.py tests/unit/test_mirror.py
git commit -m "feat(core): mirror Claude memories into vault (rsync + python fallback)"
```

---

## Task 10: `core/mirror.py` — explicit fallback test (no rsync)

**Files:**
- Modify: `tests/unit/test_mirror.py`

- [ ] **Step 1: Add the failing test**

Append to `tests/unit/test_mirror.py`:

```python
def test_python_fallback_when_rsync_missing(tmp_home: Path, tmp_vault: Path, monkeypatch: pytest.MonkeyPatch):
    """Critical test from spec § 10.3: test_missing_rsync_fallback."""
    _make_claude_project(tmp_home, "-home-x-myrepo", {
        "deep/nested/file.md": "deep content",
        "top.md": "top content",
    })
    monkeypatch.setattr(mirror, "_has_rsync", lambda: False)
    cfg = {"vaultRoot": str(tmp_vault)}
    mirror.mirror_all(cfg)
    target = tmp_vault / "bots" / "myrepo" / "memory"
    assert (target / "top.md").read_text() == "top content"
    assert (target / "deep" / "nested" / "file.md").read_text() == "deep content"


def test_mirror_lock_prevents_concurrent_runs(tmp_home: Path, tmp_vault: Path):
    _make_claude_project(tmp_home, "-home-x-myrepo", {"a.md": "x"})
    cfg = {"vaultRoot": str(tmp_vault)}
    # Hold the lock manually
    lock = tmp_vault / ".mirror.lock"
    lock.mkdir()
    os.utime(lock, None)  # fresh
    try:
        mirror.mirror_all(cfg)
    finally:
        lock.rmdir()
    # Second mirror noop'd, so target memory dir should not exist.
    assert not (tmp_vault / "bots" / "myrepo" / "memory" / "a.md").exists()
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/unit/test_mirror.py -v
```

- [ ] **Step 3: If passing, commit. If failing, fix and re-run.**

```bash
git add tests/unit/test_mirror.py
git commit -m "test(core): mirror python fallback and lock contention coverage"
```

---

## Task 11: `core/wiki.py` — `promote_note`

**Files:**
- Create: `src/mnemo/core/wiki.py`
- Create: `tests/unit/test_wiki.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_wiki.py
from __future__ import annotations

import re
from pathlib import Path

import pytest

from mnemo.core import wiki


def test_promote_copies_to_wiki_sources(tmp_vault: Path):
    src = tmp_vault / "bots" / "myrepo" / "logs" / "2026-04-11.md"
    src.parent.mkdir(parents=True)
    src.write_text("# my notes\nbody")
    cfg = {"vaultRoot": str(tmp_vault)}
    out = wiki.promote_note(src, cfg)
    assert out.parent == tmp_vault / "wiki" / "sources"
    text = out.read_text()
    assert "---" in text
    assert "origin:" in text
    assert "promoted_at:" in text
    assert "# my notes" in text
    assert "body" in text


def test_promote_preserves_existing_frontmatter(tmp_vault: Path):
    src = tmp_vault / "shared" / "people" / "alice.md"
    src.parent.mkdir(parents=True)
    src.write_text("---\nname: Alice\n---\n# Alice")
    cfg = {"vaultRoot": str(tmp_vault)}
    out = wiki.promote_note(src, cfg)
    text = out.read_text()
    assert "name: Alice" in text
    assert "promoted_at:" in text


def test_promote_idempotent_overwrite(tmp_vault: Path):
    src = tmp_vault / "shared" / "x.md"
    src.parent.mkdir(parents=True)
    src.write_text("v1")
    cfg = {"vaultRoot": str(tmp_vault)}
    wiki.promote_note(src, cfg)
    src.write_text("v2")
    out = wiki.promote_note(src, cfg)
    assert "v2" in out.read_text()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_wiki.py -v
```

- [ ] **Step 3: Implement `promote_note` in `core/wiki.py`**

```python
# src/mnemo/core/wiki.py
"""Promote and compile wiki content."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo.core import paths

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def _merge_frontmatter(existing: str, additions: dict[str, str]) -> str:
    lines = [l for l in existing.splitlines() if l.strip()]
    keys = {l.split(":", 1)[0].strip() for l in lines if ":" in l}
    for k, v in additions.items():
        if k not in keys:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def promote_note(source: Path, cfg: dict[str, Any]) -> Path:
    source = Path(source)
    text = source.read_text()
    fm, body = _split_frontmatter(text)
    additions = {
        "origin": str(source),
        "promoted_at": datetime.now().isoformat(timespec="seconds"),
    }
    merged_fm = _merge_frontmatter(fm, additions)
    new_text = f"---\n{merged_fm}\n---\n{body}"
    out_dir = paths.vault_root(cfg) / "wiki" / "sources"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / source.name
    out_path.write_text(new_text)
    return out_path
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_wiki.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/wiki.py tests/unit/test_wiki.py
git commit -m "feat(core): wiki.promote_note with frontmatter merging"
```

---

## Task 12: `core/wiki.py` — `compile_wiki`

**Files:**
- Modify: `src/mnemo/core/wiki.py`
- Modify: `tests/unit/test_wiki.py`

- [ ] **Step 1: Append failing tests**

```python
def test_compile_wiki_copies_sources_to_compiled(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    src = tmp_vault / "wiki" / "sources" / "alpha.md"
    src.write_text("# Alpha")
    (tmp_vault / "wiki" / "sources" / "beta.md").write_text("# Beta")
    wiki.compile_wiki(cfg)
    compiled = tmp_vault / "wiki" / "compiled"
    assert (compiled / "alpha.md").read_text() == "# Alpha"
    assert (compiled / "beta.md").read_text() == "# Beta"


def test_compile_wiki_writes_index(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    (tmp_vault / "wiki" / "sources" / "alpha.md").write_text("# Alpha")
    (tmp_vault / "wiki" / "sources" / "beta.md").write_text("# Beta")
    wiki.compile_wiki(cfg)
    index = (tmp_vault / "wiki" / "compiled" / "index.md").read_text()
    assert "alpha" in index
    assert "beta" in index
    assert "[[alpha]]" in index or "alpha.md" in index


def test_compile_wiki_idempotent(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    (tmp_vault / "wiki" / "sources" / "a.md").write_text("v1")
    wiki.compile_wiki(cfg)
    wiki.compile_wiki(cfg)
    assert (tmp_vault / "wiki" / "compiled" / "a.md").read_text() == "v1"
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_wiki.py -v
```

- [ ] **Step 3: Append `compile_wiki` to `src/mnemo/core/wiki.py`**

```python
def compile_wiki(cfg: dict[str, Any]) -> Path:
    sources = paths.vault_root(cfg) / "wiki" / "sources"
    compiled = paths.vault_root(cfg) / "wiki" / "compiled"
    compiled.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    index_lines = ["---", "tags: [wiki, index]", "---", "# Wiki", ""]
    entries = sorted(p for p in sources.iterdir() if p.is_file() and p.suffix == ".md")
    for src in entries:
        target = compiled / src.name
        target.write_text(src.read_text())
        stem = src.stem
        index_lines.append(f"- [[{stem}]]")
    (compiled / "index.md").write_text("\n".join(index_lines) + "\n")
    return compiled / "index.md"
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_wiki.py -v
```
Expected: 6 passed total.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/core/wiki.py tests/unit/test_wiki.py
git commit -m "feat(core): wiki.compile_wiki with index generation"
```

---

# M3 — Hooks

> Hooks all share the same skeleton: parse stdin, check breaker, load config, delegate, swallow exceptions, return 0. The TDD style here is to write the hook end-to-end against a mock stdin and assert log/state side-effects.

## Task 13: `hooks/session_start.py`

**Files:**
- Create: `src/mnemo/hooks/session_start.py`
- Create: `tests/integration/test_hook_session_start.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_hook_session_start.py
from __future__ import annotations

import io
import json
import os
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.hooks import session_start
from mnemo.core import session


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_session_start_writes_log_and_caches_session(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({
        "session_id": "S1",
        "cwd": str(repo),
        "source": "startup",
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_start.main()
    assert rc == 0
    cached = session.load("S1")
    assert cached is not None
    assert cached["agent"] == "myrepo"
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🟢 session started (startup)" in log


def test_session_start_swallows_malformed_payload(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{not valid"))
    rc = session_start.main()
    assert rc == 0  # never crash


def test_session_start_respects_disabled_capture(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = hook_env / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(hook_env),
        "capture": {"sessionStartEnd": False},
    }))
    repo = tmp_path / "r2"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({"session_id": "S2", "cwd": str(repo), "source": "resume"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_start.main()
    assert rc == 0
    log_dir = hook_env / "bots" / "r2" / "logs"
    assert not log_dir.exists() or not any(log_dir.iterdir())
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/integration/test_hook_session_start.py -v
```

- [ ] **Step 3: Implement `hooks/session_start.py`**

```python
# src/mnemo/hooks/session_start.py
"""SessionStart hook entry point."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, mirror, paths, session

        cfg = config.load_config()
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cwd = payload.get("cwd") or os.getcwd()
        ainfo = agent.resolve_agent(cwd)
        info = {
            **asdict(ainfo),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "cwd_at_start": cwd,
        }
        try:
            session.save(sid, info)
            session.cleanup_stale()
        except Exception as e:
            errors.log_error(vault, "session_start.cache", e)
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            errors.log_error(vault, "session_start.mirror", e)
        if cfg.get("capture", {}).get("sessionStartEnd", True):
            source = payload.get("source", "startup")
            try:
                log_writer.append_line(ainfo.name, f"🟢 session started ({source})", cfg)
            except Exception as e:
                errors.log_error(vault, "session_start.log", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_start.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/integration/test_hook_session_start.py -v
```
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/session_start.py tests/integration/test_hook_session_start.py
git commit -m "feat(hooks): session_start hook with caching and initial mirror"
```

---

## Task 14: `hooks/session_end.py`

**Files:**
- Create: `src/mnemo/hooks/session_end.py`
- Create: `tests/integration/test_hook_session_end.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_hook_session_end.py
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import session
from mnemo.hooks import session_end


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_session_end_logs_and_clears_cache(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S1", {"agent": "myrepo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S1", "reason": "exit"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🔴 session ended (exit)" in log
    assert session.load("S1") is None


def test_session_end_falls_back_when_cache_missing(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "r3"
    (repo / ".git").mkdir(parents=True)
    payload = json.dumps({"session_id": "missing", "reason": "compact", "cwd": str(repo)})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = session_end.main()
    assert rc == 0
    log = (hook_env / "bots" / "r3" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🔴 session ended (compact)" in log
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/integration/test_hook_session_end.py -v
```

- [ ] **Step 3: Implement `hooks/session_end.py`**

```python
# src/mnemo/hooks/session_end.py
"""SessionEnd hook entry point."""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, mirror, paths, session

        cfg = config.load_config()
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cached = session.load(sid)
        if cached and cached.get("name"):
            agent_name = cached["name"]
        elif cached and cached.get("agent"):
            agent_name = cached["agent"]
        else:
            cwd = payload.get("cwd") or os.getcwd()
            agent_name = agent.resolve_agent(cwd).name
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            errors.log_error(vault, "session_end.mirror", e)
        if cfg.get("capture", {}).get("sessionStartEnd", True):
            reason = payload.get("reason", "exit")
            try:
                log_writer.append_line(agent_name, f"🔴 session ended ({reason})", cfg)
            except Exception as e:
                errors.log_error(vault, "session_end.log", e)
        try:
            session.clear(sid)
        except Exception as e:
            errors.log_error(vault, "session_end.clear", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "session_end.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

> Note: cached info originally used `AgentInfo.name` which serializes as `name` via dataclass `asdict`. The fallback `cached.get("agent")` covers older cache formats. The `name` key is the canonical one going forward.

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/integration/test_hook_session_end.py -v
```
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/session_end.py tests/integration/test_hook_session_end.py
git commit -m "feat(hooks): session_end hook with final mirror and cache clear"
```

---

## Task 15: `hooks/user_prompt.py`

**Files:**
- Create: `src/mnemo/hooks/user_prompt.py`
- Create: `tests/integration/test_hook_user_prompt.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_hook_user_prompt.py
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import session
from mnemo.hooks import user_prompt


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_logs_first_line_of_prompt(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "add validation\nto the form"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert user_prompt.main() == 0
    log = (hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "💬 add validation" in log
    assert "to the form" not in log  # only first line


def test_truncates_long_prompts(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    huge = "x" * 500
    payload = json.dumps({"session_id": "S", "prompt": huge})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log = (hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    line = [l for l in log.splitlines() if "💬" in l][0]
    # First-line truncation cap is 200 chars in spec
    assert len(line) < 350  # accounting for prefix


def test_skips_empty_prompt(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "   \n\n"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log_path = hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()


def test_skips_system_reminder(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "<system-reminder>x</system-reminder>"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log_path = hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()


def test_escapes_backticks(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "fix `bad` thing"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log = (hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "fix" in log and "bad" in log
    # backticks neutralized to single quotes (chosen escape)
    assert "`" not in [l for l in log.splitlines() if "💬" in l][0]


def test_respects_capture_flag(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = hook_env / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(hook_env),
        "capture": {"userPrompt": False},
    }))
    session.save("S", {"name": "repo", "repo_root": "/x", "has_git": True})
    payload = json.dumps({"session_id": "S", "prompt": "anything"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    user_prompt.main()
    log_path = hook_env / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/integration/test_hook_user_prompt.py -v
```

- [ ] **Step 3: Implement `hooks/user_prompt.py`**

```python
# src/mnemo/hooks/user_prompt.py
"""UserPromptSubmit hook entry point."""
from __future__ import annotations

import json
import os
import sys

PROMPT_LINE_CAP = 200


def _first_line(prompt: str) -> str:
    for raw in prompt.splitlines():
        stripped = raw.strip()
        if stripped:
            return stripped
    return ""


def _sanitize(line: str) -> str:
    cleaned = line.replace("`", "'")
    if len(cleaned) > PROMPT_LINE_CAP:
        cleaned = cleaned[: PROMPT_LINE_CAP - 3] + "..."
    return cleaned


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, paths, session

        cfg = config.load_config()
        if not cfg.get("capture", {}).get("userPrompt", True):
            return 0
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        prompt = payload.get("prompt", "") or ""
        first = _first_line(prompt)
        if not first or "system-reminder" in first.lower():
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cached = session.load(sid)
        agent_name = (cached or {}).get("name") or (cached or {}).get("agent")
        if not agent_name:
            cwd = payload.get("cwd") or os.getcwd()
            agent_name = agent.resolve_agent(cwd).name
        try:
            log_writer.append_line(agent_name, f"💬 {_sanitize(first)}", cfg)
        except Exception as e:
            errors.log_error(vault, "user_prompt.log", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "user_prompt.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/integration/test_hook_user_prompt.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/user_prompt.py tests/integration/test_hook_user_prompt.py
git commit -m "feat(hooks): user_prompt hook with first-line capture and sanitization"
```

---

## Task 16: `hooks/post_tool_use.py`

**Files:**
- Create: `src/mnemo/hooks/post_tool_use.py`
- Create: `tests/integration/test_hook_post_tool_use.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_hook_post_tool_use.py
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import session
from mnemo.hooks import post_tool_use


@pytest.fixture
def hook_env(tmp_vault: Path, tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    return tmp_vault


def test_logs_edit_with_relative_path(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    src_file = repo / "src" / "x.py"
    src_file.parent.mkdir(parents=True)
    src_file.write_text("x")
    session.save("S", {"name": "myrepo", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(src_file)},
        "tool_response": {"filePath": str(src_file), "success": True},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert post_tool_use.main() == 0
    log = (hook_env / "bots" / "myrepo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "✏️ edited `src/x.py`" in log


def test_logs_write_as_created(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    f = repo / "new.py"
    session.save("S", {"name": "r", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Write",
        "tool_input": {"file_path": str(f)},
        "tool_response": {"filePath": str(f)},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    post_tool_use.main()
    log = (hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "✏️ created `new.py`" in log


def test_uses_basename_when_outside_repo(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    outside = tmp_path / "elsewhere" / "stray.md"
    outside.parent.mkdir()
    session.save("S", {"name": "r", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(outside)},
        "tool_response": {"filePath": str(outside)},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    post_tool_use.main()
    log = (hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "stray.md" in log


def test_skips_when_file_path_missing(hook_env: Path, monkeypatch: pytest.MonkeyPatch):
    session.save("S", {"name": "r", "repo_root": "/x", "has_git": False})
    payload = json.dumps({"session_id": "S", "tool_name": "Edit", "tool_input": {}, "tool_response": {}})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    assert post_tool_use.main() == 0
    log_path = hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()


def test_respects_capture_flag(hook_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = hook_env / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(hook_env),
        "capture": {"fileEdits": False},
    }))
    repo = tmp_path / "r"
    (repo / ".git").mkdir(parents=True)
    f = repo / "x.py"
    session.save("S", {"name": "r", "repo_root": str(repo), "has_git": True})
    payload = json.dumps({
        "session_id": "S",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(f)},
        "tool_response": {"filePath": str(f)},
    })
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    post_tool_use.main()
    log_path = hook_env / "bots" / "r" / "logs" / f"{date.today().isoformat()}.md"
    assert not log_path.exists()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/integration/test_hook_post_tool_use.py -v
```

- [ ] **Step 3: Implement `hooks/post_tool_use.py`**

```python
# src/mnemo/hooks/post_tool_use.py
"""PostToolUse hook entry point (Write|Edit only)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _extract_file_path(payload: dict) -> str | None:
    response = payload.get("tool_response") or {}
    if isinstance(response, dict):
        fp = response.get("filePath") or response.get("file_path")
        if fp:
            return str(fp)
    inputs = payload.get("tool_input") or {}
    if isinstance(inputs, dict):
        fp = inputs.get("file_path")
        if fp:
            return str(fp)
    return None


def _display_path(file_path: str, repo_root: str | None) -> str:
    p = Path(file_path)
    if repo_root:
        try:
            return str(p.resolve().relative_to(Path(repo_root).resolve()))
        except ValueError:
            pass
    return p.name


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        from mnemo.core import agent, config, errors, log_writer, paths, session

        cfg = config.load_config()
        if not cfg.get("capture", {}).get("fileEdits", True):
            return 0
        vault = paths.vault_root(cfg)
        if not errors.should_run(vault):
            return 0
        file_path = _extract_file_path(payload)
        if not file_path:
            return 0
        sid = str(payload.get("session_id", "")) or "unknown"
        cached = session.load(sid)
        agent_name = (cached or {}).get("name") or (cached or {}).get("agent")
        repo_root = (cached or {}).get("repo_root")
        if not agent_name:
            cwd = payload.get("cwd") or os.getcwd()
            ainfo = agent.resolve_agent(cwd)
            agent_name = ainfo.name
            repo_root = ainfo.repo_root if ainfo.has_git else None
        verb = "created" if payload.get("tool_name") == "Write" else "edited"
        display = _display_path(file_path, repo_root)
        try:
            log_writer.append_line(agent_name, f"✏️ {verb} `{display}`", cfg)
        except Exception as e:
            errors.log_error(vault, "post_tool_use.log", e)
    except Exception as e:
        try:
            from mnemo.core import config as _c, errors as _e, paths as _p
            _e.log_error(_p.vault_root(_c.load_config()), "post_tool_use.outer", e)
        except Exception:
            pass
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/integration/test_hook_post_tool_use.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/hooks/post_tool_use.py tests/integration/test_hook_post_tool_use.py
git commit -m "feat(hooks): post_tool_use hook for Write|Edit with relative paths"
```

---

## Task 17: Hook robustness — `test_hook_never_raises` (50 malformed payloads)

**Files:**
- Create: `tests/integration/test_hooks_never_raise.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_hooks_never_raise.py
"""Critical test from spec § 10.3: hooks must never crash on malformed input."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo.hooks import session_start, session_end, user_prompt, post_tool_use

ALL_HOOKS = [session_start, session_end, user_prompt, post_tool_use]

MALFORMED_PAYLOADS = [
    # 1-10: invalid JSON shapes
    "",
    "{",
    "}",
    "null",
    "true",
    "[]",
    "[1,2,3]",
    '"a string"',
    "42",
    "{not even close",
    # 11-20: empty / minimal objects
    "{}",
    '{"session_id": null}',
    '{"session_id": ""}',
    '{"session_id": 12345}',  # numeric id
    '{"session_id": ["a","b"]}',
    '{"session_id": {"nested": true}}',
    '{"cwd": null}',
    '{"cwd": "/no/such/path/here/at/all"}',
    '{"prompt": null}',
    '{"prompt": ""}',
    # 21-30: edge content
    '{"prompt": "\\u0000"}',
    '{"prompt": "' + "x" * 50_000 + '"}',
    '{"prompt": "\\n\\n\\n"}',
    '{"prompt": "<system-reminder>x</system-reminder>"}',
    '{"prompt": "🦄🌈"}',
    '{"reason": "weird-reason"}',
    '{"source": ""}',
    '{"source": null}',
    '{"source": 42}',
    '{"tool_name": "Bash"}',  # not Write/Edit
    # 31-40: tool_input/tool_response shapes
    '{"tool_name": "Edit", "tool_input": null}',
    '{"tool_name": "Edit", "tool_input": []}',
    '{"tool_name": "Edit", "tool_input": "string"}',
    '{"tool_name": "Edit", "tool_input": {"file_path": null}}',
    '{"tool_name": "Edit", "tool_input": {"file_path": 12345}}',
    '{"tool_name": "Edit", "tool_input": {"file_path": ""}}',
    '{"tool_name": "Edit", "tool_response": null}',
    '{"tool_name": "Edit", "tool_response": "fail"}',
    '{"tool_name": "Edit", "tool_response": {"filePath": null}}',
    '{"tool_name": "Write", "tool_response": {"filePath": "/etc/passwd"}}',
    # 41-50: combinations of partial data
    '{"session_id": "x", "cwd": "/", "prompt": "ok"}',
    '{"session_id": "x", "cwd": null, "prompt": null}',
    '{"session_id": "x", "tool_name": "Edit", "tool_input": {"file_path": "/tmp/x"}}',
    '{"session_id": "abc", "reason": null}',
    '{"session_id": "abc", "source": "resume", "cwd": "/"}',
    '{"session_id": "abc", "tool_name": "Edit"}',
    '{"session_id": "x".*}',  # garbage
    '\\xff\\xfe binary',
    '{"prompt": ' + json.dumps("a" * 5000) + '}',
    '{"session_id": "ok", "cwd": "/tmp", "tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}, "tool_response": {"filePath": "/tmp/x"}}',
]


@pytest.mark.parametrize("hook", ALL_HOOKS)
@pytest.mark.parametrize("payload", MALFORMED_PAYLOADS)
def test_hook_never_raises(
    hook,
    payload: str,
    tmp_vault: Path,
    tmp_home: Path,
    tmp_tempdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    rc = hook.main()
    assert rc == 0, f"hook {hook.__name__} returned {rc} on payload {payload[:80]!r}"


def test_circuit_breaker_short_circuits_all_hooks(
    tmp_vault: Path,
    tmp_home: Path,
    tmp_tempdir: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Critical test § 10.3: test_circuit_breaker_threshold."""
    from mnemo.core import errors
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_vault / "mnemo.config.json"))
    for i in range(15):
        try:
            raise ValueError(f"e{i}")
        except ValueError as e:
            errors.log_error(tmp_vault, "test", e)
    assert not errors.should_run(tmp_vault)
    valid_payload = json.dumps({"session_id": "x", "cwd": "/tmp", "prompt": "hello"})
    for hook in ALL_HOOKS:
        monkeypatch.setattr(sys, "stdin", io.StringIO(valid_payload))
        assert hook.main() == 0
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/integration/test_hooks_never_raise.py -v
```
Expected: 200 parameterized passes + 1 circuit-breaker pass = 201 passed.

- [ ] **Step 3: If any fail, fix the offending hook (most likely a missing `try/except` around an inner `dict.get` or path operation), then re-run.**

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_hooks_never_raise.py
git commit -m "test(hooks): exhaustive malformed-payload + circuit breaker coverage"
```

---

# M4 — Install + CLI

## Task 18: `install/preflight.py`

**Files:**
- Create: `src/mnemo/install/preflight.py`
- Create: `tests/unit/test_preflight.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_preflight.py
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from mnemo.install import preflight


def test_clean_env_passes(tmp_home: Path):
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    assert result.ok is True
    assert all(i.severity != "error" for i in result.issues)


def test_python_version_check(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preflight, "_python_ok", lambda: False)
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    assert result.ok is False
    assert any(i.kind == "python_version" for i in result.issues)


def test_unwritable_vault_parent(tmp_path: Path):
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o500)  # read+exec only
    try:
        result = preflight.run_preflight(vault_root=parent / "mnemo")
        assert result.ok is False
        assert any(i.kind == "vault_unwritable" for i in result.issues)
    finally:
        parent.chmod(0o700)


def test_missing_rsync_is_warning_not_error(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    assert result.ok is True  # warning only
    assert any(i.kind == "rsync_missing" and i.severity == "warning" for i in result.issues)


def test_issue_has_remediation(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preflight, "_python_ok", lambda: False)
    result = preflight.run_preflight(vault_root=tmp_home / "mnemo")
    issues = [i for i in result.issues if i.kind == "python_version"]
    assert issues and issues[0].remediation
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_preflight.py -v
```

- [ ] **Step 3: Implement `install/preflight.py`**

```python
# src/mnemo/install/preflight.py
"""Pre-install environment validation."""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Severity = Literal["error", "warning", "info"]


@dataclass
class Issue:
    kind: str
    severity: Severity
    message: str
    remediation: str


@dataclass
class PreflightResult:
    ok: bool
    issues: list[Issue] = field(default_factory=list)


def _python_ok() -> bool:
    return sys.version_info >= (3, 8)


def _vault_writable(vault_root: Path) -> bool:
    parent = vault_root.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        probe = parent / ".mnemo-write-test"
        probe.write_text("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def _settings_writable() -> bool:
    settings = Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        settings.parent.mkdir(parents=True, exist_ok=True)
        if settings.exists():
            return os.access(settings, os.W_OK)
        return os.access(settings.parent, os.W_OK)
    except OSError:
        return False


def _disk_space_ok(vault_root: Path, min_bytes: int = 10 * 1024 * 1024) -> bool:
    try:
        target = vault_root if vault_root.exists() else vault_root.parent
        free = shutil.disk_usage(target).free
        return free >= min_bytes
    except OSError:
        return True  # don't block on probe failure


def run_preflight(vault_root: Path | None = None) -> PreflightResult:
    vault_root = Path(vault_root) if vault_root else Path(os.path.expanduser("~/mnemo"))
    issues: list[Issue] = []

    if not _python_ok():
        issues.append(Issue(
            "python_version", "error",
            f"Python 3.8+ required (have {sys.version_info.major}.{sys.version_info.minor})",
            "Upgrade Python: https://www.python.org/downloads/",
        ))
    if not _vault_writable(vault_root):
        issues.append(Issue(
            "vault_unwritable", "error",
            f"Cannot write to {vault_root.parent}",
            f"Pick a different vault location with --vault-root, or run: chmod u+w {vault_root.parent}",
        ))
    if not _settings_writable():
        issues.append(Issue(
            "settings_unwritable", "error",
            "Cannot write to ~/.claude/settings.json",
            "Run: chmod u+w ~/.claude/settings.json",
        ))
    if not _disk_space_ok(vault_root):
        issues.append(Issue(
            "disk_space", "error",
            "Less than 10MB free disk space at vault location",
            "Free up disk space or pick a different --vault-root",
        ))
    if shutil.which("rsync") is None:
        issues.append(Issue(
            "rsync_missing", "warning",
            "rsync not found in PATH — using slower pure-Python fallback",
            "Install rsync (apt install rsync / brew install rsync) for faster mirror",
        ))

    ok = not any(i.severity == "error" for i in issues)
    return PreflightResult(ok=ok, issues=issues)
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_preflight.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/install/preflight.py tests/unit/test_preflight.py
git commit -m "feat(install): preflight checks with actionable remediation"
```

---

## Task 19: `install/scaffold.py` + bundled templates

**Files:**
- Create: `src/mnemo/install/scaffold.py`
- Create: `src/mnemo/templates/HOME.md`
- Create: `src/mnemo/templates/README.md`
- Create: `src/mnemo/templates/mnemo.config.json`
- Create: `src/mnemo/templates/graph-dark-gold.css`
- Create: `tests/unit/test_scaffold.py`

> Note: Templates are placeholders here and get fleshed out in Task 28. They must exist for scaffold tests to pass.

- [ ] **Step 1: Create minimal placeholder templates**

`src/mnemo/templates/HOME.md`:
```markdown
---
tags: [home]
---
# 🧠 Welcome to your mnemo vault

Your second brain that grows while you vibe-code.
```

`src/mnemo/templates/README.md`:
```markdown
# mnemo vault

This vault was scaffolded by [mnemo](https://github.com/xyrlan/mnemo).
```

`src/mnemo/templates/mnemo.config.json`:
```json
{
  "vaultRoot": "~/mnemo",
  "capture": {
    "sessionStartEnd": true,
    "userPrompt": true,
    "fileEdits": true
  }
}
```

`src/mnemo/templates/graph-dark-gold.css`:
```css
/* mnemo graph theme — placeholder. See Task 28 for the polished version. */
.graph-view.color-fill { color: #c9a227; }
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/unit/test_scaffold.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import scaffold


def test_scaffold_creates_full_tree(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    assert (vault / "HOME.md").exists()
    assert (vault / "README.md").exists()
    assert (vault / "mnemo.config.json").exists()
    assert (vault / ".obsidian" / "snippets" / "graph-dark-gold.css").exists()
    assert (vault / "bots").is_dir()
    assert (vault / "shared" / "people").is_dir()
    assert (vault / "shared" / "companies").is_dir()
    assert (vault / "shared" / "projects").is_dir()
    assert (vault / "shared" / "decisions").is_dir()
    assert (vault / "wiki" / "sources").is_dir()
    assert (vault / "wiki" / "compiled").is_dir()


def test_scaffold_idempotent(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    (vault / "HOME.md").write_text("# user customized")
    scaffold.scaffold_vault(vault)
    assert (vault / "HOME.md").read_text() == "# user customized"


def test_scaffold_writes_config_with_vault_root(tmp_path: Path):
    vault = tmp_path / "vault"
    scaffold.scaffold_vault(vault)
    cfg = json.loads((vault / "mnemo.config.json").read_text())
    assert cfg["vaultRoot"] == str(vault)
```

- [ ] **Step 3: Run tests, expect failure**

```bash
pytest tests/unit/test_scaffold.py -v
```

- [ ] **Step 4: Implement `install/scaffold.py`**

```python
# src/mnemo/install/scaffold.py
"""Idempotent vault scaffolding."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

DIRS = [
    "bots",
    "shared/people",
    "shared/companies",
    "shared/projects",
    "shared/decisions",
    "wiki/sources",
    "wiki/compiled",
    ".obsidian/snippets",
]

TEMPLATE_FILES = {
    "HOME.md": "HOME.md",
    "README.md": "README.md",
    ".obsidian/snippets/graph-dark-gold.css": "graph-dark-gold.css",
}


def _read_template(name: str) -> str:
    return resources.files("mnemo.templates").joinpath(name).read_text(encoding="utf-8")


def scaffold_vault(vault_root: Path) -> None:
    vault_root = Path(vault_root)
    vault_root.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (vault_root / d).mkdir(parents=True, exist_ok=True)
    for rel, template_name in TEMPLATE_FILES.items():
        target = vault_root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_read_template(template_name), encoding="utf-8")
    cfg_path = vault_root / "mnemo.config.json"
    if not cfg_path.exists():
        cfg = json.loads(_read_template("mnemo.config.json"))
        cfg["vaultRoot"] = str(vault_root)
        cfg_path.write_text(json.dumps(cfg, indent=2))
```

- [ ] **Step 5: Run tests, expect green**

```bash
pytest tests/unit/test_scaffold.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/install/scaffold.py src/mnemo/templates/ tests/unit/test_scaffold.py
git commit -m "feat(install): idempotent vault scaffold with bundled templates"
```

---

## Task 20: `install/settings.py` — inject and uninject hooks

**Files:**
- Create: `src/mnemo/install/settings.py`
- Create: `tests/unit/test_settings_inject.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_settings_inject.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.install import settings


def test_inject_into_empty_settings(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]
    assert "SessionStart" in hooks
    assert "SessionEnd" in hooks
    assert "UserPromptSubmit" in hooks
    assert "PostToolUse" in hooks
    # PostToolUse must have matcher Write|Edit
    pt = hooks["PostToolUse"][0]
    assert pt["matcher"] == "Write|Edit"


def test_inject_creates_backup(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(json.dumps({"existing": True}))
    settings.inject_hooks(settings_path)
    backups = list(settings_path.parent.glob("settings.json.bak.*"))
    assert len(backups) == 1


def test_inject_preserves_other_plugin_hooks(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    other = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "other-plugin-hook"}]}],
        }
    }
    settings_path.write_text(json.dumps(other))
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    starts = data["hooks"]["SessionStart"]
    cmds = [
        h["command"]
        for entry in starts
        for h in entry.get("hooks", [])
    ]
    assert any("other-plugin-hook" in c for c in cmds)
    assert any("mnemo" in c for c in cmds)


def test_inject_idempotent(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings.inject_hooks(settings_path)
    settings.inject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    starts = data["hooks"]["SessionStart"]
    mnemo_count = sum(
        1
        for entry in starts
        for h in entry.get("hooks", [])
        if "mnemo" in h.get("command", "")
    )
    assert mnemo_count == 1


def test_uninject_removes_only_mnemo(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    other = {
        "hooks": {
            "SessionStart": [{"hooks": [{"type": "command", "command": "other-plugin-hook"}]}],
        }
    }
    settings_path.write_text(json.dumps(other))
    settings.inject_hooks(settings_path)
    settings.uninject_hooks(settings_path)
    data = json.loads(settings_path.read_text())
    cmds = [
        h["command"]
        for entry in data["hooks"].get("SessionStart", [])
        for h in entry.get("hooks", [])
    ]
    assert any("other-plugin-hook" in c for c in cmds)
    assert not any("mnemo" in c for c in cmds)


def test_inject_aborts_on_malformed_settings(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{not json")
    with pytest.raises(settings.SettingsError):
        settings.inject_hooks(settings_path)
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_settings_inject.py -v
```

- [ ] **Step 3: Implement `install/settings.py`**

```python
# src/mnemo/install/settings.py
"""Inject mnemo hooks into ~/.claude/settings.json."""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo.core import locks

MNEMO_TAG = "mnemo:"  # marker substring in command field


class SettingsError(Exception):
    pass


def _hook_command(module: str) -> str:
    """Return the command line that invokes a mnemo hook."""
    return f"{MNEMO_TAG} {sys.executable or 'python3'} -m mnemo.hooks.{module}"


HOOK_DEFINITIONS: dict[str, dict[str, Any]] = {
    "SessionStart": {
        "module": "session_start",
        "matcher": None,
        "async": False,
    },
    "SessionEnd": {
        "module": "session_end",
        "matcher": None,
        "async": False,
    },
    "UserPromptSubmit": {
        "module": "user_prompt",
        "matcher": None,
        "async": True,
    },
    "PostToolUse": {
        "module": "post_tool_use",
        "matcher": "Write|Edit",
        "async": True,
    },
}


def _build_entry(event: str, defn: dict[str, Any]) -> dict[str, Any]:
    hook = {"type": "command", "command": _hook_command(defn["module"])}
    if defn.get("async"):
        hook["async"] = True
    entry: dict[str, Any] = {"hooks": [hook]}
    if defn.get("matcher"):
        entry["matcher"] = defn["matcher"]
    return entry


def _read_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text()
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise SettingsError(
            f"Cannot parse {path}. mnemo refuses to overwrite a malformed settings.json. "
            f"Fix the JSON or remove the file and re-run /mnemo init. ({e})"
        )
    if not isinstance(data, dict):
        raise SettingsError(f"{path} root must be a JSON object")
    return data


def _strip_mnemo_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove every entry whose hook list is entirely mnemo commands; preserve mixed entries."""
    cleaned: list[dict[str, Any]] = []
    for entry in entries:
        hooks = entry.get("hooks", [])
        non_mnemo = [h for h in hooks if MNEMO_TAG not in h.get("command", "")]
        if non_mnemo:
            new = dict(entry)
            new["hooks"] = non_mnemo
            cleaned.append(new)
        # else: drop the whole entry — it was 100% mnemo
    return cleaned


def _backup(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.bak.{stamp}")
    backup.write_text(path.read_text())


def _with_lock(path: Path):
    return locks.try_lock(path.parent / ".mnemo-settings.lock")


def inject_hooks(settings_path: Path) -> None:
    settings_path = Path(settings_path)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_inject(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_inject(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    hooks = data.setdefault("hooks", {})
    for event, defn in HOOK_DEFINITIONS.items():
        existing = hooks.get(event, [])
        existing = _strip_mnemo_entries(existing)
        existing.append(_build_entry(event, defn))
        hooks[event] = existing
    settings_path.write_text(json.dumps(data, indent=2))


def uninject_hooks(settings_path: Path) -> None:
    settings_path = Path(settings_path)
    if not settings_path.exists():
        return
    deadline = time.time() + 5.0
    while True:
        with _with_lock(settings_path) as held:
            if held:
                _do_uninject(settings_path)
                return
        if time.time() > deadline:
            raise SettingsError("Timed out waiting for settings.json lock (5s)")
        time.sleep(0.05)


def _do_uninject(settings_path: Path) -> None:
    data = _read_settings(settings_path)
    _backup(settings_path)
    hooks = data.get("hooks", {})
    for event in list(HOOK_DEFINITIONS):
        if event in hooks:
            cleaned = _strip_mnemo_entries(hooks[event])
            if cleaned:
                hooks[event] = cleaned
            else:
                hooks.pop(event)
    if not hooks:
        data.pop("hooks", None)
    settings_path.write_text(json.dumps(data, indent=2))
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_settings_inject.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/install/settings.py tests/unit/test_settings_inject.py
git commit -m "feat(install): settings.json hook injection with backup and lock"
```

---

## Task 21: Concurrent inject test (`test_concurrent_inject_hooks`)

**Files:**
- Create: `tests/integration/test_settings_concurrent.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_settings_concurrent.py
"""Critical test from spec § 10.3: 5 concurrent inject_hooks must produce a valid settings.json."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from mnemo.install import settings as inj


def test_concurrent_inject_hooks(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    errors: list[Exception] = []

    def worker():
        try:
            inj.inject_hooks(settings_path)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors: {errors}"
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]
    for event in ("SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse"):
        assert event in hooks
        mnemo_count = sum(
            1
            for entry in hooks[event]
            for h in entry.get("hooks", [])
            if "mnemo" in h.get("command", "")
        )
        assert mnemo_count == 1, f"{event} has {mnemo_count} mnemo entries (expected exactly 1)"
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/integration/test_settings_concurrent.py -v
```
Expected: pass. If it fails (race condition), debug — likely a missed lock acquire/release path in `install/settings.py`.

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_settings_concurrent.py
git commit -m "test(install): 5×concurrent inject_hooks idempotency"
```

---

## Task 22: `cli.py` skeleton — argparse dispatch

**Files:**
- Create: `src/mnemo/cli.py`
- Create: `src/mnemo/__main__.py`
- Create: `tests/unit/test_cli_dispatch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cli_dispatch.py
from __future__ import annotations

import pytest

from mnemo import cli


def test_help_lists_all_commands(capsys: pytest.CaptureFixture):
    rc = cli.main(["help"])
    captured = capsys.readouterr()
    assert rc == 0
    for cmd in ("init", "status", "doctor", "open", "promote", "compile", "fix", "uninstall", "help"):
        assert cmd in captured.out


def test_unknown_command_returns_nonzero(capsys: pytest.CaptureFixture):
    rc = cli.main(["bogus-cmd"])
    captured = capsys.readouterr()
    assert rc != 0


def test_no_args_shows_help(capsys: pytest.CaptureFixture):
    rc = cli.main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert "init" in captured.out
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_cli_dispatch.py -v
```

- [ ] **Step 3: Implement `src/mnemo/cli.py` skeleton**

```python
# src/mnemo/cli.py
"""mnemo command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


def command(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], int]) -> Callable:
        COMMANDS[name] = fn
        return fn
    return deco


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mnemo", description="The Obsidian that populates itself.")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="first-run setup (idempotent)")
    init.add_argument("--yes", "-y", action="store_true", help="skip prompts (for automation)")
    init.add_argument("--vault-root", type=str, default=None, help="override vault location")
    init.add_argument("--no-mirror", action="store_true", help="skip initial Claude memory mirror")
    init.add_argument("--quiet", action="store_true", help="suppress informational output")

    sub.add_parser("status", help="vault state + hook health + recent activity")
    sub.add_parser("doctor", help="full diagnostic with actionable fixes")
    sub.add_parser("open", help="open vault in Obsidian or file manager")
    promote = sub.add_parser("promote", help="promote a note to wiki/sources/")
    promote.add_argument("source", type=str)
    sub.add_parser("compile", help="regenerate wiki/compiled/ from sources")
    sub.add_parser("fix", help="reset circuit breaker")
    uninstall = sub.add_parser("uninstall", help="remove hooks (keeps vault)")
    uninstall.add_argument("--yes", "-y", action="store_true")
    sub.add_parser("help", help="list commands")
    return p


@command("help")
def cmd_help(_args: argparse.Namespace) -> int:
    parser = _build_parser()
    parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    name = args.command or "help"
    fn = COMMANDS.get(name)
    if fn is None:
        print(f"unknown command: {name}", file=sys.stderr)
        return 2
    try:
        return fn(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
```

- [ ] **Step 4: Implement `src/mnemo/__main__.py`**

```python
# src/mnemo/__main__.py
from mnemo.cli import main

if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
```

- [ ] **Step 5: Run tests, expect green**

```bash
pytest tests/unit/test_cli_dispatch.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/cli.py src/mnemo/__main__.py tests/unit/test_cli_dispatch.py
git commit -m "feat(cli): argparse dispatch skeleton with help"
```

---

## Task 23: `cli init` command (interactive + `--yes`)

**Files:**
- Modify: `src/mnemo/cli.py`
- Create: `tests/integration/test_cli_init.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_cli_init.py
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from mnemo import cli


def test_init_yes_creates_vault_and_injects(tmp_home: Path, capsys: pytest.CaptureFixture):
    rc = cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault")])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    vault = tmp_home / "vault"
    assert (vault / "HOME.md").exists()
    assert (vault / "mnemo.config.json").exists()
    settings_path = tmp_home / ".claude" / "settings.json"
    assert settings_path.exists()
    data = json.loads(settings_path.read_text())
    assert "SessionStart" in data["hooks"]


def test_init_idempotent(tmp_home: Path):
    args = ["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"]
    assert cli.main(args) == 0
    assert cli.main(args) == 0


def test_init_no_mirror_skips_claude_sync(tmp_home: Path):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    # No bots/<agent>/memory dirs should have been created from a sync.
    bots = tmp_home / "v" / "bots"
    assert bots.exists()
    assert not any(bots.iterdir())


def test_init_quiet_suppresses_stdout(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    out = capsys.readouterr().out
    assert out.strip() == ""


def test_init_interactive_uses_default_when_blank(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    answers = iter(["", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = cli.main(["init"])
    assert rc == 0
    default_vault = tmp_home / "mnemo"
    assert default_vault.exists()


def test_init_interactive_aborts_on_no(tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    answers = iter([str(tmp_home / "v"), "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    rc = cli.main(["init"])
    assert rc != 0
    captured = capsys.readouterr()
    assert "abort" in (captured.out + captured.err).lower()
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/integration/test_cli_init.py -v
```

- [ ] **Step 3: Implement the `init` command in `src/mnemo/cli.py`**

Append to `src/mnemo/cli.py`:

```python
@command("init")
def cmd_init(args: argparse.Namespace) -> int:
    import json
    import os
    from mnemo.core import config as cfg_mod, mirror
    from mnemo.install import preflight, scaffold, settings as inj

    quiet = bool(args.quiet)
    say = (lambda *a, **k: None) if quiet else print

    # 1. Determine vault root
    vault_root: Path
    if args.vault_root:
        vault_root = Path(os.path.expanduser(args.vault_root))
    elif args.yes:
        vault_root = Path(os.path.expanduser("~/mnemo"))
    else:
        try:
            answer = input(f"Vault location [{os.path.expanduser('~/mnemo')}]: ").strip()
        except EOFError:
            answer = ""
        vault_root = Path(os.path.expanduser(answer or "~/mnemo"))

    # 2. Preflight
    say("Running preflight checks…")
    result = preflight.run_preflight(vault_root=vault_root)
    for issue in result.issues:
        say(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        say(f"       → {issue.remediation}")
    if not result.ok:
        print("Preflight failed. Resolve the issues above and retry.", file=sys.stderr)
        return 1

    # 3. Confirm settings.json modification (interactive only)
    if not args.yes:
        try:
            confirm = input("Modify ~/.claude/settings.json to install hooks? [y/N]: ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in ("y", "yes"):
            print("Aborted by user.", file=sys.stderr)
            return 2

    # 4. Scaffold vault
    say(f"Scaffolding vault at {vault_root}…")
    scaffold.scaffold_vault(vault_root)

    # 5. Inject hooks
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    say(f"Injecting hooks into {settings_path}…")
    try:
        inj.inject_hooks(settings_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 6. Optional initial mirror
    if not args.no_mirror:
        say("Mirroring existing Claude memories…")
        cfg = cfg_mod.load_config(vault_root / "mnemo.config.json")
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            say(f"  (mirror skipped: {e})")

    say("✅ mnemo is ready. Open the vault with: mnemo open")
    return 0
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/integration/test_cli_init.py -v
```
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/cli.py tests/integration/test_cli_init.py
git commit -m "feat(cli): /mnemo init with interactive and --yes modes"
```

---

## Task 24: `cli` — `status`, `doctor`, `fix`, `open`

**Files:**
- Modify: `src/mnemo/cli.py`
- Create: `tests/unit/test_cli_status_doctor.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cli_status_doctor.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.core import errors


def test_status_clean(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    rc = cli.main(["status"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "vault" in out.lower()
    assert "hooks" in out.lower()
    assert "circuit breaker" in out.lower()
    assert "closed" in out.lower() or "ok" in out.lower()


def test_status_reports_open_breaker(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    vault = tmp_home / "v"
    for i in range(15):
        try:
            raise ValueError(f"e{i}")
        except ValueError as e:
            errors.log_error(vault, "test", e)
    cli.main(["status"])
    out = capsys.readouterr().out
    assert "open" in out.lower()


def test_doctor_runs_preflight_and_reports(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "preflight" in out.lower() or "diagnostic" in out.lower()


def test_fix_resets_breaker(tmp_home: Path, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    vault = tmp_home / "v"
    for i in range(15):
        try:
            raise ValueError("e")
        except ValueError as e:
            errors.log_error(vault, "test", e)
    assert not errors.should_run(vault)
    cli.main(["fix"])
    assert errors.should_run(vault) is True


def test_open_returns_zero_when_no_opener(tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    monkeypatch.setattr(cli, "_run_open", lambda path: None)
    rc = cli.main(["open"])
    assert rc == 0
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_cli_status_doctor.py -v
```

- [ ] **Step 3: Implement the commands in `src/mnemo/cli.py`**

Append to `src/mnemo/cli.py`:

```python
def _resolve_vault() -> Path:
    from mnemo.core import config as cfg_mod, paths as paths_mod
    cfg = cfg_mod.load_config()
    return paths_mod.vault_root(cfg)


def _run_open(path: Path) -> None:
    import subprocess
    if sys.platform.startswith("darwin"):
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


@command("status")
def cmd_status(_args: argparse.Namespace) -> int:
    import os, json
    from mnemo.core import errors as err_mod

    vault = _resolve_vault()
    print(f"Vault: {vault}  ({'exists' if vault.exists() else 'MISSING'})")
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            installed = sum(
                1
                for ev in ("SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse")
                for entry in data.get("hooks", {}).get(ev, [])
                for h in entry.get("hooks", [])
                if "mnemo" in h.get("command", "")
            )
            print(f"Hooks installed: {installed}/4")
        except json.JSONDecodeError:
            print("Hooks: settings.json malformed (see /mnemo doctor)")
    else:
        print("Hooks: settings.json missing")
    breaker = "closed (ok)" if err_mod.should_run(vault) else "OPEN — recent errors detected"
    print(f"Circuit breaker: {breaker}")
    log = vault / ".errors.log"
    if log.exists():
        print(f"Error log: {log} ({log.stat().st_size} bytes)")
    return 0


@command("doctor")
def cmd_doctor(_args: argparse.Namespace) -> int:
    from mnemo.install import preflight
    vault = _resolve_vault()
    print("Running diagnostic / preflight checks…")
    result = preflight.run_preflight(vault_root=vault)
    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        print(f"       → {issue.remediation}")
    print("OK" if result.ok else "Issues found above.")
    return 0 if result.ok else 1


@command("fix")
def cmd_fix(_args: argparse.Namespace) -> int:
    from mnemo.core import errors as err_mod
    vault = _resolve_vault()
    err_mod.reset(vault)
    print("Circuit breaker reset.")
    return 0


@command("open")
def cmd_open(_args: argparse.Namespace) -> int:
    vault = _resolve_vault()
    _run_open(vault)
    print(f"Opened {vault}")
    return 0
```

> Note: `_run_open` is mocked by tests so we don't actually try to launch a GUI in CI.

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_cli_status_doctor.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/cli.py tests/unit/test_cli_status_doctor.py
git commit -m "feat(cli): status, doctor, fix, open commands"
```

---

## Task 25: `cli` — `promote`, `compile`, `uninstall`

**Files:**
- Modify: `src/mnemo/cli.py`
- Create: `tests/unit/test_cli_wiki_uninstall.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_cli_wiki_uninstall.py
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli


def _init(tmp_home: Path) -> Path:
    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    return tmp_home / "v"


def test_promote_copies_to_wiki_sources(tmp_home: Path, capsys: pytest.CaptureFixture):
    vault = _init(tmp_home)
    note = vault / "shared" / "notes.md"
    note.write_text("# Notes")
    rc = cli.main(["promote", str(note)])
    captured = capsys.readouterr()
    assert rc == 0
    assert (vault / "wiki" / "sources" / "notes.md").exists()


def test_compile_generates_index(tmp_home: Path):
    vault = _init(tmp_home)
    (vault / "wiki" / "sources" / "alpha.md").write_text("# A")
    (vault / "wiki" / "sources" / "beta.md").write_text("# B")
    rc = cli.main(["compile"])
    assert rc == 0
    assert (vault / "wiki" / "compiled" / "index.md").exists()
    assert (vault / "wiki" / "compiled" / "alpha.md").exists()


def test_uninstall_removes_hooks_keeps_vault(tmp_home: Path):
    vault = _init(tmp_home)
    settings_path = tmp_home / ".claude" / "settings.json"
    rc = cli.main(["uninstall", "--yes"])
    assert rc == 0
    assert vault.exists()  # vault preserved
    data = json.loads(settings_path.read_text())
    cmds = [
        h.get("command", "")
        for ev in data.get("hooks", {}).values()
        for entry in ev
        for h in entry.get("hooks", [])
    ]
    assert not any("mnemo" in c for c in cmds)


def test_uninstall_interactive_aborts_on_no(tmp_home: Path, monkeypatch: pytest.MonkeyPatch):
    _init(tmp_home)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")
    rc = cli.main(["uninstall"])
    assert rc != 0
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_cli_wiki_uninstall.py -v
```

- [ ] **Step 3: Implement the commands in `src/mnemo/cli.py`**

Append to `src/mnemo/cli.py`:

```python
@command("promote")
def cmd_promote(args: argparse.Namespace) -> int:
    from mnemo.core import config as cfg_mod, wiki
    cfg = cfg_mod.load_config()
    src = Path(args.source)
    if not src.exists():
        print(f"Source not found: {src}", file=sys.stderr)
        return 1
    out = wiki.promote_note(src, cfg)
    print(f"Promoted to {out}")
    return 0


@command("compile")
def cmd_compile(_args: argparse.Namespace) -> int:
    from mnemo.core import config as cfg_mod, wiki
    cfg = cfg_mod.load_config()
    out = wiki.compile_wiki(cfg)
    print(f"Compiled wiki index: {out}")
    return 0


@command("uninstall")
def cmd_uninstall(args: argparse.Namespace) -> int:
    import os
    from mnemo.install import settings as inj
    if not args.yes:
        try:
            answer = input("Remove mnemo hooks from settings.json? Vault data is preserved. [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    try:
        inj.uninject_hooks(settings_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print("Hooks removed. Vault preserved.")
    return 0
```

- [ ] **Step 4: Run tests, expect green**

```bash
pytest tests/unit/test_cli_wiki_uninstall.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/cli.py tests/unit/test_cli_wiki_uninstall.py
git commit -m "feat(cli): promote, compile, uninstall commands"
```

---

## Task 26: Reversibility test (`test_uninstall_reversible`)

**Files:**
- Create: `tests/integration/test_uninstall_reversible.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/integration/test_uninstall_reversible.py
"""Critical test from spec § 10.3."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli


def test_install_uninstall_round_trip(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True)
    pre = {"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "other"}]}]}}
    settings_path.write_text(json.dumps(pre, indent=2))

    cli.main(["init", "--yes", "--vault-root", str(tmp_home / "v"), "--no-mirror", "--quiet"])
    after_install = json.loads(settings_path.read_text())
    starts = after_install["hooks"]["SessionStart"]
    cmds_after_install = [
        h.get("command", "")
        for entry in starts
        for h in entry.get("hooks", [])
    ]
    assert any("other" in c for c in cmds_after_install)
    assert any("mnemo" in c for c in cmds_after_install)

    cli.main(["uninstall", "--yes"])
    after_uninstall = json.loads(settings_path.read_text())
    starts2 = after_uninstall.get("hooks", {}).get("SessionStart", [])
    cmds_after_uninstall = [
        h.get("command", "")
        for entry in starts2
        for h in entry.get("hooks", [])
    ]
    assert any("other" in c for c in cmds_after_uninstall)
    assert not any("mnemo" in c for c in cmds_after_uninstall)
    assert (tmp_home / "v" / "HOME.md").exists()  # vault untouched
```

- [ ] **Step 2: Run the test**

```bash
pytest tests/integration/test_uninstall_reversible.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/integration/test_uninstall_reversible.py
git commit -m "test(install): full install/uninstall round-trip preserves user state"
```

---

# M5 — Plugin packaging

## Task 27: `.claude-plugin/plugin.json` and `marketplace.json`

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.claude-plugin/marketplace.json`
- Create: `tests/unit/test_plugin_manifest.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_plugin_manifest.py
from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_plugin_json_well_formed():
    data = json.loads((REPO / ".claude-plugin" / "plugin.json").read_text())
    assert data["name"] == "mnemo"
    assert data["version"]
    assert "commands" in data and isinstance(data["commands"], list)
    expected_cmds = {"init", "status", "doctor", "open", "promote", "compile", "fix", "uninstall", "help"}
    cmd_names = {c.get("name") for c in data["commands"]}
    assert expected_cmds.issubset(cmd_names)


def test_marketplace_json_well_formed():
    data = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert data["name"]
    assert "plugins" in data
```

- [ ] **Step 2: Run tests, expect failure**

```bash
pytest tests/unit/test_plugin_manifest.py -v
```

- [ ] **Step 3: Create `.claude-plugin/plugin.json`**

```json
{
  "name": "mnemo",
  "version": "0.1.0",
  "description": "The Obsidian that populates itself so your Claude never forgets.",
  "author": "xyrlan",
  "license": "MIT",
  "homepage": "https://github.com/xyrlan/mnemo",
  "commands": [
    { "name": "init",      "description": "first-run setup", "command": "python3 -m mnemo init" },
    { "name": "status",    "description": "vault state + hook health", "command": "python3 -m mnemo status" },
    { "name": "doctor",    "description": "full diagnostic", "command": "python3 -m mnemo doctor" },
    { "name": "open",      "description": "open vault in Obsidian", "command": "python3 -m mnemo open" },
    { "name": "promote",   "description": "promote a note to wiki/sources", "command": "python3 -m mnemo promote" },
    { "name": "compile",   "description": "regenerate wiki/compiled", "command": "python3 -m mnemo compile" },
    { "name": "fix",       "description": "reset circuit breaker", "command": "python3 -m mnemo fix" },
    { "name": "uninstall", "description": "remove hooks (keeps vault)", "command": "python3 -m mnemo uninstall" },
    { "name": "help",      "description": "list commands", "command": "python3 -m mnemo help" }
  ]
}
```

- [ ] **Step 4: Create `.claude-plugin/marketplace.json`**

```json
{
  "name": "mnemo-marketplace",
  "description": "mnemo plugin marketplace listing",
  "plugins": [
    {
      "name": "mnemo",
      "source": "github:xyrlan/mnemo",
      "version": "0.1.0"
    }
  ]
}
```

- [ ] **Step 5: Run tests, expect green**

```bash
pytest tests/unit/test_plugin_manifest.py -v
```
Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add .claude-plugin/ tests/unit/test_plugin_manifest.py
git commit -m "feat(plugin): claude-plugin manifest and marketplace listing"
```

---

## Task 28: Polished templates (HOME.md, README.md, config, graph CSS)

**Files:**
- Modify: `src/mnemo/templates/HOME.md`
- Modify: `src/mnemo/templates/README.md`
- Modify: `src/mnemo/templates/mnemo.config.json`
- Modify: `src/mnemo/templates/graph-dark-gold.css`

- [ ] **Step 1: Replace `src/mnemo/templates/HOME.md` with the dashboard**

```markdown
---
tags: [home, dashboard]
---
# 🧠 Welcome to your mnemo vault

This vault is **populated automatically** by mnemo as you use Claude Code.
You almost never need to write here directly — except in `working/` and `shared/`.

## Tier 1 — Raw capture (auto-managed)
- [[bots]] — daily logs and Claude memory mirror, one folder per repo

## Tier 2 — Canonical facts (you maintain)
- [[shared/people]]
- [[shared/companies]]
- [[shared/projects]]
- [[shared/decisions]]

## Tier 3 — Curated wiki
- [[wiki/sources]] — promoted notes
- [[wiki/compiled]] — regeneratable index

## Quick commands
- `/mnemo status` — health check
- `/mnemo doctor` — diagnose problems
- `/mnemo promote <file>` — move a note into the wiki
- `/mnemo compile` — regenerate the wiki index
```

- [ ] **Step 2: Replace `src/mnemo/templates/README.md`**

```markdown
# mnemo vault

This vault was scaffolded by [mnemo](https://github.com/xyrlan/mnemo) — a Claude Code
plugin that captures every session into a local Obsidian-compatible markdown vault.

## Layout

- `bots/<agent>/logs/` — daily log files (plugin-managed, append-only)
- `bots/<agent>/memory/` — mirror of Claude Code memory files
- `bots/<agent>/working/` — your scratch space (plugin never touches this)
- `shared/` — canonical facts you curate manually
- `wiki/sources/` — notes promoted with `/mnemo promote`
- `wiki/compiled/` — regenerated by `/mnemo compile`

## Open in Obsidian

Point Obsidian at this folder. The bundled `graph-dark-gold.css` snippet styles
the graph view if you enable it under Settings → Appearance → CSS Snippets.

## Privacy

100% local. No telemetry. No network calls. Delete this folder anytime.
```

- [ ] **Step 3: Replace `src/mnemo/templates/mnemo.config.json` with the full schema**

```json
{
  "vaultRoot": "~/mnemo",
  "capture": {
    "sessionStartEnd": true,
    "userPrompt": true,
    "fileEdits": true
  },
  "agent": {
    "strategy": "git-root",
    "overrides": {}
  },
  "async": {
    "userPrompt": true,
    "postToolUse": true
  }
}
```

- [ ] **Step 4: Replace `src/mnemo/templates/graph-dark-gold.css`**

```css
/* mnemo — dark gold graph theme. Drop into Obsidian → Settings → CSS snippets. */
.theme-dark .graph-view.color-fill {
  color: #c9a227;
}
.theme-dark .graph-view.color-line {
  color: #5a4a1a;
}
.theme-dark .graph-view.color-text {
  color: #e6d28b;
}
.theme-dark .graph-view.color-circle {
  color: #c9a227;
}
.theme-dark .graph-view.color-fill-tag {
  color: #f0c14b;
}
```

- [ ] **Step 5: Re-run scaffold tests to confirm nothing broke**

```bash
pytest tests/unit/test_scaffold.py -v
```
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add src/mnemo/templates/
git commit -m "feat(templates): polished HOME, README, config, and graph CSS"
```

---

# M6 — E2E + docs + CI

## Task 29: E2E full session cycle test

**Files:**
- Create: `tests/e2e/test_full_session_cycle.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/e2e/test_full_session_cycle.py
"""Critical test from spec § 10.3: full SessionStart → prompts → edits → SessionEnd."""
from __future__ import annotations

import io
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.hooks import session_start, session_end, user_prompt, post_tool_use


def _stdin(monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))


def test_full_session_cycle(tmp_home: Path, tmp_tempdir: Path, monkeypatch: pytest.MonkeyPatch):
    # 1. Install
    rc = cli.main(["init", "--yes", "--vault-root", str(tmp_home / "vault"), "--no-mirror", "--quiet"])
    assert rc == 0

    # 2. Set up a fake repo
    repo = tmp_home / "repo"
    (repo / ".git").mkdir(parents=True)
    src = repo / "src" / "main.py"
    src.parent.mkdir(parents=True)
    src.write_text("print('hi')")

    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(tmp_home / "vault" / "mnemo.config.json"))

    # 3. SessionStart
    _stdin(monkeypatch, {"session_id": "S1", "cwd": str(repo), "source": "startup"})
    assert session_start.main() == 0

    # 4. Three prompts
    for prompt in ["add validation", "fix the bug", "write tests"]:
        _stdin(monkeypatch, {"session_id": "S1", "prompt": prompt})
        assert user_prompt.main() == 0

    # 5. Two file edits
    _stdin(monkeypatch, {
        "session_id": "S1",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(src)},
        "tool_response": {"filePath": str(src)},
    })
    assert post_tool_use.main() == 0
    new_file = repo / "src" / "validate.py"
    _stdin(monkeypatch, {
        "session_id": "S1",
        "tool_name": "Write",
        "tool_input": {"file_path": str(new_file)},
        "tool_response": {"filePath": str(new_file)},
    })
    assert post_tool_use.main() == 0

    # 6. SessionEnd
    _stdin(monkeypatch, {"session_id": "S1", "reason": "exit"})
    assert session_end.main() == 0

    # 7. Verify the daily log
    log = (tmp_home / "vault" / "bots" / "repo" / "logs" / f"{date.today().isoformat()}.md").read_text()
    assert "🟢 session started (startup)" in log
    assert "💬 add validation" in log
    assert "💬 fix the bug" in log
    assert "💬 write tests" in log
    assert "✏️ edited `src/main.py`" in log
    assert "✏️ created `src/validate.py`" in log
    assert "🔴 session ended (exit)" in log

    # 8. Cache is cleared
    from mnemo.core import session as sess
    assert sess.load("S1") is None
```

- [ ] **Step 2: Run**

```bash
pytest tests/e2e/test_full_session_cycle.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_full_session_cycle.py
git commit -m "test(e2e): full session cycle from install through SessionEnd"
```

---

## Task 30: Coverage gate verification

**Files:** none new — verifies the cumulative test suite.

- [ ] **Step 1: Run the full suite with coverage**

```bash
pytest --cov=mnemo --cov-report=term-missing --cov-fail-under=85 tests/
```
Expected: all tests pass, coverage ≥85%.

- [ ] **Step 2: If coverage < 85%, identify gaps from the report and add targeted tests**

For each module under 85%, add a unit test in `tests/unit/test_<module>.py` covering the missed branches. Re-run.

- [ ] **Step 3: Commit any added tests**

```bash
git add tests/
git commit -m "test: raise coverage to ≥85% project total"
```

---

## Task 31: Top-level docs (README, getting-started, configuration, troubleshooting, CHANGELOG, LICENSE)

**Files:**
- Create: `README.md`
- Create: `CHANGELOG.md`
- Create: `LICENSE`
- Create: `docs/getting-started.md`
- Create: `docs/configuration.md`
- Create: `docs/troubleshooting.md`

- [ ] **Step 1: Create top-level `README.md`**

```markdown
# mnemo

> The Obsidian that populates itself so your Claude never forgets.

**mnemo** is a Claude Code plugin that automatically captures every session
into a local Obsidian-compatible markdown vault. Hooks-only, stdlib-only,
zero telemetry, runs identically on Linux, macOS, and Windows.

## Install

```
/plugin install mnemo@claude-plugins-official
/mnemo init
```

That's it. Now use Claude Code normally and your vault grows on its own.

## What gets captured

- **Session starts and ends** — `🟢` and `🔴` markers in the daily log
- **Every prompt** — first non-empty line, ≤200 chars
- **Every file Write/Edit** — relative path with create/edit verb
- **Claude memories** — mirrored from `~/.claude/projects/*/memory/`

## Where it goes

`~/mnemo/bots/<repo-name>/logs/YYYY-MM-DD.md`. See [docs/getting-started.md](docs/getting-started.md).

## Privacy

100% local. Zero telemetry. Zero network. No third-party packages. Read the [source](src/mnemo).

## License

MIT — see [LICENSE](LICENSE).
```

- [ ] **Step 2: Create `CHANGELOG.md`**

```markdown
# Changelog

All notable changes to mnemo will be documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] — TBD

### Added
- Hooks-only capture: SessionStart, SessionEnd, UserPromptSubmit, PostToolUse(Write|Edit)
- Three-tier vault: `bots/`, `shared/`, `wiki/`
- Mirror of `~/.claude/projects/*/memory/` to `bots/<agent>/memory/`
- `/mnemo` slash commands: init, status, doctor, open, promote, compile, fix, uninstall, help
- `--yes` non-interactive install for dotfiles
- Cross-platform atomic locks (`os.mkdir`-based)
- Circuit breaker (>10 errors/hour pauses hooks)
- Pure-Python rsync fallback for Windows
```

- [ ] **Step 3: Create `LICENSE` (MIT)**

```
MIT License

Copyright (c) 2026 xyrlan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

- [ ] **Step 4: Create `docs/getting-started.md`**

```markdown
# Getting started with mnemo

## Install (Claude Code plugin)

```
/plugin install mnemo@claude-plugins-official
/mnemo init
```

`/mnemo init` will:
1. Run preflight checks (Python version, writable vault, settings.json access)
2. Ask where to put your vault (default: `~/mnemo`)
3. Ask permission to modify `~/.claude/settings.json` (with backup)
4. Scaffold the vault directory tree
5. Mirror existing Claude Code memories

## Install (manual / dotfiles)

```bash
git clone https://github.com/xyrlan/mnemo ~/.mnemo-repo
cd ~/.mnemo-repo
pip install -e .
python -m mnemo init --yes --vault-root ~/Documents/brain
```

## Daily flow

Just use Claude Code. Open your vault in Obsidian whenever you want to browse:

```
/mnemo open
```

Your daily log lives at `~/mnemo/bots/<repo-name>/logs/YYYY-MM-DD.md`.

## Promoting notes to the wiki

```
/mnemo promote ~/mnemo/shared/people/alice.md
/mnemo compile
```

## Uninstalling

```
/mnemo uninstall
```

This removes hooks but **never** deletes your vault.
```

- [ ] **Step 5: Create `docs/configuration.md`**

```markdown
# Configuration

mnemo's config lives at `~/mnemo/mnemo.config.json` (or wherever your vault root is).

```json
{
  "vaultRoot": "~/mnemo",
  "capture": {
    "sessionStartEnd": true,
    "userPrompt": true,
    "fileEdits": true
  },
  "agent": {
    "strategy": "git-root",
    "overrides": {}
  },
  "async": {
    "userPrompt": true,
    "postToolUse": true
  }
}
```

## Keys

| Key | Default | Meaning |
|---|---|---|
| `vaultRoot` | `~/mnemo` | Where the vault lives. Tilde is expanded. |
| `capture.sessionStartEnd` | `true` | Log 🟢/🔴 markers at session boundaries |
| `capture.userPrompt` | `true` | Log first line of each prompt |
| `capture.fileEdits` | `true` | Log Write/Edit tool calls |
| `agent.strategy` | `git-root` | How agent names are derived (only `git-root` in v0.1) |
| `agent.overrides` | `{}` | Reserved for future use |
| `async.userPrompt` | `true` | Run UserPromptSubmit hook async (no visible latency) |
| `async.postToolUse` | `true` | Run PostToolUse hook async (no visible latency) |

## Environment overrides

- `MNEMO_CONFIG_PATH` — load config from this path instead of the default

## Disabling capture entirely

Set every `capture.*` to `false` and run `/mnemo status` to confirm hooks no longer write.
```

- [ ] **Step 6: Create `docs/troubleshooting.md`**

```markdown
# Troubleshooting

## `/mnemo status` says circuit breaker is OPEN

mnemo opens its circuit breaker after >10 errors in an hour. To investigate:

```
/mnemo doctor
cat ~/mnemo/.errors.log | tail
```

To reset (after fixing the underlying issue):

```
/mnemo fix
```

## Daily log isn't growing

1. `/mnemo status` — are hooks installed (4/4)?
2. `cat ~/.claude/settings.json | jq .hooks` — see the actual entries
3. `cat ~/mnemo/.errors.log` — any error log entries?
4. Check `capture.*` flags in `~/mnemo/mnemo.config.json`

## Vault path has unusual characters

mnemo sanitizes agent names but the `vaultRoot` itself must be a path your shell
and Python can access. Avoid characters like `*`, `?`, or newlines.

## Windows native (no WSL)

mnemo works on native Windows but `rsync` is missing — the pure-Python fallback
takes over automatically. It's slower (~5-10× per-file) but functional.

## My settings.json is malformed and `/mnemo init` refuses to run

That's by design — mnemo will not overwrite a settings.json it can't parse.
Fix the JSON or move it aside, then re-run `/mnemo init`.

## I want to nuke everything

```
/mnemo uninstall
rm -rf ~/mnemo  # only if you really want to lose your captured history
```
```

- [ ] **Step 7: Commit**

```bash
git add README.md CHANGELOG.md LICENSE docs/
git commit -m "docs: README, getting-started, configuration, troubleshooting, LICENSE"
```

---

## Task 32: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  test:
    name: ${{ matrix.os }} / py${{ matrix.python-version }}
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ['3.8', '3.9', '3.10', '3.11', '3.12']

    steps:
      - uses: actions/checkout@v4
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Unit tests
        run: pytest tests/unit/ -v
      - name: Integration tests
        run: pytest tests/integration/ -v
      - name: E2E tests
        run: pytest tests/e2e/ -v
      - name: Coverage gate
        run: pytest --cov=mnemo --cov-report=term-missing --cov-fail-under=85 tests/

  windows-experimental:
    name: windows-latest / py3.11 (experimental)
    runs-on: windows-latest
    continue-on-error: true
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - run: pytest tests/unit/ -v
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: matrix workflow (Linux+macOS × 5 Python versions, Windows experimental)"
```

---

## Task 33: Final green-bar verification

**Files:** none new.

- [ ] **Step 1: Run the entire suite**

```bash
pytest --cov=mnemo --cov-report=term-missing --cov-fail-under=85 -v tests/
```
Expected: every test passes; coverage ≥85% project total, ≥90% on `core/`, ≥80% on `hooks/` and `install/`, ≥70% on `cli.py`.

- [ ] **Step 2: Verify the install/uninstall round-trip on a real machine (manual)**

```bash
# In a scratch Claude Code session
python -m mnemo init --yes --vault-root /tmp/mnemo-smoke --no-mirror
python -m mnemo status
python -m mnemo uninstall --yes
ls /tmp/mnemo-smoke  # vault should still exist
```

- [ ] **Step 3: Tag a dev release**

```bash
git tag v0.1.0-dev.1
```

(Do NOT push the tag without explicit user approval — releases are user-gated.)

---

# Out-of-scope notes

**M7 (beta dogfood) and M8 (launch):** these are operational, not implementation. They are tracked in the spec § 11.1 and § 11.2 but no code work is needed.

**v0.2 LLM extraction, v0.3 enriched capture, v0.4 graph automation:** see spec § 12. Each gets its own future plan.

---

# Self-review

Spec coverage check (each spec section → task that implements it):

| Spec § | Requirement | Task |
|---|---|---|
| 4.1 | Hooks-only, no daemons | Tasks 13-16 (no scheduler) |
| 4.2 | Python 3.8+ stdlib only | Task 0 (`pyproject.toml` has `dependencies = []`) |
| 4.3 | Data flow diagram | Tasks 13-16 + 9-10 |
| 4.4 | Three-tier vault structure | Task 19 (scaffold) |
| 4.4 | `working/` user-only | Task 19 (created empty, never touched) |
| 5.2 | `core/agent.py` | Task 3 |
| 5.2 | `core/config.py` | Task 1 |
| 5.2 | `core/locks.py` | Task 4 |
| 5.2 | `core/mirror.py` (with rsync + fallback) | Tasks 9-10 |
| 5.2 | `core/session.py` | Task 5 |
| 5.2 | `core/log_writer.py` | Tasks 7-8 |
| 5.2 | `core/errors.py` (rotation, breaker) | Task 6 |
| 5.2 | `core/paths.py` | Task 2 |
| 5.2 | `core/wiki.py` (promote + compile) | Tasks 11-12 |
| 5.3 | Hook entry points | Tasks 13-16 |
| 5.4 | `install/preflight.py` | Task 18 |
| 5.4 | `install/scaffold.py` | Task 19 |
| 5.4 | `install/settings.py` (lock, backup) | Tasks 20-21 |
| 5.5 | Zero deps | Task 0 |
| 6.1-6.4 | Hook flows | Tasks 13-16 |
| 7.1 | Atomic log writes | Tasks 7-8 |
| 7.2 | Mirror lock | Tasks 9-10 |
| 7.3 | Settings lock | Tasks 20-21 |
| 7.4 | Session cache self-heal | Task 5 |
| 8.1-8.3 | Try/except + circuit breaker | Tasks 6, 13-17 |
| 8.4 | Edge cases | Task 17 (50 malformed payloads) |
| 8.5 | Backup + reversibility | Tasks 20, 26 |
| 9.3 | First-run wizard (interactive + `--yes`) | Task 23 |
| 9.4 | All slash commands | Tasks 22-25 |
| 9.5 | Uninstall | Tasks 25, 26 |
| 10.3 | Critical tests | Tasks 8 (concurrent), 17 (never_raises, breaker), 21 (concurrent inject), 26 (uninstall_reversible), 29 (full_session_cycle), 10 (rsync fallback), 4 (stale_lock_recovery) |
| 10.4 | CI matrix | Task 32 |
| Appendix A.1 | Daily log format | Task 7 (`_header` + `_format_line`) |
| Appendix A.2 | Error log JSONL | Task 6 |
| Appendix A.3 | Session cache JSON | Task 5 |
| Appendix A.4 | Config schema | Tasks 1, 28 |
| §11.1 M5 | plugin.json + marketplace.json + templates | Tasks 27, 28 |
| §11.1 M6 | E2E + docs + CI | Tasks 29-32 |

**Gaps fixed during review:**
- Initially the coverage gate had no dedicated task → added Task 30.
- Initially the `_run_open` helper wasn't testable → added a monkeypatch hook in Task 24.
- Initially the `cached.get("agent")` legacy fallback was missing → added in `hooks/session_end.py` and `hooks/post_tool_use.py` to handle older cache formats.

**Type consistency check:**
- `AgentInfo.name` (Task 3) is what gets serialized into the session cache via `asdict()` (Task 13). Hooks read it as `cached["name"]` with fallback `cached.get("agent")` for any prior-format cache files.
- `cfg` is consistently a `dict[str, Any]` returned by `config.load_config()`, never a dataclass.
- `paths.vault_root(cfg)` always returns `pathlib.Path`.
- `errors.log_error(vault_root, where, exc)` signature is identical across all callers.
- `wiki.promote_note(source, cfg)` and `wiki.compile_wiki(cfg)` both take a `cfg` dict (not a vault path) — consistent in `cli.py` and `tests/unit/test_wiki.py`.
- The settings injection marker is `MNEMO_TAG = "mnemo:"` — used in both `_strip_mnemo_entries` and the substring check in `cli.cmd_status`.
