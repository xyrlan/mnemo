"""Sharing a repo's rules between vaults (#245).

The on-disk contract lives in :mod:`mnemo.core.share.format`; ``publish``
writes a tree of portable pages into the repo and ``import`` stages them
into another vault's ``shared/_inbox/``. Everything here is re-exported so
the two commands import from one place.
"""
from __future__ import annotations

from mnemo.core.share.format import (
    SHARE_DIR,
    PortableRule,
    from_portable,
    is_imported_frontmatter,
    iter_portable,
    portable_hash,
    to_portable,
    to_vault_page,
    vault_id,
)

__all__ = [
    "SHARE_DIR",
    "PortableRule",
    "from_portable",
    "is_imported_frontmatter",
    "iter_portable",
    "portable_hash",
    "to_portable",
    "to_vault_page",
    "vault_id",
]
