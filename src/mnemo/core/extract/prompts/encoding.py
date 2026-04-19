"""Memory-file chunking + encoding helpers used by every consolidation prompt.

Extracted verbatim from the legacy ``mnemo.core.extract.prompts``
monolith in v0.9 PR F2.
"""
from __future__ import annotations

from typing import Iterator

from mnemo.core.extract.scanner import MemoryFile


def chunks_for(files: list[MemoryFile], chunk_size: int) -> Iterator[list[MemoryFile]]:
    for i in range(0, len(files), chunk_size):
        yield files[i:i + chunk_size]


def _encode_file(f: MemoryFile) -> str:
    fm_lines = [f"{k}: {v}" for k, v in f.frontmatter.items()]
    fm_block = "\n".join(fm_lines) if fm_lines else f"type: {f.type}"
    return (
        f"<<<FILE: {f.path}>>>\n"
        f"---\n{fm_block}\n---\n"
        f"{f.body}\n"
        f"<<<END>>>\n"
    )


def _render_files(files: list[MemoryFile]) -> str:
    return "\n".join(_encode_file(f) for f in files)
