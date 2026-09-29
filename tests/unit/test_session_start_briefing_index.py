"""SessionStart hands the session an index of the ten newest briefings' TL;DRs (#551).

#548 (``tools/measure_briefing_index.py``) measured the index; #551 ships it.
The hook and the tool build it with one module, ``core/briefing_index.py``,
so these tests pin that the hook's block is the tool's block for the same
briefings, and what the hook adds around it: the pool by mtime, the cap, the
log rows.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from mnemo.core import briefing_index, briefing_select
from mnemo.core.mcp import access_log
from mnemo.hooks import session_start
from mnemo.hooks.session_start import ENVELOPE_MAX_BYTES, _build_injection_payload

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools import measure_briefing_index as tool  # noqa: E402

T0 = 1_700_000_000


@pytest.fixture
def telemetry_on(monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))


def _write(vault: Path, project: str, sid: str, *, at: int, date: str = "2026-09-28",
           tldr: str | None = None, body: str | None = None) -> Path:
    d = vault / "bots" / project / "briefings" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    if body is None:
        body = (f"# Briefing — {project} — {sid}\n\n## TL;DR\n\n{tldr or 'Did ' + sid + '.'}\n\n"
                "## What I did\n\n- a long section the index leaves out\n")
    p = d / f"{sid}.md"
    p.write_text(
        f"---\ntype: briefing\nagent: {project}\nsession_id: {sid}\ndate: {date}\n"
        f"duration_minutes: 12\n---\n\n{body}",
        encoding="utf-8",
    )
    os.utime(p, (T0 + at, T0 + at))
    return p


def _rows(vault: Path) -> list[dict]:
    log = vault / ".mnemo" / "briefing-log.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _size(text: str) -> int:
    return len(text.encode("utf-8"))


# --- one builder -------------------------------------------------------------------------


def test_the_tool_builds_with_the_hooks_module():
    for name in ("tldr", "cut_to", "fair_cut", "entry_head", "index_block"):
        assert getattr(tool, name) is getattr(briefing_index, name)
    assert (tool.INDEX_OPEN, tool.INDEX_CLOSE) == (briefing_index.INDEX_OPEN, briefing_index.INDEX_CLOSE)
    assert tool.POOL == briefing_index.POOL == briefing_select.POOL_SIZE


def test_the_hooks_block_is_the_tools_block_for_the_same_briefings(tmp_path):
    for i in range(12):
        _write(tmp_path, "p", f"s{i:02d}", at=i, date=f"2026-09-{i + 1:02d}")
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index")

    # the tool's view: #534's candidates, newest by mtime, with their frontmatter
    recs = briefing_select.recent_briefings(tmp_path, "p")
    entries = [({k: str(v) for k, v in r.frontmatter.items()}, tool.tldr(r.body.strip())) for r in recs]
    block, cut = tool.index_block(entries, ENVELOPE_MAX_BYTES - 0)
    assert cut == 0
    # alone in the envelope it opens it, as #548's arms had it (``with_slot``)
    assert out == block.lstrip("\n")
    assert out.startswith("[recent-briefings count=10 newest first")
    assert out.endswith("[/recent-briefings]")
    heads = [ln for ln in out.splitlines() if ln.startswith("### ")]
    assert heads == [f"### 2026-09-{d:02d}" for d in range(12, 2, -1)]
    assert "Did s11." in out and "Did s02." in out and "s01" not in out
    assert "a long section the index leaves out" not in out


# --- the pool ------------------------------------------------------------------------------


def test_the_pool_is_newest_by_mtime_not_by_session_id(tmp_path):
    # Three sessions on one day: the old sort put "zzz" first whatever its time.
    _write(tmp_path, "p", "zzz", at=1, tldr="written first")
    _write(tmp_path, "p", "aaa", at=3, tldr="written last")
    _write(tmp_path, "p", "mmm", at=2, tldr="written second")
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index")
    assert out.index("written last") < out.index("written second") < out.index("written first")


def test_a_briefing_without_a_tldr_lists_its_first_paragraph(tmp_path):
    _write(tmp_path, "p", "old", at=1, body="# Briefing\n\nStopped at line 42 of auth.ts\n\nMore.\n")
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index")
    assert "### 2026-09-28\nStopped at line 42 of auth.ts\n[/recent-briefings]" in out
    assert "More." not in out


def test_no_briefing_no_block(tmp_path):
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index")
    assert out == ""


# --- the cap -------------------------------------------------------------------------------


def test_the_tldrs_share_the_room_left_after_the_other_blocks(tmp_path):
    for i in range(10):
        _write(tmp_path, "p", f"s{i}", at=i, tldr=("word%d " % i) * 200)
    notice = "[mnemo] " + "n" * 4000
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index", blocks=[notice])
    assert _size(out) <= ENVELOPE_MAX_BYTES
    assert out.startswith(notice)
    assert out.count("### ") == 10
    assert out.count(briefing_index.ELLIPSIS) == 10
    assert out.endswith("[/recent-briefings]")


def test_when_not_even_the_headings_fit_the_oldest_entries_go(tmp_path):
    for i in range(10):
        _write(tmp_path, "p", f"s{i}", at=i, date=f"2026-09-{i + 1:02d}")
    recs = briefing_select.recent_briefings(tmp_path, "p")
    room = 200
    block, n, _ = briefing_index.fit(recs, room)
    assert 0 < n < 10
    assert _size(block) <= room
    assert f"count={n} " in block
    heads = [ln for ln in block.splitlines() if ln.startswith("### ")]
    assert heads == [f"### 2026-09-{d:02d}" for d in range(10, 10 - n, -1)]
    assert briefing_index.fit(recs, 20) == ("", 0, 0)


def test_an_envelope_with_no_room_left_carries_no_index(tmp_path):
    _write(tmp_path, "p", "s", at=1)
    notice = "[mnemo] " + "n" * (ENVELOPE_MAX_BYTES - 50)
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index", blocks=[notice])
    assert out == notice


# --- the log -------------------------------------------------------------------------------


def test_each_listed_briefing_gets_a_row_newest_first(tmp_path, telemetry_on):
    for i in range(12):
        _write(tmp_path, "p", f"s{i:02d}", at=i)
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index", session_id="reader", source="clear")
    rows = _rows(tmp_path)
    assert [r["session_id"] for r in rows] == [f"s{i:02d}" for i in range(11, 1, -1)]
    assert {r["mode"] for r in rows} == {"index"}
    assert {r["entries"] for r in rows} == {10}
    assert {r["index_bytes"] for r in rows} == {_size(out.lstrip("\n"))}
    assert {r["reader_session_id"] for r in rows} == {"reader"}
    assert {r["source"] for r in rows} == {"clear"}


def test_the_whole_briefing_row_says_last(tmp_path, telemetry_on):
    _write(tmp_path, "p", "s", at=1)
    _build_injection_payload(tmp_path, current_project="p", inject_briefing=True, briefing_mode="last")
    (row,) = _rows(tmp_path)
    assert (row["mode"], row["entries"], row["index_bytes"]) == ("last", 1, None)


def test_a_failing_recorder_does_not_cost_the_index(tmp_path, monkeypatch):
    from mnemo.core import briefing

    def boom(*a, **kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(briefing, "record_briefing_read", boom)
    _write(tmp_path, "p", "s", at=1, tldr="Stopped at line 42")
    out = _build_injection_payload(tmp_path, current_project="p", inject_briefing=True,
                                   briefing_mode="index")
    assert "Stopped at line 42" in out


# --- the hook, end to end ------------------------------------------------------------------


def test_the_hook_ships_the_index_by_default(tmp_vault, tmp_home, tmp_tempdir, monkeypatch, capsys):
    cfg_path = tmp_vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(tmp_vault),
        "injection": {"enabled": True, "telemetry": {"enabled": True}},
        "briefings": {"enabled": True},
        "capture": {"sessionStartEnd": False},
    }), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))
    for i in range(3):
        _write(tmp_vault, "vault", f"s{i}", at=i, tldr=f"Work item {i} is where it stopped.")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"session_id": "new-session", "cwd": str(tmp_vault), "source": "startup"})))

    assert session_start.main() == 0

    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "[recent-briefings count=3 newest first" in context
    assert "[last-briefing" not in context
    assert context.index("Work item 2") < context.index("Work item 0")

    log = tmp_vault / ".mnemo" / "mcp-access-log.jsonl"
    rows = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    (inj,) = [r for r in rows if r.get("tool") == "session_start.inject"]
    assert inj["included_briefing"] is True
    assert inj["briefing_trimmed"] is False
    reads = _rows(tmp_vault)
    assert [r["session_id"] for r in reads] == ["s2", "s1", "s0"]
    assert {(r["mode"], r["entries"], r["reader_session_id"]) for r in reads} == {("index", 3, "new-session")}
