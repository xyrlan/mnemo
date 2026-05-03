from __future__ import annotations

import io
import json
from unittest.mock import patch

from mnemo.hooks import session_start


def test_session_start_bumps_cleanup_to_48h(tmp_path, monkeypatch):
    """Catchup window is 26h; cleanup must retain at least that long."""
    payload = json.dumps({"session_id": "sid-test", "cwd": str(tmp_path), "source": "startup"})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    monkeypatch.setattr("sys.stdout", io.StringIO())

    with patch("mnemo.core.session.cleanup_stale") as cleanup:
        session_start.main()

    cleanup.assert_called_once()
    kwargs = cleanup.call_args.kwargs
    args = cleanup.call_args.args
    arg_value = kwargs.get("max_age_seconds") if kwargs else (args[0] if args else None)
    assert arg_value == 48 * 3600
