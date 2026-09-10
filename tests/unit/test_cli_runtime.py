"""Unit tests for mnemo.cli.runtime — process-level startup concerns."""
from __future__ import annotations

from mnemo.cli import runtime


def test_main_forces_utf8_streams(monkeypatch):
    """Windows gives a piped stdout the ANSI codepage (cp1252), and the
    first `→` in doctor's output raised UnicodeEncodeError. PYTHONUTF8 is
    ignored by the PyInstaller bootloader, so reconfigure at startup."""
    seen = []

    class _Stream:
        def reconfigure(self, **kw):
            seen.append(kw)

    monkeypatch.setattr(runtime.sys, "stdout", _Stream())
    monkeypatch.setattr(runtime.sys, "stderr", _Stream())
    runtime._force_utf8_streams()
    assert seen == [{"encoding": "utf-8", "errors": "replace"}] * 2


def test_force_utf8_streams_tolerates_streams_without_reconfigure(monkeypatch):
    monkeypatch.setattr(runtime.sys, "stdout", object())
    monkeypatch.setattr(runtime.sys, "stderr", object())
    runtime._force_utf8_streams()  # must not raise
