"""Execute a rewrite plan: archive, write, delete the proposal, advance the ledger.

The vault is not a git repository and only 1 of the real vault's 35 proposals has
any archive coverage, so there is no rollback path but the one this module
creates. Shape mirrors ``reclassify_apply``: pristine ``originals/`` plus a
``manifest.json`` that :func:`undo` replays.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.rewrites import merge as M
from mnemo.core.rewrites.classify import classify
from mnemo.core.rewrites.types import ApplyPlan, ApplyReport, Rewrite

_ACTION_FOR_KIND = {
    "insert_only": "merge",
    "mixed": "replace",
    "full_rewrite": "replace",
}


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def plan(vault_root: Path, *, include: set[str]) -> ApplyPlan:
    """Build a plan covering exactly the rewrites whose ``key`` is in *include*."""
    entries: list[tuple[Rewrite, str]] = []
    for r in classify(vault_root):
        if r.key in include:
            entries.append((r, _ACTION_FOR_KIND[r.kind]))
    return ApplyPlan(run_id=_run_id(), entries=entries)


def apply(plan_obj: ApplyPlan, vault_root: Path) -> ApplyReport:
    """Execute *plan_obj*, keeping byte-exact originals for :func:`undo`."""
    vault_root = Path(vault_root)
    report = ApplyReport()
    if not plan_obj.entries:
        return report

    arch = vault_root / "shared" / "_archive" / f"rewrites-{plan_obj.run_id}"
    # Re-applying a run would copy already-merged files over the pristine
    # originals and overwrite the manifest, silently destroying undo. Guard on
    # the manifest, not the directory — same as reclassify_apply.
    if (arch / "manifest.json").exists():
        raise RuntimeError(f"run {plan_obj.run_id} already applied; undo it first")
    originals = arch / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    report.archive_dir = arch

    state_path = vault_root / ".mnemo" / "extraction-state.json"
    state: dict = {"entries": {}}
    state_backup: str | None = None
    if state_path.exists():
        backup = originals / "extraction-state.json"
        shutil.copy2(state_path, backup)
        state_backup = str(backup.relative_to(vault_root))
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {"entries": {}}
    state.setdefault("entries", {})

    moves: list[dict] = []
    for rewrite, action in plan_obj.entries:
        if action == "skip":
            report.skipped.append({"key": rewrite.key, "reason": "skipped by plan"})
            continue
        try:
            live_text = rewrite.live.read_text(encoding="utf-8")
            prop_text = rewrite.proposal.read_text(encoding="utf-8")
        except OSError as exc:
            report.skipped.append({"key": rewrite.key, "reason": f"read: {exc}"})
            continue

        # Pristine original first — nothing is overwritten before it is archived.
        shutil.copy2(rewrite.live, originals / f"{rewrite.live.stem}.md")

        builder = M.merge_insert_only if action == "merge" else M.replace_wholesale
        merged = builder(live_text, prop_text, vault_root=vault_root)
        atomic_write(rewrite.live, merged)
        rewrite.proposal.unlink()

        # THE FIX: advance written_hash to what is now on disk. Without this the
        # merged file reads as a user edit and the next run re-proposes it.
        entry = state["entries"].get(rewrite.key)
        if entry is not None:
            entry["written_hash"] = content_hash(rewrite.live)
            entry["written_at"] = plan_obj.run_id
            entry["last_sync"] = plan_obj.run_id
        else:
            report.notes.append(f"{rewrite.key}: no state entry; hash not reconciled")

        moves.append({
            "key": rewrite.key,
            "slug": rewrite.live.stem,
            "kind": rewrite.kind,
            "action": action,
            "live_path": str(rewrite.live.relative_to(vault_root)),
            "proposal_path": str(rewrite.proposal.relative_to(vault_root)),
        })
        if action == "merge":
            report.merged += 1
        else:
            report.replaced += 1

    (arch / "manifest.json").write_text(
        json.dumps({
            "run_id": plan_obj.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "moves": moves,
            "skipped": report.skipped,
            "state_backup": state_backup,
        }, indent=2),
        encoding="utf-8",
    )

    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_bytes(json.dumps(state, indent=2).encode("utf-8"))
    tmp.replace(state_path)
    return report


def undo(vault_root: Path, run_id: str) -> int:
    """Restore every file *run_id* touched, byte for byte. Returns files restored."""
    vault_root = Path(vault_root)
    arch = vault_root / "shared" / "_archive" / f"rewrites-{run_id}"
    manifest_path = arch / "manifest.json"
    if not manifest_path.exists():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    originals = arch / "originals"
    restored = 0

    for move in manifest.get("moves") or []:
        src = originals / f"{move.get('slug')}.md"
        dest = vault_root / str(move.get("live_path") or "")
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            restored += 1

    backup_rel = manifest.get("state_backup")
    if backup_rel:
        backup = vault_root / str(backup_rel)
        if backup.exists():
            state_path = vault_root / ".mnemo" / "extraction-state.json"
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_bytes(backup.read_bytes())
            restored += 1
    return restored
