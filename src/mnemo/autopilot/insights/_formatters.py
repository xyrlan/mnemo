"""Number-formatting helpers for the autopilot digest.

All functions are pure; no I/O.
"""
from __future__ import annotations


def fmt_pct(val: float) -> str:
    """Format a percentage value: ``fmt_pct(90.0) == "90.0%"``."""
    return f"{val:.1f}%"


def fmt_delta_pp(delta: float) -> str:
    """Format a percentage-point delta: ``fmt_delta_pp(1.2) == "Δ +1.2pp"``."""
    sign = "+" if delta >= 0 else ""
    return f"Δ {sign}{delta:.1f}pp"


def fmt_delta(delta: float) -> str:
    """Format a generic delta: ``fmt_delta(0.001) == "Δ +0.001"``."""
    sign = "+" if delta >= 0 else ""
    return f"Δ {sign}{delta:.3f}"


def fmt_int(n: int) -> str:
    """Format an integer with thousands comma separators."""
    return f"{n:,}"
