"""#120: doctor's stripped-enforce advisory must not list reclassify originals."""
from pathlib import Path

from mnemo.cli.commands.doctor_checks import rules as doctor_rules

PAGE = "---\nname: x\ntype: feedback\npromoted_without_enforce: true\n---\nbody\n"


def _write(p: Path) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(PAGE, encoding="utf-8")
    return p


def test_stripped_enforce_ignores_archive(tmp_path, capsys):
    _write(tmp_path / "shared" / "feedback" / "live.md")
    _write(tmp_path / "shared" / "_archive" / "reclassify-r" / "originals" / "feedback" / "old.md")
    assert doctor_rules._doctor_check_stripped_enforce(tmp_path) is True
    out = capsys.readouterr().out
    assert "1 auto-promoted rule(s)" in out
    assert "shared/feedback/live.md" in out.replace("\\", "/")
    assert "_archive" not in out
