"""Does a Windows session take a turn on its pipe only after the peer key? (#725)

Usage:
    PYTHONPATH=src python3 tools/measure_windows_inbox.py [--model haiku] [--keep] [--json]

Windows only. It starts **one throwaway ``claude --bg`` session** in a fresh
temp directory — an LLM session, so it costs a few short turns on the
account — and stops it when done. Then it does what a finishing child does,
through the code mnemo ships:

1. finds the session in Claude Code's registry,
   :func:`mnemo.core.sessions.inbox.registry_address` (live pid, matching
   start time, this host);
2. writes a user turn on the pipe **with no auth line**
   (:func:`~mnemo.core.sessions.inbox.write_pipe`);
3. writes a second one the way a notice goes out,
   :func:`~mnemo.core.sessions.inbox.post_pipe`: the session's peer key
   first, then the turn.

Each turn carries a fresh nonce. It reads the session's transcript and says
which nonce arrived as a user turn. :func:`verdict` passes when the
authenticated turn arrived and the unauthenticated one did not.

The peer token is read by ``post_pipe`` and goes nowhere else: not to stdout,
not into the report, not into the scratch dir. The report says only whether
a key was found.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: How long to wait for the registry to name the session, for it to go idle,
#: and for a turn to reach the transcript.
REGISTRY_WAIT_SECONDS = 60.0
IDLE_WAIT_SECONDS = 180.0
ARRIVE_WAIT_SECONDS = 60.0

#: How long the unauthenticated turn gets before it is declared dropped. The
#: authenticated one is sent after this, so a late arrival is still seen.
DROP_WAIT_SECONDS = 15.0


def arrived(lines: Iterable[str], nonces: Iterable[str]) -> Dict[str, bool]:
    """Which of *nonces* appear in a ``user`` line of a transcript.

    The raw line is searched, so a turn Claude Code wraps in its own text
    (a peer warning, a list of content blocks) still counts.
    """
    wanted = list(nonces)
    out = {nonce: False for nonce in wanted}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("type") != "user":
            continue
        for nonce in wanted:
            if nonce in line:
                out[nonce] = True
    return out


def verdict(report: Dict[str, Any]) -> List[str]:
    """Every way *report* falls short of #725's protocol; empty when it passes."""
    if report.get("error"):
        return [str(report["error"])]
    out = []
    if not report.get("address"):
        return ["the registry never named a live session"]
    if not report.get("key_found"):
        out.append("no peer key with the entry's start time and host")
    if report.get("noauth_arrived"):
        out.append("the turn without an auth line arrived; auth is not required")
    if report.get("auth_error"):
        out.append(f"the authenticated write failed: {report['auth_error']}")
    if not report.get("auth_arrived"):
        out.append("the authenticated turn never reached the transcript")
    return out


# --- the live run -----------------------------------------------------------

def _poll(probe, seconds: float, every: float = 1.0):
    deadline = time.monotonic() + seconds
    while True:
        found = probe()
        if found or time.monotonic() >= deadline:
            return found
        time.sleep(every)


def _full_id(short: str, folder: Path) -> Optional[str]:
    for path in folder.glob("*.json"):
        try:
            sid = json.loads(path.read_text(encoding="utf-8")).get("sessionId") or ""
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(sid, str) and sid.startswith(short):
            return sid
    return None


def _status(sid: str, folder: Path) -> Optional[str]:
    from mnemo.core.sessions import registry

    entry = registry.live_entry(sid, directory=folder)
    return (entry or {}).get("status")


def _transcript(sid: str, home: Path) -> List[str]:
    for path in (home / "projects").glob(f"*/{sid}.jsonl"):
        try:
            return path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
    return []


def measure(model: str, keep: bool) -> Dict[str, Any]:
    from mnemo.core import claude_cli
    from mnemo.core.sessions import inbox, registry

    folder = registry.sessions_dir()
    home = folder.parent
    scratch = Path(tempfile.mkdtemp(prefix="mnemo-725-"))
    report: Dict[str, Any] = {"scratch": str(scratch)}
    short = ""
    run = subprocess.run(
        ["claude", "--bg", "--model", model, "Reply with the single word OK, then stop."],
        cwd=str(scratch), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        if run.returncode != 0:
            report["error"] = run.stderr.strip() or run.stdout.strip()
            return report
        short = claude_cli.require_short_id(run.stdout)
        report["short_id"] = short
        sid = _poll(lambda: _full_id(short, folder), REGISTRY_WAIT_SECONDS)
        address = sid and _poll(lambda: inbox.registry_address(sid, directory=folder),
                                REGISTRY_WAIT_SECONDS)
        if not address:
            return report
        report["address"] = {k: address[k] for k in ("socket", "pid", "pid_domain")}
        report["key_found"] = bool(inbox.peer_token(address))
        report["idle_before_send"] = bool(
            _poll(lambda: _status(sid, folder) == "idle", IDLE_WAIT_SECONDS))
        noauth, auth = (f"PROBE-NOAUTH-{uuid.uuid4().hex[:8]}",
                        f"PROBE-AUTH-{uuid.uuid4().hex[:8]}")
        report["noauth_error"] = inbox.write_pipe(address["socket"], [inbox.user_turn(
            f"{noauth}: reply with the single word OK.")]) or None
        time.sleep(DROP_WAIT_SECONDS)
        report["auth_error"] = inbox.post_pipe(
            address, f"{auth}: reply with the single word OK.") or None
        seen = _poll(lambda: arrived(_transcript(sid, home), [auth])[auth] or None,
                     ARRIVE_WAIT_SECONDS)
        both = arrived(_transcript(sid, home), [noauth, auth])
        report["noauth_arrived"], report["auth_arrived"] = both[noauth], bool(seen) or both[auth]
        return report
    finally:
        if short:
            subprocess.run(["claude", "stop", short], capture_output=True)
        if not keep:
            time.sleep(2)
            shutil.rmtree(scratch, ignore_errors=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--keep", action="store_true", help="keep the scratch dir")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if sys.platform != "win32":
        print("Windows only: the pipe and its auth line are Windows' alone.", file=sys.stderr)
        return 2
    report = measure(args.model, args.keep)
    report["verdict"] = verdict(report)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "one throwaway --bg session: an interactive parent runs the same pipe server "
        "but is not exercised here",
        "the turns are sent while the session is idle; how a busy session queues one "
        "is not measured",
        "'dropped' means absent from the transcript after "
        f"{DROP_WAIT_SECONDS:g}s plus the authenticated turn's wait",
    ])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        for key in ("short_id", "address", "key_found", "idle_before_send", "noauth_error",
                    "noauth_arrived", "auth_error", "auth_arrived", "error"):
            if key in report:
                print(f"{key}: {report[key]}")
        print("PASS" if not report["verdict"] else "FAIL: " + "; ".join(report["verdict"]))
        print(_provenance.line(prov))
    return 0 if not report["verdict"] else 1


if __name__ == "__main__":
    sys.exit(main())
