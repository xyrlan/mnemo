# Follow-ups #114–#120 — design

Date: 2026-09-02. Scope: the seven issues filed from the corrections-layer
reviews (PRs #109–#112). Four PRs, one writer at a time, TDD, double review.

| PR | Issues | Theme |
|----|--------|-------|
| A | #117, #118, #120 | hygiene: test isolation, repo `.mcp.json`, doctor archive walk |
| B | #115, #116 | ops: circuit-breaker visibility, briefing retention |
| C | #114 | rule identity: `slug:` migration |
| D | #119 | reclassify `keep` bar |

Real-vault facts these designs rest on (measured 2026-09-02):

- 1648 live pages under `shared/` (excluding `_archive`, `_inbox`); 100% carry
  `name:`, 100% have `name != file stem`, 0 carry `slug:`.
- 1489 pages under `shared/_archive/` (reclassify originals), 9 in `_inbox`.
- 299 briefings across 14 agents, 2.6 MB total.
- `.errors.log`: 53 lines, all `session_start.injection BrokenPipeError` from
  test noise.
- Reclassify plan `~/mnemo/.mnemo/reclassify-plan.json`: 1436 verdicts, 61 keep.

---

## PR A — hygiene

### #117 test isolation

**Problem.** `tests/conftest.py` has a `tmp_home` fixture but it is opt-in.
Six tests call a hook `main()` with the real `HOME`:
`test_session_start_cleanup.py:17`, `test_session_start_autoscaffold.py:27`,
`test_session_start_first_run_notice.py:225`,
`test_hooks_worktree_canonical.py:90,172`,
`test_hook_pre_tool_use_reflex_integration.py:19`. Config resolution
(`core/config.py:127-133`) falls back to `~/mnemo/mnemo.config.json`, and
`paths.vault_root` expands `~`, so those tests read the developer's vault and
write to its `.errors.log`, tripping the real circuit breaker.

**Design.**

1. `tmp_home` becomes `autouse=True`. It sets `HOME`/`USERPROFILE`, patches
   `pathlib.Path.home` to return the tmp home, and sets `MNEMO_CONFIG_PATH`
   to `<tmp_home>/mnemo/mnemo.config.json` **only when the env var is not
   already set by the test** (tests that set their own keep it; the fixture
   runs first because autouse fixtures are instantiated before
   test-requested ones, and a later `monkeypatch.setenv` overrides it).
   The default config file is not created: `load_config` must keep working on
   a missing path (it already does — defaults).
2. New autouse fixture `_real_vault_guard`: at session start (module import)
   record the real home from `os.environ["HOME"]` before any patching; per
   test, snapshot `(size, mtime_ns)` of `<real_home>/mnemo/.errors.log` and
   the newest mtime under `<real_home>/mnemo/.mnemo/` and
   `<real_home>/.claude/projects/` (shallow: directory mtimes only, no walk).
   After the test, if any changed, `pytest.fail("test touched the real
   vault: ...")`. Tests marked `recall` opt out (they run against the real
   vault by design). Cost: a handful of `stat` calls per test.
3. Every test that breaks under the autouse home is fixed to build its own
   tmp vault + config (the `_run_hook` pattern in
   `test_hook_session_start_backfill.py`). Expect the six above, possibly a
   few that read `~/.claude/settings.json`.

**Not in scope.** Removing `Path.home()` calls from `src/` — the guard makes
them safe under test; production behaviour is unchanged.

### #118 repo `.mcp.json`

**Problem.** The repo root doubles as the plugin root; `.mcp.json` says
`"command": "${CLAUDE_PLUGIN_ROOT}/bin/mnemo.cmd"`. Claude Code expands
`${CLAUDE_PLUGIN_ROOT}` for plugins, but when a developer opens the repo as a
project the variable is unset, the literal string is spawned (ENOENT), and the
project entry shadows the working user-scope server of the same name (project
scope wins on collision).

**Design.**

1. `.mcp.json` command becomes
   `"${CLAUDE_PLUGIN_ROOT:-${CLAUDE_PROJECT_DIR}}/bin/mnemo.cmd"`. Claude
   Code's project `.mcp.json` supports `${VAR:-default}` and
   `${CLAUDE_PROJECT_DIR}`. Plugin installs still resolve to the plugin root.
   Verified locally with `claude mcp list` run inside the repo (must show
   `mnemo` connected) before merge. If nested defaults turn out not to expand,
   fall back to `"${CLAUDE_PLUGIN_ROOT:-.}/bin/mnemo.cmd"` (relative to the
   launch cwd, which for a project `.mcp.json` is the project root) and note
   the limitation in the file comment.
2. `bin/launch` gains a developer short-circuit before the binary lookup.
   A plugin install is a git clone of this same repo, so the source tree
   itself cannot be the discriminator. The branch fires only when
   `CLAUDE_PLUGIN_ROOT` is unset **and** either `MNEMO_DEV=1` is set or
   `$PLUGIN_ROOT/src/mnemo_claude.egg-info` exists (the gitignored artefact
   of `pip install -e .`; plugin clones never have it). It then execs
   `${MNEMO_PYTHON:-python3} -m mnemo "$@"` with
   `PYTHONPATH="$PLUGIN_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"`. mnemo is
   stdlib-only, so any Python ≥ 3.10 on PATH runs the source tree. If no
   interpreter is found the script logs one line and falls through to the
   normal binary path.
3. `tests/integration/test_plugin_launcher.py` gets a case: unset
   `CLAUDE_PLUGIN_ROOT`, run `bin/launch --version` from the repo, expect the
   source version string and no download attempt (network stubbed by
   `PATH` without curl/wget, or by asserting the exec happened before
   `download_binary`).
4. `docs/getting-started.md` (developer section) gets two lines: why the
   repo's own `.mcp.json` resolves to the source tree, and
   `disabledMcpjsonServers: ["mnemo"]` as the alternative for people who
   prefer the global registration.

### #120 doctor scans `shared/_archive/**`

**Problem.** `_doctor_check_stripped_enforce` (`doctor_checks/rules.py:127`)
uses `shared.rglob("*.md")`, so after `mnemo reclassify --apply` it lists
1489 archived originals. Same walker shape in `list_enforced.py:16`,
`disable_rule.py:17`, `autopilot/proposer/eos_extractor.py:85`.

**Design.** New helper in `core/filters.py`:

```python
def iter_shared_pages(vault_root: Path, *, include_inbox: bool = True) -> Iterator[Path]:
    """Every rule page under shared/, skipping shared/_archive/** always and
    shared/_inbox/** when include_inbox is False. Sorted for determinism."""
```

A path is archived when `"_archive"` is any component of its path relative to
`shared/` (not just the parent, since originals live two levels down). The four
walkers switch to it; doctor and `list-enforced` use `include_inbox=False`,
`disable-rule` and `eos_extractor` keep inbox. Unit test with an `_archive`
fixture proves each caller skips it.

---

## PR B — ops

### #115 circuit breaker trips silently

**Problem.** `errors.should_run` (threshold 10/hour) gates every hook; when
open, `session_start.main` returns at line 439 before any output. `mnemo
status` prints `Circuit breaker: OPEN — recent errors detected` with no
remedy; `mnemo doctor` says nothing.

**Design.**

1. `core/errors.py` gains `recent_summary(vault_root) -> tuple[int, list[tuple[str,int]]]`:
   count of breaker-relevant errors in the last hour (same exclusions as
   `should_run`) plus `where` buckets sorted by count desc. Single pass over
   the log, same fail-open shape.
2. `session_start.main`: when `should_run` is False it emits (via the existing
   `_emit_injection`) one line and returns 0:
   `[mnemo] paused: circuit breaker open (N errors in the last hour, most from
   <where>). Hooks are off until it cools down (1h) — run `mnemo fix` to reset
   now, `mnemo doctor` to see the errors.` Emitted every session while open;
   no dedupe — the breaker self-heals in an hour and the line is the only
   signal the user has. The other three hooks stay silent (prompt-path
   budget; PreToolUse output would read as a denial).
3. `mnemo status`: the OPEN line becomes
   `Circuit breaker: OPEN — N errors in the last hour (top: session_start.injection ×15). Run `mnemo fix` to reset.`
   The closed line is unchanged.
4. `mnemo doctor`: new check in `doctor_checks/misc.py`, `_doctor_check_circuit_breaker`,
   prints `✗ circuit breaker OPEN …` with the same remedy and returns False
   (it is a real fault, not advisory). When closed it prints the ok line.

### #116 briefings accumulate forever

**Problem.** `core/briefing.py:166` writes `bots/<agent>/briefings/sessions/<id>.md`
per session; nothing prunes. Briefings are also inputs: rule pages cite them in
`sources:`, `mnemo recall` searches them, `mnemo learn` reads the latest.

**Design.**

1. Config under `briefings`: `retentionDays: 180` (0 = never prune),
   `keepPerAgent: 20` (newest N per agent always kept).
2. `core/briefing.py` gains `prune(vault_root, cfg, *, now=None, dry_run=False) -> PruneReport`
   with fields `scanned, protected_by_sources, kept_recent, kept_min, deleted: list[Path]`.
   Algorithm: collect every `bots/*/briefings/sessions/*.md`; age from the
   file's mtime (briefings are written once; no frontmatter date to trust);
   protected set = every `sources:` entry of every page yielded by
   `filters.iter_shared_pages(vault, include_inbox=True)` (frontmatter parse
   only); per agent, sort newest first, keep the first `keepPerAgent`,
   then delete the rest that are older than `retentionDays` and not protected.
   Deletion is `unlink`; briefings are derived from transcripts that stay in
   `~/.claude/projects`. Also removes the same-stem sidecars if any exist
   (check `briefing.py` for a `.json` companion; none known).
3. Trigger: `session_start.main`, next to `session.cleanup_stale`, at most once
   per 7 days via a marker `.mnemo/briefings-prune.last` (ISO timestamp);
   wrapped in the same try/`log_error("session_start.briefings_prune")`.
   Also `mnemo briefing --prune [--dry-run]` (flags on the existing command)
   printing the report.
4. `mnemo status` gains a line in the Vault section:
   `Briefings: 299 across 14 agents (2.6 MB) — 0 prunable (retention 180d, keep 20/agent)`.
   Computed with `dry_run=True`; when `retentionDays` is 0 the tail reads
   `retention off`.

---

## PR C — #114 rule identity

**Problem.** Pages are written to `shared/<type>/<slug>.md` where `<slug>` is
the normalized LLM slug, and carry `name: <display name>` but no `slug:`.
`derive_rule_slug` (slug → name → stem) therefore returns the display name
for the reflex index, the activation index, `disable-rule` and the MCP tools,
while the learned ledger, `mnemo learn` and the existing-rules prompt use the
LLM slug (= normalized stem). One rule, two identifiers.

**Design.** Canonical slug = `scanner._normalize_slug(stem)`; make it explicit
in frontmatter and let the existing priority chain pick it up.

1. `_render_page` emits `slug: <page.slug>` immediately after `name:`.
   `page.slug` is already normalized by the response parser.
2. New `core/migrations/slugs.py` with
   `stamp_slugs(vault_root, *, dry_run=False) -> SlugReport(scanned, stamped, skipped: list[tuple[Path,str]])`:
   for every page from `iter_shared_pages(vault, include_inbox=True)` whose
   frontmatter lacks a non-empty string `slug`, insert
   `slug: <_normalize_slug(stem)>` after the `name:` line (or as the first
   frontmatter line when `name:` is missing) using the `_fm_span` surgery
   style from `reclassify_apply.py` and `atomic.atomic_write_bytes`. Pages
   whose frontmatter cannot be parsed are skipped and listed. Idempotent:
   second run stamps 0.
3. Callers: `session_start.main` runs `stamp_slugs` right before the index
   rebuild block (same try/`log_error` shape; only pages without a slug are
   touched, so steady-state cost is one frontmatter scan, ~0.5 s at 1.6k
   pages, and it is skipped entirely when a marker `.mnemo/slugs-stamped.v1`
   exists — written after the first run that stamps 0 remaining). `extract`
   runs it before `apply_pages` (so a vault that never sees a session start,
   e.g. CI, is migrated too). `reclassify_apply` needs no change: it rebuilds
   indexes after the pages it rewrote, and `_rewrite_*` preserves unknown
   lines.
4. `mnemo doctor` check: `N page(s) missing slug: — run any session or
   `mnemo extract` to migrate` (advisory when N > 0, ok line when 0).
5. `derive_rule_slug` unchanged. After migration every consumer resolves the
   same kebab slug. Consequences called out in CHANGELOG: index keys,
   `disable-rule` argument, MCP `read_mnemo_rule` slug and `mnemo why`
   output switch from display names to kebab slugs; the activation activity
   log keeps old display-name rows (append-only telemetry, not remapped).
6. `disable_rule._find_rule_file` keeps matching stem and derived slug — a
   display name still resolves for muscle memory.

**Tests.** Unit: stamping inserts after `name:`, is idempotent, skips
`_archive`, skips unparsable pages, `_render_page` emits `slug:`,
`derive_rule_slug` prefers it, both index builders key by it on a migrated
fixture, marker short-circuit. Integration: session_start on a legacy tmp
vault migrates then rebuilds; `mnemo learn` output slug == index key.

---

## PR D — #119 reclassify `keep` bar

**Problem.** `validate` (`reclassify.py:318`) accepts a `keep` when the quote
is a substring of any user turn of a source session. Generic lines
("implementa os fixes", "vamos testar a opcao A?") pass. `evidence.quote_verified`
at extraction time has the same bar.

**Constraint.** Lexical overlap between quote and rule cannot be required:
quotes are Portuguese user turns, rules are English (e.g. "vamo mudar o env
do app para prod e subir" ↔ `build-and-deployment-configuration-should-follow-environment`
is a legitimate keep with near-zero token overlap).

**Design.**

1. `core/corrections.py` gains `quote_is_specific(quote) -> bool`: normalize,
   tokenize on `\w+`, drop a small PT+EN stopword list (articles, pronouns,
   prepositions, auxiliaries, and the generic imperatives `implementa`,
   `implementar`, `faz`, `fazer`, `aplica`, `aplicar`, `testa`, `testar`,
   `vamos`, `vamo`, `bora`, `pode`, `ok`, `sim`, `apply`, `do`, `implement`,
   `test`, `run`), require ≥ 6 remaining tokens. `MIN_QUOTE_CHARS` stays.
2. Reclassify prompt: `keep` verdicts must also fill `link`: one sentence
   stating what in the quote establishes the rule. `Verdict` gains `link`.
   `validate` demotes a keep whose quote fails `quote_is_specific` (reason
   `quote-generic`) or whose `link` is empty (reason `link-missing`). The
   `link` is recorded in the plan JSON and in the `evidence:` block written
   by `_rewrite_keep` as `evidence.link` so a human can audit it.
3. `evidence.verify_page` applies `quote_is_specific` too (reason surfaces as
   the existing demotion path — no new field).
4. Calibration, no LLM calls: a script under `tools/` (or a `recall`-marked
   test) replays the 61 keeps in the saved plan through the new gate and
   prints survivors/demotions with their quotes. The PR description reports
   the numbers; the threshold (6) is adjusted only if legitimate keeps such
   as the deploy-env example above are lost.

**Not in scope.** A second LLM pass — cost per rule doubles and the single
pass with a mandatory `link` gives the same auditability.

---

## Cross-cutting

- Every PR: CHANGELOG `[Unreleased]` entry per issue, `Closes #N` in the PR
  body, full unit suite green with `/usr/local/bin/python3 -m pytest`.
- Windows CI: all new file reads/writes pass `encoding="utf-8"`; manifest
  paths POSIX.
- No new detached spawns; `_no_real_detached_jobs` stays sufficient.
