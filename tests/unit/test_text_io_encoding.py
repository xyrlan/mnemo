"""Every text read and write names its encoding (#255).

``Path.read_text()``, ``Path.write_text(...)``, ``open(path)`` and
``os.fdopen(fd, "w")`` without ``encoding=`` use the platform default: UTF-8
on macOS and Linux, cp1252 on the Windows runner (Python < 3.15). A file that
crosses that boundary — written by Claude Code, edited by the user, or checked
into the repo — then round-trips an em dash as mojibake or dies with
``'charmap' codec can't decode byte 0x90``. Three Windows-only CI failures on
2026-09-13 were this class, each fixed at the one call site CI happened to hit
(#233, #236, #243); this test closes the class instead.

The first half pins the invariant by scanning ``src/``, ``tools/`` and
``tests/`` with the AST — the way ``test_public_api_surface.py`` and
``test_release_workflow.py`` pin other repo-wide invariants. The second half
reproduces the Windows failure on any platform: ``cp1252_default`` patches
``io.open`` (which ``Path.read_text``, ``Path.open``, ``os.fdopen`` and the
builtin all resolve at call time) so a call without ``encoding=`` decodes as
cp1252, exactly what the Windows runner does. Monkeypatching
``locale.getpreferredencoding`` does *not* work for this: the builtin reads
the locale encoding in C, never through that Python function.
"""
from __future__ import annotations

import ast
import builtins
import io
import json
import pathlib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src", "tools", "tests")

# Names whose text mode is implied: position of a positional ``encoding``.
_TEXT_ONLY = {"read_text": 0, "write_text": 1}
# Names that take a mode: position of the mode argument, and whether the
# receiver may be a module whose same-named function is *not* text I/O.
_MODED = {"open": 1, "fdopen": 1, "NamedTemporaryFile": 0, "TemporaryFile": 0}
_TEXT_WRAPPERS = {"TextIOWrapper"}
# ``<module>.open`` that is not text file I/O: raw fds, compressed/archive
# streams (binary by default), browsers.
_NOT_TEXT_IO = {"os", "gzip", "bz2", "lzma", "tarfile", "zipfile", "shelve", "dbm", "webbrowser"}
# A call that means to use the platform default says so on its own line; the
# marker is the review trail, not an escape hatch.
EXEMPT_MARKER = "# encoding: platform default"


def _mode_is_binary(call: ast.Call, position: int) -> bool | None:
    """True for a literal binary mode, False for text, None when not a literal."""
    mode: ast.expr | None = None
    if len(call.args) > position:
        mode = call.args[position]
    for kw in call.keywords:
        if kw.arg == "mode":
            mode = kw.value
    if mode is None:
        return False  # the default mode is "r"
    if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
        return "b" in mode.value
    return None


def _receiver_module(call: ast.Call) -> str | None:
    fn = call.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
        return fn.value.id
    return None


def bare_text_io_calls(source: str, filename: str = "<string>") -> list[tuple[int, str]]:
    """``(line, call name)`` for every text-mode file open without ``encoding=``."""
    hits: list[tuple[int, str]] = []
    lines = source.splitlines()
    for node in ast.walk(ast.parse(source, filename=filename)):
        if not isinstance(node, ast.Call):
            continue
        if EXEMPT_MARKER in lines[node.lineno - 1]:
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else None
        if name is None or any(kw.arg == "encoding" for kw in node.keywords):
            continue
        if name in _TEXT_ONLY:
            if len(node.args) <= _TEXT_ONLY[name]:
                hits.append((node.lineno, name))
        elif name in _MODED:
            if name == "open" and _receiver_module(node) in _NOT_TEXT_IO:
                continue
            # Path.open(mode) has the mode first; the builtin has the file first.
            position = 0 if (name == "open" and isinstance(fn, ast.Attribute) and _receiver_module(node) != "io") else _MODED[name]
            if _mode_is_binary(node, position) is not True:
                hits.append((node.lineno, name))
        elif name in _TEXT_WRAPPERS:
            hits.append((node.lineno, name))
    return hits


def _scan(root: Path) -> list[str]:
    out: list[str] = []
    for py in sorted(root.rglob("*.py")):
        for line, name in bare_text_io_calls(py.read_text(encoding="utf-8"), str(py)):
            out.append(f"{py.relative_to(REPO)}:{line}: {name}")
    return out


@pytest.mark.parametrize("root", SCAN_ROOTS)
def test_every_text_read_and_write_names_its_encoding(root: str) -> None:
    offenders = _scan(REPO / root)
    assert not offenders, (
        f"{len(offenders)} text read/write call(s) under {root}/ use the platform "
        "default encoding (cp1252 on the Windows runner). Pass encoding=\"utf-8\" "
        "(or utf-8 + errors=... for files the user may have edited):\n  "
        + "\n  ".join(offenders)
    )


# --- the scanner itself must catch every shape it claims to, or the test above
# passes vacuously the day a refactor breaks it.

@pytest.mark.parametrize(
    "snippet, expected",
    [
        ("p.read_text()", ["read_text"]),
        ("p.read_text(errors='replace')", ["read_text"]),
        ("p.write_text(body)", ["write_text"]),
        ("p.write_text(body, newline='\\n')", ["write_text"]),
        ("open(p)", ["open"]),
        ("open(p, 'w')", ["open"]),
        ("open(p, mode='r')", ["open"]),
        ("open(p, mode)", ["open"]),  # non-literal mode: flagged, prove it binary
        ("p.open()", ["open"]),
        ("p.open('a')", ["open"]),
        ("io.open(p, 'w')", ["open"]),
        ("os.fdopen(fd, 'w')", ["fdopen"]),
        ("tempfile.NamedTemporaryFile('w')", ["NamedTemporaryFile"]),
        ("io.TextIOWrapper(buf)", ["TextIOWrapper"]),
        # Named, binary, or not file I/O at all: never flagged.
        ("p.read_text(encoding='utf-8')", []),
        ("p.read_text('utf-8')", []),
        ("p.write_text(body, 'utf-8')", []),
        ("p.write_text(body, encoding='utf-8')", []),
        ("open(p, encoding='utf-8')", []),
        ("open(p, 'rb')", []),
        ("open(p, mode='wb')", []),
        ("p.open('rb')", []),
        ("os.fdopen(fd, 'wb')", []),
        ("os.open(p, os.O_RDONLY)", []),
        ("p.read_bytes()", []),
        ("webbrowser.open(url)", []),
        ("gzip.open(p)", []),
        ("p.read_text()  # encoding: platform default", []),
    ],
)
def test_scanner_recognises_each_shape(snippet: str, expected: list[str]) -> None:
    assert [name for _, name in bare_text_io_calls(snippet)] == expected


# --- reproduce the Windows runner on any platform --------------------------

@pytest.fixture
def cp1252_default(monkeypatch: pytest.MonkeyPatch):
    """Make every text open without ``encoding=`` use cp1252, as on Windows."""
    real_open = io.open

    def cp1252_open(file, mode="r", buffering=-1, encoding=None, errors=None,
                    newline=None, closefd=True, opener=None):
        if "b" not in mode and encoding in (None, "locale"):
            encoding = "cp1252"
        return real_open(file, mode, buffering, encoding, errors, newline, closefd, opener)

    monkeypatch.setattr(io, "open", cp1252_open)
    monkeypatch.setattr(builtins, "open", cp1252_open)
    # Python 3.10 alone routes ``Path.open`` through ``_NormalAccessor.open``,
    # a class attribute bound to ``io.open`` at import time, so patching
    # ``io.open`` afterwards never reaches ``Path.read_text`` there. 3.8/3.9
    # and 3.11+ call ``io.open`` by name at call time.
    accessor = getattr(pathlib, "_NormalAccessor", None)
    if accessor is not None and hasattr(accessor, "open"):
        monkeypatch.setattr(accessor, "open", staticmethod(cp1252_open))


def test_fixture_reproduces_the_windows_failure(cp1252_default, tmp_path: Path) -> None:
    """The exact CI error from #243: a box-drawing character holds byte 0x90."""
    page = tmp_path / "page.md"
    page.write_text("┐", encoding="utf-8")
    with pytest.raises(UnicodeDecodeError, match="charmap"):
        page.read_text()  # encoding: platform default


def test_settings_with_a_non_ascii_path_survive_windows(cp1252_default, tmp_path: Path) -> None:
    """Claude Code writes settings.json as UTF-8 on every platform; mnemo
    must read it as UTF-8 too, or a user under ``C:\\Users\\João`` gets a
    hook entry rewritten with mojibake (or a swallowed decode error)."""
    from mnemo.install import settings as s

    path = tmp_path / "settings.json"
    original = {"env": {"HOME": "C:\\Users\\João"}, "note": "cp1252 cannot hold ┐"}
    path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

    assert s._read_settings(path) == original


def test_settings_backup_is_byte_exact(cp1252_default, tmp_path: Path) -> None:
    from mnemo.install import settings as s

    path = tmp_path / "settings.json"
    raw = json.dumps({"k": "café ┐"}, ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw)

    s._backup(path)

    backups = list(tmp_path.glob("settings.json.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == raw


def test_session_cache_round_trips_non_ascii(cp1252_default, tmp_tempdir: Path) -> None:
    from mnemo.core import session

    info = {"agent": "mnemo", "cwd": "C:\\Users\\João\\projeto ┐"}
    session.save("sid-255", info)

    assert session.load("sid-255") == info
    assert [e["cwd"] for e in session.iter_unanalyzed()] == [info["cwd"]]


def test_slash_command_probe_tolerates_foreign_bytes(cp1252_default, tmp_path: Path) -> None:
    """A third-party command file saved in another encoding must be left
    alone by inject and uninject, not crash them: the probe only looks for
    an ASCII tag."""
    from mnemo.install import settings as s

    commands = tmp_path / "commands"
    commands.mkdir()
    name = next(iter(s.SLASH_COMMANDS))
    theirs = commands / f"{name}.md"
    theirs.write_bytes("# Meu comando, não é do mnemo\n".encode("latin-1"))
    other = commands / "other.md"
    other.write_bytes(b"\xff\xfe not utf-8 at all\n")

    s.inject_slash_commands(commands)
    assert theirs.read_bytes() == "# Meu comando, não é do mnemo\n".encode("latin-1")

    s.uninject_slash_commands(commands)
    assert theirs.exists() and other.exists()


def test_gitignore_keeps_undecodable_bytes(cp1252_default, tmp_path: Path) -> None:
    """``mnemo init --project`` appends to a file the user owns; a comment in
    another encoding must come out byte for byte, with the entries added."""
    from mnemo.cli.commands import init

    gi = tmp_path / ".gitignore"
    theirs = "# configuração local\nnode_modules/\n".encode("latin-1")
    gi.write_bytes(theirs)

    init._ensure_gitignore(tmp_path)

    body = gi.read_bytes()
    assert body.startswith(theirs)
    assert b".claude/" in body and b".mnemo/" in body


def test_enforced_rule_pages_read_as_utf8(cp1252_default, tmp_vault: Path) -> None:
    from mnemo.cli.commands.list_enforced import _iter_enforced

    page = tmp_vault / "shared" / "feedback" / "regra.md"
    page.parent.mkdir(parents=True)
    page.write_text(
        "---\nname: regra\nenforce:\n  bash: ['rm -rf']\n---\n\nNão use — nunca ┐.\n",
        encoding="utf-8",
    )

    hits = list(_iter_enforced(tmp_vault))
    assert [p.name for p, _fm, _enf in hits] == ["regra.md"]
