"""What a dispatched child spends before its first edit (#269).

The classifier cases below are not invented shapes. Most "not a mutation" cases
are ones a first version of the classifier got wrong on the real dispatch
transcripts of 2026-09-14 — 11 of its 26 Bash "first mutations" were one of
them — and most "mutation" cases are how a real child actually changed its
tree: 15 of 50 did it through ``Bash``, 4 without ever calling ``Edit``.
"""
from __future__ import annotations

import json

import pytest

from mnemo.core.activity import exploration_for
from mnemo.core.activity.exploration import (
    Exploration,
    bash_mutates,
    is_mutation,
    measure,
    total,
)

CWD = "/Users/x/github/mnemo-wt-269"


# --- Bash: how children really changed the tree ------------------------------

@pytest.mark.parametrize("command", [
    # #247 never called Edit: every change went through a python heredoc.
    "python3 - <<'PYEOF'\nimport pathlib\np = pathlib.Path(\"src/mnemo/hooks/session_end.py\")\n"
    "s = p.read_text(encoding=\"utf-8\")\np.write_text(s.replace('a', 'b'), encoding=\"utf-8\")\nPYEOF",
    # #176, #244, #257: append tests with cat.
    "cat >> tests/unit/test_corrections.py <<'EOF'\n\ndef test_x():\n    assert 1 > 0\nEOF",
    # #243: rewrite a file wholesale.
    "cat > README.md <<'EOF'\n# mnemo\nEOF",
    # #233: create a package directory and move into it.
    "mkdir -p src/mnemo/skills/x && git mv skills/x/SKILL.md src/mnemo/skills/x/SKILL.md",
    # clubinho #191: copy a probe spec into the tree to run it there.
    "cp /Users/x/.claude/jobs/fd7ed8c6/tmp/probe.spec.ts /Users/x/github/mnemo-wt-269/backend/test/probe.spec.ts",
    "sed -i '' 's/old/new/' src/mnemo/core/x.py",
    "git commit -qm 'fix'",
    "git stash",
    "echo hi | tee notes.txt",
    "echo hi > /Users/x/github/mnemo-wt-269/out.txt",
    "PYTHONPATH=src python3 -c 'open(\"x.txt\", \"w\").write(\"1\")'",
    "node -e 'require(\"fs\").writeFileSync(\"a.json\", \"{}\")'",
    "rm src/mnemo/core/old.py",
])
def test_a_tree_write_is_a_mutation(command) -> None:
    assert bash_mutates(command, CWD) is True


@pytest.mark.parametrize("command", [
    # A probe written to the job's tmp: the heredoc body is file *content*, and
    # its `.write_text(` must not read as code being run.
    "mkdir -p \"$CLAUDE_JOB_DIR/tmp\" && cat > \"$CLAUDE_JOB_DIR/tmp/probe.py\" <<'PY'\n"
    "from pathlib import Path\nPath('c.md').write_text('x')\nPY",
    # #273 auditing for bare write_text calls: a grep pattern is not a write.
    "grep -rn --include='*.py' '\\.write_text(' src tools | grep -vc encoding",
    # #193 / #247: `str.replace(` is not `Path.replace(`.
    "PYTHONPATH=src python3 -c \"names = {n.replace(chr(39)*2, chr(39)) for n in x}\"",
    # #200: a variable pointing at the job's tmp.
    "T=\"$CLAUDE_JOB_DIR/tmp\"; : > \"$T/win.txt\"",
    # #270: a scratch repo in mktemp, then work inside it.
    "T=$(mktemp -d); cd $T && git init -q . && echo hi > README.md && git add . && git commit -qm init",
    # #271: an absolute scratch dir held in a variable, then cd'd into.
    "T=/Users/x/.claude/jobs/1f87be87/tmp/probe && mkdir -p $T && cd $T && echo hello > target.txt",
    # #270: a redirect's target is not a second argument to mkdir.
    "mkdir -p /Users/x/.claude/jobs/../../.claude/jobs 2>/dev/null",
    # Reading the main checkout is not changing this one.
    "cd ~/github/mnemo && git checkout master",
    "git -C /Users/x/github/mnemo commit -m x",
    "git checkout -b fix/issue-269",
    "git stash list",
    "ls 2>&1 | head",
    "echo hi >/dev/null",
    "grep 'a > b' x.py",
    "sed -n 1,40p src/mnemo/core/x.py",
    "python3 - \"$P\" <<'EOF'\nimport json,sys\nprint(len(open(sys.argv[1]).read()) > 0)\nEOF",
    "cp src/a.py /tmp/a.py",
    "",
])
def test_reading_or_writing_elsewhere_is_not(command) -> None:
    assert bash_mutates(command, CWD) is False


def test_a_path_that_climbs_out_of_the_tree_is_outside() -> None:
    assert bash_mutates(f"touch {CWD}/../mnemo/x", CWD) is False
    assert bash_mutates(f"touch {CWD}/sub/../x", CWD) is True


# --- file tools ---------------------------------------------------------------

def test_edit_inside_the_tree_is_a_mutation() -> None:
    assert is_mutation("Edit", {"file_path": f"{CWD}/src/x.py"}, CWD)
    assert is_mutation("Write", {"file_path": "relative/x.py"}, CWD)


def test_a_memory_note_is_not_a_tree_edit() -> None:
    """Five children's first ``Write`` was a memory note or scratch file (#176, #243, #248, #257)."""
    memory = "/Users/x/.claude/projects/-Users-x-github-mnemo/memory/note.md"
    assert not is_mutation("Write", {"file_path": memory}, CWD)


def test_read_tools_are_never_mutations() -> None:
    assert not is_mutation("Read", {"file_path": f"{CWD}/x.py"}, CWD)
    assert not is_mutation("Grep", {"pattern": "x"}, CWD)
    assert not is_mutation("Bash", "not a dict", CWD)


# --- measuring a transcript ---------------------------------------------------

def _turn(*tools, context=None, sidechain=False):
    usage = None
    if context is not None:
        usage = {"input_tokens": 2, "cache_read_input_tokens": context - 2,
                 "cache_creation_input_tokens": 0}
    message = {"role": "assistant", "content": [
        {"type": "tool_use", "id": f"t{i}", "name": name, "input": input_}
        for i, (name, input_) in enumerate(tools)
    ]}
    if usage:
        message["usage"] = usage
    event = {"type": "assistant", "cwd": CWD, "message": message}
    if sidechain:
        event["isSidechain"] = True
    return event


def _reflex(*slugs):
    text = "mnemo reflex context:\n" + "\n".join(f"• [[{s}]]: preview (call read_mnemo_rule…)." for s in slugs)
    return {"type": "attachment", "attachment": {"type": "hook_additional_context", "content": [text]}}


READ = ("Read", {"file_path": f"{CWD}/a.py"})
GREP = ("Grep", {"pattern": "x"})
EDIT = ("Edit", {"file_path": f"{CWD}/a.py"})


def test_counts_uses_and_context_growth_up_to_the_first_edit() -> None:
    events = [
        _reflex("mnemo__a", "mnemo__b"),
        _turn(READ, context=70_000),
        _turn(GREP, READ, context=82_000),
        _turn(EDIT, context=95_000),
        _turn(READ, READ, READ, context=120_000),  # after: not exploration
    ]

    found = measure(events)

    assert found == Exploration(uses=3, tokens=25_000, baseline=70_000, reached=True,
                                tool="Edit", target="a.py", injected=2)


def test_uses_in_the_same_turn_before_the_edit_still_count() -> None:
    found = measure([_turn(READ, GREP, EDIT, context=70_000)])
    assert (found.uses, found.tokens, found.reached) == (2, 0, True)


def test_no_edit_is_a_floor_not_a_number() -> None:
    found = measure([_turn(READ, context=70_000), _turn(GREP, context=90_000)])
    assert (found.uses, found.tokens, found.reached, found.tool) == (2, 20_000, False, None)


def test_a_later_prompts_injection_is_not_at_start() -> None:
    found = measure([_turn(READ, context=70_000), _reflex("mnemo__late"), _turn(EDIT, context=71_000)])
    assert found.injected is None


def test_subagent_turns_are_not_the_childs_own() -> None:
    events = [_turn(READ, context=70_000), _turn(EDIT, context=10, sidechain=True),
              _turn(GREP, READ, sidechain=True), _turn(EDIT, context=80_000)]
    found = measure(events)
    assert (found.uses, found.tokens) == (1, 10_000)


def test_cwd_falls_back_to_the_transcript() -> None:
    outside = ("Write", {"file_path": "/elsewhere/a.py"})
    found = measure([_turn(outside, context=5), _turn(EDIT, context=9)])
    assert (found.uses, found.reached) == (1, True)


def test_reader_stops_at_the_first_edit_and_ignores_a_half_written_line(tmp_path) -> None:
    path = tmp_path / "child.jsonl"
    lines = [json.dumps(e) for e in (_turn(READ, context=100), _turn(EDIT, context=150))]
    path.write_text("\n".join(lines) + "\nnot json at all\n" + '{"type": "assist', encoding="utf-8")

    assert exploration_for(str(path), CWD) == Exploration(
        uses=1, tokens=50, baseline=100, reached=True, tool="Edit", target="a.py")


def test_reader_answers_none_without_a_transcript(tmp_path) -> None:
    assert exploration_for(None) is None
    assert exploration_for(str(tmp_path / "gone.jsonl")) is None


def test_total_sums_what_was_measured_and_skips_what_was_not() -> None:
    items = [Exploration(uses=3, tokens=10), None, Exploration(uses=4, tokens=5)]
    assert total(items) == (2, 7, 15)


def test_a_meter_is_final_once_it_sees_the_first_edit() -> None:
    """The reader relies on ``feed`` returning True; a caller that keeps feeding
    must still get the number as of the first edit, not a later one."""
    from mnemo.core.activity.exploration import Meter

    meter = Meter(CWD)
    assert meter.feed(_turn(READ, context=100)) is False
    assert meter.feed(_turn(EDIT, context=150)) is True
    assert meter.feed(_turn(READ, GREP, context=900)) is True

    assert (meter.result.uses, meter.result.tokens) == (1, 50)
