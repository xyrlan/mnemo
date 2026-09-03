# The five-minute loop, recorded for real. See tools/demo/README.md.
#
#   DEMO_ROOT=/tmp/mnemo-demo bash tools/demo/setup.sh
#   vhs tools/demo/loop.tape
#
# Every wait on model output is Wait+Screen with a generous timeout; a fixed
# Sleep is only used where nothing on screen is predictable (Claude's first
# reply). Frames 2 and 4 are the proof and hold the longest.

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
Type "alias claude='claude --strict-mcp-config --mcp-config $DEMO_ROOT/mcp-empty.json'; cd $DEMO_ROOT/app && clear"
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
Sleep 12s
Type "/exit"
Enter
Wait+Screen@20s /\$\s*$/
Sleep 1s

# --- frame 2: mnemo learn ---------------------------------------------------
Type "clear"
Enter
Type "$MNEMO learn"
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
Sleep 3s
Type "/exit"
Enter
Wait+Screen@20s /\$\s*$/
Sleep 1s

# --- frame 4: the receipt ---------------------------------------------------
Type "clear"
Enter
Type "$MNEMO why"
Enter
Wait+Screen@20s /injected\s+\S*yarn/
Sleep 5s

# --- notes for whoever adjusts this tape ------------------------------------
#
# `Wait+Screen@<timeout> /<regex>/` polls the screen until the regex matches
# (vhs >= 0.9). vhs pads every screen line to the terminal width, so an
# end-of-screen match needs `\s*$`, not ` ?$`. The hidden first line sets
# `PS1='$ '` (vhs's own bash prompt is `> `); that is what the `/\$\s*$/`
# waits after `/exit` look for. The frame-1/3 wait must match something the
# TUI draws, never the typed word `claude` (that is on screen before the TUI
# is up); today the header reads `Claude Code v2.1.259`. If that wording
# changes, loosen the regex — it only needs to detect "the TUI is up".
# The hidden alias runs claude with `--strict-mcp-config` and an empty MCP
# config (written by setup.sh) so the maintainer's own MCP servers do not
# print "N MCP servers need authentication" across frame 1.
