"""Doctor check: an installed mnemo hook matches fewer tools than this version ships.

``inject_hooks`` writes each hook's ``matcher`` once, at ``mnemo init``. When a
release widens one — #271 added ``Read`` to ``PreToolUse`` so enrichment fires
when a file is opened — nothing rewrites the installs that already exist, and
``mnemo status`` still counts the hook as healthy because the command is there.
Measured on the maintainer's machine over 2026-W37, the missing ``Read`` halved
what enrichment delivered (23 notes against 40) with no signal anywhere (#303).

Stateless on purpose: it compares what is on disk now with what the running
code ships, so it notices drift introduced by any later upgrade — unlike a
notice printed at install time.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# A matcher mnemo can reason about is a plain ``A|B|C`` alternation. Anything
# with regex syntax in it is the user's own edit, and guessing what it matches
# would turn a diagnostic into a false alarm.
_TOOL_NAME = re.compile(r"^\w+$")

# Claude Code treats an absent, empty or ``*`` matcher as "every tool".
_MATCH_ALL = (None, "", "*")


def _mnemo_matchers(entries: object) -> list[object]:
    from mnemo.install.settings import is_mnemo_hook_command

    matchers: list[object] = []
    if not isinstance(entries, list):
        return matchers
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks")
        if not isinstance(hooks, list):
            continue
        if any(
            isinstance(h, dict) and is_mnemo_hook_command(h.get("command", ""))
            for h in hooks
        ):
            matchers.append(entry.get("matcher"))
    return matchers


def missing_tools(settings: dict) -> dict[str, list[str]]:
    """Map each hook event to the shipped tools its installed matcher lacks.

    Only events that carry a mnemo entry and ship a matcher are judged. With
    several mnemo entries on one event (a duplicate install) Claude Code fires
    all of them, so a tool covered by any one of them is covered.
    """
    from mnemo.install.settings import HOOK_DEFINITIONS

    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return {}
    drift: dict[str, list[str]] = {}
    for event, defn in HOOK_DEFINITIONS.items():
        shipped = defn.get("matcher")
        if not shipped:
            continue
        matchers = _mnemo_matchers(hooks.get(event))
        if not matchers:
            continue
        covered: set[str] = set()
        judgeable = True
        for matcher in matchers:
            if matcher in _MATCH_ALL:
                judgeable = False
                break
            if not isinstance(matcher, str):
                judgeable = False
                break
            tokens = [t.strip() for t in matcher.split("|")]
            if not all(_TOOL_NAME.match(t) for t in tokens):
                judgeable = False
                break
            covered.update(tokens)
        if not judgeable:
            continue
        lacking = [t for t in shipped.split("|") if t not in covered]
        if lacking:
            drift[event] = lacking
    return drift


def check_hook_matcher(
    claude_dir: Path | None = None, cwd: Path | None = None,
) -> list[str] | None:
    """Return finding lines, or None when every installed matcher is current.

    Both scopes ``mnemo init`` writes are read: the global
    ``~/.claude/settings.json`` and the project ``<cwd>/.claude/settings.json``.
    """
    import os

    from mnemo.install.settings import HOOK_DEFINITIONS

    home_claude = claude_dir if claude_dir is not None else Path(os.path.expanduser("~/.claude"))
    here = cwd if cwd is not None else Path.cwd()
    scopes = [
        (home_claude / "settings.json", "mnemo init --hooks-only"),
        (here / ".claude" / "settings.json", "mnemo init --project --hooks-only"),
    ]

    findings: list[str] = []
    seen: set[Path] = set()
    for path, remedy in scopes:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue  # a malformed settings.json is preflight's to report
        if not isinstance(data, dict):
            continue
        drift = missing_tools(data)
        if not drift:
            continue
        for event, lacking in drift.items():
            names = ", ".join(lacking)
            verb = "never reaches" if len(lacking) == 1 else "never reach"
            findings.append(
                f"{event} hook in {path} predates this version's matcher: "
                f"{names} {verb} it (ships `{HOOK_DEFINITIONS[event]['matcher']}`)"
            )
        findings.append(
            f"    → run `{remedy}` — rewrites mnemo's hooks and nothing else "
            "(config, statusLine and MCP are left as they are)"
        )
    return findings or None


def _doctor_check_hook_matcher(
    vault: Path, claude_dir: Path | None = None, cwd: Path | None = None,
) -> bool:
    """Doctor-registry adapter — True when silent, False on warning.

    ``vault`` is unused: hooks are wired per machine or per project, not per
    vault. ``claude_dir`` and ``cwd`` are injectable because ``HOME`` is ignored
    by ``expanduser`` on Windows (see ``duplicate_install``).
    """
    try:
        findings = check_hook_matcher(claude_dir, cwd)
    except Exception:
        # A diagnostic must never be what breaks `mnemo doctor`.
        return True
    if not findings:
        return True
    for msg in findings:
        print(f"  ⚠ {msg}" if not msg.startswith("    ") else f"  {msg}")
    return False
