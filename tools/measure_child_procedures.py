"""How often a dispatched child rediscovers — or never finds — how work is run here (#385).

Usage:
    PYTHONPATH=src python3 tools/measure_child_procedures.py [--projects ~/.claude/projects] [--list] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

#385 asks whether dispatch should hand every child a named procedure for its
repo — Devin's "playbook". Before designing one, count what children actually
get wrong. The population is every transcript whose ``cwd`` parses as a
dispatch worktree (``dispatch.issue_for_cwd``), pytest's own ``live-wt-N``
trees excluded, so a child whose job ``claude rm`` already pruned still counts.

Three things are counted per child:

- **Channels.** Which standing-instruction files Claude Code attached, by its
  own ``attachment.type == "instructions"`` records: the repo's ``CLAUDE.md``
  (``Project``) and Claude Code's own per-project auto-memory index
  (``AutoMem``). This is what the child was *told* before it did anything.
- **Probes.** A probe is a command shape a child runs (``trigger``) paired
  with what that shape has to carry *in a dispatch worktree of that repo*
  (``needs``). Each probe is a **boundary** fact — how work is run here —
  never an approach. Heredoc bodies are stripped first, so a test file being
  written that quotes ``pytest`` is not counted as running it.
- **Setup failures.** Errors a child hit that a stated procedure would have
  spared it, read out of its own tool results.

Per probe a child lands in one of three states, and the middle one is the
point of the issue:

- **kept** — every run carried it. The child knew, or worked it out first try.
- **rediscovered** — ran without it, then with it. The knowledge was
  reachable, and the child paid to reach it.
- **missed** — never ran it correctly. Whatever it reported, it measured
  something else. This is the expensive state: it is silent.

What this is not:

- **Not causal.** Repos differ in more than their instruction channels, and
  the probes are per repo. The comparison this supports is within a repo over
  time, and across repos only as a gradient worth explaining.
- **Not a lint.** A probe's ``needs`` is what the *repo* requires, not what is
  universally correct; a run without it is sometimes deliberate (the #329
  child ran a bare ``pytest`` on purpose, to show the failure). ``--list``
  prints every missing run so a count can be read against what was run.

The probes below are this machine's repos because that is where the
transcripts are. Nothing here is imported by ``mnemo`` itself — in particular
``core/dispatch.py`` holds no repo's procedure, which is the constraint #385
sets and the measurement's own conclusion (see ``docs/getting-started.md``,
"What a child already knows about your repo").
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Pattern, Tuple

from mnemo.core.activity.exploration import _split_heredocs
from mnemo.core.dispatch import issue_for_cwd

#: ``(repo, name, trigger, needs, why)``. ``trigger`` says "the child is doing
#: this kind of work"; ``needs`` says "here, that has to look like this".
PROBES: Tuple[Tuple[str, str, Pattern, Pattern, str], ...] = (
    (
        "mnemo", "run the suite",
        re.compile(r"(?:^|[;&|(\n]|&&|\|\|)\s*(?:[A-Za-z_]\w*=\S*\s+)*"
                   r"(?:(?:python3?|py)\s+-m\s+pytest|pytest)\b"),
        re.compile(r"PYTHONPATH"),
        "the editable install points at the main checkout, so a bare `pytest` "
        "in a worktree imports master's `src` and a green suite proves nothing",
    ),
    (
        "mnemo-desktop", "run cargo",
        re.compile(r"\bcargo (?:test|build)\b"),
        re.compile(r"CARGO_TARGET_DIR"),
        "every worktree building into its own `target/` races the others and "
        "costs `Text file busy`",
    ),
    (
        "mnemo-desktop", "run cargo under the command-line tools",
        re.compile(r"\bcargo (?:test|build)\b"),
        re.compile(r"DEVELOPER_DIR|SDKROOT"),
        "without a full Xcode, `cargo` needs `DEVELOPER_DIR`/`SDKROOT` "
        "pointed at the command-line tools",
    ),
    (
        "clubinho", "run the suite",
        re.compile(r"\bnpm (?:run )?test\b"),
        re.compile(r"--runInBand"),
        "jest in parallel freezes the machine — stated in the repo's CLAUDE.md",
    ),
    (
        "clubinho", "run the suite without running out of heap",
        re.compile(r"\bnpm (?:run )?test\b"),
        re.compile(r"max-old-space-size"),
        "the full suite exceeds node's default heap — not stated anywhere",
    ),
)

#: Errors in a child's own tool results that a stated procedure would spare it.
SETUP_FAILURES: Tuple[Tuple[str, Pattern], ...] = (
    ("worktree has no node_modules", re.compile(
        r"Cannot find module|ERR_MODULE_NOT_FOUND|command not found: (?:vitest|jest|tsc)")),
    ("target dir raced", re.compile(r"Text file busy|ETXTBSY")),
    ("no full Xcode", re.compile(r"xcrun: error|agreeing to the Xcode|Xcode[^\n]{0,40}license")),
    ("node heap exhausted", re.compile(
        r"JavaScript heap out of memory|Ineffective mark-compacts")),
)

#: How far into a tool result to look. The failures above announce themselves;
#: scanning a 200k-line build log for them costs more than it finds.
_RESULT_SCAN = 20_000

#: What separates one command from the next. A probe is looked for inside the
#: statement that runs it and no further: ``pytest x; PYTHONPATH=src pytest y``
#: is one bare run and one correct one, not two correct ones. A pipe is not a
#: separator — ``npm test -- --runInBand | tail`` is one statement, and the
#: flag that matters is on the left of it.
_STATEMENT = re.compile(r"(?:&&|\|\||;|\n)")


def _covers(body: str, match: "re.Match[str]", needs: Pattern) -> bool:
    """Did this invocation carry what the repo requires?

    True when *needs* appears in the invocation's own statement — an env
    assignment in front of it, a flag after it — or when an earlier ``export``
    in the same command set it for everything that follows.
    """
    starts = [m.end() for m in _STATEMENT.finditer(body, 0, match.start())]
    start = starts[-1] if starts else 0
    after = _STATEMENT.search(body, match.end())
    statement = body[start:after.start() if after else len(body)]
    if needs.search(statement):
        return True
    return any(
        needs.search(line) for line in
        re.findall(r"\bexport\b[^\n;&|]*", body[:match.start()])
    )


def _records(path: str) -> Iterable[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    yield record
    except OSError:
        return


def _blocks(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    content = (record.get("message") or {}).get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _cwd_of(path: str) -> Optional[str]:
    """The ``cwd`` of the first event that records one."""
    for index, record in enumerate(_records(path)):
        if index > 200:
            return None
        if isinstance(record.get("cwd"), str):
            return record["cwd"]
    return None


def repo_of(cwd: str) -> str:
    """The repo a worktree was cut from: its own name with the suffix removed."""
    from mnemo.core.dispatch import _WT_RE

    return _WT_RE.sub("", os.path.basename(cwd.rstrip("/")))


def _transcripts(projects: str) -> List[Tuple[str, str]]:
    out = []
    for directory in sorted(glob.glob(os.path.join(projects, "*"))):
        for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
            cwd = _cwd_of(path)
            if cwd and issue_for_cwd(cwd) is not None and "/pytest-of-" not in cwd.replace("\\", "/"):
                out.append((path, cwd))
    return out


def _result_text(block: Dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content[:_RESULT_SCAN]
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") for b in content
            if isinstance(b, dict) and isinstance(b.get("text"), str)
        )[:_RESULT_SCAN]
    return ""


def child(path: str, cwd: str) -> Dict[str, Any]:
    """One child: the channels it was given, its probe runs, what it hit."""
    repo = repo_of(cwd)
    probes = {
        name: {"runs": 0, "missing": [], "first_missing": None, "first_kept": None}
        for r, name, _, _, _ in PROBES if r == repo
    }
    channels: Dict[str, bool] = {"Project": False, "AutoMem": False}
    failures: List[str] = []
    day = ""
    order = 0

    for record in _records(path):
        if not day and isinstance(record.get("timestamp"), str):
            day = record["timestamp"][:10]
        attachment = record.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "instructions":
            for entry in attachment.get("files") or []:
                if isinstance(entry, dict) and entry.get("type") in channels:
                    channels[entry["type"]] = True
        for block in _blocks(record):
            kind = block.get("type")
            if kind == "tool_use" and block.get("name") == "Bash":
                command = (block.get("input") or {}).get("command")
                if not isinstance(command, str):
                    continue
                order += 1
                body, _ = _split_heredocs(command)
                for r, name, trigger, needs, _why in PROBES:
                    if r != repo:
                        continue
                    for match in trigger.finditer(body):
                        state = probes[name]
                        state["runs"] += 1
                        if _covers(body, match, needs):
                            if state["first_kept"] is None:
                                state["first_kept"] = order
                        else:
                            state["missing"].append(body[match.start():match.end() + 70].strip())
                            if state["first_missing"] is None:
                                state["first_missing"] = order
            elif kind == "tool_result":
                text = _result_text(block)
                for label, pattern in SETUP_FAILURES:
                    if label not in failures and pattern.search(text):
                        failures.append(label)

    for state in probes.values():
        kept, missed = state["first_kept"], state["first_missing"]
        if not state["runs"]:
            state["verdict"] = None
        elif missed is None:
            state["verdict"] = "kept"
        elif kept is None:
            state["verdict"] = "missed"
        else:
            state["verdict"] = "rediscovered" if missed < kept else "kept"

    return {
        "transcript": path,
        "worktree": os.path.basename(cwd.rstrip("/")),
        "repo": repo,
        "day": day,
        "channels": channels,
        "probes": probes,
        "failures": failures,
    }


def measure(projects: str) -> Dict[str, Any]:
    children = [child(path, cwd) for path, cwd in _transcripts(projects)]
    children.sort(key=lambda c: (c["repo"], c["day"], c["worktree"]))

    repos: Dict[str, Dict[str, Any]] = {}
    for row in children:
        bucket = repos.setdefault(row["repo"], {
            "children": 0, "claude_md": 0, "auto_mem": 0, "probes": {}, "failures": {},
        })
        bucket["children"] += 1
        bucket["claude_md"] += bool(row["channels"]["Project"])
        bucket["auto_mem"] += bool(row["channels"]["AutoMem"])
        for name, state in row["probes"].items():
            if state["verdict"] is None:
                continue
            probe = bucket["probes"].setdefault(
                name, {"children": 0, "runs": 0, "missing": 0,
                       "kept": 0, "rediscovered": 0, "missed": 0})
            probe["children"] += 1
            probe["runs"] += state["runs"]
            probe["missing"] += len(state["missing"])
            probe[state["verdict"]] += 1
        for label in row["failures"]:
            bucket["failures"][label] = bucket["failures"].get(label, 0) + 1

    return {"repos": repos, "children": children}


def _why(repo: str, name: str) -> str:
    for r, n, _t, _n, why in PROBES:
        if (r, n) == (repo, name):
            return why
    return ""


def format_report(report: Dict[str, Any], *, listing: bool = False) -> str:
    lines: List[str] = []
    for repo, bucket in sorted(report["repos"].items()):
        n = bucket["children"]
        lines.append(f"{repo}  —  {n} children")
        lines.append(f"    channels: CLAUDE.md {bucket['claude_md']}/{n}, "
                     f"auto-memory {bucket['auto_mem']}/{n}")
        for name, probe in sorted(bucket["probes"].items()):
            lines.append(
                f"    {name:52} {probe['children']:3} children, {probe['runs']:4} runs, "
                f"{probe['missing']:3} missing")
            lines.append(
                f"    {'':52} kept {probe['kept']:3}   rediscovered "
                f"{probe['rediscovered']:3}   missed {probe['missed']:3}")
        for label, count in sorted(bucket["failures"].items(), key=lambda kv: -kv[1]):
            lines.append(f"    hit: {label:47} {count:3} children")
        lines.append("")
    if listing:
        lines.append("every run that missed its probe:")
        for row in report["children"]:
            for name, state in sorted(row["probes"].items()):
                for run in state["missing"]:
                    lines.append(f"  {row['day']}  {row['worktree'][-30:]:30}  "
                                 f"{name[:28]:28}  {' '.join(run.split())[:80]}")
        lines.append("")
        lines.append("why each probe:")
        for repo, name, _t, _n, why in PROBES:
            lines.append(f"  {repo}/{name}: {why}")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--list", action="store_true",
                        help="print every run that missed its probe, and why each probe exists")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = measure(args.projects)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report, listing=args.list), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
