"""Retirement: the only friction piece that writes to a vault page.

What is pinned here is the safety argument. A retirement writes exactly four
frontmatter keys and ``undo`` removes exactly those; it refuses a cycle, a
missing replacement and a record the ledger does not hold; more than the
per-run cap retires nothing; and a ``superseded_by`` key the ledger cannot
back retires nothing either.
"""
from __future__ import annotations

import inspect
from dataclasses import replace
from pathlib import Path

import pytest

from mnemo.core import filters
from mnemo.core.filters import (
    SUPERSEDED_AT,
    SUPERSEDED_BY,
    SUPERSEDED_BY_FRICTION,
    SUPERSEDES,
    parse_frontmatter,
)
from mnemo.core.friction import ledger, retire as R
from mnemo.core.mcp import access_log
from tests.unit import _retire_fixtures as fx


@pytest.fixture(autouse=True)
def _telemetry_on(monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))


@pytest.fixture
def vault(tmp_vault: Path) -> Path:
    fx.seed(tmp_vault)
    return tmp_vault


def _fm(vault: Path, slug: str) -> dict:
    return parse_frontmatter((vault / "shared" / "feedback" / f"{slug}.md").read_text(encoding="utf-8"))


def _bytes(vault: Path, slug: str) -> bytes:
    return (vault / "shared" / "feedback" / f"{slug}.md").read_bytes()


# --- the contract's signatures -------------------------------------------------


def test_exposed_signatures_match_the_contract():
    assert isinstance(R.MAX_RETIREMENTS_PER_RUN, int) and R.MAX_RETIREMENTS_PER_RUN == 5
    sig = inspect.signature(R.retire)
    assert list(sig.parameters) == ["vault_root", "rec", "replacement"]
    assert sig.parameters["replacement"].kind is inspect.Parameter.KEYWORD_ONLY
    assert list(inspect.signature(R.undo).parameters) == ["vault_root", "friction_id"]
    sig = inspect.signature(R.is_retired)
    assert list(sig.parameters) == ["fm", "vault_root"]
    assert sig.parameters["vault_root"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["vault_root"].default is None
    sig = inspect.signature(R.plan_retirements)
    assert list(sig.parameters) == ["vault_root", "since"]
    assert sig.parameters["since"].default is None
    # One predicate, not two: retire re-exports the one filters owns.
    assert R.is_retired is filters.is_retired


# --- retire ---------------------------------------------------------------------


def test_retire_writes_both_sides_and_nothing_else(vault):
    rec = fx.record(vault)
    old_before, new_before = _bytes(vault, fx.OLD), _bytes(vault, fx.NEW)

    result = R.retire(vault, rec, replacement=fx.NEW)

    assert result.ok and result.action == "retired"
    assert result.slugs == [fx.OLD] and result.replacement == fx.NEW
    old = _fm(vault, fx.OLD)
    assert old[SUPERSEDED_BY] == fx.NEW
    assert old[SUPERSEDED_BY_FRICTION] == rec.id
    assert old[SUPERSEDED_AT].endswith("Z")
    assert _fm(vault, fx.NEW)[SUPERSEDES] == [fx.OLD]
    # Every other line of both pages is untouched, body included.
    for before, slug, added in ((old_before, fx.OLD, 3), (new_before, fx.NEW, 2)):
        after = _bytes(vault, slug).decode().splitlines()
        kept = before.decode().splitlines()
        assert [ln for ln in after if ln not in kept] and len(after) == len(kept) + added
        assert all(ln in after for ln in kept)
    assert filters.is_retired(old, vault_root=vault)


def test_undo_restores_both_pages_byte_for_byte(vault):
    rec = fx.record(vault)
    before = {s: _bytes(vault, s) for s in (fx.OLD, fx.NEW)}
    assert R.retire(vault, rec, replacement=fx.NEW).ok

    result = R.undo(vault, rec.id)

    assert result.ok and result.action == "undone" and result.slugs == [fx.OLD]
    assert {s: _bytes(vault, s) for s in (fx.OLD, fx.NEW)} == before
    assert not filters.is_retired(_fm(vault, fx.OLD), vault_root=vault)


def test_undo_keeps_other_supersedes_entries(vault):
    fx.write_rule(vault, "older-still", body="Always merge with --admin on every repo.",
                  extra="")
    first = fx.record(vault)
    second = fx.record(vault, quote=fx.QUOTE + " em todo repo", contradicts=["older-still"])
    assert R.retire(vault, first, replacement=fx.NEW).ok
    assert R.retire(vault, second, replacement=fx.NEW).ok
    assert _fm(vault, fx.NEW)[SUPERSEDES] == [fx.OLD, "older-still"]

    assert R.undo(vault, first.id).ok

    assert _fm(vault, fx.NEW)[SUPERSEDES] == ["older-still"]
    assert SUPERSEDED_BY in _fm(vault, "older-still")


def test_undo_of_an_unknown_id_is_refused_not_raised(vault):
    result = R.undo(vault, "f-20260916-000000000000")
    assert not result.ok and "f-20260916-000000000000" in result.reason


def test_retiring_twice_with_the_same_record_is_a_no_op(vault):
    rec = fx.record(vault)
    assert R.retire(vault, rec, replacement=fx.NEW).ok
    snapshot = _bytes(vault, fx.OLD)

    again = R.retire(vault, rec, replacement=fx.NEW)

    assert again.ok and again.action == "already" and again.written == []
    assert _bytes(vault, fx.OLD) == snapshot


def test_a_page_retired_by_another_correction_is_refused(vault):
    fx.write_rule(vault, "third-view", body="Merge with the queue.", evidence_quote="usa a fila")
    first = fx.record(vault)
    other = fx.record(vault, quote="usa a fila de merge", session_id="other")
    assert R.retire(vault, first, replacement=fx.NEW).ok

    result = R.retire(vault, other, replacement="third-view")

    assert not result.ok
    assert fx.OLD in result.reason and fx.NEW in result.reason


def test_cycle_is_refused_naming_both_slugs(vault):
    rec = fx.record(vault)
    assert R.retire(vault, rec, replacement=fx.NEW).ok
    back = fx.record(vault, quote="volta a usar --admin sempre", contradicts=[fx.NEW],
                     session_id="later")
    before = _bytes(vault, fx.NEW)

    result = R.retire(vault, back, replacement=fx.OLD)

    assert not result.ok and "cycle" in result.reason
    assert fx.OLD in result.reason and fx.NEW in result.reason
    assert _bytes(vault, fx.NEW) == before


def test_transitive_cycle_is_refused(vault):
    fx.write_rule(vault, "c-rule", body="Merge through the merge queue.")
    ab = fx.record(vault)
    bc = fx.record(vault, quote="usa a merge queue", contradicts=[fx.NEW], session_id="s2")
    assert R.retire(vault, ab, replacement=fx.NEW).ok
    assert R.retire(vault, bc, replacement="c-rule").ok
    ca = fx.record(vault, quote="nada de merge queue", contradicts=["c-rule"], session_id="s3")

    result = R.retire(vault, ca, replacement=fx.OLD)

    assert not result.ok and "cycle" in result.reason
    assert "c-rule" in result.reason and fx.OLD in result.reason


def test_a_chain_is_allowed(vault):
    fx.write_rule(vault, "c-rule", body="Merge through the merge queue.")
    ab = fx.record(vault)
    bc = fx.record(vault, quote="usa a merge queue", contradicts=[fx.NEW], session_id="s2")
    assert R.retire(vault, ab, replacement=fx.NEW).ok
    assert R.retire(vault, bc, replacement="c-rule").ok
    assert _fm(vault, fx.NEW)[SUPERSEDED_BY] == "c-rule"
    assert _fm(vault, fx.NEW)[SUPERSEDES] == [fx.OLD]


@pytest.mark.parametrize("replacement", [None, "", "no-such-page", fx.OLD])
def test_no_usable_replacement_is_refused(vault, replacement):
    rec = fx.record(vault)
    before = _bytes(vault, fx.OLD)

    result = R.retire(vault, rec, replacement=replacement)

    assert not result.ok and result.action == "refused"
    assert fx.OLD in result.reason
    assert _bytes(vault, fx.OLD) == before


def test_a_record_the_ledger_does_not_hold_is_refused(vault):
    rec = fx.record(vault)
    stranger = replace(rec, id="f-20260916-abcdefabcdef")

    result = R.retire(vault, stranger, replacement=fx.NEW)

    assert not result.ok and "ledger" in result.reason
    assert SUPERSEDED_BY not in _fm(vault, fx.OLD)


def test_an_unlinked_record_is_refused(vault):
    rec = fx.record(vault, contradicts=(), link_basis=ledger.LINK_NONE)
    assert not R.retire(vault, rec, replacement=fx.NEW).ok


def test_a_missing_target_is_refused(vault):
    rec = fx.record(vault, contradicts=["gone-rule"])
    result = R.retire(vault, rec, replacement=fx.NEW)
    assert not result.ok and "gone-rule" in result.reason


def test_a_failed_write_puts_the_first_page_back_and_does_not_raise(vault, monkeypatch):
    from mnemo.core import atomic

    rec = fx.record(vault)
    before = {s: _bytes(vault, s) for s in (fx.OLD, fx.NEW)}
    real = atomic.atomic_write_bytes
    calls = {"n": 0}

    def flaky(path, data):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        real(path, data)

    monkeypatch.setattr(atomic, "atomic_write_bytes", flaky)

    result = R.retire(vault, rec, replacement=fx.NEW)

    assert not result.ok and "disk full" in result.reason
    assert {s: _bytes(vault, s) for s in (fx.OLD, fx.NEW)} == before
    assert "friction.retire" in (vault / ".errors.log").read_text(encoding="utf-8")


def test_retire_rebuilds_the_reflex_index(vault):
    from mnemo.core.reflex import index as reflex_index

    reflex_index.write_index(vault, reflex_index.build_index(vault))
    assert reflex_index.load_index(vault)["docs"][fx.OLD]["retired"] is False
    rec = fx.record(vault)

    assert R.retire(vault, rec, replacement=fx.NEW).ok
    assert reflex_index.load_index(vault)["docs"][fx.OLD]["retired"] is True

    assert R.undo(vault, rec.id).ok
    assert reflex_index.load_index(vault)["docs"][fx.OLD]["retired"] is False


# --- is_retired -----------------------------------------------------------------


def test_superseded_by_without_a_ledger_record_is_not_retired(vault):
    fx.write_rule(vault, "hand-edited", body="x", extra=(
        f"{SUPERSEDED_BY}: {fx.NEW}\n{SUPERSEDED_AT}: '2026-09-16T00:00:00Z'\n"
        f"{SUPERSEDED_BY_FRICTION}: f-20260916-000000000000\n"
    ))
    assert not filters.is_retired(_fm(vault, "hand-edited"), vault_root=vault)


def test_a_real_friction_id_copied_onto_another_page_is_not_retired(vault):
    rec = fx.record(vault)
    fx.write_rule(vault, "bystander", body="x", extra=(
        f"{SUPERSEDED_BY}: {fx.NEW}\n{SUPERSEDED_BY_FRICTION}: {rec.id}\n"
    ))
    assert not filters.is_retired(_fm(vault, "bystander"), vault_root=vault)


def test_without_a_vault_the_predicate_honours_nothing(vault):
    rec = fx.record(vault)
    assert R.retire(vault, rec, replacement=fx.NEW).ok
    fm = _fm(vault, fx.OLD)
    assert filters.is_retired(fm, vault_root=vault)
    assert not filters.is_retired(fm)


def test_an_unlinked_ledger_row_backs_nothing(vault):
    rec = fx.record(vault, contradicts=(), link_basis=ledger.LINK_NONE)
    fm = {"name": fx.OLD, SUPERSEDED_BY: fx.NEW, SUPERSEDED_BY_FRICTION: rec.id}
    assert not filters.is_retired(fm, vault_root=vault)


def test_a_page_with_no_keys_is_live(vault):
    assert not filters.is_retired(_fm(vault, fx.OLD), vault_root=vault)
    assert not filters.is_retired({}, vault_root=vault)


# --- plan + cap -----------------------------------------------------------------


def test_plan_finds_the_replacement_by_evidence_and_carries_the_quote(vault):
    rec = fx.record(vault)

    plans = R.plan_retirements(vault)

    assert [(p.target, p.replacement, p.status) for p in plans] == [
        (fx.OLD, fx.NEW, R.PLAN_RETIRE)
    ]
    assert plans[0].friction_id == rec.id
    assert fx.QUOTE in plans[0].line()
    assert _fm(vault, fx.OLD).get(SUPERSEDED_BY) is None  # dry run writes nothing


def test_plan_reports_missing_and_unreplaced_without_writing(vault):
    fx.record(vault, contradicts=["vanished-rule"])
    fx.record(vault, quote="nunca rode pep8", contradicts=["pep8"], session_id="s9")

    plans = {p.target: p for p in R.plan_retirements(vault)}

    assert plans["vanished-rule"].status == R.PLAN_MISSING
    assert plans["pep8"].status == R.PLAN_REFUSED
    assert "no replacement" in plans["pep8"].reason
    assert "nunca rode pep8" in plans["pep8"].line()


def test_plan_skips_unlinked_records_and_honours_since(vault):
    fx.record(vault, contradicts=(), link_basis=ledger.LINK_NONE, session_id="s0")
    fx.record(vault, ts="2026-09-01T00:00:00Z")
    assert R.plan_retirements(vault, since="2026-09-02") == []
    assert len(R.plan_retirements(vault)) == 1


def test_plan_catches_a_cycle_between_two_pending_records(vault):
    fx.record(vault)  # OLD -> NEW
    fx.record(vault, quote="nao, sem admin nunca mais", contradicts=[fx.NEW],
              session_id="s2")
    # the second record's evidence page is the OLD rule: NEW -> OLD would cycle
    old = vault / "shared" / "feedback" / f"{fx.OLD}.md"
    old.write_text(old.read_text(encoding="utf-8").replace(
        "stability: stable\n",
        "stability: stable\nevidence:\n  quote: 'nao, sem admin nunca mais'\n  source: x\n"),
        encoding="utf-8")

    plans = R.plan_retirements(vault)

    assert [p.status for p in plans] == [R.PLAN_RETIRE, R.PLAN_REFUSED]
    assert "cycle" in plans[1].reason


def test_apply_writes_the_plan_and_reports_done_on_rerun(vault):
    rec = fx.record(vault)
    run = R.apply_retirements(vault, R.plan_retirements(vault))

    assert [r.slugs for r in run.retired] == [[fx.OLD]]
    assert [p.status for p in R.plan_retirements(vault)] == [R.PLAN_DONE]
    assert R.apply_retirements(vault, R.plan_retirements(vault)).results == []
    assert filters.is_retired(_fm(vault, fx.OLD), vault_root=vault)
    assert rec.id == _fm(vault, fx.OLD)[SUPERSEDED_BY_FRICTION]


def test_over_the_cap_retires_nothing_and_reports_the_overflow(vault):
    targets = []
    for i in range(R.MAX_RETIREMENTS_PER_RUN + 1):
        slug = f"victim-{i}"
        fx.write_rule(vault, slug, body=f"victim rule {i}")
        targets.append(slug)
    fx.record(vault, contradicts=targets)
    before = {s: _bytes(vault, s) for s in targets + [fx.NEW]}

    plans = R.plan_retirements(vault)
    run = R.apply_retirements(vault, plans)

    assert sum(p.status == R.PLAN_RETIRE for p in plans) == R.MAX_RETIREMENTS_PER_RUN + 1
    assert run.results == [] and run.overflow == R.MAX_RETIREMENTS_PER_RUN + 1
    assert str(R.MAX_RETIREMENTS_PER_RUN) in run.skipped
    assert {s: _bytes(vault, s) for s in targets + [fx.NEW]} == before


def test_at_the_cap_everything_retires(vault):
    targets = []
    for i in range(R.MAX_RETIREMENTS_PER_RUN):
        slug = f"victim-{i}"
        fx.write_rule(vault, slug, body=f"victim rule {i}")
        targets.append(slug)
    fx.record(vault, contradicts=targets)

    run = R.apply_retirements(vault, R.plan_retirements(vault))

    assert len(run.retired) == R.MAX_RETIREMENTS_PER_RUN and run.overflow == 0
    assert _fm(vault, fx.NEW)[SUPERSEDES] == targets


# --- autoRetire -----------------------------------------------------------------


def test_auto_retire_is_off_by_default_and_writes_nothing(vault):
    rec = fx.record(vault)
    before = _bytes(vault, fx.OLD)

    run = R.auto_retire(vault, [rec], cfg={})

    assert run.results == [] and "autoRetire" in run.skipped
    assert _bytes(vault, fx.OLD) == before
    assert R.auto_retire_enabled({}) is False
    assert R.auto_retire_enabled({"friction": {"autoRetire": "yes"}}) is False


def test_auto_retire_runs_when_switched_on(vault):
    rec = fx.record(vault)
    run = R.auto_retire(vault, [rec], cfg={"friction": {"autoRetire": True}})
    assert [r.slugs for r in run.retired] == [[fx.OLD]]


# --- line endings ------------------------------------------------------------
#
# `_read` keeps a page's bytes on purpose, so on Windows every line of a real
# page ends in CRLF. The first cut of `_split` matched "---\n" literally and
# refused every CRLF page, which is why 19 tests here were red on Windows and
# green everywhere else: the fixtures wrote LF through `write_text`, which the
# platform then translated, so the suite disagreed with itself by platform.

@pytest.mark.parametrize("nl", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_frontmatter_surgery_keeps_the_page_line_endings(nl):
    page = nl.join(["---", "name: x", "type: reference", "---", "body line", ""])
    out = R._with_keys(page, {"superseded_by": "y"}, {})

    assert "superseded_by: y" in out
    assert R._split(out) is not None, "the rewritten page must still parse"

    crlf = out.count("\r\n")
    bare_lf = out.count("\n") - crlf
    if nl == "\r\n":
        assert bare_lf == 0, "a CRLF page must not come back with LF lines"
    else:
        assert crlf == 0, "an LF page must not gain CRLF lines"
