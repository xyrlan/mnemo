"""Reading Claude Code's background-session state for the live queue.

Claude Code 2.1.269 writes ``~/.claude/jobs/<short-id>/state.json`` for every
``claude --bg`` session. mnemo reads it and never writes it: the state belongs
to Claude Code, and an upstream schema change must degrade our render rather
than break a command.

See ``docs/superpowers/specs/2026-09-12-live-session-queue-design.md``.
"""
from __future__ import annotations

from mnemo.core.sessions.jobs import Session, jobs_dir, read_sessions

__all__ = ["Session", "jobs_dir", "read_sessions"]
