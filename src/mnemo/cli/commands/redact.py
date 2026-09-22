"""``mnemo redact`` — secrets already on disk in pages and briefings (#418).

    mnemo redact            # report: file, line, kind — never the value
    mnemo redact --apply    # rewrite those files with the values redacted
    mnemo redact --json     # machine-readable report

Walks ``shared/`` and ``bots/*/briefings/``; see :mod:`mnemo.core.redact_vault`
for what it leaves alone and why.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command


@command("redact")
def cmd_redact(args: argparse.Namespace) -> int:
    import json as _json

    from mnemo import cli
    from mnemo.core import redact_vault as RV

    vault = cli._resolve_vault()
    apply = bool(getattr(args, "apply", False))
    try:
        report = RV.apply(vault) if apply else RV.scan(vault)
    except RV.VaultBusy as exc:
        print(f"mnemo redact: {exc}")
        return 1

    if getattr(args, "json", False):
        print(_json.dumps({
            "files_scanned": report.files_scanned,
            "files": len(report.files),
            "hits": [{"path": h.path, "line": h.line, "kind": h.kind} for h in report.hits],
            "rewritten": report.rewritten,
        }, indent=2))
        return 0

    if not report.hits:
        print(f"No secrets found in {report.files_scanned} pages and briefings.")
        return 0

    by_file: dict = {}
    for h in report.hits:
        by_file.setdefault(h.path, []).append(h)
    for path, hits in by_file.items():
        where = ", ".join(f"line {h.line} ({h.kind})" for h in hits)
        print(f"  {path}: {where}")
    n, f = len(report.hits), len(by_file)
    summary = (f"{n} secret{'s' if n != 1 else ''} in {f} file{'s' if f != 1 else ''} "
               f"({report.files_scanned} scanned)")
    if apply:
        print(f"Redacted {summary}.")
    else:
        print(f"{summary}. Values are not shown.")
        print("  → run `mnemo redact --apply` to replace them with [redacted] in place")
    return 0
