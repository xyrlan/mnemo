"""Machine-local secrets, outside the vault and outside the config (#406).

`recall.rerank` needs an API key, and the process that needs it is the MCP
server — which Claude Code spawns, not the user's shell. An `export` in
`.zshrc` reaches that server only when `claude` itself was started from that
shell, and an app launched from the Dock has no shell environment at all
(mnemo-desktop PR #55). So the environment variable cannot be the only source.

Not the config file and not the vault: `mnemo.config.json` lives *inside* the
vault, the vault may be a git repo or a synced folder, and a repo-local config
is committed. This file lives at `~/.mnemo/secrets.json` instead —
`MNEMO_SECRETS_PATH` overrides it, which is how tests reach it without
patching `HOME` (`ntpath.expanduser` ignores `HOME` and reads `USERPROFILE`).

Shape, one section per feature and one entry per provider inside it::

    {"recall.rerank": {"typesafe": "<key>"}}

Nothing here raises. :func:`read` is called from the MCP path, where a
malformed or unreadable file must cost the rerank stage its key and nothing
else: the caller falls back exactly as it does when no key exists at all.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

#: The one section that exists today. A second feature wanting a key adds a
#: section, not a file.
RERANK_SECTION = "recall.rerank"

_ENV_PATH = "MNEMO_SECRETS_PATH"
_DEFAULT_PATH = "~/.mnemo/secrets.json"

#: Owner-only, on the platforms that have modes. Windows has none and this is
#: a no-op there — which is said plainly rather than implied away.
_FILE_MODE = 0o600
_DIR_MODE = 0o700


def path() -> Path:
    """Where the secrets file lives, override first."""
    override = os.environ.get(_ENV_PATH)
    if override:
        return Path(override)
    return Path(os.path.expanduser(_DEFAULT_PATH))


def _load() -> Dict[str, Any]:
    """The whole file as a dict, or ``{}`` for every way it can fail."""
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def read(provider: str, *, section: str = RERANK_SECTION) -> Optional[str]:
    """That provider's key, or ``None``.

    ``None`` for a missing file, an unreadable one, one that is not JSON, one
    whose section or entry is missing, and one whose entry is not a non-empty
    string. The caller cannot tell those apart on purpose: every one of them
    means "no key here", and the stage degrades the same way for all of them.
    """
    block = _load().get(section)
    if not isinstance(block, dict):
        return None
    value = block.get(provider)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _save(data: Dict[str, Any]) -> None:
    """Replace the file with *data*, never widening its mode.

    Written to a sibling created at ``0600`` and renamed over the target: an
    ``os.open`` onto an existing path ignores its mode argument, so writing in
    place would leave a file someone had already made world-readable exactly
    as it was. The rename is atomic, so a reader never sees half a file.
    """
    target = path()
    os.makedirs(str(target.parent), mode=_DIR_MODE, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _FILE_MODE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2) + "\n")
    except Exception:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
    os.replace(str(tmp), str(target))


def write(provider: str, key: str, *, section: str = RERANK_SECTION) -> Path:
    """Store *key* under *provider*, keeping every other entry the file holds.

    Returns the path written, so a caller can name it without recomputing it.
    Unlike :func:`read` this does raise: it is called from a CLI command where
    a failed write must not be reported as a success.
    """
    data = _load()
    block = data.get(section)
    if not isinstance(block, dict):
        block = {}
    block[provider] = key
    data[section] = block
    _save(data)
    return path()


def remove(provider: str, *, section: str = RERANK_SECTION) -> bool:
    """Drop that provider's entry. True when something was removed.

    An empty section is removed with it, and a file left holding nothing is
    unlinked: turning the feature off should leave no key on the machine, not
    an empty envelope suggesting there is one.
    """
    data = _load()
    block = data.get(section)
    if not isinstance(block, dict) or provider not in block:
        return False
    del block[provider]
    if block:
        data[section] = block
    else:
        data.pop(section, None)
    if data:
        _save(data)
    else:
        try:
            os.unlink(str(path()))
        except OSError:
            pass
    return True
