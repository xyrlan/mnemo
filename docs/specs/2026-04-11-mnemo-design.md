# mnemo — Design Document

**Version:** v0.1 design
**Date:** 2026-04-11
**Status:** Draft (awaiting implementation plan)
**Author:** xyrlan

---

## 1. Summary

`mnemo` is a Claude Code plugin that automatically captures the context of every Claude Code session into a local Obsidian-compatible markdown vault organized in three tiers (raw logs, canonical facts, curated wiki). The plugin is **hooks-only** — no daemons, no background processes, no platform-specific scheduling — and runs identically on Linux, macOS, and Windows (via WSL or native Python).

**Tagline:** _"The Obsidian that populates itself so your Claude never forgets."_

**Audience:** "vibe coders" — developers who use Claude Code heavily in flow-state and want their context, decisions, and work history captured without discipline or tooling overhead.

**MVP scope (v0.1):** pure capture — mirror Claude Code memories, log prompts, log file edits, session boundaries. No LLM extraction.

**Phase 2 (v0.2):** opt-in LLM extraction to automatically populate canonical pages (Tier 2) from captured Tier 1 content, using user-provided `ANTHROPIC_API_KEY`.

---

## 2. Problem and motivation

### 2.1 — The pain

Vibe coders suffer from two overlapping problems:

**Problem 1 — Re-contextualization fatigue.** Every new Claude Code session starts cold. The user re-explains project constraints, decisions made last week, file conventions, pain points. The knowledge exists somewhere — in chat history, in scattered notes, in git commits — but it's not structured or accessible when the next session starts.

**Problem 2 — "I want a second brain but I'm too lazy to maintain one."** Personal knowledge management (PKM) tools like Obsidian, Logseq, and Roam promise a "second brain" but require **disciplined daily input**. Vibe coders don't have that discipline; they're in flow-state shipping features. They want the benefit of PKM without the maintenance tax.

### 2.2 — The insight

Claude Code already generates **all the raw material for a brain**:
- Session memories in `~/.claude/projects/*/memory/` (feedback, project facts)
- Every prompt the user submits
- Every file the agent writes or edits
- Decisions made during conversations

This content is **already produced** by the normal use of Claude Code. The missing layer is **capture and organization** into a searchable, linkable, graph-navigable format.

### 2.3 — Positioning

**"The Obsidian that populates itself so your Claude never forgets."**

Two promises in one line:
- **Effortless PKM** — vault grows while you vibe-code, zero discipline required
- **Continuity** — Claude sessions build on each other; nothing important is lost

---

## 3. Goals and non-goals

### 3.1 — Goals (v0.1)

1. **Zero-friction install.** One command (`/plugin install mnemo@claude-plugins-official`), one init (`/mnemo init`), done in under 30 seconds.
2. **Invisible capture.** Once installed, zero ongoing user action required. Hooks fire, content accumulates.
3. **Cross-platform by design.** Works identically on Linux, macOS, WSL, and best-effort on native Windows.
4. **Local-first and private.** All data stays on the user's machine. Zero telemetry, zero network calls in v0.1.
5. **Non-invasive and reversible.** Does not modify user code, projects, or data. Uninstall is complete and trivial.
6. **Robust by default.** Never crashes the Claude Code session. Errors are silent, logged, and recoverable.
7. **Obsidian-optimal but editor-agnostic.** Vault is plain markdown; Obsidian users get the graph skin automatically, but any editor works.

### 3.2 — Non-goals (v0.1)

1. **No LLM extraction.** Tier 2 (canonical pages) must be curated manually in v0.1. Extraction comes in v0.2.
2. **No background daemons.** No systemd, launchd, cron, or filesystem watchers. Hooks only.
3. **No cloud sync.** If users want multi-machine sync, they use their own git/Dropbox/Syncthing on the vault directory.
4. **No hosted SaaS.** Zero server infrastructure. v0.1 is 100% client-side.
5. **No support for non-Claude-Code AI clients.** Cursor, Windsurf, Cline, etc. are out of scope for v0.1. Could be v2.x if there's demand.
6. **No telemetry.** We don't collect usage stats, error reports, or anything else from users.

---

## 4. Architecture

### 4.1 — Key insight: hooks-only

Claude Code session memory files (`~/.claude/projects/*/memory/`) **only change while Claude Code is running**. Nothing else modifies them. This means **hook-driven capture at `SessionStart` and `SessionEnd` captures 100% of changes with zero gap** — there is no window during which changes can be missed.

This insight eliminates the need for:
- Systemd timers (Linux)
- Launchd agents (macOS)
- Task Scheduler (Windows)
- inotify/fswatch/chokidar daemons
- Any background process whatsoever

Result: **cross-platform compatibility comes for free**. The same Python code runs everywhere. Installation has no platform-specific scheduler setup.

### 4.2 — Stack

| Concern | Choice | Rationale |
|---------|--------|-----------|
| Language | Python 3.8+ (stdlib only) | Available on macOS and Linux out of the box; no `pip install` friction; stdlib has everything we need |
| Distribution | Claude Code plugin (primary) + GitHub repo (fallback) | Plugin = zero-friction install; repo = OSS visibility, manual install, contributor path |
| Config format | JSON | Human-readable, stdlib native, no YAML dependency |
| Data format | Plain markdown | Portable, future-proof, works with any editor |
| Locking | `os.mkdir` atomic primitive | Cross-platform without OS-specific imports (no `fcntl`, no `msvcrt`) |
| IPC between hooks | File in `/tmp/mnemo/session-<id>.json` | Simple, fast, cross-platform, self-cleaning |

### 4.3 — High-level data flow

```
┌────────────────────────────────────────────────────────────┐
│  Claude Code session in ~/projects/sg-imports              │
└────────────┬───────────────────────────────────────────────┘
             │
             │ (1) SessionStart fires
             ▼
     ┌───────────────────┐
     │ mnemo hook        │  → detect git repo (sg-imports)
     │  session_start.py │  → cache agent in /tmp/mnemo/session-<id>.json
     │                   │  → mirror ~/.claude/projects/.../memory → ~/mnemo/bots/sg-imports/memory
     └────────┬──────────┘  → atomic append "🟢 session started" to today's log
              │
              │ (2) user works; hooks fire on each event
              ▼
     ┌───────────────────┐
     │ mnemo hooks       │  → UserPromptSubmit (async) → log "💬 <prompt>"
     │ user_prompt.py    │  → PostToolUse Write|Edit (async) → log "✏️ edited <file>"
     │ post_tool_use.py  │
     └────────┬──────────┘
              │
              │ (3) SessionEnd fires
              ▼
     ┌───────────────────┐
     │ mnemo hook        │  → final mirror
     │  session_end.py   │  → append "🔴 session ended"
     │                   │  → delete /tmp/mnemo/session-<id>.json
     └───────────────────┘
              │
              ▼
┌────────────────────────────────────────────────────────────┐
│  ~/mnemo/ — user's vault                                   │
│    bots/sg-imports/logs/2026-04-11.md                      │
│    bots/sg-imports/memory/*.md                             │
│    shared/                    (manually curated by user)   │
│    wiki/                      (manually promoted by user)  │
└────────────────────────────────────────────────────────────┘
```

### 4.4 — Three-tier vault structure

```
~/mnemo/
├── HOME.md                    ← dashboard (user's entry point)
├── README.md
├── mnemo.config.json          ← plugin config
├── .obsidian/
│   └── snippets/
│       └── graph-dark-gold.css
├── bots/                      ← Tier 1: raw capture (plugin-managed)
│   └── <agent>/
│       ├── logs/
│       │   └── YYYY-MM-DD.md  ← daily append-only log
│       ├── memory/            ← mirror of ~/.claude/projects/*/memory/
│       └── working/           ← user drafts
├── shared/                    ← Tier 2: canonical facts (user-curated)
│   ├── people/
│   ├── companies/
│   ├── projects/
│   └── decisions/
└── wiki/                      ← Tier 3: curated knowledge
    ├── sources/               ← promoted notes (user-triggered)
    └── compiled/              ← output of compile_wiki (regeneratable)
```

**Tier model:**
- **Tier 1** is the inbox — raw, noisy, plugin-managed, never deleted by the plugin.
- **Tier 2** is the truth layer — concise, canonical, user-maintained. Promoted from Tier 1 manually (or automatically in v0.2 via LLM extraction).
- **Tier 3** is published knowledge — stable, curated, worth referencing long-term.

**Rule of trust:** content in higher tiers has been reviewed and is more trustworthy. Plugin never auto-promotes across tier boundaries in v0.1.

---

## 5. Components

### 5.1 — Module tree

```
mnemo/
├── .claude-plugin/
│   ├── plugin.json               # marketplace manifest
│   └── marketplace.json          # optional
├── src/mnemo/
│   ├── __init__.py
│   ├── __main__.py               # python -m mnemo <command>
│   ├── cli.py                    # /mnemo slash commands
│   ├── hooks/
│   │   ├── __init__.py
│   │   ├── session_start.py
│   │   ├── session_end.py
│   │   ├── user_prompt.py
│   │   └── post_tool_use.py
│   ├── core/
│   │   ├── agent.py              # git repo detection + agent naming
│   │   ├── config.py             # config load/save
│   │   ├── locks.py              # cross-platform atomic lock
│   │   ├── mirror.py             # Claude → vault sync
│   │   ├── session.py            # /tmp session cache
│   │   ├── log_writer.py         # atomic append to daily log
│   │   ├── errors.py             # error logging + circuit breaker
│   │   ├── paths.py              # vault path resolution
│   │   └── wiki.py               # promote + compile
│   ├── install/
│   │   ├── preflight.py
│   │   ├── scaffold.py
│   │   └── settings.py
│   └── templates/
│       ├── HOME.md
│       ├── README.md
│       ├── mnemo.config.json
│       └── graph-dark-gold.css
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── docs/
│   ├── specs/
│   ├── getting-started.md
│   ├── configuration.md
│   └── troubleshooting.md
├── pyproject.toml
└── README.md
```

### 5.2 — Core modules (one-paragraph summaries)

**`core/agent.py` — agent resolution.** Single public function `resolve_agent(cwd: str) -> AgentInfo`. Walks up the directory tree from `cwd` looking for `.git/`. If found, `agent = basename(repo_root)`. If not, `agent = basename(cwd)`. Returns a dataclass `AgentInfo(name, repo_root, has_git)`. Pure, no I/O beyond `os.path`.

**`core/config.py` — config loading.** Loads `~/mnemo/mnemo.config.json` (or path from `MNEMO_CONFIG_PATH` env). Has sensible defaults for every key. Preserves unknown keys (forward compatibility). Config schema:

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

**`core/locks.py` — cross-platform advisory lock.** Context manager `try_lock(lock_dir)` using `os.mkdir` as the atomic primitive. Non-blocking. Self-heals against stale locks (>60s old). Zero OS-specific imports. Works identically on POSIX and Windows.

**`core/mirror.py` — Claude → vault sync.** Function `mirror_all(vault_root)`. Acquires mirror lock, iterates `~/.claude/projects/*/memory/`, rsyncs each to `vault_root/bots/<agent>/memory/`. **Never uses `--delete`** — user's manual notes are sacred. Falls back to pure-Python file copy if `rsync` is not available (Windows native).

**`core/session.py` — session cache.** Interface: `save(session_id, info)`, `load(session_id) -> dict | None`, `clear(session_id)`, `cleanup_stale()`. Cache file lives at `{tempdir}/mnemo/session-<id>.json`. Self-heals against corrupted cache (returns `None`, caller falls back to on-the-fly detection).

**`core/log_writer.py` — atomic append.** Function `append_line(agent, line, vault_root)`. Constructs path `vault_root/bots/<agent>/logs/YYYY-MM-DD.md`, creates header if new, opens in `"ab"` mode, writes the line in a single syscall (POSIX atomic under PIPE_BUF). Truncates lines > 3800 bytes to stay under atomicity threshold.

**`core/errors.py` — error logging + circuit breaker.** `log_error(vault_root, where, exc)` appends JSON line to `.errors.log` (best-effort, never raises). `should_run(vault_root) -> bool` returns `False` if >10 errors in last hour (circuit breaker). `reset(vault_root)` archives `.errors.log` with timestamp and closes the breaker. Log rotation at 5MB.

**`core/paths.py` — path resolution.** Resolves `vault_root` from config with `~/` expansion, validates writeability, provides helpers for common paths (logs dir, memory dir, etc). Central point for "where is X?" questions.

**`core/wiki.py` — promote + compile.** `promote_note(source, vault_root)` copies a note from `bots/` or `shared/` to `wiki/sources/`, adds frontmatter with origin + date. `compile_wiki(vault_root)` regenerates `wiki/compiled/` from `wiki/sources/` plus an index. Both are idempotent.

### 5.3 — Hook entry points (`hooks/*.py`)

Each is a ~20-line `main()` that:
1. `json.load(sys.stdin)` — parse payload from Claude Code
2. Check circuit breaker (`errors.should_run()`)
3. Load config
4. Delegate to `core/` modules
5. Wrap everything in top-level `try/except`
6. Always `return 0`

Zero business logic in hooks — they are orchestrators.

### 5.4 — Install modules

**`install/preflight.py`** — Validates Python version, writable vault dir, writable `~/.claude/settings.json`, disk space, `rsync` availability, Obsidian presence. Returns `PreflightResult(ok, issues)`. Every issue has actionable remediation text.

**`install/scaffold.py`** — Creates vault directory tree, copies templates, writes default config. Idempotent: safe to re-run without overwriting existing files.

**`install/settings.py`** — Reads `~/.claude/settings.json`, merges mnemo hooks (preserving hooks from other plugins), backs up the original with timestamp, writes the merged version. `uninject_hooks()` reverses the process. Uses `core/locks` to prevent race conditions from concurrent `/mnemo init` calls.

### 5.5 — Dependencies

**Zero.** Python 3.8+ stdlib only. Explicit decision to avoid:
- `pip install` friction
- Virtualenv conflicts
- Supply-chain vulnerabilities
- Version-specific bugs from third-party packages

Uses: `pathlib`, `json`, `subprocess` (for calling `rsync`), `tempfile`, `datetime`, `os`, `sys`, `traceback`, `argparse`, `shutil`, `dataclasses`, `threading` (test only).

**System dependency:** `rsync` via `subprocess`. If missing (Windows native without WSL), fallback is a pure-Python file walker (~30 lines).

---

## 6. Data flow (event-by-event)

### 6.1 — `SessionStart` (sync, 40–80ms)

**Payload (stdin):**
```json
{
  "session_id": "abc123",
  "cwd": "/home/user/github/sg-imports",
  "source": "startup" | "resume" | "clear",
  "transcript_path": "..."
}
```

**Steps:**
1. Parse JSON, check circuit breaker, load config
2. `agent.resolve_agent(cwd)` — walks up to find `.git`, returns `AgentInfo`
3. `session.save(session_id, agent_info)` — writes `/tmp/mnemo/session-<id>.json`
4. `session.cleanup_stale()` — opportunistic cleanup of files >24h old
5. `mirror.mirror_all(vault_root)` — syncs Claude memory to vault (rsync under lock)
6. If `cfg.capture.sessionStartEnd`: `log_writer.append_line("🟢 session started (source)")`
7. Return 0

**Side-effects:**
- `/tmp/mnemo/session-<id>.json` created
- Files mirrored in `~/mnemo/bots/<agent>/memory/`
- One line appended to `~/mnemo/bots/<agent>/logs/YYYY-MM-DD.md`

### 6.2 — `UserPromptSubmit` (async, <5ms visible)

**Payload:**
```json
{
  "session_id": "abc123",
  "cwd": "...",
  "prompt": "user's prompt text, possibly multi-line"
}
```

**Settings.json has `"async": true`** — Claude Code fires and doesn't wait.

**Steps:**
1. Parse, circuit-break, load config
2. If `not cfg.capture.userPrompt`: return 0
3. `session.load(session_id)` — lookup cached agent; fallback to `agent.resolve_agent(cwd)` if missing
4. Extract first non-empty line of prompt, strip whitespace, truncate at 200 chars, escape backticks
5. Skip if empty or contains "system-reminder"
6. `log_writer.append_line("💬 {first_line}")`
7. Return 0

**Concurrency:** multiple async invocations from the same session write to the same log file. Atomic append under `O_APPEND` guarantees correct ordering and no corruption for lines under PIPE_BUF (~4KB on Linux, 512B on macOS).

### 6.3 — `PostToolUse` (matcher `Write|Edit`, async, <5ms visible)

**Payload:**
```json
{
  "session_id": "abc123",
  "tool_name": "Edit",
  "tool_input": { "file_path": "...", "old_string": "...", "new_string": "..." },
  "tool_response": { "filePath": "...", "success": true }
}
```

**Steps:**
1. Parse, circuit-break, load config
2. If `not cfg.capture.fileEdits`: return 0
3. Extract `file_path` from `tool_response.filePath` or `tool_input.file_path`
4. Load session cache for agent
5. Compute relative path (repo-relative if in git repo, basename otherwise)
6. Compute verb: "created" if `Write`, "edited" if `Edit`
7. `log_writer.append_line("✏️ {verb} \`{display}\`")`
8. Return 0

**No debouncing in v0.1.** 20 edits to the same file = 20 log lines. If this proves noisy in real usage, v0.1.1 adds deduplication.

### 6.4 — `SessionEnd` (sync, 30–60ms)

**Payload:**
```json
{
  "session_id": "abc123",
  "reason": "exit" | "clear" | "compact" | "interrupt"
}
```

**Steps:**
1. Parse, circuit-break, load config
2. Load session cache for agent
3. Final `mirror.mirror_all()` — catch any last memory updates from Claude Code
4. If `cfg.capture.sessionStartEnd`: `log_writer.append_line("🔴 session ended ({reason})")`
5. `session.clear(session_id)` — delete the cache file
6. Return 0

**Why sync, not async:** the parent Claude Code process is exiting. Async subprocess might be killed by the OS before completion. Sync guarantees cleanup and final mirror run to completion.

### 6.5 — Example: full session trace

User opens terminal, `cd ~/github/sg-imports`, runs `claude`, submits one prompt, Claude edits two files, user `exit`s.

```
t=0.00s  $ claude
          → SessionStart hook (sync, ~60ms)
          → agent="sg-imports", mirror, log "🟢 session started (startup)"

t=5.20s  user: "add validation to login form" [enter]
          → UserPromptSubmit hook (async)
          → log "💬 add validation to login form"

t=12.4s  Claude calls Edit on src/login/form.tsx
          → PostToolUse hook (async)
          → log "✏️ edited `src/login/form.tsx`"

t=14.1s  Claude calls Edit on src/login/__tests__/form.test.tsx
          → PostToolUse hook (async)
          → log "✏️ edited `src/login/__tests__/form.test.tsx`"

t=18.5s  $ exit
          → SessionEnd hook (sync, ~50ms)
          → final mirror, log "🔴 session ended (exit)"
          → cleanup cache file
```

Log file `~/mnemo/bots/sg-imports/logs/2026-04-11.md`:
```markdown
---
tags: [log, sg-imports]
date: 2026-04-11
---
# 2026-04-11 — sg-imports

- **14:22** — 🟢 session started (startup)
- **14:22** — 💬 add validation to login form
- **14:22** — ✏️ edited `src/login/form.tsx`
- **14:22** — ✏️ edited `src/login/__tests__/form.test.tsx`
- **14:22** — 🔴 session ended (exit)
```

**Total user-visible latency added across a 20-minute session:** ~110ms, concentrated at session boundaries. Zero visible latency during active use.

---

## 7. Concurrency, atomicity, and thread safety

### 7.1 — Log writes

POSIX guarantees that a `write()` syscall of size ≤ `PIPE_BUF` is atomic for writers sharing a file descriptor opened with `O_APPEND`. PIPE_BUF is 4096 bytes on Linux, 512 bytes on macOS. Log lines are ~100 bytes. The implementation:

```python
line = f"- **{now}** — 💬 {prompt}\n"
if len(line.encode("utf-8")) > 3800:
    line = line[:3800] + "...\n"  # safety margin under PIPE_BUF
with open(log_file, "ab", buffering=0) as fh:
    fh.write(line.encode("utf-8"))  # single syscall, atomic
```

**Result:** multiple sessions writing to the same daily log produce lines in arrival order with zero corruption.

### 7.2 — Mirror concurrency

Two `SessionStart` events firing in near-simultaneous sessions would both try to `rsync` into the same vault directory. Solution: `core/locks.try_lock(vault_root / ".mirror.lock")`. If the lock is held, the second mirror no-ops — safe because the first mirror covers the same source files.

### 7.3 — Settings.json injection (install-time)

Race condition: two `/mnemo init` invocations read settings.json, each computes a merge, the second write overwrites the first. Solution: `install/settings.py` acquires `~/.claude/.mnemo-settings.lock` before read-modify-write. Wait up to 5s; on timeout, abort with clear error.

### 7.4 — Session cache

One file per session, keyed by `session_id`. Zero contention between sessions. Corrupted cache (e.g., torn write, disk issue) is detected on read and the file is deleted; the hook falls back to on-the-fly agent detection.

---

## 8. Error handling and robustness

### 8.1 — The rule

**No hook ever crashes the Claude Code session.** All logic wrapped in try/except at the entry point. Always `exit 0`.

### 8.2 — Error categories

| Category | Examples | Handling | User-visible? |
|----------|----------|----------|---------------|
| Transient | Lock contention, rsync busy | Silent no-op / skip | No |
| Recoverable | Corrupted cache, invalid config, missing path | Fallback + `.errors.log` | Only via `/mnemo doctor` |
| Fatal | PermissionError, disk full, Python crash | Log + circuit breaker | Via `/mnemo status` |

### 8.3 — Circuit breaker

Opens when there are >10 errors in the last hour. While open, every hook reads the error log head and returns immediately without writing anything. The Claude Code session continues normally.

`/mnemo status` shows the breaker state. `/mnemo doctor` diagnoses root cause. `/mnemo fix` archives `.errors.log` and closes the breaker.

### 8.4 — Key edge cases

- **Malformed stdin JSON** → caught, logged, exit 0
- **Missing `session_id`** → fallback to on-the-fly agent detection
- **Permission denied on vault** → circuit breaker opens, doctor shows fix
- **Disk full** → circuit breaker opens quickly, user clears space + `/mnemo fix`
- **Corrupted session cache** → file deleted, fallback to detection
- **Missing `rsync`** → pure-Python fallback (slower but functional)
- **Git repo root is `/`** → agent name "root", still works
- **Concurrent `/mnemo init`** → lock on settings.json write
- **Pre-existing malformed `settings.json`** → abort with clear message, never overwrite what we don't understand
- **`/tmp` is noexec** → graceful degradation, fallback to on-the-fly detection

### 8.5 — Backup and reversibility

- `~/.claude/settings.json` is backed up to `.bak.<timestamp>` on every modification
- Uninstall restores the backup and removes hook entries
- Vault is never deleted by the plugin, only by explicit user action
- Reset archives `.errors.log` instead of deleting

---

## 9. Installation and first-run experience

### 9.1 — Install path A: Claude Code plugin (primary)

```
/plugin install mnemo@claude-plugins-official
```

Claude Code downloads, registers slash commands, shows:
```
✅ Installed: mnemo v0.1.0
   Run /mnemo init to set up your vault.
```

### 9.2 — Install path B: manual (OSS repo)

```
git clone https://github.com/xyrlan/mnemo ~/.mnemo-repo
cd ~/.mnemo-repo
./install.sh
```

Both paths converge on the same `python -m mnemo init` entry point.

### 9.3 — First-run wizard (`/mnemo init`)

1. **Preflight checks** with actionable error messages
2. **Optional vault path override** (default: `~/mnemo/`)
3. **Scaffold** vault structure with templates
4. **Inject hooks** into `settings.json` with backup
5. **Initial mirror** from existing Claude Code memories
6. **Success summary** with next steps

**Total time:** 3–5 seconds end-to-end.

### 9.4 — Post-install commands

```
/mnemo init        — first-run setup (idempotent)
/mnemo status      — vault state + hook health + recent activity
/mnemo doctor      — full diagnostic with actionable fixes
/mnemo open        — open vault in Obsidian or file manager
/mnemo promote     — promote a note to wiki/sources/
/mnemo compile     — regenerate wiki/compiled/ from sources
/mnemo fix         — reset circuit breaker
/mnemo uninstall   — remove hooks (keeps vault)
/mnemo help        — list commands
```

### 9.5 — Uninstall

```
/mnemo uninstall
```

Removes hook entries from `settings.json` (restoring from backup when possible). **Never deletes vault data.** Reinstall is trivial: `/mnemo init` again finds the existing vault.

---

## 10. Testing strategy

### 10.1 — Pyramid

```
            Dogfood (3+ real users, 1+ week each)
           ─────────────────────────────────────────
           E2E (~10 tests: install flow, full cycles)
          ─────────────────────────────────────────────
          Integration (~30 tests: hooks + core together)
         ─────────────────────────────────────────────────
         Unit (~100 tests: isolated core/ functions)
```

### 10.2 — Coverage targets

| Area | Target |
|------|--------|
| `core/*` (pure logic) | >90% |
| `hooks/*` (thin orchestration) | >80% |
| `install/*` | >80% |
| `cli.py` | >70% |
| **Project total** | **>85%** |

### 10.3 — Critical tests (must pass)

- **`test_concurrent_log_writes`** — 50 threads × 20 lines each in the same log, verify all lines present and uncorrupted
- **`test_concurrent_inject_hooks`** — 5 `/mnemo init` processes simultaneously, verify settings.json is intact and valid
- **`test_full_session_cycle`** — SessionStart → 3 prompts → 2 edits → SessionEnd, verify log content is correct
- **`test_hook_never_raises`** — 50 variations of malformed payload in each hook, all must exit 0
- **`test_stale_lock_recovery`** — lock dir with ancient mtime must be reclaimed
- **`test_circuit_breaker_threshold`** — after 10 errors, hooks must short-circuit
- **`test_missing_rsync_fallback`** — mock `shutil.which("rsync") → None`, verify Python fallback works
- **`test_uninstall_reversible`** — install + uninstall + verify settings.json is restored and vault is intact

### 10.4 — CI pipeline

GitHub Actions:
- Matrix: Ubuntu + macOS × Python 3.8, 3.9, 3.10, 3.11, 3.12
- Jobs: unit → integration → e2e → coverage gate (>85%)
- Windows: runs unit tests as `continue-on-error: true` (experimental)
- Gate: all Linux + macOS jobs must pass to merge

### 10.5 — Beta and launch

- **Phase 1 (1 week):** self-dogfood — the author uses mnemo in daily work
- **Phase 2 (1 week):** closed beta with 2-3 developer friends who use Claude Code
- **Phase 3:** public launch via marketplace + GitHub + landing page + tweet
- **Release gate:** zero fatal errors across Phase 1 + 2, all reported issues closed or triaged

---

## 11. Release plan

### 11.1 — Milestones

```
M1 — Core modules                           (week 1-2)
  ├ agent.py, paths.py, locks.py, session.py
  ├ log_writer.py (with concurrency test)
  ├ errors.py (with circuit breaker test)
  └ config.py

M2 — Mirror + wiki                           (week 2)
  ├ mirror.py (with rsync + Python fallback)
  └ wiki.py (promote + compile)

M3 — Hooks                                   (week 3)
  ├ session_start.py
  ├ session_end.py
  ├ user_prompt.py
  └ post_tool_use.py

M4 — Install + CLI                           (week 3-4)
  ├ preflight.py
  ├ scaffold.py
  ├ settings.py
  └ cli.py (all commands)

M5 — Plugin packaging                        (week 4)
  ├ plugin.json
  ├ marketplace submission prep
  └ templates

M6 — Test + docs                             (week 5)
  ├ E2E suite
  ├ README + getting-started + troubleshooting
  └ CI pipeline green

M7 — Beta                                    (week 6)
  └ self-dogfood + closed beta

M8 — v0.1.0 launch                           (week 7)
  └ marketplace submission + tweet
```

### 11.2 — Release gates

```
v0.1.0 CHECKLIST
□ All CI passing (Linux + macOS × 5 Python versions)
□ Coverage ≥85%
□ Self-dogfood 1 week, 0 fatal errors
□ Closed beta 1 week, 3 testers, all issues addressed
□ README + docs complete
□ CHANGELOG.md populated
□ LICENSE (MIT recommended)
□ GitHub release drafted
□ Marketplace submission prepared
□ Landing page live
□ Tweet draft ready
□ Tag: v0.1.0
□ Ship
```

---

## 12. Future roadmap (non-binding)

### 12.1 — v0.2 — LLM extraction

- Opt-in extraction of canonical pages from Tier 1 content using Claude API
- User provides `ANTHROPIC_API_KEY`
- Extracted pages go to `_inbox/` with `#needs-review` tag
- User manually promotes to `shared/` after review
- Batch-based, runs on `SessionEnd` or manual `/mnemo extract`
- Cost transparency: dry-run mode estimates tokens before spending

### 12.2 — v0.3 — Enriched capture

- `PostToolUse` for `Bash` (optional, user must opt in due to secret leak risk)
- `PostToolUse` for `WebFetch`
- `Notification` events
- Debouncing of repeated file edits

### 12.3 — v0.4 — Graph automation

- Auto-suggest wikilinks based on entity mentions
- Detect broken links and suggest fixes
- Graph cluster analysis

### 12.4 — v1.0 — Maturity

- Multi-client support investigation (Cursor, Windsurf)
- Hosted sync option (optional, end-to-end encrypted)
- Plugin marketplace submission finalized
- Long-term support commitment

---

## 13. Open questions

These are questions that do NOT block v0.1 but should be answered before v0.2:

1. **LLM extraction prompt design** — what exactly does the prompt look like? Few-shot examples? Structured output schema?
2. **Token budgeting for extraction** — default batch size, rate limits, cost warnings
3. **Conflict resolution in Tier 2** — if extraction suggests updating an existing canonical page, how is merge handled?
4. **Multi-machine sync** — do we document user-driven git sync, or build it in?
5. **Backup strategy** — should the plugin do automatic snapshots of the vault, or leave it to the user?
6. **Plugin auto-update** — does mnemo update itself, or wait for the user?

---

## 14. Design decisions log

Decisions made during brainstorming, preserved here for future maintainers:

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Plugin + OSS repo, not SaaS | Zero infra, maximum privacy, OSS visibility |
| 2 | Claude Code only, not multi-client (v0.1) | Claude Code has the richest hook system; multi-client is 10× more work for diminishing returns |
| 3 | Phased scope: capture first, extraction later | Ship faster, validate with users before investing in LLM pipeline |
| 4 | Obsidian recommended, not required | Plain markdown is future-proof and works with any editor |
| 5 | Hooks-only, no daemons | Claude Code memories only change while CC runs → hooks capture 100% with no gap; cross-platform comes free |
| 6 | Python stdlib only, no third-party deps | Zero install friction, no supply-chain risk |
| 7 | `os.mkdir` for locks, not `fcntl`/`msvcrt` | Cross-platform without OS-specific imports |
| 8 | Git repo detection + session cache for agent naming | Stable across `cd`, pretty names from repo root, captured once per session |
| 9 | Default `~/mnemo/`, configurable | Opinionated default, escape hatch for power users |
| 10 | Name: `mnemo` (Greek memory muse) | Unique, memorable, zero trademark collisions |
| 11 | Capture scope default: SessionStart/End + Prompts + Write/Edit | Sweet spot between minimalist (too sparse) and maximalist (too noisy/risky) |
| 12 | UserPrompt + PostToolUse hooks are async | Zero visible latency during active use |
| 13 | Circuit breaker at 10 errors/hour | Protects against runaway failure loops without being too aggressive |
| 14 | Backup settings.json on every modification | User trust — reversibility is table stakes |
| 15 | Uninstall never deletes vault data | User data is sacred |

---

## Appendix A — File formats

### A.1 — Daily log format

```markdown
---
tags: [log, <agent>]
date: YYYY-MM-DD
---
# YYYY-MM-DD — <agent>

- **HH:MM** — 🟢 session started (<source>)
- **HH:MM** — 💬 <prompt first line>
- **HH:MM** — ✏️ edited `<relative file path>`
- **HH:MM** — ✏️ created `<relative file path>`
- **HH:MM** — 🔴 session ended (<reason>)
```

### A.2 — Error log format

JSON lines, one entry per line:

```json
{"timestamp": "2026-04-11T14:30:22", "where": "log_writer", "kind": "PermissionError", "message": "[Errno 13] ...", "traceback_summary": "..."}
```

### A.3 — Session cache format

```json
{
  "agent": "sg-imports",
  "repo_root": "/home/user/github/sg-imports",
  "has_git": true,
  "started_at": "2026-04-11T14:22:01",
  "cwd_at_start": "/home/user/github/sg-imports"
}
```

### A.4 — Config format

See Section 5.2 for full schema.

---

**End of design document.**
