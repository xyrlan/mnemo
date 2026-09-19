"""What the session-start procedure offer costs: bytes, tokens, hook time (#397).

Usage:
    PYTHONPATH=src python3 tools/measure_procedure_offer.py
    PYTHONPATH=src python3 tools/measure_procedure_offer.py --vault ~/mnemo --json

Read-only: no LLM calls, no writes, no ledger rows. The block is rendered by
the hook's own function with :func:`mnemo.core.procedures.record` neutralised,
so what is measured is the text a session would actually receive and not a
reconstruction of it that could drift from one.

The block rides on the session-start prompt beside a briefing that costs ~1783
tokens at 90.9% of starts, so "how much does it cost" is not rhetorical, and
this repo's standing rule is that the number ships with the thing that measured
it. Three costs, and they are different in kind:

- **bytes and tokens** — what the prompt pays, per repo that has a candidate.
  Tokens are ``bytes / 4``, the same conversion ``tools/measure_exploration.py``
  uses, so the figures are comparable with the ones already quoted.
- **hook time** — what session start pays *per start*, which is the offer path
  only: reading a ledger and a cache. It is reported against the same path
  before this existed (the staged block alone), because the added cost is the
  difference and not the total.
- **scan time** — what finding candidates costs, which the hook does **not**
  pay: it runs detached, at most once per ``refreshIntervalHours``, and the
  block reads its cache. Reported so the reason for that split stays checkable.

Nothing here decides anything. A block that has grown past a kilobyte, or a
hook path that has grown past a millisecond, is a decision somebody should make
on purpose.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional

from mnemo.core import procedures as P

#: The repo's own conversion, kept in one place so two measurements of the same
#: block cannot report two different token counts.
BYTES_PER_TOKEN = 4.0


def _bench(fn: Callable[[], Any], repeats: int = 50) -> float:
    """Milliseconds per call, best-effort and warm — the hook path is warm too."""
    fn()
    start = time.perf_counter()
    for _ in range(repeats):
        fn()
    return (time.perf_counter() - start) / repeats * 1000.0


def measure(
    vault_root: str,
    *,
    projects: Optional[str] = None,
    repeats: int = 50,
) -> Dict[str, Any]:
    """Render the block for every repo that has one, and time the paths."""
    from pathlib import Path

    from mnemo.core import config as config_mod
    from mnemo.hooks import session_start

    vault = Path(os.path.expanduser(vault_root))
    cfg = config_mod.load_config()
    # Bypass the two bounds that exist to make the block *rare*: the question
    # here is what one costs when it fires, and a run that happened to land
    # inside today's interval would otherwise measure an empty string.
    unbounded = {
        **cfg,
        "procedures": {
            **(cfg.get("procedures") or {}),
            "offerOnSessionStart": True,
            "offerIntervalHours": 0,
            "offerCooldownDays": 0,
        },
    }

    scanned = None
    if projects:
        start = time.perf_counter()
        candidates = P.scan(
            projects,
            min_children=int((cfg.get("procedures") or {}).get("minChildren", 2)),
            max_shape_repos=int((cfg.get("procedures") or {}).get("maxShapeRepos", 2)),
            ledger_rows=P.ledger_rows(vault),
        )
        scanned = {
            "seconds": round(time.perf_counter() - start, 3),
            "candidates": len([c for c in candidates if not c.stated]),
        }

    cached, generated_at = P.read_cache(vault)
    repos = sorted({c.repo for c in cached})

    written: List[Dict[str, Any]] = []
    recorded = P.record
    P.record = lambda *a, **k: None  # type: ignore[assignment]
    try:
        for repo in repos:
            root = next((c.repo_root for c in cached if c.repo == repo and c.repo_root), "")
            # A path that is not a dispatch worktree: the block is silent inside
            # one on purpose, and measuring that silence says nothing.
            block = session_start._procedures_offer_block(
                vault, unbounded, repo, root or None, None
            )
            if not block:
                continue
            size = len(block.encode("utf-8"))
            written.append({
                "repo": repo,
                "bytes": size,
                "tokens": round(size / BYTES_PER_TOKEN),
                "bullets": len([ln for ln in block.splitlines() if ln.startswith("• ")]),
                "block": block,
            })

        first = written[0]["repo"] if written else (repos[0] if repos else "")
        root = next((c.repo_root for c in cached if c.repo == first and c.repo_root), None)
        before = _bench(
            lambda: session_start._staged_offer_block(vault, cfg, first), repeats
        )
        after = _bench(
            lambda: session_start._offer_block(vault, cfg, first, root, None), repeats
        )
    finally:
        P.record = recorded  # type: ignore[assignment]

    return {
        "vault": str(vault),
        "generated_at": generated_at.isoformat() if generated_at else None,
        "cached": len(cached),
        "blocks": written,
        "hook_ms": {
            "staged_offer_only": round(before, 3),
            "arbitrated_offer": round(after, 3),
            "added": round(after - before, 3),
        },
        "scan": scanned,
    }


def format_report(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    stamp = report["generated_at"] or "never — run `mnemo procedures --refresh`"
    lines.append(f"{report['cached']} candidate(s) cached in {report['vault']} "
                 f"(scanned {stamp})")
    lines.append("")
    if not report["blocks"]:
        lines.append("  no repo has an undecided candidate, so no session start "
                     "would see a block")
        lines.append("")
    for row in report["blocks"]:
        lines.append(f"  {row['repo']:16} {row['bytes']:5} bytes  "
                     f"~{row['tokens']:4} tokens  {row['bullets']} bullet(s)")
        for line in row["block"].splitlines():
            lines.append(f"      {line}")
        lines.append("")

    hook = report["hook_ms"]
    lines.append(f"hook: {hook['staged_offer_only']:.3f} ms before this existed, "
                 f"{hook['arbitrated_offer']:.3f} ms with the offer arbitrated "
                 f"— {hook['added']:+.3f} ms per session start")
    if report["scan"]:
        scan = report["scan"]
        lines.append(f"scan: {scan['seconds']:.2f} s for {scan['candidates']} undecided "
                     "candidate(s) — detached, never on the session-start path")
    lines.append("")
    lines.append("A briefing costs ~1783 tokens at 90.9% of session starts, and at most")
    lines.append("one offer block ships per start — this one or the staged-page one.")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--vault", default="~/mnemo")
    parser.add_argument("--projects", default=None,
                        help="also time a full scan of this transcript directory")
    parser.add_argument("--repeats", type=int, default=50)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = measure(args.vault, projects=args.projects, repeats=args.repeats)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
