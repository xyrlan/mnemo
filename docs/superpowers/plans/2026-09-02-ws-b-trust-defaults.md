# WS-B — Trust Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** mnemo makes no network call and spends no LLM call without the user opting in, and every rule it learns is announced with a one-line veto.

**Architecture:** One predicate, `mnemo.autopilot.core.network.enabled()`, reads `autopilot.network.enabled` (default `false`) and gates every `gh` call site (digest issue, self-fix PRs, telemetry issue, outcome poller, label creation) plus the `_gh` helpers themselves. `backfill.autoOnFirstSession` flips to `false` and the first session injects a one-line invitation instead. Extraction appends fresh auto-promoted pages to `.mnemo/learned.jsonl`; `session_start` injects up to five of them per project with a veto command and advances a per-project marker. `disable-rule` becomes a public command.

**Tech Stack:** Python 3.8+ stdlib only, pytest. Spec: `docs/superpowers/specs/2026-09-01-corrections-layer-design.md` §B.

**Conventions:** branch `feat/ws-b-trust-defaults` off `master` (after PR #109 merges). Commit trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01UrfBRCJ4yj7YNLAR66rhk5`. Tests: `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider <path>`; full suite before the PR. Hooks must never raise into Claude Code: every new hook code path is wrapped and logged via `errors.log_error`. Never run the real hooks against the real vault in tests — every test builds a tmp vault and patches `paths.vault_root` / `config.load_config` the way `tests/unit/test_session_start_briefing_injection.py` does.

---

## File map

| File | Change |
|---|---|
| `src/mnemo/core/config.py` | `DEFAULTS["autopilot"] = {"network": {"enabled": False}}`; `DEFAULTS["backfill"]["autoOnFirstSession"] = False` |
| `src/mnemo/autopilot/core/network.py` | **new** — `enabled(cfg=None) -> bool`, `OFF_MESSAGE` |
| `src/mnemo/autopilot/insights/digest.py` | gate `create_issue` |
| `src/mnemo/autopilot/selffix/doctor_fixer.py`, `dead_rule_sweep.py` | gate after the `repo_root is None` branch: cures stay applied in place, no worktree/PR |
| `src/mnemo/autopilot/selffix/telemetry_doctor.py` | gate before `open_issue` |
| `src/mnemo/autopilot/selffix/outcome_poller.py` | `poll_outcomes` returns 0 when off |
| `src/mnemo/autopilot/core/labels.py` | `ensure_label_exists` returns False when off |
| `src/mnemo/autopilot/selffix/_gh.py` | `push_branch`, `open_pr`, `open_issue` refuse when off (belt and braces) |
| `src/mnemo/cli/commands/autopilot.py` | `status` prints `Network: off — autopilot.network.enabled=false (no gh calls)` / `on` |
| `src/mnemo/hooks/session_start.py` | `_first_run_notice(...)`, `_learned_block(...)`, appended to the envelope |
| `src/mnemo/core/backfill/ledger.py` | `firstRunNoticeShown` flag helpers |
| `src/mnemo/core/learned.py` | **new** — `record`, `pending`, `mark_announced` |
| `src/mnemo/core/extract/__init__.py` | record learned pages after each `apply_pages` |
| `src/mnemo/cli/parser.py` | remove `disable-rule` from `ADVANCED_COMMANDS` |
| `docs/configuration.md`, `CHANGELOG.md` | document |

---

### Task 1: `autopilot.network.enabled` predicate and config defaults

**Files:**
- Create: `src/mnemo/autopilot/core/network.py`
- Modify: `src/mnemo/core/config.py`
- Test: `tests/unit/test_autopilot_network_flag.py`

- [ ] **Step 1: Failing test**

```python
from mnemo.autopilot.core import network
from mnemo.core.config import DEFAULTS


def test_default_is_off():
    assert DEFAULTS["autopilot"]["network"]["enabled"] is False
    assert DEFAULTS["backfill"]["autoOnFirstSession"] is False


def test_enabled_reads_cfg():
    assert network.enabled({"autopilot": {"network": {"enabled": True}}}) is True
    assert network.enabled({"autopilot": {"network": {"enabled": False}}}) is False
    assert network.enabled({}) is False


def test_enabled_loads_config_when_none(monkeypatch):
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {"autopilot": {"network": {"enabled": True}}})
    assert network.enabled() is True
```

- [ ] **Step 2: Run, expect `ModuleNotFoundError` / `KeyError: 'autopilot'`.**

- [ ] **Step 3: Implement**

`src/mnemo/core/config.py` — add to `DEFAULTS` (after `"reflex"`):
```python
    "autopilot": {
        # Nothing mnemo does reaches the network unless this is on: no
        # `gh issue create`, no self-fix PRs, no outcome polling. Local
        # maintenance (indexes, sweep, calibration) is unaffected.
        "network": {"enabled": False},
    },
```
and change `"autoOnFirstSession": True` → `False` in `"backfill"`.

`src/mnemo/autopilot/core/network.py`:
```python
"""The one switch for anything that leaves the machine."""
from __future__ import annotations

from typing import Any, Optional

OFF_MESSAGE = "[autopilot] network off (autopilot.network.enabled=false) — skipped"


def enabled(cfg: Optional[dict[str, Any]] = None) -> bool:
    if cfg is None:
        from mnemo.core import config as config_mod
        cfg = config_mod.load_config()
    return bool(((cfg.get("autopilot") or {}).get("network") or {}).get("enabled", False))
```

- [ ] **Step 4: Run test → PASS. Also run `tests/unit -k "config or backfill"`; a test asserting `autoOnFirstSession` defaults True must be updated to False (that flip is the point).**
- [ ] **Step 5: Commit** `feat(autopilot): autopilot.network.enabled switch, off by default; backfill first-run is opt-in`

---

### Task 2: Gate every `gh` call site

**Files:**
- Modify: `digest.py::create_issue`, `telemetry_doctor.py` (before `_gh.open_issue`), `doctor_fixer.py` and `dead_rule_sweep.py` (right after the `if repo_root is None:` block), `outcome_poller.py::poll_outcomes` (top), `labels.py::ensure_label_exists` (top), `_gh.py::push_branch/open_pr/open_issue` (top)
- Test: `tests/unit/test_autopilot_network_gate.py`

- [ ] **Step 1: Failing tests** — for each site, with default config (network off), spy on `subprocess.run` / `subprocess.Popen` and assert **zero** calls whose argv starts with `"gh"`, and assert the function's documented off-value (`create_issue → None`, `poll_outcomes → 0`, `ensure_label_exists → False`, `open_pr → None`, `open_issue → None`, `push_branch → False`). For doctor_fixer / dead_rule_sweep use their existing test fixtures (see `tests/autopilot/selffix/`) with a git repo vault and assert: cures/archives are applied on disk, no worktree is created (`_gh.create_worktree` spied, not called), return is `None`, and the printed line contains `network off`. Then one test per site with `{"autopilot": {"network": {"enabled": True}}}` asserting the old path runs (spy sees a `gh` argv) — this is the mutation guard proving the gate is load-bearing.

- [ ] **Step 2: Run → FAIL (gh argv observed).**

- [ ] **Step 3: Implement** — at each site:
```python
from mnemo.autopilot.core import network
...
if not network.enabled():
    print(network.OFF_MESSAGE)
    return <off-value>
```
In `doctor_fixer.py` / `dead_rule_sweep.py` place it after the `repo_root is None` block so the "applied in place, no PR" behaviour is identical: message `f"[autopilot] {n} fix(es) applied in place; network off, no PR opened"`. In `_gh.py` the three network functions check `network.enabled()` first and return their failure value silently (they are already called only after the caller's gate; this is the backstop).

- [ ] **Step 4: Run gate tests + `tests/autopilot` + `tests/unit -k autopilot` → PASS.**
- [ ] **Step 5: Commit** `feat(autopilot): no gh calls unless autopilot.network.enabled`

---

### Task 3: `mnemo autopilot status` shows the network switch

**Files:** `src/mnemo/cli/commands/autopilot.py::_do_status`; test in `tests/autopilot/cli/` (extend the existing status test there).

- [ ] Test: with default config the output contains `Network: off (autopilot.network.enabled=false — no gh calls)`; with the flag on, `Network: on`.
- [ ] Implement: one `print` after the `State:` line using `network.enabled(cfg)` (load config once in `_do_status`).
- [ ] Commit `feat(cli): autopilot status reports the network switch`

---

### Task 4: First-run notice replaces the automatic backfill

**Files:**
- Modify: `src/mnemo/core/backfill/ledger.py` — `notice_shown(led) -> bool`, `mark_notice_shown(vault_root)`
- Modify: `src/mnemo/hooks/session_start.py` — `_first_run_notice(vault, cfg, cwd, project) -> str`; call it in `main()` right after `_build_injection_payload` and append to `payload_text` (also emit when `payload_text` was empty — the notice alone is a valid envelope)
- Test: `tests/unit/test_session_start_first_run_notice.py`

- [ ] **Step 1: Failing tests**
  - Vault with no ledger, `backfill.enabled=True`, `autoOnFirstSession=False`, `find_transcripts` patched to return 3 transcripts for the project → envelope contains exactly `[mnemo] first run: 3 past session(s) for this repo can be learned with \`mnemo backfill\` (opt-in, about 3 Haiku calls).` and the ledger now has `firstRunNoticeShown: true`.
  - Second session → no notice.
  - `installRunDone: true` → no notice. `backfill.enabled=False` → no notice. Zero transcripts → no notice (nothing to invite).
  - `_spawn_detached_backfill` is NOT called with default config (the autouse conftest fixture already stubs it — assert via a spy that the stub was not invoked).
  - Any exception inside the notice code is logged under `session_start.first_run_notice` and the envelope is still emitted.

- [ ] **Step 2: Run → FAIL.**

- [ ] **Step 3: Implement**

`ledger.py`:
```python
def notice_shown(led: dict) -> bool:
    return bool(led.get("firstRunNoticeShown", False))


def mark_notice_shown(vault_root: Path) -> None:
    led = load(vault_root)
    led["firstRunNoticeShown"] = True
    save(vault_root, led)
```

`session_start.py`:
```python
def _first_run_notice(vault_root: Path, cfg: dict, project: str) -> str:
    """One line, once per vault, instead of spending LLM calls unasked."""
    try:
        from mnemo.core.backfill import discover, ledger as _ledger

        backfill_cfg = cfg.get("backfill") or {}
        if not backfill_cfg.get("enabled", True):
            return ""
        led = _ledger.load(vault_root)
        if led.get("installRunDone") or _ledger.notice_shown(led):
            return ""
        n = len(discover.find_transcripts(project=project))
        if n == 0:
            return ""
        _ledger.mark_notice_shown(vault_root)
        return (
            f"[mnemo] first run: {n} past session(s) for this repo can be learned "
            f"with `mnemo backfill` (opt-in, about {n} Haiku calls)."
        )
    except Exception as exc:
        try:
            from mnemo.core import errors as _e
            _e.log_error(vault_root, "session_start.first_run_notice", exc)
        except Exception:
            pass
        return ""
```
In `main()` (injection block): after `payload_text = _build_injection_payload(...)`, do `notice = _first_run_notice(vault, cfg, canonical_name)`; `if notice: payload_text = (payload_text + "\n\n" + notice) if payload_text else notice`. Keep `_maybe_schedule_install_backfill` as is — with the new default it returns early on `autoOnFirstSession=False`.

- [ ] **Step 4: Run new tests + `tests/unit -k "session_start or backfill"` → PASS.** Existing tests that assumed auto backfill spawns on first session must now set `autoOnFirstSession: True` explicitly in their config — update only that.
- [ ] **Step 5: Commit** `feat(hooks): first-run notice instead of an unasked backfill`

---

### Task 5: Learned ledger

**Files:**
- Create: `src/mnemo/core/learned.py`
- Modify: `src/mnemo/core/extract/__init__.py` (after each `inbox.apply_pages(...)` in `_run_extraction_body`, and after `promote.promote_projects` for project pages)
- Test: `tests/unit/test_learned_ledger.py`

- [ ] **Step 1: Failing tests**
```python
from pathlib import Path
from mnemo.core import learned


def test_record_and_pending_roundtrip(tmp_path):
    learned.record(tmp_path, run_id="r1", entries=[
        {"slug": "use-yarn", "type": "feedback", "name": "Use yarn", "projects": ["proj"],
         "confidence": "verified", "quote": "use yarn not npm"},
        {"slug": "other", "type": "reference", "name": "Other", "projects": ["zzz"], "confidence": "inferred", "quote": None},
    ])
    assert [e["slug"] for e in learned.pending(tmp_path, "proj")] == ["use-yarn"]
    learned.mark_announced(tmp_path, "proj")
    assert learned.pending(tmp_path, "proj") == []
    learned.record(tmp_path, run_id="r2", entries=[{"slug": "n2", "type": "feedback", "name": "N2", "projects": ["proj"], "confidence": "inferred", "quote": None}])
    assert [e["slug"] for e in learned.pending(tmp_path, "proj")] == ["n2"]


def test_universal_entries_pend_for_every_project(tmp_path):
    learned.record(tmp_path, run_id="r1", entries=[{"slug": "u", "type": "feedback", "name": "U", "projects": ["a", "b"], "confidence": "verified", "quote": "q"}])
    assert learned.pending(tmp_path, "zzz") and learned.pending(tmp_path, "zzz")[0]["slug"] == "u"


def test_corrupt_lines_are_skipped(tmp_path):
    (tmp_path / ".mnemo").mkdir()
    (tmp_path / ".mnemo" / "learned.jsonl").write_text("not json\n")
    assert learned.pending(tmp_path, "proj") == []
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `learned.py`: `LEDGER = ".mnemo/learned.jsonl"`, `MARKERS = ".mnemo/announced.json"` (`{project: <iso ts of last announce>}`); `record(vault_root, *, run_id, entries)` appends one JSON line per entry with `ts` (ISO now) and `run_id`; `pending(vault_root, project, *, limit=None)` returns entries with `ts > markers[project]` (or all when no marker) whose `projects` contains `project` OR has ≥ 2 projects (universal); `mark_announced(vault_root, project)` writes now. Use `mnemo.core.atomic.atomic_write_bytes` for the markers file; append to the ledger with `open(..., "a")`. Rotate the ledger at 1 MB (drop the oldest half), same pattern as other logs. Tolerate bad lines.

  In `_run_extraction_body`, after each `apply_result = inbox.apply_pages(...)`: build entries for keys in `apply_result.auto_promoted + apply_result.universal_promoted` from the corresponding pages (`{slug, type, name, projects: projects_for_rule(page.source_files), confidence, quote: page.evidence["quote"] if page.evidence else None}`) and call `learned.record(...)` inside try/except → `errors.log_error(vault_root, "extract.learned", exc)`. Do the same for `project_result.written_fresh` after `promote_projects` (type `project`, confidence `inferred`, no quote).
- [ ] **Step 4: Run + `tests/unit/test_extract_orchestrator.py` → PASS; add one orchestrator assertion that a verified page shows up in `learned.pending(vault, "proj")`.**
- [ ] **Step 5: Commit** `feat(extract): record every freshly promoted rule in .mnemo/learned.jsonl`

---

### Task 6: Announce learned rules with a veto

**Files:**
- Modify: `src/mnemo/hooks/session_start.py` — `_learned_block(vault_root, project) -> str`, appended after the first-run notice
- Modify: `src/mnemo/cli/parser.py` — remove `"disable-rule"` from `ADVANCED_COMMANDS`
- Test: `tests/unit/test_session_start_learned_announce.py`, extend `tests/cli` help test if one enumerates public commands

- [ ] **Step 1: Failing tests**
  - Ledger with 7 pending entries for `proj` → envelope contains `[mnemo learned since your last session]`, exactly 5 bullet lines, the first being `• use-yarn — Use yarn (verified from: "use yarn not npm") · veto: mnemo disable-rule use-yarn`, an inferred entry rendered without the parenthetical, a closing line `(2 more — mnemo status)`, and `[/mnemo learned]`; after the call `learned.pending(vault, "proj")` is empty (marker advanced).
  - No pending → no block. Exception inside → logged `session_start.learned`, envelope still emitted.
  - `mnemo help` lists `disable-rule`.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** — `_learned_block` reads `learned.pending(vault_root, project)`, renders up to 5, then `learned.mark_announced(vault_root, project)`; wrapped in try/except like the notice. Append in `main()` after the notice. Parser: drop the entry from `ADVANCED_COMMANDS`.
- [ ] **Step 4: Run + all session_start tests → PASS.**
- [ ] **Step 5: Commit** `feat(hooks): announce newly learned rules with a one-line veto`

---

### Task 7: Docs, changelog, full suite, PR

- [ ] `docs/configuration.md`: `autopilot.network.enabled` row (default `false`, "Allow the autopilot to call `gh` — digest issues, self-fix PRs, outcome polling. Everything local runs regardless."); `backfill.autoOnFirstSession` default → `false` with the first-run notice explained; a short "What mnemo tells the agent" note listing the `[mnemo learned …]` block and `mnemo disable-rule`.
- [ ] `CHANGELOG.md` `[Unreleased]` → add under **Changed**: network off by default (what changes for users who relied on digest issues / self-fix PRs: set `autopilot.network.enabled: true`), backfill opt-in, learned announcement with veto, `disable-rule` public.
- [ ] Full suite green; `git push -u origin feat/ws-b-trust-defaults`; `gh pr create` with a body listing the three behaviour changes and the one-line config to restore the old behaviour.

## Out of scope
README rewrite (WS-D), `mnemo learn` (WS-C), enforcement changes.
