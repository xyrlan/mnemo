"""`mnemo inbox` — the review queue for plain staged pages (#380).

    mnemo inbox                     # what is staged for this project
    mnemo inbox --all               # every project, plus the unattributable
    mnemo inbox --show KEY          # print one staged page
    mnemo inbox --promote KEY       # move it into shared/<type>/ (recall sees it)
    mnemo inbox --drop KEY          # archive it and take it out of the queue
    mnemo inbox --stats             # depth, age, and what drained lately

The command ``shared/_inbox/`` never had. ``mnemo rewrites`` acts on the
``.proposed.md`` half of the queue; the plain pages — evidence-gate demotions,
backfill reconstructions, multi-source stagings — had no command at all, and
``doctor`` could only say "promotion is a manual ``mv``". A ``mv`` also leaves
the extractor's ledger saying ``inbox``, which is why this is a command and not
a documented shell line; see ``core.inbox._update_state_entry``.

Listing writes nothing. Both decisions are per key and explicit: there is no
``--promote-all``, because the judgement is the point.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


def _resolve_project(vault) -> str | None:
    """The canonical project for the directory the user is standing in.

    Canonical (#225): a worktree resolves to the repo it belongs to, so
    ``mnemo inbox`` inside ``mnemo-wt-380`` lists the same queue as ``mnemo``
    itself — and the same one the session-start offer drew from.
    """
    import os

    try:
        from mnemo.core.agent import resolve_canonical_agent

        return resolve_canonical_agent(os.getcwd()).name
    except Exception:  # noqa: BLE001 — a listing is better than a traceback
        return None


def _match(pages: list, key: str) -> tuple[object | None, str]:
    """Find one page by ``<type>/<slug>`` or by a bare slug. ``(page, error)``.

    A bare slug is accepted only while it is unambiguous. Two types can hold
    the same slug — the cross-type duplicates #187 measured are exactly that —
    and guessing between them would act on the wrong page silently.
    """
    exact = [p for p in pages if p.key == key]
    if len(exact) == 1:
        return exact[0], ""
    by_slug = [p for p in pages if p.slug == key]
    if len(by_slug) == 1:
        return by_slug[0], ""
    if len(by_slug) > 1:
        names = ", ".join(sorted(p.key for p in by_slug))
        return None, f"{key} is staged under more than one type: {names}"
    return None, f"no staged page for {key}"


def _days(value) -> str:
    """Days, with a decimal only when the number has one."""
    if value is None:
        return "\u2014"
    return f"{value:g}d"


def _print_listing(pages: list, *, project: str | None, other: int) -> None:
    import time

    from mnemo.core.inbox import median

    now = time.time()
    scope = f"for {project}" if project else "across every project"
    n = len(pages)
    word = "page" if n == 1 else "pages"
    ages = sorted(p.age_days(now) for p in pages)
    print(f"{n} staged {word} {scope} in shared/_inbox/ "
          f"(median {_days(median(ages))}, oldest {_days(ages[-1] if ages else None)})\n")

    width = max((len(p.key) for p in pages), default=0)
    for p in pages:
        desc = " ".join(p.description.split())
        if len(desc) > 72:
            desc = desc[:71] + "…"
        print(f"  {p.key:<{width}}  {p.reason:<12} {p.age_days(now):>3}d  {desc}")
    print()
    print("nothing was written — this is a listing only.")
    print("  `mnemo inbox --promote KEY` moves one into shared/<type>/, where recall sees it")
    print("  `mnemo inbox --drop KEY` archives it and takes it out of the queue")
    print("  `mnemo inbox --show KEY` prints one page")
    if other:
        print(f"  ({other} more staged for other projects — `mnemo inbox --all`)")


def _print_stats(stats: dict) -> None:
    print(f"{stats['staged']} staged in shared/_inbox/ "
          f"(median {_days(stats['median_age_days'])}, "
          f"oldest {_days(stats['oldest_age_days'])})")
    print(f"last {stats['window_days']} days: {stats['offered']} offered at session start, "
          f"{stats['promoted']} promoted, {stats['dropped']} dropped "
          f"({stats['resolved']} resolved)")
    if stats.get("expired") or stats.get("restored"):
        # Apart from `resolved`: nobody decided these (#429).
        print(f"  held pages expired unreviewed: {stats['expired']}, "
              f"restored: {stats['restored']} (`mnemo inbox --restore KEY`)")
    if stats["median_decision_days"] is None:
        # Said plainly rather than printed as 0: no page has yet been offered
        # *and* decided, so there is no latency to report — which is a
        # different fact from "decisions are instant".
        print("median offer → decision: no page has been both offered and decided yet")
    else:
        print(f"median offer → decision: {_days(stats['median_decision_days'])}")


@command("inbox")
def cmd_inbox(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core import inbox as I

    vault = cli._resolve_vault()

    # One action per invocation, same rule as ``mnemo rewrites``: resolving a
    # conflict by precedence silently picks a winner, which is tolerable for
    # flags that print and not for two that write.
    chosen = [
        name for name in ("show", "promote", "drop", "restore")
        if getattr(args, name, None)
    ]
    if getattr(args, "stats", False):
        chosen.append("stats")
    if len(chosen) > 1:
        print(f"pick one action, got: {', '.join('--' + c for c in chosen)}")
        return 1

    if getattr(args, "stats", False):
        _print_stats(I.stats(vault))
        return 0

    if getattr(args, "restore", None):
        # Not matched against the queue: a page to restore is, by definition,
        # not in it. The key is looked up in the archive instead.
        result = I.restore(vault, args.restore)
        print(result.message)
        return 0 if result.ok else 1

    everything = I.staged_pages(vault)
    key = getattr(args, "promote", None) or getattr(args, "drop", None) or getattr(args, "show", None)
    if key:
        # Resolved against the whole vault, not the current project: the key
        # may have come from an offer made in another repo, and refusing it
        # because of where the shell happens to be would be a riddle.
        page, err = _match(everything, key)
        if page is None:
            print(err)
            return 1
        project = page.projects[0] if page.projects else None
        if getattr(args, "show", None):
            print(page.path.read_text(encoding="utf-8", errors="replace"), end="")
            return 0
        if getattr(args, "promote", None):
            result = I.promote(vault, page, project=project)
        else:
            result = I.drop(vault, page, project=project)
        print(result.message)
        if not result.ok:
            return 1
        if not result.state_updated:
            # Worth saying: the extractor's ledger has no entry for this page,
            # so nothing stops a later run from staging the same slug again.
            print("  note: no extraction-state entry for this page — a later "
                  "`mnemo extract` may stage it again from its source")
        if getattr(args, "promote", None):
            print("  rule-activation and reflex indexes rebuilt; recall can reach it now")
        return 0

    if getattr(args, "all", False):
        pages, project, other = everything, None, 0
    else:
        project = _resolve_project(vault)
        pages = [p for p in everything if project is not None and project in p.projects]
        other = len(everything) - len(pages)

    if not pages:
        if other:
            print(f"nothing staged for {project} — {other} page(s) wait for other "
                  "projects (`mnemo inbox --all`)")
        else:
            print("nothing staged in shared/_inbox/")
        return 0

    _print_listing(pages, project=project, other=other)
    return 0
