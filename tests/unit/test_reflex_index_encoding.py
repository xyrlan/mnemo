"""#779: the v2 posting strings, ``LazyPostings`` and reading a v1 file."""
from __future__ import annotations

import json
from pathlib import Path

from mnemo.core.reflex import index as reflex_index

FIELDS = ("name", "topic_tags", "aliases", "description", "body", "evidence")


def _tf(**counts) -> dict:
    return {f: counts.get(f, 0) for f in FIELDS}


def _index() -> dict:
    return {
        "schema_version": reflex_index.SCHEMA_VERSION,
        "generated_at": "2026-10-28T00:00:00Z",
        "avg_field_length": {f: 1.0 for f in FIELDS},
        "doc_count": 3,
        "postings": {
            "yarn": [{"slug": "a", "tf": _tf(body=3)},
                     {"slug": "c", "tf": _tf(name=1, body=12, evidence=2)}],
            "npm": [{"slug": "b", "tf": _tf(topic_tags=1, aliases=2, description=330)}],
        },
        "docs": {s: {"field_length": _tf(body=1), "preview": "é ✓", "path": f"shared/feedback/{s}.md",
                     "stability": "stable", "projects": ["p"], "universal": False,
                     "retired": False} for s in ("a", "b", "c")},
    }


def _write(vault: Path, data: bytes) -> None:
    path = vault / ".mnemo" / reflex_index.INDEX_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_postings_are_one_string_per_term_with_only_the_nonzero_counts(tmp_path):
    (tmp_path / ".mnemo").mkdir()
    reflex_index.write_index(tmp_path, _index())
    data = json.loads((tmp_path / ".mnemo" / reflex_index.INDEX_FILENAME).read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["doc_ids"] == ["a", "b", "c"]
    assert data["postings"] == {"yarn": "0b3 2n1b12e2", "npm": "1t1a2d330"}


def test_a_v2_file_loads_back_as_the_in_memory_index(tmp_path):
    index = _index()
    _write(tmp_path, reflex_index.encode_index(index))
    loaded = reflex_index.load_index(tmp_path)
    postings = loaded["postings"]
    assert postings["yarn"] == index["postings"]["yarn"]
    assert postings.get("npm") == index["postings"]["npm"]
    assert postings.get("pnpm", []) == []
    assert "yarn" in postings and "pnpm" not in postings
    assert len(postings) == 2 and sorted(postings) == ["npm", "yarn"]
    assert postings["yarn"] is postings["yarn"]  # decoded once
    assert loaded["docs"] == index["docs"]


def test_a_loaded_v2_index_can_be_written_again(tmp_path):
    index = _index()
    _write(tmp_path, reflex_index.encode_index(index))
    loaded = reflex_index.load_index(tmp_path)
    assert reflex_index.encode_index(loaded) == reflex_index.encode_index(index)


def test_a_v1_file_still_loads_as_it_was_written(tmp_path):
    """The rebuild path: a v1 file serves prompts until the next SessionStart
    rewrites it, instead of reading as missing in between."""
    index = dict(_index(), schema_version=1)
    _write(tmp_path, json.dumps(index, indent=2).encode("utf-8"))
    assert reflex_index.load_index(tmp_path) == index


def test_a_v2_file_without_doc_ids_reads_as_missing(tmp_path):
    data = json.loads(reflex_index.encode_index(_index()))
    del data["doc_ids"]
    _write(tmp_path, json.dumps(data).encode("utf-8"))
    assert reflex_index.load_index(tmp_path) is None
