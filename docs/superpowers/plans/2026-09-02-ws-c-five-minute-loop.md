# WS-C — The Five-Minute Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** correct Claude once, run `mnemo learn`, and see the rule fire on the next prompt — in one session, no waiting.

**Architecture:** `core/learn.py::learn(cfg, *, cwd, session_id=None)` runs the two pipeline stages synchronously on the current session's transcript: briefing (with the verified `## Corrections` section) then extraction (dirty files only — the fresh briefing is what is dirty), which already rebuilds both indexes and records the learned ledger. It reports the ledger delta. The hook-driven path stays asynchronous but its debounce lets the very first extraction of a vault run immediately and counts briefings as new material. Public CLI `mnemo learn` and slash `/mnemo:learn`. Docs gain the five-minute walkthrough.

**Tech Stack:** Python 3.8+ stdlib only, pytest. Spec §C of `docs/superpowers/specs/2026-09-01-corrections-layer-design.md`.

**Conventions:** branch `feat/ws-c-five-minute-loop` off `master` after WS-B merges. Trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01UrfBRCJ4yj7YNLAR66rhk5`. Tests with `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider`; LLM stubbed via `monkeypatch.setattr(llm_mod, "call", ...)`; tmp vaults only; the autouse no-spawn guard in `tests/conftest.py` already prevents real children.

**Facts the implementer needs:** `core/briefing.generate_session_briefing(jsonl_path, agent, cfg) -> Path | None` returns None when the transcript has zero file mutations (`_count_file_mutations`). `core/extract.run_extraction(cfg, *, dry_run=False, force=False, background=False) -> ExtractionSummary` takes the extract lock (`.mnemo/extract.lock`, raises/returns "another extraction is in progress" when held), processes dirty files only, rebuilds the rule-activation and reflex indexes, and calls `learned.record`. `core/learned.pending/recent/_read` expose ledger entries with `seq`. `core/backfill/discover.find_transcripts(project=...)` returns `Transcript(path, agent, cwd, mtime)` newest first; `core/agent.resolve_canonical_agent(cwd).name` gives the project. `hooks/session_end._debounce_passes(state_path, vault_root, cfg, *, now=None)` gates the auto extraction (time gate on `last_run`, count gate over `bots/*/memory/*.md` mtimes). `install/settings.SLASH_COMMANDS` is the source of truth for slash commands; `tools/sync_plugin_manifest.py` regenerates `commands/` and the plugin manifest (CI fails on stale generated files — run it and commit the output).

---

## File map

| File | Change |
|---|---|
| `src/mnemo/core/learn.py` | **new** — `newest_transcript(cwd, *, session_id=None)`, `learn(cfg, *, cwd, session_id=None, dry_run=False) -> LearnReport` |
| `src/mnemo/core/briefing.py` | `generate_session_briefing(..., min_mutations=1)` keyword; `learn` passes 0 |
| `src/mnemo/cli/commands/learn.py`, `cli/parser.py`, `cli/commands/__init__.py` | public `mnemo learn [--session ID] [--dry-run]` |
| `src/mnemo/install/settings.py` | `SLASH_COMMANDS["learn"]`; regenerate `commands/` + manifests |
| `src/mnemo/hooks/session_end.py` | `_debounce_passes`: first extraction runs immediately; briefings count as new material |
| `docs/getting-started.md`, `docs/configuration.md`, `CHANGELOG.md` | "Five minutes" walkthrough, GIF storyboard, notes |

---

### Task 1: `core/learn.py`

**Files:** create `src/mnemo/core/learn.py`; modify `src/mnemo/core/briefing.py`; test `tests/unit/test_learn.py`.

- [ ] **Step 1: Failing tests** — `tests/unit/test_learn.py`:
  - `newest_transcript(cwd)` picks the newest jsonl for the cwd's project (patch `discover.find_transcripts` to return two `Transcript`s; assert the first's path); with `session_id="abc"` returns the transcript whose stem is `abc` regardless of order; returns None when nothing matches.
  - `learn()` end to end on a tmp vault with `paths.vault_root` patched, a jsonl whose user turn is `no — never retry on 4xx, only on 5xx` and NO file mutations, `llm.call` stubbed to return (1st call) a briefing body with `## Corrections\n- "never retry on 4xx, only on 5xx" → Retry only on 5xx` and (2nd call) a feedback page JSON citing that briefing with matching evidence: assert `report.learned == [{"slug": "retry-5xx-only", "name": ..., "confidence": "verified", "quote": "never retry on 4xx, only on 5xx"}]`, the page exists at `shared/feedback/retry-5xx-only.md`, `.mnemo/reflex-index.json` contains the slug, and the briefing was written even though the session had no file mutations.
  - `learn()` when the LLM returns a briefing with no Corrections and no pages → `report.learned == []` and `report.hint` is the non-empty "nothing new" text.
  - `dry_run=True` → no LLM call, no files written, `report.would_read == <jsonl path>`.
  - Lock held (create `.mnemo/extract.lock` fresh) → `report.error` mentions "another extraction is in progress"; nothing written.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `briefing.generate_session_briefing(jsonl_path, agent, cfg, *, min_mutations=1)`: replace the hard `== 0` check with `< min_mutations`. `learn.py`:
  ```python
  @dataclass
  class LearnReport:
      transcript: Optional[Path] = None
      briefing: Optional[Path] = None
      learned: list = field(default_factory=list)   # dicts from the ledger delta
      staged: int = 0                                # summary.demoted_unverified
      hint: str = ""
      error: str = ""
      would_read: Optional[Path] = None
  ```
  `newest_transcript(cwd, *, session_id=None)`: `project = agent.resolve_canonical_agent(cwd).name`; `ts = discover.find_transcripts(project=project)`; pick by stem when `session_id`, else `ts[0]`. `learn(cfg, *, cwd, session_id=None, dry_run=False)`: resolve transcript (error if none: "no transcript for this directory yet — start a Claude Code session here first"); if dry_run return `would_read`; record `before = max seq in learned ledger`; call `generate_session_briefing(path, project, cfg, min_mutations=0)`; call `run_extraction(cfg)` (catch the lock error → `report.error`); `after` = ledger entries with `seq > before` filtered to this project → `report.learned`; `report.staged = summary.demoted_unverified`; when `learned` is empty set `hint = "nothing new: no corrections found in this session. A correction is you telling Claude to stop, change, prefer, or never/always do something — say it in your own words and run `mnemo learn` again."`. Never leave the extract lock behind.
- [ ] **Step 4: Run → PASS. Commit** `feat(core): mnemo learn — briefing + extraction on the current session, synchronously`.

### Task 2: CLI + slash command

**Files:** create `src/mnemo/cli/commands/learn.py`; modify `cli/parser.py` (public subparser `learn` with `--session ID`, `--dry-run`; NOT in `ADVANCED_COMMANDS`), `cli/commands/__init__.py`, `install/settings.py` (`"learn": {"description": "learn from this session now: briefing + extraction, then the rule fires on your next prompt", "args": ("learn",)}`); run `python3 tools/sync_plugin_manifest.py` and commit the regenerated `commands/learn.md` + manifests. Tests: `tests/cli/test_learn_cli.py` — parser registration; `cmd_learn` prints `learned: <slug> — <name> (evidence: "<quote>")` per entry, or the hint, or the error with exit 1; `--dry-run` prints `would read: <path>`; `tests/unit/test_slash_command_rendering.py` — `learn` renders and carries the mnemo tag. Output format (exact):
  ```
  read: ~/.claude/projects/<enc>/<id>.jsonl
  briefing: bots/<proj>/briefings/sessions/<id>.md (2 correction(s))
  learned: retry-5xx-only — Retry only on 5xx (evidence: "never retry on 4xx, only on 5xx")
  staged for review: 1 (shared/_inbox/reference/)
  next prompt about this will surface it — check with `mnemo why`
  ```
  Commit `feat(cli): mnemo learn and /mnemo:learn`.

### Task 3: Hook debounce — first extraction runs immediately; briefings count

**Files:** `src/mnemo/hooks/session_end.py::_debounce_passes`; tests in `tests/unit/test_session_end_schedule.py` (extend).

- [ ] Tests: no `last_run` in state and zero memory files but one briefing under `bots/x/briefings/sessions/` → True; `last_run` 10 minutes ago with a new briefing → False (time gate still holds after the first run); `last_run` 2 hours ago and only a new briefing (no memory file) → True (briefings count as new material).
- [ ] Implement: when `last_run` is falsy return True early (first extraction of a vault is never debounced); in the count gate also glob `bots/*/briefings/sessions/*.md`.
- [ ] Commit `fix(hooks): first extraction is never debounced; briefings count as new material`.

### Task 4: Docs, changelog, PR

- [ ] `docs/getting-started.md`: new section **Five minutes** — exact sequence: (1) in any repo, tell Claude something in your own words (`"never use npm here, always yarn"`), (2) `mnemo learn`, (3) read the `learned:` line, (4) next prompt about packages → the rule appears as `mnemo reflex context`; `mnemo why` shows the arithmetic. Plus a 20-second GIF storyboard (three frames: the correction, the `mnemo learn` output, the next prompt with the injected rule) the maintainer records.
- [ ] `docs/configuration.md`: note that `extraction.auto.minIntervalMinutes` never blocks the first extraction, and that `mnemo learn` bypasses the debounce.
- [ ] `CHANGELOG.md` `[Unreleased]` **Added**: `mnemo learn` / `/mnemo:learn`; **Changed**: first extraction immediate, briefings count.
- [ ] Full suite green (`0 failed`), push `feat/ws-c-five-minute-loop`, PR titled `feat: mnemo learn — the five-minute loop (WS-C)`.

## Out of scope
README rewrite and the number block (WS-D); Codex/Cursor adapters.
