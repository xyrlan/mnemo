"""corrections.quote_is_specific: the content-token gate on a keep quote (#119)."""
from __future__ import annotations

import pytest

from mnemo.core.corrections import quote_is_specific


@pytest.mark.parametrize("q", [
    "implementa os fixes",
    "vamos testar a opcao A?",
    "pode aplicar",
    "ok, faz isso",
    "yes do it",
])
def test_generic_quotes_rejected(q):
    assert quote_is_specific(q) is False


@pytest.mark.parametrize("q", [
    "vamo mudar o env do app para prod e subir",
    "usa o valor que voce calculou mesmo, pode aplicar o fix + backfill",
    "never run migrations against production without a backup first",
])
def test_specific_quotes_accepted(q):
    assert quote_is_specific(q) is True
