"""``tools/measure_owner_members.py`` over synthetic oracle rows (#675)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_owner_members as tool  # noqa: E402

from mnemo.core import landing  # noqa: E402

RUST = "pub struct S {\n    pub a: u8,\n    pub b: u8,\n}\n\n// T is only mentioned here\n"


def _corpus(tmp_path: Path, files: dict, rows: list) -> Path:
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    oracle = tmp_path / "rows.tsv"
    oracle.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return oracle


def _compare(tmp_path: Path, files: dict, rows: list, **kw) -> dict:
    return tool.compare(tool.read_rows([_corpus(tmp_path, files, rows)]), root=tmp_path, **kw)


def test_rows_for_one_owner_are_summed_and_an_unparsed_file_kept_apart(tmp_path: Path) -> None:
    oracle = _corpus(tmp_path, {}, ["a.rs\tS\ta", "a.rs\tS\tb", "b.rs\t\t", "c.rs\t!PARSE\t"])

    assert tool.read_rows([oracle]) == {"a.rs": {"S": {"a", "b"}}, "b.rs": {}, "c.rs": None}


def test_a_stray_carriage_return_in_a_path_is_an_unread_file(tmp_path: Path) -> None:
    """A CRLF file list gave the oracle `a.rs\\r`: that row must not split in two."""
    (tmp_path / "rows.tsv").write_bytes(b"a.rs\r\tS\ta\r\nb.rs\tT\tb\r\n")

    files = tool.read_rows([tmp_path / "rows.tsv"])

    assert files == {"a.rs\r": {"S": {"a"}}, "b.rs": {"T": {"b"}}}


def test_an_owner_read_as_the_parser_reads_it_is_exact(tmp_path: Path) -> None:
    report = _compare(tmp_path, {"a.rs": RUST}, ["a.rs\tS\ta,b"])

    assert report["languages"] == {"rust": {
        "owners": 1, "exact": 1, "differs": 0, "unreadable": 0,
        "members": 2, "missing": 0, "extra": 0,
    }}
    assert report["differences"] == []


def test_a_disagreement_names_the_missing_and_the_extra_members(tmp_path: Path) -> None:
    report = _compare(tmp_path, {"a.rs": RUST}, ["a.rs\tS\ta,c"])

    counts = report["languages"]["rust"]
    assert (counts["differs"], counts["missing"], counts["extra"]) == (1, 1, 1)
    assert report["differences"] == [
        {"file": "a.rs", "owner": "S", "missing": ["c"], "extra": ["b"]},
    ]


def test_an_unreadable_owner_is_counted_apart(tmp_path: Path) -> None:
    source = "interface S {\n  a: string\n}\n/* never closed\n"

    report = _compare(tmp_path, {"a.ts": source}, ["a.ts\tS\ta"])

    assert report["languages"]["typescript"]["unreadable"] == 1
    assert report["differences"] == [{"file": "a.ts", "owner": "S", "unreadable": True}]


def test_files_it_cannot_compare_are_skipped_and_counted(tmp_path: Path) -> None:
    report = _compare(tmp_path, {"a.py": "class S:\n    a = 1\n"},
                      ["a.py\tS\ta", "gone.rs\tS\ta", "c.rs\t!PARSE\t"])

    assert report["languages"] == {}
    assert report["skipped"] == {"unparsed": 1, "unread": 1, "other_language": 1}


def test_negatives_flag_a_type_the_parser_does_not_see(tmp_path: Path) -> None:
    """By the oracle's word `a.rs` declares nothing; the reader finding `S` is a false owner."""
    wrong = _compare(tmp_path, {"a.rs": RUST}, ["a.rs\t\t"], negatives=True)
    right = _compare(tmp_path, {"a.rs": RUST}, ["a.rs\tS\ta,b"], negatives=True)

    assert wrong["negatives"]["false_owners"] == [{"file": "a.rs", "owner": "S", "members": ["a", "b"]}]
    assert right["negatives"]["false_owners"] == []
    # every other word was asked: `T`, named only in a comment, among them
    assert right["negatives"]["names_checked"] == len(
        {"pub", "struct", "a", "u8", "b", "T", "is", "only", "mentioned", "here"})


def test_the_masking_cache_ends_with_the_run(tmp_path: Path) -> None:
    original = landing._code_only

    _compare(tmp_path, {"a.rs": RUST}, ["a.rs\tS\ta,b"], negatives=True)

    assert landing._code_only is original


def test_main_prints_the_counts_then_provenance(tmp_path: Path, capsys) -> None:
    oracle = _corpus(tmp_path, {"a.rs": RUST}, ["a.rs\tS\ta,b"])

    assert tool.main([str(oracle), "--root", str(tmp_path)]) == 0

    lines = capsys.readouterr().out.rstrip().splitlines()
    assert lines[0].split()[:5] == ["rust", "owners", "1", "exact", "1"]
    assert lines[-1].startswith("provenance: ")


def test_main_json_carries_provenance(tmp_path: Path, capsys) -> None:
    oracle = _corpus(tmp_path, {"a.rs": RUST}, ["a.rs\tS\ta,b"])

    assert tool.main([str(oracle), "--root", str(tmp_path), "--json"]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["languages"]["rust"]["exact"] == 1
    assert report["provenance"]["tool"] == "measure_owner_members"
