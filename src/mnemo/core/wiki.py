# src/mnemo/core/wiki.py
"""Promote and compile wiki content."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from mnemo.core import paths

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _split_frontmatter(text: str) -> tuple[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():]


def _merge_frontmatter(existing: str, additions: dict[str, str]) -> str:
    lines = [l for l in existing.splitlines() if l.strip()]
    keys = {l.split(":", 1)[0].strip() for l in lines if ":" in l}
    for k, v in additions.items():
        if k not in keys:
            lines.append(f"{k}: {v}")
    return "\n".join(lines)


def promote_note(source: Path, cfg: dict[str, Any]) -> Path:
    source = Path(source)
    text = source.read_text()
    fm, body = _split_frontmatter(text)
    additions = {
        "origin": str(source),
        "promoted_at": datetime.now().isoformat(timespec="seconds"),
    }
    merged_fm = _merge_frontmatter(fm, additions)
    new_text = f"---\n{merged_fm}\n---\n{body}"
    out_dir = paths.vault_root(cfg) / "wiki" / "sources"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / source.name
    out_path.write_text(new_text)
    return out_path


def compile_wiki(cfg: dict[str, Any]) -> Path:
    sources = paths.vault_root(cfg) / "wiki" / "sources"
    compiled = paths.vault_root(cfg) / "wiki" / "compiled"
    compiled.mkdir(parents=True, exist_ok=True)
    sources.mkdir(parents=True, exist_ok=True)
    index_lines = ["---", "tags: [wiki, index]", "---", "# Wiki", ""]
    entries = sorted(p for p in sources.iterdir() if p.is_file() and p.suffix == ".md")
    for src in entries:
        target = compiled / src.name
        target.write_text(src.read_text())
        stem = src.stem
        index_lines.append(f"- [[{stem}]]")
    (compiled / "index.md").write_text("\n".join(index_lines) + "\n")
    return compiled / "index.md"
