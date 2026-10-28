"""``tools/measure_test_spawns.py``: program names and the per-file tally (#653)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_test_spawns as tool  # noqa: E402


class _Item:
    def __init__(self, nodeid):
        self.nodeid = nodeid


def test_program_is_the_bare_lowercased_name_under_any_spelling():
    assert tool.program(None, ["git", "status"]) == "git"
    assert tool.program(None, [str(Path("C:/Program Files/Git/cmd/GIT.EXE")), "log"]) == "git"
    assert tool.program(b"/usr/bin/python3.11", ["python3.11", "-m", "mnemo"]) == "python"
    assert tool.program(None, "git rev-parse HEAD") == "git"
    assert tool.program(None, "") == ""


def test_spawns_count_against_the_running_test_s_file_only():
    c = tool.Counter()
    c.audit("subprocess.Popen", ("git", ["git", "init"], None, None))  # outside a test
    c.pytest_runtest_protocol(_Item("tests/a.py::t1"), None)
    c.pytest_runtest_setup(_Item("tests/a.py::t1"))
    c.audit("subprocess.Popen", (None, ["git", "init"], None, None))
    c.pytest_runtest_call(_Item("tests/a.py::t1"))
    c.audit("subprocess.Popen", (None, ["git", "commit"], None, None))
    c.audit("subprocess.Popen", (sys.executable, [sys.executable, "-m", "mnemo"], None, None))
    c.audit("open", ("x", "r", 0))                                      # not a spawn
    c.pytest_runtest_logfinish("tests/a.py::t1", None)
    c.pytest_runtest_protocol(_Item("tests/a.py::t2"), None)
    c.pytest_runtest_logfinish("tests/a.py::t2", None)

    rows = tool.summarize(c.files)
    assert len(rows) == 1
    row = rows[0]
    assert (row["file"], row["tests"], row["processes"]) == ("tests/a.py", 2, 3)
    assert row["spawns"] == {"git": 2, "python": 1}
    assert row["per_test"] == 1.5
    assert row["phases"] == {"setup": 1, "call": 2}
    assert "git 2, python 1" in tool.render(rows)
