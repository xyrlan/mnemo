# Changelog

All notable changes to mnemo will be documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## v0.2.0 — 2026-04-13 — LLM extraction

**Added**
- `mnemo extract` command: LLM-powered consolidation of mirrored memory files
  into `shared/_inbox/` (cluster types) and `shared/project/` (1:1 promotion).
- Passive hint in `SessionEnd`: when ≥5 new memory files accumulate since the
  last extraction, today's log gets a `🟡 N new memories — run /mnemo extract`
  line (per-day dedup).
- New config section `extraction.*` with sensible defaults for model, chunk
  size, hint threshold, subprocess timeout.
- State file at `~/mnemo/.mnemo/extraction-state.json` tracks source/written
  hashes and per-slug status (`inbox`/`promoted`/`dismissed`/`direct`).

**Changed**
- `shared/` layout mirrors memory types: `shared/feedback/`, `shared/user/`,
  `shared/reference/`, `shared/project/`. The speculative v0.1 taxonomy
  (`people/`, `companies/`, `decisions/`) is deprecated. `shared/people.md`
  from v0.1 is left in place and documented as legacy.
- `core.errors.should_run()` now filters entries with `where` prefixed
  `extract.*` — manual extraction failures never trip the hook circuit
  breaker.

**Invariants preserved**
- Zero new runtime dependencies — stdlib only.
- Cross-platform (Linux / macOS / WSL / best-effort native Windows).
- Hooks never crash the Claude Code session; extraction command may fail
  loudly on stderr.
- `shared/feedback/**`, `shared/user/**`, `shared/reference/**` are sacred —
  the plugin reads them but never writes to them.

**See:** `docs/specs/2026-04-13-mnemo-v0.2-design.md` for the full design.

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
