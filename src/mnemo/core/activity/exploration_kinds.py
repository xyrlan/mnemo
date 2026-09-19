"""What *kind* of work a child does before its first mutation (#382).

:mod:`mnemo.core.activity.exploration` answers *how much* a dispatched child
spends before it first changes the tree (#269). It cannot answer whether a
given input would have cut that spend, because it does not say what the spend
was **for**. #382 asked whether a cached repo map — DeepWiki-style — would
shrink it. The question only has an arithmetic answer once the budget is split
by what each tool use was asking:

- ``list``   — *what is here*: ``ls``, ``find``, ``tree``. A directory map
  answers these, and a directory map is small.
- ``search`` — *where does X live*: a ``grep`` across files it did not name.
  Only a symbol or content index answers these, and such an index is large.
- ``read``   — *what does this file say*: ``cat``/``sed -n``/``head <file>``,
  the ``Read`` tool, a ``grep`` inside files the command named. **No map
  replaces a read**: the child is about to change that code.
- ``run``    — a test, a script, an interpreter.
- ``context``— ``gh``, ``git log``: the issue and its history, not the repo.
- ``vault``  — an mnemo MCP call.
- ``other``  — everything else.

``list`` and ``search`` together are the **ceiling**: the most a map of any
density could remove. Measured on the 176 dispatch children that had reached a
mutation on 2026-09-19, that ceiling is **16.2% of the characters the window
returned and 4.0 of 18 uses** — reading the files the child is about to change
is 71.0%, and no map replaces it. On every repo ``mnemo dispatch`` runs in, an
index dense enough to answer the ``search`` half costs more tokens in the
opening prompt than the whole ceiling is worth. The answer to #382 was no, and
this module is how that is re-checked rather than re-argued — the same question
is asked by every proposal to put more in the opening prompt.

**One command asks more than one question**, and that is the whole difficulty.
``cat src/a.py src/b.py; ls src/cockpit`` is a read and a list in one use with
one result, and a first version of this module resolved such a command to a
single kind by priority. Measured on the same children: **82% of the ``list``
uses and 91% of the ``search`` uses were mixed, carrying 96% of the bytes
either kind was credited with** — so the rule handed a repo map nearly every
byte the co-located reads had returned, and the ceiling it printed was three
times the honest one. So a use is now **split across the
kinds its segments cover, evenly**: that ``cat``/``ls`` line is half a read and
half a list. Even shares are a proxy for byte shares, which a transcript does
not record — it keeps one result per use, not one per segment. Stated here
because it is the assumption the ceiling rests on.

Two things this is, deliberately:

- **A classifier, not a fact.** A command's text is all there is, exactly as in
  :func:`~mnemo.core.activity.exploration.bash_mutates`.
  ``measure_exploration.py --by-kind --list`` prints what it chose per session
  so a wrong choice is read, not trusted.
- **Generous where it is unsure.** An unrecognised command is ``other`` and
  earns the map nothing, but a segment that could be either is counted in, and
  a use the map only partly answers still credits the map with that part. The
  ceiling is an upper bound: if it is small here, it is smaller in truth.

Kept apart from ``exploration.py`` because the live queue reads that module on
every tick and needs none of this: nothing here runs unless a report asks for
it.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mnemo.core.activity.exploration import _mask_quotes, _split_heredocs, is_mutation

#: Every kind, in report order. Also the tie-break for :func:`classify`, which
#: names a mixed command by its first kind present — a label for a listing,
#: never the basis of the arithmetic (:func:`shares` is).
KINDS: Tuple[str, ...] = ("search", "list", "read", "run", "context", "vault", "other")

#: What a map could answer at best. Everything else is out of its reach by
#: construction, not by measurement.
ANSWERABLE: Tuple[str, ...] = ("list", "search")

_LIST = frozenset({"ls", "find", "tree", "fd"})
_READ = frozenset({"cat", "sed", "less", "more", "bat", "nl", "head", "tail", "awk"})
_RUN = frozenset({
    "pytest", "python", "python3", "node", "npm", "pnpm", "yarn", "bun", "deno",
    "cargo", "go", "make", "uv", "tox", "ruff", "mypy", "lua", "ruby", "perl",
    "jest", "vitest", "tsc", "gradle", "mvn", "dotnet", "npx",
})
#: Reads stdin when given no file to read: after a ``|`` these ask the
#: filesystem nothing at all, they narrow the answer the segment before them
#: already paid for. ``cat a.py | grep def`` is one read, not a read and a
#: search of the tree.
_FILTERS = frozenset({
    "head", "tail", "wc", "sort", "uniq", "cut", "tr", "awk", "xargs", "jq",
    "column", "grep", "sed", "nl", "cat", "rg",
})
#: Asks nothing about the repo: shell bookkeeping, control-flow keywords and the
#: closing halves of compound statements, which arrive as segments of their own
#: once a command is split on ``;`` and newlines.
_SKIP = frozenset({
    "cd", "echo", "printf", "true", "false", "export", "set", "unset", "local",
    "for", "while", "until", "if", "elif", "else", "fi", "then", "do", "done",
    "case", "esac", "in", "function", "return", "break", "continue", "exit",
    "{", "}", "(", ")", "[", "]", "[[", "]]", "#", "--",
})
_CONTEXT = frozenset({"gh", "git"})
#: ``wc -l a.py b.py`` asks how big a file is, not what it says — a question a
#: directory map can carry a line count for, so it is counted as a ``list``.
_SIZE = frozenset({"wc"})

_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
#: ``;`` ``&&`` ``||`` ``|`` and newlines. Kept with the separator so a segment
#: knows whether a filter was piped into or ran on its own.
_SPLIT = re.compile(r"(\|\||&&|[;|\n])")
_SHORT_FLAGS = re.compile(r"^-[A-Za-z]+$")


def _segments(command: str) -> List[Tuple[str, bool]]:
    """``(segment text, piped into)`` for each part of a shell command line.

    Heredoc **bodies are dropped first** and quotes masked before splitting, as
    :func:`~mnemo.core.activity.exploration.bash_mutates` does, because neither
    is shell. Measured 2026-09-19 without them: the body of every
    ``python3 - <<'PY'`` probe split on its newlines into one segment per line
    of Python, and ``import``/``def``/``return`` became 1,400 "commands" — a
    third of the whole window landed in ``other`` and every mixed command's
    real share was diluted by its own heredoc.
    """
    shell, _ = _split_heredocs(command)
    masked = _mask_quotes(shell)
    out: List[Tuple[str, bool]] = []
    piped = False
    start = 0
    for match in list(_SPLIT.finditer(masked)) + [None]:
        stop = len(masked) if match is None else match.start()
        segment = shell[start:stop]
        if segment.strip():
            out.append((segment, piped))
        if match is None:
            break
        piped = match.group() == "|"
        start = match.end()
    return out


#: Words that stand in front of the command without being it. ``do``/``then``
#: matter because splitting on ``;`` leaves a loop body as ``do gh issue view``.
_LEADING = frozenset({"sudo", "command", "time", "do", "then", "else", "!", "nohup", "exec"})


def _head(segment: str) -> Tuple[str, List[str]]:
    """``(command name, args)``, with env assignments and lead-ins stripped."""
    words = segment.split()
    while words and (_ASSIGNMENT.match(words[0]) or words[0] in _LEADING):
        words = words[1:]
    if not words:
        return "", []
    return os.path.basename(words[0].strip("'\"()`$")), words[1:]


def _grep_kind(args: List[str]) -> str:
    """``search`` when the grep roams, ``read`` when it names its files.

    A recursive grep, or one with no file operand at all (the pattern alone, or
    a ``--include`` glob), is asking *where*. A grep given paths is asking
    *what those paths say* — which is a read of files the child already
    located, and no map removes it.
    """
    recursive = False
    for arg in args:
        if arg.startswith("--"):
            if arg.startswith(("--include", "--exclude", "--recursive")):
                recursive = True
        elif _SHORT_FLAGS.match(arg) and ("r" in arg or "R" in arg):
            recursive = True
    operands = [a for a in args if not a.startswith("-")]
    return "read" if not recursive and len(operands) > 1 else "search"


def _segment_kind(segment: str, piped: bool) -> Optional[str]:
    head, args = _head(segment)
    if not head or head in _SKIP:
        return None
    operands = [a for a in args if not a.startswith("-")]
    if head in _FILTERS and piped and not operands[1 if head in ("grep", "sed", "rg") else 0:]:
        return None  # `| head -20`, `| grep def`: the question was asked upstream
    if head in _LIST:
        return "list"
    if head == "grep":
        return _grep_kind(args)
    if head in _SIZE:
        return "list"
    if head in _READ:
        return "read"
    if head in _RUN:
        return "run"
    if head in _CONTEXT:
        return "context"
    return "other"


def command_shares(command: Any) -> Dict[str, float]:
    """How one Bash command divides across kinds. The weights sum to 1.

    Every segment that asks something counts once, so ``cat a; ls b`` is
    ``{"read": .5, "list": .5}`` and ``ls; ls; cat a`` is ``{"list": .67,
    "read": .33}``. A command that asks nothing this module recognises is
    wholly ``other``, which no map answers.
    """
    kinds: List[str] = []
    if isinstance(command, str) and command.strip():
        kinds = [kind for segment, piped in _segments(command)
                 for kind in (_segment_kind(segment, piped),) if kind]
    if not kinds:
        return {"other": 1.0}
    weight = 1.0 / len(kinds)
    out: Dict[str, float] = {}
    for kind in kinds:
        out[kind] = out.get(kind, 0.0) + weight
    return out


def shares(name: Any, input_: Any) -> Dict[str, float]:
    """How one tool use divides across kinds, whatever tool made it."""
    if not isinstance(name, str) or not name:
        return {"other": 1.0}
    if name == "Bash":
        return command_shares((input_ or {}).get("command") if isinstance(input_, dict) else None)
    if name in ("Read", "NotebookRead"):
        return {"read": 1.0}
    if name == "Grep":
        return {"search": 1.0}
    if name == "Glob":
        return {"list": 1.0}
    if name.startswith("mcp__mnemo"):
        return {"vault": 1.0}
    return {"other": 1.0}


def classify(name: Any, input_: Any) -> str:
    """A single label for one use: its first kind in :data:`KINDS` order.

    For a listing and nothing else. A mixed command gets one name here and its
    real division from :func:`shares`; the two disagree on purpose.
    """
    found = shares(name, input_)
    for kind in KINDS:
        if found.get(kind):
            return kind
    return "other"


def answerable_share(name: Any, input_: Any) -> float:
    """The fraction of one use a repo map could answer: its ``list`` + ``search``."""
    found = shares(name, input_)
    return sum(found.get(kind, 0.0) for kind in ANSWERABLE)


@dataclass(frozen=True)
class Budget:
    """One child's pre-mutation spend, split by what each use was asking.

    ``uses`` and ``chars`` are **fractional**: a use that asks two questions
    contributes half to each kind, as the module docstring sets out, and the
    characters its one result returned divide the same way. Each sums to the
    whole — ``total_uses`` is the tool-use count, ``total_chars`` every
    character those uses returned — so a kind's value is readable as a share.

    ``reached`` mirrors :class:`~mnemo.core.activity.exploration.Exploration`:
    False means the transcript holds no mutation, so these are a floor.
    """

    uses: Dict[str, float] = field(default_factory=dict)
    chars: Dict[str, float] = field(default_factory=dict)
    reached: bool = False

    @property
    def total_uses(self) -> float:
        return sum(self.uses.values())

    @property
    def total_chars(self) -> float:
        return sum(self.chars.values())

    def answerable_uses(self) -> float:
        """What a repo map could remove at best: the ``list`` and ``search`` share."""
        return sum(self.uses.get(kind, 0.0) for kind in ANSWERABLE)

    def answerable_chars(self) -> float:
        return sum(self.chars.get(kind, 0.0) for kind in ANSWERABLE)

    def ceiling(self, growth: Optional[int]) -> float:
        """Tokens a *perfect, free* map could save this child, generously.

        The answerable uses' own results, plus their share of everything else
        the turns cost — the model's reasoning and tool-call blocks, which
        ``growth`` includes and result characters do not. Removing a use
        removes its turn, so crediting the map with that share is the honest
        upper bound; crediting it with only the results is the lower one
        (:meth:`answerable_chars`).

        Characters are converted at 4 per token, the ratio Claude Code's own
        context accounting uses for text. ``growth`` is
        :attr:`Exploration.tokens`; ``None`` or a total of zero uses gives 0.0.

        Still an upper bound after the split, and knowingly: it assumes the map
        is free, that the child reads it instead of looking anyway, and that
        every ``list`` and ``search`` in the window had an answer a map could
        hold. None of the three is true of any map that has been built.
        """
        if not growth or growth <= 0 or not self.total_uses:
            return 0.0
        results = self.total_chars / 4.0
        overhead = max(0.0, growth - results)
        return self.answerable_chars() / 4.0 + overhead * (
            self.answerable_uses() / self.total_uses
        )


def _result_chars(event: Dict[str, Any]) -> Dict[Any, int]:
    """``{tool_use_id: characters returned}`` for one user event."""
    message = event.get("message")
    if not isinstance(message, dict):
        return {}
    content = message.get("content")
    if not isinstance(content, list):
        return {}
    out: Dict[Any, int] = {}
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        body = block.get("content")
        if isinstance(body, str):
            size = len(body)
        elif isinstance(body, list):
            size = sum(len(part.get("text", "")) for part in body if isinstance(part, dict))
        else:
            size = 0
        out[block.get("tool_use_id")] = size
    return out


def budget(events: Iterable[Any], cwd: Optional[str] = None) -> Budget:
    """Split everything before the first mutation into kinds.

    Walks the same events :func:`~mnemo.core.activity.exploration.measure`
    does and stops at the same place, so the two numbers describe one window.
    A result whose use was never seen — a sidechain's, or one the transcript
    truncated — is dropped rather than guessed at.
    """
    uses: Dict[str, float] = {}
    chars: Dict[str, float] = {}
    pending: Dict[Any, Dict[str, float]] = {}
    reached = False
    for event in events:
        if not isinstance(event, dict):
            continue
        kind_of = event.get("type")
        if kind_of == "user":
            for use_id, size in _result_chars(event).items():
                for kind, weight in pending.pop(use_id, {}).items():
                    chars[kind] = chars.get(kind, 0.0) + size * weight
            continue
        if kind_of != "assistant" or event.get("isSidechain"):
            continue
        if cwd is None and isinstance(event.get("cwd"), str):
            cwd = event["cwd"]
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            input_ = block.get("input")
            if is_mutation(name, input_, cwd):
                reached = True
                break
            found = shares(name, input_)
            for kind, weight in found.items():
                uses[kind] = uses.get(kind, 0.0) + weight
            pending[block.get("id")] = found
        if reached:
            break
    return Budget(uses=uses, chars=chars, reached=reached)
