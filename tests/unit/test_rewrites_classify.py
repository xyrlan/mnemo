"""Classification of staged ``.proposed.md`` rewrites (#159).

The real vault's 35 proposals split 11 / 18 / 6 across insert_only / mixed /
full_rewrite. One accept semantic cannot serve all three: a plain ``mv``
discards live content in the 11, and a plain append makes the 6 assert both
``MARKETPLACE_ENABLED = false`` and ``= true``.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core.rewrites import classify as C


def _write(p: Path, fm: str, body: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\n{fm}\n---\n\n{body}", encoding="utf-8")
    return p


def _pair(vault: Path, slug: str, live_body: str, prop_body: str, *, page_type: str = "project"):
    fm = f"name: n\nslug: {slug}\ntype: {page_type}\nsources:\n  - bots/a/memory/{slug}.md"
    _write(vault / "shared" / page_type / f"{slug}.md", fm, live_body)
    _write(vault / "shared" / "_inbox" / page_type / f"{slug}.proposed.md", fm, prop_body)


def test_appended_lines_classify_as_insert_only(tmp_vault: Path):
    _pair(
        tmp_vault,
        "a__x",
        "line one\nline two\n",
        "line one\nline two\nline three\n",
    )

    rewrites = C.classify(tmp_vault)

    assert len(rewrites) == 1
    r = rewrites[0]
    assert r.kind == "insert_only"
    assert r.keep_ratio == 1.0
    assert r.inserted_lines == 1
    assert r.dropped_lines == 0
    assert r.key == "project/a__x"


def test_replaced_middle_line_classifies_as_mixed(tmp_vault: Path):
    _pair(
        tmp_vault,
        "a__y",
        "keep one\nOLD middle\nkeep two\n",
        "keep one\nNEW middle\nkeep two\n",
    )

    r = C.classify(tmp_vault)[0]

    assert r.kind == "mixed"
    assert 0.0 < r.keep_ratio < 1.0
    # A ``replace`` region counts on BOTH sides: one line left, one arrived.
    # Pinned so a later change to the opcode accounting cannot quietly turn
    # "1 line changed" into "+1" or "-1" alone.
    assert r.dropped_lines == 1
    assert r.inserted_lines == 1


def test_identical_body_is_insert_only_with_nothing_inserted(tmp_vault: Path):
    """A no-op proposal is safe to merge — and must report that it adds nothing.

    ``insert_only`` is ``changed <= {"insert"}``, a subset test, so an empty
    opcode-change set qualifies. That is correct (merging a no-op loses
    nothing), but it means ``--apply-safe`` will sweep such a proposal up, so
    the counts it reports have to be honest. Theoretical on the real vault
    today: all 35 staged rewrites differ substantively.
    """
    _pair(tmp_vault, "a__same", "one\ntwo\n", "one\ntwo\n")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "insert_only"
    assert r.keep_ratio == 1.0
    assert r.inserted_lines == 0
    assert r.dropped_lines == 0


def test_disjoint_body_classifies_as_full_rewrite(tmp_vault: Path):
    _pair(
        tmp_vault,
        "a__z",
        "MARKETPLACE_ENABLED = false\naba Loja removida\n",
        "MARKETPLACE_ENABLED = true\nliberada geral\n",
    )

    r = C.classify(tmp_vault)[0]

    assert r.kind == "full_rewrite"
    assert r.keep_ratio == 0.0


def test_proposal_without_a_live_rule_is_excluded(tmp_vault: Path):
    fm = "name: n\nslug: orphan\ntype: project"
    _write(tmp_vault / "shared" / "_inbox" / "project" / "orphan.proposed.md", fm, "body\n")

    assert C.classify(tmp_vault) == []


def test_update_proposed_suffix_is_excluded(tmp_vault: Path):
    fm = "name: n\nslug: a__u\ntype: project"
    _write(tmp_vault / "shared" / "project" / "a__u.md", fm, "body\n")
    _write(tmp_vault / "shared" / "_inbox" / "project" / "a__u.update-proposed.md", fm, "body2\n")

    assert C.classify(tmp_vault) == []


def test_blank_line_only_delta_is_insert_only_and_loses_nothing(tmp_vault: Path):
    """A blank line IS an inserted line — it just costs no live content.

    ``inserted_lines`` counts lines, blank ones included, so this reports 1.
    An earlier name claimed "no inserted content" and asserted only
    ``dropped_lines``, which made the one number the name was about the one
    number nobody checked. What matters for ``--apply-safe`` is that nothing
    was dropped.
    """
    _pair(tmp_vault, "a__b", "one\ntwo\n", "one\n\ntwo\n")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "insert_only"
    assert r.keep_ratio == 1.0
    assert r.dropped_lines == 0
    assert r.inserted_lines == 1


def test_partial_deletion_classifies_as_mixed(tmp_vault: Path):
    """A proposal that only removes lines is never ``insert_only``.

    ``changed == {"delete"}`` fails the ``changed <= {"insert"}`` subset test,
    so a delete-bearing proposal cannot reach ``--apply-safe``. This is the
    dominant real shape, not an edge case: 29 of the real vault's 35 staged
    rewrites delete live content. Until this test existed, the ``delete``
    opcode path was never exercised in isolation — the ``mixed`` case went
    through ``replace`` and the ``full_rewrite`` case through a two-line
    ``replace``.
    """
    _pair(tmp_vault, "a__del", "keep one\ndrop me\nkeep two\n", "keep one\nkeep two\n")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "mixed"
    assert r.dropped_lines == 1
    assert r.inserted_lines == 0


def test_total_deletion_classifies_as_full_rewrite(tmp_vault: Path):
    """Deleting every live line keeps nothing, so it is a full rewrite."""
    _pair(tmp_vault, "a__wipe", "gone one\ngone two\n", "")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "full_rewrite"
    assert r.keep_ratio == 0.0
    assert r.dropped_lines == 2
    assert r.inserted_lines == 0


def test_barely_surviving_body_is_mixed_not_full_rewrite(tmp_vault: Path):
    """Pins the ``mixed``/``full_rewrite`` boundary as exclusive at zero.

    ``full_rewrite`` means ``keep_ratio == 0.0`` exactly. One surviving line out
    of twenty is 0.05 and must stay ``mixed``, because ``full_rewrite`` is what
    licenses ``replace_wholesale`` to discard the live body. A refactor that
    rounded this to ``keep_ratio < 0.05`` would pass every other test in this
    file, which is why the boundary is pinned here.
    """
    live = "".join(f"l{i}\n" for i in range(20))
    _pair(tmp_vault, "a__thin", live, "l0\nbrand new\n")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "mixed"
    assert r.keep_ratio == 0.05
    assert r.dropped_lines == 19


def test_trailing_whitespace_only_change_is_reported_as_full_rewrite(tmp_vault: Path):
    """Documents a sharp edge: a cosmetic diff can read as ``full_rewrite``.

    ``_classify_bodies`` diffs whole lines, so ``"x "`` and ``"x"`` are a
    ``replace``, not a match. On a one-line body that means zero non-blank
    lines survive — ``keep_ratio == 0.0`` — and the proposal is labeled
    ``full_rewrite``, the label that licenses discarding the live body.

    This test pins current behavior rather than blessing it. It is the wrong
    direction for such a change (a stripped trailing space is not a superseded
    rule), but it is safe today: ``full_rewrite`` never enters
    ``--apply-safe``, so a human still sees the diff before anything is
    overwritten. Normalizing trailing whitespace before the diff would be the
    real fix; doing it here would change what ``keep_ratio`` means for every
    other case, so it belongs in its own change with its own measurement.
    """
    _pair(tmp_vault, "a__ws", "line two \n", "line two\n")

    r = C.classify(tmp_vault)[0]

    assert r.kind == "full_rewrite"
    assert r.keep_ratio == 0.0


def test_classification_covers_every_page_type_under_inbox(tmp_vault: Path):
    _pair(tmp_vault, "ref-rule", "a\n", "a\nb\n", page_type="reference")
    _pair(tmp_vault, "fb-rule", "a\n", "a\nb\n", page_type="feedback")

    keys = {r.key for r in C.classify(tmp_vault)}

    assert keys == {"reference/ref-rule", "feedback/fb-rule"}


def test_unreadable_proposal_raises_rather_than_vanishing(tmp_vault: Path):
    """An unreadable proposal must not drop silently out of the plan.

    ``classify`` deliberately does not guard the reads. A swallowed OSError
    made nothing distinguish "no rewrites are staged" from "every one of them
    failed to read" — the one failure mode a command whose purpose is making an
    invisible backlog visible must not have.
    """
    _pair(tmp_vault, "a__locked", "one\n", "one\ntwo\n")
    prop = tmp_vault / "shared" / "_inbox" / "project" / "a__locked.proposed.md"
    os.chmod(prop, 0o000)
    try:
        with pytest.raises(OSError):
            C.classify(tmp_vault)
    finally:
        os.chmod(prop, 0o644)
