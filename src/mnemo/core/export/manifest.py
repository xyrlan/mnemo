"""What ``mnemo export`` last wrote for a project.

Lives under the vault's ``.mnemo/export/<project>.json`` — next to the
learned ledger and the migration markers, outside every repo — so the only
thing an export leaves in a repo is the rules file itself.

Two readers: ``mnemo status`` (is the file stale?) and the UserPromptSubmit
hook (which slugs is Claude Code already loading, so the reflex must not
inject them again).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Set, Tuple

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.export.render import START_MARKER

MANIFEST_DIR_REL = ".mnemo/export"

# Targets Claude Code loads by itself; injecting these again is a repeat.
_CLAUDE_LOADED = {("claude", "rules"), ("claude", "claude-md")}


def manifest_path(vault_root: Path, project: str) -> Path:
    return Path(vault_root) / MANIFEST_DIR_REL / f"{project}.json"


def write_manifest(
    vault_root: Path, project: str, *, host: str, target: str, cwd: str,
    path: str, rules: Dict[str, str], format: Optional[str] = None,
) -> None:
    """*format* is ``"compact"`` or ``"full"`` — the rendering the ``rules``
    hashes were taken in, so ``staleness`` compares like with like. ``None``
    leaves the key out, which readers treat as ``"full"``: that is what every
    manifest written before the compact format existed was."""
    data = {
        "host": host,
        "target": target,
        "cwd": cwd,
        "path": path,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rules": dict(rules),
    }
    if format is not None:
        data["format"] = format
    atomic_write_bytes(manifest_path(vault_root, project),
                       (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8"))


def manifest_is_full(data: dict) -> bool:
    """Whether the manifest's hashes came from a full export (missing = full)."""
    return data.get("format", "full") != "compact"


def read_manifest(vault_root: Path, project: str) -> Optional[dict]:
    p = manifest_path(vault_root, project)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rules"), dict) else None


def delete_manifest(vault_root: Path, project: str) -> bool:
    p = manifest_path(vault_root, project)
    if not p.exists():
        return False
    p.unlink()
    return True


def exported_slugs_for(vault_root: Path, project: str, *, repo_root: str) -> Set[str]:
    """Slugs the host is already loading for this repo, else empty.

    Checked by file, not by the manifest's recorded ``cwd``: the hook passes
    ``repo_root`` as the tree it is standing in right now (via
    ``resolve_agent``), so that is the only tree probed. The manifest's
    ``cwd`` is informational — read by ``mnemo status`` to show where the
    export was last written from — and is never used to locate the file
    here. A sibling git worktree with no rules file of its own must read as
    "nothing exported", even if another worktree recorded in the manifest's
    ``cwd`` still has one; probing ``cwd`` as a fallback would leak that
    worktree's export into this one. The file existing under ``repo_root``
    and still carrying the mnemo marker is the actual fact we care about, so
    a stale or hand-edited file that lost its marker still doesn't count.
    """
    data = read_manifest(vault_root, project)
    if not data:
        return set()
    if (data.get("host"), data.get("target")) not in _CLAUDE_LOADED:
        return set()
    rel = data.get("path")
    if not isinstance(rel, str) or not rel:
        return set()

    target_path = Path(repo_root) / rel
    try:
        if not target_path.is_file():
            return set()
        text = target_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    if START_MARKER not in text:
        return set()
    return {s for s in data["rules"] if isinstance(s, str)}


def staleness(vault_root: Path, project: str, *, current: Dict[str, str]) -> Optional[Tuple[int, int]]:
    """(rules in the file, rules that differ from the vault now) or None."""
    data = read_manifest(vault_root, project)
    if not data:
        return None
    exported = data["rules"]
    changed = sum(1 for s, h in exported.items() if current.get(s) != h)
    added = sum(1 for s in current if s not in exported)
    return len(exported), changed + added
