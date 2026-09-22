"""What a parent session does right after a child's finish notice arrives.

Usage:
    PYTHONPATH=src python3 tools/measure_notice_followups.py [--projects ~/.claude/projects] [--list] [--json]

Read-only: no LLM calls, no network, no writes.

Since #357 a dispatched child that exits posts ``<mnemo-child-finished
id="…">`` into the session that dispatched it. Until the report card, that
notice said only that the child finished and pointed at ``mnemo sessions``.
Whatever else the parent needed — which PR, is it a draft, how big, is CI
green, what did the child say it did — it went and fetched, every time. This
counts that fetching, so the card's effect is a number and not a feeling.

**The population** is every user turn that opens with the notice marker. The
**window** after it is the parent's tool calls up to the next turn that is not
a tool result: the maintainer typing, another peer message, another notice, a
task notification. Whatever the parent did in that window, it did on the
notice's account.

**What a call is.** Each ``Bash`` or ``Monitor`` command is sorted into kinds
by what it runs:

- ``queue`` — ``mnemo sessions`` / ``mnemo session``: the command the thin
  notice pointed at;
- ``pr`` — ``gh pr list`` / ``gh pr view``: which PR, its state, draft flag,
  size and body;
- ``checks`` — a one-shot ``gh pr checks`` / ``gh run list|view``;
- ``ci_wait`` — a wait for CI: ``gh pr checks --watch``, ``gh run watch``, or a
  ``Monitor`` whose command polls either;
- ``other`` — everything else (reading the diff, running tests, editing).

A command can hold several kinds (``mnemo sessions; gh pr list …``). A call
is **re-derivation** when every kind it holds is one of ``queue``, ``pr`` and
``checks`` — facts a notice can carry. Reading the diff or running the tests
is review, and a notice cannot replace review, so those never count.

**Card or thin.** A notice whose header carries ``state="…"`` is a report card;
one without is the thin notice. The split is what lets a later run compare the
two populations on the same transcripts.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from typing import Any, Dict, Iterable, List, Optional

NOTICE = "<mnemo-child-finished"
HEADER = re.compile(r'<mnemo-child-finished\s+id="(\w+)"([^>]*)>')
CARD_ATTR = re.compile(r'\bstate="([\w-]+)"')


#: Kinds a notice can carry. A call made only of these is re-derivation.
CARRIED = frozenset({"queue", "pr", "checks"})


def kinds(name: str, command: str) -> List[str]:
    """What one tool call fetched, segment by segment of its command."""
    if name not in ("Bash", "Monitor"):
        return ["other"]
    # A Monitor, or a shell loop, that polls checks is a wait for CI however
    # each poll is spelled.
    polls = name == "Monitor" or bool(_LOOP.search(command))
    found: List[str] = []
    for segment, piped in _segments(command):
        kind = _segment_kind(segment, piped)
        if kind == "checks" and polls:
            kind = "ci_wait"
        if kind and kind not in found:
            found.append(kind)
    return found or ["other"]


#: Commands that only shape output or steer the shell: they fetch nothing and
#: change nothing, so a segment led by one is glue around the real command.
_GLUE = frozenset({
    "cd", "echo", "printf", "true", "false", "test", "[", "[[", "if", "then",
    "else", "elif", "fi", "for", "do", "done", "while", "sleep", "export",
    "set", "wait", "exit", "until", "break", "continue", "{", "}", "date",
})
#: Filters: glue after a pipe, but reading a file when they lead a segment.
_FILTERS = frozenset({
    "head", "tail", "grep", "jq", "cut", "awk", "sed", "sort", "uniq", "wc",
    "tr", "column", "xargs", "cat",
})

_QUOTED = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")
_SUBST = re.compile(r"\$\(|`")
_ASSIGN = re.compile(r"^(?:\w+=\S*\s+)*\w+=$|^\w+=")
_SPLIT = re.compile(r"(\|\||&&|;|\n|\||\bthen\b|\bdo\b|\belse\b)")
_LOOP = re.compile(r"\b(until|while)\b")
_PREFIXES = frozenset({"until", "while", "if", "elif", "!", "time"})


def _segments(command: str):
    """``(segment, follows_a_pipe)`` for each simple command in *command*.

    Quoted text is masked first so a ``jq`` filter's ``|`` or a message's
    ``;`` does not split a command, and a command substitution is opened up
    so ``n=$(gh pr list …)`` reads as the ``gh`` call it is.
    """
    masked = _QUOTED.sub("''", command)
    masked = _SUBST.sub(" ", masked).replace(")", " ")
    parts = _SPLIT.split(masked)
    piped = False
    for part in parts:
        if part == "|":
            piped = True
            continue
        if _SPLIT.fullmatch(part or ""):
            piped = False
            continue
        text = part.strip()
        if text:
            yield text, piped
        piped = False


def _segment_kind(segment: str, piped: bool) -> Optional[str]:
    words = segment.split()
    while words and _ASSIGN.match(words[0]) and "=" in words[0]:
        # `n=` (the value was a substitution, opened up above) or `A=b cmd`.
        _, _, value = words[0].partition("=")
        words = words[1:] if not value or len(words) > 1 else []
    if not words:
        return None
    # Keywords that lead a command without being one: `until ! gh pr checks`
    # runs `gh`, and `timeout 900 gh run watch` runs `gh`.
    while words and words[0] in _PREFIXES:
        words = words[1:]
    if words and words[0] == "timeout" and len(words) > 2:
        words = words[2:]
    if not words:
        return None
    lead = words[0]
    if lead in ("python3", "python") and words[1:3] == ["-m", "mnemo"]:
        words = ["mnemo"] + words[3:]
        lead = "mnemo"
    if lead == "mnemo" and len(words) > 1 and words[1] in ("sessions", "session"):
        return "queue"
    if lead == "gh" and len(words) > 2:
        if words[1] == "pr" and words[2] in ("list", "view"):
            return "pr"
        if words[1] == "pr" and words[2] == "checks":
            return "ci_wait" if "--watch" in words else "checks"
        if words[1] == "run" and words[2] in ("list", "view"):
            return "checks"
        if words[1] == "run" and words[2] == "watch":
            return "ci_wait"
    if lead in _GLUE:
        return None
    if lead in _FILTERS and piped:
        return None
    return "other"


def _records(path: str) -> List[Dict[str, Any]]:
    out = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    out.append(record)
    except OSError:
        return []
    return out


def _text(content: Any) -> Optional[str]:
    """A user turn's text, or ``None`` when it is only tool results."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        if parts:
            return "\n".join(parts)
    return None


def _result_chars(content: Any) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(
            len(b.get("text", "")) for b in content if isinstance(b, dict)
        )
    return 0


def windows(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One row per notice: who, which form, and the calls that followed it."""
    out: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    pending: Dict[str, Dict[str, Any]] = {}
    for record in records:
        message = record.get("message") or {}
        if record.get("type") == "user":
            content = message.get("content")
            text = _text(content)
            if text is not None:
                current = None
                pending = {}
                if NOTICE in text:
                    header = HEADER.search(text)
                    attrs = header.group(2) if header else ""
                    state = CARD_ATTR.search(attrs)
                    current = {
                        "child": header.group(1) if header else "?",
                        "form": "card" if state else "thin",
                        "state": state.group(1) if state else None,
                        "calls": [],
                    }
                    out.append(current)
                continue
            if current is not None and isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict) or block.get("type") != "tool_result":
                        continue
                    call = pending.get(block.get("tool_use_id"))
                    if call is not None:
                        call["chars"] = _result_chars(block.get("content"))
            continue
        if record.get("type") != "assistant" or current is None:
            continue
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = str(block.get("name") or "")
            inp = block.get("input") or {}
            command = str(inp.get("command") or "") if isinstance(inp, dict) else ""
            call = {
                "name": name,
                "command": command[:200],
                "kinds": kinds(name, command),
                "chars": 0,
            }
            current["calls"].append(call)
            pending[str(block.get("id"))] = call
    return out


def is_rederivation(call: Dict[str, Any]) -> bool:
    return set(call["kinds"]) <= CARRIED


def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    by_form: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        form = by_form.setdefault(row["form"], {
            "notices": 0, "calls": 0, "rederivation_calls": 0,
            "rederivation_chars": 0, "ci_wait_calls": 0,
            "notices_with_rederivation": 0, "notices_with_ci_wait": 0,
            "lookup_calls": 0, "notices_with_lookup": 0,
        })
        form["notices"] += 1
        calls = row["calls"]
        form["calls"] += len(calls)
        redo = [c for c in calls if is_rederivation(c)]
        waits = [c for c in calls if "ci_wait" in c["kinds"]]
        form["rederivation_calls"] += len(redo)
        form["rederivation_chars"] += sum(c["chars"] for c in redo)
        form["ci_wait_calls"] += len(waits)
        form["notices_with_rederivation"] += 1 if redo else 0
        form["notices_with_ci_wait"] += 1 if waits else 0
        # The upper bound: a call that fetched any carried fact, whatever
        # else it also ran. The truth sits between the two.
        lookups = [c for c in calls if set(c["kinds"]) & CARRIED]
        form["lookup_calls"] += len(lookups)
        form["notices_with_lookup"] += 1 if lookups else 0
    return by_form


def measure(projects: str) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    parents = set()
    for path in sorted(glob.glob(os.path.join(projects, "*", "*.jsonl"))):
        try:
            with open(path, encoding="utf-8") as fh:
                if NOTICE not in fh.read():
                    continue
        except OSError:
            continue
        found = windows(_records(path))
        for row in found:
            row["parent"] = os.path.basename(path)[:8]
            parents.add(row["parent"])
        rows.extend(found)
    return {"parents": len(parents), "rows": rows, "by_form": summarize(rows)}


def _print(report: Dict[str, Any], listing: bool) -> None:
    print(f"parent sessions: {report['parents']}")
    for form, s in sorted(report["by_form"].items()):
        n = s["notices"] or 1
        print(
            f"{form}: {s['notices']} notices, {s['calls']} calls after them; "
            f"re-derivation {s['rederivation_calls']} calls "
            f"({s['rederivation_calls'] / n:.2f}/notice, "
            f"{s['rederivation_chars']:,} chars of output) in "
            f"{s['notices_with_rederivation']}/{s['notices']} notices; "
            f"any lookup {s['lookup_calls']} calls in "
            f"{s['notices_with_lookup']}/{s['notices']} notices; "
            f"CI waits {s['ci_wait_calls']} in "
            f"{s['notices_with_ci_wait']}/{s['notices']} notices"
        )
    if listing:
        for row in report["rows"]:
            print(f"\n{row['parent']} <- {row['child']} [{row['form']}]")
            for call in row["calls"]:
                mark = "*" if is_rederivation(call) else " "
                kinds_ = ",".join(call["kinds"])
                print(f"  {mark} {call['name']:<8} {kinds_:<18} {call['command'][:90]!r}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--projects", default=os.path.expanduser("~/.claude/projects"),
        help="Claude Code's transcript root",
    )
    parser.add_argument("--list", action="store_true", help="print every window's calls")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    report = measure(args.projects)
    if args.json:
        json.dump(report, sys.stdout, indent=1)
        print()
    else:
        _print(report, args.list)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
