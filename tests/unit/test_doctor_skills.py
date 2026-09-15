"""#327: doctor says whether the skills mnemo ships are installed and readable.

A skill reaches a session only when `<scope>/skills/<name>/SKILL.md` exists and
opens with its frontmatter (#233). Both failures are silent everywhere else.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo.cli.commands import doctor
from mnemo.cli.commands.doctor_checks import skills as sk
from mnemo.install.settings import SKILLS, inject_skills

MNEMO_HOOK = "/usr/local/bin/python3 -m mnemo.hooks.session_start"
OTHER_HOOK = "/opt/other/tool hook run"


@pytest.fixture(autouse=True)
def _no_ambient_plugin_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLAUDE_PLUGIN_ROOT", raising=False)


def _install(claude_dir: Path, command: str = MNEMO_HOOK) -> Path:
    """A settings.json with one hook in it, mnemo's or somebody else's."""
    claude_dir.mkdir(parents=True, exist_ok=True)
    path = claude_dir / "settings.json"
    path.write_text(
        json.dumps({"hooks": {"SessionStart": [
            {"hooks": [{"type": "command", "command": command}]}
        ]}}),
        encoding="utf-8",
    )
    return path


def test_silent_where_mnemo_is_not_installed(tmp_path, capsys):
    """A bare checkout — and CI — is not told to install something it never had."""
    assert sk._doctor_check_skills(
        tmp_path, claude_dir=tmp_path / "home" / ".claude", cwd=tmp_path / "repo",
    ) is True
    assert capsys.readouterr().out == ""


def test_another_tools_hook_is_not_a_mnemo_install(tmp_path):
    claude = tmp_path / "home" / ".claude"
    _install(claude, OTHER_HOOK)
    assert sk.check_skills(claude_dir=claude, cwd=tmp_path / "repo") is None


def test_installed_machine_missing_the_skill_warns_and_names_init(tmp_path, capsys):
    claude = tmp_path / "home" / ".claude"
    _install(claude)

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is False
    out = capsys.readouterr().out
    assert "mnemo-loop" in out
    assert "not installed where Claude Code looks" in out
    assert "`mnemo init`" in out


def test_silent_after_inject_skills(tmp_path, capsys):
    """The remedy it names must be what makes it go quiet."""
    claude = tmp_path / "home" / ".claude"
    _install(claude)
    inject_skills(claude / "skills")

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is True
    assert capsys.readouterr().out == ""


def test_frontmatter_below_a_tag_is_reported_as_unreadable(tmp_path, capsys):
    """The #233 failure: anything above the first `---` and nothing is indexed."""
    claude = tmp_path / "home" / ".claude"
    _install(claude)
    inject_skills(claude / "skills")
    target = claude / "skills" / "mnemo-loop" / "SKILL.md"
    target.write_text("<!-- mnemo:skill -->\n" + target.read_text(encoding="utf-8"),
                      encoding="utf-8")

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is False
    assert "does not open with its frontmatter" in capsys.readouterr().out


def test_a_skill_naming_another_skill_is_reported(tmp_path, capsys):
    claude = tmp_path / "home" / ".claude"
    _install(claude)
    inject_skills(claude / "skills")
    target = claude / "skills" / "mnemo-loop" / "SKILL.md"
    target.write_text("---\nname: something-else\n---\n\nBody.\n", encoding="utf-8")

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is False
    assert "different `name:`" in capsys.readouterr().out


def test_an_empty_skill_is_reported(tmp_path, capsys):
    claude = tmp_path / "home" / ".claude"
    _install(claude)
    inject_skills(claude / "skills")
    target = claude / "skills" / "mnemo-loop" / "SKILL.md"
    target.write_text("---\nname: mnemo-loop\n---\n\n", encoding="utf-8")

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is False
    assert "no body" in capsys.readouterr().out


def test_project_scope_gets_the_project_remedy(tmp_path, capsys):
    cwd = tmp_path / "repo"
    _install(cwd / ".claude")

    assert sk._doctor_check_skills(
        tmp_path, claude_dir=tmp_path / "home" / ".claude", cwd=cwd,
    ) is False
    out = capsys.readouterr().out
    assert "`mnemo init --project`" in out


def test_a_plugin_root_covers_a_machine_with_no_init_scope(tmp_path, monkeypatch, capsys):
    """A plugin install ships its skills at its own root; nothing writes ~/.claude."""
    plugin = tmp_path / "plugin"
    inject_skills(plugin / "skills")
    monkeypatch.setattr(sk, "_plugin_roots", lambda *a, **k: [plugin])

    assert sk._doctor_check_skills(
        tmp_path, claude_dir=tmp_path / "home" / ".claude", cwd=tmp_path / "repo",
    ) is True
    assert capsys.readouterr().out == ""


def test_one_good_copy_is_enough_but_a_broken_one_is_still_named(tmp_path, monkeypatch, capsys):
    claude = tmp_path / "home" / ".claude"
    _install(claude)
    inject_skills(claude / "skills")
    plugin = tmp_path / "plugin"
    inject_skills(plugin / "skills")
    (plugin / "skills" / "mnemo-loop" / "SKILL.md").write_text("no frontmatter\n", encoding="utf-8")
    monkeypatch.setattr(sk, "_plugin_roots", lambda *a, **k: [plugin])

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is False
    out = capsys.readouterr().out
    assert str(plugin) in out
    # The good copy is not reported as missing.
    assert "not installed where Claude Code looks" not in out


def test_every_shipped_skill_is_judged(tmp_path, capsys):
    claude = tmp_path / "home" / ".claude"
    _install(claude)

    findings = sk.check_skills(claude_dir=claude, cwd=tmp_path / "repo") or []
    for name in SKILLS:
        assert any(f"`{name}`" in line for line in findings), name


def test_a_diagnostic_never_breaks_doctor(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sk, "check_skills", lambda *a, **k: (_ for _ in ()).throw(RuntimeError()))
    assert sk._doctor_check_skills(tmp_path) is True


def test_registered_in_doctor():
    assert dict(doctor.DOCTOR_CHECKS)["skills"] is sk._doctor_check_skills


def test_a_users_own_broken_copy_is_not_blamed_on_init(tmp_path, capsys):
    """`inject_skills` leaves an untagged file alone, so `mnemo init` is not the fix."""
    claude = tmp_path / "home" / ".claude"
    _install(claude)
    target = claude / "skills" / "mnemo-loop" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("notes\n---\nname: mnemo-loop\n---\n\nMine.\n", encoding="utf-8")

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is False
    out = capsys.readouterr().out
    assert "does not open with its frontmatter" in out
    assert "not mnemo's" in out
    assert "`mnemo init` rewrites it" not in out


def test_mnemos_own_broken_copy_is_refreshed_by_init(tmp_path, capsys):
    claude = tmp_path / "home" / ".claude"
    _install(claude)
    inject_skills(claude / "skills")
    target = claude / "skills" / "mnemo-loop" / "SKILL.md"
    target.write_text("<!-- mnemo:skill -->\n" + target.read_text(encoding="utf-8"),
                      encoding="utf-8")

    assert sk._doctor_check_skills(tmp_path, claude_dir=claude, cwd=tmp_path / "repo") is False
    assert "`mnemo init` rewrites it" in capsys.readouterr().out
