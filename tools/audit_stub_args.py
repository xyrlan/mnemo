"""Which test stubs does production code call, and with what? (#572)

Usage:
    PYTHONPATH=src python3 -m pytest -q -p tools.audit_stub_args --stub-audit=/tmp/stubs.json
    python3 tools/audit_stub_args.py /tmp/stubs.json      # the report, as text

#562's crash hid behind ``lambda p: events``: a stub that ignored its
argument stood in for a loader that calls ``path.read_text()``, and production
handed it a ``str``. A grep finds lambdas; it cannot say which of them
production calls, or with what. This plugin watches the suite run instead and
records every call **from production code** (``src/`` or ``tools/``) **into a
function defined under** ``tests/``. Two kinds:

- **patched** — the stub replaced a real function through
  ``monkeypatch.setattr``. The plugin knows the real function, so each call is
  checked against it: the arguments must bind to its signature, and each bound
  value must satisfy the real parameter's annotation (:func:`accepts`, a
  lenient runtime reading: unresolvable hints pass). A failure is a
  **candidate** mismatch, not a verdict: the value may have come from the test
  rather than from production, and an annotation can be narrower than what the
  body accepts. Each candidate is then run against the real function.
- **injected** — the stub was handed to production as an argument (a loader,
  an ``ask`` callable, a fake client's method). The plugin records which
  parameter of the calling function held it, that parameter's default when it
  is a callable (then the default is the real function and is checked as
  above), and the argument types seen. Without a default the real function is
  whatever ``main`` wires in, which takes a reader.

Every row carries the stub's parameters that its body never reads: the stubs
that, like #562's, would accept anything at all.
"""
from __future__ import annotations

import ast
import collections.abc
import dis
import functools
import importlib
import inspect
import json
import os
import sys
import threading
import types
import typing
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
TESTS = str(ROOT / "tests") + os.sep
PRODUCTION = (str(ROOT / "src") + os.sep, str(ROOT / "tools") + os.sep)
SELF = str(Path(__file__).resolve())

_MAX_ELEMENTS = 50


# --- reading values against hints ---------------------------------------------------------------

def accepts(hint: Any, value: Any) -> bool:
    """Whether ``value`` satisfies ``hint`` at runtime, leniently.

    Unions, ``Optional``, ``Literal``, ``Callable`` and the containers are
    read (elements up to :data:`_MAX_ELEMENTS`); what cannot be checked —
    forward references, type variables, non-runtime protocols — passes. An
    ``int`` is a ``float`` (PEP 484's numeric tower).
    """
    if hint is Any or hint is object or isinstance(hint, (str, typing.TypeVar, typing.ForwardRef)):
        return True
    if hint is None or hint is type(None):
        return value is None
    supertype = getattr(hint, "__supertype__", None)
    if supertype is not None:
        return accepts(supertype, value)
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Union or (sys.version_info >= (3, 10) and origin is types.UnionType):
        return any(accepts(a, value) for a in args)
    if origin is typing.Literal:
        return value in args
    if origin is collections.abc.Callable:
        return callable(value)
    if origin is not None:
        if not isinstance(origin, type):
            return True
        if not _isinstance(value, origin):
            return False
        return _elements_accept(origin, args, value)
    if hint is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if hint is complex:
        return isinstance(value, (int, float, complex)) and not isinstance(value, bool)
    if isinstance(hint, type):
        return _isinstance(value, hint)
    return True


def _isinstance(value: Any, cls: type) -> bool:
    try:
        return isinstance(value, cls)
    except TypeError:  # a protocol that is not runtime_checkable
        return True


def _elements_accept(origin: type, args: Tuple[Any, ...], value: Any) -> bool:
    if not args:
        return True
    if issubclass(origin, tuple):
        if len(args) == 2 and args[1] is Ellipsis:
            return all(accepts(args[0], v) for v in value[:_MAX_ELEMENTS])
        if args == ((),):
            return value == ()
        return len(args) == len(value) and all(accepts(a, v) for a, v in zip(args, value))
    if isinstance(value, collections.abc.Mapping) and len(args) == 2:
        items = list(value.items())[:_MAX_ELEMENTS]
        return all(accepts(args[0], k) and accepts(args[1], v) for k, v in items)
    if isinstance(value, (list, tuple, set, frozenset)) and len(args) == 1:
        return all(accepts(args[0], v) for v in list(value)[:_MAX_ELEMENTS])
    return True  # an iterator or a generator: reading it would consume it


def describe(value: Any) -> str:
    """A short type name for a report: ``str``, ``PosixPath``, ``list[str]``."""
    name = type(value).__name__
    if isinstance(value, (list, tuple, set, frozenset)) and value:
        inner = sorted({type(v).__name__ for v in list(value)[:_MAX_ELEMENTS]})
        return "%s[%s]" % (name, "|".join(inner[:3]))
    if isinstance(value, dict) and value:
        k, v = next(iter(value.items()))
        return "dict[%s, %s]" % (type(k).__name__, type(v).__name__)
    return name


def check_call(real: Callable, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Optional[str]:
    """Why the real function would not take this call, or None if it would."""
    try:
        sig = inspect.signature(real)
    except (TypeError, ValueError):
        return None
    try:
        bound = sig.bind(*args, **kwargs)
    except TypeError as exc:
        return "does not bind to %s%s: %s" % (_name(real), sig, exc)
    hints = _hints(real)
    problems = []
    for name, value in bound.arguments.items():
        if name not in hints:
            continue
        kind = sig.parameters[name].kind
        values = (value if kind == inspect.Parameter.VAR_POSITIONAL
                  else list(value.values()) if kind == inspect.Parameter.VAR_KEYWORD else [value])
        for v in values:
            if not accepts(hints[name], v):
                problems.append("%s=%s (wants %s)" % (name, describe(v), _hint_name(hints[name])))
    return "; ".join(problems) or None


def _hints(real: Callable) -> Dict[str, Any]:
    target = getattr(real, "__func__", real)
    try:
        return typing.get_type_hints(target)
    except Exception:  # an annotation that does not evaluate here: check none of them
        return {}


def _hint_name(hint: Any) -> str:
    if typing.get_args(hint) or not hasattr(hint, "__name__"):
        return str(hint).replace("typing.", "")
    return hint.__name__


def _name(fn: Any) -> str:
    fn = getattr(fn, "__func__", fn)
    return "%s.%s" % (getattr(fn, "__module__", "?"), getattr(fn, "__qualname__", repr(fn)))


def unread_params(code: types.CodeType) -> List[str]:
    """The parameters a function's body never reads."""
    n = code.co_argcount + code.co_kwonlyargcount
    n += bool(code.co_flags & inspect.CO_VARARGS) + bool(code.co_flags & inspect.CO_VARKEYWORDS)
    params = code.co_varnames[:n]
    read = set(code.co_cellvars)
    for ins in dis.get_instructions(code):
        if ins.opname.startswith(("LOAD_FAST", "LOAD_DEREF", "LOAD_CLOSURE", "DELETE_FAST")):
            read.update(ins.argval if isinstance(ins.argval, tuple) else (ins.argval,))
    return [p for p in params if p not in read]


# --- where code lives ---------------------------------------------------------------------------

def _rel(path: str) -> str:
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return path


def _is_test(path: str) -> bool:
    return path.startswith(TESTS)


def _is_production(path: str) -> bool:
    return path.startswith(PRODUCTION) and path != SELF


def _site(code: types.CodeType, lineno: Optional[int] = None) -> str:
    return "%s:%d" % (_rel(code.co_filename), lineno or code.co_firstlineno)


# --- the record ---------------------------------------------------------------------------------

class Audit:
    def __init__(self) -> None:
        self.rows: Dict[Tuple[str, ...], Dict[str, Any]] = {}
        self.test = ""
        self.patched_codes: set = set()
        self.mocks: Dict[int, Tuple[Any, Callable, str, str]] = {}
        self._lock = threading.Lock()

    def _row(self, key: Tuple[str, ...], **fields: Any) -> Dict[str, Any]:
        row = self.rows.get(key)
        if row is None:
            row = self.rows[key] = dict(fields, calls=0, types=[], problems=[], tests=[])
        return row

    def note(self, row: Dict[str, Any], types_seen: str, problem: Optional[str]) -> None:
        row["calls"] += 1
        if types_seen not in row["types"] and len(row["types"]) < 10:
            row["types"].append(types_seen)
        if problem and problem not in row["problems"] and len(row["problems"]) < 10:
            row["problems"].append(problem)
        if self.test and self.test not in row["tests"] and len(row["tests"]) < 5:
            row["tests"].append(self.test)

    # patched: monkeypatch.setattr or mock.patch replaced a real function
    def patched(self, stub_site: str, unread: List[str], real: Callable, where: str,
                caller: types.FrameType, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> None:
        key = ("patched", stub_site, _site(caller.f_code, caller.f_lineno))
        seen = ", ".join([describe(a) for a in args] + ["%s=%s" % (k, describe(v)) for k, v in kwargs.items()])
        with self._lock:
            row = self._row(key, kind="patched", stub=stub_site, caller=key[2],
                            caller_function=caller.f_code.co_name, real=_name(real),
                            target=where, unread=unread)
            self.note(row, seen, check_call(real, args, kwargs))

    def wrap(self, stub: Callable, real: Callable, where: str) -> Callable:
        code = stub.__code__
        self.patched_codes.add(code)
        unread = unread_params(code)

        @functools.wraps(stub)
        def audited(*args: Any, **kwargs: Any) -> Any:
            caller = sys._getframe(1)
            if _is_production(caller.f_code.co_filename):
                self.patched(_site(code), unread, real, where, caller, args, kwargs)
            return stub(*args, **kwargs)

        return audited

    # injected: production called a test function it was handed
    def on_start(self, code: types.CodeType, frame: types.FrameType) -> None:
        caller = frame.f_back
        if caller is None or not _is_production(caller.f_code.co_filename):
            return
        owner = caller  # a generator expression or a lambda calls on behalf of its function
        while (owner.f_code.co_name.startswith("<") and owner.f_back is not None
               and _is_production(owner.f_back.f_code.co_filename)):
            owner = owner.f_back
        holders, real = _holders(owner, code)
        wired = _wired(owner, holders[0]) if holders else []
        reals = [real] if real is not None else []
        reals += [fn for _, fn in wired if fn is not None and fn not in reals]
        key = ("injected", _site(code), _site(caller.f_code, caller.f_lineno))
        args, kwargs = _call_values(code, frame.f_locals)
        if _is_method(code):
            args = args[1:]
        seen = ", ".join([describe(a) for a in args] + ["%s=%s" % (k, describe(v)) for k, v in kwargs.items()])
        problems = []
        for fn in reals:
            problem = _check_either(fn, code, args, kwargs)
            if problem:
                problems.append(problem)
        with self._lock:
            row = self._row(key, kind="injected", stub=key[1], stub_name=getattr(code, "co_qualname", code.co_name),
                            caller=key[2], caller_function=owner.f_code.co_name, holders=holders,
                            real=", ".join(_name(fn) for fn in reals) or None,
                            wired=sorted({text for text, _ in wired}), unread=unread_params(code))
            self.note(row, seen, "; ".join(problems) or None)

    def report(self) -> List[Dict[str, Any]]:
        return sorted(self.rows.values(), key=lambda r: (r["kind"], r["caller"], r["stub"]))


def _check_either(real: Callable, code: types.CodeType, args: List[Any], kwargs: Dict[str, Any]) -> Optional[str]:
    """A stub's frame shows its parameters' values, not whether each came by
    position or by keyword. Its named parameters are tried both ways; only a
    call that fits neither is a candidate."""
    problem = check_call(real, tuple(args), kwargs)
    if problem is None:
        return None
    names = code.co_varnames[int(_is_method(code)):code.co_argcount]
    if len(args) > len(names):  # a *args tail pins everything before it to positions
        return problem
    problems = [problem]
    for split in range(len(args) - 1, -1, -1):
        moved = dict(zip(names[split:], args[split:]))
        problems.append(check_call(real, tuple(args[:split]), dict(kwargs, **moved)))
        if problems[-1] is None:
            return None
    # a call that binds one way and carries a wrong type says more than one that binds no way
    return next((p for p in problems if not p.startswith("does not bind")), problem)


def _is_method(code: types.CodeType) -> bool:
    return bool(code.co_argcount) and code.co_varnames[0] in ("self", "cls")


def _call_values(code: types.CodeType, local: Dict[str, Any]) -> Tuple[List[Any], Dict[str, Any]]:
    names = code.co_varnames
    args = [local.get(n) for n in names[:code.co_argcount]]
    kwargs = {n: local.get(n) for n in names[code.co_argcount:code.co_argcount + code.co_kwonlyargcount]}
    i = code.co_argcount + code.co_kwonlyargcount
    if code.co_flags & inspect.CO_VARARGS:
        args.extend(local.get(names[i]) or ())
        i += 1
    if code.co_flags & inspect.CO_VARKEYWORDS:
        kwargs.update(local.get(names[i]) or {})
    return args, kwargs


def _holds(value: Any, code: types.CodeType) -> bool:
    fn = getattr(value, "__func__", value)
    if getattr(fn, "__code__", None) is code or getattr(getattr(fn, "__wrapped__", None), "__code__", None) is code:
        return True
    try:
        members = vars(type(value)).values()
    except TypeError:
        return False
    return any(getattr(m, "__code__", None) is code for m in members)


def _holders(caller: types.FrameType, code: types.CodeType) -> Tuple[List[str], Optional[Callable]]:
    """The calling function's parameters that hold the stub, and the real
    function when such a parameter defaults to one."""
    ccode = caller.f_code
    n = ccode.co_argcount + ccode.co_kwonlyargcount
    params = ccode.co_varnames[:n]
    holders = [p for p in params if _holds(caller.f_locals.get(p), code)]
    real = None
    fn = caller.f_globals.get(ccode.co_name)
    if holders and inspect.isfunction(fn) and fn.__code__ is ccode:
        default = inspect.signature(fn).parameters[holders[0]].default
        if callable(default) and default is not inspect.Parameter.empty:
            real = default
    return holders, real


_SOURCES: Dict[str, Optional[ast.Module]] = {}


def _production_trees() -> List[Tuple[str, ast.Module]]:
    if not _SOURCES:
        for base in PRODUCTION:
            for path in sorted(Path(base).rglob("*.py")):
                try:
                    _SOURCES[str(path)] = ast.parse(path.read_text(encoding="utf-8"))
                except (OSError, SyntaxError, ValueError):
                    _SOURCES[str(path)] = None
    return [(p, t) for p, t in _SOURCES.items() if t is not None and p != SELF]


_WIRED: Dict[Tuple[str, str, str], List[Tuple[str, Optional[Callable]]]] = {}


def _wired(caller: types.FrameType, holder: str) -> List[Tuple[str, Optional[Callable]]]:
    """What production passes for ``holder`` wherever it calls the calling
    function by name: the expression's text, and the callable it names when
    it is a dotted name resolvable in the calling module."""
    ccode = caller.f_code
    key = (ccode.co_filename, ccode.co_name, holder)
    if key in _WIRED:
        return _WIRED[key]
    fn = caller.f_globals.get(ccode.co_name)
    found: List[Tuple[str, Optional[Callable]]] = []
    if inspect.isfunction(fn) and fn.__code__ is ccode:
        names = list(inspect.signature(fn).parameters)
        index = names.index(holder)
        for path, tree in _production_trees():
            module = _module_of(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                called = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                if called != ccode.co_name:
                    continue
                expr = next((k.value for k in node.keywords if k.arg == holder), None)
                if expr is None and len(node.args) > index and not any(
                        isinstance(a, ast.Starred) for a in node.args[:index + 1]):
                    expr = node.args[index]
                if expr is None:
                    continue
                text = "%s:%d %s" % (_rel(path), node.lineno, ast.unparse(expr) if hasattr(ast, "unparse") else "?")
                found.append((text, _resolve(expr, module, tree)))
    _WIRED[key] = found
    return found


def _imported(name: str, module: types.ModuleType, tree: ast.Module) -> Any:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if (alias.asname or alias.name) == name:
                    base = node.module or ""
                    if node.level:
                        package = (module.__package__ or "").rsplit(".", node.level - 1)[0]
                        base = "%s.%s" % (package, base) if base else package
                    try:
                        return getattr(importlib.import_module(base), alias.name, None)
                    except Exception:
                        return None
    return None


def _module_of(path: str) -> Optional[types.ModuleType]:
    for module in list(sys.modules.values()):
        if getattr(module, "__file__", None) == path:
            return module
    return None


def _resolve(expr: ast.AST, module: Optional[types.ModuleType], tree: ast.Module) -> Optional[Callable]:
    chain = []
    while isinstance(expr, ast.Attribute):
        chain.append(expr.attr)
        expr = expr.value
    if not isinstance(expr, ast.Name) or module is None:
        return None
    value = vars(module).get(expr.id)
    if value is None:  # imported inside a function, as the tools' ``main`` often does
        value = _imported(expr.id, module, tree)
    for attr in reversed(chain):
        value = getattr(value, attr, None)
    return value if inspect.isfunction(value) or inspect.ismethod(value) or inspect.isbuiltin(value) else None


# --- wiring into pytest -------------------------------------------------------------------------

AUDIT = Audit()


def _install_setattr_hook() -> None:
    from _pytest import monkeypatch as mp

    original = mp.MonkeyPatch.setattr
    notset = mp.notset

    def setattr(self, target, name=notset, value=notset, raising=True):  # noqa: A001
        if isinstance(target, str) and value is notset:
            value, (name, obj) = name, mp.derive_importpath(target, raising)
            where = target
        else:
            obj = target
            where = "%s.%s" % (getattr(obj, "__name__", type(obj).__name__), name)
        stub = value
        if (inspect.isfunction(stub) and _is_test(stub.__code__.co_filename)
                and isinstance(name, str) and hasattr(obj, name)):
            static = inspect.getattr_static(obj, name)
            real = getattr(obj, name)
            if (not isinstance(static, (staticmethod, classmethod))
                    and (inspect.isfunction(real) or inspect.ismethod(real))):
                stub = AUDIT.wrap(stub, real, where)
        if isinstance(target, str):
            return original(self, obj, name, stub, raising=raising)
        return original(self, target, name, stub, raising=raising)

    mp.MonkeyPatch.setattr = setattr


def _install_watch() -> None:
    def on_start(code, offset):
        if not _is_test(code.co_filename):
            return DISABLE
        if code not in AUDIT.patched_codes:
            AUDIT.on_start(code, sys._getframe(1))
        return None

    if hasattr(sys, "monitoring"):
        mon = sys.monitoring
        DISABLE = mon.DISABLE
        tool = next(t for t in range(mon.PROFILER_ID, 6) if mon.get_tool(t) is None)
        mon.use_tool_id(tool, "stub-audit")
        mon.register_callback(tool, mon.events.PY_START, on_start)
        mon.set_events(tool, mon.events.PY_START)
        return

    DISABLE = None

    def profile(frame, event, arg):
        if event == "call" and _is_test(frame.f_code.co_filename) and frame.f_code not in AUDIT.patched_codes:
            AUDIT.on_start(frame.f_code, frame)

    sys.setprofile(profile)
    threading.setprofile(profile)


def pytest_addoption(parser):
    parser.addoption("--stub-audit", default=None, help="write the stub audit (#572) to this JSON file")


def _install_mock_hook() -> None:
    """``mock.patch`` too: a function stub is wrapped as above, and a Mock
    that replaced a real function has every production call checked against
    it. Only the Mock itself, not ``.return_value`` chains below it."""
    from unittest import mock

    enter = mock._patch.__enter__
    call = mock.CallableMixin._mock_call
    mock_file = mock.__file__

    def audited_enter(self):
        real = where = None
        try:
            target = self.getter()
            static = inspect.getattr_static(target, self.attribute)
            candidate = getattr(target, self.attribute)
            if (not isinstance(static, (staticmethod, classmethod))
                    and (inspect.isfunction(candidate) or inspect.ismethod(candidate))):
                real = candidate
                where = "%s.%s" % (getattr(target, "__name__", type(target).__name__), self.attribute)
        except Exception:
            pass
        if real is not None and inspect.isfunction(self.new) and _is_test(self.new.__code__.co_filename):
            self.new = AUDIT.wrap(self.new, real, where)
        result = enter(self)
        if real is not None:
            new = getattr(self.getter(), self.attribute, None)
            if isinstance(new, mock.NonCallableMock):
                site = _first_test_frame()
                AUDIT.mocks[id(new)] = (new, real, where, site)
        return result

    def audited_call(self, *args, **kwargs):
        entry = AUDIT.mocks.get(id(self))
        if entry is not None and entry[0] is self:
            caller = sys._getframe(1)
            while caller is not None and caller.f_code.co_filename == mock_file:
                caller = caller.f_back
            if caller is not None and _is_production(caller.f_code.co_filename):
                AUDIT.patched(entry[3], ["(Mock)"], entry[1], entry[2], caller, args, kwargs)
        return call(self, *args, **kwargs)

    mock._patch.__enter__ = audited_enter
    mock.CallableMixin._mock_call = audited_call


def _first_test_frame() -> str:
    frame = sys._getframe(1)
    while frame is not None and not _is_test(frame.f_code.co_filename):
        frame = frame.f_back
    return _site(frame.f_code, frame.f_lineno) if frame is not None else "?"


def pytest_configure(config):
    if config.getoption("--stub-audit"):
        _install_setattr_hook()
        _install_mock_hook()
        _install_watch()


def pytest_runtest_setup(item):
    AUDIT.test = item.nodeid


def pytest_unconfigure(config):
    out = config.getoption("--stub-audit")
    if out:
        Path(out).write_text(json.dumps(AUDIT.report(), indent=1) + "\n", encoding="utf-8")


# --- the report as text -------------------------------------------------------------------------

def render(rows: List[Dict[str, Any]]) -> str:
    lines = []
    for kind in ("patched", "injected"):
        group = [r for r in rows if r["kind"] == kind]
        flagged = [r for r in group if r["problems"]]
        lines.append("## %s: %d production→stub sites, %d with a candidate mismatch" % (kind, len(group), len(flagged)))
        for r in group:
            mark = "!!" if r["problems"] else "  "
            real = r.get("real") or "(real function: see the caller's wiring)"
            held = (" via %s" % ",".join(r["holders"])) if r.get("holders") else ""
            if not r.get("real") and r.get("wired"):
                real = "(wired: %s)" % " | ".join(r["wired"][:3])
            lines.append("%s %s  ← %s %s%s  real=%s  unread=%s  seen=%s"
                         % (mark, r["stub"], r["caller"], r["caller_function"], held, real,
                            ",".join(r["unread"]) or "-", " | ".join(r["types"][:3])))
            for p in r["problems"]:
                lines.append("     %s" % p)
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(__doc__.split("\n\n")[1])
        return 2
    print(render(json.loads(Path(argv[0]).read_text(encoding="utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
