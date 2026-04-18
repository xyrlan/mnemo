"""mnemo command-line entry point."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


def command(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], int]) -> Callable:
        COMMANDS[name] = fn
        return fn
    return deco


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mnemo", description="The Obsidian that populates itself.")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="first-run setup (idempotent)")
    init.add_argument("--yes", "-y", action="store_true", help="skip prompts (for automation)")
    init.add_argument("--vault-root", type=str, default=None, help="override vault location")
    init.add_argument("--no-mirror", action="store_true", help="skip initial Claude memory mirror")
    init.add_argument("--quiet", action="store_true", help="suppress informational output")

    sub.add_parser("status", help="vault state + hook health + recent activity")
    sub.add_parser("doctor", help="full diagnostic with actionable fixes")
    sub.add_parser("open", help="open vault in Obsidian or file manager")
    sub.add_parser("fix", help="reset circuit breaker")
    extract = sub.add_parser("extract", help="LLM-powered extraction of memory files into shared/_inbox")
    extract.add_argument("--dry-run", action="store_true", help="show what would run without making LLM calls or writes")
    extract.add_argument(
        "--force",
        action="store_true",
        help=(
            "reprocess dismissed and promoted entries. DESTRUCTIVE to "
            "shared/_inbox/<type>/: every .md file in feedback/user/reference "
            "inbox dirs is deleted before the run, wiping prior slug-drift "
            "duplicates. Does not touch shared/_inbox/project/ or sacred dirs."
        ),
    )
    extract.add_argument("--background", action="store_true", help=argparse.SUPPRESS)
    briefing = sub.add_parser("briefing", help=argparse.SUPPRESS)
    briefing.add_argument("jsonl_path", type=str)
    briefing.add_argument("agent", type=str)
    sub.add_parser("mcp-server", help=argparse.SUPPRESS)
    sub.add_parser("statusline", help=argparse.SUPPRESS)
    sub.add_parser("statusline-compose", help=argparse.SUPPRESS)
    uninstall = sub.add_parser("uninstall", help="remove hooks (keeps vault)")
    uninstall.add_argument("--yes", "-y", action="store_true")
    telemetry = sub.add_parser("telemetry", help="summarize MCP access log (calls + zero-hit per project)")
    telemetry.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall = sub.add_parser("recall", help="measure retrieval ranking vs historical access-log queries")
    recall.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall.add_argument("--no-bootstrap", action="store_true", help="reuse existing cases.json instead of regenerating")
    recall.add_argument("--window-s", type=float, default=120.0, help="list→read pair window in seconds (default 120)")
    sub.add_parser("help", help="list commands")
    return p


@command("help")
def cmd_help(_args: argparse.Namespace) -> int:
    parser = _build_parser()
    parser.print_help()
    return 0


@command("init")
def cmd_init(args: argparse.Namespace) -> int:
    import json
    import os
    from mnemo.core import config as cfg_mod, mirror
    from mnemo.install import preflight, scaffold, settings as inj

    quiet = bool(args.quiet)
    say = (lambda *a, **k: None) if quiet else print

    # 1. Determine vault root
    vault_root: Path
    if args.vault_root:
        vault_root = Path(os.path.expanduser(args.vault_root))
    elif args.yes:
        vault_root = Path(os.path.expanduser("~/mnemo"))
    else:
        try:
            answer = input(f"Vault location [{os.path.expanduser('~/mnemo')}]: ").strip()
        except EOFError:
            answer = ""
        vault_root = Path(os.path.expanduser(answer or "~/mnemo"))

    # 2. Preflight
    say("Running preflight checks…")
    result = preflight.run_preflight(vault_root=vault_root)
    for issue in result.issues:
        say(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        say(f"       → {issue.remediation}")
    if not result.ok:
        print("Preflight failed. Resolve the issues above and retry.", file=sys.stderr)
        return 1

    # 3. Confirm settings.json modification (interactive only)
    if not args.yes:
        try:
            confirm = input("Modify ~/.claude/settings.json to install hooks? [y/N]: ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in ("y", "yes"):
            print("Aborted by user.", file=sys.stderr)
            return 2

    # 4. Scaffold vault
    say(f"Scaffolding vault at {vault_root}…")
    scaffold.scaffold_vault(vault_root)

    # 4b. Persist vault root to default config path so _resolve_vault() finds it
    cfg_mod.save_config({"vaultRoot": str(vault_root)})

    # 5. Inject hooks
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    say(f"Injecting hooks into {settings_path}…")
    try:
        inj.inject_hooks(settings_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5b. Register MCP server in ~/.claude.json (v0.5)
    claude_json_path = Path(os.path.expanduser("~/.claude.json"))
    say(f"Registering MCP server in {claude_json_path}…")
    try:
        inj.inject_mcp_servers(claude_json_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5c. Install additive statusLine composer (v0.5)
    say("Installing statusLine composer (additive — preserves your existing line)…")
    try:
        inj.inject_statusline(settings_path, vault_root)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 6. Optional initial mirror
    if not args.no_mirror:
        say("Mirroring existing Claude memories…")
        cfg = cfg_mod.load_config(vault_root / "mnemo.config.json")
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            say(f"  (mirror skipped: {e})")

    say("mnemo is ready. Open the vault with: mnemo open")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 2
    name = args.command or "help"
    fn = COMMANDS.get(name)
    if fn is None:
        print(f"unknown command: {name}", file=sys.stderr)
        return 2
    try:
        return fn(args)
    except KeyboardInterrupt:
        return 130


def _resolve_vault() -> Path:
    from mnemo.core import config as cfg_mod, paths as paths_mod
    cfg = cfg_mod.load_config()
    return paths_mod.vault_root(cfg)


def _run_open(path: Path) -> None:
    import subprocess
    import os
    if sys.platform.startswith("darwin"):
        subprocess.run(["open", str(path)], check=False)
    elif sys.platform.startswith("win"):
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


@command("status")
def cmd_status(_args: argparse.Namespace) -> int:
    import os, json
    from mnemo.core import errors as err_mod

    vault = _resolve_vault()
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
    return 0


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


def _read_denial_log_tail(vault: Path, max_lines: int = 1000) -> list[dict]:
    """Read last *max_lines* from denial-log.jsonl. Returns [] on any error."""
    import json as _json
    try:
        log_path = vault / ".mnemo" / "denial-log.jsonl"
        if not log_path.exists():
            return []
        text = log_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
        return entries
    except Exception:
        return []


def _count_today_denial_entries(entries: list[dict]) -> int:
    """Count entries whose timestamp starts with today's date (UTC)."""
    from datetime import datetime, timezone
    today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return sum(
        1 for e in entries
        if isinstance(e.get("timestamp"), str) and e["timestamp"].startswith(today_prefix)
    )


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


def _read_enrichment_log_tail(vault: Path, max_lines: int = 1000) -> list[dict]:
    """Read last *max_lines* from enrichment-log.jsonl. Returns [] on any error."""
    import json as _json
    try:
        log_path = vault / ".mnemo" / "enrichment-log.jsonl"
        if not log_path.exists():
            return []
        text = log_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
        return entries
    except Exception:
        return []


@command("doctor")
def cmd_doctor(_args: argparse.Namespace) -> int:
    from mnemo.install import preflight
    vault = _resolve_vault()
    print("Running diagnostic / preflight checks…")
    result = preflight.run_preflight(vault_root=vault)
    for issue in result.issues:
        print(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        print(f"       → {issue.remediation}")

    auto_ok = _doctor_check_auto_brain(vault)
    legacy_ok = _doctor_check_legacy_wiki_dirs(vault)
    statusline_ok = _doctor_check_statusline_drift(vault)
    activation_ok = _doctor_check_activation(vault)
    zero_hit_ok = _doctor_check_zero_hit(vault)
    activation_fidelity_ok = _doctor_check_activation_fidelity(vault)
    rule_integrity_ok = _doctor_check_rule_integrity(vault)
    _doctor_report_recall(vault)

    if not result.ok:
        print("Issues found above.")
        return 1
    if not (auto_ok and legacy_ok and statusline_ok and activation_ok
            and zero_hit_ok and activation_fidelity_ok and rule_integrity_ok):
        print("Warnings above.")
    else:
        print("OK")
    return 0


def _doctor_report_recall(vault: Path) -> None:
    """Emit one informational line from `.mnemo/recall-report.json` if present.

    Purely advisory — never turns doctor into a warning/error, since the recall
    suite is opt-in and the primacy rate is a trend indicator, not a pass/fail
    gate. Silent when the report is missing, malformed, or empty.
    """
    import json as _json
    path = vault / ".mnemo" / "recall-report.json"
    if not path.is_file():
        return
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        return
    report = (data.get("report") or {}) if isinstance(data, dict) else {}
    cases = report.get("cases", 0) or 0
    rate = report.get("primacy_rate_at_5")
    if cases == 0 or rate is None:
        return
    ts = data.get("generated_at")
    suffix = f" (measured {ts})" if ts else ""
    print(f"  ℹ Recall: primacy@5 = {rate:.1%} over {cases} cases{suffix}")


def _doctor_check_zero_hit(vault: Path) -> bool:
    """Warn if the access log shows a high zero-hit rate — ontology may have gaps.

    Silent when fewer than 10 total calls have been logged (small samples
    produce noise). Emits a single warning + project-level breakdown (top 3
    projects by zero-hit count) when `zero_hit_rate > _ZERO_HIT_THRESHOLD`.
    """
    from mnemo.core.mcp import access_log_summary as summary_mod

    entries = summary_mod.read_log(vault)
    summary = summary_mod.summarize(entries)
    total = summary["total_calls"]
    if total < 10:
        return True

    rate = summary["zero_hit_rate"]
    if rate <= summary_mod._ZERO_HIT_THRESHOLD:
        return True

    print(f"  \u26a0 Zero-hit calls: {rate:.1%} of {total} MCP calls returned no rules (threshold {summary_mod._ZERO_HIT_THRESHOLD:.0%})")
    offenders = sorted(
        summary["by_project"].items(),
        key=lambda kv: -int(kv[1]["zero_hit"]),
    )[:3]
    for project, bucket in offenders:
        zh = int(bucket["zero_hit"])
        calls = int(bucket["calls"])
        if zh == 0:
            continue
        print(f"       \u2192 {project}: {zh}/{calls} zero-hit")
    print("       \u2192 review the tag ontology or add rules for under-covered topics")
    return False


def _synthesize_path_for_glob(glob: str) -> str | None:
    """Produce a concrete file path that should match the glob, or None.

    Deterministic replacements:
      ``**/`` -> ``a/``   (match-zero-or-more-segments case)
      ``**``  -> ``a``    (trailing double-star)
      ``*``   -> ``sample`` (single segment)
    Returns None when the glob contains character classes or ``?`` — those
    cannot be safely synthesized without guessing which characters the author
    intended to match.
    """
    if "?" in glob or "[" in glob:
        return None
    out = glob.replace("**/", "a/").replace("**", "a").replace("*", "sample")
    return out or None


def _doctor_check_activation_fidelity(vault: Path) -> bool:
    """Positive self-check: every enforce/activates_on rule reaches the index
    and its globs actually match a synthesized path.

    Complements `_doctor_check_activation` (which validates malformed blocks)
    with a round-trip: if the frontmatter parses but the slug never enters the
    index or never self-activates on a representative path, something broke
    between authoring and dispatch.

    Returns True iff no warnings were emitted. `ℹ` info lines (for rules whose
    globs are un-synthesizable) are NOT warnings.
    """
    from mnemo.core.filters import parse_frontmatter
    from mnemo.core.rule_activation import (
        load_index,
        match_path_enrich,
        parse_activates_on_block,
        parse_enforce_block,
    )

    index = load_index(vault)
    if index is None:
        return True  # no index yet; _doctor_check_activation surfaces staleness

    indexed_slugs: set[str] = {
        slug for slug, rule in index.get("rules", {}).items()
        if rule.get("enforce") or rule.get("activates_on")
    }

    ok = True

    feedback_dir = vault / "shared" / "feedback"
    if feedback_dir.is_dir():
        for md_path in sorted(feedback_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fm = parse_frontmatter(text)
            except Exception:
                continue

            # Match `build_index`'s slug derivation exactly
            # (src/mnemo/core/rule_activation.py:323) — the index stores
            # `fm.get("slug") or fm.get("name") or md_path.stem`, NOT just
            # the filename stem. Getting this wrong silently misses hits.
            slug = fm.get("slug") or fm.get("name") or md_path.stem
            rel = md_path.name

            has_enforce = parse_enforce_block(fm) is not None
            has_enrich = parse_activates_on_block(fm) is not None

            if has_enforce and slug not in indexed_slugs:
                print(f"  \u26a0 Rule {rel!r} has a valid enforce block but is absent from the activation index")
                print(f"       \u2192 run 'mnemo extract' to rebuild the index")
                ok = False
            if has_enrich and slug not in indexed_slugs:
                print(f"  \u26a0 Rule {rel!r} has a valid activates_on block but is absent from the activation index")
                print(f"       \u2192 run 'mnemo extract' to rebuild the index")
                ok = False

    for slug, rule_entry in index.get("rules", {}).items():
        activates = rule_entry.get("activates_on")
        if not activates:
            continue
        # Use first associated project for self-activation test; universal rules
        # are reachable from any project so we just pick one from by_project.
        rule_projects = rule_entry.get("projects", [])
        project = rule_projects[0] if rule_projects else ""
        globs = activates.get("path_globs", []) or []
        tools = activates.get("tools", []) or []
        if not globs or not tools:
            continue
        any_testable = False
        mismatched = False
        for glob in globs:
            sample = _synthesize_path_for_glob(glob)
            if sample is None:
                continue
            any_testable = True
            for tool in tools:
                hits = match_path_enrich(index, project, sample, tool)
                if slug not in [h.slug for h in hits]:
                    print(f"  \u26a0 Rule {slug!r} does not self-activate: glob {glob!r} -> synthesized {sample!r}, tool {tool!r} returned no hit")
                    print(f"       \u2192 review the glob shape or the enrich build pipeline")
                    ok = False
                    mismatched = True
                    break
            if mismatched:
                break
        if not any_testable and not mismatched:
            print(f"  \u2139 Rule {slug!r} has no auto-testable path_globs (contains '?' or '[abc]' \u2014 manual verification required)")

    return ok


def _doctor_check_rule_integrity(vault: Path) -> bool:
    """Validate canonical rules in shared/{feedback,user,reference}/.

    Checks, per file:
      - frontmatter parses non-empty
      - required fields present: type, tags (non-empty), sources (non-empty)
      - every source path resolves under the vault
      - body (text after frontmatter) >= _MIN_BODY_CHARS
    Files that the shared filter marks as non-canonical (drafts in
    ``shared/_inbox/``, ``needs-review``-tagged, ``stability: evolving``) are
    excluded to avoid noise on transient extraction artefacts.
    """
    from mnemo.core.filters import is_consumer_visible, parse_frontmatter
    from mnemo.core.mcp.tools import _RETRIEVAL_TYPES, _extract_body

    shared = vault / "shared"
    if not shared.is_dir():
        return True

    ok = True
    for page_type in _RETRIEVAL_TYPES:
        type_dir = shared / page_type
        if not type_dir.is_dir():
            continue
        for md_path in sorted(type_dir.glob("*.md")):
            try:
                text = md_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                fm = parse_frontmatter(text)
            except Exception:
                fm = {}

            if not fm:
                print(f"  \u26a0 Rule {page_type}/{md_path.name}: frontmatter unparseable or missing")
                print(f"       \u2192 re-run 'mnemo extract' or fix the frontmatter manually")
                ok = False
                continue

            if not is_consumer_visible(md_path, fm, vault):
                continue  # draft / needs-review / evolving — skip integrity

            rel = f"{page_type}/{md_path.name}"
            if not fm.get("type"):
                print(f"  \u26a0 Rule {rel}: missing 'type' field in frontmatter")
                ok = False
            if not fm.get("tags"):
                print(f"  \u26a0 Rule {rel}: 'tags' field is empty or missing")
                ok = False

            sources = fm.get("sources") or []
            if not sources:
                print(f"  \u26a0 Rule {rel}: 'sources' field is empty or missing")
                ok = False
            else:
                for src in sources:
                    if not isinstance(src, str):
                        continue
                    if not (vault / src).is_file():
                        print(f"  \u26a0 Rule {rel}: source path does not resolve: {src}")
                        ok = False

            body = _extract_body(text).strip()
            if len(body) < _MIN_BODY_CHARS:
                print(f"  \u26a0 Rule {rel}: body has {len(body)} chars (min {_MIN_BODY_CHARS})")
                ok = False

    return ok


_MIN_BODY_CHARS = 50


def _doctor_check_activation(vault: Path) -> bool:
    """Four activation-related doctor checks:

    1. Malformed activate/enforce blocks in feedback files.
    2. Stale activation index (index mtime older than newest feedback file mtime).
    3. Suspicious deny_pattern (< 5 chars, or matches "echo hello").
    4. Overly-broad activates_on.path_globs (**/* or *).

    Each check is fail-safe: a bad file is skipped, not a crash.
    Returns True if no warnings were emitted.
    """
    import re as _re
    from mnemo.core.filters import parse_frontmatter
    from mnemo.core.rule_activation import (
        parse_enforce_block,
        parse_activates_on_block,
        _describe_enforce_error,
        _describe_enrich_error,
    )

    feedback_dir = vault / "shared" / "feedback"
    ok = True

    if not feedback_dir.is_dir():
        return True

    candidates = sorted(feedback_dir.glob("*.md"))
    newest_mtime: float = 0.0
    for md_path in candidates:
        try:
            mtime = md_path.stat().st_mtime
            if mtime > newest_mtime:
                newest_mtime = mtime
        except OSError:
            pass

    # --- Check 1: malformed blocks ---
    # --- Check 3: suspicious deny_pattern ---
    # --- Check 4: overly-broad path_globs ---
    _BENIGN_TEST_INPUT = "echo hello"
    _BROAD_GLOBS = {"**/*", "*"}

    for md_path in candidates:
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue  # skip unreadable files

        try:
            fm = parse_frontmatter(text)
        except Exception:
            continue

        rel = md_path.name

        # --- Check 1: enforce block present-but-invalid ---
        if fm.get("enforce") is not None:
            parsed = parse_enforce_block(fm)
            if parsed is None:
                err = _describe_enforce_error(fm)
                print(f"  \u26a0 Malformed enforce block in {rel}: {err}")
                print(f"       \u2192 fix the frontmatter in shared/feedback/{rel}")
                ok = False
            else:
                # --- Check 3: suspicious deny_pattern ---
                for pattern in parsed.get("deny_patterns", []):
                    suspicious = False
                    reason = ""
                    if len(pattern) < 5:
                        suspicious = True
                        reason = f"pattern {pattern!r} is shorter than 5 characters"
                    elif _re.search(pattern, _BENIGN_TEST_INPUT, _re.IGNORECASE | _re.DOTALL):
                        suspicious = True
                        reason = f"pattern {pattern!r} matches benign input {_BENIGN_TEST_INPUT!r} — too permissive"
                    if suspicious:
                        print(f"  \u26a0 Suspicious deny_pattern in {rel}: {reason}")
                        print(f"       \u2192 tighten the pattern so it doesn't match safe commands")
                        ok = False

        # --- Check 1: activates_on block present-but-invalid ---
        if fm.get("activates_on") is not None:
            parsed_enrich = parse_activates_on_block(fm)
            if parsed_enrich is None:
                err = _describe_enrich_error(fm)
                print(f"  \u26a0 Malformed activates_on block in {rel}: {err}")
                print(f"       \u2192 fix the frontmatter in shared/feedback/{rel}")
                ok = False
            else:
                # --- Check 4: overly-broad path_globs ---
                for glob in parsed_enrich.get("path_globs", []):
                    if glob in _BROAD_GLOBS:
                        print(f"  \u26a0 Overly-broad path_glob {glob!r} in {rel}: matches virtually every file")
                        print(f"       \u2192 narrow the glob (e.g. **/*.py, src/**/*.ts) to avoid false positives")
                        ok = False

    # --- Check 2: stale activation index ---
    index_path = vault / ".mnemo" / "rule-activation-index.json"
    if index_path.exists() and newest_mtime > 0:
        try:
            index_mtime = index_path.stat().st_mtime
            if index_mtime < newest_mtime:
                import datetime as _dt
                def _fmt(ts: float) -> str:
                    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
                print(f"  \u26a0 Activation index is stale (newest feedback file: {_fmt(newest_mtime)}, index: {_fmt(index_mtime)}). Run 'mnemo extract' to rebuild.")
                ok = False
        except OSError:
            pass

    return ok


def _doctor_check_statusline_drift(vault: Path) -> bool:
    """v0.5: warn when settings.json statusLine drifted away from our composer.

    Three states:
    - composer present + state file present → healthy (return True)
    - state file present but settings.json statusLine is something else → drift
    - no state file at all → mnemo init never ran or already uninstalled (skip)
    """
    import os
    import json as _json

    state_path = vault / ".mnemo" / "statusline-original.json"
    if not state_path.exists():
        return True  # never installed or already uninstalled — nothing to drift from

    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    if not settings_path.exists():
        print("  ⚠ statusLine state file present but ~/.claude/settings.json is missing")
        print("       → run `mnemo init` to reinstall, or `mnemo uninstall` to clean up state")
        return False

    try:
        data = _json.loads(settings_path.read_text())
    except (OSError, _json.JSONDecodeError):
        return True  # other doctor checks will report the malformed file

    current = data.get("statusLine")
    is_ours = (
        isinstance(current, dict)
        and isinstance(current.get("command"), str)
        and current["command"].strip().endswith("statusline-compose")
    )
    if is_ours:
        return True

    print("  ⚠ statusLine drift: settings.json no longer points at the mnemo composer")
    print("       → if you edited statusLine manually after `mnemo init`, run")
    print("         `mnemo init` again to re-wrap, or `mnemo uninstall` to clean up state")
    return False


def _doctor_check_legacy_wiki_dirs(vault: Path) -> bool:
    """v0.4: flag the fossil ``wiki/sources/`` and ``wiki/compiled/`` dirs.

    Extraction auto-deletes these on first v0.4 run, but users who haven't
    triggered an extract yet still see the dead dirs — warn them and tell
    them the auto-cleanup is harmless and runs next extract.
    """
    dead = [
        d for d in (vault / "wiki" / "sources", vault / "wiki" / "compiled")
        if d.exists()
    ]
    if not dead:
        return True
    # Forward slashes in user-facing output for cross-platform consistency —
    # matches the wikilink convention used everywhere else in mnemo.
    rel = ", ".join(
        str(d.relative_to(vault)).replace("\\", "/") for d in dead
    )
    print(f"  ⚠ Legacy v0.3 directories present: {rel}")
    print("       → harmless; next `mnemo extract` run will auto-delete them")
    print("         (the wiki/ hierarchy was replaced by a dashboard inside HOME.md in v0.4)")
    return False


def _doctor_check_auto_brain(vault: Path) -> bool:
    """Return True if no warnings were emitted."""
    import json as _json
    import time
    from datetime import datetime, timedelta
    from mnemo.core import config as cfg_mod

    cfg = cfg_mod.load_config()
    auto = (cfg.get("extraction", {}) or {}).get("auto", {}) or {}
    enabled = bool(auto.get("enabled", False))
    ok = True

    lock_path = vault / ".mnemo" / "extract.lock"
    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age > 600:
                print(f"  ⚠ Auto-brain: stale extract.lock at {lock_path} ({int(age)}s old); will auto-reclaim on next run")
                ok = False
        except OSError:
            pass

    last_run_path = vault / ".mnemo" / "last-auto-run.json"
    if not enabled:
        return ok

    if not last_run_path.exists():
        print("  ℹ Auto-brain: enabled but has never run. Hook scheduling may not be firing.")
        return ok

    try:
        payload = _json.loads(last_run_path.read_text(encoding="utf-8"))
    except (OSError, _json.JSONDecodeError):
        print("  ⚠ Auto-brain: last-auto-run.json is corrupt; delete to reset")
        return False

    exit_code = payload.get("exit_code", 0)
    error = payload.get("error") or {}
    finished_at = payload.get("finished_at")

    if exit_code != 0 and error:
        err_type = error.get("type", "error")
        err_msg = error.get("message", "")
        print(f"  ⚠ Auto-brain: FAILED on last run: {err_type}: {err_msg}")
        print(f"       → check ~/mnemo/.errors.log for extract.bg.* entries")
        ok = False

    if finished_at:
        try:
            finished_dt = datetime.fromisoformat(finished_at)
            if datetime.now() - finished_dt > timedelta(days=7):
                print(f"  ℹ Auto-brain: has not run successfully in 7+ days (last: {finished_at})")
                ok = False
        except ValueError:
            pass

    return ok


@command("fix")
def cmd_fix(_args: argparse.Namespace) -> int:
    from mnemo.core import errors as err_mod
    vault = _resolve_vault()
    err_mod.reset(vault)
    print("Circuit breaker reset.")
    return 0


@command("open")
def cmd_open(_args: argparse.Namespace) -> int:
    vault = _resolve_vault()
    _run_open(vault)
    print(f"Opened {vault}")
    return 0


@command("extract")
def cmd_extract(args: argparse.Namespace) -> int:
    from mnemo.core import config as cfg_mod, extract as extract_mod

    cfg = cfg_mod.load_config()
    if bool(getattr(args, "background", False)):
        return _run_extract_background(cfg, args)

    try:
        summary = extract_mod.run_extraction(cfg, dry_run=bool(args.dry_run), force=bool(args.force))
    except extract_mod.ExtractionIOError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. Partial state flushed; re-run to continue.", file=sys.stderr)
        return 130

    if args.dry_run:
        return 0

    # Summary
    total_tokens = summary.total_input_tokens + summary.total_output_tokens
    if summary.all_calls_subscription:
        cost_line = f"{total_tokens} tokens processed (subscription — no charge)"
    else:
        cost_line = f"${summary.total_cost_usd:.4f} ({total_tokens} tokens)"

    cluster_pages = summary.pages_written - summary.projects_promoted
    print("✓ extraction complete")
    print(f"  written:    {summary.pages_written} pages ({summary.projects_promoted} direct projects + {cluster_pages} cluster pages)")
    print(f"  auto-promoted: {summary.auto_promoted}")
    print(f"  conflicts:  {summary.sibling_proposed + summary.sibling_bounced}")
    print(f"  upgrades:   {summary.upgrade_proposed}")
    print(f"  updates:    {summary.update_proposed}")
    print(f"  skipped:    {summary.unchanged_skipped} unchanged, {summary.dismissed_skipped} dismissed")
    print(f"  calls:      {summary.llm_calls} LLM calls")
    print(f"  wall-time:  {summary.wall_time_s:.1f}s")
    print(f"  cost:       {cost_line}")

    if summary.failed_chunks > 0:
        print(f"  ⚠ failed_chunks: {summary.failed_chunks} (see ~/.errors.log; re-run to retry)", file=sys.stderr)
        return 1
    return 0


def _run_extract_background(cfg: dict, args: argparse.Namespace) -> int:
    import contextlib
    import os
    from mnemo.core import errors as err_mod, extract as extract_mod, paths as paths_mod

    vault_root = paths_mod.vault_root(cfg)
    devnull = open(os.devnull, "w")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            try:
                extract_mod.run_extraction(
                    cfg,
                    dry_run=bool(args.dry_run),
                    force=bool(args.force),
                    background=True,
                )
            except extract_mod.ExtractionIOError as exc:
                # Lock contention: do NOT write last-auto-run.json; log only
                err_mod.log_error(vault_root, "extract.bg.lock", exc)
                return 2
            except Exception as exc:
                # run_extraction in background mode catches most errors and
                # writes them into last-auto-run.json; this path is for
                # exceptions that escape (e.g., config issues).
                err_mod.log_error(vault_root, "extract.bg.outer", exc)
                return 1
    finally:
        devnull.close()
    return 0


@command("briefing")
def cmd_briefing(args: argparse.Namespace) -> int:
    """Hidden CLI entry point: `mnemo briefing <jsonl_path> <agent>`.

    Invoked by session_end's detached spawn. Fire-and-forget: errors are
    logged to ~/.errors.log under the vault but never propagated.
    """
    import contextlib
    import os
    from mnemo.core import briefing as briefing_mod, config as cfg_mod, errors as err_mod, paths

    cfg = cfg_mod.load_config()
    vault_root = paths.vault_root(cfg)
    devnull = open(os.devnull, "w")
    try:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            try:
                briefing_mod.generate_session_briefing(
                    Path(args.jsonl_path), args.agent, cfg,
                )
            except Exception as exc:
                err_mod.log_error(vault_root, "briefing.cli", exc)
                return 1
    finally:
        devnull.close()
    return 0


@command("mcp-server")
def cmd_mcp_server(_args: argparse.Namespace) -> int:
    """Hidden stdio entry point — wired in ~/.claude.json under mcpServers.mnemo."""
    from mnemo.core.mcp import server as mcp_server
    return mcp_server.serve()


@command("statusline")
def cmd_statusline(_args: argparse.Namespace) -> int:
    """Hidden: emit the mnemo statusline segment to stdout."""
    import os
    from mnemo import statusline as sl
    from mnemo.core import config as cfg_mod
    from mnemo.core import paths as paths_mod

    try:
        cfg = cfg_mod.load_config()
        vault = paths_mod.vault_root(cfg)
    except Exception:
        return 0
    claude_json = Path(os.path.expanduser("~/.claude.json"))
    sys.stdout.write(sl.render(vault, claude_json))
    return 0


@command("statusline-compose")
def cmd_statusline_compose(_args: argparse.Namespace) -> int:
    """Hidden: composer that runs the user's original statusLine + mnemo's segment."""
    from mnemo import statusline as sl
    return sl.compose()


@command("telemetry")
def cmd_telemetry(args: argparse.Namespace) -> int:
    """Summarize `.mnemo/mcp-access-log.jsonl` — calls per tool + zero-hit per project."""
    import json as _json
    from mnemo.core.mcp import access_log_summary as summary_mod

    vault = _resolve_vault()
    entries = summary_mod.read_log(vault)
    summary = summary_mod.summarize(entries)

    if bool(getattr(args, "json", False)):
        print(_json.dumps(summary, indent=2))
    else:
        print(summary_mod.format_human(summary))
    return 0


@command("recall")
def cmd_recall(args: argparse.Namespace) -> int:
    """Measure retrieval ranking against historical queries captured in the access log.

    Reads ``.mnemo/mcp-access-log.jsonl``, pairs each ``list_rules_by_topic`` call with
    the ``read_mnemo_rule`` that consumed it, re-runs the live ranking, and reports
    hit@3/@5/@10 + MRR + p95 latency. Outputs to ``.mnemo/recall-cases.json`` (fixture)
    and ``.mnemo/recall-report.json`` (last run).
    """
    import json as _json
    from datetime import datetime, timezone
    from mnemo.core.mcp.recall import (
        aggregate, bootstrap_cases, count_log_entries, format_report, run_case,
    )

    vault = _resolve_vault()
    mnemo_dir = vault / ".mnemo"
    cases_path = mnemo_dir / "recall-cases.json"
    report_path = mnemo_dir / "recall-report.json"
    log_path = mnemo_dir / "mcp-access-log.jsonl"
    use_json = bool(getattr(args, "json", False))

    if not args.no_bootstrap:
        if not log_path.is_file():
            print(f"error: access log missing: {log_path}", file=sys.stderr)
            return 1
        cases = bootstrap_cases(log_path, pair_window_s=args.window_s)
        mnemo_dir.mkdir(parents=True, exist_ok=True)
        cases_path.write_text(_json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    else:
        if not cases_path.is_file():
            print(
                f"error: cases file missing: {cases_path} — run `mnemo recall` without --no-bootstrap first",
                file=sys.stderr,
            )
            return 1
        cases = _json.loads(cases_path.read_text(encoding="utf-8"))

    if not cases:
        msg = "no cases generated — access log has no matching list→read pairs yet."
        if use_json:
            print(_json.dumps({"report": None, "results": [], "reason": msg}))
        else:
            print(msg)
        return 0

    results = [run_case(vault, c) for c in cases]
    log_entries = count_log_entries(log_path)
    report = aggregate(results, log_entries=log_entries)
    mnemo_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "report": report,
        "results": results,
    }
    report_path.write_text(_json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    if use_json:
        print(_json.dumps(payload, indent=2))
    else:
        print(format_report(report))
        print(f"\n(written to {report_path})")
    return 0


@command("uninstall")
def cmd_uninstall(args: argparse.Namespace) -> int:
    import os
    from mnemo.install import settings as inj
    if not args.yes:
        try:
            answer = input("Remove mnemo hooks from settings.json? Vault data is preserved. [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            print("Aborted.", file=sys.stderr)
            return 2
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    claude_json_path = Path(os.path.expanduser("~/.claude.json"))
    try:
        vault = _resolve_vault()
        inj.uninject_statusline(settings_path, vault)
        inj.uninject_hooks(settings_path)
        inj.uninject_mcp_servers(claude_json_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print("Hooks, MCP server, and statusLine removed. Vault preserved.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
