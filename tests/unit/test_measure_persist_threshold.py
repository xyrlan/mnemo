"""``tools/measure_persist_threshold.py`` over synthetic transcripts (#533).

The threshold ``session_start.ENVELOPE_MAX_BYTES`` is set against comes from
this tool, so the reading of Claude Code's ``<persisted-output>`` stub is
pinned here: where the size comes from, what counts as mnemo's envelope, and
when the cut is called clean.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_persist_threshold.py"
_spec = importlib.util.spec_from_file_location("measure_persist_threshold", _TOOL)
mpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mpt)


def _stub(kb: float, saved: str) -> str:
    return ("<persisted-output>\nOutput too large (%.1fKB). Full output saved to: %s\n\n"
            "Preview (first 2KB):\nmnemo://v1 project=p\n...\n</persisted-output>" % (kb, saved))


def _att(texts, hook="SessionStart", version="2.1.273", ts="2026-09-20T10:00:00Z") -> dict:
    return {"type": "attachment", "version": version, "timestamp": ts, "sessionId": "s",
            "attachment": {"type": "hook_additional_context", "hookName": hook, "content": texts}}


def _envelope(n: int) -> str:
    head = "mnemo://v1 project=p\n"
    return head + "x" * (n - len(head))


def test_an_inline_text_is_measured_as_it_stands():
    row = mpt.measure_text("mnemo://v1 é")
    assert (row["persisted"], row["chars"], row["bytes"], row["how"]) == (False, 12, 13, "inline")


def test_a_persisted_text_is_measured_by_its_saved_file(tmp_path):
    saved = tmp_path / "hook-1-additionalContext.txt"
    saved.write_text(_envelope(10_100), encoding="utf-8")
    row = mpt.measure_text(_stub(9.9, str(saved)))
    assert (row["persisted"], row["chars"], row["how"]) == (True, 10_100, "saved")


def test_a_persisted_text_whose_file_is_gone_falls_back_to_the_stub(tmp_path):
    row = mpt.measure_text(_stub(9.8, str(tmp_path / "gone.txt")))
    assert (row["persisted"], row["chars"], row["bytes"], row["how"]) == (True, 10035, None, "reported")


def test_only_sessionstart_texts_with_a_mnemo_marker_are_mnemos():
    rows = mpt.scan([
        _att(["mnemo://v1 project=p", "<EXTREMELY_IMPORTANT>other plugin"]),
        _att(["mnemo reflex context: [[x]]"], hook="UserPromptSubmit"),
        {"type": "user", "message": {"content": "hi"}},
    ])
    assert [r["mnemo"] for r in rows] == [True, False, False]
    assert rows[0]["version"] == "2.1.273"


def test_the_cut_is_clean_when_no_kept_text_reaches_the_smallest_persisted(tmp_path):
    saved = tmp_path / "a.txt"
    saved.write_text(_envelope(10_044), encoding="utf-8")
    rows = mpt.scan([_att([_envelope(9_872)], version="2.1.269"),
                     _att([_stub(9.8, str(saved))]),
                     _att([_stub(9.0, str(tmp_path / "gone.txt"))])])
    c = mpt.cut(rows)
    assert c["largest_kept"]["chars"] == 9_872 and c["largest_kept"]["version"] == "2.1.269"
    assert c["smallest_persisted"]["chars"] == 10_044
    # the stub-only row is rounded: counted, never the cut
    assert c["persisted"] == 2 and c["persisted_reported_only"] == 1
    assert c["clean_in_chars"] is True and c["kept_at_or_above_smallest_persisted"] == []


def test_an_overlap_is_reported_not_hidden(tmp_path):
    saved = tmp_path / "a.txt"
    saved.write_text(_envelope(10_044), encoding="utf-8")
    c = mpt.cut(mpt.scan([_att([_envelope(10_500)]), _att([_stub(9.8, str(saved))])]))
    assert c["clean_in_chars"] is False
    assert [r["chars"] for r in c["kept_at_or_above_smallest_persisted"]] == [10_500]


def test_since_counts_mnemos_persisted_envelopes_from_a_date(tmp_path):
    saved = tmp_path / "a.txt"
    saved.write_text(_envelope(10_044), encoding="utf-8")
    rows = mpt.scan([_att([_stub(9.8, str(saved))], ts="2026-09-01T00:00:00Z"),
                     _att([_envelope(8_000)], ts="2026-09-29T00:00:00Z"),
                     _att(["not mnemo"], ts="2026-09-29T00:00:00Z")])
    assert mpt.since(rows, "2026-09-28") == {"since": "2026-09-28", "envelopes": 1,
                                             "persisted": 0, "largest_chars": 8_000}


def test_run_walks_every_transcript_under_projects(tmp_path, capsys):
    proj = tmp_path / "projects" / "-repo"
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text(
        "\n".join(json.dumps(e) for e in [_att([_envelope(5_000)]), _att([_envelope(9_000)])]) + "\nnot json\n",
        encoding="utf-8")
    (proj / "b.jsonl").write_text(json.dumps(_att([_envelope(7_000)])) + "\n", encoding="utf-8")
    d = mpt.run(tmp_path / "projects", "2026-09-01")
    assert (d["transcripts"], d["hook_texts"]) == (2, 3)
    assert d["mnemo_envelopes"]["largest_kept"]["chars"] == 9_000
    assert d["mnemo_since"]["persisted"] == 0
    assert mpt.main(["--projects", str(tmp_path / "projects")]) == 0
    assert "largest kept" in capsys.readouterr().out
