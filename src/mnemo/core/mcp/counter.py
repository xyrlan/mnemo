"""Backwards-compat shim.

The module was split into session_state.py in v0.8. This file re-exports
the pre-v0.8 public surface (``increment``, ``read_today``, ``_FILENAME``,
``_path``) so existing imports from ``mnemo.core.mcp.counter`` keep working
without churn. To be removed in v0.9.
"""
from mnemo.core.mcp.session_state import (  # noqa: F401
    _FILENAME,
    _path,
    increment,
    read_today,
)
