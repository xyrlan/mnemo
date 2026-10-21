"""Doctor check: publishing is not checked without private-names.tsv (#746).

The row is visible, never a warning — a missing list is legitimate — and the
deny hook's fail-open behaviour is the same whether the file is missing,
comment-only or real.
"""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from mnemo.cli.commands import doctor
from mnemo.cli.commands.doctor_checks.private_names import (
    _doctor_check_private_names as check,
)

NAME = "Zorblax-Engine"
ALIAS = "repo-q"
_ROW = "publishing commands are not checked"


def _tsv(vault: Path, text: str) -> Path:
    path = vault / ".mnemo" / "private-names.tsv"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_the_row_is_registered() -> None:
    assert ("private_names", check) in doctor.DOCTOR_CHECKS


def test_missing_file_shows_the_row_with_its_path(tmp_path: Path, capsys) -> None:
    vault = tmp_path / "vault"

    assert check(vault) is True

    out = capsys.readouterr().out
    assert _ROW in out
    assert str(vault / ".mnemo" / "private-names.tsv") in out
    assert "name<TAB>alias" in out


@pytest.mark.parametrize("text", [
    f"{NAME}\t{ALIAS}\n",
    "# nothing private on this machine\n",
    "",
    "no tab here\n",  # broken: check() reports it, not this row
])
def test_existing_file_is_silent(tmp_path: Path, capsys, text: str) -> None:
    vault = tmp_path / "vault"
    _tsv(vault, text)

    assert check(vault) is True
    assert capsys.readouterr().out == ""


def _hook(monkeypatch, vault: Path, command: str, cwd: Path) -> str:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "enforcement": {"enabled": False},
        "enrichment": {"enabled": False},
    })
    buf = io.StringIO()
    monkeypatch.setattr("sys.stdout", buf)
    from mnemo.hooks.pre_tool_use import main

    assert main() == 0
    return buf.getvalue()


_COMMAND = f"gh issue create --repo me/public --title t --body 'see {NAME}'"


@pytest.mark.parametrize("text", [None, "# nothing private\n"])
def test_hook_lets_publishing_through_without_names(
    tmp_path: Path, monkeypatch, text,
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    if text is not None:
        _tsv(vault, text)

    assert _hook(monkeypatch, vault, _COMMAND, tmp_path) == ""


def test_hook_still_denies_with_a_list(tmp_path: Path, monkeypatch) -> None:
    vault = tmp_path / "vault"
    _tsv(vault, f"{NAME}\t{ALIAS}\n")

    out = _hook(monkeypatch, vault, _COMMAND, tmp_path)

    hook = json.loads(out)["hookSpecificOutput"]
    assert hook["permissionDecision"] == "deny"
    assert ALIAS in hook["permissionDecisionReason"]
