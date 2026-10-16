---
feature: grok-host-w1b
created: 2026-10-16
verdict: parallel
---

Wave 1 of the Grok round, its grok-install piece re-dispatched with a wider boundary. Read `design/specs/2026-10-16-grok-host.md` first:
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

<!-- Re-dispatch of wave 1's grok-install (child 61207ea6 stopped before writing
code, correctly): `mnemo export` and init's rules-file step call
`core.export.writers.target_for` directly, not `Host.export_target`, and that
table knew only claude/cursor/codex. writers.py had no owner in either wave;
it is this piece's now. Everything else is as in the wave 1 contract. -->

## grok-install

- **files:** src/mnemo/hosts/grok.py, src/mnemo/hosts/__init__.py, src/mnemo/core/export/writers.py, tests/unit/test_export_writers_grok.py, src/mnemo/cli/commands/init.py, src/mnemo/cli/commands/doctor.py, src/mnemo/cli/commands/doctor_checks/grok.py, src/mnemo/install/grok_hooks.py, tests/unit/test_host_grok.py, tests/unit/test_doctor_grok.py
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
