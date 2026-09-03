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
mkdir -p "$DEMO_ROOT/app" "$DEMO_ROOT/vault"
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

# mnemo, project-scoped, with its own vault. --no-mirror: nothing to mirror.
$MNEMO init --project --vault-root "$DEMO_ROOT/vault" --yes --no-mirror --quiet

# Let Claude run yarn without a permission prompt in frame 3. Project
# settings.json was just written by mnemo init; add the allow-list to it.
python3 - "$DEMO_ROOT/app/.claude/settings.json" <<'EOF'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
perms = d.setdefault("permissions", {})
allow = perms.setdefault("allow", [])
for rule in ("Bash(yarn add:*)", "Bash(yarn:*)", "Read", "Edit", "Write"):
    if rule not in allow:
        allow.append(rule)
json.dump(d, open(p, "w"), indent=2)
EOF

echo "demo ready at $DEMO_ROOT/app (vault: $DEMO_ROOT/vault)"
echo "record with:  cd $REPO_ROOT && vhs tools/demo/loop.tape"
