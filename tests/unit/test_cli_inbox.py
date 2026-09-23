"""``mnemo inbox`` — the command ``shared/_inbox/`` never had (#380).

``mnemo rewrites`` acts on the ``.proposed.md`` half of the queue. The plain
pages — evidence-gate demotions, backfill reconstructions, multi-source
stagings — had no command at all: ``doctor`` could only say "promotion is a
manual ``mv``", and that ``mv`` left the extractor's ledger saying ``inbox``.
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

from mnemo.cli.parser import ADVANCED_COMMANDS, COMMANDS, INTERNAL_COMMANDS, _build_parser


def _run(vault: Path, monkeypatch, **flags) -> tuple[int, str]:
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    from mnemo.core import inbox as I

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    monkeypatch.setattr(I, "_rebuild_indexes", lambda _v: None)
    defaults = {"all": False, "show": None, "promote": None, "drop": None, "stats": False}
    ns = argparse.Namespace(**{**defaults, **flags})
    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = COMMANDS["inbox"](ns)
    return rc, buf.getvalue()


def _page(vault: Path, rel: str, *, age_days: float = 1.0, project: str = "demo",
          description: str = "what it says", extra: str = "") -> Path:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    src = f"  - bots/{project}/x.md" if project else ""
    path.write_text(
        f"---\nname: {path.stem}\nslug: {path.stem}\ndescription: {description}\n"
        f"type: {path.parent.name}\n{extra}sources:\n{src}\n---\n\nbody\n",
        encoding="utf-8",
    )
    # A minute past the day boundary: ages floor, and Windows' coarse clock can
    # read `now` a tick before the `time.time()` this was computed from.
    ts = time.time() - age_days * 86400 - 60
    os.utime(path, (ts, ts))
    return path


def test_inbox_is_a_user_facing_command_with_a_handler():
    """Not advanced and not internal: a queue you have to know about to find
    is the problem this command exists to fix."""
    import mnemo.cli.commands  # noqa: F401 — populates COMMANDS
    assert "inbox" in COMMANDS
    assert "inbox" not in ADVANCED_COMMANDS and "inbox" not in INTERNAL_COMMANDS
    ns = _build_parser().parse_args(["inbox", "--promote", "reference/a"])
    assert ns.command == "inbox" and ns.promote == "reference/a"


def test_listing_writes_nothing_and_says_so(tmp_vault: Path, monkeypatch):
    page = _page(tmp_vault, "shared/_inbox/reference/demo__x.md", age_days=9)

    rc, out = _run(tmp_vault, monkeypatch, all=True)

    assert rc == 0
    assert "1 staged page" in out and "9d" in out
    assert "nothing was written" in out
    assert "--promote" in out and "--drop" in out
    assert page.exists()


def test_listing_shows_the_judges_verdict_and_nothing_for_an_unjudged_page(
    tmp_vault: Path, monkeypatch,
):
    _page(tmp_vault, "shared/_inbox/reference/kept.md", extra="reference_gate: technique\n")
    _page(tmp_vault, "shared/_inbox/reference/plain.md")

    _, out = _run(tmp_vault, monkeypatch, all=True)

    kept = next(ln for ln in out.splitlines() if "reference/kept" in ln)
    plain = next(ln for ln in out.splitlines() if "reference/plain" in ln)
    assert "[judge: technique] what it says" in kept
    assert "judge:" not in plain


def test_listing_is_scoped_to_the_project_you_are_standing_in(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/reference/mine.md", project="demo")
    _page(tmp_vault, "shared/_inbox/reference/theirs.md", project="other")
    monkeypatch.setattr("mnemo.cli.commands.inbox._resolve_project", lambda _v: "demo")

    rc, out = _run(tmp_vault, monkeypatch)

    assert rc == 0
    assert "reference/mine" in out and "reference/theirs" not in out
    assert "1 more staged for other projects" in out


def test_nothing_staged_for_here_still_points_at_what_waits_elsewhere(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/reference/theirs.md", project="other")
    monkeypatch.setattr("mnemo.cli.commands.inbox._resolve_project", lambda _v: "demo")

    rc, out = _run(tmp_vault, monkeypatch)

    assert rc == 0 and "mnemo inbox --all" in out


def test_promote_through_the_cli_moves_the_page(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md")

    rc, out = _run(tmp_vault, monkeypatch, promote="reference/demo__x")

    assert rc == 0
    assert (tmp_vault / "shared" / "reference" / "demo__x.md").is_file()
    assert "promoted reference/demo__x" in out
    assert "indexes rebuilt" in out


def test_a_bare_slug_works_while_it_is_unambiguous(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md")

    rc, _ = _run(tmp_vault, monkeypatch, promote="demo__x")

    assert rc == 0 and (tmp_vault / "shared" / "reference" / "demo__x.md").is_file()


def test_a_slug_staged_under_two_types_is_refused_not_guessed(tmp_vault: Path, monkeypatch):
    """The cross-type duplicates #187 measured are exactly this shape."""
    _page(tmp_vault, "shared/_inbox/reference/dup.md")
    _page(tmp_vault, "shared/_inbox/feedback/dup.md")

    rc, out = _run(tmp_vault, monkeypatch, promote="dup")

    assert rc == 1
    assert "more than one type" in out
    assert (tmp_vault / "shared" / "_inbox" / "reference" / "dup.md").exists()


def test_an_unknown_key_fails_loudly(tmp_vault: Path, monkeypatch):
    rc, out = _run(tmp_vault, monkeypatch, drop="reference/ghost")

    assert rc == 1 and "no staged page for reference/ghost" in out


def test_two_write_actions_are_refused_rather_than_resolved_by_precedence(
    tmp_vault: Path, monkeypatch
):
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md")

    rc, out = _run(tmp_vault, monkeypatch, promote="reference/demo__x", drop="reference/demo__x")

    assert rc == 1 and "pick one action" in out
    assert (tmp_vault / "shared" / "_inbox" / "reference" / "demo__x.md").exists()


def test_show_prints_the_page_and_changes_nothing(tmp_vault: Path, monkeypatch):
    page = _page(tmp_vault, "shared/_inbox/reference/demo__x.md")

    rc, out = _run(tmp_vault, monkeypatch, show="reference/demo__x")

    assert rc == 0 and "body" in out and page.exists()


def test_drop_through_the_cli_archives_and_reports_where(tmp_vault: Path, monkeypatch):
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md")

    rc, out = _run(tmp_vault, monkeypatch, drop="reference/demo__x")

    assert rc == 0 and "dropped reference/demo__x" in out and "_archive/dropped-" in out
    assert not (tmp_vault / "shared" / "_inbox" / "reference" / "demo__x.md").exists()


def test_stats_says_plainly_when_no_page_has_been_offered_and_decided(
    tmp_vault: Path, monkeypatch
):
    """Printed as a sentence, not as 0 — "no data" and "instant" are different
    facts and the second one would be a lie."""
    _page(tmp_vault, "shared/_inbox/reference/demo__x.md", age_days=4)

    rc, out = _run(tmp_vault, monkeypatch, stats=True)

    assert rc == 0
    assert "1 staged in shared/_inbox/" in out and "4d" in out
    assert "no page has been both offered and decided yet" in out
