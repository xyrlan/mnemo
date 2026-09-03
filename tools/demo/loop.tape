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

Hide
Type "cd $DEMO_ROOT/app && clear"
Enter
Show

# --- frame 1: the correction ------------------------------------------------
Type "claude"
Enter
Wait+Screen@60s /(?i)(claude|welcome|tips)/
Sleep 2s
Type "never use npm in this repo, always yarn"
Sleep 1s
Enter
Sleep 12s
Type "/exit"
Enter
Wait+Screen@20s /\$ ?$/
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
Wait+Screen@60s /(?i)(claude|welcome|tips)/
Sleep 2s
Type "add the lodash package to the dependencies"
Sleep 1s
Enter
Wait+Screen@120s /yarn add lodash/
Sleep 3s
Type "/exit"
Enter
Wait+Screen@20s /\$ ?$/
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
# (vhs >= 0.9). The `/\$ ?$/` wait after `/exit` matches the shell prompt
# returning; if the maintainer's prompt does not end in `$`, change both
# occurrences (or rely on vhs's default plain PS1 under `Set Shell bash`).
# If Claude Code's first screen changes wording, loosen the frame-1/3 wait
# regex; it only needs to detect "the TUI is up".
