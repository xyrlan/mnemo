"""``tools/measure_stub_calls.py`` over a synthetic tree: a ``prod/`` package
calling stubs a ``fakes/`` directory defines, the way ``src/`` calls what
``tests/`` passes it (#572)."""
from __future__ import annotations

import importlib
import itertools
import json
import sys
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_stub_calls as tool  # noqa: E402

MONITORING = pytest.mark.skipif(not hasattr(sys, "monitoring"),
                                reason="injected stubs are seen through sys.monitoring (3.12+)")

PROD = '''
from pathlib import Path


def read_events(path: Path) -> list:
    return path.read_text().splitlines()


def historical_yield(source: dict, load) -> int:
    """#562's shape: the stored path is a str, the loader wants a Path."""
    return len(load(source["path"]))


def fixed_yield(source: dict, load) -> int:
    return len(load(Path(source["path"])))


def with_default(path: str, load=read_events) -> int:
    return len(load(Path(path)))


def via_global(path: str) -> int:
    return len(read_events(path))


class Sender:
    def __init__(self, provider):
        self.provider = provider

    def run(self, prompt: str) -> str:
        return self.provider(prompt, model="m")
'''

FAKES = '''
def ignore(p):
    return ["e"]


def answer(prompt, model=None):
    return "ok"
'''


_FRESH = itertools.count()


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """``prod`` and ``fakes`` modules under *tmp_path*, freshly imported."""
    (tmp_path / "prod").mkdir()
    (tmp_path / "fakes").mkdir()
    name = "stubprod_%d" % next(_FRESH)
    (tmp_path / "prod" / (name + ".py")).write_text(textwrap.dedent(PROD), encoding="utf-8")
    (tmp_path / "fakes" / (name + "_fakes.py")).write_text(textwrap.dedent(FAKES), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path / "prod"))
    monkeypatch.syspath_prepend(str(tmp_path / "fakes"))
    prod = importlib.import_module(name)
    fakes = importlib.import_module(name + "_fakes")
    seams = {"prod/%s.py:historical_yield:load" % name: "%s:read_events" % name,
             "prod/%s.py:fixed_yield:load" % name: "%s:read_events" % name,
             "prod/%s.py:Sender.run:self.provider" % name: "%s:read_events" % name}
    rec = tool.Recorder(tmp_path, prod=("prod",), tests="fakes", seams=seams)
    yield prod, fakes, rec
    rec.uninstall()
    sys.modules.pop(name, None)
    sys.modules.pop(name + "_fakes", None)


# --- checking one call ---------------------------------------------------------------------------

def test_a_str_where_the_real_function_annotates_a_path_is_a_mismatch():
    def load(path: Path) -> List[dict]:
        return []

    assert tool.check(load, ["s.jsonl"], {}) == [
        "path: got str, the real function takes Path"]
    assert tool.check(load, [Path("s.jsonl")], {}) == []


def test_arguments_the_real_signature_cannot_bind_are_a_mismatch():
    def run(argv: List[str], *, cwd: str) -> int:
        return 0

    (missing,) = tool.check(run, [["git"]], {})
    assert missing.startswith("bind: missing a required") and "'cwd'" in missing
    assert tool.check(run, [["git"], "/x"], {}) == ["bind: too many positional arguments"]
    assert tool.check(run, [["git"]], {"cwd": "/x"}) == []


def test_annotations_read_through_optional_union_generics_and_callables():
    assert tool.type_ok(None, Optional[str])
    assert not tool.type_ok(3, Optional[str])
    assert tool.type_ok(Path("x"), Union[str, Path])
    assert tool.type_ok({"a": 1}, Dict[str, Any])
    assert not tool.type_ok(["a"], Dict[str, Any])
    assert tool.type_ok(len, "Callable[[str], int]")  # an unresolved string is not checked
    assert tool.type_ok(1, float)
    assert tool.type_ok(mock.MagicMock(), Path)


def test_a_value_the_test_built_is_its_input_not_a_mismatch():
    class FakeRule:
        pass

    def has_transcript(vault: Path, rule: Path) -> bool:
        return False

    assert tool.check(has_transcript, [Path("v"), FakeRule()], {}) == []
    assert tool.check(has_transcript, [Path("v"), object()], {}) == []
    assert tool.check(has_transcript, [Path("v"), "r"], {}) == ["rule: got str, the real function takes Path"]


def test_a_method_called_through_a_mock_on_its_class_drops_self():
    class Gh:
        def open_pr(self, title: str) -> int:
            return 1

    assert tool.check(Gh.open_pr, ["t"], {}, bound=True) == []
    assert tool.check(Gh.open_pr, [3], {}, bound=True) == ["title: got int, the real function takes str"]


def test_a_stub_frame_maps_to_the_real_signature_by_name():
    def provider(prompt: str, *, system: str, model: str, timeout: int) -> str:
        return ""

    args, kwargs = tool.by_name(provider, ["prompt", "system", "model", "timeout"],
                                {"prompt": "p", "system": "s", "model": "m", "timeout": 5})
    assert (args, kwargs) == (["p"], {"system": "s", "model": "m", "timeout": 5})


# --- recording -----------------------------------------------------------------------------------

@MONITORING
def test_562s_crash_is_found_through_a_stub_that_ignores_its_argument(tree):
    prod, fakes, rec = tree
    rec.install()
    assert prod.historical_yield({"path": "s.jsonl"}, fakes.ignore) == 1
    assert prod.fixed_yield({"path": "s.jsonl"}, fakes.ignore) == 1
    rec.uninstall()

    got = {r["site"].split(":")[1]: r for r in rec.rows}
    assert got["historical_yield"]["verdict"] == tool.MISMATCH
    assert got["historical_yield"]["kind"] == "injected"
    assert got["historical_yield"]["problems"] == ["path: got str, the real function takes Path"]
    assert got["fixed_yield"]["verdict"] == tool.AGREES
    assert got["fixed_yield"]["args"] == ["pathlib.%s" % type(Path("x")).__name__]


@MONITORING
def test_the_real_function_is_the_parameter_default_when_there_is_no_seam_entry(tree):
    prod, fakes, rec = tree
    rec.install()
    prod.with_default("s.jsonl", load=fakes.ignore)
    rec.uninstall()

    (row,) = rec.rows
    assert row["seam"] == "load" and row["real"].endswith(".read_events")
    assert row["verdict"] == tool.AGREES


@MONITORING
def test_a_stub_held_on_an_object_is_found_by_its_attribute(tree):
    prod, fakes, rec = tree
    rec.install()
    prod.Sender(fakes.answer).run("hi")
    rec.uninstall()

    (row,) = rec.rows
    assert row["seam"] == "self.provider"
    assert row["verdict"] == tool.MISMATCH  # read_events takes no model
    assert row["problems"][0].startswith("bind: ")


@MONITORING
def test_a_seam_with_no_real_function_is_unresolved_not_agreed(tree):
    prod, fakes, rec = tree
    rec.seams = {}
    rec.install()
    prod.historical_yield({"path": "s.jsonl"}, fakes.ignore)
    rec.uninstall()

    (row,) = rec.rows
    assert row["verdict"] == tool.UNRESOLVED and row["real"] is None


def test_a_monkeypatched_stub_is_checked_against_what_it_replaced_with_the_calls_own_arguments(tree):
    prod, fakes, rec = tree
    rec.install()
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(prod, "read_events", fakes.ignore)
        assert prod.via_global("s.jsonl") == 1
        assert fakes.ignore("not from production") == ["e"]
    finally:
        mp.undo()
        rec.uninstall()

    rows = [r for r in rec.rows if r["kind"] == "patched"]
    assert len(rows) == 1
    assert rows[0]["seam"].endswith(".read_events")
    assert rows[0]["problems"] == ["path: got str, the real function takes Path"]
    assert prod.read_events.__name__ == "read_events"  # undo restored the real one


def test_a_test_that_re_patches_a_fixtures_stub_is_checked_against_the_production_function(tree):
    prod, fakes, rec = tree
    rec.install()
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(prod, "read_events", fakes.answer)  # the fixture's stub
        mp.setattr(prod, "read_events", fakes.ignore)  # the test's own
        prod.via_global("s.jsonl")
    finally:
        mp.undo()
        rec.uninstall()

    (row,) = [r for r in rec.rows if r["kind"] == "patched"]
    assert row["real"].endswith(".read_events")
    assert row["verdict"] == tool.MISMATCH


def test_a_mock_is_checked_against_the_original_it_patched(tree):
    prod, fakes, rec = tree
    rec.install()
    try:
        with mock.patch.object(prod, "read_events", return_value=["e"]):
            prod.via_global("s.jsonl")
    finally:
        rec.uninstall()

    (row,) = rec.rows
    assert row["kind"] == "mock" and row["verdict"] == tool.MISMATCH
    assert row["problems"] == ["path: got str, the real function takes Path"]


# --- the report ----------------------------------------------------------------------------------

def _row(site, seam, verdict, problems=(), kind="injected", test="t::a"):
    return {"kind": kind, "site": site, "seam": seam, "real": "m.f", "args": [], "kwargs": {},
            "problems": list(problems), "verdict": verdict, "stub": "s", "test": test}


def test_a_site_takes_the_worst_verdict_of_its_calls_and_mismatches_sort_first():
    rows = [_row("a.py:f", "load", tool.AGREES), _row("a.py:f", "load", tool.MISMATCH, ["x: got str"]),
            _row("b.py:g", "run", tool.AGREES, test="t::b"), {"kind": "error", "error": "boom"}]
    rep = tool.report(rows)

    assert [(s["site"], s["verdict"], s["calls"]) for s in rep["sites"]] == [
        ("a.py:f", tool.MISMATCH, 2), ("b.py:g", tool.AGREES, 1)]
    assert rep["counts"] == {"injected": {tool.AGREES: 1, tool.UNRESOLVED: 0, tool.MISMATCH: 1}}
    assert len(rep["errors"]) == 1


def test_main_exits_non_zero_while_a_mismatch_stands(tmp_path, capsys):
    calls = tmp_path / "calls.jsonl"
    calls.write_text(json.dumps(_row("a.py:f", "load", tool.MISMATCH, ["path: got str"])) + "\n",
                     encoding="utf-8")
    assert tool.main([str(calls)]) == 1
    assert "path: got str" in capsys.readouterr().out

    calls.write_text(json.dumps(_row("a.py:f", "load", tool.AGREES)) + "\n", encoding="utf-8")
    assert tool.main([str(calls), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["sites"][0]["verdict"] == tool.AGREES


def test_every_seam_entry_names_a_real_callable():
    for key, spec in tool.SEAMS.items():
        assert callable(tool.load(spec)), key


@MONITORING
def test_a_test_class_patched_over_a_real_one_is_checked_against_it(tree, tmp_path):
    prod, fakes, rec = tree
    (tmp_path / "fakes" / "stubcls_fakes.py").write_text(textwrap.dedent('''
        class FakeReader:
            def __init__(self, path):
                self.path = path

            def __call__(self, path):
                return ["e"]
    '''), encoding="utf-8")
    fakes2 = importlib.import_module("stubcls_fakes")
    rec.install()
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(prod, "read_events", fakes2.FakeReader("x"))
        prod.via_global("s.jsonl")
    finally:
        mp.undo()
        rec.uninstall()
        sys.modules.pop("stubcls_fakes", None)

    (row,) = rec.rows
    assert row["kind"] == "patched" and row["seam"].endswith(".read_events")
    assert row["problems"] == ["path: got str, the real function takes Path"]


@MONITORING
def test_one_spy_patched_over_two_functions_is_held_to_the_one_its_call_fits(tree, tmp_path):
    prod, fakes, rec = tree
    (tmp_path / "fakes" / "stubspy_fakes.py").write_text(textwrap.dedent('''
        class Spy:
            def __call__(self, *a, **k):
                return ["e"]
    '''), encoding="utf-8")
    spy = importlib.import_module("stubspy_fakes").Spy()
    rec.install()
    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(prod, "with_default", spy)  # first, so the first guess is wrong
        mp.setattr(prod, "read_events", spy)
        prod.via_global(Path("s.jsonl"))  # fits read_events, not with_default's str
    finally:
        mp.undo()
        rec.uninstall()
        sys.modules.pop("stubspy_fakes", None)

    (row,) = rec.rows
    assert row["verdict"] == tool.AGREES and row["seam"].endswith(".read_events")
