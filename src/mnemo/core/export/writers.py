"""Where an export lands and how the file is touched.

Two kinds of target. *Whole*: the file is nothing but the block (plus a
host prelude, e.g. Cursor's mandatory frontmatter) and is rewritten
wholesale. *Block*: the file belongs to the user (CLAUDE.md, AGENTS.md) and
only the text between the markers is ours — replaced if both markers exist,
appended if neither, refused if exactly one, so a half-deleted block never
silently swallows the user's own text.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.export.render import END_MARKER, START_MARKER


class TargetError(ValueError):
    """Host/target pair that does not exist."""


class MarkerError(ValueError):
    """Exactly one of the two markers is present; refusing to guess."""


@dataclass(frozen=True)
class Target:
    host: str
    name: str          # rules | claude-md | agents-md
    kind: str          # whole | block
    path: Path
    prelude: str = ""


_CURSOR_PRELUDE = "---\ndescription: Rules mnemo learned from you\nalwaysApply: true\n---\n"

_TARGETS = {
    ("claude", "rules"): ("whole", Path(".claude") / "rules" / "mnemo.md", ""),
    ("claude", "claude-md"): ("block", Path("CLAUDE.md"), ""),
    ("cursor", "rules"): ("whole", Path(".cursor") / "rules" / "mnemo.mdc", _CURSOR_PRELUDE),
    ("codex", "agents-md"): ("block", Path("AGENTS.md"), ""),
}
_AUTO = {"claude": "rules", "cursor": "rules", "codex": "agents-md"}


def target_for(host: str, target: str, cwd: Path) -> Target:
    if host not in _AUTO:
        raise TargetError(f"unknown host {host!r}")
    name = _AUTO[host] if target == "auto" else target
    spec = _TARGETS.get((host, name))
    if spec is None:
        raise TargetError(f"--target {name} is not a {host} target")
    kind, rel, prelude = spec
    return Target(host=host, name=name, kind=kind, path=Path(cwd) / rel, prelude=prelude)


def _span(text: str) -> Tuple[Optional[int], Optional[int]]:
    """(start offset, offset just past the end marker's line) or Nones."""
    s = text.find(START_MARKER)
    e = text.find(END_MARKER)
    if s == -1 and e == -1:
        return None, None
    if s == -1 or e == -1 or e < s:
        raise MarkerError("found one mnemo marker but not the other; fix the file by hand")
    end = e + len(END_MARKER)
    if text[end:end + 1] == "\n":
        end += 1
    return s, end


def replace_block(text: str, block: str) -> str:
    s, e = _span(text)
    if s is None:
        if not text:
            return block
        sep = "" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        return text + sep + block
    return text[:s] + block + text[e:]


def strip_block(text: str) -> Optional[str]:
    s, e = _span(text)
    if s is None:
        return None
    before, after = text[:s], text[e:]
    if after.startswith("\n"):
        after = after[1:]
    elif not after:
        # Nothing follows the block: also drop the blank-line separator
        # replace_block would have inserted before it, so a strip undoes
        # exactly what an append did.
        before = before.rstrip("\n")
        if before:
            before += "\n"
    return before + after


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def write_target(target: Target, block: str) -> None:
    if target.kind == "whole":
        data = target.prelude + block
    else:
        data = replace_block(_read(target.path), block)
    atomic_write_bytes(target.path, data.encode("utf-8"))


def remove_target(target: Target) -> bool:
    """True when something was removed."""
    if target.kind == "whole":
        if not target.path.exists():
            return False
        target.path.unlink()
        return True
    if not target.path.exists():
        return False
    stripped = strip_block(_read(target.path))
    if stripped is None:
        return False
    atomic_write_bytes(target.path, stripped.encode("utf-8"))
    return True
