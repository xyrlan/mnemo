# Troubleshooting

## `/mnemo status` says circuit breaker is OPEN

mnemo opens its circuit breaker after >10 errors in an hour. To investigate:

```
/mnemo doctor
cat ~/mnemo/.errors.log | tail
```

To reset (after fixing the underlying issue):

```
/mnemo fix
```

## Daily log isn't growing

1. `/mnemo status` — are hooks installed (4/4)?
2. `cat ~/.claude/settings.json | jq .hooks` — see the actual entries
3. `cat ~/mnemo/.errors.log` — any error log entries?
4. Check `capture.*` flags in `~/mnemo/mnemo.config.json`

## Vault path has unusual characters

mnemo sanitizes agent names but the `vaultRoot` itself must be a path your shell
and Python can access. Avoid characters like `*`, `?`, or newlines.

## Windows native (no WSL)

mnemo works on native Windows but `rsync` is missing — the pure-Python fallback
takes over automatically. It's slower (~5-10× per-file) but functional.

## My settings.json is malformed and `/mnemo init` refuses to run

That's by design — mnemo will not overwrite a settings.json it can't parse.
Fix the JSON or move it aside, then re-run `/mnemo init`.

## I want to nuke everything

```
/mnemo uninstall
rm -rf ~/mnemo  # only if you really want to lose your captured history
```
