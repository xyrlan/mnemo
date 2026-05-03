"""Filesystem layout for autopilot state.

Single source of truth for paths under ``<vault_root>/.mnemo/``. Every
core module imports from here so we never hardcode ``.mnemo/...`` strings
in business logic.
"""
from __future__ import annotations

from pathlib import Path


def autopilot_dir(vault_root: Path) -> Path:
    return Path(vault_root) / ".mnemo"


def proposals_dir(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "proposals"


def autopilot_state_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "autopilot.json"


def autopilot_budget_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "autopilot-budget.json"


def frozen_recall_path(vault_root: Path) -> Path:
    return autopilot_dir(vault_root) / "recall-cases.frozen.json"


def ensure_proposals_dir(vault_root: Path) -> Path:
    p = proposals_dir(vault_root)
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_autopilot_dir(vault_root: Path) -> Path:
    p = autopilot_dir(vault_root)
    p.mkdir(parents=True, exist_ok=True)
    return p
