"""`mnemo rewrites` — review and accept staged ``.proposed.md`` rewrites (#159).

    mnemo rewrites                     # plan: what is staged, and what is safe
    mnemo rewrites --apply-safe        # merge every insert-only rewrite
    mnemo rewrites --show KEY          # full diff for one rewrite
    mnemo rewrites --accept KEY        # accept one mixed / full-rewrite
    mnemo rewrites --reject KEY        # delete one proposal, keep the live rule
    mnemo rewrites --undo RUN_ID       # restore every file an apply touched

``--apply-safe`` is deliberately not ``--apply``: the staged set includes
rewrites where the live rule is superseded outright, and an unqualified apply
over those is the mistake this command exists to prevent.
"""
from __future__ import annotations

import argparse
import difflib

from mnemo.cli.parser import command


def _print_plan(rewrites: list) -> None:
    n = len(rewrites)
    word = "rewrite" if n == 1 else "rewrites"
    print(f"{n} staged {word} in shared/_inbox/\n")

    safe = [r for r in rewrites if r.kind == "insert_only"]
    undecided = [r for r in rewrites if r.kind != "insert_only"]

    # Column width from the actual rows, not a guess. Hardcoded 55/45 misaligned
    # on the real vault, where the longest key is 70 characters
    # (reference/deploy-database-migrations-before-code-that-depends-on-new-t).
    width = max((len(r.key) for r in rewrites), default=0)

    if safe:
        print(f"  safe to merge ({len(safe)}) — insert-only, no live content dropped")
        for r in safe:
            # An insert-only rewrite can add zero body lines and still be worth
            # merging: its change is in the frontmatter, usually a description
            # the live rule has wrong. "+0 lines" alone reads as nothing to do.
            added = (
                f"+{r.inserted_lines} lines" if r.inserted_lines
                else "frontmatter only"
            )
            print(f"    {r.key:<{width}}  {added}")
        print()
    if undecided:
        print(f"  needs a decision ({len(undecided)})")
        for r in undecided:
            kind = "full rewrite" if r.kind == "full_rewrite" else "mixed"
            flag = "  ⚠ live rule fully superseded" if r.kind == "full_rewrite" else ""
            # Deliberately prints keep_ratio, NOT inserted_lines/dropped_lines.
            # A ``replace`` region counts on both sides, so one changed line
            # reads as "+1 -1" and looks like two lines of churn. The safe
            # bucket above is insert-only by construction (no replace opcodes),
            # so its ``+N`` is honest. If a future ``--show`` surfaces these
            # counts for mixed/full_rewrite, label them "changed", not "+/-".
            print(f"    {r.key:<{width}}  {kind:<13} keeps {r.keep_ratio:.0%}{flag}")
        print()
    print("(dry-run — `mnemo rewrites --apply-safe` merges the safe set; "
          "`--show KEY` prints one diff)")


@command("rewrites")
def cmd_rewrites(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core.rewrites import apply as A
    from mnemo.core.rewrites.classify import classify

    vault = cli._resolve_vault()

    # One action per invocation. The dispatch below resolves conflicts by
    # precedence, which silently picks a winner: `--accept X --reject X` would
    # reject without ever mentioning the accept. Fine for a flag that only
    # prints, not for two that write.
    chosen = [
        name for name in ("undo", "show", "accept", "reject")
        if getattr(args, name, None)
    ]
    if getattr(args, "apply_safe", False):
        chosen.append("apply-safe")
    if len(chosen) > 1:
        print(f"pick one action, got: {', '.join('--' + c for c in chosen)}")
        return 1

    if getattr(args, "undo", None):
        restored = A.undo(vault, args.undo)
        if not restored:
            print(f"no rewrites run {args.undo} found (nothing restored)")
            return 1
        print(f"restored {restored} file(s) from rewrites-{args.undo}")
        return 0

    # ``classify`` deliberately does not guard OSError — swallowing it made "no
    # rewrites staged" indistinguishable from "every one failed to read". But a
    # raw traceback is a worse kind of invisible than silence: it tells a human
    # something broke without telling them which file or why, on a command whose
    # whole purpose is making a hidden backlog visible. Raise in the library,
    # report at the boundary.
    try:
        rewrites = classify(vault)
    except OSError as exc:
        path = getattr(exc, "filename", None) or vault
        print(f"cannot read {path}: {exc.strerror or exc}")
        return 1
    if not rewrites:
        print("no staged rewrites in shared/_inbox/")
        return 0
    by_key = {r.key: r for r in rewrites}

    if getattr(args, "show", None):
        r = by_key.get(args.show)
        if r is None:
            print(f"no staged rewrite for {args.show}")
            return 1
        live = r.live.read_text(encoding="utf-8").splitlines(keepends=True)
        prop = r.proposal.read_text(encoding="utf-8").splitlines(keepends=True)
        print(f"{r.key} — {r.kind}, keeps {r.keep_ratio:.0%} of live lines\n")
        for line in difflib.unified_diff(
            live, prop, fromfile=str(r.live.name), tofile=str(r.proposal.name)
        ):
            print(line, end="")
        return 0

    if getattr(args, "reject", None):
        r = by_key.get(args.reject)
        if r is None:
            print(f"no staged rewrite for {args.reject}")
            return 1
        # Archive before deleting. ``apply`` archives every file it touches, and
        # a reject that only unlinks is the one path in this command that
        # destroys reviewed text with no way back. The extractor re-derives a
        # proposal from its source, so if that source has since moved on, the
        # rejected text is unrecoverable — which makes "reject is not permanent"
        # true of the decision but false of the content.
        arch = vault / "shared" / "_archive" / f"rejected-{A._run_id()}"
        arch.mkdir(parents=True, exist_ok=True)
        dest = arch / r.proposal.name
        dest.write_bytes(r.proposal.read_bytes())
        r.proposal.unlink()
        print(f"rejected {r.key}; live rule untouched")
        print(f"  archived to {dest.relative_to(vault)}")
        print("note: the extractor re-proposes from the source transcript, so this "
              "returns only while that source says the same thing")
        return 0

    if getattr(args, "accept", None):
        r = by_key.get(args.accept)
        if r is None:
            print(f"no staged rewrite for {args.accept}")
            return 1
        include = {r.key}
    elif getattr(args, "apply_safe", False):
        include = {r.key for r in rewrites if r.kind == "insert_only"}
        if not include:
            print("no insert-only rewrites to merge")
            return 0
    else:
        _print_plan(rewrites)
        return 0

    plan = A.plan(vault, include=include)
    try:
        report = A.apply(plan, vault)
    except A.VaultBusy as exc:
        print(str(exc))
        return 1
    except RuntimeError as exc:
        print(str(exc))
        return 1
    for note in report.notes:
        print(f"  note: {note}")
    for item in report.skipped:
        print(f"  skipped: {item.get('key')} · {item.get('reason')}")
    print(f"merged {report.merged} · replaced {report.replaced} · "
          f"skipped {len(report.skipped)}")
    print(f"undo with: mnemo rewrites --undo {plan.run_id}")
    return 0
