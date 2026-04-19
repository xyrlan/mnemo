"""``mnemo doctor`` — preflight + a registry of doctor checks.

PR H of the v0.9 refactor roadmap converted ``cmd_doctor`` from a
hardcoded and-chain of 11 calls into an OCP-compliant
``DOCTOR_CHECKS: list[tuple[name, callable]]`` registry. Adding a new
check is now a new row, not an edit to ``cmd_doctor``.
"""
from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from mnemo.cli.commands.doctor_checks import (
    activation,
    fidelity,
    misc as doctor_misc,
    reflex,
    rules,
)
from mnemo.cli.parser import command

# Registry: (display_name, check_fn). Order matters — it matches the
# original cmd_doctor invocation order in the v0.8.x cli.py monolith
# (lines 402-413). ``_doctor_check_universal_promotion`` always returns
# True today (advisory-only), so the ``if ok is False`` guard below is
# robust to either bool or None return values.
DOCTOR_CHECKS: list[tuple[str, Callable[[Path], bool]]] = [
    ("auto_brain",            doctor_misc._doctor_check_auto_brain),
    ("legacy_wiki_dirs",      doctor_misc._doctor_check_legacy_wiki_dirs),
    ("statusline_drift",      reflex._doctor_check_statusline_drift),
    ("activation",            activation._doctor_check_activation),
    ("zero_hit",              fidelity._doctor_check_zero_hit),
    ("activation_fidelity",   activation._doctor_check_activation_fidelity),
    ("rule_integrity",        rules._doctor_check_rule_integrity),
    ("reflex_index",          reflex._doctor_check_reflex_index),
    ("reflex_session_cap",    reflex._doctor_check_reflex_session_cap_hits),
    ("reflex_bilingual",      reflex._doctor_check_reflex_bilingual_gap),
    ("universal_promotion",   rules._doctor_check_universal_promotion),
]


@command("doctor")
def cmd_doctor(_args: argparse.Namespace) -> int:
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    from mnemo.install import preflight

    vault = cli._resolve_vault()
    print("Running diagnostic / preflight checks…")
    result = preflight.run_preflight(vault_root=vault)
    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        print(f"       → {issue.remediation}")

    all_ok = True
    for _name, check_fn in DOCTOR_CHECKS:
        ok = check_fn(vault)
        # universal_promotion is advisory and effectively always-True today;
        # treat None (or any non-False return) as "not a warning".
        if ok is False:
            all_ok = False

    _doctor_report_recall(vault)

    if not result.ok:
        print("Issues found above.")
        return 1
    if not all_ok:
        print("Warnings above.")
    else:
        print("OK")
    return 0


def _doctor_report_recall(vault: Path) -> None:
    """Emit one informational line from `.mnemo/recall-report.json` if present.

    Purely advisory — never turns doctor into a warning/error, since the recall
    suite is opt-in and the primacy rate is a trend indicator, not a pass/fail
    gate. Silent when the report is missing, malformed, or empty.
    """
    import json as _json
    path = vault / ".mnemo" / "recall-report.json"
    if not path.is_file():
        return
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return
    report = (data.get("report") or {}) if isinstance(data, dict) else {}
    cases = report.get("cases", 0) or 0
    rate = report.get("primacy_rate_at_5")
    if cases == 0 or rate is None:
        return
    ts = data.get("generated_at")
    suffix = f" (measured {ts})" if ts else ""
    print(f"  ℹ Recall: primacy@5 = {rate:.1%} over {cases} cases{suffix}")
