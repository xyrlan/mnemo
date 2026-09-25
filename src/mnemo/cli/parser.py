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

# The one sentence mnemo is described by everywhere a human reads it before
# using it: this --help, ``pyproject.toml``'s ``description``, the plugin
# manifest, and the README's opening line (#437). Keep the four in sync —
# ``tests/unit/test_cli_help.py`` pins it so they can't drift again the way
# "The Obsidian that populates itself" did from the README's actual pitch.
TAGLINE = (
    "Claude Code forgets your corrections. mnemo doesn't — and the "
    "sessions it fans out for you start out knowing them."
)


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
    "stale",
    "redact",
    "twins",
})

# Internal subparsers that should never appear in user-facing help (wired
# only by hooks, MCP server, statusLine composer, briefing pipeline).
INTERNAL_COMMANDS: frozenset[str] = frozenset({
    "briefing",
    "child-notices",
    "child-report",
    "hook",
    "mcp-server",
    "pr-follow",
    "statusline",
    "statusline-compose",
})


def command(name: str) -> Callable:
    def deco(fn: Callable[[argparse.Namespace], int]) -> Callable:
        COMMANDS[name] = fn
        return fn
    return deco


def filter_subparsers(parser: argparse.ArgumentParser, *, show_all: bool) -> None:
    """Strip internal (and optionally advanced) subparsers from help output.

    Both the `{a,b,c}` choices header and the per-command listing are derived
    from the subparsers action. We mutate that action in-place so the only
    visible commands are the curated user-facing set.
    """
    sub = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    if sub is None:
        return
    hidden = set(INTERNAL_COMMANDS)
    if not show_all:
        hidden |= ADVANCED_COMMANDS
    sub._choices_actions = [ca for ca in sub._choices_actions if ca.dest not in hidden]
    visible = [n for n in sub.choices.keys() if n not in hidden]
    sub.metavar = "{" + ",".join(visible) + "}"


def print_curated_help(parser: argparse.ArgumentParser, *, show_all: bool) -> None:
    """``mnemo help`` and (curated) ``mnemo --help`` share this render.

    Before #437, ``-h``/``--help`` was argparse's own default action, which
    dumps every subparser — the raw 47-verb listing the issue is about.
    ``mnemo help`` already curated that list; this makes ``--help`` agree
    with it instead of bypassing it.
    """
    filter_subparsers(parser, show_all=show_all)
    parser.print_help()
    if not show_all and ADVANCED_COMMANDS:
        print()
        print(
            f"({len(ADVANCED_COMMANDS)} advanced commands hidden — "
            "`mnemo help --all` to see them.)"
        )


class _CuratedHelpAction(argparse.Action):
    """Top-level ``-h``/``--help``, wired in place of argparse's default.

    argparse's own help action prints every subparser regardless of
    ``ADVANCED_COMMANDS``/``INTERNAL_COMMANDS``; this renders the same
    curated view ``mnemo help`` does, so asking for help either way says the
    same thing.
    """

    def __init__(self, option_strings, dest=argparse.SUPPRESS,
                 default=argparse.SUPPRESS, help=None) -> None:
        super().__init__(
            option_strings=option_strings, dest=dest, default=default,
            nargs=0, help=help,
        )

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        print_curated_help(parser, show_all=False)
        parser.exit()


def _build_parser() -> argparse.ArgumentParser:
    from mnemo._version import resolve_version
    from mnemo.core.claude_cli import EFFORT_LEVELS
    from mnemo.core.mcp.rerank import PROVIDERS as _RERANK_PROVIDERS
    from mnemo.core.dedup_judge import (
        DEFAULT_MAX_CHARS as _DEDUP_MAX_CHARS,
        DEFAULT_MAX_PAIRS as _DEDUP_MAX_PAIRS,
        DEFAULT_TIMEOUT_S as _DEDUP_TIMEOUT_S,
        DEFAULT_WORKERS as _DEDUP_WORKERS,
        DUPLICATE_SCORE as _DEDUP_DUPLICATE_SCORE,
    )
    # ``friction`` registers itself here rather than in ``cli/commands/__init__``:
    # the friction-loop contract left that file outside the piece that owns the
    # command. Every entry point builds the parser before it looks a handler up.
    from mnemo.cli.commands import friction as _friction  # noqa: F401
    _v = resolve_version()
    # add_help=False: the default -h/--help would dump every subparser
    # unfiltered. _CuratedHelpAction below replaces it with the same curated
    # view `mnemo help` prints (#437).
    p = argparse.ArgumentParser(prog="mnemo", description=TAGLINE, add_help=False)
    p.add_argument(
        "-h", "--help", action=_CuratedHelpAction,
        help="show this help message and exit",
    )
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
        "--hooks-only", action="store_true",
        help="only rewrite mnemo's hooks in settings.json to this version's shape (leaves config, statusLine, MCP, commands alone)",
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
        "--stale", action="store_true",
        help="also list finished sessions whose worktree is gone (hidden by default)",
    )
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
    # No default and no allowlist: omitted, the spawn is the byte-identical
    # command it was before, and the child resolves whatever the machine's
    # settings say. Claude Code owns the vocabulary of model ids (#268).
    dispatch_p.add_argument("--model", metavar="MODEL",
                            help="model every child of this dispatch runs on "
                                 "(alias like haiku/sonnet/opus, or a full id); "
                                 "a contract piece's own `model:` wins over it")
    # Unlike --model, a closed list: Claude Code's own (`bg-effort-flag`). It
    # ignores an unknown level with a warning, so argparse refuses the typo
    # here, before a child silently runs on the default (#351). No default:
    # omitted, no `--effort` is sent at all.
    dispatch_p.add_argument("--effort", metavar="LEVEL", choices=EFFORT_LEVELS,
                            help="reasoning effort every child of this dispatch runs at: "
                                 f"{', '.join(EFFORT_LEVELS)}; "
                                 "a contract piece's own `effort:` wins over it")
    # The maintainer's standing answer to "may I push / open a PR?" (#317),
    # given once here because this command is the one message a child reads
    # as the maintainer's own. Omitted, it is `pr` since 2026-09-16: every
    # grant ever recorded was push or push,pr, and the empty default left
    # children finished, unpublished and running. `--may none` restores the
    # withheld prompt, byte-identical to before, and `mnemo deliver`
    # publishes. `merge` is refused by the command.
    dispatch_p.add_argument("--may", metavar="WHAT",
                            help="what every child may publish once its suite passes, "
                                 "without asking: push, or pr (push + open the PR); "
                                 "defaults to pr, `none` withholds; "
                                 "a contract piece's own `may:` wins over it")
    # A posture, not a permission: it changes what the child is asked to do and
    # closes its file-editing tools, where `--may` only decides what it may
    # publish. The two are refused together in `cmd_dispatch` — a child with
    # nothing to publish cannot be given a publishing grant.
    # The help says what is closed, never that the child "cannot write": it can,
    # through the shell, measured 2026-09-17. Overstating that here is how a
    # maintainer ends up trusting the flag for something it does not do.
    dispatch_p.add_argument("--read-only", dest="read_only", action="store_true",
                            help="the child investigates the issue and comments its "
                                 "finding instead of building; Edit/Write/NotebookEdit "
                                 "are closed (it can still write through the shell) and "
                                 "it publishes nothing, so --may is refused with it")
    # Opt back *in* to the maintainer's profile. The default is lean (#270)
    # because a child needs the vault and the repo, not nine plugins; this is
    # for the child that genuinely needs one of them. Orthogonal to --model:
    # one picks who the child is, the other picks what it loads.
    dispatch_p.add_argument("--full-profile", dest="full_profile", action="store_true",
                            help="give children the full user profile (plugins, all MCP "
                                 "servers, all skills) instead of the lean default")
    # #449: #439's pilot. Two children, one prompt, one base commit, neither
    # told of the other and neither publishing; `mnemo twins` reads them blind.
    dispatch_p.add_argument("--twins", action="store_true",
                            help="run one issue as two blind children that publish "
                                 "nothing; judge them with `mnemo twins show`")
    twins_p = sub.add_parser(
        "twins",
        help="issues run twice with `dispatch --twins`: list them, read a pair "
             "blind, record which you would merge")
    twins_p.add_argument("action", nargs="?", default="list",
                         choices=("list", "show", "prefer"),
                         help="list (default), show PAIR, or prefer PAIR A|B|tie")
    twins_p.add_argument("pair", nargs="?", metavar="PAIR",
                         help="pair id, or the issue number when it has one pair")
    twins_p.add_argument("answer", nargs="?", metavar="A|B|tie",
                         help="which diff you would merge; tie when you cannot say")
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
    # Not an --all in disguise: it pushes nothing and approves nothing. The
    # briefing comes from SessionEnd, which only a stopped child fires, so a
    # child that had nothing to deliver still has to be stopped to be
    # remembered — the one case delivering could never reach.
    deliver_p.add_argument("--stop-done", dest="stop_done", action="store_true",
                           help="stop every finished child in this repo's dispatch "
                                "worktrees, delivered or not — a stopped child is "
                                "the one that writes its briefing")
    resume_p = sub.add_parser(
        "resume",
        help="wake the dispatched children the account's limit stopped, once it has reset")
    # Ids are optional, unlike `deliver`'s: waking publishes nothing and grants
    # nothing, so naming one narrows the sweep rather than approving it. See
    # cli/commands/resume.py for why "one command for all of them" is the
    # feature and not a `--all` in disguise.
    resume_p.add_argument("ids", nargs="*", metavar="ID",
                          help="session short id (a unique prefix is enough) or "
                               "issue number; omitted, every stalled child in "
                               "this repo whose reset has passed")
    resume_p.add_argument("--all", action="store_true",
                          help="every repo, not just this one")
    resume_p.add_argument("--dry-run", dest="dry_run", action="store_true",
                          help="print what would be woken and spend nothing")
    # Not a second way to wake: the same pass, waiting for the clock instead
    # of being run once it has passed. SessionStart starts one of these on its
    # own (#396); a maintainer runs it by hand to watch a dispatch out.
    resume_p.add_argument("--watch", action="store_true",
                          help="stay running and wake each child as its own reset "
                               "comes round; exits when nothing is left to watch")
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
    bf.add_argument(
        "--extract", action="store_true",
        help="after the sweep, run the first extraction for that project",
    )
    bf.add_argument(
        "--json", action="store_true",
        help="with --dry-run: print the estimate as one JSON document",
    )
    bf.add_argument(
        "--progress-json", action="store_true",
        help="print progress as JSON lines (harvest, extract, done) on stdout",
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
    # #426: spawned detached by a dispatched child's SessionEnd; never typed.
    child_report = sub.add_parser("child-report")
    child_report.add_argument("short_id", type=str)
    child_report.add_argument("--parent", type=str, required=True)
    child_report.add_argument("--cwd", type=str, default=None)
    child_report.add_argument("--transcript", type=str, default=None)

    # #436: spawned detached by a dispatched child's SessionEnd (and restarted
    # by SessionStart); never typed. Takes no arguments: its work is the ledger.
    sub.add_parser("pr-follow")

    # #502: spawned detached by SessionStart and `mnemo dispatch`; never typed.
    # Tells a parent about a child that stopped without its SessionEnd notice.
    sub.add_parser("child-notices")

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
        help="replay your own transcripts against your vault: how often a rule from an earlier session would have come back (local, lexical gates only — it does not simulate reflex.judge)",
    )
    replay.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    replay.add_argument("--project", default=None, help="only prompts typed in this project (default: every project)")
    stale = sub.add_parser(
        "stale",
        help="live rules that cite a file no longer in this repo at HEAD",
    )
    stale.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    stale.add_argument("--project", default=None, help="rules attributed to this project (default: this repo)")
    stale.add_argument("--repo", default=None, help="check against this repo instead of the current one")
    stale.add_argument("--ref", default=None, help="git ref to check against (default HEAD)")
    stale.add_argument("--why", action="store_true", help="why cited symbols are counted, not checked")
    redact_p = sub.add_parser(
        "redact",
        help="find passwords and API keys already written into pages and briefings (values never shown)",
    )
    redact_p.add_argument("--apply", action="store_true", help="replace them with [redacted] in place")
    redact_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    telemetry = sub.add_parser("telemetry", help="summarize MCP access log (calls + zero-hit per project)")
    telemetry.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall = sub.add_parser("recall", help="measure retrieval ranking vs historical access-log queries")
    recall.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    recall.add_argument("--no-bootstrap", action="store_true", help="reuse existing cases.json instead of regenerating")
    rerank_p = sub.add_parser(
        "rerank",
        help="the opt-in rerank of list_rules_by_topic: whether it is on, where its key comes from, what it has done",
    )
    rerank_mode = rerank_p.add_mutually_exclusive_group()
    rerank_mode.add_argument(
        "--setup", action="store_true",
        help="consent, then read a key (never echoed, never an argument), test it with one request, and turn the stage on",
    )
    rerank_mode.add_argument(
        "--off", action="store_true",
        help="turn both stages off: recall.rerank.provider and reflex.judge.provider back to \"none\", and the stored key off this machine",
    )
    rerank_mode.add_argument(
        "--reflex", choices=("on", "off"), default=None,
        help="the per-prompt judge (#412): `on` says what it sends, asks for consent and needs a key already set up; `off` sets reflex.judge.provider to \"none\"",
    )
    rerank_p.add_argument(
        "--provider", choices=[p for p in _RERANK_PROVIDERS if p != "none"],
        default="typesafe", help="which provider --setup configures (default: typesafe)",
    )
    rerank_p.add_argument(
        "--key-stdin", dest="key_stdin", action="store_true",
        help="read the key from stdin instead of prompting (for scripts; implies you also pass --yes)",
    )
    rerank_p.add_argument("--yes", "-y", action="store_true", help="consent without a prompt (required off a tty)")
    rerank_p.add_argument(
        "--judge", action="store_true",
        help="with --setup: also turn the per-prompt judge on, without asking (its own consent; the only way to turn it on off a tty or with --key-stdin)",
    )
    rerank_p.add_argument("--days", type=int, default=14, help="window for the access-log summary (default: 14)")
    rerank_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
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
        help="merge shared rule files that share the same name, or queue the ones a judge says say the same thing (dry-run default)",
    )
    dedup_mode = dedup.add_mutually_exclusive_group()
    dedup_mode.add_argument(
        "--apply", action="store_true",
        help="execute the name-keyed plan (default: dry-run)",
    )
    dedup_mode.add_argument(
        "--judge", action="store_true",
        help="the other dedupe: ask a judge that reads each pair which live rules state the same lesson (dry-run unless --send)",
    )
    dedup_mode.add_argument(
        "--merge", nargs=2, metavar=("KEEP", "DROP"),
        help="fold DROP into KEEP the way `reclassify --apply` executes a merge: sources unioned, DROP archived, undoable",
    )
    dedup.add_argument("--project", help="--judge: only this project's buckets (default: every project)")
    dedup.add_argument("--topic", help="--judge: only this topic (default: every topic)")
    dedup.add_argument(
        "--send", action="store_true",
        help="--judge: post both rule bodies of every pair to the provider (a third party). Without it nothing leaves the machine",
    )
    dedup.add_argument(
        "--at", type=float, default=_DEDUP_DUPLICATE_SCORE,
        help="--judge: the score a pair needs to enter the queue (default: %(default)s, halfway between \"related\" and \"same lesson\")",
    )
    dedup.add_argument(
        "--max-pairs", dest="max_pairs", type=int, default=_DEDUP_MAX_PAIRS,
        help="--judge: refuse to send more than this many pairs (default: %(default)s)",
    )
    dedup.add_argument(
        "--max-chars", dest="max_chars", type=int, default=_DEDUP_MAX_CHARS,
        help="--judge: body prefix judged per rule (default: %(default)s)",
    )
    dedup.add_argument("--workers", type=int, default=_DEDUP_WORKERS, help="--judge: requests in flight (default: %(default)s)")
    dedup.add_argument("--timeout", type=float, default=_DEDUP_TIMEOUT_S, help="--judge: seconds per request (default: %(default)s)")
    dedup.add_argument("--json", action="store_true", help="--judge: emit machine-readable JSON")
    inbox_p = sub.add_parser(
        "inbox",
        help="the review queue: staged pages for this project, and the two acts that clear it",
    )
    inbox_p.add_argument(
        "--all", action="store_true",
        help="every project, not just the one you are standing in",
    )
    inbox_p.add_argument(
        "--show", metavar="KEY",
        help="print one staged page (e.g. reference/mnemo__briefing-is-the-channel)",
    )
    inbox_p.add_argument(
        "--promote", metavar="KEY", nargs="*",
        help="move staged pages into shared/<type>/, where recall reaches them",
    )
    inbox_p.add_argument(
        "--drop", metavar="KEY", nargs="*",
        help="archive staged pages and take them out of the queue",
    )
    inbox_p.add_argument(
        "--keys-stdin", dest="keys_stdin", action="store_true",
        help="--promote/--drop: read the keys from stdin, one per line",
    )
    inbox_p.add_argument(
        "--review", action="store_true",
        help="review the queue in the terminal: everything checked, uncheck what is wrong, keep the rest",
    )
    inbox_p.add_argument(
        "--origin", choices=("backfill", "any"), default="any",
        help="list or review only pages carrying this origin (default: %(default)s)",
    )
    inbox_p.add_argument(
        "--project", metavar="P",
        help="the project whose queue to list or review (default: the one you are standing in)",
    )
    inbox_p.add_argument(
        "--json", action="store_true",
        help="the listing, or the result of --promote/--drop, as one JSON document",
    )
    inbox_p.add_argument(
        "--restore", metavar="KEY",
        help="put a dropped or expired page back in the queue from shared/_archive/",
    )
    inbox_p.add_argument(
        "--stats", action="store_true",
        help="queue depth, median age, and what drained in the last 7 days",
    )
    procedures_p = sub.add_parser(
        "procedures",
        help="procedures children keep rediscovering, as a proposed CLAUDE.md line",
    )
    procedures_p.add_argument(
        "--all", action="store_true",
        help="every repo the transcripts cover, not just the one you are standing in",
    )
    procedures_p.add_argument(
        "--show", metavar="KEY",
        help="print the proposed line and the children that paid for it (e.g. cargo-test)",
    )
    procedures_p.add_argument(
        "--accept", metavar="KEY",
        help="append that section to the repo's CLAUDE.md (the only write; appends only)",
    )
    procedures_p.add_argument(
        "--drop", metavar="KEY",
        help="take one candidate out of the queue, writing nothing",
    )
    procedures_p.add_argument(
        "--projects", metavar="DIR",
        help="where Claude Code keeps its transcripts (default ~/.claude/projects)",
    )
    procedures_p.add_argument(
        "--refresh", action="store_true",
        help="rebuild the candidate cache the session-start offer reads (writes no repo file)",
    )
    procedures_p.add_argument(
        "--stats", action="store_true",
        help="undecided candidates, and what the session-start offer drained in 7 days",
    )
    procedures_p.add_argument("--json", action="store_true", help="machine-readable listing")
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
    friction_p = sub.add_parser(
        "friction",
        help="report the friction ledger: what contradicted which rule (--backfill to recover history, --apply to write it)",
    )
    friction_p.add_argument("--backfill", action="store_true",
                            help="dry run: re-brief every session on disk and link its corrections (one LLM call per session)")
    friction_p.add_argument("--apply", action="store_true",
                            help="write the saved --backfill plan to the ledger (no LLM calls)")
    friction_p.add_argument("--fresh", action="store_true",
                            help="with --backfill: re-brief instead of reusing the last dry run")
    friction_p.add_argument("--since", metavar="YYYY-MM-DD", default=None,
                            help="only sessions (or ledger rows) on or after this date")
    friction_p.add_argument("--project", default=None, help="only this project")
    friction_p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
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
