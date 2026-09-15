"""Doctor check: the skills mnemo ships are where Claude Code looks, and readable.

A skill only exists for a session if a ``SKILL.md`` sits at
``<scope>/skills/<name>/SKILL.md`` *and* opens with its frontmatter — anything
above the first ``---`` makes Claude Code read the whole file as body, so the
skill is never indexed and never offered (#233). Both failures are silent:
nothing prints when a skill is missing, and ``mnemo status`` counts hooks, not
skills. The skill that says which verbs are a session's own and which are the
maintainer's (#327) is worth nothing if it never loads, and the only way to
find out today is to look on disk.

Stateless, like ``hook_matcher``: it compares what is installed now with what
the running code ships, so drift introduced by any later upgrade shows up.

Scoped to the installs that exist. A machine that carries mnemo's hooks in
``~/.claude/settings.json`` is expected to carry its skills next to them; a
machine running the plugin gets them from the plugin root instead, and a
checkout with neither is not judged at all.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

#: One provider of skills: where its ``skills/`` lives, how it is named in a
#: finding, the command that writes it, and whether ``inject_skills`` owns that
#: directory (the two ``mnemo init`` scopes do; a plugin root does not).
#: ``typing.Tuple`` because an alias is evaluated at import, and ``tuple[...]``
#: is a TypeError before 3.9 — which CI runs and this suite does not.
_Provider = Tuple[Path, str, str, bool]


def _settings_wires_mnemo(path: Path) -> bool:
    """True when ``path`` is a settings.json with mnemo's own hooks in it."""
    from mnemo.install.settings import is_mnemo_hook_command

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False  # a malformed settings.json is preflight's to report
    if not isinstance(data, dict):
        return False
    hooks = data.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for hook in entry.get("hooks") or []:
                if isinstance(hook, dict) and is_mnemo_hook_command(hook.get("command", "")):
                    return True
    return False


def _providers(claude_dir: Path, cwd: Path, *, env_root: bool) -> list[_Provider]:
    """Every place this machine could be serving mnemo's skills from.

    The two ``mnemo init`` scopes are included only when their settings.json
    actually wires mnemo, so a bare checkout — and CI — is silent rather than
    told to install something it never installed.
    """
    found: list[_Provider] = []
    for settings, label, remedy in (
        (claude_dir / "settings.json", str(claude_dir / "skills"), "mnemo init"),
        (cwd / ".claude" / "settings.json", str(cwd / ".claude" / "skills"),
         "mnemo init --project"),
    ):
        if settings.is_file() and _settings_wires_mnemo(settings):
            found.append((settings.parent / "skills", label, remedy, True))

    # The plugin ships its skills at its own root; nothing writes them into
    # ~/.claude, so a plugin-only machine has no init scope to look at.
    for root in _plugin_roots(claude_dir, cwd, env_root=env_root):
        found.append((root / "skills", f"plugin {root}",
                      "claude plugin update mnemo@mnemo-marketplace", False))
    return found


def _plugin_roots(claude_dir: Path, cwd: Path, *, env_root: bool) -> list[Path]:
    """Roots of every enabled mnemo plugin install, plus this process's own.

    ``CLAUDE_PLUGIN_ROOT`` is read only when the caller did not inject a
    ``claude_dir``: an injected one describes a whole machine (a fixture tree,
    or another install being inspected), and the environment this process
    happens to carry is not part of it.
    """
    roots: list[Path] = []
    own = os.environ.get("CLAUDE_PLUGIN_ROOT") if env_root else None
    if own:
        roots.append(Path(own))
    try:
        from mnemo.install import duplicate_install as dup

        for install in dup.find_duplicate_installs(claude_dir, cwd):
            if install.enabled and install.install_path is not None:
                roots.append(Path(install.install_path))
    except Exception:
        pass
    seen: set[str] = set()
    unique: list[Path] = []
    for root in roots:
        key = str(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _inspect(path: Path, name: str) -> tuple[str | None, bool]:
    """``(why Claude Code would not load it, whether it is mnemo's copy)``.

    The tag matters for the remedy, not for the diagnosis: ``inject_skills``
    rewrites a tagged file and leaves an untagged one alone, so telling someone
    to re-run ``mnemo init`` over a skill they wrote themselves would be wrong.
    """
    from mnemo.install.settings import SKILL_TAG

    try:
        body = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return "cannot be read", False
    ours = SKILL_TAG in body
    if not body.startswith("---\n"):
        return ("does not open with its frontmatter, so Claude Code reads the "
                "whole file as body and never indexes it"), ours
    close = body.find("\n---\n", 3)
    if close == -1:
        return "has an unterminated frontmatter block", ours
    if f"name: {name}" not in body[:close]:
        return f"declares a different `name:` than its directory ({name})", ours
    if not body[close + len("\n---\n"):].strip():
        return "is frontmatter with no body", ours
    return None, ours


def check_skills(
    claude_dir: Path | None = None, cwd: Path | None = None,
) -> list[str] | None:
    """Return finding lines, or None when every shipped skill is loadable.

    A skill counts as installed when *any* provider carries a loadable copy:
    Claude Code merges the plugin's skills with the user's, so one good copy is
    enough to make the skill reachable.
    """
    from mnemo.install.settings import SKILLS

    home = claude_dir if claude_dir is not None else Path(os.path.expanduser("~/.claude"))
    here = cwd if cwd is not None else Path.cwd()

    providers = _providers(Path(home), Path(here), env_root=claude_dir is None)
    if not providers:
        return None

    findings: list[str] = []
    for name in SKILLS:
        loadable = False
        broken: list[str] = []
        remedies: list[str] = []
        for skills_dir, label, remedy, init_scope in providers:
            target = skills_dir / name / "SKILL.md"
            if not target.is_file():
                remedies.append(f"    → `{remedy}` writes it to {label}")
                continue
            why, tagged = _inspect(target, name)
            if why is None:
                loadable = True
                continue
            broken.append(f"skill `{name}` at {target} {why}")
            if init_scope and not tagged:
                remedies.append(
                    f"    → that copy is not mnemo's and `{remedy}` leaves it alone — "
                    "fix or remove it"
                )
            else:
                remedies.append(f"    → `{remedy}` rewrites it")
        if not broken and loadable:
            continue
        if not broken:
            where = ", ".join(label for _dir, label, _cmd, _scope in providers)
            findings.append(
                f"skill `{name}` is not installed where Claude Code looks ({where}) — "
                "a session that would have loaded it gets nothing"
            )
        else:
            # A broken copy is named even when another provider has a good one:
            # which copy a session gets is not ours to predict.
            findings.extend(broken)
        findings.extend(dict.fromkeys(remedies))
    return findings or None


def _doctor_check_skills(
    vault: Path, claude_dir: Path | None = None, cwd: Path | None = None,
) -> bool:
    """Doctor-registry adapter — True when silent, False on warning.

    ``vault`` is unused: skills are installed per machine or per project, never
    per vault. ``claude_dir`` and ``cwd`` are injectable for the same reason
    ``duplicate_install``'s are — ``expanduser`` ignores ``HOME`` on Windows.
    """
    try:
        findings = check_skills(claude_dir, cwd)
    except Exception:
        # A diagnostic must never be what breaks `mnemo doctor`.
        return True
    if not findings:
        return True
    for msg in findings:
        print(f"  ⚠ {msg}" if not msg.startswith("    ") else f"  {msg}")
    return False
