# Spec — path enrichment status, measured (2026-09-15)

Contract piece `channels/investigate-enrichment`. Question from
`docs/contracts/channels.md`: path enrichment has fired **2 times ever**
(`.mnemo/enrichment-log.jsonl`, both 2026-09-14, project `clubinho`) while 656
rules carry `activates_on` and `enrichment.enabled=true`. Is 2 the correct
post-#271 number for this vault and this user's file traffic, or does
something still not match?

Every number below was produced by running mnemo's own functions against the
real `~/mnemo` vault, the real `~/.claude/projects` transcripts and the real
`~/.claude/settings.json` on this machine. Nothing is taken from fixtures.

## Verdict

**Fix — two narrow items. Keep the feature.**

- **2 is the correct number for the window it covers.** A replay of every
  real Read/Edit/Write since #271 went live through the real matcher
  reproduces the two logged events exactly: same session, same second, same
  file, same slug. The window is 21 hours, and 75% of its file traffic was in
  `mnemo` and `mnemo-desktop`, which have 1 and 0 reachable file rules.
- **But the install on this machine is not the one #271 shipped.** The live
  `PreToolUse` matcher in `~/.claude/settings.json` is still
  `Bash|Edit|Write|MultiEdit`, with no `Read`. #271 added `Read` in code, but
  nothing rewrote an existing install, and neither `mnemo status` nor
  `mnemo doctor` notices. Over the last full week (2026-W37), `Read` doubles
  what enrichment delivers: 40 notes against 23.
- **The remedy #271's changelog prescribes is destructive.** "Re-run
  `mnemo init`" overwrites `mnemo.config.json` with `{"vaultRoot": …}`. On
  this machine that silently resets `extraction.subprocessTimeout` from 180 to
  60 and `doctor.skipStatuslineDrift` from true to false.
- A third emission in the window was eaten by the **circuit breaker**. Errors
  from unrelated briefing work opened it for 48 minutes. This is a minor
  factor: 166 open minutes since 2026-09-01.

## 1. The two logged events reached the model

The transcripts hold the delivered context, not just the log line.
`hook_additional_context` attachments, session `05b44bb1`,
cwd `/Users/xyrlan/github/clubinho`:

| Attachment timestamp | Hook | Rule |
|---|---|---|
| 2026-09-14T21:38:57.988Z | `PreToolUse:Edit` | `plan-upgrade-reuses-renewal-infrastructure-no-new-endpoint` |
| 2026-09-14T21:39:21.010Z | `PreToolUse:Edit` | `servestatic-should-resolve-paths-correctly-for-dist-layouts` |

Those are the only mnemo `PreToolUse` attachments in any transcript on disk.
The other two `PreToolUse` attachments are #271's own `PAPAYA-271` probe.

## 2. The replay reproduces the log exactly

**When the fix went live.** The hook command is
`/usr/local/bin/python3 -m mnemo.hooks.pre_tool_use`, and that interpreter
imports `/Users/xyrlan/github/mnemo/src`: an editable install that runs
whatever the main checkout has checked out. The main checkout's reflog puts
#271 (`4a19b00`) on disk at the `pull --ff-only` of **2026-09-14 17:07:02
-0300 (20:07:02Z)**. It has stayed on master or on post-#271 branches since.

**Method.** Every `tool_use` block named Read/Edit/Write/MultiEdit in
`~/.claude/projects/**/*.jsonl` after 20:07:02Z goes through the hook's own
code path:

- `resolve_canonical_agent(cwd)` for the project;
- `pre_tool_use._repo_relative(file_path)` for the path;
- `rule_activation.match_path_enrich(index, project, rel, tool)` against the
  live index (built 2026-09-15T17:00:51Z);
- the hook's once-per-rule-per-session dedupe and 15-note cap.

Nothing is written. For calls in worktrees deleted since, the path is rebuilt
as the live hook saw it: canonical project, path relative to the worktree
root. That covers 189 calls, 32 of them sg-imports calls in
`/tmp/wt-correct-booking`. This investigation's own session is excluded.

**Window:** 484 calls in 42 sessions, up to 2026-09-15T17:11Z (186 Read,
110 Edit, 188 Write).

| Matcher | Predicted emissions | Logged |
|---|---|---|
| As installed (`Bash\|Edit\|Write\|MultiEdit`) | 3 | 2 |
| As shipped by #271 (`+Read`) | 3 | — |

Predicted under the installed matcher:

```
2026-09-14T21:38:57.911Z 05b44bb1 clubinho   Edit backend/src/subscriptions/subscriptions.service.ts  plan-upgrade-reuses-renewal-infrastructure-no-new-endpoint
2026-09-14T21:39:20.921Z 05b44bb1 clubinho   Edit backend/src/app.module.ts                           servestatic-should-resolve-paths-correctly-for-dist-layouts
2026-09-15T12:46:55.751Z e7aecf8b sg-imports Edit src/services/shipsgo.service.ts                         extract-and-reuse-shipsgo-port-sync-logic-as-helper
```

The first two are the logged events. The third was not delivered, and §4
shows why.

## 3. `Read` never reaches the hook on this machine

```
~/.claude/settings.json → hooks.PreToolUse[0].matcher = "Bash|Edit|Write|MultiEdit"
src/mnemo/install/settings.py:65         matcher = "Bash|Read|Edit|Write|MultiEdit"
```

That string was written by `18c740d` (2026-04-15, "register PreToolUse hook
with Bash|Edit|Write|MultiEdit matcher"). #271 changed `HOOK_DEFINITIONS` and
the plugin's `hooks.json`. Only `mnemo init` writes `settings.json`, and it
has not run since. The mnemo plugin is not installed here: it is absent from
`installed_plugins.json` and `enabledPlugins`, and only a stale 1.3.3 cache
directory remains. So `settings.json` is the only hook that fires.

**Reproduced live, not grepped.** From this session, whose hooks are the
user's `settings.json`, I read
`tests/unit/test_release_workflow.py`. Rule `ci-config-is-under-test` names
exactly that file (`path_globs: [tests/unit/test_release_workflow.py]`), it is
local to `mnemo`, and the breaker was closed.

- The session transcript has **0** `PreToolUse` attachments.
- `enrichment-log.jsonl` stayed at 2 lines.

The same payload fed straight to the hook
(`python3 -m mnemo.hooks.pre_tool_use`, with `MNEMO_CONFIG_PATH` pointing at a
scratch vault that holds a copy of the live index) emits for **both** `Read`
and `Edit`:

```
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": "• mnemo rule [[ci-config-is-under-test]]:\nIn this repo, CI workflow files are parsed and tested by `tests/unit/test_release_workflow.py`. …
```

What the matcher saw: `project=mnemo`, `rel=tests/unit/test_release_workflow.py`,
one hit. The matching code is correct. Claude Code simply never calls it for
a `Read`.

**Nothing surfaces the drift.**

- `mnemo status` prints `Hooks (global): 4/4`, because it counts events, not
  matchers. It also shows `Enrich rules: 9` for mnemo, but only 1 of those can
  fire (§5).
- `mnemo doctor` has no row for hook matchers.

**What `Read` is worth.** Replay with the rule-learned-before-call filter:
a note only counts as *carried* if the earliest source of its rule is dated
before the call's day. This follows the #237 lesson; without the filter,
today's index claims rules learned later.

| Window | Carried notes, installed matcher | Carried notes, `+Read` |
|---|---|---|
| 2026-W37 (full week, pre-fix traffic replayed) | 23 | **40** |
| 2026-09-07 → now, sessions with ≥1 note | 10 of 145 | **20 of 145** |
| 2026-09-07 → now, distinct (session, rule) notes | 30 | **57** (27 reachable only via Read) |

`Read` also delivers earlier, which is what #271 wanted. In session
`05b44bb1` the Read of `subscriptions.service.ts` would have carried the note
at 21:38:49, 8 seconds before the Edit that actually carried it.

## 4. The breaker ate the third emission

`errors.should_run(vault)` is the hook's first gate. It returns False when
more than 10 breaker-relevant errors fall in the last hour.
`_breaker_relevant` (`core/errors.py:48`) excludes only `extract.*` and
`session_end.schedule`.

I executed `should_run` with its clock frozen, against the error log as it
stood at each moment. That log is `.errors.log.20260915T094852` plus
`.errors.log`, truncated to entries up to that time.

| Moment (local -0300) | `should_run` |
|---|---|
| 09:46:50 / 09:46:55 / 09:47:53 (the sg-imports Read + Edits) | **False** |
| 10:10:00 | True |

Eleven errors fell between 08:54:00 and 09:06:15: 5 `session_start.injection`
`FileNotFoundError` and 6 `briefing.cli` `claude exited with code 1`. That is
the vanished-cwd failure #291 fixed for `briefing.cli`. They kept the breaker
open from 12:06:15Z to 12:54:00Z, silencing enrichment *and* Bash enforcement
in every session on the machine. Under the shipped matcher the sg-imports
Read at 12:06:43Z would have hit the same closed gate, and every later
shipsgo call in that session ended by 12:47:53Z. So **the correctly installed
matcher would also have logged exactly 2** in this window, 8 seconds earlier.

Since 2026-09-01 the breaker has been open **166 minutes** in three spans:
09-02 08:41–09:39, 09-13 23:01–09-14 00:01 and 09-15 09:07–09:55.

The biggest breaker-relevant `where` since 09-01 is
`briefing.corrections_rejected` (43 rows). That is a `ValueError` for
"correction quote not found", which is a validation outcome, not a hook
fault. `session_start.injection` has 37 and `briefing.cli` 23.

## 5. Traffic, not matching, sets the ceiling

**Post-fix window by canonical project:**

| Project | Calls | File-glob rules | Of those, match a tracked file |
|---|---|---|---|
| mnemo-desktop | 254 | 0 | 0 |
| mnemo | 109 | 3 | **1** |
| clubinho | 61 | 48 | 40 |
| sg-imports | 44 | 39 | 38 |
| central-inteligencia-frontend | 16 | 6 | (no checkout under `~/github`) |

363 of 484 calls (75%) landed where at most one rule can fire.

**Vault-wide:** 1888 rules, 656 with `activates_on`, **192** with a glob that
`names_a_file` (the #271 filter; the rest are area globs, ignored by design).
For the 186 whose repo is checked out under `~/github`, running
`_glob_matches` against `git ls-files` gives these results:

| Project | File-glob rules | Reachable | Globs | Globs matching a tracked file |
|---|---|---|---|---|
| meunu | 67 | 65 | 101 | 90 |
| clubinho | 48 | 40 | 75 | 60 |
| sg-imports | 39 | 38 | 59 | 57 |
| clearframe | 20 | 7 | 30 | 9 |
| bingx-robot | 7 | 7 | 10 | 10 |
| mnemo | 3 | 1 | 3 | 1 |
| pedrolobato | 2 | 1 | 2 | 1 |
| **total** | **186** | **159 (85%)** | **280** | **228** |

**The 52 dead globs:**

- **27 are subdirectory-relative.** The glob is an exact path suffix of a
  tracked file: `app.json` → `app/app.json` in clubinho, and `release.yml` →
  `.github/workflows/release.yml` for both of mnemo's dead ones. clubinho has
  10, clearframe 10, meunu 5, mnemo 2.
- **11 more** name a basename that exists at a different path.
- **8 were never in the repo's history.** Examples: `ui-tauri/tailwind.config.ts`,
  and `.env.local`, which is gitignored and so invisible to `ls-files`.
- **6 name files that moved or were deleted** (clearframe `src/clearframe.c`).

So the matcher is not missing a class of real traffic. Most file rules point
at real files. The dogfooding repo is nearly empty of them, which is why the
session that measures enrichment never sees it.

## 6. Recommendation

### Fix F1 — make matcher drift visible, and make the remedy safe

1. **A stateless doctor row** in `src/mnemo/cli/commands/doctor_checks/`.
   For each event in `install/settings.py:HOOK_DEFINITIONS`, compare the
   installed mnemo entry's `matcher` with the definition, and warn with the
   exact missing tools. It must be a `DOCTOR_CHECKS` row, not a once-ever hook
   notice: see *once-ever warnings go silent*, #229. `mnemo status` should
   stop counting a hook with a stale matcher as present.
2. **`mnemo init` must not replace `mnemo.config.json`.**
   `cli/commands/init.py:124,126,255,257` call
   `save_config({"vaultRoot": …})`, which writes the whole file
   (`core/config.py:159-162`). Merge `vaultRoot` into what is there.
   Executing that exact call on a copy of this machine's config left only
   `vaultRoot`. Until this lands, `changelog.d/271.fixed.md` tells standalone
   users to "re-run `mnemo init`". That fragment is unreleased, so it can
   still be corrected.
3. **Right now, on this machine** (the maintainer's call; this piece does not
   touch `~/.claude/settings.json`): re-inject the hooks alone, not `init`.

   ```sh
   python3 -c "from pathlib import Path; from mnemo.install.settings import inject_hooks; inject_hooks(Path.home()/'.claude/settings.json')"
   ```

   I ran `inject_hooks` against a copy of the live `settings.json`. The
   matcher became `Bash|Read|Edit|Write|MultiEdit`, every non-hook key was
   byte-equal, the event set was unchanged, and a `.bak.<stamp>` was written.

   **Cost of not doing it:** about half of the carried notes (23 against 40
   in W37), and delivery at Edit time instead of Read time.

### Fix F2 (low priority) — narrow the breaker

`briefing.cli` and `briefing.corrections_rejected` are background briefing
work, like the `extract.*` entries already excluded. They should not silence
PreToolUse enforcement and enrichment machine-wide. Exclude them in
`_breaker_relevant` (`core/errors.py:48-58`).

**Cost of not doing it:** 166 minutes since 09-01, with 1 lost note in the
measured window and an unknown number of lost Bash denials.

### Not recommended now

- **Suffix-matching subdirectory-relative globs.** It would revive 27 globs,
  but it is ambiguous (clubinho `src/hooks/useAuth.ts` has two candidates, in
  `app/` and `painel/`). It also widens the #271 "names one file" bar that
  kept notes at 1.6 per session. The extractor is the right place: the prompt
  already asks for file paths, and it could ask for *repo-root-relative*
  ones. Measure first whether the 27 predate #271's prompt change. Rule
  pages carry no creation date (most mtimes are the 2026-09-02 reclassify),
  so this was not established here.
- **Removal.** Removal would cost 20–40 carried notes a week, reaching 10–20
  sessions a week, in clubinho/meunu/sg-imports/clearframe. It would save
  little code: `core/rule_activation/` (1280 lines with the hook) also
  carries Bash enforcement, which shares the index and the hook.

### Document

Where F1 lands, `docs/` should say what enrichment does: file-naming globs
only, Read included, once per rule per session, and silent while the circuit
breaker is open. It should also point to `enrichment-log.jsonl` as the place
to confirm it fires. The "is it working?" answer should come from the log and
the doctor row, not from a recount like this one.

## Caveats

- The replay uses the index as of 2026-09-15T17:00:51Z, not the index at each
  call. The hindsight filter dates a rule by its earliest source briefing's
  `date:` (or the source file's mtime). A rule extracted days after that
  briefing still counts as carried, so the W37 numbers are an upper bound.
  All three post-fix rules have sources dated before 2026-09-02.
- Paths in deleted worktrees are rebuilt by pattern
  (`<repo>-wt-*/…`, `/tmp/wt-correct-booking/…`). About 50 `mnemo-desktop`
  worktree calls stayed unresolved. That project has 0 rules, so the effect
  is nil.
- The sg-imports Read at 12:06:43Z was a subagent's (`isSidechain: true`).
  Whether Claude Code runs `PreToolUse` for subagent tool calls was not
  verified here. It does not change the count, because the breaker was open
  either way.
- The post-fix window is 21 hours. It answers "is 2 correct", not "what is
  the steady-state rate". The W37 replay is the rate estimate.

## Reproduce

From a worktree of this repo, with `PYTHONPATH=src`:

- **Installed matcher:**
  `python3 -c "import json,os;print(json.load(open(os.path.expanduser('~/.claude/settings.json')))['hooks']['PreToolUse'])"`.
- **Delivered notes:** in transcripts, grep `~/.claude/projects` for
  `"hookEvent":"PreToolUse"`.
- **Replay:** for each transcript `tool_use` in {Read, Edit, Write,
  MultiEdit} after the cut-off, call `resolve_canonical_agent(cwd).name` →
  `mnemo.hooks.pre_tool_use._repo_relative(file_path)` →
  `rule_activation.match_path_enrich(load_index(vault), project, rel, tool)`,
  then apply per-session slug dedupe and a 15-note cap. Run it twice, with
  and without Read.
- **Hook, directly:** pipe a PreToolUse payload into
  `python3 -m mnemo.hooks.pre_tool_use` with `MNEMO_CONFIG_PATH` set to a
  config whose `vaultRoot` is a scratch directory holding a copy of
  `.mnemo/rule-activation-index.json`. Don't point it at the real vault: it
  appends to `enrichment-log.jsonl` and session state.
- **Breaker at a past moment:** replace `mnemo.core.errors.datetime` with a
  subclass whose `now()` returns that moment. Write the error-log rows up to
  it into a scratch vault's `.errors.log`, then call `should_run`.
- **Glob reachability:** per project,
  `names_a_file(g) and any(_glob_matches(g, f) for f in git ls-files)`.
