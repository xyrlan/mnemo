"""``mnemo init`` — first-run setup (idempotent).

Two install scopes are supported:

- *Global* (default) — writes ``~/.claude/settings.json`` + ``~/.claude.json``;
  mnemo runs in every Claude Code session.
- *Project* (``--project`` / ``--local``) — writes ``<cwd>/.claude/settings.json``
  + ``<cwd>/.mcp.json`` and forces the vault into ``<cwd>/.mnemo/``. mnemo
  only loads when Claude Code is launched in that directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from mnemo.cli.parser import command
from mnemo.install.settings import is_mnemo_hook_command


GITIGNORE_ENTRIES = (".claude/", ".mnemo/")


def _has_global_mnemo_install(home_settings: Path) -> bool:
    """True if ``~/.claude/settings.json`` already wires mnemo hooks."""
    if not home_settings.exists():
        return False
    try:
        data = json.loads(home_settings.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return False
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            for h in (entry or {}).get("hooks", []) or []:
                if is_mnemo_hook_command((h or {}).get("command", "")):
                    return True
    return False


def _ensure_gitignore(cwd: Path, entries: tuple[str, ...] = GITIGNORE_ENTRIES) -> None:
    """Append missing mnemo entries to ``<cwd>/.gitignore`` (idempotent)."""
    gi = cwd / ".gitignore"
    existing_lines: list[str] = []
    # The user owns this file: surrogateescape carries any byte that is not
    # UTF-8 through the read and back out of the write unchanged.
    if gi.exists():
        try:
            existing_lines = gi.read_text(encoding="utf-8", errors="surrogateescape").splitlines()
        except OSError:
            return
    have = {line.strip() for line in existing_lines}
    missing = [e for e in entries if e not in have]
    if not missing:
        return
    block = ["", "# mnemo (project-scoped install)"] + list(missing)
    new_text = "\n".join(existing_lines + block).rstrip("\n") + "\n"
    try:
        # newline="\n": on Windows, text mode would rewrite the user's LF file
        # as CRLF on every init; the file is theirs and comes back byte for
        # byte apart from the entries added.
        with gi.open("w", encoding="utf-8", errors="surrogateescape", newline="\n") as fh:
            fh.write(new_text)
    except OSError:
        pass


def _refresh_hooks(target_settings: Path, *, project: bool, say) -> int:
    """``mnemo init --hooks-only``: rewrite mnemo's hook entries and nothing else.

    A hook shape that changes between versions (#271 added ``Read`` to the
    ``PreToolUse`` matcher) reaches an existing install only through
    ``inject_hooks``, and a full ``mnemo init`` also re-wires the statusLine,
    the MCP server, the commands and the skills — undoing a statusLine the user
    reverted on purpose (#303). This touches ``settings.json``'s mnemo hooks
    alone, after the same backup ``inject_hooks`` always takes.

    Refused where no mnemo hook is installed: adding one there would be a new
    install (a second one, under the plugin), which is ``mnemo init``'s job.
    """
    from mnemo.install import settings as inj

    if not _has_global_mnemo_install(target_settings):
        init_cmd = "mnemo init --project" if project else "mnemo init"
        print(
            f"error: no mnemo hooks in {target_settings} to refresh — run `{init_cmd}` to install",
            file=sys.stderr,
        )
        return 1
    try:
        inj.inject_hooks(target_settings)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    say(f"Hooks refreshed in {target_settings} (previous file backed up alongside it).")
    say("New sessions pick them up; running ones keep the hooks they started with.")
    return 0


def _same_path(a: str, b: Path) -> bool:
    import os
    return bool(a) and os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(str(b)))


def _wire_grok_hooks(*, project: bool, cwd: Path, say) -> int:
    """``mnemo init --host grok``: make Grok fire mnemo's hooks exactly once.

    Grok reads ``~/.claude/settings.json`` itself, so where mnemo is wired for
    Claude nothing is added (a second copy would run every hook twice) and a
    hook file of ours from an earlier run is removed as the duplicate it has
    become. Where Grok sees no mnemo hook, the same commands go in a Grok hook
    file. ``grok inspect --json`` is the source of truth; without the binary the
    Claude settings file is read directly.
    """
    import os
    from mnemo.core.grok import paths as grok_paths
    from mnemo.hosts import grok as grok_host
    from mnemo.install import grok_hooks, settings as inj

    home = grok_paths.grok_home()
    file = grok_hooks.hook_file(home, project=project, cwd=cwd)
    # A global install is asked about from the home directory, where Grok loads
    # no project files: a hook one repo's `.claude/settings.json` carries is not
    # one every other repo's session would get.
    data = grok_host.inspect(home, cwd if project else Path.home())
    if data is not None:
        theirs = [h for h in grok_host.visible_hooks(data)
                  if not _same_path(grok_host.source_path(h), file)
                  and not _same_path(grok_host.source_path(h), file.parent)]
        where = sorted({grok_host.source_path(h) for h in theirs if grok_host.source_path(h)})
        source = ", ".join(where) or "another hook source"
    else:
        claude_settings = Path(os.path.expanduser("~/.claude/settings.json"))
        theirs = [True] if _has_global_mnemo_install(claude_settings) else []
        source = str(claude_settings)

    try:
        if theirs:
            if grok_hooks.uninstall(file):
                say(f"Removed {file}: Grok already loads mnemo's hooks from {source}.")
            say(f"Grok already loads mnemo's hooks from {source}; nothing added, "
                f"so each hook runs once.")
            return 0
        say(f"Writing mnemo's hooks to {file}…")
        grok_hooks.install(file)
    except (inj.SettingsError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if project:
        _ensure_gitignore(cwd, (".grok/hooks/mnemo.json", ".mnemo/"))
        say("Grok runs a project's hook file only in a folder it trusts: "
            "start it once with `grok --trust`, or run /hooks-trust inside it.")
    return 0


def _refresh_grok_hooks(*, project: bool, say) -> int:
    """``mnemo init --host grok --hooks-only``: rewrite mnemo's Grok hook file, and only that."""
    from mnemo.core.grok import paths as grok_paths
    from mnemo.install import grok_hooks, settings as inj

    file = grok_hooks.hook_file(grok_paths.grok_home(), project=project, cwd=Path.cwd())
    if not grok_hooks.is_installed(file):
        flag = " --project" if project else ""
        print(f"error: no mnemo hooks in {file} to refresh — run `mnemo init --host grok{flag}` "
              f"to install (hooks Grok reads from Claude's settings are refreshed by "
              f"`mnemo init --hooks-only`)", file=sys.stderr)
        return 1
    try:
        grok_hooks.install(file)
    except (inj.SettingsError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    say(f"Hooks refreshed in {file}.")
    say("New Grok sessions pick them up; running ones keep the hooks they started with.")
    return 0


def _init_other_host(args: argparse.Namespace, host_name: str) -> int:
    """``mnemo init --host cursor|codex|grok``: vault + MCP registration + rules file.

    No status line, no memory mirror — those are Claude Code seams. Cursor and
    Codex get no hooks either: learning still happens in Claude Code and they
    read what was learned. Grok also gets mnemo's hooks, once (see
    ``_wire_grok_hooks``).
    """
    import os
    from mnemo.cli.commands.export import configured_universal_threshold, print_export_notes
    from mnemo.core import config as cfg_mod, export as export_mod
    from mnemo.core.agent import resolve_agent, resolve_canonical_agent
    from mnemo.hosts import get_host
    from mnemo.hosts.codex import PROJECT_SCOPE_MESSAGE, CodexScopeError
    from mnemo.install import preflight, scaffold

    quiet = bool(args.quiet)
    say = (lambda *a, **k: None) if quiet else print
    project = bool(getattr(args, "project", False))
    cwd = Path.cwd()
    host = get_host(host_name)

    # Scope is a usage error, so it is answered before anything is written.
    if host_name == "codex" and project:
        print(f"error: {PROJECT_SCOPE_MESSAGE}", file=sys.stderr)
        return 2

    # 1. Determine vault root (same precedence as the Claude path, minus the
    #    interactive prompt: these hosts are always wired non-interactively).
    if args.vault_root:
        vault_root = Path(os.path.expanduser(args.vault_root))
    elif project:
        vault_root = cwd / ".mnemo"
    else:
        vault_root = Path(os.path.expanduser("~/mnemo"))

    # 2. Preflight. These hosts never write a Claude settings.json, so its
    #    writability is not theirs to fail on (`check_settings=False`).
    say("Running preflight checks…")
    result = preflight.run_preflight(vault_root=vault_root, check_settings=False)
    for issue in result.issues:
        say(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        say(f"       → {issue.remediation}")
    if not result.ok:
        print("Preflight failed. Resolve the issues above and retry.", file=sys.stderr)
        return 1

    # 3. Scaffold vault + persist the vault root (see cmd_init step 4b).
    say(f"Scaffolding vault at {vault_root}…")
    scaffold.scaffold_vault(vault_root)
    if project:
        cfg_mod.set_vault_root(vault_root, path=vault_root / "mnemo.config.json")
    else:
        cfg_mod.set_vault_root(vault_root)

    # 4. Register the MCP server in the host's own config.
    try:
        reg = host.register_mcp(project=project, cwd=cwd)
    except CodexScopeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if reg.method == "inherited":
        say(f"Grok already reads mnemo's MCP server from {reg.path}; nothing added.")
    else:
        say(f"MCP server registered for {host_name} ({reg.method}) — {reg.path}")
    if reg.note:
        # A note is work the user must still do by hand (the TOML to paste when
        # `codex` is not on PATH). Swallowing it under --quiet leaves a dead
        # install reporting success, so it prints either way.
        print(reg.note)

    # 4b. Grok runs mnemo's hooks too: wire them, unless it already reads them.
    if host_name == "grok":
        rc = _wire_grok_hooks(project=project, cwd=cwd, say=say)
        if rc:
            return rc

    # 5. Write the rules file, so the host has something to read right away.
    project_name = resolve_canonical_agent(str(cwd)).name
    repo_root = Path(resolve_agent(str(cwd)).repo_root).resolve()
    try:
        report = export_mod.run_export(vault_root, project=project_name,
                                       repo_root=repo_root, host=host_name,
                                       universal_threshold=configured_universal_threshold())
    except (export_mod.TargetError, export_mod.MarkerError) as exc:
        # The MCP server is already wired; only the rules file failed, and
        # `mnemo export` is the command that fixes it. Don't fail the install —
        # but say so on stderr even under --quiet, since it is unfinished work.
        print(f"rules file not written: {exc}", file=sys.stderr)
        print(f"  → fix that, then re-run `mnemo export --host {host_name}`", file=sys.stderr)
        return 0
    if report.rules:
        print_export_notes(report)
        rel = report.target.path.relative_to(repo_root).as_posix()
        plural = "s" if len(report.rules) != 1 else ""
        say(f"exported {len(report.rules)} rule{plural} → {rel}")
    else:
        say(f"no rules yet — learn in Claude Code (mnemo learn), "
            f"then `mnemo export --host {host_name}`")

    served = ("MCP tools + the rules file above" if report.rules
              else "MCP tools (the rules file comes with your first export)")
    say("")
    if host_name == "grok":
        say("Grok sessions call mnemo's MCP tools and fire its hooks. Grok loads project "
            "files (.grok/rules) only in a folder it trusts: start it once with "
            "`grok --trust`. Re-run `mnemo export --host grok` after new rules.")
        return 0
    say(f"mnemo learns from Claude Code sessions. In {host_name} it serves what it "
        f"learned: {served}. Re-run "
        f"`mnemo export --host {host_name}` after new rules.")
    return 0


@command("init")
def cmd_init(args: argparse.Namespace) -> int:
    import os
    from mnemo.core import config as cfg_mod, mirror
    from mnemo.install import preflight, scaffold, settings as inj

    quiet = bool(args.quiet)
    say = (lambda *a, **k: None) if quiet else print
    project = bool(getattr(args, "project", False))
    host_name = getattr(args, "host", "claude") or "claude"
    hooks_only = bool(getattr(args, "hooks_only", False))
    if hooks_only and host_name == "grok":
        return _refresh_grok_hooks(project=project, say=say)
    if hooks_only and host_name != "claude":
        print("error: --hooks-only rewrites Claude Code hooks; other hosts have none", file=sys.stderr)
        return 2
    if host_name != "claude":
        return _init_other_host(args, host_name)
    cwd = Path.cwd()

    # Resolve install targets per scope
    if project:
        target_settings = cwd / ".claude" / "settings.json"
        target_mcp = cwd / ".mcp.json"
    else:
        target_settings = Path(os.path.expanduser("~/.claude/settings.json"))
        target_mcp = Path(os.path.expanduser("~/.claude.json"))

    if hooks_only:
        return _refresh_hooks(target_settings, project=project, say=say)

    # Coexistence warn — project install but global is already wired
    if project and _has_global_mnemo_install(Path(os.path.expanduser("~/.claude/settings.json"))):
        say(
            "WARNING: a global mnemo install is already active at ~/.claude/settings.json.\n"
            "         Both will fire in this project — hooks will run twice. Consider running\n"
            "         `mnemo uninstall` first, or proceed if you understand the duplication."
        )
        if not args.yes:
            try:
                confirm = input("Proceed with project install anyway? [y/N]: ").strip().lower()
            except EOFError:
                confirm = ""
            if confirm not in ("y", "yes"):
                print("Aborted by user.", file=sys.stderr)
                return 2

    # 1. Determine vault root
    vault_root: Path
    if args.vault_root:
        vault_root = Path(os.path.expanduser(args.vault_root))
    elif project:
        vault_root = cwd / ".mnemo"
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
    result = preflight.run_preflight(vault_root=vault_root, settings_target=target_settings)
    for issue in result.issues:
        say(f"  [{issue.severity}] {issue.kind}: {issue.message}")
        say(f"       → {issue.remediation}")
    if not result.ok:
        print("Preflight failed. Resolve the issues above and retry.", file=sys.stderr)
        return 1

    # 3. Confirm settings modification (interactive only)
    if not args.yes:
        try:
            confirm = input(f"Modify {target_settings} to install hooks? [y/N]: ").strip().lower()
        except EOFError:
            confirm = ""
        if confirm not in ("y", "yes"):
            print("Aborted by user.", file=sys.stderr)
            return 2

    # 4. Scaffold vault
    say(f"Scaffolding vault at {vault_root}…")
    scaffold.scaffold_vault(vault_root)

    # 4b. Persist vault root.
    # Project installs write `<vault>/mnemo.config.json` (== `<cwd>/.mnemo/mnemo.config.json`),
    # which `default_config_path()` auto-detects via `_find_local_config()`. Global
    # installs keep using the singleton `default_config_path()`.
    if project:
        cfg_mod.set_vault_root(vault_root, path=vault_root / "mnemo.config.json")
    else:
        cfg_mod.set_vault_root(vault_root)

    # 5. Inject hooks
    say(f"Injecting hooks into {target_settings}…")
    try:
        inj.inject_hooks(target_settings)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5b. Register MCP server (global → ~/.claude.json; project → <cwd>/.mcp.json)
    say(f"Registering MCP server in {target_mcp}…")
    try:
        inj.inject_mcp_servers(target_mcp)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5c. Install additive statusLine composer
    say("Installing statusLine composer (additive — preserves your existing line)…")
    try:
        inj.inject_statusline(target_settings, vault_root)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5c'. Keep the transcripts mnemo learns from (#596)
    try:
        _ensure_transcript_retention(args, project=project, vault_root=vault_root, say=say)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5d. Register slash commands (replaces the /plugin install dance)
    target_commands = target_settings.parent / "commands"
    say(f"Registering slash commands in {target_commands}…")
    try:
        inj.inject_slash_commands(target_commands)
    except inj.SettingsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5d'. Skills. The plugin ships these by convention; this path has to
    # write them, or `decompose this for dispatch` finds nothing (#233).
    target_skills = target_settings.parent / "skills"
    say(f"Registering skills in {target_skills}…")
    try:
        inj.inject_skills(target_skills)
    except OSError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # 5e. Project scope — ignore install artifacts in version control
    if project:
        _ensure_gitignore(cwd)

    # 6. Optional initial mirror
    if not args.no_mirror:
        say("Mirroring existing Claude memories…")
        cfg = cfg_mod.load_config(vault_root / "mnemo.config.json")
        try:
            mirror.mirror_all(cfg)
        except Exception as e:
            say(f"  (mirror skipped: {e})")

    autopilot_state = _autopilot_state(vault_root)

    if project:
        say("")
        say("mnemo is ready (project scope).")
        say(f"  Vault:      {vault_root}  (mnemo open)")
        say("  Verify:     mnemo status")
        say(f"  Autopilot:  {autopilot_state}  (toggle: mnemo autopilot on/off)")
        say("")
        say(f"Launch Claude Code in {cwd} to activate the local hooks:")
        say("    claude")
    else:
        say("")
        say("mnemo is ready.")
        say(f"  Vault:      {vault_root}  (mnemo open)")
        say("  Verify:     mnemo status")
        say(f"  Autopilot:  {autopilot_state}  (toggle: mnemo autopilot on/off)")
        say("")
        say("Open Claude Code in any directory — mnemo is active globally.")
    return 0


def _ensure_transcript_retention(args: argparse.Namespace, *, project: bool,
                                 vault_root: Path, say) -> None:
    """Raise Claude Code's ``cleanupPeriodDays`` to mnemo's floor (#596).

    Always in ``~/.claude/settings.json``: Claude Code's startup sweep covers
    every project's transcripts with the value of wherever it was launched, so
    a project file would protect nothing launched elsewhere. A project install
    promises not to write under ``$HOME``, so there it only says what to add.
    ``--no-transcript-retention`` is remembered in mnemo's config, where a
    re-run and ``doctor`` read it.
    """
    from mnemo.core import config as cfg_mod
    from mnemo.install import transcript_retention as tr

    cfg_path = vault_root / "mnemo.config.json" if project else None
    if getattr(args, "no_transcript_retention", False):
        cfg_mod.set_config_value("install.keepTranscriptsDays", 0, path=cfg_path)
        say(f"Leaving Claude Code's {tr.SETTING} alone (--no-transcript-retention, "
            f"remembered as {tr.CONFIG_KEY}: 0).")
        return
    floor = tr.floor_days(cfg_mod.load_config(cfg_path))
    if floor == 0:
        say(f"Leaving Claude Code's {tr.SETTING} alone ({tr.CONFIG_KEY} is 0).")
        return
    target = tr.user_settings_path()
    if project:
        state, value = tr.read_days(target)
        if state in ("invalid", "unreadable") or (state == "valid" and value >= floor):
            return
        days = value if state == "valid" else tr.CLAUDE_DEFAULT_DAYS
        say(f"NOTE: Claude Code deletes transcripts older than {days} days, and mnemo "
            f"learns from them. A project install leaves ~/.claude alone; to keep them,")
        say(f"      {tr.fix_line(floor)} ({tr.opt_out_line()}).")
        return
    outcome = tr.ensure_floor(target, floor)
    if outcome.action == "raised":
        before = (f"unset, so {tr.CLAUDE_DEFAULT_DAYS} days" if outcome.before is None
                  else f"{outcome.before} days")
        say(f"Raised {tr.SETTING} in {target} to {floor} (was {before}): Claude Code "
            f"deletes older transcripts, and mnemo learns from them.")
        say(f"       → {tr.opt_out_line()}")
    elif outcome.action == "unreadable":
        say(f"  (transcript retention skipped: {target} is not a JSON object)")


def _autopilot_state(vault_root: Path) -> str:
    """Return the upper-cased autopilot state (ON/OFF/PAUSED), best-effort."""
    try:
        from mnemo.autopilot.core.kill_switch import get_state
        return get_state(vault_root=vault_root).upper()
    except Exception:
        return "?"
