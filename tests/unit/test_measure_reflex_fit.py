"""``tools/measure_reflex_fit.py`` over a synthetic vault, log and transcripts (#542)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from mnemo.core.reflex import render
from mnemo.core.text_utils import GRAPH_SECTION_MARKER

_TOOLS = Path(__file__).resolve().parents[2] / "tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mrf = _load("measure_reflex_fit")


def _page(vault: Path, slug: str, body: str) -> None:
    path = vault / "shared" / "feedback" / f"{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {slug}\ndescription: d\nsources:\n  - bots/p/memory/x.md\n"
                    f"stability: stable\n---\n{body}\n", encoding="utf-8")


def _long(n: int) -> str:
    return "\n".join(f"- line {i:04d} of a long rule body, long enough to count" for i in range(n))


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    _page(vault, "short", "A short rule.")
    _page(vault, "long", _long(300))
    return vault


def _log(vault: Path, rows) -> None:
    path = vault / ".mnemo" / "reflex-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(ts: str, emitted, reason=None) -> dict:
    return {"ts": ts, "session_id": "s", "emitted": emitted, "silence_reason": reason}


def test_the_hook_and_the_measurement_share_one_body_function():
    mfb = _load("measure_full_body")
    page = "---\nname: a\n---\nBody line.\n\n" + GRAPH_SECTION_MARKER + "\n- [[b]]\n"
    assert mfb.full_body(page) == render.full_body(page) == "Body line."
    assert mfb.full_line("• [[a]]: preview (call read_mnemo_rule …)", "Body.") == \
        render.full_line("• [[a]]", "Body.")


def test_only_emissions_inside_the_window_count():
    rows = [_row("2026-09-01T00:00:00Z", ["a"]), _row("2026-09-10T00:00:00Z", ["a"]),
            _row("2026-09-10T00:00:01Z", [], "deduped"), _row("2026-09-30T00:00:00Z", ["a"])]
    kept = mrf.emissions(rows, "2026-09-05T00:00:00Z", "2026-09-29T00:00:00Z")
    assert [r["ts"] for r in kept] == ["2026-09-10T00:00:00Z"]


def test_replay_counts_rows_and_rules_cut(tmp_path):
    vault = _vault(tmp_path)
    known = mrf.pages(vault)
    rows = [_row("t1", ["short"]), _row("t2", ["short", "long"]), _row("t3", ["gone"])]
    d = mrf.replay(rows, known)
    assert (d["rows"], d["rules"], d["rows_cut"], d["rules_cut"], d["rules_missing"]) == (3, 4, 1, 1, 1)
    assert d["over_cap"] == 0 and d["block_bytes"]["max"] <= d["max_bytes"]


def test_a_reflex_block_in_a_transcript_is_one_emission_row():
    text = render.render([render.Entry("a", "p", "Body of a.\nSecond line."),
                          render.Entry("b", "p", None)]).text
    events = [
        {"type": "attachment", "timestamp": "2026-09-20T10:00:00.123Z", "sessionId": "s1",
         "attachment": {"type": "hook_additional_context", "hookName": "UserPromptSubmit",
                        "content": [text]}},
        {"type": "attachment", "timestamp": "2026-09-20T10:00:00.123Z", "sessionId": "s1",
         "attachment": {"type": "hook_additional_context", "hookName": "SessionStart",
                        "content": ["mnemo://v1 project=p\n• [[not-reflex]]: x"]}},
        "not an event",
    ]
    rows = mrf.transcript_emissions(events)
    assert rows == [{"ts": "2026-09-20T10:00:00Z", "emitted": ["a", "b"], "session_id": "s1",
                     "silence_reason": None}]


def test_a_block_copied_into_a_second_transcript_counts_once(tmp_path, monkeypatch):
    text = render.render([render.Entry("short", "p", "A short rule.")]).text
    ev = {"type": "attachment", "timestamp": "2026-09-20T10:00:00Z", "sessionId": "s1",
          "attachment": {"type": "hook_additional_context", "content": text}}
    projects = tmp_path / "projects"
    for name in ("a/one.jsonl", "b/two.jsonl"):
        p = projects / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(ev) + "\n{torn\n", encoding="utf-8")
    assert len(mrf.transcript_rows(projects)) == 1


def test_run_reports_both_sources(tmp_path):
    vault = _vault(tmp_path)
    _log(vault, [_row("2026-09-20T10:00:00Z", ["short", "long"]),
                 _row("2026-08-01T10:00:00Z", ["long"])])
    d = mrf.run(vault, 30, "2026-09-28", projects=tmp_path / "none")
    assert (d["log"]["rows"], d["log"]["rows_cut"]) == (1, 1)
    assert d["transcripts"]["rows"] == 0
    lines = mrf.report_lines(d)
    assert "[log]" in lines and "[transcripts]" in lines
    assert any(ln.startswith("rows with a cut   1   100.0%") for ln in lines)
