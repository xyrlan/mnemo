"""Per-model USD-per-MTok lookup."""
from __future__ import annotations

import pytest

from mnemo.core import pricing


def test_known_model_prices() -> None:
    assert pricing.estimate_usd("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0) == pytest.approx(1.0)
    assert pricing.estimate_usd("claude-haiku-4-5", input_tokens=0, output_tokens=1_000_000) == pytest.approx(5.0)
    assert pricing.estimate_usd("claude-opus-4-7", input_tokens=1_000_000, output_tokens=1_000_000) > 0


def test_unknown_model_returns_none() -> None:
    assert pricing.estimate_usd("future-model-x", input_tokens=100, output_tokens=100) is None


def test_zero_tokens_zero_cost() -> None:
    assert pricing.estimate_usd("claude-haiku-4-5", input_tokens=0, output_tokens=0) == 0.0
