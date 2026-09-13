"""``mnemo dispatch --contract`` — reading the file that names the pieces.

The contract is the only durable artifact of a decomposition. It is parsed
strictly: a file that cannot be read completely is refused before any worktree
exists, because a half-understood contract dispatches children against
boundaries nobody agreed to.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from mnemo.core import contracts


VALID = """\
---
feature: contract-dispatch
created: 2026-09-12
verdict: parallel
---

## parser
- **files:** src/mnemo/core/contracts.py, tests/unit/test_contracts.py
- **exposes:** `parse_contract(path) -> Contract`
- **consumes:** nothing

## dispatch-seam
- **files:** src/mnemo/core/dispatch.py
- **consumes:** `parse_contract` from `parser`
- **exposes:** `mnemo dispatch --contract <path>`
"""


def write(tmp_path: Path, text: str) -> Path:
    target = tmp_path / "contract.md"
    target.write_text(text, encoding="utf-8")
    return target


def test_parses_feature_and_verdict(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    assert contract.feature == "contract-dispatch"
    assert contract.verdict == "parallel"


def test_parses_every_piece_in_order(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    assert [p.slug for p in contract.pieces] == ["parser", "dispatch-seam"]


def test_parses_a_piece_fields(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    piece = contract.pieces[0]
    assert piece.files == [
        "src/mnemo/core/contracts.py",
        "tests/unit/test_contracts.py",
    ]
    assert piece.exposes == ["`parse_contract(path) -> Contract`"]
    assert piece.consumes == []


def test_consumes_records_the_owning_piece(tmp_path: Path) -> None:
    contract = contracts.parse_contract(write(tmp_path, VALID))
    consumed = contract.pieces[1].consumes
    assert consumed == [("`parse_contract`", "parser")]


def test_consumes_owner_may_be_written_without_backticks(tmp_path: Path) -> None:
    """The owner is a lookup key, not quoted text — formatting must not change it."""
    text = VALID.replace("from `parser`", "from parser")
    contract = contracts.parse_contract(write(tmp_path, text))
    assert contract.pieces[1].consumes == [("`parse_contract`", "parser")]


def test_signature_with_internal_commas_stays_intact(tmp_path: Path) -> None:
    """A multi-argument signature must not be torn apart at its own commas."""
    text = VALID.replace(
        "- **exposes:** `parse_contract(path) -> Contract`",
        "- **exposes:** `merge(a, b) -> T`",
    )
    contract = contracts.parse_contract(write(tmp_path, text))
    assert contract.pieces[0].exposes == ["`merge(a, b) -> T`"]


def test_two_comma_separated_signatures_still_parse_as_two(tmp_path: Path) -> None:
    text = VALID.replace(
        "- **exposes:** `parse_contract(path) -> Contract`",
        "- **exposes:** `f(a, b) -> T`, `g() -> U`",
    )
    contract = contracts.parse_contract(write(tmp_path, text))
    assert contract.pieces[0].exposes == ["`f(a, b) -> T`", "`g() -> U`"]


def test_consumes_signature_with_commas_keeps_its_owner(tmp_path: Path) -> None:
    text = VALID.replace(
        "- **consumes:** `parse_contract` from `parser`",
        "- **consumes:** `merge(a, b) -> T` from `parser`",
    )
    contract = contracts.parse_contract(write(tmp_path, text))
    assert contract.pieces[1].consumes == [("`merge(a, b) -> T`", "parser")]


def test_files_with_several_paths_still_splits(tmp_path: Path) -> None:
    """Pin the `files` grammar (plain comma split) against regression."""
    contract = contracts.parse_contract(write(tmp_path, VALID))
    assert contract.pieces[0].files == [
        "src/mnemo/core/contracts.py",
        "tests/unit/test_contracts.py",
    ]


def test_unreadable_contract_raises_contract_error(tmp_path: Path) -> None:
    """Any failure to fully read the file must surface as ContractError.

    A raw UnicodeDecodeError (not an OSError) escaping here would break the
    promise that every refusal happens before any git state is created.
    """
    target = tmp_path / "contract.md"
    target.write_bytes(b"\xff\xfe\x00broken")
    with pytest.raises(contracts.ContractError):
        contracts.parse_contract(target)


def test_orphan_consumes_is_refused(tmp_path: Path) -> None:
    """A signature nobody exposes means the cut is wrong, not merely untidy."""
    text = VALID.replace("`parse_contract` from `parser`", "`missing` from ghost")
    with pytest.raises(contracts.ContractError, match="ghost"):
        contracts.parse_contract(write(tmp_path, text))


def test_duplicate_slug_is_refused(tmp_path: Path) -> None:
    """Two pieces with one slug would collide on the same worktree path."""
    text = VALID.replace("## dispatch-seam", "## parser")
    with pytest.raises(contracts.ContractError, match="parser"):
        contracts.parse_contract(write(tmp_path, text))


def test_unaddressable_slug_is_refused(tmp_path: Path) -> None:
    """A slug must survive being a directory name and a branch segment."""
    text = VALID.replace("## parser", "## Parser/One")
    with pytest.raises(contracts.ContractError, match="slug"):
        contracts.parse_contract(write(tmp_path, text))


def test_piece_without_files_is_refused(tmp_path: Path) -> None:
    """Without a file boundary there is nothing keeping children apart."""
    text = VALID.replace(
        "- **files:** src/mnemo/core/contracts.py, tests/unit/test_contracts.py\n", ""
    )
    with pytest.raises(contracts.ContractError, match="files"):
        contracts.parse_contract(write(tmp_path, text))


def test_missing_verdict_is_refused(tmp_path: Path) -> None:
    text = VALID.replace("verdict: parallel\n", "")
    with pytest.raises(contracts.ContractError, match="verdict"):
        contracts.parse_contract(write(tmp_path, text))


def test_no_pieces_is_refused(tmp_path: Path) -> None:
    text = VALID.split("## parser")[0]
    with pytest.raises(contracts.ContractError, match="no pieces"):
        contracts.parse_contract(write(tmp_path, text))


def test_sequential_verdict_parses_but_is_not_dispatchable(tmp_path: Path) -> None:
    """``sequential`` is a real answer, not a failure — it parses fine."""
    text = VALID.replace("verdict: parallel", "verdict: sequential")
    contract = contracts.parse_contract(write(tmp_path, text))
    assert contract.verdict == "sequential"
    assert not contract.is_dispatchable


def test_piece_consuming_from_itself_is_refused(tmp_path: Path) -> None:
    """A piece's own work is not a boundary — this is a cut that did not happen."""
    text = VALID.replace(
        "`parse_contract` from `parser`", "`parse_contract` from `dispatch-seam`"
    )
    with pytest.raises(contracts.ContractError, match="itself"):
        contracts.parse_contract(write(tmp_path, text))
