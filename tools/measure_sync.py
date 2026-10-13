"""Is one vault on two machines still one vault? (#644, the proof of #641)

Usage:
    PYTHONPATH=src python3 tools/measure_sync.py [--vault ~/mnemo] [--projects ~/.claude/projects]
        [--cwd DIR | --project NAME] [--days N] [--other-digest sha256:…] [--json]

Read-only on the hub: nothing is fetched, pushed or committed. The one thing
it writes is the rule-activation index under ``.mnemo/``, rebuilt from
``shared/`` exactly as SessionStart rebuilds it before injecting, because the
parity digest is of what SessionStart would inject.

Three reports, each over what is on this machine, and the four bars #641
set, read by the maintainer after 14 days on two machines:

**Parity.** For one repository (``--cwd``, default the current directory, or
``--project`` by name) the text SessionStart would inject — the topic menu
and the briefing block, built by the hook's own builder with the
briefing-log rows turned off and the vault spelled ``<vault>`` in the one
line that names a file, so a trimmed briefing is cut on the same byte
whatever the vault's path — with any machine path left normalised, then
digested. Printed next to the commit the work tree stands on and the hub's
tip as last fetched. The same commit and the same config on two machines
print the same digest; pass the other machine's digest as ``--other-digest``
and the bar is judged here. ``pending`` tracked files mean the work tree is
past the commit, and the digest is of the work tree.

**Push lag.** For every briefing this machine pushed, from the file's last
write to the commit that carried it: p50, p95, max, and how many waited for
a later SessionStart — pushes whose log row says ``trigger: session_start``
(#643 names its hook pushes so; the CLI's say ``cli``). The row is found by
the commit it sent, which every push row names. A briefing no push row
accounts for — the first commit goes out with ``init`` — or whose row has
no trigger is counted as unknown, not as awake. The bar is p95 ≤ 5 min *while
the machine stays awake*, so it is judged over the pushes that did not wait.
A briefing rewritten after its push, or pruned since, is counted and left
out.

**Conflicts.** Resolved automatically: the state's counters and the log's
rows. Left unresolved: unmerged index entries (``git ls-files -u``) and
tracked files holding conflict markers, both counted on disk rather than
assumed to be zero because the engine never writes either.

**SessionStart latency** is read from ``tools/measure_hook_durations.py``
over the transcripts since sync was turned on here (the first row of the
sync log), against #610's goals.

Percentiles are nearest-rank, :func:`mnemo.core.reflex.judge_stats.percentile`.
The report ends with the provenance #595 requires.
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mnemo.core.log_utils import iter_rotated_rows  # noqa: E402
from mnemo.core.reflex.judge_stats import percentile  # noqa: E402
from mnemo.core.sync import health, tracked  # noqa: E402
from mnemo.core.sync import state as sync_state  # noqa: E402

try:
    from tools import _provenance, measure_hook_durations as mhd
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]
    import measure_hook_durations as mhd  # type: ignore[no-redef]

#: The bars (#641).
PUSH_LAG_P95_SECONDS = 300
SESSION_START_P50_MS = 1500
SESSION_START_P95_MS = 5000

#: What the vault's path reads as in the digested text.
VAULT_LABEL = "<vault>"

_BRIEFING = re.compile(r"^bots/[^/]+/briefings/sessions/[^/]+\.md$")
_PREDICTED = re.compile(r"\n\n\[predicted-rules .*?\[/predicted-rules\]", re.DOTALL)
_MARKERS = (b"<<<<<<< ", b"=======", b">>>>>>> ")


# --- parity -----------------------------------------------------------------


def normalise(text: str, vault: Path) -> str:
    """``text`` with this machine's paths replaced: the vault, however it is
    spelled, becomes ``<vault>`` with forward slashes after it, and the home
    directory ``~``."""
    vault = Path(vault)
    spellings = {str(vault), vault.as_posix()}
    try:
        real = vault.resolve()
        spellings.update({str(real), real.as_posix()})
    except OSError:
        pass
    for spelled in sorted(spellings, key=len, reverse=True):
        if spelled and spelled not in ("/", "."):
            text = text.replace(spelled, VAULT_LABEL)
    text = re.sub(re.escape(VAULT_LABEL) + r"[^\s\]]*",
                  lambda m: m.group(0).replace("\\", "/"), text)
    home = os.path.expanduser("~")
    if home and home not in ("~", os.sep):
        text = text.replace(home, "~")
    return text


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def injected_text(vault: Path, project: str) -> Dict[str, Any]:
    """What SessionStart would inject for ``project``: the topic menu and the
    briefing block, with the index rebuilt first as the hook does it, and
    nothing recorded in the briefing log."""
    from mnemo.core import config as cfg_mod
    from mnemo.core import rule_activation
    from mnemo.hooks import session_start

    cfg = cfg_mod.load_config()
    rule_activation.write_index(vault, rule_activation.build_index(vault))
    mode = session_start._briefing_mode(cfg, "startup")
    text = session_start._build_injection_payload(
        vault, current_project=project, inject_briefing=(mode != "none"),
        blocks=(), briefing_mode=mode, record=False, path_label=VAULT_LABEL,
    )
    # Predicted rules come from a cache under .mnemo/ that never travels.
    text = _PREDICTED.sub("", text)
    return {"text": text, "briefingMode": mode}


def parity(vault: Path, project: str, *, other_digest: Optional[str] = None) -> Dict[str, Any]:
    from mnemo.core.sync import engine

    vault = Path(vault)
    built = injected_text(vault, project)
    text = normalise(built["text"], vault)
    out: Dict[str, Any] = {
        "project": project,
        "briefingMode": built["briefingMode"],
        "bytes": len(text.encode("utf-8")),
        "digest": digest(text),
        "commit": None,
        "hub": None,
        "pending": None,
        "otherDigest": other_digest,
    }
    if sync_state.enabled(vault):
        git = engine.Git(vault)
        tips = engine._tips(git)
        out["commit"] = tips.head
        out["hub"] = tips.theirs
        out["pending"] = engine._pending(git, tips.head_tree)
    return out


# --- push lag ---------------------------------------------------------------


def _epoch(stamp: Optional[str]) -> Optional[float]:
    when = health._parse(stamp)
    return when.timestamp() if when is not None else None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def briefing_commits(vault: Path) -> List[Dict[str, Any]]:
    """Every commit on the local branch that added a briefing: its time, the
    machine its trailer names, and the briefing paths it added."""
    from mnemo.core.sync import engine

    git = engine.Git(vault)
    if git.rev(engine.LOCAL_REF) is None:
        return []
    raw = git.out(
        "log", "--format=%x1e%H%x1f%ct%x1f%(trailers:key=Sync-Machine,valueonly,separator=%x20)",
        "--name-only", "--diff-filter=A", "--no-renames", engine.LOCAL_REF,
    )
    out: List[Dict[str, Any]] = []
    for block in raw.split("\x1e"):
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        head = lines[0].split("\x1f")
        if len(head) < 2:
            continue
        paths = [ln.strip() for ln in lines[1:] if _BRIEFING.match(ln.strip())]
        if not paths:
            continue
        machine = head[2].strip().split()[0] if len(head) > 2 and head[2].strip() else None
        out.append({"commit": head[0], "at": int(head[1]), "machine": machine, "paths": paths})
    return out


def push_trigger(log_rows: Sequence[Dict[str, Any]], commit: str) -> Optional[str]:
    """The ``trigger`` of the push row that sent ``commit``; ``None`` when no
    row names it (``init`` sends the first commit and is not a push row) or
    the row carries no trigger."""
    for row in log_rows:
        if row.get("op") == "push" and row.get("outcome") == "ok" and row.get("commit") == commit:
            trigger = row.get("trigger")
            return str(trigger) if trigger else None
    return None


def lag_rows(vault: Path, commits: Iterable[Dict[str, Any]], machine: str,
             log_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """One row per briefing this machine pushed: when it was written (the
    file's last write), when its commit was made (the push follows within
    the same call), the lag, and the push's trigger."""
    vault = Path(vault)
    rows: List[Dict[str, Any]] = []
    pruned = rewritten = other_machine = 0
    for commit in commits:
        if commit.get("machine") != machine:
            other_machine += len(commit["paths"])
            continue
        hub_at = float(commit["at"])
        trigger = push_trigger(log_rows, str(commit.get("commit")))
        for rel in commit["paths"]:
            path = vault.joinpath(*rel.split("/"))
            try:
                written_at = path.stat().st_mtime
            except OSError:
                pruned += 1
                continue
            if written_at > hub_at + 1:
                rewritten += 1
                continue
            rows.append({
                "path": rel, "commit": commit["commit"], "machine": machine,
                "writtenAt": _iso(written_at), "hubAt": _iso(hub_at),
                "lagSeconds": max(0.0, hub_at - written_at), "trigger": trigger,
            })
    return {"rows": rows, "pruned": pruned, "rewritten": rewritten,
            "otherMachine": other_machine}


def push_lag(rows: Sequence[Dict[str, Any]], *, bar_seconds: float = PUSH_LAG_P95_SECONDS,
             pruned: int = 0, rewritten: int = 0, other_machine: int = 0) -> Dict[str, Any]:
    """The push-lag report over ``rows`` (see :func:`lag_rows`)."""
    lags = [float(r["lagSeconds"]) for r in rows]
    waited = [r for r in rows if r.get("trigger") == "session_start"]
    unknown = [r for r in rows if not r.get("trigger")]
    awake = [float(r["lagSeconds"]) for r in rows
             if r.get("trigger") and r.get("trigger") != "session_start"]

    def spread(values: List[float]) -> Dict[str, Any]:
        return {"n": len(values), "p50": percentile(values, 0.5),
                "p95": percentile(values, 0.95), "max": max(values) if values else None}

    judged = awake if awake else ([] if unknown and not awake else lags)
    if not awake and unknown:
        judged = [float(r["lagSeconds"]) for r in rows if r.get("trigger") != "session_start"]
    judged_spread = spread(judged)
    return {
        "barP95Seconds": bar_seconds,
        "all": spread(lags),
        "awake": spread(awake),
        "judged": judged_spread,
        "judgedOver": ("pushes whose trigger is not session_start" if awake
                       else "pushes with no trigger recorded, taken as awake"
                       if unknown else "no pushes"),
        "waitedForSessionStart": len(waited),
        "triggerUnknown": len(unknown),
        "pruned": pruned,
        "rewrittenAfterPush": rewritten,
        "otherMachine": other_machine,
        "withinBar": (judged_spread["p95"] is not None and judged_spread["p95"] <= bar_seconds),
    }


# --- conflicts --------------------------------------------------------------


def unresolved_on_disk(vault: Path) -> Dict[str, Any]:
    """What a conflict left behind would look like, counted rather than
    assumed: unmerged index entries, and tracked files with marker lines."""
    from mnemo.core.sync import engine

    vault = Path(vault)
    unmerged = 0
    if (vault / ".git").exists():
        raw = engine.Git(vault).run("ls-files", "-u", "-z", check=False).stdout
        unmerged = len({p.split(b"\t", 1)[-1] for p in raw.split(b"\0") if p})
    marked: List[str] = []
    for rel in tracked.walk(vault):
        try:
            data = vault.joinpath(*rel.split("/")).read_bytes()
        except OSError:
            continue
        if has_markers(data):
            marked.append(rel)
    return {"unmerged": unmerged, "marked": marked}


def has_markers(data: bytes) -> bool:
    """Whether ``data`` holds all three git conflict marker lines, in order."""
    pos = 0
    for marker in _MARKERS:
        found = -1
        start = 0
        while True:
            idx = data.find(marker, start)
            if idx < 0:
                break
            if idx == 0 or data[idx - 1:idx] == b"\n":
                found = idx
                break
            start = idx + 1
        if found < pos and found != 0 or found < 0:
            return False
        pos = found
    return True


def conflicts(log_rows: Sequence[Dict[str, Any]], state: Dict[str, Any],
              on_disk: Dict[str, Any]) -> Dict[str, Any]:
    from_log = sum(int(r.get("conflicts") or 0) for r in log_rows
                   if r.get("op") in ("pull", "push", "join", "init"))
    unresolved = int(on_disk.get("unmerged") or 0) + len(on_disk.get("marked") or [])
    return {
        "resolved": int(state.get("conflictsResolved") or 0),
        "resolvedInLog": from_log,
        "losersKept": int(state.get("losersKept") or 0),
        "losersDir": tracked.LOSERS_DIR,
        "unresolved": unresolved,
        "unmerged": int(on_disk.get("unmerged") or 0),
        "marked": list(on_disk.get("marked") or []),
    }


# --- latency, from measure_hook_durations ----------------------------------


def session_start_latency(projects: str, *, since: Optional[str]) -> Dict[str, Any]:
    transcripts = (glob.glob(os.path.join(projects, "*", "*.jsonl"))
                   + glob.glob(os.path.join(projects, "*", "*", "subagents", "*.jsonl")))

    def rows() -> Iterable[Dict[str, Any]]:
        for path in sorted(transcripts):
            yield from mhd.transcript_rows(path)

    entry = mhd.hook_durations(rows(), since=since)["session_start"]
    completed = entry["completed"]
    return {
        "since": since,
        "transcripts": len(transcripts),
        "n": completed["n"],
        "p50": completed["p50"],
        "p95": completed["p95"],
        "cancelled": entry["cancelled"],
        "barP50Ms": SESSION_START_P50_MS,
        "barP95Ms": SESSION_START_P95_MS,
    }


# --- the bars ---------------------------------------------------------------


def bars(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    par, lag, con, lat = (report["parity"], report["pushLag"], report["conflicts"],
                          report["sessionStart"])
    out: List[Dict[str, Any]] = []
    if par.get("otherDigest"):
        same = par["otherDigest"] == par["digest"]
        out.append({"bar": "parity identical", "value": f"{par['digest']} vs {par['otherDigest']}",
                    "verdict": "pass" if same else "fail"})
    else:
        out.append({"bar": "parity identical", "value": par["digest"],
                    "verdict": "unknown: run on the other machine at the same commit and "
                               "pass its digest as --other-digest"})
    p95 = lag["judged"]["p95"]
    out.append({
        "bar": f"push lag p95 ≤ {lag['barP95Seconds'] / 60:g} min while awake",
        "value": (f"p95 {p95:.0f} s over {lag['judged']['n']} ({lag['judgedOver']})"
                  if p95 is not None else "no briefing pushed from this machine"),
        "verdict": "unknown" if p95 is None else ("pass" if lag["withinBar"] else "fail"),
    })
    if lat["p50"] is None:
        verdict, value = "unknown", "no completed SessionStart since sync was turned on"
    else:
        ok = lat["p50"] <= lat["barP50Ms"] and lat["p95"] <= lat["barP95Ms"]
        verdict = "pass" if ok else "fail"
        value = f"p50 {lat['p50']:.0f} ms, p95 {lat['p95']:.0f} ms over {lat['n']}"
    out.append({"bar": f"SessionStart with sync on p50 ≤ {lat['barP50Ms'] / 1000:g} s and "
                       f"p95 ≤ {lat['barP95Ms'] / 1000:g} s", "value": value, "verdict": verdict})
    out.append({"bar": "0 conflicts left unresolved",
                "value": f"{con['unresolved']} unresolved ({con['resolved']} resolved for the primary)",
                "verdict": "pass" if con["unresolved"] == 0 else "fail"})
    return out


# --- the run ----------------------------------------------------------------


def _log_rows(vault: Path) -> List[Dict[str, Any]]:
    return list(iter_rotated_rows(Path(vault) / ".mnemo" / "sync-log.jsonl"))


def _since(log_rows: Sequence[Dict[str, Any]], days: Optional[int]) -> Optional[str]:
    first = min((str(r.get("at")) for r in log_rows if r.get("at")), default=None)
    cutoff = mhd._cutoff(days)
    if first and cutoff:
        return max(first, cutoff)
    return first or cutoff


def measure(vault: str, projects: str, *, project: str, machine: Optional[str] = None,
            days: Optional[int] = None, other_digest: Optional[str] = None) -> Dict[str, Any]:
    from mnemo.core import config as cfg_mod

    root = Path(vault).expanduser()
    machine = machine or sync_state.machine_id(cfg_mod.load_config())
    log_rows = _log_rows(root)
    state = sync_state.load(root)
    enabled = sync_state.enabled(root)
    lagged = (lag_rows(root, briefing_commits(root), machine, log_rows) if enabled
              else {"rows": [], "pruned": 0, "rewritten": 0, "otherMachine": 0})
    report = {
        "vault": str(root),
        "machine": machine,
        "syncEnabled": enabled,
        "days": days,
        "parity": parity(root, project, other_digest=other_digest),
        "pushLag": push_lag(lagged["rows"], pruned=lagged["pruned"],
                            rewritten=lagged["rewritten"], other_machine=lagged["otherMachine"]),
        "pushLagRows": lagged["rows"],
        "conflicts": conflicts(log_rows, state, unresolved_on_disk(root) if enabled
                               else {"unmerged": 0, "marked": []}),
        "sessionStart": session_start_latency(os.path.expanduser(projects),
                                              since=_since(log_rows, days)),
        "logRows": len(log_rows),
    }
    report["bars"] = bars(report)
    return report


def _s(value: Any) -> str:
    return "—" if value is None else f"{value:.0f} s"


def render(report: Dict[str, Any]) -> str:
    par, lag, con, lat = (report["parity"], report["pushLag"], report["conflicts"],
                          report["sessionStart"])
    lines = [f"sync on this machine ('{report['machine']}'): {'on' if report['syncEnabled'] else 'off'}",
             "",
             f"parity — project {par['project']}, briefing mode {par['briefingMode']}",
             f"  commit {par['commit'] or '—'}  hub {par['hub'] or '—'}  pending files {par['pending'] if par['pending'] is not None else '—'}",
             f"  digest {par['digest']} over {par['bytes']} bytes",
             "",
             f"push lag — {lag['all']['n']} briefings pushed from this machine",
             f"  all:   p50 {_s(lag['all']['p50'])}, p95 {_s(lag['all']['p95'])}, max {_s(lag['all']['max'])}",
             f"  awake: p50 {_s(lag['awake']['p50'])}, p95 {_s(lag['awake']['p95'])} over {lag['awake']['n']}",
             f"  waited for a later SessionStart: {lag['waitedForSessionStart']}; trigger unknown: {lag['triggerUnknown']}",
             f"  left out: {lag['rewrittenAfterPush']} rewritten after their push, {lag['pruned']} pruned since; "
             f"{lag['otherMachine']} briefings came from another machine",
             "",
             f"conflicts — {con['resolved']} resolved for the primary ({con['resolvedInLog']} in the log), "
             f"{con['losersKept']} losing versions kept under {con['losersDir']}/",
             f"  unresolved: {con['unresolved']} ({con['unmerged']} unmerged index entries, "
             f"{len(con['marked'])} files with markers)"]
    lines += [f"    {rel}" for rel in con["marked"]]
    lines += ["",
              f"SessionStart since {lat['since'] or 'the beginning'} — {lat['n']} completed over "
              f"{lat['transcripts']} transcripts, {lat['cancelled']} cancelled",
              f"  p50 {lat['p50'] if lat['p50'] is None else int(lat['p50'])} ms, "
              f"p95 {lat['p95'] if lat['p95'] is None else int(lat['p95'])} ms",
              "",
              "bars"]
    for bar in report["bars"]:
        lines.append(f"  {bar['verdict'].upper():8s} {bar['bar']}: {bar['value']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--vault", default="~/mnemo")
    parser.add_argument("--projects", default="~/.claude/projects")
    parser.add_argument("--cwd", default=None, help="the repository SessionStart would inject for (default: here)")
    parser.add_argument("--project", default=None, help="its canonical name, instead of --cwd")
    parser.add_argument("--days", type=int, default=None)
    parser.add_argument("--other-digest", default=None, help="the parity digest printed on the other machine")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    project = args.project
    if not project:
        from mnemo.core.agent import resolve_canonical_agent

        project = resolve_canonical_agent(args.cwd or os.getcwd()).name
    report = measure(args.vault, args.projects, project=project, days=args.days,
                     other_digest=args.other_digest)
    prov = _provenance.provenance(__file__, argv, vault=args.vault, blind_spots=[
        _provenance.transcripts_blind_spot(args.projects),
        "a briefing's write time is its file's mtime on this machine; a briefing another "
        "machine wrote is not measured here",
        "a machine asleep is not observable: pushes whose trigger is session_start are "
        "taken as the ones that waited",
    ])
    if args.json:
        print(json.dumps(_provenance.stamp(report, prov), indent=1))
    else:
        print(render(report))
        print(_provenance.line(prov))
    return 0


if __name__ == "__main__":
    sys.exit(main())
