"""``tools/measure_dispatch_reports.py``: the #306 count, on transcripts shaped
like Claude Code's (``type``/``message.content`` blocks, a human prompt as a
plain string, a tool result as a ``tool_result`` block)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_dispatch_reports as tool  # noqa: E402

FOOTER = "#197  a1b2c3d4  /x/app-wt-197\n\n  profile: lean\n  queue:  mnemo sessions\n  attach: claude attach a1b2c3d4\n"


def _human(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}, "timestamp": "2026-09-15T10:00:00Z"}


def _call(uid: str, command: str, name: str = "Bash", **extra) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": uid, "name": name, "input": {"command": command, **extra}}]}}


def _result(uid: str, output: str, ts: str = "2026-09-15T10:01:00Z") -> dict:
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": uid, "content": output}]}}


def _say(text: str) -> dict:
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _write(tmp_path: Path, project: str, records: list) -> None:
    d = tmp_path / project
    d.mkdir(exist_ok=True)
    (d / f"{len(list(d.iterdir()))}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_a_repeated_hint_with_nothing_armed_is_unbacked(tmp_path: Path) -> None:
    _write(tmp_path, "-a-app", [
        _human("dispatch 197"),
        _call("t1", "mnemo dispatch 197 2>&1 | tail -25"),
        _result("t1", FOOTER),
        _say("Três sessões rodando. Acompanho com `mnemo sessions`; te aviso quando terminarem."),
    ])

    report = tool.measure(str(tmp_path))

    assert report["before_note"] == {"reports": 1, "mentions": 1, "unbacked": 1}


def test_a_watcher_or_a_queue_run_backs_the_mention(tmp_path: Path) -> None:
    _write(tmp_path, "-a-app", [
        _human("go"),
        _call("t1", "PYTHONPATH=src python3 -m mnemo dispatch 197"),
        _result("t1", FOOTER),
        _call("m1", "until mnemo sessions --json | grep done; do sleep 30; done", name="Monitor"),
        _say("Monitor armed on `mnemo sessions`."),
    ])
    _write(tmp_path, "-a-app", [
        _human("go"),
        _call("t1", "mnemo dispatch 197"),
        _result("t1", FOOTER),
        _call("q1", "mnemo sessions"),
        _result("q1", "no background sessions"),
        _say("`mnemo sessions` is empty right after spawn."),
    ])

    report = tool.measure(str(tmp_path))

    assert report["before_note"] == {"reports": 2, "mentions": 2, "unbacked": 0}


def test_only_a_real_dispatch_counts(tmp_path: Path) -> None:
    """Reading ``dispatch.py`` prints the footer's source; a dry run and a
    refusal print no footer. None of them is a dispatch report."""
    _write(tmp_path, "-a-mnemo", [
        _human("look"),
        _call("c1", "cat src/mnemo/cli/commands/dispatch.py"),
        _result("c1", '        print("  queue:  mnemo sessions")\n'),
        _call("d1", "mnemo dispatch 197 --dry-run"),
        _result("d1", "#197  fix/issue-197  /x/app-wt-197\n"),
        _say("the footer says `mnemo sessions`"),
    ])

    assert tool.measure(str(tmp_path))["before_note"]["reports"] == 0


def test_the_report_ends_at_the_next_human_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "-a-app", [
        _human("go"),
        _call("t1", "mnemo dispatch 197"),
        _result("t1", FOOTER),
        _say("Dispatched `a1b2c3d4`."),
        _human("how do I see them?"),
        _say("Run `mnemo sessions`."),
    ])

    assert tool.measure(str(tmp_path))["before_note"] == {"reports": 1, "mentions": 0, "unbacked": 0}


def test_reports_that_saw_the_note_are_counted_apart(tmp_path: Path) -> None:
    """The before/after split reads the output itself, so it needs no date."""
    from mnemo.cli.commands.dispatch import AGENT_NOTE

    _write(tmp_path, "-a-app", [
        _human("go"),
        _call("t1", "mnemo dispatch 197"),
        _result("t1", FOOTER + "\n" + "\n".join(AGENT_NOTE) + "\n"),
        _say("Dispatched `a1b2c3d4`; you can follow it with `mnemo sessions`."),
    ])

    report = tool.measure(str(tmp_path))

    assert report["before_note"]["reports"] == 0
    assert report["with_note"] == {"reports": 1, "mentions": 1, "unbacked": 1}


def test_the_note_that_names_the_finish_notice_also_dates_a_report() -> None:
    """#426 reworded the note for `notifyParent: true`; both wordings mean the
    session saw a note, so the #306 split must read either."""
    from mnemo.cli.commands.dispatch import AGENT_NOTE_NOTIFIED

    assert tool.NOTE_NOTIFIED in " ".join(line.strip() for line in AGENT_NOTE_NOTIFIED)
