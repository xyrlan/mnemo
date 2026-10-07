"""Do the test stubs production calls get what the real function takes? (#572)

Usage:
    MNEMO_STUB_CALLS=calls.jsonl PYTHONPATH=src python3 -m pytest -q -p tools.measure_stub_calls
    PYTHONPATH=src python3 tools/measure_stub_calls.py calls.jsonl [--json]

#562 fixed a crash a test could not see: ``historical_yield`` handed the
briefing module's ``_load_jsonl_events`` a ``str`` where it reads a ``Path``,
and the test passed ``lambda p: events``, a stub that takes anything. This
tool finds every such seam by running the suite, not by reading it: as a
pytest plugin it records each call production code (``src/``, ``tools/``)
makes into a callable defined under ``tests/``, with the values production
passed, and binds those values to the real function the stub stands in for —
its signature and its annotations, checked against the values themselves.

Three ways a stub reaches production, and where the real function comes from:

- **patched** — ``monkeypatch.setattr`` put a test function where a real one
  was. The plugin wraps the stub as it is installed, so the arguments are the
  call's own. The real function is what the attribute held before the first
  patch of the test, so a test re-patching a conftest stub is still checked
  against the production function under both.
- **mock** — a ``unittest.mock`` object replaced a real callable, through
  ``mock.patch`` or ``monkeypatch``. The real function is the patched original.
- **injected** — production received the stub as an argument (``load=``,
  ``run=``, ``provider``) or holds it on an object (``self.provider``). The
  real function is the parameter's default when that is a callable, else the
  seam's entry in :data:`SEAMS`, written by reading what production passes
  there. Without either the site is *unresolved* and the report lists it.
  The stub's frame is all there is to read, so a value it took by keyword is
  matched to the real function's parameter of the same name. Needs
  ``sys.monitoring`` (Python 3.12+); on older Pythons only patched and mock
  calls are recorded.

A call's verdict is **mismatch** when its values do not bind to the real
signature or one is not an instance of its annotation (``Optional``, ``Union``
and generics by their origin; a string annotation that does not resolve is not
checked), **agrees** otherwise, and **unresolved** with no real function. A
value the test built itself — an instance of a class a test module defines, or
a bare ``object()`` — is the test's input rather than production's, and is not
held against an annotation. The
report groups the calls by site — the production function and the seam — with
the worst verdict any call there had.

Measured at #572 (master ``8485302``, 6583 tests): see the PR for the sites
and their verdicts.
"""
from __future__ import annotations

import argparse
import functools
import importlib
import inspect
import json
import os
import sys
import types
import typing
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

try:  # the report runs without pytest; the plugin hooks need it
    import pytest

    _hookimpl = pytest.hookimpl
except ImportError:  # pragma: no cover
    def _hookimpl(**_kw: Any) -> Callable[[Any], Any]:
        return lambda f: f

ROOT = Path(__file__).resolve().parents[1]
_HERE = os.path.realpath(__file__)
ENV = "MNEMO_STUB_CALLS"

MISMATCH, AGREES, UNRESOLVED = "mismatch", "agrees", "unresolved"
_RANK = {AGREES: 0, UNRESOLVED: 1, MISMATCH: 2}

#: What production passes at an injected seam whose parameter has no callable
#: default, read from the production caller at #572. Key: ``file:qualname:seam``
#: (``seam`` is the local name, or ``self.attr``); value: ``module:attr`` of the
#: real callable, or of one with the same signature where production passes a
#: closure around it.
SEAMS: Dict[str, str] = {
    "src/mnemo/autopilot/insights/digest.py:post_digest_issue:_run": "subprocess:run",
    "src/mnemo/core/ci_corrections.py:rules_from_github:changed_files": "mnemo.core.ci_corrections:_git_changed_files",
    "src/mnemo/core/ci_corrections.py:rules_from_github:fetch_log": "mnemo.core.ci_corrections:_gh_failed_log",
    "src/mnemo/core/backfill/harvest.py:harvest_session:provider": "mnemo.core.llm:_claude_cli",
    "src/mnemo/core/briefing.py:generate_session_briefing:provider": "mnemo.core.llm:_claude_cli",
    "src/mnemo/core/extract/__init__.py:_run_extraction_body:provider": "mnemo.core.llm:_claude_cli",
    "src/mnemo/core/friction/backfill.py:_link:ranker": "mnemo.core.friction.candidates:rank",
    "src/mnemo/core/friction/backfill.py:_link:resolver": "mnemo.core.friction.link:resolve",
    "src/mnemo/core/friction/link.py:resolve:runner": "mnemo.core.friction.link:_default_runner",
    "src/mnemo/core/sessions/child_notices.py:on_session_start:reader": "mnemo.core.sessions.child_notices:_read_sessions",
    "src/mnemo/core/sessions/child_notices.py:on_session_start:spawn": "mnemo.core.sessions.child_notices:_spawn_watcher",
    "src/mnemo/core/sessions/child_notices.py:sweep:reader": "mnemo.core.sessions.child_notices:_read_sessions",
    "src/mnemo/core/sessions/child_notices.py:sweep:tell": "mnemo.core.sessions.child_notices:_tell",
    "src/mnemo/core/sessions/child_notices.py:watch:clock": "time:time",
    "src/mnemo/core/sessions/child_notices.py:watch:sleeper": "time:sleep",
    "src/mnemo/core/sessions/pr_follow.py:_one:wake_fn": "mnemo.core.sessions.wake:wake_for_pr",
    "src/mnemo/core/sessions/pr_follow.py:_one:tell": "mnemo.core.sessions.pr_follow:_notify",
    "src/mnemo/core/sessions/pr_follow.py:_close:tell": "mnemo.core.sessions.pr_follow:_notify",
    "src/mnemo/core/sessions/pr_follow.py:on_session_end:spawn": "mnemo.core.sessions.pr_follow:_spawn_watcher",
    "src/mnemo/core/sessions/pr_follow.py:on_session_start:spawn": "mnemo.core.sessions.pr_follow:_spawn_watcher",
    "src/mnemo/core/sessions/pr_follow.py:sweep:reader": "mnemo.core.sessions.pr_follow:_read_sessions",
    "src/mnemo/core/sessions/pr_follow.py:watch:clock": "time:time",
    "src/mnemo/core/sessions/pr_follow.py:watch:sleeper": "time:sleep",
    "src/mnemo/core/sessions/report_card.py:_git:run": "mnemo.core.sessions.report_card:_run",
    "src/mnemo/core/sessions/rewake.py:_wake_one:wake_fn": "mnemo.core.sessions.wake:wake",
    "src/mnemo/core/sessions/rewake.py:on_session_start:spawn": "mnemo.core.sessions.rewake:_spawn_watcher",
    "src/mnemo/core/sessions/rewake.py:watch:clock": "time:time",
    "src/mnemo/core/sessions/rewake.py:watch:reader": "mnemo.core.sessions.rewake:_read_sessions",
    "src/mnemo/core/sessions/rewake.py:watch:sleeper": "time:sleep",
    "src/mnemo/core/sessions/tree_sweep.py:_git_ok:run": "mnemo.core.sessions.tree_sweep:_run",
    "src/mnemo/core/sessions/tree_sweep.py:sweep:reader": "mnemo.core.sessions.tree_sweep:_read_sessions",
    "src/mnemo/core/twins.py:_fresh_tags:draw": "mnemo.core.dispatch:new_twin_tag",
    "tools/measure_briefing_judge_pick.py:timed.<locals>.call:provider": "mnemo.core.llm:_claude_cli",
    "tools/measure_briefing_judge_pick.py:timed.<locals>.call:clock": "time:monotonic",
    "tools/measure_project_gate.py:main.<locals>.ask:provider": "mnemo.core.llm:_claude_cli",
    "tools/measure_reflex_reach.py:main:provider": "mnemo.core.llm:_claude_cli",
    "tools/measure_repeated_corrections.py:run_calls:provider": "mnemo.core.llm:_claude_cli",
    "tools/measure_rule_lift.py:main:provider": "mnemo.core.llm:_claude_cli",
    "tools/measure_prevented_repeats.py:Sender.run.<locals>.worker:self.provider": "mnemo.core.llm:_claude_cli",
    "tools/measure_dispatch_outcomes.py:changed_files:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:check_state:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:classify_red:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:failure_kind:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:followup:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:pr_facts:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:pr_for_branch:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:repo_for:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_dispatch_outcomes.py:cached.<locals>._run:run": "mnemo.core.sessions.report_card:_run",
    "tools/measure_full_body_fresh.py:historical_yield:load": "mnemo.core.briefing:_load_jsonl_events",
    "tools/measure_recall_judged.py:units_from_log:parse_ts": "mnemo.core.mcp.recall:_parse_ts",
    "tools/measure_rerank_judges.py:judge_chunk:caller": "tools.measure_rerank_judges:_cli_caller",
    "tools/measure_rule_lift.py:prompt_in_context:uid_of": "tools.measure_reflex_gate:unit_id",
    "tools/measure_reflex_gate.py:gate_row:keep": "tools.measure_reflex_gate:shipped_slugs",
}


# --- checking one call ---------------------------------------------------------------------------

def type_name(value: Any) -> str:
    """``str``, ``pathlib.PosixPath``, ``list[str]`` (by the first item)."""
    t = type(value)
    name = t.__qualname__ if t.__module__ == "builtins" else t.__module__ + "." + t.__qualname__
    if isinstance(value, (list, tuple, set, frozenset)) and value:
        name += "[" + type_name(next(iter(value))) + "]"
    return name


_UNION_TYPES = tuple(t for t in (getattr(types, "UnionType", None),) if t is not None)


def type_ok(value: Any, hint: Any) -> bool:
    """Is *value* an instance of the annotation *hint*, as far as it can tell?"""
    if hint is Any or hint is inspect.Parameter.empty or isinstance(hint, (str, typing.ForwardRef)):
        return True
    if hint is None or hint is type(None):
        return value is None
    origin = typing.get_origin(hint)
    if origin is typing.Union or (_UNION_TYPES and isinstance(hint, _UNION_TYPES)):
        return any(type_ok(value, a) for a in typing.get_args(hint))
    if origin is typing.Literal:
        return value in typing.get_args(hint)
    if origin is not None:
        import collections.abc as cabc

        if origin is cabc.Callable:
            return callable(value)
        hint = origin
    if not isinstance(hint, type):
        return True
    if hint is float and isinstance(value, int):
        return True
    try:
        return isinstance(value, hint) or type(value).__module__ == "unittest.mock"
    except TypeError:
        return True


def test_made(value: Any) -> bool:
    """A value the test built — an instance of a class a test module defines,
    or a bare ``object()`` placeholder — is the test's input, not something
    production passes; it is not held against the real function's annotation."""
    t = type(value)
    return t is object or t.__module__.split(".")[0] == "tests" or "test_" in t.__module__


def check(real: Callable[..., Any], args: Sequence[Any], kwargs: Dict[str, Any],
          bound: bool = False) -> List[str]:
    """Why *args*/*kwargs* would not do for *real*, one line each; ``[]`` when
    they would. *bound*: *real* is a function called as a method, its first
    parameter already filled."""
    try:
        sig = inspect.signature(real)
    except (TypeError, ValueError):
        return []
    if bound:
        sig = sig.replace(parameters=list(sig.parameters.values())[1:])
    try:
        got = sig.bind(*args, **kwargs)
    except TypeError as exc:
        return ["bind: %s" % exc]
    try:
        hints = typing.get_type_hints(real.__init__ if isinstance(real, type) else real)
    except Exception:
        hints = {}
    out = []
    for name, value in got.arguments.items():
        param = sig.parameters[name]
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        hint = hints.get(name, inspect.Parameter.empty)
        if not type_ok(value, hint) and not test_made(value):
            out.append("%s: got %s, the real function takes %s" % (
                name, type_name(value), getattr(hint, "__name__", None) or repr(hint)))
    return out


def by_name(real: Callable[..., Any], names: Sequence[str], values: Dict[str, Any],
            extra_args: Sequence[Any] = (), extra_kwargs: Optional[Dict[str, Any]] = None
            ) -> Tuple[List[Any], Dict[str, Any]]:
    """Arguments for *real* from what a stub's frame holds: its named
    parameters in order, by keyword from the first one *real* takes only by
    keyword, then whatever its ``*args``/``**kwargs`` caught."""
    try:
        params = inspect.signature(real).parameters
    except (TypeError, ValueError):
        params = {}  # type: ignore[assignment]
    args: List[Any] = []
    kwargs: Dict[str, Any] = {}
    for name in names:
        p = params.get(name)
        if kwargs or (p is not None and p.kind == p.KEYWORD_ONLY):
            kwargs[name] = values.get(name)
        else:
            args.append(values.get(name))
    args.extend(extra_args)
    kwargs.update(extra_kwargs or {})
    return args, kwargs


def load(spec: str) -> Callable[..., Any]:
    """``module:attr.path`` -> the object."""
    mod, _, attr = spec.partition(":")
    obj: Any = importlib.import_module(mod)
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


def _qual(obj: Any) -> str:
    return "%s.%s" % (getattr(obj, "__module__", "?"), getattr(obj, "__qualname__", type(obj).__name__))


# --- recording ------------------------------------------------------------------------------------

def _code_qualname(code: types.CodeType) -> str:
    return getattr(code, "co_qualname", code.co_name)  # 3.11+


class Recorder:
    """Records production calls into test-defined callables while installed.

    *prod* and *tests* are directories under *root*; a frame belongs to one by
    its file. Rows land in :attr:`rows`, one per distinct (site, real, argument
    types, verdict)."""

    def __init__(self, root: Path = ROOT, prod: Sequence[str] = ("src", "tools"),
                 tests: str = "tests", seams: Optional[Dict[str, str]] = None) -> None:
        self.root = Path(os.path.realpath(root))
        self.prod = tuple(str(self.root / p) + os.sep for p in prod)
        self.tests = str(self.root / tests) + os.sep
        self.seams = SEAMS if seams is None else seams
        self.rows: List[Dict[str, Any]] = []
        self.test: Optional[str] = None
        self._seen: set = set()
        self._where: Dict[str, str] = {}
        self._mock_real: Dict[int, Tuple[Any, Callable[..., Any], bool]] = {}
        self._first: Dict[Tuple[int, str], Any] = {}
        self._code_real: Dict[types.CodeType, List[Tuple[Any, str]]] = {}
        self._undo: List[Callable[[], None]] = []
        self._tool: Optional[int] = None
        self._wrapper_code: Optional[types.CodeType] = None

    # where a file belongs
    def _kind(self, filename: Optional[str]) -> str:
        if not filename:
            return ""
        if filename not in self._where:
            path = os.path.realpath(filename)
            self._where[filename] = ("" if path == _HERE
                                     else "tests" if path.startswith(self.tests)
                                     else "prod" if path.startswith(self.prod) else "")
        return self._where[filename]

    def _site(self, frame: types.FrameType) -> str:
        path = os.path.relpath(os.path.realpath(frame.f_code.co_filename), self.root)
        return "%s:%s" % (path.replace(os.sep, "/"), _code_qualname(frame.f_code))

    def _row(self, kind: str, stub: str, caller: types.FrameType, seam: str,
             real: Optional[Callable[..., Any]], args: Sequence[Any], kwargs: Dict[str, Any],
             bound: bool = False) -> None:
        problems = check(real, args, kwargs, bound) if real is not None else []
        verdict = UNRESOLVED if real is None else MISMATCH if problems else AGREES
        row = {"kind": kind, "site": self._site(caller), "seam": seam,
               "real": _qual(real) if real is not None else None,
               "args": [type_name(a) for a in args],
               "kwargs": {k: type_name(v) for k, v in sorted(kwargs.items())},
               "problems": problems, "verdict": verdict, "stub": stub, "test": self.test}
        key = json.dumps([row[k] for k in ("kind", "site", "seam", "real", "args", "kwargs", "problems")])
        if key not in self._seen:
            self._seen.add(key)
            self.rows.append(row)

    # patched: monkeypatch.setattr of a test function over a real one
    def _wrap(self, stub: types.FunctionType, real: Callable[..., Any], seam: str) -> Callable[..., Any]:
        rec = self

        @functools.wraps(stub)
        def recorded(*args: Any, **kwargs: Any) -> Any:
            caller = sys._getframe(1)
            if rec._kind(caller.f_code.co_filename) == "prod":
                rec._row("patched", _code_qualname(stub.__code__), caller, seam, real, args, kwargs)
            return stub(*args, **kwargs)

        self._wrapper_code = recorded.__code__
        return recorded

    def _on_setattr(self, mp: Any) -> None:
        from unittest import mock

        obj, name, old = mp._setattr[-1]
        for o, n, v in mp._setattr:  # the first patch of this attribute in the test
            if o is obj and n == name:
                old = v
                break
        key = (id(obj), name)
        if self._kind(getattr(getattr(old, "__code__", None), "co_filename", None)) == "tests":
            old = self._first.get(key, old)
        else:
            self._first[key] = old
        new = (obj.__dict__.get(name) if isinstance(obj, type) else getattr(obj, name, None))
        method = isinstance(obj, type) and isinstance(old, types.FunctionType)
        if isinstance(old, (classmethod, staticmethod)) and isinstance(obj, type):
            old = old.__get__(None, obj)  # what calling it through the class reaches
        if not callable(old):
            return
        seam = "%s.%s" % (getattr(obj, "__name__", type(obj).__name__), name)
        if isinstance(new, types.FunctionType) and self._kind(new.__code__.co_filename) == "tests":
            setattr(obj, name, self._wrap(new, old, seam))
        elif isinstance(new, (classmethod, staticmethod)):
            code = getattr(new.__func__, "__code__", None)
            if code is not None and self._kind(code.co_filename) == "tests":
                self._code_real.setdefault(code, []).append((old, seam))
        elif isinstance(new, type):
            for hook in ("__init__", "__call__"):
                code = getattr(new.__dict__.get(hook), "__code__", None)
                if code is not None and self._kind(code.co_filename) == "tests":
                    self._code_real.setdefault(code, []).append((old, seam))
        elif isinstance(new, mock.NonCallableMock):
            self._mock_real[id(new)] = (new, old, method)
        elif callable(new) and not isinstance(new, (types.FunctionType, types.BuiltinFunctionType)):
            code = getattr(getattr(type(new), "__call__", None), "__code__", None)
            if code is not None and self._kind(code.co_filename) == "tests":
                self._code_real.setdefault(code, []).append((old, seam))

    # mock: a Mock called from production
    def _on_mock_call(self, m: Any, caller: types.FrameType, args: Sequence[Any],
                      kwargs: Dict[str, Any]) -> None:
        if self._kind(caller.f_code.co_filename) != "prod":
            return
        held, real, bound = self._mock_real.get(id(m), (None, None, False))
        if held is not m:  # an id a collected mock left behind
            real, bound = None, False
        self._row("mock", "Mock(%s)" % (m._extract_mock_name(),), caller,
                  _qual(real) if real is not None else m._extract_mock_name(), real, args, kwargs, bound)

    # injected: sys.monitoring sees a test function start; who called it?
    def _on_start(self, code: types.CodeType, _offset: int) -> Any:
        if self._kind(code.co_filename) != "tests" or code.co_name == "<genexpr>":
            return self._mon.DISABLE
        try:
            frame = sys._getframe(1)
            caller = frame.f_back
            if (frame.f_code is not code or caller is None or caller.f_code is self._wrapper_code
                    or self._kind(caller.f_code.co_filename) != "prod"):
                return None
            self._injected(code, frame, caller)
        except Exception as exc:  # never break the run
            self.rows.append({"kind": "error", "error": repr(exc), "test": self.test})
        return None

    def _injected(self, code: types.CodeType, frame: types.FrameType, caller: types.FrameType) -> None:
        loc = frame.f_locals
        n = code.co_argcount + code.co_kwonlyargcount
        names = list(code.co_varnames[:n])
        me = loc.get("self") if names[:1] == ["self"] else None
        if me is not None or (names[:1] == ["cls"] and isinstance(loc.get("cls"), type)):
            names = names[1:]
        patched = self._code_real.get(code)
        if patched:  # a test class or classmethod patched over a real callable
            # One stub can stand in for several (a spy on both subprocess.run
            # and Popen): the call is held to whichever its arguments fit.
            tries = [(real, where) + by_name(real, names, loc, *self._rest(code, loc))
                     for real, where in patched]
            real, where, args, kwargs = next(
                (t for t in tries if not check(t[0], t[2], t[3])), tries[0])
            self._row("patched", _code_qualname(code), caller, where, real, args, kwargs)
            return
        seam = next((k for k, v in caller.f_locals.items() if getattr(v, "__code__", None) is code), None)
        if seam is None and me is not None:
            seam = next((k for k, v in caller.f_locals.items() if v is me), None)
        held = lambda v: getattr(v, "__code__", None) is code or (me is not None and v is me)  # noqa: E731
        if seam is None:
            for k, v in caller.f_locals.items():
                attrs = getattr(v, "__dict__", None)
                if isinstance(attrs, dict) and not isinstance(v, type):
                    hit = next((a for a, w in attrs.items() if held(w)), None)
                    if hit is not None:
                        seam = "%s.%s" % (k, hit)
                        break
        real = self._real_for(caller, seam) if seam else None
        extra_args, extra_kwargs = self._rest(code, loc)
        if real is not None:
            args, kwargs = by_name(real, names, loc, extra_args, extra_kwargs)
        else:
            args, kwargs = [loc.get(k) for k in names] + list(extra_args), extra_kwargs
        self._row("injected", _code_qualname(code), caller, seam or "?", real, args, kwargs)

    @staticmethod
    def _rest(code: types.CodeType, loc: Dict[str, Any]) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
        """What the stub's ``*args`` and ``**kwargs`` caught."""
        idx = code.co_argcount + code.co_kwonlyargcount
        extra_args: Tuple[Any, ...] = ()
        extra_kwargs: Dict[str, Any] = {}
        if code.co_flags & inspect.CO_VARARGS:
            extra_args = tuple(loc.get(code.co_varnames[idx]) or ())
            idx += 1
        if code.co_flags & inspect.CO_VARKEYWORDS:
            extra_kwargs = dict(loc.get(code.co_varnames[idx]) or {})
        return extra_args, extra_kwargs

    def _real_for(self, caller: types.FrameType, seam: str) -> Optional[Callable[..., Any]]:
        spec = self.seams.get("%s:%s" % (self._site(caller), seam))
        if spec:
            return load(spec)
        if "." in seam:
            return None
        fn: Any = caller.f_globals.get("__name__") and sys.modules.get(caller.f_globals["__name__"])
        for part in _code_qualname(caller.f_code).split("."):
            fn = getattr(fn, part, None)
        if getattr(fn, "__code__", None) is not caller.f_code:  # patched in this test: the original
            fn = next((v for v in self._first.values() if getattr(v, "__code__", None) is caller.f_code), None)
        if fn is None:
            return None
        try:
            default = inspect.signature(fn).parameters[seam].default
        except (TypeError, ValueError, KeyError):
            return None
        return default if callable(default) and default is not inspect.Parameter.empty else None

    def reset(self, test: Optional[str] = None) -> None:
        """A new test: its patches are its own, and ids may be reused."""
        self.test = test
        self._mock_real.clear()
        self._first.clear()
        self._code_real.clear()

    def install(self) -> "Recorder":
        import _pytest.monkeypatch as mpmod
        from unittest import mock

        rec = self
        orig_setattr = mpmod.MonkeyPatch.setattr

        def setattr_(mp: Any, *a: Any, **k: Any) -> Any:
            out = orig_setattr(mp, *a, **k)
            try:
                rec._on_setattr(mp)
            except Exception as exc:
                rec.rows.append({"kind": "error", "error": repr(exc), "test": rec.test})
            return out

        mpmod.MonkeyPatch.setattr = setattr_  # type: ignore[method-assign]
        self._undo.append(lambda: setattr(mpmod.MonkeyPatch, "setattr", orig_setattr))

        orig_call = mock.CallableMixin.__call__

        def call_(m: Any, *a: Any, **k: Any) -> Any:
            try:
                rec._on_mock_call(m, sys._getframe(1), a, k)
            except Exception as exc:
                rec.rows.append({"kind": "error", "error": repr(exc), "test": rec.test})
            return orig_call(m, *a, **k)

        mock.CallableMixin.__call__ = call_  # type: ignore[method-assign]
        self._undo.append(lambda: setattr(mock.CallableMixin, "__call__", orig_call))

        orig_enter = mock._patch.__enter__

        def enter_(p: Any) -> Any:
            new = orig_enter(p)
            original = getattr(p, "temp_original", None)
            if isinstance(original, (classmethod, staticmethod)) and isinstance(p.target, type):
                original = original.__get__(None, p.target)
            if isinstance(new, mock.NonCallableMock) and callable(original):
                rec._mock_real[id(new)] = (new, original, isinstance(p.target, type)
                                           and isinstance(original, types.FunctionType))
            return new

        mock._patch.__enter__ = enter_  # type: ignore[method-assign]
        self._undo.append(lambda: setattr(mock._patch, "__enter__", orig_enter))

        mon = getattr(sys, "monitoring", None)
        if mon is not None:
            self._mon = mon
            tool = next(i for i in (3, 4, 2, 1, 0, 5) if mon.get_tool(i) is None)
            mon.use_tool_id(tool, "measure_stub_calls")
            mon.register_callback(tool, mon.events.PY_START, self._on_start)
            mon.set_events(tool, mon.events.PY_START)
            self._tool = tool

            def off() -> None:
                mon.set_events(tool, 0)
                mon.register_callback(tool, mon.events.PY_START, None)
                mon.free_tool_id(tool)

            self._undo.append(off)
        return self

    def uninstall(self) -> None:
        while self._undo:
            self._undo.pop()()


# --- the pytest plugin ---------------------------------------------------------------------------

_ACTIVE: Dict[str, Any] = {}


def pytest_configure(config: Any) -> None:
    out = os.environ.get(ENV)
    if out and "recorder" not in _ACTIVE:
        _ACTIVE["recorder"] = Recorder().install()
        _ACTIVE["out"] = out


@_hookimpl(tryfirst=True)
def pytest_runtest_setup(item: Any) -> None:
    rec = _ACTIVE.get("recorder")
    if rec is not None:
        rec.reset(item.nodeid)


def pytest_unconfigure(config: Any) -> None:
    rec = _ACTIVE.pop("recorder", None)
    if rec is None:
        return
    rec.uninstall()
    with open(_ACTIVE.pop("out"), "w", encoding="utf-8") as fh:
        for row in rec.rows:
            fh.write(json.dumps(row) + "\n")


# --- the report ----------------------------------------------------------------------------------

def sites(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per (site, seam): its worst verdict, the real function, how
    many distinct calls, and every problem seen there. Mismatches first."""
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for r in rows:
        if r.get("kind") == "error":
            continue
        s = out.setdefault((r["site"], r["seam"]), {
            "site": r["site"], "seam": r["seam"], "kind": r["kind"], "real": r["real"],
            "calls": 0, "verdict": AGREES, "problems": [], "tests": []})
        s["calls"] += 1
        s["real"] = s["real"] or r["real"]
        if _RANK[r["verdict"]] > _RANK[s["verdict"]]:
            s["verdict"] = r["verdict"]
        s["problems"] += [p for p in r["problems"] if p not in s["problems"]]
        if r.get("test") and r["test"] not in s["tests"]:
            s["tests"].append(r["test"])
    return sorted(out.values(), key=lambda s: (-_RANK[s["verdict"]], s["kind"], s["site"], s["seam"]))


def report(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    found = sites(rows)
    counts: Dict[str, Dict[str, int]] = {}
    for s in found:
        counts.setdefault(s["kind"], {AGREES: 0, UNRESOLVED: 0, MISMATCH: 0})[s["verdict"]] += 1
    return {"sites": found, "counts": counts,
            "errors": [r for r in rows if r.get("kind") == "error"]}


def render(rep: Dict[str, Any]) -> str:
    lines = []
    for kind, c in sorted(rep["counts"].items()):
        lines.append("%-9s %4d sites: %d mismatch, %d agree, %d unresolved" % (
            kind, sum(c.values()), c[MISMATCH], c[AGREES], c[UNRESOLVED]))
    if rep["errors"]:
        lines.append("recorder errors: %d" % len(rep["errors"]))
    for s in rep["sites"]:
        lines.append("%-10s %-8s %s [%s] -> %s (%d)" % (
            s["verdict"], s["kind"], s["site"], s["seam"], s["real"] or "?", s["calls"]))
        lines += ["    " + p for p in s["problems"]]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("calls", help="the JSONL the plugin wrote ($%s)" % ENV)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = [json.loads(line) for line in Path(args.calls).read_text(encoding="utf-8").splitlines() if line]
    rep = report(rows)
    print(json.dumps(rep, indent=2) if args.json else render(rep))
    return 1 if any(s["verdict"] == MISMATCH for s in rep["sites"]) else 0


if __name__ == "__main__":
    sys.exit(main())
