"""Which lean shape can a child of a bypass parent start in? (#822)

Usage:
    PYTHONPATH=src python3 tools/measure_bypass_lean.py [--dir DIR] [--model haiku] [--keep] [--json]

It tries six ``claude --bg`` launches. Two are expected to be refused before
anything starts. The other **four start throwaway sessions** on the account,
each one short turn, and two of those are then woken for one more turn. Every
session is stopped and removed when done. They run in fresh plain directories
under *--dir* (default: the current directory). It has to be a directory
Claude Code already trusts, and a fresh git repository is not one: ``--bg``
refuses it with "Workspace not trusted".

1. ``lean``: ``--permission-mode bypassPermissions`` with the lean flags as
   dispatch passed them before #822 (``child_profile.LEAN_FLAGS`` and mnemo's
   hand-back). Expected: refused, with ``claude_cli.BYPASS_REFUSAL``.
2. ``flag_consent``: the same, with ``skipDangerousModePermissionPrompt: true``
   also in its ``--settings`` file. Expected: refused the same way.
3. ``local_consent``: the same, with the key only in the directory's
   ``.claude/settings.local.json``. Records the mode it ran. Then the key is
   removed, and the session is stopped and woken with ``claude --bg --resume
   <id>`` as ``old_wake``, the shape of a child dispatched before #822.
   Expected: neither runs bypass.
4. ``profile``: ``child_profile.lean_args(dir, permission_mode=bypass)``, what
   dispatch starts since #822. Expected: ``bypassPermissions``, and no skill
   from a plugin it closed. Then stopped and woken as ``wake``. Expected: the
   woken turns are still ``bypassPermissions``.
5. ``baseline``: ``child_profile.lean_args(dir)`` with no mode, the lean
   ``auto`` child. For its first-turn input.
6. ``full``: ``--permission-mode bypassPermissions`` and nothing else, the
   full profile. For its first-turn input.

First-turn input is ``input + cache_creation + cache_read`` of the first
assistant record, as in ``child_profile``'s #270 table.
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
CONSENT = "skipDangerousModePermissionPrompt"
PROMPT = "Reply with exactly the word OK and nothing else. Do not use any tools."
WAKE = "WAKEPROBE: reply with exactly the word AGAIN. Do not use any tools."
_SKILL_RE = re.compile(r"^- (\S+?):(?:\s|$)", re.M)


# --- reading a transcript -----------------------------------------------------

def _usage_total(usage: Any) -> Optional[int]:
    if not isinstance(usage, dict):
        return None
    keys = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    values = [usage.get(k) for k in keys]
    if not any(isinstance(v, int) for v in values):
        return None
    return sum(v for v in values if isinstance(v, int))


def summarize(lines: Iterable[str], *, after: Optional[str] = None) -> Dict[str, Any]:
    """Modes, first-turn input and listed skills in a transcript's *lines*.

    With *after*, only what follows the first user turn containing it. Modes
    are read off ``user`` and ``assistant`` records, where Claude Code stamps
    each turn. An API error's synthetic record is not a turn.
    """
    modes: List[str] = []
    skills: List[str] = []
    first: Optional[int] = None
    assistant = 0
    started = after is None
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        attachment = row.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "skill_listing":
            skills = _SKILL_RE.findall(str(attachment.get("content") or ""))
            continue
        if row.get("type") not in ("user", "assistant"):
            continue
        message = row.get("message") or {}
        content = message.get("content")
        if not started:
            text = content if isinstance(content, str) else " ".join(
                str(b.get("text") or "") for b in content or [] if isinstance(b, dict))
            if row["type"] == "user" and after in text:
                started = True
            else:
                continue
        if isinstance(row.get("permissionMode"), str):
            modes.append(row["permissionMode"])
        if row["type"] == "assistant" and message.get("model") != "<synthetic>":
            assistant += 1
            if first is None:
                first = _usage_total(message.get("usage"))
    return {"modes": sorted(set(modes)), "first_turn_input": first, "skills": skills,
            "assistant_turns": assistant}


def plugin_skills(skills: Iterable[str], closed: Iterable[str]) -> List[str]:
    """The listed *skills* that belong to a plugin in *closed* (``name@market``)."""
    prefixes = {str(plugin).split("@", 1)[0] + ":" for plugin in closed}
    return [s for s in skills if any(s.startswith(p) for p in prefixes)]


def verdict(report: Dict[str, Any]) -> List[str]:
    """Every way *report* departs from the ``bg-bypass-needs-user-settings``
    assumption; empty when it holds."""
    from mnemo.core import claude_cli

    out = []
    for arm in ("lean", "flag_consent"):
        refused = (report.get(arm) or {}).get("refused")
        if not refused:
            out.append(f"{arm}: not refused; the CLI starts this shape again")
        elif claude_cli.BYPASS_REFUSAL not in refused:
            out.append(f"{arm}: refused for another reason: {refused[:200]}")
    for arm in ("local_consent", "old_wake"):
        seen = report.get(arm) or {}
        if BYPASS in (seen.get("modes") or []):
            out.append(f"{arm}: ran bypassPermissions; the old shape works again")
        elif not seen.get("modes") and not seen.get("refused"):
            out.append(f"{arm}: neither refused nor seen running")
    profile = report.get("profile") or {}
    if profile.get("refused"):
        out.append(f"profile: refused: {profile['refused'][:200]}")
    elif profile.get("modes") != [BYPASS]:
        out.append(f"profile: ran {profile.get('modes') or 'unknown'}, expected {BYPASS}")
    leaked = plugin_skills(profile.get("skills") or [], profile.get("closed") or [])
    if leaked:
        out.append(f"profile: a closed plugin's skills were listed: {', '.join(leaked[:5])}")
    wake = report.get("wake") or {}
    if wake.get("modes") != [BYPASS]:
        out.append(f"wake: the woken turns ran {wake.get('modes') or 'unknown'}, "
                   f"expected {BYPASS}")
    return out


# --- the live part ------------------------------------------------------------

def _env() -> Dict[str, str]:
    """This process's environment, minus what would tie a probe to the session
    running the tool: the probes are not its children."""
    drop = ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDECODE")
    return {k: v for k, v in os.environ.items() if k not in drop}


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

    run = subprocess.run(["claude", "--bg", *args], cwd=str(cwd), capture_output=True,
                         text=True, encoding="utf-8", errors="replace", env=_env())
    if run.returncode != 0:
        return {"refused": run.stderr.strip() or run.stdout.strip()}
    return {"short_id": claude_cli.require_short_id(run.stdout)}


def _settle(short: str, *, after: Optional[str] = None, seconds: float = 180) -> Dict[str, Any]:
    deadline = time.monotonic() + seconds
    seen = summarize(_read(short), after=after)
    while seen["assistant_turns"] < 1 and time.monotonic() < deadline:
        time.sleep(2)
        seen = summarize(_read(short), after=after)
    return seen


def _stop(short: str) -> None:
    subprocess.run(["claude", "stop", short], capture_output=True, env=_env())


def _wake(short: str, cwd: Path) -> Dict[str, Any]:
    """Stop *short*, wake it the way mnemo's wake channel does, read the woken turn."""
    _stop(short)
    time.sleep(5)
    path = _transcript(short)
    if path is None:
        return {"refused": "no transcript to wake"}
    run = subprocess.run(["claude", "--bg", "--resume", path.stem, WAKE], cwd=str(cwd),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", env=_env())
    if run.returncode != 0:
        return {"refused": run.stderr.strip() or run.stdout.strip()}
    return _settle(short, after="WAKEPROBE")


def _dir(scratch: Path, name: str) -> Path:
    path = scratch / name
    path.mkdir()
    return path


def measure(base: Path, model: str, keep: bool) -> Dict[str, Any]:
    from mnemo.core import child_profile

    scratch = Path(tempfile.mkdtemp(prefix=".mnemo-822-", dir=str(base)))
    report: Dict[str, Any] = {"scratch": str(scratch)}
    started: List[str] = []
    mode = ["--permission-mode", BYPASS, "--model", model]

    def run(name: str, cwd: Path, args: List[str]) -> Dict[str, Any]:
        spawn = _bg([*mode, *args, PROMPT] if name != "baseline"
                    else ["--model", model, *args, PROMPT], cwd)
        report[name] = spawn
        if spawn.get("short_id"):
            started.append(spawn["short_id"])
            spawn.update(_settle(spawn["short_id"]))
        return spawn

    try:
        here = _dir(scratch, "lean")
        run("lean", here, child_profile.lean_args(here))

        here = _dir(scratch, "flag-consent")
        run("flag_consent", here, child_profile.lean_args(here, {CONSENT: True}))

        here = _dir(scratch, "local-consent")
        local = here / ".claude" / "settings.local.json"
        local.parent.mkdir()
        local.write_text(json.dumps({CONSENT: True}), encoding="utf-8")
        spawn = run("local_consent", here, child_profile.lean_args(here))
        if spawn.get("short_id"):
            local.unlink()
            report["old_wake"] = _wake(spawn["short_id"], here)

        here = _dir(scratch, "profile")
        spawn = run("profile", here, child_profile.lean_args(here, permission_mode=BYPASS))
        spawn["closed"] = sorted(child_profile.plugins_to_close(here))
        if spawn.get("short_id"):
            report["wake"] = _wake(spawn["short_id"], here)

        here = _dir(scratch, "baseline")
        run("baseline", here, child_profile.lean_args(here))

        here = _dir(scratch, "full")
        run("full", here, [])
        return report
    finally:
        for short in started:
            _stop(short)
            subprocess.run(["claude", "rm", short], capture_output=True, env=_env())
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
        "one session per arm: the token counts are single readings, not a spread",
        "throwaway directories, not a dispatch: no parent link, no worktree, and "
        "mnemo's SessionStart briefing is for an empty project",
        "only this machine's user settings: what loading them costs depends on "
        "the plugins, skills and hooks installed",
        "consent in MDM or server-managed policy, and a machine that never "
        "accepted the disclaimer, are not exercised",
    ])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        for key in ("lean", "flag_consent", "local_consent", "old_wake", "profile",
                    "wake", "baseline", "full"):
            if key in report:
                shown = {k: v for k, v in report[key].items() if k != "skills"}
                shown["skills"] = len(report[key].get("skills") or [])
                print(f"{key}: {shown}")
        print("PASS" if not report["verdict"] else "FAIL: " + "; ".join(report["verdict"]))
        print(_provenance.line(prov))
    return 0 if not report["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
