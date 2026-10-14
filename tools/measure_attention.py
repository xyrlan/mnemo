"""How long dispatched children wait on their human, before and after the Nexus (#662).

Usage:
    PYTHONPATH=src python3 tools/measure_attention.py [--after 2026-10-20T14:00]
        [--jobs DIR ...] [--usage ~/.mnemo-desktop/usage.jsonl]
        [--desktop-repo ~/github/mnemo-desktop] [--boot 2000] [--seed 0]
        [--list] [--json]

Report-only. It reads files, makes no LLM call, writes nothing, sends nothing
anywhere, and holds nothing to a bar: the Nexus (mnemo-desktop#396) ships
whatever it says (decision Q6 of that spec).

**Episodes.** Claude Code writes every change of a background job to
``<config>/jobs/<short>/timeline.jsonl`` as ``{at, state, detail, text}``,
and this reads every config dir ``accounts.roots()`` knows. An episode opens
on a ``blocked`` row and closes on the first row that leaves it:

- a row in another state (``working``, ``running``, ``stopped``, ``done``);
- a ``blocked`` row with an empty ``text``. That is a prompt delivered while
  the job sat blocked — the human's answer (``continue``, ``sim, pode criar
  …``) — written before the state moves. 12 of the 74 ``blocked`` rows on the
  maintainer's machine are this, and the turn they start can take minutes to
  write its first row, so closing on the state change would charge the
  child's own work to the human;
- a ``blocked`` row of another kind (below): the old wait was answered and
  the child stopped again on something else.

A ``blocked`` row of the kind already open is the same wait re-reported
(eighteen rate-limit retries in three minutes on one job) and changes nothing.
An episode no row closes is **open**: the job's record ends inside it. Open
episodes are counted on their own and never enter a median.

**Kinds, and why rate limits are not episodes.** A row that is an API error
has a twin in the job's transcript (``state.json``'s ``linkScanPath``): the
synthetic ``isApiErrorMessage`` record ``core/sessions/stalls.py`` already
reads. On this machine all 52 such rows whose transcript survives found their
record within 0.2 s of ``at``; :data:`MATCH_SECONDS` allows 5. Each row is classified by
``stalls.from_record`` and nothing else:

- ``rate_limit`` — :data:`stalls.Kind.RATE_LIMIT`, a window with a reset
  epoch. A clock frees it, ``rewake`` wakes it, and the Nexus draws it
  asleep, not ``needs-you``. **Not an episode**; counted beside them.
- ``api_transient`` / ``api_person`` — the other two kinds (connection lost,
  overloaded; a spent Fable credit, a login). Nothing wakes these but a
  person, and the Nexus draws them ``needs-you``, so they are episodes.
- ``ask`` — a ``blocked`` row with no API error at its time: the child asked
  (a question, a permission, a verdict, a dialog). The timeline does not say
  which of those it was; this does not guess.
- ``unread`` — no transcript to look in, so a rate limit cannot be told
  apart. Counted on its own, never in the headline.

**Before and after: when the Nexus was installed.** ``pnpm run install-app``
(mnemo-desktop ``scripts/install-app.mjs``) writes
``/Applications/mnemo.app/Contents/Resources/mnemo-build.json`` with
``{sha, version, installed_at}``. That is the only record of an install, and
it holds the **latest** one: a reinstall overwrites it. So "after" starts at
the earliest of these, each a moment a Nexus build was provably installed
(each is at or after the true install, and the earliest is the tightest):

1. ``--after``, when given — the exact answer, and the only one;
2. the stamp's ``installed_at``, when its ``sha`` contains the Nexus. That is
   checked in a mnemo-desktop checkout (``--desktop-repo``): the Nexus commit
   is the first one on ``main`` that adds the string ``nexus.visible``, and
   the stamp counts when ``git merge-base --is-ancestor`` says it holds it.
   No checkout, or no such commit yet, and the stamp is shown, not used;
3. the first ``nexus.open`` / ``nexus.visible`` event in the usage log.

None of them, and every episode is "before".

**Attribution.** The desktop logs ``{"event": "nexus.open", "ts"}`` and
``{"event": "nexus.visible", "ms", "ts"}`` to ``~/.mnemo-desktop/usage.jsonl``.
A day (local calendar day, the day the episode *opened*) is a Nexus day when
it holds a ``nexus.open`` or a ``nexus.visible`` with ``ms > 0``. "After" is
split into episodes on Nexus days and on the rest: a drop on days nobody
opened it is not the Nexus's.

**Statistics.** Minutes, median and p90 (linear between closest ranks), each
with a percentile-bootstrap 95% CI (``--boot`` resamples, ``--seed``); the
difference after − before resamples each side on its own. A side with fewer
than :data:`THIN` episodes is flagged thin in the text.

First reading, 2026-10-14, before any Nexus build existed, so all of it is
"before": 817 jobs across 2 config dirs, 38 with a ``blocked`` row, episodes
opened 2026-09-15 to 2026-10-07. **13 ended episodes, median 7 min [3 min,
44 min], p90 57 min [24 min, 4.3 h]** — by kind 5 ``ask``, 7 ``api_transient``,
1 ``api_person``; 8 closed by a reply. **12 more are open**, 6 of them a spent
Fable credit nobody answered. Set apart: 15 rate-limit stalls (8 ended, 7
open) and 1 ``unread``. The probe the issue quotes (17 ended, median 44 min,
p90 4.3 h; 22 open) did not set the rate limits apart. Thirteen is a thin baseline: the
CIs above span an order of magnitude.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from datetime import date, datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mnemo.core.sessions import stalls  # noqa: E402

try:
    from tools import _provenance
except ImportError:  # run as a script: tools/ is sys.path[0]
    import _provenance  # type: ignore[no-redef]

#: How far a transcript's API-error record may sit from a timeline row's
#: ``at`` and still be that row's error. Measured offsets: 0.0 to 0.2 s.
MATCH_SECONDS = 5.0

RATE_LIMIT, API_TRANSIENT, API_PERSON, ASK, UNREAD = (
    "rate_limit", "api_transient", "api_person", "ask", "unread")
#: The kinds that are "needs you" episodes. ``rate_limit`` waits on a clock,
#: ``unread`` cannot be told from one.
EPISODE_KINDS = (ASK, API_TRANSIENT, API_PERSON)
ALL_KINDS = EPISODE_KINDS + (RATE_LIMIT, UNREAD)

_BY_STALL = {stalls.Kind.RATE_LIMIT: RATE_LIMIT,
             stalls.Kind.TRANSIENT: API_TRANSIENT,
             stalls.Kind.HUMAN: API_PERSON}

#: Below this many ended episodes a side is called thin in the text.
THIN = 10

STAMP = Path("/Applications/mnemo.app/Contents/Resources/mnemo-build.json")
USAGE = Path(os.path.expanduser("~/.mnemo-desktop/usage.jsonl"))
DESKTOP_REPO = Path(os.path.expanduser("~/github/mnemo-desktop"))
#: The string whose first appearance on the desktop's ``main`` marks the Nexus.
NEXUS_MARK = "nexus.visible"

Runner = Callable[[Sequence[str]], Optional[str]]


# --- reading ---------------------------------------------------------------

def epoch(value: Any) -> Optional[float]:
    """An ISO string or a unix number as epoch seconds; ``None`` otherwise."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.astimezone()  # a bare time is the reader's local time
    return moment.timestamp()


def _jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        return []
    return out


def read_timeline(path: Path) -> List[Dict[str, Any]]:
    """The job's rows with a readable ``at``, oldest first, as ``{at, state, text}``."""
    rows = []
    for i, r in enumerate(_jsonl(path)):
        at = epoch(r.get("at"))
        if at is None:
            continue
        text = r.get("text")
        rows.append({"at": at, "i": i, "state": r.get("state"),
                     "text": text if isinstance(text, str) else ""})
    rows.sort(key=lambda r: (r["at"], r["i"]))
    return rows


def api_errors(transcript: Optional[str]) -> Optional[List[Tuple[float, stalls.Stall]]]:
    """``(epoch, Stall)`` per API-error record, or ``None`` when there is no transcript."""
    if not transcript or not os.path.isfile(transcript):
        return None
    out = []
    for record in _jsonl(Path(transcript)):
        found = stalls.from_record(record)
        at = epoch(record.get("timestamp"))
        if found is not None and at is not None:
            out.append((at, found))
    return out


def kind_at(at: float, errors: Optional[List[Tuple[float, stalls.Stall]]]) -> str:
    """The kind of a ``blocked`` row written at *at*: its API error's, or ``ask``."""
    if errors is None:
        return UNREAD
    near = [(abs(t - at), s) for t, s in errors if abs(t - at) <= MATCH_SECONDS]
    if not near:
        return ASK
    return _BY_STALL.get(min(near, key=lambda p: p[0])[1].kind, API_PERSON)


# --- episodes --------------------------------------------------------------

def episodes(rows: Sequence[Dict[str, Any]], kind_of: Callable[[float], str],
             *, job: str = "") -> List[Dict[str, Any]]:
    """The job's ``blocked`` stretches, per the module docstring.

    *kind_of* maps a ``blocked`` row's ``at`` to its kind; it is only asked
    about rows that can open an episode.
    """
    out: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None

    def close(at: float, by: str) -> None:
        current["end"] = at
        current["minutes"] = (at - current["start"]) / 60.0
        current["ended_by"] = by

    for row in rows:
        if row["state"] != "blocked":
            if current is not None:
                close(row["at"], str(row["state"]))
                current = None
            continue
        if not row["text"]:
            if current is not None:
                close(row["at"], "reply")
                current = None
            continue
        kind = kind_of(row["at"])
        if current is not None and current["kind"] == kind:
            continue
        if current is not None:
            close(row["at"], "blocked")
        current = {"job": job, "kind": kind, "start": row["at"], "end": None,
                   "minutes": None, "ended_by": None}
        out.append(current)
    return out


def read_jobs(jobs_dirs: Iterable[Path]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Every episode under *jobs_dirs*, plus what was read."""
    found: List[Dict[str, Any]] = []
    seen = {"dirs": 0, "jobs": 0, "jobs_blocked": 0}
    for jobs_dir in jobs_dirs:
        try:
            names = sorted(os.listdir(jobs_dir))
        except OSError:
            continue
        seen["dirs"] += 1
        for name in names:
            timeline = Path(jobs_dir) / name / "timeline.jsonl"
            if not timeline.is_file():
                continue
            seen["jobs"] += 1
            rows = read_timeline(timeline)
            if not any(r["state"] == "blocked" for r in rows):
                continue
            seen["jobs_blocked"] += 1
            try:
                state = json.loads((Path(jobs_dir) / name / "state.json")
                                   .read_text(encoding="utf-8", errors="replace"))
            except (OSError, ValueError):
                state = {}
            path = state.get("linkScanPath") if isinstance(state, dict) else None
            errors = api_errors(path if isinstance(path, str) else None)
            found.extend(episodes(rows, lambda at: kind_at(at, errors), job=name))
    return found, seen


# --- the install, and the days the Nexus was used --------------------------

def _git(repo: Path) -> Runner:
    def run(args: Sequence[str]) -> Optional[str]:
        try:
            done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                                  encoding="utf-8", errors="replace", timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None
    return run


def nexus_commit(git: Runner) -> Optional[str]:
    """The first commit on the desktop's ``main`` that adds :data:`NEXUS_MARK`."""
    for ref in ("main", "origin/main"):
        if git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"]) is None:
            continue
        out = git(["log", "--reverse", "--format=%H", "-S", NEXUS_MARK, ref])
        return out.split()[0] if out else None
    return None


def read_stamp(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def stamp_has_nexus(stamp: Optional[Dict[str, Any]], git: Optional[Runner]) -> Optional[bool]:
    """Whether the stamped build holds the Nexus; ``None`` when nothing can say."""
    sha = stamp.get("sha") if stamp else None
    if not isinstance(sha, str) or not sha or git is None:
        return None
    nexus = nexus_commit(git)
    if nexus is None:
        return None
    return git(["merge-base", "--is-ancestor", nexus, sha]) is not None


def nexus_use(usage: Sequence[Dict[str, Any]], tz: Optional[tzinfo]) -> Tuple[Optional[float], Set[date]]:
    """The first Nexus event, and the local days it was opened or on screen."""
    first: Optional[float] = None
    days: Set[date] = set()
    for r in usage:
        event, ts = r.get("event"), epoch(r.get("ts"))
        if event not in ("nexus.open", "nexus.visible") or ts is None:
            continue
        first = ts if first is None else min(first, ts)
        ms = r.get("ms")
        if event == "nexus.open" or (isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0):
            days.add(_day(ts, tz))
    return first, days


def after_start(*, flag: Optional[float], stamp: Optional[Dict[str, Any]],
                stamp_nexus: Optional[bool], first_event: Optional[float]) -> Dict[str, Any]:
    """When "after" starts, and each piece of evidence that was weighed."""
    stamped = epoch(stamp.get("installed_at")) if stamp else None
    evidence = {
        "flag": flag,
        "stamp_installed_at": stamped,
        "stamp_sha": stamp.get("sha") if stamp else None,
        "stamp_has_nexus": stamp_nexus,
        "first_nexus_event": first_event,
    }
    if flag is not None:
        return {"at": flag, "source": "flag", "evidence": evidence}
    candidates = []
    if stamped is not None and stamp_nexus:
        candidates.append((stamped, "stamp"))
    if first_event is not None:
        candidates.append((first_event, "first_nexus_event"))
    if not candidates:
        return {"at": None, "source": None, "evidence": evidence}
    at, source = min(candidates)
    return {"at": at, "source": source, "evidence": evidence}


def _day(ts: float, tz: Optional[tzinfo]) -> date:
    return datetime.fromtimestamp(ts, tz=tz or _local()).date()


def _local() -> tzinfo:
    return datetime.now().astimezone().tzinfo or timezone.utc


# --- statistics ------------------------------------------------------------

def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """The *q* (0-1) percentile of *values*, linear between closest ranks."""
    if not values:
        return None
    ordered = sorted(values)
    spot = (len(ordered) - 1) * q
    low = int(spot)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (spot - low)


def _resample(values: Sequence[float], rng: random.Random) -> List[float]:
    n = len(values)
    return [values[rng.randrange(n)] for _ in range(n)]


def _ci(boots: List[float]) -> List[Optional[float]]:
    return [percentile(boots, 0.025), percentile(boots, 0.975)] if boots else [None, None]


def describe(values: Sequence[float], *, boot: int, seed: int) -> Dict[str, Any]:
    """n, median and p90 of *values* (minutes), each with a bootstrap 95% CI."""
    out: Dict[str, Any] = {"n": len(values)}
    rng = random.Random(seed)
    samples = [_resample(values, rng) for _ in range(boot)] if values else []
    for name, q in (("median", 0.5), ("p90", 0.9)):
        out[name] = percentile(values, q)
        out[name + "_ci"] = _ci([percentile(s, q) for s in samples])
    return out


def difference(before: Sequence[float], after: Sequence[float], *,
               boot: int, seed: int) -> Optional[Dict[str, Any]]:
    """after − before in median and p90, each side resampled on its own."""
    if not before or not after:
        return None
    rng = random.Random(seed)
    out: Dict[str, Any] = {}
    pairs = [(_resample(before, rng), _resample(after, rng)) for _ in range(boot)]
    for name, q in (("median", 0.5), ("p90", 0.9)):
        out[name] = percentile(after, q) - percentile(before, q)
        out[name + "_ci"] = _ci([percentile(a, q) - percentile(b, q) for b, a in pairs])
    return out


# --- the report ------------------------------------------------------------

def _side(eps: Sequence[Dict[str, Any]], *, boot: int, seed: int) -> Dict[str, Any]:
    ended = [e for e in eps if e["kind"] in EPISODE_KINDS and e["end"] is not None]
    return {
        "episodes": describe([e["minutes"] for e in ended], boot=boot, seed=seed),
        "open": sum(1 for e in eps if e["kind"] in EPISODE_KINDS and e["end"] is None),
        "by_kind": {k: {"ended": sum(1 for e in eps if e["kind"] == k and e["end"] is not None),
                        "open": sum(1 for e in eps if e["kind"] == k and e["end"] is None)}
                    for k in ALL_KINDS},
        "ended_by": _count(e["ended_by"] for e in ended),
    }


def _count(values: Iterable[Any]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return dict(sorted(out.items()))


def _minutes(eps: Sequence[Dict[str, Any]]) -> List[float]:
    return [e["minutes"] for e in eps if e["kind"] in EPISODE_KINDS and e["end"] is not None]


def summarize(eps: Sequence[Dict[str, Any]], *, start: Dict[str, Any],
              nexus_days: Set[date], seen: Dict[str, int], boot: int = 2000,
              seed: int = 0, tz: Optional[tzinfo] = None) -> Dict[str, Any]:
    at = start["at"]
    before = [e for e in eps if at is None or e["start"] < at]
    after = [e for e in eps if at is not None and e["start"] >= at]
    used = [e for e in after if _day(e["start"], tz) in nexus_days]
    unused = [e for e in after if _day(e["start"], tz) not in nexus_days]
    starts = [e["start"] for e in eps]
    return {
        "read": dict(seen),
        "span": [min(starts), max(starts)] if starts else None,
        "after_start": start,
        "nexus_days": sorted(d.isoformat() for d in nexus_days),
        "before": _side(before, boot=boot, seed=seed),
        "after": _side(after, boot=boot, seed=seed),
        "after_nexus_days": _side(used, boot=boot, seed=seed),
        "after_other_days": _side(unused, boot=boot, seed=seed),
        "difference": difference(_minutes(before), _minutes(after), boot=boot, seed=seed),
        "difference_nexus_days": difference(_minutes(before), _minutes(used), boot=boot, seed=seed),
        "episodes": [dict(e) for e in eps],
    }


def _iso(ts: Optional[float]) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _dur(minutes: Optional[float]) -> str:
    if minutes is None:
        return "—"
    sign = "-" if minutes < 0 else ""
    m = abs(minutes)
    return f"{sign}{m:.0f} min" if m < 90 else f"{sign}{m / 60:.1f} h"


def _stat(d: Dict[str, Any], name: str) -> str:
    lo, hi = d[name + "_ci"]
    return f"{_dur(d[name])} [{_dur(lo)}, {_dur(hi)}]"


def _side_lines(label: str, s: Dict[str, Any]) -> List[str]:
    e = s["episodes"]
    k = s["by_kind"]
    head = f"{label}: {e['n']} ended episodes"
    if e["n"]:
        head += f" — median {_stat(e, 'median')}, p90 {_stat(e, 'p90')}"
    if e["n"] < THIN:
        head += f"  (thin: under {THIN})"
    lines = [head,
             "  by kind (ended/open): " + ", ".join(
                 f"{kind} {k[kind]['ended']}/{k[kind]['open']}" for kind in EPISODE_KINDS),
             f"  still open when the job's record ends: {s['open']}",
             f"  set apart — rate-limit stalls {k[RATE_LIMIT]['ended']} ended, "
             f"{k[RATE_LIMIT]['open']} open; no transcript to tell {k[UNREAD]['ended']} "
             f"ended, {k[UNREAD]['open']} open"]
    if s["ended_by"]:
        lines.append("  closed by: " + ", ".join(f"{k_} {v}" for k_, v in s["ended_by"].items()))
    return lines


def _diff_line(label: str, d: Optional[Dict[str, Any]]) -> str:
    if d is None:
        return f"{label}: no difference — a side has no ended episode"
    return f"{label}: median {_stat(d, 'median')}, p90 {_stat(d, 'p90')}"


def format_report(report: Dict[str, Any], *, listing: bool = False) -> str:
    r = report["read"]
    span = report["span"]
    start = report["after_start"]
    ev = start["evidence"]
    lines = [
        f"sample: {r['jobs']} jobs in {r['dirs']} config dir(s), {r['jobs_blocked']} with a "
        f"blocked row; episodes opened {_iso(span[0]) if span else '—'} to "
        f"{_iso(span[1]) if span else '—'}",
        "episode = a dispatched child blocked on its human; rate-limit stalls are set apart "
        "(core/sessions/stalls.py on the transcript); minutes, bootstrap 95% CI in brackets",
    ]
    if start["at"] is None:
        lines.append("after: no Nexus install found (no --after, no stamp holding the Nexus, "
                     "no nexus.* event) — everything below is before")
    else:
        lines.append(f"after starts {_iso(start['at'])}, from {start['source']}")
    lines.append(f"  install stamp: {_iso(ev['stamp_installed_at'])}, sha {ev['stamp_sha'] or '—'}, "
                 f"holds the Nexus: {ev['stamp_has_nexus']}; first nexus.* event "
                 f"{_iso(ev['first_nexus_event'])}; Nexus days {len(report['nexus_days'])}")
    lines.append("")
    lines += _side_lines("before", report["before"])
    if start["at"] is not None:
        lines += _side_lines("after", report["after"])
        lines += _side_lines("after, days the Nexus was open", report["after_nexus_days"])
        lines += _side_lines("after, days it was not", report["after_other_days"])
        lines.append(_diff_line("after - before", report["difference"]))
        lines.append(_diff_line("after on Nexus days - before", report["difference_nexus_days"]))
    if listing:
        lines.append("")
        for e in report["episodes"]:
            lines.append(f"  {e['job']}  {_iso(e['start'])}  {e['kind']:<13}  "
                         f"{_dur(e['minutes']) if e['end'] is not None else 'open':>8}  "
                         f"{e['ended_by'] or ''}")
    return "\n".join(lines) + "\n"


def _jobs_dirs(given: Optional[Sequence[str]]) -> List[Path]:
    if given:
        return [Path(d).expanduser() for d in given]
    from mnemo.core.sessions import accounts

    return [root.jobs for root in accounts.roots()]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--after", help="when a Nexus build was installed (ISO time or unix "
                                        "seconds); overrides the stamp and the usage log")
    parser.add_argument("--jobs", action="append", metavar="DIR",
                        help="a Claude Code jobs dir; repeatable (default: every config dir "
                             "mnemo knows, via accounts.roots())")
    parser.add_argument("--usage", default=str(USAGE), help="the desktop's usage log")
    parser.add_argument("--stamp", default=str(STAMP), help="install-app's build stamp")
    parser.add_argument("--desktop-repo", default=str(DESKTOP_REPO),
                        help="a mnemo-desktop checkout, to tell whether the stamp holds the Nexus")
    parser.add_argument("--boot", type=int, default=2000, help="bootstrap resamples")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--list", action="store_true", help="one line per episode")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    flag = None
    if args.after:
        flag = epoch(args.after)
        if flag is None:
            sys.stderr.write(f"--after: cannot read {args.after!r} as a time\n")
            return 2
    eps, seen = read_jobs(_jobs_dirs(args.jobs))
    repo = Path(args.desktop_repo).expanduser()
    git = _git(repo) if (repo / ".git").exists() else None
    stamp = read_stamp(Path(args.stamp).expanduser())
    first, days = nexus_use(_jsonl(Path(args.usage).expanduser()), None)
    start = after_start(flag=flag, stamp=stamp, stamp_nexus=stamp_has_nexus(stamp, git),
                        first_event=first)
    report = summarize(eps, start=start, nexus_days=days, seen=seen,
                       boot=args.boot, seed=args.seed)
    prov = _provenance.provenance(__file__, argv, blind_spots=[
        "the install stamp holds only the latest install: without --after, 'after' starts "
        "at the earliest proof a Nexus build was installed, which can be later than the install",
        "an 'ask' episode is any blocked row with no API error at its time; the timeline does "
        "not say whether it was a question, a permission or a dialog",
        "a job whose transcript is gone cannot be told from a rate limit and is set apart "
        "as 'unread'",
        "Claude Code prunes old jobs: the before side is only what is still on disk",
    ])
    if args.json:
        sys.stdout.write(json.dumps(_provenance.stamp(report, prov), indent=2, default=str) + "\n")
    else:
        sys.stdout.write(format_report(report, listing=args.list) + _provenance.line(prov) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
