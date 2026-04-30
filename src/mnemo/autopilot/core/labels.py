"""GitHub label constants for autopilot-opened PRs."""
from __future__ import annotations

import subprocess

SELF_FIX_LABEL = "mnemo:self-fix"
SELF_FIX_LABEL_COLOR = "0E8A16"
SELF_FIX_LABEL_DESC = "Auto-opened PR by mnemo autopilot"


def ensure_label_exists() -> bool:
    """Idempotent ``gh label create --force``. Returns False when ``gh`` is
    unavailable or the call fails — autopilot still works in record-only mode."""
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
