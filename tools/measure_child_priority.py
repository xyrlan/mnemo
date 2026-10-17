"""Does a marked ``--bg`` child come out below-normal, and nothing above it? (#723)

Usage:
    PYTHONPATH=src python3 tools/measure_child_priority.py [--model haiku] [--keep] [--json]

Windows only. It starts **one throwaway ``claude --bg`` session** in a fresh
temp directory — an LLM session, so it costs one short turn on the account —
and stops it when done. The session gets a ``--settings`` file holding:

- the dispatcher's marker, ``env: {MNEMO_CHILD_PRIORITY: below_normal}``;
- a ``SessionStart`` hook running this file's ``probe`` mode, which does what
  mnemo's hook does — :func:`mnemo.core.child_priority.lower_session` — and
  records whether Claude Code's registry already named the session when the
  hook ran, and how long it waited for it;
- ``permissions.allow: ["Bash"]`` and a prompt asking for one command, this
  file's ``self`` mode, which records the priority class it was born with.

Then, from outside, it reads the priority class of the session's process and
of its two ``claude`` ancestors (the pty host and the shared daemon). It then
stops the session and wakes it with ``claude --bg --resume <full id>``, the
channel ``mnemo``'s wakes use, and reads all of it again: a woken child is a
new process, and must come back lowered too. :func:`verdict` asks, for the
start and for the wake, the session and its later command ``BelowNormal``,
the pty host and the daemon ``Normal``.

This is the method of the #723 measurement comment, rebuilt on the registry
lookup the hook now uses rather than on the hook's process ancestry.
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
from typing import Any, Callable, Dict, List, Optional

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: Win32 priority classes by value, named as ``Get-Process`` names them.
CLASSES = {
    0x0040: "Idle", 0x4000: "BelowNormal", 0x0020: "Normal",
    0x8000: "AboveNormal", 0x0080: "High", 0x0100: "RealTime",
}

#: How long the probe waits for the registry to name its session.
PROBE_WAIT_SECONDS = 5.0


def probe(
    payload: Dict[str, Any], *, find: Callable[[str], Optional[Dict[str, Any]]],
    lower: Callable[[str], Optional[str]],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    wait: float = PROBE_WAIT_SECONDS,
) -> Dict[str, Any]:
    """What the ``SessionStart`` hook saw: the registry entry, then the lowering."""
    sid = str(payload.get("session_id") or "")
    started = clock()
    entry = find(sid)
    first_try = entry is not None
    while entry is None and clock() - started < wait:
        sleep(0.1)
        entry = find(sid)
    waited_ms = round((clock() - started) * 1000)
    return {
        "session_id": sid,
        "registry_at_first_try": first_try,
        "registry_waited_ms": waited_ms,
        "pid": entry.get("pid") if entry else None,
        "kind": entry.get("kind") if entry else None,
        "lower_failed": lower(sid),
    }


def verdict(report: Dict[str, Any]) -> List[str]:
    """Every way *report* falls short of #723's acceptance; empty when it passes."""
    out = _verdict(report.get("start") or {})
    out += [f"after the wake: {why}" for why in _verdict(report.get("wake") or {})]
    return out


def _verdict(report: Dict[str, Any]) -> List[str]:
    out = []
    hook = report.get("probe") or {}
    if not hook:
        out.append("the SessionStart probe never ran")
    else:
        if not hook.get("registry_at_first_try"):
            out.append("the registry did not name the session when the hook ran")
        if hook.get("lower_failed"):
            out.append(f"lowering failed: {hook['lower_failed']}")
    expect = {"session": "BelowNormal", "command": "BelowNormal",
              "pty_host": "Normal", "daemon": "Normal"}
    classes = report.get("classes") or {}
    for name, want in expect.items():
        got = classes.get(name)
        if got != want:
            out.append(f"{name} is {got or 'unknown'}, expected {want}")
    return out


# --- the two modes the throwaway session runs ------------------------------

def _probe_main(out: Path) -> int:
    from mnemo.core import child_priority
    from mnemo.core.sessions import registry

    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    record = probe(
        payload if isinstance(payload, dict) else {},
        find=registry.live_entry,
        lower=lambda sid: child_priority.lower_session(sid, None),
    )
    out.write_text(json.dumps(record), encoding="utf-8")
    return 0


def _own_class() -> Optional[str]:
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    return CLASSES.get(kernel32.GetPriorityClass(kernel32.GetCurrentProcess()))


def _self_main(out: Path) -> int:
    out.write_text(json.dumps({"class": _own_class(), "pid": os.getpid()}), encoding="utf-8")
    return 0


# --- the outside view -------------------------------------------------------

def _process(pid: int) -> Dict[str, Any]:
    """Name, parent and priority class of *pid*, read through PowerShell."""
    script = (
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={int(pid)}'; "
        f"$g = Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; "
        "@{name=$p.Name; parent=$p.ParentProcessId; class=\"$($g.PriorityClass)\"} "
        "| ConvertTo-Json -Compress"
    )
    run = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                         capture_output=True, text=True)
    try:
        return json.loads(run.stdout)
    except ValueError:
        return {}


def _wait_for(path: Path, seconds: float) -> Optional[Dict[str, Any]]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(1)
    return None


def _observe(probe_out: Path, self_out: Path) -> Dict[str, Any]:
    """The probe's record, the command's class, and the classes from outside."""
    seen: Dict[str, Any] = {"probe": _wait_for(probe_out, 120)}
    command = _wait_for(self_out, 240)
    classes: Dict[str, Any] = {"command": (command or {}).get("class")}
    pid = (seen["probe"] or {}).get("pid")
    if pid:
        session = _process(pid)
        pty = _process(session.get("parent") or 0)
        daemon = _process(pty.get("parent") or 0)
        classes.update(session=session.get("class"), pty_host=pty.get("class"),
                       daemon=daemon.get("class"))
        seen["chain"] = [session.get("name"), pty.get("name"), daemon.get("name")]
    seen["classes"] = classes
    return seen


def measure(model: str, keep: bool) -> Dict[str, Any]:
    from mnemo.core import child_priority, claude_cli

    scratch = Path(tempfile.mkdtemp(prefix="mnemo-723-"))
    probe_out, self_out = scratch / "probe.json", scratch / "self.json"
    me = Path(__file__).resolve().as_posix()
    py = Path(sys.executable).as_posix()
    settings = {
        "env": {child_priority.ENV: child_priority.BELOW_NORMAL},
        "hooks": {"SessionStart": [{"hooks": [{
            "type": "command",
            "command": f'"{py}" "{me}" probe --out "{probe_out.as_posix()}"',
        }]}]},
        "permissions": {"allow": ["Bash"]},
    }
    (scratch / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    prompt = (f'Run exactly this one Bash command, then stop: "{py}" "{me}" self '
              f'--out "{self_out.as_posix()}"')
    run = subprocess.run(
        ["claude", "--bg", "--model", model, "--settings",
         str(scratch / "settings.json"), prompt],
        cwd=str(scratch), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    report: Dict[str, Any] = {"scratch": str(scratch)}
    short = ""
    try:
        if run.returncode != 0:
            report["error"] = run.stderr.strip() or run.stdout.strip()
            return report
        short = claude_cli.require_short_id(run.stdout)
        report["short_id"] = short
        report["start"] = _observe(probe_out, self_out)
        sid = (report["start"].get("probe") or {}).get("session_id")
        subprocess.run(["claude", "stop", short], capture_output=True)
        if sid:
            time.sleep(5)
            for path in (probe_out, self_out):
                path.unlink(missing_ok=True)
            subprocess.run(["claude", "--bg", "--resume", sid, prompt], cwd=str(scratch),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
            report["wake"] = _observe(probe_out, self_out)
        return report
    finally:
        if short:
            subprocess.run(["claude", "stop", short], capture_output=True)
        if not keep:
            time.sleep(2)
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="mode")
    for mode in ("probe", "self"):
        sub.add_parser(mode).add_argument("--out", required=True, type=Path)
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--keep", action="store_true", help="keep the scratch dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.mode == "probe":
        return _probe_main(args.out)
    if args.mode == "self":
        return _self_main(args.out)
    if sys.platform != "win32":
        print("Windows only: #723 lowers children on Windows alone.", file=sys.stderr)
        return 2
    report = measure(args.model, args.keep)
    report["verdict"] = verdict(report)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "one throwaway session in an empty temp dir: a child's own repo, profile and "
        "hooks are not part of it",
        "the session's later command is the one the model was asked to run; a model that "
        "does not run it leaves 'command' unknown",
        "a respawn the daemon makes on its own is not exercised; the wake is "
        "`claude --bg --resume`, the channel mnemo's wakes use",
    ])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        for key in ("short_id", "start", "wake", "error"):
            if key in report:
                print(f"{key}: {report[key]}")
        print("PASS" if not report["verdict"] else "FAIL: " + "; ".join(report["verdict"]))
        print(_provenance.line(prov))
    return 0 if not report["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
