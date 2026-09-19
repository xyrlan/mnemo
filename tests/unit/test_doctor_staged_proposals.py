"""``mnemo doctor`` surfaces the ``_inbox`` review queue (#159, #375).

#159 taught doctor to count the ``.proposed.md`` rewrites: after #156
relocated the strays, ``shared/_inbox/`` held 33 nobody had looked at.

#375 is the other half. Plain staged pages — evidence-gate demotions and
multi-source stagings — were excluded on the grounds that they follow their
own path, and that path has no consumer: not served (``filters``), not
listed (``rewrites.classify``), not counted. 194 of them on the real vault,
against 2 proposals. Doctor now prints both populations, separately, and
names what ``mnemo extract --force`` does to them.

The scope of both counts is ``_inbox/<type>/``, not an ``rglob`` of
``_inbox``: see ``filters.iter_staged_pages``.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from mnemo.cli.commands.doctor_checks import rules as doctor_rules

_PLAIN_LINE = "staged pages awaiting review"


def _page(
    vault: Path,
    rel: str,
    *,
    age_days: float = 0.0,
    frontmatter: str = "name: n\ntype: project\n",
) -> Path:
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{frontmatter}---\n\nbody\n", encoding="utf-8")
    if age_days:
        ts = time.time() - age_days * 86400
        os.utime(p, (ts, ts))
    return p


# --- the four populations (#375 acceptance) -------------------------------


def test_quiet_when_nothing_is_staged(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/project/a__x.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    assert "no staged rewrites awaiting review" in out
    assert _PLAIN_LINE not in out  # zero plain pages -> no new line


def test_only_proposals(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/project/a__x.md")
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    assert "1 staged rewrite awaiting review in shared/_inbox/" in out
    assert _PLAIN_LINE not in out


def test_only_plain_pages(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/reference/b__y.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    # The proposal half stays quiet, exactly as it did before #375.
    assert "no staged rewrites awaiting review" in out
    assert "1 staged page awaiting review in shared/_inbox/" in out


def test_both_populations_are_reported_separately(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/project/a__x.md")
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")
    _page(tmp_path, "shared/_inbox/reference/b__y.md")
    _page(tmp_path, "shared/_inbox/reference/b__z.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    assert "1 staged rewrite awaiting review in shared/_inbox/" in out
    assert "2 staged pages awaiting review in shared/_inbox/" in out
    # Kept apart, never summed: the two need different decisions.
    assert "3 staged" not in out


def test_neither_count_swallows_the_other(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/reference/plain.md")
    _page(tmp_path, "shared/_inbox/reference/prop.proposed.md")
    _page(tmp_path, "shared/_inbox/reference/upd.update-proposed.md")

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    out = capsys.readouterr().out
    assert "2 staged rewrites awaiting review" in out
    assert "1 staged page awaiting review" in out


# --- the proposal line (#159, unchanged behaviour) ------------------------


def test_counts_proposals_across_inbox_types(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")
    _page(tmp_path, "shared/_inbox/project/a__y.update-proposed.md")
    _page(tmp_path, "shared/_inbox/feedback/a__z.proposed.md")
    # Strays beside live rules belong to ``stray_proposed``, not here.
    _page(tmp_path, "shared/project/a__w.proposed.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    assert "3 staged rewrites awaiting review in shared/_inbox/" in out


def test_names_the_oldest_proposal(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/project/a__new.proposed.md", age_days=1)
    _page(tmp_path, "shared/_inbox/project/a__old.proposed.md", age_days=12)

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    out = capsys.readouterr().out
    assert "2 staged rewrites awaiting review" in out
    assert "oldest a__old.proposed.md, 12 days" in out


def test_singular_wording(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    assert "1 staged rewrite awaiting review" in capsys.readouterr().out


def test_says_when_rewrites_cannot_act_on_a_proposal(tmp_path: Path, capsys) -> None:
    # No ``shared/project/a__x.md``: ``rewrites.classify`` skips a proposal
    # with nothing to merge into, so doctor must not quote a number the
    # command then refuses to show.
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    out = capsys.readouterr().out
    assert "none of them still has a live rule to merge into" in out


def test_no_orphan_note_when_every_proposal_is_actionable(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/project/a__x.md")
    _page(tmp_path, "shared/_inbox/project/a__x.proposed.md")

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    out = capsys.readouterr().out
    assert "live rule to merge into" not in out


# --- the plain line (#375) ------------------------------------------------


def test_plain_line_names_oldest_and_the_force_risk(tmp_path: Path, capsys) -> None:
    _page(tmp_path, "shared/_inbox/reference/new.md", age_days=1)
    _page(tmp_path, "shared/_inbox/reference/old.md", age_days=9)

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    out = capsys.readouterr().out
    assert "2 staged pages awaiting review in shared/_inbox/" in out
    assert "oldest old.md, 9 days" in out
    # #380 replaced the manual `mv` with a command; doctor points at it.
    assert "`mnemo inbox`" in out
    assert "`mnemo extract --force` deletes" in out


def test_plain_line_splits_by_why_the_page_staged(tmp_path: Path, capsys) -> None:
    _page(
        tmp_path,
        "shared/_inbox/reference/demoted.md",
        frontmatter="name: d\ntype: reference\nconfidence: inferred\ndemoted_from: feedback\n",
    )
    _page(
        tmp_path,
        "shared/_inbox/reference/multi.md",
        frontmatter="name: m\ntype: reference\nsources:\n  - a.md\n  - b.md\n",
    )
    _page(
        tmp_path,
        "shared/_inbox/reference/lone.md",
        frontmatter="name: l\ntype: reference\nsources:\n  - a.md\n",
    )

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    out = capsys.readouterr().out
    assert "3 staged pages awaiting review" in out
    assert "1 demotion, 1 multi-source, 1 other" in out


def test_plain_line_counts_backfill_pages(tmp_path: Path, capsys) -> None:
    # Reconstructed from an archived transcript, so it always stages — and it
    # is the one population ``extract --force`` spares, which is why the line
    # tells them apart.
    _page(
        tmp_path,
        "shared/_inbox/reference/harvested.md",
        frontmatter="name: h\ntype: reference\norigin: backfill\n",
    )

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    assert "(1 backfill;" in capsys.readouterr().out


def test_plain_line_pluralises_demotions(tmp_path: Path, capsys) -> None:
    for slug in ("a", "b"):
        _page(
            tmp_path,
            f"shared/_inbox/reference/{slug}.md",
            frontmatter="name: n\ntype: reference\ndemoted_from: feedback\n",
        )

    doctor_rules._doctor_check_staged_proposals(tmp_path)

    assert "(2 demotions;" in capsys.readouterr().out


def test_an_unreadable_frontmatter_still_counts(tmp_path: Path, capsys) -> None:
    p = tmp_path / "shared/_inbox/reference/broken.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("no frontmatter at all\n", encoding="utf-8")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    assert "1 staged page awaiting review" in capsys.readouterr().out


# --- scope: _inbox/<type>/ only -------------------------------------------


def test_archive_dirs_left_under_inbox_are_not_the_review_queue(
    tmp_path: Path, capsys
) -> None:
    # An older ``mnemo rewrites`` archived under ``_inbox/`` instead of
    # ``_archive/``. The real vault carried 34 such copies against 2 live
    # proposals, and ``rglob`` counted every one of them.
    _page(tmp_path, "shared/_inbox/proposals/a__x.proposed.md")
    _page(tmp_path, "shared/_inbox/rejected-20260912T164010/a__y.proposed.md")
    _page(tmp_path, "shared/_inbox/proposals/a__z.md")

    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    assert "no staged rewrites awaiting review" in out
    assert _PLAIN_LINE not in out


def test_missing_inbox_is_quiet(tmp_path: Path, capsys) -> None:
    assert doctor_rules._doctor_check_staged_proposals(tmp_path) is True

    out = capsys.readouterr().out
    assert "no staged rewrites awaiting review" in out
    assert _PLAIN_LINE not in out


def test_registered_in_doctor_checks() -> None:
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    names = [n for n, _ in DOCTOR_CHECKS]
    assert "staged_proposals" in names
    # Right after the shadowing check: strays first, then the backlog they join.
    assert names.index("staged_proposals") == names.index("stray_proposed") + 1
