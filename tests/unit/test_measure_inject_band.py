"""``tools/measure_inject_band.py`` over synthetic logs, transcripts and pages (#764)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.hooks.user_prompt_submit import _prompt_hash

_TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mib = _load("measure_inject_band")

NOTICE_TEXT = "<task-notification>\n<task-id>b1</task-id>\nexit code 1\n</task-notification>"


def _row(sid: str, text: str, ts: str, scores, emitted=None, status="ok", sizes=None) -> dict:
    emitted = [s for s, _ in scores] if emitted is None else emitted
    row = {"session_id": sid, "project": "p", "prompt_hash": _prompt_hash(text),
           "emitted": emitted, "scores": [1.0] * len(emitted), "silence_reason": None,
           "judge": {"status": status, "scores": [[s, v] for s, v in scores]}, "ts": ts}
    if sizes is not None:
        row["rule_bytes"] = sizes
    return row


def _pair(row: str, slug: str, band: str, kind: str, label=None, *, session="s",
          score=None) -> dict:
    out = {"row": row, "slug": slug, "band": band, "kind": kind, "session_id": session,
           "score": score if score is not None else (0.45 if band == mib.NEAR else 0.7)}
    if label is not None:
        out["label"] = label
    return out


# --- bands, kinds, rows ------------------------------------------------------

def test_bands_split_at_the_shipped_bar_and_at_half():
    assert mib.band_of(0.39) == mib.BELOW
    assert mib.band_of(0.4) == mib.NEAR
    assert mib.band_of(0.49) == mib.NEAR
    assert mib.band_of(0.5) == mib.ABOVE


def test_a_prompt_opening_with_a_machine_envelope_is_a_notice():
    assert mib.kind_of(NOTICE_TEXT) == mib.NOTICE
    assert mib.kind_of("  <cross-session-message from=x>hi</cross-session-message>") == mib.NOTICE
    assert mib.kind_of("<mnemo-child-finished id=1>") == mib.NOTICE
    assert mib.kind_of("why does <task-notification> show up here?") == mib.TYPED
    assert mib.notice_tag(NOTICE_TEXT) == "task-notification"
    assert mib.notice_tag("fix the test") is None


def test_rows_skip_torn_lines_and_count_a_row_in_both_generations_once():
    row = _row("s", "hello", "2026-10-20T10:00:00Z", [("a", 0.5)])
    lines = [json.dumps(row), "", "{torn", json.dumps(row), "[1, 2]"]
    assert mib.read_rows(lines) == [row]


def test_only_rules_the_judge_decided_are_pairs_with_their_own_score_and_bytes():
    rows = [
        _row("s", "one", "2026-10-20T10:00:00Z", [("a", 0.45), ("b", 0.8), ("c", 0.2)],
             emitted=["b", "a"], sizes=[300, 100]),
        _row("s", "two", "2026-10-20T10:01:00Z", [("a", 0.9)], status="timeout"),
        _row("s", "three", "2026-10-20T10:02:00Z", [("x", 0.6)], emitted=["x", "unscored"]),
    ]
    pairs = mib.injected_pairs(rows)
    assert [(p["slug"], p["score"], p["band"], p["bytes"]) for p in pairs] == [
        ("b", 0.8, mib.ABOVE, 300), ("a", 0.45, mib.NEAR, 100), ("x", 0.6, mib.ABOVE, None)]
    assert pairs[0]["row"] == pairs[1]["row"] != pairs[2]["row"]


# --- finding the prompt again ------------------------------------------------

def _transcript(*entries) -> list:
    return [json.dumps(e) for e in entries]


def test_typed_and_queued_prompts_are_found_by_the_hooks_own_hash():
    lines = _transcript(
        {"type": "user", "message": {"role": "user", "content": "typed by a person"}},
        {"type": "user", "isSidechain": True, "message": {"content": "a subagent's prompt"}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
        {"type": "queue-operation", "operation": "remove", "content": NOTICE_TEXT},
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "queued typing"}},
    ) + ["{torn"]
    found = mib.transcript_prompts(lines, _prompt_hash)
    assert found[_prompt_hash("typed by a person")] == "typed by a person"
    assert found[_prompt_hash("typed by a person\n")] == "typed by a person"
    assert found[_prompt_hash(NOTICE_TEXT)] == NOTICE_TEXT
    assert found[_prompt_hash("queued typing")] == "queued typing"
    assert _prompt_hash("a subagent's prompt") not in found


# --- the draw ----------------------------------------------------------------

def _population(near: int, above: int) -> list:
    pairs = []
    for i in range(near):
        pairs.append(_pair("n%03d" % i, "r", mib.NEAR, mib.TYPED))
    for i in range(above):
        pairs.append(_pair("a%03d" % i, "r", mib.ABOVE, mib.NOTICE))
    return pairs


def test_the_draw_takes_per_band_from_each_band_and_is_seeded():
    pairs = _population(10, 30)
    first = mib.draw(pairs, per_band=5, seed=1)
    assert sum(p["band"] == mib.NEAR for p in first) == 5
    assert sum(p["band"] == mib.ABOVE for p in first) == 5
    assert first == mib.draw(list(reversed(pairs)), per_band=5, seed=1)
    assert first != mib.draw(pairs, per_band=5, seed=2)


def test_the_draw_keeps_one_prompts_pairs_together():
    pairs = [_pair("r1", "a", mib.NEAR, mib.TYPED), _pair("r2", "b", mib.ABOVE, mib.TYPED),
             _pair("r1", "c", mib.ABOVE, mib.TYPED), _pair("r3", "d", mib.NEAR, mib.TYPED)]
    rows = [p["row"] for p in mib.draw(pairs, per_band=5, seed=3)]
    assert len(rows) == 4
    first = rows.index("r1")
    assert rows[first + 1] == "r1"


def test_a_blind_chunk_carries_no_score_band_kind_or_slug():
    sample = [dict(_pair("r1", "secret-slug", mib.NEAR, mib.NOTICE), prompt="P", rule="R"),
              dict(_pair("r1", "other", mib.ABOVE, mib.NOTICE), prompt="P", rule="R2")]
    chunks, ids = mib.mrg.blind_chunks(mib.as_units(sample), {})
    assert chunks == [[{"message": "P", "rules": [{"id": 0, "rule": "R"}, {"id": 1, "rule": "R2"}]}]]
    assert ids == {"0": ["r1", "secret-slug"], "1": ["r1", "other"]}


# --- the pre-registered readings ---------------------------------------------

def _rated(near_on: int, near_n: int, above_on: int, above_n: int, kind=mib.TYPED) -> list:
    out = []
    for i in range(near_n):
        out.append(_pair("n%s%03d" % (kind, i), "r", mib.NEAR, kind,
                         mib.ON_POINT if i < near_on else mib.NOISE))
    for i in range(above_n):
        out.append(_pair("a%s%03d" % (kind, i), "r", mib.ABOVE, kind,
                         mib.ON_POINT if i < above_on else mib.NOISE))
    return out


def _above_vs_near(rated):
    return mib.diff_ci(rated, lambda p: p["band"] == mib.ABOVE,
                       lambda p: p["band"] == mib.NEAR, draws=2000)


def test_a_band_far_less_on_point_reads_raise():
    cmp = _above_vs_near(_rated(5, 100, 40, 100))
    assert cmp["diff"] == pytest.approx(0.35)
    assert cmp["ci"][0] > 0
    assert mib.verdict(cmp, yes="raise", no="keep") == "raise"


def test_two_bands_alike_read_keep():
    cmp = _above_vs_near(_rated(20, 100, 22, 100))
    assert cmp["ci"][0] <= 0 <= cmp["ci"][1]
    assert mib.verdict(cmp, yes="raise", no="keep") == "keep"


def test_a_band_lower_but_not_half_reads_neither():
    cmp = _above_vs_near(_rated(80, 400, 120, 400))
    assert cmp["ci"][0] > 0
    assert cmp["low"] > cmp["high"] / 2
    assert mib.verdict(cmp, yes="raise", no="keep") == "neither"


def test_no_labels_is_no_data_not_keep():
    cmp = _above_vs_near([])
    assert cmp["ci"] is None
    assert mib.verdict(cmp, yes="raise", no="keep") == "no data"


def test_the_bootstrap_moves_a_prompts_pairs_together():
    # Every prompt holds one near and one above pair with the same label, so
    # the two bands are equal in every resample, never just on average.
    rated = []
    for i in range(30):
        label = mib.ON_POINT if i % 3 == 0 else mib.NOISE
        rated.append(_pair("r%d" % i, "a", mib.NEAR, mib.TYPED, label))
        rated.append(_pair("r%d" % i, "b", mib.ABOVE, mib.TYPED, label))
    cmp = _above_vs_near(rated)
    assert cmp["ci"] == [0.0, 0.0]


# --- the repeat detector -----------------------------------------------------

def _repeating(slug: str, sessions: int, score: float, kind: str) -> list:
    return [_pair("%s%d" % (slug, i), slug, mib.band_of(score), kind, session="s%d" % i,
                  score=score) for i in range(sessions)]


def test_the_score_detector_wants_many_sessions_and_a_median_under_half():
    pairs = (_repeating("low", 12, 0.45, mib.TYPED) + _repeating("few", 5, 0.41, mib.TYPED)
             + _repeating("high", 12, 0.7, mib.TYPED))
    assert [r["slug"] for r in mib.repeaters(pairs, by="score")] == ["low"]


def test_the_kind_detector_flags_a_rule_living_on_notices_at_any_score():
    pairs = (_repeating("ci-aphorism", 12, 0.53, mib.NOTICE)
             + _repeating("domain", 12, 0.45, mib.TYPED))
    flagged = mib.repeaters(pairs, by="kind")
    assert [r["slug"] for r in flagged] == ["ci-aphorism"]
    assert flagged[0]["notices"] == 12 and flagged[0]["sessions"] == 12


def test_report_reads_every_cell_and_both_detectors():
    population = _repeating("ci-aphorism", 12, 0.45, mib.NOTICE) + _rated(1, 10, 5, 10)
    sample = population
    labels = {}
    for p in sample:
        labels.setdefault(p["row"], {})[p["slug"]] = p.get("label", mib.NOISE)
    for p in population:
        p.setdefault("bytes", 100)
        p.setdefault("ts", "2026-10-20T10:00:00Z")
    data = mib.report(population, [dict(p) for p in sample], labels)
    assert data["labelled"] == len(sample)
    assert data["cells"]["notice/near"]["n"] == 12
    assert data["cells"]["notice/near"]["noise"] == 1.0
    assert data["repeaters"]["kind"]["verdict"] == "doctor row"
    assert data["repeaters"]["score"]["flagged"][0]["labels"] == [0] * 12
    text = mib.format_report(data, rater="r")
    assert "on-point above - near (primary)" in text
    assert "ci-aphorism" in text


# --- end to end on a synthetic vault -----------------------------------------

def _page(vault: Path, slug: str, body: str, *, disabled: bool = False) -> None:
    path = vault / "shared" / "feedback" / ("%s.md" % slug)
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = "disabled: true\n" if disabled else ""
    path.write_text("---\nname: %s\ndescription: d\n%s---\n%s\n" % (slug, extra, body),
                    encoding="utf-8")


def test_build_export_import_and_report_round_trip(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    _page(vault, "ci-rule", "Check the output, not the exit code.", disabled=True)
    _page(vault, "domain-rule", "The lure stops at the wall.")
    rows = [
        _row("s1", NOTICE_TEXT, "2026-10-20T10:00:00Z", [("ci-rule", 0.45)], sizes=[50]),
        _row("s1", "why does the lure stop", "2026-10-20T10:05:00Z",
             [("domain-rule", 0.8), ("ci-rule", 0.42)], sizes=[40, 50]),
        _row("s2", "a prompt nobody can find", "2026-10-20T11:00:00Z", [("domain-rule", 0.6)]),
    ]
    log = vault / ".mnemo" / "reflex-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    projects = tmp_path / "projects"
    transcript = projects / "proj" / "s1.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text("\n".join(_transcript(
        {"type": "user", "sessionId": "s1", "message": {"content": "why does the lure stop"}},
        {"type": "queue-operation", "sessionId": "s1", "operation": "remove",
         "content": NOTICE_TEXT},
    )) + "\n", encoding="utf-8")
    (projects / "proj" / "s2.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)

    assert mib.main(["--build", "--projects", str(projects)]) == 0
    out_dir = vault / ".mnemo" / mib.OUT_DIR
    population = json.loads((out_dir / mib.POPULATION_NAME).read_text(encoding="utf-8"))["pairs"]
    assert [(p["slug"], p["kind"], p["disabled_now"]) for p in population] == [
        ("ci-rule", mib.NOTICE, True), ("domain-rule", mib.TYPED, False),
        ("ci-rule", mib.TYPED, True), ("domain-rule", None, False)]
    sample = json.loads((out_dir / mib.SAMPLE_NAME).read_text(encoding="utf-8"))["pairs"]
    assert len(sample) == 3
    assert {p["rule"] for p in sample} == {"Check the output, not the exit code.",
                                           "The lure stops at the wall."}

    blind = tmp_path / "blind"
    assert mib.main(["--export-blind", str(blind)]) == 0
    ids = json.loads((blind / "ids.json").read_text(encoding="utf-8"))["ids"]
    chunk = (blind / "chunk-001.json").read_text(encoding="utf-8")
    assert "ci-rule" not in chunk and "0.45" not in chunk
    assert (blind / "RUBRIC.txt").read_text(encoding="utf-8") == mib.RUBRIC
    answers = {key: (mib.ON_POINT if slug == "domain-rule" else mib.NOISE)
               for key, (_, slug) in ids.items()}
    (blind / "chunk-001.labels.json").write_text(json.dumps(answers), encoding="utf-8")
    assert mib.main(["--import-labels", str(blind)]) == 0
    assert mib.main(["--build", "--projects", str(projects)]) == 1

    capsys.readouterr()
    assert mib.main(["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["labelled"] == 3
    assert data["cells"]["all/near"]["on_point"] == 0.0
    assert data["cells"]["all/above"]["on_point"] == 1.0
    assert data["population"]["kinds"]["unmatched"]["pairs"] == 1
    assert data["population"]["notice_tags"] == {"task-notification": 1}


def test_import_refuses_a_partial_answer(tmp_path, monkeypatch, capsys):
    vault = tmp_path / "vault"
    out_dir = vault / ".mnemo" / mib.OUT_DIR
    out_dir.mkdir(parents=True)
    sample = [dict(_pair("r1", "a", mib.NEAR, mib.TYPED), prompt="P", rule="R", ts="t", bytes=1),
              dict(_pair("r1", "b", mib.ABOVE, mib.TYPED), prompt="P", rule="R", ts="t", bytes=1)]
    (out_dir / mib.POPULATION_NAME).write_text(json.dumps({"pairs": sample}), encoding="utf-8")
    (out_dir / mib.SAMPLE_NAME).write_text(json.dumps({"pairs": sample}), encoding="utf-8")
    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: vault)
    blind = tmp_path / "blind"
    assert mib.main(["--export-blind", str(blind)]) == 0
    (blind / "chunk-001.labels.json").write_text(json.dumps({"0": 2}), encoding="utf-8")
    assert mib.main(["--import-labels", str(blind)]) == 1
    assert not list(out_dir.glob("labels-*.json"))
