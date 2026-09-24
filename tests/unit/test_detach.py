"""#452: detached workers get a hidden console, never none at all."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from mnemo import _detach

SRC = Path(_detach.__file__).resolve().parent


def test_windows_uses_create_no_window_and_a_new_group(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    kwargs = _detach.detach_kwargs()

    assert kwargs == {"creationflags": 0x08000000 | 0x00000200}


def test_windows_never_sets_detached_process(monkeypatch):
    # Windows ignores CREATE_NO_WINDOW when DETACHED_PROCESS is also set,
    # and every console child of a console-less worker opens a visible window.
    monkeypatch.setattr(sys, "platform", "win32")

    assert not _detach.detach_kwargs()["creationflags"] & 0x00000008


def test_posix_starts_a_new_session(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    assert _detach.detach_kwargs() == {"start_new_session": True}


def test_no_detached_process_left_in_src():
    offenders = [
        str(p.relative_to(SRC))
        for p in SRC.rglob("*.py")
        if p.name != "_detach.py"
        and re.search(r"DETACHED_PROCESS|0x0+8\b", p.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# --- #475: a worker must not be a descendant of the hook that started it ---

import os  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402

import pytest  # noqa: E402

_REAL_ORPHANED = _detach._orphaned  # conftest runs it inline for every test

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="fork and ppid are POSIX")

# A stand-in for a SessionEnd hook: starts one sleeping worker the way it is
# told to, prints the worker's pid, then stays alive — the hook that ran past
# Claude Code's 1.5 s bound.
_HOOK = """
import subprocess, sys, time
from mnemo import _detach
worker = [sys.executable, "-c", "import time; time.sleep(30)"]
if sys.argv[1] == "spawn":
    pid = _detach.spawn(worker)
else:  # what every hook spawn did before #475
    pid = subprocess.Popen(worker, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, close_fds=True,
                           start_new_session=True).pid
print(pid, flush=True)
time.sleep(30)
"""


def _descendants(root: int) -> set:
    """Claude Code 2.1.281's ``killProcessTree`` walk: every process by ppid
    from ``ps -A``, breadth first from *root*, with no session filter."""
    out = subprocess.run(["ps", "-A", "-o", "pid=", "-o", "ppid="],
                         capture_output=True, text=True, check=True).stdout
    children: dict = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, queue = set(), [root]
    while queue:
        for pid in children.get(queue.pop(0), []):
            if pid > 1 and pid not in found:
                found.add(pid)
                queue.append(pid)
    return found


def _tree_kill(root: int) -> None:
    """SIGTERM the group and every descendant, as the hook's kill does."""
    targets = _descendants(root)
    try:
        os.killpg(root, signal.SIGTERM)
    except OSError:
        os.kill(root, signal.SIGTERM)
    for pid in targets:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A killed worker is a zombie until init reaps it; ps says so.
    state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                           capture_output=True, text=True).stdout.strip()
    return bool(state) and not state.startswith("Z")


def _run_hook(how: str):
    env = dict(os.environ, PYTHONPATH=str(SRC.parent))
    hook = subprocess.Popen([sys.executable, "-c", _HOOK, how], stdout=subprocess.PIPE,
                            text=True, env=env, start_new_session=True)
    worker = int(hook.stdout.readline())
    return hook, worker


def _wait_dead(pid: int) -> bool:
    deadline = time.time() + 5
    while time.time() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return False


@posix_only
@pytest.mark.real_spawn
def test_a_new_session_alone_leaves_the_worker_in_the_hooks_tree():
    """The failure #475 recorded, reproduced: setsid does not take a worker
    out of a ppid walk, so the hook's tree kill SIGTERMs it."""
    hook, worker = _run_hook("popen")
    try:
        assert worker in _descendants(hook.pid)
        _tree_kill(hook.pid)
        assert _wait_dead(worker), "the tree kill should have reached the worker"
    finally:
        for pid in (worker, hook.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        hook.wait()


@posix_only
@pytest.mark.real_spawn
def test_a_spawned_worker_survives_the_hooks_tree_kill(monkeypatch):
    monkeypatch.setattr(_detach, "_orphaned", _REAL_ORPHANED)
    hook, worker = _run_hook("spawn")
    try:
        assert worker not in _descendants(hook.pid)
        assert _alive(worker)
        _tree_kill(hook.pid)
        hook.wait(timeout=5)
        time.sleep(0.3)
        assert _alive(worker), "the worker died with the hook"
    finally:
        for pid in (worker, hook.pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        if hook.poll() is None:
            hook.wait()


@posix_only
@pytest.mark.real_spawn
def test_orphaned_returns_the_workers_pid_not_the_intermediates():
    def start():
        return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL).pid

    pid = _REAL_ORPHANED(start)
    try:
        ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                              capture_output=True, text=True).stdout.strip()
        assert _alive(pid)
        # Its parent, the intermediate, is gone: it is nobody's child here.
        assert ppid and int(ppid) != os.getpid()
        assert pid not in _descendants(os.getpid())
    finally:
        os.kill(pid, signal.SIGKILL)


@posix_only
@pytest.mark.real_spawn
def test_orphaned_raises_what_the_intermediate_hit():
    def start():
        raise FileNotFoundError("no such mnemo")

    with pytest.raises(OSError, match="FileNotFoundError: no such mnemo"):
        _REAL_ORPHANED(start)


def test_windows_starts_the_worker_directly(monkeypatch):
    """No fork on Windows; its worker is started as before."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(_detach, "_orphaned", lambda start: pytest.fail("forked on win32"))
    seen = {}

    class FakePopen:
        pid = 4242

        def __init__(self, argv, **kwargs):
            seen.update(kwargs)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)

    assert _detach.spawn(["mnemo"]) == 4242
    assert seen["creationflags"] == 0x08000000 | 0x00000200
