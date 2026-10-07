"""Catch a test writing to the developer's real paths, and only that (#612).

Two channels, because a test can write in two ways:

* **In its own process.** One audit hook (``sys.addaudithook``, 3.8+) sees
  every ``open`` for writing, ``mkdir``, ``rename``, ``remove``... the
  interpreter performs, with the path. A write under a watched root while a
  test is running is that test's write, at any depth -- other processes never
  reach this hook, so a parallel Claude Code session cannot trip it.

* **Through a process it starts.** A child is out of the hook's sight, but
  its start is not (``subprocess.Popen``, ``os.system``, ``os.fork``...), and
  neither are the HOME, argv, cwd and ``MNEMO_*`` environment it gets. A
  child under the test's temp HOME that was handed no watched path cannot find
  one, so only a child that *can* reach the real paths -- real HOME, or a
  watched path in what it was given -- makes the guard ``stat`` them before
  and after. Other sessions append to the real ``.errors.log`` all day; a
  test that ran ``git`` must not be blamed for that.

  ``~/.claude/projects`` -- which every Claude Code session on the machine
  adds to, every dispatched child once per worktree -- is checked after any
  child, and a new entry is blamed only when it is named after one of the
  test's own paths (its temp root or its cwd), the way Claude Code names a
  project directory.

What it does not see: a removed ``~/.claude/projects`` entry; a write a
spawned process makes inside an entry that already existed; a write through
a file descriptor opened before the test began.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set

# Events whose path arguments are written to. Value: indexes of those args.
_WRITE_EVENTS = {
    "os.mkdir": (0,),
    "os.remove": (0,),
    "os.rmdir": (0,),
    "os.rename": (0, 1),  # os.replace raises this one too
    "os.link": (1,),
    "os.symlink": (1,),
    "os.truncate": (0,),
    "os.utime": (0,),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.chflags": (0,),
    "os.lchflags": (0,),
    "os.setxattr": (0,),
    "os.removexattr": (0,),
    "shutil.rmtree": (0,),
    "shutil.copyfile": (1,),
    "shutil.copytree": (1,),
    "sqlite3.connect": (0,),
}
_SPAWN_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.posix_spawn", "os.spawn", "os.exec",
    "os.fork", "os.forkpty", "os.startfile",
})
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

_ACTIVE: List["RealPathGuard"] = []
_HOOKED = False


def encode_project_dir(path: str) -> str:
    """The name Claude Code gives ``~/.claude/projects/<name>`` for a cwd."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


def _norm(p) -> Optional[str]:
    if isinstance(p, int) or p is None:
        return None
    try:
        s = os.fsdecode(os.fspath(p))
    except TypeError:
        return None
    if s == ":memory:" or s.startswith("file:"):
        return None
    return os.path.normcase(os.path.abspath(s))


def _strings(value) -> List[str]:
    if value is None or isinstance(value, int):
        return []
    if isinstance(value, (list, tuple)):
        return [x for v in value for x in _strings(v)]
    try:
        return [os.fsdecode(os.fspath(value))]
    except TypeError:
        return [str(value)]


def _names(text: str, root: str) -> bool:
    """Does ``text`` mention ``root`` itself (not ``~/mnemo-wt-612`` for ``~/mnemo``)?"""
    start = text.find(root)
    while start != -1:
        end = start + len(root)
        if end == len(text) or text[end] in ("/", os.sep, '"', "'", " ", ":", ";", ","):
            return True
        start = text.find(root, start + 1)
    return False


def _spawn_args(event: str, args: tuple):
    """``(argv strings, cwd, env)`` of a process start; env ``None`` = inherited."""
    if event == "subprocess.Popen":  # (executable, args, cwd, env)
        return _strings(args[0]) + _strings(args[1]), args[2], args[3]
    if event in ("os.posix_spawn", "os.exec"):  # (path, argv, env)
        return _strings(args[0]) + _strings(args[1]), None, args[2]
    if event == "os.spawn":  # (mode, path, args, env)
        return _strings(args[1]) + _strings(args[2]), None, args[3]
    return _strings(args[:1]) if event in ("os.system", "os.startfile") else [], None, None


def _hook(event: str, args: tuple) -> None:
    if not _ACTIVE:
        return
    try:
        if event == "open":
            path, mode, flags = args[0], args[1], args[2]
            if isinstance(mode, str):
                if not any(c in mode for c in "wax+"):
                    return
            elif not (flags or 0) & _WRITE_FLAGS:
                return
            paths = (path,)
        elif event in _WRITE_EVENTS:
            paths = tuple(args[i] for i in _WRITE_EVENTS[event] if i < len(args))
        elif event in _SPAWN_EVENTS:
            argv, cwd, env = _spawn_args(event, args)
            for guard in _ACTIVE:
                guard.spawned = True
                if not guard.reachable and guard._reaches(argv, cwd, env):
                    guard.reachable = True
            return
        else:
            return
        for raw in paths:
            p = _norm(raw)
            if p is None:
                continue
            for guard in _ACTIVE:
                guard._see_write(event, p)
    except Exception:  # an audit hook that raises aborts the audited call
        pass


def _install() -> None:
    global _HOOKED
    if not _HOOKED:
        sys.addaudithook(_hook)  # cannot be removed; one hook serves every guard
        _HOOKED = True


class RealPathGuard:
    def __init__(
        self,
        write_roots: Iterable[Path],
        stat_paths: Iterable[Path],
        projects_dir: Path,
        home: Optional[Path] = None,
    ):
        self.write_roots = [Path(p) for p in write_roots]
        self.stat_paths = [Path(p) for p in stat_paths]
        self.projects_dir = Path(projects_dir)
        self._roots = [_norm(p) for p in self.write_roots]
        self._known: Optional[Set[str]] = None
        self.home = _norm(home) if home is not None else None
        self.spawned = False
        self.reachable = False
        self._writes: List[str] = []
        self._own: List[str] = []
        self._before: list = []

    def _see_write(self, event: str, path: str) -> None:
        for root in self._roots:
            if path == root or path.startswith(root.rstrip(os.sep) + os.sep):
                self._writes.append(f"{event} {path}")
                return

    def _reaches(self, argv: List[str], cwd, env) -> bool:
        """Can a child started with these reach a watched path?"""
        env = os.environ if env is None else env
        if self.home is not None:
            for key in ("HOME", "USERPROFILE"):
                value = env.get(key) if hasattr(env, "get") else None
                if value and _norm(value) == self.home:
                    return True
        given = list(argv) + _strings(cwd)
        if hasattr(env, "items"):
            given += [
                v for k, v in ((os.fsdecode(k), os.fsdecode(v)) for k, v in env.items())
                if k.startswith("MNEMO_")
            ]
        return any(_names(os.path.normcase(text), root) for text in given for root in self._roots)

    def _listdir(self) -> Set[str]:
        try:
            return set(os.listdir(str(self.projects_dir)))
        except OSError:
            return set()

    @staticmethod
    def _stat(p: Path):
        try:
            s = p.stat()
            return (s.st_size, s.st_mtime_ns)
        except OSError:
            return None

    def begin(self, own_paths: Iterable[Path]) -> None:
        _install()
        if self._known is None:
            self._known = self._listdir()
        own: Set[str] = set()
        for p in own_paths:
            for variant in (os.path.abspath(str(p)), os.path.realpath(str(p))):
                own.add(encode_project_dir(variant))
        self._own = sorted(own)
        self.spawned = False
        self.reachable = False
        self._writes = []
        self._before = [self._stat(p) for p in self.stat_paths]
        _ACTIVE.append(self)

    def _attributed(self, name: str) -> bool:
        return any(name == o or name.startswith(o + "-") for o in self._own)

    def end(self) -> List[str]:
        """Stop watching; return what the test wrote, one line per write."""
        if self in _ACTIVE:
            _ACTIVE.remove(self)
        violations = list(self._writes)
        if self.reachable:
            for p, before in zip(self.stat_paths, self._before):
                after = self._stat(p)
                if after != before:
                    violations.append(f"changed by a spawned process: {p} (before={before}, after={after})")
        if self.spawned:
            current = self._listdir()
            known = self._known or set()
            for name in sorted(current - known):
                if self._attributed(name):
                    violations.append(f"created by a spawned process: {self.projects_dir / name}")
            self._known = current
        return violations
