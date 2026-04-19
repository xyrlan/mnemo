"""Doctor-check sub-package — one module per concern.

Each public ``_doctor_check_*`` function is registered in
``cli.commands.doctor.DOCTOR_CHECKS`` (an OCP-compliant
``list[tuple[name, callable]]``); adding a new check is a new
row, not an edit to ``cmd_doctor``.
"""
from __future__ import annotations

from mnemo.cli.commands.doctor_checks.activation import (  # noqa: F401
    _doctor_check_activation,
    _doctor_check_activation_fidelity,
)
from mnemo.cli.commands.doctor_checks.fidelity import (  # noqa: F401
    _doctor_check_zero_hit,
)
from mnemo.cli.commands.doctor_checks.misc import (  # noqa: F401
    _doctor_check_auto_brain,
    _doctor_check_legacy_wiki_dirs,
)
from mnemo.cli.commands.doctor_checks.reflex import (  # noqa: F401
    _doctor_check_reflex_bilingual_gap,
    _doctor_check_reflex_index,
    _doctor_check_reflex_session_cap_hits,
    _doctor_check_statusline_drift,
)
from mnemo.cli.commands.doctor_checks.rules import (  # noqa: F401
    _doctor_check_rule_integrity,
    _doctor_check_universal_promotion,
)
