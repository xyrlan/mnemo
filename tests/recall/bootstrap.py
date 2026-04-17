"""Regenerate ``tests/recall/cases.json`` from an access-log.

Usage:
    python -m tests.recall.bootstrap                # read vault from mnemo.config.json
    python -m tests.recall.bootstrap --vault PATH   # override vault root
    python -m tests.recall.bootstrap --log PATH     # override log path directly

The script is idempotent: running it twice against the same log produces the
same ``cases.json``. Re-run it whenever the access-log grows so the regression
suite widens its coverage.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mnemo.core.mcp.recall import bootstrap_cases
from mnemo.core.paths import vault_root as resolve_vault_root


_CASES_PATH = Path(__file__).parent / "cases.json"


def _default_log_path() -> Path:
    cfg_path = Path.cwd() / "mnemo.config.json"
    cfg = {}
    if cfg_path.is_file():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    vault = resolve_vault_root(cfg)
    return vault / ".mnemo" / "mcp-access-log.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="bootstrap-recall-cases")
    ap.add_argument("--log", type=Path, help="Path to mcp-access-log.jsonl")
    ap.add_argument("--vault", type=Path, help="Vault root; log resolved to <vault>/.mnemo/mcp-access-log.jsonl")
    ap.add_argument("--window-s", type=float, default=120.0,
                    help="Pair window in seconds (list→read); default 120")
    ap.add_argument("--out", type=Path, default=_CASES_PATH, help="Output path for cases.json")
    args = ap.parse_args(argv)

    if args.log is not None:
        log_path = args.log
    elif args.vault is not None:
        log_path = args.vault / ".mnemo" / "mcp-access-log.jsonl"
    else:
        log_path = _default_log_path()

    if not log_path.is_file():
        print(f"access log not found: {log_path}", file=sys.stderr)
        return 1

    cases = bootstrap_cases(log_path, pair_window_s=args.window_s)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(cases)} cases → {args.out}")
    if cases:
        projects = sorted({c["project"] for c in cases})
        topics = sorted({c["topic"] for c in cases})
        print(f"  projects: {', '.join(projects)}")
        print(f"  topics  : {', '.join(topics)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
