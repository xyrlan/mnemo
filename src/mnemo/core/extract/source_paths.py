"""Normalize rule ``source_files`` to vault-relative POSIX paths.

The scanner walks ``vault_root / "bots"`` with an absolute ``vault_root``, so
``str(mf.path)`` recorded machine-absolute source paths — brittle across vault
moves and inconsistent with the LLM-extracted sources, which are already
vault-relative. This module is the single chokepoint every write site routes
through.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def _as_parts(raw: str) -> list[str]:
    """Split a path string into segments, tolerating either separator.

    Sources may have been written on a different OS than the one reading them,
    so we can't rely on the local ``Path`` flavour to parse a stored string.
    """
    flavour = PureWindowsPath if "\\" in raw else PurePosixPath
    return [p for p in flavour(raw).parts if p not in ("", "/", "\\")]


def vault_relative_source(src: str | Path, vault_root: Path) -> str:
    """Return *src* as a vault-relative POSIX path.

    - Already-relative paths are returned unchanged (normalized to POSIX).
    - Absolute paths under *vault_root* are relativized against it.
    - Paths from a different vault location are relativized by their ``bots/``
      segment, so a source written under an old vault still resolves.
    - Anything with no ``bots/`` anchor and not under the vault is returned
      as-is: better a stable odd path than a wrong guess.
    """
    raw = str(src)
    try:
        candidate = Path(raw)
        if candidate.is_absolute():
            try:
                return candidate.relative_to(vault_root).as_posix()
            except ValueError:
                pass
    except (TypeError, ValueError):
        pass

    parts = _as_parts(raw)
    if "bots" in parts:
        idx = parts.index("bots")
        return PurePosixPath(*parts[idx:]).as_posix()

    # Relative and un-anchored — normalize separators, leave content alone.
    if not Path(raw).is_absolute():
        return PurePosixPath(*parts).as_posix() if parts else raw
    return raw


def normalize_sources(sources: list[str], vault_root: Path) -> list[str]:
    """Apply :func:`vault_relative_source` across a list, order-preserving."""
    return [vault_relative_source(s, vault_root) for s in sources]
