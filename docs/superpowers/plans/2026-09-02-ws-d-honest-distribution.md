# WS-D — Honest Distribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** the README says what mnemo does, why it beats CLAUDE.md and Claude Code's auto memory, and what the numbers are — with nothing in it that the code contradicts; the slash-command surface shrinks to the four that matter.

**Architecture:** documentation plus one small code change: `mnemo status` gains a `Numbers` section that prints the same reproducible figures the README quotes (reflex emit rate over the last 14 days from `.mnemo/reflex-log.jsonl`, and `primacy@5` from `.mnemo/recall-report.json` when present), so README and tool agree. `SLASH_COMMANDS`/`PLUGIN_COMMANDS` in `install/settings.py` drop to `status`, `why`, `doctor`, `learn` (+ `help`), and `tools/sync_plugin_manifest.py` regenerates `commands/` and the plugin manifest (CI fails on stale generated files).

**Tech Stack:** Python 3.8+ stdlib only, pytest. Spec §D of `docs/superpowers/specs/2026-09-01-corrections-layer-design.md`.

**Conventions:** branch `feat/ws-d-honest-distribution` off `master` after WS-C merges. Trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01UrfBRCJ4yj7YNLAR66rhk5`. Tests with `/usr/local/bin/python3 -m pytest -q -p no:cacheprovider`. `tests/unit/test_docs_accuracy.py` checks config keys in `docs/configuration.md` and internal links — keep it green. Never claim a number in the README that `mnemo status` cannot print.

**Facts:** measured 2026-09-01 on the maintainer's vault (see memory `dogfood-validation-2026-09-01`): reflex injected on 68/877 prompts = 7.8% over 14 days; `mnemo recall` primacy@5 41.7% over 72 cases; big-bucket rerank (PR #105) primacy@5 16% → 31% over 530 evaluations. A reflex injection is one line (`• [[slug]]: <300-char preview>`) ≈ 80–100 tokens. `.mnemo/reflex-log.jsonl` rows carry `ts`, `emitted` (list), `project`; `.mnemo/recall-report.json` has `report.primacy_rate_at_5` and `report.cases`. `docs/getting-started.md` gained a "Five minutes" section in WS-C. README sections today: intro, Install, Check it worked, Day one isn't empty, Commands, Autopilot, Where things live, Docs, Privacy, License.

---

### Task 1: `mnemo status` prints the numbers

**Files:** `src/mnemo/core/numbers.py` (new: `reflex_emit_rate(vault_root, *, days=14) -> tuple[int, int] | None` counting emitted/total rows within the window, and `recall_primacy(vault_root) -> tuple[float, int] | None` reading the report), `src/mnemo/cli/commands/status.py` (section `Numbers (last 14 days):` → `  reflex: injected on N of M prompts (P%)` and `  recall: primacy@5 X% over C cases (mnemo recall, <date>)`; each line omitted when its source file is absent), tests `tests/unit/test_numbers.py` + status test extension.

- [ ] Tests first: seeded reflex log with rows inside/outside the window → correct counts; corrupt lines skipped; missing files → None and no section; report present → line rendered with the report's `generated_at` date.
- [ ] Implement; commit `feat(cli): mnemo status prints the reflex emit rate and recall number`.

### Task 2: Slash commands 8 → 4

**Files:** `src/mnemo/install/settings.py` (`SLASH_COMMANDS` keeps `status`, `doctor`, `learn`, `help` plus the non-plugin `init`/`init-project`/`uninstall`/`uninstall-project`; `PLUGIN_COMMANDS` = `status`, `why`, `doctor`, `learn`, `help`), regenerate with `python3 tools/sync_plugin_manifest.py` and commit `commands/` + `.claude-plugin/*.json`; `tests/unit/test_slash_command_rendering.py` and any test enumerating `PLUGIN_COMMANDS` updated to the new set; `mnemo help` still lists everything as CLI subcommands.

- [ ] Verify `why` exists in `SLASH_COMMANDS`/`PLUGIN_COMMANDS` today (grep) — add it if it only lives in `commands/why.md`.
- [ ] Removed slash commands (`open`, `fix`, `statusline`, `migrate`) stay available as `mnemo <name>`; README "Commands" says so.
- [ ] Commit `feat(plugin): slash commands are status, why, doctor, learn`.

### Task 3: README rewrite (top, Check it worked, Day one, Commands, Autopilot, Privacy)

**File:** `README.md`. Keep Install, Where things live, Docs, License as they are (Install unchanged).

- [ ] **Top.** Tagline: `Claude Code forgets your corrections. mnemo doesn't.` Then the same-project story in three lines: Monday you say "never use npm here, use yarn"; `mnemo learn` (or the end of the session); Thursday's prompt about packages gets the rule injected before Claude answers — one line, ~80 tokens, only when it clearly applies. Then one sentence on cross-project: rules that recur in two repos become universal and follow you everywhere. Then the numbers block (exactly what `mnemo status` prints, quoted from the maintainer's vault with the date):

  ```
  reflex: injected on 68 of 877 prompts over 14 days (7.8%), ~80 tokens each
  recall: primacy@5 41.7% over 72 cases (mnemo recall, 2026-09-01)
  big topics (>20 rules): primacy@5 16% → 31% with query-aware ranking
  ```
  followed by "Your own numbers: `mnemo status`."
- [ ] **How it compares** (new section, three rows): CLAUDE.md — you write and prune it by hand, it is loaded whole every session; Claude Code auto memory — Claude writes it, the first 200 lines load every session and the rest is silently dropped, nothing ranks per prompt; mnemo — learns from your corrections with a verifiable quote, keeps rules out of the sacred dir until they verify, and injects at most one rule per prompt chosen by BM25F against the prompt text; no database, no daemon, no per-prompt LLM call.
- [ ] **Check it worked** → point to the five-minute loop in `docs/getting-started.md` and show a real `mnemo learn` output block, then `/mnemo:why`.
- [ ] **Day one** → rewrite for the opt-in default: the first session shows the one-line invitation; `mnemo backfill --dry-run` shows the cost; nothing runs until you say so.
- [ ] **Commands** → the four slash commands; CLI list for the rest.
- [ ] **Autopilot** → "fully local by default: index rebuilds, dead-rule sweep, threshold calibration. It opens GitHub issues or PRs only if you set `autopilot.network.enabled` to true."
- [ ] **Privacy** → replace "Zero network calls" with: "No network calls unless you turn `autopilot.network.enabled` on. LLM calls go through the `claude` CLI you already have — one per session for the briefing, a few per extraction — and never on the prompt path." Keep the binary-download sentence.
- [ ] Remove jargon from user-facing text: "reflex" → "per-prompt recall" on first mention (keep `/mnemo:why` output verbatim), `bots/` explained as "per-project capture", no "Tier".
- [ ] Run `tests/unit/test_docs_accuracy.py` (internal links) and re-read the README top to bottom once for claims the code contradicts.
- [ ] Commit `docs(readme): honest top — corrections, comparison, numbers, opt-in defaults`.

### Task 4: Changelog, full suite, PR

- [ ] `CHANGELOG.md` `[Unreleased]`: **Added** `mnemo status` numbers; **Changed** slash commands reduced (list the removed ones and their CLI equivalents), README claims corrected.
- [ ] Full suite green; push; PR `docs: honest README, numbers in status, four slash commands (WS-D)`.

## After merge
Release 1.1.0 following `.github/workflows/release.yml` (manual `workflow_dispatch` build must be green before the tag). Record the demo GIF from the five-minute storyboard.
