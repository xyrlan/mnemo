"""Idempotent vault scaffolding."""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

DIRS = [
    "bots",
    "shared/people",
    "shared/companies",
    "shared/projects",
    "shared/decisions",
    "wiki/sources",
    "wiki/compiled",
    ".obsidian/snippets",
]

TEMPLATE_FILES = {
    "HOME.md": "HOME.md",
    "README.md": "README.md",
    ".obsidian/snippets/graph-dark-gold.css": "graph-dark-gold.css",
}


def _read_template(name: str) -> str:
    return resources.files("mnemo.templates").joinpath(name).read_text(encoding="utf-8")


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
