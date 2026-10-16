"""``tools/measure_grok_stops.py`` over a synthetic Grok home (grok-host w2)."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_grok_stops as tool  # noqa: E402

LIMIT = ("API error (status 429 Too Many Requests): subscription:free-usage-exhausted: "
         "Usage resets over a rolling 24-hour window")


def _write(path: Path, records: List[Dict[str, Any]]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")


def _update(**update: Any) -> Dict[str, Any]:
    return {"timestamp": 1792156853, "method": "_x.ai/session/update",
            "params": {"sessionId": "s", "update": update}}


def _session(home: Path, sid: str, outcome: str, updates: List[Dict[str, Any]], *,
             used: int = 0, single_call_input: int = 0) -> None:
    directory = home / "sessions" / "%2Frepo" / sid
    directory.mkdir(parents=True)
    _write(directory / "events.jsonl", [
        {"type": "turn_started"}, {"type": "phase_changed"}, {"type": "phase_changed"},
        {"type": "turn_ended", "outcome": outcome}])
    _write(directory / "updates.jsonl", updates)
    if used:
        (directory / "signals.json").write_text(json.dumps({"contextTokensUsed": used}),
                                                encoding="utf-8")
        (directory / "usage.json").write_text(json.dumps(
            {"turns": [{"modelCalls": 1, "inputTokens": single_call_input}]}), encoding="utf-8")


def test_counts_outcomes_stop_reasons_windows_and_context(tmp_path: Path) -> None:
    home = tmp_path / ".grok"
    _session(home, "01a1-0000-0000-0000-00000000aaaa", "completed",
             [_update(sessionUpdate="turn_completed", stop_reason="end_turn")],
             used=15263, single_call_input=15271)
    _session(home, "01a1-0000-0000-0000-00000000bbbb", "error",
             [_update(sessionUpdate="retry_state", type="exhausted", reason=LIMIT),
              _update(sessionUpdate="turn_completed", stop_reason="rate_limit")],
             used=1294, single_call_input=14815)
    _session(home, "01a1-0000-0000-0000-00000000cccc", "error",
             [_update(sessionUpdate="turn_completed", stop_reason="rate_limit")])

    report = tool.measure([home])

    assert report["sessions"] == 3
    assert report["events"] == {"phase_changed": 6, "turn_started": 3, "turn_ended": 3}
    assert report["turn_outcomes"] == {"error": 2, "completed": 1}
    assert report["stop_reasons"] == {"rate_limit": 2, "end_turn": 1}
    assert report["rate_limits"] == {"total": 2, "window": {"24h": 1, "none": 1}}
    assert report["error_turns"] == {"turns": 2, "sessions": 2, "sessions_last_stop_rate_limit": 2}
    assert report["context"]["compared"] == 2
    assert report["context"]["within_1pct"] == 1
    assert {p["session"] for p in report["context"]["pairs"]} == {"0000aaaa", "0000bbbb"}
    assert "3 sessions in 1 home(s)" in tool.render(report)


def test_a_missing_home_counts_nothing(tmp_path: Path) -> None:
    report = tool.measure([tmp_path / "nowhere"])
    assert report["sessions"] == 0 and report["context"]["compared"] == 0


def test_main_prints_json_with_provenance(tmp_path: Path, capsys) -> None:
    assert tool.main(["--home", str(tmp_path), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["sessions"] == 0 and "provenance" in out
