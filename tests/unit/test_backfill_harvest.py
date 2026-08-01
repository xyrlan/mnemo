"""Harvest: transcript in, origin-stamped memory files out."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from mnemo.core import llm
from mnemo.core.backfill import harvest
from mnemo.core.extract import prompts
from mnemo.core.extract.prompts.templates.harvest import _FEW_SHOT_HARVEST
from mnemo.core.extract.scanner import parse_frontmatter


@pytest.fixture
def cfg(tmp_path: Path) -> dict:
    root = tmp_path / "vault"
    (root / "bots").mkdir(parents=True)
    return {
        "vaultRoot": str(root),
        "extraction": {"model": "claude-haiku-4-5", "subprocessTimeout": 60},
        "backfill": {"minFileMutations": 1},
    }


def _write_transcript(tmp_path: Path, *, mutations: int) -> Path:
    events = [{
        "type": "user",
        "message": {"role": "user", "content": [{"type": "text", "text": "no, use pathlib"}]},
    }]
    for i in range(mutations):
        events.append({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Edit", "id": f"t{i}", "input": {}}],
            },
        })
    p = tmp_path / "sess-1.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return p


def _stub_llm_text(monkeypatch, text: str) -> dict:
    """Stub `llm.call`, returning a dict that captures how it was invoked."""
    seen: dict = {}

    def fake_call(prompt, **kwargs):
        seen["prompt"] = prompt
        seen.update(kwargs)
        return llm.LLMResponse(
            text=text,
            total_cost_usd=0.0,
            input_tokens=10,
            output_tokens=10,
            api_key_source="subscription",
            raw={},
        )

    monkeypatch.setattr(harvest.llm, "call", fake_call)
    return seen


def _stub_llm(monkeypatch, pages: list[dict]) -> dict:
    return _stub_llm_text(monkeypatch, json.dumps({"pages": pages}))


def test_writes_a_memory_file_per_page(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [{
        "slug": "use-pathlib",
        "type": "feedback",
        "name": "Use pathlib",
        "description": "Prefer pathlib over os.path",
        "body": "User corrected os.path usage; pathlib is the house style.",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)

    assert len(written) == 1
    assert written[0].name == "use-pathlib.md"
    assert written[0].parent == Path(cfg["vaultRoot"]) / "bots" / "alpha" / "memory"


def test_calls_the_llm_with_the_harvest_prompt_and_configured_model(cfg, tmp_path, monkeypatch):
    """Guards against silently inheriting briefing's prompt or hardcoding the model."""
    cfg["extraction"] = {"model": "claude-sonnet-4-5", "subprocessTimeout": 123}
    seen = _stub_llm(monkeypatch, [])
    t = _write_transcript(tmp_path, mutations=1)

    harvest.harvest_session(t, "alpha", cfg)

    assert seen["system"] is prompts.HARVEST_SYSTEM_PROMPT
    assert seen["model"] == "claude-sonnet-4-5"
    assert seen["timeout"] == 123
    assert "no, use pathlib" in seen["prompt"]


def test_llm_failure_propagates_to_the_caller(cfg, tmp_path, monkeypatch):
    """Task 7 catches per-session so one bad transcript can't abort a sweep."""
    def boom(*a, **k):
        raise llm.LLMSubprocessError("subprocess died")

    monkeypatch.setattr(harvest.llm, "call", boom)
    t = _write_transcript(tmp_path, mutations=1)

    with pytest.raises(llm.LLMSubprocessError):
        harvest.harvest_session(t, "alpha", cfg)


def test_unparseable_llm_response_raises(cfg, tmp_path, monkeypatch):
    _stub_llm_text(monkeypatch, "I could not find any lessons, sorry.")
    t = _write_transcript(tmp_path, mutations=1)

    with pytest.raises(llm.LLMParseError):
        harvest.harvest_session(t, "alpha", cfg)


def test_stamps_origin_backfill_in_frontmatter(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [{
        "slug": "use-pathlib",
        "type": "feedback",
        "name": "Use pathlib",
        "description": "Prefer pathlib",
        "body": "House style.",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)
    text = written[0].read_text(encoding="utf-8")
    fm, _body = parse_frontmatter(text)

    # `parse_frontmatter` is a flat key: value reader — it flattens the nested
    # `metadata:` block, so the stamp is readable as a top-level key. The raw
    # `metadata.origin: backfill` shape is asserted separately below because
    # the extraction gate (task 6) matches on the file text.
    assert fm["origin"] == "backfill"
    assert fm["type"] == "feedback"
    assert fm["node_type"] == "memory"
    assert fm["originSessionId"] == "sess-1"
    assert "metadata:\n  node_type: memory\n" in text
    assert "  origin: backfill\n" in text


def test_harvested_file_is_readable_by_the_extraction_scanner(cfg, tmp_path, monkeypatch):
    """The whole point of the memory-file seam: the scanner must type it right."""
    from mnemo.core.extract.scanner import _read_memory_file

    _stub_llm(monkeypatch, [{
        "slug": "queue-single-threaded",
        "type": "project",
        "name": "Queue stays single-threaded",
        "description": "Concurrency constraint",
        "body": "The consumer must remain single-threaded.",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)
    mf = _read_memory_file(written[0], "alpha")

    assert mf.type == "project"
    assert mf.slug == "queue-single-threaded"
    assert "single-threaded" in mf.body


def test_multiline_description_cannot_break_out_of_the_frontmatter(cfg, tmp_path, monkeypatch):
    """A `---` inside the description used to close the block early, burying the stamp."""
    _stub_llm(monkeypatch, [{
        "slug": "queue-single-threaded",
        "type": "project",
        "name": "Queue stays single-threaded",
        "description": "Constraint summary\n---\nsee ADR-7: the 2024 incident",
        "body": "The consumer must remain single-threaded.",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)
    text = written[0].read_text(encoding="utf-8")
    fm, body = parse_frontmatter(text)

    assert fm["origin"] == "backfill"
    assert fm["type"] == "project"
    assert fm["originSessionId"] == "sess-1"
    # The description survives as one line, and contributes no stray keys.
    assert fm["description"] == '"Constraint summary --- see ADR-7: the 2024 incident"'
    assert "see ADR-7" not in body
    assert set(fm) == {"name", "description", "metadata", "node_type", "type",
                       "originSessionId", "origin"}


def test_quotes_in_description_do_not_end_the_value(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [{
        "slug": "s", "type": "feedback", "name": "N",
        "description": 'Never say "any"', "body": "b",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    fm, _ = parse_frontmatter(harvest.harvest_session(t, "alpha", cfg)[0].read_text("utf-8"))
    assert fm["description"] == "\"Never say 'any'\""
    assert fm["origin"] == "backfill"


def test_zero_mutation_session_is_skipped_without_calling_llm(cfg, tmp_path, monkeypatch):
    def explode(*a, **k):
        raise AssertionError("LLM must not be called for a zero-mutation session")

    monkeypatch.setattr(harvest.llm, "call", explode)
    t = _write_transcript(tmp_path, mutations=0)

    assert harvest.harvest_session(t, "alpha", cfg) == []


@pytest.mark.parametrize(
    ("threshold", "mutations", "expect_written"),
    [(3, 2, 0), (3, 3, 1), (3, 4, 1)],
)
def test_min_file_mutations_threshold_is_inclusive(
    cfg, tmp_path, monkeypatch, threshold, mutations, expect_written
):
    """`minFileMutations: 3` means three edits is enough — guards an off-by-one."""
    cfg["backfill"] = {"minFileMutations": threshold}
    _stub_llm(monkeypatch, [
        {"slug": "s", "type": "feedback", "name": "N", "description": "d", "body": "b"},
    ])
    t = _write_transcript(tmp_path, mutations=mutations)

    assert len(harvest.harvest_session(t, "alpha", cfg)) == expect_written


@pytest.mark.parametrize("payload", ['{"pages": null}', '{"pages": "nope"}', "{}"])
def test_non_list_pages_returns_empty_rather_than_raising(cfg, tmp_path, monkeypatch, payload):
    _stub_llm_text(monkeypatch, payload)
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []


def test_non_dict_entry_inside_pages_is_skipped(cfg, tmp_path, monkeypatch):
    _stub_llm_text(monkeypatch, json.dumps({"pages": [
        "just a string",
        None,
        {"slug": "good", "type": "feedback", "name": "G", "description": "d", "body": "b"},
    ]}))
    t = _write_transcript(tmp_path, mutations=1)

    assert [p.name for p in harvest.harvest_session(t, "alpha", cfg)] == ["good.md"]


def test_two_pages_colliding_on_one_slug_write_once(cfg, tmp_path, monkeypatch):
    """`Use Pathlib!` and `use-pathlib` normalise to the same filename."""
    _stub_llm(monkeypatch, [
        {"slug": "Use Pathlib!", "type": "feedback", "name": "First",
         "description": "d", "body": "first body"},
        {"slug": "use-pathlib", "type": "feedback", "name": "Second",
         "description": "d", "body": "second body"},
    ])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)

    assert [p.name for p in written] == ["use-pathlib.md"]
    # First page wins; the second must not have silently overwritten it.
    assert "first body" in written[0].read_text(encoding="utf-8")


def test_invalid_page_type_is_dropped(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [
        {"slug": "good", "type": "feedback", "name": "G", "description": "d", "body": "b"},
        {"slug": "bad", "type": "nonsense", "name": "B", "description": "d", "body": "b"},
    ])
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)
    assert [p.name for p in written] == ["good.md"]


def test_empty_body_page_is_dropped(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [
        {"slug": "hollow", "type": "feedback", "name": "H", "description": "d", "body": "   "},
    ])
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []


def test_slugless_page_is_dropped_not_written_as_untitled(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [
        {"slug": "", "type": "feedback", "name": "N", "description": "d", "body": "b"},
    ])
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []


def test_empty_pages_array_writes_nothing(cfg, tmp_path, monkeypatch):
    _stub_llm(monkeypatch, [])
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []


def test_existing_memory_file_is_not_overwritten(cfg, tmp_path, monkeypatch):
    memory_dir = Path(cfg["vaultRoot"]) / "bots" / "alpha" / "memory"
    memory_dir.mkdir(parents=True)
    existing = memory_dir / "use-pathlib.md"
    existing.write_text("LIVE CONTENT", encoding="utf-8")

    _stub_llm(monkeypatch, [{
        "slug": "use-pathlib", "type": "feedback",
        "name": "Use pathlib", "description": "d", "body": "b",
    }])
    t = _write_transcript(tmp_path, mutations=1)

    assert harvest.harvest_session(t, "alpha", cfg) == []
    assert existing.read_text(encoding="utf-8") == "LIVE CONTENT"


# --- few-shot round-trip -----------------------------------------------------
#
# `tests/unit/test_prompts_few_shot_schema.py` round-trips the consolidation
# few-shots through `_parse_pages_from_response`. Harvest cannot reuse that
# gate: it hard-requires a non-empty `source_files`, which harvest pages
# deliberately lack. So round-trip the harvest examples through the real
# production path instead — `harvest_session` with the example as LLM output.

_OUTPUT_JSON_RE = re.compile(r'^(\{"pages":\s*\[.*\]\})\s*$', re.MULTILINE)

_HARVEST_BLOBS = _OUTPUT_JSON_RE.findall(_FEW_SHOT_HARVEST)


def test_few_shot_harvest_has_extractable_outputs():
    """Guard: if the few-shot shape drifts, the round-trip below would vacuously pass."""
    assert len(_HARVEST_BLOBS) == 3


@pytest.mark.parametrize(
    "blob", _HARVEST_BLOBS, ids=[f"_FEW_SHOT_HARVEST#{i}" for i in range(len(_HARVEST_BLOBS))]
)
def test_few_shot_harvest_examples_survive_harvest(blob, cfg, tmp_path, monkeypatch):
    """Every page we show the model must be a page harvest would actually write."""
    expected = json.loads(blob)["pages"]
    _stub_llm_text(monkeypatch, blob)
    t = _write_transcript(tmp_path, mutations=1)

    written = harvest.harvest_session(t, "alpha", cfg)

    assert [p.stem for p in written] == [p["slug"] for p in expected]
