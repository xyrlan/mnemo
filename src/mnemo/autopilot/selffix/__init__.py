"""Autopilot Tier 1 — Self-Fix.

Modules:
- doctor_fixer: detect + fix auto-fixable doctor warnings
- dead_rule_sweep: detect + archive dead rules
- telemetry_doctor: detect telemetry anomalies + open draft PR
- outcome_poller: poll closed self-fix PRs for outcomes
- _perimeter: perimeter guard (must be enforced before every PR)
- _gh: thin wrapper around the gh CLI
"""
from __future__ import annotations
