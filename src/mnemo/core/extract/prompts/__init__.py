"""Backwards-compat shim for ``mnemo.core.extract.prompts`` (v0.9).

The 529-line ``prompts.py`` was split into a package in v0.9 PR F2 (Wave 3
of the v0.9 refactor roadmap). This shim re-exports the pre-v0.9 public
surface so existing importers — both in-tree and downstream — keep
working without churn. New code should import from concrete sub-modules
(``prompts.render``, ``prompts.encoding``, ``prompts.templates.*``).

NOTE: ``build_briefing_prompt``'s signature changed in PR F2 — it now
accepts a pre-flattened ``transcript: str`` rather than
``events: list[dict]``. Use
:func:`mnemo.core.transcript.flatten_transcript_events` to produce the
transcript string from raw events.
"""
from mnemo.core.extract.prompts.encoding import chunks_for  # noqa: F401
from mnemo.core.extract.prompts.render import (  # noqa: F401
    build_briefing_prompt,
    build_consolidation_prompt,
    build_feedback_prompt,
    build_reference_prompt,
    build_user_prompt,
)
from mnemo.core.extract.prompts.templates.briefing import (  # noqa: F401
    BRIEFING_SYSTEM_PROMPT,
)
# The three underscore-private few-shot constants are re-exported because
# PR F1's schema regression test (tests/unit/test_prompts_few_shot_schema.py)
# does ``prompts._FEW_SHOT_*`` attribute access.
from mnemo.core.extract.prompts.templates.few_shot_feedback import (  # noqa: F401
    _FEW_SHOT_FEEDBACK,
)
from mnemo.core.extract.prompts.templates.few_shot_simple import (  # noqa: F401
    _FEW_SHOT_REFERENCE,
    _FEW_SHOT_USER,
)
from mnemo.core.extract.prompts.templates.system_feedback import (  # noqa: F401
    FEEDBACK_SYSTEM_PROMPT,
)
from mnemo.core.extract.prompts.templates.system_simple import (  # noqa: F401
    REFERENCE_SYSTEM_PROMPT,
    USER_SYSTEM_PROMPT,
)
