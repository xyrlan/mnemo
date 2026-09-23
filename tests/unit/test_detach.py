"""#452: detached workers get a hidden console, never none at all."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from mnemo import _detach

SRC = Path(_detach.__file__).resolve().parent


def test_windows_uses_create_no_window_and_a_new_group(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    kwargs = _detach.detach_kwargs()

    assert kwargs == {"creationflags": 0x08000000 | 0x00000200}


def test_windows_never_sets_detached_process(monkeypatch):
    # Windows ignores CREATE_NO_WINDOW when DETACHED_PROCESS is also set,
    # and every console child of a console-less worker opens a visible window.
    monkeypatch.setattr(sys, "platform", "win32")

    assert not _detach.detach_kwargs()["creationflags"] & 0x00000008


def test_posix_starts_a_new_session(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert _detach.detach_kwargs() == {"start_new_session": True}


def test_no_detached_process_left_in_src():
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "_detach.py"
        and re.search(r"DETACHED_PROCESS|0x0+8\b", p.read_text(encoding="utf-8"))
    ]

    assert offenders == []
