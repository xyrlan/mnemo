#!/usr/bin/env bash
# Build the throwaway repo + vault the demo tape records against.
#
#   DEMO_ROOT=/tmp/mnemo-demo bash tools/demo/setup.sh
#
# Refuses to run while a global mnemo is active: its reflex would fire next
# to the demo's, and frame 4 would show two injections.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEMO_ROOT="${DEMO_ROOT:-/tmp/mnemo-demo}"
MNEMO="${MNEMO:-/usr/local/bin/python3 -m mnemo}"
SETTINGS="$HOME/.claude/settings.json"

fail() { echo "setup: $*" >&2; exit 1; }

# --- guard: no global mnemo -------------------------------------------------
if [ -f "$SETTINGS" ]; then
  if grep -Eq '"command": *"[^"]*mnemo[^"]*hook' "$SETTINGS" || grep -Eq 'mnemo\.hooks\.' "$SETTINGS"; then
    fail "global mnemo hooks are wired in $SETTINGS — run 'mnemo uninstall' (or move the file aside) before recording, then restore it"
  fi
  if grep -Eq '"mnemo@[^"]*": *true' "$SETTINGS"; then
    fail "the mnemo plugin is enabled in $SETTINGS — disable it (/plugin) before recording, then re-enable"
  fi
fi
command -v claude >/dev/null || fail "'claude' is not on PATH"
$MNEMO --version >/dev/null 2>&1 || fail "'$MNEMO' does not run; set MNEMO to a working mnemo"

# --- fresh tree -------------------------------------------------------------
rm -rf "$DEMO_ROOT"
mkdir -p "$DEMO_ROOT/app" "$REPO_ROOT/docs/assets"
cd "$DEMO_ROOT/app"
git init -q -b main
cat > package.json <<'EOF'
{
  "name": "app",
  "version": "0.1.0",
  "private": true,
  "dependencies": {}
}
EOF
: > yarn.lock
printf '# app\n\nA small demo app.\n' > README.md
git add -A && git -c user.name=demo -c user.email=demo@example.com commit -qm "init"

# mnemo, project-scoped: the vault lands at ./.mnemo, which is the only
# project location config discovery knows about (a --vault-root elsewhere
# would leave learn/why/hooks on the global vault). --no-mirror: nothing to mirror.
$MNEMO init --project --yes --no-mirror --quiet
[ -f "$DEMO_ROOT/app/.mnemo/mnemo.config.json" ] || fail "mnemo init did not write $DEMO_ROOT/app/.mnemo/mnemo.config.json"

# The demo is foreground-only: `mnemo learn` briefs and extracts by itself, so
# the SessionEnd hook's detached briefing and first-run extraction would only
# race it for the lock ("another extraction is in progress") in frame 2.
python3 - "$DEMO_ROOT/app/.mnemo/mnemo.config.json" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d.setdefault("extraction", {}).setdefault("auto", {})["enabled"] = False
d.setdefault("briefings", {})["enabled"] = False
json.dump(d, open(p, "w"), indent=2)
EOF

# No MCP in the demo: nothing on screen uses it, a project .mcp.json makes
# Claude Code ask for approval on first start (which would eat frame 1), and
# the maintainer's own servers would print an auth warning. The tape runs
# claude with `--strict-mcp-config --mcp-config $DEMO_ROOT/mcp-empty.json`.
rm -f "$DEMO_ROOT/app/.mcp.json"
echo '{"mcpServers": {}}' > "$DEMO_ROOT/mcp-empty.json"

# The shell the tape records in sources this file (hidden prelude). `mnemo`
# is the checkout's mnemo; `claude` is a wrapper that runs the real CLI with
# the demo flags and a watchdog. The watchdog matters: Claude Code 2.1.259
# opens a claude.ai session-bridge for every session, and at /exit its
# teardown can race the archive call and leave the process idling for
# minutes after the session is over. The demo's own SessionEnd hook writes
# "session ended" to the vault log the moment /exit runs (transcript already
# complete), so a shutdown still alive four seconds after that line is
# killed and the shell comes back for the next frame.
cat > "$DEMO_ROOT/demo-shell.sh" <<EOF
alias mnemo="$MNEMO"
DEMO_ROOT="$DEMO_ROOT"
EOF
cat >> "$DEMO_ROOT/demo-shell.sh" <<'EOF'
claude() {
  local log="$DEMO_ROOT/app/.mnemo/bots/app/logs/$(date +%F).md"
  local before; before=$(grep -c 'session ended' "$log" 2>/dev/null); before=${before:-0}
  { (
    local pid="" i now
    for i in $(seq 1 60); do pid=$(pgrep -P $$ -f 'claude ' | head -1); [ -n "$pid" ] && break; sleep 0.5; done
    [ -n "$pid" ] || exit 0
    while kill -0 "$pid" 2>/dev/null; do
      now=$(grep -c 'session ended' "$log" 2>/dev/null); now=${now:-0}
      if [ "$now" -gt "$before" ]; then
        sleep 4; kill -9 "$pid" 2>/dev/null; exit 0
      fi
      sleep 1
    done
  ) >/dev/null 2>&1 & } 2>/dev/null   # the group's stderr swallows "[1] 12345"
  local watchdog=$!
  command claude --model sonnet --disallowedTools Write,Edit,NotebookEdit \
    --strict-mcp-config --mcp-config "$DEMO_ROOT/mcp-empty.json" "$@"
  kill "$watchdog" 2>/dev/null; wait "$watchdog" 2>/dev/null
  # A killed TUI can leave the terminal in raw mode; hand the shell back in
  # a known state and print a marker the tape can wait for.
  stty sane 2>/dev/null; clear; echo DEMO-SHELL-READY
}
EOF

# Claude Code opens a "Quick safety check: is this a project you trust?"
# dialog the first time it starts in a new directory, and `/exit` typed into
# that dialog picks "No, exit". Pre-answer it for the demo path — both
# spellings, since /tmp is a symlink on macOS and Claude keys projects by the
# physical path. ~/.claude.json is backed up first.
CLAUDE_JSON="$HOME/.claude.json"
APP_PHYS="$(cd "$DEMO_ROOT/app" && pwd -P)"
[ -f "$CLAUDE_JSON" ] && cp "$CLAUDE_JSON" "$CLAUDE_JSON.demo-backup"
python3 - "$CLAUDE_JSON" "$DEMO_ROOT/app" "$APP_PHYS" <<'EOF'
import json, os, sys
p, *paths = sys.argv[1:]
d = json.load(open(p)) if os.path.exists(p) else {}
projects = d.setdefault("projects", {})
for path in dict.fromkeys(paths):
    projects.setdefault(path, {})["hasTrustDialogAccepted"] = True
json.dump(d, open(p, "w"), indent=2)
EOF

# Let Claude run yarn without a permission prompt in frame 3. Project
# settings.json was just written by mnemo init; add the allow-list to it.
# Claude Code's own auto-memory is off for the demo: otherwise frame 1 shows
# Claude saving the preference to MEMORY.md as well, and frame 3 can no
# longer be attributed to mnemo's injection.
python3 - "$DEMO_ROOT/app/.claude/settings.json" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
perms = d.setdefault("permissions", {})
allow = perms.setdefault("allow", [])
for rule in ("Bash(yarn add:*)", "Bash(yarn:*)", "Read"):
    if rule not in allow:
        allow.append(rule)
d["autoMemoryEnabled"] = False
json.dump(d, open(p, "w"), indent=2)
EOF

# Fresh Claude Code state for the demo path too (transcripts and auto-memory
# from an earlier take), so `mnemo learn` reads this take's session only.
for p in "$DEMO_ROOT/app" "$APP_PHYS"; do
  rm -rf "$HOME/.claude/projects/$(printf '%s' "$p" | tr '/' '-')"
done

echo "demo ready at $DEMO_ROOT/app (vault: $DEMO_ROOT/app/.mnemo)"
echo "record with:  cd $REPO_ROOT && vhs tools/demo/loop.tape"
