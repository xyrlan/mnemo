"""The cap on ``list_rules_by_topic`` and the reflex-index cache behind it (#765).

A topic held p50 62 / max 244 rules, and the whole list went back on every
call. What is pinned: the server shows the first ``limit`` (default 10) and
never hides a rule the rerank marked; a cut list says how many it left out
and how to get them; an uncut one keeps the bare-list shape; the log records
what was shown and the total; and the 137 MB reflex index is parsed once per
file version, not once per call (and not twice per reranked call).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from mnemo.core.mcp import server, tools
from mnemo.core.mcp.server import DEFAULT_LIST_LIMIT, cap, handle_request
from mnemo.core.reflex import index as reflex_index


@pytest.fixture(autouse=True)
def _vault_wide(monkeypatch):
    monkeypatch.setattr("mnemo.core.mcp.tools._resolve_current_project", lambda vault_root: None)
    monkeypatch.setattr("mnemo.core.mcp.access_log._load_telemetry_config", lambda: (True, 1_048_576))


def _write_rules(vault: Path, n: int, topic: str = "git") -> None:
    target = vault / "shared" / "feedback"
    target.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (target / f"rule-{i:03d}.md").write_text(
            "---\n"
            f"name: rule-{i:03d}\n"
            "description: d\n"
            "type: feedback\n"
            "stability: stable\n"
            "sources:\n"
            "  - bots/a/m.md\n"
            "tags:\n"
            "  - auto-promoted\n"
            f"  - {topic}\n"
            "---\n\nthe rule body\n",
            encoding="utf-8")


def _call(vault: Path, **arguments):
    resp = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "list_rules_by_topic", "arguments": {"topic": "git", **arguments}}}, vault_root=vault)
    return json.loads(resp["result"]["content"][0]["text"])


def _logged(vault: Path) -> dict:
    lines = (vault / ".mnemo" / "mcp-access-log.jsonl").read_text(encoding="utf-8").splitlines()
    return json.loads(lines[-1])


# --- cap ---------------------------------------------------------------------


def _rules(n, marked=()):
    return [dict({"slug": "s%d" % i}, **({"relevant": i in marked} if marked else {}))
            for i in range(n)]


def test_cap_shows_the_first_limit():
    assert [r["slug"] for r in cap(_rules(30), 10)] == ["s%d" % i for i in range(10)]


def test_cap_never_hides_a_marked_rule():
    # The rerank puts the marked rules first; 14 marked outrank a limit of 10.
    shown = cap(_rules(30, marked=set(range(14))), 10)
    assert len(shown) == 14 and all(r["relevant"] for r in shown)


def test_cap_counts_only_true_marks():
    assert len(cap(_rules(30, marked={0}), 3)) == 3


def test_cap_zero_shows_everything():
    assert len(cap(_rules(244), 0)) == 244


@pytest.mark.parametrize("value, expected", [
    (None, DEFAULT_LIST_LIMIT), (5, 5), ("7", 7), (0, 0), (-3, 0),
    ("all", DEFAULT_LIST_LIMIT), (True, DEFAULT_LIST_LIMIT), (2.9, 2),
])
def test_limit_argument(value, expected):
    assert server._limit(value) == expected


# --- the server ----------------------------------------------------------------


def test_a_long_topic_is_cut_and_says_how_to_get_the_rest(tmp_vault):
    _write_rules(tmp_vault, 15)
    payload = _call(tmp_vault)
    assert len(payload["rules"]) == DEFAULT_LIST_LIMIT
    assert payload["more"] == 5
    assert "limit=0" in payload["note"] and "15" in payload["note"]


def test_the_log_records_what_was_shown_and_the_total(tmp_vault):
    _write_rules(tmp_vault, 15)
    shown = _call(tmp_vault)["rules"]
    row = _logged(tmp_vault)
    assert row["result_count"] == DEFAULT_LIST_LIMIT
    assert row["total"] == 15
    assert row["hit_slugs"] == [r["slug"] for r in shown]


def test_limit_zero_returns_the_whole_topic_as_a_list(tmp_vault):
    _write_rules(tmp_vault, 15)
    payload = _call(tmp_vault, limit=0)
    assert isinstance(payload, list) and len(payload) == 15
    assert _logged(tmp_vault)["args"]["limit"] == 0


def test_an_explicit_limit_is_honoured(tmp_vault):
    _write_rules(tmp_vault, 15)
    assert len(_call(tmp_vault, limit=3)["rules"]) == 3


def test_a_short_topic_keeps_the_bare_list(tmp_vault):
    _write_rules(tmp_vault, 4)
    payload = _call(tmp_vault)
    assert isinstance(payload, list) and len(payload) == 4
    assert _logged(tmp_vault)["total"] == 4


def test_every_marked_rule_survives_the_cap(tmp_vault, monkeypatch):
    _write_rules(tmp_vault, 20)

    def mark_twelve(vault_root, matches, query, *, project, cfg):
        for m in matches[:12]:
            m["relevant"] = True
        for m in matches[12:]:
            m["relevant"] = False
        return matches, {"provider": "typesafe", "status": "ok"}

    monkeypatch.setattr(server.mcp_rerank, "apply", mark_twelve)
    payload = _call(tmp_vault, query="anything")
    assert len(payload["rules"]) == 12 and payload["more"] == 8
    assert all(r["relevant"] for r in payload["rules"])


# --- the reflex index, parsed once per file version ---------------------------


def _write_index(vault: Path, doc_count: int) -> None:
    reflex_index.write_index(vault, {
        "schema_version": reflex_index.SCHEMA_VERSION, "generated_at": "x",
        "avg_field_length": {}, "doc_count": doc_count, "postings": {}, "docs": {}})


def test_the_reflex_index_is_parsed_once_per_file_version(tmp_vault, monkeypatch):
    _write_index(tmp_vault, 1)
    parses = []
    real = reflex_index.load_index
    monkeypatch.setattr(reflex_index, "load_index", lambda root: parses.append(1) or real(root))
    first = tools.load_reflex_index(tmp_vault)
    assert tools.load_reflex_index(tmp_vault) is first
    assert len(parses) == 1

    _write_index(tmp_vault, 22)  # a SessionStart rebuild: new size, new mtime
    path = tmp_vault / ".mnemo" / reflex_index.INDEX_FILENAME
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert tools.load_reflex_index(tmp_vault)["doc_count"] == 22
    assert len(parses) == 2


def test_no_reflex_index_is_none_and_not_cached(tmp_vault):
    assert tools.load_reflex_index(tmp_vault) is None
    _write_index(tmp_vault, 3)
    assert tools.load_reflex_index(tmp_vault)["doc_count"] == 3


def test_a_reranked_call_parses_the_index_once(tmp_vault, monkeypatch):
    """The rerank's tie-break reads the index the list already parsed."""
    from mnemo.core.mcp import rerank

    _write_index(tmp_vault, 1)
    tools._REFLEX_CACHE.clear()
    parses = []
    real = reflex_index.load_index
    monkeypatch.setattr(reflex_index, "load_index", lambda root: parses.append(1) or real(root))
    tools._rerank_by_query(tmp_vault, [{"slug": "a", "type": "feedback", "source_count": 1}],
                           "a query about git")
    rerank.bm25f_scores(tmp_vault, "a query about git", ["a"])
    assert len(parses) == 1
