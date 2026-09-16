"""The child is told how to end itself (2026-09-16 design)."""
from mnemo.core import dispatch


def test_closing_clause_names_the_self_stop_command():
    text = dispatch._closing_clause()
    assert "claude stop" in text
    assert "${CLAUDE_CODE_SESSION_ID:0:8}" in text


def test_closing_clause_orders_report_before_stop():
    text = dispatch._closing_clause()
    assert text.index("report") < text.index("claude stop")


def test_closing_clause_defers_to_git_not_to_judgement():
    text = dispatch._closing_clause()
    assert "git" in text
    # The child must not decide "there is nothing to deliver" on its own.
    assert "commit" in text


def test_issue_prompt_carries_the_closing_clause():
    prompt = dispatch.build_prompt(
        211, title="t", body="b", may=("push", "pr"),
    )
    assert "claude stop" in prompt


def test_piece_prompt_carries_the_closing_clause():
    from mnemo.core import contracts

    piece = contracts.Piece(
        slug="parser",
        files=["src/mnemo/core/contracts.py"],
        exposes=["parse_contract(path) -> Contract"],
    )
    prompt = dispatch.build_piece_prompt(
        piece, feature="contract-dispatch", may=("push", "pr"),
    )
    assert "claude stop" in prompt


def test_refusing_child_is_still_told_to_report_and_stop():
    """A child with no grant still ends itself — the stop is unconditional."""
    prompt = dispatch.build_prompt(211, title="t", body="b", may=())
    assert "claude stop" in prompt
