from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from mnemo.core import log_writer


def test_appends_creates_file_with_header(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    log_writer.append_line("foo", "🟢 session started", cfg)
    log_path = tmp_vault / "bots" / "foo" / "logs" / f"{date.today().isoformat()}.md"
    content = log_path.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    assert "tags: [log, foo]" in content
    assert "# " in content
    assert re.search(r"- \*\*\d\d:\d\d\*\* — 🟢 session started", content)


def test_subsequent_append_does_not_duplicate_header(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    log_writer.append_line("foo", "first", cfg)
    log_writer.append_line("foo", "second", cfg)
    log_path = tmp_vault / "bots" / "foo" / "logs" / f"{date.today().isoformat()}.md"
    content = log_path.read_text(encoding="utf-8")
    assert content.count("tags: [log, foo]") == 1
    assert "first" in content and "second" in content


def test_truncates_oversize_lines(tmp_vault: Path):
    cfg = {"vaultRoot": str(tmp_vault)}
    huge = "x" * 5000
    log_writer.append_line("foo", f"💬 {huge}", cfg)
    log_path = tmp_vault / "bots" / "foo" / "logs" / f"{date.today().isoformat()}.md"
    line = [l for l in log_path.read_text(encoding="utf-8").splitlines() if l.startswith("- ")][0]
    assert len(line.encode("utf-8")) <= 3800


def test_creates_parent_dirs(tmp_path: Path):
    cfg = {"vaultRoot": str(tmp_path / "deeply" / "nested" / "vault")}
    log_writer.append_line("bar", "hello", cfg)
    log_path = tmp_path / "deeply" / "nested" / "vault" / "bots" / "bar" / "logs" / f"{date.today().isoformat()}.md"
    assert log_path.exists()
