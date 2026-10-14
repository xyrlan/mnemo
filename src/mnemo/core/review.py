"""A fresh-context reviewer at two checkpoints inside a dispatched child (#661).

Off by default. With the switch on, the child's opening prompt gains a
checkpoint clause (:func:`checkpoint_clause`) and the child is spawned with
one extra subagent defined on its command line (:func:`agents_json`): the
reviewer. The child hands its work to that subagent at each checkpoint and
decides every finding itself.

**Why a subagent inside the child, not a second child.** A concurrent
"analyzer" child that messages the implementers was rejected on 2026-10-14:
it reviews a moving target (half-written code reads as missing tests), it is
the same model on the same repo with no extra information, it doubles the
cost of a one-child dispatch, and it reverses the rule at the top of
:mod:`mnemo.core.dispatch` that dispatch never messages a child (#187). A
completion judge *after* the child was looked at on 2026-09-22 and had
little to catch: most post-child fixes were CI, environment or Windows, which
a reader cannot see.

**The reviewer.** Defined once, here, as the ``--agents`` JSON the child is
spawned with: :data:`REVIEWER_PROMPT` as its system prompt, ``model:
inherit`` so it runs on the child's own model, and :data:`REVIEWER_TOOLS` —
reading tools, the shell, and mnemo's rule tools, never ``Edit``/``Write``/
``NotebookEdit``. Like ``--read-only`` (#371) that closes the file tools, not
the shell: it prevents an accident, never intent.

``tools/measure_review_gate.py`` runs this same definition — imported, not
copied — over past child PRs, as ``claude --print --agent``.

**The two checkpoints.**

- *Design*, after exploring and before writing code, only when the change
  touches persisted data or a schema, a public API or CLI, a UI flow, or
  concurrency/scale (:data:`DESIGN_CATEGORIES`). Otherwise the child records
  :data:`DESIGN_SKIP`.
- *Pre-PR*, on the finished diff, only while :data:`PRE_PR_GATE` says the
  offline gate passed. The gate is the one this checkpoint can be tested by
  before a pilot; the design checkpoint has no such test and goes ahead.

**Where the outcome goes.** The child pipes its decisions to ``mnemo review
record`` (:func:`parse_record`), which appends one ledger row per finding to
``<vault>/.mnemo/review-ledger.jsonl`` (:func:`record`) and prints the
section, under :data:`HEADING`, that goes in the PR body and the closing
report (:func:`pr_section`). Nothing is written into the worktree.

**Blind twins.** ``mnemo twins show`` strips that section out of a closing
report (:func:`strip_section`) and refuses when a :data:`MARKERS` phrase is
still left in a diff or a report (:func:`markers_in`): a reader who can see
which twin was reviewed is no longer blind.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

#: The subagent's name, as the child's Agent tool addresses it.
AGENT_NAME = "mnemo-reviewer"

#: The fixed heading of the outcome section, in the PR body and the report.
HEADING = "## Review checkpoints"

#: What the child records when no design decision is at stake.
DESIGN_SKIP = "design checkpoint: skipped, no design decision"

#: The only changes the design checkpoint runs for, as the child names them.
DESIGN_CATEGORIES = ("schema", "api", "ui", "concurrency")

DESIGN = "design"
PRE_PR = "pre-pr"
CHECKPOINT_NAMES = (DESIGN, PRE_PR)

#: The offline gate's verdict for the pre-PR checkpoint (#661 part 2), from
#: the one real run of ``tools/measure_review_gate.py`` on 2026-10-14: the
#: reviewer caught the known bug in 0 of the 10 candidates that survived
#: re-verification, against a bar of 4, so the pre-PR checkpoint is left out
#: of the pilot. ``"pass"`` would include it; the design checkpoint goes ahead
#: either way, because the gate cannot test it.
PRE_PR_GATE = "fail"

#: What a finding's outcome may be. The child decides, with a reason.
OUTCOMES = ("accepted", "rejected")

#: The reviewer's tools: it reads, runs read-only commands and asks the vault.
#: No ``Edit``/``Write``/``NotebookEdit``. mnemo's rule tools are named the
#: way Claude Code exposes the MCP server the lean profile hands back.
REVIEWER_TOOLS = (
    "Read", "Grep", "Glob", "Bash",
    "mcp__mnemo__get_mnemo_topics",
    "mcp__mnemo__list_rules_by_topic",
    "mcp__mnemo__read_mnemo_rule",
)

#: Phrases only a reviewed twin's work carries. Anything that survives
#: :func:`strip_section` with one of these in it makes ``twins show`` refuse.
MARKERS = (
    "Review checkpoints",
    "design checkpoint",
    "pre-PR checkpoint",
    AGENT_NAME,
    "mnemo review record",
    "review-ledger",
)

LEDGER_NAME = "review-ledger.jsonl"


REVIEWER_PROMPT = """You review one change to this repository, at a checkpoint the implementer
chose. You did not write it and you have not seen the conversation that
produced it: judge the code and the note you are given, not the author's
account of them.

Judge against the project's own written standards and nothing else:
- the vault's rules, through the mnemo MCP tools when they are available
  (list_rules_by_topic with the task as the query, then read_mnemo_rule);
- the CLAUDE.md files in the repository;
- precedent elsewhere in the repository: how the same problem is already
  solved here, cited as file:line.
A defect needs no rule: a concrete failure (this input or this state leads to
this wrong result, crash or lost data) anchored at the file:line that
produces it.

Scope is the blast radius of the change: what it touches and what depends on
it. Anything wider (refactor a neighbour, change the architecture) is a
suggested issue, never a finding.

On a front end, UX is read from the code only: consistency with the existing
components, loading/error/empty states, basic accessibility.

At the design checkpoint you get a design note, not code. Judge the decision
(schema, API or CLI shape, UI flow, concurrency) against the repository's
precedent and rules: a finding there names what the design will break, or
which precedent it departs from and where.

At the pre-PR checkpoint you get a commit range. Read the diff yourself, then
the code it touches and the code that calls it.

You may read and run read-only commands. Do not write, edit, create or delete
any file, and do not commit, push or comment anywhere.

Report three lists:
- findings: defects, and departures from a written standard, each with an
  anchor. Only these go back to the implementer, who decides each one.
- suggestions: taste with no anchor ("better UX", "more scalable").
- suggested_issues: work wider than the blast radius.
An empty findings list is a valid answer. Do not invent a finding to have one.

End your reply with exactly one JSON object in a ```json block:
{"findings": [{"category": "bug|schema|api|ui|concurrency|test|standard",
  "file": "path/in/repo", "anchor": "file:line, a rule slug, or CLAUDE.md:line",
  "finding": "what fails, and how"}],
 "suggestions": ["..."], "suggested_issues": ["..."]}"""


#: The pre-PR hand-off, filled in by the child and by the gate alike.
PRE_PR_TASK = """Pre-PR checkpoint for: {what}

The change is the commit range {base}..HEAD in this repository. Read it with
`git diff {base} HEAD`, then read whatever it touches and whatever depends on
it. Report as your instructions say."""

#: The design hand-off. The note is passed inline and never written to disk.
DESIGN_TASK = """Design checkpoint for: {what}

Category: {category}

Design note:
{note}

Report as your instructions say."""


def agent_definition() -> Dict[str, Any]:
    """The reviewer, as one entry of ``claude --agents``."""
    return {
        "description": "Fresh-context reviewer for this change's review "
                       "checkpoints. Read-only; judges against the project's "
                       "own written standards.",
        "prompt": REVIEWER_PROMPT,
        "tools": list(REVIEWER_TOOLS),
        "model": "inherit",
    }


def agents_json() -> str:
    """The ``--agents`` value a reviewed child is spawned with."""
    return json.dumps({AGENT_NAME: agent_definition()}, sort_keys=True)


def checkpoints(*, gate: str = PRE_PR_GATE) -> Tuple[str, ...]:
    """The checkpoints a reviewed child runs: design always, pre-PR on a pass."""
    return (DESIGN, PRE_PR) if gate == "pass" else (DESIGN,)


# --- the child's prompt ----------------------------------------------------

_INTRO = (
    "Review checkpoints. At {count} in this task you hand your work to a "
    "reviewer with a fresh context: call the Agent tool with subagent_type "
    "`{agent}`. It runs on your model, reads but has no file tools that "
    "write, and judges against this project's own written standards — vault "
    "rules, CLAUDE.md, and precedent in the repo cited as file:line."
)

_DESIGN_STEP = (
    "Design checkpoint, after exploring and before writing code. Run it only "
    "when the change touches persisted data or a schema, a public API or "
    "CLI, a UI flow, or concurrency/scale. Write a short design note — in "
    "the Agent prompt only, never into the tree — and send it filled into "
    "this, with the category one of {categories}: \"Design checkpoint for: "
    "<the issue>. Category: <category>. Design note: <note>\". When none "
    "applies, do not run it; record `{skip}`."
)

_PRE_PR_STEP = (
    "Pre-PR checkpoint, on the finished diff, once the suite is green and "
    "before you publish anything: send \"Pre-PR checkpoint for: <the issue>. "
    "The change is the commit range <base>..HEAD in this repository. Read "
    "it with `git diff <base> HEAD`, then read whatever it touches and "
    "whatever depends on it.\" with <base> the commit your branch started "
    "from."
)

_DECIDE = (
    "You decide. Accept each finding (and act on it) or reject it, with a "
    "one-line reason either way. The reviewer's suggestions and suggested "
    "issues are not findings: list them, do not act on them. Then pipe your "
    "decisions as JSON to `mnemo review record` (`mnemo review record "
    "--help` shows the shape): it keeps the ledger and prints a "
    "`{heading}` section. Put that section in the pull request body if you "
    "open one, and at the end of your closing report either way, and mention "
    "the review nowhere else: not in the code, the commits or the rest of the "
    "report. The review leaves no file in the worktree."
)


def checkpoint_clause(names: Sequence[str]) -> str:
    """The prompt block for *names*, or ``""`` when there is none.

    ``""`` is load-bearing: with the switch off the prompt is the one every
    child was given before #661, byte for byte.
    """
    from mnemo.core.dispatch import _wrap

    names = tuple(n for n in CHECKPOINT_NAMES if n in names)
    if not names:
        return ""
    steps = []
    if DESIGN in names:
        steps.append(_DESIGN_STEP.format(
            categories=", ".join(DESIGN_CATEGORIES), skip=DESIGN_SKIP))
    if PRE_PR in names:
        steps.append(_PRE_PR_STEP)
    count = "two points" if len(steps) == 2 else "one point"
    body = "\n".join(
        _wrap(step, initial_indent=f"{n}. ", subsequent_indent="   ")
        for n, step in enumerate(steps, start=1)
    )
    return (
        "\n" + _wrap(_INTRO.format(count=count, agent=AGENT_NAME)) + "\n\n"
        + body + "\n\n" + _wrap(_DECIDE.format(heading=HEADING)) + "\n"
    )


# --- the record: ledger rows and the PR-body section -----------------------

RECORD_HELP = """Read the review's outcome as JSON on stdin, keep one ledger row per finding
and print the section for the PR body and the closing report. The shape:

  {"design": "skipped" | "schema" | "api" | "ui" | "concurrency",
   "findings": [{"checkpoint": "design" | "pre-pr",
                 "category": "bug", "anchor": "src/x.py:12",
                 "finding": "what the reviewer said",
                 "outcome": "accepted" | "rejected",
                 "reason": "one line: why"}],
   "suggestions": ["..."], "suggested_issues": ["..."]}
"""


class RecordError(ValueError):
    """The outcome the child piped in is not the documented shape."""


def _line(value: Any) -> str:
    return " ".join(str(value or "").split())


def parse_record(text: str) -> Dict[str, Any]:
    """The child's outcome, checked. Raises :class:`RecordError` saying what is wrong."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RecordError(f"not JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RecordError("expected one JSON object")
    design = _line(data.get("design") or "skipped")
    if design not in ("skipped",) + DESIGN_CATEGORIES:
        raise RecordError(
            f"design {design!r}: say skipped or one of {', '.join(DESIGN_CATEGORIES)}")
    findings = []
    for i, raw in enumerate(data.get("findings") or []):
        if not isinstance(raw, dict):
            raise RecordError(f"finding {i + 1}: expected an object")
        row = {key: _line(raw.get(key)) for key in
               ("checkpoint", "category", "anchor", "finding", "outcome", "reason")}
        if row["checkpoint"] not in CHECKPOINT_NAMES:
            raise RecordError(
                f"finding {i + 1}: checkpoint must be one of {', '.join(CHECKPOINT_NAMES)}")
        if row["outcome"] not in OUTCOMES:
            raise RecordError(f"finding {i + 1}: outcome must be accepted or rejected")
        if not row["reason"]:
            raise RecordError(f"finding {i + 1}: every outcome needs a one-line reason")
        findings.append(row)
    lists = {}
    for key in ("suggestions", "suggested_issues"):
        value = data.get(key) or []
        if not isinstance(value, list):
            raise RecordError(f"{key}: expected a list")
        lists[key] = [_line(v) for v in value if _line(v)]
    return {"design": design, "findings": findings, **lists}


def ledger_path(vault_root: Path | str) -> Path:
    return Path(vault_root) / ".mnemo" / LEDGER_NAME


def context_of(cwd: Path | str, env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Who is recording: the issue and twin tag the tree encodes, the session."""
    from mnemo.core import dispatch

    env = os.environ if env is None else env
    return {
        "issue": dispatch.issue_for_cwd(cwd),
        "twin_tag": dispatch.twin_tag_for_cwd(cwd),
        "tree": Path(cwd).name,
        "session": (env.get("CLAUDE_CODE_SESSION_ID") or "")[:8],
    }


def record(vault_root: Path | str, outcome: Mapping[str, Any],
           context: Mapping[str, Any]) -> int:
    """Append one ledger row per finding. Returns the number of rows written.

    Raises ``OSError`` — the CLI says so and still prints the section, which
    is the copy a reader sees first.
    """
    rows = outcome.get("findings") or []
    if not rows:
        return 0
    path = ledger_path(vault_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps({"at": at, **dict(context), **row}, sort_keys=True) + "\n")
    return len(rows)


def read_ledger(vault_root: Path | str) -> List[Dict[str, Any]]:
    """Every ledger row, oldest first. Never raises."""
    try:
        text = ledger_path(vault_root).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return []
    out = []
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def pr_section(outcome: Mapping[str, Any]) -> str:
    """The section under :data:`HEADING`, in the PR body and the closing report."""
    lines = [HEADING, ""]
    design = outcome.get("design") or "skipped"
    if design == "skipped":
        lines.append(f"- {DESIGN_SKIP}")
    else:
        lines.append(f"- design checkpoint: ran ({design})")
    findings = list(outcome.get("findings") or [])
    if findings:
        lines += ["", "| checkpoint | category | anchor | finding | outcome | reason |",
                  "|---|---|---|---|---|---|"]
        for f in findings:
            cells = [f.get(k, "") for k in
                     ("checkpoint", "category", "anchor", "finding", "outcome", "reason")]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    else:
        lines.append("- no findings")
    for key, title in (("suggestions", "Suggestions (low priority, not acted on)"),
                       ("suggested_issues", "Suggested issues (not acted on)")):
        items = outcome.get(key) or []
        if items:
            lines += ["", f"{title}:"] + [f"- {item}" for item in items]
    return "\n".join(lines) + "\n"


# --- blind twins -----------------------------------------------------------

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+Review checkpoints\s*$", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s")


def strip_section(text: str) -> str:
    """*text* without its :data:`HEADING` section(s).

    A section runs from the heading to the next heading of the same or a
    higher level, or to the end.
    """
    out: List[str] = []
    level = 0
    for line in text.splitlines(keepends=True):
        if _HEADING_RE.match(line):
            level = len(_ANY_HEADING_RE.match(line).group(1))
            continue
        if level:
            other = _ANY_HEADING_RE.match(line)
            if other and len(other.group(1)) <= level:
                level = 0
            else:
                continue
        out.append(line)
    return "".join(out)


def markers_in(text: str) -> List[str]:
    """The :data:`MARKERS` phrases *text* still carries, case-insensitively."""
    low = text.lower()
    return [m for m in MARKERS if m.lower() in low]


def findings_json(text: str) -> Optional[Dict[str, Any]]:
    """The reviewer's closing JSON object out of its reply, or ``None``."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text or "", re.DOTALL)
    for raw in reversed(blocks):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("findings"), list):
            return data
    return None
