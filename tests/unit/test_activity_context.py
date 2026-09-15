"""The token column is the context size, read from the transcript (#307).

The two fixtures are real transcript tails from ``~/.claude/projects``, cut on
2026-09-15 with every event, key and ``usage`` block kept verbatim and only
text, tool inputs, paths and account ids replaced by ``[redacted]``. The
expected numbers are not this module's arithmetic: they are what
``claude -p /context --resume <id> --fork-session --no-session-persistence``
printed for those two sessions on 2.1.272.
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from mnemo.core.activity.context import context_for, last_context
from mnemo.core.activity.tail import read_tail
from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import render_queue

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "transcripts"

#: ``fee17ecd``, a finished clubinho child. ``state.json`` said ``tokens:
#: 14540``; ``/context`` printed ``92.6k``.
FEE17ECD = FIXTURES / "context_fee17ecd.jsonl"
FEE17ECD_STATE_TOKENS = 14540
FEE17ECD_CONTEXT_PRINTED = "92.6k"

#: ``900f2ea5``: the last assistant event is a refusal written as
#: ``model: "<synthetic>"`` with zero usage. ``/context`` printed ``32.1k``.
SYNTHETIC = FIXTURES / "context_synthetic_900f2ea5.jsonl"
SYNTHETIC_CONTEXT_PRINTED = "32.1k"


def _as_context_prints(n: int) -> str:
    """``/context``'s own format: one decimal, ``k``."""
    return f"{n / 1000:.1f}k".replace(".0k", "k")


def _events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_the_number_is_what_context_printed():
    value = context_for(str(FEE17ECD))

    assert value == 92566
    assert _as_context_prints(value) == FEE17ECD_CONTEXT_PRINTED
    assert value != FEE17ECD_STATE_TOKENS


def test_a_synthetic_last_turn_is_skipped_as_context_skips_it():
    events = _events(SYNTHETIC)
    last = [e for e in events if e.get("type") == "assistant"][-1]
    assert last["message"]["model"] == "<synthetic>"  # the fixture still has the shape

    value = context_for(str(SYNTHETIC))

    assert value == 32110
    assert _as_context_prints(value) == SYNTHETIC_CONTEXT_PRINTED


def test_a_turn_outside_the_tail_window_is_still_found(tmp_path):
    """One 847KB transcript on disk had its last real turn before the 256KB window.

    The window is gone since #308 — the breakdown needs the file from its first
    turn — so this now pins that a cold read still finds that turn.
    """
    padded = tmp_path / "long.jsonl"
    tail = [e for e in _events(FEE17ECD) if e.get("type") != "assistant"]
    body = FEE17ECD.read_bytes() + b"".join(
        json.dumps(e).encode("utf-8") + b"\n" for e in tail * 20
    )
    padded.write_bytes(body)
    events, _ = read_tail(str(padded), 0, len(body) // 4)
    assert events and last_context(events) is None  # a tail window would miss it

    assert context_for(str(padded)) == 92566


def test_no_transcript_no_number(tmp_path):
    assert context_for(None) is None
    assert context_for(str(tmp_path / "gone.jsonl")) is None


def test_the_cache_reads_only_what_was_appended(tmp_path):
    """The watch path: a later turn replaces the value, a quiet tick keeps it."""
    path = tmp_path / "t.jsonl"
    shutil.copyfile(SYNTHETIC, path)
    cache: dict = {}

    assert context_for(str(path), cache) == 32110
    offset = cache[str(path)][0]
    assert offset == path.stat().st_size

    real = [e for e in _events(FEE17ECD) if e.get("type") == "assistant"][-1]
    with path.open("ab") as fh:
        fh.write(json.dumps({"type": "last-prompt"}).encode("utf-8") + b"\n")
    assert context_for(str(path), cache) == 32110  # nothing new: carried

    with path.open("ab") as fh:
        fh.write(json.dumps(real).encode("utf-8") + b"\n")
    assert context_for(str(path), cache) == 92566


def test_the_column_shows_context_not_state_tokens():
    s = Session(short_id="fee17ecd", state="working", tempo="active", name="child",
                tokens=FEE17ECD_STATE_TOKENS, link_scan_path=str(FEE17ECD),
                context_tokens=context_for(str(FEE17ECD)))

    row = next(line for line in render_queue([s]).splitlines() if "fee17ecd" in line)

    assert row.rstrip().endswith("92k")
    assert "14k" not in row


def test_unknown_context_never_falls_back_to_state_tokens():
    s = Session(short_id="a", state="working", tempo="active", name="child", tokens=14540)

    row = next(line for line in render_queue([s]).splitlines() if line.startswith("  a "))

    assert "14k" not in row


def test_json_carries_both_numbers(monkeypatch, capsys):
    from mnemo.cli.commands import sessions as sessions_cmd

    found = [Session(short_id="fee17ecd", state="done", tempo="idle",
                     tokens=FEE17ECD_STATE_TOKENS, link_scan_path=str(FEE17ECD))]
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda root=None, *, cwd=None: found)
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda sessions, *, vault_root: 0)

    args = argparse.Namespace(json=True, watch=False, **{"all": True})
    assert sessions_cmd.cmd_sessions(args) == 0
    (row,) = json.loads(capsys.readouterr().out)

    assert row["tokens"] == FEE17ECD_STATE_TOKENS  # raw field kept, unrenamed
    assert row["context_tokens"] == 92566


def test_a_rewritten_transcript_does_not_keep_the_old_number(tmp_path):
    """Shrunk below the bookmark: re-read cold, and a file with no turn has none."""
    path = tmp_path / "t.jsonl"
    shutil.copyfile(FEE17ECD, path)
    cache: dict = {}
    assert context_for(str(path), cache) == 92566

    path.write_bytes(json.dumps({"type": "last-prompt"}).encode("utf-8") + b"\n")

    assert context_for(str(path), cache) is None
