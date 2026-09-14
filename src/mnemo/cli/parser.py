"""Argparse wiring + the COMMANDS registry + @command decorator.

Split out of the v0.8.x ``mnemo.cli`` monolith by PR H. Each
``cli/commands/*.py`` module imports :data:`COMMANDS` and the
:func:`command` decorator from here and registers its handler at
import time. The package's ``__init__.py`` triggers those imports so
the registry is populated by the time :func:`mnemo.cli.runtime.main`
looks up a handler.
"""
from __future__ import annotations

import argparse
from typing import Callable

COMMANDS: dict[str, Callable[[argparse.Namespace], int]] = {}


# Commands that are real but advanced/maintenance — hidden from
# ``mnemo help`` by default; surfaced via ``mnemo help --all``.
ADVANCED_COMMANDS: frozenset[str] = frozenset({
    "telemetry",
    "recall",
    "recall-sessions",
    "migrate-worktree-briefings",
    "migrate-plugin",
    "dedup-rules",
    "reclassify",
    "reverify",
    "list-enforced",
    "regen-graph-edges",
    "rewrites",
})

# Internal subparsers that should never appear in user-facing help (wired
# only by hooks, MCP server, statusLine composer, briefing pipeline).
INTERNAL_COMMANDS: frozenset[str] = frozenset({
    "briefing",
    "hook",
    "mcp-server",
    "statusline",
    "statusline-compose",
})


def command(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], int]) -> Callable:
        COMMANDS[name] = fn
        return fn
    return deco


def _build_parser() -> argparse.ArgumentParser:
    from mnemo._version import resolve_version
    _v = resolve_version()
    p = argparse.ArgumentParser(prog="mnemo", description="The Obsidian that populates itself.")
    p.add_argument("--version", "-V", action="version", version=f"mnemo {_v}")
    sub = p.add_subparsers(dest="command")

    init = sub.add_parser("init", help="first-run setup (idempotent)")
    init.add_argument("--yes", "-y", action="store_true", help="skip prompts (for automation)")
    init.add_argument("--vault-root", type=str, default=None, help="override vault location")
    init.add_argument("--no-mirror", action="store_true", help="skip initial Claude memory mirror")
    init.add_argument("--quiet", action="store_true", help="suppress informational output")
    init.add_argument(
        "--project", "--local", dest="project", action="store_true",
        help="install only in the current project (writes <cwd>/.claude/settings.json + <cwd>/.mcp.json + <cwd>/.mnemo/ instead of $HOME)",
    )
    init.add_argument(
        "--host", choices=["claude", "cursor", "codex"], default="claude",
        help="which tool to wire: claude (default: hooks + MCP), cursor or codex (MCP + rules file only)",
    )

    status = sub.add_parser("status", help="vault state + hook health + recent activity")
    status.add_argument(
        "--scope", choices=["project", "global", "all"], default="all",
        help="which install scope to report (default: all)",
    )
    sessions = sub.add_parser("sessions", help="live queue of Claude Code background sessions")
    sessions.add_argument("--json", action="store_true", help="machine-readable listing")
    sessions.add_argument("--watch", action="store_true", help="redraw until Ctrl-C (ignored with --json; see --interval, --append)")
    sessions.add_argument("--all", action="store_true", help="every repo, not just this one")
    sessions.add_argument(
        "--append", action="store_true",
        help="watch by appending only the rows that changed, instead of clearing the screen",
    )
    sessions.add_argument(
        "--interval", type=float, default=2.0, metavar="SECONDS",
        help="seconds between watch ticks (default: 2)",
    )
    sessions.add_argument(
        "--consume-unblocks",
        dest="consume_unblocks",
        action="store_true",
        help="learn from every session that was answered while blocked, then print what it taught",
    )
    session_p = sub.add_parser(
        "session", help="recent actions of one background session")
    session_p.add_argument("short_id", metavar="SHORT_ID",
                           help="session short id (a unique prefix is enough)")
    session_p.add_argument("--limit", type=int, default=15,
                           help="how many recent actions to show (default: 15)")
    session_p.add_argument("--follow", "-f", action="store_true",
                           help="keep printing new actions as they arrive, until Ctrl-C")
    session_p.add_argument("--interval", type=float, default=2.0, metavar="SECONDS",
                           help="seconds between polls when following (default: 2)")
    dispatch_p = sub.add_parser(
        "dispatch",
        help="spawn a background child per issue, or per piece of a contract")
    # nargs="*", not "+": --contract has to be able to stand alone. The two
    # inputs cannot share a mutually exclusive group (argparse refuses a
    # variadic positional in one), so the command itself refuses the overlap.
    dispatch_p.add_argument("issues", nargs="*", type=int, metavar="ISSUE",
                            help="GitHub issue number(s) to dispatch")
    # nargs="?": --contract's argument is optional so `--contract --example`
    # reads as "the example of a contract" rather than needing a second flag
    # spelling. Bare `--contract` with neither a path nor --example is refused
    # by the command, which can say what is missing; argparse cannot.
    dispatch_p.add_argument("--contract", metavar="PATH", nargs="?", const="",
                            help="dispatch each piece of a decomposition contract "
                                 "(format: skills/decomposing-for-dispatch/SKILL.md)")
    dispatch_p.add_argument("--example", action="store_true",
                            help="print a commented example contract and exit; "
                                 "redirect it to a file to start one")
    dispatch_p.add_argument("--dry-run", dest="dry_run", action="store_true",
                            help="print the worktree and branch per child without spawning")
    deliver_p = sub.add_parser(
        "deliver",
        help="review what a dispatch produced, or push + open a PR for named children")
    # Ids are positional and there is deliberately no --all: naming a child is
    # the approval, and one flag approving N children is the failure #215 is
    # about. --review is read-only and refuses to run alongside them.
    deliver_p.add_argument("ids", nargs="*", metavar="ID",
                           help="session short id, issue number or piece slug to deliver")
    deliver_p.add_argument("--review", action="store_true",
                           help="read-only: every dispatch worktree, its branch, and whether it is deliverable")
    land_p = sub.add_parser(
        "land",
        help="a delivered contract's pieces in landing order with their PRs and "
             "signatures; --merge rehearses the merge and then lands them")
    land_p.add_argument("contract", metavar="PATH",
                        help="the contract that was dispatched")
    # Read-only by default: the view is the check that makes the merge safe,
    # and it is useful on its own. --merge is the one irreversible step, and
    # it runs only after a rehearsal in a throwaway worktree passed in full.
    land_p.add_argument("--merge", action="store_true",
                        help="rehearse the merge in order (suite at each step), then "
                             "gh pr merge each piece; stops at the first failure")
    land_p.add_argument("--suite", metavar="CMD", default=None,
                        help="the test command the rehearsal runs after each merge "
                             "(default: python -m pytest -q)")
    land_p.add_argument("--method", choices=["squash", "merge", "rebase"],
                        default="squash", help="gh pr merge method (default: squash)")
    land_p.add_argument("--admin", action="store_true",
                        help="pass --admin to gh pr merge (a branch protection you own "
                             "and are choosing to bypass; never implied)")
    sub.add_parser("doctor", help="full diagnostic with actionable fixes")
    autopilot = sub.add_parser("autopilot", help="autonomous monitoring + self-fix")
    autosub = autopilot.add_subparsers(dest="autopilot_action")
    autosub.required = True
    autosub.add_parser("on", help="enable autopilot")
    autosub.add_parser("off", help="disable autopilot + revoke jobs")
    pause_p = autosub.add_parser("pause", help="temporarily stop autopilot")
    pause_p.add_argument("--hours", type=int, default=24,
                         help="pause duration in hours (default 24)")
    autosub.add_parser("status", help="show autopilot state + jobs + budget")
    digest_p = autosub.add_parser("digest", help="generate weekly health digest")
    digest_p.add_argument(
        "--post", action="store_true",
        help="also create a GitHub issue (label: mnemo:digest)",
    )
    digest_p.add_argument(
        "--since", type=str, default="7d",
        help="time window for telemetry (e.g. 7d, 30d — default 7d)",
    )
    autosub.add_parser("collect-misses", help="write rule_candidate proposals from recall misses")
    # self-fix sub-subparser
    selffix_p = autosub.add_parser("self-fix", help="detect and fix doctor warnings, dead rules, telemetry bugs")
    selffix_p.add_argument("--dry-run", action="store_true", help="list fixable items without opening a PR")
    selffix_sub = selffix_p.add_subparsers(dest="selffix_action")
    selffix_sub.required = False
    selffix_doctor = selffix_sub.add_parser("doctor", help="fix auto-fixable doctor warnings")
    selffix_doctor.add_argument("--dry-run", action="store_true", help="list fixable warnings without opening a PR")
    selffix_sweep = selffix_sub.add_parser("sweep", help="archive dead rules (0 hits in 90d)")
    selffix_sweep.add_argument("--dry-run", action="store_true", help="list dead rules without archiving")
    selffix_telemetry = selffix_sub.add_parser("telemetry", help="open draft PR for telemetry anomalies")
    selffix_telemetry.add_argument("--dry-run", action="store_true", help="list anomalies without opening a PR")
    tune = autosub.add_parser("tune", help="run self-tuner (BM25F grid search + reflex calibration)")
    tunesub = tune.add_subparsers(dest="tune_target")
    tunesub.required = False
    for _tune_name in ("bm25", "reflex", "all"):
        _tp = tunesub.add_parser(_tune_name, help=f"run {_tune_name} tuner")
        _tp.add_argument("--dry-run", action="store_true", help="print proposal without writing files")
        if _tune_name in ("reflex", "all"):
            _tp.add_argument("--project", type=str, default=None,
                             help="limit reflex calibration to this project")
    # Tier 3: propose + preempt + proposals queue
    propose_p = autosub.add_parser("propose", help="run end-of-session rule analysis")
    propose_p.add_argument("--session-id", dest="session_id", required=True,
                           help="session ID to analyze")
    autosub.add_parser("preempt", help="predict next-action rules and write preempt-cache.json")
    proposals_p = autosub.add_parser("proposals", help="manage the proposals queue")
    propsub = proposals_p.add_subparsers(dest="proposals_action")
    propsub.required = True
    list_p = propsub.add_parser("list", help="list proposals")
    list_p.add_argument("--status", default=None, help="filter by status")
    list_p.add_argument("--kind", default=None, help="filter by kind")
    list_p.add_argument("--project", default=None, help="filter by project")
    review_p = propsub.add_parser("review", help="review a proposal interactively")
    review_p.add_argument("--id", default=None, dest="id", help="proposal ID to review")
    review_p.add_argument("--accept", action="store_true", help="accept without prompt")
    review_p.add_argument("--reject", action="store_true", help="reject without prompt")
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
    bf = sub.add_parser("backfill", help="populate the vault from past session transcripts")
    # Mutually exclusive: they are answers to the same question, and the
    # implementation can only honour one. Silently letting `--project` win
    # over an `--all` the user typed is worse than saying so.
    bf_scope = bf.add_mutually_exclusive_group()
    bf_scope.add_argument("--all", action="store_true", help="every project, not just this repo")
    bf.add_argument("--dry-run", action="store_true", help="show what would be harvested, write nothing")
    bf_scope.add_argument("--project", type=str, default=None, help="limit to one project by name")
    bf.add_argument("--limit", type=int, default=None, help="cap the number of sessions")
    bf.add_argument("--yes", "-y", action="store_true", help="skip the confirmation prompt")
    bf.add_argument(
        "--retry-failed", action="store_true",
        help="clear previously failed sessions so they are attempted again",
    )
    bf.add_argument("--install-run", action="store_true", help=argparse.SUPPRESS)
    imp = sub.add_parser(
        "import",
        help="stage a published rules tree (.mnemo-shared/) into this vault, in shared/_inbox/ for review",
    )
    imp.add_argument(
        "path", nargs="?", default=None,
        help="a .mnemo-shared/ directory (default: this repo's)",
    )
    imp.add_argument("--dry-run", action="store_true", help="print every routing decision, write nothing")
    # Hidden subparsers: omit ``help=`` entirely so argparse doesn't create a
    # ChoicesPseudoAction for them. Passing ``help=argparse.SUPPRESS`` was the
    # documented way to hide a subparser, but Python 3.14 regressed it: the
    # listing still appears with the literal "==SUPPRESS==" string as the help
    # column. Skipping ``help=`` works on every supported Python version.
    briefing = sub.add_parser("briefing")
    # Positionals are optional so `mnemo briefing --prune` parses; the
    # command itself rejects a call that has neither (#116).
    briefing.add_argument("jsonl_path", type=str, nargs="?")
    briefing.add_argument("agent", type=str, nargs="?")
    briefing.add_argument("--prune", action="store_true")
    briefing.add_argument("--dry-run", action="store_true")
    # Wired into settings.json by standalone installs, which have no
    # `python -m mnemo.hooks.<event>` module path to invoke. Choices come from
    # the same table that defines what gets installed, so the two cannot drift.
    from mnemo.install.settings import HOOK_MODULES
    hook = sub.add_parser("hook")
    hook.add_argument("event", choices=sorted(HOOK_MODULES))
    sub.add_parser("mcp-server")
    # Bare `statusline` stays internal (the composer calls it every render);
    # the two flags are the user-facing opt-in, since a plugin cannot declare
    # a status line for the user.
    statusline = sub.add_parser("statusline")
    statusline.add_argument("--install", action="store_true",
                            help="wire the mnemo status line into ~/.claude/settings.json")
    statusline.add_argument("--remove", action="store_true",
                            help="remove it and restore any status line it replaced")
    sub.add_parser("statusline-compose")
    uninstall = sub.add_parser("uninstall", help="remove hooks (keeps vault)")
    uninstall.add_argument("--yes", "-y", action="store_true")
    uninstall.add_argument(
        "--project", "--local", dest="project", action="store_true",
        help="remove only the project-local install (<cwd>/.claude/settings.json + <cwd>/.mcp.json)",
    )
    uninstall.add_argument(
        "--host", choices=["claude", "cursor", "codex"], default="claude",
        help="which tool's MCP registration to remove (default: claude)",
    )
    why = sub.add_parser("why", help="explain the last few reflex decisions (what fired, what nearly did, why not)")
    why.add_argument("--limit", type=int, default=10, help="how many decisions to show (default 10)")
    why.add_argument("--all-projects", action="store_true", help="include decisions from every repo, not just this one")
    why.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    replay = sub.add_parser(
        "replay",
        help="replay your own transcripts against your vault: how often a rule from an earlier session would have come back",
    )
    replay.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    replay.add_argument("--project", default=None, help="only prompts typed in this project (default: every project)")
    telemetry = sub.add_parser("telemetry", help="summarize MCP access log (calls + zero-hit per project)")
    telemetry.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall = sub.add_parser("recall", help="measure retrieval ranking vs historical access-log queries")
    recall.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall.add_argument("--no-bootstrap", action="store_true", help="reuse existing cases.json instead of regenerating")
    recall_sessions = sub.add_parser("recall-sessions", help="recall harness built from extraction sessions (delta detector, not comparable to `recall`)")
    recall_sessions.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall.add_argument("--window-s", type=float, default=120.0, help="list→read pair window in seconds (default 120)")
    migrate = sub.add_parser(
        "migrate-worktree-briefings",
        help="move orphan worktree briefings to canonical dir, rewriting every citation (uses name-prefix heuristic — always --dry-run first)",
    )
    migrate.add_argument(
        "--repos", nargs="+", default=[],
        help="canonical repo paths whose worktree briefings should be relocated",
    )
    migrate_plugin = sub.add_parser(
        "migrate-plugin",
        help="remove a pre-plugin `mnemo init` install so hooks stop firing twice",
    )
    migrate_plugin.add_argument("--dry-run", action="store_true", help="report without changing anything")
    migrate.add_argument(
        "--dry-run", action="store_true",
        help="list planned moves without performing them",
    )
    dedup = sub.add_parser(
        "dedup-rules",
        help="merge shared rule files that share the same name (dry-run default)",
    )
    dedup.add_argument(
        "--apply", action="store_true",
        help="execute the plan (default: dry-run)",
    )
    rewrites_p = sub.add_parser(
        "rewrites",
        help="review and accept staged _inbox rewrites of live rules (lists only; writes need a flag)",
    )
    rewrites_p.add_argument(
        "--apply-safe", action="store_true",
        help="merge every insert-only rewrite (nothing live is dropped)",
    )
    rewrites_p.add_argument(
        "--show", metavar="KEY",
        help="print the full diff for one rewrite (e.g. project/clubinho__sprints-github)",
    )
    rewrites_p.add_argument(
        "--accept", metavar="KEY",
        help="accept one mixed or full rewrite, taking the proposal's body",
    )
    rewrites_p.add_argument(
        "--reject", metavar="KEY",
        help="delete one staged proposal, leaving the live rule untouched",
    )
    rewrites_p.add_argument(
        "--undo", metavar="RUN_ID",
        help="restore every file a previous apply touched, byte for byte",
    )
    reverify = sub.add_parser(
        "reverify",
        help="re-brief the sessions behind label-only verified rules and let today's evidence gate decide (dry run; --apply to execute)",
    )
    reverify.add_argument("--apply", action="store_true", help="execute the saved dry run (no LLM calls)")
    reverify.add_argument("--undo", metavar="RUN_ID", help="restore every file a previous --apply touched, byte for byte")
    reverify.add_argument("--fresh", action="store_true",
                          help="re-brief every session again instead of reusing the scratch briefings of the last dry run")
    reverify.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    reclass = sub.add_parser(
        "reclassify",
        help="grade legacy feedback rules with an LLM (plan by default; --apply to execute)",
    )
    reclass.add_argument(
        "--apply", action="store_true",
        help="execute the saved plan (no LLM calls)",
    )
    reclass.add_argument(
        "--undo", metavar="RUN_ID",
        help="restore every file a previous --apply touched, byte for byte",
    )
    reclass.add_argument(
        "--limit", type=int, metavar="N",
        help="grade at most N rules (useful for a trial run)",
    )
    reclass.add_argument(
        "--yes", "-y", action="store_true",
        help="skip the confirmation prompt",
    )
    learn = sub.add_parser(
        "learn",
        help="learn from this session now (briefing + extraction, synchronously)",
    )
    learn.add_argument(
        "--session", metavar="ID", default=None,
        help="learn from this session id instead of the newest one",
    )
    learn.add_argument(
        "--dry-run", action="store_true",
        help="report which transcript would be read, then stop",
    )
    export = sub.add_parser(
        "export",
        help="write this project's rules to a file Claude Code / Cursor / Codex loads",
    )
    export.add_argument("--host", choices=["claude", "cursor", "codex"], default="claude",
                        help="which tool will read the file (default: claude)")
    export.add_argument("--target", choices=["auto", "rules", "claude-md", "agents-md"], default="auto",
                        help="rules file (default per host), or a managed block in CLAUDE.md / AGENTS.md")
    export.add_argument("--project", default=None, help="export another project's rules instead of the cwd's")
    export.add_argument("--types", default="feedback,user", help="page types to include (default: feedback,user)")
    export.add_argument("--all-types", action="store_true",
                        help="include reference and project pages too (cannot combine with --types)")
    export.add_argument("--limit", type=int, default=None, metavar="N", help="keep only the first N after ordering")
    export.add_argument("--full", action="store_true",
                        help="write each rule's whole body (default: its first paragraph and your quote)")
    export.add_argument("--dry-run", action="store_true", help="print the block, write nothing")
    export.add_argument("--remove", action="store_true", help="delete the exported file / block and its manifest")
    publish = sub.add_parser(
        "publish",
        help="write this repo's rules into the repo (.mnemo-shared/) for a teammate's `mnemo import`",
    )
    publish.add_argument("--project", default=None, help="publish another project's rules instead of the cwd's")
    publish.add_argument("--types", default="feedback",
                         help="page types to include (default: feedback; user pages carry names and emails)")
    publish.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    publish.add_argument("--remove", action="store_true", help="delete this vault's published files and the manifest")
    disable = sub.add_parser("disable-rule", help="set runtime: false on a rule's frontmatter by slug")
    disable.add_argument("slug", help="rule slug (from the block message or `mnemo list-enforced`)")
    sub.add_parser("list-enforced", help="audit rules with enforce blocks (can block tool calls)")
    sub.add_parser(
        "regen-graph-edges",
        help="refresh `## Sources` wikilink section on shared rules (idempotent, Obsidian graph)",
    )
    help_p = sub.add_parser("help", help="list commands")
    help_p.add_argument(
        "--all", action="store_true",
        help="include advanced/maintenance commands",
    )
    return p
