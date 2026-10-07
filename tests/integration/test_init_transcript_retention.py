"""``mnemo init`` keeps the transcripts mnemo learns from (#596)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mnemo import cli
from mnemo.install import transcript_retention as tr

# Keys a user's settings.json really carries: nested, unicode, a list.
OTHER_KEYS = {
    "model": "opus",
    "env": {"FOO": "bär"},
    "permissions": {"allow": ["Bash(git status)"], "deny": []},
    "theme": "dark",
}


def _settings(home: Path) -> Path:
    return home / ".claude" / "settings.json"


def _seed(home: Path, data: dict) -> Path:
    path = _settings(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def _init(home: Path, *extra: str) -> int:
    return cli.main(["init", "--yes", "--vault-root", str(home / "vault"),
                     "--no-mirror", *extra])


def _read(home: Path) -> dict:
    return json.loads(_settings(home).read_text(encoding="utf-8"))


def test_unset_is_raised_to_the_floor_and_said(tmp_home: Path, capsys) -> None:
    _seed(tmp_home, OTHER_KEYS)

    assert _init(tmp_home) == 0

    data = _read(tmp_home)
    assert data[tr.SETTING] == tr.DEFAULT_FLOOR_DAYS == 365
    for key, value in OTHER_KEYS.items():
        assert data[key] == value
    out = capsys.readouterr().out
    assert "Raised cleanupPeriodDays" in out and "was unset, so 30 days" in out
    assert "--no-transcript-retention" in out


def test_a_fresh_home_gets_the_floor(tmp_home: Path) -> None:
    assert _init(tmp_home, "--quiet") == 0
    assert _read(tmp_home)[tr.SETTING] == 365


def test_a_low_value_is_raised(tmp_home: Path, capsys) -> None:
    _seed(tmp_home, {**OTHER_KEYS, tr.SETTING: 10})

    assert _init(tmp_home) == 0

    assert _read(tmp_home)[tr.SETTING] == 365
    assert "(was 10 days)" in capsys.readouterr().out


def test_a_higher_value_is_never_lowered(tmp_home: Path, capsys) -> None:
    _seed(tmp_home, {**OTHER_KEYS, tr.SETTING: 3650})

    assert _init(tmp_home) == 0

    assert _read(tmp_home)[tr.SETTING] == 3650
    assert "Raised cleanupPeriodDays" not in capsys.readouterr().out


def test_rerun_is_idempotent(tmp_home: Path, capsys) -> None:
    _seed(tmp_home, OTHER_KEYS)
    assert _init(tmp_home, "--quiet") == 0
    first = _settings(tmp_home).read_bytes()

    assert _init(tmp_home) == 0

    assert _settings(tmp_home).read_bytes() == first
    assert "Raised cleanupPeriodDays" not in capsys.readouterr().out


def test_opt_out_flag_leaves_the_setting_and_is_remembered(tmp_home: Path, capsys) -> None:
    _seed(tmp_home, {**OTHER_KEYS, tr.SETTING: 10})

    assert _init(tmp_home, "--no-transcript-retention") == 0
    assert _read(tmp_home)[tr.SETTING] == 10
    assert "Leaving Claude Code's cleanupPeriodDays alone" in capsys.readouterr().out

    # A later plain re-run reads the opt-out from mnemo's config.
    assert _init(tmp_home) == 0
    assert _read(tmp_home)[tr.SETTING] == 10
    assert "install.keepTranscriptsDays is 0" in capsys.readouterr().out


def test_hooks_only_leaves_the_setting_alone(tmp_home: Path) -> None:
    assert _init(tmp_home, "--quiet", "--no-transcript-retention") == 0
    _seed(tmp_home, {**_read(tmp_home), tr.SETTING: 10})
    assert cli.main(["init", "--hooks-only", "--yes", "--quiet"]) == 0
    assert _read(tmp_home)[tr.SETTING] == 10


def test_project_install_never_writes_home_but_says_what_to_add(
        tmp_home: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    proj = tmp_home / "proj"
    proj.mkdir()
    monkeypatch.chdir(proj)

    assert cli.main(["init", "--project", "--yes", "--no-mirror"]) == 0

    assert not _settings(tmp_home).exists()
    assert tr.SETTING not in json.loads((proj / ".claude" / "settings.json").read_text(encoding="utf-8"))
    out = capsys.readouterr().out
    assert 'add "cleanupPeriodDays": 365 to ~/.claude/settings.json' in out


# --- ensure_floor: the merge itself ---------------------------------------

def test_ensure_floor_changes_that_key_and_no_other_byte(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(OTHER_KEYS, indent=2), encoding="utf-8")

    outcome = tr.ensure_floor(path, 365)

    assert outcome == tr.Outcome("raised", None, 365)
    assert path.read_bytes() == json.dumps({**OTHER_KEYS, tr.SETTING: 365}, indent=2).encode("utf-8")
    assert len(list(tmp_path.glob("settings.json.bak.*"))) == 1


def test_ensure_floor_does_not_write_a_file_it_keeps(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    raw = '{"cleanupPeriodDays": 3650,   "theme": "dark"}'  # not mnemo's formatting
    path.write_text(raw, encoding="utf-8")

    assert tr.ensure_floor(path, 365) == tr.Outcome("kept", 3650, 3650)
    assert path.read_text(encoding="utf-8") == raw
    assert not list(tmp_path.glob("settings.json.bak.*"))


@pytest.mark.parametrize("value", [0, "30", True, None])
def test_ensure_floor_leaves_a_value_claude_code_rejects(tmp_path: Path, value) -> None:
    # Claude Code skips its sweep when the key is set but invalid: nothing to fix.
    path = tmp_path / "settings.json"
    raw = json.dumps({tr.SETTING: value})
    path.write_text(raw, encoding="utf-8")

    assert tr.ensure_floor(path, 365).action == "invalid"
    assert path.read_text(encoding="utf-8") == raw
