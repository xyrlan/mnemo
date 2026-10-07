"""How long each ``mnemo doctor`` row takes on this machine's vault (#571).

Usage:
    PYTHONPATH=src python3 tools/measure_doctor_rows.py [--rows NAME ...] [--rounds N] [--json]

``mnemo doctor`` went from 4.8 s (2026-09-22) to 26 s (2026-10-07), and
mnemo-desktop's vault screen waits for it. A cProfile of the whole command
points at the functions; this names the *row*, which is what gets fixed. Each
row in ``DOCTOR_CHECKS`` is called on its own, its output swallowed, and its
wall time kept; ``--rounds`` repeats the sweep and the report shows the median
and the spread, because on a shared machine one sweep is one sample of the
load as much as of the code.

To compare two trees, run it under each one's ``PYTHONPATH`` back to back.
Rows that keep a cache (``rediscovered_procedures``) are cold on the first
round after the cache is deleted and warm after; the per-round times show it.

Measured at #571 (local vault, 3,345 indexed rules, 767 child transcripts,
load 33–66): ``universal_promotion`` 14–21 s before, 0.7–0.9 s after;
``rediscovered_procedures`` 13–20 s before, 0.3–1.7 s after with a warm cache.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


def time_rows(
    vault: Path,
    checks: Sequence[Tuple[str, Callable[[Path], Any]]],
    *,
    rounds: int = 1,
    only: Optional[Iterable[str]] = None,
    clock: Callable[[], float] = time.perf_counter,
) -> List[Dict[str, Any]]:
    """``[{"row", "seconds": [per round]}]`` in registry order.

    A row that raises is timed up to the raise and marked, the way the doctor
    itself would have stopped there; the sweep goes on to the next row.
    """
    wanted = set(only or [])
    rows = [(name, fn) for name, fn in checks if not wanted or name in wanted]
    out: Dict[str, Dict[str, Any]] = {name: {"row": name, "seconds": [], "raised": False}
                                     for name, _fn in rows}
    for _round in range(max(1, rounds)):
        for name, fn in rows:
            start = clock()
            try:
                with contextlib.redirect_stdout(io.StringIO()):
                    fn(vault)
            except Exception:  # noqa: BLE001 — one broken row must not end the sweep
                out[name]["raised"] = True
            out[name]["seconds"].append(clock() - start)
    return [out[name] for name, _fn in rows]


def render(rows: Sequence[Dict[str, Any]]) -> str:
    lines = [f"{'row':28s} {'median':>8s} {'min':>8s} {'max':>8s}  per round"]
    for row in rows:
        secs = row["seconds"]
        per_round = " ".join(f"{s:.2f}" for s in secs)
        mark = "  (raised)" if row.get("raised") else ""
        lines.append(f"{row['row']:28s} {statistics.median(secs):8.2f} "
                     f"{min(secs):8.2f} {max(secs):8.2f}  {per_round}{mark}")
    total = sum(statistics.median(row["seconds"]) for row in rows)
    lines.append(f"{'total (sum of medians)':28s} {total:8.2f}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vault", default=None, help="the vault (default: mnemo's configured one)")
    ap.add_argument("--rows", nargs="*", default=None, help="only these rows")
    ap.add_argument("--rounds", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    if args.vault:
        vault = Path(args.vault).expanduser()
    else:
        from mnemo import cli
        vault = cli._resolve_vault()
    rows = time_rows(vault, DOCTOR_CHECKS, rounds=args.rounds, only=args.rows)
    if args.json:
        print(json.dumps(rows, indent=1))
    else:
        print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
