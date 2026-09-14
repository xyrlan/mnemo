"""The portable page contract (#245): vault page ⇄ portable page ⇄ staged page.

Pages come from ``_export_fixtures.write_rule`` (the ``_render_page`` shape)
and from one copy of a real vault page, never hand-typed frontmatter.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core.filters import is_consumer_visible, parse_frontmatter, topic_tags
from mnemo.core.share import (
    SHARE_DIR,
    PortableRule,
    from_portable,
    is_imported_frontmatter,
    iter_portable,
    portable_hash,
    to_portable,
    to_vault_page,
    vault_id,
)
from tests.unit._export_fixtures import write_rule

TODAY = "2026-09-13"

# One page copied from the real vault, shape as the extractor left it there.
REAL_PAGE = """---
name: 'Totals in reports must use global aggregates, not row sums'
slug: aggregation-totals-vs-line-sums
description: 'When clients have overlapping attributes across rows, total row must reuse global aggregates, not sum line items'
type: feedback
extracted_at: 2026-08-19T16:41:05
extraction_run: 2026-08-19T16:41:05
stability: stable
confidence: verified
last_sync: 2026-08-19T16:41:05
sources:
  - bots/clubinho/briefings/sessions/887285b2-cfc0-4b0a-88cf-63b8d4be69b9.md
tags:
  - auto-promoted
  - data-integrity
  - reporting
  - testing
evidence:
  quote: 'No nosso @painel/ em ativacao do app ajustar contendo a quantidade de ativações por plano.'
  source: 'briefing: bots/clubinho/briefings/sessions/887285b2-cfc0-4b0a-88cf-63b8d4be69b9.md — user turns, turn 1'
---

In reports where a single entity can appear across multiple rows, the total row must use the global aggregate figures, NOT the sum of the individual line items.

**Why:** summing line items creates a divergent total.

<!-- mnemo:graph-section -->
## Sources
- [[bots/clubinho/briefings/sessions/887285b2-cfc0-4b0a-88cf-63b8d4be69b9]]
"""

# A demoted page carrying every local-only block the contract says must not travel.
LOCAL_ONLY_PAGE = """---
name: Yarn as canonical package manager
slug: yarn-as-canonical-package-manager
description: Use yarn classic for JS/TS dependency management in this project
type: reference
confidence: inferred
demoted_from: feedback
extracted_at: 2026-06-09T14:09:02
extraction_run: 2026-06-09T14:09:02
stability: stable
last_sync: 2026-06-09T14:09:02
promoted_without_enforce: true
runtime: false
sources:
  - bots/central-inteligencia-frontend/briefings/sessions/06ecbace.md
enforce:
  tool: Bash
  deny_pattern: '(?:^|&&|;|\\|)\\s*(?:sudo\\s+)?(?:npm|bun)\\s+(?:install|ci|i|add|remove|uninstall)\\b'
  reason: 'Project pins yarn classic.'
activates_on:
  path_globs:
    - package.json
tags:
  - auto-promoted
  - package-management
---

Always use yarn (classic, v1+) for JS/TS package management.

> _mnemo auto-promoter stripped an `enforce:` block from this rule._
> _Review the pattern and re-add manually if safe._

<!-- mnemo:graph-section -->
## Sources
- [[bots/central-inteligencia-frontend/briefings/sessions/06ecbace]]
"""


def _fixture_text(tmp_vault: Path, **kw) -> str:
    return write_rule(tmp_vault, **kw).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# SHARE_DIR
# --------------------------------------------------------------------------


def test_share_dir_is_not_under_a_gitignored_directory():
    assert SHARE_DIR == ".mnemo-shared"
    assert not SHARE_DIR.startswith(".mnemo/") and not SHARE_DIR.startswith(".claude/")


# --------------------------------------------------------------------------
# to_portable
# --------------------------------------------------------------------------


def test_to_portable_keeps_identity_evidence_topics_and_body(tmp_vault: Path):
    text = _fixture_text(tmp_vault, slug="use-yarn", quote="always yarn here", projects=("app", "other"))
    out = to_portable(text, vault="v-one", project="app", today=TODAY)
    assert out is not None
    fm = parse_frontmatter(out)
    assert fm["name"] == "Use yarn"
    assert fm["slug"] == "use-yarn"
    assert fm["description"] == "about use-yarn"
    assert fm["type"] == "feedback"
    assert fm["stability"] == "stable"
    assert fm["evidence"] == {"quote": "always yarn here", "source": "briefing: x — user turns, turn 1"}
    assert fm["published"] == {"vault": "v-one", "project": "app", "date": TODAY, "source_count": "2"}
    assert out.endswith("---\n\nDo the thing.\n")
    assert "graph-section" not in out and "[[" not in out


def test_to_portable_drops_every_local_only_key(tmp_vault: Path):
    out = to_portable(LOCAL_ONLY_PAGE, vault="v-one", project="app", today=TODAY)
    assert out is not None
    fm = parse_frontmatter(out)
    for key in ("sources", "extracted_at", "extraction_run", "last_sync", "promoted_at",
                "enforce", "activates_on", "demoted_from", "promoted_without_enforce", "runtime"):
        assert key not in fm, key
    assert "deny_pattern" not in out and "bots/" not in out
    # The auto-promoter's advisory note is local bookkeeping too.
    assert "_mnemo" not in out
    assert out.endswith("---\n\nAlways use yarn (classic, v1+) for JS/TS package management.\n")


def test_to_portable_strips_managed_tags_and_keeps_topics():
    out = to_portable(REAL_PAGE, vault="v", project="clubinho", today=TODAY)
    fm = parse_frontmatter(out)
    assert fm["tags"] == ["data-integrity", "reporting", "testing"]
    assert topic_tags(fm) == fm["tags"]


def test_to_portable_confidence_is_verified_or_inferred_only(tmp_vault: Path):
    verified = to_portable(REAL_PAGE, vault="v", project="p", today=TODAY)
    assert parse_frontmatter(verified)["confidence"] == "verified"
    no_key = to_portable(_fixture_text(tmp_vault, slug="x"), vault="v", project="p", today=TODAY)
    assert parse_frontmatter(no_key)["confidence"] == "inferred"
    demoted = to_portable(LOCAL_ONLY_PAGE, vault="v", project="p", today=TODAY)
    assert parse_frontmatter(demoted)["confidence"] == "inferred"


def test_to_portable_refuses_non_rule_pages():
    assert to_portable("# just a readme\n", vault="v", project="p", today=TODAY) is None
    assert to_portable("---\ntype: feedback\n---\n\nno slug\n", vault="v", project="p", today=TODAY) is None
    assert to_portable("---\nslug: x\n---\n\nno type\n", vault="v", project="p", today=TODAY) is None
    assert to_portable("---\nslug: x\ntype: feedback\n", vault="v", project="p", today=TODAY) is None


def test_to_portable_date_is_the_vaults_own_and_ignores_today():
    a = to_portable(REAL_PAGE, vault="v", project="p", today="2026-09-13")
    b = to_portable(REAL_PAGE, vault="v", project="p", today="2026-12-31")
    assert a == b
    assert parse_frontmatter(a)["published"]["date"] == "2026-08-19"


def test_to_portable_uses_today_only_for_an_untimestamped_page(tmp_vault: Path):
    text = _fixture_text(tmp_vault, slug="hand-written")
    assert "extracted_at" not in text
    a = to_portable(text, vault="v", project="p", today="2026-09-13")
    b = to_portable(text, vault="v", project="p", today="2026-12-31")
    assert parse_frontmatter(a)["published"]["date"] == "2026-09-13"
    assert parse_frontmatter(b)["published"]["date"] == "2026-12-31"


def test_to_portable_accepts_a_date_object_for_today(tmp_vault: Path):
    from datetime import date

    text = _fixture_text(tmp_vault, slug="hand-written")
    out = to_portable(text, vault="v", project="p", today=date(2026, 9, 13))
    assert parse_frontmatter(out)["published"]["date"] == "2026-09-13"


def test_to_portable_is_byte_stable(tmp_vault: Path):
    text = _fixture_text(tmp_vault, slug="stable", quote="q")
    assert to_portable(text, vault="v", project="p", today=TODAY) == to_portable(text, vault="v", project="p", today=TODAY)


def test_to_portable_quotes_yaml_specials_so_the_reader_round_trips():
    page = (
        "---\nname: 'Merge: needs --admin'\nslug: merge-admin\n"
        "description: 'why: [reasons] & more'\ntype: feedback\n---\n\nBody.\n"
    )
    out = to_portable(page, vault="v", project="p", today=TODAY)
    fm = parse_frontmatter(out)
    assert fm["name"] == "Merge: needs --admin"
    assert fm["description"] == "why: [reasons] & more"


# --------------------------------------------------------------------------
# from_portable
# --------------------------------------------------------------------------


def test_from_portable_round_trips_what_to_portable_wrote(tmp_vault: Path):
    text = _fixture_text(tmp_vault, slug="use-yarn", quote="always yarn", projects=("app", "b", "c"))
    portable = to_portable(text, vault="v-one", project="app", today=TODAY)
    rule = from_portable(portable)
    assert isinstance(rule, PortableRule)
    assert rule.slug == "use-yarn"
    assert rule.type == "feedback"
    assert rule.name == "Use yarn"
    assert rule.description == "about use-yarn"
    assert rule.body == "Do the thing.\n"
    assert rule.tags == ()
    assert rule.confidence == "inferred"
    assert rule.stability == "stable"
    assert rule.quote == "always yarn"
    assert rule.evidence_source == "briefing: x — user turns, turn 1"
    assert rule.vault == "v-one"
    assert rule.project == "app"
    assert rule.published_at == TODAY
    assert rule.source_count == 3
    assert rule.hash == portable_hash(portable)


def test_from_portable_reads_the_real_page():
    rule = from_portable(to_portable(REAL_PAGE, vault="v", project="clubinho", today=TODAY))
    assert rule.confidence == "verified"
    assert rule.tags == ("data-integrity", "reporting", "testing")
    assert rule.published_at == "2026-08-19"
    assert rule.source_count == 1
    assert rule.quote.startswith("No nosso @painel/")
    assert rule.body.startswith("In reports where") and "graph-section" not in rule.body


def test_from_portable_refuses_pages_without_provenance(tmp_vault: Path):
    assert from_portable("# README for the tree\n") is None
    assert from_portable(_fixture_text(tmp_vault, slug="vault-page")) is None
    assert from_portable("---\nslug: x\ntype: feedback\npublished:\n  project: p\n---\n\nno vault\n") is None
    assert from_portable("---\ntype: feedback\npublished:\n  vault: v\n---\n\nno slug\n") is None


def test_portable_rule_is_frozen(tmp_vault: Path):
    rule = from_portable(to_portable(_fixture_text(tmp_vault, slug="s"), vault="v", project="p", today=TODAY))
    with pytest.raises(AttributeError):
        rule.slug = "other"  # type: ignore[misc]


# --------------------------------------------------------------------------
# to_vault_page
# --------------------------------------------------------------------------


def _imported_page(tmp_vault: Path, **kw) -> str:
    kw.setdefault("slug", "use-yarn")
    kw.setdefault("quote", "always yarn")
    rule = from_portable(to_portable(_fixture_text(tmp_vault, **kw), vault="v-one", project="theirs", today="2026-09-01"))
    return to_vault_page(rule, project="mine", today=TODAY)


def test_to_vault_page_has_the_render_page_key_order(tmp_vault: Path):
    page = _imported_page(tmp_vault)
    keys = [line.split(":")[0] for line in page.split("\n---\n")[0].splitlines()[1:] if line and not line.startswith(" ")]
    assert keys == ["name", "slug", "description", "type", "stability", "confidence", "origin",
                    "projects", "sources", "tags", "evidence", "imported"]


def test_to_vault_page_stamps_import_provenance(tmp_vault: Path):
    page = _imported_page(tmp_vault)
    fm = parse_frontmatter(page)
    assert fm["origin"] == "imported"
    assert is_imported_frontmatter(fm)
    assert fm["projects"] == ["mine"]
    assert fm["sources"] == []
    assert fm["tags"][0] == "needs-review"
    assert fm["evidence"] == {"quote": "always yarn", "source": "briefing: x — user turns, turn 1"}
    assert fm["imported"] == {"vault": "v-one", "project": "theirs", "date": "2026-09-01",
                              "source_count": "1", "at": TODAY}
    assert page.endswith("---\n\nDo the thing.\n")


def test_to_vault_page_is_attributed_to_the_local_project(tmp_vault: Path):
    from mnemo.core.rule_activation.index import projects_for_rule

    fm = parse_frontmatter(_imported_page(tmp_vault))
    assert projects_for_rule(fm["sources"], frontmatter=fm) == ["mine"]


def test_to_vault_page_confidence_never_reads_as_the_users_own(tmp_vault: Path):
    verified = from_portable(to_portable(REAL_PAGE, vault="v", project="p", today=TODAY))
    assert parse_frontmatter(to_vault_page(verified, project="mine", today=TODAY))["confidence"] == "verified-elsewhere"
    inferred = from_portable(to_portable(LOCAL_ONLY_PAGE, vault="v", project="p", today=TODAY))
    assert parse_frontmatter(to_vault_page(inferred, project="mine", today=TODAY))["confidence"] == "inferred"


def test_to_vault_page_never_writes_enforce_or_activates_on():
    rule = from_portable(to_portable(LOCAL_ONLY_PAGE, vault="v", project="p", today=TODAY))
    page = to_vault_page(rule, project="mine", today=TODAY)
    fm = parse_frontmatter(page)
    assert "enforce" not in fm and "activates_on" not in fm
    assert "deny_pattern" not in page


def test_to_vault_page_is_a_draft_and_not_backfill(tmp_vault: Path):
    from mnemo.core.backfill.origin import is_backfill_frontmatter

    page = _imported_page(tmp_vault)
    fm = parse_frontmatter(page)
    assert not is_backfill_frontmatter(fm)
    staged = tmp_vault / "shared" / "_inbox" / "feedback" / "use-yarn.md"
    assert not is_consumer_visible(staged, fm, tmp_vault)


def test_to_vault_page_survives_the_flat_parser_too(tmp_vault: Path):
    from mnemo.core.extract.scanner import parse_frontmatter as flat_parse

    fm, _ = flat_parse(_imported_page(tmp_vault))
    assert is_imported_frontmatter(fm)
    assert fm["confidence"] == "inferred"


# --------------------------------------------------------------------------
# the hop: re-publishing an imported rule keeps the first vault
# --------------------------------------------------------------------------


def test_hop_preserves_the_first_vaults_provenance():
    first = to_portable(REAL_PAGE, vault="vault-a", project="clubinho", today="2026-09-01")
    staged = to_vault_page(from_portable(first), project="mine", today="2026-09-05")
    second = to_portable(staged, vault="vault-b", project="mine", today="2026-09-13")
    hop = from_portable(second)
    assert hop.vault == "vault-a"
    assert hop.project == "clubinho"
    assert hop.published_at == "2026-08-19"
    assert hop.source_count == 1
    assert hop.confidence == "verified"
    assert "vault-b" not in second
    assert parse_frontmatter(second)["tags"] == ["data-integrity", "reporting", "testing"]
    assert "origin" not in parse_frontmatter(second) and "imported" not in parse_frontmatter(second)


def test_hop_is_byte_identical_to_the_first_publish():
    first = to_portable(REAL_PAGE, vault="vault-a", project="clubinho", today="2026-09-01")
    staged = to_vault_page(from_portable(first), project="mine", today="2026-09-05")
    assert to_portable(staged, vault="vault-b", project="mine", today="2026-09-13") == first


# --------------------------------------------------------------------------
# iter_portable
# --------------------------------------------------------------------------


def test_iter_portable_walks_type_dirs_sorted_and_flags_strays(tmp_vault: Path, tmp_path: Path):
    root = tmp_path / "repo" / SHARE_DIR
    for slug in ("zeta", "alpha"):
        (root / "feedback").mkdir(parents=True, exist_ok=True)
        (root / "feedback" / f"{slug}.md").write_text(
            to_portable(_fixture_text(tmp_vault, slug=slug), vault="v", project="p", today=TODAY), encoding="utf-8"
        )
    (root / "reference").mkdir()
    (root / "reference" / "notes.md").write_text("# not a rule\n", encoding="utf-8")
    (root / "README.md").write_text("top-level, not walked\n", encoding="utf-8")
    (root / "feedback" / "nested").mkdir()
    (root / "feedback" / "nested" / "deep.md").write_text("too deep\n", encoding="utf-8")

    got = iter_portable(root)
    assert [p.relative_to(root).as_posix() for p, _ in got] == [
        "feedback/alpha.md", "feedback/zeta.md", "reference/notes.md",
    ]
    assert got[0][1].slug == "alpha" and got[1][1].slug == "zeta"
    assert got[2][1] is None


def test_iter_portable_missing_root_is_empty(tmp_path: Path):
    assert iter_portable(tmp_path / "nowhere") == []
    assert iter_portable(str(tmp_path / "nowhere")) == []


def test_iter_portable_undecodable_file_is_flagged_not_fatal(tmp_path: Path):
    root = tmp_path / SHARE_DIR
    (root / "feedback").mkdir(parents=True)
    (root / "feedback" / "bad.md").write_bytes(b"\xff\xfe---\nslug: x\n---\n")
    assert iter_portable(root) == [(root / "feedback" / "bad.md", None)]


# --------------------------------------------------------------------------
# portable_hash
# --------------------------------------------------------------------------


def test_portable_hash_ignores_line_endings_and_sees_content(tmp_vault: Path):
    text = to_portable(_fixture_text(tmp_vault, slug="s"), vault="v", project="p", today=TODAY)
    assert portable_hash(text) == portable_hash(text.replace("\n", "\r\n"))
    assert portable_hash(text) != portable_hash(text.replace("Do the thing", "Do another thing"))
    assert len(portable_hash(text)) == 64


def test_portable_hash_matches_the_rule_read_from_a_crlf_checkout(tmp_vault: Path, tmp_path: Path):
    text = to_portable(_fixture_text(tmp_vault, slug="s"), vault="v", project="p", today=TODAY)
    root = tmp_path / SHARE_DIR
    (root / "feedback").mkdir(parents=True)
    (root / "feedback" / "s.md").write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    [(_, rule)] = iter_portable(root)
    assert rule is not None and rule.hash == portable_hash(text)


# --------------------------------------------------------------------------
# vault_id
# --------------------------------------------------------------------------


def test_vault_id_is_created_once_and_read_back(tmp_vault: Path):
    first = vault_id(tmp_vault)
    assert (tmp_vault / ".mnemo" / "vault-id").read_text(encoding="utf-8") == first + "\n"
    assert vault_id(tmp_vault) == first
    assert vault_id(str(tmp_vault)) == first
    assert len(first) == 32 and int(first, 16) >= 0


def test_vault_id_is_random_not_derived(tmp_path: Path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    assert vault_id(a) != vault_id(b)
    (a / ".mnemo" / "vault-id").unlink()
    assert vault_id(a) != vault_id(b)


def test_vault_id_carries_no_pii(tmp_vault: Path, monkeypatch: pytest.MonkeyPatch):
    import socket

    vid = vault_id(tmp_vault)
    for secret in (socket.gethostname(), str(tmp_vault), "@"):
        assert secret not in vid


def test_vault_id_regenerates_a_blank_file(tmp_vault: Path):
    path = tmp_vault / ".mnemo" / "vault-id"
    path.parent.mkdir(parents=True)
    path.write_text("  \n", encoding="utf-8")
    vid = vault_id(tmp_vault)
    assert vid and path.read_text(encoding="utf-8").strip() == vid


# --------------------------------------------------------------------------
# is_imported_frontmatter
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fm, expected", [
    ({"origin": "imported"}, True),
    ({"metadata": {"origin": "imported"}}, True),
    ({"origin": "backfill"}, False),
    ({"metadata": {"origin": "backfill"}}, False),
    ({"origin": ""}, False),
    ({}, False),
    (None, False),
    ("origin: imported", False),
])
def test_is_imported_frontmatter(fm, expected):
    assert is_imported_frontmatter(fm) is expected
