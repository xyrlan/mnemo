"""``tools/measure_test_durations.py`` over synthetic CI logs (#653)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_test_durations as tool  # noqa: E402

_P = "windows-latest / py3.11\tUNKNOWN STEP\t"


def _log(*rows):
    return [_P + "2026-10-21T14:49:00.0000000Z ============ test session starts ============",
            _P + "2026-10-21T14:49:10.0000000Z scheduling tests via LoadScheduling"] + [
        _P + "2026-10-21T14:49:%s Z [%s] [ 50%%] %s %s " % (ts, gw, outcome, node)
        for ts, gw, outcome, node in rows]


def _rows(*rows):
    # GitHub writes seven fractional digits and no space before the Z.
    return [(ts + "0000000"[: 7 - len(ts.split(".")[1])], gw, out, node)
            for ts, gw, out, node in rows]


def _lines(*rows):
    return [line.replace(" Z [", "Z [") for line in _log(*_rows(*rows))]


def test_a_test_s_time_is_the_gap_since_its_own_worker_s_last_report():
    parsed = tool.parse(_lines(
        ("11.0", "gw0", "PASSED", "tests/a.py::t1"),     # 1 s after scheduling
        ("12.5", "gw1", "PASSED", "tests/b.py::t1"),     # gw1's first: 2.5 s
        ("14.0", "gw0", "PASSED", "tests/a.py::t2"),     # 3 s after gw0's last
        ("14.2", "gw1", "SKIPPED", "tests/b.py::t2"),    # 1.7 s after gw1's last
    ))
    times = tool.test_times(parsed)
    assert {k: round(v, 3) for k, v in times.items()} == {
        "tests/a.py::t1": 1.0, "tests/b.py::t1": 2.5,
        "tests/a.py::t2": 3.0, "tests/b.py::t2": 1.7}


def test_files_rank_by_summed_time_and_shares_add_up():
    parsed = tool.parse(_lines(
        ("11.0", "gw0", "PASSED", "tests/fast.py::t1"),
        ("19.0", "gw0", "PASSED", "tests/slow.py::t1"),
        ("20.0", "gw0", "PASSED", "tests/fast.py::t2"),
    ))
    s = tool.summarize(parsed)
    assert [f["file"] for f in s["top"]] == ["tests/slow.py", "tests/fast.py"]
    assert round(s["busy_seconds"], 3) == 10.0
    assert round(s["top"][0]["share"], 3) == 0.8
    assert s["top"][1]["tests"] == 2 and round(s["top"][1]["slowest"], 3) == 1.0
    assert round(s["tests_wall_seconds"], 3) == 10.0
    assert round(s["session_to_scheduling_seconds"], 3) == 10.0


def test_a_parametrized_id_with_spaces_is_one_test_not_a_truncated_one():
    parsed = tool.parse(_lines(
        ("11.0", "gw0", "PASSED", 'tests/a.py::t[{"a": 1}-x]'),
        ("12.0", "gw0", "PASSED", 'tests/a.py::t[{"a": 2}-x]'),
    ))
    assert sorted(tool.test_times(parsed)) == [
        'tests/a.py::t[{"a": 1}-x]', 'tests/a.py::t[{"a": 2}-x]']


def test_a_second_report_for_one_test_adds_to_it():
    parsed = tool.parse(_lines(
        ("11.0", "gw0", "PASSED", "tests/a.py::t1"),
        ("13.0", "gw0", "ERROR", "tests/a.py::t1"),      # teardown error
    ))
    assert round(tool.test_times(parsed)["tests/a.py::t1"], 3) == 3.0


def test_source_marks_files_that_start_processes(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "spawn.py").write_text("import subprocess\n", encoding="utf-8")
    (tmp_path / "tests" / "pure.py").write_text("def test(): pass\n", encoding="utf-8")
    parsed = tool.parse(_lines(
        ("13.0", "gw0", "PASSED", "tests/spawn.py::t1"),
        ("14.0", "gw0", "PASSED", "tests/pure.py::t1"),
        ("15.0", "gw0", "PASSED", "tests/gone.py::t1"),
    ))
    s = tool.summarize(parsed, source=tmp_path)
    marks = {f["file"]: f["spawns"] for f in s["top"]}
    assert marks == {"tests/spawn.py": True, "tests/pure.py": False, "tests/gone.py": None}
    assert round(s["spawn"]["spawning"]["seconds"], 3) == 3.0
    assert s["spawn"]["other"]["tests"] == 1 and s["spawn"]["unread"] == 1
    assert "P = the file's text starts processes" in tool.render(s)


def test_main_reads_a_log_file_and_ends_with_provenance(tmp_path, capsys):
    log = tmp_path / "job.log"
    log.write_text("\n".join(_lines(("11.0", "gw0", "PASSED", "tests/a.py::t1"))) + "\n",
                   encoding="utf-8")
    assert tool.main([str(log)]) == 0
    out = capsys.readouterr().out
    assert "tests/a.py" in out and out.rstrip().splitlines()[-1].startswith("provenance")
