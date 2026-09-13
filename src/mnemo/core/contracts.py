"""The contract a decomposition produces: the pieces, and the boundary between.

A contract file is markdown with frontmatter and one ``##`` section per piece.
It is the only durable artifact of a decomposition — dispatch reads it, and the
maintainer reviews it before anything is spawned.

Parsing is strict on purpose. The file is written by a model and reviewed by a
human, so a shape this module does not recognise is far more likely to be a
mistake than a dialect worth tolerating. Every refusal happens here, before
:mod:`mnemo.core.dispatch` creates any git state.

Frontmatter is read with :func:`mnemo.core.filters.parse_frontmatter` rather
than a fresh regex: mnemo already has one reader for the shape it writes, and a
second one drifts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from mnemo.core.filters import parse_frontmatter

# A slug must survive being a directory name and a branch segment, so it is
# constrained to what `-wt-c-<slug>` can express. Enforced at parse time so a
# contract can never name a piece the addressing scheme cannot address.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_FIELD_RE = re.compile(r"^-\s+\*\*(files|exposes|consumes)\:\*\*\s*(.*)$")
# "`sig` from owner" — the signature keeps its backticks (it is quoted
# verbatim so a later diff of the contract is meaningful, and Task 5 quotes it
# into a child's prompt as literal text). The owner is a lookup key rather
# than displayed text, so its backticks are optional and stripped when
# present: a model or human writing the contract by hand has no reason to
# know that quoting a piece slug changes whether it resolves.
_FROM_RE = re.compile(r"^(.*?)\s+from\s+`?(.+?)`?$")

# One `exposes`/`consumes` entry: a backtick-quoted signature, optionally
# followed by " from <owner>" (owner backticks optional, see `_FROM_RE`).
# Matched instead of split on "," because a signature's own argument list
# routinely contains commas ("`merge(a, b) -> T`"), and those are not list
# separators. Anchored on the opening/closing backtick pair so a comma inside
# them can never be mistaken for one between two items.
_SIG_ITEM_RE = re.compile(r"`[^`]*`(?:\s+from\s+`?[^,`]+`?)?")


class ContractError(ValueError):
    """A contract file could not be read, or read but not trusted.

    Raised before any git state exists, so catching it means nothing was
    created and nothing needs cleaning up.
    """


@dataclass(frozen=True)
class Piece:
    """One unit of parallel work: a boundary, plus what crosses it."""

    slug: str
    files: list[str] = field(default_factory=list)
    exposes: list[str] = field(default_factory=list)
    # (signature, owning piece slug) — the owner is validated, not decorative.
    consumes: list[tuple[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class Contract:
    feature: str
    verdict: str
    pieces: list[Piece] = field(default_factory=list)
    path: Path | None = None

    @property
    def is_dispatchable(self) -> bool:
        """``sequential`` is a valid verdict that must not spawn anything."""
        return self.verdict == "parallel"


def _is_empty_list(cleaned: str) -> bool:
    return not cleaned or cleaned.lower() in {"nothing", "none", "-"}


def _split_list(value: str) -> list[str]:
    """``files``: ``a, b , c`` -> ``["a", "b", "c"]``; ``nothing``/`` `` -> ``[]``.

    A path never contains a comma, so a plain split is correct here. This is
    NOT used for ``exposes``/``consumes`` — see :func:`_split_signatures`,
    which those fields need because a signature's own arguments do contain
    commas.
    """
    cleaned = value.strip()
    if _is_empty_list(cleaned):
        return []
    return [part.strip() for part in cleaned.split(",") if part.strip()]


def _split_signatures(value: str) -> list[str]:
    """``exposes``/``consumes``: split on signatures, not on every comma.

    ``exposes``/``consumes`` hold backtick-quoted signatures
    (`` `merge(a, b) -> T` ``), and a multi-argument signature's own commas
    are not list separators. Splitting naively on "," tears such a signature
    in half — silently, since the halves are still valid-looking strings — and
    that corruption would then be quoted verbatim into a child's prompt
    (Task 5). So each item is matched as a backtick span instead, with any
    trailing " from <owner>" folded into the same item.

    Falls back to the plain comma split when there are no backticks at all,
    since a contract written without them has no commas-in-signatures problem
    to protect against, and still deserves multi-item support.
    """
    cleaned = value.strip()
    if _is_empty_list(cleaned):
        return []
    if "`" not in cleaned:
        return _split_list(cleaned)
    return [m.group(0).strip() for m in _SIG_ITEM_RE.finditer(cleaned)]


def _validate(contract: Contract) -> None:
    """Refuse a contract that would dispatch children against a bad boundary.

    Every check runs against the whole file before the caller creates any
    worktree, so a contract is either entirely dispatchable or entirely
    refused — never half-spawned.

    Deliberately does not check that the owner actually *exposes* the consumed
    signature. The two are written by hand and differ cosmetically — a space, a
    backtick, a renamed argument — so string equality would refuse well-formed
    contracts over formatting. Whether the boundary was real is measured after
    the fact, by whether the children collided, not asserted before it.
    """
    if contract.verdict not in {"parallel", "sequential"}:
        raise ContractError(
            f"verdict must be 'parallel' or 'sequential', got {contract.verdict!r}"
        )
    if not contract.pieces:
        raise ContractError("no pieces: a contract names at least one")

    seen: set[str] = set()
    for piece in contract.pieces:
        if not SLUG_RE.match(piece.slug):
            raise ContractError(
                f"slug {piece.slug!r} is not addressable: "
                "use lowercase letters, digits and hyphens"
            )
        if piece.slug in seen:
            raise ContractError(f"duplicate piece slug {piece.slug!r}")
        seen.add(piece.slug)
        if not piece.files:
            raise ContractError(f"piece {piece.slug!r} declares no files boundary")

    for piece in contract.pieces:
        for signature, owner in piece.consumes:
            if owner == piece.slug:
                raise ContractError(
                    f"piece {piece.slug!r} consumes {signature} from itself: "
                    "a piece's own work is not a boundary it can depend on"
                )
            if owner not in seen:
                raise ContractError(
                    f"piece {piece.slug!r} consumes {signature} from unknown "
                    f"piece {owner!r}"
                )


def parse_contract(path: Path | str) -> Contract:
    """Read a contract file into a :class:`Contract`, or refuse."""
    target = Path(path)
    try:
        text = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # UnicodeDecodeError is not an OSError, but it is exactly as much a
        # "could not read this file" failure as a missing-file OSError — the
        # CLI layer (Task 7) needs every refusal to arrive as ContractError
        # so it can promise the caller that no git state was created.
        raise ContractError(f"cannot read contract {target}: {exc}") from exc

    meta = parse_frontmatter(text)
    feature = str(meta.get("feature") or "").strip()
    verdict = str(meta.get("verdict") or "").strip()

    pieces: list[Piece] = []
    slug: str | None = None
    files: list[str] = []
    exposes: list[str] = []
    consumes: list[tuple[str, str]] = []

    def flush() -> None:
        # Bind the current accumulator values now, not the names — a
        # `list.append` further down would otherwise be seen by every
        # `Piece` already flushed, since Python closures capture variables,
        # not values. Passing the lists into `Piece(...)` directly (as
        # opposed to storing them first in a local) does the binding at
        # call time, so this is actually safe; the guard is left explicit
        # so a future refactor doesn't reintroduce the classic mutable-
        # default/closure trap by accident.
        if slug is not None:
            pieces.append(
                Piece(slug=slug, files=files, exposes=exposes, consumes=consumes)
            )

    for line in text.splitlines():
        heading = _HEADING_RE.match(line)
        if heading:
            flush()
            slug = heading.group(1).strip()
            files, exposes, consumes = [], [], []
            continue
        matched = _FIELD_RE.match(line)
        if not matched or slug is None:
            continue
        key, value = matched.group(1), matched.group(2)
        if key == "files":
            files = _split_list(value)
        elif key == "exposes":
            exposes = _split_signatures(value)
        else:
            for item in _split_signatures(value):
                owner = _FROM_RE.match(item)
                if owner:
                    consumes.append((owner.group(1).strip(), owner.group(2).strip()))
                else:
                    consumes.append((item, ""))
    flush()

    contract = Contract(feature=feature, verdict=verdict, pieces=pieces, path=target)
    _validate(contract)
    return contract
