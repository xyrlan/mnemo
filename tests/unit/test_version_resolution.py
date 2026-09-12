"""``resolve_version`` reports the code that is running, not the metadata.

The two diverge under an editable install, where ``dist-info`` is written once
at ``pip install -e`` time and never refreshed. That is the maintainer's own
setup, so the stale string is most likely to mislead exactly when someone is
debugging their own vault.
"""
from __future__ import annotations

import mnemo
from mnemo._version import DIST_NAME, resolve_version


def test_reports_the_imported_packages_version():
    assert resolve_version() == mnemo.__version__


def test_ignores_stale_distribution_metadata(monkeypatch):
    """An editable install pinned at 1.3.0 must not mask 1.4.0 source."""
    import importlib.metadata as md

    def _stale(name: str) -> str:
        return "0.0.1-stale"

    monkeypatch.setattr(md, "version", _stale)
    assert resolve_version() == mnemo.__version__
    assert resolve_version() != "0.0.1-stale"


def test_survives_a_missing_distribution(monkeypatch):
    """A frozen/standalone build has no dist-info at all; the constant is
    always there, so there is nothing left to fall back from."""
    import importlib.metadata as md

    def _boom(name: str) -> str:
        raise md.PackageNotFoundError(name)

    monkeypatch.setattr(md, "version", _boom)
    assert resolve_version() == mnemo.__version__


def test_version_is_a_real_dotted_string():
    v = resolve_version()
    assert v and v != "unknown"
    assert v[0].isdigit()


def test_dist_name_is_the_pypi_spelling():
    """Kept for diagnostics and tools/sync_npm_version.py."""
    assert DIST_NAME == "mnemo-claude"
