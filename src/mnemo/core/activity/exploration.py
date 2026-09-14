"""What a session spent finding its way around before it changed anything (#269).

The case for dispatch is that a child starts out knowing the vault; its cost
is that it pays the per-session baseline again and then re-explores a repo the
parent already knew. The baseline is a fixed price. The re-exploration is the
part the vault is supposed to shrink, and until this module nothing measured
it, so "the child's context comes from the vault" was a claim.

One number per transcript, read from the transcript alone:

- ``uses`` — tool uses before the **first mutation of the working tree**;
- ``tokens`` — how much the context grew between the session's first model
  turn and the turn that issued that mutation. The first turn's context is
  ``baseline``: system prompt, skills, hooks, the opening prompt and whatever
  reflex injected into it. Growth past it is what exploring put in the window,
  and the child carries it on every turn after;
- ``injected`` — how many reflex rules arrived with the opening prompt, read
  from the ``mnemo reflex context:`` block the hook wrote into the transcript.
  The transcript copy is used rather than ``reflex-log.jsonl`` because the log
  rotates at 1MB (it held five days on 2026-09-14) and the transcript does not.

**What a mutation is** is the whole measurement, and it is not "the first
``Edit``". Measured on the 50 dispatch transcripts on disk on 2026-09-14: 15
children first changed their tree through ``Bash`` (``cat >> tests/…``,
``python3 - <<'PYEOF' … write_text(…)`` — auto mode steers toward it), 4 of
them never called ``Edit`` or ``Write`` at all (#247 among them), and 5 others
called ``Write`` first on a memory note or a scratch file outside the repo.
Counting tool names gets 15 of 50 children wrong. So:

- ``Edit``/``Write``/``MultiEdit``/``NotebookEdit`` count when their path is
  inside the session's ``cwd``;
- ``Bash`` counts when the command writes to a path not provably outside
  ``cwd`` (a redirect, ``tee``, ``sed -i``, ``cp``/``mv``/``rm``/``mkdir``/
  ``touch``), runs a tree-changing ``git`` subcommand, or runs inline code that
  calls a file-write API. A command's text is all there is to go on, so this
  is a classifier, not a fact — ``tools/measure_exploration.py --list`` prints
  what it chose for every transcript so the choice can be read, not trusted.
  Read by hand on the 50: one is wrong (#270's child ran a Python script that
  wrote into a ``mktemp`` directory passed as ``argv``, which reads as a tree
  write), so that child's count stops early.

Pure: event dicts in, :class:`Exploration` out. Reading the file lives in
:func:`mnemo.core.activity.exploration_for`, which stops at the first mutation.
"""
from __future__ import annotations

import os
import posixpath
import re
import shlex
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

_FILE_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
_PATH_KEYS = ("file_path", "notebook_path")

REFLEX_HEADER = "mnemo reflex context:"
REFLEX_BULLET = "• [["


@dataclass(frozen=True)
class Exploration:
    """Everything a session did before it first changed the working tree.

    ``reached`` is False when the transcript holds no mutation yet (a session
    still exploring, or one that ended without editing). Its ``uses`` and
    ``tokens`` are then a floor, not the number — render them as such.
    ``injected`` is ``None`` when the opening prompt carried no reflex block,
    which is indistinguishable on disk from reflex being disabled; ``0`` never
    occurs, because the hook writes no block when it emits nothing.
    """

    uses: int = 0
    tokens: int = 0
    baseline: Optional[int] = None
    reached: bool = False
    tool: Optional[str] = None
    target: Optional[str] = None
    injected: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Bash classification
# ---------------------------------------------------------------------------

# A heredoc opener: `<<`, `<<-`, optionally quoted delimiter. What its body *is*
# depends on the command that owns it — code for `python3 - <<EOF`, file
# content for `cat > x <<EOF` — so bodies are kept apart and judged per owner.
_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Inline code writing a file: pathlib, open() in a write/append mode, shutil,
# os-level moves and removals, node's fs. Applied **only** to code an
# interpreter runs. Measured 2026-09-14: scanning the whole command called 11
# of 26 Bash "first mutations" wrong — a probe script written to the job's tmp
# with `cat >`, a `grep '\.write_text('` audit, and `str.replace(` read as
# `Path.replace(`, which is why no bare `.replace(`/`.rename(` is listed.
_CODE_WRITE = re.compile(
    r"\.write_(?:text|bytes)\s*\("
    r"|\bopen\s*\([^)]*?,\s*(?:mode\s*=\s*)?['\"][wax]b?\+?['\"]"
    r"|\bshutil\.(?:copy\w*|move|rmtree)\s*\("
    r"|\bos\.(?:remove|unlink|rename|replace|makedirs|mkdir|rmdir)\s*\("
    r"|\.(?:unlink|mkdir|touch)\s*\("
    r"|\.(?:writeFile|appendFile|mkdir|rm|unlink|rename)Sync\s*\("
    r"|\bfs\.(?:writeFile|appendFile|rm|unlink|mkdir|rename)\w*\s*\("
)
_INTERPRETER = re.compile(r"^(?:python[0-9.]*|node|ruby|perl|deno|bun)$")
_CODE_FLAGS = ("-c", "-e")

# git subcommands that change files in the working tree or its history.
_GIT_MUTATING = frozenset({
    "commit", "apply", "am", "restore", "rm", "mv", "merge", "rebase",
    "cherry-pick", "revert", "pull", "clean", "reset", "stash", "checkout",
    "switch",
})
# `git stash list/show` and `git checkout -b`/`git switch -c` leave files alone.
_GIT_READ_ONLY_STASH = frozenset({"list", "show"})
_GIT_NEW_BRANCH_FLAGS = frozenset({"-b", "-B", "-c", "-C", "--orphan"})

_WRITE_COMMANDS = frozenset({"cp", "mv", "rm", "mkdir", "touch", "ln", "rmdir", "patch"})

# Where a write provably lands outside any repository worktree.
_OUTSIDE_PREFIXES = ("/dev/", "/tmp", "/private/tmp", "/var/folders", "/private/var")
_OUTSIDE_VARS = ("$CLAUDE_JOB_DIR", "${CLAUDE_JOB_DIR}", "$TMPDIR", "${TMPDIR}")
# What `$(mktemp …)` is replaced with when a variable holds one: a temp path,
# which is all the classifier needs to know about it.
_MKTEMP = "/tmp/mktemp"

_DRIVE = re.compile(r"^[A-Za-z]:/")
_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|\n]")
# The operator only, matched on quote-masked text; the target is then read from
# the raw text at the same offset, so a quoted path is still a path.
_REDIRECT = re.compile(r"(?<![<>=\-])(?:[0-9]|&)?>>?(?![&=>(])")
# A segment that is nothing but `NAME=value`.
_ASSIGNMENT = re.compile(r"""^\s*([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|'[^']*'|\$\([^)]*\)|\S*)\s*$""")
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def _split_heredocs(command: str) -> Tuple[str, List[Tuple[int, str]]]:
    """``(shell text without bodies, [(opener offset in that text, body)])``."""
    out: List[str] = []
    bodies: List[Tuple[int, str]] = []
    pending: List[Tuple[str, int, List[str]]] = []
    offset = 0
    for line in command.split("\n"):
        if pending:
            delimiter, at, body = pending[0]
            if line.strip() == delimiter:
                bodies.append((at, "\n".join(body)))
                pending.pop(0)
            else:
                body.append(line)
            continue
        for match in _HEREDOC.finditer(line):
            pending.append((match.group(2), offset + match.start(), []))
        out.append(line)
        offset += len(line) + 1
    for _, at, body in pending:  # an unterminated heredoc runs to the end
        bodies.append((at, "\n".join(body)))
    return "\n".join(out), bodies


def _mask_quotes(text: str) -> str:
    """Blank out quoted spans so `grep '->'` or `echo "a > b"` read as no redirect.

    Same length as *text*, so offsets carry across. Newlines inside quotes are
    blanked too: a multi-line ``python3 -c "…"`` is one segment, not twenty.
    """
    out = []
    quote = None
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            out.append(" ")
        elif ch in ("'", '"'):
            quote = ch
            out.append(" ")
        else:
            out.append(ch)
    return "".join(out)


def _outside(target: str, cwd: Optional[str]) -> bool:
    """True when *target* is provably not inside *cwd*.

    Relative paths resolve against ``cwd``, so they are inside. An absolute
    path under ``cwd`` is inside even when ``cwd`` itself sits under a temp
    prefix. The job and temp variables are outside: no worktree dispatch
    creates a tree under them, and this module cannot expand them to check.
    An unknown variable is not provably anywhere, so it counts as inside.
    """
    t = target.strip("'\"")
    if not t:
        return True
    for var in ("$HOME", "${HOME}"):
        if t.startswith(var):
            t = "~" + t[len(var):]
    if any(t.startswith(v) for v in _OUTSIDE_VARS):
        return True
    if t.startswith("~"):
        expanded = os.path.expanduser(t)
        if expanded == t:
            return True
        t = expanded
    if t.startswith("$"):
        return False
    # Spelled out rather than os.path.isabs: ntpath changed its answer for a
    # rooted "/x" in 3.13, and transcripts carry POSIX paths on every host.
    t = t.replace("\\", "/")
    if not (t.startswith("/") or _DRIVE.match(t)):
        return False
    t = posixpath.normpath(t)  # `/a/wt/../other` is not inside `/a/wt`
    if cwd:
        root = cwd.replace("\\", "/").rstrip("/")
        return not (t == root or t.startswith(root + "/"))
    return any(t.startswith(p) for p in _OUTSIDE_PREFIXES)


def _expand(text: str, env: Dict[str, str]) -> str:
    """Substitute the variables this command assigned; leave the rest literal."""
    def sub(match: "re.Match[str]") -> str:
        name = match.group(1) or match.group(2)
        return env.get(name, match.group(0))
    return _VARIABLE.sub(sub, text)


def _words(segment: str) -> List[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def _lands_inside(target: str, cwd: Optional[str], away: bool) -> bool:
    """A write to *target* changes the tree: not provably outside, not after a ``cd`` away."""
    if not target:
        return False
    t = target.strip("'\"")
    if away and not t.startswith(("/", "~", "$")) and not _DRIVE.match(t.replace("\\", "/")):
        return False
    return not _outside(t, cwd)


def _git_mutates(args: List[str], cwd: Optional[str], away: bool) -> bool:
    index = 0
    elsewhere = away
    while index < len(args) and args[index].startswith("-"):
        if args[index] == "-C" and index + 1 < len(args):
            elsewhere = _outside(args[index + 1], cwd)
            index += 2
        elif args[index] == "-c":
            index += 2
        else:
            index += 1
    if index >= len(args) or elsewhere:
        return False
    sub, rest = args[index], args[index + 1:]
    if sub not in _GIT_MUTATING:
        return False
    if sub == "stash":
        # A bare `git stash` does change the tree; `list`/`show` do not.
        return not rest or rest[0] not in _GIT_READ_ONLY_STASH
    if sub in ("checkout", "switch") and any(a in _GIT_NEW_BRANCH_FLAGS for a in rest):
        return False
    return True


def _segment_mutates(
    segment: str,
    masked: str,
    bodies: List[str],
    cwd: Optional[str],
    away: bool,
) -> bool:
    """Whether one ``;``/``&&``/``|``-separated segment writes into the tree.

    *away* is True once an earlier segment ``cd``'d out of ``cwd``: a relative
    path or a bare ``git commit`` then lands in that other directory. *bodies*
    are the heredoc bodies this segment opened.
    """
    command_only = segment
    for match in reversed(list(_REDIRECT.finditer(masked))):
        tail = segment[match.end():]
        rest = tail.split()
        target = rest[0] if rest else ""
        if _lands_inside(target, cwd, away):
            return True
        # Cut the redirect out before splitting words, or `mkdir -p x
        # 2>/dev/null` reads `2>/dev/null` as a second, relative, directory.
        cut = match.end() + (tail.find(target) + len(target) if target else 0)
        command_only = command_only[:match.start()] + " " + command_only[cut:]

    words = _words(command_only)
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words = words[1:]  # `PYTHONPATH=src python3 …` runs python3
    if not words:
        return False
    head = os.path.basename(words[0])
    args = words[1:]

    if _INTERPRETER.match(head):
        # Code's paths are expressions this module cannot resolve, so a write
        # API is enough — unless the command already went somewhere else.
        if away:
            return False
        code = list(bodies)
        code.extend(args[i + 1] for i, a in enumerate(args[:-1]) if a in _CODE_FLAGS)
        return any(_CODE_WRITE.search(c) for c in code)

    if head == "git":
        return _git_mutates(args, cwd, away)

    if head == "tee":
        return any(_lands_inside(a, cwd, away) for a in args if not a.startswith("-"))

    if head in ("sed", "perl"):
        if not any(a.startswith("-i") or a.startswith("-pi") for a in args):
            return False
        files = [a for a in args if not a.startswith("-")][1:]
        return any(_lands_inside(f, cwd, away) for f in files)

    if head in _WRITE_COMMANDS:
        targets = [a for a in args if not a.startswith("-")]
        if head in ("cp", "mv", "ln") and targets:
            targets = targets[-1:]  # the destination is what changes
        return any(_lands_inside(t, cwd, away) for t in targets)

    return False


def bash_mutates(command: Any, cwd: Optional[str] = None) -> bool:
    """True when *command* plausibly changes the working tree under *cwd*."""
    if not isinstance(command, str) or not command.strip():
        return False
    shell, bodies = _split_heredocs(command)
    masked = _mask_quotes(shell)

    env: Dict[str, str] = {}
    away = False
    start = 0
    bounds = [(m.start(), m.end()) for m in _SEGMENT_SPLIT.finditer(masked)]
    bounds.append((len(masked), len(masked)))
    for sep_start, sep_end in bounds:
        seg_start, start = start, sep_end
        raw = shell[seg_start:sep_start]
        if not raw.strip():
            continue

        assignment = _ASSIGNMENT.match(raw)
        if assignment:
            value = assignment.group(2).strip("'\"")
            env[assignment.group(1)] = _MKTEMP if value.startswith("$(mktemp") else _expand(value, env)
            continue

        segment = _expand(raw, env)
        # Expansion changes lengths, so the quote mask is recomputed rather
        # than sliced when a variable was substituted.
        seg_masked = masked[seg_start:sep_start] if segment == raw else _mask_quotes(segment)
        owned = [body for at, body in bodies if seg_start <= at < sep_start]

        words = _words(segment)
        if words and words[0] == "cd":
            # `cd` with no argument goes home, which is outside every worktree.
            away = _outside(words[1], cwd) if len(words) > 1 else True
            continue
        if _segment_mutates(segment, seg_masked, owned, cwd, away):
            return True
    return False


def is_mutation(name: Any, input_: Any, cwd: Optional[str] = None) -> bool:
    """True when one tool use changes the working tree under *cwd*."""
    if not isinstance(input_, dict):
        return False
    if name in _FILE_TOOLS:
        for key in _PATH_KEYS:
            path = input_.get(key)
            if isinstance(path, str) and path:
                return not _outside(path, cwd)
        return False
    if name == "Bash":
        return bash_mutates(input_.get("command"), cwd)
    return False


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------

def _context(usage: Any) -> Optional[int]:
    """Input tokens the model saw on one turn, cache reads and writes included."""
    if not isinstance(usage, dict):
        return None
    total = 0
    seen = False
    for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            total += value
            seen = True
    return total if seen else None


def _reflex_count(attachment: Any) -> Optional[int]:
    if not isinstance(attachment, dict) or attachment.get("type") != "hook_additional_context":
        return None
    content = attachment.get("content")
    if isinstance(content, str):
        content = [content]
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, str) and block.startswith(REFLEX_HEADER):
            return sum(1 for line in block.split("\n") if line.startswith(REFLEX_BULLET))
    return None


def _target(name: str, input_: Dict[str, Any]) -> Optional[str]:
    from mnemo.core.activity.summarize import _target as summarize_target

    return summarize_target(name, input_)


class Meter:
    """Feed events in order; :attr:`result` is final once :attr:`done` is True.

    Incremental so the file reader can stop at the first mutation instead of
    parsing the megabytes of work that follow it.
    """

    def __init__(self, cwd: Optional[str] = None) -> None:
        self.cwd = cwd
        self.uses = 0
        self.baseline: Optional[int] = None
        self.latest: Optional[int] = None
        self.injected: Optional[int] = None
        self.done = False
        self.tool: Optional[str] = None
        self.target: Optional[str] = None
        self.mutation_context: Optional[int] = None

    def feed(self, event: Any) -> bool:
        """Consume one event; return True once the first mutation was seen."""
        if self.done or not isinstance(event, dict):
            return self.done

        if self.cwd is None and isinstance(event.get("cwd"), str):
            self.cwd = event["cwd"]

        kind = event.get("type")
        if kind == "attachment" and self.baseline is None:
            # Only the opening prompt's injection counts as "at start". Once
            # the model has taken a turn, a later block answers a later prompt.
            count = _reflex_count(event.get("attachment"))
            if count is not None:
                self.injected = (self.injected or 0) + count
            return False

        if kind != "assistant" or event.get("isSidechain"):
            return False
        message = event.get("message")
        if not isinstance(message, dict):
            return False

        context = _context(message.get("usage"))
        if context is not None:
            if self.baseline is None:
                self.baseline = context
            self.latest = context

        content = message.get("content")
        if not isinstance(content, list):
            return False
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            input_ = block.get("input")
            if is_mutation(name, input_, self.cwd):
                self.done = True
                self.tool = name
                self.target = _target(name, input_) if isinstance(input_, dict) else None
                self.mutation_context = context if context is not None else self.latest
                return True
            self.uses += 1
        return False

    @property
    def result(self) -> Exploration:
        end = self.mutation_context if self.done else self.latest
        grown = 0
        if self.baseline is not None and end is not None:
            grown = max(0, end - self.baseline)
        return Exploration(
            uses=self.uses,
            tokens=grown,
            baseline=self.baseline,
            reached=self.done,
            tool=self.tool,
            target=self.target,
            injected=self.injected,
        )


def measure(events: Iterable[Any], cwd: Optional[str] = None) -> Exploration:
    """The :class:`Exploration` of a whole event list."""
    meter = Meter(cwd)
    for event in events:
        if meter.feed(event):
            break
    return meter.result


def total(explorations: Iterable[Optional[Exploration]]) -> Tuple[int, int, int]:
    """``(sessions, uses, tokens)`` summed over the explorations that exist.

    A dispatch's cost is the sum of its children's, which is why the number is
    kept per session rather than per queue.
    """
    count = uses = tokens = 0
    for item in explorations:
        if item is None:
            continue
        count += 1
        uses += item.uses
        tokens += item.tokens
    return count, uses, tokens
