"""Go-aheads and questions are not corrections (#758)."""
from __future__ import annotations

import pytest

from mnemo.core import corrections
from mnemo.core.corrections import Correction

# The first five are quoted in #758 from a vault's friction ledger; the rest are
# written to the same shape.
GO_AHEADS = [
    "Pode mergear os dois por favor",
    "PODE SUBIR POR FAVOR!!",
    "pode abrir o pr e mergear",
    "Segue com o deploy",
    "boa pode mergear e subir a release por favor",
    "sim, faz isso por favor",
    "despacha os filhos por favor",
    "pode fazer a opcao 2 que voce recomendou",
    "pode mergear o teste passou",
    "go ahead",
    "LGTM, ship it",
    "ok, merge it",
]

QUESTIONS = [
    "eai como esta indo a migracao do banco?",
    "Espera, onde fica o log? Eu consigo abrir ele daqui?",
    "how is the release going?",
]

# Turns that open like a go-ahead or ask a question, and still say how to work.
CORRECTIONS = [
    "pode mergear quando o CI passar",
    "pode seguir com tudo sem me perguntar por favor",
    "Pode mergear e seguir mas nao mexe nos templates",
    "pode fazer o merge, VOCE TEM PERMISSAO PARA MUDAR O SETTINGS",
    "go ahead, but never push to main",
    "ok, no — use a worktree",
    "go ahead and merge it only after CI passes",
    "can't you check that through our integration?",
    "why didn't you use the helper?",
    "por que voce nao usou o helper?",
    "pq voce removeu o teste?",
    "voce consegue abrir a issue no outro repo? acho que tem que ser no repo principal",
    "quer dizer so faz o merge por enquanto",
    "never use npm in this repo, always yarn",
    "Pedir confirmacao sempre",
    "you can skip the integration tests here",
    # A long turn opening with a go-ahead may hold a rule no marker names.
    "pode mergear " + "e depois olha o deploy do site e confere a pagina " * 5,
]


@pytest.mark.parametrize("turn", GO_AHEADS)
def test_go_ahead(turn):
    assert corrections.screen_reason(turn) == "go-ahead"


@pytest.mark.parametrize("turn", QUESTIONS)
def test_question(turn):
    assert corrections.screen_reason(turn) == "question"


@pytest.mark.parametrize("turn", CORRECTIONS)
def test_correction_is_not_screened(turn):
    assert corrections.screen_reason(turn) == ""


def test_portuguese_no_mid_sentence_is_not_a_negation():
    assert corrections.is_go_ahead("pode mergear no main")
    assert not corrections.is_go_ahead("ok, no, merge it later")


def test_screen_judges_the_whole_turn_not_the_quote():
    turns = ["pode mergear, mas nunca sem rodar o CI", "pode mergear os dois por favor"]
    kept, dropped = corrections.screen(
        [Correction("pode mergear, mas", "Run CI first"),
         Correction("pode mergear os dois", "Merge")],
        turns,
    )
    assert [c.quote for c in kept] == ["pode mergear, mas"]
    assert [c.quote for c in dropped] == ["pode mergear os dois"]


SUMMARY = (
    "This session is being continued from a previous conversation that ran out of "
    "context. The summary below covers the earlier portion of the conversation.\n\n"
    "- User said: \"Eu reviso e abro os PRs\""
)


def test_a_compaction_summary_is_not_a_reaction_turn():
    turns = ["add a retry helper", SUMMARY, "never retry on 4xx"]
    assert corrections.reaction_indexes(turns) == [0, 2]
    assert corrections.locate("Eu reviso e abro os PRs", turns) is None
    kept, rejected = corrections.verify([Correction("Eu reviso e abro os PRs", "x")], turns)
    assert kept == [] and len(rejected) == 1


def test_rejection_reason_says_where_the_quote_was():
    brief = "Work on issue #12 in this repo: never retry on 4xx"
    turns = [brief, "<bash-input>gh pr merge 3 --admin</bash-input>", SUMMARY, "fine, thanks a lot"]
    assert corrections.rejection_reason("never retry on 4xx", turns) == "dispatch brief"
    assert corrections.rejection_reason("gh pr merge 3 --admin", turns) == "shell turn"
    assert corrections.rejection_reason("Eu reviso e abro", turns) == "compact summary"
    assert corrections.rejection_reason("always use tabs please", turns) == "not typed"
    assert corrections.rejection_reason("ok", turns) == "short"


def test_rejection_causes_counts_most_first():
    turns = ["Work on issue #12 in this repo: never retry on 4xx"]
    items = [Correction("always use tabs please", "x"), Correction("never use npm here", "y"),
             Correction("never retry on 4xx", "z")]
    assert corrections.rejection_causes(items, turns) == "not typed: 2, dispatch brief: 1"


def test_an_echoed_bullet_template_is_not_an_item():
    """Haiku copies the prompt's shape when it has nothing to report."""
    body = ('## Corrections\n'
            '- "<verbatim quote from a USER TURN>" → <one-line rule it establishes>\n'
            '- "never use npm in this repo" → Use yarn\n')
    assert corrections.parse_section(body) == [Correction("never use npm in this repo", "Use yarn")]
