"""Fold changelog.d/ fragments into CHANGELOG.md's ``## [Unreleased]``.

Five issues dispatched in parallel (#233–#237) touched disjoint code and every
PR still conflicted with every other one, in one file: each child added its
entry at the top of ``## [Unreleased]``, so the first merge invalidated the
other four and the rest were rebased, re-tested and re-run through CI one at
a time — the hour the parallel dispatch was meant to save.

So a change documents itself in a file of its own that nothing else touches::

    changelog.d/<id>.<section>.md

``<id>`` is the issue or PR number (or ``<feature>-<slug>`` for a contract
piece); ``<section>`` is one of :data:`SECTIONS`. The file holds the ``- ``
bullet(s) exactly as they will appear under the heading — the same prose an
entry carried when it was written into ``CHANGELOG.md`` by hand.

At release, ``python3 tools/assemble_changelog.py`` inserts every fragment at
the top of its ``### Section`` inside ``## [Unreleased]`` (creating a missing
heading in canonical order), removes the fragments, and writes the file back.
Nothing already in the file is reformatted; with no fragments the file is not
touched at all. ``--check`` validates without writing; ``--fail-if-pending``
is the release guard — it exits nonzero while any fragment is left, so a tag
pushed before assembling cannot publish a release whose notes are elsewhere.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional, Sequence, Tuple

FRAGMENT_DIR = "changelog.d"
CHANGELOG = "CHANGELOG.md"
UNRELEASED = "## [Unreleased]"

# Keep a Changelog's sections in the order the assembled file lists them,
# plus the two this changelog already uses at either end.
SECTIONS: Tuple[str, ...] = (
    "breaking", "added", "changed", "deprecated", "removed", "fixed", "security", "internal",
)
_RANK = {name: i for i, name in enumerate(SECTIONS)}

# Anything not matching this is refused by name rather than skipped: a fragment
# that is silently left behind is exactly the lost entry this tool exists to
# prevent. README.md and dotfiles are the only files that may live alongside.
_FRAGMENT_RE = re.compile(r"^(?P<id>[^.]+)\.(?P<section>[^.]+)\.md$")
_IGNORED = {"README.md"}


class Fragment(NamedTuple):
    path: Path
    id: str
    section: str
    body: str


def _id_key(fragment_id: str) -> Tuple[int, object]:
    return (0, int(fragment_id)) if fragment_id.isdigit() else (1, fragment_id)


def read_fragments(fragment_dir: Path) -> List[Fragment]:
    """Every fragment in *fragment_dir*, validated, in assembly order.

    Raises :class:`SystemExit` naming the offending file for anything that
    cannot be placed: a name without a section, a section the changelog does
    not use, an empty body, or a body that is not a bullet.
    """
    if not fragment_dir.is_dir():
        return []
    out: List[Fragment] = []
    for path in sorted(fragment_dir.iterdir()):
        if path.name.startswith(".") or path.name in _IGNORED or path.is_dir():
            continue
        m = _FRAGMENT_RE.match(path.name)
        if not m:
            raise SystemExit(
                f"{path}: not a changelog fragment — name it <id>.<section>.md, "
                f"with <section> one of {', '.join(SECTIONS)}"
            )
        section = m.group("section")
        if section not in _RANK:
            raise SystemExit(
                f"{path}: unknown section {section!r} — one of {', '.join(SECTIONS)}"
            )
        body = path.read_text(encoding="utf-8").strip("\n").rstrip()
        if not body.strip():
            raise SystemExit(f"{path}: empty fragment")
        if not body.startswith("- "):
            raise SystemExit(
                f"{path}: a fragment is the `- ` bullet(s) exactly as they will "
                "appear under the heading, not a note about them"
            )
        out.append(Fragment(path=path, id=m.group("id"), section=section, body=body))
    out.sort(key=lambda f: (_RANK[f.section], _id_key(f.id)))
    return out


def _heading(section: str) -> str:
    return f"### {section.capitalize()}"


def _section_of(line: str) -> Optional[str]:
    """The canonical section a ``### `` line names, or None for a bespoke one."""
    if not line.startswith("### "):
        return None
    name = line[4:].strip().lower()
    return name if name in _RANK else None


def _body_lines(fragments: Iterable[Fragment]) -> List[str]:
    text = "\n\n".join(f.body for f in fragments)
    return [ln + "\n" for ln in text.split("\n")]


def assemble_text(changelog: str, fragments: Sequence[Fragment]) -> str:
    """*changelog* with *fragments* placed under their headings in Unreleased.

    Only the first ``## [Unreleased]`` block is touched; a ``### Fixed`` under
    a released version is never a target. Within the block the first heading
    of a name wins (a hand-merged block can carry two ``### Added``). A
    missing heading is created before the first existing canonical heading
    that ranks after it, else at the end of the block.
    """
    lines = changelog.splitlines(keepends=True)
    try:
        start = next(i for i, ln in enumerate(lines) if ln.rstrip("\r\n") == UNRELEASED)
    except StopIteration:
        raise SystemExit(f"{CHANGELOG}: no `{UNRELEASED}` heading to assemble into")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    block = lines[start + 1:end]

    by_section = {}  # type: dict
    for f in fragments:
        by_section.setdefault(f.section, []).append(f)

    for section in SECTIONS:
        group = by_section.get(section)
        if not group:
            continue
        body = _body_lines(group)
        existing = next(
            (i for i, ln in enumerate(block) if _section_of(ln.rstrip("\r\n")) == section),
            None,
        )
        if existing is not None:
            block[existing + 1:existing + 1] = ["\n"] + body
            continue
        later = next(
            (
                i for i, ln in enumerate(block)
                if _section_of(ln.rstrip("\r\n")) is not None
                and _RANK[_section_of(ln.rstrip("\r\n"))] > _RANK[section]
            ),
            None,
        )
        if later is not None:
            block[later:later] = [_heading(section) + "\n", "\n"] + body + ["\n"]
        else:
            last = next((i for i in range(len(block) - 1, -1, -1) if block[i].strip()), None)
            at = 0 if last is None else last + 1
            block[at:at] = ["\n", _heading(section) + "\n", "\n"] + body

    return "".join(lines[:start + 1] + block + lines[end:])


def assemble(repo_root: Path, *, check: bool = False) -> List[Path]:
    """Assemble *repo_root*'s fragments into its changelog; return the paths used.

    With *check*, validate every fragment and the changelog's heading but
    write nothing and remove nothing. With no fragments the changelog is not
    opened for writing, so a release with none leaves it byte for byte.
    """
    repo_root = Path(repo_root)
    fragments = read_fragments(repo_root / FRAGMENT_DIR)
    if not fragments:
        return []
    changelog_path = repo_root / CHANGELOG
    text = changelog_path.read_text(encoding="utf-8")
    assembled = assemble_text(text, fragments)
    if check:
        return [f.path for f in fragments]
    changelog_path.write_text(assembled, encoding="utf-8")
    for f in fragments:
        f.path.unlink()
    return [f.path for f in fragments]


def main(argv: Optional[Sequence[str]] = None, *, repo_root: Optional[Path] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--check", action="store_true",
        help="validate the fragments and the changelog heading; write nothing",
    )
    parser.add_argument(
        "--fail-if-pending", action="store_true",
        help="the release guard: exit 1 while any fragment is still unassembled",
    )
    args = parser.parse_args(argv)
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parent.parent
    rel = f"{FRAGMENT_DIR}/"

    if args.fail_if_pending:
        pending = assemble(root, check=True)
        if pending:
            names = ", ".join(p.name for p in pending)
            print(
                f"{rel}{names}: still unassembled — run `python3 tools/assemble_changelog.py`, "
                f"commit {CHANGELOG}, and re-tag",
                file=sys.stderr,
            )
            raise SystemExit(1)
        print(f"{rel}: nothing pending")
        return 0

    used = assemble(root, check=args.check)
    if not used:
        print(f"{rel}: no fragments; {CHANGELOG} untouched")
    elif args.check:
        print(f"{rel}: {len(used)} fragment(s) valid: " + ", ".join(p.name for p in used))
    else:
        print(
            f"{CHANGELOG} {UNRELEASED} ← {len(used)} fragment(s): "
            + ", ".join(p.name for p in used)
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
