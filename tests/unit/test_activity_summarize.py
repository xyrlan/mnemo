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


# --- extra target keys added after the Step 6 real-data pass ---
#
# Not part of the plan's original spec: Step 6 (target-quality check against
# every real worktree transcript) found these tools at high enough volume,
# with an unambiguous single scalar key, to be worth adding. Pinned here so
# they don't silently regress.

def test_extra_observed_tools_find_a_target():
    cases = [
        ("EnterWorktree", {"path": "/Users/x/github/repo/.claude/worktrees/cavebot-andares"},
         "cavebot-andares"),
        ("mcp__claude-in-chrome__computer", {"action": "left_click", "coordinate": [1, 2]},
         "left_click"),
        ("mcp__claude-in-chrome__navigate", {"url": "http://localhost:3000/dashboard"},
         "http://localhost:3000/dashboard"),
        ("mcp__mnemo__list_rules_by_topic", {"topic": "automation", "query": "..."}, "automation"),
        ("Monitor", {"command": "...", "description": "checks do PR 59"}, "checks do PR 59"),
        ("TaskCreate", {"subject": "Fix P0: register error handling", "description": "..."},
         "Fix P0: register error handling"),
    ]
    for tool, input_, expected in cases:
        assert summarize([_assistant(tool, input_)]).target == expected, tool
