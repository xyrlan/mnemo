"""How long mnemo's hooks and the reflex judge really take (#593).

Usage:
    PYTHONPATH=src python3 tools/measure_hook_durations.py [--vault ~/mnemo] [--projects ~/.claude/projects] [--days N] [--wall-ms 2500] [--json]
    PYTHONPATH=src python3 tools/measure_hook_durations.py --timed RUNS --scratch DIR --cwd PROJECT [--transcript PATH]

The first form is read-only: no LLM calls, no network, no writes.

Two questions, one per half of #593.

**Does the judge's row ever run past its wall?** Every ``reflex-log.jsonl``
row (and its rotated ``.1``) with a ``judge`` object is read; of the
``status: ok`` rows it reports p50/p95/p99/max of ``ms`` and how many are over
``--wall-ms``. Before #593 the wall wrapped the HTTP call only, so the key
lookup and the page reads could push an ``ok`` row past 2,500 ms; after it, no
row's ``ms`` can exceed the wall by more than thread-scheduling noise, and
``over_wall`` counts rows logged before the fix.

**What timeout can each hook declare without killing normal work?** Claude
Code writes a transcript attachment for a hook that printed something, failed
or was cancelled, carrying ``durationMs`` (and ``timedOut``/``timeoutMs`` for a
cancellation). Of mnemo's four hooks that reaches ``SessionStart`` (it always
prints the briefing), ``PreToolUse`` (when it injects) and the
``UserPromptSubmit`` runs Claude Code cancelled. A quiet ``UserPromptSubmit``
and every ``SessionEnd`` leave no attachment — the session is gone by the
time the latter finishes — so for those two this reports what it found and
says how much of the population that is; it does not invent the rest.

**Timed runs.** ``--timed`` runs ``session_start`` (#610),
``user_prompt_submit`` and ``session_end`` ``RUNS`` times each as Claude
Code would — a fresh interpreter, the payload on stdin — and times the whole
process. It never touches the real vault: ``--vault`` is cloned into
``--scratch`` (``cp -c``, copy-on-write on APFS), a config naming the clone is
written beside it, ``TMPDIR`` points into the scratch dir, the reflex judge is
turned off in the clone (so nothing leaves the machine; the judge adds at most
its own wall, ``timeoutSeconds``, on top), and every detached spawn — the
extraction, the briefing, the ``pr-follow`` watcher — is replaced by a no-op,
so what is timed is the synchronous work a hook timeout would cut. The
clone's ``install.autoRepairHooks`` is off, so ``session_start`` never writes
the real ``settings.json``, and ``MNEMO_HOOK_PHASES`` is set, so each
``session_start`` run logs its phase times into the clone; the report prints
the phases of the run at the p50 and at the p95, and each phase's own spread.
``--cwd`` is the project the session pretends to be in; a project path rather
than a temp dir, because a hook in a temp dir returns before any work (#420).

A hook is mnemo's when its command runs ``mnemo.hooks.<name>`` (``mnemo
init``'s settings entries) or ``hook <name>`` through mnemo's launcher (the
plugin's ``hooks.json``). ``mnemo-desktop``'s hook is a different program and
is not counted.

Percentiles are nearest-rank, :func:`mnemo.core.reflex.judge_stats.percentile`
— the one ``mnemo rerank`` prints — so a number here is a number there.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mnemo.core.log_utils import iter_rotated_rows  # noqa: E402
from mnemo.core.reflex.judge import DEFAULT_TIMEOUT_S  # noqa: E402
from mnemo.core.reflex.judge_stats import percentile  # noqa: E402
from mnemo.hooks.session_start import PHASES_ENV, PHASES_LOG  # noqa: E402

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

DEFAULT_WALL_MS = int(DEFAULT_TIMEOUT_S * 1000)

#: The four hooks mnemo installs, by the event Claude Code fires them on.
HOOKS = {
    "session_start": "SessionStart",
    "user_prompt_submit": "UserPromptSubmit",
    "pre_tool_use": "PreToolUse",
    "session_end": "SessionEnd",
}

_COMMAND = re.compile(r"(?:mnemo\.hooks\.|\bmnemo(?:\.cmd)?\"?\s+hook\s+)(%s)\b"
                      % "|".join(HOOKS))


def mnemo_hook(command: Any) -> Optional[str]:
    """The mnemo hook a command runs (``session_start``…), or ``None``."""
    text = str(command or "")
    if "mnemo-desktop" in text:
        return None
    found = _COMMAND.search(text)
    return found.group(1) if found else None


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _spread(values: List[float]) -> Dict[str, Any]:
    return {
        "n": len(values),
        "p50": percentile(values, 0.5),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values) if values else None,
    }


def _cutoff(days: Optional[int]) -> Optional[str]:
    if not days:
        return None
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def judge_rows(rows: Iterable[Dict[str, Any]], *, wall_ms: float = DEFAULT_WALL_MS,
               since: Optional[str] = None) -> Dict[str, Any]:
    """The ``ok`` judge rows' spread of ``ms``, and how many ran past the wall."""
    by_status: Dict[str, int] = {}
    ok: List[float] = []
    for row in rows:
        info = row.get("judge")
        if not isinstance(info, dict):
            continue
        if since and str(row.get("ts") or "") < since:
            continue
        status = str(info.get("status") or "unknown")
        by_status[status] = by_status.get(status, 0) + 1
        ms = _number(info.get("ms"))
        if status == "ok" and ms is not None:
            ok.append(ms)
    over = sorted(ms for ms in ok if ms > wall_ms)
    return {"wall_ms": wall_ms, "by_status": by_status, "ok": _spread(ok),
            "over_wall": len(over), "over_wall_ms": over}


def transcript_rows(path: str) -> Iterable[Dict[str, Any]]:
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            if '"durationMs"' not in line or "mnemo" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def _empty(hook: str) -> Dict[str, Any]:
    return {"event": HOOKS[hook], "all": [], "completed": [], "cancelled": 0,
            "timed_out": 0, "timeouts_ms": {}}


def hook_durations(rows: Iterable[Dict[str, Any]], *,
                   since: Optional[str] = None) -> Dict[str, Any]:
    """Per mnemo hook: the spread of ``durationMs`` and the cancellations.

    ``all`` is every attachment carrying a duration; ``completed`` leaves out
    the runs Claude Code cancelled, whose duration is the timeout that killed
    them rather than the work.
    """
    out: Dict[str, Any] = {}
    for row in rows:
        attachment = row.get("attachment")
        if not isinstance(attachment, dict):
            continue
        hook = mnemo_hook(attachment.get("command"))
        ms = _number(attachment.get("durationMs"))
        if hook is None or ms is None:
            continue
        if since and str(row.get("timestamp") or "") < since:
            continue
        entry = out.setdefault(hook, _empty(hook))
        entry["all"].append(ms)
        if attachment.get("type") == "hook_cancelled" or attachment.get("timedOut"):
            entry["cancelled"] += 1
            if attachment.get("timedOut"):
                entry["timed_out"] += 1
                limit = str(attachment.get("timeoutMs"))
                entry["timeouts_ms"][limit] = entry["timeouts_ms"].get(limit, 0) + 1
        else:
            entry["completed"].append(ms)
    for hook in HOOKS:
        entry = out.setdefault(hook, _empty(hook))
        entry["all"] = _spread(entry["all"])
        entry["completed"] = _spread(entry["completed"])
    return out


def _when(stamp: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_prompt(row: Dict[str, Any]) -> bool:
    if row.get("type") != "user" or row.get("isMeta"):
        return False
    content = (row.get("message") or {}).get("content")
    return isinstance(content, str) or (isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "text" for part in content))


def first_prompt_gap(rows: Iterable[Dict[str, Any]]) -> Optional[Dict[str, float]]:
    """Seconds from mnemo's ``SessionStart:startup`` finishing to the first
    prompt of the session, and how long the hook ran (#610).

    Claude Code writes the hook's attachment when the hook returns, so a
    prompt it held until then lands at a gap of about zero however long the
    hook ran, and a prompt it let through early lands at a negative gap. None
    when the transcript has no such hook run or no prompt.
    """
    end: Optional[datetime] = None
    ms: Optional[float] = None
    for row in rows:
        attachment = row.get("attachment")
        if (end is None and isinstance(attachment, dict)
                and attachment.get("type") == "hook_success"
                and str(attachment.get("hookName") or "") == "SessionStart:startup"
                and mnemo_hook(attachment.get("command")) == "session_start"):
            end = _when(row.get("timestamp"))
            ms = _number(attachment.get("durationMs"))
        elif _is_prompt(row):
            first = _when(row.get("timestamp"))
            if end is None or ms is None or first is None:
                return None
            return {"gap_s": (first - end).total_seconds(), "hook_ms": ms}
    return None


def _head_rows(path: str) -> Iterable[Dict[str, Any]]:
    try:
        handle = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return
    with handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def prompt_holds(gaps: Iterable[Dict[str, float]], *, slow_ms: float = 10000,
                 within_s: float = 1.0) -> Dict[str, Any]:
    """Does the first prompt wait for SessionStart? Over the runs that took
    at least *slow_ms* — long enough that a user or a dispatched child's
    launch prompt would show up mid-hook if Claude Code let it — how many
    first prompts landed within *within_s* after the hook returned, and how
    many before it did."""
    gaps = list(gaps)
    slow = [g for g in gaps if g["hook_ms"] >= slow_ms]
    return {
        "sessions": len(gaps),
        "prompt_before_hook_end": sum(1 for g in gaps if g["gap_s"] < 0),
        "slow_ms": slow_ms,
        "slow": len(slow),
        "slow_prompt_before_hook_end": sum(1 for g in slow if g["gap_s"] < 0),
        "slow_prompt_within_s": within_s,
        "slow_prompt_within": sum(1 for g in slow if 0 <= g["gap_s"] < within_s),
    }


def measure(vault_root: str, projects_root: str, *, days: Optional[int] = None,
            wall_ms: float = DEFAULT_WALL_MS) -> Dict[str, Any]:
    since = _cutoff(days)
    log = os.path.join(vault_root, ".mnemo", "reflex-log.jsonl")
    transcripts = (glob.glob(os.path.join(projects_root, "*", "*.jsonl"))
                   + glob.glob(os.path.join(projects_root, "*", "*", "subagents", "*.jsonl")))

    def _all() -> Iterable[Dict[str, Any]]:
        for path in sorted(transcripts):
            yield from transcript_rows(path)

    from pathlib import Path

    def _gaps() -> Iterable[Dict[str, float]]:
        for path in sorted(transcripts):
            gap = first_prompt_gap(_head_rows(path))
            if gap is not None:
                yield gap

    return {
        "days": days,
        "transcripts": len(transcripts),
        "judge": judge_rows(iter_rotated_rows(Path(log)), wall_ms=wall_ms, since=since),
        "hooks": hook_durations(_all(), since=since),
        "first_prompt": prompt_holds(_gaps()),
    }


#: Run inside each timed hook process: the spawns are what a hook leaves
#: behind, not what it waits for, and a timed run must not start real workers.
_DRIVER = """
import importlib, sys
import mnemo._detach
mnemo._detach.spawn = lambda *a, **k: 0
import mnemo.hooks.session_start as _start
_start._spawn_detached = lambda *a, **k: None
import mnemo.autopilot.core.scheduler as _autopilot
_autopilot.run_detached = lambda **k: None
sys.exit(importlib.import_module("mnemo.hooks." + sys.argv[1]).main())
"""

#: Engineering prompts for ``user_prompt_submit``: long enough to pass the
#: token pre-gate, varied enough to rank different rules.
PROMPTS = [
    "the release workflow failed on windows, can you look at the ci logs and fix the test",
    "add a doctor check that warns when the reflex index is older than the vault",
    "why does the session start hook take so long on a big vault, profile it",
    "refactor the briefing picker so the judge and the lexical stage share one ranking",
    "write a test for the pr-follow watcher when the check run never started",
    "the dispatch child did not tell its parent it finished, find out why",
    "measure how often the reflex injects a rule the agent then ignores",
    "update the changelog fragment and open a pull request for this branch",
]


def time_command(argv: List[str], *, stdin: str, env: Dict[str, str], cwd: str) -> float:
    """Wall milliseconds of one process, from start to exit."""
    import subprocess
    import time

    started = time.perf_counter()
    subprocess.run(argv, input=stdin.encode("utf-8"), env=env, cwd=cwd,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return (time.perf_counter() - started) * 1000


def prepare_scratch(vault_root: str, scratch: str) -> Dict[str, str]:
    """Clone the vault into *scratch* and return the environment a timed hook runs in."""
    import shutil
    import subprocess

    clone = os.path.join(scratch, "vault")
    if not os.path.isdir(clone):
        os.makedirs(scratch, exist_ok=True)
        done = subprocess.run(["cp", "-cR", vault_root, clone], check=False).returncode == 0
        if not done:
            shutil.copytree(vault_root, clone, symlinks=True)
    config_path = os.path.join(clone, "mnemo.config.json")
    try:
        with open(config_path, encoding="utf-8") as handle:
            cfg = json.load(handle)
    except (OSError, ValueError):
        cfg = {}
    cfg["vaultRoot"] = clone
    cfg.setdefault("reflex", {}).setdefault("judge", {})["provider"] = "none"
    # session_start repairs drifted hook matchers in the real settings.json;
    # a timed run must not write there.
    cfg.setdefault("install", {})["autoRepairHooks"] = False
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(cfg, handle, indent=2)
    tmp = os.path.join(scratch, "tmp")
    os.makedirs(tmp, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k != "MNEMO_HOOKS_OFF"}
    env.update({"MNEMO_CONFIG_PATH": config_path, "TMPDIR": tmp, PHASES_ENV: "1",
                "PYTHONPATH": mnemo_src()})
    return env


def mnemo_src() -> str:
    """The directory holding the ``mnemo`` this tool imported (#610).

    A timed hook runs with ``--cwd`` as its working directory, so a relative
    ``PYTHONPATH=src`` resolved there, not here: run from a worktree against
    ``--cwd ~/github/mnemo``, every hook imported the main checkout's mnemo
    and timed code the branch never touched. Pinned absolute, a timed run
    times the code the report's provenance names."""
    import mnemo

    return os.path.dirname(os.path.dirname(os.path.abspath(mnemo.__file__)))


def phase_rows(vault: str, session_ids: Iterable[str]) -> List[Dict[str, Any]]:
    """The per-phase rows the timed ``session_start`` runs wrote (#610)."""
    wanted = set(session_ids)
    out: List[Dict[str, Any]] = []
    try:
        with open(os.path.join(vault, PHASES_LOG), encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("session_id") in wanted:
                    out.append(row)
    except OSError:
        pass
    return out


def phase_breakdown(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The phase rows of the run at the p50 and at the p95 of ``total_ms``,
    and per phase its p50/p95/max over every run."""
    if not rows:
        return {}
    ranked = sorted(rows, key=lambda r: float(r.get("total_ms") or 0))
    def at(q: float) -> Dict[str, Any]:
        # nearest rank, as percentile() takes it
        import math
        return ranked[max(0, min(len(ranked) - 1, int(math.ceil(q * len(ranked))) - 1))]
    names: List[str] = []
    for row in rows:
        for name in row.get("phases") or {}:
            if name not in names:
                names.append(name)
    per_phase = {name: _spread([float((r.get("phases") or {}).get(name) or 0) for r in rows])
                 for name in names}
    return {"p50_run": at(0.5), "p95_run": at(0.95), "per_phase": per_phase}


def timed_runs(runs: int, *, env: Dict[str, str], cwd: str,
               transcript: Optional[str] = None,
               python: str = sys.executable) -> Dict[str, Any]:
    """Time ``session_start``, ``user_prompt_submit`` and ``session_end``
    *runs* times each; ``session_start`` also reports its phase breakdown."""
    import uuid

    out: Dict[str, Any] = {}
    start_sids: List[str] = []
    for hook in ("session_start", "user_prompt_submit", "session_end"):
        values: List[float] = []
        for i in range(runs):
            sid = str(uuid.uuid4())
            payload: Dict[str, Any] = {"session_id": sid, "cwd": cwd,
                                       "hook_event_name": HOOKS[hook]}
            if hook == "session_start":
                payload["source"] = "startup"
                start_sids.append(sid)
            elif hook == "user_prompt_submit":
                payload["prompt"] = PROMPTS[i % len(PROMPTS)]
            else:
                payload["reason"] = "exit"
                if transcript:
                    payload["transcript_path"] = transcript
            values.append(time_command([python, "-c", _DRIVER, hook], stdin=json.dumps(payload),
                                       env=env, cwd=cwd))
        out[hook] = _spread(values)
    vault = os.path.dirname(env.get("MNEMO_CONFIG_PATH") or "")
    out["session_start_phases"] = phase_breakdown(phase_rows(vault, start_sids))
    return out


def _ms(value: Any) -> str:
    return "-" if value is None else "{:,}".format(int(value))


def render(report: Dict[str, Any]) -> str:
    judge = report["judge"]
    ok = judge["ok"]
    lines = [
        "reflex judge (%s): %s ok rows, p50 %s ms, p95 %s, p99 %s, max %s; "
        "%d over the %s ms wall" % (
            "last %dd" % report["days"] if report["days"] else "all rows",
            "{:,}".format(ok["n"]), _ms(ok["p50"]), _ms(ok["p95"]), _ms(ok["p99"]),
            _ms(ok["max"]), judge["over_wall"], _ms(judge["wall_ms"])),
        "",
        "hook durations from %d transcripts (ms; completed runs, cancellations apart):"
        % report["transcripts"],
        "  %-20s %6s %8s %8s %8s %8s  %s" % ("hook", "n", "p50", "p95", "p99", "max",
                                            "cancelled"),
    ]
    for hook in HOOKS:
        entry = report["hooks"][hook]
        done = entry["completed"]
        cancelled = "%d" % entry["cancelled"]
        if entry["timeouts_ms"]:
            cancelled += ", %d timed out (" % entry["timed_out"] + ", ".join(
                "at %s ms x%d" % (_ms(float(k)) if k not in ("None", "") else "?", v)
                for k, v in sorted(entry["timeouts_ms"].items())) + ")"
        lines.append("  %-20s %6d %8s %8s %8s %8s  %s" % (
            entry["event"], done["n"], _ms(done["p50"]), _ms(done["p95"]),
            _ms(done["p99"]), _ms(done["max"]), cancelled))
    held = report.get("first_prompt")
    if held:
        lines += ["", "first prompt vs SessionStart:startup (%d sessions): %d prompts timestamped "
                  "before the hook returned; of the %d runs >= %s ms, %d prompts landed within "
                  "%.0f s after it returned and %d before" % (
                      held["sessions"], held["prompt_before_hook_end"], held["slow"],
                      _ms(held["slow_ms"]), held["slow_prompt_within"],
                      held["slow_prompt_within_s"], held["slow_prompt_before_hook_end"])]
    return "\n".join(lines)


def render_phases(breakdown: Dict[str, Any]) -> str:
    if not breakdown:
        return "SessionStart phases: no rows (was %s set?)" % PHASES_ENV
    p50, p95 = breakdown["p50_run"], breakdown["p95_run"]
    lines = ["", "SessionStart phases (ms; in-process, after interpreter start-up):",
             "  %-24s %9s %9s %9s %9s %9s" % ("phase", "p50 run", "p95 run",
                                             "p50", "p95", "max")]
    for name, spread in breakdown["per_phase"].items():
        lines.append("  %-24s %9s %9s %9s %9s %9s" % (
            name, _ms((p50.get("phases") or {}).get(name)),
            _ms((p95.get("phases") or {}).get(name)),
            _ms(spread["p50"]), _ms(spread["p95"]), _ms(spread["max"])))
    lines.append("  %-24s %9s %9s" % ("total in-process", _ms(p50.get("total_ms")),
                                       _ms(p95.get("total_ms"))))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", default=os.path.expanduser("~/mnemo"))
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--days", type=int, default=None,
                        help="only rows from the last N days (default: every row)")
    parser.add_argument("--wall-ms", type=float, default=DEFAULT_WALL_MS)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timed", type=int, default=0, metavar="RUNS",
                        help="time session_start, user_prompt_submit and session_end RUNS times each")
    parser.add_argument("--scratch", default=None,
                        help="where --timed clones the vault (required with --timed)")
    parser.add_argument("--cwd", default=os.getcwd(),
                        help="the project directory a timed session runs in")
    parser.add_argument("--transcript", default=None,
                        help="a transcript a timed session_end is handed")
    args = parser.parse_args(argv)
    if args.timed:
        if not args.scratch:
            parser.error("--timed needs --scratch")
        env = prepare_scratch(os.path.expanduser(args.vault), os.path.expanduser(args.scratch))
        timed = timed_runs(args.timed, env=env, cwd=os.path.expanduser(args.cwd),
                           transcript=args.transcript)
        prov = _provenance.provenance(__file__, argv, vault=os.path.expanduser(args.vault))
        if args.json:
            print(json.dumps(_provenance.stamp(timed, prov), indent=2))
        else:
            print(_provenance.line(prov))
            for hook, spread in timed.items():
                if hook in HOOKS:
                    print("%-20s n %d  p50 %s  p95 %s  p99 %s  max %s (ms)" % (
                        HOOKS[hook], spread["n"], _ms(spread["p50"]), _ms(spread["p95"]),
                        _ms(spread["p99"]), _ms(spread["max"])))
            print(render_phases(timed.get("session_start_phases") or {}))
        return 0
    report = measure(os.path.expanduser(args.vault), os.path.expanduser(args.projects),
                     days=args.days, wall_ms=args.wall_ms)
    projects = os.path.expanduser(args.projects)
    prov = _provenance.provenance(__file__, argv, vault=os.path.expanduser(args.vault),
                                  blind_spots=[_provenance.transcripts_blind_spot(projects)])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=2))
    else:
        print(_provenance.line(prov))
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
