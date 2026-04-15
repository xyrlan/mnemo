"""Claude Code statusLine renderer + additive composer (v0.5).

Two pieces:

- :func:`render` — emits the mnemo segment, e.g. ``mnemo mcp · 9 topics · 7↓ today``.
  Returns an empty string when the MCP server is not registered in
  ``~/.claude.json`` (so the status line silently disappears for users who
  haven't run ``mnemo init``).
- :func:`compose` — the entry point referenced from ``settings.json``. Reads
  the user's *original* statusLine command from ``<vault>/.mnemo/statusline-original.json``,
  runs both it and ``render``, concatenates with `` · ``, and prints. This is
  how mnemo coexists with a user's pre-existing status line: additive, not
  replacement. Restoration on ``mnemo uninstall`` is clean because the
  original command was preserved in mnemo state, never lost.

Performance: ``render`` scans ``shared/<type>/`` to count topics. For typical
vaults (<100 pages) this is well under 50ms, comfortable for a status line
that refreshes on demand. If a vault grows past a few thousand pages we'll
revisit with a cached count.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

STATE_FILENAME = "statusline-original.json"
SEPARATOR = " · "
COMPOSER_TIMEOUT_S = 2.0


def _state_path(vault_root: Path) -> Path:
    return vault_root / ".mnemo" / STATE_FILENAME


def _mcp_registered(claude_json_path: Path) -> bool:
    try:
        data = json.loads(claude_json_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers") or {}
    return isinstance(servers, dict) and "mnemo" in servers


def _count_today_denials(vault_root: Path) -> int:
    """Count denial-log entries from today UTC. Reads at most the last 1000 lines.

    Returns 0 on any error (file missing, malformed, etc.).
    """
    import json as _json
    from datetime import date, timezone as _tz

    try:
        log_path = vault_root / ".mnemo" / "denial-log.jsonl"
        if not log_path.exists():
            return 0
        # Read last 1000 lines for speed
        text = log_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > 1000:
            lines = lines[-1000:]
        today_prefix = date.today().strftime("%Y-%m-%d")
        count = 0
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                ts = entry.get("timestamp", "")
                if isinstance(ts, str) and ts.startswith(today_prefix):
                    count += 1
            except (_json.JSONDecodeError, ValueError):
                continue
        return count
    except Exception:
        return 0


def _activation_segments(vault_root: Path, cwd: str | None) -> list[str]:
    """Build per-project activation statusline segments.

    Returns an empty list when:
    - both enforcement and enrichment are disabled, OR
    - the activation index is absent.

    Never raises.
    """
    try:
        from mnemo.core import config as cfg_mod
        from mnemo.core.rule_activation import load_index

        cfg = cfg_mod.load_config()
        enforce_enabled = bool((cfg.get("enforcement") or {}).get("enabled", False))
        enrich_enabled = bool((cfg.get("enrichment") or {}).get("enabled", False))

        if not enforce_enabled and not enrich_enabled:
            return []

        index = load_index(vault_root)
        if index is None:
            return []

        # Determine current project from cwd
        from mnemo.core.agent import resolve_agent
        effective_cwd = cwd or str(Path.cwd())
        agent = resolve_agent(effective_cwd)
        project = agent.name

        enforce_rules = index.get("enforce_by_project", {}).get(project, [])
        enrich_rules = index.get("enrich_by_project", {}).get(project, [])

        n_enforce = len(enforce_rules)
        n_enrich = len(enrich_rules)
        n_blocks = _count_today_denials(vault_root)

        parts: list[str] = []
        if enforce_enabled and n_enforce > 0:
            parts.append(f"{n_enforce}\u26d4 rules")
        if enforce_enabled and n_blocks > 0:
            parts.append(f"{n_blocks} blocks")
        if enrich_enabled and n_enrich > 0:
            parts.append(f"{n_enrich}\U0001f4a1 active")
        return parts
    except Exception:
        return []


def render(vault_root: Path, claude_json_path: Path, *, cwd: str | None = None) -> str:
    """Return the mnemo statusline segment, or '' when nothing should show."""
    if not _mcp_registered(claude_json_path):
        return ""
    try:
        from mnemo.core.mcp.counter import read_today
        from mnemo.core.mcp.tools import get_mnemo_topics

        topics = get_mnemo_topics(vault_root)
        count = read_today(vault_root)
    except Exception:
        return ""

    parts = [f"mnemo mcp · {len(topics)} topics · {count}↓ today"]

    activation = _activation_segments(vault_root, cwd)
    parts.extend(activation)

    return SEPARATOR.join(parts)


def _run_original(original_cmd: str | None) -> str:
    """Execute the user's original statusLine command and return its stdout.

    ``shell=True`` is intentional: the original command is whatever the user
    had previously trusted Claude Code to run. We're not constructing a
    command from untrusted input — we're re-running an existing one.

    Crashes are swallowed so the mnemo segment still gets a chance to show.
    """
    if not original_cmd:
        return ""
    try:
        proc = subprocess.run(
            original_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=COMPOSER_TIMEOUT_S,
        )
        return (proc.stdout or "").strip()
    except (subprocess.SubprocessError, OSError):
        return ""


def compose(out: object = None) -> int:
    """Read state, run original + mnemo, print concatenated result."""
    out_stream = out if out is not None else sys.stdout
    try:
        from mnemo.core import config as cfg_mod
        from mnemo.core import paths as paths_mod

        cfg = cfg_mod.load_config()
        vault = paths_mod.vault_root(cfg)
    except Exception:
        out_stream.write("")
        return 0

    parts: list[str] = []

    state = _read_state(vault)
    original_segment = _run_original(state.get("command") if state else None)
    if original_segment:
        parts.append(original_segment)

    claude_json = Path(os.path.expanduser("~/.claude.json"))
    mnemo_segment = render(vault, claude_json)
    if mnemo_segment:
        parts.append(mnemo_segment)

    out_stream.write(SEPARATOR.join(parts))
    return 0


def _read_state(vault_root: Path) -> dict[str, Any] | None:
    try:
        text = _state_path(vault_root).read_text(encoding="utf-8")
        data = json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def write_state(vault_root: Path, original: dict[str, Any] | None) -> None:
    """Persist the user's original statusLine spec for later restoration.

    ``original`` may be ``None`` (no pre-existing statusLine — restore means
    "remove the key entirely") or a dict with at least a ``command`` field.
    """
    path = _state_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if original is None:
        payload = {"command": None}
    else:
        payload = {
            "command": original.get("command"),
            "type": original.get("type") or "command",
        }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_state(vault_root: Path) -> dict[str, Any] | None:
    """Public alias for the state reader (used by uninject_statusline)."""
    return _read_state(vault_root)


def clear_state(vault_root: Path) -> None:
    """Remove the state file, used during uninstall."""
    try:
        _state_path(vault_root).unlink()
    except (FileNotFoundError, OSError):
        pass
