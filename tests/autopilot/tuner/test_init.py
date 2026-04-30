"""Tests for tuner __init__ exports — T13."""
from __future__ import annotations


def test_register_tune_jobs_importable():
    from mnemo.autopilot.tuner import register_tune_jobs
    assert callable(register_tune_jobs)


def test_tuner_package_clean_import():
    """Package imports without side effects."""
    import mnemo.autopilot.tuner  # noqa: F401


def test_all_exports():
    import mnemo.autopilot.tuner as tuner
    assert hasattr(tuner, "register_tune_jobs")
