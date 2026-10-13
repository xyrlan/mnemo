"""The config seed the primary publishes for a machine that joins (#642).

``mnemo.config.json`` never travels: it names this machine's vault and may
name its paths and its keys. What travels is a seed — every key of the
primary's config that is neither — which ``mnemo sync join`` writes once into
the new machine's config. After that the two files are independent.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from mnemo.core.redact import find

# Keys that are this machine's and nobody else's.
_MACHINE_KEYS = (("vaultRoot",), ("sync", "machineId"))

# ``/x``, ``~/x``, ``C:\x``, ``\\server\share``, ``file:///x``.
_PATH = re.compile(r"^(?:~|/|\\\\|[A-Za-z]:[\\/]|file:)")

# A key that says its value is a credential. ``keyEnv`` is not one: it holds
# the *name* of an environment variable, which is exactly what should travel.
_SECRET_KEY = re.compile(
    r"(?i)(?:token|secret|passw(?:or)?d|passphrase|credential|api[_-]?key|private[_-]?key)s?$"
)


def _secret_shaped(key: str, value: str) -> bool:
    if _SECRET_KEY.search(key):
        return True
    return bool(find(value)) or bool(find(f"{key}: {value}"))


def _clean(node: Any, path: tuple) -> Any:
    """``node`` without paths and secrets; ``None`` means drop it."""
    if isinstance(node, dict):
        out: Dict[str, Any] = {}
        for key, value in node.items():
            here = path + (str(key),)
            if here in _MACHINE_KEYS:
                continue
            if isinstance(value, str) and _secret_shaped(str(key), value):
                continue
            kept = _clean(value, here)
            if kept is not None:
                out[str(key)] = kept
        # A section emptied by the filter says nothing; one that was empty
        # to begin with (``agent.overrides``) is the user's setting.
        return out if out or not node else None
    if isinstance(node, list):
        items = [_clean(v, path) for v in node]
        return [v for v in items if v is not None]
    if isinstance(node, str):
        if _PATH.match(node) or find(node):
            return None
    return node


def build_seed(raw: Dict[str, Any]) -> Dict[str, Any]:
    """The part of a config file that is safe and useful on another machine."""
    seed = _clean(raw, ())
    return seed if isinstance(seed, dict) else {}
