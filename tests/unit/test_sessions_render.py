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


def test_oldest_blocked_first() -> None:
    out = render_queue([
        _blocked("new", "recente", name="recente", updated_at="2026-09-12T12:30:00.000Z"),
        _blocked("old", "antiga", name="antiga", updated_at="2026-09-12T12:00:00.000Z"),
    ])

    assert out.index("antiga") < out.index("recente")


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
