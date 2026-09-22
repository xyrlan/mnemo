"""``tools/measure_rerank_reads.py`` over synthetic log rows and transcripts (#416).

Nothing here opens a socket. What is pinned is what the report has to be right
about: a read is credited only inside its own session, to the latest list that
showed the slug; a judged row the log cannot audit is counted, not guessed at;
the arithmetic of the buckets; and — because a reader that expects a field its
writer never writes measures nothing — that the row the server writes is the
row this tool reads: ``test_the_row_the_server_writes_is_the_row_the_tool_reads``
runs a judged list and a read through ``handle_request`` and reports on the log
they left.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from mnemo.core.mcp import rerank, server

_TOOL = Path(__file__).resolve().parents[2] / "tools" / "measure_rerank_reads.py"
_spec = importlib.util.spec_from_file_location("measure_rerank_reads", _TOOL)
mrr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mrr)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("a test reached the network")
    monkeypatch.setattr("urllib.request.urlopen", refuse)
    monkeypatch.delenv(rerank.DEFAULT_KEY_ENV, raising=False)


# --- the log side -----------------------------------------------------------


def _list(session, shown, *, scores=None, marked=(), query="q", ts="2026-09-22T10:00:00Z",
          status="ok"):
    row = {"timestamp": ts, "tool": "list_rules_by_topic", "session_id": session,
           "args": {"topic": "t", "scope": "project", **({"query": query} if query else {})},
           "hit_slugs": list(shown)}
    if scores is not None:
        row["rerank"] = {"provider": "typesafe", "status": status, "judged": len(scores),
                         "relevant": len(marked), "relevant_slugs": list(marked),
                         "scores": [[s, 0.5] for s in scores]}
    return row


def _read(session, slug, ts="2026-09-22T10:01:00Z"):
    return {"timestamp": ts, "tool": "read_mnemo_rule", "session_id": session,
            "args": {"slug": slug, "scope": "project"}, "hit_slugs": [slug]}


def test_a_read_is_credited_to_the_latest_list_in_its_own_session_that_showed_it():
    rows = [
        _list("A", ["a", "b", "c", "d"], scores=["a", "b", "c"], marked=["a"]),
        _list("B", ["a", "x"]),                    # another session showing "a"
        _read("A", "a"),
        _read("A", "a"),                           # twice is still one read
        _read("A", "d"),                           # shown, never judged
        _list("A", ["c"], query=None),             # an unqueried list showing "c"
        _read("A", "c"),                           # ...is where this read came from
        _read("B", "b"),                           # B never saw "b"
    ]
    events, skipped = mrr.events_from_log(rows)
    calls, off_list = mrr.pair(events)
    judged = calls[0]
    assert judged["session"] == "A" and judged["reads"] == ["a", "d"]
    assert calls[1]["reads"] == []                 # B's list: B read nothing it showed
    assert calls[2]["reads"] == ["c"]
    assert off_list == 1
    assert skipped == {"before_416": 0, "no_session": 0}


def test_a_judged_row_the_log_cannot_audit_is_counted_and_left_out():
    before = _list(None, ["a", "b"])
    before["rerank"] = {"provider": "typesafe", "status": "ok", "judged": 2, "relevant": 1}
    sessionless = _list(None, ["a", "b"], scores=["a", "b"], marked=["a"])
    events, skipped = mrr.events_from_log([before, sessionless, _read(None, "a"),
                                           {"tool": "session_start.inject"}])
    assert events == []
    assert skipped == {"before_416": 1, "no_session": 1}


def test_a_list_the_stage_fell_back_on_is_a_baseline_list():
    rows = [_list("A", ["a", "b"], scores=[], status="no_key"), _read("A", "a")]
    calls, _ = mrr.pair(mrr.events_from_log(rows)[0])
    assert calls[0]["judged"] == [] and calls[0]["queried"]
    data = mrr.report(calls, 0)
    assert data["judged_calls"] == 0 and data["baseline_calls"] == 1
    assert data["baseline"]["top_3"]["read"] == 1


# --- the transcript side ----------------------------------------------------


def _use(name, uid, ts="2026-09-22T10:00:00.000Z", **args):
    return {"timestamp": ts, "message": {"content": [
        {"type": "tool_use", "id": uid, "name": name, "input": args}]}}


def _result(uid, items):
    return {"message": {"content": [{"type": "tool_result", "tool_use_id": uid,
                                     "content": [{"type": "text", "text": json.dumps(items)}]}]}}


def test_a_transcript_gives_the_marks_the_agent_was_shown():
    records = [
        _use("mcp__mnemo__list_rules_by_topic", "t1", topic="t", query="q"),
        _result("t1", [{"slug": "a", "relevant": True}, {"slug": "b", "relevant": False},
                       {"slug": "c"}]),
        _use("mcp__mnemo__read_mnemo_rule", "r1", slug="a"),
        _use("mcp__mnemo__read_mnemo_rule", "r2", slug="b"),
    ]
    calls, off_list = mrr.pair(mrr.events_from_transcript(records, session="s1"))
    assert len(calls) == 1 and off_list == 0
    call = calls[0]
    assert (call["shown"], call["judged"], call["marked"]) == (["a", "b", "c"], ["a", "b"], ["a"])
    assert call["reads"] == ["a", "b"]


def test_a_read_in_the_same_turn_as_the_list_is_not_from_it():
    both = {"timestamp": "2026-09-22T10:00:00.000Z", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "mcp__mnemo__list_rules_by_topic",
         "input": {"topic": "t", "query": "q"}},
        {"type": "tool_use", "id": "r1", "name": "mcp__mnemo__read_mnemo_rule",
         "input": {"slug": "a"}}]}}
    records = [both, _result("t1", [{"slug": "a", "relevant": True}, {"slug": "b"}])]
    calls, off_list = mrr.pair(mrr.events_from_transcript(records, session="s1"))
    assert calls[0]["reads"] == [] and off_list == 1


def test_a_window_keeps_what_is_inside_it():
    events = [{"kind": "read", "session": "s", "ts": "2026-09-01T00:00:00Z", "slug": "a"},
              {"kind": "read", "session": "s", "ts": "2026-09-22T10:00:00.000Z", "slug": "b"},
              {"kind": "read", "session": "s", "ts": "", "slug": "c"}]
    assert [e["slug"] for e in mrr.since(events, "2026-09-15T00:00:00Z")] == ["b"]
    assert len(mrr.since(events, "")) == 3


# --- the report -------------------------------------------------------------


def test_the_report_counts_the_three_buckets_and_the_issues_three_numbers():
    rows = [
        _list("A", ["a", "b", "c", "d", "e"], scores=["a", "b", "c", "d"], marked=["a", "b"]),
        _read("A", "a"), _read("A", "c"), _read("A", "e"),
        _list("B", ["x", "y"], scores=["x", "y"], marked=[]),
        _read("B", "y"),
        _list("C", ["p", "q", "r", "s"]),          # baseline
        _read("C", "p"), _read("C", "s"),
    ]
    calls, off_list = mrr.pair(mrr.events_from_log(rows)[0])
    data = mrr.report(calls, off_list)
    assert (data["judged_calls"], data["sessions"]) == (2, 2)
    assert (data["marked_then_read"], data["unmarked_then_read"],
            data["marked_never_read"]) == (1, 2, 1)
    assert {k: (v["shown"], v["read"]) for k, v in data["buckets"].items()} == {
        "marked": (2, 1), "unmarked": (4, 2), "unjudged": (1, 1)}
    assert (data["nothing_marked"], data["reads_where_nothing_marked"]) == (1, 1)
    assert data["baseline_calls"] == 1
    assert (data["baseline"]["top_3"]["shown"], data["baseline"]["top_3"]["read"]) == (3, 1)
    assert (data["baseline"]["rest"]["shown"], data["baseline"]["rest"]["read"]) == (1, 1)
    printed = mrr.format_report(data, "synthetic")
    assert "marked-then-read 1   unmarked-then-read 2   marked-never-read 1" in printed


def test_the_wilson_interval():
    assert mrr.wilson(0, 0) is None
    low, high = mrr.wilson(5, 10)
    assert round(low, 3) == 0.237 and round(high, 3) == 0.763
    assert mrr.wilson(0, 4)[0] == 0.0 and mrr.wilson(4, 4)[1] == 1.0


def test_an_empty_log_is_a_report_of_nothing():
    data = mrr.report([], 0, {"before_416": 8, "no_session": 0})
    printed = mrr.format_report(data, "empty")
    assert data["judged_calls"] == 0 and data["buckets"]["marked"]["rate"] is None
    assert "8 logged before #416" in printed


# --- the writer and the reader agree ----------------------------------------


def _page(vault: Path, slug: str, body: str) -> None:
    target = vault / "shared" / "feedback"
    target.mkdir(parents=True, exist_ok=True)
    (target / (slug + ".md")).write_text(
        "---\nname: %s\ndescription: d\ntype: feedback\nstability: stable\n"
        "sources:\n  - bots/a/m.md\ntags:\n  - workflow\n---\n\n%s\n" % (slug, body),
        encoding="utf-8")


def test_the_row_the_server_writes_is_the_row_the_tool_reads(tmp_vault, monkeypatch, capsys):
    monkeypatch.setattr("mnemo.core.mcp.tools._resolve_current_project", lambda vault_root: None)
    _page(tmp_vault, "the-one", "Commit a script before running it on prod.")
    _page(tmp_vault, "other", "Squash before merge.")
    _page(tmp_vault, "third", "Name branches after the issue.")

    def judge(state, questions):
        return {"answers": {key: {"noul": 0.9 if "Commit a script" in q["instructions"] else 0.1}
                            for key, q in questions.items()}}

    real = rerank.apply
    monkeypatch.setattr(rerank, "apply", lambda *a, **k: real(*a, client=judge, **k))
    monkeypatch.setenv(server.SESSION_ENV, "sess-1")
    monkeypatch.delenv(server.SOCKET_ENV, raising=False)
    cfg = {"recall": {"rerank": {"provider": "typesafe"}}}

    def call(name, arguments):
        server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}},
                              vault_root=tmp_vault, cfg=cfg)

    call("list_rules_by_topic", {"topic": "workflow", "query": "run a script on prod"})
    call("read_mnemo_rule", {"slug": "the-one"})
    call("read_mnemo_rule", {"slug": "third"})

    events, skipped = mrr._log_events(tmp_vault, "")
    calls, off_list = mrr.pair(events)
    data = mrr.report(calls, off_list, skipped)
    assert (data["judged_calls"], data["marked_then_read"], data["unmarked_then_read"],
            data["marked_never_read"], off_list) == (1, 1, 1, 0, 0)

    monkeypatch.setattr("mnemo.cli._resolve_vault", lambda: tmp_vault)
    assert mrr.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["marked_then_read"] == 1


def test_transcripts_are_read_from_the_projects_directory(tmp_path, capsys):
    project = tmp_path / "projects" / "-w-repo"
    project.mkdir(parents=True)
    records = [
        _use("mcp__mnemo__list_rules_by_topic", "t1", topic="t", query="q"),
        _result("t1", [{"slug": "a", "relevant": True}, {"slug": "b", "relevant": False}]),
        _use("mcp__mnemo__read_mnemo_rule", "r1", slug="a"),
    ]
    (project / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in records) + "\n",
                                      encoding="utf-8")
    assert mrr.main(["--transcripts", "--projects", str(tmp_path / "projects")]) == 0
    printed = capsys.readouterr().out
    assert "marked-then-read 1   unmarked-then-read 0   marked-never-read 0" in printed
