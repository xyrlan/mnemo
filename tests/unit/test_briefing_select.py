"""briefing_select — which briefing the SessionStart envelope carries."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from mnemo.core import briefing, briefing_select
from mnemo.core.briefing import BriefingRecord
from mnemo.hooks import session_start


def _write(vault: Path, agent: str, session_id: str, *, date: str | None, body: str) -> Path:
    d = vault / "bots" / agent / "briefings" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    date_line = f"date: {date}\n" if date else ""
    p = d / f"{session_id}.md"
    p.write_text(
        "---\n"
        "type: briefing\n"
        f"agent: {agent}\n"
        f"session_id: {session_id}\n"
        f"{date_line}"
        "duration_minutes: 30\n"
        "---\n\n"
        f"{body}\n",
        encoding="utf-8",
    )
    return p


def _rec(session_id: str, body: str) -> BriefingRecord:
    return BriefingRecord(
        path=Path(f"/v/bots/p/briefings/sessions/{session_id}.md"),
        frontmatter={"session_id": session_id},
        body=body,
    )


_FILLER = [
    "Released v1.3.0 and tagged the build after the changelog pass.",
    "Chased a Windows encoding failure in the doctor output on a pipe.",
    "Drained the inbox backlog and rejected four reference rewrites.",
    "Calibrated the reflex floor for young vaults with few rules.",
    "Wrote the marketplace manifest and fixed the owner field.",
    "Moved the plugin launcher to bash so hooks spawn on macOS.",
]


def _pool(target_body: str, target_at: int) -> list[BriefingRecord]:
    bodies = list(_FILLER)
    bodies.insert(target_at, target_body)
    return [_rec(f"s{i}", b) for i, b in enumerate(bodies)]


# --- pick() with no query is exactly today's newest-wins -------------------


def test_pick_without_query_matches_pick_latest_briefing(tmp_path: Path) -> None:
    _write(tmp_path, "p", "aaa", date="2026-09-01", body="old")
    _write(tmp_path, "p", "zzz", date="2026-09-03", body="tie loses on id")
    _write(tmp_path, "p", "zzzz", date="2026-09-03", body="tie wins on id")
    undated = _write(tmp_path, "p", "undated", date=None, body="no date")
    os.utime(undated, (9_999_999_999, 9_999_999_999))  # newest mtime still sorts last

    expected = briefing.pick_latest_briefing(tmp_path, "p")
    got = briefing_select.pick(tmp_path, "p", query=None)
    assert got is not None and expected is not None
    assert got.path == expected.path
    assert got.body == expected.body
    assert got.frontmatter == expected.frontmatter


def test_pick_returns_none_without_briefings(tmp_path: Path) -> None:
    assert briefing_select.pick(tmp_path, "ghost", query=None) is None
    assert briefing_select.pick(tmp_path, "ghost", query="fix/issue-269") is None


def test_recent_briefings_is_capped_and_newest_first(tmp_path: Path) -> None:
    for day in range(1, 16):
        _write(tmp_path, "p", f"s{day:02d}", date=f"2026-09-{day:02d}", body="b")
    recent = briefing_select.recent_briefings(tmp_path, "p")
    assert len(recent) == briefing_select.POOL_SIZE
    assert [r.frontmatter["session_id"] for r in recent[:2]] == ["s15", "s14"]


def test_pick_with_query_can_reach_an_older_briefing(tmp_path: Path) -> None:
    for i, body in enumerate(_FILLER):
        _write(tmp_path, "p", f"f{i}", date=f"2026-09-1{i}", body=body)
    _write(tmp_path, "p", "old", date="2026-09-01",
           body="Built the dispatch watch modes. Watch modes poll the dispatch queue.")
    got = briefing_select.pick(tmp_path, "p", query="feat/dispatch/watch-modes")
    assert got is not None and got.frontmatter["session_id"] == "old"


# --- the query ---------------------------------------------------------------


def test_query_tokens_split_branch_parts_and_drop_scheme_words() -> None:
    assert briefing_select.query_tokens("feat/dispatch-last-metre/watch-modes") == [
        "dispatch", "last", "metre", "watch", "modes",
    ]
    assert briefing_select.query_tokens("fix/issue-269") == ["269"]


@pytest.mark.parametrize("query", [None, "", "master", "main", "fix/issue", "feat/wip"])
def test_a_query_that_names_no_task_keeps_the_newest(query) -> None:
    pool = _pool("Built the dispatch watch modes.", target_at=3)
    choice = briefing_select.choose(pool, query)
    assert choice.record is pool[0]
    assert choice.reason == "no_query"


# --- choose() ----------------------------------------------------------------


def test_choose_on_empty_pool_is_none() -> None:
    choice = briefing_select.choose([], "fix/issue-269")
    assert choice.record is None
    assert choice.reason == "no_briefings"


def test_a_single_briefing_is_returned_whatever_the_query() -> None:
    only = _rec("s0", "nothing about the query")
    assert briefing_select.choose([only], "feat/watch-modes").record is only


def test_a_clear_match_replaces_the_newest() -> None:
    target = "Built the dispatch watch modes. Watch modes poll the dispatch queue."
    pool = _pool(target, target_at=4)
    choice = briefing_select.choose(pool, "feat/dispatch/watch-modes")
    assert choice.record is pool[4]
    assert choice.reason == "ranked"
    assert choice.scores[0][0] == "s4"


def test_kebab_tokens_in_a_body_match_their_parts() -> None:
    target = "Shipped `watch-modes` for `dispatch-queue`; watch-modes is on by default."
    pool = _pool(target, target_at=2)
    choice = briefing_select.choose(pool, "feat/watch/modes")
    assert choice.record is pool[2]


def test_a_one_word_query_can_rank() -> None:
    # Reflex's overlap bar is 2 terms; a one-word branch has only one to match.
    target = "Fixed #269: the exploration count. 269 children measured for #269."
    pool = _pool(target, target_at=3)
    choice = briefing_select.choose(pool, "fix/issue-269")
    assert choice.query_tokens == ["269"]
    assert choice.record is pool[3]
    assert choice.reason == "ranked"


def test_a_match_on_the_newest_confirms_it() -> None:
    target = "Built the dispatch watch modes. Watch modes poll the dispatch queue."
    pool = _pool(target, target_at=0)
    choice = briefing_select.choose(pool, "feat/dispatch/watch-modes")
    assert choice.record is pool[0]
    assert choice.reason == "newest_confirmed"


def test_nothing_matching_keeps_the_newest() -> None:
    pool = _pool("Built the dispatch watch modes.", target_at=3)
    choice = briefing_select.choose(pool, "fix/issue-9999")
    assert choice.record is pool[0]
    assert choice.reason == "no_match"


def test_two_equal_matches_are_not_confident_and_keep_the_newest() -> None:
    body = "Built the dispatch watch modes. Watch modes poll the dispatch queue."
    bodies = list(_FILLER)
    bodies.insert(2, body)
    bodies.insert(5, body)
    pool = [_rec(f"s{i}", b) for i, b in enumerate(bodies)]
    choice = briefing_select.choose(pool, "feat/dispatch/watch-modes")
    assert choice.record is pool[0]
    assert choice.reason == "relative_gap_fail"


def test_a_weak_single_mention_does_not_clear_the_floor() -> None:
    # One passing mention in a long body, in a pool where the word is common
    # enough to carry little idf: below reflex's floor, so the newest stays.
    long_body = ("Reviewed the release notes and the doctor output. " * 40) + "queue"
    bodies = [f"{f} queue" for f in _FILLER[:4]] + [long_body] + _FILLER[4:]
    pool = [_rec(f"s{i}", b) for i, b in enumerate(bodies)]
    choice = briefing_select.choose(pool, "feat/queue")
    assert choice.record is pool[0]
    assert choice.reason == "absolute_floor_fail"


def test_duplicate_session_ids_do_not_collide() -> None:
    # A migrated worktree briefing can share an id with another file; the
    # matching one must still be the one returned.
    pool = [_rec("dup", f) for f in _FILLER]
    pool.insert(3, _rec("dup", "Built the dispatch watch modes. Watch modes poll the dispatch queue."))
    choice = briefing_select.choose(pool, "feat/dispatch/watch-modes")
    assert choice.record is pool[3]


# --- the hook ----------------------------------------------------------------


def test_hook_picks_with_no_query(tmp_path: Path, monkeypatch) -> None:
    seen: list = []
    real_pick = briefing_select.pick

    def spy(vault_root, agent_name, *, query):
        seen.append(query)
        return real_pick(vault_root, agent_name, query=query)

    monkeypatch.setattr(briefing_select, "pick", spy)
    _write(tmp_path, "p", "abc", date="2026-09-15", body="Stopped at line 42")
    payload = session_start._build_injection_payload(
        tmp_path, current_project="p", inject_briefing=True,
    )
    assert seen == [None]
    assert "[last-briefing session=abc date=2026-09-15 duration_minutes=30]" in payload


def test_hook_records_the_briefing_it_injects(tmp_path: Path, monkeypatch) -> None:
    reads: list = []
    monkeypatch.setattr(
        briefing, "record_briefing_read",
        lambda vault_root, record, **kw: reads.append((vault_root, record, kw)),
        raising=False,
    )
    _write(tmp_path, "p", "old", date="2026-09-01", body="old body")
    _write(tmp_path, "p", "new", date="2026-09-15", body="new body")
    payload = session_start._build_injection_payload(
        tmp_path, current_project="p", inject_briefing=True,
        session_id="reader", source="startup",
    )
    assert "new body" in payload
    assert len(reads) == 1
    vault_root, rec, kw = reads[0]
    assert kw == {"reader_session_id": "reader", "source": "startup"}
    assert vault_root == tmp_path
    assert rec.frontmatter["session_id"] == "new"
    assert rec.body.rstrip() in payload


def test_hook_records_nothing_without_a_briefing(tmp_path: Path, monkeypatch) -> None:
    reads: list = []
    monkeypatch.setattr(briefing, "record_briefing_read",
                        lambda *a, **kw: reads.append(a), raising=False)
    session_start._build_injection_payload(tmp_path, current_project="p", inject_briefing=True)
    session_start._build_injection_payload(tmp_path, current_project="p", inject_briefing=False)
    assert reads == []


def test_a_failing_recorder_does_not_cost_the_briefing(tmp_path: Path, monkeypatch) -> None:
    def boom(vault_root, record, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(briefing, "record_briefing_read", boom, raising=False)
    _write(tmp_path, "p", "abc", date="2026-09-15", body="Stopped at line 42")
    payload = session_start._build_injection_payload(
        tmp_path, current_project="p", inject_briefing=True,
    )
    assert "Stopped at line 42" in payload


# --- tools/measure_briefing_query.py -------------------------------------------


def test_replay_only_pools_briefings_that_existed_at_session_start(tmp_path: Path) -> None:
    from tools import measure_briefing_query as tool

    t0 = 1_700_000_000
    hour = 3600

    def put(sid: str, body: str, day: int, stamp: int) -> None:
        p = _write(tmp_path, "p", sid, date=f"2026-09-{day:02d}", body=body)
        os.utime(p, (stamp, stamp))

    put("match", "Built dispatch watch-modes in `src/mnemo/core/watch.py`.", 1, t0)
    for i, body in enumerate(_FILLER):
        put(f"f{i}", f"{body} Touched `src/other{i}.py`.", i + 2, t0 + (i + 1) * hour)
    target_written = t0 + 10 * hour  # duration 30 min -> started half an hour earlier
    # Written ten minutes before the target's briefing: during the target's
    # session, so the hook could not have injected it. Shares three files.
    put("late", "Touched `watch.py`, `a.py` and `b.py` for watch-modes.", 20, target_written - 600)
    put("target", "**Branch:** `feat/watch-modes`\nEdited `src/mnemo/core/watch.py`, "
        "`a.py` and `b.py`.", 21, target_written)

    result = tool.measure(tool.load(tmp_path, "p"))
    [row] = [r for r in result["rows"] if r["session"] == "target"]
    assert row["best"] == 1  # "late" (overlap 3) was never in the pool
    assert (row["reason"], row["newest"], row["gated"]) == ("ranked", 0, 1)
    assert result["counts"]["hits_gated"] == 1
    assert result["counts"]["hits_newest"] == 0
    assert tool.branch_named("**Branch:** master (clean)") == "master"
    assert tool.files_named("see `a/b/c.py` and README.md") == {"c.py"}
