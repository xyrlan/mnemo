"""Read a Grok Build session history as the events mnemo reads from Claude Code.

Every reader of a session in mnemo (the briefing, ``mnemo learn``,
corrections, friction capture, backfill's harvest) was written against Claude
Code's jsonl: one event per line, ``type`` ``user`` or ``assistant``, a
``message`` with ``role`` and ``content`` blocks (``text``, ``tool_use``,
``tool_result``), a ``timestamp`` and a ``uuid``. :func:`load_events` turns a
Grok ``chat_history.jsonl`` into that shape, so none of them needs a Grok
branch.

What a Grok history holds, measured on grok 1.0.60 (2026-10-16, a ``-p``
session that wrote, edited, ran and read a file) and read from the Apache-2.0
source at ``github.com/xai-org/grok-build`` commit ``2bdd1d6``
(``xai-grok-sampling-types/src/conversation.rs``, ``ConversationItem``):

- One record per line, tagged by ``type``: ``system`` (the system prompt),
  ``user`` (``content`` is a list of parts), ``assistant`` (``content`` is a
  string; ``tool_calls`` ``[{id, name, arguments}]`` with ``arguments`` a JSON
  string; ``model_id``), ``tool_result`` (``tool_call_id``, ``content``),
  ``reasoning`` and ``backend_tool_call``. No record carries a timestamp, a
  cwd or a session id.
- A ``user`` record is the person's only when ``synthetic_reason`` is absent
  (``human``) or ``interjection`` (typed mid-turn), and even then the words
  are wrapped: ``<user_query>\\n…\\n</user_query>``, which Grok's own
  ``extract_user_query`` unwraps. The untagged ``user`` record right after
  the system prompt is the ``<user_info>``/rules prefix, which Grok's source
  says it "skips by position" until it gets its own reason; it carries no
  ``<user_query>``, and that is how it is told apart here.
- Everything else Grok injects as a ``user`` record carries a
  ``synthetic_reason`` (``system_reminder``, ``project_instructions``,
  ``task_completed``, ``stop_hook_feedback`` …). None of it is the user's
  words.
- Tool names are Grok's own (``write``, ``search_replace``,
  ``hashline_edit``, ``run_terminal_command``, ``read_file`` …), and are kept:
  :data:`MUTATION_TOOL_NAMES` is what counts as a file edit.

Times come from the files beside the history: ``rewind_points.jsonl`` stamps
each prompt (``prompt_index`` → ``created_at``), and ``summary.json`` gives
the session's ``created_at`` and ``updated_at``, stamped on the first and last
event that has no time of its own so a briefing's date and duration hold.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mnemo.core.grok import paths

#: Grok's tools that change a file (``xai-grok-tools/src/types/claude_alias.rs``:
#: what Claude's Write, Edit, MultiEdit and NotebookEdit map to).
MUTATION_TOOL_NAMES = frozenset({"write", "search_replace", "hashline_edit"})

#: ``synthetic_reason`` values whose text the person typed. Absent means
#: ``human``; an interjection is a prompt sent while the model was running.
_USER_REASONS = frozenset({"", "human", "interjection"})

#: What an injected record becomes: an event no reader takes for a turn.
#: ``flatten_transcript_events`` skips it (no ``message``), ``user_turns``
#: skips it (not ``user``), and a tool that wants to see what Grok injected
#: finds it by this subtype with the reason and text intact.
INJECTED_SUBTYPE = "grok_injected"

_USER_QUERY = re.compile(r"<user_query>(.*?)</user_query>", re.S)

SUMMARY = "summary.json"
REWIND_POINTS = "rewind_points.jsonl"


def history_path(path: Path) -> Path:
    """The ``chat_history.jsonl`` a Grok path stands for.

    A hook's ``transcript_path`` names ``updates.jsonl``; the conversation the
    model saw is ``chat_history.jsonl`` beside it. Any other path comes back
    unchanged.
    """
    path = Path(path)
    if path.name == paths.UPDATES:
        return path.with_name(paths.CHAT_HISTORY)
    return path


def is_grok_history(path: Path) -> bool:
    """Whether ``path`` is a Grok session's history (or its ``updates.jsonl``).

    Decided by name: every Grok session writes ``chat_history.jsonl`` and Claude
    Code names every transcript ``<session id>.jsonl``, so a Claude transcript
    is never taken for a Grok one. ``updates.jsonl`` counts only with the
    history beside it.
    """
    path = Path(path)
    if path.name == paths.CHAT_HISTORY:
        return True
    if path.name == paths.UPDATES:
        return path.with_name(paths.CHAT_HISTORY).is_file()
    return False


def grok_session_id(path: Path) -> Optional[str]:
    """The id of the Grok session ``path`` belongs to, or ``None``.

    It is the session directory's name (``<home>/sessions/<cwd>/<id>/``):
    every Grok history is called ``chat_history.jsonl``, so the stem that
    names a Claude session names nothing here. ``None`` for a path that is
    not a Grok history.
    """
    if not is_grok_history(path):
        return None
    name = history_path(Path(path)).parent.name
    return name or None


def session_cwd(session_dir: Path) -> Optional[str]:
    """The cwd a Grok session ran in: ``summary.json``'s, else its directory's."""
    summary = _read_json(session_dir / SUMMARY)
    info = summary.get("info") if isinstance(summary, dict) else None
    cwd = info.get("cwd") if isinstance(info, dict) else None
    if isinstance(cwd, str) and cwd:
        return cwd
    return paths.decode_cwd_dir(session_dir.parent)


def load_events(path: Path) -> List[Dict[str, Any]]:
    """A Grok history as Claude-Code-shaped events; ``[]`` when unreadable.

    ``path`` is the session's ``chat_history.jsonl`` (or its
    ``updates.jsonl``, read as the history beside it). Bad lines are skipped,
    as ``briefing._load_jsonl_events`` skips them in a Claude transcript.

    - The person's prompts become ``user`` events whose ``content`` is the text
      inside ``<user_query>``.
    - Assistant text and tool calls become one ``assistant`` event with
      ``text`` and ``tool_use`` blocks (Grok's tool name, parsed ``input``).
    - A tool result becomes a ``user`` event holding one ``tool_result``
      block, as Claude Code writes it.
    - The system prompt and reasoning are dropped; every injected ``user``
      record becomes a ``system`` event of subtype :data:`INJECTED_SUBTYPE`
      with no ``message``.
    """
    history = history_path(Path(path))
    try:
        raw = history.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    session_dir = history.parent
    session_id = session_dir.name
    cwd = session_cwd(session_dir) or ""
    prompt_times = _prompt_times(session_dir)

    events: List[Dict[str, Any]] = []
    for index, line in enumerate(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        event = _convert(record, prompt_times)
        if event is None:
            continue
        event.setdefault("uuid", f"{session_id}:{index}")
        event["sessionId"] = session_id
        if cwd:
            event["cwd"] = cwd
        events.append(event)

    _stamp_ends(events, session_dir)
    return events


def _convert(record: Dict[str, Any], prompt_times: Dict[int, str]) -> Optional[Dict[str, Any]]:
    kind = record.get("type")
    if kind == "user":
        return _user_event(record, prompt_times)
    if kind == "assistant":
        return _assistant_event(record)
    if kind == "tool_result":
        return {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": str(record.get("tool_call_id") or ""),
                    "content": _text(record.get("content")),
                }],
            },
        }
    # system prompt, reasoning, backend_tool_call: nothing a reader of a
    # Claude transcript would find there either.
    return None


def _user_event(record: Dict[str, Any], prompt_times: Dict[int, str]) -> Dict[str, Any]:
    text = _text(record.get("content"))
    reason = str(record.get("synthetic_reason") or "")
    query = user_query(text) if reason in _USER_REASONS else None
    if query is None:
        return {
            "type": "system",
            "subtype": INJECTED_SUBTYPE,
            "synthetic_reason": reason or "session_prefix",
            "content": text,
        }
    event: Dict[str, Any] = {
        "type": "user",
        "message": {"role": "user", "content": query},
    }
    prompt_index = record.get("prompt_index")
    if isinstance(prompt_index, int) and not isinstance(prompt_index, bool):
        event["promptIndex"] = prompt_index
        stamp = prompt_times.get(prompt_index)
        if stamp:
            event["timestamp"] = stamp
    return event


def _assistant_event(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    text = _text(record.get("content"))
    if text.strip():
        blocks.append({"type": "text", "text": text})
    for call in record.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        blocks.append({
            "type": "tool_use",
            "id": str(call.get("id") or ""),
            "name": str(call.get("name") or ""),
            "input": _arguments(call.get("arguments")),
        })
    if not blocks:
        return None
    message: Dict[str, Any] = {"role": "assistant", "content": blocks}
    if record.get("model_id"):
        message["model"] = str(record["model_id"])
    return {"type": "assistant", "message": message}


def user_query(text: str) -> Optional[str]:
    """The person's words inside ``<user_query>``, or ``None`` when there are none.

    Grok wraps each prompt (and each interjection) in one block; text outside
    it is Grok's own framing. A record with no block is not a prompt.
    """
    found = [m.group(1).strip() for m in _USER_QUERY.finditer(text or "")]
    found = [q for q in found if q]
    return "\n\n".join(found) if found else None


def _text(content: Any) -> str:
    """Text of a Grok ``content``: a string, or the ``text`` parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _arguments(raw: Any) -> Dict[str, Any]:
    """A tool call's ``arguments`` (a JSON string on disk) as a dict."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {"arguments": raw}
        return parsed if isinstance(parsed, dict) else {"arguments": parsed}
    return {}


def _prompt_times(session_dir: Path) -> Dict[int, str]:
    """``prompt_index`` → when the prompt was sent, from ``rewind_points.jsonl``."""
    out: Dict[int, str] = {}
    for record in _read_jsonl(session_dir / REWIND_POINTS):
        index = record.get("prompt_index")
        stamp = _iso(record.get("created_at"))
        if isinstance(index, int) and stamp and index not in out:
            out[index] = stamp
    return out


def _stamp_ends(events: List[Dict[str, Any]], session_dir: Path) -> None:
    """Give the first and last event the session's start and end, when unstamped."""
    if not events:
        return
    summary = _read_json(session_dir / SUMMARY)
    if not isinstance(summary, dict):
        return
    start = _iso(summary.get("created_at"))
    end = _iso(summary.get("updated_at") or summary.get("last_active_at"))
    if start and "timestamp" not in events[0]:
        events[0]["timestamp"] = start
    if end and "timestamp" not in events[-1]:
        events[-1]["timestamp"] = end


def _iso(value: Any) -> Optional[str]:
    """A Grok RFC 3339 time as ``YYYY-MM-DDTHH:MM:SS.ffffffZ``, or ``None``.

    Grok writes microseconds today; a nanosecond or zone-offset time is
    folded into the one shape ``datetime.fromisoformat`` reads on Python 3.8.
    """
    if not isinstance(value, str) or not value:
        return None
    match = re.match(
        r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?$",
        value.strip(),
    )
    if not match:
        return None
    base, fraction, zone = match.groups()
    micros = (fraction or "").ljust(6, "0")[:6]
    try:
        stamp = datetime.fromisoformat(f"{base}.{micros}{'+00:00' if zone in (None, 'Z') else zone}")
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    out = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out
