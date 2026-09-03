# Recording the five-minute loop

The GIF at `docs/assets/loop.gif` is recorded, not staged: the tape drives
the real `claude` CLI against a throwaway repo and a fresh vault.

## Prerequisites

- `brew install vhs` (brings ttyd and ffmpeg)
- `claude` on PATH and logged in
- `MNEMO` (default `/usr/local/bin/python3 -m mnemo`) running a build that
  includes the cold-start floor fix (#131). Without it a one-rule vault
  never clears `absoluteFloor` and frame 4 reads `silent`, not `injected`.
- **No global mnemo active** — `tools/demo/setup.sh` refuses otherwise,
  because a second reflex would inject next to the demo's. Move
  `~/.claude/settings.json` aside (or `mnemo uninstall`) for the recording
  and restore it afterwards; if the plugin is enabled, disable it in
  `/plugin` for the duration.

`setup.sh` also pre-answers Claude Code's folder-trust dialog for the demo
path in `~/.claude.json` (backup at `~/.claude.json.demo-backup`), removes
the demo's `.mcp.json` (nothing on screen uses MCP, and a project MCP server
would prompt for approval), and turns off the demo vault's background
briefing and extraction so the typed `mnemo learn` never races the
SessionEnd hook for the extraction lock.

The tape's hidden prelude unsets any inherited `CLAUDE_CODE_*` variables
(recording from inside a Claude Code session would otherwise start the demo
with transcript saving off), disables Claude Code's alternate-screen renderer
so vhs can see the TUI text, and aliases `claude` to run with
`--strict-mcp-config` and an empty MCP config so your personal MCP servers
stay out of the frame. Your `~/.claude/settings.json` display settings
(status line, vim mode, permission mode) still show; that is fine.

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
- If Claude persists the rule itself in frame 1 (writes a `CLAUDE.md`, a
  memory file, anything on disk), discard too: frame 3 would no longer be
  attributable to mnemo. The demo hides Write/Edit and turns auto-memory
  off to make this rare, not impossible.
- The README never carries a cherry-picked run without a log entry.

## Runs

| date | model (claude --version / default model) | outcome |
|------|------------------------------------------|---------|
| 2026-09-03 | 2.1.259 / Fable 5.1 (maintainer's default) | take 1: all four frames held (`injected use-yarn-not-npm (0.43)`), **discarded**: typed `$MNEMO learn` literally, and Claude also saved the rule to its own auto-memory, so frame 3 was not attributable |
| 2026-09-03 | 2.1.259 / Opus 5 then Sonnet 5 | takes 2–13 **discarded** while hardening the tape: slash-menu Enter, Opus exploring the repo for minutes, Escape mid-reply, Claude writing a CLAUDE.md, and above all the shutdown hang after `/exit` (bridge reconnect loop; fixed by the watchdog in `demo-shell.sh`). None reached a GIF worth judging |
| 2026-09-03 | 2.1.259 / Sonnet 5 | take 14: all four frames held (`injected use-yarn-never-npm (0.52)`), **discarded**: `[1] pid` job notice and a debug banner from the recording harness in frames 1 and 3 |
| 2026-09-03 | 2.1.259 / Sonnet 5 | take 15 **discarded**: API `529 Overloaded` in frame 1 |
| 2026-09-03 | 2.1.259 / Sonnet 5 | take 16 **kept**: correction → `learned: use-yarn-for-package-management … (evidence: "never use npm in this repo, always yarn")` → `yarn add lodash` → `injected use-yarn-for-package-management (0.60)`; 53 s, 659 KB. Claude Code's "75% of your weekly limit" banner shows in frame 3 |
