"""``mnemo status`` — vault state + hook health + recent activity."""
from __future__ import annotations

import argparse
from pathlib import Path

from mnemo.cli._helpers import (
    _count_today_denial_entries,
    _read_denial_log_tail,
    _read_enrichment_log_tail,
)
from mnemo.cli.parser import command


@command("status")
def cmd_status(_args: argparse.Namespace) -> int:
    import os, json
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    from mnemo.core import errors as err_mod

    vault = cli._resolve_vault()
    print(f"Vault: {vault}  ({'exists' if vault.exists() else 'MISSING'})")
    from mnemo.install.settings import HOOK_DEFINITIONS

    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    expected_events = tuple(HOOK_DEFINITIONS.keys())
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            installed = sum(
                1
                for ev in expected_events
                for entry in data.get("hooks", {}).get(ev, [])
                for h in entry.get("hooks", [])
                if "mnemo" in h.get("command", "")
            )
            print(f"Hooks installed: {installed}/{len(expected_events)}")
        except json.JSONDecodeError:
            print("Hooks: settings.json malformed (see mnemo doctor)")
    else:
        print("Hooks: settings.json missing")
    breaker = "closed (ok)" if err_mod.should_run(vault) else "OPEN — recent errors detected"
    print(f"Circuit breaker: {breaker}")
    log = vault / ".errors.log"
    if log.exists():
        print(f"Error log: {log} ({log.stat().st_size} bytes)")
    _print_auto_brain_status(vault)
    _print_activation_status(vault)
    _print_reflex_status(vault)
    return 0


def _print_reflex_status(vault: Path) -> None:
    """v0.8: one-liner reporting today's reflex emissions when enabled."""
    from mnemo.core.config import load_config
    from mnemo.core.mcp.session_state import read_today_emissions

    cfg = load_config()
    if not bool((cfg.get("reflex") or {}).get("enabled", False)):
        return
    emissions = read_today_emissions(vault)
    suffix = "emission" if emissions == 1 else "emissions"
    print(f"\nReflex: enabled ({emissions} {suffix} today)")


def _print_auto_brain_status(vault: Path) -> None:
    import json as _json
    import time
    from datetime import datetime
    from mnemo.core import config as cfg_mod

    cfg = cfg_mod.load_config()
    auto = (cfg.get("extraction", {}) or {}).get("auto", {}) or {}
    enabled = bool(auto.get("enabled", False))
    min_new = int(auto.get("minNewMemories", 5) or 5)
    min_interval = int(auto.get("minIntervalMinutes", 60) or 60)

    print("Auto-brain:")

    lock_path = vault / ".mnemo" / "extract.lock"
    if lock_path.exists():
        try:
            age = int(time.time() - lock_path.stat().st_mtime)
            print(f"  running now: extract.lock held, started {age}s ago")
        except OSError:
            print("  running now: extract.lock present")

    if not enabled:
        print("  enabled:     no (set extraction.auto.enabled=true to activate)")
        return

    print(f"  enabled:     yes (minNewMemories={min_new}, minIntervalMinutes={min_interval})")

    last_run_path = vault / ".mnemo" / "last-auto-run.json"
    if not last_run_path.exists():
        print("  last run:    (none yet)")
        return

    try:
        payload = _json.loads(last_run_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        print("  last run:    (corrupt last-auto-run.json)")
        return

    exit_code = payload.get("exit_code", 0)
    summary = payload.get("summary", {}) or {}
    finished_at = payload.get("finished_at")
    elapsed_str = "unknown"
    if finished_at:
        try:
            finished_dt = datetime.fromisoformat(finished_at)
            delta = datetime.now() - finished_dt
            total_sec = int(delta.total_seconds())
            if total_sec < 60:
                elapsed_str = f"{total_sec}s ago"
            elif total_sec < 3600:
                elapsed_str = f"{total_sec // 60}m ago"
            else:
                elapsed_str = f"{total_sec // 3600}h ago"
        except ValueError:
            pass

    pages = summary.get("pages_written", 0)
    auto_n = summary.get("auto_promoted", 0)
    siblings = summary.get("sibling_proposed", 0) + summary.get("sibling_bounced", 0)
    upgrades = summary.get("upgrade_proposed", 0)

    if exit_code == 0:
        print(f"  last run:    {elapsed_str} — {pages} pages ({auto_n} auto-promoted), {siblings} conflicts")
    else:
        err = payload.get("error") or {}
        err_type = err.get("type", "error")
        print(f"  last run:    {elapsed_str} — FAILED ({err_type}); see ~/.errors.log")
    if upgrades:
        print(f"  upgrades:    {upgrades} proposed")


def _print_activation_status(vault: Path) -> None:
    """Print an Activation: section to stdout — only when enforcement or enrichment is on."""
    import json as _json
    from mnemo.core import config as cfg_mod
    from mnemo.core.rule_activation import load_index

    cfg = cfg_mod.load_config()
    enforce_enabled = bool((cfg.get("enforcement") or {}).get("enabled", False))
    enrich_enabled = bool((cfg.get("enrichment") or {}).get("enabled", False))

    if not enforce_enabled and not enrich_enabled:
        return

    print("Activation:")
    print(f"  Enforcement: {'enabled' if enforce_enabled else 'disabled'}")
    print(f"  Enrichment:  {'enabled' if enrich_enabled else 'disabled'}")

    index = load_index(vault)
    if index is None:
        print("  Rule activation index: missing")
    else:
        built_at = index.get("built_at", "?")
        vault_root_str = index.get("vault_root", "?")
        print(f"  Rule activation index: present (built_at={built_at}, vault_root={vault_root_str})")

        # Determine current project
        try:
            from mnemo.core.agent import resolve_agent
            import os as _os
            agent = resolve_agent(_os.getcwd())
            project = agent.name
        except Exception:
            project = ""

        from mnemo.core.rule_activation import (
            iter_enforce_rules_for_project, iter_enrich_rules_for_project,
        )
        print(f"  Per-project rule counts (current={project}, includes universal):")
        n_enforce = sum(1 for _ in iter_enforce_rules_for_project(index, project))
        n_enrich = sum(1 for _ in iter_enrich_rules_for_project(index, project))
        print(f"    Enforce rules: {n_enforce}")
        print(f"    Enrich rules:  {n_enrich}")

        malformed = index.get("malformed", []) or []
        if malformed:
            print(f"  Malformed rules (rejected at parse time): {len(malformed)}")
            print("    (see 'mnemo doctor' for details)")

    # Denial log
    entries = _read_denial_log_tail(vault)
    n_today = _count_today_denial_entries(entries)
    print(f"  Recent denials (today): {n_today}")

    if enrich_enabled:
        enrich_entries = _read_enrichment_log_tail(vault)
        n_enrich_today = _count_today_denial_entries(enrich_entries)
        print(f"  Recent enrichments (today): {n_enrich_today}")

    # Last denial
    if entries:
        last = entries[-1]
        ts = last.get("timestamp", "?")
        cmd = last.get("command", "")
        print(f"  Last denial: {ts} — {cmd}")
    else:
        print("  Last denial: none")
