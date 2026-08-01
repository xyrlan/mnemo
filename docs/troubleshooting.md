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
