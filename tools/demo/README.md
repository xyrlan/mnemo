# Recording the five-minute loop

The GIF at `docs/assets/loop.gif` is recorded, not staged: the tape drives
the real `claude` CLI against a throwaway repo and a fresh vault.

## Prerequisites

- `brew install vhs` (brings ttyd and ffmpeg)
- `claude` on PATH and logged in
- **No global mnemo active** — `tools/demo/setup.sh` refuses otherwise,
  because a second reflex would inject next to the demo's. Move
  `~/.claude/settings.json` aside (or `mnemo uninstall`) for the recording
  and restore it afterwards; if the plugin is enabled, disable it in
  `/plugin` for the duration.

## Record

```bash
DEMO_ROOT=/tmp/mnemo-demo bash tools/demo/setup.sh
vhs tools/demo/loop.tape
```

Check the four frames in the result (correction → `learned:` line with the
quote → `yarn add lodash` → `injected …yarn`), then check the size
(`ls -la docs/assets/loop.gif`, budget 3 MB — reduce `Width`/`Height` before
touching frame durations).

## Rules

- If Claude's reply in frame 3 does not use yarn, or `mnemo learn` stages
  the rule instead of learning it, **discard the run and retry**. Never
  edit the GIF. Log the run below either way.
- The README never carries a cherry-picked run without a log entry.

## Runs

| date | model (claude --version / default model) | outcome |
|------|------------------------------------------|---------|
| _none yet_ | | |
