"""Backwards-compat shim — preserves the pre-v0.9 public surface.

The original 849-line ``src/mnemo/core/rule_activation.py`` was split into
:mod:`.parsing`, :mod:`.globs`, :mod:`.matching`, :mod:`.index`, and
:mod:`.activity_log` in v0.9 PR G. This shim re-exports every name the
public-API surface test pins so existing importers keep working.

Three cleanups landed in the same PR:

* ``parse_enforce_block`` + ``parse_activates_on_block`` +
  ``_describe_*_error`` collapsed into a single :func:`parse_block` walker.
  The two thin wrappers remain (via the shim) for back-compat; the two
  describe helpers are deleted with no deprecation window.
* ``_is_universal`` promoted to public :func:`is_universal` (single in-tree
  consumer — ``reflex/index.py`` — updated atomically).
* ``build_index`` decomposed via a new private ``_build_rule_entry`` helper;
  the orchestrator dropped from 138 lines to <30.
"""
from __future__ import annotations

# Re-export the `is_consumer_visible` helper so existing tests that do
# `with patch("mnemo.core.rule_activation.is_consumer_visible", ...)` keep
# patching the right attribute. (``mnemo.core.filters`` is the actual home.)
from mnemo.core.filters import is_consumer_visible  # noqa: F401
from mnemo.core.rule_activation.activity_log import (  # noqa: F401
    log_denial,
    log_enrichment,
)
from mnemo.core.rule_activation.index import (  # noqa: F401
    INDEX_FILENAME,
    INDEX_VERSION,
    build_index,
    is_universal,
    load_index,
    projects_for_rule,
    write_index,
)
from mnemo.core.rule_activation.matching import (  # noqa: F401
    EnforceHit,
    EnrichHit,
    _glob_matches,
    _glob_to_regex,
    iter_enforce_rules_for_project,
    iter_enrich_rules_for_project,
    match_bash_enforce,
    match_path_enrich,
    normalize_bash_command,
)
from mnemo.core.rule_activation.parsing import (  # noqa: F401
    parse_activates_on_block,
    parse_block,
    parse_enforce_block,
)
