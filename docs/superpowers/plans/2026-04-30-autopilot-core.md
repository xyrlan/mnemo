# Autopilot Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the foundational `src/mnemo/autopilot/core/` package + `mnemo autopilot {on,off,pause,status}` CLI so the 4 Tier branches (T0/T1/T2/T3) can build on a common scaffold without conflicts.

**Architecture:** New `mnemo.autopilot` package with `core/` submodule (proposals store, dispatcher, kill switch, frozen recall snapshot, PR budget, label constants) plus 4 empty Tier sub-packages. One new CLI command (`mnemo autopilot`) wires the kill switch + status reporting. Zero changes to existing artefacts beyond the parser/registry stubs.

**Tech Stack:** Python 3.10+, pytest, argparse, JSON files under `<vault>/.mnemo/`, `gh` CLI for label bootstrap. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-04-30-autopilot-core-design.md`

---

## File Structure

**Created:**
- `src/mnemo/autopilot/__init__.py`
- `src/mnemo/autopilot/core/__init__.py`
- `src/mnemo/autopilot/core/proposals.py` — proposal store (read/write/list/expire)
- `src/mnemo/autopilot/core/kill_switch.py` — `.mnemo/autopilot.json` state
- `src/mnemo/autopilot/core/dispatcher.py` — schedule/CronCreate wrapper + record-only mode
- `src/mnemo/autopilot/core/frozen_recall.py` — recall test set freezer
- `src/mnemo/autopilot/core/pr_budget.py` — PR caps + auto-pause
- `src/mnemo/autopilot/core/labels.py` — `mnemo:self-fix` constants + `gh` bootstrap
- `src/mnemo/autopilot/insights/__init__.py` (placeholder for Tier 0)
- `src/mnemo/autopilot/selffix/__init__.py` (placeholder for Tier 1)
- `src/mnemo/autopilot/tuner/__init__.py` (placeholder for Tier 2)
- `src/mnemo/autopilot/proposer/__init__.py` (placeholder for Tier 3)
- `src/mnemo/cli/commands/autopilot.py` — `@command("autopilot")` handler
- `tests/autopilot/__init__.py`
- `tests/autopilot/core/__init__.py`
- `tests/autopilot/core/test_proposals.py`
- `tests/autopilot/core/test_kill_switch.py`
- `tests/autopilot/core/test_dispatcher.py`
- `tests/autopilot/core/test_frozen_recall.py`
- `tests/autopilot/core/test_pr_budget.py`
- `tests/autopilot/core/test_labels.py`
- `tests/autopilot/cli/__init__.py`
- `tests/autopilot/cli/test_autopilot_cli.py`

**Modified:**
- `src/mnemo/cli/parser.py` — add `autopilot` subparser block
- `src/mnemo/cli/commands/__init__.py` — import the new module to trigger `@command` registration

**Helper:**
- `src/mnemo/autopilot/core/_dirs.py` — single resolver `autopilot_dir(vault_root) -> Path` for `.mnemo/` location, used everywhere

---

## Task 1: Scaffold the package tree + dirs helper

**Files:**
- Create: `src/mnemo/autopilot/__init__.py`
- Create: `src/mnemo/autopilot/core/__init__.py`
- Create: `src/mnemo/autopilot/core/_dirs.py`
- Create: `src/mnemo/autopilot/insights/__init__.py`
- Create: `src/mnemo/autopilot/selffix/__init__.py`
- Create: `src/mnemo/autopilot/tuner/__init__.py`
- Create: `src/mnemo/autopilot/proposer/__init__.py`
- Create: `tests/autopilot/__init__.py`
- Create: `tests/autopilot/core/__init__.py`
- Create: `tests/autopilot/core/test_dirs.py`

- [ ] **Step 1: Write the failing test**

`tests/autopilot/core/test_dirs.py`:

```python
from pathlib import Path

from mnemo.autopilot.core._dirs import (
    autopilot_dir,
    proposals_dir,
    autopilot_state_path,
    autopilot_budget_path,
    autopilot_jobs_path,
    frozen_recall_path,
)


def test_autopilot_dir_is_under_vault_mnemo(tmp_path: Path):
    assert autopilot_dir(tmp_path) == tmp_path / ".mnemo"


def test_paths_are_namespaced(tmp_path: Path):
    assert proposals_dir(tmp_path) == tmp_path / ".mnemo" / "proposals"
    assert autopilot_state_path(tmp_path) == tmp_path / ".mnemo" / "autopilot.json"
    assert autopilot_budget_path(tmp_path) == tmp_path / ".mnemo" / "autopilot-budget.json"
    assert autopilot_jobs_path(tmp_path) == tmp_path / ".mnemo" / "autopilot-jobs.json"
    assert frozen_recall_path(tmp_path) == tmp_path / ".mnemo" / "recall-cases.frozen.json"


def test_proposals_dir_is_created_on_demand(tmp_path: Path):
    from mnemo.autopilot.core._dirs import ensure_proposals_dir
    p = ensure_proposals_dir(tmp_path)
    assert p.exists() and p.is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_dirs.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mnemo.autopilot'`

- [ ] **Step 3: Create empty `__init__.py` files for all 5 sub-packages**

`src/mnemo/autopilot/__init__.py`:

```python
"""Autopilot — autonomous monitoring + self-fix loop for mnemo.

This package hosts the foundational core (proposals, dispatcher, kill switch,
PR budget, frozen recall set, label constants) plus 4 Tier sub-packages
(insights, selffix, tuner, proposer) that are populated by separate Tier
specs and merged sequentially.
"""
```

`src/mnemo/autopilot/core/__init__.py`:

```python
"""Shared core primitives for mnemo autopilot. See ``mnemo/autopilot/core/_dirs.py``
for filesystem layout."""
```

`src/mnemo/autopilot/insights/__init__.py`, `selffix/__init__.py`, `tuner/__init__.py`, `proposer/__init__.py` (each):

```python
"""Tier placeholder — populated by its own spec/plan."""
```

- [ ] **Step 4: Implement `_dirs.py`**

`src/mnemo/autopilot/core/_dirs.py`:

```python
"""Filesystem layout for autopilot state.

Single source of truth for paths under ``<vault_root>/.mnemo/``. Every
core module imports from here so we never hardcode ``.mnemo/...`` strings
in business logic.
"""
from __future__ import annotations

from pathlib import Path


def autopilot_dir(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo"


def proposals_dir(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "proposals"


def autopilot_state_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "autopilot.json"


def autopilot_budget_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "autopilot-budget.json"


def autopilot_jobs_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "autopilot-jobs.json"


def frozen_recall_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "recall-cases.frozen.json"


def ensure_proposals_dir(vault_root: Path) -> Path:
    p = proposals_dir(vault_root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_autopilot_dir(vault_root: Path) -> Path:
    p = autopilot_dir(vault_root)
    p.mkdir(parents=True, exist_ok=True)
    return p
```

- [ ] **Step 5: Create empty `tests/autopilot/__init__.py` and `tests/autopilot/core/__init__.py`**

Both files: empty (zero bytes).

- [ ] **Step 6: Run test to verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_dirs.py -v`
Expected: 3 PASS

- [ ] **Step 7: Commit**

```bash
git add src/mnemo/autopilot/ tests/autopilot/
git commit -m "feat(autopilot): scaffold autopilot package + paths helper"
```

---

## Task 2: Proposals store — write_proposal

**Files:**
- Create: `src/mnemo/autopilot/core/proposals.py`
- Create: `tests/autopilot/core/test_proposals.py`

- [ ] **Step 1: Write the failing test**

`tests/autopilot/core/test_proposals.py`:

```python
import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.proposals import write_proposal, Proposal


def test_write_proposal_creates_file(tmp_path: Path):
    p = write_proposal(
        vault_root=tmp_path,
        kind="rule_candidate",
        source="tier0.miss_detector",
        payload={"slug_hint": "foo-bar", "reason": "miss in recall"},
        project="mnemo",
        confidence=0.42,
    )
    assert isinstance(p, Proposal)
    assert p.kind == "rule_candidate"
    assert p.source == "tier0.miss_detector"
    assert p.project == "mnemo"
    assert p.confidence == 0.42
    assert p.status == "pending"
    assert p.created_at.endswith("Z")
    assert p.applied_pr is None

    files = list((tmp_path / ".mnemo" / "proposals").iterdir())
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert data["schema_version"] == 1
    assert data["id"] == p.id
    assert data["payload"]["slug_hint"] == "foo-bar"


def test_write_proposal_id_format(tmp_path: Path):
    p = write_proposal(
        vault_root=tmp_path, kind="dead_rule", source="x", payload={}
    )
    # id format: YYYY-MM-DDTHH-MM-SSZ-<6hex>
    parts = p.id.split("-")
    assert len(parts) >= 4
    assert p.id.endswith(parts[-1])
    assert len(parts[-1]) == 6


def test_write_proposal_rejects_unknown_kind(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown kind"):
        write_proposal(
            vault_root=tmp_path, kind="not_a_kind", source="x", payload={}
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_proposals.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement `proposals.py` (write_proposal only)**

`src/mnemo/autopilot/core/proposals.py`:

```python
"""Proposal queue for autopilot Tiers.

Storage: one JSON file per proposal under ``<vault>/.mnemo/proposals/``,
named ``<UTC-timestamp>-<6hex>.json``. Lockless append-only — parallel
agents can write concurrently; ID collisions are resolved by suffixing.
"""
from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

from mnemo.autopilot.core._dirs import ensure_proposals_dir, proposals_dir

SCHEMA_VERSION = 1

ProposalKind = Literal[
    "rule_candidate",
    "dead_rule",
    "doctor_warning",
    "bm25_tune",
    "telemetry_bug",
]
ProposalStatus = Literal["pending", "accepted", "rejected", "applied", "expired"]

_VALID_KINDS = {"rule_candidate", "dead_rule", "doctor_warning", "bm25_tune", "telemetry_bug"}
_VALID_STATUSES = {"pending", "accepted", "rejected", "applied", "expired"}


@dataclass
class Proposal:
    id: str
    kind: str
    source: str
    project: Optional[str]
    confidence: float
    payload: dict[str, Any]
    status: str
    created_at: str
    decided_at: Optional[str] = None
    applied_pr: Optional[int] = None
    schema_version: int = SCHEMA_VERSION


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_id(now: str) -> str:
    safe = now.replace(":", "-")
    return f"{safe}-{secrets.token_hex(3)}"


def _path_for(vault_root: Path, proposal_id: str) -> Path:
    return proposals_dir(vault_root) / f"{proposal_id}.json"


def write_proposal(
    *,
    vault_root: Path,
    kind: str,
    source: str,
    payload: dict[str, Any],
    project: Optional[str] = None,
    confidence: float = 0.0,
) -> Proposal:
    if kind not in _VALID_KINDS:
        raise ValueError(f"unknown kind: {kind!r} (valid: {sorted(_VALID_KINDS)})")
    ensure_proposals_dir(vault_root)
    now = _now_iso()
    proposal_id = _make_id(now)
    target = _path_for(vault_root, proposal_id)
    while target.exists():
        proposal_id = _make_id(now)
        target = _path_for(vault_root, proposal_id)

    p = Proposal(
        id=proposal_id,
        kind=kind,
        source=source,
        project=project,
        confidence=confidence,
        payload=payload,
        status="pending",
        created_at=now,
    )
    target.write_text(json.dumps(asdict(p), indent=2, sort_keys=True))
    return p
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_proposals.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/proposals.py tests/autopilot/core/test_proposals.py
git commit -m "feat(autopilot): proposals store — write_proposal"
```

---

## Task 3: Proposals store — list / update / expire

**Files:**
- Modify: `src/mnemo/autopilot/core/proposals.py`
- Modify: `tests/autopilot/core/test_proposals.py`

- [ ] **Step 1: Add failing tests for list/update/expire**

Append to `tests/autopilot/core/test_proposals.py`:

```python
from datetime import datetime, timedelta, timezone

from mnemo.autopilot.core.proposals import (
    list_proposals,
    update_status,
    expire_old,
)


def test_list_proposals_filters(tmp_path: Path):
    write_proposal(vault_root=tmp_path, kind="rule_candidate", source="a",
                   payload={}, project="mnemo")
    write_proposal(vault_root=tmp_path, kind="dead_rule", source="b",
                   payload={}, project="mnemo")
    write_proposal(vault_root=tmp_path, kind="rule_candidate", source="c",
                   payload={}, project="other")

    all_p = list_proposals(vault_root=tmp_path)
    assert len(all_p) == 3

    by_kind = list_proposals(vault_root=tmp_path, kind="rule_candidate")
    assert len(by_kind) == 2

    by_proj = list_proposals(vault_root=tmp_path, project="mnemo")
    assert len(by_proj) == 2

    by_both = list_proposals(vault_root=tmp_path, kind="dead_rule", project="mnemo")
    assert len(by_both) == 1


def test_list_proposals_empty_when_dir_missing(tmp_path: Path):
    assert list_proposals(vault_root=tmp_path) == []


def test_update_status_persists(tmp_path: Path):
    p = write_proposal(vault_root=tmp_path, kind="doctor_warning", source="x",
                       payload={"warning": "foo"})
    updated = update_status(vault_root=tmp_path, proposal_id=p.id,
                            status="applied", applied_pr=99)
    assert updated.status == "applied"
    assert updated.applied_pr == 99
    assert updated.decided_at is not None

    reread = list_proposals(vault_root=tmp_path)[0]
    assert reread.status == "applied"
    assert reread.applied_pr == 99


def test_update_status_rejects_unknown_status(tmp_path: Path):
    p = write_proposal(vault_root=tmp_path, kind="doctor_warning", source="x",
                       payload={})
    with pytest.raises(ValueError, match="unknown status"):
        update_status(vault_root=tmp_path, proposal_id=p.id, status="bogus")


def test_update_status_raises_when_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        update_status(vault_root=tmp_path, proposal_id="nope", status="applied")


def test_expire_old_marks_pending_only(tmp_path: Path, monkeypatch):
    p1 = write_proposal(vault_root=tmp_path, kind="rule_candidate", source="x",
                        payload={})
    p2 = write_proposal(vault_root=tmp_path, kind="rule_candidate", source="y",
                        payload={})

    # backdate p1 by 40 days, leave p2 fresh
    from mnemo.autopilot.core._dirs import proposals_dir as _pd
    f1 = _pd(tmp_path) / f"{p1.id}.json"
    data = json.loads(f1.read_text())
    old_ts = (datetime.now(timezone.utc) - timedelta(days=40)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    data["created_at"] = old_ts
    f1.write_text(json.dumps(data, indent=2, sort_keys=True))

    # mark p2 as already applied — expire_old must not touch it
    update_status(vault_root=tmp_path, proposal_id=p2.id, status="applied")
    p3 = write_proposal(vault_root=tmp_path, kind="rule_candidate", source="z",
                        payload={})

    n = expire_old(vault_root=tmp_path, days=30)
    assert n == 1

    statuses = {p.id: p.status for p in list_proposals(vault_root=tmp_path)}
    assert statuses[p1.id] == "expired"
    assert statuses[p2.id] == "applied"
    assert statuses[p3.id] == "pending"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_proposals.py -v`
Expected: FAIL with `ImportError: cannot import name 'list_proposals'`

- [ ] **Step 3: Implement list/update/expire**

Append to `src/mnemo/autopilot/core/proposals.py`:

```python
def _read_one(path: Path) -> Proposal:
    data = json.loads(path.read_text())
    return Proposal(
        id=data["id"],
        kind=data["kind"],
        source=data["source"],
        project=data.get("project"),
        confidence=float(data.get("confidence", 0.0)),
        payload=data.get("payload", {}),
        status=data.get("status", "pending"),
        created_at=data["created_at"],
        decided_at=data.get("decided_at"),
        applied_pr=data.get("applied_pr"),
        schema_version=data.get("schema_version", SCHEMA_VERSION),
    )


def list_proposals(
    *,
    vault_root: Path,
    status: Optional[str] = None,
    kind: Optional[str] = None,
    project: Optional[str] = None,
) -> list[Proposal]:
    pdir = proposals_dir(vault_root)
    if not pdir.exists():
        return []
    items: list[Proposal] = []
    for f in sorted(pdir.iterdir()):
        if not f.name.endswith(".json"):
            continue
        try:
            p = _read_one(f)
        except (json.JSONDecodeError, KeyError):
            continue
        if status is not None and p.status != status:
            continue
        if kind is not None and p.kind != kind:
            continue
        if project is not None and p.project != project:
            continue
        items.append(p)
    return items


def update_status(
    *,
    vault_root: Path,
    proposal_id: str,
    status: str,
    applied_pr: Optional[int] = None,
) -> Proposal:
    if status not in _VALID_STATUSES:
        raise ValueError(f"unknown status: {status!r} (valid: {sorted(_VALID_STATUSES)})")
    target = _path_for(vault_root, proposal_id)
    if not target.exists():
        raise FileNotFoundError(f"proposal {proposal_id!r} not found at {target}")
    data = json.loads(target.read_text())
    data["status"] = status
    data["decided_at"] = _now_iso()
    if applied_pr is not None:
        data["applied_pr"] = applied_pr
    target.write_text(json.dumps(data, indent=2, sort_keys=True))
    return _read_one(target)


def expire_old(*, vault_root: Path, days: int = 30) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    count = 0
    for p in list_proposals(vault_root=vault_root, status="pending"):
        try:
            created = datetime.strptime(p.created_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            continue
        if created < cutoff:
            update_status(vault_root=vault_root, proposal_id=p.id, status="expired")
            count += 1
    return count
```

Add `from datetime import timedelta` at the top imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_proposals.py -v`
Expected: all PASS (8 tests total)

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/proposals.py tests/autopilot/core/test_proposals.py
git commit -m "feat(autopilot): proposals — list/update_status/expire_old"
```

---

## Task 4: Kill switch

**Files:**
- Create: `src/mnemo/autopilot/core/kill_switch.py`
- Create: `tests/autopilot/core/test_kill_switch.py`

- [ ] **Step 1: Write the failing test**

`tests/autopilot/core/test_kill_switch.py`:

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from mnemo.autopilot.core.kill_switch import (
    get_state,
    is_active,
    set_state,
)


def test_default_state_is_off(tmp_path: Path):
    assert get_state(vault_root=tmp_path) == "off"
    assert is_active(vault_root=tmp_path) is False


def test_set_state_persists(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    assert get_state(vault_root=tmp_path) == "on"
    assert is_active(vault_root=tmp_path) is True

    data = json.loads((tmp_path / ".mnemo" / "autopilot.json").read_text())
    assert data["state"] == "on"
    assert data["schema_version"] == 1
    assert data["last_changed_by"] == "cli"


def test_paused_state_blocks_active_until_expiry(tmp_path: Path):
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    set_state(vault_root=tmp_path, state="paused", paused_until=until,
              source="auto")
    assert get_state(vault_root=tmp_path) == "paused"
    assert is_active(vault_root=tmp_path) is False


def test_paused_state_resumes_after_expiry(tmp_path: Path):
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    set_state(vault_root=tmp_path, state="paused", paused_until=past)
    # state still reads paused, but is_active treats expiry as on-equivalent
    assert get_state(vault_root=tmp_path) == "paused"
    assert is_active(vault_root=tmp_path) is True


def test_set_state_rejects_unknown(tmp_path: Path):
    with pytest.raises(ValueError, match="unknown state"):
        set_state(vault_root=tmp_path, state="bogus")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_kill_switch.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement kill_switch.py**

`src/mnemo/autopilot/core/kill_switch.py`:

```python
"""Authoritative on/off/paused state for autopilot."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from mnemo.autopilot.core._dirs import (
    autopilot_state_path,
    ensure_autopilot_dir,
)

SCHEMA_VERSION = 1
State = Literal["on", "off", "paused"]
_VALID_STATES = {"on", "off", "paused"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(vault_root: Path) -> dict:
    path = autopilot_state_path(vault_root)
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "state": "off",
            "paused_until": None,
            "last_changed_at": None,
            "last_changed_by": None,
        }
    return json.loads(path.read_text())


def get_state(*, vault_root: Path) -> str:
    return _read(vault_root)["state"]


def is_active(*, vault_root: Path) -> bool:
    data = _read(vault_root)
    if data["state"] == "on":
        return True
    if data["state"] == "off":
        return False
    # paused: active iff paused_until expired
    pu = data.get("paused_until")
    if not pu:
        return False
    try:
        until = datetime.strptime(pu, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return datetime.now(timezone.utc) > until


def set_state(
    *,
    vault_root: Path,
    state: str,
    paused_until: Optional[str] = None,
    source: str = "cli",
) -> None:
    if state not in _VALID_STATES:
        raise ValueError(f"unknown state: {state!r} (valid: {sorted(_VALID_STATES)})")
    ensure_autopilot_dir(vault_root)
    data = {
        "schema_version": SCHEMA_VERSION,
        "state": state,
        "paused_until": paused_until,
        "last_changed_at": _now_iso(),
        "last_changed_by": source,
    }
    autopilot_state_path(vault_root).write_text(
        json.dumps(data, indent=2, sort_keys=True)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_kill_switch.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/kill_switch.py tests/autopilot/core/test_kill_switch.py
git commit -m "feat(autopilot): kill switch (on/off/paused with auto-resume)"
```

---

## Task 5: Frozen recall set

**Files:**
- Create: `src/mnemo/autopilot/core/frozen_recall.py`
- Create: `tests/autopilot/core/test_frozen_recall.py`

- [ ] **Step 1: Write the failing test**

`tests/autopilot/core/test_frozen_recall.py`:

```python
import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.frozen_recall import (
    FrozenSetMissing,
    freeze_current,
    load_frozen,
    frozen_path,
)


def _write_recall(vault_root: Path, payload: dict) -> Path:
    d = vault_root / ".mnemo"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "recall-cases.json"
    p.write_text(json.dumps(payload))
    return p


def test_freeze_copies_current(tmp_path: Path):
    _write_recall(tmp_path, {"cases": [{"id": "a"}], "v": 1})
    out = freeze_current(vault_root=tmp_path)
    assert out == frozen_path(vault_root=tmp_path)
    assert json.loads(out.read_text()) == {"cases": [{"id": "a"}], "v": 1}


def test_freeze_is_idempotent_unless_force(tmp_path: Path):
    _write_recall(tmp_path, {"v": 1})
    freeze_current(vault_root=tmp_path)
    # mutate source
    _write_recall(tmp_path, {"v": 2})
    freeze_current(vault_root=tmp_path)
    assert json.loads(load_frozen(vault_root=tmp_path).read()) == {"v": 1}

    freeze_current(vault_root=tmp_path, force=True)
    assert json.loads(load_frozen(vault_root=tmp_path).read()) == {"v": 2}


def test_load_frozen_raises_when_missing(tmp_path: Path):
    with pytest.raises(FrozenSetMissing):
        load_frozen(vault_root=tmp_path)


def test_freeze_raises_when_recall_missing(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        freeze_current(vault_root=tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_frozen_recall.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement frozen_recall.py**

`src/mnemo/autopilot/core/frozen_recall.py`:

```python
"""Snapshot of recall-cases.json so Tier 2 tuners cannot optimize against drift."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import IO

from mnemo.autopilot.core._dirs import (
    autopilot_dir,
    ensure_autopilot_dir,
    frozen_recall_path,
)


class FrozenSetMissing(RuntimeError):
    """Raised when callers expect a frozen recall set but none has been created."""


def _source_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "recall-cases.json"


def frozen_path(*, vault_root: Path) -> Path:
    return frozen_recall_path(vault_root)


def freeze_current(*, vault_root: Path, force: bool = False) -> Path:
    src = _source_path(vault_root)
    if not src.exists():
        raise FileNotFoundError(f"no recall-cases.json at {src}")
    ensure_autopilot_dir(vault_root)
    dest = frozen_path(vault_root=vault_root)
    if dest.exists() and not force:
        return dest
    shutil.copyfile(src, dest)
    return dest


def load_frozen(*, vault_root: Path) -> IO[str]:
    p = frozen_path(vault_root=vault_root)
    if not p.exists():
        raise FrozenSetMissing(f"no frozen recall set at {p}")
    return p.open("r", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_frozen_recall.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/frozen_recall.py tests/autopilot/core/test_frozen_recall.py
git commit -m "feat(autopilot): frozen recall set freezer + loader"
```

---

## Task 6: Label constants + ensure_label_exists

**Files:**
- Create: `src/mnemo/autopilot/core/labels.py`
- Create: `tests/autopilot/core/test_labels.py`

- [ ] **Step 1: Write the failing test**

`tests/autopilot/core/test_labels.py`:

```python
import subprocess
from unittest.mock import patch

from mnemo.autopilot.core.labels import (
    SELF_FIX_LABEL,
    SELF_FIX_LABEL_COLOR,
    SELF_FIX_LABEL_DESC,
    ensure_label_exists,
)


def test_constants_are_stable():
    assert SELF_FIX_LABEL == "mnemo:self-fix"
    assert SELF_FIX_LABEL_COLOR == "0E8A16"
    assert "auto" in SELF_FIX_LABEL_DESC.lower()


def test_ensure_label_exists_calls_gh():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        ok = ensure_label_exists()
    assert ok is True
    cmd = run.call_args[0][0]
    assert cmd[:3] == ["gh", "label", "create"]
    assert SELF_FIX_LABEL in cmd
    assert "--force" in cmd


def test_ensure_label_swallows_failure():
    with patch("subprocess.run", side_effect=FileNotFoundError("gh missing")):
        ok = ensure_label_exists()
    assert ok is False


def test_ensure_label_swallows_nonzero():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "oops"
        ok = ensure_label_exists()
    assert ok is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_labels.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement labels.py**

`src/mnemo/autopilot/core/labels.py`:

```python
"""GitHub label constants for autopilot-opened PRs."""
from __future__ import annotations

import subprocess

SELF_FIX_LABEL = "mnemo:self-fix"
SELF_FIX_LABEL_COLOR = "0E8A16"
SELF_FIX_LABEL_DESC = "Auto-opened PR by mnemo autopilot"


def ensure_label_exists() -> bool:
    """Idempotent ``gh label create --force``. Returns False when ``gh`` is
    unavailable or the call fails — autopilot still works in record-only mode."""
    try:
        result = subprocess.run(
            [
                "gh", "label", "create", SELF_FIX_LABEL,
                "--color", SELF_FIX_LABEL_COLOR,
                "--description", SELF_FIX_LABEL_DESC,
                "--force",
            ],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_labels.py -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/labels.py tests/autopilot/core/test_labels.py
git commit -m "feat(autopilot): mnemo:self-fix label constants + idempotent ensure"
```

---

## Task 7: PR budget — tracking + caps

**Files:**
- Create: `src/mnemo/autopilot/core/pr_budget.py`
- Create: `tests/autopilot/core/test_pr_budget.py`

- [ ] **Step 1: Write the failing test**

`tests/autopilot/core/test_pr_budget.py`:

```python
import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.pr_budget import (
    can_open,
    record_opened,
    record_outcome,
)
from mnemo.autopilot.core.kill_switch import get_state, set_state


def test_default_budget_allows_first_pr(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    ok, reason = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is True
    assert reason == ""


def test_budget_blocks_when_kill_switch_off(tmp_path: Path):
    # default state off
    ok, reason = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is False
    assert "kill switch" in reason.lower() or "off" in reason.lower()


def test_budget_blocks_after_daily_cap(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    ok, reason = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is False
    assert "daily" in reason.lower() or "cap" in reason.lower()


def test_budget_categories_are_independent(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    ok, _ = can_open(vault_root=tmp_path, category="dead_rule_sweep")
    assert ok is True


def test_two_closed_in_a_row_pauses_autopilot(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    record_outcome(vault_root=tmp_path, pr_number=10, outcome="closed")
    # still on
    assert get_state(vault_root=tmp_path) == "on"
    # second closed across a different day or category triggers the trip
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=11)
    record_outcome(vault_root=tmp_path, pr_number=11, outcome="closed")
    assert get_state(vault_root=tmp_path) == "paused"


def test_merged_outcome_resets_streak(tmp_path: Path):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)
    record_outcome(vault_root=tmp_path, pr_number=10, outcome="closed")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=11)
    record_outcome(vault_root=tmp_path, pr_number=11, outcome="merged")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=12)
    record_outcome(vault_root=tmp_path, pr_number=12, outcome="closed")
    assert get_state(vault_root=tmp_path) == "on"


def test_window_rolls_over_after_utc_day(tmp_path: Path, monkeypatch):
    set_state(vault_root=tmp_path, state="on")
    record_opened(vault_root=tmp_path, category="doctor_self_fix", pr_number=10)

    # roll the window manually to a previous day
    from mnemo.autopilot.core._dirs import autopilot_budget_path
    p = autopilot_budget_path(tmp_path)
    data = json.loads(p.read_text())
    data["window_start"] = "2000-01-01T00:00:00Z"
    p.write_text(json.dumps(data))

    ok, _ = can_open(vault_root=tmp_path, category="doctor_self_fix")
    assert ok is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_pr_budget.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement pr_budget.py**

`src/mnemo/autopilot/core/pr_budget.py`:

```python
"""Per-category daily PR caps + auto-pause on consecutive closed PRs."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from mnemo.autopilot.core._dirs import (
    autopilot_budget_path,
    ensure_autopilot_dir,
)
from mnemo.autopilot.core.kill_switch import is_active, set_state

SCHEMA_VERSION = 1
DAILY_CAP_PER_CATEGORY = 1
PAUSE_HOURS_AFTER_TWO_CLOSED = 24
RECENT_OUTCOMES_LIMIT = 10

Outcome = Literal["merged", "closed", "abandoned"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today_start_iso() -> str:
    n = _now()
    return n.replace(hour=0, minute=0, second=0, microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _read(vault_root: Path) -> dict:
    path = autopilot_budget_path(vault_root)
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "window_start": _today_start_iso(),
            "counts": {},
            "recent_outcomes": [],
        }
    data = json.loads(path.read_text())
    # roll over if window aged out
    try:
        ws = datetime.strptime(data["window_start"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, KeyError):
        ws = _now() - timedelta(days=2)
    if (_now() - ws).total_seconds() >= 24 * 3600:
        data["window_start"] = _today_start_iso()
        data["counts"] = {}
    return data


def _write(vault_root: Path, data: dict) -> None:
    ensure_autopilot_dir(vault_root)
    autopilot_budget_path(vault_root).write_text(
        json.dumps(data, indent=2, sort_keys=True)
    )


def can_open(*, vault_root: Path, category: str) -> tuple[bool, str]:
    if not is_active(vault_root=vault_root):
        return False, "autopilot kill switch is off or paused"
    data = _read(vault_root)
    used = data["counts"].get(category, 0)
    if used >= DAILY_CAP_PER_CATEGORY:
        return False, f"daily cap reached for {category} ({used}/{DAILY_CAP_PER_CATEGORY})"
    return True, ""


def record_opened(*, vault_root: Path, category: str, pr_number: int) -> None:
    data = _read(vault_root)
    data["counts"][category] = data["counts"].get(category, 0) + 1
    _write(vault_root, data)


def record_outcome(
    *, vault_root: Path, pr_number: int, outcome: str
) -> None:
    data = _read(vault_root)
    data["recent_outcomes"].append({
        "pr": pr_number,
        "outcome": outcome,
        "ts": _now_iso(),
    })
    data["recent_outcomes"] = data["recent_outcomes"][-RECENT_OUTCOMES_LIMIT:]
    _write(vault_root, data)

    # auto-pause: if last 2 outcomes are both 'closed', pause
    last_two = data["recent_outcomes"][-2:]
    if len(last_two) == 2 and all(o["outcome"] == "closed" for o in last_two):
        until = (_now() + timedelta(hours=PAUSE_HOURS_AFTER_TWO_CLOSED)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        set_state(
            vault_root=vault_root,
            state="paused",
            paused_until=until,
            source="auto",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_pr_budget.py -v`
Expected: 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/pr_budget.py tests/autopilot/core/test_pr_budget.py
git commit -m "feat(autopilot): PR budget with daily caps + auto-pause on 2 closed"
```

---

## Task 8: Dispatcher (record-only mode)

**Files:**
- Create: `src/mnemo/autopilot/core/dispatcher.py`
- Create: `tests/autopilot/core/test_dispatcher.py`

The dispatcher is record-only in this PR. Real CronCreate integration is left to a Tier when/if needed; this keeps core testable without harness assumptions.

- [ ] **Step 1: Write the failing test**

`tests/autopilot/core/test_dispatcher.py`:

```python
import json
from pathlib import Path

import pytest

from mnemo.autopilot.core.dispatcher import (
    schedule_autopilot_job,
    list_autopilot_jobs,
    cancel_autopilot_job,
)


def test_schedule_records_job(tmp_path: Path):
    h = schedule_autopilot_job(
        vault_root=tmp_path,
        name="autopilot.tier0.digest",
        cron="0 9 * * 1",
        command="mnemo autopilot digest",
    )
    assert h.name == "autopilot.tier0.digest"
    assert h.cron == "0 9 * * 1"

    data = json.loads((tmp_path / ".mnemo" / "autopilot-jobs.json").read_text())
    assert "autopilot.tier0.digest" in data["jobs"]


def test_schedule_namespaces_must_start_with_autopilot(tmp_path: Path):
    with pytest.raises(ValueError, match="autopilot\\."):
        schedule_autopilot_job(
            vault_root=tmp_path, name="random.job",
            cron="* * * * *", command="x",
        )


def test_list_jobs_empty(tmp_path: Path):
    assert list_autopilot_jobs(vault_root=tmp_path) == []


def test_list_jobs_returns_registered(tmp_path: Path):
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier1.selffix",
        cron="0 8 * * *", command="mnemo autopilot self-fix",
    )
    jobs = list_autopilot_jobs(vault_root=tmp_path)
    assert len(jobs) == 1
    assert jobs[0].name == "autopilot.tier1.selffix"


def test_cancel_removes_job(tmp_path: Path):
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier1.selffix",
        cron="0 8 * * *", command="x",
    )
    assert cancel_autopilot_job(vault_root=tmp_path, name="autopilot.tier1.selffix") is True
    assert list_autopilot_jobs(vault_root=tmp_path) == []
    assert cancel_autopilot_job(vault_root=tmp_path, name="autopilot.tier1.selffix") is False


def test_schedule_is_idempotent_on_same_name(tmp_path: Path):
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier0.digest",
        cron="0 9 * * 1", command="cmd-v1",
    )
    schedule_autopilot_job(
        vault_root=tmp_path, name="autopilot.tier0.digest",
        cron="0 10 * * 1", command="cmd-v2",
    )
    jobs = list_autopilot_jobs(vault_root=tmp_path)
    assert len(jobs) == 1
    assert jobs[0].cron == "0 10 * * 1"
    assert jobs[0].command == "cmd-v2"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_dispatcher.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement dispatcher.py**

`src/mnemo/autopilot/core/dispatcher.py`:

```python
"""Record-only autopilot job dispatcher.

Real CronCreate integration is intentionally deferred to whichever Tier
first needs it. For now we record intent in ``.mnemo/autopilot-jobs.json``
so tests + ``mnemo autopilot status`` can show pending jobs without
depending on the harness scheduler.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

from mnemo.autopilot.core._dirs import (
    autopilot_jobs_path,
    ensure_autopilot_dir,
)

SCHEMA_VERSION = 1
NAMESPACE_PREFIX = "autopilot."


@dataclass
class JobInfo:
    name: str
    cron: str
    command: str
    created_at: str


JobHandle = JobInfo


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read(vault_root: Path) -> dict:
    path = autopilot_jobs_path(vault_root)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "jobs": {}}
    return json.loads(path.read_text())


def _write(vault_root: Path, data: dict) -> None:
    ensure_autopilot_dir(vault_root)
    autopilot_jobs_path(vault_root).write_text(
        json.dumps(data, indent=2, sort_keys=True)
    )


def schedule_autopilot_job(
    *,
    vault_root: Path,
    name: str,
    cron: str,
    command: str,
) -> JobHandle:
    if not name.startswith(NAMESPACE_PREFIX):
        raise ValueError(f"job name must start with {NAMESPACE_PREFIX!r}: {name!r}")
    data = _read(vault_root)
    info = JobInfo(name=name, cron=cron, command=command, created_at=_now_iso())
    data["jobs"][name] = asdict(info)
    _write(vault_root, data)
    return info


def list_autopilot_jobs(*, vault_root: Path) -> list[JobInfo]:
    data = _read(vault_root)
    return [
        JobInfo(**v) for v in sorted(
            data.get("jobs", {}).values(), key=lambda j: j["name"]
        )
    ]


def cancel_autopilot_job(*, vault_root: Path, name: str) -> bool:
    data = _read(vault_root)
    if name not in data.get("jobs", {}):
        return False
    del data["jobs"][name]
    _write(vault_root, data)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/core/test_dispatcher.py -v`
Expected: 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/mnemo/autopilot/core/dispatcher.py tests/autopilot/core/test_dispatcher.py
git commit -m "feat(autopilot): record-only job dispatcher"
```

---

## Task 9: `mnemo autopilot` CLI command

**Files:**
- Create: `src/mnemo/cli/commands/autopilot.py`
- Modify: `src/mnemo/cli/parser.py`
- Modify: `src/mnemo/cli/commands/__init__.py`
- Create: `tests/autopilot/cli/__init__.py` (empty)
- Create: `tests/autopilot/cli/test_autopilot_cli.py`

- [ ] **Step 1: Write the failing test**

`tests/autopilot/cli/test_autopilot_cli.py`:

```python
import json
from pathlib import Path

import pytest

from mnemo.cli.runtime import main


def _run(monkeypatch, tmp_path: Path, *args: str, capsys) -> tuple[int, str]:
    # mnemo resolves vault from cfg; point everything at tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "mnemo.cli._resolve_vault", lambda: tmp_path, raising=False
    )
    rc = main(["mnemo", *args])
    out, _err = capsys.readouterr()
    return rc, out


def test_autopilot_status_default_off(monkeypatch, tmp_path, capsys):
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert rc == 0
    assert "off" in out.lower()


def test_autopilot_on_then_status_shows_on(monkeypatch, tmp_path, capsys):
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert rc == 0
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert "on" in out.lower()
    # frozen recall is bootstrapped iff recall-cases.json exists; absence is fine here


def test_autopilot_off(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "off", capsys=capsys)
    assert rc == 0
    rc, out = _run(monkeypatch, tmp_path, "autopilot", "status", capsys=capsys)
    assert "off" in out.lower()


def test_autopilot_pause_with_hours(monkeypatch, tmp_path, capsys):
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    rc, _ = _run(monkeypatch, tmp_path, "autopilot", "pause", "--hours", "2",
                 capsys=capsys)
    assert rc == 0
    state = json.loads((tmp_path / ".mnemo" / "autopilot.json").read_text())
    assert state["state"] == "paused"
    assert state["paused_until"] is not None


def test_autopilot_freezes_recall_on_on_when_present(
    monkeypatch, tmp_path, capsys
):
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / "recall-cases.json").write_text('{"v":1}')
    _run(monkeypatch, tmp_path, "autopilot", "on", capsys=capsys)
    assert (tmp_path / ".mnemo" / "recall-cases.frozen.json").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/cli/test_autopilot_cli.py -v`
Expected: FAIL — `autopilot` is not a registered command

- [ ] **Step 3: Add subparser block to `src/mnemo/cli/parser.py`**

Find the existing `sub.add_parser(...)` chain and add (after `sub.add_parser("doctor", ...)`):

```python
    autopilot = sub.add_parser("autopilot", help="autonomous monitoring + self-fix")
    autosub = autopilot.add_subparsers(dest="autopilot_action")
    autosub.required = True
    autosub.add_parser("on", help="enable autopilot")
    autosub.add_parser("off", help="disable autopilot + revoke jobs")
    pause_p = autosub.add_parser("pause", help="temporarily stop autopilot")
    pause_p.add_argument("--hours", type=int, default=24,
                         help="pause duration in hours (default 24)")
    autosub.add_parser("status", help="show autopilot state + jobs + budget")
```

- [ ] **Step 4: Implement the command handler**

`src/mnemo/cli/commands/autopilot.py`:

```python
"""``mnemo autopilot {on,off,pause,status}`` — autopilot kill switch + status."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mnemo.cli.parser import command


def _vault() -> Path:
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    return cli._resolve_vault()


@command("autopilot")
def cmd_autopilot(args: argparse.Namespace) -> int:
    action = getattr(args, "autopilot_action", None)
    handler = {
        "on": _do_on,
        "off": _do_off,
        "pause": _do_pause,
        "status": _do_status,
    }.get(action)
    if handler is None:
        print("usage: mnemo autopilot {on,off,pause,status}")
        return 2
    return handler(args)


def _do_on(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state
    from mnemo.autopilot.core.frozen_recall import freeze_current
    from mnemo.autopilot.core.labels import ensure_label_exists

    vault = _vault()
    set_state(vault_root=vault, state="on", source="cli")
    # bootstrap frozen recall if recall-cases.json exists; ignore otherwise
    try:
        freeze_current(vault_root=vault)
    except FileNotFoundError:
        pass
    ensure_label_exists()
    print("autopilot: on")
    return 0


def _do_off(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state
    from mnemo.autopilot.core.dispatcher import (
        list_autopilot_jobs,
        cancel_autopilot_job,
    )

    vault = _vault()
    for job in list_autopilot_jobs(vault_root=vault):
        cancel_autopilot_job(vault_root=vault, name=job.name)
    set_state(vault_root=vault, state="off", source="cli")
    print("autopilot: off")
    return 0


def _do_pause(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import set_state

    vault = _vault()
    hours = max(1, int(getattr(args, "hours", 24) or 24))
    until = (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    set_state(vault_root=vault, state="paused", paused_until=until, source="cli")
    print(f"autopilot: paused for {hours}h (until {until})")
    return 0


def _do_status(args: argparse.Namespace) -> int:
    from mnemo.autopilot.core.kill_switch import get_state, is_active
    from mnemo.autopilot.core.dispatcher import list_autopilot_jobs
    from mnemo.autopilot.core._dirs import autopilot_budget_path
    import json

    vault = _vault()
    state = get_state(vault_root=vault)
    active = is_active(vault_root=vault)
    print(f"State: {state} ({'active' if active else 'inactive'})")

    bp = autopilot_budget_path(vault)
    if bp.exists():
        data = json.loads(bp.read_text())
        print(f"Budget window start: {data.get('window_start')}")
        counts = data.get("counts", {})
        if counts:
            print("Counts today:")
            for k, v in sorted(counts.items()):
                print(f"  {k}: {v}")
        else:
            print("Counts today: (none)")
        recent = data.get("recent_outcomes", [])
        if recent:
            print("Recent outcomes:")
            for o in recent[-5:]:
                print(f"  PR #{o['pr']}: {o['outcome']} @ {o['ts']}")
    else:
        print("Budget: (no activity yet)")

    jobs = list_autopilot_jobs(vault_root=vault)
    if jobs:
        print("Scheduled jobs:")
        for j in jobs:
            print(f"  {j.name}  cron={j.cron}  cmd={j.command}")
    else:
        print("Scheduled jobs: (none)")
    return 0
```

- [ ] **Step 5: Register the command in `src/mnemo/cli/commands/__init__.py`**

Add `autopilot` to the import tuple:

```python
from mnemo.cli.commands import (  # noqa: F401  — trigger @command registration
    autopilot,
    briefing,
    dedup_rules,
    disable_rule,
    doctor,
    extract,
    init,
    list_enforced,
    migrate_worktree_briefings,
    misc,
    recall,
    regen_graph_edges,
    statusline,
    status,
    telemetry,
)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/cli/test_autopilot_cli.py -v`
Expected: 5 PASS

- [ ] **Step 7: Run the full new suite to verify no regressions**

Run: `PYTHONPATH=$(pwd)/src pytest tests/autopilot/ -v`
Expected: all PASS (~35 tests across the 7 test files)

- [ ] **Step 8: Commit**

```bash
git add src/mnemo/cli/commands/autopilot.py src/mnemo/cli/parser.py src/mnemo/cli/commands/__init__.py tests/autopilot/cli/
git commit -m "feat(autopilot): mnemo autopilot {on,off,pause,status} CLI"
```

---

## Task 10: Full repo regression run

**Files:** none (verification only)

- [ ] **Step 1: Run the entire test suite**

Run: `PYTHONPATH=$(pwd)/src pytest -q`
Expected: previous green count + ~35 new tests = ~1180 passing, 0 failing.

- [ ] **Step 2: Run mnemo CLI smoke**

Run:

```bash
PYTHONPATH=$(pwd)/src python -m mnemo autopilot status
PYTHONPATH=$(pwd)/src python -m mnemo autopilot on
PYTHONPATH=$(pwd)/src python -m mnemo autopilot status
PYTHONPATH=$(pwd)/src python -m mnemo autopilot pause --hours 1
PYTHONPATH=$(pwd)/src python -m mnemo autopilot off
```

Expected: each prints the relevant state line; no tracebacks.

- [ ] **Step 3: Doctor sanity check**

Run: `PYTHONPATH=$(pwd)/src python -m mnemo doctor 2>&1 | tail -20`
Expected: no NEW warnings caused by autopilot (preexisting warnings about frontmatter / source paths are unchanged).

- [ ] **Step 4: Commit any incidental fixes (if any)**

If the smoke surfaced a regression, fix it in a follow-up commit:

```bash
git add -p
git commit -m "fix(autopilot): <specific fix>"
```

If clean, no commit needed.

---

## Self-Review (already done inline)

**Spec coverage:**
- Proposals store ✅ Tasks 2–3
- Dispatcher (record-only) ✅ Task 8
- Kill switch ✅ Task 4
- Frozen recall set ✅ Task 5
- PR budget + auto-pause ✅ Task 7
- Label constants ✅ Task 6
- CLI `mnemo autopilot {on,off,pause,status}` ✅ Task 9
- Empty Tier sub-package placeholders ✅ Task 1
- No changes to existing artefacts beyond parser/registry stubs ✅ Tasks 1, 9

**Placeholder scan:** none (all code blocks complete, no TBD/TODO).

**Type consistency:** `Proposal` dataclass shape matches schema; `JobInfo`/`JobHandle` aliased; `is_active` / `set_state` signatures consistent across kill_switch/pr_budget/CLI.

**Open spec items handled inline:**
- CronCreate availability → record-only mode in dispatcher ✅
- Proposal expiry → 30-day default, configurable later (constant in code) — defer "configurable via autopilot.json" to Tier-time when first consumer cares
- `mnemo autopilot status` strictly autopilot state ✅
