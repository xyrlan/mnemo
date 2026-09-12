"""Frontmatter merge policy for staged rewrites (#159).

Policies are not arbitrary. Measured on the real vault: ``sources[]`` deltas in
the auto-merge set are purely the ``/Users/xyrlan/mnemo/`` absolute-path prefix
(#163, fixed v1.3.3) — but 3 of 11 have live=2 → prop=1, so proposal-wins drops
a source. Live ``description`` values are factually stale ("bloqueia assinante em
dia" vs "RESOLVIDO 2026-08-11"). And ``tdd-red-green-per-feature`` flips
``auto-promoted`` → ``needs-review``, which would re-mark a reviewed rule as a
draft.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.filters import is_consumer_visible, parse_frontmatter
from mnemo.core.rewrites import merge as M


def test_live_page_without_frontmatter_is_refused(tmp_vault: Path):
    """Skip rather than corrupt, as ``dedup_rules._merge_group_inplace`` does.

    ``_build`` used to start from an empty frontmatter string, append the
    proposal's scalars and wrap them in fresh fences — fabricating a rule with a
    ``name``, ``description`` and ``sources`` the live file never had.
    ``classify`` only requires the live file to exist, not to carry frontmatter,
    so this was reachable through the sanctioned pipeline.

    The bodies here are deliberately insert-only (the proposal appends one line
    and drops nothing), so ``merge_insert_only``'s precondition check passes and
    execution actually reaches ``_build`` — the guard under test. An earlier
    version of this test used disjoint bodies and tripped ``NotInsertOnly``
    first, proving nothing about frontmatter.
    """
    live = "plain body, no fences\n"
    proposal = (
        "---\nname: n\nslug: s\ntype: project\ndescription: invented\n---\n\n"
        "plain body, no fences\nappended\n"
    )

    with pytest.raises(M.NoLiveFrontmatter):
        M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    # replace_wholesale has no precondition check, so it reaches _build directly
    # — the same refusal must hold there or the corruption path stays open.
    with pytest.raises(M.NoLiveFrontmatter):
        M.replace_wholesale(live, proposal, vault_root=tmp_vault)


def test_scalar_values_are_requoted_for_real_yaml(tmp_vault: Path):
    """``parse_frontmatter`` dequotes, so re-rendering must re-quote.

    A ``description`` of ``#hashtag first`` interpolated raw becomes
    ``description: #hashtag first``. This codebase's own reader tolerates that,
    but every standards-compliant YAML parser — Obsidian's included — reads it as
    ``null`` followed by a comment. Rendering through the writer's own
    ``_yaml_scalar`` keeps merge output byte-compatible with what the writer
    would have produced.
    """
    live = "---\nname: n\nslug: s\ntype: project\ndescription: old\n---\n\nb\n"
    proposal = (
        "---\nname: n\nslug: s\ntype: project\n"
        "description: '#hashtag first'\n---\n\nb\nmore\n"
    )

    out = M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    assert "description: '#hashtag first'" in out
    assert parse_frontmatter(out)["description"] == "#hashtag first"


def test_stability_comes_from_the_live_rule(tmp_vault: Path):
    """A tentative transcript must not hide a stable rule from recall.

    ``filters.is_consumer_visible`` treats ``stability: evolving`` as
    non-visible, and the extraction prompt shows the model emitting ``evolving``
    when a decision reads as still in flux. ``stability`` was in
    ``_PROPOSAL_WINS`` unmeasured, so one such proposal would have flipped a
    stable rule to ``evolving`` and silently dropped it from recall, reflex and
    export — the exact failure #159 exists to fix.
    """
    live = "---\nname: n\nslug: s\ntype: feedback\nstability: stable\n---\n\nb\n"
    proposal = "---\nname: n\nslug: s\ntype: feedback\nstability: evolving\n---\n\nb\nmore\n"

    out = M.merge_insert_only(live, proposal, vault_root=tmp_vault)
    fm = parse_frontmatter(out)

    assert fm["stability"] == "stable"
    assert is_consumer_visible(tmp_vault / "shared" / "feedback" / "s.md", fm, tmp_vault)


def test_merge_insert_only_refuses_a_pair_that_drops_live_content(tmp_vault: Path):
    """The precondition is checked, not merely documented.

    Handed a ``mixed`` pair this behaved exactly like ``replace_wholesale`` and
    dropped the live-only lines with no signal. ``apply._ACTION_FOR_KIND`` never
    does that, but this is a plain public function and a repair script would.
    The check reuses ``classify._classify_bodies`` so "insert only" has one
    definition rather than two that can drift.
    """
    live = "---\nname: n\nslug: s\ntype: project\n---\n\nkeep me\nDROPPED LIVE LINE\n"
    proposal = "---\nname: n\nslug: s\ntype: project\n---\n\nkeep me\n"

    with pytest.raises(M.NotInsertOnly):
        M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    # replace_wholesale is the honest way to discard live prose.
    out = M.replace_wholesale(live, proposal, vault_root=tmp_vault)
    assert "DROPPED LIVE LINE" not in out


def test_merge_invents_no_frontmatter_key_neither_page_had(tmp_vault: Path):
    """A merge must not add ``tags:``/``sources:`` to a page that lacked them.

    ``_rewrite_block`` appends ``key: []`` when the key is absent and there are
    no values, so calling it unconditionally grew a spurious ``tags: []`` line.
    Confirmed on real files: ``shared/project/clubinho__sprints-github.md`` and
    its staged proposal both have no ``tags:`` block.

    Invisible to every reader (``parse_frontmatter`` treats ``[]`` and absent
    alike, and ``project`` pages are outside ``_RETRIEVAL_TYPES``), but this
    pipeline exists because silent frontmatter churn made the backlog
    unreadable — inventing keys is the same class of change.
    """
    live = "---\nname: n\nslug: s\ntype: project\n---\n\nbody line\n"
    proposal = "---\nname: n\nslug: s\ntype: project\n---\n\nbody line\nmore\n"

    out = M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    assert "tags:" not in out
    assert "sources:" not in out
    assert "more" in out


def test_sources_are_normalized_and_unioned(tmp_vault: Path):
    live = (
        "---\nname: n\nslug: s\ntype: project\n"
        "sources:\n  - bots/a/memory/s.md\n  - bots/a/memory/extra.md\n---\n\nbody\n"
    )
    proposal = (
        "---\nname: n\nslug: s\ntype: project\n"
        f"sources:\n  - {tmp_vault}/bots/a/memory/s.md\n---\n\nbody\nmore\n"
    )

    out = M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    assert parse_frontmatter(out)["sources"] == [
        "bots/a/memory/s.md",
        "bots/a/memory/extra.md",
    ]


def _pair_text(live_fm: str, prop_fm: str, live_body: str = "a\n", prop_body: str = "a\nb\n"):
    return (
        f"---\n{live_fm}\n---\n\n{live_body}",
        f"---\n{prop_fm}\n---\n\n{prop_body}",
    )


def test_description_comes_from_the_proposal(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project\ndescription: only 2 of 5 events — bloqueia assinante",
        "name: n\nslug: s\ntype: project\ndescription: RESOLVIDO 2026-08-11 (issue #285)",
    )

    out = M.merge_insert_only(live, prop, vault_root=tmp_vault)

    assert parse_frontmatter(out)["description"] == "RESOLVIDO 2026-08-11 (issue #285)"


def test_activation_keys_come_from_the_live_rule(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project\nactivates_on: git commit\nconfidence: explicit",
        "name: n\nslug: s\ntype: project\nactivates_on: npm test\nconfidence: inferred",
    )

    fm = parse_frontmatter(M.merge_insert_only(live, prop, vault_root=tmp_vault))

    assert fm["activates_on"] == "git commit"
    assert fm["confidence"] == "explicit"


def test_managed_tag_marker_is_never_copied_from_the_proposal(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: reference\ntags:\n  - auto-promoted\n  - testing\n  - tdd",
        "name: n\nslug: s\ntype: reference\ntags:\n  - needs-review\n  - testing\n  - process",
    )

    tags = parse_frontmatter(M.merge_insert_only(live, prop, vault_root=tmp_vault))["tags"]

    assert "auto-promoted" in tags
    assert "needs-review" not in tags
    # Topic tags from both sides survive.
    assert {"testing", "tdd", "process"} <= set(tags)


def test_insert_only_merge_preserves_every_live_body_line(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project",
        "name: n\nslug: s\ntype: project",
        live_body="first\nsecond\n",
        prop_body="first\nsecond\nthird\n",
    )

    out = M.merge_insert_only(live, prop, vault_root=tmp_vault)

    for line in ("first", "second", "third"):
        assert line in out


def test_merge_is_idempotent(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project\nsources:\n  - bots/a/memory/s.md",
        "name: n\nslug: s\ntype: project\nsources:\n  - bots/a/memory/s.md",
    )

    once = M.merge_insert_only(live, prop, vault_root=tmp_vault)
    twice = M.merge_insert_only(once, prop, vault_root=tmp_vault)

    assert once == twice


def test_enforce_block_never_crosses_from_the_proposal(tmp_vault: Path):
    """``enforce`` is a live-wins nested block, and the asymmetry matters.

    ``rendering._render_page`` strips ``enforce`` from auto-promoted pages as a
    safety rail (C3, 2026-04-23) so one briefing line cannot become a
    session-wide hard block. A proposal's ``enforce`` reaching a live rule would
    defeat that rail, and losing a live rule's own ``enforce`` would silently
    disarm a rule a human armed.
    """
    live = (
        "---\nname: n\nslug: s\ntype: feedback\n"
        "enforce:\n  deny_command: git push\n  deny_pattern: --force\n---\n\nb\n"
    )
    proposal = (
        "---\nname: n\nslug: s\ntype: feedback\n"
        "enforce:\n  deny_command: rm\n---\n\nb\nmore\n"
    )

    out = M.merge_insert_only(live, proposal, vault_root=tmp_vault)

    assert "deny_command: git push" in out
    assert "deny_pattern: --force" in out
    assert "deny_command: rm" not in out


def test_demoted_from_and_timestamps_follow_their_policies(tmp_vault: Path):
    """``demoted_from`` is live-wins; the run stamps are proposal-wins.

    Both rows sat untested after Task 4. ``demoted_from`` records a reclassify
    decision, which is not the extractor's to revise from a transcript, while the
    stamps describe the run that produced the proposal and should follow it.
    """
    live = (
        "---\nname: n\nslug: s\ntype: reference\n"
        "demoted_from: feedback\nextraction_run: 2026-05-01T00:00:00\n---\n\nb\n"
    )
    proposal = (
        "---\nname: n\nslug: s\ntype: reference\n"
        "demoted_from: user\nextraction_run: 2026-09-12T10:00:00\n---\n\nb\nmore\n"
    )

    fm = parse_frontmatter(M.merge_insert_only(live, proposal, vault_root=tmp_vault))

    assert fm["demoted_from"] == "feedback"
    assert fm["extraction_run"] == "2026-09-12T10:00:00"


def test_replace_wholesale_takes_the_proposal_body(tmp_vault: Path):
    live, prop = _pair_text(
        "name: n\nslug: s\ntype: project",
        "name: n\nslug: s\ntype: project",
        live_body="MARKETPLACE_ENABLED = false\n",
        prop_body="MARKETPLACE_ENABLED = true\n",
    )

    out = M.replace_wholesale(live, prop, vault_root=tmp_vault)

    assert "MARKETPLACE_ENABLED = true" in out
    assert "MARKETPLACE_ENABLED = false" not in out
