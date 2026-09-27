"""``tools/measure_skill_relevance.py`` over synthetic child transcripts and a stub judge (#508).

Nothing here touches the network: the judge is a function the test hands in,
so a test can read every state and question the tool would send. What is
pinned is what decides #508: which children count as pieces, what each one
received versus missed, that an unanswered question stays "not judged", and
the pre-registered cut and 20% line.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_skill_relevance.py"
_spec = importlib.util.spec_from_file_location("measure_skill_relevance", _TOOL)
msr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(msr)

BUILTINS = {"simplify": "Review the changed code for reuse.",
            "run": "Launch and drive this project's app."}
LISTING = {
    "type": "skill_listing",
    "names": list(BUILTINS),
    "content": "- simplify: Review the changed code\nfor reuse.\n- run: Launch and drive this project's app.",
    "skillCount": 2,
    "isInitial": True,
}


def _write(projects: Path, folder: str, sid: str, records) -> str:
    path = projects / folder / (sid + ".jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return str(path)


def _child(projects: Path, cwd: str, sid: str, task: str, *, stamp: str,
           listing=LISTING, before=()):
    records = [{"type": "system", "cwd": cwd, "timestamp": stamp}]
    if listing is not None:
        records.append({"type": "attachment", "cwd": cwd, "attachment": listing})
    records.extend(before)
    records.append({"type": "user", "cwd": cwd, "message": {"role": "user", "content": task}})
    return _write(projects, cwd.strip("/").replace("/", "-"), sid, records)


def _skills_dir(root: Path, skills) -> Path:
    for name, description in skills.items():
        folder = root / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            "---\nname: {}\ndescription: {}\n---\n\n# body\n".format(name, description),
            encoding="utf-8")
    return root


def _judge(table, calls=None):
    """A client answering ``noul`` from ``{skill name: probability}``; a name
    absent from the table is a question the judge skipped."""
    def client(state, questions):
        if calls is not None:
            calls.append((state, questions))
        answers = {}
        for key, q in questions.items():
            name = q["instructions"].split("Skill ", 1)[1].split(":", 1)[0]
            if name in table:
                answers[key] = {"noul": table[name]}
        return {"answers": answers}
    return client


def _piece(cwd="/w/meunu-wt-1", task="fix the checkout total", received=None, started=1.0):
    return msr.Piece(cwd=cwd, transcript="t.jsonl", started=started, task=task,
                     received=dict(BUILTINS if received is None else received))


def _row(cwd, arms_scores):
    return {"cwd": cwd, "transcript": "t", "task_chars": 10, "task_head": "task " + cwd,
            "skills": {name: {"arm": arm, "score": score}
                       for name, (arm, score) in arms_scores.items()}}


# -- reading a child --------------------------------------------------------

def test_a_listing_description_runs_to_the_next_listed_name_not_to_a_newline():
    parsed = msr.parse_listing(LISTING)
    assert parsed == {"simplify": "Review the changed code\nfor reuse.",
                      "run": "Launch and drive this project's app."}


def test_a_listed_name_with_no_entry_in_the_content_keeps_an_empty_description():
    parsed = msr.parse_listing({"names": ["simplify", "ghost"], "content": "- simplify: x"})
    assert parsed == {"simplify": "x", "ghost": ""}


def test_the_task_is_the_first_real_prompt_not_meta_a_command_echo_or_a_tool_result(tmp_path):
    before = [
        {"type": "user", "isMeta": True, "message": {"content": "meta line"}},
        {"type": "user", "message": {"content": "<local-command-caveat>x</local-command-caveat>"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "out"}]}},
    ]
    path = _child(tmp_path, "/w/meunu-wt-489", "a", "Work on issue #489", stamp="2026-09-26T10:00:00Z",
                  before=before)
    task, listing = msr.read_child(path)
    assert task == "Work on issue #489"
    assert listing == msr.parse_listing(LISTING)


def test_a_prompt_sent_as_text_blocks_is_read_too(tmp_path):
    path = _write(tmp_path, "d", "s", [
        {"type": "attachment", "attachment": LISTING},
        {"type": "user", "message": {"content": [{"type": "text", "text": "You are building one piece"}]}},
    ])
    assert msr.read_child(path)[0] == "You are building one piece"


def test_pieces_are_one_per_worktree_one_per_task_newest_first(tmp_path):
    p = tmp_path / "projects"
    _child(p, "/w/meunu-wt-1", "first", "task one", stamp="2026-09-24T10:00:00Z")
    # A resume of the same worktree later: the piece is still the first transcript.
    _child(p, "/w/meunu-wt-1", "resumed", "still working?", stamp="2026-09-25T10:00:00Z")
    _child(p, "/w/meunu-wt-2", "b", "task two", stamp="2026-09-25T12:00:00Z")
    # Twins: two worktrees handed the same prompt are one task.
    _child(p, "/w/mnemo-wt-449-abc123", "t1", "twin task", stamp="2026-09-26T09:00:00Z")
    _child(p, "/w/mnemo-wt-449-def456", "t2", "twin task", stamp="2026-09-26T09:00:01Z")
    # A probe under a job's scratch and a child with no listing are not measured.
    _child(p, "/Users/x/.claude/jobs/j1/tmp/proj-wt-3", "probe", "probe", stamp="2026-09-26T11:00:00Z")
    _child(p, "/w/meunu-wt-4", "bare", "no listing", stamp="2026-09-26T11:30:00Z", listing=None)

    pieces = msr.pieces_from(msr.dispatch_transcripts(str(p)), last=10)
    assert [x.task for x in pieces] == ["twin task", "task two", "task one"]
    assert pieces[2].transcript.endswith("first.jsonl")
    assert [x.task for x in msr.pieces_from(msr.dispatch_transcripts(str(p)), last=2)] == \
        ["twin task", "task two"]


def test_maintainer_skills_read_the_frontmatter_and_skip_a_folder_without_a_skill(tmp_path):
    root = _skills_dir(tmp_path / "skills", {"tdd": "Test-driven development.", "pr": "Writing a PR body."})
    (root / "synced").mkdir()
    assert msr.maintainer_skills(root) == {"pr": "Writing a PR body.", "tdd": "Test-driven development."}


def test_a_maintainer_skill_the_child_listed_counts_as_received_not_missing():
    full_profile = _piece(received=dict(BUILTINS, tdd="Test-driven development."))
    arms = {name: arm for name, _d, arm in msr.candidates(full_profile, {"tdd": "t", "pr": "p"})}
    assert arms == {"simplify": msr.RECEIVED, "run": msr.RECEIVED,
                    "tdd": msr.RECEIVED, "pr": msr.MISSING}


# -- judging ---------------------------------------------------------------

def test_one_request_per_piece_with_one_question_per_skill_and_the_task_clipped():
    calls = []
    piece = _piece(task="x" * (msr.TASK_CHARS + 500))
    row = msr.judge_piece(piece, {"tdd": "t"}, _judge({"tdd": 0.8, "run": 0.1, "simplify": 0.3}, calls))
    assert len(calls) == 1
    state, questions = calls[0]
    assert state == {"developer_task": "x" * msr.TASK_CHARS}
    assert len(questions) == 3 and all(q["type"] == "noul" for q in questions.values())
    assert row["skills"]["tdd"] == {"arm": msr.MISSING, "score": 0.8}
    assert row["skills"]["run"] == {"arm": msr.RECEIVED, "score": 0.1}


def test_a_plugin_heavy_listing_is_split_into_batches_with_the_missing_skills_first():
    calls = []
    many = {"plugin-%02d" % i: "d" for i in range(msr.QUESTIONS_PER_REQUEST + 5)}
    table = dict({name: 0.1 for name in many}, tdd=0.9)
    row = msr.judge_piece(_piece(received=many), {"tdd": "t"}, _judge(table, calls))
    assert [len(q) for _s, q in calls] == [msr.QUESTIONS_PER_REQUEST, 6]
    assert "Skill tdd:" in calls[0][1]["s0"]["instructions"]
    assert all(s["score"] is not None for s in row["skills"].values())


def test_a_failed_batch_leaves_only_its_own_skills_unjudged():
    many = {"plugin-%02d" % i: "d" for i in range(msr.QUESTIONS_PER_REQUEST + 5)}
    seen = []

    def second_fails(state, questions):
        seen.append(questions)
        if len(seen) == 2:
            raise OSError("413")
        return _judge({"tdd": 0.9})(state, questions)
    row = msr.judge_piece(_piece(received=many), {"tdd": "t"}, second_fails)
    assert row["skills"]["tdd"]["score"] == 0.9
    assert row["skills"]["plugin-36"]["score"] is None


def test_a_skipped_question_or_a_failed_request_is_not_judged_never_irrelevant():
    row = msr.judge_piece(_piece(), {"tdd": "t"}, _judge({"run": 0.9}))
    assert row["skills"]["tdd"]["score"] is None

    def broken(state, questions):
        raise OSError("timeout")
    row = msr.judge_piece(_piece(), {"tdd": "t"}, broken)
    assert all(s["score"] is None for s in row["skills"].values())
    assert msr.summarize([row])["unjudged"] == 1
    assert msr.summarize([row])["missing"]["of"] == 0


# -- the report --------------------------------------------------------------

def test_the_cut_and_the_decision_line_are_the_ones_508_registered():
    assert msr.CUT == 0.5
    assert msr.DECISION_SHARE == 0.20


def test_exactly_twenty_percent_of_pieces_goes_to_the_ab_and_less_is_dropped():
    hit = _row("/w/a-wt-1", {"tdd": (msr.MISSING, 0.5)})
    miss = [_row("/w/a-wt-%d" % i, {"tdd": (msr.MISSING, 0.49)}) for i in range(2, 7)]
    assert msr.summarize([hit] + miss[:4])["decision"] == "a/b"   # 1/5 = 20%
    assert msr.summarize([hit] + miss)["decision"] == "drop"      # 1/6 < 20%


def test_a_received_skill_over_the_cut_does_not_count_toward_the_decision():
    rows = [_row("/w/a-wt-1", {"run": (msr.RECEIVED, 0.99), "tdd": (msr.MISSING, 0.1)})]
    summary = msr.summarize(rows)
    assert summary["missing"]["pieces"] == 0
    assert summary["received"]["pieces"] == 1
    assert summary["decision"] == "drop"


def test_sensitivity_rows_move_the_cut_but_never_the_decision():
    rows = [_row("/w/a-wt-1", {"tdd": (msr.MISSING, 0.45)})]
    summary = msr.summarize(rows)
    assert summary["sensitivity"]["0.4"]["pieces"] == 1
    assert summary["sensitivity"]["0.69"]["pieces"] == 0
    assert summary["decision"] == "drop"


def test_per_skill_and_per_repo_counts():
    rows = [_row("/w/meunu-wt-1", {"tdd": (msr.MISSING, 0.9), "pr": (msr.MISSING, 0.2)}),
            _row("/w/meunu-wt-2", {"tdd": (msr.MISSING, 0.7), "pr": (msr.MISSING, None)}),
            _row("/w/mnemo-desktop-wt-c-tab", {"tdd": (msr.MISSING, 0.1), "pr": (msr.MISSING, 0.1)})]
    summary = msr.summarize(rows)
    assert summary["missing_per_skill"] == [("tdd", 2, 3), ("pr", 0, 2)]
    assert summary["per_repo"]["meunu"]["pieces"] == 2
    assert summary["per_repo"]["mnemo-desktop"]["pieces"] == 0


def test_an_excluded_skill_moves_the_post_hoc_share_and_never_the_decision():
    rows = [_row("/w/a-wt-1", {"pr": (msr.MISSING, 0.9), "tdd": (msr.MISSING, 0.1)}),
            _row("/w/a-wt-2", {"pr": (msr.MISSING, 0.9), "tdd": (msr.MISSING, 0.8)})]
    plain = msr.summarize(rows)
    assert plain["missing_excluding"] is None
    summary = msr.summarize(rows, ["pr"])
    assert summary["missing"]["pieces"] == 2
    assert summary["decision"] == "a/b"
    assert summary["missing_excluding"]["pieces"] == 1
    report = msr.format_report(summary, [])
    assert "post-hoc, not the decision — excluding pr: 1/2 = 50%" in report


def test_the_split_by_prompt_kind_reads_the_template_the_child_was_handed():
    issue = dict(_row("/w/meunu-wt-1", {"pr": (msr.MISSING, 0.9)}),
                 task_head="Work on issue #1 in this repo: x")
    piece = dict(_row("/w/meunu-wt-c-a", {"pr": (msr.MISSING, 0.2)}),
                 task_head="You are building one piece of the feature \"f\": a")
    kinds = msr.summarize([issue, piece])["per_kind"]
    assert kinds["issue"]["pieces"] == 1 and kinds["contract piece"]["pieces"] == 0


def test_the_hand_check_sample_is_missing_pairs_half_over_half_under_and_repeatable():
    rows = [_row("/w/a-wt-%d" % i, {"tdd": (msr.MISSING, i / 10), "run": (msr.RECEIVED, 0.9)})
            for i in range(10)]
    first = msr.sample(rows, 4)
    assert first == msr.sample(rows, 4)
    assert [p["score"] >= msr.CUT for p in first] == [True, True, False, False]
    # Ten received pairs sit over the cut; asking for more than the missing
    # pool holds must still never hand one back.
    everything = msr.sample(rows, 20)
    assert {p["skill"] for p in everything} == {"tdd"}
    assert len(everything) == 10


# -- the command ---------------------------------------------------------------

def _setup(tmp_path):
    projects = tmp_path / "projects"
    _child(projects, "/w/meunu-wt-1", "a", "fix a flaky test", stamp="2026-09-26T10:00:00Z")
    _child(projects, "/w/meunu-wt-2", "b", "write the PR", stamp="2026-09-26T11:00:00Z")
    skills = _skills_dir(tmp_path / "skills", {"tdd": "Test-driven development.", "pr": "Writing a PR body."})
    return ["--projects", str(projects), "--skills-dir", str(skills),
            "--scores", str(tmp_path / "scores.json")]


def test_a_dry_run_sends_nothing_and_writes_no_scores(tmp_path, capsys):
    calls = []
    assert msr.main(_setup(tmp_path), client=_judge({}, calls)) == 0
    assert calls == []
    assert not (tmp_path / "scores.json").exists()
    out = capsys.readouterr().out
    assert out.startswith("dry run: 2 pieces, 2 maintainer skills, 8 questions in 2 requests")


def test_send_writes_scores_and_a_later_run_reports_from_them_without_sending(tmp_path, capsys):
    args = _setup(tmp_path)
    judge = _judge({"tdd": 0.8, "pr": 0.3, "simplify": 0.1, "run": 0.1})
    assert msr.main(args + ["--send", "--json"], client=judge) == 0
    sent = json.loads(capsys.readouterr().out)
    assert sent["summary"]["missing"]["pieces"] == 2
    assert sent["summary"]["decision"] == "a/b"

    calls = []
    assert msr.main(args + ["--json"], client=_judge({}, calls)) == 0
    assert calls == []
    assert json.loads(capsys.readouterr().out)["summary"] == sent["summary"]


def test_send_without_a_key_refuses(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(msr.rerank, "resolve_key", lambda chosen: (None, "none"))
    assert msr.main(_setup(tmp_path) + ["--send"]) == 1
    assert "needs a key" in capsys.readouterr().err
    assert not (tmp_path / "scores.json").exists()


def test_the_text_report_carries_the_decision_and_the_hand_check(tmp_path, capsys):
    args = _setup(tmp_path)
    msr.main(args + ["--send"], client=_judge({"tdd": 0.8, "pr": 0.3, "simplify": 0.1, "run": 0.1}))
    out = capsys.readouterr().out
    assert "pieces with >= 1 relevant MISSING skill:  2/2 = 100%" in out
    assert "decision (pre-registered, #508): >= 20% -> A/B" in out
    assert "hand check" in out
    assert "meunu" in out
