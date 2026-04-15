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
    uninstall = sub.add_parser("uninstall", help="remove hooks (keeps vault)")
    uninstall.add_argument("--yes", "-y", action="store_true")
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
    settings_path = Path(os.path.expanduser("~/.claude/settings.json"))
    if settings_path.exists():
        try:
            data = json.loads(settings_path.read_text())
            installed = sum(
                1
                for ev in ("SessionStart", "SessionEnd", "UserPromptSubmit", "PostToolUse")
                for entry in data.get("hooks", {}).get(ev, [])
                for h in entry.get("hooks", [])
                if "mnemo" in h.get("command", "")
            )
            print(f"Hooks installed: {installed}/4")
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

    if not result.ok:
        print("Issues found above.")
        return 1
    if not auto_ok or not legacy_ok:
        print("Warnings above.")
    else:
        print("OK")
    return 0


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
    try:
        inj.uninject_hooks(settings_path)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print("Hooks removed. Vault preserved.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
