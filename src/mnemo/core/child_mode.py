"""A dispatched child runs in its parent's permission class (#741).

Nobody chose the mode a child ran in: ``claude --bg`` without
``--permission-mode`` starts ``auto``, which is in the *prompting* class. A
``bypass`` parent's inbound policy holds a peer turn from another class for the
user's approval (:mod:`mnemo.core.sessions.inbound_policy`), so every finish
notice from a child of a bypass parent waited for a click, and the only lever
was ``"crossSessionInbound": "accept"`` in the operator's own settings.

**The class, not the mode.** A child of a ``bypassPermissions`` parent is
started with ``--permission-mode bypassPermissions``; its notice is then
attested ``bypass`` and accepted with no setting at all. A child of a
prompting parent keeps ``auto``, whatever the parent's exact mode: an
unattended ``default`` child would stop at its first prompt with nobody there
to answer it, and a prompting parent already accepts an unattested notice.
``plan``, an unreadable transcript and a dispatch from a plain terminal keep
``auto`` too. The operator chose to let the parent act without prompts, and
the child does the parent's work; what it may *publish* is still ``--may``'s.

**Measured on 2.1.306 (2026-10-21), on throwaway ``--bg`` sessions:**

- ``--disallowedTools Edit Write NotebookEdit`` still removes those tools under
  ``bypassPermissions``: a read-only child asked to use ``Write`` found it in
  neither its tool list nor ToolSearch, and wrote nothing. So a read-only
  dispatch inherits the class like any other.
- ``--bg --resume <id>`` restores the saved ``--permission-mode`` (the CLI
  prints "woke session … with its saved options (--permission-mode,
  --disallowedTools, --model)", and the resumed turns record
  ``bypassPermissions``), so ``sessions/wake.py`` passes nothing.
- With ``permissions.disableBypassPermissionsMode: "disable"`` in a settings
  file, ``--permission-mode bypassPermissions`` neither fails nor warns: the
  child quietly runs ``auto``. So that setting is read here, wherever mnemo
  can read it, and the dispatch says the child keeps ``auto`` and why. Where
  it cannot be read (MDM, server-managed policy) the child still starts, in
  ``auto``, and ``child-reports.jsonl`` records the mode it really ran.

``dispatch.childMode: "auto"`` in the config opts out. Grok children have no
such mode and are never asked. Nothing here writes ``crossSessionInbound`` or
attests anything: the attestation stays the child's real mode, read from its
transcript when the notice is sent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from mnemo.core.sessions import inbound_policy

MATCH_PARENT = "match-parent"
AUTO = "auto"
BYPASS_MODE = "bypassPermissions"


@dataclass(frozen=True)
class Choice:
    """The ``--permission-mode`` to start a child with, or None for ``auto``.

    ``note`` says why, in words for the dispatch report; ``""`` when the child
    runs ``auto`` for the ordinary reason (a prompting parent, no parent).
    """

    mode: Optional[str] = None
    note: str = ""


def wanted(cfg: Optional[Mapping[str, Any]]) -> bool:
    """False only when ``dispatch.childMode`` is ``auto``.

    An unknown value keeps the default, as ``childPriority`` does: a typo must
    neither change the child's class nor abort the dispatch.
    """
    section = cfg.get("dispatch") if isinstance(cfg, Mapping) else None
    raw = section.get("childMode") if isinstance(section, Mapping) else None
    return not (isinstance(raw, str) and raw.strip().lower() == AUTO)


def _disables_bypass(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    perms = data.get("permissions") if isinstance(data, dict) else None
    return isinstance(perms, dict) and perms.get("disableBypassPermissionsMode") == "disable"


def bypass_disabled_by(cwd: Path | str, *, home: Optional[Path] = None,
                       managed: Optional[Iterable[Path]] = None) -> Optional[Path]:
    """The settings file that turns bypass off for a child in *cwd*, or None.

    Managed policy, the user's settings and the tree's two project files. A
    lean child does not load the user's file, but a user who disabled bypass
    is not asked to have their children run it.
    """
    paths = list(inbound_policy.managed_settings_paths() if managed is None else managed)
    if home is not None:
        paths.append(Path(home) / "settings.json")
    paths += [Path(cwd) / ".claude" / name for name in ("settings.json", "settings.local.json")]
    return next((p for p in paths if _disables_bypass(p)), None)


def choose(cfg: Optional[Mapping[str, Any]], *, cwd: Path | str,
           env: Optional[Mapping[str, str]] = None,
           homes: Optional[Iterable[Path]] = None,
           managed: Optional[Iterable[Path]] = None) -> Choice:
    """What a Claude child dispatched into *cwd* from this process runs in.

    Never raises: anything unreadable leaves the child in ``auto``, as before.
    """
    try:
        if not wanted(cfg):
            return Choice()
        from mnemo.core.sessions import parents

        parent = parents.parent_from_env(env)
        if parent is None:
            return Choice()
        if homes is None:
            from mnemo.core.sessions import accounts

            homes = [root.home for root in accounts.roots()]
        found = inbound_policy.find_transcript(parent, homes)
        if found is None:
            return Choice()
        transcript, home = found
        if inbound_policy.mode_class(inbound_policy.last_mode(transcript)) != inbound_policy.BYPASS:
            return Choice()
        blocker = bypass_disabled_by(cwd, home=home, managed=managed)
        if blocker is not None:
            return Choice(note=f"auto, not bypassPermissions like this session: {blocker} "
                               "sets permissions.disableBypassPermissionsMode")
        return Choice(BYPASS_MODE, "bypassPermissions, like this session "
                                   "(dispatch.childMode: auto opts out)")
    except Exception:  # noqa: BLE001 — never raises: auto is today's behaviour
        return Choice()
