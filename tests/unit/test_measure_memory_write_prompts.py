"""``tools/measure_memory_write_prompts.py`` over synthetic transcripts (#637).

The count #637 was filed on (every memory write spelled through an account
dir waited for a person, none spelled through ``~/.claude`` did) and the one
that says the fix works (an account-spelled write that was rewritten and not
prompted) are both read by this tool, so the pairing and the arithmetic are
pinned here. Transcript paths are data, not files: they are spelled the way
Claude Code records them, and the tool is given a projects tree in ``tmp_path``.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_memory_write_prompts.py"
_spec = importlib.util.spec_from_file_location("measure_memory_write_prompts", _TOOL)
mmw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mmw)

ACCOUNT = "/Users/you/.claude-work/projects/-proj/memory/MEMORY.md"
DEFAULT = "/Users/you/.claude/projects/-proj/memory/MEMORY.md"
PROMPTED = {"decision": "accept", "source": "user_temporary"}
CLASSIFIER = {"decision": "accept", "source": "config", "reasonType": "classifier"}
CONFIG = {"decision": "accept", "source": "config", "reasonType": "other"}


def _use(call_id, file_path, *, tool="Write", ts="2026-10-13T10:00:00.000Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [
        {"type": "tool_use", "id": call_id, "name": tool,
         "input": {"file_path": file_path, "content": "x"}}]}}


def _result(call_id, decision=None, *, ts="2026-10-13T10:00:02.000Z", applied=None):
    row = {"type": "user", "timestamp": ts, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}]}}
    if decision is not None:
        row["permissionDecision"] = decision
    if applied is not None:
        row["toolUseResult"] = {"type": "update", "filePath": applied}
    return row


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# --- which paths are memory writes, and under which spelling -------------------

@pytest.mark.parametrize("path,expected", [
    (ACCOUNT, "account"),
    (DEFAULT, "default"),
    ("C:\\Users\\you\\.claude-work\\projects\\-proj\\memory\\a.md", "account"),
    ("C:\\Users\\you\\.claude\\projects\\-proj\\memory\\a.md", "default"),
    ("/Users/you/code/projects/app/memory/notes.md", "other"),
    ("/Users/you/.claude/projects/-proj/abc.jsonl", None),
    ("/Users/you/code/memory/a.md", None),
    ("/Users/you/.claude/projects/memory/a.md", None),
    (None, None),
])
def test_spelling_reads_the_config_dir_above_projects(path, expected):
    assert mmw.spelling(path) == expected


def test_only_the_tools_that_write_a_file_are_counted():
    rows = [_use("a", ACCOUNT, tool="Read"), _result("a", CONFIG),
            _use("b", ACCOUNT, tool="Edit"), _result("b", PROMPTED),
            _use("c", ACCOUNT, tool="MultiEdit"), _result("c", PROMPTED),
            _use("d", "/Users/you/code/app.py"), _result("d", CONFIG)]
    assert [w["tool"] for w in mmw.memory_writes(rows)] == ["Edit", "MultiEdit"]


# --- the pair: decision, wait, rewritten --------------------------------------

def test_a_write_is_paired_with_its_result_row_by_id():
    rows = [_use("a", ACCOUNT, ts="2026-10-13T10:00:00.000Z"),
            _use("b", DEFAULT, ts="2026-10-13T10:00:01.000Z"),
            _result("b", CONFIG, ts="2026-10-13T10:00:01.500Z"),
            _result("a", PROMPTED, ts="2026-10-13T16:00:00.000Z")]
    first, second = mmw.memory_writes(rows)
    assert (first["spelling"], first["decision"], first["wait_s"]) == ("account", PROMPTED, 21600.0)
    assert (second["spelling"], second["decision"], second["wait_s"]) == ("default", CONFIG, 0.5)


def test_a_call_with_no_result_and_a_result_with_no_decision_are_told_apart():
    rows = [_use("a", ACCOUNT), _use("b", ACCOUNT), _result("b")]
    waiting, unrecorded = mmw.memory_writes(rows)
    assert mmw.decision_label(waiting["decision"]) == ("no result", "-", "-")
    assert waiting["wait_s"] is None
    assert mmw.decision_label(unrecorded["decision"]) == ("not recorded", "-", "-")
    assert unrecorded["wait_s"] == 2.0


def test_rewritten_is_a_result_that_names_another_path():
    rows = [_use("a", ACCOUNT), _result("a", CLASSIFIER, applied=DEFAULT),
            _use("b", ACCOUNT), _result("b", PROMPTED, applied=ACCOUNT),
            _use("c", ACCOUNT), _result("c", PROMPTED)]
    assert [w["rewritten"] for w in mmw.memory_writes(rows)] == [True, False, False]


def test_separators_alone_do_not_make_a_rewrite():
    asked = "C:\\Users\\you\\.claude\\projects\\-proj\\memory\\a.md"
    rows = [_use("a", asked), _result("a", CONFIG, applied=asked.replace("\\", "/"))]
    assert mmw.memory_writes(rows)[0]["rewritten"] is False


# --- the report ---------------------------------------------------------------

def test_summarize_counts_by_spelling_source_and_reason_type():
    rows = []
    for i, wait in enumerate((10, 20, 30, 21600)):
        rows += [_use("p%d" % i, ACCOUNT, ts="2026-10-13T00:00:00.000Z"),
                 _result("p%d" % i, PROMPTED,
                         ts=(mmw._when("2026-10-13T00:00:00.000Z")
                             + mmw.timedelta(seconds=wait)).isoformat())]
    rows += [_use("r", ACCOUNT), _result("r", CLASSIFIER, applied=DEFAULT),
             _use("d1", DEFAULT), _result("d1", CONFIG),
             _use("d2", DEFAULT), _result("d2", CLASSIFIER)]
    report = mmw.summarize(mmw.memory_writes(rows))

    account = report["account"]
    assert (account["writes"], account["prompted"], account["rewritten"]) == (5, 4, 1)
    top, second = account["decisions"]
    assert (top["source"], top["reasonType"], top["writes"], top["rewritten"]) == (
        "user_temporary", "-", 4, 0)
    assert top["wait_s"] == {"n": 4, "p50": 30.0, "p95": 21600.0, "max": 21600.0}
    assert (second["source"], second["reasonType"], second["writes"], second["rewritten"]) == (
        "config", "classifier", 1, 1)

    default = report["default"]
    assert (default["writes"], default["prompted"], default["rewritten"]) == (2, 0, 0)
    assert sorted((r["source"], r["reasonType"], r["writes"]) for r in default["decisions"]) == [
        ("config", "classifier", 1), ("config", "other", 1)]
    assert report["other"] == {"writes": 0, "rewritten": 0, "prompted": 0, "decisions": []}


# --- over a projects tree -----------------------------------------------------

def test_measure_reads_every_transcript_and_counts_a_copied_row_once(tmp_path):
    projects = tmp_path / "projects"
    first = [_use("a", ACCOUNT), _result("a", PROMPTED)]
    _write_jsonl(projects / "-proj" / "s1.jsonl", first)
    # A resumed session copies the rows it inherited, then adds its own.
    _write_jsonl(projects / "-proj" / "s2.jsonl", first + [
        _use("b", DEFAULT, ts="2026-10-13T11:00:00.000Z"),
        _result("b", CONFIG, ts="2026-10-13T11:00:00.100Z")])
    _write_jsonl(projects / "-proj" / "s2" / "subagents" / "agent-1.jsonl", [
        _use("c", ACCOUNT, ts="2026-10-13T12:00:00.000Z"),
        _result("c", PROMPTED, ts="2026-10-13T12:00:09.000Z")])
    report = mmw.measure(str(projects))
    assert report["transcripts"] == 3 and report["writes"] == 3
    assert report["spellings"]["account"]["prompted"] == 2
    assert report["spellings"]["default"]["writes"] == 1
    assert (report["first"], report["last"]) == (
        "2026-10-13T10:00:00.000Z", "2026-10-13T12:00:00.000Z")


def test_a_copy_cut_off_before_its_result_does_not_hide_the_answered_one(tmp_path):
    projects = tmp_path / "projects"
    _write_jsonl(projects / "-proj" / "a.jsonl", [_use("a", ACCOUNT)])
    _write_jsonl(projects / "-proj" / "b.jsonl", [_use("a", ACCOUNT), _result("a", PROMPTED)])
    account = mmw.measure(str(projects))["spellings"]["account"]
    assert (account["writes"], account["prompted"]) == (1, 1)


def test_days_drops_older_writes(tmp_path):
    projects = tmp_path / "projects"
    recent = mmw.datetime.now(mmw.timezone.utc).isoformat()
    _write_jsonl(projects / "-proj" / "s.jsonl", [
        _use("old", ACCOUNT, ts="2020-01-01T00:00:00.000Z"),
        _result("old", PROMPTED, ts="2020-01-01T00:00:01.000Z"),
        _use("new", ACCOUNT, ts=recent), _result("new", PROMPTED, ts=recent)])
    assert mmw.measure(str(projects), days=6)["writes"] == 1
    assert mmw.measure(str(projects))["writes"] == 2


def test_a_broken_line_and_a_missing_tree_are_not_errors(tmp_path):
    projects = tmp_path / "projects"
    path = projects / "-proj" / "s.jsonl"
    _write_jsonl(path, [_use("a", ACCOUNT), _result("a", PROMPTED)])
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"tool_use" memory not json\n[1, 2]\n')
    assert mmw.measure(str(projects))["writes"] == 1
    assert mmw.measure(str(tmp_path / "nowhere"))["writes"] == 0


def test_the_text_report_names_each_spelling_and_the_json_one_is_stamped(tmp_path, capsys):
    projects = tmp_path / "projects"
    _write_jsonl(projects / "-proj" / "s.jsonl", [
        _use("a", ACCOUNT), _result("a", PROMPTED),
        _use("b", ACCOUNT), _result("b", CLASSIFIER, applied=DEFAULT)])
    assert mmw.main(["--projects", str(projects)]) == 0
    text = capsys.readouterr().out
    assert "account  (~/.claude-<slug>/projects/…/memory/): 2 writes, 1 prompted, 1 rewritten" in text
    assert "user_temporary" in text and "classifier" in text
    assert ".claude-work" not in text  # the slug can be an address
    assert text.rstrip().splitlines()[-1].startswith("provenance: ")

    assert mmw.main(["--projects", str(projects), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["spellings"]["account"]["writes"] == 2
    assert data["provenance"]["tool"] == "measure_memory_write_prompts"
