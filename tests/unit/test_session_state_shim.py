"""Legacy mnemo.core.mcp.counter imports must keep working in v0.8."""
from __future__ import annotations

def test_counter_shim_reexports_increment_and_read_today():
    from mnemo.core.mcp import counter as legacy
    from mnemo.core.mcp import session_state as new

    # Same object identity — the shim truly re-exports.
    assert legacy.increment is new.increment
    assert legacy.read_today is new.read_today

def test_counter_shim_path_constant_unchanged():
    from mnemo.core.mcp import session_state as st

    # Filename on disk MUST stay "mcp-call-counter.json" for backwards compat
    # with statusline.py and server.py readers.
    assert st._FILENAME == "mcp-call-counter.json"
