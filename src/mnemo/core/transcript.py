"""Flatten Claude Code jsonl session events into a plain-text transcript.

Extracted from ``mnemo.core.extract.prompts.build_briefing_prompt`` in
v0.9 PR F2 — it's event-parsing logic, not prompt composition (SRP fix).
The briefing caller now composes:

    transcript = flatten_transcript_events(events)
    prompt = build_briefing_prompt(transcript)
"""
from __future__ import annotations


def flatten_transcript_events(events: list[dict]) -> str:
    """Render a list of Claude Code jsonl events into a flat text transcript.

    Tolerates malformed events: non-dict entries, missing ``message``,
    string-or-list ``content``, and unknown content-block types are all
    skipped without raising. ``tool_result`` blocks longer than 400
    characters are truncated with an ellipsis to keep the prompt within
    sensible token budgets.
    """
    lines: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        etype = str(ev.get("type") or "")
        msg = ev.get("message") or {}
        role = str(msg.get("role") or etype or "?")
        content = msg.get("content")
        text = ""
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    parts.append(str(block.get("text") or ""))
                elif btype == "tool_use":
                    name = block.get("name") or "tool"
                    parts.append(f"[tool_use: {name}]")
                elif btype == "tool_result":
                    preview = str(block.get("content") or "")
                    if len(preview) > 400:
                        preview = preview[:400] + "…"
                    parts.append(f"[tool_result: {preview}]")
            text = "\n".join(p for p in parts if p)
        if not text.strip():
            continue
        lines.append(f"[{role}] {text}")

    return "\n\n".join(lines)
