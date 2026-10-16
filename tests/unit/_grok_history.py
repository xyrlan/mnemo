"""Grok Build session directories shaped like the real ones, for unit tests.

The records are a grok 1.0.60 ``-p`` session recorded on 2026-10-16 (write a
file, edit it, ``cat`` it, read it back) with the paths redacted and the long
texts cut. Field for field they are what ``chat_history.jsonl``,
``rewind_points.jsonl`` and ``summary.json`` held.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote

SESSION_ID = "01a144ca-899c-78a1-a925-c8f76ff7de8f"
CWD = "/Users/you/proj"
PROMPT = (
    "Create a file notes.txt containing the word alpha. Then change alpha to beta in "
    "it with an edit. Then run the shell command: cat notes.txt. Then read notes.txt "
    "with your file reading tool. Reply DONE."
)


def recorded_history(cwd: str = CWD, prompt: str = PROMPT) -> List[Dict[str, Any]]:
    notes = f"{cwd}/notes.txt"
    return [
        {"type": "system", "content": "You are Grok 4.7 released by xAI. You are an autonomous agent…"},
        {"type": "user", "content": [{"type": "text", "text": (
            f"<user_info>\nOS Version: macos\nShell: /bin/zsh\nWorkspace Path: {cwd}\n"
            "Today's date: 2026-10-16\n</user_info>\n\n<rules>\nThe rules section h…</rules>"
        )}]},
        {"type": "user", "content": [{"type": "text", "text": (
            "<system-reminder>\nThe following skills are available for use…\n</system-reminder>"
        )}], "synthetic_reason": "system_reminder"},
        {"type": "user", "content": [{"type": "text", "text": f"<user_query>\n{prompt}\n</user_query>"}],
         "prompt_index": 0},
        {"type": "reasoning", "id": "rs_0bae74b9", "summary": [
            {"type": "summary_text", "text": "The user wants me to create a file"}],
         "encrypted_content": "Ypws…", "status": "completed"},
        {"type": "assistant",
         "content": "I'll create `notes.txt` with `alpha`, then edit it to `beta`.",
         "tool_calls": [{"id": "call-3b51-0", "name": "write",
                         "arguments": json.dumps({"file_path": notes, "content": "alpha\n"})}],
         "model_id": "grok-4.7", "model_fingerprint": "fp_f95ce4c3856c43c4", "reasoning_effort": "high"},
        {"type": "tool_result", "tool_call_id": "call-3b51-0",
         "content": f"The file {notes} has been created."},
        {"type": "assistant", "content": "",
         "tool_calls": [{"id": "call-9df6-1", "name": "search_replace",
                         "arguments": json.dumps({"file_path": notes, "old_string": "alpha",
                                                  "new_string": "beta"})}],
         "model_id": "grok-4.7"},
        {"type": "tool_result", "tool_call_id": "call-9df6-1",
         "content": f"The file {notes} has been updated successfully."},
        {"type": "assistant", "content": "",
         "tool_calls": [
             {"id": "call-ed4d-2", "name": "run_terminal_command",
              "arguments": json.dumps({"command": "cat notes.txt", "description": "Print notes.txt"})},
             {"id": "call-ed4d-3", "name": "read_file", "arguments": json.dumps({"target_file": notes})}],
         "model_id": "grok-4.7"},
        {"type": "tool_result", "tool_call_id": "call-ed4d-3", "content": "1→beta\n"},
        {"type": "tool_result", "tool_call_id": "call-ed4d-2", "content": "exit: 0\nbeta\n"},
        {"type": "assistant", "content": "DONE", "model_id": "grok-4.7"},
    ]


def write_session(
    home: Path,
    *,
    cwd: str = CWD,
    session_id: str = SESSION_ID,
    records: Optional[List[Dict[str, Any]]] = None,
    created_at: str = "2026-10-16T12:58:06.700000Z",
    updated_at: str = "2026-10-16T12:58:19.181318Z",
    prompt_times: Optional[Dict[int, str]] = None,
    summary_cwd: bool = True,
    dir_name: Optional[str] = None,
) -> Path:
    """Write ``<home>/sessions/<encoded cwd>/<id>/`` and return its history path."""
    encoded = dir_name if dir_name is not None else quote(cwd, safe="")
    session_dir = home / "sessions" / encoded / session_id
    session_dir.mkdir(parents=True)
    history = session_dir / "chat_history.jsonl"
    rows = recorded_history(cwd) if records is None else records
    history.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (session_dir / "updates.jsonl").write_text("", encoding="utf-8")
    times = {0: "2026-10-16T12:58:06.852575Z"} if prompt_times is None else prompt_times
    (session_dir / "rewind_points.jsonl").write_text("".join(
        json.dumps({"prompt_index": i, "created_at": t, "file_snapshots": {}, "after_snapshots": {}}) + "\n"
        for i, t in sorted(times.items())
    ), encoding="utf-8")
    summary: Dict[str, Any] = {
        "info": {"id": session_id}, "created_at": created_at, "updated_at": updated_at,
        "session_kind": "headless", "grok_home": str(home),
    }
    if summary_cwd:
        summary["info"]["cwd"] = cwd
    (session_dir / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return history
