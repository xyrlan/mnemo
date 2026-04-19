"""System prompts for the simpler USER and REFERENCE consolidation calls.

These two prompts share the short ``_TAGS_GUIDANCE_SHORT`` /
``_ALIASES_GUIDANCE_SHORT`` fragments defined alongside the heavier
feedback prompt — imported from ``system_feedback`` rather than
duplicated here so a single edit propagates to all three.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations

from mnemo.core.extract.prompts.templates.system_feedback import (
    _ALIASES_GUIDANCE_SHORT,
    _TAGS_GUIDANCE_SHORT,
)


USER_SYSTEM_PROMPT = (
    "You are consolidating user-profile memories across multiple Claude Code "
    "agents into canonical pages. Group files that describe the SAME trait or "
    "role. Produce one page per trait cluster. Preserve the 'Why' / 'How to "
    "apply' structure." + _TAGS_GUIDANCE_SHORT + _ALIASES_GUIDANCE_SHORT +
    " Output MUST be valid JSON matching the requested schema."
)

REFERENCE_SYSTEM_PROMPT = (
    "You are consolidating reference memories (pointers to external systems "
    "like Linear, Grafana, Notion) across agents into canonical pages. Group "
    "files that point to the SAME external resource. Produce one page per "
    "resource cluster." + _TAGS_GUIDANCE_SHORT + _ALIASES_GUIDANCE_SHORT +
    " Output MUST be valid JSON matching the requested schema."
)
