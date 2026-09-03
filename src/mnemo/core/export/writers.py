"""Where an export lands and how the file is touched.

Two kinds of target. *Whole*: the file is nothing but the block (plus a
host prelude, e.g. Cursor's mandatory frontmatter) and is rewritten
wholesale. *Block*: the file belongs to the user (CLAUDE.md, AGENTS.md) and
only the text between the markers is ours — replaced if both markers exist,
appended if neither, refused if exactly one, so a half-deleted block never
silently swallows the user's own text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from mnemo.core.atomic import atomic_write_bytes
from mnemo.core.export.render import END_MARKER, START_MARKER

# Markers must anchor the start of a line: a marker-like sequence quoted or
# indented inside the user's own prose (a fenced snippet in CLAUDE.md, a
# mid-sentence mention) must never be mistaken for a real mnemo block.
_START_RE = re.compile(r"^" + re.escape(START_MARKER), re.M)
_END_RE = re.compile(r"^" + re.escape(END_MARKER) + r"[ \t]*\r?$", re.M)


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
    starts = list(_START_RE.finditer(text))
    ends = list(_END_RE.finditer(text))
    if not starts and not ends:
        return None, None
    if len(starts) > 1 or len(ends) > 1:
        raise MarkerError("more than one mnemo block in the file; remove the extra one by hand")
    if len(starts) != 1 or len(ends) != 1 or ends[0].start() < starts[0].start():
        raise MarkerError("found one mnemo marker but not the other; fix the file by hand")
    s = starts[0].start()
    end = ends[0].end()
    if text[end:end + 2] == "\r\n":
        end += 2
    elif text[end:end + 1] == "\n":
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
    elif not after and before.endswith("\n\n"):
        # Nothing follows the block: drop at most the one separator newline
        # replace_block may have inserted before it. Not a full rstrip — the
        # user's own blank lines before that point are left alone.
        before = before[:-1]
    return before + after


def _read(path: Path) -> str:
    # We write the file back below, so a lossy decode (errors="replace")
    # would corrupt it; fail loudly instead of silently mangling bytes we
    # don't understand. newline="" disables universal-newline translation so
    # a CRLF file's line endings survive the round trip untouched.
    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError:
        raise TargetError(f"{path} is not valid UTF-8; mnemo export cannot safely rewrite it")
    except OSError as exc:
        raise TargetError(f"cannot read {path}: {exc}")


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
    if not stripped:
        # The block was the whole file; leave nothing behind rather than an
        # empty file.
        target.path.unlink()
        return True
    atomic_write_bytes(target.path, stripped.encode("utf-8"))
    return True
