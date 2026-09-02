"""GitHub label constants for autopilot-opened PRs."""
from __future__ import annotations

import subprocess

from mnemo.autopilot.core import network

SELF_FIX_LABEL = "mnemo:self-fix"
SELF_FIX_LABEL_COLOR = "0E8A16"
SELF_FIX_LABEL_DESC = "Auto-opened PR by mnemo autopilot"


def ensure_label_exists() -> bool:
    """Idempotent ``gh label create --force``. Returns False when ``gh`` is
    unavailable, the call fails, or ``autopilot.network.enabled`` is off —
    autopilot still works in record-only mode."""
    if not network.enabled():
        print(network.OFF_MESSAGE)
        return False
    try:
        result = subprocess.run(
            [
                "gh", "label", "create", SELF_FIX_LABEL,
                "--color", SELF_FIX_LABEL_COLOR,
                "--description", SELF_FIX_LABEL_DESC,
                "--force",
            ],
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0
