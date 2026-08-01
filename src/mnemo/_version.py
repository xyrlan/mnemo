"""Single entry point for "what version of mnemo is this?".

Three call sites used to inline the same ``importlib.metadata`` lookup and
each fell back to the literal ``"unknown"``. That fallback is not hypothetical:
a frozen/standalone build has no installed distribution metadata to read, so
every one of them would report ``unknown``. Falling back to ``__version__``
instead keeps the answer truthful in that case.
"""
from __future__ import annotations

DIST_NAME = "mnemo-claude"


def resolve_version() -> str:
    """Return the installed distribution version, else the baked-in constant."""
    from importlib.metadata import PackageNotFoundError, version as _pkg_version

    from mnemo import __version__

    try:
        return _pkg_version(DIST_NAME)
    except PackageNotFoundError:
        return __version__
