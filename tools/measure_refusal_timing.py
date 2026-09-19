"""When a dispatch child's doubt about its issue first appears (#383).

Usage:
    PYTHONPATH=src python3 tools/measure_refusal_timing.py [--projects ~/.claude/projects] [--list] [--json]

Read-only: no LLM calls, no writes, no behaviour change.

#383 proposed that a child rate its confidence in the issue *before* paying
for the exploration, so that a low rating comments and stops instead of
building. That only pays if the doubt is knowable early. This counts whether
it is, over every dispatch transcript on disk, and is the measurement #383's
"Done means" asks for before anything is built.

The population is ``measure_exploration``'s: every transcript whose ``cwd``
parses as a ``<repo>-wt-<issue>`` or ``<repo>-wt-c-<piece>`` worktree, minus
pytest's live trees. Three numbers per child, all read from the transcript:

- **refusal** — whether the child's own text records a refusal, a correction
  of the issue's premise, or a re-scope. Read from what the child wrote, not
  from whether a PR exists: a child that shipped a PR *and* refused one bullet
  of the issue is a refusal here, because the bullet is what a confidence
  rating would have had to catch.
- **doubt_at** — how many tool uses had run when the doubt first appears in
  the child's own words. The closing report is written after the last tool
  use, so ``doubt_at == tools`` means "the child said it only at the end".
- **mutation_at** — ``exploration_for``'s count: tool uses before the first
  change to the working tree. Doubt before that point is doubt the child held
  before it started paying to build.

**What the marker can and cannot see.** A child that narrates nothing until
its report holds no early doubt *in text* even if it knew, so ``doubt_at`` is
an upper bound on when the child could have said it, never a lower one. The
asymmetry runs the safe way for #383's question: it can only make early signal
look **more** common than it is, and the measured answer is still that early
signal is rare. ``--list`` prints the sentence every marker matched so a hit
can be read rather than trusted. Read by hand over all 33 refusals on
2026-09-19: 13 carry a mid-run doubt hit, of which one is wrong (a
pluralisation helper that "already exists", nothing to do with the issue) and
one is the child correcting its **own** earlier reasoning rather than the
issue's. Neither falls in the ``N<=10`` band, so the early count survives the
read intact: all four are genuine, three of them re-scopes the child read
straight out of ``gh issue view --comments``.

The blocking count is the same question from the other side: how often a child
stopped to ask at all, and after how many tool uses. A second *human* turn is
the signal (``detector.is_human_turn``), split by what actually arrived,
because the raw count flatters the question badly: a peer session's message, a
pasted image and Claude Code's own "Continue from where you left off" all land
as human turns and none of them is a maintainer answering a blocked child.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mnemo.core.activity import exploration_for
from mnemo.core.dispatch import issue_for_cwd
from mnemo.core.sessions.detector import is_human_turn, turn_text

#: The tool-use counts #383 asks about: "within its first N tool uses".
BANDS = (3, 5, 10)

#: A report section that announces a refusal or a correction of the issue.
#: Matched over the child's whole text, because a child that shipped may still
#: have refused a bullet, and that is the case a rating would have to catch.
_REFUSAL_SECTION = re.compile(
    r"(?:^|\n)[#*\s]*(?:What I refused|O que recusei|Refused|Recusei|Declined"
    r"|The refusal|What I declined|What I (?:didn'?t|did not) (?:do|build|implement)"
    r"|Por que n[ãa]o implementei|Where the issue is wrong"
    r"|(?:One|Two|Three) (?:thing|things|correction|corrections)[^\n]{0,40}issue[^\n]{0,40}"
    r"(?:wrong|premise)|Part of the (?:issue|proposal) I declined)", re.I)

#: …unless the section says there was none. A child that writes "What I
#: refused: nothing" is reporting the absence, which is not a refusal.
_EMPTY_SECTION = re.compile(r"^\W*(?:nothing|nada|none|n/?a)\b", re.I)

#: The issue's premise, contradicted. The second alternative is the bare
#: headline shape a report uses when the premise *is* the paragraph
#: ("**Premise half wrong.** Issue says …"), which the possessive forms miss.
_PREMISE = re.compile(
    r"(?:issue'?s?|the)\s+premise[^\n.]{0,80}?(?:wrong|half|doesn'?t hold|does not hold|errada)"
    r"|(?:^|\n|\*\*)\s*premise\b[^\n.]{0,80}?(?:wrong|half|doesn'?t hold|does not hold)"
    r"|premi[ss]\w+[^\n.]{0,60}(?:que o c[óo]digo n[ãa]o confirm|errada)"
    r"|\bissue got wrong\b|\bwrong premise\b|\brests on a premise\b"
    r"|\bthe issue (?:is|was) wrong\b", re.I)

#: The issue, re-scoped or not implemented at all.
_RESCOPE = re.compile(
    r"\bre-?scoped\b|\bn[ãa]o implementei\b|\bdid not implement\b|\bimplemented nothing\b"
    r"|\bRefused #\d+ as specified\b", re.I)

#: Doubt as it reads *mid-run*, before there is a report to put it in. Looser
#: than the three above on purpose — this is the number #383 wants to be high,
#: so the generous reading is the honest one to report against it.
_DOUBT = re.compile(
    r"\bpremi[ss]\w+"
    r"|the issue (?:says|claims|asks|proposes|assumes|wants)[^\n.]{0,120}?\b(?:but|however|n[ãa]o)\b"
    r"|\b(?:doesn'?t|does not|n[ãa]o) exist\b"
    r"|\balready (?:shipped|landed|closed|fixed|done|exists)\b"
    r"|\b(?:contradicts?|contradiz\w*)\b"
    r"|\b(?:is|was) (?:already )?wrong\b|\bwrong about\b|\bnot true\b|\bisn'?t true\b"
    r"|\bthe issue (?:is|was) (?:out of date|stale|outdated)\b"
    r"|\bI (?:will|'ll|should) (?:refuse|decline|not implement)\b"
    r"|\bre-?scope[sd]?\b", re.I)


# ---------------------------------------------------------------------------
# reading a transcript
# ---------------------------------------------------------------------------


def _events(path: str) -> Iterable[Dict[str, Any]]:
    try:
        with open(path, "rb") as fh:
            for raw in fh:
                try:
                    event = json.loads(raw.decode("utf-8", "replace"))
                except ValueError:
                    continue
                if isinstance(event, dict):
                    yield event
    except OSError:
        return


def walk(path: str) -> Tuple[List[Tuple[int, str]], int, List[Tuple[int, str]]]:
    """``(assistant text turns, total tool uses, human turns after the first)``.

    Each text turn is tagged with how many tool uses preceded it, so a marker
    found in it is dated in the same unit #383 asks its question in. Tool uses
    are counted per ``tool_use`` block, not per assistant event: one turn that
    fires three tools is three. Human turns carry their text so the second one
    can be told apart from a peer message (:func:`second_turn_kind`).
    """
    turns: List[Tuple[int, str]] = []
    humans: List[Tuple[int, str]] = []
    used = 0
    for event in _events(path):
        if is_human_turn(event):
            said = (turn_text(event) or "").strip()
            if said:
                humans.append((used, said))
            continue
        if event.get("type") != "assistant":
            continue
        text: List[str] = []
        fired = 0
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                fired += 1
        joined = "\n".join(text).strip()
        if joined:
            turns.append((used, joined))
        used += fired
    return turns, used, humans[1:]


def is_refusal(text: str) -> Optional[str]:
    """Which of the three shapes *text* records, or ``None``.

    Order matters only for the label: a child often writes all three, and the
    first one found is the one reported. ``--list`` prints the sentence.
    """
    for match in _REFUSAL_SECTION.finditer(text):
        after = text[match.end():match.end() + 140].strip(" :*—-\n")
        if not _EMPTY_SECTION.match(after):
            return "section"
    if _PREMISE.search(text):
        return "premise"
    if _RESCOPE.search(text):
        return "rescope"
    return None


def first_doubt(turns: List[Tuple[int, str]]) -> Optional[Tuple[int, str]]:
    """``(tool uses so far, the sentence)`` for the earliest doubt in text."""
    for used, text in turns:
        match = _DOUBT.search(text)
        if match:
            start = max(0, match.start() - 100)
            return used, " ".join(text[start:match.end() + 140].split())
    return None


def child(path: str, cwd: str) -> Dict[str, Any]:
    turns, used, answered = walk(path)
    whole = "\n\n".join(text for _, text in turns)
    doubt = first_doubt(turns)
    exploration = exploration_for(path)
    return {
        "child": os.path.basename(cwd.rstrip("/\\")),
        "path": path,
        "tools": used,
        "turns": len(turns),
        "refusal": is_refusal(whole),
        "doubt_at": None if doubt is None else doubt[0],
        "doubt": None if doubt is None else doubt[1],
        "mutation_at": exploration.uses if exploration.reached else None,
        "answered_at": [at for at, _ in answered],
        "answered_by": second_turn_kind(answered[0][1]) if answered else None,
    }


# ---------------------------------------------------------------------------
# the population
# ---------------------------------------------------------------------------


def _is_test_tree(cwd: str) -> bool:
    return "/pytest-of-" in cwd.replace("\\", "/")


def _cwd_of(path: str) -> Optional[str]:
    for index, event in enumerate(_events(path)):
        if index > 200:
            return None
        if isinstance(event.get("cwd"), str):
            return event["cwd"]
    return None


def transcripts(projects: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    for directory in sorted(glob.glob(os.path.join(projects, "*"))):
        for path in sorted(glob.glob(os.path.join(directory, "*.jsonl"))):
            cwd = _cwd_of(path)
            if cwd and issue_for_cwd(cwd) is not None and not _is_test_tree(cwd):
                out.append((path, cwd))
    return out


def measure(projects: str) -> Dict[str, Any]:
    """The #383 question, as counts over every dispatch child on disk."""
    rows = [child(path, cwd) for path, cwd in transcripts(projects)]
    # A child still mid-run has written no report, so it can neither be a
    # refusal nor prove one absent. `turns <= 1` with no refusal marker is the
    # shape: the opening "I'll start by reading the issue" and nothing since.
    started = [r for r in rows if r["tools"] > 0]
    refusals = [r for r in started if r["refusal"]]
    early = [r for r in refusals if r["doubt_at"] is not None]
    report: Dict[str, Any] = {
        "population": len(rows),
        "started": len(started),
        "refusals": len(refusals),
        "by_kind": {
            kind: sum(1 for r in refusals if r["refusal"] == kind)
            for kind in ("section", "premise", "rescope")
        },
        "never_mutated": sum(1 for r in started if r["mutation_at"] is None),
        "refused_without_building": sum(
            1 for r in refusals if r["mutation_at"] is None),
        "within": {
            n: sum(1 for r in early if r["doubt_at"] <= n) for n in BANDS
        },
        "before_first_mutation": sum(
            1 for r in early
            if r["mutation_at"] is not None and r["doubt_at"] < r["mutation_at"]),
        "only_in_the_report": sum(
            1 for r in refusals
            if r["doubt_at"] is None or r["doubt_at"] >= r["tools"]),
        "median_tools": _median([r["tools"] for r in refusals]),
        "median_mutation_at": _median(
            [r["mutation_at"] for r in refusals if r["mutation_at"] is not None]),
        "blocked": {
            "children": sum(1 for r in started if r["answered_at"]),
            "earliest": min((r["answered_at"][0] for r in started if r["answered_at"]),
                            default=None),
            "by_kind": _kinds(started),
        },
        "rows": rows,
    }
    return report


def _median(values: List[int]) -> Optional[float]:
    return statistics.median(values) if values else None


#: What a second human turn turns out to be. Only ``typed`` can be the
#: maintainer answering; the other three arrive without anyone being asked.
_PEER = "Another Claude session sent a message"
_IMAGE = "[Image:"
_RESTART = "Continue from where you left off"


def second_turn_kind(text: str) -> str:
    if text.startswith(_PEER):
        return "peer"
    if text.startswith(_IMAGE):
        return "image"
    if text.startswith(_RESTART):
        return "auto_restart"
    return "typed"


def _kinds(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    out = {"typed": 0, "peer": 0, "image": 0, "auto_restart": 0}
    for row in rows:
        if row["answered_by"]:
            out[row["answered_by"]] += 1
    return out


# ---------------------------------------------------------------------------
# output
# ---------------------------------------------------------------------------


def render(report: Dict[str, Any]) -> str:
    lines = [
        f"dispatch children on disk: {report['population']} "
        f"({report['started']} ran at least one tool)",
        f"recorded a refusal, a premise correction or a re-scope: {report['refusals']}"
        f"  {report['by_kind']}",
        f"  of those, refused without changing the tree: "
        f"{report['refused_without_building']}",
        f"  median tool uses: {report['median_tools']}, "
        f"median uses before the first mutation: {report['median_mutation_at']}",
        "",
        "doubt first stated in the child's own words, within its first N tool uses:",
    ]
    for n in BANDS:
        lines.append(f"  N={n:<3} {report['within'][n]:3} of {report['refusals']}")
    lines += [
        f"  before its first mutation   {report['before_first_mutation']:3} "
        f"of {report['refusals']}",
        f"  only in the closing report  {report['only_in_the_report']:3} "
        f"of {report['refusals']}",
        "",
        f"children that got a second human turn: {report['blocked']['children']} "
        f"of {report['started']}, earliest after "
        f"{report['blocked']['earliest']} tool uses",
        f"  what arrived: {report['blocked']['by_kind']}",
    ]
    return "\n".join(lines)


def render_list(report: Dict[str, Any]) -> str:
    lines = [f"{'child':34} {'tools':>5} {'mut@':>5} {'doubt@':>6} {'kind':>8}  sentence"]
    for row in sorted(report["rows"], key=lambda r: r["child"]):
        if not row["refusal"]:
            continue
        lines.append(
            f"{row['child']:34} {row['tools']:5} {str(row['mutation_at']):>5} "
            f"{str(row['doubt_at']):>6} {row['refusal']:>8}  {(row['doubt'] or '')[:150]}"
        )
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--projects", default=os.path.expanduser("~/.claude/projects"))
    parser.add_argument("--list", action="store_true",
                        help="print every refusal with the sentence the marker matched")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = measure(args.projects)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    print(render(report))
    if args.list:
        print()
        print(render_list(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
