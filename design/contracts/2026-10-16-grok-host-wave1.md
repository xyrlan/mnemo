---
feature: grok-host-w1
created: 2026-10-16
verdict: parallel
---

Wave 1 of the Grok round. Read `design/specs/2026-10-16-grok-host.md` first:
every fact a piece needs is there with how it was measured, and the
decisions are not yours to reopen (say so in the PR if one is wrong).

The seams are on master already: `mnemo.core.agent_cli.current_cli`,
`mnemo.core.grok.paths` (homes, session directories, `short_id`, leader
socket), `Session.agent`, the `--agent` flags, `mnemo grok` as a parser entry
with a stub handler, and `grok` in every `--host` choice. Build on them; do
not move them.

Rules for all five pieces:

- **Claude stays byte-identical** (decision 8). With `MNEMO_AGENT` unset and
  no `GROK_SESSION_ID`, what your files produce for a Claude session is what
  master produces. A test proves it.
- **Measure, then claim.** A Grok behaviour your code relies on is checked
  against the real `grok` (live test, marked so the default suite skips it)
  or against the source at `github.com/xai-org/grok-build`, and the PR says
  which. `grok` 1.0.60 is on PATH and `~/.grok` is logged in to a free xAI
  account; its only model is `grok-4.7`. Spend it on what needs measuring.
- **A live probe never reaches the maintainer's vault.** Grok fires every
  hook in `~/.claude/settings.json`, mnemo's included, so a probe session run
  as-is writes extractions and briefings into the real vault. Run probes with
  `HOME` set to a throwaway dir (your own `.claude/settings.json` in it if the
  probe is about hooks) and `GROK_HOME=~/.grok` for the login, or with
  `GROK_CLAUDE_HOOKS_ENABLED=0` when hooks are not the subject. Never write
  to `~/.grok` beyond what `grok` itself writes there, never print or copy
  `auth.json`, and stop any leader you started.
- **No Grok binary in the default suite.** Unit tests use fixtures shaped
  like the real files (copy the shapes from the spec's Facts, or record a
  real one and redact it).
- Run the suite from your worktree with `PYTHONPATH=src`, on Python 3.8
  syntax, comparing paths as paths (CLAUDE.md).

## grok-acp

- **files:** src/mnemo/core/grok/acp.py, src/mnemo/cli/commands/grok.py, tests/unit/test_grok_acp.py, tests/unit/test_cli_grok.py, tests/live/test_grok_leader_live.py
- **exposes:** `GrokUnavailable(RuntimeError)`, `RosterEntry(session_id: str, cwd: str, activity: str, resident: bool, model_id: Optional[str], effort: Optional[str], title: Optional[str], last_change_ms: Optional[int], home: Path)`, `Leader(home: Optional[Path] = None)`, `Leader.new_session(cwd: str, *, model: Optional[str] = None, effort: Optional[str] = None, yolo: bool = True) -> str`, `Leader.prompt(session_id: str, cwd: str, text: str, *, wait: bool = False, timeout: float = 600) -> Optional[str]`, `Leader.roster() -> List[RosterEntry]`, `Leader.close(session_id: str) -> str`, `Leader.billing() -> Dict[str, Any]`, `mnemo grok roster [--home DIR] --json`, `mnemo grok send ID TEXT [--home DIR]`, `mnemo grok close ID [--home DIR]`, `mnemo grok billing [--home DIR] --json`
- **consumes:** nothing
- **effort:** high

mnemo's one client of Grok's leader. Everything else in mnemo and in
mnemo-desktop reaches a live Grok session through this module or the `mnemo
grok` command it backs — the desktop has no ACP client of its own.

What it must do, measured live:

- `new_session` returns the id of a session that keeps working after this
  process exits, and with `yolo=True` runs its tools with **no client
  attached** and nobody to answer a permission request. The spec marks
  `_meta.yoloMode` unconfirmed; find what actually does it and test it live.
- `prompt(wait=False)` returns once the turn has started, never after it
  ended; `wait=True` returns the model's text. It must work on a session that
  is `working`, `idle` or `dormant`.
- `roster` is the live state per session; `close` returns Grok's outcome.
- A home whose leader socket would exceed the platform's Unix-socket limit
  still works (spec: Leader and ACP). Where the socket goes instead is yours
  to choose; it must be stable per home so every mnemo process and the
  desktop find the same leader.
- `GrokUnavailable` when `grok` is not on PATH, the home has no login, or the
  leader does not come up — with a message a person can act on.

`mnemo grok` replaces the stub in `cli/commands/grok.py`. `roster` without
`--home` covers every home in `paths.grok_homes()`. JSON shapes, which
mnemo-desktop is written against:

- `roster --json`: `[{"session_id", "short_id", "cwd", "activity",
  "resident", "model_id", "effort", "title", "last_change_ms", "home"}]`,
  `short_id` from `paths.short_id`.
- `send` exits 0 once the turn started; `close` prints the outcome; both exit
  non-zero with the reason on stderr when the session or leader is not there.
- `billing --json`: the result of `_x.ai/billing`, unchanged.

## grok-hooks

- **files:** src/mnemo/hooks/pre_tool_use.py, src/mnemo/hooks/user_prompt_submit.py, src/mnemo/hooks/session_start.py, src/mnemo/hooks/session_end.py, src/mnemo/core/grok/hooks.py, src/mnemo/core/grok/briefing_file.py, tools/measure_grok_reach.py, tests/unit/test_grok_hooks.py, tests/unit/test_grok_briefing_file.py, tests/unit/test_measure_grok_reach.py
- **exposes:** `is_grok_payload(payload: Mapping[str, Any], env: Optional[Mapping[str, str]] = None) -> bool`, `claude_tool_name(name: str) -> Optional[str]`, `python tools/measure_grok_reach.py [--home DIR] [--since DATE] [--json]`
- **consumes:** `load_events(path: Path) -> List[Dict[str, Any]]` from grok-transcripts
- **effort:** high

mnemo's four hooks, made to do in a Grok session what they do in a Claude
one, through the channels Grok actually delivers (spec decisions 6 and 7).

- **PreToolUse:** enforcement, enrichment and the memory-path guard act on
  Grok's tool names and input keys (`run_terminal_command`, `read_file` with
  `target_file`, `write`, `search_replace`, `hashline_edit`).
- **Per-prompt context:** what UserPromptSubmit returns as
  `additionalContext` for Claude (reflex, held notices) reaches a Grok
  session's model on the first PreToolUse after that prompt, once. A turn
  with no tool call gets none; say in the PR how often that is on real
  sessions.
- **Briefing:** the SessionStart briefing for a project reaches the model of
  the *next* Grok session in that project at its first turn, through a file
  Grok loads at startup. It must not reach other projects, must never be
  committed to anyone's repo, and must survive a worktree. Which file, and
  how the project is trusted if it needs to be, is yours — measured.
- **SessionEnd:** a Grok payload's `transcript_path` names `updates.jsonl`;
  extraction and the briefing read `chat_history.jsonl` beside it through
  `load_events`. Grok gives SessionEnd hooks 1.5 s: everything slow runs
  detached, and the hook returns well inside that.
- **One run per event** on a machine where Grok reads mnemo's hooks from
  both `~/.claude/settings.json` and a Grok hook file (grok-install may add
  one): if both would fire, the second is a no-op.
- `tools/measure_grok_reach.py` reads real Grok sessions' `chat_history.jsonl`
  and reports, per channel (briefing at turn 1, per-prompt context), how many
  sessions or turns mnemo's injected text reached, with unit tests over
  synthetic histories (CLAUDE.md: a measured claim ships with what measured
  it).

## grok-transcripts

- **files:** src/mnemo/core/grok/transcript.py, src/mnemo/core/backfill/discover.py, src/mnemo/core/transcript.py, src/mnemo/core/briefing.py, src/mnemo/core/learn.py, tests/unit/test_grok_transcript.py, tests/unit/test_discover_grok.py, tests/unit/test_briefing_grok.py
- **exposes:** `load_events(path: Path) -> List[Dict[str, Any]]`, `is_grok_history(path: Path) -> bool`, `grok_session_id(path: Path) -> Optional[str]`
- **consumes:** nothing
- **effort:** high

mnemo learns from Grok sessions as it does from Claude ones: discovery,
backfill, `mnemo learn`, extraction and the session briefing all accept a Grok
`chat_history.jsonl`.

- `load_events` turns a Grok history into the event shape the rest of mnemo
  already reads from a Claude transcript, so callers downstream need no Grok
  branch. Injected user records (`synthetic_reason`) are not the user's words.
- Discovery finds Grok sessions in every home of `paths.grok_homes()` for a
  project's cwd and its worktrees, beside the Claude ones.
- A Grok session's id is its directory name, not the file stem (every Grok
  file is called `chat_history.jsonl`); its short id is `paths.short_id`.
- Tool names in a Grok history are Grok's (spec: Hooks); whatever counts
  "mutation tools" for the briefing counts Grok's too.

## grok-llm

- **files:** src/mnemo/core/llm.py, src/mnemo/core/config.py, tests/unit/test_llm_grok.py
- **exposes:** `PROVIDERS["grok-cli"]`, `resolve(cfg: Optional[Mapping[str, Any]]) -> Provider`
- **consumes:** nothing
- **model:** sonnet
- **effort:** medium

mnemo's own model calls (extraction, briefing, reclassify, gates) on a
machine that may have only `grok` (spec decision 9).

- `grok-cli` calls `grok -p` and returns the same `LLMResponse` and raises
  the same errors `claude-cli` does, so no caller changes.
- An unset `extraction.provider` takes the current CLI's provider
  (`agent_cli.current_cli()`); a named one still wins. Claude's default stays
  `claude-cli` byte-for-byte.
- The model ids in config defaults are Claude's. A Grok call must not be
  sent a Claude id; what it is sent instead is yours, and documented in
  `docs/configuration.md`'s existing provider section only if config keys
  change (that file is not in your boundary otherwise — say so in the PR).
- The helper never fires mnemo's hooks (`GROK_CLAUDE_HOOKS_ENABLED=0` was
  measured to stop them) and never writes a session the user sees.

## grok-install

- **files:** src/mnemo/hosts/grok.py, src/mnemo/hosts/__init__.py, src/mnemo/cli/commands/init.py, src/mnemo/cli/commands/doctor.py, src/mnemo/cli/commands/doctor_checks/grok.py, src/mnemo/install/grok_hooks.py, tests/unit/test_host_grok.py, tests/unit/test_doctor_grok.py
- **exposes:** `HOSTS["grok"]`, `mnemo init --host grok`, `mnemo uninstall --host grok`, `mnemo export --host grok`
- **consumes:** nothing
- **model:** sonnet
- **effort:** high

`mnemo init --host grok` leaves a machine where a Grok session gets mnemo's
MCP server and mnemo's hooks exactly once:

- With Claude installed and wired, Grok already reads both from
  `~/.claude/settings.json` and `~/.claude.json` (spec: Hooks); init says so
  and adds nothing that would make a hook run twice.
- With no Claude, it wires them through Grok's own hook files and MCP config
  for the account's `GROK_HOME`.
- `mnemo doctor` reports, when `grok` is on PATH: its version, which homes
  exist and which are logged in (no secret printed), whether mnemo's hooks
  and MCP are visible to Grok (`grok inspect --json` is the source of truth),
  and whether each home's leader socket path fits the platform limit.
- `mnemo export --host grok` writes the rules file Grok reads (the spec lists
  what Grok loads).
