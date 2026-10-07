"""Doctor check: an auto-memory MEMORY.md longer than Claude Code loads (#573)."""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.cli.commands.doctor_checks.native_memory import _doctor_check_native_memory_cut
from mnemo.core import native_memory as nm


def _index(root: Path, project: str, text: str) -> Path:
    memory = root / project / "memory"
    memory.mkdir(parents=True)
    path = memory / nm.INDEX
    path.write_bytes(text.encode("utf-8"))
    return path


def _entries(n: int, width: int = 40) -> str:
    """A heading and ``n`` entries of ``width`` characters each."""
    lines = ["# Memory Index"]
    for i in range(n):
        head = f"- [Note {i}](note-{i}.md) — "
        lines.append(head + "x" * (width - len(head)))
    return "\n".join(lines) + "\n"


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(nm, "projects_root", lambda: root)
    return root


def test_an_index_within_the_limits_is_silent(root, capsys) -> None:
    _index(root, "-Users-you-small", _entries(nm.LINE_LIMIT - 1))  # exactly 200 lines

    assert nm.cut_indexes() == []
    assert _doctor_check_native_memory_cut(root) is True
    assert capsys.readouterr().out == ""


def test_an_index_over_the_line_limit_counts_the_entries_past_it(root, capsys) -> None:
    _index(root, "-Users-you-long", _entries(245))  # 246 lines, as on 2026-09-29

    [cut] = nm.cut_indexes()
    assert cut["project"] == "-Users-you-long"
    assert (cut["lines"], cut["loaded_lines"], cut["entries_past"]) == (246, 200, 46)

    assert _doctor_check_native_memory_cut(root) is False
    out = capsys.readouterr().out
    assert "1 project has a MEMORY.md longer than Claude Code loads" in out
    assert "-Users-you-long: 246 lines" in out
    assert "loads 200 lines, 46 index entries past the cut" in out


def test_an_index_over_the_size_limit_is_cut_at_a_whole_line(root, capsys) -> None:
    # 100 lines of 300 characters: 30,000 characters, well under 200 lines.
    _index(root, "-Users-you-wide", _entries(99, width=300))

    [cut] = nm.cut_indexes()
    assert cut["lines"] == 100
    # The 14-character heading plus 83 entries of 300 characters and their
    # newlines make 24,997 characters; one more entry would pass 25,000.
    assert cut["loaded_lines"] == 84
    assert cut["entries_past"] == 16
    assert cut["chars"] > nm.CHAR_LIMIT

    assert _doctor_check_native_memory_cut(root) is False
    out = capsys.readouterr().out
    assert "loads 84 lines, 16 index entries past the cut" in out


def test_the_size_limit_is_characters_not_bytes(root) -> None:
    # 99 lines of 240 two-byte characters: ~23,900 characters but ~47,500
    # bytes. Claude Code loads it whole (measured, see core/native_memory).
    lines = ["é" * 240 for _ in range(99)]
    path = _index(root, "-Users-you-accents", "\n".join(lines) + "\n")
    assert path.stat().st_size > 1.5 * nm.CHAR_LIMIT

    assert nm.cut_indexes() == []


def test_a_missing_index_or_projects_dir_is_silent(root, tmp_path, monkeypatch, capsys) -> None:
    (root / "-Users-you-empty" / "memory").mkdir(parents=True)
    (root / "-Users-you-bare").mkdir()
    (root / "stray-file").write_text("not a project", encoding="utf-8")

    assert _doctor_check_native_memory_cut(root) is True
    monkeypatch.setattr(nm, "projects_root", lambda: tmp_path / "no-such-dir")
    assert _doctor_check_native_memory_cut(root) is True
    assert capsys.readouterr().out == ""


def test_only_the_cut_projects_are_listed_in_name_order(root, capsys) -> None:
    _index(root, "-Users-you-b", _entries(300))
    _index(root, "-Users-you-ok", _entries(10))
    _index(root, "-Users-you-a", _entries(201))

    assert [c["project"] for c in nm.cut_indexes()] == ["-Users-you-a", "-Users-you-b"]
    assert _doctor_check_native_memory_cut(root) is False
    out = capsys.readouterr().out
    assert "2 projects have a MEMORY.md" in out
    assert "-Users-you-ok" not in out
    assert "loads 200 lines, 2 index entries past the cut" in out


def test_blank_lines_and_headings_past_the_cut_are_not_entries() -> None:
    text = _entries(199) + "\n## Older\n\n- [Old](old.md) — kept\n"
    cut = nm.index_cut(text)
    assert cut is not None
    assert cut["entries_past"] == 1


def test_crlf_indexes_count_the_same_lines() -> None:
    text = _entries(245)
    assert nm.index_cut(text.replace("\n", "\r\n"))["loaded_lines"] == 200


def test_the_check_is_registered() -> None:
    from mnemo.cli.commands.doctor import DOCTOR_CHECKS

    assert ("native_memory_cut", _doctor_check_native_memory_cut) in DOCTOR_CHECKS
