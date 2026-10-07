"""``tools/measure_doctor_rows.py`` over a synthetic registry and a fake clock (#571)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_doctor_rows as tool  # noqa: E402


class _Clock:
    """Each row advances time by what it was told to cost."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _registry(clock: _Clock, costs: dict, calls: list):
    def row(name, cost):
        def check(vault):
            calls.append((name, vault))
            print(f"{name} says something")
            clock.now += cost.pop(0) if isinstance(cost, list) else cost
            if name == "broken":
                raise RuntimeError("boom")
            return True
        return check
    return [(name, row(name, cost)) for name, cost in costs.items()]


def test_each_row_is_timed_in_registry_order_with_its_output_swallowed(tmp_path, capsys) -> None:
    clock, calls = _Clock(), []
    checks = _registry(clock, {"fast": 0.25, "slow": 12.0, "mid": 1.5}, calls)
    rows = tool.time_rows(tmp_path, checks, clock=clock)

    assert [(r["row"], r["seconds"]) for r in rows] == [
        ("fast", [0.25]), ("slow", [12.0]), ("mid", [1.5])]
    assert calls == [("fast", tmp_path), ("slow", tmp_path), ("mid", tmp_path)]
    assert capsys.readouterr().out == ""


def test_rounds_keep_every_sample_and_the_report_shows_the_median(tmp_path) -> None:
    clock = _Clock()
    checks = _registry(clock, {"cached": [9.0, 0.5, 0.25]}, [])
    rows = tool.time_rows(tmp_path, checks, rounds=3, clock=clock)

    assert rows[0]["seconds"] == [9.0, 0.5, 0.25]
    line = tool.render(rows).splitlines()[1]
    assert line.split()[:4] == ["cached", "0.50", "0.25", "9.00"]
    assert line.endswith("9.00 0.50 0.25")


def test_only_named_rows_run(tmp_path) -> None:
    clock, calls = _Clock(), []
    checks = _registry(clock, {"a": 1.0, "b": 2.0, "c": 3.0}, calls)
    rows = tool.time_rows(tmp_path, checks, only=["c", "a"], clock=clock)
    assert [r["row"] for r in rows] == ["a", "c"]
    assert [name for name, _v in calls] == ["a", "c"]


def test_a_row_that_raises_is_timed_marked_and_the_sweep_goes_on(tmp_path) -> None:
    clock = _Clock()
    checks = _registry(clock, {"broken": 2.0, "after": 1.0}, [])
    rows = tool.time_rows(tmp_path, checks, clock=clock)

    assert rows[0] == {"row": "broken", "seconds": [2.0], "raised": True}
    assert rows[1]["seconds"] == [1.0]
    report = tool.render(rows)
    assert "(raised)" in report.splitlines()[1]
    assert report.splitlines()[-1].split()[-1] == "3.00"
