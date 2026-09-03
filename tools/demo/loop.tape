# The five-minute loop, recorded for real. See tools/demo/README.md.
#
#   DEMO_ROOT=/tmp/mnemo-demo bash tools/demo/setup.sh
#   vhs tools/demo/loop.tape
#
# Every wait on model output is Wait+Screen with a generous timeout. Claude's
# turns end with a "✻ Worked for Ns · done H:MM" line; frame 1 waits for it
# before typing /exit, because typing into a busy turn is what breaks a take.
# Frame 3 cannot wait for it (the model may keep investigating after the
# install), so it holds 8s after `yarn add lodash` and sends Escape first.
# Frames 2 and 4 are the proof and hold the longest.

Output docs/assets/loop.gif

Set Shell bash
Set FontSize 16
Set Width 1000
Set Height 600
Set Padding 16
Set Theme "Catppuccin Mocha"
Set TypingSpeed 60ms
Set PlaybackSpeed 1.0

Env DEMO_ROOT "/tmp/mnemo-demo"
Env MNEMO "/usr/local/bin/python3 -m mnemo"
# Classic inline renderer. With `tui: fullscreen` Claude Code draws on the
# alternate screen buffer, which Wait+Screen never sees — every wait on TUI
# text would time out.
Env CLAUDE_CODE_DISABLE_ALTERNATE_SCREEN "1"

Hide
# Scrub any Claude Code session the recording was started from (an inherited
# CLAUDE_CODE_CHILD_SESSION turns transcript saving off, and `mnemo learn`
# reads the transcript), keep the maintainer's personal MCP servers out of
# the demo, and give the shell a plain `$ ` prompt for the waits below.
Type "for v in $(env | grep -o '^CLAUDE[A-Z_]*'); do unset $v; done; export PATH=$HOME/.local/bin:/opt/homebrew/bin:$PATH PS1='$ '"
Enter
Type "source $DEMO_ROOT/demo-shell.sh; cd $DEMO_ROOT/app && clear"
Enter
Show

# --- frame 1: the correction ------------------------------------------------
Type "claude"
Enter
Wait+Screen@60s /Claude Code v[0-9]/
Sleep 2s
Type "never use npm in this repo, always yarn"
Sleep 1s
Enter
Wait+Screen@180s /· done /
Sleep 3s
# The exit is hidden: Claude Code's shutdown sometimes idles for minutes in
# a session-bridge reconnect loop (see notes); the `claude` wrapper from
# demo-shell.sh kills a shutdown that outlives the SessionEnd hook by 4s,
# and nobody needs to watch it. Recording resumes on the cleared screen.
Hide
Type "/exit"
Sleep 1s
Tab
Sleep 1s
Enter
Wait+Screen@90s /DEMO-SHELL-READY/
Type "clear"
Enter
Sleep 500ms
Show

# --- frame 2: mnemo learn ---------------------------------------------------
Type "mnemo learn"
Enter
Wait+Screen@180s /learned: .*yarn/
Sleep 6s

# --- frame 3: the next prompt uses yarn -------------------------------------
Type "clear"
Enter
Type "claude"
Enter
Wait+Screen@60s /Claude Code v[0-9]/
Sleep 2s
Type "add the lodash package to the dependencies"
Sleep 1s
Enter
Wait+Screen@120s /yarn add lodash/
Sleep 8s
# Bound the turn: the model sometimes keeps investigating after the install
# (registry lookups, lockfile reads). Escape is a no-op on an idle prompt
# and an interrupt on a busy one, so the frame ends either way — hidden, so
# an "Interrupted" banner never reaches the GIF.
Hide
Escape
Sleep 1s
Type "/exit"
Sleep 1s
Tab
Sleep 1s
Enter
Wait+Screen@90s /DEMO-SHELL-READY/
Type "clear"
Enter
Sleep 500ms
Show

# --- frame 4: the receipt ---------------------------------------------------
Type "mnemo why"
Enter
Wait+Screen@20s /injected\s+\S*yarn/
Sleep 5s

# --- notes for whoever adjusts this tape ------------------------------------
#
# `Wait+Screen@<timeout> /<regex>/` polls the screen until the regex matches
# (vhs >= 0.9). vhs pads every screen line to the terminal width, so an
# end-of-screen match needs `\s*$`, not ` ?$`. The hidden first line sets
# `PS1='$ '` (vhs's own bash prompt is `> `). After each `/exit` the tape
# waits for `DEMO-SHELL-READY`, which the `claude` wrapper prints once the
# process is gone and the tty is sane. The frame-1/3 wait must match something the
# TUI draws, never the typed word `claude` (that is on screen before the TUI
# is up); today the header reads `Claude Code v2.1.259`. If that wording
# changes, loosen the regex — it only needs to detect "the TUI is up".
# The hidden prelude sources $DEMO_ROOT/demo-shell.sh (written by setup.sh):
# `mnemo` aliases the checkout's mnemo, and `claude` becomes a wrapper that
# runs the real CLI with `--strict-mcp-config` and an empty MCP config (so
# the maintainer's own MCP servers do not print "N MCP servers need
# authentication" across frame 1), `--model sonnet` (the larger models tend
# to answer the correction by exploring the repo with tool calls, which
# makes frame 1 long and busy) and `--disallowedTools Write,Edit,…` (the
# model otherwise sometimes persists the correction itself in a CLAUDE.md,
# and frame 3 could no longer be attributed to mnemo; `yarn add` needs only
# Bash). The wrapper also runs the shutdown watchdog described below.
# `/exit` is typed, then Tab (accept the slash-menu completion), then Enter:
# with the menu open, Enter alone only highlights the row (vim mode submits
# directly, which is why a single Enter can seem to work). Never Ctrl+D —
# it reaches bash and logs the recording shell out.
# Both exits sit inside Hide/Show. Claude Code 2.1.259 creates a claude.ai
# session-bridge for every interactive session (no user setting turns it
# off; `autoUploadSessions` and `remoteControlAtStartup` were tried), and at
# /exit its teardown sometimes races the archive call ("Epoch mismatch (409,
# session_not_active) … closing with 4090 for poll-loop recovery" in
# `claude --debug-file`) and the process idles for minutes (one take: still
# stuck after 240s; Ctrl+C is ignored in that state) before the shell prompt
# returns. /exit has already run by then — the transcript is written and
# the SessionEnd hook has completed — so the wrapper's watchdog, which
# kills the process 4s after the hook's "session ended" log line, loses
# nothing and bounds the wait.
