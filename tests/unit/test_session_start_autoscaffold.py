"""SessionStart completes a vault that `mnemo init` never scaffolded.

Under the plugin there is no install step: hooks and the MCP server are
declared by the plugin, so nothing ever runs `mnemo init`. The hooks used to
create only what they touched — bots/, briefings/, .mnemo/ — leaving the vault
without HOME.md, the config file, or shared/, which is where extracted rules
live. Scaffolding on demand is what makes a zero-command install real.
"""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.hooks import session_start


def _run(monkeypatch, tmp_path: Path, cwd: Path) -> None:
    payload = json.dumps({"session_id": "s1", "cwd": str(cwd), "source": "startup"})

    class _Stdin:
        @staticmethod
        def read() -> str:
            return payload

    monkeypatch.setattr("sys.stdin", _Stdin())
    monkeypatch.setattr("json.load", lambda _f: json.loads(payload))
    session_start.main()


def test_scaffolds_a_vault_that_was_never_initialised(monkeypatch, tmp_path: Path):
    vault = tmp_path / "vault"
    monkeypatch.setattr(
        "mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False
    )
    cwd = tmp_path / "proj"
    cwd.mkdir()

    _run(monkeypatch, tmp_path, cwd)

    assert (vault / "HOME.md").exists(), "dashboard missing"
    assert (vault / "shared").is_dir(), "rules directory missing"


def test_does_not_clobber_an_existing_dashboard(monkeypatch, tmp_path: Path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "HOME.md").write_text("# my notes, hands off\n")
    monkeypatch.setattr(
        "mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False
    )
    cwd = tmp_path / "proj"
    cwd.mkdir()

    _run(monkeypatch, tmp_path, cwd)

    assert (vault / "HOME.md").read_text() == "# my notes, hands off\n"


def test_a_scaffold_failure_does_not_break_the_session(monkeypatch, tmp_path: Path):
    """Every hook path is fail-open; this one is no exception."""
    vault = tmp_path / "vault"
    monkeypatch.setattr(
        "mnemo.core.paths.vault_root", lambda _cfg=None: vault, raising=False
    )

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("mnemo.install.scaffold.scaffold_vault", boom)
    cwd = tmp_path / "proj"
    cwd.mkdir()

    _run(monkeypatch, tmp_path, cwd)  # must not raise
