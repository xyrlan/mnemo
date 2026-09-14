"""`mnemo init` writes the packaged skills where Claude Code looks (#233).

Mirrors test_inject_slash_commands: mnemo-tagged files are mnemo's to rewrite
and remove; anything else under the same name belongs to the user.
"""
from pathlib import Path

import pytest

from mnemo.install import settings as inj


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    # A subdirectory: the autouse home fixture already populates tmp_path.
    return tmp_path / "skills"


def test_inject_skills_writes_every_packaged_skill(skills_dir: Path):
    inj.inject_skills(skills_dir)

    names = {p.name for p in skills_dir.iterdir()}
    assert names == set(inj.SKILLS)
    for name in inj.SKILLS:
        assert (skills_dir / name / "SKILL.md").is_file()


def test_injected_skill_keeps_the_frontmatter_first_and_the_tag_under_it(skills_dir: Path):
    """A tag above the `---` makes Claude Code read the whole file as body."""
    inj.inject_skills(skills_dir)
    body = (skills_dir / "decomposing-for-dispatch" / "SKILL.md").read_text(encoding="utf-8")

    assert body.startswith("---\nname: decomposing-for-dispatch\n")
    assert "\n---\n" + inj.SKILL_TAG + "\n" in body
    # The tag is the only difference from the packaged file.
    assert body.replace(inj.SKILL_TAG + "\n", "", 1) == inj.read_skill("decomposing-for-dispatch")


def test_inject_skills_is_idempotent_and_refreshes_its_own_file(skills_dir: Path):
    inj.inject_skills(skills_dir)
    target = skills_dir / "decomposing-for-dispatch" / "SKILL.md"
    target.write_text(inj.SKILL_TAG + "\nan older version\n")

    inj.inject_skills(skills_dir)

    assert target.read_text(encoding="utf-8") == inj.render_skill("decomposing-for-dispatch")
    assert {p.name for p in skills_dir.iterdir()} == set(inj.SKILLS)


def test_inject_skills_leaves_a_users_skill_of_the_same_name_alone(skills_dir: Path):
    theirs = skills_dir / "decomposing-for-dispatch" / "SKILL.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("---\nname: decomposing-for-dispatch\n---\n\nMine.\n")

    inj.inject_skills(skills_dir)

    assert theirs.read_text().endswith("Mine.\n")


def test_uninject_skills_removes_only_mnemo_tagged_files(skills_dir: Path):
    theirs = skills_dir / "their-skill" / "SKILL.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("---\nname: their-skill\n---\n\nTheirs.\n")
    inj.inject_skills(skills_dir)

    inj.uninject_skills(skills_dir)

    assert {p.name for p in skills_dir.iterdir()} == {"their-skill"}
    assert theirs.read_text().endswith("Theirs.\n")


def test_uninject_skills_keeps_a_directory_the_user_added_to(skills_dir: Path):
    """A supporting file next to SKILL.md is theirs; only the skill goes."""
    inj.inject_skills(skills_dir)
    extra = skills_dir / "decomposing-for-dispatch" / "notes.md"
    extra.write_text("keep me\n")

    inj.uninject_skills(skills_dir)

    assert not (skills_dir / "decomposing-for-dispatch" / "SKILL.md").exists()
    assert extra.read_text() == "keep me\n"


def test_uninject_skills_spares_an_untagged_skill_under_mnemos_name(skills_dir: Path):
    theirs = skills_dir / "decomposing-for-dispatch" / "SKILL.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("---\nname: decomposing-for-dispatch\n---\n\nMine.\n")

    inj.uninject_skills(skills_dir)

    assert theirs.exists()


def test_uninject_skills_handles_missing_dir(skills_dir: Path):
    inj.uninject_skills(skills_dir / "nonexistent")


def test_tag_lands_first_when_there_is_no_frontmatter():
    assert inj._tag_after_frontmatter("plain\n", "<!-- t -->") == "<!-- t -->\nplain\n"


def test_read_skill_is_the_file_on_disk():
    from pathlib import Path as _P
    import mnemo.skills as pkg

    on_disk = _P(pkg.__file__).parent / "decomposing-for-dispatch" / "SKILL.md"
    assert inj.read_skill("decomposing-for-dispatch") == on_disk.read_text(encoding="utf-8")
