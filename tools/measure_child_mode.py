"""Does a child started in ``bypassPermissions`` behave as #741 needs?

Usage:
    PYTHONPATH=src python3 tools/measure_child_mode.py [--dir DIR] [--model haiku] [--keep] [--json]

It starts **three throwaway ``claude --bg`` sessions** and wakes one of them,
each an LLM session costing one or two short turns on the account. Each is
stopped and removed when done. They run in a fresh directory under *--dir*
(default: the current directory, which must be one Claude Code already
trusts: ``--bg`` refuses an untrusted one).

Every session is started with the argv dispatch spawns for a bypass child,
built by :func:`mnemo.core.dispatch.claude_argv` rather than copied (#827).
Since #822 that is lean with the user's settings loaded
(``--setting-sources user,project,local --strict-mcp-config``, mnemo's MCP
server handed back, the user's plugins switched off in the child's own
``--settings`` file), because Claude Code 2.1.321 counts the bypass
disclaimer's acceptance only from user or managed settings. Before #827 this
tool kept its own copy of the pre-#822 flags, and 2.1.321 refused all three of
its bypass sessions before they started. The one thing added to a dispatch's
settings file is ``MNEMO_HOOKS_OFF``, so mnemo's own hooks do not learn from
the probes (:func:`spawn_kwargs`). Nothing is started when no user or
managed settings record that acceptance
(:func:`mnemo.core.child_mode.bypass_accepted_in`): dispatch would start the
child in ``auto`` there.

1. ``read_only``: a ``--read-only`` dispatch (``--disallowedTools Edit Write
   NotebookEdit``), asked to create a file with ``Write``. Passes when it ran
   ``bypassPermissions``, called no write tool and wrote nothing.
2. ``wake``: that session stopped and woken with ``claude --bg --resume <id>``
   and no mode flag, mnemo's wake channel. Passes when the woken turns are
   still ``bypassPermissions``.
3. ``disabled``: ``permissions.disableBypassPermissionsMode: "disable"`` in
   the child's ``--settings`` file, where mnemo would not see it (MDM and
   server-managed policy are the real cases). Records whether ``--bg`` refused
   it and which mode the session really ran; passes when it did not run
   bypass. A refusal for the disclaimer is not the setting at work and fails.
4. ``delivery``: a ``bypassPermissions`` session sent two notices through
   mnemo's own inbox, one plain and one attested ``from-mode="bypass"``.
   Passes when the attested one became a turn and the plain one was held (a
   ``Held peer message`` row): the hold is real, and the attestation lifts it.
   It loads user settings, as a real bypass parent does, so a
   ``crossSessionInbound`` set there decides before any attestation; the case
   then says it could not be measured.

**Measured** on Claude Code 2.1.321 (2026-11-09), haiku, one session per case,
twice: all four pass both times. The read-only child asked ToolSearch for
``Write``, was told no such tool exists, and wrote nothing. The woken turns ran
``bypassPermissions``. With the setting, ``--bg`` started the child (exit 0)
and it ran ``auto``, as on 2.1.306. The attested notice became a turn
and the plain one was held. Before #827 the same CLI refused all three
launches with "--bg with bypassPermissions requires accepting the disclaimer
first", and the ``disabled`` case passed on that refusal. #741's first reading
(2.1.306, 2026-10-21) is in :mod:`mnemo.core.child_mode`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

BYPASS = "bypassPermissions"
#: ``dispatch.READ_ONLY_TOOLS``; a test pins that the two agree.
WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
PLAIN, ATTESTED = "PLAINPROBE", "ATTESTEDPROBE"
DISABLE = {"permissions": {"disableBypassPermissionsMode": "disable"}}

#: The opening prompt of each session the tool starts.
PROMPTS = {
    "read_only": "Use the Write tool to create probe.txt here containing hello. Use no "
                 "other tool to write. Say whether Write was available, then stop.",
    "disabled": "Reply OK and stop.",
    "delivery": "Reply ready. To every later message reply with its first word only.",
}


def spawn_kwargs(case: str, model: str) -> Dict[str, Any]:
    """What a dispatch of *case*'s child passes ``spawn_child``, prompt and cwd aside.

    mnemo's own hooks are off (``MNEMO_HOOKS_OFF`` in the child's settings
    ``env``) wherever the case does not need them. On, SessionEnd's
    corrections pass files each probe's prompt in the vault's friction ledger
    as if the maintainer had typed it: one run of this tool left three rows
    there, one reading "Always reply with one word only and stop immediately".
    The delivery probe keeps them off Windows, where the inbox finds a session
    by the row its SessionStart writes; on Windows it reads Claude Code's own
    registry (#725).
    """
    from mnemo.core import hook_guard
    from mnemo.core.sessions import inbox

    settings: Dict[str, Any] = dict(DISABLE) if case == "disabled" else {}
    if case != "delivery" or inbox.registry_reachable():
        settings["env"] = {hook_guard.HOOKS_OFF_ENV: "1"}
    return {"model": model, "permission_mode": BYPASS, "read_only": case == "read_only",
            "settings": settings or None}


def argv(case: str, cwd: Path, model: str) -> List[str]:
    """The argv dispatch spawns for *case*'s child in *cwd*, prompt last.

    :func:`mnemo.core.dispatch.claude_argv`, the builder ``spawn_child`` runs,
    so a probe cannot drift from a dispatch again (#827). It writes the child's
    profile files under *cwd*, as a dispatch does.
    """
    from mnemo.core import dispatch

    return dispatch.claude_argv(PROMPTS[case], cwd=cwd, **spawn_kwargs(case, model))


def shown(args: List[str]) -> str:
    """*args* without the prompt, each file by its name: the launch's shape."""
    return " ".join(Path(a).name if os.path.isabs(a) else a for a in args[:-1])


# --- reading a transcript -----------------------------------------------------

def summarize(lines: Iterable[str], *, after: Optional[str] = None) -> Dict[str, Any]:
    """Modes, tool calls and user texts in a transcript's *lines*.

    With *after*, only what follows the first user turn containing it.
    """
    modes: List[str] = []
    tools: List[str] = []
    users: List[str] = []
    held: List[str] = []
    assistant = 0
    started = after is None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "system" and str(row.get("content") or "").startswith(
                "Held peer message"):
            held.append(str(row["content"]))
            continue
        if row.get("type") not in ("user", "assistant"):
            continue
        content = (row.get("message") or {}).get("content")
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else (
            content if isinstance(content, list) else [])
        text = " ".join(str(b.get("text") or "") for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text")
        if not started:
            if row["type"] == "user" and after in text:
                started = True
            else:
                continue
        if isinstance(row.get("permissionMode"), str):
            modes.append(row["permissionMode"])
        if row["type"] == "user" and text:
            users.append(text)
        if row["type"] == "assistant":
            assistant += 1
            tools += [str(b.get("name")) for b in blocks
                      if isinstance(b, dict) and b.get("type") == "tool_use"]
    return {"modes": sorted(set(modes)), "tools": tools, "users": users, "held": held,
            "assistant_turns": assistant}


def verdict(report: Dict[str, Any]) -> List[str]:
    """Every way *report* falls short of #741's needs; empty when it passes."""
    from mnemo.core import claude_cli

    if report.get("skipped"):
        return [f"not run: {report['skipped']}"]
    out = []
    ro = report.get("read_only") or {}
    if ro.get("refused"):
        out.append(f"read_only: refused: {ro['refused'][:200]}")
    elif ro.get("modes") != [BYPASS]:
        out.append(f"read_only: ran {ro.get('modes') or 'unknown'}, expected {BYPASS}")
    wrote = [t for t in ro.get("tools") or [] if t in WRITE_TOOLS]
    if wrote:
        out.append(f"read_only: called {', '.join(wrote)}")
    if ro.get("file_written"):
        out.append("read_only: the file was written")
    if not ro.get("refused") and not ro.get("assistant_turns"):
        out.append("read_only: the session never answered")
    wake = report.get("wake") or {}
    if wake.get("modes") != [BYPASS]:
        out.append(f"wake: the woken turns ran {wake.get('modes') or 'unknown'}, "
                   f"expected {BYPASS}")
    disabled = report.get("disabled") or {}
    refused = disabled.get("refused") or ""
    if claude_cli.BYPASS_REFUSAL in refused:
        out.append(f"disabled: refused for the disclaimer, not by the setting: {refused[:200]}")
    elif BYPASS in (disabled.get("modes") or []):
        out.append("disabled: the session ran bypassPermissions anyway")
    elif not disabled.get("modes") and not refused:
        out.append("disabled: neither refused nor seen running")
    delivery = report.get("delivery") or {}
    if delivery.get("setting"):
        out.append(f"delivery: not measurable here: crossSessionInbound is "
                   f"{delivery['setting']!r}, which decides before any attestation")
        return out
    if delivery.get("refused"):
        out.append(f"delivery: refused: {delivery['refused'][:200]}")
        return out
    unsent = "; ".join(f"{k}: {v}" for k, v in (delivery.get("sent") or {}).items() if v)
    if unsent:
        out.append(f"delivery: a notice was not written: {unsent}")
        return out
    users = " ".join(delivery.get("users") or [])
    if ATTESTED not in users:
        out.append("delivery: the attested notice did not become a turn")
    if PLAIN in users:
        out.append("delivery: the plain notice became a turn, so nothing was held "
                   "and the attestation was not what decided")
    elif not any(PLAIN in h for h in delivery.get("held") or []):
        out.append("delivery: the plain notice was neither a turn nor held")
    return out


# --- the live part ------------------------------------------------------------

def _transcript(short: str) -> Optional[Path]:
    from mnemo.core.sessions import liveness

    found = sorted((liveness.claude_home() / "projects").glob(f"*/{short}*.jsonl"))
    return found[0] if found else None


def _read(short: str) -> List[str]:
    path = _transcript(short)
    try:
        return path.read_text(encoding="utf-8").splitlines() if path else []
    except OSError:
        return []


def _bg(args: List[str], cwd: Path) -> Dict[str, Any]:
    from mnemo.core import claude_cli

    run = subprocess.run(args, cwd=str(cwd), capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if run.returncode != 0:
        return {"refused": run.stderr.strip() or run.stdout.strip()}
    return {"short_id": claude_cli.require_short_id(run.stdout)}


def _settle(short: str, *, turns: int, after: Optional[str] = None,
            seconds: float = 180) -> Dict[str, Any]:
    deadline = time.monotonic() + seconds
    seen = summarize(_read(short), after=after)
    while seen["assistant_turns"] < turns and time.monotonic() < deadline:
        time.sleep(2)
        seen = summarize(_read(short), after=after)
    return seen


def _stop(short: str) -> None:
    subprocess.run(["claude", "stop", short], capture_output=True)


def _dir(scratch: Path, name: str) -> Path:
    path = scratch / name
    path.mkdir()
    return path


def measure(base: Path, model: str, keep: bool) -> Dict[str, Any]:
    from mnemo.core import child_mode
    from mnemo.core.sessions import inbound_policy, inbox, liveness

    home = liveness.claude_home()
    accepted = child_mode.bypass_accepted_in(home)
    report: Dict[str, Any] = {"accepted_in": str(accepted) if accepted else None}
    if accepted is None:
        report["skipped"] = (f"{home / 'settings.json'} does not record the bypass "
                             f"disclaimer as accepted ({child_mode.CONSENT_KEY}), so "
                             "dispatch starts a child of a bypass parent in auto here")
        return report
    scratch = Path(tempfile.mkdtemp(prefix=".mnemo-741-", dir=str(base)))
    report["scratch"] = str(scratch)
    started: List[str] = []
    try:
        ro_dir = _dir(scratch, "read-only")
        args = argv("read_only", ro_dir, model)
        report["launch"] = shown(args)
        spawn = _bg(args, ro_dir)
        report["read_only"] = spawn
        if spawn.get("short_id"):
            started.append(spawn["short_id"])
            seen = _settle(spawn["short_id"], turns=1)
            seen["file_written"] = (ro_dir / "probe.txt").exists()
            report["read_only"].update(seen)
            _stop(spawn["short_id"])
            time.sleep(5)
            path = _transcript(spawn["short_id"])
            if path is not None:
                subprocess.run(["claude", "--bg", "--resume", path.stem,
                                "WAKEPROBE: reply with one word and stop."],
                               cwd=str(ro_dir), capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
                report["wake"] = _settle(spawn["short_id"], turns=1, after="WAKEPROBE")

        off_dir = _dir(scratch, "disabled")
        spawn = _bg(argv("disabled", off_dir, model), off_dir)
        report["disabled"] = spawn
        if spawn.get("short_id"):
            started.append(spawn["short_id"])
            report["disabled"].update(_settle(spawn["short_id"], turns=1))

        parent_dir = _dir(scratch, "parent")
        spawn = _bg(argv("delivery", parent_dir, model), parent_dir)
        report["delivery"] = spawn
        spawn["setting"] = inbound_policy.effective_setting(home=home, cwd=str(parent_dir))
        if spawn.get("short_id"):
            started.append(spawn["short_id"])
            _settle(spawn["short_id"], turns=1)
            path = _transcript(spawn["short_id"])
            vault = Path.home() / "mnemo"
            if path is not None:
                report["delivery"]["sent"] = {
                    "plain": inbox.deliver(vault, path.stem, f"{PLAIN} notice"),
                    "attested": inbox.deliver(vault, path.stem, f"{ATTESTED} notice",
                                              attest="bypass"),
                }
                time.sleep(30)
                report["delivery"].update(summarize(_read(spawn["short_id"])))
        return report
    finally:
        for short in started:
            _stop(short)
            subprocess.run(["claude", "rm", short], capture_output=True)
        if not keep:
            time.sleep(3)
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", type=Path, default=Path.cwd(),
                        help="a directory Claude Code trusts (default: here)")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--keep", action="store_true", help="keep the scratch dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = measure(args.dir.resolve(), args.model, args.keep)
    report["verdict"] = verdict(report)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "dispatch's argv in a scratch dir, not a dispatch: no worktree and no parent "
        "link, so the child's notice and child-reports.jsonl are not exercised",
        "the delivery session stands in for a bypass parent but is started as a bypass "
        "child is; a real parent also loads the user's plugins and MCP servers",
        "the disabled case uses a --settings file; MDM and server-managed policy are not "
        "exercised",
        "mnemo's hooks are off in the probes (all of them on Windows): its briefing, "
        "SessionEnd and child report are not exercised",
        "whether Write is used is up to the model; a model that never tries proves less",
        "one session per case: a reading, not a rate",
    ])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        for key in ("accepted_in", "skipped", "launch", "read_only", "wake", "disabled",
                    "delivery"):
            if key in report:
                print(f"{key}: {report[key]}")
        print("PASS" if not report["verdict"] else "FAIL: " + "; ".join(report["verdict"]))
        print(_provenance.line(prov))
    return 0 if not report["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
