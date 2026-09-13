# Session Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show what a background session is *doing* — last tool plus movement — so a child sitting at `active` is distinguishable between progressing, stuck, and looping.

**Architecture:** Three new units under `src/mnemo/core/activity/`. `tail.py` turns (path, offset) into (events, new_offset) and knows nothing about sessions. `summarize.py` turns raw event dicts into an `Activity` and knows nothing about files. `__init__.py` joins them against a `Session`. `render_queue` gains an optional activities dict; `mnemo session <id>` is a new command for the detail view. Offsets live in memory for the life of a `--watch` loop only.

**Tech Stack:** Python 3.8+ (`from __future__ import annotations` everywhere; no `X | Y` at runtime — see below), stdlib only (`json`, `io`, `os`, `dataclasses`), pytest.

**Spec:** `docs/superpowers/specs/2026-09-13-session-activity-design.md`

---

## Critical constraints for this codebase

Read these before Task 1. Each has burned a previous session.

**1. Python 3.8/3.9 compatibility.** CI runs py3.8 and py3.9. `from __future__ import
annotations` covers *annotations* but NOT runtime-evaluated expressions. A type
alias is an expression:

```python
Offsets = dict[str, int]        # BREAKS on py3.8 — TypeError at import
Offsets = "dict[str, int]"      # no
from typing import Dict
Offsets = Dict[str, int]        # correct
```

2915 local tests passed on 3.14 while py3.8 failed on exactly this (PR #209).
Annotations inside `def` and dataclass fields are fine. Module-level aliases,
`cast(...)`, and default values are not.

**2. `Session` is `frozen=True`** (`jobs.py:32`). You cannot attach an
`Activity` to it. Activities travel as a separate `Dict[str, Activity]` keyed by
`short_id`.

**3. Never `git add -A`.** Other sessions may share this tree. Every commit in
this plan names its files explicitly.

**4. Run the full suite before any commit that touches `render.py`.** CI config
and render output are both under test.

---

## Transcript format — measured, not assumed

Measured 2026-09-13 across all `*--claude-worktrees-*/*.jsonl` on this machine.

A transcript line is one JSON object. There are **17 event types**; only
`type == "assistant"` carries `tool_use` blocks. Observed distribution in one
child: `attachment` 253, `assistant` 217, `user` 128, `atis-latch` 40,
`last-prompt` 40, `mode` 39, `permission-mode` 39, `bridge-session` 39,
`ai-title` 38, `relocated` 35, `worktree-state` 35, `pr-link` 14, `system` 9,
`queue-operation` 6, `file-history-snapshot` 4, `file-history-delta` 2,
`cost-state` 1. Filtering on `assistant` first discards ~75% of lines cheaply.

Event shape (top-level keys): `type`, `timestamp`, `message`, `uuid`,
`parentUuid`, `sessionId`, `cwd`, `gitBranch`, `version`, `requestId`,
`isSidechain`, `userType`, `entrypoint`, `effort`, `apiBlockIndex`,
`session_id`.

`timestamp` is ISO-8601 with `Z`: `'2026-09-03T01:17:53.404Z'`.

`message.content` is a list of blocks. A `tool_use` block:

```json
{
  "type": "tool_use",
  "id": "toolu_01VsxdVP43941aaVWVq6Qfqe",
  "name": "Skill",
  "input": {"skill": "superpowers:subagent-driven-development"},
  "caller": {"type": "direct"}
}
```

Tool frequency and the `input` keys that carry a usable target:

| Tool | Count | Target comes from |
|---|---|---|
| `Bash` | 2070 | `description`, else `command` |
| `Edit` | 394 | `file_path` (basename) |
| `Write` | 133 | `file_path` (basename) |
| `Read` | 117 | `file_path` (basename) |
| `Agent` | 90 | `description` |
| `TaskOutput` | 86 | `task_id` |
| `mcp__claude-in-chrome__computer` | 50 | `action` |
| `SendMessage` | 42 | `to` |
| `ToolSearch` | 36 | `query` |
| `Skill` | 29 | `skill` |
| `AskUserQuestion` | 13 | — (no scalar target) |
| `EnterWorktree` | 9 | `name` |

`Bash` is 67% of all tool uses, so its extraction matters most. `Grep`/`Glob`
did not appear in this sample but take `pattern`; included in the table below
for completeness since they are common elsewhere.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/mnemo/core/activity/__init__.py` | Join a `Session` to its transcript; `activity_for` and `activities_for` |
| `src/mnemo/core/activity/tail.py` | Incremental byte reader: `(path, offset) -> (events, new_offset)` |
| `src/mnemo/core/activity/summarize.py` | `Activity` dataclass; `summarize` (one) and `recent_actions` (list) |
| `src/mnemo/cli/commands/session.py` | New `mnemo session <short_id>` command |
| `tests/unit/test_activity_tail.py` | Tail: growth, partial line, truncation, cold start |
| `tests/unit/test_activity_summarize.py` | Summarize: pure, fixed dicts |
| `tests/unit/test_activity_join.py` | Join: missing path, missing file, offset threading |
| `tests/unit/test_session_command.py` | The new command |
| `src/mnemo/core/sessions/render.py` | **Modify** — optional activities arg |
| `src/mnemo/cli/commands/sessions.py` | **Modify** — thread offsets through `--watch` |
| `src/mnemo/cli/parser.py` | **Modify** — register `session` subparser |
| `tests/unit/test_sessions_render.py` | **Modify** — add activity cases, keep byte-identical baseline |

Tasks 1–3 are independent of each other in principle, but the plan orders them
bottom-up so each task's tests can use the real types from the previous one.

---

## Task 1: The incremental tail reader

**Files:**
- Create: `src/mnemo/core/activity/__init__.py` (empty for now — package marker)
- Create: `src/mnemo/core/activity/tail.py`
- Test: `tests/unit/test_activity_tail.py`

- [ ] **Step 1: Create the package marker**

```bash
mkdir -p /Users/xyrlan/github/mnemo/src/mnemo/core/activity
printf '"""Read what a background session is doing from its transcript."""\n' \
  > /Users/xyrlan/github/mnemo/src/mnemo/core/activity/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_activity_tail.py`:

```python
"""The tail reader: bytes in, events out, offset threaded.

Every test writes a real file. The reader's whole job is byte offsets and
partial lines, and a mock would only assert that the mock works.
"""
from __future__ import annotations

import json

from mnemo.core.activity.tail import read_tail


def _line(**fields) -> str:
    return json.dumps(fields) + "\n"


def test_cold_start_reads_whole_file_when_small(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + _line(type="assistant", n=2))

    events, offset = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1, 2]
    assert offset == p.stat().st_size


def test_second_read_returns_only_the_delta(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1))
    _, offset = read_tail(str(p), 0)

    with p.open("a") as fh:
        fh.write(_line(type="assistant", n=2))
    events, new_offset = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [2]
    assert new_offset == p.stat().st_size


def test_nothing_new_returns_empty_and_same_offset(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1))
    _, offset = read_tail(str(p), 0)

    events, new_offset = read_tail(str(p), offset)

    assert events == []
    assert new_offset == offset


def test_partial_trailing_line_is_not_consumed(tmp_path):
    """A child mid-write must not cost us the line it is writing."""
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + '{"type": "assistant", "n": 2')

    events, offset = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1]
    assert offset == len(_line(type="assistant", n=1).encode())


def test_completed_line_arrives_on_the_next_read(tmp_path):
    p = tmp_path / "t.jsonl"
    first = _line(type="assistant", n=1)
    p.write_text(first + '{"type": "assistant", "n": 2')
    _, offset = read_tail(str(p), 0)

    with p.open("a") as fh:
        fh.write("}\n")
    events, _ = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [2]


def test_cold_start_windows_a_large_file_and_drops_the_partial_head(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(_line(type="assistant", n=i, pad="x" * 200) for i in range(400)))
    size = p.stat().st_size
    assert size > 4096, "fixture must exceed the window for this to mean anything"

    events, offset = read_tail(str(p), 0, window=4096)

    assert events, "window must yield something"
    assert len(events) < 400, "window must not have read the whole file"
    assert offset == size
    assert all("n" in e for e in events), "a truncated head line must be dropped"
    assert events[-1]["n"] == 399, "the window is the tail, not the head"


def test_truncated_file_resets_and_rereads(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("".join(_line(type="assistant", n=i) for i in range(10)))
    _, offset = read_tail(str(p), 0)

    p.write_text(_line(type="assistant", n=99))
    events, new_offset = read_tail(str(p), offset)

    assert [e["n"] for e in events] == [99]
    assert new_offset == p.stat().st_size


def test_invalid_json_line_is_skipped(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + "not json\n" + _line(type="assistant", n=2))

    events, _ = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1, 2]


def test_non_object_json_line_is_skipped(tmp_path):
    """Valid JSON that is not a dict would break every consumer downstream."""
    p = tmp_path / "t.jsonl"
    p.write_text(_line(type="assistant", n=1) + "[1, 2, 3]\n" + '"a string"\n')

    events, _ = read_tail(str(p), 0)

    assert [e["n"] for e in events] == [1]


def test_missing_file_returns_empty(tmp_path):
    events, offset = read_tail(str(tmp_path / "nope.jsonl"), 0)

    assert events == []
    assert offset == 0


def test_empty_file_returns_empty(tmp_path):
    p = tmp_path / "t.jsonl"
    p.write_text("")

    events, offset = read_tail(str(p), 0)

    assert events == []
    assert offset == 0


def test_directory_in_place_of_file_returns_empty(tmp_path):
    events, offset = read_tail(str(tmp_path), 0)

    assert events == []
    assert offset == 0
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_activity_tail.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'mnemo.core.activity.tail'`.

- [ ] **Step 4: Write the implementation**

Create `src/mnemo/core/activity/tail.py`:

```python
"""Read new lines from an append-only transcript, without re-reading the file.

Claude Code writes one JSON object per line and only ever appends, so a byte
offset is a complete bookmark. Worktree children measured 1.6-3.6MB on
2026-09-13 (5-10x the 327KB global median), which is why the cold start takes
a tail window instead of the whole file: four children re-read every 2s would
be ~10MB of JSON parsed per tick to render four lines.

Knows nothing about sessions or activity. Bytes in, event dicts out.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Tuple

# Wide enough to hold the ~15 most recent actions, and ~7% of the median
# worktree child. Transcript bytes are dominated by tool_result payloads; few
# exceed 10KB. One constant with a default, not a structural choice.
WINDOW = 262144


def read_tail(
    path: str,
    offset: int,
    window: int = WINDOW,
) -> Tuple[List[Dict[str, Any]], int]:
    """Return (new events, new offset) from *path*, starting at *offset*.

    ``offset == 0`` is a cold start: seek to the last *window* bytes and drop
    the first line, which is almost certainly cut in half. ``offset > 0`` reads
    forward from the bookmark.

    A trailing line with no newline is left unconsumed and does not advance the
    offset — the child may be mid-write, and half a line is worth nothing.

    Every failure returns ``([], offset_or_0)``. A queue that raises because a
    transcript moved is worse than one that shows a session without detail.
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0

    if size == 0:
        return [], 0

    # The file shrank: a rotation or truncation happened and the old bookmark
    # now points into unrelated bytes. Re-read rather than parse garbage.
    if offset > size:
        offset = 0

    if offset == size:
        return [], offset

    start = offset
    drop_first = False
    if offset == 0 and size > window:
        start = size - window
        drop_first = True

    try:
        with open(path, "rb") as fh:
            fh.seek(start)
            blob = fh.read()
    except OSError:
        return [], offset

    consumed = blob.rfind(b"\n")
    if consumed == -1:
        # Not one complete line in the window. Hold the bookmark.
        return [], offset

    complete = blob[: consumed + 1]
    new_offset = start + consumed + 1

    lines = complete.split(b"\n")
    if drop_first:
        lines = lines[1:]

    events = []  # type: List[Dict[str, Any]]
    for raw in lines:
        if not raw.strip():
            continue
        try:
            event = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue  # a half-written or corrupt line costs only itself
        if isinstance(event, dict):
            events.append(event)

    return events, new_offset
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_activity_tail.py -v
```

Expected: 12 passed.

- [ ] **Step 6: Verify it works on a real transcript**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -c "
import glob
from mnemo.core.activity.tail import read_tail
f = sorted(glob.glob('/Users/xyrlan/.claude/projects/*--claude-worktrees-*/*.jsonl'))[0]
ev, off = read_tail(f, 0)
print('events in window:', len(ev))
print('offset:', off)
assert ev, 'real transcript yielded nothing'
assert all(isinstance(e, dict) for e in ev)
ev2, off2 = read_tail(f, off)
assert ev2 == [] and off2 == off, 'second read of a static file must be empty'
print('OK')
"
```

Expected: a nonzero event count, then `OK`.

- [ ] **Step 7: Commit**

```bash
cd /Users/xyrlan/github/mnemo
git add src/mnemo/core/activity/__init__.py src/mnemo/core/activity/tail.py tests/unit/test_activity_tail.py
git commit -m "feat(activity): read new transcript lines from a byte offset

Worktree children measured 1.6-3.6MB, 5-10x the global median, so
re-reading each file per tick would be ~10MB of JSON per 2s to render
four lines. Cold start takes a 256KB tail window instead.

A trailing line without a newline is left unconsumed: the child may be
mid-write. A file smaller than the bookmark means truncation, so the
offset resets rather than parsing unrelated bytes.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AxLNbB2ibbEYGoAp9gUw7H"
```

---

## Task 2: Distill events into an Activity

**Files:**
- Create: `src/mnemo/core/activity/summarize.py`
- Test: `tests/unit/test_activity_summarize.py`

Note on why this does not reuse `flatten_transcript_events`
(`src/mnemo/core/transcript.py:16`): that function returns a flattened `str`,
renders a tool use as the literal `[tool_use: Bash]`, and truncates
`tool_result` at 400 chars. It discards `input`, which is where the edited
filename lives. It builds the briefing prompt; it is the wrong input here.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_activity_summarize.py`:

```python
"""Distilling raw transcript events into one Activity.

Pure function, fixed dicts. Event shapes here are copied from real
transcripts measured 2026-09-13, including the 16 event types that carry no
tool_use at all.
"""
from __future__ import annotations

from mnemo.core.activity.summarize import Activity, recent_actions, summarize


def _assistant(name, input_, ts="2026-09-13T14:02:11.000Z"):
    """One assistant event carrying a single tool_use block."""
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": name, "input": input_}],
        },
    }


def _text(body, ts="2026-09-13T14:00:00.000Z"):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {"role": "assistant", "content": [{"type": "text", "text": body}]},
    }


def test_empty_events_give_no_activity():
    assert summarize([]) is None


def test_no_tool_use_gives_no_activity():
    assert summarize([_text("Vou começar analisando a estrutura")]) is None


def test_single_tool_use():
    act = summarize([_assistant("Edit", {"file_path": "/repo/src/dispatch.py"})])

    assert act == Activity(
        tool="Edit", target="dispatch.py", since=0,
        at="2026-09-13T14:02:11.000Z", repeated=False,
    )


def test_since_counts_tool_uses_after_the_last_one():
    """The counter is the movement signal: it separates stalled from working."""
    events = [
        _assistant("Read", {"file_path": "/repo/a.py"}),
        _assistant("Edit", {"file_path": "/repo/b.py"}),
        _assistant("Edit", {"file_path": "/repo/c.py"}),
        _assistant("Bash", {"command": "pytest", "description": "Run tests"}),
    ]

    act = summarize(events)

    assert act.tool == "Bash"
    assert act.target == "Run tests"
    assert act.since == 3


def test_repeated_tool_and_target_is_flagged():
    """Same grep four times is the loop signal."""
    same = [_assistant("Grep", {"pattern": "linkScanOffset"}) for _ in range(4)]

    act = summarize(same)

    assert act.repeated is True
    assert act.since == 3


def test_different_target_same_tool_is_not_repeated():
    events = [
        _assistant("Read", {"file_path": "/repo/a.py"}),
        _assistant("Read", {"file_path": "/repo/b.py"}),
    ]

    assert summarize(events).repeated is False


def test_bash_prefers_description_over_command():
    act = summarize([_assistant("Bash", {"command": "pytest -q tests/", "description": "Run tests"})])

    assert act.target == "Run tests"


def test_bash_falls_back_to_command():
    act = summarize([_assistant("Bash", {"command": "pytest -q tests/"})])

    assert act.target == "pytest -q tests/"


def test_file_tools_use_the_basename():
    for tool in ("Edit", "Write", "Read"):
        act = summarize([_assistant(tool, {"file_path": "/very/long/path/to/render.py"})])
        assert act.target == "render.py", tool


def test_known_tools_each_find_a_target():
    """Input keys measured from real worktree transcripts."""
    cases = [
        ("Agent", {"description": "Map dispatch", "prompt": "..."}, "Map dispatch"),
        ("Skill", {"skill": "superpowers:brainstorming"}, "superpowers:brainstorming"),
        ("ToolSearch", {"query": "select:Read", "max_results": 5}, "select:Read"),
        ("SendMessage", {"to": "child-197", "message": "..."}, "child-197"),
        ("TaskOutput", {"task_id": "abc123"}, "abc123"),
        ("Grep", {"pattern": "linkScanPath"}, "linkScanPath"),
        ("Glob", {"pattern": "**/*.py"}, "**/*.py"),
        ("EnterWorktree", {"name": "feat-x"}, "feat-x"),
    ]
    for tool, input_, expected in cases:
        assert summarize([_assistant(tool, input_)]).target == expected, tool


def test_unknown_tool_keeps_the_name_and_has_no_target():
    act = summarize([_assistant("SomeFutureTool", {"weird": "shape"})])

    assert act.tool == "SomeFutureTool"
    assert act.target is None


def test_tool_with_no_scalar_target():
    act = summarize([_assistant("AskUserQuestion", {"questions": [{"q": "?"}]})])

    assert act.tool == "AskUserQuestion"
    assert act.target is None


def test_long_target_is_truncated():
    act = summarize([_assistant("Bash", {"command": "x" * 200})])

    assert len(act.target) <= 40


def test_target_newlines_are_flattened():
    """A multi-line command must not break the one-line render."""
    act = summarize([_assistant("Bash", {"command": "cd /repo\npytest"})])

    assert "\n" not in act.target


def test_non_assistant_events_are_ignored():
    """16 of the 17 real event types carry no tool_use; none may confuse us."""
    noise = [
        {"type": "attachment", "timestamp": "2026-09-13T13:00:00.000Z"},
        {"type": "atis-latch", "timestamp": "2026-09-13T13:00:01.000Z"},
        {"type": "user", "timestamp": "2026-09-13T13:00:02.000Z",
         "message": {"role": "user", "content": "oi"}},
        {"type": "worktree-state", "timestamp": "2026-09-13T13:00:03.000Z"},
    ]

    assert summarize(noise) is None

    act = summarize(noise + [_assistant("Edit", {"file_path": "/r/x.py"})])
    assert act.tool == "Edit"
    assert act.since == 0


def test_malformed_events_are_tolerated():
    """Real files carry lines this reader has never seen. None may raise."""
    junk = [
        {},
        {"type": "assistant"},
        {"type": "assistant", "message": None},
        {"type": "assistant", "message": {"content": "a string, not a list"}},
        {"type": "assistant", "message": {"content": [None, 7, "x"]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Edit"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Edit", "input": "not a dict"}]}},
    ]

    act = summarize(junk)

    # The two nameless/inputless blocks yield nothing usable; the named ones
    # give a tool with no target. Either way: no exception.
    assert act is None or act.tool == "Edit"


def test_several_tool_uses_in_one_event():
    """Parallel tool calls arrive as multiple blocks in one message."""
    event = {
        "type": "assistant",
        "timestamp": "2026-09-13T14:05:00.000Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "a", "name": "Read", "input": {"file_path": "/r/a.py"}},
            {"type": "tool_use", "id": "b", "name": "Read", "input": {"file_path": "/r/b.py"}},
        ]},
    }

    act = summarize([event])

    assert act.target == "b.py", "the last block in the event is the latest"
    assert act.since == 1


def test_missing_timestamp_gives_none_at():
    event = _assistant("Edit", {"file_path": "/r/a.py"})
    del event["timestamp"]

    assert summarize([event]).at is None


# --- recent_actions: the layer-2 list ---

def test_recent_actions_returns_oldest_first():
    events = [
        _assistant("Read", {"file_path": "/r/a.py"}, ts="2026-09-13T14:00:00.000Z"),
        _assistant("Edit", {"file_path": "/r/b.py"}, ts="2026-09-13T14:01:00.000Z"),
    ]

    actions = recent_actions(events)

    assert [a.tool for a in actions] == ["Read", "Edit"]
    assert [a.target for a in actions] == ["a.py", "b.py"]


def test_recent_actions_limit_keeps_the_newest():
    events = [_assistant("Read", {"file_path": "/r/%d.py" % i}) for i in range(20)]

    actions = recent_actions(events, limit=5)

    assert len(actions) == 5
    assert [a.target for a in actions] == ["15.py", "16.py", "17.py", "18.py", "19.py"]


def test_recent_actions_flags_consecutive_repeats():
    events = [
        _assistant("Grep", {"pattern": "x"}),
        _assistant("Grep", {"pattern": "x"}),
        _assistant("Read", {"file_path": "/r/a.py"}),
    ]

    actions = recent_actions(events)

    assert [a.repeated for a in actions] == [False, True, False]


def test_recent_actions_on_empty_is_empty():
    assert recent_actions([]) == []
    assert recent_actions([_text("thinking")]) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_activity_summarize.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'mnemo.core.activity.summarize'`.

- [ ] **Step 3: Write the implementation**

Create `src/mnemo/core/activity/summarize.py`:

```python
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
    "EnterWorktree": ("name",),
    "WebFetch": ("url",),
    "WebSearch": ("query",),
}

# Tools whose target is a path: show the basename, not 90 columns of prefix.
_BASENAME_TOOLS = ("Edit", "Write", "Read", "NotebookEdit")


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
        if name in _BASENAME_TOOLS:
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_activity_summarize.py -v
```

Expected: 22 passed.

- [ ] **Step 5: Verify against real transcripts**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -c "
import glob
from mnemo.core.activity.tail import read_tail
from mnemo.core.activity.summarize import recent_actions, summarize
files = sorted(glob.glob('/Users/xyrlan/.claude/projects/*--claude-worktrees-*/*.jsonl'))
hits = 0
for f in files:
    ev, _ = read_tail(f, 0)
    act = summarize(ev)
    if act:
        hits += 1
        print('%-14s %-40s +%d%s' % (act.tool, act.target or '-', act.since, ' LOOP' if act.repeated else ''))
    n = len(recent_actions(ev))
    assert n <= 15, n
print()
print('%d of %d transcripts yielded an activity' % (hits, len(files)))
assert hits, 'no real transcript produced an activity'
"
```

Expected: one line per transcript with a plausible tool and target, then a
nonzero count. If the target column is mostly `-`, `_TARGET_KEYS` is missing a
tool this codebase actually uses — add it and re-run.

- [ ] **Step 6: Commit**

```bash
cd /Users/xyrlan/github/mnemo
git add src/mnemo/core/activity/summarize.py tests/unit/test_activity_summarize.py
git commit -m "feat(activity): distill transcript events into last tool plus movement

Last tool alone cannot tell a stalled session from a looping one, so the
Activity carries a count of the tool uses since and a repeated flag. Those
three fields separate the situations a bare \`active\` conflates.

Target extraction is keyed per tool from real measurements: Bash is 67% of
all tool uses and its human-written \`description\` beats the shell line.
File tools show a basename.

Not built on flatten_transcript_events — that renders a tool use as the
literal string \`[tool_use: Bash]\` and discards \`input\`, which is where
the filename is.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AxLNbB2ibbEYGoAp9gUw7H"
```

---

## Task 3: Join a Session to its transcript

**Files:**
- Modify: `src/mnemo/core/activity/__init__.py`
- Test: `tests/unit/test_activity_join.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_activity_join.py`:

```python
"""Joining a Session to the transcript its state.json points at.

`linkScanPath` has been parsed into Session.link_scan_path since the queue
shipped (jobs.py:133) and nothing ever opened it. These tests are that open.
"""
from __future__ import annotations

import json

from mnemo.core.activity import activities_for, activity_for
from mnemo.core.sessions.jobs import Session


def _transcript(tmp_path, name, tools):
    p = tmp_path / name
    lines = []
    for tool, target_key, target in tools:
        lines.append(json.dumps({
            "type": "assistant",
            "timestamp": "2026-09-13T14:02:11.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t", "name": tool, "input": {target_key: target}},
            ]},
        }))
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def test_reads_the_transcript_named_by_link_scan_path(tmp_path):
    path = _transcript(tmp_path, "a.jsonl", [("Edit", "file_path", "/r/dispatch.py")])
    session = Session(short_id="abc", link_scan_path=path)

    act, offset = activity_for(session, 0)

    assert act.tool == "Edit"
    assert act.target == "dispatch.py"
    assert offset > 0


def test_no_link_scan_path_gives_no_activity(tmp_path):
    act, offset = activity_for(Session(short_id="abc"), 0)

    assert act is None
    assert offset == 0


def test_missing_file_gives_no_activity(tmp_path):
    session = Session(short_id="abc", link_scan_path=str(tmp_path / "gone.jsonl"))

    act, offset = activity_for(session, 0)

    assert act is None
    assert offset == 0


def test_offset_advances_and_second_read_sees_only_new_work(tmp_path):
    path = _transcript(tmp_path, "a.jsonl", [("Read", "file_path", "/r/a.py")])
    session = Session(short_id="abc", link_scan_path=path)
    first, offset = activity_for(session, 0)
    assert first.tool == "Read"

    with open(path, "a") as fh:
        fh.write(json.dumps({
            "type": "assistant", "timestamp": "2026-09-13T14:03:00.000Z",
            "message": {"role": "assistant", "content": [
                {"type": "tool_use", "id": "t2", "name": "Bash",
                 "input": {"description": "Run tests"}}]},
        }) + "\n")
    second, new_offset = activity_for(session, offset)

    assert second.tool == "Bash"
    assert second.target == "Run tests"
    assert new_offset > offset


def test_no_new_events_returns_none_and_holds_the_offset(tmp_path):
    """The caller keeps showing the previous activity; see sessions.py."""
    path = _transcript(tmp_path, "a.jsonl", [("Read", "file_path", "/r/a.py")])
    session = Session(short_id="abc", link_scan_path=path)
    _, offset = activity_for(session, 0)

    act, new_offset = activity_for(session, offset)

    assert act is None
    assert new_offset == offset


def test_activities_for_maps_by_short_id_and_threads_offsets(tmp_path):
    a = _transcript(tmp_path, "a.jsonl", [("Edit", "file_path", "/r/a.py")])
    b = _transcript(tmp_path, "b.jsonl", [("Bash", "description", "Build")])
    sessions = [
        Session(short_id="aaa", link_scan_path=a),
        Session(short_id="bbb", link_scan_path=b),
        Session(short_id="ccc"),  # no transcript at all
    ]
    offsets = {}

    acts = activities_for(sessions, offsets)

    assert acts["aaa"].target == "a.py"
    assert acts["bbb"].target == "Build"
    assert "ccc" not in acts
    assert offsets["aaa"] > 0 and offsets["bbb"] > 0
    assert "ccc" not in offsets


def test_activities_for_keeps_the_previous_activity_when_nothing_is_new(tmp_path):
    """A quiet session must not blink back to '—' on the next tick."""
    a = _transcript(tmp_path, "a.jsonl", [("Edit", "file_path", "/r/a.py")])
    sessions = [Session(short_id="aaa", link_scan_path=a)]
    offsets = {}

    first = activities_for(sessions, offsets)
    second = activities_for(sessions, offsets, previous=first)

    assert second["aaa"].target == "a.py"


def test_activities_for_tolerates_an_unreadable_transcript(tmp_path):
    sessions = [Session(short_id="aaa", link_scan_path=str(tmp_path))]  # a directory

    acts = activities_for(sessions, {})

    assert acts == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_activity_join.py -v
```

Expected: `ImportError: cannot import name 'activities_for' from 'mnemo.core.activity'`.

- [ ] **Step 3: Write the implementation**

Replace `src/mnemo/core/activity/__init__.py` entirely:

```python
"""What a background session is doing, read from its own transcript.

``state.json`` has always handed over ``linkScanPath`` — the absolute path to
the child's ``.jsonl`` — and ``jobs.py:133`` has always parsed it into
``Session.link_scan_path``. Nothing ever opened it: ``detector.py:110``
forwards it into an unblock marker and ``unblocks.py:113`` ignores it in favour
of re-resolving through ``learn()``. This module opens it.

The queue answers *is it alive* (``live``) and *does it need me* (``tempo``).
This answers *is it progressing* — the third axis, and the one that tells a
working child from a stuck one from a looping one.

Offsets are the caller's, held in memory for the life of a ``--watch`` loop.
Nothing here writes to disk: an offset only has value inside a live watch, and
between separate invocations what the maintainer wants is current state, not
the delta since yesterday.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from mnemo.core.activity.summarize import Activity, recent_actions, summarize
from mnemo.core.activity.tail import WINDOW, read_tail

__all__ = [
    "Activity",
    "WINDOW",
    "activities_for",
    "activity_for",
    "read_tail",
    "recent_actions",
    "summarize",
]


def activity_for(session, offset: int = 0) -> Tuple[Optional[Activity], int]:
    """Read *session*'s transcript from *offset*; return (activity, new offset).

    ``(None, offset)`` when the session names no transcript, the file is gone,
    or nothing new arrived. A session with no ``link_scan_path`` is normal, not
    an error: not every Claude Code version writes the field.
    """
    path = getattr(session, "link_scan_path", None)
    if not path:
        return None, offset

    events, new_offset = read_tail(path, offset)
    if not events:
        return None, new_offset

    return summarize(events), new_offset


def activities_for(
    sessions: List,
    offsets: Dict[str, int],
    previous: Optional[Dict[str, Activity]] = None,
) -> Dict[str, Activity]:
    """Activity per ``short_id``, advancing *offsets* in place.

    *previous* carries the last tick's result forward: a session that wrote
    nothing since is still doing whatever it was doing, and blinking the column
    back to em-dash would read as "stopped". Sessions with no activity at all
    are simply absent from the result.
    """
    out = {}  # type: Dict[str, Activity]
    for session in sessions:
        short_id = getattr(session, "short_id", None)
        if not short_id:
            continue
        act, new_offset = activity_for(session, offsets.get(short_id, 0))
        if new_offset:
            offsets[short_id] = new_offset
        if act is None and previous:
            act = previous.get(short_id)
        if act is not None:
            out[short_id] = act
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_activity_join.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Run the whole activity package**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_activity_tail.py tests/unit/test_activity_summarize.py tests/unit/test_activity_join.py -q
```

Expected: 42 passed.

- [ ] **Step 6: Commit**

```bash
cd /Users/xyrlan/github/mnemo
git add src/mnemo/core/activity/__init__.py tests/unit/test_activity_join.py
git commit -m "feat(activity): join a Session to the transcript it already names

state.json has always handed over linkScanPath and jobs.py:133 has always
parsed it; detector.py forwarded it into a marker and unblocks.py:113
ignored it. The pointer into the live session was write-only. This opens it.

Offsets belong to the caller and stay in memory: an offset only has value
inside a live --watch, and between invocations the maintainer wants current
state, not yesterday's delta. A tick with no new events keeps the previous
activity rather than blinking the column to em-dash.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AxLNbB2ibbEYGoAp9gUw7H"
```

---

## Task 4: Render activity in the queue (layer 1)

**Files:**
- Modify: `src/mnemo/core/sessions/render.py:81-128`
- Test: `tests/unit/test_sessions_render.py` (modify — append cases)

The `working` bucket line is today (`render.py:105`):

```python
lines.append(f"  {s.short_id}  {s.label:<22} {s.detail or '—':<34}{_tokens(s):>6}")
```

`detail` is a free-text string Claude Code happens to write. Activity is
derived and more specific, so it wins the column when present, and `detail`
remains the fallback.

- [ ] **Step 1: Confirm the existing baseline passes**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_render.py -q
```

Expected: all pass. Note the count — Step 5 must not reduce it.

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/test_sessions_render.py`:

```python
# --- activity column (layer 1) ---

from mnemo.core.activity.summarize import Activity  # noqa: E402


def _working(short_id="abc", **kw):
    from mnemo.core.sessions.jobs import Session
    kw.setdefault("tempo", "active")
    kw.setdefault("name", "child")
    return Session(short_id=short_id, **kw)


def test_render_without_activities_is_unchanged():
    """The backward-compatibility test: the old call must produce old bytes."""
    sessions = [_working(detail="building")]

    assert render_queue(sessions) == render_queue(sessions, None)
    assert render_queue(sessions, {}) == render_queue(sessions)


def test_activity_replaces_detail_in_the_working_bucket():
    sessions = [_working(detail="building")]
    acts = {"abc": Activity(tool="Edit", target="dispatch.py", since=3)}

    out = render_queue(sessions, acts)

    assert "Edit dispatch.py (+3)" in out
    assert "building" not in out


def test_detail_is_the_fallback_when_a_session_has_no_activity():
    sessions = [_working(detail="building")]

    out = render_queue(sessions, {})

    assert "building" in out


def test_zero_since_shows_no_counter():
    sessions = [_working()]
    acts = {"abc": Activity(tool="Bash", target="Run tests", since=0)}

    out = render_queue(sessions, acts)

    assert "Bash Run tests" in out
    assert "(+0)" not in out


def test_repeated_tool_is_marked():
    """The loop signal has to be visible without counting columns."""
    sessions = [_working()]
    acts = {"abc": Activity(tool="Grep", target="linkScanOffset", since=7, repeated=True)}

    out = render_queue(sessions, acts)

    assert "Grep linkScanOffset (+7)" in out
    assert "↻" in out


def test_activity_without_a_target_still_renders():
    sessions = [_working()]
    acts = {"abc": Activity(tool="AskUserQuestion", target=None, since=1)}

    out = render_queue(sessions, acts)

    assert "AskUserQuestion" in out


def test_activity_does_not_leak_into_the_waiting_bucket():
    """A blocked session's claim is `needs`; activity would bury it."""
    sessions = [_working(short_id="w", tempo="blocked", needs="qual opção?")]
    acts = {"w": Activity(tool="Edit", target="x.py", since=2)}

    out = render_queue(sessions, acts)

    assert "qual opção?" in out
    assert "Edit x.py" not in out


def test_activity_for_an_unlisted_session_is_ignored():
    sessions = [_working()]
    acts = {"someone-else": Activity(tool="Edit", target="x.py")}

    out = render_queue(sessions, acts)

    assert "x.py" not in out


def test_long_activity_does_not_break_the_column():
    sessions = [_working()]
    acts = {"abc": Activity(tool="Bash", target="x" * 40, since=99)}

    out = render_queue(sessions, acts)

    for line in out.splitlines():
        assert len(line) <= 100, line
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_render.py -q -k activity
```

Expected: `TypeError: render_queue() takes 1 positional argument but 2 were given`.

- [ ] **Step 4: Write the implementation**

In `src/mnemo/core/sessions/render.py`, add after `_prs` (line 78):

```python
def _activity(act) -> str:
    """One column of what a session is doing, or '' when nothing is known.

    ``(+N)`` is the movement signal and ``↻`` the loop signal: a count that
    rises with an unchanged target is a session going in circles, which reads
    identically to progress without the mark.
    """
    if act is None or not act.tool:
        return ""
    text = act.tool if not act.target else f"{act.tool} {act.target}"
    if act.since:
        text += f" (+{act.since})"
    if act.repeated:
        text += " ↻"
    return text
```

Then change the signature and the `working` bucket:

```python
def render_queue(sessions: list[Session], activities=None) -> str:
    """Render the whole queue, blocked first.

    *activities* maps ``short_id`` to :class:`~mnemo.core.activity.Activity`.
    Omitted, the output is byte-identical to the queue that shipped in v1.4.0 —
    the column is additive, and every caller that predates it keeps working.

    Only the working bucket uses it. A blocked session's claim on the
    maintainer is ``needs``; burying that under a tool name would invert the
    ordering the whole queue exists to provide.
    """
    if not sessions:
        return EMPTY

    acts = activities or {}
```

and replace line 105 with:

```python
            detail = _activity(acts.get(s.short_id)) or s.detail or "—"
            lines.append(f"  {s.short_id}  {s.label:<22} {detail:<34}{_tokens(s):>6}")
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_render.py -v
```

Expected: every previously-passing test still passes, plus 9 new ones.

- [ ] **Step 6: Run the full suite — render output is widely asserted**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: no new failures against the Step 1 baseline. If something in
`test_statusline.py` or a CLI test breaks, a caller is asserting on the exact
line — fix the caller only if the activities dict was passed; a plain
`render_queue(sessions)` regression means `_activity` leaked into the no-arg path.

- [ ] **Step 7: Commit**

```bash
cd /Users/xyrlan/github/mnemo
git add src/mnemo/core/sessions/render.py tests/unit/test_sessions_render.py
git commit -m "feat(sessions): show what a working session is doing

The working bucket showed \`detail\`, a free-text string Claude Code
happens to write. Activity is derived and more specific, so it takes the
column and detail stays the fallback.

(+N) is movement and ↻ is a loop: a rising count with an unchanged target
reads exactly like progress without the mark.

Additive — render_queue with no activities is byte-identical to v1.4.0,
asserted directly. The waiting bucket is untouched: a blocked session's
claim is \`needs\`, and burying it under a tool name would invert the
ordering the queue exists to provide.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AxLNbB2ibbEYGoAp9gUw7H"
```

---

## Task 5: Thread offsets through `--watch`

**Files:**
- Modify: `src/mnemo/cli/commands/sessions.py:48-95`
- Test: `tests/unit/test_sessions_command_activity.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_sessions_command_activity.py`:

```python
"""`mnemo sessions` reading activity, and `--watch` not re-reading from zero.

The offset dict is the whole point of the watch path: without it every tick
re-reads a 1.6-3.6MB transcript per child.
"""
from __future__ import annotations

import argparse
import json

import pytest

from mnemo.cli.commands.sessions import cmd_sessions


def _args(**kw):
    kw.setdefault("json", False)
    kw.setdefault("watch", False)
    kw.setdefault("all", True)
    kw.setdefault("consume_unblocks", False)
    return argparse.Namespace(**kw)


@pytest.fixture
def transcript(tmp_path):
    p = tmp_path / "child.jsonl"
    p.write_text(json.dumps({
        "type": "assistant",
        "timestamp": "2026-09-13T14:02:11.000Z",
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": "Edit",
             "input": {"file_path": "/r/dispatch.py"}}]},
    }) + "\n")
    return p


@pytest.fixture
def one_session(monkeypatch, transcript):
    from mnemo.core.sessions.jobs import Session

    session = Session(short_id="abc", tempo="active", name="child",
                      link_scan_path=str(transcript))
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [session])
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep",
                        lambda *a, **kw: None)
    return session


def test_single_invocation_shows_activity(one_session, capsys):
    assert cmd_sessions(_args()) == 0

    out = capsys.readouterr().out
    assert "Edit dispatch.py" in out


def test_json_output_is_unchanged_by_activity(one_session, capsys):
    """--json is a Session dump; activity is a render concern, not a field."""
    assert cmd_sessions(_args(json=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["short_id"] == "abc"
    assert "activity" not in payload[0]


def test_watch_does_not_reread_from_zero(one_session, transcript, monkeypatch, capsys):
    """Second tick must read only the delta. This is the measured cost fix."""
    reads = []
    real = __import__("mnemo.core.activity.tail", fromlist=["read_tail"]).read_tail

    def spy(path, offset, window=None):
        reads.append(offset)
        return real(path, offset) if window is None else real(path, offset, window)

    monkeypatch.setattr("mnemo.core.activity.read_tail", spy)

    ticks = [0]

    def fake_sleep(_):
        ticks[0] += 1
        if ticks[0] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    assert cmd_sessions(_args(watch=True)) == 0

    assert len(reads) >= 2, reads
    assert reads[0] == 0, "first tick is a cold start"
    assert reads[1] > 0, "second tick must resume from the bookmark"


def test_watch_keeps_showing_activity_when_nothing_changes(
        one_session, monkeypatch, capsys):
    """A quiet child must not blink to em-dash on the second tick."""
    ticks = [0]

    def fake_sleep(_):
        ticks[0] += 1
        if ticks[0] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", fake_sleep)

    assert cmd_sessions(_args(watch=True)) == 0

    out = capsys.readouterr().out
    assert out.count("Edit dispatch.py") >= 2, out


def test_session_without_a_transcript_still_renders(monkeypatch, capsys):
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", tempo="active",
                                              name="child", detail="building")])
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda *a, **kw: None)

    assert cmd_sessions(_args()) == 0

    assert "building" in capsys.readouterr().out


def test_activity_failure_never_breaks_the_queue(monkeypatch, capsys):
    """The queue must print even when the activity read explodes."""
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", tempo="active",
                                              name="child", detail="building")])
    monkeypatch.setattr("mnemo.core.sessions.detector.sweep", lambda *a, **kw: None)

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("mnemo.cli.commands.sessions.activities_for", boom, raising=False)

    assert cmd_sessions(_args()) == 0
    assert "building" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_command_activity.py -v
```

Expected: failures — `"Edit dispatch.py" not in out`, since nothing calls
`activities_for` yet.

- [ ] **Step 3: Write the implementation**

In `src/mnemo/cli/commands/sessions.py`, inside `cmd_sessions`, add to the
imports at line 57:

```python
    from mnemo.core.activity import activities_for
```

Then replace the render block (lines 78-95) with exactly this:

```python
    if bool(getattr(args, "json", False)):
        print(_json.dumps([asdict(s) for s in _read()], indent=2, ensure_ascii=False))
        return 0

    # In memory, for the life of this process. An offset only has value inside
    # a live watch; a single invocation wants current state, not a delta.
    #
    # `previous` is a one-element list, not a bare dict, because `_activities`
    # both reads and replaces it on every tick. A plain name would need
    # `nonlocal`; the list keeps the closure honest with one fewer keyword.
    offsets = {}
    previous = [{}]

    def _activities(found):
        """Never let a transcript read cost us the queue itself."""
        try:
            previous[0] = activities_for(found, offsets, previous=previous[0])
        except Exception:
            previous[0] = {}
        return previous[0]

    if bool(getattr(args, "watch", False)):
        # Only a terminal understands the escape; redirected to a log it would
        # be raw bytes on every redraw.
        clear = "\033[2J\033[H" if sys.stdout.isatty() else ""  # clear + home
        try:
            while True:
                found = _read()
                acts = _activities(found)
                print(clear, end="")
                print(render_queue(found, acts))
                time.sleep(2)
        except KeyboardInterrupt:
            return 0

    found = _read()
    print(render_queue(found, _activities(found)))
    return 0
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_sessions_command_activity.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Run the full suite**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: no new failures.

- [ ] **Step 6: Commit**

```bash
cd /Users/xyrlan/github/mnemo
git add src/mnemo/cli/commands/sessions.py tests/unit/test_sessions_command_activity.py
git commit -m "feat(sessions): thread transcript offsets through --watch

Each tick resumes from the previous bookmark instead of re-reading the
file. Worktree children measured 1.6-3.6MB, so a stateless watch would
parse ~10MB of JSON every 2s for four children to draw four lines.

Offsets stay in memory and die with the process. A tick that reads nothing
new carries the previous activity forward, so a quiet child does not blink
to em-dash and read as stopped.

Activity failures are swallowed: the queue must print even when a
transcript moved, and --json stays a plain Session dump.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AxLNbB2ibbEYGoAp9gUw7H"
```

---

## Task 6: `mnemo session <short_id>` (layer 2)

**Files:**
- Create: `src/mnemo/cli/commands/session.py`
- Modify: `src/mnemo/cli/parser.py:77-86` (after the `sessions` block)
- Modify: `src/mnemo/cli/commands/__init__.py:28-33` (the registration tuple)
- Test: `tests/unit/test_session_command.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/test_session_command.py`:

```python
"""`mnemo session <short_id>` — the detail view.

Layer 2: you look, you decide, you go to `claude attach` if you want in.
No --follow in this version.
"""
from __future__ import annotations

import argparse
import json

import pytest

from mnemo.cli.commands.session import cmd_session


def _args(short_id="abc", limit=15):
    return argparse.Namespace(short_id=short_id, limit=limit)


def _event(tool, key, value, ts="2026-09-13T14:02:11.000Z"):
    return json.dumps({
        "type": "assistant",
        "timestamp": ts,
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t", "name": tool, "input": {key: value}}]},
    })


@pytest.fixture
def session_with_actions(monkeypatch, tmp_path):
    from mnemo.core.sessions.jobs import Session

    p = tmp_path / "child.jsonl"
    p.write_text("\n".join([
        _event("Grep", "pattern", "linkScanOffset", "2026-09-13T14:02:11.000Z"),
        _event("Read", "file_path", "/r/detector.py", "2026-09-13T14:02:19.000Z"),
        _event("Edit", "file_path", "/r/detector.py", "2026-09-13T14:03:02.000Z"),
        _event("Bash", "description", "Run tests", "2026-09-13T14:03:40.000Z"),
        _event("Grep", "pattern", "linkScanOffset", "2026-09-13T14:04:15.000Z"),
    ]) + "\n")

    session = Session(short_id="abc", tempo="active", state="active", name="measure-edges",
                      cwd="/Users/x/github/mnemo-wt-203", live=True,
                      link_scan_path=str(p))
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [session])
    return session


def test_lists_actions_oldest_first(session_with_actions, capsys):
    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert out.index("Grep") < out.index("Read") < out.index("Bash")


def test_shows_the_header_with_label_and_cwd(session_with_actions, capsys):
    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert "measure-edges" in out
    assert "mnemo-wt-203" in out


def test_shows_tool_and_target_per_action(session_with_actions, capsys):
    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert "detector.py" in out
    assert "Run tests" in out


def test_marks_a_repeated_action(session_with_actions, capsys):
    """The same grep twice is the loop signal, and it is why you look here."""
    assert cmd_session(_args()) == 0

    assert "↻" in capsys.readouterr().out


def test_limit_is_respected(session_with_actions, capsys):
    assert cmd_session(_args(limit=2)) == 0

    out = capsys.readouterr().out
    assert "Bash" in out, "the newest actions are the ones kept"
    assert "Read" not in out


def test_unknown_short_id_reports_and_fails(monkeypatch, capsys):
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions", lambda **kw: [])

    assert cmd_session(_args(short_id="nope")) == 1

    assert "nope" in capsys.readouterr().out


def test_session_without_a_transcript_says_so(monkeypatch, capsys):
    from mnemo.core.sessions.jobs import Session

    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", name="child")])

    assert cmd_session(_args()) == 0

    out = capsys.readouterr().out
    assert "abc" in out
    assert "transcript" in out.lower()


def test_transcript_with_no_tool_use_says_so(monkeypatch, tmp_path, capsys):
    from mnemo.core.sessions.jobs import Session

    p = tmp_path / "c.jsonl"
    p.write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-09-13T14:00:00.000Z",
        "message": {"role": "assistant", "content": [
            {"type": "text", "text": "thinking about it"}]},
    }) + "\n")
    monkeypatch.setattr("mnemo.core.sessions.jobs.read_sessions",
                        lambda **kw: [Session(short_id="abc", name="child",
                                              link_scan_path=str(p))])

    assert cmd_session(_args()) == 0

    assert "nenhuma ação" in capsys.readouterr().out


def test_prefix_match_on_short_id(session_with_actions, capsys):
    """Typing four characters of a short id is enough."""
    assert cmd_session(_args(short_id="ab")) == 0

    assert "measure-edges" in capsys.readouterr().out
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_session_command.py -v
```

Expected: `ModuleNotFoundError: No module named 'mnemo.cli.commands.session'`.

- [ ] **Step 3: Write the command**

Create `src/mnemo/cli/commands/session.py`:

```python
"""``mnemo session <short_id>`` — what one background session has been doing.

Layer 2 of the activity view. The queue gives one line per session so the
maintainer can scan; this gives the last N actions of one session, for when
that line looks wrong and the question becomes "wrong how".

No ``--follow``. You look, you decide, and you go to ``claude attach`` if you
want to be inside it — attach is Claude Code's job, not this one's.

Human-only, like the queue: no hook and no MCP tool exposes it. The parent
session's context is the scarce resource the whole feature protects.
"""
from __future__ import annotations

import argparse

from mnemo.cli.parser import command

NO_ACTIONS = "  nenhuma ação registrada na janela lida"


def _find(sessions, short_id: str):
    """Exact match first, then a unique prefix. None when neither resolves."""
    for s in sessions:
        if s.short_id == short_id:
            return s
    matches = [s for s in sessions if s.short_id.startswith(short_id)]
    return matches[0] if len(matches) == 1 else None


def _clock(at):
    """``14:02:11`` from an ISO timestamp, or '' when it is unusable."""
    if not isinstance(at, str) or "T" not in at:
        return ""
    return at.split("T", 1)[1].split(".")[0].replace("Z", "")[:8]


@command("session")
def cmd_session(args: argparse.Namespace) -> int:
    """Print the recent actions of one background session."""
    from mnemo.core.activity import read_tail, recent_actions
    from mnemo.core.sessions.jobs import read_sessions

    short_id = str(getattr(args, "short_id", "") or "")
    limit = int(getattr(args, "limit", 15) or 15)

    session = _find(read_sessions(cwd=None), short_id)
    if session is None:
        print(f"sessão não encontrada: {short_id}")
        print("  listar: mnemo sessions --all")
        return 1

    flags = [f for f in (session.state, session.tempo) if f]
    if session.live is True:
        flags.append("live")
    elif session.live is False:
        flags.append("morta")
    print(f"{session.short_id}  {session.label}  {' · '.join(flags)}")
    if session.cwd:
        print(session.cwd)
    print("")

    if not session.link_scan_path:
        print("  esta sessão não registrou um transcript (linkScanPath ausente)")
        return 0

    events, _ = read_tail(session.link_scan_path, 0)
    actions = recent_actions(events, limit=limit)

    if not actions:
        print(NO_ACTIONS)
        return 0

    for act in actions:
        mark = "  ↻" if act.repeated else ""
        print(f"  {_clock(act.at):>8}  {act.tool or '?':<12}  {act.target or '':<40}{mark}")

    print("")
    print(f"  attach: claude attach {session.short_id}")
    return 0
```

- [ ] **Step 4: Register the subparser**

In `src/mnemo/cli/parser.py`, after the `sessions` block (line 86, right before
`dispatch_p = sub.add_parser(`):

```python
    session_p = sub.add_parser(
        "session", help="recent actions of one background session")
    session_p.add_argument("short_id", metavar="SHORT_ID",
                           help="session short id (a unique prefix is enough)")
    session_p.add_argument("--limit", type=int, default=15,
                           help="how many recent actions to show (default: 15)")
```

Then register the module so the `@command` decorator runs. Commands are
imported as one alphabetical tuple in `src/mnemo/cli/commands/__init__.py:11`
(`mnemo/cli/__init__.py:20` imports that package to trigger registration).

The block currently reads, at lines 28-33:

```python
    recall,
    recall_sessions,
    reclassify,
    regen_graph_edges,
    rewrites,
    sessions,
```

Insert `session,` before `sessions,` — alphabetical, and the singular sorts
first:

```python
    recall,
    recall_sessions,
    reclassify,
    regen_graph_edges,
    rewrites,
    session,
    sessions,
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/unit/test_session_command.py -v
```

Expected: 9 passed.

- [ ] **Step 6: Verify the command is wired end to end**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m mnemo session --help
```

Expected: usage for `mnemo session` showing `SHORT_ID` and `--limit`. A
`invalid choice: 'session'` means Step 4's import is missing.

- [ ] **Step 7: Run the full suite**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: no new failures. `tests/unit/test_cli_parser.py` may assert the set
of subcommands — if it fails, add `session` to its expected list.

- [ ] **Step 8: Commit**

```bash
cd /Users/xyrlan/github/mnemo
git add src/mnemo/cli/commands/session.py src/mnemo/cli/commands/__init__.py src/mnemo/cli/parser.py tests/unit/test_session_command.py
git commit -m "feat(sessions): add \`mnemo session <id>\` for one session's recent actions

The queue gives one line per session so the maintainer can scan. This gives
the last N actions of one, for when that line looks wrong and the question
becomes 'wrong how' — a repeated grep four times over is the case it exists
to show.

No --follow: you look, you decide, you go to \`claude attach\` if you want
to be inside it. Attach is Claude Code's job.

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AxLNbB2ibbEYGoAp9gUw7H"
```

---

## Task 7: Verify on real sessions, then document

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `README.md` (the sessions section, if one exists)

- [ ] **Step 1: Check py3.8 compatibility of every new file**

The one failure mode local tests cannot catch (PR #209). Grep the new code for
runtime-evaluated 3.10+ syntax:

```bash
cd /Users/xyrlan/github/mnemo
grep -n '^[A-Za-z_]* *[:=].*\[.*|' src/mnemo/core/activity/*.py src/mnemo/cli/commands/session.py
grep -n 'cast(\|TypeAlias\|: *dict\[\|: *list\[\|: *tuple\[' src/mnemo/core/activity/*.py src/mnemo/cli/commands/session.py
```

Expected: no module-level alias using `X | Y` or a bare `dict[...]`. Matches
inside a `def` signature are fine (`from __future__ import annotations` covers
them). Anything at module level must use `typing.Dict`/`List`/`Tuple`.

- [ ] **Step 2: Verify against a real live session**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m mnemo sessions --all
```

Expected: the queue prints. Any session in TRABALHANDO with a transcript should
show a tool and target rather than its old `detail`. If every row shows `—`,
either no session has `linkScanPath` in its `state.json` (check with the next
command) or the join is broken.

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -c "
from mnemo.core.sessions.jobs import read_sessions
for s in read_sessions(cwd=None):
    print(s.short_id, '|', s.label, '|', 'path:', bool(s.link_scan_path))
"
```

- [ ] **Step 3: Verify layer 2 on a real session**

Pick a `short_id` from Step 2 that has `path: True`:

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m mnemo session <short_id>
```

Expected: a header, then up to 15 timestamped actions. If actions appear with
empty target columns for common tools, `_TARGET_KEYS` in `summarize.py` needs
that tool added.

- [ ] **Step 4: Time the watch path**

The measured cost was the reason for the bookmark; confirm it paid off.

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -c "
import time
from mnemo.core.activity import activities_for
from mnemo.core.sessions.jobs import read_sessions

found = read_sessions(cwd=None)
offsets, prev = {}, {}
t0 = time.time(); prev = activities_for(found, offsets, previous=prev); cold = time.time() - t0
t0 = time.time(); activities_for(found, offsets, previous=prev); warm = time.time() - t0
print('sessions: %d' % len(found))
print('cold tick: %.1f ms' % (cold * 1000))
print('warm tick: %.1f ms' % (warm * 1000))
print('bytes bookmarked: %d' % sum(offsets.values()))
"
```

Expected: the warm tick is markedly cheaper than the cold one. A warm tick as
slow as cold means offsets are not being retained.

- [ ] **Step 5: Update the CHANGELOG**

Add under the unreleased heading (match the file's existing style):

```markdown
### Added

- `mnemo sessions` now shows what each working session is *doing* — the last
  tool, its target, a `(+N)` count of tool uses since, and `↻` when the same
  tool and target repeat. A session stuck at `active` is now distinguishable
  from one making progress and one going in circles.
- `mnemo session <short_id>` lists that session's recent actions (default 15,
  `--limit` to change).

Read from `linkScanPath`, which `state.json` has always provided and nothing
ever opened. Transcript offsets are held in memory for the life of a `--watch`
loop, so each tick reads only new bytes — worktree children measured
1.6–3.6 MB, and a stateless watch would parse ~10 MB every 2 s for four
children.
```

- [ ] **Step 6: Run the full suite one last time**

```bash
cd /Users/xyrlan/github/mnemo && PYTHONPATH=src python3 -m pytest tests/ -q 2>&1 | tail -5
```

Expected: all pass. Record the count in the commit body.

- [ ] **Step 7: Commit**

```bash
cd /Users/xyrlan/github/mnemo
git add CHANGELOG.md
git commit -m "docs(changelog): record the session activity view

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01AxLNbB2ibbEYGoAp9gUw7H"
```

---

## Out of scope

From the spec, restated so execution does not drift:

- `--follow` on `mnemo session`
- Persisting offsets to disk, or reviving `linkScanOffset`
- A dispatch plan artifact
- Anything about liveness or #196
- A TUI framework — `--watch` stays `print("\033[2J\033[H")` + `sleep(2)`

## Known risk — discharged 2026-09-13

The original text here said no `mnemo dispatch` child had ever left a
transcript, so the first real dispatch run would be the true test. **That was
wrong**, from grepping `~/.claude/projects` for `-wt-` before accounting for
the dash-encoding of the path. Nine real dispatch-child transcripts exist
(`-Users-xyrlan-github-mnemo-wt-{158,176×2,187,193,195,196,197,200}`), p50
1.0 MB, max 1.4 MB — smaller than the native-worktree proxy (~2.5 MB) but
still 3x the global median, so the design conclusion is unchanged.

Verified through Tasks 1+2: **9/9 produce a readable activity**, and
`recent_actions` on #197 renders its real closing sequence. Every one of them
mentions `linkScanPath`, so the field the whole design rests on is present in
practice.

**The live path is now covered too.** `~/.claude/jobs/` holds only `pins.json`
(no session running), so instead of waiting for one, the path was driven
through `read_sessions(root=<tmp>)` with a `state.json` written in the real
camelCase shape (`state`, `tempo`, `name`, `cwd`, `tokens`, `sessionId`,
`linkScanPath`, `updatedAt`) pointing at the real #197 dispatch transcript.
Result: the session parsed, `link_scan_path` came through, `label` recovered
`#197` from `cwd` via `issue_for_cwd`, and `render_queue` printed
`Bash Commit (+20)`. What is still untested is only the timing of a genuinely
concurrent writer, which no fixture can stand in for.

## Finding: the label column is unbudgeted (pre-existing, out of scope)

Driving real data through the renderer exposed a layout bug that every fixture
in this plan hides, because the fixtures use short names like `child`.

Real labels run 30–34 characters (`"#197 dispatch a feature's pieces"` = 32,
`"#203 measure unblock edge coverage"` = 34) against a `{s.label:<22}` field.
`jobs.py` caps `label` at 40 chars, so the column can overflow by up to 18 —
and the overflow shoves whatever follows, which is why the verification output
read `pieces Bash Commit (+20)` with no column break.

**This predates this branch**: `git show 3e2851b:src/mnemo/core/sessions/render.py`
has the same unbudgeted `{s.label:<22}` in all four buckets, already shoving
`detail`. The activity column did not cause it; it made it visible, because an
activity string is longer and more structured than the `—` that used to sit
there.

Deliberately **not fixed here**. Budgeting `label` touches all four buckets and
the waiting bucket's whole job is to be readable, so it deserves its own change
with its own before/after on real data — not a drive-by inside an activity
feature. Worth filing.
