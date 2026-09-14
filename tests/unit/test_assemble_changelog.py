"""changelog.d/ fragments fold into CHANGELOG.md's [Unreleased] at release.

Five issues dispatched in parallel (#233–#237) touched disjoint code and still
conflicted with each other in one file: every child added its entry at the top
of `## [Unreleased]`, so the first merge invalidated the other four. A change
now documents itself in its own file under `changelog.d/`, which nothing else
edits, and the release step assembles them. The assembled file must be the
same file it is today: same headings, same prose, nothing reformatted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools import assemble_changelog as ac

REPO = Path(__file__).resolve().parents[2]

CHANGELOG = """\
# Changelog

All notable changes to mnemo will be documented here.

## [Unreleased]

### Added

- **Existing added entry.** Already assembled. (#1)

### Fixed

- **Existing fixed entry.** Already assembled. (#2)

## [1.5.0] — 2026-09-13

### Added

- **Released added entry.** (#0)

### Fixed

- **Released fixed entry.** (#0)
"""


def _repo(tmp_path: Path, changelog: str = CHANGELOG, **fragments: str) -> Path:
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    d = tmp_path / "changelog.d"
    d.mkdir()
    for name, body in fragments.items():
        (d / name).write_text(body, encoding="utf-8")
    return tmp_path


def _unreleased(text: str) -> str:
    start = text.index("## [Unreleased]")
    end = text.index("\n## [", start + 1)
    return text[start:end]


def _released(text: str) -> str:
    return text[text.index("## [1.5.0]"):]


# --- a fragment lands under the right heading, in [Unreleased] only ---------


def test_a_fragment_lands_under_its_heading_in_unreleased(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"246.fixed.md": "- **New fixed entry.** (#246)\n"})

    consumed = ac.assemble(repo)

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    block = _unreleased(text)
    assert "- **New fixed entry.** (#246)" in block
    # Under `### Fixed`, not `### Added`: the entry sits between the two.
    assert block.index("### Fixed") < block.index("New fixed entry")
    assert "New fixed entry" not in block[: block.index("### Fixed")]
    assert [p.name for p in consumed] == ["246.fixed.md"]


def test_a_fragment_never_lands_in_a_released_version(tmp_path: Path) -> None:
    """`### Fixed` also exists under 1.5.0; only the first block is live."""
    repo = _repo(tmp_path, **{"246.fixed.md": "- **New fixed entry.** (#246)\n"})

    ac.assemble(repo)

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert _released(text) == _released(CHANGELOG)


def test_the_new_entry_goes_to_the_top_of_its_section(tmp_path: Path) -> None:
    """Newest first, which is how entries were added by hand until now."""
    repo = _repo(tmp_path, **{"246.fixed.md": "- **New fixed entry.** (#246)\n"})

    ac.assemble(repo)

    block = _unreleased((repo / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert block.index("New fixed entry") < block.index("Existing fixed entry")


def test_existing_entries_and_headings_survive_verbatim(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"246.fixed.md": "- **New fixed entry.** (#246)\n"})

    ac.assemble(repo)

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    for line in CHANGELOG.splitlines():
        assert line in text.splitlines()


def test_a_missing_heading_is_created_in_canonical_order(tmp_path: Path) -> None:
    """Changed sits between Added and Fixed, as in every released version."""
    repo = _repo(tmp_path, **{"246.changed.md": "- **A changed entry.** (#246)\n"})

    ac.assemble(repo)

    block = _unreleased((repo / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert block.index("### Added") < block.index("### Changed") < block.index("### Fixed")
    assert block.index("### Changed") < block.index("A changed entry")


def test_a_heading_after_every_existing_one_is_appended_at_the_end(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"246.internal.md": "- **An internal note.** (#246)\n"})

    ac.assemble(repo)

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    block = _unreleased(text)
    assert block.index("### Fixed") < block.index("### Internal")
    # Still separated from the next version heading by a blank line.
    assert "- **An internal note.** (#246)\n\n## [1.5.0]" in text


def test_an_empty_unreleased_gets_its_first_heading(tmp_path: Path) -> None:
    """Right after a release the block is bare — the common case."""
    bare = "# Changelog\n\n## [Unreleased]\n\n## [1.5.0] — 2026-09-13\n\n### Added\n\n- x\n"
    repo = _repo(tmp_path, bare, **{"246.fixed.md": "- **New fixed entry.** (#246)\n"})

    ac.assemble(repo)

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith(
        "# Changelog\n\n## [Unreleased]\n\n### Fixed\n\n- **New fixed entry.** (#246)\n\n## [1.5.0]"
    )


def test_several_fragments_in_one_section_are_all_present_and_ordered(tmp_path: Path) -> None:
    repo = _repo(
        tmp_path,
        **{
            "9.fixed.md": "- nine\n",
            "10.fixed.md": "- ten\n",
            "247.fixed.md": "- two-four-seven\n",
        },
    )

    ac.assemble(repo)

    block = _unreleased((repo / "CHANGELOG.md").read_text(encoding="utf-8"))
    # Numeric-aware, ascending, and each bullet separated by a blank line.
    assert "- nine\n\n- ten\n\n- two-four-seven\n" in block


def test_a_multi_paragraph_fragment_keeps_its_shape(tmp_path: Path) -> None:
    body = "- **Head.** First paragraph.\n\n  Second paragraph, indented under the bullet.\n"
    repo = _repo(tmp_path, **{"246.added.md": body})

    ac.assemble(repo)

    assert body in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


# --- consumed fragments are removed; a release with none changes nothing ----


def test_assembled_fragments_are_removed(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"246.fixed.md": "- x\n", "247.added.md": "- y\n"})

    ac.assemble(repo)

    assert sorted(p.name for p in (repo / "changelog.d").iterdir()) == []


def test_a_readme_in_the_fragment_dir_is_neither_assembled_nor_removed(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"README.md": "how to write one\n", "246.fixed.md": "- x\n"})

    ac.assemble(repo)

    assert (repo / "changelog.d" / "README.md").exists()
    assert "how to write one" not in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


def test_no_fragments_leaves_the_changelog_byte_for_byte(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"README.md": "how to write one\n"})
    before = (repo / "CHANGELOG.md").read_bytes()
    mtime = (repo / "CHANGELOG.md").stat().st_mtime_ns

    assert ac.assemble(repo) == []

    assert (repo / "CHANGELOG.md").read_bytes() == before
    assert (repo / "CHANGELOG.md").stat().st_mtime_ns == mtime


def test_no_fragment_dir_at_all_is_a_no_op(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG, encoding="utf-8")

    assert ac.assemble(tmp_path) == []
    assert (tmp_path / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG


def test_check_mode_validates_without_writing_or_removing(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"246.fixed.md": "- x\n"})

    consumed = ac.assemble(repo, check=True)

    assert [p.name for p in consumed] == ["246.fixed.md"]
    assert (repo / "changelog.d" / "246.fixed.md").exists()
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG


# --- a fragment that cannot be placed is refused by name -------------------


def test_a_misnamed_fragment_is_refused_by_name(tmp_path: Path) -> None:
    """Silently skipping it would lose the entry — the failure this replaces."""
    repo = _repo(tmp_path, **{"246.md": "- x\n"})

    with pytest.raises(SystemExit, match="246.md"):
        ac.assemble(repo)

    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG


def test_an_unknown_section_is_refused_by_name(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"246.improved.md": "- x\n"})

    with pytest.raises(SystemExit, match="improved"):
        ac.assemble(repo)


def test_an_empty_fragment_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"246.fixed.md": "\n\n"})

    with pytest.raises(SystemExit, match="246.fixed.md"):
        ac.assemble(repo)


def test_a_fragment_that_is_not_a_bullet_is_refused(tmp_path: Path) -> None:
    """The fragment is the entry as it will appear, not a note about it."""
    repo = _repo(tmp_path, **{"246.fixed.md": "Fixed the thing.\n"})

    with pytest.raises(SystemExit, match="246.fixed.md"):
        ac.assemble(repo)


def test_a_changelog_without_an_unreleased_heading_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, "# Changelog\n\n## [1.5.0]\n", **{"246.fixed.md": "- x\n"})

    with pytest.raises(SystemExit, match=r"\[Unreleased\]"):
        ac.assemble(repo)


def test_a_bad_fragment_fails_before_any_good_one_is_consumed(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"245.fixed.md": "- good\n", "246.md": "- bad\n"})

    with pytest.raises(SystemExit):
        ac.assemble(repo)

    assert (repo / "changelog.d" / "245.fixed.md").exists()
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == CHANGELOG


# --- the command line ------------------------------------------------------


def test_fail_if_pending_refuses_while_fragments_remain(tmp_path: Path, capsys) -> None:
    """The release guard: a tag pushed with fragments left must not publish."""
    repo = _repo(tmp_path, **{"246.fixed.md": "- x\n"})

    with pytest.raises(SystemExit) as exc:
        ac.main(["--fail-if-pending"], repo_root=repo)

    assert exc.value.code not in (0, None)
    assert "246.fixed.md" in capsys.readouterr().err
    assert (repo / "changelog.d" / "246.fixed.md").exists()


def test_fail_if_pending_passes_when_the_dir_is_empty(tmp_path: Path) -> None:
    repo = _repo(tmp_path, **{"README.md": "how\n"})

    assert ac.main(["--fail-if-pending"], repo_root=repo) == 0


def test_the_cli_assembles_and_reports(tmp_path: Path, capsys) -> None:
    repo = _repo(tmp_path, **{"246.fixed.md": "- x\n"})

    assert ac.main([], repo_root=repo) == 0

    assert "246.fixed.md" in capsys.readouterr().out
    assert "- x" in (repo / "CHANGELOG.md").read_text(encoding="utf-8")


# --- the real repo -----------------------------------------------------------


def test_the_repos_own_fragments_are_valid() -> None:
    """A misnamed fragment on a PR fails here, on every CI platform, before
    the release step ever sees it."""
    ac.assemble(REPO, check=True)


def test_the_repos_changelog_has_the_heading_the_tool_needs() -> None:
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "\n## [Unreleased]\n" in text
