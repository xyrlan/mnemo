"""Run-to-run spread and tie rate over issues run as twins, and the study they size (#449).

Usage:
    PYTHONPATH=src python3 tools/measure_child_pairs.py [--vault ~/mnemo] [--agreement 0.78] [--list] [--json]

Read-only: no LLM calls, no network, no writes.

#439 sized a child-level vault A/B — two children on one issue, one with the
vault and one without, a blind "which would you merge?" — at 85 to 580 pairs,
and named the two unknowns that decide where in that range it lands. Both are
measured here, over the pairs ``mnemo dispatch <n> --twins`` recorded in
``<vault>/.mnemo/dispatch-twins.jsonl``:

- **Run-to-run spread**, as the log-sd of one run around its issue's mean,
  from the twins' **output tokens** — ``state.json`` ``tokens``, which #439
  measured to be the cumulative output tokens (median ratio 1.0004 to the
  transcript's sum over 109 children) — and from their **wall time**
  (``createdAt`` to the first finish). With two runs per issue the difference
  of their logs ``d`` carries it: ``sd = sqrt(sum(d^2) / 2n)``, the pooled
  within-pair estimate, ``n`` degrees of freedom. Read from the snapshot
  ``mnemo twins show`` took when both twins had finished, so a pair outlives
  Claude Code pruning its jobs; a pair never shown is read live, if its jobs
  are still on disk.
- **Tie rate**: answered ``tie`` over pairs answered at all.

Then the **pairs the full study needs**, with #439's own sizing, which this
reproduces to the pair (the unit tests pin its table):

- Preference: a two-sided sign test on the non-tied pairs, alpha 0.05, power
  0.8, normal approximation, with each label read right with probability
  ``c`` where ``c^2 + (1-c)^2`` is the rater's self-agreement (#411: 0.78 —
  a different labelling task, so a placeholder; ``--agreement`` replaces it).
  Divided by ``1 - tie rate`` for the pairs to *run*.
- Tokens, the secondary metric: a paired test on log output tokens, to detect
  a 15% reduction; the paired difference has sd ``sqrt(2)`` times the
  run-to-run spread.

Six pairs pin neither number down. The report gives a 95% interval for each
— chi-square for the spread (computed here, no scipy), Wilson for the tie
rate — and the pairs needed at both ends, so the study is sized from the
range and not from the point.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from statistics import NormalDist
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: #439's alpha (two-sided) and power.
ALPHA = 0.05
POWER = 0.80
#: #411's same-rater agreement, #439's placeholder for judge noise.
AGREEMENT = 0.78
#: The preference effects #439 tabulated; the issue asks for the last three.
EFFECTS = (0.70, 0.65, 0.60)
#: The token reduction #439 sized the secondary metric for.
REDUCTION = 0.15

_Z = NormalDist()


def label_accuracy(agreement: float) -> float:
    """``c`` such that two independent reads agree with probability *agreement*.

    ``c^2 + (1-c)^2 = agreement``; the root above one half. Agreement at or
    below 0.5 means the rater is noise, and is refused.
    """
    if not 0.5 < agreement <= 1.0:
        raise ValueError(f"agreement {agreement} must be in (0.5, 1]")
    return (1 + math.sqrt(2 * agreement - 1)) / 2


def sign_test_pairs_raw(effect: float, *, agreement: float = AGREEMENT,
                        alpha: float = ALPHA, power: float = POWER) -> float:
    """Non-tied pairs for a sign test to see *effect*, before rounding."""
    c = label_accuracy(agreement)
    q = effect * c + (1 - effect) * (1 - c)
    za, zb = _Z.inv_cdf(1 - alpha / 2), _Z.inv_cdf(power)
    return ((za * 0.5 + zb * math.sqrt(q * (1 - q))) / (q - 0.5)) ** 2


def sign_test_pairs(effect: float, *, agreement: float = AGREEMENT,
                    tie_rate: float = 0.0) -> Optional[int]:
    """Pairs to run so the non-tied ones carry a sign test at *effect*.

    ``None`` when every pair ties: no number of pairs is enough.
    """
    if tie_rate >= 1:
        return None
    return round(sign_test_pairs_raw(effect, agreement=agreement) / (1 - tie_rate))


def token_pairs(spread: float, *, reduction: float = REDUCTION,
                alpha: float = ALPHA, power: float = POWER) -> int:
    """Pairs for a paired test on log tokens to see *reduction*, at run *spread*."""
    za, zb = _Z.inv_cdf(1 - alpha / 2), _Z.inv_cdf(power)
    delta = -math.log(1 - reduction)
    return round(((za + zb) * math.sqrt(2) * spread / delta) ** 2)


def _chi2_cdf(x: float, k: int) -> float:
    """P(chi-square with *k* degrees of freedom <= *x*): the regularised lower
    incomplete gamma P(k/2, x/2), by its series below s+1 and its continued
    fraction above (Numerical Recipes 6.2)."""
    if x <= 0:
        return 0.0
    a, z = k / 2, x / 2
    log_front = a * math.log(z) - z - math.lgamma(a)
    if z < a + 1:
        term = total = 1 / a
        n = a
        while abs(term) > abs(total) * 1e-15:
            n += 1
            term *= z / n
            total += term
        return total * math.exp(log_front)
    b = z + 1 - a
    c, d = 1e300, 1 / b
    h = d
    for i in range(1, 1000):
        an = -i * (i - a)
        b += 2
        d = an * d + b
        d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = b + an / c
        c = c if abs(c) > 1e-300 else 1e-300
        h *= d * c
        if abs(d * c - 1) < 1e-15:
            break
    return 1 - math.exp(log_front) * h


def _chi2_quantile(p: float, k: int) -> float:
    """The chi-square quantile, by bisection on :func:`_chi2_cdf`.

    Exact to the bisection rather than an approximation: Wilson-Hilferty goes
    negative at one degree of freedom, which is where a pilot starts.
    """
    low, high = 0.0, max(10.0, 10.0 * k)
    while _chi2_cdf(high, k) < p:
        high *= 2
    for _ in range(200):
        mid = (low + high) / 2
        if _chi2_cdf(mid, k) < p:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def spread(values: Sequence[Tuple[float, float]]) -> Dict[str, Any]:
    """Run-to-run log-sd over twin pairs ``(a, b)``, with a 95% interval.

    Pairs with a missing or non-positive value are left out and counted.
    """
    diffs = [math.log(a) - math.log(b) for a, b in values
             if a is not None and b is not None and a > 0 and b > 0]
    n = len(diffs)
    out: Dict[str, Any] = {"pairs": n, "skipped": len(values) - n,
                           "log_sd": None, "low": None, "high": None,
                           "median_ratio": None}
    if not n:
        return out
    ss = sum(d * d for d in diffs) / 2
    out["log_sd"] = math.sqrt(ss / n)
    out["low"] = math.sqrt(ss / _chi2_quantile(0.975, n))
    out["high"] = math.sqrt(ss / _chi2_quantile(0.025, n))
    out["median_ratio"] = statistics.median(math.exp(abs(d)) for d in diffs)
    return out


def wilson(k: int, n: int) -> Tuple[Optional[float], Optional[float]]:
    """95% Wilson interval for *k* of *n*."""
    if not n:
        return None, None
    z = _Z.inv_cdf(0.975)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def _metric(pair: Any, tag: str, key: str, live: Any) -> Optional[float]:
    snap = (pair.metrics or {}).get(tag)
    if isinstance(snap, dict) and snap.get(key) is not None:
        return snap.get(key)
    return live(tag).get(key)


def rows(pairs: Sequence[Any], *, live: Any = None) -> List[Dict[str, Any]]:
    """One row per complete pair: both twins' tokens and wall time, and the answer.

    *live* maps a tag to :func:`mnemo.core.twins.metrics_of` of its job, for
    a pair never shown; the default reads the jobs directory.
    """
    from mnemo.core import twins

    out = []
    for pair in pairs:
        started = pair.started
        if len(started) != 2:
            continue

        def _live(tag: str, pair=pair) -> Dict[str, Any]:
            if live is not None:
                return live(tag)
            twin = pair.twin(tag)
            return twins.metrics_of(twins._state(twin.short_id))

        tags = [t.tag for t in started]
        out.append({
            "pair": pair.pair, "issue": pair.issue,
            "tokens": [_metric(pair, t, "tokens", _live) for t in tags],
            "wall_seconds": [_metric(pair, t, "wall_seconds", _live) for t in tags],
            "choice": pair.choice,
        })
    return out


def measure(pair_rows: Sequence[Dict[str, Any]], *,
            agreement: float = AGREEMENT) -> Dict[str, Any]:
    """The report: both spreads, the tie rate, and the pairs they size."""
    tokens = spread([tuple(r["tokens"]) for r in pair_rows])
    wall = spread([tuple(r["wall_seconds"]) for r in pair_rows])
    answered = [r for r in pair_rows if r.get("choice")]
    ties = sum(1 for r in answered if r["choice"] == "tie")
    tie_rate = ties / len(answered) if answered else None
    tie_low, tie_high = wilson(ties, len(answered))

    def _prefs(rate: Optional[float]) -> Dict[str, Optional[int]]:
        return {f"{round(e * 100)}/{round((1 - e) * 100)}":
                (sign_test_pairs(e, agreement=agreement, tie_rate=rate)
                 if rate is not None else None)
                for e in EFFECTS}

    def _tok(value: Optional[float]) -> Optional[int]:
        return token_pairs(value) if value is not None else None

    return {
        "pairs": len(pair_rows),
        "tokens": tokens,
        "wall": wall,
        "answered": len(answered),
        "ties": ties,
        "tie_rate": tie_rate,
        "tie_low": tie_low,
        "tie_high": tie_high,
        "agreement": agreement,
        "non_tied_pairs": {f"{round(e * 100)}/{round((1 - e) * 100)}":
                           sign_test_pairs(e, agreement=agreement) for e in EFFECTS},
        "preference_pairs": {"point": _prefs(tie_rate), "low": _prefs(tie_low),
                             "high": _prefs(tie_high)},
        "token_pairs": {"point": _tok(tokens["log_sd"]), "low": _tok(tokens["low"]),
                        "high": _tok(tokens["high"])},
    }


def _num(value: Optional[float], fmt: str = "{:.2f}") -> str:
    return "—" if value is None else fmt.format(value)


def format_report(report: Dict[str, Any], *, pair_rows: Sequence[Dict[str, Any]] = (),
                  listing: bool = False) -> str:
    lines = [f"pairs: {report['pairs']} complete (two twins started)"]
    for key, name in (("tokens", "output tokens"), ("wall", "wall time")):
        s = report[key]
        lines.append(
            f"{name}: run-to-run log-sd {_num(s['log_sd'])} "
            f"[95% {_num(s['low'])}–{_num(s['high'])}] over {s['pairs']} pair(s)"
            + (f", {s['skipped']} without a value" if s["skipped"] else "")
            + (f"; median twin ratio {_num(s['median_ratio'])}x" if s["median_ratio"] else "")
        )
    lines.append(
        f"ties: {report['ties']} of {report['answered']} answered = "
        f"{_num(report['tie_rate'], '{:.0%}')} "
        f"[95% {_num(report['tie_low'], '{:.0%}')}–{_num(report['tie_high'], '{:.0%}')}]"
    )
    lines.append("")
    lines.append(f"pairs the study needs (sign test, alpha {ALPHA}, power {POWER}, "
                 f"rater agreement {report['agreement']}):")
    prefs = report["preference_pairs"]
    for effect, n in report["non_tied_pairs"].items():
        lines.append(
            f"  {effect}: {n} non-tied → {_num(prefs['point'][effect], '{}')} to run "
            f"[{_num(prefs['low'][effect], '{}')}–{_num(prefs['high'][effect], '{}')}]"
        )
    tok = report["token_pairs"]
    lines.append(
        f"  tokens −{round(REDUCTION * 100)}%: {_num(tok['point'], '{}')} pairs "
        f"[{_num(tok['low'], '{}')}–{_num(tok['high'], '{}')}]"
    )
    if listing:
        lines.append("")
        for r in pair_rows:
            lines.append(
                f"  {r['pair']}  #{r['issue']}  tokens {r['tokens']}  "
                f"wall {[None if w is None else round(w) for w in r['wall_seconds']]}  "
                f"answer {r['choice'] or '—'}"
            )
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--vault", help="vault root (default: the configured one)")
    parser.add_argument("--agreement", type=float, default=AGREEMENT,
                        help=f"the rater's self-agreement (default {AGREEMENT}, #411)")
    parser.add_argument("--list", action="store_true", help="print every pair")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from mnemo.core import twins

    vault = Path(args.vault).expanduser() if args.vault else twins.default_vault()
    pair_rows = rows(list(twins.read_pairs(vault).values()))
    report = measure(pair_rows, agreement=args.agreement)
    if args.json:
        print(json.dumps({"report": report, "pairs": pair_rows}, indent=2))
    else:
        sys.stdout.write(format_report(report, pair_rows=pair_rows, listing=args.list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
