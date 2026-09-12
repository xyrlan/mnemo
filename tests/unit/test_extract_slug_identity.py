"""A slug that reappears under a different type is the same rule, not a new one.

The three body-similarity layers (:func:`dedup._detect_drift_slug`,
:func:`dedup._detect_stem_collision`, :func:`dedup._detect_similar_existing`)
all filter state entries by ``key.startswith(f"{page.type}/")``, so a slug
reappearing under another type is invisible to every one of them. Lowering the
body threshold cannot rescue this: measured on the real vault, the live
duplicate pairs score 0.136 / 0.185 / 0.271 Jaccard, and 0.136 sits *below* the
p90 (0.131) of 79,800 unrelated pairs. Token overlap cannot see synonymy (#187).

Slug identity is the cheaper and stronger signal. Measured on the 2026-09-12
vault: 1852 pages, 1849 distinct slugs, 3 colliding — and the 1320 pages that
changed type in the 2026-09-02 reclassify left **zero** live twins behind, so a
slug living under two types is never a legitimate steady state here.

Two findings shape the design, both measured rather than assumed:

1. **Never redirect.** A redirect onto an ``auto_promoted`` slug does *not*
   route to a proposal: ``_is_upgrade`` requires ``not is_auto``, and a
   single-source page targets ``shared/<type>/``, so it reaches
   ``_apply_auto_promoted`` → ``_handle_target_exists``, which overwrites the
   sacred file outright (``branches/auto_promoted.py:118``). In every real pair
   one side is ``verified``+auto-promoted and the other ``inferred``, so a
   redirect would let the weaker page destroy the stronger one. We stage a
   ``.proposed.md`` and let a human merge.

2. **Never fire on a demoted page.** ``evidence.verify_page`` demotes a failed
   feedback page with ``replace(page, type="reference")`` and keeps the slug
   (``evidence.py:67``), so *every* demoted page is structurally a cross-type
   slug collision — 1386 of the 1852 live pages carry ``demoted_from:
   feedback``. Firing on those would walk them back into the injectable tier,
   reopening #177. The durable reading is the markdown marker: **0 of 3675**
   state entries carry ``unverified_feedback``, so the entry flag alone is dead
   weight on a real vault.
"""
from __future__ import annotations

from pathlib import Path

from mnemo.core.extract.inbox import apply_pages, dedup
from mnemo.core.extract.inbox.io import content_hash
from mnemo.core.extract.inbox.types import ExtractedPage
from mnemo.core.extract.scanner import ExtractionState, StateEntry

# The real #187 pair, close to verbatim. Their bodies share almost no
# vocabulary ("odometry / waypoint / global map" vs "trail / voting / arrival
# detection"), which is exactly why the body gate cannot see them.
FEEDBACK_BODY = (
    "Navigate via sequential waypoint marks without a global map.\n\n"
    "**Why:** accumulated error from odometry drifts over long routes.\n\n"
    "**How to apply:** chain the next waypoint off the previous mark."
)
REFERENCE_BODY = (
    "Cavebot v2 uses chain-based trail navigation with voting-based mark "
    "tracking and arrival detection.\n\n"
    "**Why:** coordinate estimation misses rotations.\n\n"
    "**How to apply:** implement trail tracking with a tuned voting radius."
)


def _seed(
    root: Path,
    state: ExtractionState,
    slug: str,
    page_type: str,
    name: str,
    body: str = FEEDBACK_BODY,
    status: str = "auto_promoted",
    demoted: bool = False,
    staged: bool = False,
) -> Path:
    """Write an existing page to disk and register its state entry.

    ``staged`` puts the page under ``shared/_inbox/<type>/`` instead of the
    sacred ``shared/<type>/`` — where the evidence gate parks a demoted page.
    """
    parts = [root, "shared"]
    if staged:
        parts.append("_inbox")
    parent = Path(*[str(p) for p in parts]) / page_type
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / f"{slug}.md"
    marker = "demoted_from: feedback\n" if demoted else ""
    path.write_text(
        f"---\nname: {name}\ndescription: {name}\ntype: {page_type}\n{marker}"
        f"sources:\n  - bots/a/briefings/sessions/1.md\ntags:\n  - navigation\n---\n{body}\n"
    )
    state.entries[f"{page_type}/{slug}"] = StateEntry(
        source_files=["bots/a/briefings/sessions/1.md"],
        source_hash="h0",
        written_hash=content_hash(path),
        written_at="r0",
        status=status,
    )
    return path


def _page(
    slug: str,
    page_type: str,
    name: str,
    body: str = REFERENCE_BODY,
    sources: list[str] | None = None,
    source_hash: str = "h1",
    unverified: bool = False,
) -> ExtractedPage:
    return ExtractedPage(
        slug=slug,
        type=page_type,
        name=name,
        description=name,
        body=body,
        source_files=sources or ["bots/b/briefings/sessions/2.md"],
        source_hash=source_hash,
        confidence="inferred" if unverified else "verified",
        unverified_feedback=unverified,
    )


# ---------------------------------------------------------------------------
# The premise: no threshold change could have fixed #187.
# ---------------------------------------------------------------------------


def test_real_duplicate_pair_is_invisible_to_the_body_gate():
    assert not dedup._bodies_similar(FEEDBACK_BODY, REFERENCE_BODY)


# ---------------------------------------------------------------------------
# The detector.
# ---------------------------------------------------------------------------


def test_detects_same_slug_under_a_different_type(tmp_path):
    """The #187 case: returns the type the slug already lives under."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback",
          "Chain-based navigation, no odometry")
    page = _page("chain-navigation-no-odometry", "reference",
                 "Chain-based navigation without odometry dependency")

    assert dedup._detect_slug_identity(page, state, tmp_path) == "feedback"


def test_fires_regardless_of_body_overlap(tmp_path):
    """Slug identity is the signal; the body gate is deliberately not consulted."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback", "Chain nav",
          body="Something about database indexes and query planners entirely.")
    page = _page("chain-navigation-no-odometry", "reference", "Chain nav")

    assert dedup._detect_slug_identity(page, state, tmp_path) == "feedback"


def test_ignores_a_slug_that_exists_only_under_the_same_type(tmp_path):
    """Same-type is the normal update path — not this layer's business."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "reference", "Chain nav")
    page = _page("chain-navigation-no-odometry", "reference", "Chain nav")

    assert dedup._detect_slug_identity(page, state, tmp_path) is None


def test_ignores_a_promoted_and_staged_copy_of_the_same_type(tmp_path):
    """The ``backup-by-risk-not-ritual`` shape: one state key, two files.

    ``shared/feedback/<slug>.md`` and ``shared/_inbox/feedback/<slug>.md`` are
    the same key, so this is the normal update path rather than a second rule.
    Listed in #187 alongside the cross-type pairs, but it is not one of them.
    """
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "backup-by-risk-not-ritual", "feedback", "Backup by risk")
    staged = tmp_path / "shared" / "_inbox" / "feedback"
    staged.mkdir(parents=True, exist_ok=True)
    (staged / "backup-by-risk-not-ritual.md").write_text(
        "---\nname: Backup by risk\ntype: feedback\n---\nStaged copy.\n"
    )
    page = _page("backup-by-risk-not-ritual", "feedback", "Backup by risk")

    assert dedup._detect_slug_identity(page, state, tmp_path) is None


def test_ignores_a_slug_that_does_not_collide(tmp_path):
    """The 1849 distinct slugs must never fire."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "some-other-rule", "feedback", "Some other rule")
    page = _page("chain-navigation-no-odometry", "reference", "Chain navigation")

    assert dedup._detect_slug_identity(page, state, tmp_path) is None


def test_ignores_a_state_entry_whose_file_is_gone(tmp_path):
    """Stale entries are skipped, as in every other layer.

    Three of the five cross-type slug keys in the real state file are exactly
    this shape: a ``feedback/`` entry with nothing on disk.
    """
    state = ExtractionState(last_run=None)
    path = _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback", "Chain nav")
    path.unlink()
    page = _page("chain-navigation-no-odometry", "reference", "Chain nav")

    assert dedup._detect_slug_identity(page, state, tmp_path) is None


# ---------------------------------------------------------------------------
# The #177 hazard. These are the tests that matter most.
# ---------------------------------------------------------------------------


def test_never_fires_when_the_incoming_page_is_demoted(tmp_path):
    """This run's gate said no; the layer must not launder it back."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback",
          "Chain-based navigation, no odometry")
    page = _page("chain-navigation-no-odometry", "reference", "Chain nav", unverified=True)

    assert dedup._detect_slug_identity(page, state, tmp_path) is None


def test_never_fires_when_the_existing_page_on_disk_is_demoted(tmp_path):
    """The durable reading, and the only one that works on a real vault.

    A run that re-emits a staged demoted page as a native ``reference`` page
    arrives with ``unverified_feedback=False`` — that is the #177 mechanism —
    so the guard has to read the marker off the staged markdown.
    """
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "vault-cleanup-before-ranking-work", "reference",
          "Deduplicate and promote inbox rules", demoted=True, staged=True, status="inbox")
    page = _page("vault-cleanup-before-ranking-work", "feedback",
                 "Deduplicate and promote inbox rules", body=FEEDBACK_BODY)

    assert dedup._detect_slug_identity(page, state, tmp_path) is None


def test_never_fires_onto_a_dismissed_entry(tmp_path):
    """Both apply branches bail on dismissed, so acting there destroys the page."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback", "Chain nav",
          status="dismissed")
    page = _page("chain-navigation-no-odometry", "reference", "Chain nav")

    assert dedup._detect_slug_identity(page, state, tmp_path) is None


# ---------------------------------------------------------------------------
# End to end through apply_pages: propose, never overwrite.
# ---------------------------------------------------------------------------


def test_cross_type_duplicate_stages_a_proposal_and_spares_the_sacred_page(tmp_path):
    """The #187 outcome: a reviewable proposal, and the verified page untouched."""
    state = ExtractionState(last_run=None)
    sacred = _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback",
                   "Chain-based navigation, no odometry")
    before = sacred.read_text()
    page = _page("chain-navigation-no-odometry", "reference",
                 "Chain-based navigation without odometry dependency")

    result = apply_pages([page], state, tmp_path, run_id="r1")

    # The sacred feedback page is byte-identical.
    assert sacred.read_text() == before
    # No second live page was minted under the other type.
    assert not (tmp_path / "shared" / "reference" / "chain-navigation-no-odometry.md").exists()
    # A proposal is staged for review, keyed to the type it would merge into.
    proposal = (
        tmp_path / "shared" / "_inbox" / "feedback"
        / "chain-navigation-no-odometry.proposed.md"
    )
    assert proposal.exists()
    assert [k for k, _ in result.sibling_proposed] == ["feedback/chain-navigation-no-odometry"]
    # The new page never acquired a state entry of its own.
    assert "reference/chain-navigation-no-odometry" not in state.entries


def test_restaging_the_same_proposal_does_not_churn_the_file(tmp_path):
    """The page gets no state entry, so every later run re-detects it.

    ``_render_page`` stamps ``extracted_at``/``extraction_run`` from the run id
    on every render, so a naive rewrite would rewrite an unreviewed proposal on
    every extract run forever, with only the timestamps moving.
    """
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback", "Chain nav")
    proposal = (
        tmp_path / "shared" / "_inbox" / "feedback"
        / "chain-navigation-no-odometry.proposed.md"
    )

    apply_pages([_page("chain-navigation-no-odometry", "reference", "Chain nav")],
                state, tmp_path, run_id="2026-09-12T10:00:00")
    first = proposal.read_text()

    apply_pages([_page("chain-navigation-no-odometry", "reference", "Chain nav")],
                state, tmp_path, run_id="2026-09-12T11:30:00")

    assert proposal.read_text() == first


def test_restaging_does_rewrite_when_the_rule_itself_changed(tmp_path):
    """Idempotence must not freeze a proposal whose content genuinely moved."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback", "Chain nav")
    proposal = (
        tmp_path / "shared" / "_inbox" / "feedback"
        / "chain-navigation-no-odometry.proposed.md"
    )

    apply_pages([_page("chain-navigation-no-odometry", "reference", "Chain nav")],
                state, tmp_path, run_id="2026-09-12T10:00:00")
    first = proposal.read_text()

    apply_pages([_page("chain-navigation-no-odometry", "reference", "Chain nav",
                       body="A materially different rule body entirely.")],
                state, tmp_path, run_id="2026-09-12T11:30:00")

    assert proposal.read_text() != first
    assert "materially different" in proposal.read_text()


def test_page_type_is_never_mutated(tmp_path):
    """Downstream reads ``page.type`` for the state key, the sticky-demotion
    probe (``apply._resolve_sticky_demotion``) and the similarity index.
    Rewriting it in the redirect chain silently breaks all three."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback", "Chain nav")
    page = _page("chain-navigation-no-odometry", "reference", "Chain nav")

    apply_pages([page], state, tmp_path, run_id="r1")

    assert page.type == "reference"
    assert page.slug == "chain-navigation-no-odometry"


def test_demoted_page_still_stages_as_reference_through_apply(tmp_path):
    """End-to-end #177 guard: the gate's demotion survives this layer."""
    state = ExtractionState(last_run=None)
    sacred = _seed(tmp_path, state, "chain-navigation-no-odometry", "feedback",
                   "Chain-based navigation, no odometry")
    before = sacred.read_text()
    page = _page("chain-navigation-no-odometry", "reference", "Chain nav", unverified=True)

    apply_pages([page], state, tmp_path, run_id="r1")

    assert sacred.read_text() == before
    assert (
        tmp_path / "shared" / "_inbox" / "reference" / "chain-navigation-no-odometry.md"
    ).exists()


def test_unrelated_page_is_untouched(tmp_path):
    """No collision, no interference with the normal write path."""
    state = ExtractionState(last_run=None)
    _seed(tmp_path, state, "some-other-rule", "feedback", "Some other rule")
    page = _page("chain-navigation-no-odometry", "reference", "Chain navigation")

    apply_pages([page], state, tmp_path, run_id="r1")

    assert "reference/chain-navigation-no-odometry" in state.entries
