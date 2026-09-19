# src/mnemo/core/config.py
"""Config loading with defaults and forward-compat preservation."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "vaultRoot": "~/mnemo",
    "capture": {
        "sessionStartEnd": True,
    },
    "agent": {
        "strategy": "git-root",
        "overrides": {},
    },
    "extraction": {
        # #358: which provider answers the model calls (``core.llm.PROVIDERS``).
        "provider": "claude-cli",
        "model": "claude-haiku-4-5",
        "chunkSize": 10,
        "subprocessTimeout": 60,
        "costSoftCap": None,
        "auto": {
            "enabled": True,
            "minNewMemories": 1,
            "minIntervalMinutes": 60,
        },
    },
    "dispatch": {
        # #357: when a dispatched child exits, tell the session that spawned
        # it, over that session's own inbox socket. On by default because the
        # alternative it replaces is the maintainer relaying it by hand, and
        # the notice is one line that carries no authority (it opens with a
        # marker the unblock detector skips). Turn it off if a session being
        # poked mid-thought by an unrelated child is worse than waiting.
        # Delivery is best-effort: a parent that has exited is not queued for.
        "notifyParent": True,
    },
    "resume": {
        # #396: wake a rate-limited child once its own reset has passed,
        # without anyone typing `mnemo resume`. On by default because it
        # grants nothing and publishes nothing — the child carries on with the
        # prompt it already had — and because the alternative it replaces is
        # six children idling until somebody notices. Only a stall carrying a
        # `quotaLimits.resetsAt` is ever woken, at most once per reset window.
        # Turn `auto` off if an account window being spent while you are not
        # watching is worse than children idling until you are.
        "auto": True,
        "maxPerPass": 5,
    },
    "procedures": {
        # #392: what counts as a procedure children keep rediscovering.
        # ``minChildren`` is the bar for "keep" — one child working something
        # out is a child, two is the repo. ``maxShapeRepos`` is what keeps the
        # harness's own commands out: a shape children of more than this many
        # repos run is ``git log``, not this repo's way of running work.
        "minChildren": 2,
        "maxShapeRepos": 2,
    },
    "briefings": {
        "enabled": True,
        "injectLastOnSessionStart": True,
        # #116: briefings are written once per session and never pruned.
        # retentionDays=0 disables pruning; keepPerAgent newest always survive.
        "retentionDays": 180,
        "keepPerAgent": 20,
    },
    "backfill": {
        "enabled": True,
        "installCap": 20,
        "minFileMutations": 1,
        "autoOnFirstSession": False,
    },
    "injection": {
        "enabled": True,
        "maxTopicsPerScope": 15,
        "telemetry": {
            "enabled": True,
            "log": {"maxBytes": 1_048_576},
        },
    },
    "enforcement": {
        # v0.5: enabled by default. The PreToolUse hook is fail-open at every
        # stage and only acts on rules that survive the consumer-visible gate
        # in build_index, so the worst-case impact of a misconfigured rule is
        # an unblocked tool call — never a broken Claude Code session.
        "enabled": True,
        "log": {"maxBytes": 1_048_576},
    },
    "enrichment": {
        "enabled": True,
        "maxRulesPerCall": 3,
        "bodyPreviewChars": 300,
        "maxEmissionsPerSession": 15,
        "log": {"maxBytes": 1_048_576},
    },
    "scoping": {
        "universalThreshold": 2,
    },
    "inbox": {
        # #380: what waits in shared/_inbox/ reaches the maintainer at session
        # start instead of only when they think to run `mnemo doctor`. The
        # three numbers below are the whole noise budget — a block of at most
        # `offerMax` bullets, at most one per project per `offerIntervalHours`,
        # and no page repeated inside `offerCooldownDays`. Set
        # `offerOnSessionStart` false to keep the queue silent; `mnemo inbox`
        # still lists it.
        "offerOnSessionStart": True,
        "offerMax": 2,
        "offerCooldownDays": 7,
        "offerIntervalHours": 24,
    },
    "install": {
        # #337: a matcher this version widened reaches an install that already
        # exists only through `inject_hooks` — `mnemo init` writes each one
        # once. Session start repairs the drift it finds, once per distinct
        # drift, so a shipped fix lands without the user having thought to run
        # `mnemo doctor`. Set false to keep a matcher you narrowed by hand;
        # `doctor` still reports the drift either way.
        "autoRepairHooks": True,
    },
    "doctor": {
        # Set ``skipStatuslineDrift`` to true to silence the statusLine
        # drift warning when you've intentionally reverted to the default
        # Claude Code statusLine (e.g., after uninstalling a custom composer
        # but keeping the mnemo state file). Default false: drift is loud.
        "skipStatuslineDrift": False,
    },
    "autopilot": {
        # Nothing mnemo does reaches the network unless this is on: no
        # `gh issue create`, no self-fix PRs, no outcome polling. Local
        # maintenance (indexes, sweep, calibration) is unaffected.
        "network": {"enabled": False},
    },
    "reflex": {
        "enabled": True,  # v0.8.0 stable — flip to False in mnemo.config.json to disable
        "maxEmissionsPerSession": 10,
        "thresholds": {
            "termOverlapMin": 2,
            "relativeGap": 1.0,
            "absoluteFloor": 2.0,
            "floorReferenceDocs": 30,
            "minQueryTokens": 3,
        },
        "bm25f": {
            "k1": 1.5,
            "b": 0.75,
            "fieldWeights": {
                "name": 3.0,
                "topic_tags": 3.0,
                "aliases": 2.5,
                "description": 2.0,
                "body": 1.0,
                "evidence": 2.5,
            },
        },
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _find_local_config(cwd: Path | None = None) -> Path | None:
    """Return ``<cwd>/.mnemo/mnemo.config.json`` if present, else None.

    Project-local installs (``mnemo init --project``) place the vault under
    ``<cwd>/.mnemo``; the config resolver prefers it so per-project sessions
    are isolated from the global config singleton.
    """
    base = Path(cwd) if cwd is not None else Path.cwd()
    candidate = base / ".mnemo" / "mnemo.config.json"
    return candidate if candidate.exists() else None


def default_config_path() -> Path:
    env = os.environ.get("MNEMO_CONFIG_PATH")
    if env:
        return Path(env)
    local = _find_local_config()
    if local is not None:
        return local
    return Path(os.path.expanduser("~/mnemo/mnemo.config.json"))


def load_config(path: Path | None = None, missing_path: Path | None = None) -> dict[str, Any]:
    """Return a config dict with all defaults populated.

    `path` overrides the default lookup. `missing_path` is a no-op convenience
    used by tests to assert "no file present" without depending on $HOME.
    """
    cfg_path = path or default_config_path()
    if missing_path is not None and not missing_path.exists():
        cfg_path = missing_path
    try:
        raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (FileNotFoundError, OSError, json.JSONDecodeError, ValueError):
        raw = {}
    return _deep_merge(DEFAULTS, raw)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    cfg_path = path or default_config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def set_vault_root(vault_root: Path, path: Path | None = None) -> None:
    """Point the config at *vault_root*, keeping every other key the file holds.

    ``mnemo init`` used to ``save_config({"vaultRoot": ...})``, which replaced
    the whole file: re-running it to repair a hook reset
    ``extraction.subprocessTimeout`` and ``doctor.skipStatuslineDrift`` to their
    defaults with no warning (#303). Only the raw file is merged — never
    ``load_config``'s defaults, which would freeze today's defaults into it.

    A file that is not a JSON object cannot be merged; its bytes are kept in a
    ``.bak.<stamp>`` sibling before it is replaced.
    """
    from datetime import datetime

    cfg_path = path or default_config_path()
    raw: dict[str, Any] = {}
    try:
        data = cfg_path.read_bytes()
    except FileNotFoundError:
        data = None
    if data is not None and data.strip():
        try:
            parsed = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, dict):
            raw = parsed
        else:
            stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
            cfg_path.with_name(f"{cfg_path.name}.bak.{stamp}").write_bytes(data)
    raw["vaultRoot"] = str(vault_root)
    save_config(raw, path=cfg_path)
