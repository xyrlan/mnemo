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
#: installed version differs so this does not silently go stale.
VERIFIED_AGAINST = "2.1.270"
VERIFIED_ON = "2026-09-14"


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
            "child whose transcript reads claude-haiku-4-5-20251001 on every "
            "assistant record). When nothing is passed, whether a value "
            "appears depends on the profile: a **full-profile** child "
            "resolves the machine default into it (`['--model', "
            "'opus[1m]']`, and 23 of 23 children measured before the lean "
            "profile existed), while a **lean** child (#270, the default) "
            "passes its own `--settings` and so has no user-level default to "
            "resolve — it carries the profile flags and no `--model` at all. "
            "`Session.model` is therefore None for a lean child that named no "
            "model, which is honest rather than missing. The sibling key "
            "`model` is null on every child of either kind and is not read."
        ),
        used_by="dispatch.spawn_child (--model), sessions.jobs.Session.model",
        verified=f"{VERIFIED_AGAINST} on 2026-09-14",
    ),
    Assumption(
        key="jobs-state-json",
        claim=(
            "`~/.claude/jobs/<id>/state.json` exists by the time `--bg` returns "
            "(measured: 0.00s after exit) and is a JSON object whose `cwd` is "
            "the spawn cwd verbatim; `state`, `tempo`, `sessionId` (the full "
            "uuid, prefixed by the short id) and `intent` (the prompt) are "
            "present from the first write. `linkScanPath` and `cliVersion` "
            "are absent at spawn and filled in later; `needs` and "
            "`suggestedReply` were not observed on this version at all. "
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
        used_by="the live test's own cleanup (dispatch never stops a child)",
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
            "restores the three `mcp__mnemo__*` tools. Measured over three "
            "`--bg` children per arm (one-word prompt, empty repo, first-turn "
            "total input read from each transcript): 55,180/58,285/58,287 on "
            "the full profile against 42,293/42,295/42,296 lean — ~16k tokens "
            "and ~27%. Without `--mcp-config`, a lean child lists no "
            "`mcp__*` tools at all. `--safe-mode` and `--bare` are rejected as blunter: the "
            "first disables mnemo too, the second additionally forces "
            "ANTHROPIC_API_KEY/apiKeyHelper auth, which an OAuth maintainer's "
            "child does not have."
        ),
        used_by="dispatch.spawn_child via child_profile.lean_args",
        verified=_V,
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
            "resident (`retire <id>: settled, idle 8h`, sooner with "
            "`[low memory]`); `claude stop` on a `done` child ends its process "
            "(8 `settled (killed)` ids still read state=done, none in the "
            "roster) while leaving state=done. No subcommand, flag or setting "
            "drains or caps the pool (`claude daemon --help`, `claude --help`), "
            "and killing the spare only makes the daemon spawn another, so "
            "mnemo reports it and retires nothing."
        ),
        used_by="doctor background_processes via sessions.residents.census",
        verified=f"{VERIFIED_AGAINST} on 2026-09-14",
        how=(
            "hand measurement (#280): daemon.log vs roster.json vs `ps`; the "
            "live test checks one idle spare after a spawn and that `stop` "
            "ends the worker's process"
        ),
    ),
    Assumption(
        key="resume-bifurcates",
        claim=(
            "`claude --resume <id>` on a *running* background session starts "
            "a copy rather than continuing it (the `--bg` help says as much: "
            "\"starts a copy and says so when the session is already "
            "running\"). `claude attach <id>` and `SendMessage` reach the "
            "live one. dispatch never resumes a child."
        ),
        used_by="dispatch (routed around, not depended on)",
        verified="2.1.269 on 2026-09-12",
        how="hand measurement (#197); not exercised by the live test",
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
            ["claude", "--version"], capture_output=True, text=True, timeout=15,
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
    from mnemo.core.sessions.jobs import jobs_dir, normalize_cwd

    entry = jobs_dir() / short_id
    state_path = entry / "state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except OSError:
        return str(ContractBroken(
            "jobs-state-json",
            f"no readable {state_path} after `claude --bg` returned id {short_id}",
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
