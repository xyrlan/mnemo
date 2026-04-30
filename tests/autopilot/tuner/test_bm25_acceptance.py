"""Tests for BM25 acceptance gates — T5."""
from __future__ import annotations

import pytest

from mnemo.autopilot.tuner._scorer import ScoreReport
from mnemo.autopilot.tuner.bm25_tuner import meets_acceptance


class TestMeetsAcceptance:
    def _report(self, primacy=0.8, mrr=0.6, p95=10.0, n=20):
        return ScoreReport(primacy_at_5=primacy, mrr=mrr, p95_latency_ms=p95, n_cases=n)

    def test_all_criteria_pass(self):
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.53, mrr=0.33, p95=10.0)
        assert meets_acceptance(before, after) is True

    def test_fails_when_primacy_delta_too_small(self):
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.51, mrr=0.33, p95=10.0)  # only +1pp
        assert meets_acceptance(before, after) is False

    def test_fails_when_mrr_delta_too_small(self):
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.53, mrr=0.31, p95=10.0)  # only +0.01
        assert meets_acceptance(before, after) is False

    def test_fails_when_latency_regresses(self):
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.53, mrr=0.33, p95=10.6)  # +6% > 5%
        assert meets_acceptance(before, after) is False

    def test_passes_when_latency_just_within_limit(self):
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.53, mrr=0.33, p95=10.49)  # +4.9%
        assert meets_acceptance(before, after) is True

    def test_passes_when_latency_improves(self):
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.53, mrr=0.33, p95=8.0)
        assert meets_acceptance(before, after) is True

    def test_exact_threshold_primacy(self):
        """Exactly 2pp increase should pass."""
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.52, mrr=0.33, p95=10.0)
        assert meets_acceptance(before, after) is True

    def test_exact_threshold_mrr(self):
        """Exactly 0.02 MRR increase should pass."""
        before = self._report(primacy=0.5, mrr=0.3, p95=10.0)
        after = self._report(primacy=0.53, mrr=0.32, p95=10.0)
        assert meets_acceptance(before, after) is True

    def test_zero_p95_before_does_not_crash(self):
        """If baseline p95=0, any after latency passes."""
        before = self._report(primacy=0.5, mrr=0.3, p95=0.0)
        after = self._report(primacy=0.53, mrr=0.33, p95=5.0)
        assert meets_acceptance(before, after) is True
