"""Per-model token pricing — USD per million tokens.

Keep this table in sync with Anthropic's published pricing
(https://www.anthropic.com/pricing). mnemo is stdlib-only and does not
fetch prices at runtime — bump the table when Anthropic changes prices.

**Status: PLACEHOLDER VALUES.** The rates below are starting estimates
ordered relative to each other (Haiku < Sonnet < Opus) but MUST be
verified against https://www.anthropic.com/pricing before tagging a
v0.10 release. Ship placeholder prices and `mnemo telemetry`'s USD
column will be systematically wrong.
"""
from __future__ import annotations

# (input_per_mtok_usd, output_per_mtok_usd)
_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5":          (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6":         (3.0, 15.0),
    "claude-opus-4-7":           (15.0, 75.0),
}


def estimate_usd(model: str, *, input_tokens: int, output_tokens: int) -> float | None:
    """Return USD cost for ``input_tokens`` + ``output_tokens`` at ``model``'s rate.

    Returns None for unknown models. Returns 0.0 for zero tokens regardless of model.
    """
    if input_tokens == 0 and output_tokens == 0:
        return 0.0
    rates = _PRICES.get(model)
    if rates is None:
        return None
    in_rate, out_rate = rates
    return (input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate


def known_models() -> tuple[str, ...]:
    return tuple(_PRICES.keys())
