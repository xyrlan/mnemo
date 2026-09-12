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
from typing import Callable

from mnemo.core import locks
from mnemo.core.extract.inbox.io import atomic_write, content_hash
from mnemo.core.rewrites import merge as M
from mnemo.core.rewrites.classify import classify
from mnemo.core.rewrites.types import ApplyPlan, ApplyReport, Rewrite


class VaultBusy(RuntimeError):
    """Another ``apply`` run holds the vault lock.

    Raised rather than returning an empty report, so a caller can tell "nothing
    was staged" from "someone else is mid-write". Existing ``try_lock`` callers
    return early on contention, which is right for a background mirror but wrong
    here: this writes rules and reconciles the ledger, and a silent no-op would
    read as success.
    """

_ACTION_FOR_KIND = {
    "insert_only": "merge",
    "mixed": "replace",
    "full_rewrite": "replace",
}


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _atomic_json(path: Path, payload: dict, run_id: str) -> None:
    """Write *payload* as JSON via a run-scoped ``.tmp`` + ``os.replace``.

    Shared by the manifest and the ledger so neither can be left truncated. The
    ``run_id`` in the temp name keeps two runs from colliding on one scratch
    path even if the lock above ever fails to hold.
    """
    tmp = path.with_suffix(f"{path.suffix}.{run_id}.tmp")
    tmp.write_bytes(json.dumps(payload, indent=2).encode("utf-8"))
    tmp.replace(path)


def plan(vault_root: Path, *, include: set[str]) -> ApplyPlan:
    """Build a plan covering exactly the rewrites whose ``key`` is in *include*."""
    entries: list[tuple[Rewrite, str]] = []
    for r in classify(vault_root):
        if r.key in include:
            entries.append((r, _ACTION_FOR_KIND[r.kind]))
    return ApplyPlan(run_id=_run_id(), entries=entries)


def apply(plan_obj: ApplyPlan, vault_root: Path) -> ApplyReport:
    """Execute *plan_obj*, keeping byte-exact originals for :func:`undo`.

    Holds a vault-wide lock for the whole run. Without it, two concurrent runs
    each read the entire ledger, mutate a private copy, and flush a full
    overwrite — so the second to flush silently discards the first's
    ``written_hash`` updates while both rule files are already written. The
    first run's proposal is deleted by then, so ``classify`` returns nothing and
    the disagreement between ledger and disk is permanently undetectable. That
    is strictly worse than the bug this module fixes: the original re-proposed
    forever and was at least visible. Reproduced 3/3 with two threads before
    the lock was added.
    """
    vault_root = Path(vault_root)
    report = ApplyReport()
    if not plan_obj.entries:
        return report

    with locks.try_lock(vault_root / ".mnemo" / "rewrites.lock") as held:
        if not held:
            raise VaultBusy(
                "another mnemo rewrites run is in progress; retry when it finishes"
            )
        return _apply_locked(plan_obj, vault_root, report)


def _apply_locked(plan_obj: ApplyPlan, vault_root: Path, report: ApplyReport) -> ApplyReport:
    """The body of :func:`apply`, run with the vault lock held."""
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

    manifest_path = arch / "manifest.json"
    moves: list[dict] = []

    # Defined AFTER every rebinding of the names below, because it closes over
    # them by reference: ``state`` in particular is rebound above when the ledger
    # exists on disk. Moving this definition earlier would silently capture the
    # placeholder dict and flush stale data with no error.
    def _flush() -> None:
        """Persist the manifest and the ledger to reflect exactly what has landed.

        Called after every applied rewrite and once more in a ``finally``, so an
        uncaught failure anywhere in the loop still leaves a recoverable state.

        The original shape wrote both once, after the loop. A mid-loop failure —
        ``atomic_write`` raising on rewrite 2 of 3 — therefore left rewrite 1
        written to disk, its proposal deleted, no manifest at all, and a ledger
        still carrying the stale hash: unrecoverable by ``undo`` and silently
        disagreeing with disk. Verified by monkeypatching the write.
        """
        # Both writes go through a run-scoped .tmp + os.replace. The manifest was
        # a plain write_text, which left a *truncated but existing* manifest if
        # the process died mid-write — and ``undo``'s only guard is
        # ``exists()``, so that turned "nothing to restore" into a raise. The
        # tmp names carry the run_id so a lock failure degrades to one run
        # refusing rather than two clobbering the same scratch file.
        _atomic_json(manifest_path, {
            "run_id": plan_obj.run_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "moves": moves,
            "skipped": report.skipped,
            "notes": report.notes,
            "state_backup": state_backup,
        }, plan_obj.run_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_json(state_path, state, plan_obj.run_id)

    try:
        _apply_entries(
            plan_obj, vault_root, report, state, originals, moves, _flush
        )
    finally:
        # Even an uncaught raise leaves a manifest naming exactly what landed and
        # a ledger matching disk, so ``undo`` can recover the completed work.
        _flush()

    return report


def _apply_entries(
    plan_obj: ApplyPlan,
    vault_root: Path,
    report: ApplyReport,
    state: dict,
    originals: Path,
    moves: list[dict],
    _flush: Callable[[], None],
) -> None:
    """The per-rewrite loop. Split out so :func:`apply` can wrap it in ``finally``.

    Mutates ``state``, ``moves`` and ``report`` in place — never rebinds them.
    ``_flush`` closes over the same three objects, so a rebinding here would
    desync its view and flush stale data silently.
    """
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
        # No ``return report`` at the end of this function: it annotates
        # ``-> None`` and mutates ``report`` in place. ``_apply_locked`` owns the
        # return.

        # Pristine originals first — nothing is overwritten or deleted before it
        # is archived. A rule skipped below keeps its copies here with no
        # ``moves`` entry, so ``undo`` ignores them. Harmless (neither file was
        # touched) and cheaper than deciding after the fact whether to clean up.
        #
        # The proposal is archived too, under ``proposals/``. ``undo`` restored
        # only the live half before, so an undone rewrite left the vault's
        # content correct but the reviewed proposal gone for good — recoverable
        # only by re-running extraction and hoping it rebuilt the same text.
        shutil.copy2(rewrite.live, originals / f"{rewrite.live.stem}.md")
        proposals_dir = originals.parent / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rewrite.proposal, proposals_dir / rewrite.proposal.name)

        builder = M.merge_insert_only if action == "merge" else M.replace_wholesale
        try:
            merged = builder(live_text, prop_text, vault_root=vault_root)
        except (M.NoLiveFrontmatter, M.NotAScalar) as exc:
            # One malformed rule must not abort a batch of 35. Both conditions are
            # properties of a single page: a live file with no frontmatter block,
            # or a nested value reaching the scalar writer. Same shape as the
            # read-OSError case above, so same handling — record and continue.
            #
            # Catching these keeps one bad page from costing the batch its
            # progress. It is not what makes a crash recoverable, though — that
            # is ``_flush`` running per rewrite and in a ``finally``.
            report.skipped.append({"key": rewrite.key, "reason": f"merge: {exc}"})
            continue
        # NotInsertOnly is deliberately NOT caught. classify's ``kind`` and
        # merge's precondition both call ``_classify_bodies``, so they cannot
        # disagree — if it fires, the dispatch table is wrong rather than one
        # page being bad, and aborting is the honest signal.
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

        # Persist after each rewrite, not once at the end. Everything applied so
        # far is then already recoverable if the next one dies.
        _flush()


def undo(vault_root: Path, run_id: str) -> int:
    """Restore every file *run_id* touched, byte for byte. Returns files restored."""
    vault_root = Path(vault_root)
    arch = vault_root / "shared" / "_archive" / f"rewrites-{run_id}"
    manifest_path = arch / "manifest.json"
    if not manifest_path.exists():
        return 0
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # A truncated manifest means nothing usable to replay. Return 0 like the
        # missing-manifest case rather than raising: undo is the recovery path,
        # so it must not itself be the thing that crashes.
        return 0
    originals = arch / "originals"
    restored = 0

    proposals_dir = arch / "proposals"
    for move in manifest.get("moves") or []:
        src = originals / f"{move.get('slug')}.md"
        dest = vault_root / str(move.get("live_path") or "")
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(src.read_bytes())
            restored += 1
        # Re-stage the proposal this run consumed, so an undo is fully
        # reversible rather than only restoring the live half.
        prop_rel = move.get("proposal_path")
        if prop_rel:
            prop_dest = vault_root / str(prop_rel)
            prop_src = proposals_dir / prop_dest.name
            if prop_src.exists():
                prop_dest.parent.mkdir(parents=True, exist_ok=True)
                prop_dest.write_bytes(prop_src.read_bytes())
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
