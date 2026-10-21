"""Vault-wide index with consumer-visibility gate + per-doc projects/universal."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.reflex.index import build_index, load_index

FRONTMATTER_TEMPLATE = (
    "---\n"
    "name: {name}\n"
    "description: {description}\n"
    "tags:\n"
    "{tags_block}"
    "sources:\n"
    "{sources_block}"
    "{extra}"
    "stability: {stability}\n"
    "---\n"
    "{body}\n"
)


def _write_rule(vault: Path, subdir: str, filename: str, **kw) -> Path:
    path = vault / "shared" / subdir / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = kw.get("tags") or ["auto-promoted"]
    sources = kw.get("sources") or ["bots/mnemo/memory/example.md"]
    aliases = kw.get("aliases")
    extra = ""
    if aliases:
        extra = "aliases:\n" + "".join(f"  - {a}\n" for a in aliases)
    path.write_text(FRONTMATTER_TEMPLATE.format(
        name=kw.get("name", filename.replace(".md", "")),
        description=kw.get("description", "desc"),
        tags_block="".join(f"  - {t}\n" for t in tags),
        sources_block="".join(f"  - {s}\n" for s in sources),
        extra=extra,
        stability=kw.get("stability", "stable"),
        body=kw.get("body", "Actual rule body content."),
    ), encoding="utf-8")
    return path


def test_build_index_includes_only_consumer_visible_rules(tmp_vault):
    # Visible rule
    _write_rule(tmp_vault, "feedback", "keep.md", name="keep")
    # Inbox rule — must be skipped
    _write_rule(tmp_vault, "_inbox/feedback", "draft.md", name="draft")
    # needs-review but hand-promoted into shared/feedback/ — must be KEPT
    # (v0.18: location is the authority on draft-ness, not the stale tag).
    _write_rule(tmp_vault, "feedback", "review.md", name="review",
                tags=["needs-review"])
    # needs-review AND still staged — must be skipped, on location
    _write_rule(tmp_vault, "_inbox/feedback", "staged.md", name="staged",
                tags=["needs-review"])
    # evolving — must be skipped
    _write_rule(tmp_vault, "feedback", "evolving.md", name="flaky",
                stability="evolving")

    idx = build_index(tmp_vault, universal_threshold=2)
    slugs = set(idx["docs"].keys())
    assert slugs == {"keep", "review"}, f"expected keep+review, got {slugs}"


def test_build_index_emits_projects_and_universal_per_doc(tmp_vault):
    _write_rule(tmp_vault, "feedback", "a.md", name="a",
                sources=["bots/projA/memory/x.md"])
    _write_rule(tmp_vault, "feedback", "b.md", name="b",
                sources=["bots/projA/memory/y.md", "bots/projB/memory/z.md"])

    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["docs"]["a"]["projects"] == ["projA"]
    assert idx["docs"]["a"]["universal"] is False
    assert idx["docs"]["b"]["projects"] == ["projA", "projB"]
    assert idx["docs"]["b"]["universal"] is True


def test_build_index_indexes_aliases_field(tmp_vault):
    _write_rule(tmp_vault, "feedback", "c.md", name="c",
                description="Mock database in tests",
                aliases=["banco", "database", "db"])

    idx = build_index(tmp_vault, universal_threshold=2)

    # "banco" must appear in postings → points back to slug "c".
    assert "banco" in idx["postings"]
    assert any(p["slug"] == "c" for p in idx["postings"]["banco"])
    assert idx["docs"]["c"]["field_length"]["aliases"] == 3


def test_build_index_keeps_auto_promoter_advisory_out_of_preview_and_tokens(tmp_vault):
    """#134: the stripped-enforce note must neither open the preview Claude
    receives nor feed the BM25 postings."""
    advisory = (
        "> _mnemo auto-promoter stripped an `enforce:` block from this rule._\n"
        "> _Review the pattern and re-add manually if safe._\n"
    )
    _write_rule(tmp_vault, "feedback", "top.md", name="top",
                body=advisory + "\nUse yarn, never npm.")
    _write_rule(tmp_vault, "feedback", "bottom.md", name="bottom",
                body="Use yarn, never npm.\n\n" + advisory)

    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["docs"]["top"]["preview"] == "Use yarn, never npm."
    assert idx["docs"]["bottom"]["preview"] == "Use yarn, never npm."
    for tok in ("promoter", "stripped", "advisory", "manually"):
        assert tok not in idx["postings"], f"advisory token {tok!r} was indexed"
    assert "yarn" in idx["postings"]


def test_build_index_keeps_frontmatter_out_of_body_field(tmp_vault):
    """#137: frontmatter keys, source paths and run ids are metadata, not rule
    text — they must not land in the ``body`` postings nor inflate its length."""
    _write_rule(tmp_vault, "feedback", "yarn.md", name="yarn",
                sources=["bots/mnemo/memory/zqpath-marker.md"],
                body="Use yarn, never npm.")

    idx = build_index(tmp_vault, universal_threshold=2)

    body_terms = {
        term for term, entries in idx["postings"].items()
        if any(e["slug"] == "yarn" and e["tf"]["body"] > 0 for e in entries)
    }
    for tok in ("---", "sources", "stability", "zqpath-marker", "stable"):
        assert tok not in body_terms, f"frontmatter token {tok!r} indexed as body"
    assert "yarn" in body_terms
    assert idx["docs"]["yarn"]["field_length"]["body"] == len(
        ["use", "yarn", "never", "npm"])


def test_build_index_schema_shape(tmp_vault):
    _write_rule(tmp_vault, "feedback", "a.md", name="a")
    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["schema_version"] == 1
    assert "generated_at" in idx
    # No scope / project top-level fields (C3 fix).
    assert "scope" not in idx
    assert "project" not in idx
    assert isinstance(idx["avg_field_length"], dict)
    assert set(idx["avg_field_length"]) == {
        "name", "topic_tags", "aliases", "description", "body", "evidence",
    }
    assert isinstance(idx["postings"], dict)
    assert isinstance(idx["docs"], dict)


def test_build_index_tolerates_non_utf8_files(tmp_vault):
    """A .md with invalid UTF-8 must not abort the build."""
    path = tmp_vault / "shared" / "feedback" / "broken.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    # Valid frontmatter + bogus high-byte sequence in the body
    path.write_bytes(
        b"---\nname: broken\ndescription: d\ntags:\n  - t\n"
        b"sources:\n  - bots/mnemo/memory/x.md\nstability: stable\n---\n"
        b"\xff\xfe garbled body \xc3\x28\n"
    )
    _write_rule(tmp_vault, "feedback", "ok.md", name="ok")

    idx = build_index(tmp_vault, universal_threshold=2)
    slugs = set(idx["docs"].keys())
    assert "ok" in slugs
    assert "broken" in slugs  # errors="replace" preserves the rule


def test_reflex_load_index_logs_corrupt_json(tmp_vault):
    """Corruption leaves a trail. Missing / version-skew stay silent."""
    errors_log = tmp_vault / ".errors.log"
    mnemo_dir = tmp_vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "reflex-index.json").write_text("{ broken ", encoding="utf-8")

    assert load_index(tmp_vault) is None
    assert errors_log.exists()
    assert "reflex.index.load.parse" in errors_log.read_text(encoding="utf-8")


def test_reflex_load_index_silent_on_missing_file(tmp_vault):
    errors_log = tmp_vault / ".errors.log"
    assert load_index(tmp_vault) is None
    assert not errors_log.exists()


def test_reflex_load_index_silent_on_version_skew(tmp_vault):
    errors_log = tmp_vault / ".errors.log"
    mnemo_dir = tmp_vault / ".mnemo"
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    (mnemo_dir / "reflex-index.json").write_text(
        json.dumps({"schema_version": 999, "docs": {}, "postings": {}}),
        encoding="utf-8",
    )
    assert load_index(tmp_vault) is None
    assert not errors_log.exists()


def test_build_index_scopes_an_imported_promoted_rule_to_its_local_project(tmp_vault):
    """#302: an imported page has ``sources: []`` and carries its project only
    in ``projects:``. Without the frontmatter fallback it indexed with no
    project, so ``candidates_for_project`` never offered it and the reflex
    never injected it — while the MCP tools, which read the page, listed it.

    The page is the real import output (``to_vault_page``), promoted the way a
    human promotes a draft: moved from ``_inbox/`` into ``shared/feedback/``.
    """
    from mnemo.core.reflex.decide import candidates_for_project
    from mnemo.core.share import from_portable, to_portable, to_vault_page
    from tests.unit._export_fixtures import write_rule

    native = write_rule(tmp_vault, slug="use-yarn", quote="always yarn",
                        projects=("theirs",)).read_text(encoding="utf-8")
    rule = from_portable(to_portable(native, vault="v-one", project="theirs",
                                     today="2026-09-01"))
    staged = to_vault_page(rule, project="mine", today="2026-09-13")
    for stale in (tmp_vault / "shared").rglob("*.md"):
        stale.unlink()
    promoted = tmp_vault / "shared" / "feedback" / "use-yarn.md"
    promoted.parent.mkdir(parents=True, exist_ok=True)
    promoted.write_text(staged, encoding="utf-8")

    idx = build_index(tmp_vault, universal_threshold=2)

    assert idx["docs"]["use-yarn"]["projects"] == ["mine"]
    assert idx["docs"]["use-yarn"]["universal"] is False
    assert candidates_for_project(idx, "mine") == ["use-yarn"]
    assert candidates_for_project(idx, "theirs") == []


def test_each_doc_points_at_its_page_so_the_hook_can_read_its_body(tmp_vault):
    """#542: the hook reads the whole body of the rules it emits from here."""
    page = _write_rule(tmp_vault, "feedback", "use-yarn.md", name="yarn-rule", body="Use yarn.")
    idx = build_index(tmp_vault)
    assert idx["docs"]["yarn-rule"]["path"] == "shared/feedback/use-yarn.md"
    assert (tmp_vault / idx["docs"]["yarn-rule"]["path"]) == page


def _lines_run(build, vault: Path) -> int:
    """Run ``build(vault)`` and count the lines of mnemo's own code it ran.

    A count of work, not of seconds: the same vault costs the same count on an
    idle box and under every pytest-xdist worker at once (#750), where wall
    time measured the neighbours. The tracer that was installed — coverage's,
    under CI — is put back, and loses only this one call."""
    import sys

    import mnemo

    package = str(Path(mnemo.__file__).parent)
    lines = [0]

    def count(frame, event, arg):
        if event == "line":
            lines[0] += 1
        return count

    def enter(frame, event, arg):
        return count if frame.f_code.co_filename.startswith(package) else None

    previous = sys.gettrace()
    sys.settrace(enter)
    try:
        index = build(vault)
    finally:
        sys.settrace(previous)
    assert len(index["postings"]["common00"]) == index["doc_count"]
    return lines[0]


def _shared_term_vault(root: Path, n: int) -> Path:
    """``n`` pages of one shape, all sharing 60 terms: only ``n`` varies."""
    words = " ".join("common%02d" % i for i in range(60))
    vault = root / ("v%d" % n)
    for i in range(n):
        _write_rule(vault, "feedback", "r%04d.md" % i, name="rule %04d" % i,
                    body=words + " unique%04d" % i)
    return vault


def _growth(build, root: Path) -> float:
    """How many times the work grows when the vault grows eightfold."""
    small = _lines_run(build, _shared_term_vault(root, 25))
    large = _lines_run(build, _shared_term_vault(root, 200))
    return large / small


def test_build_index_is_linear_in_the_number_of_pages_sharing_a_term(tmp_path):
    """#610: the postings merge scanned a term's whole list for the current
    slug, once per term per page, so a vault of N pages that share common
    words cost O(N^2): 24 s of SessionStart's 35 s on a 3,400-rule vault.

    The bound counts lines run, not seconds. An absolute 4 s failed at 10.8 s
    on a dev box running other sessions (#729); the ratio of two timings that
    replaced it failed at 21.5x on a Windows CI runner under pytest-xdist
    (#750). Every page here has the same shape, so a linear build runs the
    same lines per page plus a fixed few: eight times the pages cost at most
    eight times the lines, exactly, on any load. The #610 merge runs 29x.
    A scan in C — ``x in list`` — runs no lines and would not be counted."""
    growth = _growth(build_index, tmp_path)
    assert growth <= 8, "8x the pages ran %.1fx the lines" % growth


def test_the_linear_bound_fails_the_quadratic_merge_of_610(tmp_path):
    """The bound above catches the regression it guards: the #610 merge,
    put back into this build_index, fails it."""
    from mnemo.core.reflex import index as index_mod

    source = Path(index_mod.__file__).read_text(encoding="utf-8")
    fixed = "bucket = buckets.get(tok)"
    assert source.count(fixed) == 1, "build_index changed; update this injection"
    scan = ('bucket = next((e for e in postings.get(tok, ()) '
            'if e["slug"] == slug), None)')
    namespace = {"__name__": index_mod.__name__}
    exec(compile(source.replace(fixed, scan), index_mod.__file__, "exec"), namespace)

    growth = _growth(namespace["build_index"], tmp_path)
    assert growth > 8, "the quadratic merge ran only %.1fx the lines" % growth
