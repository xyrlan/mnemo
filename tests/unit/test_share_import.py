"""``run_import``: stage what changed, never promote, never overwrite a stranger.

Every portable page here is built through the format piece's ``to_portable``
(see ``_share_stub``), so the tree's shape is whatever ``format`` says it is.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit._export_fixtures import write_rule
from tests.unit._share_stub import ensure_format, publish_rule

fmt = ensure_format()

from mnemo.core.share import imports as imp  # noqa: E402  — after the seam is in place

TODAY = "2026-09-13"


@pytest.fixture
def vault(tmp_vault: Path) -> Path:
    (tmp_vault / ".mnemo").mkdir(exist_ok=True)
    return tmp_vault


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo" / fmt.SHARE_DIR
    root.mkdir(parents=True)
    return root


def _run(vault: Path, tree: Path, *, dry_run: bool = False, project: str = "mnemo"):
    return imp.run_import(vault, share_root=tree, project=project, dry_run=dry_run, today=TODAY)


def _actions(report) -> dict[str, str]:
    return {d.slug or d.source.name: d.action for d in report.decisions}


def _ledger(vault: Path) -> dict:
    return json.loads((vault / ".mnemo" / "share" / "imports.json").read_text(encoding="utf-8"))


# --- the default route -----------------------------------------------------

def test_stages_into_inbox_with_local_project_and_no_promotion(vault, tree):
    publish_rule(tree, slug="use-yarn", vault="other-vault", project="their-clone",
                 quote="always yarn")

    report = _run(vault, tree, project="my-clone")

    assert _actions(report) == {"use-yarn": "staged"}
    staged = vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    assert staged.is_file()
    assert not (vault / "shared" / "feedback" / "use-yarn.md").exists()
    from mnemo.core.filters import parse_frontmatter
    fm = parse_frontmatter(staged.read_text(encoding="utf-8"))
    assert fmt.is_imported_frontmatter(fm)
    assert fm.get("projects") == ["my-clone"], "the LOCAL name is stamped, not the publisher's"
    assert fm.get("confidence") == "verified-elsewhere"
    assert fm.get("enforce") is None and fm.get("activates_on") is None
    assert report.staged == 1 and report.wrote == 1


def test_inferred_rule_stays_inferred(vault, tree):
    publish_rule(tree, slug="tidy", vault="other-vault", quote=None)

    _run(vault, tree)

    from mnemo.core.filters import parse_frontmatter
    fm = parse_frontmatter(
        (vault / "shared" / "_inbox" / "feedback" / "tidy.md").read_text(encoding="utf-8")
    )
    assert fm.get("confidence") == "inferred"


def test_no_extraction_state_entry_is_written(vault, tree):
    """#248: an entry would say 'the extractor owns this file'. It does not."""
    publish_rule(tree, slug="use-yarn", vault="other-vault")

    _run(vault, tree)

    assert not (vault / ".mnemo" / "extraction-state.json").exists()


# --- only what changed -----------------------------------------------------

def test_rerun_is_a_no_op_and_counts_unchanged(vault, tree):
    publish_rule(tree, slug="use-yarn", vault="other-vault")
    _run(vault, tree)
    staged = vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    before = staged.read_bytes()

    report = _run(vault, tree)

    assert _actions(report) == {"use-yarn": "unchanged"}
    assert staged.read_bytes() == before
    assert report.unchanged == 1 and report.wrote == 0


def test_deleted_from_inbox_is_not_restaged(vault, tree):
    """Removing the staged page is a review decision; the ledger remembers it."""
    publish_rule(tree, slug="use-yarn", vault="other-vault")
    _run(vault, tree)
    (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").unlink()

    report = _run(vault, tree)

    assert _actions(report) == {"use-yarn": "unchanged"}
    assert not (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").exists()


def test_republished_rule_overwrites_our_unreviewed_page(vault, tree):
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="v1\n")
    _run(vault, tree)
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="v2 with more\n")

    report = _run(vault, tree)

    assert _actions(report) == {"use-yarn": "staged"}
    text = (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").read_text(encoding="utf-8")
    assert "v2 with more" in text and "v1\n" not in text


def test_hand_edited_staged_page_is_refused_not_overwritten(vault, tree):
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="v1\n")
    _run(vault, tree)
    staged = vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    staged.write_text(staged.read_text(encoding="utf-8") + "\nmy note\n", encoding="utf-8")
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="v2\n")

    report = _run(vault, tree)

    (d,) = report.decisions
    assert d.action == "refused" and "edited" in d.reason
    assert "my note" in staged.read_text(encoding="utf-8")
    assert _ledger(vault)["rules"]["feedback/use-yarn"]["target"].endswith("use-yarn.md")


def test_extractor_owned_inbox_page_is_refused(vault, tree):
    write_rule(vault, slug="use-yarn", inbox=True)
    publish_rule(tree, slug="use-yarn", vault="other-vault")

    report = _run(vault, tree)

    (d,) = report.decisions
    assert d.action == "refused"
    assert "shared/_inbox/feedback/use-yarn.md" in d.reason
    assert "auto-promoted" in (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").read_text()


def test_same_tree_from_a_second_path_is_unchanged(vault, tree, tmp_path):
    """The ledger keys by rule, not by share root."""
    publish_rule(tree, slug="use-yarn", vault="other-vault")
    _run(vault, tree)
    import shutil
    other = tmp_path / "sibling-clone" / fmt.SHARE_DIR
    shutil.copytree(tree, other)

    report = _run(vault, other)

    assert _actions(report) == {"use-yarn": "unchanged"}


# --- yours ------------------------------------------------------------------

def test_own_publication_is_skipped_as_yours(vault, tree):
    mine = fmt.vault_id(vault)
    publish_rule(tree, slug="use-yarn", vault=mine)
    publish_rule(tree, slug="theirs", vault="other-vault")

    report = _run(vault, tree)

    assert _actions(report) == {"use-yarn": "yours", "theirs": "staged"}
    assert not (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").exists()
    assert report.yours == 1


# --- live page → proposed sibling -------------------------------------------

def test_live_page_gets_a_proposed_sibling_and_is_never_touched(vault, tree):
    live = write_rule(vault, slug="use-yarn", body="Local wording.\n")
    before = live.read_bytes()
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="Their wording.\n")

    report = _run(vault, tree)

    (d,) = report.decisions
    assert d.action == "proposed"
    assert d.target == vault / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    assert d.target.is_file() and "Their wording." in d.target.read_text(encoding="utf-8")
    assert live.read_bytes() == before
    assert not (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").exists()


def test_proposed_sibling_is_what_mnemo_rewrites_reviews(vault, tree):
    write_rule(vault, slug="use-yarn", body="Local wording.\n")
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="Local wording.\nMore.\n")
    _run(vault, tree)

    from mnemo.core.rewrites.classify import classify

    (rw,) = classify(vault)
    assert rw.key == "feedback/use-yarn"
    assert rw.live == vault / "shared" / "feedback" / "use-yarn.md"


def test_promoted_import_at_same_hash_is_unchanged_not_reproposed(vault, tree):
    """The user moved our staged page into shared/<type>/. A re-import of the
    same publication must not open a rewrite against it."""
    publish_rule(tree, slug="use-yarn", vault="other-vault")
    _run(vault, tree)
    staged = vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    live = vault / "shared" / "feedback" / "use-yarn.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(live)

    report = _run(vault, tree)

    assert _actions(report) == {"use-yarn": "unchanged"}
    assert not (vault / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md").exists()


def test_promoted_import_then_republished_becomes_a_proposal(vault, tree):
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="v1\n")
    _run(vault, tree)
    staged = vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    live = vault / "shared" / "feedback" / "use-yarn.md"
    live.parent.mkdir(parents=True, exist_ok=True)
    staged.rename(live)
    publish_rule(tree, slug="use-yarn", vault="other-vault", body="v2\n")

    report = _run(vault, tree)

    assert _actions(report) == {"use-yarn": "proposed"}
    assert "v1" in live.read_text(encoding="utf-8")


def test_stranger_proposed_sibling_is_refused(vault, tree):
    write_rule(vault, slug="use-yarn")
    sibling = vault / "shared" / "_inbox" / "feedback" / "use-yarn.proposed.md"
    sibling.parent.mkdir(parents=True, exist_ok=True)
    sibling.write_text("---\nslug: use-yarn\n---\nextractor's rewrite\n", encoding="utf-8")
    publish_rule(tree, slug="use-yarn", vault="other-vault")

    report = _run(vault, tree)

    (d,) = report.decisions
    assert d.action == "refused" and "use-yarn.proposed.md" in d.reason
    assert "extractor's rewrite" in sibling.read_text(encoding="utf-8")


# --- cross-type collision ---------------------------------------------------

def test_slug_live_under_another_type_is_refused_by_path(vault, tree):
    write_rule(vault, slug="use-yarn", page_type="reference")
    publish_rule(tree, slug="use-yarn", vault="other-vault", page_type="feedback")

    report = _run(vault, tree)

    (d,) = report.decisions
    assert d.action == "refused"
    assert "shared/reference/use-yarn.md" in d.reason
    assert not (vault / "shared" / "_inbox" / "feedback" / "use-yarn.md").exists()


def test_slug_under_another_type_in_inbox_only_does_not_block(vault, tree):
    """Consumer-visible is the bar; a staged draft of another type is not."""
    write_rule(vault, slug="use-yarn", page_type="reference", inbox=True)
    publish_rule(tree, slug="use-yarn", vault="other-vault", page_type="feedback")

    report = _run(vault, tree)

    assert _actions(report) == {"use-yarn": "staged"}


def test_evolving_page_of_another_type_does_not_block(vault, tree):
    write_rule(vault, slug="use-yarn", page_type="reference", stability="evolving")
    publish_rule(tree, slug="use-yarn", vault="other-vault", page_type="feedback")

    assert _actions(_run(vault, tree)) == {"use-yarn": "staged"}


# --- not a page -------------------------------------------------------------

def test_stray_markdown_is_reported_by_path_and_never_staged(vault, tree):
    (tree / "feedback").mkdir()
    stray = tree / "feedback" / "README.md"
    stray.write_text("# not a rule\n", encoding="utf-8")
    publish_rule(tree, slug="real", vault="other-vault")

    report = _run(vault, tree)

    by_name = {d.source.name: d for d in report.decisions}
    assert by_name["README.md"].action == "not-a-page"
    assert by_name["real.md"].action == "staged"
    assert not (vault / "shared" / "_inbox" / "feedback" / "README.md").exists()
    assert report.not_pages == 1


def test_empty_tree_yields_an_empty_report(vault, tree):
    report = _run(vault, tree)
    assert report.decisions == () and report.wrote == 0


# --- dry run ----------------------------------------------------------------

def test_dry_run_decides_everything_and_writes_nothing(vault, tree):
    write_rule(vault, slug="live-one")
    write_rule(vault, slug="clash", page_type="reference")
    publish_rule(tree, slug="fresh", vault="other-vault")
    publish_rule(tree, slug="live-one", vault="other-vault")
    publish_rule(tree, slug="clash", vault="other-vault")
    publish_rule(tree, slug="mine", vault=fmt.vault_id(vault))

    report = _run(vault, tree, dry_run=True)

    assert _actions(report) == {
        "fresh": "staged", "live-one": "proposed", "clash": "refused", "mine": "yours",
    }
    assert report.dry_run is True
    assert not (vault / "shared" / "_inbox").exists()
    assert not (vault / ".mnemo" / "share").exists()


def test_dry_run_targets_name_where_each_page_would_go(vault, tree):
    write_rule(vault, slug="live-one")
    publish_rule(tree, slug="fresh", vault="other-vault")
    publish_rule(tree, slug="live-one", vault="other-vault")

    targets = {d.slug: d.target for d in _run(vault, tree, dry_run=True).decisions}

    assert targets["fresh"] == vault / "shared" / "_inbox" / "feedback" / "fresh.md"
    assert targets["live-one"] == vault / "shared" / "_inbox" / "feedback" / "live-one.proposed.md"


# --- ledger -----------------------------------------------------------------

def test_ledger_records_hash_target_and_bytes_written(vault, tree):
    publish_rule(tree, slug="use-yarn", vault="other-vault")
    _run(vault, tree)

    led = _ledger(vault)
    entry = led["rules"]["feedback/use-yarn"]
    assert entry["target"] == "shared/_inbox/feedback/use-yarn.md"
    assert entry["vault"] == "other-vault"
    assert entry["from"] == str(tree)
    assert entry["at"] == TODAY
    assert entry["hash"] and entry["written"].startswith("sha256:")


def test_corrupt_ledger_reads_as_empty(vault, tree):
    p = vault / ".mnemo" / "share" / "imports.json"
    p.parent.mkdir(parents=True)
    p.write_text("{not json", encoding="utf-8")
    publish_rule(tree, slug="use-yarn", vault="other-vault")

    assert _actions(_run(vault, tree)) == {"use-yarn": "staged"}
    assert "feedback/use-yarn" in _ledger(vault)["rules"]


def test_a_failed_write_is_reported_and_the_run_continues(vault, tree, monkeypatch):
    publish_rule(tree, slug="a-bad", vault="other-vault")
    publish_rule(tree, slug="b-good", vault="other-vault")
    real = imp.atomic_write_bytes

    def flaky(path, data):
        if path.name == "a-bad.md":
            raise PermissionError("no")
        real(path, data)

    monkeypatch.setattr(imp, "atomic_write_bytes", flaky)

    report = _run(vault, tree)

    assert _actions(report) == {"a-bad": "failed", "b-good": "staged"}
    assert report.failed == 1
    assert set(_ledger(vault)["rules"]) == {"feedback/b-good"}


# --- pending (the notice's question) ---------------------------------------

def test_pending_hashes_exclude_seen_and_yours(vault, tree):
    publish_rule(tree, slug="seen", vault="other-vault")
    publish_rule(tree, slug="new", vault="other-vault")
    publish_rule(tree, slug="mine", vault=fmt.vault_id(vault))
    (tree / "feedback" / "README.md").write_text("stray\n", encoding="utf-8")
    _run(vault, tree)
    publish_rule(tree, slug="new", vault="other-vault", body="changed\n")

    pending = imp.pending_hashes(vault, tree)

    assert len(pending) == 1
    assert imp.pending_hashes(vault, tree / "nope") == []


def test_notice_marker_is_per_project_and_per_pending_set(vault):
    led = imp.load_ledger(vault)
    assert not imp.notice_shown(led, "app", "d1")
    imp.mark_notice_shown(vault, "app", "d1")
    led = imp.load_ledger(vault)
    assert imp.notice_shown(led, "app", "d1")
    assert not imp.notice_shown(led, "app", "d2"), "a changed tree invites again"
    assert not imp.notice_shown(led, "other", "d1"), "another repo gets its own"
