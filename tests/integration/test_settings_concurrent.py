"""Critical test from spec § 10.3: 5 concurrent inject_hooks must produce a valid settings.json."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from mnemo.install import settings as inj


def test_concurrent_inject_hooks(tmp_home: Path):
    settings_path = tmp_home / ".claude" / "settings.json"
    errors: list[Exception] = []

    def worker():
        try:
            inj.inject_hooks(settings_path)
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"unexpected errors: {errors}"
    data = json.loads(settings_path.read_text())
    hooks = data["hooks"]
    # v0.3.1: only SessionStart and SessionEnd are registered; the write-only
    # UserPromptSubmit and PostToolUse hooks were removed.
    for event in ("SessionStart", "SessionEnd"):
        assert event in hooks
        mnemo_count = sum(
            1
            for entry in hooks[event]
            for h in entry.get("hooks", [])
            if "mnemo" in h.get("command", "")
        )
        assert mnemo_count == 1, f"{event} has {mnemo_count} mnemo entries (expected exactly 1)"
