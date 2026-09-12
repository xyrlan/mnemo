"""Single entry point for "what version of mnemo is this?".

Three call sites used to inline the same ``importlib.metadata`` lookup and
each fell back to the literal ``"unknown"``. That fallback is not hypothetical:
a frozen/standalone build has no installed distribution metadata to read, so
every one of them would report ``unknown``. Falling back to ``__version__``
instead keeps the answer truthful in that case.

The lookup used to prefer ``importlib.metadata`` and fall back to the constant.
That reports the *installed distribution*, which is not the same thing as the
code in memory: under an editable install the ``dist-info`` is written once, at
``pip install -e`` time, and never refreshed — so every release bump after it
leaves the metadata stale while the import path serves the new source.

Measured on the maintainer's own machine: ``mnemo --version`` said ``1.3.0``
from ``__editable__.mnemo_claude-1.3.0.pth`` while the working tree it pointed
at was ``1.4.0``. That is the one setup where the two can disagree, and it is
exactly the setup someone debugging their own vault is in — the stale string
was read as "a stale binary is running" and sent a #184 diagnosis the wrong way.

``__version__`` is a literal in the module that was actually imported, so it
answers "which code is running", is always present (no fallback needed, frozen
builds included), and agrees with the metadata in every non-editable install.
"""
from __future__ import annotations

#: The PyPI distribution name. No longer consulted by :func:`resolve_version`
#: (see above), but kept as the canonical spelling for anything that needs to
#: name the distribution rather than the package — ``pip install`` hints in
#: diagnostics, and ``tools/sync_npm_version.py``, which carries its own copy.
DIST_NAME = "mnemo-claude"


def resolve_version() -> str:
    """Return the version of the ``mnemo`` package that is actually imported."""
    from mnemo import __version__

    return __version__
