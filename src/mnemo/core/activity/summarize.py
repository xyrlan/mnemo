"""Turn raw transcript events into what a session is doing.

Pure: event dicts in, :class:`Activity` out, no I/O.

Deliberately not built on ``core.transcript.flatten_transcript_events``. That
renders a tool use as the literal string ``[tool_use: Bash]`` and truncates
tool results at 400 chars — it throws away ``input``, which is exactly where
the edited filename is. It exists to build a briefing prompt.

The distilled shape is *last tool plus a count since*, because that is the
minimum that separates the three situations a bare ``active`` conflates:
progressing (count rising, target changing), stalled (count frozen), and
looping (count rising, same target).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

TARGET_MAX = 40

# Which input key names the target, per tool. Measured across every
# `*--claude-worktrees-*` transcript on 2026-09-13: Bash is 67% of all tool
# uses (2070 of ~3100), so its extraction matters most — and `description` is
# the human-written one, which beats a shell line every time.
_TARGET_KEYS = {
    "Bash": ("description", "command"),
    "Edit": ("file_path",),
    "Write": ("file_path",),
    "Read": ("file_path",),
    "NotebookEdit": ("notebook_path",),
    "Grep": ("pattern",),
    "Glob": ("pattern",),
    "Agent": ("description",),
    "Skill": ("skill",),
    "ToolSearch": ("query",),
    "SendMessage": ("to",),
    "TaskOutput": ("task_id",),
    "EnterWorktree": ("name", "path"),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
    "mcp__claude-in-chrome__computer": ("action",),
    "mcp__claude-in-chrome__navigate": ("url",),
    "mcp__mnemo__list_rules_by_topic": ("topic",),
    "Monitor": ("description",),
    "TaskCreate": ("subject",),
}

# Tools whose target is a path: show the basename, not 90 columns of prefix.
_BASENAME_TOOLS = ("Edit", "Write", "Read", "NotebookEdit")

# Input keys that hold a filesystem path even on a tool not in
# _BASENAME_TOOLS above (e.g. EnterWorktree's rarer `path` shape, observed
# alongside its usual `name` slug) — basename these specifically rather than
# basename-ing the tool's primary key too, which could clip a slug like
# "feat/pr-f-hosts" down to "pr-f-hosts".
_BASENAME_KEYS = ("path",)


@dataclass(frozen=True)
class Activity:
    """What a session was last seen doing.

    ``since`` is the number of tool uses observed *after* this one, which is
    always 0 for the summary of a window (it is the last) and meaningful in
    :func:`recent_actions`. ``repeated`` means the immediately preceding tool
    use had the same tool and target.
    """

    tool: Optional[str] = None
    target: Optional[str] = None
    since: int = 0
    at: Optional[str] = None
    repeated: bool = False


def _clean(value: Any) -> Optional[str]:
    """A single-line, bounded string, or None when there is nothing usable."""
    if not isinstance(value, str):
        return None
    flat = " ".join(value.split())
    if not flat:
        return None
    return flat[:TARGET_MAX]


def _target(name: str, input_: Any) -> Optional[str]:
    if not isinstance(input_, dict):
        return None
    for key in _TARGET_KEYS.get(name, ()):
        raw = input_.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        if name in _BASENAME_TOOLS or key in _BASENAME_KEYS:
            return _clean(os.path.basename(raw.rstrip("/")) or raw)
        return _clean(raw)
    return None


def _tool_uses(events: List[Dict[str, Any]]) -> List[Activity]:
    """Every tool use in *events*, oldest first, without since/repeated set.

    Only ``type == "assistant"`` carries a tool_use block; the other 16 real
    event types are skipped before any block is inspected.
    """
    out = []  # type: List[Activity]
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        message = event.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        at = event.get("timestamp")
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name")
            if not isinstance(name, str) or not name:
                continue
            out.append(Activity(
                tool=name,
                target=_target(name, block.get("input")),
                at=at if isinstance(at, str) else None,
            ))
    return out


def _with_repeats(uses: List[Activity]) -> List[Activity]:
    """Flag each use that matches the one immediately before it."""
    out = []  # type: List[Activity]
    for i, use in enumerate(uses):
        prev = uses[i - 1] if i else None
        repeated = bool(prev and prev.tool == use.tool and prev.target == use.target)
        out.append(Activity(
            tool=use.tool, target=use.target, since=use.since,
            at=use.at, repeated=repeated,
        ))
    return out


def summarize(events: List[Dict[str, Any]]) -> Optional[Activity]:
    """The last tool use in *events*, with a count of the ones before it.

    ``None`` when the window holds no tool use at all — a session that is
    thinking, or writing a long message, genuinely has nothing to show.
    """
    uses = _tool_uses(events)
    if not uses:
        return None

    last = uses[-1]
    prev = uses[-2] if len(uses) > 1 else None
    return Activity(
        tool=last.tool,
        target=last.target,
        since=len(uses) - 1,
        at=last.at,
        repeated=bool(prev and prev.tool == last.tool and prev.target == last.target),
    )


def recent_actions(
    events: List[Dict[str, Any]],
    limit: int = 15,
) -> List[Activity]:
    """The last *limit* tool uses, oldest first — the layer-2 detail view."""
    uses = _with_repeats(_tool_uses(events))
    return uses[-limit:] if limit and limit > 0 else uses
