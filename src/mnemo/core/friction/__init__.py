"""Friction — what contradicted the vault, recorded as structure.

A correction is the user telling the assistant it was wrong. Today each one
becomes a line of briefing prose and dies there: it marks no rule, feeds no
ranking, retires nothing. This package turns it into a record that names the
rule it contradicts.

Wave 1 is the ledger alone — the on-disk shape everything else consumes. The
candidate ranking, the contradiction pass, the retroactive backfill and the
``mnemo friction`` report join it here as they land.
"""
from __future__ import annotations

from mnemo.core.friction.ledger import (
    LEDGER_NAME,
    LINK_BASES,
    LINK_EXTRACTOR,
    LINK_EXTRACTOR_INJECTED,
    LINK_NONE,
    FrictionRecord,
    iter_records,
    ledger_path,
    record,
    record_id,
)

__all__ = [
    "LEDGER_NAME",
    "LINK_BASES",
    "LINK_EXTRACTOR",
    "LINK_EXTRACTOR_INJECTED",
    "LINK_NONE",
    "FrictionRecord",
    "iter_records",
    "ledger_path",
    "record",
    "record_id",
]
