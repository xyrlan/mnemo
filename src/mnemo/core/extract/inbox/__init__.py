"""Backwards-compat shim for ``mnemo.core.extract.inbox`` (v0.9 PR I).

The 717-line ``inbox.py`` was split into a package in v0.9 PR I. This
shim re-exports the pre-v0.9 public surface so existing importers keep
working without churn. New code should import from the concrete
sub-modules:

- :mod:`mnemo.core.extract.inbox.io`        — atomic_write + content_hash
- :mod:`mnemo.core.extract.inbox.paths`     — target / sibling / inbox / promoted
- :mod:`mnemo.core.extract.inbox.types`     — ExtractedPage, ApplyResult, errors
- :mod:`mnemo.core.extract.inbox.state_io`  — SCHEMA_VERSION + load/atomic-write state
- :mod:`mnemo.core.extract.inbox.rendering` — YAML scalars + page renderer
- :mod:`mnemo.core.extract.inbox.dedup`     — dedupe + drift / stem guardrails
- :mod:`mnemo.core.extract.inbox.apply`     — apply_pages (table-driven dispatcher)
- :mod:`mnemo.core.extract.inbox.branches`  — per-branch apply handlers

The deprecated underscore aliases ``_atomic_write`` / ``_file_hash``
remain re-exported from :mod:`io` with a ``DeprecationWarning``.
Removal is scheduled for v0.10.
"""
from mnemo.core.extract.inbox.apply import apply_pages
from mnemo.core.extract.inbox.dedup import (
    _bodies_similar,
    _stem_slug,
    dedupe_by_slug,
)
from mnemo.core.extract.inbox.io import (
    _atomic_write,
    _file_hash,
    atomic_write,
    content_hash,
)
from mnemo.core.extract.inbox.paths import (
    _is_auto_promoted_target,
    _sibling_path,
    _target_path_for_page,
)
from mnemo.core.extract.inbox.rendering import _extract_body, _render_page
from mnemo.core.extract.inbox.state_io import (
    SCHEMA_VERSION,
    StateSchemaError,
    atomic_write_state,
    load_state,
)
from mnemo.core.extract.inbox.types import (
    ApplyResult,
    ExtractedPage,
    ExtractionIOError,
)

__all__ = [
    # public surface (frozen by tests/unit/test_public_api_surface.py)
    "ApplyResult",
    "ExtractedPage",
    "ExtractionIOError",
    "StateSchemaError",
    "apply_pages",
    "atomic_write",
    "atomic_write_state",
    "content_hash",
    "dedupe_by_slug",
    "load_state",
    # private helpers imported by promote.py and tests
    "_atomic_write",
    "_bodies_similar",
    "_extract_body",
    "_file_hash",
    "_is_auto_promoted_target",
    "_render_page",
    "_sibling_path",
    "_stem_slug",
    "_target_path_for_page",
    # constant
    "SCHEMA_VERSION",
]
