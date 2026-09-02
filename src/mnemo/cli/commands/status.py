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
from mnemo.install.settings import is_mnemo_hook_command


def _count_mnemo_hooks(settings_path: Path, expected_events: tuple[str, ...]) -> int | None:
    """Return mnemo-hook count or ``None`` when the settings file is malformed."""
    import json
    if not settings_path.exists():
        return 0
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        return None
    return sum(
        1
        for ev in expected_events
        for entry in data.get("hooks", {}).get(ev, [])
        for h in entry.get("hooks", [])
        if is_mnemo_hook_command(h.get("command", ""))
    )


def _count_plugin_hooks(expected_events: tuple[str, ...]) -> int | None:
    """Count hooks the plugin declares, or None when not running as one.

    A plugin install has no hooks in settings.json — they live in the plugin's
    own hooks.json. Reading only settings.json made status report "not
    installed" to users whose install was working.
    """
    import json
    import os

    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if not root:
        return None
    try:
        data = json.loads((Path(root) / "hooks" / "hooks.json").read_text())
    except (OSError, json.JSONDecodeError):
        return 0
    hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
    return sum(
        1
        for ev in expected_events
        for entry in hooks.get(ev, [])
        for _ in entry.get("hooks", [])
    )


def _print_scope_line(label: str, settings_path: Path, expected_events: tuple[str, ...]) -> None:
    n = _count_mnemo_hooks(settings_path, expected_events)
    if not settings_path.exists():
        print(f"Hooks ({label}): settings.json missing — {settings_path}")
    elif n is None:
        print(f"Hooks ({label}): settings.json malformed — {settings_path} (see mnemo doctor)")
    else:
        print(f"Hooks ({label}): {n}/{len(expected_events)} — {settings_path}")


@command("status")
def cmd_status(args: argparse.Namespace) -> int:
    import os
    from mnemo import cli  # late binding for monkeypatched _resolve_vault
    from mnemo.core import errors as err_mod

    vault = cli._resolve_vault()
    print(f"Vault: {vault}  ({'exists' if vault.exists() else 'MISSING'})")
    from mnemo.install.settings import HOOK_DEFINITIONS

    expected_events = tuple(HOOK_DEFINITIONS.keys())
    scope = getattr(args, "scope", "all") or "all"
    cwd = Path.cwd()
    project_settings = cwd / ".claude" / "settings.json"
    global_settings = Path(os.path.expanduser("~/.claude/settings.json"))

    # Under a plugin the two settings.json scopes are both legitimately empty,
    # so reporting them would read as "mnemo is not installed". One line about
    # where the hooks actually come from replaces both.
    plugin_n = _count_plugin_hooks(expected_events)
    if plugin_n is not None:
        print(f"Hooks (plugin): {plugin_n}/{len(expected_events)} — declared by the mnemo plugin")
    else:
        if scope in ("project", "all"):
            _print_scope_line("project", project_settings, expected_events)
        if scope in ("global", "all"):
            _print_scope_line("global", global_settings, expected_events)
    breaker = "closed (ok)" if err_mod.should_run(vault) else "OPEN — recent errors detected"
    print(f"Circuit breaker: {breaker}")
    log = vault / ".errors.log"
    if log.exists():
        print(f"Error log: {log} ({log.stat().st_size} bytes)")
    _print_auto_brain_status(vault)
    _print_activation_status(vault)
    _print_reflex_status(vault)
    _print_learned_status(vault)
    return 0


def _current_project() -> str | None:
    """The project name status is reporting on, or None when it cannot tell."""
    import os as _os
    try:
        from mnemo.core.agent import resolve_agent
        return resolve_agent(_os.getcwd()).name or None
    except Exception:  # noqa: BLE001 — a status line is not worth a traceback
        return None


def _print_learned_status(vault: Path) -> None:
    """The tail of the learned ledger — the overflow the session-start block
    points at with ``(N more — mnemo status)``, plus everything already
    announced, so a rule the user half-remembers is one command away."""
    from mnemo.core import learned
    from mnemo.core import config as cfg_mod

    project = _current_project()
    try:
        threshold = int((cfg_mod.load_config().get("scoping") or {}).get(
            "universalThreshold", 2))
    except Exception:  # noqa: BLE001
        threshold = 2
    entries = learned.recent(vault, project, limit=10, universal_threshold=threshold)
    if not entries:
        return
    print(f"\nRecently learned ({project or 'all projects'}):")
    for e in entries:
        slug = e.get("slug") or ""
        name = e.get("name") or slug
        confidence = "verified" if e.get("confidence") == "verified" else "inferred"
        print(f"  • {slug} — {name} [{confidence}]")
    print(f"  (ledger: {learned.LEDGER_REL})")


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
