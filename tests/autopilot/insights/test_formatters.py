from __future__ import annotations

import pytest

from mnemo.autopilot.insights._formatters import (
    fmt_pct,
    fmt_delta_pp,
    fmt_delta,
    fmt_int,
)


def test_fmt_pct_basic():
    assert fmt_pct(90.0) == "90.0%"
    assert fmt_pct(0.0) == "0.0%"
    assert fmt_pct(100.0) == "100.0%"
    assert fmt_pct(5.678) == "5.7%"


def test_fmt_delta_pp_positive():
    assert fmt_delta_pp(1.0) == "Δ +1.0pp"


def test_fmt_delta_pp_negative():
    assert fmt_delta_pp(-1.2) == "Δ -1.2pp"


def test_fmt_delta_pp_zero():
    assert fmt_delta_pp(0.0) == "Δ +0.0pp"


def test_fmt_delta_positive():
    assert fmt_delta(0.001) == "Δ +0.001"


def test_fmt_delta_negative():
    assert fmt_delta(-0.002) == "Δ -0.002"


def test_fmt_delta_zero():
    assert fmt_delta(0.0) == "Δ +0.000"


def test_fmt_int_large():
    assert fmt_int(1346) == "1,346"


def test_fmt_int_small():
    assert fmt_int(5) == "5"


def test_fmt_int_zero():
    assert fmt_int(0) == "0"
