"""The child is told how to end itself (2026-09-16 design)."""
import pytest

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


@pytest.mark.parametrize("may", [(), ("push",), ("push", "pr")])
def test_the_closing_steps_stay_wrapped_under_their_numbers(may) -> None:
    """Wrapped under its number: a line at column 0 would read as a new section.

    Every grant phrases step 2 differently and the longest of them is the one
    that must still fit, so each is measured rather than the default alone.
    """
    lines = dispatch._closing_clause(may).splitlines()
    steps = [line for line in lines if line[:2] in ("1.", "2.", "3.")]
    assert len(steps) == 3
    assert all(len(line) <= 78 for line in lines)
    # Continuations hang under their number instead of starting a section.
    for line in lines:
        if line and line not in steps and not line.endswith(":"):
            assert line.startswith("   "), line


# --- the default grant (2026-09-16) -------------------------------------------


def test_absent_may_flag_defaults_to_pr():
    """Nothing said means the child publishes (2026-09-16 design)."""
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant(None) == ("push", "pr")


def test_explicit_none_still_withholds():
    """`--may none` is how a maintainer opts out; it must survive the default."""
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant("none") == ()


def test_explicit_push_is_not_upgraded():
    from mnemo.cli.commands import dispatch as cmd

    assert cmd._default_grant("push") == ("push",)


def test_a_contract_piece_can_still_withhold_against_the_default():
    """`piece_grant` already resolves precedence; the new default flows
    through it as the flag's value, so `may: none` on a piece must still win."""
    from mnemo.core import contracts, dispatch
    from mnemo.cli.commands import dispatch as cmd

    default = cmd._default_grant(None)
    spike = contracts.Piece(slug="spike", files=["x.py"], may=())
    normal = contracts.Piece(slug="normal", files=["y.py"])

    assert dispatch.piece_grant(spike, default) == ()
    assert dispatch.piece_grant(normal, default) == ("push", "pr")
