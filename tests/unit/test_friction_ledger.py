"""The friction ledger's on-disk contract.

The ledger is the durable end of mnemo's learning loop: three later pieces
(the contradiction pass, the retroactive backfill, the ``mnemo friction``
report) are written against the shape these tests pin, so the signatures are
asserted as explicitly as the behaviour.

Two guarantees carry the design and get the most attention here. A duplicate
``(session_id, quote)`` is refused, because the backfill sweeps the same
sessions on every rerun and an appending ledger would multiply one correction
by the number of sweeps — inflating exactly the count the ledger exists to
produce. And :func:`record` never raises: a ledger row is never worth an
extraction.
"""
from __future__ import annotations

import inspect
import json
import os
import stat
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from mnemo.core import corrections, friction
from mnemo.core.ci_corrections import ORIGIN_CI, ORIGIN_USER
from mnemo.core.friction import ledger
from mnemo.core.friction.ledger import FrictionRecord
from mnemo.core.mcp import access_log

# One of the real corrections the design was measured on.
QUOTE = (
    "essa regra de sempre fazer backup antes de um deploy cegamente, "
    "nao pode se tornar uma ma pratica?"
)


@pytest.fixture
def telemetry_on(monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1_048_576))


#: One row built by :func:`_rec` is exactly this many bytes on disk, with the
#: session id held to three characters. The rotation tests count rows rather
#: than guess at sizes. The number is the same on every platform because the
#: ledger writes with ``newline=""`` — Windows CI is what proved that matters,
#: by rotating one record earlier on 441-byte CRLF rows.
ROW_BYTES = 440
#: Three rows fit under this; the fourth trips the rotation.
ROTATE_AFTER = 3
TINY_CAP = ROW_BYTES * ROTATE_AFTER - 1


@pytest.fixture
def tiny_ledger(monkeypatch):
    """Telemetry on with a rotation cap small enough to cross in a test."""
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, TINY_CAP))


def _rec(**kw) -> FrictionRecord:
    base = dict(
        ts="2026-09-16T02:50:04Z",
        session_id="e7fb983c-0000-4000-8000-000000000001",
        project="mnemo",
        quote=QUOTE,
        rule_text="Do not back up blindly before every deploy.",
        briefing="bots/mnemo/briefings/sessions/e7fb983c.md",
    )
    base.update(kw)
    return FrictionRecord(**base)


def _lines(vault: Path) -> list:
    path = ledger.ledger_path(vault)
    if not path.exists():
        return []
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _write_raw(vault: Path, text: str) -> Path:
    path = ledger.ledger_path(vault)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# --- the contract other pieces are written against -------------------------


def test_signatures_are_the_contract():
    assert ledger.LEDGER_NAME == "friction-ledger.jsonl"
    assert list(inspect.signature(ledger.record).parameters) == ["vault_root", "rec"]
    assert list(inspect.signature(ledger.record_id).parameters) == ["rec"]

    params = inspect.signature(ledger.iter_records).parameters
    assert list(params) == ["vault_root", "project", "since"]
    assert params["project"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["since"].kind is inspect.Parameter.KEYWORD_ONLY
    assert params["project"].default is None
    assert params["since"].default is None


def test_public_names_are_reachable_from_the_package():
    for name in ("LEDGER_NAME", "FrictionRecord", "record", "iter_records", "record_id"):
        assert getattr(friction, name) is getattr(ledger, name)


def test_record_carries_every_field_the_data_model_lists():
    fields = {f.name for f in FrictionRecord.__dataclass_fields__.values()}
    assert fields == {
        "id", "ts", "session_id", "project", "quote", "rule_text", "briefing",
        "contradicts", "link_basis", "injected_in_session", "origin", "backfilled",
    }


def test_the_ledger_lives_beside_the_other_telemetry(tmp_path):
    assert ledger.ledger_path(tmp_path) == tmp_path / ".mnemo" / "friction-ledger.jsonl"


# --- link_basis is validated at construction -------------------------------


@pytest.mark.parametrize("basis", ["extractor", "extractor+injected", "none"])
def test_valid_link_basis_is_accepted(basis):
    assert _rec(link_basis=basis).link_basis == basis


@pytest.mark.parametrize("basis", ["guess", "EXTRACTOR", "injected", "", None, 0])
def test_invalid_link_basis_is_refused_at_construction(basis):
    # A consumer branches on this value, so a typo must fail where it is
    # written rather than sit in the ledger matching no branch.
    with pytest.raises(ValueError):
        _rec(link_basis=basis)


def test_the_error_names_the_accepted_values():
    with pytest.raises(ValueError, match="extractor\\+injected"):
        _rec(link_basis="guess")


def test_link_basis_defaults_to_none():
    # A correction is never lost because the contradiction pass failed.
    assert FrictionRecord().link_basis == ledger.LINK_NONE
    assert ledger.LINK_BASES == ("extractor", "extractor+injected", "none")


def test_origin_defaults_to_the_user_channel():
    assert FrictionRecord().origin == ORIGIN_USER


# --- round-trip ------------------------------------------------------------


def test_append_read_round_trip(tmp_path, telemetry_on):
    rec = _rec(
        contradicts=["mnemo__merge-requires-admin"],
        link_basis=ledger.LINK_EXTRACTOR_INJECTED,
        injected_in_session=["mnemo__merge-requires-admin", "mnemo__one-session-per-tree"],
        origin=ORIGIN_CI,
        backfilled=True,
    )

    written = ledger.record(tmp_path, rec)

    assert written == ledger.record_id(rec)
    (back,) = list(ledger.iter_records(tmp_path))
    assert back == rec.__class__(**{**rec.__dict__, "id": written})
    assert back.quote == QUOTE
    assert back.contradicts == ["mnemo__merge-requires-admin"]
    assert back.link_basis == "extractor+injected"
    assert back.origin == ORIGIN_CI
    assert back.backfilled is True


def test_records_come_back_oldest_first(tmp_path, telemetry_on):
    for n in range(3):
        ledger.record(tmp_path, _rec(session_id="s{}".format(n), quote="{} {}".format(QUOTE, n)))

    assert [r.session_id for r in ledger.iter_records(tmp_path)] == ["s0", "s1", "s2"]


def test_an_empty_ts_is_stamped_and_an_empty_id_derived(tmp_path, telemetry_on):
    before = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    written = ledger.record(tmp_path, _rec(ts="", id=""))

    (back,) = list(ledger.iter_records(tmp_path))
    assert back.ts >= before
    assert back.id == written == ledger.record_id(back)
    # The id's date prefix is read off ts, so ts must be stamped first.
    assert back.id.startswith("f-{}-".format(back.ts[:10].replace("-", "")))


def test_an_id_the_caller_set_is_kept(tmp_path, telemetry_on):
    written = ledger.record(tmp_path, _rec(id="f-20260101-deadbeefcafe"))

    assert written == "f-20260101-deadbeefcafe"
    assert list(ledger.iter_records(tmp_path))[0].id == "f-20260101-deadbeefcafe"


def test_unicode_quotes_survive_the_round_trip(tmp_path, telemetry_on):
    quote = "nós vamos comentar todo esse grupo de camadas por enquanto"

    ledger.record(tmp_path, _rec(quote=quote))

    assert list(ledger.iter_records(tmp_path))[0].quote == quote


def test_a_row_is_one_json_object_per_line(tmp_path, telemetry_on):
    ledger.record(tmp_path, _rec())
    ledger.record(tmp_path, _rec(session_id="other"))

    rows = [json.loads(ln) for ln in _lines(tmp_path)]
    assert len(rows) == 2
    assert set(rows[0]) == {f.name for f in FrictionRecord.__dataclass_fields__.values()}


# --- the id -----------------------------------------------------------------


def test_record_id_is_stable_across_a_rerun_that_links_differently():
    # The backfill re-runs the contradiction pass; an LLM that names a
    # different slug the second time must not produce a second row.
    first = _rec(contradicts=["mnemo__a"], link_basis=ledger.LINK_EXTRACTOR)
    second = _rec(
        contradicts=["mnemo__b", "mnemo__c"],
        link_basis=ledger.LINK_EXTRACTOR_INJECTED,
        injected_in_session=["mnemo__b"],
        backfilled=True,
    )

    assert ledger.record_id(first) == ledger.record_id(second)


def test_record_id_ignores_how_the_quote_was_punctuated():
    # A rerun that re-quotes the same words with curly quotes or different
    # whitespace is the same correction.
    assert ledger.record_id(_rec(quote="  “{}”  ".format(QUOTE.upper()))) == ledger.record_id(_rec())
    assert corrections.normalize(QUOTE) == corrections.normalize(" “{}” ".format(QUOTE.upper()))


def test_record_id_separates_different_sessions_and_different_quotes():
    ids = {
        ledger.record_id(_rec()),
        ledger.record_id(_rec(session_id="another")),
        ledger.record_id(_rec(quote="pode fazer a correcao que voce acha melhor")),
    }
    assert len(ids) == 3


def test_record_id_shape_is_dated_and_wide_enough_to_not_collide():
    rid = ledger.record_id(_rec())
    prefix, day, digest = rid.split("-")
    assert (prefix, day) == ("f", "20260916")
    assert len(digest) == 12 and set(digest) <= set("0123456789abcdef")


def test_record_id_of_an_undated_record_is_still_an_id():
    assert ledger.record_id(_rec(ts="")).startswith("f-00000000-")
    assert ledger.record_id(_rec(ts="not-a-date")).startswith("f-00000000-")


# --- duplicates -------------------------------------------------------------


def test_a_duplicate_session_and_quote_is_refused(tmp_path, telemetry_on):
    first = ledger.record(tmp_path, _rec())

    again = ledger.record(tmp_path, _rec())

    assert first is not None
    assert again is None, "a refused duplicate reports that nothing was appended"
    assert len(_lines(tmp_path)) == 1


def test_a_rerun_that_links_differently_is_still_a_duplicate(tmp_path, telemetry_on):
    ledger.record(tmp_path, _rec(link_basis=ledger.LINK_NONE))

    assert ledger.record(tmp_path, _rec(
        contradicts=["mnemo__merge-requires-admin"],
        link_basis=ledger.LINK_EXTRACTOR,
        backfilled=True,
    )) is None
    assert len(_lines(tmp_path)) == 1


def test_the_same_quote_in_another_session_is_not_a_duplicate(tmp_path, telemetry_on):
    ledger.record(tmp_path, _rec())

    assert ledger.record(tmp_path, _rec(session_id="a-second-session")) is not None
    assert len(_lines(tmp_path)) == 2


def test_two_corrections_in_one_session_both_land(tmp_path, telemetry_on):
    ledger.record(tmp_path, _rec())

    assert ledger.record(tmp_path, _rec(quote="nos temos autoridade para isso")) is not None
    assert len(_lines(tmp_path)) == 2


def test_a_duplicate_is_caught_across_a_rotation(tmp_path, tiny_ledger):
    ledger.record(tmp_path, _rec())
    for n in range(ROTATE_AFTER):
        ledger.record(tmp_path, _rec(session_id="f{:02d}".format(n)))
    assert (tmp_path / ".mnemo" / "friction-ledger.jsonl.1").exists()

    assert ledger.record(tmp_path, _rec()) is None, "the rotated file is searched too"


def test_a_malformed_neighbour_does_not_let_a_duplicate_through(tmp_path, telemetry_on):
    # A row too broken to parse still occupies its correction's slot.
    row = ledger._to_row(ledger._resolved(_rec()))
    row["link_basis"] = "corrupted-by-hand"
    _write_raw(tmp_path, json.dumps(row, ensure_ascii=False) + "\n")

    assert ledger.record(tmp_path, _rec()) is None
    assert len(_lines(tmp_path)) == 1


# --- rotation ---------------------------------------------------------------


def test_rotation_at_the_boundary_keeps_every_record_readable(tmp_path, tiny_ledger):
    live = tmp_path / ".mnemo" / "friction-ledger.jsonl"
    rotated = tmp_path / ".mnemo" / "friction-ledger.jsonl.1"

    for n in range(ROTATE_AFTER):
        ledger.record(tmp_path, _rec(session_id="s{:02d}".format(n)))
    assert not rotated.exists(), "the cap is not crossed until it is exceeded"
    assert live.stat().st_size == ROW_BYTES * ROTATE_AFTER, "ROW_BYTES still holds"

    for n in range(ROTATE_AFTER, ROTATE_AFTER * 2):
        ledger.record(tmp_path, _rec(session_id="s{:02d}".format(n)))

    assert rotated.exists()
    assert len(_lines(tmp_path)) == ROTATE_AFTER, "the live file really did shed rows"
    ids = [r.session_id for r in ledger.iter_records(tmp_path)]
    assert ids == ["s{:02d}".format(n) for n in range(ROTATE_AFTER * 2)], (
        "rotated rows come first, so the newest record is still last"
    )


def test_a_second_rotation_drops_the_oldest_generation(tmp_path, tiny_ledger):
    # The bound the ledger inherits from ``briefing-log.jsonl``: one rotated
    # sibling, so history older than two files is gone. Pinned rather than
    # fixed, because the ledger is specified to follow that rotation — at the
    # measured ~8.75 corrections a day and ~440 bytes a row, 1 MiB is about
    # nine months, and the report piece is where a longer window gets argued.
    for n in range(ROTATE_AFTER * 3):
        ledger.record(tmp_path, _rec(session_id="s{:02d}".format(n)))

    ids = [r.session_id for r in ledger.iter_records(tmp_path)]
    assert ids == ["s{:02d}".format(n) for n in range(ROTATE_AFTER, ROTATE_AFTER * 3)]


def test_a_row_is_lf_terminated_on_every_platform(tmp_path, telemetry_on):
    # The rotation cap is a byte budget; a CRLF row would spend it at a
    # different rate on Windows and rotate at a different record.
    ledger.record(tmp_path, _rec(session_id="s00"))  # the ROW_BYTES shape

    raw = ledger.ledger_path(tmp_path).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert len(raw) == ROW_BYTES


def test_rotation_only_fires_past_the_cap(tmp_path, telemetry_on):
    ledger.record(tmp_path, _rec())

    assert not (tmp_path / ".mnemo" / "friction-ledger.jsonl.1").exists()


# --- a hand-edited ledger must not crash a reader ---------------------------


def test_a_malformed_line_is_skipped(tmp_path, telemetry_on):
    good_a = json.dumps(ledger._to_row(ledger._resolved(_rec(session_id="good-a"))))
    good_b = json.dumps(ledger._to_row(ledger._resolved(_rec(session_id="good-b"))))
    _write_raw(
        tmp_path,
        "\n".join([
            good_a,
            '{"id": "f-1", "quote": "torn by a killed appen',  # torn JSON
            "",                                                 # blank
            "[1, 2, 3]",                                        # not an object
            "not json at all",
            json.dumps({"session_id": "bad", "link_basis": "guess"}),
            good_b,
        ]) + "\n",
    )

    assert [r.session_id for r in ledger.iter_records(tmp_path)] == ["good-a", "good-b"]


def test_a_row_missing_keys_takes_the_defaults(tmp_path):
    # A missing key is not corruption — only a value nothing branches on is.
    _write_raw(tmp_path, json.dumps({"session_id": "sparse", "quote": QUOTE}) + "\n")

    (rec,) = list(ledger.iter_records(tmp_path))
    assert rec.session_id == "sparse"
    assert rec.contradicts == [] and rec.injected_in_session == []
    assert rec.link_basis == ledger.LINK_NONE
    assert rec.origin == ORIGIN_USER
    assert rec.backfilled is False


def test_rows_with_wrongly_typed_values_are_coerced_not_raised(tmp_path):
    _write_raw(tmp_path, json.dumps({
        "session_id": 42,
        "quote": None,
        "contradicts": "mnemo__a",
        "injected_in_session": ["ok", 7, None],
        "backfilled": "yes",
    }) + "\n")

    (rec,) = list(ledger.iter_records(tmp_path))
    assert rec.session_id == "" and rec.quote == ""
    assert rec.contradicts == []
    assert rec.injected_in_session == ["ok"]
    assert rec.backfilled is True


def test_reading_a_vault_with_no_ledger_yields_nothing(tmp_path):
    assert list(ledger.iter_records(tmp_path)) == []
    assert list(ledger.iter_records(tmp_path / "does" / "not" / "exist")) == []


# --- filters ----------------------------------------------------------------


def _seed_projects(vault: Path) -> None:
    for project, day in (("mnemo", "10"), ("clubinho", "12"), ("mnemo", "16")):
        ledger.record(vault, _rec(
            project=project,
            session_id="{}-{}".format(project, day),
            quote="{} {} {}".format(QUOTE, project, day),
            ts="2026-09-{}T02:50:04Z".format(day),
        ))


def test_project_matches_exactly(tmp_path, telemetry_on):
    _seed_projects(tmp_path)

    assert [r.project for r in ledger.iter_records(tmp_path, project="mnemo")] == ["mnemo"] * 2
    assert list(ledger.iter_records(tmp_path, project="mnem")) == []
    assert list(ledger.iter_records(tmp_path, project="MNEMO")) == []
    assert len(list(ledger.iter_records(tmp_path))) == 3


def test_since_is_an_inclusive_lower_bound(tmp_path, telemetry_on):
    _seed_projects(tmp_path)

    assert len(list(ledger.iter_records(tmp_path, since=date(2026, 9, 12)))) == 2
    assert len(list(ledger.iter_records(tmp_path, since=date(2026, 9, 13)))) == 1
    assert len(list(ledger.iter_records(tmp_path, since=date(2026, 1, 1)))) == 3


def test_since_also_takes_an_iso_string(tmp_path, telemetry_on):
    _seed_projects(tmp_path)

    assert len(list(ledger.iter_records(tmp_path, since="2026-09-12"))) == 2
    assert len(list(ledger.iter_records(tmp_path, since=datetime(2026, 9, 12, 23, 59)))) == 2


def test_a_since_that_is_not_a_date_is_a_caller_error(tmp_path):
    with pytest.raises(ValueError):
        list(ledger.iter_records(tmp_path, since="last tuesday"))


def test_an_undated_record_falls_outside_a_since_window(tmp_path, telemetry_on):
    ledger.record(tmp_path, _rec(ts="   "))

    assert len(list(ledger.iter_records(tmp_path))) == 1
    assert list(ledger.iter_records(tmp_path, since=date(2020, 1, 1))) == []


def test_filters_combine(tmp_path, telemetry_on):
    _seed_projects(tmp_path)

    got = list(ledger.iter_records(tmp_path, project="mnemo", since=date(2026, 9, 11)))
    assert [r.session_id for r in got] == ["mnemo-16"]


# --- never raises -----------------------------------------------------------


def test_an_unwritable_vault_returns_none_rather_than_raising(tmp_path, telemetry_on):
    # ``.mnemo`` occupied by a regular file: mkdir cannot succeed, on every
    # platform. A read-only vault is the same failure with a different errno.
    (tmp_path / ".mnemo").write_text("not a directory", encoding="utf-8")

    assert ledger.record(tmp_path, _rec()) is None
    assert (tmp_path / ".mnemo").read_text(encoding="utf-8") == "not a directory"


def test_a_failed_write_leaves_one_errors_log_row(tmp_path, telemetry_on):
    (tmp_path / ".mnemo").write_text("not a directory", encoding="utf-8")

    ledger.record(tmp_path, _rec())

    rows = [json.loads(ln) for ln in (tmp_path / ".errors.log").read_text(
        encoding="utf-8").splitlines() if ln.strip()]
    assert [r["where"] for r in rows] == ["friction.ledger.record"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits do not gate writes on Windows")
@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores mode bits")
def test_a_read_only_vault_returns_none(tmp_path, telemetry_on):
    vault = tmp_path / "vault"
    vault.mkdir()
    vault.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        assert ledger.record(vault, _rec()) is None
    finally:
        vault.chmod(stat.S_IRWXU)


def test_a_write_that_explodes_mid_append_returns_none(tmp_path, telemetry_on, monkeypatch):
    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(ledger, "open", boom, raising=False)
    assert ledger.record(tmp_path, _rec()) is None


def test_an_unreadable_telemetry_config_disables_the_ledger(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(access_log, "_load_telemetry_config", boom)
    assert ledger.record(tmp_path, _rec()) is None
    assert not ledger.ledger_path(tmp_path).exists()


# --- the telemetry switch ---------------------------------------------------


def test_telemetry_off_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (False, 1_048_576))

    assert ledger.record(tmp_path, _rec()) is None
    assert not ledger.ledger_path(tmp_path).exists()


def test_the_switch_is_the_one_the_briefing_log_uses(tmp_path, monkeypatch):
    # Delegated, not re-read: one switch means the ledger cannot drift from
    # the log it is specified to follow.
    seen = []

    def spy():
        seen.append(True)
        return True, 1_048_576

    monkeypatch.setattr(access_log, "_load_telemetry_config", spy)
    ledger.record(tmp_path, _rec())

    assert seen == [True]


def test_the_cap_comes_from_config_not_a_constant(tmp_path, monkeypatch):
    monkeypatch.setattr(access_log, "_load_telemetry_config", lambda: (True, 1))

    ledger.record(tmp_path, _rec())
    ledger.record(tmp_path, _rec(session_id="second"))

    assert (tmp_path / ".mnemo" / "friction-ledger.jsonl.1").exists()


# --- the boundary this module does not police -------------------------------


def test_the_module_states_that_it_does_not_verify_the_quote():
    # A later caller must not assume the ledger re-checks the quote against
    # the transcript; ``corrections.verify`` is the caller's job.
    doc = ledger.__doc__ or ""
    assert "corrections.verify" in doc
    assert "not verified here" in doc


def test_an_unverified_quote_is_stored_verbatim_without_complaint(tmp_path, telemetry_on):
    # Recording the boundary as behaviour, not only as prose: nothing here
    # rejects a quote, which is exactly why the caller must verify first.
    fabricated = "the user never typed these words"

    assert ledger.record(tmp_path, _rec(quote=fabricated)) is not None
    assert list(ledger.iter_records(tmp_path))[0].quote == fabricated


# --- #360: rows quoting a shell command stop counting -------------------------


def test_a_row_quoting_a_shell_command_is_not_read_back(tmp_path, telemetry_on):
    shell = "<bash-input> gh pr merge 186 --squash --delete-branch --admin</bash-input>"
    ledger.record(tmp_path, _rec(quote=shell, contradicts=["merge-requires-admin"],
                                 link_basis=ledger.LINK_EXTRACTOR))
    kept = ledger.record(tmp_path, _rec(session_id="s2"))

    assert [r.id for r in ledger.iter_records(tmp_path)] == [kept]
    # Still on disk — the ledger is append-only — and still blocks a re-append.
    assert len(_lines(tmp_path)) == 2
    assert ledger.record(tmp_path, _rec(quote=shell)) is None
    assert len(_lines(tmp_path)) == 2
