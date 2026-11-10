"""What mnemo assumes about the ``claude`` CLI, stated in one place (#235).

``mnemo dispatch``, ``mnemo sessions`` and ``mnemo deliver`` are built on
observable behaviour of Claude Code that Claude Code does not document as a
contract: the shape of what ``claude --bg`` prints, the files it leaves under
``~/.claude/jobs/``, the daemon roster, and what ``--resume`` does to a live
session. Every one of those has already changed under us at least once (#211:
the id moved into a five-line help block and grew SGR colour), and the unit
suite could not notice because it verifies the parser against fixtures that
mirror the *last observed* output.

So the assumptions are written down here as data — :data:`ASSUMPTIONS` — with
the ``claude --version`` each was last verified against, and three things read
them:

- :func:`short_id_from` and :func:`verify_registered` are the assumptions as
  code, and the messages they produce on failure name the assumption that
  broke and the installed version, so a maintainer reads *which* observable
  changed rather than a wrong id in a column.
- ``tests/live/test_claude_cli_live.py`` exercises them against the **installed**
  binary. It spawns a real session, so it is opt-in (``-m live_claude``); the
  default run deselects it.
- :mod:`mnemo.core.dispatch` imports the parser from here rather than owning it,
  so the parser and its statement cannot drift apart.

Nothing here pins a Claude Code version or vendors its behaviour. When the CLI
changes, the live test fails naming the assumption, the assumption is updated,
and ``verified`` is bumped — that is the whole procedure.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

#: The ``claude --version`` the live test last passed against, and when.
#: Bump both when it passes on a newer version; the live test warns when the
#: installed version differs so this does not silently go stale. Only the
#: assumptions whose live tests passed in full carry it (``_V``); the others
#: name their own version.
VERIFIED_AGAINST = "2.1.321"
VERIFIED_ON = "2026-11-09"


@dataclass(frozen=True)
class Assumption:
    """One observable behaviour of the ``claude`` CLI that mnemo relies on.

    ``key`` is what a failure message cites. ``used_by`` names the code that
    would break if the claim stopped holding, so the reader of a failure knows
    the blast radius without grepping. ``verified`` is the version and date the
    claim was last checked against a real binary; ``how`` says whether that
    check is the live test or a hand measurement.
    """

    key: str
    claim: str
    used_by: str
    verified: str
    how: str = "live test"


_V = f"{VERIFIED_AGAINST} on {VERIFIED_ON}"

#: The ``--effort`` levels Claude Code accepts, in its own order (#351).
#: A closed list, unlike model ids: the CLI prints it verbatim for an unknown
#: level and then *ignores* the level rather than failing, so a typo passed
#: through would run a child on the default with only a warning nobody reads.
#: Checked against the installed binary by the ``bg-effort-flag`` live test.
EFFORT_LEVELS: Tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

#: The model aliases, lowest rank first, as the advisor pairing orders them
#: (#818). Claude Code's advisor docs give the pairing as a table of versions
#: — an Opus 4.8 session takes a Sonnet 5.5 advisor, an Opus 5.5 session does
#: not — so only the *aliases* are ordered here: each resolves to its
#: family's built-in default version, and among those the table is a plain
#: order (Fable 5.2 takes only Fable, Opus 5.5 takes Fable and Opus 5+,
#: Sonnet 5.5 takes those and Sonnet 5.5). A full id is never ranked: the
#: table's exceptions live there, and a guess would refuse a pair that works.
ADVISOR_ALIAS_RANK: Tuple[str, ...] = ("haiku", "sonnet", "opus", "fable")


def _alias_rank(model: Optional[str]) -> Optional[int]:
    """*model*'s place in :data:`ADVISOR_ALIAS_RANK`, or ``None``.

    A context suffix does not change the model: ``opus[1m]`` is ranked as
    ``opus``. Anything that is not a bare alias is ``None``.
    """
    if not model:
        return None
    alias = model.split("[", 1)[0].strip().lower()
    try:
        return ADVISOR_ALIAS_RANK.index(alias)
    except ValueError:
        return None


def advisor_ranks_below(model: Optional[str], advisor: Optional[str]) -> bool:
    """True when *advisor* provably ranks below the session *model* (#818).

    Only for two aliases, the one case the pairing is a plain order
    (:data:`ADVISOR_ALIAS_RANK`). ``False`` for everything it cannot prove: a
    full id on either side, or no *model* — a child on the machine's default
    — whose rank is not known before it starts.
    """
    child, adviser = _alias_rank(model), _alias_rank(advisor)
    return child is not None and adviser is not None and adviser < child


#: How ``--bg`` starts its refusal of a bypass child whose disclaimer
#: acceptance it cannot see, in both of the sentences it has for that (#822).
BYPASS_REFUSAL = "--bg with bypassPermissions requires accepting the disclaimer"
#: The second half of the refusal when the cause is that user settings were
#: left out of the launch, which ``--full-profile`` puts back in.
BYPASS_NO_USER_SETTINGS = "which this launch does not read"

#: How ``--bg`` starts its refusal of a workspace nobody trusted, in all three
#: of its sentences for that (#826, ``bg-workspace-trust``).
TRUST_REFUSAL = "Workspace not trusted."

#: The contract. Ordered as dispatch meets them: spawn, read back, watch, avoid.
ASSUMPTIONS: Tuple[Assumption, ...] = (
    Assumption(
        key="bg-positional-prompt",
        claim=(
            "`claude --bg '<prompt>'` takes the prompt positionally and exits 0 "
            "once the session is registered. `-p/--print` conflicts with `--bg` "
            "and would start no attachable session; `timeout` is not wrapped "
            "around it (absent from the macOS PATH)."
        ),
        used_by="dispatch.spawn_child",
        verified=_V,
    ),
    Assumption(
        key="bg-workspace-trust",
        claim=(
            "`claude --bg` starts only in a workspace Claude Code trusts. "
            "Anywhere else it exits 1 and starts no session, printing "
            "\"Workspace not trusted. Run `claude` in <cwd> once and accept "
            "the trust prompt, then retry.\" Trust is a `projects.<dir>."
            "hasTrustDialogAccepted: true` in `~/.claude.json`, and the "
            "binary's check looks for it in this order (`trusted_by` here). "
            "Inside a git repository it looks first at the repository's "
            "canonical root, which for a worktree is its main checkout, "
            "wherever the worktree lies. Then it looks from the cwd up to "
            "the repository's own root, never above it. Outside a "
            "repository it looks at the cwd and every directory above it. "
            "Accepting the prompt anywhere in a repository saves the entry "
            "at that canonical root: \"trusting it trusts that whole "
            "repository\". So a fresh `git init` is refused even inside a "
            "trusted checkout's worktree, and a worktree of a trusted "
            "checkout starts both inside the checkout's folder and outside "
            "it. A plain directory starts only when it or a directory above "
            "it is trusted. On the measuring machine a plain temp directory "
            "started only through an old entry for the home directory. This "
            "version's own refusal for home says it \"is trusted one session "
            "at a time\", so a newer machine has no such entry (read in the "
            "binary, not measured). `claude -p` is not "
            "checked. `CLAUDE_CODE_SANDBOXED` turns the check off; mnemo "
            "never sets it."
        ),
        used_by=(
            "dispatch._spawn_into: a child's worktree is trusted through the "
            "checkout dispatch runs from, and on a refusal the tree is removed "
            "and the hint names that checkout; the live tests' trusted_tree"
        ),
        verified="2.1.321 on 2026-11-09",
        how=(
            "tools/measure_bg_trust.py (#826), one `--bg` launch per arm, and "
            "the installed binary's own check, read; the live test is refused "
            "in a fresh repository inside a trusted worktree and starts in a "
            "worktree outside its checkout's folder"
        ),
    ),
    Assumption(
        key="bg-prints-short-id",
        claim=(
            "stdout of `--bg` contains exactly one token shaped like a session "
            "short id — eight lowercase hex digits — once SGR escapes "
            "(ESC[…m) are stripped. Observed shape: line 1 `backgrounded · "
            "ESC[36m<id>ESC[39m`, then four dim hint lines (`claude agents`, "
            "`attach <id>`, `logs <id>`, `stop <id>`) whose last word is "
            "`session`. Colour depends on the environment (FORCE_COLOR); the "
            "parser keys on shape, never on position."
        ),
        used_by="dispatch.spawn_child via claude_cli.short_id_from",
        verified=_V,
    ),
    Assumption(
        key="bg-model-flag",
        claim=(
            "`claude --bg --model <id> '<prompt>'` is accepted: the flags "
            "compose, the child runs on <id>, and `state.json` records the "
            "flags it was spawned with under `respawnFlags` — a list where "
            "`--model` is followed by its value (measured: a `--model haiku` "
            "child whose transcript reads claude-haiku-5-5 on every assistant "
            "record on 2.1.321, claude-haiku-4-5-20251001 on 2.1.270). When "
            "nothing is passed, whether a value "
            "appears depends on the profile: a **full-profile** child "
            "resolves the machine default into it (`['--model', "
            "'opus[1m]']` on 2.1.270, and 23 of 23 children measured before "
            "the lean profile existed), while a **lean** child (#270, the default) "
            "passes its own `--settings` and so has no user-level default to "
            "resolve — it carries the profile flags and no `--model` at all. "
            "`Session.model` is therefore None for a lean child that named no "
            "model, which is honest rather than missing. The sibling key "
            "`model` is null on every child of either kind and is not read."
        ),
        used_by="dispatch.spawn_child (--model), sessions.jobs.Session.model",
        verified=_V,
    ),
    Assumption(
        key="bg-effort-flag",
        claim=(
            "`claude --bg --effort <level> '<prompt>'` is accepted, and "
            "`state.json` records it under `respawnFlags` the way it records "
            "`--model`: `--effort` followed by its value, on a full-profile "
            "child (`['--effort', 'high', '--model', 'opus[1m]']`) and on a "
            "lean one (`['--effort', 'low', '--setting-sources', ...]`) alike. "
            "Unlike the model, no default is ever resolved into the flags: "
            "children spawned without `--effort` carry none on either "
            "profile, so `Session.effort` is None for them — the default, not "
            "a failure to read. The levels are exactly `EFFORT_LEVELS`, the "
            "list `claude --effort <unknown> --version` prints after "
            "`Valid values:`; an unknown level is a warning, not an error, "
            "and the child silently runs on the default — which is why "
            "dispatch refuses one before spawning. Measured by the flags "
            "recorded, not by the child's reasoning: no observable says how "
            "hard a child actually thought."
        ),
        used_by=(
            "dispatch.spawn_child (--effort), contracts (effort:), "
            "cli dispatch --effort, sessions.jobs.Session.effort"
        ),
        verified=_V,
    ),
    Assumption(
        key="bg-advisor-flag",
        claim=(
            "`claude --bg --model <m> --advisor <a> '<prompt>'` is accepted on "
            "the lean profile, and `respawnFlags` records `--advisor` followed "
            "by its value (Claude Code moves it first: `['--advisor', 'opus', "
            "'--setting-sources', ..., '--model', 'sonnet']`). The advised child "
            "gets an `advisor` tool and a `# Advisor Tool` section in its "
            "context; the session decides when to call it. A call is an "
            "`assistant` record holding a `server_tool_use` block named "
            "`advisor` with an empty `input`, then an `advisor_tool_result` "
            "whose content is `advisor_redacted_result` — the advice is "
            "encrypted, not readable. Its cost is an entry of `message.usage."
            "iterations` with `type: \"advisor_message\"` and the advisor's "
            "own `model` (`claude-opus-5-5`), which the record's top-level "
            "`usage` does **not** include: that is the sum of the `message` "
            "iterations alone (measured 4 = 2+2 input, 45 = 26+19 output). "
            "Every assistant record's own `model` stays the child's. The "
            "advisor reads the child's whole context uncached: 51,631 input, "
            "0 cache read, 426 output on a lean sonnet child's first turn "
            "(59,150 on a full-profile one). An advisor ranked below the "
            "child's model is **dropped without a word**: a lean `--model "
            "sonnet --advisor claude-haiku-5-5` child exits 0, prints its id, "
            "keeps `--advisor` in `respawnFlags`, and runs with no advisor "
            "tool and no call — so `respawnFlags` says what was asked for, "
            "not what ran, and dispatch refuses what it can prove inverted "
            "(`advisor_ranks_below`). Not evidence of an advisor: a "
            "top-level `advisorModel` on assistant records, and an "
            "`advisor_20260301` tool marked `defer_loading` in "
            "`api-request-shape`, both seen on a full-profile child spawned "
            "with no advisor that had none and made no call. The "
            "`advisorModel` settings key reaches a `--bg` child too (hand "
            "measurement on the full profile); dispatch passes the flag "
            "because `respawnFlags` shows it and a wake keeps it."
        ),
        used_by=(
            "dispatch.spawn_child (--advisor), dispatch.check_advisor, "
            "contracts (advisor:), cli dispatch --advisor, twins arms, "
            "sessions.jobs.Session.advisor, tools/measure_advisor_pairs.py"
        ),
        verified="2.1.321 on 2026-11-09",
        how=(
            "hand measurement (#818): three plain `--bg` sonnet children "
            "(flag, settings, none), then two lean ones through "
            "`dispatch.spawn_child` (`sonnet` + `opus`, `sonnet` + "
            "`claude-haiku-5-5`), transcripts and `state.json` read back; "
            "the live test spawns the cheapest advised child and checks the "
            "flag, the call and the iteration"
        ),
    ),
    Assumption(
        key="jobs-state-json",
        claim=(
            "`<config dir>/jobs/<id>/state.json` — `~/.claude`, or "
            "`$CLAUDE_CONFIG_DIR` when the spawning process has it set (#640: "
            "both children of 2026-10-13 were under the account dir) — exists "
            "by the time `--bg` returns "
            "(measured: 0.00s after exit) and is a JSON object whose `cwd` is "
            "the spawn cwd verbatim; `state`, `tempo`, `sessionId` (the full "
            "uuid, prefixed by the short id) and `intent` (the prompt) are "
            "present from the first write. `linkScanPath` and `cliVersion` "
            "are absent at spawn and filled in later; `needs` and "
            "`suggestedReply` were not observed at spawn on 2.1.270 at all. "
            "`~/.claude/jobs/pins.json` is a file sibling, not a session."
        ),
        used_by="sessions.jobs.read_sessions, delivery.find_worktree, "
                "dispatch (worktree ↔ session mapping is `cwd`)",
        verified=_V,
    ),
    Assumption(
        key="state-tempo-vocabulary",
        claim=(
            "`state` ∈ {working, blocked, done, stopped}; `tempo` ∈ {active, "
            "blocked, idle}. The axes overlap on the word `blocked`; only "
            "`tempo` answers whether a human is needed. A finished child reads "
            "state=done, tempo=idle; `claude stop` on a done session leaves "
            "them unchanged."
        ),
        used_by="sessions.jobs.Session.is_blocked / is_done, render, statusline",
        verified=_V,
    ),
    Assumption(
        key="answer-keeps-blocked-state",
        claim=(
            "A prompt reaching a session rewrites its record as `{...previous, "
            "detail: <the prompt, whitespace collapsed>, tempo: \"active\", "
            "inFlight: <fresh>, needs: undefined}`, with no `state` key: "
            "`state` stays what it was. The mid-turn updater then returns "
            "early while `state` is `blocked`. So a session that asked "
            "(`state=blocked, tempo=blocked`) and was answered reads "
            "`state=blocked, tempo=active` for the whole turn the answer "
            "starts, until the end-of-turn classifier writes a new pair. Every "
            "literal `state: \"blocked\", tempo: ...` in the binary has "
            "`tempo: \"blocked\"` (11 of 11). They include `trailing-q`, the "
            "turn that ends in a plain-text question, and `blocked-marker`, "
            "`ask-verb`, `awaiting-user` and `auth-prose`. So a `blocked` "
            "state with any other tempo has been answered, and only `tempo` "
            "says a human is needed. The one such record on this machine "
            "(written by 2.1.303) has its `detail` equal to the maintainer's "
            "typed prompt, written 0.62s after it "
            "(`tools/measure_session_states.py`)."
        ),
        used_by="sessions.jobs.Session.is_blocked (why it reads `tempo` alone)",
        verified="2.1.321 on 2026-11-09",
        how=("hand reading of the installed binary's bundled JavaScript (#737), "
             "the same shapes found in 2.1.319, the binary it replaced; "
             "tests/live/test_claude_cli_live.py re-checks the three shapes "
             "without a spawn"),
    ),
    Assumption(
        key="daemon-roster-pid",
        claim=(
            "`~/.claude/daemon/roster.json` has `workers[<short id>].pid`, an "
            "int, present by the time `--bg` returns; the roster is a superset "
            "of the job dirs, so absence from a readable roster means the "
            "process is gone."
        ),
        used_by="sessions.liveness (is_abandoned / is_waiting)",
        verified=_V,
    ),
    Assumption(
        key="transcript-jsonl",
        claim=(
            "Once set, `linkScanPath` is the absolute path of a JSONL file; "
            "each line is a JSON object with a `type`, and `assistant` lines "
            "carry `message.content[]` blocks where `type == 'tool_use'` "
            "blocks have `name` and `input`."
        ),
        used_by="activity (mnemo sessions activity column, mnemo session)",
        verified=_V,
    ),
    Assumption(
        key="stop-rm-noninteractive",
        claim=(
            "`claude stop <id>` and `claude rm <id>` work on a pipe and exit 0; "
            "`rm` deletes `~/.claude/jobs/<id>/`."
        ),
        used_by="delivery.stop_session (deliver stops a done child once its "
                "PR is open, #311); the live test's own cleanup",
        verified=_V,
    ),
    Assumption(
        key="agents-needs-tty",
        claim=(
            "`claude agents` refuses on a pipe (exit 1: \"requires an "
            "interactive terminal\") and points at `claude agents --json`. "
            "dispatch never calls it; scripted readers use `mnemo sessions "
            "--json`, which reads state.json directly."
        ),
        used_by="dispatch report hints (routed around, not depended on)",
        verified=_V,
    ),
    Assumption(
        key="lean-child-profile",
        claim=(
            "`--setting-sources project,local` loads the repo's settings and "
            "not `~/.claude/settings.json`, so the user's `enabledPlugins`, "
            "statusline and unrelated hooks do not load; `--strict-mcp-config` "
            "ignores every MCP configuration except one passed with "
            "`--mcp-config`. Both are accepted alongside `--bg`. Handing "
            "mnemo's own hooks back with `--settings <file>` fires its "
            "SessionStart briefing, and its server with `--mcp-config <file>` "
            "restores the three `mcp__mnemo__*` tools. Measured on 2.1.270 over three "
            "`--bg` children per arm (one-word prompt, empty repo, first-turn "
            "total input read from each transcript): 55,180/58,285/58,287 on "
            "the full profile against 42,293/42,295/42,296 lean — ~16k tokens "
            "and ~27%. Without `--mcp-config`, a lean child lists no "
            "`mcp__*` tools at all. `--safe-mode` and `--bare` are rejected as blunter: the "
            "first disables mnemo too, the second additionally forces "
            "ANTHROPIC_API_KEY/apiKeyHelper auth, which an OAuth maintainer's "
            "child does not have. A child started in bypassPermissions is the "
            "exception: it needs `user` in the sources too "
            "(`bg-bypass-needs-user-settings`, #822)."
        ),
        used_by="dispatch.spawn_child via child_profile.lean_args",
        verified=_V,
    ),
    Assumption(
        key="bg-bypass-needs-user-settings",
        claim=(
            "`claude --bg --permission-mode bypassPermissions` counts the "
            "bypass disclaimer as accepted only through settings it reads, and "
            "a background child counts only user or managed settings "
            "(`skipDangerousModePermissionPrompt: true`). Under the lean flags "
            "(`--setting-sources project,local`) on a machine whose user "
            "settings carry it, `--bg` exits 1 printing \"--bg with "
            "bypassPermissions requires accepting the disclaimer first. "
            "Acceptance is kept in your user settings, which this launch does "
            "not read. Launch again with user settings on.\" It does the same "
            "with the key in the child's own `--settings` file. With the key "
            "only in the tree's `.claude/settings.local.json`, it starts, but "
            "the child runs `default`. With `--setting-sources "
            "user,project,local` it starts and runs `bypassPermissions`, and an "
            "`enabledPlugins: {<id>: false}` in `--settings` takes the user's "
            "plugin back out of the skill listing. A wake (`--bg --resume`) of "
            "a session saved with bypassPermissions and setting sources "
            "without `user` is accepted, but the woken turns run `default`. "
            "Flags passed on that wake start a copy instead (\"keeps its own "
            "saved options, so the flags you passed started a copy\"). The "
            "lean shape worked from 2.1.306 to 2.1.312: the job records on the "
            "maintainer's machine hold 43 such children, the last from "
            "2026-10-29. Which release changed it was not measured."
        ),
        used_by=(
            "child_profile.lean_args (BYPASS_LEAN_FLAGS), child_mode.choose "
            "(where the acceptance is looked for), dispatch._bypass_way_out, "
            "sessions.wake (refuses the old shape)"
        ),
        verified="2.1.321 on 2026-11-09",
        how=(
            "tools/measure_bypass_lean.py (#822) against the installed binary; "
            "the refusal and the user-settings arm are re-checked by "
            "tests/live/test_claude_cli_live.py"
        ),
    ),
    Assumption(
        key="daemon-spare-pool",
        claim=(
            "The daemon keeps ONE idle pre-warmed process: a `bg-pty-host … "
            "--bg-spare <x>.claim.sock` whose child is `claude bg-spare`. A "
            "`--bg` claims it and the daemon spawns its replacement at once "
            "(every one of 86 `claimed-spare` lines in `daemon.log` is followed "
            "within 5 ms by exactly one `spare spawned`), so dispatching N "
            "children leaves one idle spare, not N. A claimed spare keeps its "
            "argv for life — `ps | grep bg-spare` counts running children too; "
            "the roster's `workers[<id>].pid` is the host pid and is what tells "
            "them apart, with `supervisorPid` as the spare's expected parent. "
            "The daemon itself reaps spares a previous daemon left "
            "(`orphan-spare reap`) and retires finished children it keeps "
            "resident (`retire <id>: settled, idle 60m` in 34 of 45 log lines, "
            "`idle 8h` in 5 just after a daemon start, 11-44m with "
            "`[low memory]` in 6; #350); `claude stop` on a `done` child ends its process "
            "(8 `settled (killed)` ids still read state=done, none in the "
            "roster) while leaving state=done. No subcommand, flag or setting "
            "drains or caps the pool (`claude daemon --help`, `claude --help`), "
            "and killing the spare only makes the daemon spawn another, so "
            "mnemo reports it and retires nothing."
        ),
        used_by="doctor background_processes via sessions.residents.census",
        verified="2.1.270 on 2026-09-14",
        how=(
            "hand measurement (#280): daemon.log vs roster.json vs `ps`; the "
            "live test checks one idle spare after a spawn and that `stop` "
            "ends the worker's process. On Windows, where the census does not "
            "run, it checks only the second; that half passed on 2.1.321 "
            "(2026-11-09)"
        ),
    ),
    Assumption(
        key="stop-fires-session-end",
        claim=(
            "`claude stop <id>` on a `done` child runs its `SessionEnd` hook "
            "(payload `hook_event_name: SessionEnd`, `reason: \"other\"`, the "
            "child's `session_id` and spawn `cwd`) before the process ends; "
            "`daemon.log` then records `bg settled <id> (killed)` and "
            "`state.json` still reads `done`. Measured on a `--bg` haiku child "
            "whose only hooks, passed with `--settings`, wrote marker files: "
            "SessionEnd fired at 18:51:14Z, `settled (killed)` was logged at "
            "18:51:15.6Z, both pids gone. \"(killed)\" is how the daemon "
            "names the settle, not a sign the hook was skipped. A child that "
            "is left `done` never fires it (#247). mnemo's own SessionEnd "
            "still writes nothing while its circuit breaker is open "
            "(`session_end.main` returns first)."
        ),
        used_by="deliver._stop_finished via delivery.stop_session (#311)",
        verified="2.1.272 on 2026-09-15",
        how="hand measurement (#311); not exercised by the live test",
    ),
    Assumption(
        key="parent-session-env",
        claim=(
            "Claude Code exports `CLAUDE_CODE_SESSION_ID` into the environment "
            "of commands its Bash tool runs, holding the running session's "
            "full id — the same uuid as that session's `sessionId` in "
            "`state.json`, whose first eight characters name its job "
            "directory (measured from inside a `--bg` child: all three "
            "agreed). A plain terminal does not have it. Nothing Claude Code "
            "writes carries the dispatching session: the roster's `dispatch` "
            "record reads `source: \"shell\"` with an empty `env`, and "
            "`state.json` has no parent key."
        ),
        used_by="dispatch._spawn_into via sessions.parents.record; "
                "`mnemo sessions --json` parent_session",
        verified="2.1.272 on 2026-09-15",
        how="hand measurement (#288); not exercised by the live test",
    ),
    Assumption(
        key="mcp-server-session",
        claim=(
            "A stdio MCP server Claude Code spawns has `CLAUDE_CODE_SESSION_ID` "
            "and `CLAUDE_CODE_MESSAGING_SOCKET` (`/tmp/cc-socks/<pid>.sock`, "
            "the pid of the `claude` process) in its environment, but the "
            "session id is the one it was spawned for: the server outlives a "
            "`/clear`, and its environment keeps the old id. "
            "`~/.claude/sessions/<pid>.json` follows the session — "
            "`$CLAUDE_CONFIG_DIR/sessions/<pid>.json` when that is set "
            "(#671) — its "
            "`sessionId` is the live one and its `messagingSocketPath` is the "
            "exported socket. The `_meta` the binary builds for a stdio "
            "server's `tools/call` adds `claudecode/toolUseId`, not a session "
            "id, so the request cannot say either. Measured on the eight "
            "mnemo servers running on 2026-09-22: all eight exported a socket "
            "whose sessions file named it back; in two (interactive, up since "
            "00:53) the environment id was no longer that file's `sessionId`; "
            "and in two the server's parent was a wrapper, not `claude`, so "
            "`getppid()` would have found no sessions file."
        ),
        used_by="mcp.server.session_id (the access log's `session_id`, #416)",
        verified="2.1.280 on 2026-09-22",
        how=("hand measurement (#416): `ps eww` on the live servers against "
             "their parent's sessions file, and the `_meta` keys in the "
             "installed binary; not exercised by the live test"),
    ),
    Assumption(
        key="context-from-transcript-usage",
        claim=(
            "The context size `/context` prints is the last non-synthetic "
            "`assistant` event's `message.usage` in the session transcript "
            "(`linkScanPath`), summed as `input_tokens + "
            "cache_creation_input_tokens + cache_read_input_tokens` "
            "(measured: 92.6k / 160.2k / 98k printed against 92,566 / "
            "160,181 / 98,024 read). An API error or refusal is an assistant "
            "event with `model: \"<synthetic>\"` and zero usage, and "
            "`/context` skips it (32.1k printed after one). `state.json`'s "
            "`tokens` is not this number: 3-65x below it on all 18 jobs on "
            "disk, and matching no transcript quantity."
        ),
        used_by="activity.context.measure; `mnemo sessions` token column "
                "and `--json` context_tokens",
        verified="2.1.272 on 2026-09-15",
        how=("hand measurement (#307): `claude -p /context --resume <id> "
             "--fork-session --no-session-persistence` on finished sessions; "
             "not exercised by the live test"),
    ),
    Assumption(
        key="tool-result-cost-from-growth",
        claim=(
            "A tool's result is a `tool_result` block in a `user` event's "
            "`message.content`, naming its call by `tool_use_id`; its content "
            "is a string or a list of `text` / `image` (base64 `source.data`) "
            "blocks. The context a turn adds is the next real turn's input "
            "minus this turn's input and `output_tokens`, and one response is "
            "written as several `assistant` events sharing `message.id` and "
            "`usage`, with parallel results between them (2,767 of 29,770 "
            "messages). Tool-result text costs 2.22-2.30 chars/token on the "
            "5-family models (2,612 turns, p10-p90 per result 1.87-2.67); an "
            "image costs its pixels / 750 (1,891 / 705 / 565 measured). "
            "`/context`'s own per-tool figures are `len(JSON.stringify(block))"
            " / 4` over calls and results, as a share of the window: 207% "
            "for Read on a session whose context was 818k, 99.9% of it base64."
        ),
        used_by="activity.context.measure; `mnemo sessions` row suffix "
                "and `--json` context_breakdown",
        verified="2.1.272 on 2026-09-15",
        how=("hand measurement (#308) over the 404 transcripts in "
             "~/.claude/projects and the 2.1.272 binary's /context code; "
             "not exercised by the live test"),
    ),
    Assumption(
        key="resume-bifurcates",
        claim=(
            "`claude --resume <id>` on a *running* background session starts "
            "a copy rather than continuing it (the `--bg` help says as much: "
            "\"starts a copy and says so when the session is already "
            "running\"). `claude attach <id>` and `SendMessage` reach the "
            "live one. dispatch never resumes a child; `mnemo resume` resumes "
            "only one the roster proves dead, which is the other half of the "
            "same rule — see `resume-under-own-id`."
        ),
        used_by="dispatch (routed around, not depended on); "
                "sessions.wake (guarded against, not depended on)",
        verified="2.1.269 on 2026-09-12",
        how="hand measurement (#197); not exercised by the live test",
    ),
    Assumption(
        key="resume-under-own-id",
        claim=(
            "`claude --bg --resume <FULL session id> '<prompt>'` on a session "
            "whose process is gone wakes it **in place**: same short id, same "
            "`~/.claude/jobs/<id>/`, same transcript (the prompt is appended "
            "to it), same `cwd`, conversation intact. stdout is the ordinary "
            "`--bg` block preceded by `note: woke session <short id> with its "
            "saved options (...)`, and those options are *every* flag the "
            "session was spawned with, not just the model: a probe spawned "
            "with `--disallowedTools Edit Write NotebookEdit "
            "--setting-sources project,local --strict-mcp-config "
            "--mcp-config <f> --settings <f> --model haiku` came back "
            "carrying all seven in `respawnFlags`, so a read-only child does "
            "not regain `Edit` and a lean child does not lose mnemo's hooks. "
            "`SessionStart` fires again on the wake (a marker hook wrote two "
            "files for one spawn plus one wake). The woken turn lands in the "
            "transcript as `origin.kind: \"human\"`, `promptSource: "
            "\"typed\"` — the opening prompt's own shape, which is why "
            "`sessions.wake` sends a constant and takes no message. Passing "
            "the **short** id instead does not fail: it prints `note: started "
            "a copy of that conversation as <new id>` and runs a SECOND "
            "session in the same worktree."
        ),
        used_by="sessions.wake.wake, cli resume",
        verified="2.1.278 on 2026-09-19",
        how=("hand measurement (#393): two throwaway `--bg` haiku children, "
             "stopped and then woken, `respawnFlags` and transcript read back "
             "on each side; not exercised by the live test, which would have "
             "to spawn and wake"),
    ),
    Assumption(
        key="api-error-block",
        claim=(
            "An API error that stops a turn is written to the transcript as "
            "an `assistant` record with `isApiErrorMessage: true`, `message."
            "model: \"<synthetic>\"`, zero usage, `error` (Claude Code's own "
            "code: rate_limit, server_error, invalid_request, "
            "authentication_failed, unknown) and, for a 429, `apiErrorStatus: "
            "429` plus `quotaLimits` with `status`, `rateLimitType` "
            "(`five_hour` / `seven_day`) and `resetsAt`, a unix epoch. The "
            "session's `state.json` then reads `state: blocked, tempo: "
            "blocked` with `needs` set to Claude Code's rendered sentence for "
            "that code — `rate limited — wait and retry` and the rest of "
            "`sessions.stalls.NEEDS_BY_ERROR`, transcribed from the binary's "
            "own switch. Measured over the 79 API-error records in the 404 "
            "transcripts on this machine (2026-09-19): 23 "
            "authentication_failed, 18 rate_limit, 17 invalid_request, 13 "
            "server_error, 8 unknown; 14 of the 18 rate limits carry "
            "`resetsAt` and the 4 that do not are a per-model credit cap "
            "(\"You've reached your Fable limit\"), not a window."
        ),
        used_by="sessions.stalls (the STALLED bucket and what `mnemo resume` "
                "will wake)",
        verified="2.1.278 on 2026-09-19",
        how=("hand measurement (#393) over ~/.claude/projects plus the "
             "installed binary's own switch; the sentences are re-checked "
             "against the binary by tests/live/test_claude_cli_live.py, "
             "which needs no spawn for that one, and were still there on "
             "2.1.321 (2026-11-09)"),
    ),
)


def assumption(key: str) -> Assumption:
    """The assumption named *key*. A typo here is a bug, so it raises."""
    for item in ASSUMPTIONS:
        if item.key == key:
            return item
    raise KeyError(key)


class ContractBroken(RuntimeError):
    """An observable behaviour of the ``claude`` CLI did not hold.

    Carries the :class:`Assumption` and what was observed; ``str()`` renders
    both with the installed ``claude --version`` so the report names what
    changed, not just that something did.
    """

    def __init__(self, key: str, observed: str, *, version: Optional[str] = None):
        self.assumption = assumption(key)
        self.observed = observed
        self.version = claude_version() if version is None else version
        super().__init__(self._render())

    def _render(self) -> str:
        a = self.assumption
        installed = self.version or "unknown (`claude --version` failed)"
        return (
            f"claude CLI assumption `{a.key}` did not hold "
            f"(installed claude {installed}; last verified against {a.verified}): "
            f"{self.observed}. Expected: {a.claim} "
            f"Depended on by: {a.used_by}. "
            f"See mnemo.core.claude_cli and run `pytest -m live_claude` after "
            f"updating the assumption."
        )


# --- the version -----------------------------------------------------------

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


def parse_version(stdout: str) -> Optional[str]:
    """``"2.1.270 (Claude Code)"`` → ``"2.1.270"``; ``None`` when no triple."""
    match = _VERSION_RE.search(stdout or "")
    return match.group(1) if match else None


def claude_version() -> Optional[str]:
    """The installed ``claude --version`` as ``x.y.z``, or ``None``. Never raises.

    Only called on failure paths and by the live test, so the extra process is
    never on the dispatch hot path.
    """
    try:
        result = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_version(result.stdout)


# --- `--bg` output: the id ---------------------------------------------------

# A Claude Code session short id: exactly eight lowercase hex digits, as every
# id under `~/.claude/jobs/` is. The trailing `$` is load-bearing — without it
# a 40-char commit sha echoed above the block matches on its first eight
# characters and is returned as an id addressing no session.
SHORT_ID_RE = re.compile(r"^[0-9a-f]{8}$")

# SGR escapes, which `claude --bg` emits around the id under `FORCE_COLOR`
# — verified against live spawns with and without it, not assumed. Stripped
# before matching, because the colored id arrives as the single token
# ESC[36m<id>ESC[39m (no spaces) and so matches no id shape at all.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def short_id_from(stdout: str) -> str:
    """The first token in *stdout* shaped like a session short id, else ``""``.

    Scans tokens rather than lines: a line index is only correct while nothing
    is ever printed above the block, and a single warning on stdout would make
    "the last token of the first line" return the last word of the warning
    (#211, with a new cause). See ``bg-prints-short-id``.
    """
    for token in ANSI_RE.sub("", stdout or "").split():
        if SHORT_ID_RE.match(token):
            return token
    return ""


def require_short_id(stdout: str) -> str:
    """:func:`short_id_from`, raising :class:`ContractBroken` on a miss.

    The observed output is quoted with escapes made visible, because the
    difference between a bare id and a coloured one is invisible on a
    terminal and is precisely what #211 was made of.
    """
    found = short_id_from(stdout)
    if found:
        return found
    shown = (stdout or "").strip()
    if len(shown) > 200:
        shown = shown[:200] + "…"
    observed = (
        f"`claude --bg` exited 0 but printed no session id; stdout was {shown!r}"
        if shown else "`claude --bg` exited 0 and printed nothing on stdout"
    )
    raise ContractBroken("bg-prints-short-id", observed)


# --- the advisor in a transcript (`bg-advisor-flag`) --------------------------

#: The ``usage.iterations`` type an advisor's turn is recorded under.
ADVISOR_ITERATION = "advisor_message"


def split_usage(usage: object) -> Tuple[Tuple[dict, ...], Tuple[dict, ...]]:
    """One response's ``message.usage`` as ``(own, advisor)`` token entries.

    *own* are the child's own model turns, *advisor* the ``advisor_message``
    iterations, each carrying its ``model``. The top-level counts are the sum
    of the ``message`` iterations alone, so a usage without ``iterations`` —
    every response that called no server tool — is its own single entry, and
    one with them is read iteration by iteration: adding the top level to the
    advisor's would count nothing twice, but reading the top level alone
    would lose the advisor's tokens entirely.
    """
    if not isinstance(usage, dict):
        return (), ()
    iterations = usage.get("iterations")
    if not isinstance(iterations, list) or not iterations:
        return (usage,), ()
    own = tuple(it for it in iterations
                if isinstance(it, dict) and it.get("type") != ADVISOR_ITERATION)
    advisor = tuple(it for it in iterations
                    if isinstance(it, dict) and it.get("type") == ADVISOR_ITERATION)
    return own, advisor


# --- workspace trust (`bg-workspace-trust`) -----------------------------------


def _norm(path: "Path | str") -> str:
    """*path* as the trust check compares it: real, and case-folded where the
    filesystem is (Windows keys are written with forward slashes)."""
    return os.path.normcase(os.path.realpath(str(path)))


def _same(a: "Path | str", b: "Path | str") -> bool:
    return _norm(a) == _norm(b)


def git_root(cwd: "Path | str") -> Optional[Path]:
    """The nearest directory at or above *cwd* holding a ``.git``, or ``None``.

    A file counts as well as a directory: a worktree's ``.git`` is a file.
    """
    here = Path(os.path.abspath(cwd))
    for directory in (here, *here.parents):
        if (directory / ".git").exists():
            return directory
    return None


def trust_root(cwd: "Path | str") -> Optional[Path]:
    """Where Claude Code keeps the trust of *cwd*'s repository, or ``None``.

    The repository's root, except for a worktree: there it is the main
    checkout, read from the ``.git`` file's ``gitdir``, its ``commondir`` and
    the ``gitdir`` that points back. A ``.git`` file whose links do not close
    that loop (a submodule's, a hand-made one) is its own root, as the binary
    treats it. ``None`` outside any repository.
    """
    root = git_root(cwd)
    if root is None:
        return None
    marker = root / ".git"
    if marker.is_dir():
        return root
    try:
        text = marker.read_text(encoding="utf-8").strip()
        if not text.startswith("gitdir:"):
            return root
        admin = Path(os.path.normpath(root / text[len("gitdir:"):].strip()))
        common = Path(os.path.normpath(
            admin / (admin / "commondir").read_text(encoding="utf-8").strip()))
        back = (admin / "gitdir").read_text(encoding="utf-8").strip()
    except OSError:
        return root
    if not _same(admin.parent, common / "worktrees") or not _same(admin / back, marker):
        return root
    return common.parent if common.name == ".git" else common


def trusted_by(cwd: "Path | str", projects: object) -> Optional[str]:
    """The ``~/.claude.json`` ``projects`` key that makes *cwd* trusted, or ``None``.

    The rule of ``bg-workspace-trust``, as the 2.1.321 binary checks it before
    a ``--bg`` launch: in a repository, :func:`trust_root` first, then from
    *cwd* up to the repository's own root and never above; outside one, *cwd*
    and every directory above it. *cwd* is taken through ``realpath`` first,
    as the binary does. Keys are compared as paths, so a Windows key's
    forward slashes and drive-letter case do not matter. Reads nothing:
    *projects* is the caller's.
    """
    if not isinstance(projects, dict):
        return None
    trusted = {_norm(key): key for key, entry in projects.items()
               if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True}

    def entry_for(directory: Path) -> Optional[str]:
        return trusted.get(_norm(directory))

    here = Path(os.path.realpath(cwd))
    root = git_root(here)
    if root is not None:
        found = entry_for(trust_root(here) or root)
        if found:
            return found
    for directory in (here, *here.parents):
        found = entry_for(directory)
        if found or (root is not None and _same(directory, root)):
            return found
    return None


# --- the jobs directory ------------------------------------------------------


def verify_registered(short_id: str, *, cwd: "Path | str") -> Optional[str]:
    """Check ``jobs-state-json`` for a child just spawned; a message or ``None``.

    Read immediately after ``--bg`` returns, with no polling, because the
    file was measured to exist synchronously (0.00s). A message rather than an
    exception: by this point the child is running, and the only honest
    response is to say what could not be confirmed and hand the maintainer to
    ``mnemo sessions``, which reads the same file and will be equally blind.

    ``cwd`` is compared through ``realpath`` on both sides — the same rule
    ``sessions.jobs.normalize_cwd`` applies — because a worktree can reach one
    directory through two spellings and a false mismatch here would accuse a
    healthy spawn.
    """
    from mnemo.core.sessions import accounts
    from mnemo.core.sessions.jobs import normalize_cwd

    # The child is registered under the Claude account that spawned it, which
    # is `~/.claude` only when `CLAUDE_CONFIG_DIR` is unset (#640). Looking in
    # the default alone printed this warning for two healthy children on
    # 2026-10-13. The spawning account's path comes first, so it is the one
    # named when the file is nowhere.
    candidates = accounts.state_paths(short_id)
    state_path = next((p for p in candidates if p.is_file()), candidates[0])
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError:
        elsewhere = "".join(f", nor {p}" for p in candidates if p != state_path)
        return str(ContractBroken(
            "jobs-state-json",
            f"no readable {state_path}{elsewhere} after `claude --bg` returned id {short_id}",
        ))
    except ValueError:
        return str(ContractBroken(
            "jobs-state-json", f"{state_path} is not valid JSON",
        ))
    if not isinstance(data, dict):
        return str(ContractBroken(
            "jobs-state-json", f"{state_path} is a JSON {type(data).__name__}, not an object",
        ))

    recorded = data.get("cwd")
    if not isinstance(recorded, str) or not recorded:
        return str(ContractBroken(
            "jobs-state-json", f"{state_path} has no `cwd`; the worktree ↔ session "
            "mapping dispatch relies on is gone",
        ))
    if normalize_cwd(recorded) != normalize_cwd(str(cwd)):
        return str(ContractBroken(
            "jobs-state-json",
            f"{state_path} records cwd {recorded!r}, not the worktree {os.fspath(cwd)!r}",
        ))
    return None
