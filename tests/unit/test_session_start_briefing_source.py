"""SessionStart gates the briefing on Claude Code's ``source`` (#352).

A resumed or forked session already holds the briefing its first start
injected, and a compacted one just made room. Before #352, ``source`` only
decorated a log line, so every one of those got the whole block again.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

BRIEFING_FRAME = "[last-briefing session=abc123 date=2026-04-19 duration_minutes=17]"


def _seed(vault: Path, monkeypatch, *, inject_last: bool = True) -> None:
    cfg_path = vault / "mnemo.config.json"
    cfg_path.write_text(json.dumps({
        "vaultRoot": str(vault),
        "injection": {"enabled": True, "telemetry": {"enabled": True}},
        "briefings": {"enabled": True, "injectLastOnSessionStart": inject_last},
        "capture": {"sessionStartEnd": False},
    }), encoding="utf-8")
    monkeypatch.setenv("MNEMO_CONFIG_PATH", str(cfg_path))

    sessions_dir = vault / "bots" / "vault" / "briefings" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / "abc123.md").write_text(
        "---\n"
        "type: briefing\n"
        "session_id: abc123\n"
        "date: 2026-04-19\n"
        "duration_minutes: 17\n"
        "---\n\n"
        "# Briefing\n\nStopped at line 42 of auth.ts\n",
        encoding="utf-8",
    )
    # One universal rule, so the topic envelope has something to say even
    # when the briefing is withheld.
    rule_dir = vault / "shared" / "feedback"
    rule_dir.mkdir(parents=True, exist_ok=True)
    (rule_dir / "use-tabs.md").write_text(
        "---\n"
        "name: use-tabs\n"
        "description: d\n"
        "type: feedback\n"
        "stability: stable\n"
        "sources:\n"
        "  - bots/a/memory/x.md\n"
        "  - bots/b/memory/y.md\n"
        "tags:\n"
        "  - style\n"
        "---\n\nbody\n",
        encoding="utf-8",
    )


def _run(vault: Path, monkeypatch, capsys, source: str | None) -> tuple[str, list[dict]]:
    from mnemo.hooks import session_start

    payload = {"session_id": "s1", "cwd": str(vault)}
    if source is not None:
        payload["source"] = source
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert session_start.main() == 0

    out = capsys.readouterr().out
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out else ""
    log = vault / ".mnemo" / "mcp-access-log.jsonl"
    rows = []
    if log.exists():
        rows = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return context, [r for r in rows if r.get("tool") == "session_start.inject"]


@pytest.mark.parametrize("source", ["startup", "clear", None, "some-future-source"])
def test_fresh_context_gets_the_briefing(
    tmp_vault, tmp_home, tmp_tempdir, monkeypatch, capsys, source
) -> None:
    _seed(tmp_vault, monkeypatch)
    context, rows = _run(tmp_vault, monkeypatch, capsys, source)

    assert BRIEFING_FRAME in context
    assert "Stopped at line 42 of auth.ts" in context
    assert len(rows) == 1
    assert rows[0]["included_briefing"] is True
    assert rows[0]["source"] == (source or "startup")


@pytest.mark.parametrize("source", ["resume", "fork", "compact"])
def test_context_that_holds_it_gets_no_second_copy(
    tmp_vault, tmp_home, tmp_tempdir, monkeypatch, capsys, source
) -> None:
    _seed(tmp_vault, monkeypatch)
    context, rows = _run(tmp_vault, monkeypatch, capsys, source)

    assert "[last-briefing" not in context
    assert "Stopped at line 42" not in context
    # The topic envelope still goes out: it is what tells the model to call
    # list_rules_by_topic at all.
    assert context.startswith("mnemo://v1")
    assert len(rows) == 1
    assert rows[0]["included_briefing"] is False
    assert rows[0]["source"] == source


def test_only_the_startup_counts_as_a_briefing_read(
    tmp_vault, tmp_home, tmp_tempdir, monkeypatch, capsys
) -> None:
    _seed(tmp_vault, monkeypatch)
    log = tmp_vault / ".mnemo" / "briefing-log.jsonl"

    def reads() -> int:
        if not log.exists():
            return 0
        return sum(1 for line in log.read_text(encoding="utf-8").splitlines() if line.strip())

    _run(tmp_vault, monkeypatch, capsys, "startup")
    assert reads() == 1
    _run(tmp_vault, monkeypatch, capsys, "resume")
    assert reads() == 1


def test_rows_name_the_session_that_received_them(
    tmp_vault, tmp_home, tmp_tempdir, monkeypatch, capsys
) -> None:
    # #359: the briefing row's ``session_id`` is the briefing's author; the
    # hook's own session id and source are what make a row countable.
    _seed(tmp_vault, monkeypatch)
    _, inject_rows = _run(tmp_vault, monkeypatch, capsys, "clear")

    (read,) = [
        json.loads(line)
        for line in (tmp_vault / ".mnemo" / "briefing-log.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert read["session_id"] == "abc123"
    assert read["reader_session_id"] == "s1"
    assert read["source"] == "clear"
    assert inject_rows[0]["session_id"] == "s1"


def test_config_off_still_wins_on_startup(
    tmp_vault, tmp_home, tmp_tempdir, monkeypatch, capsys
) -> None:
    _seed(tmp_vault, monkeypatch, inject_last=False)
    context, _ = _run(tmp_vault, monkeypatch, capsys, "startup")

    assert "[last-briefing" not in context


def test_briefing_wanted_table() -> None:
    from mnemo.hooks.session_start import _briefing_wanted

    on = {"briefings": {"injectLastOnSessionStart": True}}
    assert _briefing_wanted(on, "startup")
    assert _briefing_wanted(on, "clear")
    assert not _briefing_wanted(on, "resume")
    assert not _briefing_wanted(on, "fork")
    assert not _briefing_wanted(on, "compact")
    assert _briefing_wanted({}, "startup")  # default is on
    assert not _briefing_wanted({"briefings": {"injectLastOnSessionStart": False}}, "startup")
