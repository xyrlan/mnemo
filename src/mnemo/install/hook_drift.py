"""Drift between the hook matchers on disk and the ones this version ships.

``inject_hooks`` writes each hook's ``matcher`` once, at ``mnemo init``. When a
release widens one — #271 added ``Read`` to ``PreToolUse`` so enrichment fires
when a file is opened — nothing rewrites the installs that already exist, and
``mnemo status`` still counts the hook as healthy because the command is there.
Measured on the maintainer's machine over 2026-W37, the missing ``Read`` halved
what enrichment delivered (23 notes against 40) with no signal anywhere (#303).

#303 gave that a ``doctor`` row and ``mnemo init --hooks-only`` to repair it.
#337 is the observation that both are opt-in: the row only speaks to someone
already running ``doctor``, so a shipped matcher change still reaches no
install whose owner never does. The detection lives here, out of the doctor
package, because three callers now need it — the doctor row, the ``mnemo
status`` line, and the session-start repair that closes the loop unasked.

Stateless on purpose: it compares what is on disk now with what the running
code ships, so it notices drift introduced by any later upgrade — unlike a
notice printed at install time.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# A matcher mnemo can reason about is a plain ``A|B|C`` alternation. Anything
# with regex syntax in it is the user's own edit, and guessing what it matches
# would turn a diagnostic into a false alarm.
_TOOL_NAME = re.compile(r"^\w+$")

# Claude Code treats an absent, empty or ``*`` matcher as "every tool".
_MATCH_ALL = (None, "", "*")

#: Where :func:`auto_repair` records the drift it has already fixed, relative
#: to the vault root. One entry per (scope, missing tools) pair.
REPAIR_MARKER_REL = ".mnemo/hook-matcher-repairs.json"


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


def _reach(lacking: list[str]) -> str:
    verb = "never reaches" if len(lacking) == 1 else "never reach"
    return f"{', '.join(lacking)} {verb} it"


@dataclass(frozen=True)
class Drift:
    """One settings.json scope whose mnemo matchers are narrower than shipped."""

    path: Path
    missing: dict[str, list[str]]
    project: bool

    @property
    def scope(self) -> str:
        return "project" if self.project else "global"

    @property
    def remedy(self) -> str:
        """The command that repairs this scope — and nothing else (#303)."""
        return "mnemo init --project --hooks-only" if self.project else "mnemo init --hooks-only"

    @property
    def signature(self) -> str:
        """Identity of this drift, for the once-per-drift repair marker.

        Keyed on the scope *and* on what is missing, so the next release that
        widens a matcher is repaired again, while a matcher the user narrowed
        back by hand after a repair is left alone instead of being fought
        every session.
        """
        body = ";".join(f"{ev}:{','.join(tools)}" for ev, tools in sorted(self.missing.items()))
        return f"{self.path}|{body}"

    def doctor_lines(self) -> list[str]:
        """One finding per drifted event, as ``mnemo doctor`` prints them."""
        from mnemo.install.settings import HOOK_DEFINITIONS

        return [
            f"{event} hook in {self.path} predates this version's matcher: "
            f"{_reach(lacking)} (ships `{HOOK_DEFINITIONS[event]['matcher']}`)"
            for event, lacking in self.missing.items()
        ]

    def status_lines(self) -> list[str]:
        """One line per drifted event for ``mnemo status``, remedy included."""
        return [
            f"Hooks ({self.scope}): {event} matcher predates this version — "
            f"{_reach(lacking)}. Run `{self.remedy}`"
            for event, lacking in self.missing.items()
        ]

    def repair_notice(self) -> str:
        """What the session-start repair says on stderr once it has written."""
        events = ", ".join(
            f"{event} ({', '.join(lacking)})" for event, lacking in self.missing.items()
        )
        return (
            f"[mnemo] hook matcher in {self.path} predated this version — {events} "
            f"never reached it. Repaired in place (previous file backed up alongside it); "
            f"new sessions pick it up. Set install.autoRepairHooks=false to stop this."
        )


def scan(claude_dir: Path | None = None, cwd: Path | None = None) -> list[Drift]:
    """Every settings.json scope ``mnemo init`` writes that has drifted.

    Both scopes are read: the global ``~/.claude/settings.json`` and the
    project ``<cwd>/.claude/settings.json``. A plugin install declares its
    hooks in the plugin's own ``hooks.json`` — versioned with the code, and
    held to ``HOOK_DEFINITIONS`` by a manifest test — so it has no entry in
    either file and is silently and correctly judged drift-free here.
    """
    home_claude = claude_dir if claude_dir is not None else Path(os.path.expanduser("~/.claude"))
    here = cwd if cwd is not None else Path.cwd()
    scopes = [
        (home_claude / "settings.json", False),
        (here / ".claude" / "settings.json", True),
    ]

    drifts: list[Drift] = []
    seen: set[Path] = set()
    for path, project in scopes:
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
        missing = missing_tools(data)
        if missing:
            drifts.append(Drift(path=path, missing=missing, project=project))
    return drifts


def repair(drift: Drift) -> None:
    """Rewrite mnemo's hooks in one scope — exactly ``init --hooks-only`` does.

    ``inject_hooks`` backs the file up first and holds the same lock every
    other writer takes, and it touches nothing outside mnemo's own entries:
    config, statusLine, MCP and another tool's hooks come through unchanged.
    """
    from mnemo.install import settings as inj

    inj.inject_hooks(drift.path)


def _read_marker(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write_marker(path: Path, data: dict[str, str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass  # the repair already landed; losing the marker only costs a retry


def auto_repair(
    vault: Path,
    *,
    claude_dir: Path | None = None,
    cwd: Path | None = None,
) -> list[str]:
    """Repair every drifted scope this vault has not repaired before.

    Returns one notice line per scope written, for the caller to print. The
    marker is what keeps this from being a fight: a drift is repaired once,
    so a user who deliberately narrows a matcher afterwards keeps their edit
    and only ``doctor`` still mentions it.
    """
    from mnemo.install.settings import SettingsError

    drifts = scan(claude_dir=claude_dir, cwd=cwd)
    if not drifts:
        return []
    marker = Path(vault) / REPAIR_MARKER_REL
    done = _read_marker(marker)
    notices: list[str] = []
    added: dict[str, str] = {}
    for drift in drifts:
        if drift.signature in done:
            continue
        try:
            repair(drift)
        except (SettingsError, OSError):
            # Another session holds the lock, or the file is not ours to
            # write. Leaving the marker alone means the next session retries.
            continue
        added[drift.signature] = datetime.now().isoformat(timespec="seconds")
        notices.append(drift.repair_notice())
    if added:
        _write_marker(marker, {**done, **added})
    return notices
