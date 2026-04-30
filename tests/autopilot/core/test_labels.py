import subprocess
from unittest.mock import patch

from mnemo.autopilot.core.labels import (
    SELF_FIX_LABEL,
    SELF_FIX_LABEL_COLOR,
    SELF_FIX_LABEL_DESC,
    ensure_label_exists,
)


def test_constants_are_stable():
    assert SELF_FIX_LABEL == "mnemo:self-fix"
    assert SELF_FIX_LABEL_COLOR == "0E8A16"
    assert "auto" in SELF_FIX_LABEL_DESC.lower()


def test_ensure_label_exists_calls_gh():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 0
        ok = ensure_label_exists()
    assert ok is True
    cmd = run.call_args[0][0]
    assert cmd[:3] == ["gh", "label", "create"]
    assert SELF_FIX_LABEL in cmd
    assert "--force" in cmd


def test_ensure_label_swallows_failure():
    with patch("subprocess.run", side_effect=FileNotFoundError("gh missing")):
        ok = ensure_label_exists()
    assert ok is False


def test_ensure_label_swallows_nonzero():
    with patch("subprocess.run") as run:
        run.return_value.returncode = 1
        run.return_value.stderr = "oops"
        ok = ensure_label_exists()
    assert ok is False
