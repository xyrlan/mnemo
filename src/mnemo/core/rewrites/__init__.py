"""Staged `.proposed.md` rewrite reconciliation (#159).

``shared/_inbox/`` accumulates ``.proposed.md`` rewrites of live rules. They are
excluded from every consumer surface by ``filters.is_consumer_visible``, so recall
serves the un-updated live rule instead — and the extractor re-proposes the same
rewrite on every run because accept was a manual ``mv`` that never reconciled
``entry.written_hash``.

This package classifies each rewrite, merges the safe ones, and advances the
ledger so an accepted rule stops re-proposing.
"""
from __future__ import annotations
