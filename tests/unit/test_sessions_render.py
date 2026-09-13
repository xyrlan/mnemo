"""Rendering the session queue.

The ordering is the message: blocked first, oldest first. The maintainer
reads line one and knows where to go.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import _age, render_queue

NOW = datetime(2026, 9, 12, 12, 30, tzinfo=timezone.utc)


def _blocked(short_id: str, needs: str, *, updated_at: str = "2026-09-12T12:00:00.000Z", **kw) -> Session:
    return Session(short_id=short_id, state="working", tempo="blocked",
                   needs=needs, updated_at=updated_at, **kw)


def test_empty_says_so() -> None:
    assert "nenhuma sessão em background" in render_queue([])


def test_blocked_section_comes_first() -> None:
    out = render_queue([
        Session(short_id="w0", state="working", tempo="active", name="trabalhando"),
        _blocked("b0", "responde?", name="bloqueada"),
    ])

    assert out.index("TE ESPERANDO") < out.index("TRABALHANDO")
    assert out.index("bloqueada") < out.index("trabalhando")


def test_freshest_blocked_first() -> None:
    """Inverted by #196. This test used to assert oldest-first, which was the bug.

    ``tempo`` freezes at the moment a process stops writing, so among blocked
    sessions the stalest is the one most likely to be a corpse. Oldest-first
    pinned zombies to the top of the queue by construction.
    """
    out = render_queue([
        _blocked("new", "recente", name="recente", updated_at="2026-09-12T12:30:00.000Z"),
        _blocked("old", "antiga", name="antiga", updated_at="2026-09-12T12:00:00.000Z"),
    ])

    assert out.index("recente") < out.index("antiga")


def test_needs_is_shown_and_falls_back_to_detail() -> None:
    out = render_queue([
        _blocked("a", "a pergunta", name="com-needs"),
        Session(short_id="b", state="working", tempo="blocked",
                detail="o detalhe", name="sem-needs"),
    ])

    assert "a pergunta" in out
    assert "o detalhe" in out


def test_suggested_reply_renders_when_present() -> None:
    out = render_queue([_blocked("a", "qual idioma?", name="x", suggested_reply="português")])

    assert "sugerido" in out
    assert "português" in out


def test_done_section_lists_pull_requests() -> None:
    out = render_queue([Session(
        short_id="e51f", state="done", tempo="idle", name="entregue",
        children=({"id": "307", "kind": "pr"}, {"id": "308", "kind": "pr"}),
    )])

    assert "PRONTAS" in out
    assert "#307" in out and "#308" in out


def test_attach_hint_names_the_first_blocked_session() -> None:
    out = render_queue([_blocked("a3f1", "q?", name="x")])

    assert "claude attach a3f1" in out


def test_no_attach_hint_when_nothing_is_blocked() -> None:
    out = render_queue([Session(short_id="w0", state="working", tempo="active", name="w")])

    assert "claude attach" not in out


def test_age_says_agora_under_a_minute() -> None:
    assert _age("2026-09-12T12:29:30.000Z", now=NOW) == "agora"


def test_age_counts_minutes_up_to_an_hour() -> None:
    assert _age("2026-09-12T12:29:00.000Z", now=NOW) == "1m"
    assert _age("2026-09-12T11:31:00.000Z", now=NOW) == "59m"


def test_age_switches_to_hours_at_sixty_minutes() -> None:
    assert _age("2026-09-12T11:30:00.000Z", now=NOW) == "1h"
    assert _age("2026-09-11T12:30:00.000Z", now=NOW) == "24h"


def test_age_is_blank_without_a_timestamp() -> None:
    assert _age(None, now=NOW) == ""
    assert _age("", now=NOW) == ""


def test_age_is_blank_when_the_timestamp_is_unparsable() -> None:
    assert _age("ontem de tarde", now=NOW) == ""


def test_age_accepts_a_timestamp_without_a_timezone() -> None:
    """An updatedAt with no Z and no offset must degrade, not crash."""
    assert _age("2026-09-12T12:00:00", now=NOW) == "30m"


def test_age_of_a_future_timestamp_reads_as_agora() -> None:
    """Documents today's behaviour: a negative delta falls in the <1min branch."""
    assert _age("2026-09-12T13:00:00.000Z", now=NOW) == "agora"


# --- activity column (layer 1) ---

from mnemo.core.activity.summarize import Activity  # noqa: E402


def _working(short_id="abc", **kw):
    kw.setdefault("tempo", "active")
    kw.setdefault("name", "child")
    return Session(short_id=short_id, **kw)


def test_render_without_activities_is_unchanged():
    """The backward-compatibility test: the old call must produce old bytes."""
    sessions = [_working(detail="building")]

    assert render_queue(sessions) == render_queue(sessions, None)
    assert render_queue(sessions, {}) == render_queue(sessions)


def test_activity_replaces_detail_in_the_working_bucket():
    sessions = [_working(detail="building")]
    acts = {"abc": Activity(tool="Edit", target="dispatch.py", since=3)}

    out = render_queue(sessions, acts)

    assert "Edit dispatch.py (+3)" in out
    assert "building" not in out


def test_detail_is_the_fallback_when_a_session_has_no_activity():
    sessions = [_working(detail="building")]

    out = render_queue(sessions, {})

    assert "building" in out


def test_zero_since_shows_no_counter():
    sessions = [_working()]
    acts = {"abc": Activity(tool="Bash", target="Run tests", since=0)}

    out = render_queue(sessions, acts)

    assert "Bash Run tests" in out
    assert "(+0)" not in out


def test_repeated_tool_is_marked():
    """The loop signal has to be visible without counting columns."""
    sessions = [_working()]
    acts = {"abc": Activity(tool="Grep", target="linkScanOffset", since=7, repeated=True)}

    out = render_queue(sessions, acts)

    assert "Grep linkScanOffset (+7)" in out
    assert "↻" in out


def test_activity_without_a_target_still_renders():
    sessions = [_working()]
    acts = {"abc": Activity(tool="AskUserQuestion", target=None, since=1)}

    out = render_queue(sessions, acts)

    assert "AskUserQuestion" in out


def test_activity_does_not_leak_into_the_waiting_bucket():
    """A blocked session's claim is `needs`; activity would bury it."""
    sessions = [_working(short_id="w", tempo="blocked", needs="qual opção?")]
    acts = {"w": Activity(tool="Edit", target="x.py", since=2)}

    out = render_queue(sessions, acts)

    assert "qual opção?" in out
    assert "Edit x.py" not in out


def test_activity_for_an_unlisted_session_is_ignored():
    sessions = [_working()]
    acts = {"someone-else": Activity(tool="Edit", target="x.py")}

    out = render_queue(sessions, acts)

    assert "x.py" not in out


def test_long_activity_does_not_break_the_column():
    sessions = [_working()]
    acts = {"abc": Activity(tool="Bash", target="x" * 40, since=99)}

    out = render_queue(sessions, acts)

    for line in out.splitlines():
        assert len(line) <= 100, line
