"""v0.3 --background CLI flag behavior."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout


def _run_cli(argv, monkeypatch, vault):
    from mnemo import cli

    monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
        "vaultRoot": str(vault),
        "extraction": {
            "model": "claude-haiku-4-5",
            "chunkSize": 10,
            "subprocessTimeout": 60,
        },
    })
    return cli.main(argv)


def test_background_flag_suppresses_stdout(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
        code = _run_cli(["extract", "--background"], monkeypatch, vault)

    assert code == 0
    assert stdout_buf.getvalue() == "", "background mode must not print to stdout"
    assert stderr_buf.getvalue() == "", "background mode must not print to stderr"


def test_background_writes_last_auto_run_on_success(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()

    _run_cli(["extract", "--background"], monkeypatch, vault)

    last_run = vault / ".mnemo" / "last-auto-run.json"
    assert last_run.exists()
    payload = json.loads(last_run.read_text())
    assert payload["exit_code"] == 0
    assert payload["mode"] == "background"


def test_background_skips_last_auto_run_on_lock_contention(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "bots").mkdir(parents=True)
    (vault / ".mnemo").mkdir()

    # Pre-create the lock dir to force contention
    lock_path = vault / ".mnemo" / "extract.lock"
    lock_path.mkdir()

    code = _run_cli(["extract", "--background"], monkeypatch, vault)

    assert code == 2
    last_run = vault / ".mnemo" / "last-auto-run.json"
    assert not last_run.exists(), "lock-contended runs must not overwrite the success record"
    errors_log = vault / ".errors.log"
    assert errors_log.exists()
    assert "extract.bg.lock" in errors_log.read_text()


def test_background_flag_hidden_from_help(capsys):
    from mnemo import cli

    try:
        cli.main(["extract", "--help"])
    except SystemExit:
        pass
    captured = capsys.readouterr()
    assert "--background" not in captured.out, "--background must be hidden from help"
