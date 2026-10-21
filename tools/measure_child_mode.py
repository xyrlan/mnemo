"""Does a child started in ``bypassPermissions`` behave as #741 needs?

Usage:
    PYTHONPATH=src python3 tools/measure_child_mode.py [--dir DIR] [--model haiku] [--keep] [--json]

It starts **four throwaway ``claude --bg`` sessions**, each an LLM session
costing one or two short turns on the account, and stops each when done. They
run in a fresh directory under *--dir* (default: the current directory, which
must be one Claude Code already trusts: ``--bg`` refuses an untrusted one).
Every session is lean (``--setting-sources project,local --strict-mcp-config``),
as a dispatched child is, so the user's own ``crossSessionInbound`` is not in
force for any of them.

1. ``read_only``: ``--permission-mode bypassPermissions`` with
   ``--disallowedTools Edit Write NotebookEdit``, asked to create a file with
   ``Write``. Passes when it ran ``bypassPermissions``, called no write tool
   and wrote nothing.
2. ``wake``: that session stopped and woken with ``claude --bg --resume <id>``
   and no mode flag, mnemo's wake channel. Passes when the woken turns are
   still ``bypassPermissions``.
3. ``disabled``: ``bypassPermissions`` asked for with a ``--settings`` file
   setting ``permissions.disableBypassPermissionsMode: "disable"``. Records
   whether ``--bg`` refused it and which mode the session really ran; passes
   when it did not run bypass.
4. ``delivery``: a ``bypassPermissions`` session sent two notices through
   mnemo's own inbox, one plain and one attested ``from-mode="bypass"``.
   Passes when the attested one became a turn and the plain one was held (a
   ``Held peer message`` row): the hold is real, and the attestation lifts it.
"""
from __future__ import annotations

import argparse
import json
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
LEAN = ["--setting-sources", "project,local", "--strict-mcp-config"]
WRITE_TOOLS = ("Edit", "Write", "NotebookEdit")
PLAIN, ATTESTED = "PLAINPROBE", "ATTESTEDPROBE"


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
    out = []
    ro = report.get("read_only") or {}
    if ro.get("modes") != [BYPASS]:
        out.append(f"read_only: ran {ro.get('modes') or 'unknown'}, expected {BYPASS}")
    wrote = [t for t in ro.get("tools") or [] if t in WRITE_TOOLS]
    if wrote:
        out.append(f"read_only: called {', '.join(wrote)}")
    if ro.get("file_written"):
        out.append("read_only: the file was written")
    if not ro.get("assistant_turns"):
        out.append("read_only: the session never answered")
    wake = report.get("wake") or {}
    if wake.get("modes") != [BYPASS]:
        out.append(f"wake: the woken turns ran {wake.get('modes') or 'unknown'}, "
                   f"expected {BYPASS}")
    disabled = report.get("disabled") or {}
    if BYPASS in (disabled.get("modes") or []):
        out.append("disabled: the session ran bypassPermissions anyway")
    elif not disabled.get("modes") and not disabled.get("refused"):
        out.append("disabled: neither refused nor seen running")
    delivery = report.get("delivery") or {}
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

    run = subprocess.run(["claude", "--bg", *args], cwd=str(cwd), capture_output=True,
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


def measure(base: Path, model: str, keep: bool) -> Dict[str, Any]:
    from mnemo.core.sessions import inbox

    scratch = Path(tempfile.mkdtemp(prefix=".mnemo-741-", dir=str(base)))
    report: Dict[str, Any] = {"scratch": str(scratch)}
    started: List[str] = []
    try:
        ro_dir = scratch / "read-only"
        ro_dir.mkdir()
        spawn = _bg([*LEAN, "--permission-mode", BYPASS, "--model", model,
                     "--disallowedTools", *WRITE_TOOLS, "--",
                     "Use the Write tool to create probe.txt here containing hello. Use no "
                     "other tool to write. Say whether Write was available, then stop."],
                    ro_dir)
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

        off_dir = scratch / "disabled"
        off_dir.mkdir()
        (off_dir / "off.json").write_text(json.dumps(
            {"permissions": {"disableBypassPermissionsMode": "disable"}}), encoding="utf-8")
        spawn = _bg([*LEAN, "--permission-mode", BYPASS, "--model", model,
                     "--settings", str(off_dir / "off.json"), "Reply OK and stop."], off_dir)
        report["disabled"] = spawn
        if spawn.get("short_id"):
            started.append(spawn["short_id"])
            report["disabled"].update(_settle(spawn["short_id"], turns=1))

        parent_dir = scratch / "parent"
        parent_dir.mkdir()
        spawn = _bg([*LEAN, "--permission-mode", BYPASS, "--model", model,
                     "Reply ready. To every later message reply with its first word only."],
                    parent_dir)
        report["delivery"] = spawn
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
        "lean sessions in a scratch dir, not a dispatch: the parent link, the child's "
        "SessionEnd hook and child-reports.jsonl are not exercised",
        "the disabled case uses a --settings file; MDM and server-managed policy are not "
        "exercised",
        "whether Write is used is up to the model; a model that never tries proves less",
    ])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        for key in ("read_only", "wake", "disabled", "delivery"):
            if key in report:
                print(f"{key}: {report[key]}")
        print("PASS" if not report["verdict"] else "FAIL: " + "; ".join(report["verdict"]))
        print(_provenance.line(prov))
    return 0 if not report["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
