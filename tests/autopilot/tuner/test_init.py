"""Tests for tuner __init__ exports."""
from __future__ import annotations


def test_tuner_package_clean_import():
    """Package imports without side effects."""
    import mnemo.autopilot.tuner  # noqa: F401


def test_tuner_no_dispatcher_export():
    """Tier 2 cadence is hook-driven; the dead ``register_tune_jobs`` shim
    must not be re-introduced."""
    import mnemo.autopilot.tuner as tuner
    assert not hasattr(tuner, "register_tune_jobs")
