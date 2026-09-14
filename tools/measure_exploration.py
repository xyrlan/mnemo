"""How much a dispatched child explores before its first edit, by reflex injection (#269).

Usage:
    PYTHONPATH=src python3 tools/measure_exploration.py [--projects ~/.claude/projects] [--list] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

The population is every transcript under a ``<repo>-wt-<issue>`` or
``<repo>-wt-c-<piece>`` project directory — the worktrees ``mnemo dispatch``
creates — so children whose job ``claude rm`` already removed still count.
The path must parse as a dispatch worktree (``dispatch.issue_for_cwd``), and
pytest's live-test trees (``…/pytest-of-<user>/…/live-wt-N``), which parse as
one, are excluded.

The question is the one #269 asks and nothing else answers: **did the vault
replace exploration, or just precede it?** So the population is split by how
many reflex rules the opening prompt carried, and each bucket reports its size
next to its medians. Two things this is not:

- **Not causal.** Whether reflex fires depends on the prompt, and so does how
  much a child has to explore: an issue that names a file and a line both
  scores differently and needs fewer reads. The split is a correlation over
  what happened, with its sample size, and should be read as one.
- **Not the whole vault.** Every child also gets the SessionStart topic list
  and the last briefing, so "no reflex rules" is not "no vault context". The
  split isolates reflex, which is the lever #158/#244 turn.

``--list`` prints the first mutation chosen for every transcript. Read it
before trusting a median: the Bash classifier works from command text, and
this listing is how a wrong choice gets seen.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from typing import Any, Dict, List, Optional, Tuple

from mnemo.core.activity import exploration_for
from mnemo.core.dispatch import issue_for_cwd


def _transcripts(projects: str) -> List[Tuple[str, str]]:
    """``(transcript path, worktree cwd)`` for every dispatch child on disk."""
    out = []
    for directory in sorted(glob.glob(os.path.join(projects, "*"))):
        for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
            cwd = _cwd_of(path)
            if cwd and issue_for_cwd(cwd) is not None and not _is_test_tree(cwd):
                out.append((path, cwd))
    return out


def _is_test_tree(cwd: str) -> bool:
    """Pytest's live ``claude --bg`` probes name their trees ``live-wt-N`` under a
    temp dir; they parse as dispatch worktrees and explore nothing."""
    return "/pytest-of-" in cwd.replace("\\", "/")


def _cwd_of(path: str) -> Optional[str]:
    """The ``cwd`` the transcript's first event that has one records."""
    try:
        with open(path, "rb") as fh:
            for index, raw in enumerate(fh):
                if index > 200:
                    return None
                try:
                    event = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(event, dict) and isinstance(event.get("cwd"), str):
                    return event["cwd"]
    except OSError:
        return None
    return None


def _bucket(injected: Optional[int]) -> str:
    if not injected:
        return "0 regras"
    return "1 regra" if injected == 1 else f"{min(injected, 3)}{'+' if injected > 3 else ''} regras"


def _median(values: List[int]) -> Optional[float]:
    return statistics.median(values) if values else None


def _spearman(xs: List[float], ys: List[float]) -> Optional[float]:
    """Rank correlation, ties averaged. ``None`` below three points or no spread."""
    if len(xs) < 3:
        return None

    def ranks(values: List[float]) -> List[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    if not vx or not vy:
        return None
    return cov / (vx * vy) ** 0.5


def measure(projects: str) -> Dict[str, Any]:
    rows = []
    for path, cwd in _transcripts(projects):
        found = exploration_for(path, cwd)
        if found is None or found.baseline is None:
            continue  # opened and never took a model turn: nothing to measure
        rows.append({
            "transcript": path,
            "worktree": os.path.basename(cwd),
            **found.as_dict(),
        })

    reached = [r for r in rows if r["reached"]]
    buckets: Dict[str, Dict[str, Any]] = {}
    for row in reached:
        b = buckets.setdefault(_bucket(row["injected"]), {"n": 0, "uses": [], "tokens": []})
        b["n"] += 1
        b["uses"].append(row["uses"])
        b["tokens"].append(row["tokens"])

    summary = {
        name: {
            "n": b["n"],
            "median_uses": _median(b["uses"]),
            "median_tokens": _median(b["tokens"]),
        }
        for name, b in sorted(buckets.items())
    }
    injected_counts = [float(r["injected"] or 0) for r in reached]
    return {
        "transcripts": len(rows),
        "reached_mutation": len(reached),
        "never_mutated": len(rows) - len(reached),
        "median_baseline": _median([r["baseline"] for r in rows]),
        "by_injection": summary,
        "spearman_injected_vs_uses": _spearman(injected_counts, [float(r["uses"]) for r in reached]),
        "spearman_injected_vs_tokens": _spearman(injected_counts, [float(r["tokens"]) for r in reached]),
        "rows": rows,
    }


def _k(value: Optional[float]) -> str:
    return "—" if value is None else f"{value / 1000:.0f}k"


def format_report(report: Dict[str, Any], *, listing: bool = False) -> str:
    lines = []
    if listing:
        for r in report["rows"]:
            first = f"{r['tool']} {r['target'] or ''}".strip() if r["reached"] else "(sem mutação)"
            lines.append(
                f"{r['worktree']:<32} usos={r['uses']:>3} +{_k(r['tokens']):>5} "
                f"base={_k(r['baseline']):>4} regras={r['injected'] or 0}  → {first}"
            )
        lines.append("")

    lines.append(
        f"{report['transcripts']} transcripts de dispatch; {report['reached_mutation']} chegaram a "
        f"uma mutação, {report['never_mutated']} não (excluídos das medianas)."
    )
    lines.append(f"linha de base mediana (1º turno): {_k(report['median_baseline'])} tokens")
    lines.append("")
    lines.append("antes da 1ª mutação, por regras injetadas no prompt de abertura:")
    for name, b in report["by_injection"].items():
        lines.append(
            f"  {name:<10} n={b['n']:>2}  usos (mediana) {b['median_uses']:>5}  "
            f"tokens (mediana) {_k(b['median_tokens']):>5}"
        )
    rho_u = report["spearman_injected_vs_uses"]
    rho_t = report["spearman_injected_vs_tokens"]
    n = report["reached_mutation"]
    fmt = lambda v: "—" if v is None else f"{v:+.2f}"  # noqa: E731
    lines.append(f"Spearman (regras × usos) {fmt(rho_u)}, (regras × tokens) {fmt(rho_t)}, n={n}")
    lines.append("correlação, não causa: o prompt decide tanto o que o reflex injeta quanto o quanto há para explorar.")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--list", action="store_true", help="print the first mutation chosen per transcript")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = measure(args.projects)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, listing=args.list), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
