# Changelog

All notable changes to mnemo will be documented here.
This project adheres to [Semantic Versioning](https://semver.org/).

## [1.1.0] — 2026-09-02

The corrections release. 1.0 proved mnemo could measure itself; the post-1.0
audit then showed what it was measuring: a vault where 98% of rules had a
single source, ~40% were textbook advice the model already knew, and the
top-ranked rule was the extractor's own prompt echoed back. The root cause
was that the extractor never saw the user's words. This release makes
mnemo the corrections layer it claimed to be: briefings quote you verbatim
and verify the quote against the transcript, feedback rules need that quote
to be promoted, re-learned rules reinforce the existing page instead of
minting a duplicate, nothing leaves the machine or spends an LLM call
without opt-in, every learned rule is announced with a one-line veto, and
`mnemo learn` closes the loop inside a single session. The README now says
only what `mnemo status` can print.


### Changed

- **The first extraction of a vault is never debounced.** A fresh vault has no
  extracted pages, and only an extraction produces the pages the count gate
  counts, so `extraction.auto.minNewMemories` held new installs back forever.
  A missing last-run marker now runs immediately — that first pass is what
  shows a new user mnemo works at all.
- **Session briefings count as new material** toward the automatic-extraction
  count gate, alongside memory files. A session whose only product is a
  correction mutates no files but does write a briefing, which is exactly what
  consolidation reads. The time gate is untouched: new material does not buy a
  pass through `extraction.auto.minIntervalMinutes`.
- `generate_session_briefing` gained a `min_mutations` keyword (default `1`,
  unchanged behaviour) so a caller can brief a session that touched no files.
  `mnemo learn` passes `0`.
- **Feedback rules now require evidence.** The session briefing carries a
  `## Corrections` section quoting the user verbatim; each quote is checked
  mechanically against the transcript and fabricated ones are dropped. A
  feedback page reaches `shared/feedback/` only when it cites one of those
  quotes as `evidence:` from one of its own source briefings
  (`confidence: verified`). Everything else is staged as an inferred
  `reference` page in `shared/_inbox/reference/` with `demoted_from: feedback`
  — including feedback-typed pages from Claude Code's own auto-memory, which
  carry no user quote.
- **Extraction reinforces existing rules instead of minting duplicates.** The
  consolidation prompt lists the vault's existing slugs, and a similarity pass
  (weighted Jaccard ≥ 0.32 on stemmed name/description/body AND name-stem
  overlap ≥ 0.27, calibrated on a 1,436-page vault: 38 redirects, 2 judged
  false) redirects a page onto an existing slug when it states the same rule,
  so `source_count` accrues and universal promotion can fire. Never redirects
  onto a dismissed slug.
- The reflex scores the evidence quote as its own BM25F field
  (`reflex.bm25f.fieldWeights.evidence`, default 2.5).
- **The autopilot no longer touches the network without opt-in.**
  `autopilot.network.enabled` now defaults to `false`: self-fix cures still
  apply in place and every run is logged to `.mnemo/autopilot-runs.log`, but
  nothing calls `gh`. If you relied on digest issues, self-fix PRs or outcome
  polling, restore them with
  `{"autopilot": {"network": {"enabled": true}}}`.
- **The first-run backfill is opt-in.** `backfill.autoOnFirstSession` defaults
  to `false`, so a fresh install no longer sweeps your old transcripts through
  the LLM unasked. The first session in a repo with harvestable transcripts
  prints a one-line invitation instead — how many sessions, what it would
  cost, and that `mnemo backfill --dry-run` prices it exactly — shown once per
  repo. Restore the old behaviour with
  `{"backfill": {"autoOnFirstSession": true}}`.

- **Self-fix works on Windows.** The perimeter guard compared backslash
  paths against `shared/`-style prefixes, so every autopilot self-fix PR on
  Windows aborted with a perimeter violation. Paths are now compared in POSIX
  form.

### Added

- **`mnemo learn` / `/mnemo:learn` — teach the vault from this session, now.**
  Correct Claude in your own words, run it, and the rule is live on your next
  prompt. It runs the two stages the `SessionEnd` hook runs, but synchronously
  and in the foreground: a session briefing (with `min_mutations=0`, because
  the session that earns a `mnemo learn` is one where you only *said*
  something) carrying the verified `## Corrections`, then extraction **scoped
  to that briefing alone** — other projects' dirty pages wait for the normal
  end-of-session run rather than being swept into LLM calls you didn't ask
  for. It prints what it read, the briefing and its correction count, and one
  `learned:` line per rule with your own sentence quoted back as the evidence.
  It never opens a PR and never touches the network beyond the LLM calls
  extraction already makes. If another extraction holds the lock it stops and
  says so — that run will pick up this briefing anyway. `--session <id>` learns
  from an earlier session, `--dry-run` names the transcript it would read.
  See [Five minutes](docs/getting-started.md#five-minutes).
- `mnemo reclassify` — grades the existing feedback vault under the same rules
  (keep / demote / merge / archive) with a saved plan, `--apply` (no LLM
  calls), `--limit`, and a byte-exact `--undo`.
- Prompt-echo guard: pages that repeat the extractor's own instructions are
  staged as reference pages instead of being promoted. E-mails, API tokens and
  32-character hex ids are redacted from rule name, description and body
  before writing (RFC 2606 example domains and `git@` remotes are left alone).
- **Learned rules are announced at session start, each with its veto.** A
  `[mnemo learned since your last session]` block lists what extraction
  promoted since this project last looked — up to 5 bullets, each ending in
  `veto: mnemo disable-rule <slug>`, with the source sentence shown for
  `verified` rules. A rule written silently is a rule nobody can correct;
  this is the disclosure half of letting extraction write on its own. Backed
  by `.mnemo/learned.jsonl` and a per-project marker in
  `.mnemo/announced.json`, so nothing is announced twice.
- `mnemo status` grew a **Recently learned** section: the last 10 rules
  relevant to the current project, announced or not — the overflow the
  session-start block points at.
- `mnemo disable-rule` is now a public command rather than an internal one,
  since the session-start announcement hands it to users by name.
- **`mnemo status` prints a `Numbers (last 14 days)` section** — the reflex
  emit rate (from `.mnemo/reflex-log.jsonl`) and `primacy@5` (from the last
  `mnemo recall` run, `.mnemo/recall-report.json`), in the same shape the
  README quotes them:

  ```
  Numbers (last 14 days):
    reflex: injected on 90 of 1041 prompts (8.7%)
    recall: primacy@5 41.7% over 72 cases (mnemo recall, 2026-09-01)
  ```

  Either line — or the whole section — is omitted when its source file is
  missing or holds no row inside the window: a number the tool cannot measure
  is a number the README may not claim, so "no data" is never rendered as
  "0%". New `mnemo.core.numbers` module backs both readers and is fail-safe by
  construction (a missing, truncated, or hand-edited file yields `None`, never
  an exception).

### Changed

- **Plugin slash commands are down to five: `status`, `why`, `doctor`,
  `learn`, `help`.** `/mnemo:open`, `/mnemo:fix`, `/mnemo:statusline` and
  `/mnemo:migrate` are removed — each was a thin wrapper around a CLI command
  that's just as easy to type: `mnemo open`, `mnemo fix`,
  `mnemo statusline --install`, `mnemo migrate-plugin`. The five that remain
  are the ones worth a slash: read state, or teach the vault, without leaving
  the conversation. `mnemo help` (and `/mnemo:help`) still lists every
  command, including the ones that lost their slash.
- **README rewritten** around the corrections layer, an honest comparison to
  plain Claude Code memory and to Obsidian-backed note vaults, the dated
  `Numbers (last 14 days)` figures above in place of prior claims, and the
  opt-in defaults shipped in WS-B (network, backfill) stated as opt-in rather
  than implied always-on. The **"zero network calls" claim is gone** — mnemo
  calls the `claude` CLI for extraction and briefings by design; what's opt-in
  is the autopilot's own network use (`gh`), not LLM calls.

## [1.0.0] — 2026-09-01

The 1.0 gate was never code quality — the plugin distribution, four-platform
binaries, ~2000 tests and the autopilot have been in place for months. What
blocked it was not having an honest number for "does this work?". This release
is the one where that number exists: a 14-day measured window (2026-08-04 →
08-18) plus 14 more days on the fixes below, with every figure produced by
commands that ship in the box (`mnemo recall`, `mnemo recall-sessions`,
`mnemo why`, `mnemo doctor`). The reflex injects on ~7–8% of prompts with the
default thresholds; ranking inside a topic bucket was the weak point and is the
headline change here.

### Added

- **Query-aware ranking in `list_rules_by_topic`.** The tool takes an optional
  `query` — the agent's task text — and reranks the topic bucket with the
  reflex's BM25F scorer: rules that score ≥ 1.0 against the query rise by
  score, everything else keeps its `source_count` order as a floor. Without
  `query` the output is byte-identical to before. The SessionStart injection
  now tells agents to pass their task, so the feature is live end-to-end
  without any configuration.

  Why: in buckets past ~20 rules almost everything ties at `source_count=1`
  (46 of 48 in the largest), so the tiebreak degenerated into alphabetical
  noise and the relevant rule sank to rank ~40. Measured on the real code path
  over big buckets (530 evaluations, 84 cases): primacy@5 16% → 31%,
  primacy@3 10% → 23%.

- **`mnemo recall-sessions`.** A second recall harness, built from the sessions
  each rule was extracted from rather than from the access log. `mnemo recall`
  tops out at the number of `read_mnemo_rule` calls in the log; this one has a
  case for every session that produced a rule, so a ranking change can be told
  apart from noise. It is a delta detector — its absolute numbers are not
  comparable to `mnemo recall`, and the command says so on every run.


- **Cold-start backfill.** A new vault used to inject nothing for weeks,
  because it had nothing to say. mnemo now reconstructs memory from the
  session transcripts Claude Code has been keeping all along
  (`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`), which the existing
  extraction pipeline turns into rules.

  ```bash
  mnemo backfill                  # this repo
  mnemo backfill --all            # every project on the machine
  mnemo backfill --dry-run        # count, projects and a rough token estimate
  ```

  Also `--project NAME`, `--limit N`, `--yes/-y`, and `--retry-failed`. The
  command prints what it's about to read and asks before spending. Failures are
  recorded per transcript and stepped over; an interrupted sweep resumes where
  it stopped; a transcript that fails three times is retired until
  `--retry-failed`. Exit codes: `0` done, `1` some sessions failed, `2` aborted,
  `130` interrupted.

- **Backfilled material always stages for review.** Pages produced from old
  transcripts are stamped `origin: backfill`, and every rule derived from one
  is written to `shared/_inbox/<type>/` — never auto-promoted into `shared/`,
  whatever its source count, and the stamp survives across extraction runs.
  These are the model's reconstruction of sessions nobody watched; they get a
  human before they get to influence anything. `mnemo doctor` lists what's
  staged, and reports a first-run sweep that failed where nobody could see it.

- Config: `backfill.enabled`, `backfill.installCap`,
  `backfill.minFileMutations`, `backfill.autoOnFirstSession`. See
  [docs/configuration.md](docs/configuration.md).

### Fixed

- **Per-project reflex calibration never reached the hook.** The autopilot
  has written `.mnemo/reflex-config.<project>.json` since 0.17 — and nothing
  on the prompt path ever read it. Every prompt ran on the 1.5/2.0 defaults,
  including projects the calibrator had tuned. The hook now merges the project
  file over the global config per key (file > global > defaults; a missing or
  corrupt file changes nothing). The calibrator also used to hand back the
  *defaults* whenever a project's emit rate was inside the 3–12% band, which
  with live wiring would have undone a working calibration the moment it
  worked; it now keeps the current thresholds when in band. Emission receipts
  record the thresholds that admitted them, so `mnemo why` shows the real
  arithmetic per project.

- **Concurrent SessionStarts could crash on the index files.** Both the reflex
  index and the rule-activation index staged writes through a fixed
  `<file>.tmp` sibling, so two sessions starting at once raced: one process's
  rename consumed the other's temp file and the loser raised
  `FileNotFoundError`. Both writers now go through `core/atomic.py` — a unique
  temp file per call, plus retries — the same pattern the session cache
  already used.

- **Autopilot self-fix PRs never carried a diff.** Three stacked defects:
  the branch was cut with `git checkout -b` in the live checkout (stealing
  `HEAD` from whoever was working there), nothing ever committed, and the
  repository was resolved from the process cwd rather than from the vault.
  PRs are now built in a throwaway `git worktree`, committed, and anchored on
  the repository that holds the edited files. Telemetry findings, which change
  no files, are filed as issues instead.

- **The dead-rule sweep archived every rule, at any age.** Autopilot's sweep
  archives rules with no usage signal in the last 180 days, behind an age guard
  meant to protect new rules. The guard read a `created_at:` frontmatter field
  that no writer in mnemo has ever emitted — pages carry `extracted_at:` /
  `promoted_at:` / `extraction_run:` instead — so it parsed to "unknown age"
  for every page ever written, and it was coded to archive on unknown
  (`if created is not None and created > cutoff`). It failed open where it had
  to fail closed. A rule created minutes ago was swept as dead.

  The damage compounded with the promotion bug above: rules that couldn't
  inject couldn't earn a usage signal, so they looked dead and were swept. The
  categories that inject least were wiped fastest. On the maintainer's vault
  that read 942 archived against 113 live, `feedback` fell 15 → 2 in a single
  session, and 7 hand-promoted rules were archived within hours of promotion.

  Age is now taken from fields pages actually carry — `created_at`,
  `promoted_at`, `extracted_at`, `extraction_run`, then filesystem mtime — and
  a page whose age cannot be established is **never** archived. Nothing was
  deleted: archived rules are in `shared/_archive/` and can be moved back. `mnemo doctor` and the
  docs both tell you to review a staged page and move it into
  `shared/<type>/`. Promotion is a plain `mv` — there is no promote command,
  and nothing rewrites the frontmatter — so the page kept the `needs-review`
  tag it was written with. The visibility filter read that tag as "still a
  draft" and hid the page from injection, the MCP tools, the reflex index and
  the HOME dashboard. Every keeper anyone ever promoted by hand was silently
  doing nothing; on the maintainer's own vault that was 7 rules.

  **Location is now the only authority on draft-ness**: under
  `shared/_inbox/` it is a draft, under `shared/<type>/` it is live. The tag
  is left as a harmless marker (`topic_tags` still strips it, so it never
  shows up as a topic). `stability: evolving` is unchanged and still hides a
  page wherever it lives.

  ⚠ **Behaviour change for existing vaults.** Rules you promoted months ago
  and assumed were live will now actually become live, on the first session
  after upgrading. If some of them shouldn't be, move them back under
  `shared/_inbox/<type>/` or delete them — see
  [docs/troubleshooting.md](docs/troubleshooting.md).

### Removed

- **`extraction.preferAPI`**, a documented config key that nothing has ever
  read. Every LLM call goes through the `claude` CLI and uses whatever auth
  that CLI already has; there was no code path the flag could switch. It is
  gone from the defaults and from the config reference. A config file that
  still carries the key keeps merging harmlessly — no migration needed.

### ⚠ Upgrade note — this spends LLM calls on your next session

**Read this before upgrading.** The "have we done the first-run sweep?" marker
defaults to *not done* for every vault that existed before this release. So
your next session is treated as a first session: mnemo spawns a background
sweep of **the repo that session is in**, harvesting up to `backfill.installCap`
(**20**) of its most recent transcripts.

What that costs: **up to 20 calls to the `claude` CLI**, one per session,
using `extraction.model` (Haiku by default). On a Pro/Max subscription that
draws on your subscription with no per-token charge; on API-key auth it is
billed. It happens once per vault — not once per repo, not once per session —
and only for the repo you happen to be in. Sessions that touched no files are
skipped without a call.

It runs detached, so it will not slow your session down, and its output goes
nowhere — `mnemo doctor` is where you find out how it went.

To upgrade without it ever running, put this in
`~/mnemo/mnemo.config.json` **first**:

```json
{ "backfill": { "autoOnFirstSession": false } }
```

`mnemo backfill` still works by hand after that. To disable backfill entirely,
including the command:

```json
{ "backfill": { "enabled": false } }
```

If it already ran and you'd rather it hadn't: the pages it wrote are in
`bots/<repo>/memory/` alongside the `origin: backfill` stamp, and anything
extracted from them is sitting in `shared/_inbox/` — not in `shared/` — so
deleting them is a local, reversible cleanup.

## [0.17.2] — 2026-08-01

The release that actually ships binaries. 0.17.0 and 0.17.1 were both blocked
before publishing them; this is the first tag whose GitHub Release carries the
four platform builds the plugin install needs.

### Fixed

- **Intel macOS built on a runner that never started.** The `macos-13` job sat
  queued indefinitely on two consecutive releases — that runner image is on
  its way out. Switched to `macos-15-intel`. PyInstaller cannot
  cross-compile, so an Intel build needs an Intel runner, and dropping the
  target would leave Intel Mac users with a silent no-op: the launcher fails
  open when its asset is missing.

### Added

- `workflow_dispatch` on the release workflow, so the binary matrix can be
  exercised without burning a version number. All three publishing jobs are
  guarded on the ref being a tag, so a manual run builds and stops.

## [0.17.1] — 2026-08-01

Fixes the 0.17.0 release itself. That tag published to PyPI and npm but
shipped **no binaries**, so the plugin install it was cut for did not work.

### Fixed

- **The Windows binary job failed on a missing `shasum`.** Neither `shasum`
  nor `sha256sum` is guaranteed in the Git Bash that GitHub runs
  `shell: bash` under on Windows. Checksums are now generated with Python,
  which every one of these jobs already sets up, in a format byte-identical
  to `shasum -a 256` so `bin/launch` parses it unchanged.
- **The release published before it built.** `publish-pypi` ran first and
  unconditionally, so when the Windows job failed, PyPI and npm were already
  advertising a version whose release had no binaries — a version number,
  once taken, cannot be reused. Publishing is now gated behind the binary
  build: the fallible step runs first, the irreversible step second. Tests
  pin the ordering so it cannot quietly regress.

## [0.17.0] — 2026-08-01

This is the release that makes the plugin install work: the launcher fetches
its binary from the GitHub Release matching the plugin's version, and 0.16.0
predates the binary build job.

### Added

- **Install with no terminal.** mnemo is now a Claude Code plugin:

  ```
  /plugin marketplace add xyrlan/mnemo
  /plugin install mnemo@mnemo-marketplace
  ```

  No Python, no Node, no PATH setup. It ships as a self-contained binary that
  the plugin fetches for your platform on first use, verified against a
  published SHA-256. npm and PyPI keep working for dotfile setups and CI.
- Standalone binaries for macOS (arm64/x64), Linux x64, and Windows x64,
  built and attached to each GitHub Release.
- `mnemo statusline --install` / `--remove`. Plugins cannot declare a status
  line, so the heartbeat becomes an explicit opt-in rather than a reason to
  open a terminal. Still additive — any status line it replaces is preserved.
- `/mnemo:migrate` (`mnemo migrate-plugin`), for users who ran `mnemo init`
  before installing the plugin. Both sets of hooks otherwise stay live and
  every session gets doubled capture, injection, and enforcement.
- `mnemo hook <event>`, the binary-invocable equivalent of
  `python -m mnemo.hooks.<event>`.

### Fixed

- **The vault was never scaffolded under a plugin install.** No `mnemo init`
  runs, and the hooks only created the directories they touched — so there was
  no `HOME.md`, no config file, and no `shared/` for extracted rules to land
  in. `SessionStart` now scaffolds when `HOME.md` is absent.
- **`mnemo status` reported "settings.json missing" to plugin users** — i.e.
  "not installed" — because hook health was read only from `settings.json`,
  where a plugin legitimately has nothing. It now reports the plugin's hooks.
- **`npx @xyrlan/mnemo install` bailed when `python3` was absent even with
  `uv` installed.** It checked for Python *before* choosing an installer, so
  the one tool that provisions its own CPython — and would therefore have
  worked — was never reached.
- Hook-ownership detection no longer matches a bare `"mnemo"` substring, which
  counted any unrelated command sitting under a path containing "mnemo".

### Changed

- Docs rewritten around the plugin install, and corrected against the code:
  `getting-started.md` documented 2 hooks where 4 ship, `configuration.md`
  described the v0.1 key set, and both used a `/mnemo <cmd>` slash syntax that
  never existed. Config keys in the reference tables are now fully qualified
  so any row can be copied straight into `mnemo.config.json`. New
  `docs/obsidian.md`; the v0.1 backlog moved to `docs/archive/`.
- The vault templates shipped into every new vault said background features
  were "off by default". They have been on since 0.15.0.
- A test suite now checks the docs against the code: every referenced command
  and config key must exist, and internal links must resolve.

## [0.16.0] — 2026-08-01

Three months of fixes that were merged to `master` but never released: the
0.15.0 tag was the last one cut, so PRs #86–#92 never reached PyPI or npm.
This release ships them and closes the gaps that let the drift happen.

### Fixed

- **Windows: hooks and statusLine were wired with backslash paths.** Claude Code
  dispatches hook commands through bash/Git Bash, which eats `\`, so the
  installed commands could fail to run. `mnemo init` now emits POSIX-style
  paths for the hook and statusLine commands (#88).
- **Autopilot reflex calibration was measured against the wrong denominator.**
  `emit_rate` divided by every prompt seen, including ones skipped before
  scoring ever happened (`index_missing`, `below_min_tokens`). That deflated
  the observed rate 2–4× and meant genuinely chatty projects never got tuned
  down. It now divides by *eligible* prompts, and the min-sample guard counts
  eligible prompts too (#89).
- **Autopilot's pytest gate spawned a bare `pytest`,** which resolved against
  whatever happened to be on PATH rather than the interpreter running mnemo.
  It now spawns via `sys.executable` (#90).
- **Autopilot self-fix healed source-path hygiene** — machine-absolute paths in
  rule provenance are relativized, and sources whose briefing moved are
  relocated — and the universal-promotion signal that fired on nearly every
  rule was dropped as noise (#91).
- **Universal promotion was blocked and project rules went unindexed** in the
  extraction and activation paths (#86, #87).

### Changed

- `mnemo --version`, the landing card, and the MCP server's advertised version
  now resolve through a single helper (`mnemo._version.resolve_version`). All
  three previously inlined the same `importlib.metadata` lookup, and the MCP
  server didn't do the lookup at all — it reported a hardcoded `0.8.0`. The
  fallback is now the baked-in `__version__` rather than the literal
  `"unknown"`, which matters for builds with no distribution metadata to read.

### Release tooling

These are the reasons 0.16.0 was three months late; each is now enforced.

- `tools/sync_npm_version.py` also rewrites `PIN_SPEC` in
  `npm/lib/bootstrap.js`. It was a hand-edit in the release commit, and
  forgetting it ships an npm wrapper that installs the *previous* minor.
- `tools/sync_plugin_manifest.py` also bumps `.claude-plugin/marketplace.json`,
  which nothing synced and which had drifted twelve minors behind (0.4.0).
- CI now runs the npm test suite and fails when the generated
  `.claude-plugin/` manifests are stale. Previously `npm test` ran only during
  publish — *after* the PyPI job had already succeeded — so an npm-side
  regression would leave the two registries on different versions.

## [0.15.0] — 2026-05-04

### Changed (default behaviour)

- **Autopilot is ON by default.** Fresh vaults activate the autopilot loop
  (Tier 0 digest, Tier 1 self-fix, Tier 2 BM25 / reflex tuners, Tier 3
  end-of-session proposer) without `mnemo autopilot on`. Vaults that
  previously ran `mnemo autopilot off` keep that explicit choice — the
  on-disk state file always wins over the new no-file default.
  Disable with `mnemo autopilot off` (#80).

### Added

- `mnemo help --all` flag surfacing 7 advanced/maintenance commands
  (`telemetry`, `recall`, `migrate-worktree-briefings`, `dedup-rules`,
  `disable-rule`, `list-enforced`, `regen-graph-edges`) that are now
  hidden from the default help listing (#83).
- `mnemo init` ends with a richer summary card: vault path, verify
  command, and live autopilot state — so users learn how to toggle the
  new default (#82).
- Bare `mnemo` (no subcommand) now prints a 5-line orientation card
  instead of the full argparse dump. `mnemo help` and `mnemo --help`
  still show the full listing (#82).

### Fixed

- **Python 3.14 argparse regression:** `help=argparse.SUPPRESS` on a
  subparser stopped hiding the entry (rendered the literal
  `==SUPPRESS==` string instead). Switched the four internal
  subparsers (`briefing`, `mcp-server`, `statusline`,
  `statusline-compose`) to omit `help=` entirely, which hides them on
  every supported Python version (#82).
- **macOS `pip --user` install hint:** the npm bootstrap told users to
  add `~/.local/bin` to PATH, but on macOS pip-user lives in
  `~/Library/Python/<X.Y>/bin`. Now the hint shells out to
  `python3 -m site --user-base` for the authoritative path on every
  platform (#81).
- Seven test regressions on master, surfaced after the default flip
  and the Tier 3 git-signal subprocess work (#84).

## [0.11.0] — 2026-04-23

### Breaking

- **Rule schema:** `enforce.deny_command` now requires a paired `deny_pattern` regex.
  Bare `deny_command: "git push"` is rejected at index load. Run `mnemo doctor`
  to surface affected rules; fix by adding a `deny_pattern` that narrows the match,
  or remove the enforce block entirely.
- **Rule-activation index schema:** bumped from v3 to v4 to force a transparent
  rebuild that populates the new `path` field on every entry. No user action required.

### Added

- `mnemo list-enforced` — audit every rule with an `enforce:` block (path + tool +
  trigger + reason). One-shot way to see what can hard-block your tool calls.
- `mnemo disable-rule <slug>` — flip `runtime: false` on a rule's frontmatter
  without touching its body. Suggested by the PreToolUse block message.
- PreToolUse deny envelope now includes the offending rule's path and a
  disable-hint, so users can fix the block without grepping the vault.

### Changed

- Auto-promoted pages have their `enforce:` block stripped (flagged with
  `promoted_without_enforce: true` in frontmatter + review note in the body).
  Manual promotion and hand-authored rules are unaffected. `mnemo doctor`
  surfaces stripped rules so you can review and re-add manually if safe.
- Extractor prompt tightened: the LLM now emits `enforce:` only when the
  source briefing contains explicit blocking intent ("never allow",
  "always refuse", "the hook should block"). A command in backticks is
  no longer sufficient justification.

### Migration

Existing rules with bare `deny_command` will fail to load after upgrade. After
pulling:

1. Run `mnemo fix` — rebuilds the rule-activation index at v4 (adds `path` field).
2. Run `mnemo doctor` — the `rules` check lists every rule rejected by the new
   validator, by absolute path.
3. Fix each offender one of three ways:
   - Add a `deny_pattern` regex that narrows the block (preferred).
   - Remove the `enforce:` block — the rule stays advisory.
   - Run `mnemo disable-rule <slug>` — sets `runtime: false` without edits.

### Skipped versions

v0.9.x and v0.10.x shipped in master but were never reflected in
`pyproject.toml` (both remained at 0.8.0). This release jumps 0.8 → 0.11
to align the package version with the already-shipped feature set. The
v0.10 and v0.9 changelog sections below document the shipped content and
remain `[Unreleased]` from the perspective of PyPI tagging.

## [Unreleased] — v0.10.0

### Added

- **Session handoff injection.** SessionStart now appends the most recent briefing's body (under `[last-briefing …]`) to the `mnemo://v1` envelope when `briefings.injectLastOnSessionStart` is true (default). Claude wakes up with the previous session's handoff context already in scope.
- **Worktree-aware canonical agent.** New `agent.resolve_canonical_agent` follows `.git` worktree pointers to the main repo, so all worktrees of a repo share a single briefing pool. Briefing writer (SessionEnd) now uses canonical naming.
- **`mnemo migrate-worktree-briefings`** — one-shot CLI to relocate orphan worktree briefings written before the canonical-agent change. Uses a name-prefix heuristic; always `--dry-run` first.
- **`mnemo doctor`** now flags orphan worktree briefing dirs and suggests the migration command. (Silent for early upgraders who haven't written a canonical briefing yet.)
- **Cost telemetry.** `llm.call()` invocations and SessionStart injections both write structured entries to `mcp-access-log.jsonl`. `mnemo telemetry` now reports per-purpose token totals + estimated USD via a hard-coded pricing table.

### Changed

- `_build_injection_payload` accepts `inject_briefing: bool` parameter (default `False` for backwards compat with direct callers; SessionStart hook passes `True` by default via config).
- `access_log_summary.summarize` returns two new top-level keys: `llm_cost`, `injection_stats`. Existing keys unchanged.

## [Unreleased] — v0.9.0

### Changed

- `rule-activation-index` schema bumped from v2 to v3. The v0.8.x
  `file_stem` field was added without a version bump, so existing
  v2 indexes silently fall back to slow glob scanning. v3 forces
  a transparent rebuild on first load (already-load-bearing
  auto-rebuild path: `load_validated_json` returns `None` on
  schema mismatch; SessionStart and extract hooks call
  `build_index` whenever `load_index` returns `None`). First run
  after upgrade takes a few seconds longer; nothing else visible.
  ([refactor roadmap PR E](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

### Removed

- `mnemo.core.mcp.counter` v0.8 backwards-compat shim. Importers must use
  `mnemo.core.mcp.session_state` directly. The shim was scheduled for
  v0.9 removal in the v0.8 CHANGELOG. ([refactor roadmap PR D](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

### Internal

- `mnemo.core.rule_activation` monolith (849 LOC) split into a package:
  `parsing.py`, `globs.py`, `matching.py`, `index.py`, `activity_log.py`,
  plus a back-compat shim at `__init__.py`. The pre-v0.9 import surface
  is preserved. `parse_enforce_block` + `parse_activates_on_block` +
  `_describe_*_error` collapsed into a single `parse_block(kind, fm)`
  walker (the two thin wrappers stay for back-compat; the two describe
  helpers are deleted). `_is_universal` promoted to public `is_universal`
  (single in-tree consumer at `reflex/index.py` updated atomically; no
  deprecation window). `build_index` orchestrator decomposed via a new
  `_build_rule_entry` helper (138L → <30L).
  ([refactor roadmap PR G](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))
- `mnemo.core.extract.inbox` monolith (717 LOC) split into an 8-module
  package: `io.py`, `paths.py`, `types.py`, `state_io.py`, `rendering.py`,
  `dedup.py`, `apply.py`, `branches/{auto_promoted,inbox_flow,upgrade}.py`,
  plus a back-compat shim at `__init__.py`. The pre-v0.9 import surface is
  preserved. Five duplication clusters consolidated:

    - D1: `vault_root / "shared" / type / f"{slug}.md"` inlined at 5 sites
      → all routed through `paths._inbox_path` / `paths._promoted_path`.
    - D2: `.proposed.md` sibling construction at 3 sites → `paths._sibling_path`.
    - D3: `"sha256:" + hashlib.sha256(...)` one-liners at 3 sites → new
      public `content_hash(source)` in `inbox/io.py` (polymorphic over
      `Path` / `str` / `bytes`).
    - D4: 5 duplicate fresh-write `StateEntry` blocks → new
      `StateEntry.mark_written(*, run_id, new_hash, source_files,
      source_hash, status=None)` method in `extract/scanner.py`.
    - D5: `SCHEMA_VERSION = 2` duplicated across `inbox.py:25` and
      `scanner.py:39` (ExtractionState dataclass default) → scanner uses
      a `field(default_factory=...)` that defers to
      `inbox/state_io.py::SCHEMA_VERSION` (single source of truth;
      function-level import side-steps the circular dependency).

  New public helpers `atomic_write` and `content_hash` replace the
  underscore aliases `_atomic_write` / `_file_hash`, which remain as
  back-compat re-exports with `DeprecationWarning` (removal scheduled
  for v0.10). `apply_pages` internal dispatch converted to a
  table-driven `(status_predicate, handler)` map (OCP fix). The
  96-line `_apply_inbox` body split into three handlers
  (`_handle_no_entry`, `_handle_dismissed`, `_handle_promoted`,
  `_handle_inbox_status`); the 77-line `_apply_auto_promoted` split
  into smaller per-status helpers. `extract/promote.py` migrated to
  the new `atomic_write` / `content_hash` names. `extract/promote.py`
  mutation sites at lines 80-86 and 95-99 intentionally NOT migrated
  to `mark_written` — they have a divergent shape (no `last_sync`
  update), and forcing a unified API there would change v0.8
  behavior for direct-promotion entries. Deferred:
  `rule_activation.index._atomic_write_bytes` consolidation into a
  shared `io_utils.py` module (follow-up nit-PR).
  ([refactor roadmap PR I](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))
- `mnemo.core.extract.prompts` monolith (529 LOC) split into a `prompts/`
  package with a `templates/` sub-package. Three near-identical
  `build_{feedback,user,reference}_prompt` builders unified into a single
  `build_consolidation_prompt(kind, files, *, vault_root=None)` dispatching
  on a kind→(label, cluster_clause, few_shot) table; thin wrappers preserve
  existing call-sites. `build_briefing_prompt` signature changed — now
  accepts a pre-flattened `transcript: str` rather than `events: list[dict]`
  (SRP fix: event-parsing moved to a new `mnemo.core.transcript` module).
  In-tree callers updated. Pre-v0.9 import surface preserved via the
  package's `__init__.py` shim, including the three underscore-private
  `_FEW_SHOT_*` constants that PR F1's schema regression test accesses.
  ([refactor roadmap PR F2](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))
- `mnemo.cli` monolith (1294 LOC) split into a `cli/` package:
  `parser.py` (argparse + COMMANDS registry + @command decorator),
  `runtime.py` (main + _resolve_vault + _run_open), `commands/*.py`
  (one module per command: init, status, doctor, extract, briefing,
  recall, telemetry, statusline, misc), `commands/doctor_checks/*.py`
  (one module per concern: activation, fidelity, rules, reflex, misc),
  `_helpers/` (absorbs the PR-A `cli_helpers.py`). `cmd_doctor` converted
  to an OCP-compliant `(name, check_fn)` registry — adding a new check
  is now a new row, not an edit to `cmd_doctor`. Pre-v0.9 import
  surface preserved via the package's `__init__.py` shim (re-exports
  `main`, `COMMANDS`, `_resolve_vault` — the three names pinned by the
  public API surface test plus the single symbol the 10 in-repo
  monkeypatches target). ([refactor roadmap PR H](docs/superpowers/plans/2026-04-19-refactor-roadmap.md))

## v0.8.0 — 2026-04-19 — Prompt Reflex

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

- `reflex.enabled = true` by default in v0.8.0 stable. Kill switch: set
  `"reflex": {"enabled": false}` in `mnemo.config.json`.
- `reflex` config block scoped to the knobs that are actually wired:
  `enabled`, `maxEmissionsPerSession`, `thresholds`, and `bm25f`. Additional
  knobs (maxHits, previewChars, dedupeTtlMinutes, log.maxBytes,
  debug.logRawPrompt) are deferred until v0.9 when they'll be wired.

## v0.7.0 — 2026-04-18

### Breaking

- `scope="project"` on MCP retrieval (`list_rules_by_topic`,
  `read_mnemo_rule`, `get_mnemo_topics`) now returns **local + universal**
  rules. Pass `scope="local-only"` to preserve v0.6.2 "strict local" behaviour.
- Rule-activation index schema bumped to v2. Existing v1 indexes load as
  `None` and are regenerated automatically on the next SessionStart.
- Index top-level keys `enforce_by_project` and `enrich_by_project` are
  removed. Consumers read the unified `rules` table plus the derived
  `by_project` / `universal` lookup tables, or use the new iterators
  `iter_enforce_rules_for_project` / `iter_enrich_rules_for_project`.

### Note on MCP fallback

If MCP is invoked *before* the first SessionStart of v0.7.0 has rebuilt the
index, retrieval falls back to a glob+parse walk of `shared/{feedback,user,reference}/`.
In that fallback path, **universality is not evaluated** — every rule is
treated as local (equivalent to `scope="local-only"`). Running a SessionStart
(or `mnemo extract`) after upgrade is all that's needed to enable the full
v0.7 semantics.

### Added

- Automatic **universal promotion** at `distinct_projects >= 2`
  (configurable via `scoping.universalThreshold`).
- Structured `mnemo://v1` SessionStart injection envelope with per-scope
  topic lines and a `injection.maxTopicsPerScope` cap (default 15).
- `mnemo doctor` reports universal promotion health.
- `shared/project/` pages now carry `runtime: false` to document their
  human-surface-only role.

### Changed

- MCP retrieval now reads the unified index for O(1) lookups; glob+parse is
  kept as a fallback for missing/stale indexes.
- SessionStart rebuilds the index whenever `injection.enabled` is true
  (in addition to the existing enforcement/enrichment triggers).

## v0.6.0 — 2026-04-16 — Loop enabled by default

**Changed**
- Defaults flipped from `false` → `true` for `extraction.auto.enabled`,
  `briefings.enabled`, `injection.enabled`, and `enrichment.enabled`.
  `mnemo init` now produces a working product from session one, instead
  of an inert scaffold awaiting manual JSON configuration.
- `enforcement.enabled` was already `true` since v0.5; unchanged.

**Backward compatibility**
- `_deep_merge` in `core/config.py` preserves explicit `enabled: false`
  values in existing user configs. Users who had opted out of specific
  features continue to see opt-out behavior with no action required.

**Rationale**
- The opt-in pattern shipped since v0.3 ("ship dark, dogfood, then flip")
  imposed JSON-editing friction without safety benefit during solo
  dogfood, and contradicted the project tagline ("the Obsidian that
  populates itself"). Flipping defaults aligns the zero-config experience
  with the product promise.

**Migration**
- Users who wanted the features on: no action needed — defaults now match
  your existing explicit config.
- Users who wanted the features off: add `"enabled": false` blocks to
  `~/mnemo/mnemo.config.json`. See README "Runtime flags".

**Tests**: 779 passing, 2 skipped (opt-in E2E only).

## v0.5.0 — 2026-04-15 — MCP injection (the loop closes)

**Added**
- **MCP stdio server** (`src/mnemo/core/mcp/`): a long-running JSON-RPC 2.0
  process exposing three read-only tools to Claude Code:
  - `list_rules_by_topic(topic)` — returns slugs sorted by source_count desc
    so multi-agent synthesized rules surface first
  - `read_mnemo_rule(slug)` — returns the full body + frontmatter for a slug
  - `get_mnemo_topics()` — returns the union of all topic tags in the vault
  Hand-rolled stdlib-only (no `mcp` SDK dependency, consistent with mnemo's
  `dependencies = []` policy). Both tools apply the v0.4 shared filter from
  `core/filters.py` so machine view and the HOME dashboard stay in lockstep.
  Project pages are excluded by design: they have no topic tags by
  construction and their sources are already in Claude's auto-memory.
- **SessionStart MCP topic injection**: when `injection.enabled=true`, the
  SessionStart hook emits a `hookSpecificOutput.additionalContext` JSON
  envelope listing the vault's topic tags plus a one-line instruction
  telling Claude to call `list_rules_by_topic` + `read_mnemo_rule` BEFORE
  writing code when the task matches a known topic. ~120 tokens per session
  regardless of vault size.
- **`mnemo init` registers the MCP server in `~/.claude.json`** under
  `mcpServers.mnemo`. `mnemo uninstall` removes it. Fully idempotent.
- **New config flag `injection.enabled`** (default `false`, opt-in per the
  v0.3 conservative pattern). Flip to `true` in `~/mnemo/mnemo.config.json`
  to activate after dogfood validates the injection mechanism in your vault.
- **Hidden CLI subcommand `mnemo mcp-server`**: stdio entry point referenced
  from `~/.claude.json`. Not surfaced in `mnemo --help`.

- **Status line integration**: `mnemo init` now wires an additive
  `statusLine` composer into `~/.claude/settings.json`. Output looks like
  `mnemo mcp · 9 topics · 7↓ today` — topic count from your vault plus
  the number of times Claude has consulted mnemo via MCP today (counter
  resets daily, atomic write, lives in `<vault>/.mnemo/mcp-call-counter.json`).
  If you already had a custom statusLine, mnemo **does not overwrite it** —
  the composer wraps your original command and concatenates outputs with
  ` · `. Your original is preserved in `<vault>/.mnemo/statusline-original.json`
  and restored by `mnemo uninstall`. `mnemo doctor` warns if you edit
  settings.json manually and drift away from the composer.

**Internal**
- Injection mechanism de-risked on 2026-04-15 via a throwaway prototype that
  proved `hookSpecificOutput.additionalContext` injects into interactive
  Claude sessions, not just `claude --print` one-shot mode. The prototype is
  removed in this release.

**Tagline status**: "Capture → Present → Inject" is now complete. v0.3
shipped capture, v0.3.1 shipped dense input (briefings), v0.4 shipped
auto-presentation (HOME dashboard + tags), v0.5 ships auto-injection.

## v0.4.0 — 2026-04-14 — HOME dashboard + dimensional tags

**Added**
- **HOME.md dashboard**: `run_extraction` now regenerates a managed block inside
  `HOME.md` at vault root at the end of every run. The block groups consumer-visible
  `shared/` pages by trust tier (cross-agent synthesized first, auto-promoted
  direct reformats second) AND by topic tag. Wikilinks are path-qualified
  (`[[shared/<type>/<slug>]]`). The rest of `HOME.md` is user-owned — mnemo only
  touches content between `<!-- mnemo:dashboard:begin -->` and `<!-- mnemo:dashboard:end -->`.
- **Dimensional tags**: the extraction JSON schema gains a `tags: [topic1, topic2]`
  field. Each prompt builder now receives `vault_root` and injects a per-page-type
  "Existing vault tags" hint into the prompt so the LLM reuses the established
  vocabulary instead of inventing synonyms. Tags persist into frontmatter as a
  unified list alongside the existing system marker (`auto-promoted` /
  `needs-review`).
- **Shared filter module** (`core/filters.py`): single source of truth for
  "consumer-visible" pages — three-condition predicate (path, needs-review tag,
  stability). Both the v0.4 HOME dashboard and the planned v0.5 MCP tools will
  call the same function so human and machine views stay in lockstep. Ships with
  `collect_existing_tags(vault_root, page_type)` for the controlled-vocabulary
  hint and a minimal frontmatter parser for the exact YAML shape mnemo writes.
- **`mnemo doctor` legacy-wiki warning**: surfaces `wiki/sources/` and
  `wiki/compiled/` as orphaned v0.3 directories with a note that the next
  `mnemo extract` will auto-delete them.

**Changed**
- **`dedupe_by_slug` bug fix**: pre-v0.4 dedupe silently dropped both `stability`
  and newly-added `tags` on the floor when merging cross-chunk slug collisions.
  It now preserves `stability` from the chosen cluster and unions `tags` across
  all merged pages.
- **HOME.md template** rewritten: dashboard block skeleton at the top (after
  frontmatter), "Tier 3 — Curated wiki" section removed, `/mnemo promote` and
  `/mnemo compile` removed from quick commands. The user-editable welcome
  content sits below the managed block.
- **README template** drops references to `wiki/sources/`/`wiki/compiled/`;
  documents `HOME.md` as the landing page with an auto-generated dashboard region.

**Removed**
- **`/mnemo promote` and `/mnemo compile` CLI commands** — the manual wiki
  promotion + compilation flow is gone. The dashboard auto-regenerates as a side
  effect of `mnemo extract`, which is a superset. `cmd_promote`, `cmd_compile`,
  `core/wiki.py`, and `tests/unit/test_wiki.py` are deleted entirely.
- **`wiki/sources/` and `wiki/compiled/` directories**: scaffold no longer
  creates them; the first v0.4 `mnemo extract` on an existing vault auto-deletes
  both (and the empty `wiki/` parent if nothing else lives there). Plugin
  manifest (`.claude-plugin/plugin.json`) loses the `promote` and `compile`
  command entries.

**Invariants preserved**
- Zero new runtime dependencies — stdlib only.
- Dashboard failures never abort extraction (wrapped in try/except around an
  `OSError` boundary).
- `shared/<type>/**` remains sacred — extraction writes there, nothing else does.
- Dry-run extraction never touches `HOME.md` or deletes legacy directories.

**See:** `docs/superpowers/plans/` for the full v0.4 plan and
`project_mnemo_v0.4_direction.md` for the "Shared filter specification".

## v0.3.1 — 2026-04-14 — briefings + stability + force-wipe

**Added**
- Per-session briefings module (`core/briefing.py`) generates a structured
  shift-handoff markdown file at every session end, gated on `briefings.enabled`.
- `ExtractedPage.stability: {stable|evolving}` field populated by the LLM and
  persisted into frontmatter; the feedback system prompt teaches the LLM to
  emit `evolving` on hedging language.
- Scanner routes briefings as feedback input so the extraction pipeline mines
  the "Decisions made" and "Dead ends" sections.
- `mnemo extract --force` wipes `shared/_inbox/{feedback,user,reference}/*.md`
  to kill slug-drift duplicates from prior force runs.

**Changed**
- `minNewMemories` default lowered from 5 to 1 — with briefings producing one
  dense file per session, a single new file is enough signal for the background
  auto-spawn.

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
