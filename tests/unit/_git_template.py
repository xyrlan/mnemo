"""Build a git fixture once per worker, and hand each test its own copy (#799).

A fixture's ``git init``/``commit``/``push`` costs a process each, about
130 ms on CI's Windows runner, and most of the slow files' fixtures build the
same repository for every test. :func:`copy` runs *build* the first time a
worker asks for *key*, in a directory of its own, and ``copytree``\\ s the
result into the test's ``tmp_path`` after that — the way
``test_sync_two_vaults``'s ``pair`` already does.

A copy carries along every absolute path git recorded while building: a
worktree's ``.git`` file, its ``.git/worktrees/<name>/gitdir``, a remote's
URL. :func:`relink_worktree` and :func:`set_remote_url` rewrite those for the
copy, and :func:`copy` then refuses one whose git metadata still names the
template, so a test can never reach back into it — or into another test.
"""
from __future__ import annotations

import contextlib
import os
import re
import shutil
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator

import pytest

_BUILT: Dict[str, Path] = {}


def copy(
    key: str,
    build: Callable[[Path], None],
    dest: Path,
    factory: pytest.TempPathFactory,
    relink: Callable[[Path], None] = lambda root: None,
) -> Path:
    """Copy the template *key* (built by ``build(root)`` once) into *dest*.

    Every entry *build* left in its root is copied under *dest* by the same
    name; *relink* then gets *dest* to rewrite the absolute paths the copy
    carried along.
    """
    if key not in _BUILT:
        root = factory.mktemp(f"template-{key}")
        with _no_auto_maintenance():
            build(root)
        _BUILT[key] = root
    template = _BUILT[key]
    dest.mkdir(parents=True, exist_ok=True)
    for entry in template.iterdir():
        shutil.copytree(str(entry), str(dest / entry.name), symlinks=True)
    relink(dest)
    stale = list(_naming(dest, template))
    assert not stale, f"the copy of {key!r} still names its template in: {stale}"
    return dest


@contextlib.contextmanager
def _no_auto_maintenance() -> Iterator[None]:
    """Build with git's automatic maintenance off, through the environment.

    Since git 2.47 a ``commit`` or ``push`` may leave ``git maintenance run
    --auto`` running detached, and it takes ``.git/objects/maintenance.lock``
    in the template after *build* has returned: a copy made then trips over
    the lock vanishing mid-``copytree`` (seen on macOS CI). Nothing may touch
    a template once it is built.
    """
    n = int(os.environ.get("GIT_CONFIG_COUNT") or 0)
    added = {"GIT_CONFIG_COUNT": str(n + 2)}
    for i, (k, v) in enumerate((("maintenance.auto", "false"), ("gc.auto", "0")), n):
        added[f"GIT_CONFIG_KEY_{i}"], added[f"GIT_CONFIG_VALUE_{i}"] = k, v
    saved = {k: os.environ.get(k) for k in added}
    os.environ.update(added)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def relink_worktree(repo: Path, tree: Path) -> None:
    """Point the copied *tree* and *repo* at each other, not at the template."""
    admin = repo / ".git" / "worktrees" / tree.name
    _rewrite(tree / ".git", f"gitdir: {admin.as_posix()}\n")
    _rewrite(admin / "gitdir", f"{(tree / '.git').as_posix()}\n")


def set_remote_url(repo: Path, remote: str, url: Path) -> None:
    """``git remote set-url`` without the process: rewrite *repo*'s config."""
    config = repo / ".git" / "config"
    text = config.read_text(encoding="utf-8")
    section = re.escape(f'[remote "{remote}"]')
    new, n = re.subn(
        rf"({section}[^\[]*?\n\s*url\s*=\s*)[^\n]*",
        lambda m: m.group(1) + url.as_posix(), text,
    )
    assert n == 1, f"no url for remote {remote!r} in {config}"
    _rewrite(config, new)


def _rewrite(path: Path, text: str) -> None:
    """Replace *path*'s text in place: Git for Windows hides a worktree's
    ``.git`` file, and Windows refuses to recreate a hidden file (``"w"``)."""
    with open(path, "r+", encoding="utf-8", newline="") as f:
        f.write(text)
        f.truncate()


def _slashed(text: str) -> str:
    """*text* lowercased, every run of backslashes one ``/``: how git may spell a path."""
    return re.sub(r"\\+", "/", text).lower()


def _naming(root: Path, template: Path) -> Iterable[str]:
    """The git metadata files under *root* that still name *template*."""
    needle = _slashed(str(template))
    for gitpath in root.rglob(".git"):
        files = [gitpath] if gitpath.is_file() else [
            gitpath / "config", *(gitpath / "worktrees").glob("*/gitdir"),
        ]
        for f in files:
            if f.is_file() and needle in _slashed(f.read_text(encoding="utf-8", errors="replace")):
                yield str(f)
