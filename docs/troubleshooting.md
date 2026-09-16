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

```bash
mnemo migrate-plugin
```

That removes the older one, leaving a timestamped backup of each settings file.
Your vault is untouched.

## A hook is installed but only half works

`mnemo init` writes each hook's tool matcher once. When a release widens one —
v1.4 added `Read` to the `PreToolUse` matcher so rules surface when you *open*
a file, not only when you edit it — an install that already exists keeps the
old one. Everything still reports healthy, because the hook is there; it just
reaches fewer tools than the code expects. Measured on one install, that cost
roughly half of what path-scoped enrichment would have delivered.

mnemo repairs this by itself now: the next session start rewrites mnemo's hook
entries in `settings.json` and tells you on stderr what it changed. The write
is the same narrow one `mnemo init --hooks-only` performs — your config,
statusLine, MCP servers and other tools' hooks are left exactly as they are,
and the previous file is backed up alongside it. The session that repairs it
keeps the hooks it started with; the next one picks up the new matcher.

If you narrowed a matcher on purpose, re-narrow it: the repair runs once per
distinct drift and will not fight you. To turn it off entirely, set
`install.autoRepairHooks` to `false` in `mnemo.config.json`. Either way
`mnemo status` and `/mnemo:doctor` keep reporting the drift, with the command
that fixes it:

```bash
mnemo init --hooks-only            # global install
mnemo init --project --hooks-only  # project-scoped install
```

Plugin installs are never affected: their hooks ship inside the plugin, so
they move with the version.

## Circuit breaker is OPEN

mnemo opens it after more than 10 errors in an hour, to stop a broken
extraction from retrying forever.

```
/mnemo:doctor
tail ~/mnemo/.errors.log
```

Once the underlying issue is fixed:

```bash
mnemo fix
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
  limit. It stopped at the first such failure and held nothing against the
  transcripts it never reached, so nothing is lost. Run `mnemo backfill` in a
  terminal: it does the same work in the foreground and prints the actual
  error. The one-shot is not spent, so a later session will also retry on its
  own.
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

## After upgrading to v0.18, rules I promoted months ago suddenly went live

They did, and they should have been live all along.

Promoting a staged page is a plain `mv` into `shared/<type>/`. Nothing rewrites
its frontmatter, so it keeps the `needs-review` tag it was written with — and
until v0.18 the visibility filter treated that tag as "still a draft" and hid
the page from injection, the MCP tools and the HOME dashboard. Every page you
reviewed and moved by hand was silently doing nothing.

v0.18 makes **location** the only authority on draft-ness: under
`shared/_inbox/` it's a draft, under `shared/<type>/` it's live. So the first
session after upgrading rebuilds the index and your hand-promoted rules start
injecting.

If some of them shouldn't be live, you have two ways out:

```bash
mv ~/mnemo/shared/feedback/x.md ~/mnemo/shared/_inbox/feedback/x.md   # back to draft
rm ~/mnemo/shared/feedback/x.md                                       # or just delete it
```

To hide a rule without moving it, set `stability: evolving` in its frontmatter
— that filter is unchanged and still hides a page wherever it lives.

To see what is actually live right now, run `/mnemo:doctor`.

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

## `mnemo sessions` says there are none, but I have some running

```
  nenhuma sessão em background
```

The queue is scoped to the repo you are standing in. A session started
somewhere else is real but out of scope:

```bash
mnemo sessions --all
```

If `--all` is also empty, the sessions are not where mnemo looks. It reads
Claude Code's own jobs directory, read-only — it does not track sessions
itself, so a session that never registered there cannot appear. `/mnemo:doctor`
reports what it found:

```
  ✓ 4 background sessions (1 waiting)
```

**A worktree is its own scope.** This is the common surprise: dispatching
children into worktrees puts each one in a different directory, so standing in
the main checkout you see none of them. `mnemo sessions --all` is the view you
want while a dispatch is running. (What normalization does handle is the same
directory reached through two different strings — a symlink, or `/tmp` on
macOS; it resolves those so the queue does not come up empty.)

## A session is running but missing from the queue

`/mnemo:doctor` counts the entries it could not parse, which is the case a
plain "no sessions" line would hide:

```
  ⚠ 4 background sessions (1 waiting, 1 unreadable)
```

An unreadable entry is one whose state file mnemo could not read or make sense
of. The queue skips it rather than guessing; it is still a real session, and
`claude attach <short_id>` still reaches it. If the count is
`could not read <path>`, the whole jobs directory is unreadable — a permissions
problem on that directory rather than anything in the vault.

Death does not remove a session from the queue. A session whose process died
while working stays in `TRABALHANDO` with whatever it was last doing — the
bucket is chosen by what the session recorded, not by whether the process is
still alive. What liveness does decide is the blocked pair: a session blocked
on a human moves to `ABANDONADAS` once the process behind it is *provably*
gone, so a question nobody will ever answer stops competing for the top of the
queue. Provably is the operative word — only a roster that positively reports
the pid as gone counts, and an unknown one is left in `TE ESPERANDO` rather
than being written off.

## The activity column is empty, or stuck

An empty column means the session has no transcript to read yet
(`linkScanPath` absent) or nothing has happened in it since mnemo started
looking. `mnemo session <short_id>` says which:

```
  esta sessão não registrou um transcript (linkScanPath ausente)
  nenhuma ação registrada na janela lida
```

A column that never moves is the signal working, not failing: the count rises
only when tool uses happen, so a frozen `(+N)` is a stalled session. Tell it
apart from a loop by the `↻` mark — a count rising against an unchanged target
is going in circles, which without the mark reads exactly like progress.

`(+N)` is not a lifetime total. A one-shot `mnemo sessions` reads the tail end
of each transcript, so the count covers the recent window rather than the whole
session; under `--watch`, each later redraw reads only what was appended since
the last one. Bookmarks live in memory and die with the process — which is why
a number that looks small for a long-running session is not a bug, and why
`--watch` shows movement more usefully than re-running the one-shot.

When a tick finds nothing new, the column holds its previous value rather than
blanking — a session that wrote nothing since is still doing whatever it was
doing, and an em-dash there would read as "stopped".

## Removing everything

Plugin: `/plugin uninstall mnemo`
npm: `npx @xyrlan/mnemo uninstall`
pipx/uv: `mnemo uninstall` then `pipx uninstall mnemo-claude`

The vault survives all of them. Deleting it is a separate, conscious step:
`rm -rf ~/mnemo`.
