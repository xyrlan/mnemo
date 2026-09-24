"""`mnemo inbox` — the review queue for plain staged pages (#380).

    mnemo inbox                     # what is staged for this project
    mnemo inbox --all               # every project, plus the unattributable
    mnemo inbox --show KEY          # print one staged page
    mnemo inbox --promote KEY       # move it into shared/<type>/ (recall sees it)
    mnemo inbox --drop KEY          # archive it and take it out of the queue
    mnemo inbox --stats             # depth, age, and what drained lately
    mnemo inbox --json [--origin backfill] [--project P]   # the listing, for a program
    mnemo inbox --promote K1 K2 ... [--json]   # several at once; --keys-stdin reads them
    mnemo inbox --review [--origin backfill]   # a checklist: uncheck, keep the rest

The command ``shared/_inbox/`` never had. ``mnemo rewrites`` acts on the
``.proposed.md`` half of the queue; the plain pages — evidence-gate demotions,
backfill reconstructions, multi-source stagings — had no command at all, and
``doctor`` could only say "promotion is a manual ``mv``". A ``mv`` also leaves
the extractor's ledger saying ``inbox``, which is why this is a command and not
a documented shell line; see ``core.inbox._update_state_entry``.

Listing writes nothing. Both decisions are per key and explicit: there is no
``--promote-all``, because the judgement is the point. A batch names every key
it decides, and ``--review`` starts every page checked but decides nothing
until the user says keep (#495, the install review).
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
    """Find one page by ``<type>/<slug>`` or by a bare slug — see ``core.inbox.match``."""
    from mnemo.core.inbox import match

    return match(pages, key)


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
        verdict = f"[{p.gate_label}] " if p.gate_label else ""
        print(f"  {p.key:<{width}}  {p.reason:<12} {p.age_days(now):>3}d  {verdict}{desc}")
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


def _keys(value) -> list | None:
    """``--promote``/``--drop`` as a list, or None when the flag is absent.

    ``nargs="*"`` so ``--promote --keys-stdin`` parses; an empty list is the
    flag given with its keys on stdin, not the flag missing.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    return list(value)


def _stdin_keys() -> list:
    import sys

    return [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]


def _scope(vault, args, everything: list) -> tuple[list, str | None, int]:
    """``(pages, project, other)``: the listing's pages, whose they are, how many are not.

    ``--project P`` names the queue; ``--all`` takes every one; otherwise the
    project the shell is standing in. ``--origin backfill`` narrows the lot
    before the split, so ``other`` counts pages of that origin too.
    """
    if getattr(args, "origin", "any") == "backfill":
        everything = [p for p in everything if p.backfill]
    if getattr(args, "all", False):
        return everything, None, 0
    project = getattr(args, "project", None) or _resolve_project(vault)
    pages = [p for p in everything if project is not None and project in p.projects]
    return pages, project, len(everything) - len(pages)


def _print_json(doc: dict) -> None:
    import json
    import sys

    # UTF-8 whatever the console's code page: the reader is a program, and a
    # page name in Portuguese must not become a UnicodeEncodeError on Windows.
    sys.stdout.flush()
    out = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(out.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(out)


def _decide_batch(vault, action: str, keys: list, *, as_json: bool) -> int:
    from mnemo.core import inbox as I

    outcomes = I.decide_many(vault, keys, action=action, via=I.VIA_REVIEW)
    failed = any(not o.result.ok for o in outcomes)
    if as_json:
        _print_json(I.batch_json(action, outcomes))
        return 1 if failed else 0
    for o in outcomes:
        print(o.result.message)
        if o.result.ok and not o.result.state_updated:
            print("  note: no extraction-state entry for this page — a later "
                  "`mnemo extract` may stage it again from its source")
    if action == I.PROMOTED and any(o.result.ok for o in outcomes):
        print("  rule-activation and reflex indexes rebuilt; recall can reach them now")
    return 1 if failed else 0


#: Group order on the review checklist — the three types a backfill stages
#: most of (32/9/15 on clubinho), then the rest alphabetically.
_REVIEW_ORDER = ("project", "feedback", "reference")


def _review_groups(pages: list) -> list:
    """``[(type, [page, ...]), ...]`` in :data:`_REVIEW_ORDER`, oldest first within each."""
    by_type: dict = {}
    for p in pages:
        by_type.setdefault(p.type, []).append(p)
    order = [t for t in _REVIEW_ORDER if t in by_type]
    order += sorted(t for t in by_type if t not in _REVIEW_ORDER)
    return [(t, by_type[t]) for t in order]


def _print_checklist(groups: list, checked: set) -> None:
    n = 0
    for page_type, pages in groups:
        print(f"\n{page_type} ({len(pages)})")
        for p in pages:
            n += 1
            mark = "x" if n in checked else " "
            desc = " ".join(p.description.split())
            if len(desc) > 60:
                desc = desc[:59] + "…"
            line = f"  [{mark}] {n:>3}  {p.name}"
            print(f"{line} — {desc}" if desc else line)


def _parse_toggles(text: str, total: int) -> set | None:
    """Numbers and ranges (``3 5-8``, commas allowed) → a set, or None if any is invalid."""
    picked: set = set()
    for token in text.replace(",", " ").split():
        lo, sep, hi = token.partition("-")
        try:
            a = int(lo)
            b = int(hi) if sep else a
        except ValueError:
            return None
        if a > b or a < 1 or b > total:
            return None
        picked.update(range(a, b + 1))
    return picked or None


_REVIEW_HELP = ("numbers or ranges toggle (3, 5-8) · all / none · "
                "k: keep the checked, drop the rest · q: quit, decide nothing")


def _review(vault, pages: list, *, project: str | None) -> int:
    """The terminal checklist. Off a tty it prints and decides nothing."""
    import sys

    from mnemo.core import inbox as I

    groups = _review_groups(pages)
    ordered = [p for _, ps in groups for p in ps]
    total = len(ordered)
    checked = set(range(1, total + 1))
    scope = f"for {project}" if project else "across every project"
    word = "page" if total == 1 else "pages"
    print(f"{total} staged {word} {scope}. Every one starts checked: "
          "uncheck what is wrong or transient, keep the rest.")
    _print_checklist(groups, checked)
    print()

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("not a terminal — nothing was decided. Run `mnemo inbox --review` "
              "in one, or decide with `mnemo inbox --promote KEY ... --json`.")
        return 0

    while True:
        try:
            answer = input(f"{_REVIEW_HELP}\n> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            answer = "q"
            print()
        if answer in ("q", "quit"):
            print("nothing was decided — every page is still staged.")
            return 0
        if answer in ("k", "keep"):
            keep = [p.key for i, p in enumerate(ordered, 1) if i in checked]
            drop = [p.key for i, p in enumerate(ordered, 1) if i not in checked]
            try:
                sure = input(f"promote {len(keep)}, drop {len(drop)}? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                sure = ""
                print()
            if sure not in ("y", "yes"):
                continue
            break
        if answer == "all":
            checked = set(range(1, total + 1))
        elif answer == "none":
            checked = set()
        else:
            picked = _parse_toggles(answer, total)
            if picked is None:
                print(f"not understood: {answer!r} — numbers run 1 to {total}")
                continue
            checked ^= picked
        _print_checklist(groups, checked)
        print()

    outcomes = []
    if keep:
        outcomes += I.decide_many(vault, keep, action=I.PROMOTED, via=I.VIA_REVIEW)
    if drop:
        outcomes += I.decide_many(vault, drop, action=I.DROPPED, via=I.VIA_REVIEW)
    done = [o for o in outcomes if o.result.ok]
    failed = [o for o in outcomes if not o.result.ok]
    promoted = sum(1 for o in done if o.result.message.startswith("promoted"))
    print(f"promoted {promoted}, dropped {len(done) - promoted}"
          + (f", {len(failed)} failed:" if failed else "."))
    for o in failed:
        print(f"  {o.key}: {o.result.message}")
    if promoted:
        print("rule-activation and reflex indexes rebuilt; recall can reach the kept pages now")
    return 1 if failed else 0


@command("inbox")
def cmd_inbox(args: argparse.Namespace) -> int:
    from mnemo import cli
    from mnemo.core import inbox as I

    vault = cli._resolve_vault()
    promote_keys = _keys(getattr(args, "promote", None))
    drop_keys = _keys(getattr(args, "drop", None))
    as_json = bool(getattr(args, "json", False))

    # One action per invocation, same rule as ``mnemo rewrites``: resolving a
    # conflict by precedence silently picks a winner, which is tolerable for
    # flags that print and not for two that write.
    given = {
        "show": bool(getattr(args, "show", None)),
        "promote": promote_keys is not None,
        "drop": drop_keys is not None,
        "restore": bool(getattr(args, "restore", None)),
        "stats": bool(getattr(args, "stats", False)),
        "review": bool(getattr(args, "review", False)),
    }
    chosen = [name for name, on in given.items() if on]
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

    keys = promote_keys if promote_keys is not None else drop_keys
    if getattr(args, "keys_stdin", False):
        if keys is None:
            print("--keys-stdin needs --promote or --drop")
            return 1
        keys = keys + _stdin_keys()
    if keys is not None and (as_json or getattr(args, "keys_stdin", False) or len(keys) != 1):
        # The batch (#495): every key decided, each failure its own, and the
        # ledger row says it came from a review.
        if not keys:
            print("no keys given")
            return 1
        action = I.PROMOTED if promote_keys is not None else I.DROPPED
        return _decide_batch(vault, action, keys, as_json=as_json)

    everything = I.staged_pages(vault)
    key = (keys[0] if keys else None) or getattr(args, "show", None)
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
        if promote_keys is not None:
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
        if promote_keys is not None:
            print("  rule-activation and reflex indexes rebuilt; recall can reach it now")
        return 0

    pages, project, other = _scope(vault, args, everything)

    if getattr(args, "review", False):
        if not pages:
            print("nothing staged to review" + (f" for {project}" if project else ""))
            return 0
        return _review(vault, pages, project=project)

    if as_json:
        from mnemo.core import config as cfg_mod

        _print_json(I.listing(vault, pages, project=project,
                              origin=getattr(args, "origin", "any") or "any",
                              cfg=cfg_mod.load_config()))
        return 0

    if not pages:
        if other:
            print(f"nothing staged for {project} — {other} page(s) wait for other "
                  "projects (`mnemo inbox --all`)")
        else:
            print("nothing staged in shared/_inbox/")
        return 0

    _print_listing(pages, project=project, other=other)
    return 0
