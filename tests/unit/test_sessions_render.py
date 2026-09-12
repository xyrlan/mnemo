"""Rendering the session queue.

The ordering is the message: blocked first, oldest first. The maintainer
reads line one and knows where to go.
"""
from __future__ import annotations

from mnemo.core.sessions.jobs import Session
from mnemo.core.sessions.render import render_queue


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
