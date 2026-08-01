# Troubleshooting

Commands below are shown in their plugin form (`/mnemo:doctor`). On an npm or
pipx install, drop the prefix and run them in a terminal (`mnemo doctor`).

Start with `/mnemo:doctor` — it checks most of what's on this page and prints
the fix.

## Nothing is happening at all

1. `/mnemo:status` — is the vault there, and are hooks reported?
   - Plugin install: expect `Hooks (plugin): 4/4`
   - npm/pipx install: expect `4/4` next to a settings.json path
2. Did you restart Claude Code after installing? Hooks are read at startup.
3. `cat ~/mnemo/.errors.log` — anything recent?

### Plugin: "mnemo: download failed"

The plugin fetches its binary from GitHub Releases on first use. If that
fails, mnemo does nothing rather than breaking your session, and retries next
session. Common causes:

- **Offline or behind a proxy.** Retry when connected.
- **Unsupported platform.** Builds exist for macOS (arm64/x64), Linux x64, and
  Windows x64. Anything else: install via `pipx install mnemo-claude` instead.
- **`checksum mismatch — refusing to install`.** mnemo verifies the download
  against a published SHA-256 and will not install a binary that fails it.
  Please [open an issue](https://github.com/xyrlan/mnemo/issues).
- **No `shasum`/`sha256sum` available.** mnemo refuses to install unverified.
  Install coreutils, or use pipx.

The cache lives under the plugin's data directory, keyed by version. Deleting
it forces a clean refetch.

## Everything happens twice

Duplicated log lines, rules injected twice: you have both a plugin install and
an older `mnemo init` install, and both sets of hooks fire.

```
/mnemo:migrate
```

That removes the older one, leaving a timestamped backup of each settings file.
Your vault is untouched.

## Circuit breaker is OPEN

mnemo opens it after more than 10 errors in an hour, to stop a broken
extraction from retrying forever.

```
/mnemo:doctor
tail ~/mnemo/.errors.log
```

Once the underlying issue is fixed:

```
/mnemo:fix
```

## The daily log isn't growing

1. `/mnemo:status` — are hooks reported as installed?
2. `cat ~/mnemo/.errors.log`
3. Check `capture.sessionStartEnd` in `~/mnemo/mnemo.config.json`
4. npm/pipx installs only: `cat ~/.claude/settings.json | jq .hooks`

## Rules are never injected

Reflex is deliberately conservative — it stays silent rather than injecting
something irrelevant.

- `/mnemo:doctor` reports the reflex index state and recent emit rate
- A brand-new vault has no rules yet: the index builds after your first
  extraction, so expect nothing on day one
- Short prompts are skipped by design (`reflex.thresholds.minQueryTokens`)
- Per-session cap is `reflex.maxEmissionsPerSession`, default 10

See [configuration.md](configuration.md) to loosen the thresholds — though the
autopilot retunes them from your own hit/miss data, so give it a few sessions
first.

## Backfill: the first-run sweep never finished

The automatic sweep runs detached with its output discarded — the hook's stdout
carries the injection envelope, so the child cannot print to your terminal.
`/mnemo:doctor` is where you find out it happened. It reports three shapes:

- **"failed N times and never completed"** — the sweep aborted on the machine,
  not on a transcript: no `claude` CLI on `PATH`, expired auth, or a rate
  limit. Nothing was harvested and nothing was held against your transcripts.
  Run `mnemo backfill` in a terminal: it does the same work in the foreground
  and prints the actual error. The one-shot is not spent, so a later session
  will also retry on its own.
- **"started Nh ago and never finished"** — the process was killed mid-sweep
  and left its lock behind. Nothing is running. `mnemo backfill` resumes;
  already-harvested sessions are skipped.
- **"finished having harvested nothing — N sessions failed"** — the sweep ran
  to the end but every session failed on its own merits. See below.

Detailed errors are in `~/mnemo/.errors.log` under `where=backfill.harvest` and
`where=session_start.backfill`.

## Backfill: transcripts that stopped being retried

A transcript that fails three times is retired — nothing decrements the counter,
and archived transcripts never change, so it would otherwise be skipped forever.
`mnemo backfill` says so:

```
backfill: nothing to do — 12 already harvested, 3 gave up after 3 failed
attempts (see /Users/you/mnemo/.errors.log).
          `mnemo backfill --retry-failed` clears them for another try.
```

Clear them and try again:

```bash
mnemo backfill --retry-failed
```

That drops the failed entries vault-wide, whatever `--project` you pass, and
leaves finished work alone. If they keep failing, read `~/mnemo/.errors.log` —
the usual culprit is a single enormous transcript timing out twice at
`extraction.subprocessTimeout`.

## `shared/_inbox/` is full of pages I didn't write

That's backfill, working as intended. Pages reconstructed from old transcripts
are stamped `origin: backfill` and always stage for review — they are never
auto-promoted into `shared/`, whatever their source count. `mnemo doctor` lists
them:

```
2 backfill rule(s) staged in _inbox/ awaiting review
  • shared/_inbox/project/mnemo.md
```

Read each one. Move the keepers into the matching directory
(`shared/_inbox/feedback/x.md` → `shared/feedback/x.md`) and delete the rest —
nothing under `_inbox/` takes part in injection. Expect to delete most of them:
even rules extracted from *live* sessions get archived far more often than
they're kept, and a reconstruction of a session nobody watched is a weaker
signal than that.

To stop producing more: `"backfill": { "enabled": false }`.

## Backfill's cost estimate looks wrong

It's a rough figure, and only for input. It's measured on the flattened text
actually sent to the model — tool inputs dropped, tool results truncated — not
on the size of the `.jsonl` files, which overstates by one to two orders of
magnitude. It still counts sessions that `backfill.minFileMutations` may skip
without any call at all, and it says nothing about output tokens. Treat it as
an order of magnitude, not a bill. If your `claude` CLI runs on a Pro/Max
subscription, there's no per-token charge for those calls anyway.

`mnemo backfill --dry-run` prints the estimate and writes nothing.

## `doctor` warns about statusLine drift

You hand-edited `~/.claude/settings.json` after installing. Re-run `mnemo init`
(npm/pipx) or `mnemo statusline --install` (plugin) to reconcile. If you manage
that file deliberately, silence the check with
`doctor.skipStatuslineDrift: true`.

## `mnemo init` refuses to run: malformed settings.json

By design — mnemo will not overwrite a `settings.json` it cannot parse. Fix the
JSON or move it aside, then re-run.

## Vault path has unusual characters

mnemo sanitizes project names, but `vaultRoot` itself must be a path your shell
and Python can reach. Avoid `*`, `?`, and newlines.

## Windows without WSL

Supported. `rsync` is absent, so a pure-Python fallback takes over — slower per
file, but functional. The plugin needs `bash` on `PATH` (Git for Windows
provides it); without it, hooks skip silently rather than erroring.

## Removing everything

Plugin: `/plugin uninstall mnemo`
npm: `npx @xyrlan/mnemo uninstall`
pipx/uv: `mnemo uninstall` then `pipx uninstall mnemo-claude`

The vault survives all of them. Deleting it is a separate, conscious step:
`rm -rf ~/mnemo`.
