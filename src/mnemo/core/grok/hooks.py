"""mnemo's hooks in a Grok Build session: telling one apart, and what differs.

Grok fires the hooks in ``~/.claude/settings.json`` (spec decision 7), so the
same four hook modules run in a Grok session and a Claude one. What they see
and what reaches the model differ, measured on grok 1.0.60 (2026-10-16, a
throwaway ``HOME`` with probe hooks, ``tests/unit/test_grok_hooks.py`` keeps
the shapes):

- **The payload.** Grok sends camelCase keys (``hookEventName`` in snake case,
  ``sessionId``, ``toolName``, ``toolUseId``, ``promptId``, ``timestamp``,
  ``workspaceRoot``) *and* Claude's snake_case aliases (``hook_event_name`` in
  PascalCase, ``session_id``, ``tool_name``, ``tool_input``,
  ``transcript_path``). A Claude payload has no camelCase key, which is what
  :func:`is_grok_payload` reads. The hook process also carries
  ``GROK_HOOK_EVENT``, ``GROK_HOOK_NAME`` and ``GROK_SESSION_ID``.
- **Tool names.** ``tool_name`` is Grok's own: ``run_terminal_command`` for
  Bash, ``read_file`` (input key ``target_file``) for Read, ``write``,
  ``search_replace`` and ``hashline_edit`` (``file_path``) for Write and Edit.
  :func:`claude_tool_name` maps them back, from Grok's own table
  (``xai-grok-tools/src/types/claude_alias.rs``), and :func:`normalize` hands
  the hooks a payload in Claude's vocabulary.
- **What reaches the model.** ``additionalContext`` from SessionStart and
  UserPromptSubmit never does; from PreToolUse it does, as a user record with
  ``synthetic_reason: system_reminder`` after the tool's result. So what
  UserPromptSubmit has for a Grok session is stashed (:func:`stash`) and the
  first PreToolUse after it hands it over (:func:`take`).
- **Two hook sources, one event.** With mnemo's hooks both in
  ``~/.claude/settings.json`` and in a Grok hook file, Grok drops a
  byte-identical copy itself but runs two commands that differ at all, both
  with the *same* payload: same ``timestamp``, same ``toolUseId`` or
  ``promptId`` (measured: one ``global/settings:…``, one ``project/…`` run,
  40 ms apart). :func:`claim_once` lets the first one through.

Nothing here runs for a Claude payload: every hook asks
:func:`is_grok_payload` first and keeps its Claude path as it was.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

#: Set by Grok on every hook process it starts (``pre_tool_use``, …).
HOOK_EVENT_ENV = "GROK_HOOK_EVENT"
#: Set by Grok on every hook process and on every command its shell runs.
SESSION_ENV = "GROK_SESSION_ID"

#: Keys only a Grok payload carries. Claude Code's are all snake_case.
_GROK_ONLY_KEYS = ("hookEventName", "sessionId")

#: Grok's tool name -> the Claude Code tool mnemo's hooks were written for.
#: From Grok's own correspondence table (``claude_alias.rs`` at grok-build
#: ``559751f``), one Claude name per Grok tool: where Grok lists a tool under
#: two Claude names (``search_replace`` is both Write and Edit there), the one
#: the tool does is kept — ``write`` creates a file, the other two edit one.
GROK_TO_CLAUDE: Dict[str, str] = {
    "run_terminal_command": "Bash",
    "read_file": "Read",
    "hashline_read": "Read",
    "write": "Write",
    "search_replace": "Edit",
    "hashline_edit": "Edit",
    "grep": "Grep",
    "hashline_grep": "Grep",
    "list_dir": "Glob",
    "lsp": "LSP",
    "web_search": "WebSearch",
    "web_fetch": "WebFetch",
    "todo_write": "TodoWrite",
    "ask_user_question": "AskUserQuestion",
    "get_command_or_subagent_output": "TaskOutput",
    "get_terminal_command_output": "BashOutput",
    "kill_command_or_subagent": "TaskStop",
    "kill_terminal_command": "KillShell",
    "skill": "Skill",
    "search_tool": "ToolSearch",
    "spawn_subagent": "Agent",
    "enter_plan_mode": "EnterPlanMode",
    "exit_plan_mode": "ExitPlanMode",
    "scheduler_create": "CronCreate",
    "scheduler_delete": "CronDelete",
    "scheduler_list": "CronList",
}

#: Claude Code's own names, which :func:`claude_tool_name` passes through.
CLAUDE_TOOLS = frozenset(GROK_TO_CLAUDE.values()) | frozenset({
    "MultiEdit", "NotebookEdit", "LS", "Task", "PowerShell", "BashOutputTool",
    "AgentOutputTool", "KillBash",
})

#: Grok input keys that Claude spells otherwise: ``read_file`` names its
#: file ``target_file`` (``ReadFileInput``'s serde rename).
_INPUT_KEYS = {"target_file": "file_path"}

#: Where an event's claim is kept: beside the session cache
#: (:mod:`mnemo.core.session`), outside any vault, since the claim is taken
#: before a hook reads its config.
_ONCE_DIR = "grok-hook-once"
#: A claim older than this is swept; two copies of one event start ~40 ms apart.
ONCE_TTL_S = 24 * 3600

#: Per-session context waiting for the first PreToolUse, under ``<vault>/.mnemo/``.
PENDING_DIR = "grok-pending"
#: Context older than this is dropped unread: the turn it was for is long over.
PENDING_TTL_S = 6 * 3600
#: The parts of pending context, in the order they are handed over: the
#: session's briefing, child notices held for it (claimed from their own
#: store, so kept until sent), and the context of its latest prompt.
BRIEFING = "briefing"
HELD = "held"
PROMPT = "prompt"
_PARTS = (BRIEFING, HELD, PROMPT)

#: Opens every block mnemo hands a Grok session, so a reader of its
#: ``chat_history.jsonl`` (``tools/measure_grok_reach.py``) can tell mnemo's
#: text from everything else without knowing what the text was.
MARKERS = {
    BRIEFING: "[mnemo:briefing via=first-tool-call]",
    HELD: "[mnemo:held-notices]",
    PROMPT: "[mnemo:prompt-context]",
}

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def is_grok_payload(payload: Mapping[str, Any], env: Optional[Mapping[str, str]] = None) -> bool:
    """True when ``payload`` came from a Grok Build session, not Claude Code.

    The payload decides when it can: Grok's carries camelCase keys a Claude
    one never has. With neither (a payload trimmed to the aliases), the
    process environment does, but only when ``GROK_HOOK_EVENT`` is set *and*
    ``GROK_SESSION_ID`` names this payload's session: a ``claude`` started
    from inside a Grok session (or by a Grok hook) inherits both variables,
    and its own session id is not Grok's.
    """
    if not isinstance(payload, Mapping):
        return False
    if any(key in payload for key in _GROK_ONLY_KEYS):
        return True
    env = os.environ if env is None else env
    if not (env.get(HOOK_EVENT_ENV) or "").strip():
        return False
    sid = str(payload.get("session_id") or "").strip()
    return bool(sid) and sid == (env.get(SESSION_ENV) or "").strip()


def claude_tool_name(name: str) -> Optional[str]:
    """The Claude Code tool a Grok tool name stands for, or ``None``.

    A Claude name comes back as itself, so a caller may pass either; a name
    neither CLI uses (an MCP tool, a tool Grok added later) is ``None``.
    """
    if not isinstance(name, str) or not name:
        return None
    if name in CLAUDE_TOOLS:
        return name
    return GROK_TO_CLAUDE.get(name)


def normalize(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy of a Grok payload in Claude Code's vocabulary.

    ``tool_name`` becomes the Claude name (unknown names are kept, so no hook
    mistakes one for a tool it acts on), ``tool_input`` gains ``file_path``
    where Grok says ``target_file``, and the original name is kept under
    ``grok_tool_name``. Every other key is left as it came.
    """
    out = dict(payload)
    name = out.get("tool_name") or out.get("toolName")
    if isinstance(name, str) and name:
        out["grok_tool_name"] = name
        out["tool_name"] = claude_tool_name(name) or name
    tool_input = out.get("tool_input")
    if not isinstance(tool_input, Mapping):
        tool_input = out.get("toolInput")
    if isinstance(tool_input, Mapping):
        fixed = dict(tool_input)
        for grok_key, claude_key in _INPUT_KEYS.items():
            if grok_key in fixed and claude_key not in fixed:
                fixed[claude_key] = fixed[grok_key]
        out["tool_input"] = fixed
    if not out.get("session_id") and out.get("sessionId"):
        out["session_id"] = out["sessionId"]
    return out


def session_id(payload: Mapping[str, Any]) -> str:
    return str(payload.get("session_id") or payload.get("sessionId") or "")


# --- one run per event -------------------------------------------------------


def _once_root() -> Path:
    return Path(tempfile.gettempdir()) / "mnemo" / _ONCE_DIR


def event_key(payload: Mapping[str, Any]) -> Optional[str]:
    """What makes this firing one event: session, event, stamp and its id.

    ``None`` when the payload has no ``timestamp``: without it two real
    events (the same command run twice) could look like one, and running a
    hook twice is the lesser harm.
    """
    stamp = payload.get("timestamp")
    sid = session_id(payload)
    if not stamp or not sid:
        return None
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    ident = (payload.get("tool_use_id") or payload.get("toolUseId")
             or payload.get("promptId") or "")
    raw = "\x1f".join(str(x) for x in (sid, event, stamp, ident))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def claim_once(payload: Mapping[str, Any], root: Optional[Path] = None) -> bool:
    """True for the first hook process to see this event, False for a copy.

    The claim is a file created with ``O_EXCL``, so two processes started
    together cannot both win. Fail-open: any error but "already claimed"
    lets the hook run.
    """
    key = event_key(payload)
    if key is None:
        return True
    base = _once_root() if root is None else Path(root)
    try:
        base.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(base / key), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError:
        return True
    os.close(fd)
    return True


def sweep_claims(root: Optional[Path] = None, *, now: Optional[float] = None) -> int:
    """Remove claims older than :data:`ONCE_TTL_S`; return how many went."""
    base = _once_root() if root is None else Path(root)
    now = time.time() if now is None else now
    gone = 0
    try:
        entries = list(base.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if now - entry.stat().st_mtime > ONCE_TTL_S:
                entry.unlink()
                gone += 1
        except OSError:
            continue
    return gone


# --- context waiting for the first PreToolUse --------------------------------


def _pending_path(vault: Path, sid: str) -> Path:
    safe = _SAFE.sub("_", sid) or "unknown"
    return Path(vault) / ".mnemo" / PENDING_DIR / f"{safe}.json"


def _read_parts(path: Path) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    parts = data.get("parts") if isinstance(data, dict) else None
    return parts if isinstance(parts, dict) else {}


def stash(vault: Path, sid: str, part: str, text: str, *, append: bool = False,
          now: Optional[float] = None) -> None:
    """Hold ``text`` as this session's ``part`` until its next PreToolUse.

    A new value replaces the part's old one (a prompt's context is for that
    prompt's turn, and a turn with no tool call never gets it), and an empty
    one removes it; with ``append`` it is added after what is waiting, which
    is how held notices, already taken from their own store, are never
    dropped. The other parts are left alone: a briefing waiting since
    SessionStart still goes with the first tool call of a later turn.
    """
    if part not in _PARTS or not sid:
        return
    path = _pending_path(vault, sid)
    parts = _read_parts(path)
    stamp = time.time() if now is None else now
    prior = parts.get(part) if isinstance(parts.get(part), dict) else None
    if append and prior and isinstance(prior.get("text"), str) and prior["text"]:
        if text:
            parts[part] = {"text": prior["text"] + "\n\n" + text, "ts": stamp}
    elif text:
        parts[part] = {"text": text, "ts": stamp}
    else:
        parts.pop(part, None)
    if not parts:
        try:
            path.unlink()
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"session_id": sid, "parts": parts}, handle)
        os.replace(tmp, str(path))
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def take(vault: Path, sid: str, *, now: Optional[float] = None) -> str:
    """This session's pending context, once, marked; ``""`` when none.

    The file is renamed away before it is read, so of two PreToolUse hooks
    running at once (Grok runs tool calls in parallel) exactly one gets it.
    """
    if not sid:
        return ""
    path = _pending_path(vault, sid)
    taken = path.with_name("%s.taken-%d" % (path.name, os.getpid()))
    try:
        os.replace(str(path), str(taken))
    except OSError:
        return ""
    try:
        parts = _read_parts(taken)
    finally:
        try:
            taken.unlink()
        except OSError:
            pass
    now = time.time() if now is None else now
    blocks = []
    for part in _PARTS:
        entry = parts.get(part)
        if not isinstance(entry, dict):
            continue
        text = entry.get("text")
        try:
            fresh = now - float(entry.get("ts") or 0) <= PENDING_TTL_S
        except (TypeError, ValueError):
            fresh = False
        if isinstance(text, str) and text and fresh:
            blocks.append(MARKERS[part] + "\n" + text)
    return "\n\n".join(blocks)


def drop(vault: Path, sid: str) -> None:
    """Forget whatever this session still had waiting (its SessionEnd)."""
    try:
        _pending_path(vault, sid).unlink()
    except OSError:
        pass


def context_of(hook_stdout: str) -> str:
    """The ``additionalContext`` a hook printed, or ``""``."""
    try:
        data = json.loads(hook_stdout or "{}")
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    out = data.get("hookSpecificOutput")
    text = out.get("additionalContext") if isinstance(out, dict) else None
    return text if isinstance(text, str) else ""


def with_context(hook_stdout: str, context: str) -> str:
    """``hook_stdout`` (a PreToolUse envelope, or nothing) carrying ``context`` too.

    ``context`` goes first, ahead of any enrichment the hook found, and an
    ``updatedInput`` the hook set is kept. A deny is returned unchanged: the
    caller does not take pending context for a call it denies.
    """
    if not context:
        return hook_stdout
    try:
        data = json.loads(hook_stdout) if hook_stdout.strip() else {}
    except ValueError:
        return hook_stdout
    if not isinstance(data, dict):
        return hook_stdout
    out = data.get("hookSpecificOutput")
    out = dict(out) if isinstance(out, dict) else {"hookEventName": "PreToolUse"}
    if out.get("permissionDecision") == "deny":
        return hook_stdout
    prior = out.get("additionalContext")
    out["additionalContext"] = context + ("\n\n" + prior if isinstance(prior, str) and prior else "")
    data["hookSpecificOutput"] = out
    return json.dumps(data)


def is_deny(hook_stdout: str) -> bool:
    try:
        data = json.loads(hook_stdout or "{}")
    except ValueError:
        return False
    out = data.get("hookSpecificOutput") if isinstance(data, dict) else None
    return isinstance(out, dict) and out.get("permissionDecision") == "deny"
