# PR G — Demo GIF Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A committed vhs tape that records the five-minute loop against the real `claude` CLI in a throwaway repo, producing `docs/assets/loop.gif` for the top of the README.

**Architecture:** Three files under `tools/demo/`: `setup.sh` builds the throwaway repo and vault and refuses to run while the maintainer's global mnemo is active (otherwise two reflexes fire and frame 4 shows two injections); `loop.tape` drives the terminal with `Wait+Screen` regexes so model latency never breaks a frame; `README.md` logs every recording run. No Python changes. The GIF is a build artefact that is committed on purpose, with a size budget.

**Tech Stack:** vhs (`brew install vhs`; brings ttyd + ffmpeg), the real `claude` CLI, `mnemo` from this checkout (`/usr/local/bin/python3 -m mnemo`), bash.

**Spec:** `docs/superpowers/specs/2026-09-02-distribution-design.md` § 3.

**Session rules:** this plan runs in its own worktree/session in parallel with PR F (`docs/superpowers/plans/2026-09-03-pr-f-hosts.md`), which touches `src/`, `tests/`, the README "Commands" section, the getting-started "Taking your rules with you" section and CHANGELOG `### Added`. This plan touches only `tools/demo/`, `docs/assets/`, the README **tagline area** (one image line) and the getting-started **"GIF storyboard"** subsection, plus its own CHANGELOG bullet — keep to those to avoid merge conflicts. Tasks 1–2 are subagent-safe; Task 3 (recording) needs the maintainer at the keyboard with a Claude login and the global mnemo disabled, so it is theirs.

**Facts the tape relies on** (verified in the repo on 2026-09-03):
- `mnemo learn` prints `read: …`, `briefing: … (N correction(s))`, `learned: <slug> — <name> (evidence: "…")`, then `next prompt about this will surface it — check with \`mnemo why\``. Only a rule with a verified quote gets the `evidence:` part.
- `mnemo why` prints `HH:MM:SS  injected  <slug> (score)` for an emission and `HH:MM:SS  silent    …` otherwise.
- The reflex injects through the `UserPromptSubmit` hook's `additionalContext`, which the Claude Code TUI does **not** show; a viewer only sees Claude's behaviour and `mnemo why`.
- `mnemo init --project --vault-root <dir> --yes --no-mirror` writes `<repo>/.claude/settings.json` hooks, `<repo>/.mcp.json`, and the vault at `<dir>`.
- The maintainer's machine wires mnemo through `hooks` in `~/.claude/settings.json` (not through `enabledPlugins`). Other machines may have `mnemo@mnemo-marketplace` under `enabledPlugins`. The guard checks both.
- The slug the LLM picks for the yarn rule is not fixed (`use-yarn-not-npm`, `yarn-over-npm`, …); every regex matches on `yarn`, never on a full slug.

---

## File structure

| Path | Responsibility |
|------|----------------|
| `tools/demo/setup.sh` | build `$DEMO_ROOT` (repo + vault), refuse if a global mnemo is active |
| `tools/demo/loop.tape` | the vhs recording script |
| `tools/demo/README.md` | how to record, and the log of runs (date, model, kept/discarded) |
| `docs/assets/loop.gif` | the artefact (≤ 3 MB) |
| `README.md` | one image line under the tagline |
| `docs/getting-started.md` | "GIF storyboard" subsection rewritten to four frames + pointer to the tape |
| `CHANGELOG.md` | one bullet |

---

### Task 1: `tools/demo/setup.sh`

**Files:**
- Create: `tools/demo/setup.sh` (executable)

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
# Build the throwaway repo + vault the demo tape records against.
#
#   DEMO_ROOT=/tmp/mnemo-demo bash tools/demo/setup.sh
#
# Refuses to run while a global mnemo is active: its reflex would fire next
# to the demo's, and frame 4 would show two injections.
set -euo pipefail

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
echo "record with:  cd $(git rev-parse --show-toplevel 2>/dev/null || pwd) && vhs tools/demo/loop.tape"
```

Make it executable: `chmod +x tools/demo/setup.sh`.

- [ ] **Step 2: Dry-check the guard without touching the real settings**

```bash
HOME=$(mktemp -d) DEMO_ROOT=$(mktemp -d)/demo bash tools/demo/setup.sh; echo "exit=$?"
```

Expected: with an empty fake HOME the guard passes, `claude` must be on PATH (if it is not, the script fails with `'claude' is not on PATH` — acceptable for the check), and on success the tree exists with `app/.claude/settings.json` containing `"Bash(yarn add:*)"` and `app/.mcp.json`. Then, with the real HOME on the maintainer's machine, `bash tools/demo/setup.sh` must **fail** with the "global mnemo hooks are wired" line — that failure is the guard working.

- [ ] **Step 3: Commit**

```bash
git add tools/demo/setup.sh
git commit -m "demo: setup script for the five-minute-loop recording"
```

---

### Task 2: `tools/demo/loop.tape` and `tools/demo/README.md`

**Files:**
- Create: `tools/demo/loop.tape`, `tools/demo/README.md`

- [ ] **Step 1: Write the tape**

```tape
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
Type "add lodash as a dependency"
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
```

Notes for whoever adjusts it: `Wait+Screen@<timeout> /<regex>/` polls the screen until the regex matches (vhs ≥ 0.9). The `/\$ ?$/` wait after `/exit` matches the shell prompt returning; if the maintainer's prompt does not end in `$`, change both occurrences to match it (or set `Set Shell bash` with a plain `PS1`, which vhs does by default). If Claude Code's first screen changes wording, loosen the frame-1/3 wait regex; it only needs to detect "the TUI is up".

- [ ] **Step 2: Write `tools/demo/README.md`**

```markdown
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
```

- [ ] **Step 3: Commit**

```bash
git add tools/demo/loop.tape tools/demo/README.md
git commit -m "demo: vhs tape and recording notes for the five-minute loop"
```

---

### Task 3: Record (maintainer)

**Files:**
- Create: `docs/assets/loop.gif`
- Modify: `tools/demo/README.md` (runs table)

- [ ] **Step 1: Install vhs**

```bash
brew install vhs && vhs --version
```

- [ ] **Step 2: Disable the global mnemo for the session**

```bash
cp ~/.claude/settings.json ~/.claude/settings.json.demo-backup
```

Then remove the mnemo entries from `hooks` in `~/.claude/settings.json` (or `mnemo uninstall --yes`, which also removes the status line and slash commands — the backup restores everything). If `enabledPlugins` has a `mnemo@…` key, set it to `false` for now.

- [ ] **Step 3: Build and record**

```bash
DEMO_ROOT=/tmp/mnemo-demo bash tools/demo/setup.sh
vhs tools/demo/loop.tape
ls -la docs/assets/loop.gif
```

Expected: the tape finishes without a `Wait` timeout; the GIF is under 3 MB. If a wait times out, vhs prints which line; fix the regex (see the notes in the tape) and re-run `setup.sh` + `vhs` — always from a clean `$DEMO_ROOT`.

- [ ] **Step 4: Judge the run**

Open the GIF. All four must hold: the typed correction; a `learned:` line with `evidence: "never use npm in this repo, always yarn"`; Claude running `yarn add lodash`; `mnemo why` showing `injected` with a yarn slug. Any miss → discard, log it, retry from Step 3 (`setup.sh` wipes the tree).

- [ ] **Step 5: Restore the global mnemo**

```bash
cp ~/.claude/settings.json.demo-backup ~/.claude/settings.json && rm ~/.claude/settings.json.demo-backup
```

(and re-enable the plugin if you disabled it). Run `mnemo status` to confirm `Hooks (global): 4/4`.

- [ ] **Step 6: Log and commit**

Add a row to the runs table in `tools/demo/README.md` (date, `claude --version`, kept/discarded with the reason). Then:

```bash
git add docs/assets/loop.gif tools/demo/README.md
git commit -m "demo: record the five-minute loop"
```

---

### Task 4: README, getting-started storyboard, changelog

**Files:**
- Modify: `README.md` (directly under the tagline block, before the "Monday, in your app repo" paragraph)
- Modify: `docs/getting-started.md` ("### GIF storyboard" subsection, ~line 131)
- Modify: `CHANGELOG.md` (`[Unreleased]` → `### Added`)

- [ ] **Step 1: README**

Insert after the `> Claude Code forgets your corrections. mnemo doesn't.` line and its blank line:

```markdown
![The five-minute loop: correct Claude, run mnemo learn, the next prompt already knows](docs/assets/loop.gif)
```

- [ ] **Step 2: Storyboard**

Replace the whole "### GIF storyboard" subsection with:

```markdown
### GIF storyboard

`docs/assets/loop.gif` is recorded by `tools/demo/loop.tape` against the real
`claude` CLI in a throwaway repo (`tools/demo/README.md` has the procedure and
the log of runs). Four frames, about 30 seconds, no cuts mid-frame:

| # | On screen | Hold |
|---|-----------|------|
| 1 | `claude` in the demo repo; the user types `never use npm in this repo, always yarn`; Claude acknowledges. | 3s |
| 2 | `/exit`, then `mnemo learn`: the `learned:` line, with `evidence: "never use npm in this repo, always yarn"` — your own sentence, carried back. | 6s |
| 3 | `claude` again; the user types `add lodash as a dependency`; Claude runs `yarn add lodash`, not `npm install`. | 3s |
| 4 | `/exit`, then `mnemo why`: the top entry reads `injected  <slug>`. | 5s |

Frame 4 exists because the injection itself is invisible in the TUI
(`UserPromptSubmit` context is not rendered); what a viewer can see is
Claude's behaviour and the receipt. Frames 2 and 4 are the proof and hold
longest. A run where Claude does not reach for yarn is discarded and logged,
never edited.
```

- [ ] **Step 3: CHANGELOG**

Under `## [Unreleased]` → `### Added`:

```markdown
- **Demo GIF.** `docs/assets/loop.gif` shows the five-minute loop — correct
  Claude, `mnemo learn`, the next prompt already knows, `mnemo why` shows the
  receipt — recorded from `tools/demo/loop.tape` against the real `claude`
  CLI in a throwaway repo (`tools/demo/setup.sh`).
```

- [ ] **Step 4: Commit and PR**

```bash
git add README.md docs/getting-started.md CHANGELOG.md
git commit -m "docs: demo GIF in the README and the four-frame storyboard"
```

Branch `feat/pr-g-demo-gif`, title `docs: five-minute-loop demo GIF (vhs tape + recording)`. Body: spec § 3 link, the run log row, GIF size, and a note that no Python changed. CI is docs-only but must still be green. If PR F merged first, rebase; the only shared files are README and CHANGELOG, in different sections.

---

## Self-review against the spec § 3

- Committed vhs tape, real `claude`, throwaway repo, fresh vault: Tasks 1–2. ✔
- `setup.sh` asserts the maintainer's global mnemo is not active (hooks *and* plugin), exits 1 with the instruction: Task 1. ✔
- Tape settings (100×30-ish → 1000×600 px at 16 pt, `PlaybackSpeed 1.0`, `Wait+Screen@60s` style waits, output `docs/assets/loop.gif`): Task 2. ✔
- GIF committed, 3 MB budget, reduce size before durations: Task 3. ✔
- README image under the tagline, existing "Check it worked" text untouched: Task 4. ✔
- Storyboard rewritten to four frames pointing at the tape: Task 4. ✔
- Discard-and-log rule, never edit, README never carries an unlogged run: Task 2 README + Task 3 Step 4. ✔
- Frames: correction / `learned:` + evidence / `yarn add lodash` / `mnemo why` `injected`: Task 2 tape. ✔
- Acceptance "GIF twice in a row from a clean `$DEMO_ROOT`": Task 3 Step 3 (`setup.sh` wipes the tree; record twice if in doubt). ✔
