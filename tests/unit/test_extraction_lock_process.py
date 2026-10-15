"""Cross-process extraction lock regressions."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_HOLD_EXTRACTION = """
import sys, time
from pathlib import Path
import mnemo.core.extract as extract
from mnemo.core.extract import run_extraction

ready = Path(sys.argv[2])
delay = float(sys.argv[3])
def hold(*args, **kwargs):
    ready.write_text("ready", encoding="utf-8")
    time.sleep(delay)

extract._run_extraction_body = hold
run_extraction({"vaultRoot": sys.argv[1]})
"""

_TRY_EXTRACTION = """
import sys
import mnemo.core.extract as extract
from mnemo.core.extract import ExtractionIOError, run_extraction

extract._run_extraction_body = lambda *args, **kwargs: (_ for _ in ()).throw(
    RuntimeError("contender unexpectedly acquired the lock")
)
try:
    run_extraction({"vaultRoot": sys.argv[1]})
except ExtractionIOError:
    print("LOCKED", flush=True)
    raise SystemExit(23)
"""


def _start_python(tmp_path: Path, name: str, code: str, *args: object) -> subprocess.Popen:
    script = tmp_path / f"{name}.py"
    script.write_text(code, encoding="utf-8")
    return subprocess.Popen(
        [sys.executable, str(script), *(str(arg) for arg in args)],
        cwd=str(Path(__file__).resolve().parents[2]),
        env=_python_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _python_env() -> dict:
    source = Path(__file__).resolve().parents[2] / "src"
    pythonpath = os.environ.get("PYTHONPATH", "")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(source), pythonpath) if part
    )
    return env


def _wait_ready(process: subprocess.Popen, ready: Path) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if ready.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            pytest.fail(f"holder exited before acquiring the lock: {stdout}\n{stderr}")
        time.sleep(0.02)
    process.kill()
    stdout, stderr = process.communicate(timeout=5)
    pytest.fail(f"holder did not acquire the lock: {stdout}\n{stderr}")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    process.communicate(timeout=10)


def test_run_extraction_refuses_live_holder_past_stale_threshold(
    tmp_vault: Path, tmp_path: Path
) -> None:
    lock = tmp_vault / ".mnemo" / "extract.lock"
    ready = tmp_path / "holder-ready"
    holder = _start_python(tmp_path, "holder", _HOLD_EXTRACTION, tmp_vault, ready, 10)
    try:
        _wait_ready(holder, ready)
        old = time.time() - 600
        os.utime(lock, (old, old))
        assert holder.poll() is None

        contender = subprocess.run(
            [sys.executable, "-c", _TRY_EXTRACTION, str(tmp_vault)],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=_python_env(),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )

        assert contender.returncode == 23, contender.stdout + contender.stderr
        assert "LOCKED" in contender.stdout
        assert holder.poll() is None
        assert lock.is_dir()
    finally:
        _stop(holder)


def test_run_extraction_reclaims_lock_after_holder_process_dies(
    tmp_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mnemo.core import extract

    lock = tmp_vault / ".mnemo" / "extract.lock"
    ready = tmp_path / "holder-ready"
    holder = _start_python(tmp_path, "dead-holder", _HOLD_EXTRACTION, tmp_vault, ready, 30)
    _wait_ready(holder, ready)
    try:
        assert lock.is_dir()
        holder.terminate()
        holder.communicate(timeout=10)

        body_calls = []
        monkeypatch.setattr(
            extract, "_run_extraction_body", lambda *args, **kwargs: body_calls.append(True)
        )
        extract.run_extraction({"vaultRoot": str(tmp_vault)})

        assert body_calls == [True]
        assert not lock.exists()
    finally:
        _stop(holder)


def test_session_end_doctor_and_status_agree_for_live_lock(
    tmp_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from mnemo.cli.commands.doctor_checks.misc import _doctor_check_auto_brain
    from mnemo.cli.commands.status import _print_auto_brain_status
    from mnemo.hooks import session_end

    lock = tmp_vault / ".mnemo" / "extract.lock"
    ready = tmp_path / "holder-ready"
    holder = _start_python(tmp_path, "live-status-holder", _HOLD_EXTRACTION, tmp_vault, ready, 30)
    _wait_ready(holder, ready)
    try:
        old = time.time() - 900
        os.utime(lock, (old, old))
        monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
            "vaultRoot": str(tmp_vault), "extraction": {"auto": {"enabled": False}},
        })

        assert session_end._lock_held(lock) is True
        assert _doctor_check_auto_brain(tmp_vault) is True
        assert "stale" not in capsys.readouterr().out.lower()

        _print_auto_brain_status(tmp_vault)
        output = capsys.readouterr().out.lower()
        assert "running now" in output
        assert "stale" not in output
    finally:
        _stop(holder)


def test_session_end_doctor_and_status_agree_for_dead_lock(
    tmp_vault: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from mnemo.cli.commands.doctor_checks.misc import _doctor_check_auto_brain
    from mnemo.cli.commands.status import _print_auto_brain_status
    from mnemo.hooks import session_end

    lock = tmp_vault / ".mnemo" / "extract.lock"
    ready = tmp_path / "holder-ready"
    holder = _start_python(tmp_path, "dead-status-holder", _HOLD_EXTRACTION, tmp_vault, ready, 30)
    _wait_ready(holder, ready)
    try:
        holder.terminate()
        holder.communicate(timeout=10)
        monkeypatch.setattr("mnemo.core.config.load_config", lambda: {
            "vaultRoot": str(tmp_vault), "extraction": {"auto": {"enabled": False}},
        })

        assert session_end._lock_held(lock) is False
        assert _doctor_check_auto_brain(tmp_vault) is False
        doctor_output = capsys.readouterr().out.lower()
        assert "stale" in doctor_output
        assert "extract.lock" in doctor_output

        _print_auto_brain_status(tmp_vault)
        status_output = capsys.readouterr().out.lower()
        assert "running now" not in status_output
        assert "stale extract.lock present" in status_output
    finally:
        _stop(holder)
