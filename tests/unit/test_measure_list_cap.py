"""``tools/measure_list_cap.py`` over synthetic access-log rows (#765).

Pinned: the cap it evaluates is the server's (marked rules always shown); a
read counts as outside only when no earlier list *of its own session* would
have shown it; rows already capped by the server are left out of the cap
columns; latency is split by the rerank stage's status. And the row the
server writes is the row the tool reads.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from mnemo.core.mcp import server

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_list_cap.py"
_spec = importlib.util.spec_from_file_location("measure_list_cap", _TOOL)
mlc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mlc)


def _list(session, n, *, marked=(), status=None, ms=1000.0, ts="2026-10-20T10:00:00Z"):
    row = {"tool": "list_rules_by_topic", "session_id": session, "timestamp": ts,
           "hit_slugs": ["s%d" % i for i in range(n)], "result_count": n, "elapsed_ms": ms}
    if status:
        row["rerank"] = {"status": status, "relevant_slugs": ["s%d" % i for i in marked]}
    return row


def _read(session, slug, ts="2026-10-20T10:01:00Z"):
    return {"tool": "read_mnemo_rule", "session_id": session, "timestamp": ts,
            "args": {"slug": slug}, "result_count": 1}


def _cap(data, limit):
    return next(c for c in data["caps"] if c["limit"] == limit)


def test_a_read_past_the_cap_is_outside():
    data = mlc.report([_list("A", 30), _read("A", "s3"), _read("A", "s25")], [10])
    assert data["paired_reads"] == 2
    assert _cap(data, 10)["reads_outside"] == 1


def test_a_marked_rule_past_the_limit_is_still_shown():
    rows = [_list("A", 30, marked=range(14), status="ok"), _read("A", "s12")]
    data = mlc.report(rows, [10])
    assert _cap(data, 10)["reads_outside"] == 0
    assert _cap(data, 10)["items"]["max"] == 14


def test_reads_pair_only_within_their_session():
    rows = [_list("A", 30), _read("B", "s25"), _list("B", 30), _read("B", "s2")]
    data = mlc.report(rows, [10])
    assert data["paired_reads"] == 1  # B's first read came before B listed
    assert _cap(data, 10)["reads_outside"] == 0


def test_any_earlier_list_of_the_session_can_show_the_read():
    rows = [_list("A", 30), _list("A", 3), _read("A", "s5")]
    assert _cap(mlc.report(rows, [10]), 10)["reads_outside"] == 0


def test_rows_the_server_already_capped_are_left_out():
    capped = dict(_list("A", 10), total=40)
    data = mlc.report([capped, _read("A", "s2")], [5])
    assert data["already_capped"] == 1
    assert data["paired_reads"] == 0
    assert _cap(data, 5)["bytes"]["n"] == 0


def test_bytes_are_the_json_of_the_shown_slugs():
    data = mlc.report([_list("A", 30)], [10])
    assert _cap(data, 10)["bytes"]["max"] == len(json.dumps(["s%d" % i for i in range(10)]))
    assert data["listed"]["items"]["max"] == 30


def test_latency_is_split_by_rerank_status():
    rows = [_list("A", 5, status="ok", ms=4000.0), _list("A", 5, status="ok", ms=5000.0),
            _list("A", 5, status="no_key", ms=1000.0), _list("A", 5, ms=900.0)]
    lat = mlc.report(rows, [10])["elapsed_ms_by_rerank_status"]
    assert lat["ok"]["n"] == 2 and lat["ok"]["max"] == 5000.0
    assert lat["no_key"]["p50"] == 1000.0
    assert lat["off"]["n"] == 1


def test_torn_lines_are_skipped(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text('{"tool": "x"}\n\x00\x00garbage\n[1]\n{"tool": "y"}\n', encoding="utf-8")
    assert [r["tool"] for r in mlc.read_rows(log)] == ["x", "y"]


def test_the_report_formats(tmp_path):
    text = mlc.format_report(mlc.report([_list("A", 30), _read("A", "s20")], [5, 10]))
    assert "reads outside 1 of 1" in text and "limit 10" in text


def test_the_row_the_server_writes_is_the_row_the_tool_reads(tmp_vault, monkeypatch):
    monkeypatch.setattr("mnemo.core.mcp.tools._resolve_current_project", lambda vault_root: None)
    monkeypatch.setattr("mnemo.core.mcp.access_log._load_telemetry_config", lambda: (True, 1_048_576))
    monkeypatch.setenv(server.SESSION_ENV, "sess-1")
    monkeypatch.delenv(server.SOCKET_ENV, raising=False)
    target = tmp_vault / "shared" / "feedback"
    target.mkdir(parents=True)
    for i in range(3):
        (target / f"r{i}.md").write_text(
            f"---\nname: r{i}\ndescription: d\ntype: feedback\nstability: stable\n"
            "sources:\n  - bots/a/m.md\ntags:\n  - auto-promoted\n  - git\n---\n\nbody\n",
            encoding="utf-8")
    for name, arguments in (("list_rules_by_topic", {"topic": "git", "limit": 0}),
                            ("read_mnemo_rule", {"slug": "r2"})):
        server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}}, vault_root=tmp_vault)
    rows = mlc.read_rows(tmp_vault / ".mnemo" / "mcp-access-log.jsonl")
    # The server's own rows carry ``total``: counted as capped, never re-capped.
    data = mlc.report(rows, [1])
    assert data["lists"] == 1 and data["already_capped"] == 1
    # The same row, as one written before #765, pairs the read.
    for r in rows:
        r.pop("total", None)
    data = mlc.report(rows, [1])
    assert data["paired_reads"] == 1 and _cap(data, 1)["reads_outside"] == 1
