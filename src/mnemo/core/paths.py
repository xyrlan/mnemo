# src/mnemo/core/paths.py
"""Vault path resolution helpers."""
from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any


def vault_root(cfg: dict[str, Any]) -> Path:
    return Path(os.path.expanduser(cfg.get("vaultRoot", "~/mnemo")))


def bots_dir(cfg: dict[str, Any]) -> Path:
    return vault_root(cfg) / "bots"


def agent_dir(cfg: dict[str, Any], agent: str) -> Path:
    return bots_dir(cfg) / agent


def logs_dir(cfg: dict[str, Any], agent: str) -> Path:
    return agent_dir(cfg, agent) / "logs"


def memory_dir(cfg: dict[str, Any], agent: str) -> Path:
    return agent_dir(cfg, agent) / "memory"


def working_dir(cfg: dict[str, Any], agent: str) -> Path:
    return agent_dir(cfg, agent) / "working"


def today_log(cfg: dict[str, Any], agent: str) -> Path:
    return logs_dir(cfg, agent) / f"{date.today().isoformat()}.md"


def errors_log(cfg: dict[str, Any]) -> Path:
    return vault_root(cfg) / ".errors.log"


def ensure_writeable(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
