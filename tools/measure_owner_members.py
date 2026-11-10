"""How closely ``mnemo land`` reads a Rust or TypeScript type's members, against
the languages' own parsers (#675).

Usage:
    # 1. the oracles, once per corpus — each prints file<TAB>owner<TAB>members:
    cargo build --release --manifest-path tools/owner_oracles/syn/Cargo.toml \\
        --target-dir DIR
    DIR/release/owner-oracle FILE.rs ... > rs.tsv
    node tools/owner_oracles/tsc.js .../typescript/lib/typescript.js FILES.txt > ts.tsv
    # 2. the comparison:
    PYTHONPATH=src python3 tools/measure_owner_members.py rs.tsv ts.tsv \\
        [--root DIR] [--negatives] [--json]

``land`` checks a dotted signature, `ChildSession.notice`, among what the
type called ``ChildSession`` declares in the piece's files. For Python that
is :mod:`ast`; for Rust and TS it is ``landing._owner_members``, a reader
with no parser behind it, so its answer is worth measuring rather than
trusting. Each oracle row names a file, a type in it and that type's members
as ``syn`` or ``tsc`` parse them; for every row this asks the reader the
same question and counts:

- **exact** — the same member set;
- **differs** — a member the parser sees and the reader does not (a
  ``missing`` one fails a landing that was right) or the other way round
  (an ``extra`` one lets a missing member through);
- **unreadable** — the reader returned ``None`` and ``land`` falls back to
  the bare-name lookup, the behaviour before #675.

``--negatives`` asks the opposite question: every other identifier in each
file, which the parser says names no type there, must read as ``None``. A
false owner would scope a lookup to a type that is not there and turn a
right ✓ into ✗. It is slower — one call per distinct word per file.

A file the oracle could not parse (``!PARSE``) is skipped and counted. File
paths print relative to ``--root`` when given, and with the home directory
as ``~``. Nothing is written and nothing is sent anywhere.
"""
from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

from mnemo.core import landing

_WORD = re.compile(r"\b[A-Za-z_]\w*\b")


def language(path: str) -> Optional[str]:
    suffix = os.path.splitext(path)[1].lower()
    if suffix in landing._RUST_SUFFIXES:
        return "rust"
    if suffix in landing._TS_SUFFIXES:
        return "typescript"
    return None


def read_rows(paths: Sequence[Path]) -> Dict[str, Optional[Dict[str, Set[str]]]]:
    """``file -> {owner: members}`` from oracle output; ``None`` for ``!PARSE``.

    Rows for one owner in one file are summed, as the reader sums every
    declaration of it (Rust `impl` blocks, TS declaration merging).
    """
    files: Dict[str, Optional[Dict[str, Set[str]]]] = {}
    for path in paths:
        # `\n` only, and no universal newlines: a `\r` left in a path by a
        # CRLF file list must reach the count as an unreadable file, not
        # split its row in two.
        for line in path.read_bytes().decode("utf-8").split("\n"):
            line = line[:-1] if line.endswith("\r") else line
            if not line.strip():
                continue
            name, owner, members = (line.split("\t") + ["", ""])[:3]
            if owner == "!PARSE":
                files[name] = None
                continue
            owners = files.setdefault(name, {})
            if owners is None or not owner:
                continue
            owners.setdefault(owner, set()).update(m for m in members.split(",") if m)
    return files


def _blank_counts() -> Dict[str, int]:
    return {"owners": 0, "exact": 0, "differs": 0, "unreadable": 0,
            "members": 0, "missing": 0, "extra": 0}


def compare(files: Dict[str, Optional[Dict[str, Set[str]]]], *,
            root: Optional[Path] = None, negatives: bool = False) -> Dict[str, Any]:
    by_language: Dict[str, Dict[str, int]] = {}
    differences: List[Dict[str, Any]] = []
    skipped = {"unparsed": 0, "unread": 0, "other_language": 0}
    names_checked = 0
    false_owners: List[Dict[str, Any]] = []
    # One masking per file, not one per question: `--negatives` asks hundreds.
    code_only = landing._code_only
    landing._code_only = functools.lru_cache(maxsize=4)(code_only)
    try:
        for name in sorted(files):
            owners = files[name]
            lang = language(name)
            if lang is None:
                skipped["other_language"] += 1
                continue
            if owners is None:
                skipped["unparsed"] += 1
                continue
            try:
                source = (root / name if root else Path(name)).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped["unread"] += 1
                continue
            counts = by_language.setdefault(lang, _blank_counts())
            shown = _provenance.redact(name)
            for owner in sorted(owners):
                want = owners[owner]
                got = landing._owner_members(name, source, owner)
                counts["owners"] += 1
                counts["members"] += len(want)
                if got is None:
                    counts["unreadable"] += 1
                    differences.append({"file": shown, "owner": owner, "unreadable": True})
                    continue
                missing, extra = sorted(want - got), sorted(got - want)
                counts["missing"] += len(missing)
                counts["extra"] += len(extra)
                if missing or extra:
                    counts["differs"] += 1
                    differences.append({"file": shown, "owner": owner,
                                        "missing": missing, "extra": extra})
                else:
                    counts["exact"] += 1
            if negatives:
                for word in sorted(set(_WORD.findall(source)) - set(owners)):
                    names_checked += 1
                    got = landing._owner_members(name, source, word)
                    if got is not None:
                        false_owners.append({"file": shown, "owner": word,
                                             "members": sorted(got)})
    finally:
        landing._code_only = code_only
    report: Dict[str, Any] = {"languages": by_language, "differences": differences,
                              "skipped": skipped}
    if negatives:
        report["negatives"] = {"names_checked": names_checked, "false_owners": false_owners}
    return report


def render(report: Dict[str, Any]) -> str:
    out = []
    for lang, c in sorted(report["languages"].items()):
        out.append(f"{lang:<11} owners {c['owners']}  exact {c['exact']}  "
                   f"differs {c['differs']}  unreadable {c['unreadable']}  "
                   f"members {c['members']}  missing {c['missing']}  extra {c['extra']}")
    skipped = report["skipped"]
    if any(skipped.values()):
        out.append("skipped     " + "  ".join(f"{k} {v}" for k, v in skipped.items() if v))
    if "negatives" in report:
        neg = report["negatives"]
        out.append(f"negatives   names {neg['names_checked']}  "
                   f"false owners {len(neg['false_owners'])}")
        for row in neg["false_owners"]:
            out.append(f"  false owner {row['file']}: {row['owner']}")
    for row in report["differences"]:
        where = f"{row['file']}: {row['owner']}"
        if row.get("unreadable"):
            out.append(f"  unreadable {where}")
        else:
            out.append(f"  differs    {where}  missing {row['missing']}  extra {row['extra']}")
    return "\n".join(out)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("oracle", nargs="+", type=Path,
                    help="file<TAB>owner<TAB>members rows from tools/owner_oracles")
    ap.add_argument("--root", type=Path, help="what the rows' relative paths are under")
    ap.add_argument("--negatives", action="store_true",
                    help="also check that no other word in a file reads as a type")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = compare(read_rows(args.oracle), root=args.root, negatives=args.negatives)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "only the files the oracle rows name",
        "names a contract cannot spell (quoted, computed) are not compared"])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=1))
    else:
        print(render(report))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
