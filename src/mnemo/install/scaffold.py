"""Idempotent vault scaffolding."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

DIRS = [
    "bots",
    # Auto-populated Tier 2 — `mnemo extract` writes here. Type names match
    # scanner._VALID_TYPES; `shared/project/` is singular to match promote.py.
    "shared/feedback",
    "shared/user",
    "shared/reference",
    "shared/project",
    ".obsidian/snippets",
]

# Written only when absent, so a user's own tweaks survive. This matters more
# for the ``.obsidian/*.json`` entries than the rest: Obsidian rewrites them
# whenever a setting changes, and scaffolding runs on every SessionStart.
TEMPLATE_FILES = {
    "HOME.md": "HOME.md",
    "README.md": "README.md",
    ".obsidian/snippets/graph-dark-gold.css": "graph-dark-gold.css",
    # Obsidian's own defaults render a mnemo vault as an unreadable hairball:
    # orphans shown (the `_archive`/`logs` ring), no exclusions, and no color
    # groups, so the type structure mnemo maintains is invisible. The graph is
    # the first thing a new user opens — ship it configured, not just themed.
    ".obsidian/graph.json": "graph.json",
    # ``shared/_archive/`` (reclassify originals) and ``bots/*/logs/`` are not
    # rules; ``core.filters.iter_shared_pages`` skips them everywhere. Obsidian
    # has no way to know that, so tell it: ``userIgnoreFilters`` drops them from
    # search, graph and the quick switcher while leaving the files on disk.
    ".obsidian/app.json": "app.json",
}


def _read_template(name: str) -> str:
    try:
        return (resources.files("mnemo.templates") / name).read_text(encoding="utf-8")
    except AttributeError:
        # Python 3.8 fallback — resources.files() added in 3.9
        return resources.read_text("mnemo.templates", name, encoding="utf-8")


def scaffold_vault(vault_root: Path) -> None:
    vault_root = Path(vault_root)
    vault_root.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (vault_root / d).mkdir(parents=True, exist_ok=True)
    for rel, template_name in TEMPLATE_FILES.items():
        target = vault_root / rel
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_read_template(template_name), encoding="utf-8")
    cfg_path = vault_root / "mnemo.config.json"
    if not cfg_path.exists():
        cfg = json.loads(_read_template("mnemo.config.json"))
        cfg["vaultRoot"] = str(vault_root)
        cfg_path.write_text(json.dumps(cfg, indent=2))
