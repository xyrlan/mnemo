"""``tools/audit_stub_args.py`` over synthetic production and test code (#572)."""
from __future__ import annotations

import json
import os
import sys
import types
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import audit_stub_args as tool  # noqa: E402


# --- reading values against hints ---------------------------------------------------------------

@pytest.mark.parametrize("hint,value,ok", [
    (Path, Path("a"), True),
    (Path, "a", False),  # #562
    (Optional[Path], None, True),
    (Union[str, Path], "a", True),
    (List[str], ["a", "b"], True),
    (List[str], ["a", Path("b")], False),
    (Dict[str, int], {"a": 1}, True),
    (Dict[str, int], {"a": "1"}, False),
    (Tuple[str, int], ("a", 1), True),
    (Tuple[str, ...], ("a", 1), False),
    (Callable[[Path], list], len, True),
    (Callable[[Path], list], "len", False),
    (float, 1, True),
    (float, True, False),
    (int, "1", False),
    ("Unresolved", 1, True),
])
def test_accepts_reads_unions_containers_and_the_numeric_tower(hint, value, ok):
    assert tool.accepts(hint, value) is ok


def test_check_call_names_the_argument_the_real_function_would_refuse():
    def load(path: Path, *, limit: int = 0) -> list:
        return []

    assert tool.check_call(load, (Path("a"),), {}) is None
    assert tool.check_call(load, ("a",), {}) == "path=str (wants Path)"
    assert "does not bind" in tool.check_call(load, (Path("a"),), {"lmit": 1})
    assert "does not bind" in tool.check_call(load, (), {})

    def sweep(sessions: List[Path]) -> None:
        return None

    assert tool.check_call(sweep, (["a"],), {}) == "sessions=list[str] (wants List[pathlib.Path])"


def test_unread_params_are_the_ones_a_stub_never_looks_at():
    assert tool.unread_params((lambda p: []).__code__) == ["p"]
    assert tool.unread_params((lambda p, **kw: p).__code__) == ["kw"]
    assert tool.unread_params((lambda *a, **k: None).__code__) == ["a", "k"]

    def kept(argv):
        return lambda: argv

    assert tool.unread_params(kept.__code__) == []


def test_describe_names_element_types():
    assert tool.describe("a") == "str"
    assert tool.describe(["a", 1]) == "list[int|str]"
    assert tool.describe({"a": 1}) == "dict[str, int]"


# --- production calling a stub ------------------------------------------------------------------

PRODUCTION = '''
from pathlib import Path


def real_load(path: Path) -> list:
    return path.read_text().splitlines()


def tally(paths, load):
    return sum(len(load(p)) for p in paths)


def main(paths):
    return tally(paths, real_load)


def run(paths):
    return sum(len(real_load(p)) for p in paths)
'''


@pytest.fixture()
def layout(tmp_path, monkeypatch):
    """A repo with ``src/prod.py`` imported as a module, and a way to compile
    a stub as if it were written in ``tests/``."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    source = tmp_path / "src" / "prod.py"
    source.write_text(PRODUCTION, encoding="utf-8")
    module = types.ModuleType("prod_572")
    module.__file__ = str(source)
    exec(compile(PRODUCTION, str(source), "exec"), module.__dict__)
    monkeypatch.setitem(sys.modules, "prod_572", module)
    monkeypatch.setattr(tool, "TESTS", str(tmp_path / "tests") + os.sep)
    monkeypatch.setattr(tool, "PRODUCTION", (str(tmp_path / "src") + os.sep,))
    monkeypatch.setattr(tool, "_SOURCES", {})
    monkeypatch.setattr(tool, "_WIRED", {})

    def stub(text):
        scope: dict = {}
        exec(compile("f = " + text, str(tmp_path / "tests" / "test_prod.py"), "exec"), scope)
        return scope["f"]

    return module, stub


def test_a_patched_stub_is_checked_against_the_function_it_replaced(layout):
    module, stub = layout
    audit = tool.Audit()
    module.real_load = audit.wrap(stub("lambda p: ['x']"), module.real_load, "prod.real_load")

    assert module.run(["a.jsonl"]) == 1

    [row] = audit.report()
    assert row["kind"] == "patched" and row["real"] == "prod_572.real_load"
    assert row["unread"] == ["p"] and row["types"] == ["str"]
    assert row["problems"] == ["path=str (wants Path)"]


def test_a_stub_the_test_calls_itself_is_not_recorded(layout):
    module, stub = layout
    audit = tool.Audit()
    wrapped = audit.wrap(stub("lambda p: []"), module.real_load, "prod.real_load")
    wrapped("a")
    assert audit.report() == []


def test_an_injected_stub_is_checked_against_what_production_wires_in(layout):
    """#562's shape: the stub is an argument, and ``main`` passes the real
    loader in its place."""
    module, stub = layout
    audit = tool.Audit()
    load = stub("lambda p: ['x']")

    def watch(frame, event, arg):
        if event == "call" and frame.f_code is load.__code__:
            audit.on_start(frame.f_code, frame)

    sys.setprofile(watch)
    try:
        module.tally(["a.jsonl"], load)
    finally:
        sys.setprofile(None)

    [row] = audit.report()
    assert row["kind"] == "injected" and row["holders"] == ["load"]
    assert row["real"] == "prod_572.real_load"
    assert row["wired"][0].endswith("real_load")
    assert row["problems"] == ["path=str (wants Path)"]


def test_the_report_marks_candidates_and_round_trips_through_json(layout, tmp_path, capsys):
    module, stub = layout
    audit = tool.Audit()
    module.real_load = audit.wrap(stub("lambda p: ['x']"), module.real_load, "prod.real_load")
    module.run(["a.jsonl"])
    out = tmp_path / "audit.json"
    out.write_text(json.dumps(audit.report()), encoding="utf-8")

    assert tool.main([str(out)]) == 0
    text = capsys.readouterr().out
    assert "## patched: 1 production→stub sites, 1 with a candidate mismatch" in text
    assert "path=str (wants Path)" in text


def test_a_mock_that_replaced_a_function_is_checked_too(layout, monkeypatch):
    from unittest import mock

    module, stub = layout
    audit = tool.Audit()
    monkeypatch.setattr(tool, "AUDIT", audit)
    # restored by monkeypatch after the test: the hook only lives for this one
    monkeypatch.setattr(mock._patch, "__enter__", mock._patch.__enter__)
    monkeypatch.setattr(mock.CallableMixin, "_mock_call", mock.CallableMixin._mock_call)
    tool._install_mock_hook()

    with mock.patch.object(module, "real_load", return_value=["x"]):
        assert module.run(["a.jsonl"]) == 1

    [row] = audit.report()
    assert row["kind"] == "patched" and row["real"] == "prod_572.real_load"
    assert row["problems"] == ["path=str (wants Path)"]


def test_an_injected_stub_s_positional_parameter_may_have_come_by_keyword():
    """A stub's frame cannot say how a value arrived: ``fetch(issue,
    repo_root=root)`` and ``fetch(issue, root)`` look the same from inside
    ``def fake(issue, repo_root)``. Only a call that fits neither is a
    candidate."""
    def fetch_issue(issue: int, *, repo_root: Path) -> None:
        return None

    def fake(issue, repo_root):
        return None

    assert tool._check_either(fetch_issue, fake.__code__, [7, Path(".")], {}) is None
    assert "does not bind" in tool._check_either(fetch_issue, fake.__code__, [7, Path("."), 1], {})
    assert tool._check_either(fetch_issue, fake.__code__, ["7", Path(".")], {}) == "issue=str (wants int)"
