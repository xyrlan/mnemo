"""#573: the doctor says when an auto-memory MEMORY.md is longer than Claude Code loads."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pytest

from mnemo.cli.commands.doctor_checks.native_memory import _doctor_check_native_memory_cut
from mnemo.core import native_memory as nm


def _project(home: Path, name: str, index: Optional[str]) -> Path:
    mem = home / ".claude" / "projects" / name / "memory"
    mem.mkdir(parents=True)
    if index is not None:
        (mem / "MEMORY.md").write_text(index, encoding="utf-8")
    return mem


def _entries(n: int, width: int = 0) -> str:
    return "# Memory Index\n" + "".join(
        f"- [Note {i}](note-{i}.md) — {'x' * width}\n" for i in range(n))


@pytest.fixture
def run(tmp_home: Path, tmp_path: Path, capsys):
    def go():
        ok = _doctor_check_native_memory_cut(tmp_path)
        return ok, capsys.readouterr().out
    return go


def test_an_index_within_both_limits_is_silent(tmp_home, run):
    _project(tmp_home, "-proj-a", _entries(199))  # 200 lines with the header
    assert run() == (True, "")


def test_no_projects_directory_is_silent(tmp_home, run):
    assert run() == (True, "")


def test_a_missing_memory_index_is_silent(tmp_home, run):
    mem = _project(tmp_home, "-proj-a", None)
    (mem / "note.md").write_text("a topic file, no index\n", encoding="utf-8")
    _project(tmp_home, "-proj-b", None).rmdir()  # a project with no memory/ at all
    assert run() == (True, "")


def test_an_index_over_the_line_limit_is_reported(tmp_home, run):
    _project(tmp_home, "-proj-ok", _entries(10))
    _project(tmp_home, "-proj-long", _entries(245))  # 246 lines
    ok, out = run()
    assert ok is False
    assert "1 project(s)" in out and "200 lines or 25,000 characters" in out
    assert "~/.claude/projects/-proj-long/memory/MEMORY.md: 246 lines" in out
    assert "over the 200-line limit; loads 200 lines, 46 of 245 index entries past the cut" in out
    assert "-proj-ok" not in out
    assert str(tmp_home) not in out


def test_an_index_over_the_character_limit_is_reported(tmp_home, run):
    # 101 lines: the header (14 chars) plus 100 entries of 300+ characters.
    index = _entries(100, width=290)
    assert len(index.splitlines()) < nm.LINE_LIMIT and len(index) > nm.CHAR_LIMIT
    _project(tmp_home, "-proj-wide", index)
    cut = nm.index_cut(index)
    ok, out = run()
    assert ok is False
    assert "over the 25,000-character limit" in out
    assert f"loads {cut['loaded_lines']} lines" in out
    assert cut["loaded_lines"] < 101
    assert f"{cut['entries_past']} of 100 index entries past the cut" in out
    assert cut["entries_past"] == 101 - cut["loaded_lines"]


def test_the_character_limit_counts_characters_not_bytes(tmp_home, run):
    # Three-byte characters: 24,000 characters, 72,000 bytes, all loaded.
    index = "\n".join("…" * 239 for _ in range(100))
    assert len(index) < nm.CHAR_LIMIT < len(index.encode("utf-8"))
    _project(tmp_home, "-proj-a", index)
    assert run() == (True, "")


def test_frontmatter_and_block_comments_do_not_count(tmp_home, run):
    # Claude Code strips both before loading (docs/en/errors, since v2.1.211).
    body = _entries(199)  # 200 lines: exactly the limit
    index = "---\nname: index\n---\n<!-- one line -->\n<!--\nseveral\nlines\n-->\n" + body
    _project(tmp_home, "-proj-a", index)
    assert run() == (True, "")
    assert nm.index_cut(index)["lines"] == 200


def test_an_inline_comment_line_still_counts():
    assert nm.loadable_lines("<!-- note --> - [a](a.md)\nb") == ["<!-- note --> - [a](a.md)", "b"]


def test_projects_are_listed_most_entries_past_the_cut_first(tmp_home, run):
    _project(tmp_home, "-proj-a", _entries(210))
    _project(tmp_home, "-proj-b", _entries(260))
    out = run()[1]
    assert out.index("-proj-b") < out.index("-proj-a")


def test_the_check_is_cheap_over_dozens_of_projects(tmp_home):
    for i in range(60):
        _project(tmp_home, f"-proj-{i}", _entries(250 if i % 3 == 0 else 50, width=60))
    start = time.perf_counter()
    found = nm.over_limit()
    elapsed = time.perf_counter() - start
    assert len(found) == 20
    assert elapsed < 0.1, elapsed


def test_the_measurement_tool_shares_the_limit():
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "tools" / "measure_native_memory_cut.py"
    spec = importlib.util.spec_from_file_location("measure_native_memory_cut_573", path)
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    assert tool.loaded_lines is nm.loaded_lines
    assert (tool.LINE_LIMIT, tool.CHAR_LIMIT) == (nm.LINE_LIMIT, nm.CHAR_LIMIT)


def test_doctor_registry_includes_the_check():
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert "native_memory_cut" in [name for name, _ in DOCTOR_CHECKS]
